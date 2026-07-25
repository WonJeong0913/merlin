"""Live Codex hook bridge for HarnessX pre-execution tool governance.

The hook accepts Codex hook JSON on stdin and emits only the documented hook
decision JSON on stdout.  Raw commands and tool responses are evaluated in
memory and represented in the append-only audit as hashes and bounded counts.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .harnessx_runtime import (
    ExactToolCallPolicyProcessor,
    ExactToolInputPolicyProcessor,
    HarnessXRuntime,
    HarnessXVariantSpec,
    ToolCallEvent,
    build_harnessx_runtime_from_variant,
    harnessx_variant_from_payload,
    make_default_harnessx_registry,
    snapshot_harnessx_variant,
)


MAX_HOOK_INPUT_BYTES = 1_048_576
MAX_AUDIT_BYTES = 8_388_608
MAX_AUDIT_RECORDS = 10_000


class HarnessXLiveHookError(RuntimeError):
    """Raised when the live hook cannot make or record a trustworthy decision."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_text(_canonical_json(value))


def _hash_identifier(value: object) -> str:
    return _sha256_text(value if isinstance(value, str) else "")[:24]


def _require_string(value: object, *, label: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise HarnessXLiveHookError(f"{label} must be a string")
    return value


def _normalize_string_tuple(value: object, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise HarnessXLiveHookError(f"{label} must be a list of non-empty strings")
    if len(set(value)) != len(value):
        raise HarnessXLiveHookError(f"{label} must not contain duplicates")
    return tuple(sorted(value))


@dataclass(frozen=True, slots=True)
class LiveToolPolicy:
    policy_id: str
    model_id: str
    variant: HarnessXVariantSpec
    variant_sha256: str
    sha256: str


def load_live_tool_policy(path: str | Path) -> LiveToolPolicy:
    requested_path = Path(path).expanduser()
    if requested_path.is_symlink():
        raise HarnessXLiveHookError("policy must be a regular file")
    policy_path = requested_path.resolve(strict=True)
    if not policy_path.is_file():
        raise HarnessXLiveHookError("policy must be a regular file")
    try:
        raw = policy_path.read_bytes()
    except OSError as exc:
        raise HarnessXLiveHookError("policy cannot be read") from exc
    if len(raw) > 65_536:
        raise HarnessXLiveHookError("policy exceeds the byte bound")
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessXLiveHookError("policy is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        raise HarnessXLiveHookError("policy schema is unsupported")
    declared_sha = payload.get("policy_sha256")
    body = {key: value for key, value in payload.items() if key != "policy_sha256"}
    if not isinstance(declared_sha, str) or declared_sha != _sha256_json(body):
        raise HarnessXLiveHookError("policy SHA-256 mismatch")
    policy_id = _require_string(payload.get("policy_id"), label="policy_id")
    model_configuration = payload.get("model_configuration")
    if (
        not isinstance(model_configuration, dict)
        or set(model_configuration) != {"model_id", "role", "fallback_policy"}
        or model_configuration.get("role") != "main"
        or model_configuration.get("fallback_policy") != "none"
    ):
        raise HarnessXLiveHookError("model configuration is invalid")
    model_id = _require_string(model_configuration.get("model_id"), label="model_id")
    variant_payload = payload.get("harness_configuration")
    if not isinstance(variant_payload, dict):
        raise HarnessXLiveHookError("harness configuration is missing")
    try:
        variant = harnessx_variant_from_payload(variant_payload)
    except Exception as exc:
        raise HarnessXLiveHookError("harness configuration is invalid") from exc
    declared_variant_sha = payload.get("harness_configuration_sha256")
    if not isinstance(declared_variant_sha, str) or declared_variant_sha != variant.sha256:
        raise HarnessXLiveHookError("harness configuration SHA-256 mismatch")
    adapter = payload.get("adapter")
    if (
        not isinstance(adapter, dict)
        or adapter.get("type") != "codex_pre_tool_use_v1"
        or adapter.get("external_hook") != "PreToolUse"
        or adapter.get("typed_hook") != "before_tool"
    ):
        raise HarnessXLiveHookError("live adapter contract is invalid")
    if (
        len(variant.processors) != 1
        or variant.processors[0].name
        not in {
            ExactToolInputPolicyProcessor.name,
            ExactToolCallPolicyProcessor.name,
        }
    ):
        raise HarnessXLiveHookError(
            "live variant must contain a registered exact tool policy processor"
        )
    try:
        build_harnessx_runtime_from_variant(variant, make_default_harnessx_registry())
    except Exception as exc:
        raise HarnessXLiveHookError("live variant cannot be reconstructed") from exc
    return LiveToolPolicy(
        policy_id=policy_id,
        model_id=model_id,
        variant=variant,
        variant_sha256=declared_variant_sha,
        sha256=declared_sha,
    )


def build_live_tool_policy_payload(
    *,
    policy_id: str,
    allowed_commands: tuple[str, ...],
    command_tool: str = "Bash",
    denied_tools: tuple[str, ...] = ("apply_patch",),
    model_id: str = "provider-default",
) -> dict[str, Any]:
    """Build a self-hashing H=(M,C) live contract from a typed HarnessX variant."""

    _require_string(policy_id, label="policy_id")
    _require_string(command_tool, label="command_tool")
    normalized_allowed = tuple(sorted(set(allowed_commands)))
    normalized_denied = tuple(sorted(set(denied_tools)))
    if any(not isinstance(item, str) or not item for item in normalized_allowed):
        raise HarnessXLiveHookError("allowed_commands contains an invalid item")
    if any(not isinstance(item, str) or not item for item in normalized_denied):
        raise HarnessXLiveHookError("denied_tools contains an invalid item")
    if command_tool in normalized_denied:
        raise HarnessXLiveHookError("command tool cannot also be denied")
    _require_string(model_id, label="model_id")
    runtime = HarnessXRuntime(
        [
            ExactToolInputPolicyProcessor(
                allowed_commands=normalized_allowed,
                command_tool=command_tool,
                denied_tools=normalized_denied,
            )
        ]
    )
    variant = snapshot_harnessx_variant(
        runtime,
        variant_id="live-read-only-tool-boundary-v1",
        summary="Exact pre-execution tool-input policy for the live Codex adapter.",
        slots={
            "tool_registry": "codex_builtin_tools",
            "tracer": "harnessx_live_hook_audit",
            "workspace": "codex_hook_input.cwd",
            "sandbox_provider": "codex_workspace_write",
            "plugin_list": "codex_session",
        },
        policy={
            "dimensions": ["D4", "D7", "D8"],
            "pre_execution_enforcement": True,
            "exact_input_match": True,
        },
        metadata={
            "source_contract": "HarnessX H=(M,C), C=(P,S)",
            "candidate_evolution_claim": False,
        },
    )
    return build_live_tool_policy_payload_from_variant(
        policy_id=policy_id,
        variant=variant,
        model_id=model_id,
    )


def build_exact_tool_call_policy_payload(
    *,
    policy_id: str,
    allowed_tool_inputs: tuple[dict[str, Any], ...],
    denied_tools: tuple[str, ...] = ("Write", "Edit", "apply_patch"),
    model_id: str = "provider-default",
) -> dict[str, Any]:
    """Build a multi-tool exact-input live policy without tool-class inference."""

    _require_string(policy_id, label="policy_id")
    _require_string(model_id, label="model_id")
    try:
        processor = ExactToolCallPolicyProcessor(
            allowed_tool_inputs=allowed_tool_inputs,
            denied_tools=denied_tools,
        )
        runtime = HarnessXRuntime([processor])
        variant = snapshot_harnessx_variant(
            runtime,
            variant_id="live-exact-multitool-boundary-v1",
            summary=(
                "Exact pre-execution multi-tool input policy for the live Codex adapter."
            ),
            slots={
                "tool_registry": "codex_builtin_tools",
                "tracer": "harnessx_live_hook_audit",
                "workspace": "codex_hook_input.cwd",
                "sandbox_provider": "codex_workspace_write",
                "plugin_list": "codex_session",
            },
            policy={
                "dimensions": ["D4", "D7", "D8"],
                "pre_execution_enforcement": True,
                "exact_input_match": True,
                "multi_tool": True,
            },
            metadata={
                "source_contract": "HarnessX H=(M,C), C=(P,S)",
                "candidate_evolution_claim": False,
            },
        )
    except Exception as exc:
        if isinstance(exc, HarnessXLiveHookError):
            raise
        raise HarnessXLiveHookError("exact multi-tool policy is invalid") from exc
    return build_live_tool_policy_payload_from_variant(
        policy_id=policy_id,
        variant=variant,
        model_id=model_id,
    )


def build_live_tool_policy_payload_from_variant(
    *,
    policy_id: str,
    variant: HarnessXVariantSpec,
    model_id: str = "provider-default",
) -> dict[str, Any]:
    """Bind an already gated typed variant to the live Codex adapter."""

    _require_string(policy_id, label="policy_id")
    _require_string(model_id, label="model_id")
    if (
        len(variant.processors) != 1
        or variant.processors[0].name
        not in {
            ExactToolInputPolicyProcessor.name,
            ExactToolCallPolicyProcessor.name,
        }
    ):
        raise HarnessXLiveHookError(
            "live variant must contain a registered exact tool policy processor"
        )
    try:
        build_harnessx_runtime_from_variant(variant, make_default_harnessx_registry())
    except Exception as exc:
        raise HarnessXLiveHookError("live variant cannot be reconstructed") from exc
    processor_config = variant.processors[0].config
    denied_tools = processor_config.get("denied_tools")
    if not isinstance(denied_tools, list):
        raise HarnessXLiveHookError("live processor configuration is invalid")
    if variant.processors[0].name == ExactToolInputPolicyProcessor.name:
        command_tool = processor_config.get("command_tool")
        if not isinstance(command_tool, str):
            raise HarnessXLiveHookError("live processor configuration is invalid")
        enforced_tools = sorted({command_tool, *denied_tools})
    else:
        raw_allowed = processor_config.get("allowed_tool_inputs")
        if not isinstance(raw_allowed, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("tool_name"), str)
            for item in raw_allowed
        ):
            raise HarnessXLiveHookError("live processor configuration is invalid")
        enforced_tools = sorted(
            {
                *(item["tool_name"] for item in raw_allowed),
                *denied_tools,
            }
        )
    body: dict[str, Any] = {
        "schema_version": 2,
        "policy_id": policy_id,
        "model_configuration": {
            "model_id": model_id,
            "role": "main",
            "fallback_policy": "none",
        },
        "harness_configuration": variant.canonical_payload(),
        "harness_configuration_sha256": variant.sha256,
        "adapter": {
            "type": "codex_pre_tool_use_v1",
            "external_hook": "PreToolUse",
            "typed_hook": "before_tool",
            "enforced_tools": enforced_tools,
        },
    }
    return {**body, "policy_sha256": _sha256_json(body)}


def write_new_live_tool_policy(
    path: str | Path,
    *,
    policy_id: str,
    allowed_commands: tuple[str, ...],
    command_tool: str = "Bash",
    denied_tools: tuple[str, ...] = ("apply_patch",),
    model_id: str = "provider-default",
) -> LiveToolPolicy:
    policy_path = Path(path)
    payload = build_live_tool_policy_payload(
        policy_id=policy_id,
        allowed_commands=allowed_commands,
        command_tool=command_tool,
        denied_tools=denied_tools,
        model_id=model_id,
    )
    try:
        with policy_path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise HarnessXLiveHookError("refusing to overwrite live tool policy") from exc
    return load_live_tool_policy(policy_path)


def write_new_exact_tool_call_policy(
    path: str | Path,
    *,
    policy_id: str,
    allowed_tool_inputs: tuple[dict[str, Any], ...],
    denied_tools: tuple[str, ...] = ("Write", "Edit", "apply_patch"),
    model_id: str = "provider-default",
) -> LiveToolPolicy:
    """Persist one new-only exact multi-tool policy."""

    policy_path = Path(path)
    payload = build_exact_tool_call_policy_payload(
        policy_id=policy_id,
        allowed_tool_inputs=allowed_tool_inputs,
        denied_tools=denied_tools,
        model_id=model_id,
    )
    try:
        with policy_path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise HarnessXLiveHookError("refusing to overwrite live tool policy") from exc
    return load_live_tool_policy(policy_path)


def write_new_live_tool_policy_from_variant(
    path: str | Path,
    *,
    policy_id: str,
    variant: HarnessXVariantSpec,
    model_id: str = "provider-default",
) -> LiveToolPolicy:
    """Persist one exact promoted variant as a new-only live contract."""

    policy_path = Path(path)
    payload = build_live_tool_policy_payload_from_variant(
        policy_id=policy_id,
        variant=variant,
        model_id=model_id,
    )
    try:
        with policy_path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise HarnessXLiveHookError("refusing to overwrite live tool policy") from exc
    return load_live_tool_policy(policy_path)


def _validated_audit_tail(lines: list[str]) -> tuple[int, str | None]:
    previous_sha: str | None = None
    for expected_sequence, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HarnessXLiveHookError("audit contains malformed JSON") from exc
        if not isinstance(record, dict):
            raise HarnessXLiveHookError("audit record must be an object")
        record_sha = record.get("record_sha256")
        body = {key: value for key, value in record.items() if key != "record_sha256"}
        if (
            record.get("sequence") != expected_sequence
            or record.get("previous_record_sha256") != previous_sha
            or not isinstance(record_sha, str)
            or record_sha != _sha256_json(body)
        ):
            raise HarnessXLiveHookError("audit hash chain validation failed")
        previous_sha = record_sha
    return len(lines), previous_sha


def load_and_validate_live_hook_audit(path: str | Path) -> tuple[dict[str, Any], ...]:
    """Read and independently validate a complete bounded audit chain."""

    requested_path = Path(path).expanduser()
    if requested_path.is_symlink():
        raise HarnessXLiveHookError("audit path must be a regular file")
    audit_path = requested_path.resolve(strict=True)
    if not audit_path.is_file():
        raise HarnessXLiveHookError("audit path must be a regular file")
    try:
        raw = audit_path.read_bytes()
    except OSError as exc:
        raise HarnessXLiveHookError("audit cannot be read") from exc
    if len(raw) > MAX_AUDIT_BYTES:
        raise HarnessXLiveHookError("audit exceeds the byte bound")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise HarnessXLiveHookError("audit is not valid UTF-8") from exc
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > MAX_AUDIT_RECORDS:
        raise HarnessXLiveHookError("audit exceeds the record bound")
    _validated_audit_tail(lines)
    return tuple(json.loads(line) for line in lines)


def append_live_hook_audit(path: str | Path, record: dict[str, Any]) -> dict[str, Any]:
    """Append one hash-chained record while holding an advisory process lock."""

    audit_path = Path(path)
    reserved = {
        "schema_version",
        "sequence",
        "previous_record_sha256",
        "record_sha256",
    }
    if reserved & record.keys():
        raise HarnessXLiveHookError("audit record contains reserved keys")
    if audit_path.exists() and (audit_path.is_symlink() or not audit_path.is_file()):
        raise HarnessXLiveHookError("audit path must be a regular file")
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with audit_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0, os.SEEK_END)
            if handle.tell() > MAX_AUDIT_BYTES:
                raise HarnessXLiveHookError("audit exceeds the byte bound")
            handle.seek(0)
            lines = [line.rstrip("\n") for line in handle if line.strip()]
            if len(lines) >= MAX_AUDIT_RECORDS:
                raise HarnessXLiveHookError("audit exceeds the record bound")
            sequence, previous_sha = _validated_audit_tail(lines)
            body = {
                "schema_version": 1,
                "sequence": sequence + 1,
                "previous_record_sha256": previous_sha,
                **record,
            }
            sealed = {**body, "record_sha256": _sha256_json(body)}
            handle.seek(0, os.SEEK_END)
            handle.write(_canonical_json(sealed) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            return sealed
    except OSError as exc:
        raise HarnessXLiveHookError("audit append failed") from exc


def _read_hook_input(stream: Any) -> dict[str, Any]:
    raw = stream.buffer.read(MAX_HOOK_INPUT_BYTES + 1)
    if len(raw) > MAX_HOOK_INPUT_BYTES:
        raise HarnessXLiveHookError("hook input exceeds the byte bound")
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessXLiveHookError("hook input is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise HarnessXLiveHookError("hook input must be an object")
    return payload


def _tool_input(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    tool_name = _require_string(payload.get("tool_name"), label="tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        raise HarnessXLiveHookError("tool_input must be an object")
    return tool_name, tool_input


def run_pre_tool_use(
    payload: dict[str, Any],
    *,
    policy: LiveToolPolicy,
    audit_path: str | Path,
) -> dict[str, Any]:
    if payload.get("hook_event_name") != "PreToolUse":
        raise HarnessXLiveHookError("expected PreToolUse input")
    tool_name, tool_input = _tool_input(payload)
    tool_input_json = _canonical_json(tool_input)
    event = ToolCallEvent(
        event_id=f"tool-{_hash_identifier(payload.get('tool_use_id'))}",
        task_id=f"session-{_hash_identifier(payload.get('session_id'))}",
        step_index=0,
        tool_name=tool_name,
        tool_input_json=tool_input_json,
        metadata={"live_pre_execution": True},
    )
    runtime = build_harnessx_runtime_from_variant(
        policy.variant,
        make_default_harnessx_registry(),
    )
    emission = runtime.emit_sync(event)
    if not emission.audit:
        # No processor ran, so `intercepted` being false carries no decision.
        # `load_live_tool_policy` already rejects this shape, so reaching here
        # means the policy was constructed in-process; refuse rather than
        # report an ungoverned call as an allow.
        raise HarnessXLiveHookError(
            "live policy ran no before_tool processor; refusing to decide"
        )
    decision = "deny" if emission.intercepted else "allow"
    command = tool_input.get("command")
    command_text = command if isinstance(command, str) else ""
    processor_outcome = emission.audit[0].outcome.value
    processor_name = emission.audit[0].processor
    append_live_hook_audit(
        audit_path,
        {
            "phase": "pre_tool_use",
            "live_pre_execution_gate_invoked": True,
            "hook_decision_returned_to_codex": True,
            "decision": decision,
            "policy_id": policy.policy_id,
            "policy_sha256": policy.sha256,
            "harness_configuration_sha256": policy.variant_sha256,
            "session_id_sha256_prefix": _hash_identifier(payload.get("session_id")),
            "turn_id_sha256_prefix": _hash_identifier(payload.get("turn_id")),
            "tool_use_id_sha256_prefix": _hash_identifier(payload.get("tool_use_id")),
            "tool_name": tool_name,
            "tool_input_sha256": _sha256_text(tool_input_json),
            "command_sha256": _sha256_text(command_text),
            "command_chars": len(command_text),
            "processor": processor_name,
            "processor_outcome": processor_outcome,
            "processor_audit": [
                {
                    "hook": item.hook.value,
                    "processor": item.processor,
                    "singleton_group": item.singleton_group,
                    "outcome": item.outcome.value,
                    "output_count": len(item.output_event_ids),
                }
                for item in emission.audit
            ],
            "raw_command_stored": False,
            "raw_tool_response_stored": False,
        },
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": (
                "HarnessX exact live-tool policy allowed this input."
                if decision == "allow"
                else "HarnessX exact live-tool policy denied this input before execution."
            ),
        }
    }


def run_post_tool_use(
    payload: dict[str, Any],
    *,
    policy: LiveToolPolicy,
    audit_path: str | Path,
) -> dict[str, Any]:
    if payload.get("hook_event_name") != "PostToolUse":
        raise HarnessXLiveHookError("expected PostToolUse input")
    tool_name, tool_input = _tool_input(payload)
    tool_response = payload.get("tool_response")
    response_json = _canonical_json(tool_response)
    tool_input_json = _canonical_json(tool_input)
    command = tool_input.get("command")
    command_text = command if isinstance(command, str) else ""
    append_live_hook_audit(
        audit_path,
        {
            "phase": "post_tool_use",
            "live_pre_execution_gate_invoked": False,
            "hook_decision_returned_to_codex": False,
            "decision": "observed_after_execution",
            "policy_id": policy.policy_id,
            "policy_sha256": policy.sha256,
            "harness_configuration_sha256": policy.variant_sha256,
            "session_id_sha256_prefix": _hash_identifier(payload.get("session_id")),
            "turn_id_sha256_prefix": _hash_identifier(payload.get("turn_id")),
            "tool_use_id_sha256_prefix": _hash_identifier(payload.get("tool_use_id")),
            "tool_name": tool_name,
            "tool_input_sha256": _sha256_text(tool_input_json),
            "command_sha256": _sha256_text(command_text),
            "command_chars": len(command_text),
            "tool_response_sha256": _sha256_text(response_json),
            "tool_response_chars": len(response_json),
            "raw_command_stored": False,
            "raw_tool_response_stored": False,
        },
    )
    return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one HarnessX live Codex hook.")
    parser.add_argument("--phase", required=True, choices=("pre", "post"))
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = _read_hook_input(sys.stdin)
        policy = load_live_tool_policy(args.policy)
        output = (
            run_pre_tool_use(payload, policy=policy, audit_path=args.audit)
            if args.phase == "pre"
            else run_post_tool_use(payload, policy=policy, audit_path=args.audit)
        )
    except Exception as exc:
        print(
            f"HarnessX live hook failed closed ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 2
    print(_canonical_json(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
