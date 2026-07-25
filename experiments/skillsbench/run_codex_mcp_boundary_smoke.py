"""Run one non-benchmark Codex MCP-only boundary canary.

The canary disables every locally observed tool-bearing Codex feature, ignores
user config and rules, adds exactly one fixed-container MCP server, and asks
the requested model to call its single ``exec`` tool once.  It is intentionally
allowed to target a missing container: the purpose is to observe the model to
MCP boundary without claiming task execution or benchmark utility.

Raw Codex JSONL, MCP protocol audit, stderr, and the final response stay under
the caller-provided raw root.  The separate safe report contains only hashes,
counts, requested-model facts, and explicit claim boundaries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from experiments.skillsbench.probe_codex_mcp_capability import (
    DEFAULT_CODEX_CANDIDATES,
    DEFAULT_SERVER,
    NATIVE_TOOL_FEATURES_TO_DISABLE,
    codex_mcp_stdio_launch,
    detect_codex_executable,
    probe_codex_feature_suppression,
    summarize_recorded_audit,
)
from src.merlin_harness.codex_adapter import CodexCliAdapterError, parse_codex_exec_jsonl
from src.merlin_harness.management import content_sha256


MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ALLOWED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "ultra"})
MAX_RAW_BYTES = 2_000_000
MCP_SERVER_KEY = "merlin_harness_task"


class CodexMcpBoundarySmokeError(ValueError):
    """Raised when the canary cannot prove its narrow boundary contract."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise CodexMcpBoundarySmokeError(
            f"refusing to overwrite smoke artifact: {path.name}"
        ) from exc


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _item_type_counts(raw_jsonl: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line_number, line in enumerate(raw_jsonl.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CodexMcpBoundarySmokeError(
                f"Codex JSONL line {line_number} is malformed"
            ) from exc
        if not isinstance(event, dict):
            raise CodexMcpBoundarySmokeError("Codex JSONL event is not an object")
        if event.get("type") not in {"item.started", "item.completed"}:
            continue
        item = event.get("item")
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise CodexMcpBoundarySmokeError("Codex item event has no typed item")
        item_type = item["type"]
        counts[item_type] = counts.get(item_type, 0) + 1
    return dict(sorted(counts.items()))


def build_command(
    *,
    codex_executable: Path,
    server_path: Path,
    raw_root: Path,
    model: str,
    effort: str,
) -> list[str]:
    """Build the exact feature-suppressed one-MCP Codex invocation."""

    workspace = raw_root / "empty-workspace"
    schema_path = raw_root / "response.schema.json"
    last_message_path = raw_root / "last-message.json"
    audit_path = raw_root / "mcp-audit.jsonl"
    server_args = [
        str(server_path),
        "--container",
        "merlin-boundary-canary-missing-container",
        "--workdir",
        "/root/task",
        "--timeout-sec",
        "30",
        "--audit-log",
        str(audit_path),
    ]
    launch = codex_mcp_stdio_launch(
        codex_executable=codex_executable,
        server_argv=server_args,
    )
    command = [str(codex_executable)]
    for feature in NATIVE_TOOL_FEATURES_TO_DISABLE:
        command.extend(("--disable", feature))
    command.extend(
        (
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--strict-config",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--color",
            "never",
            "--model",
            model,
            "-c",
            f'model_reasoning_effort={_toml_string(effort)}',
            "-c",
            "developer_instructions=\"You must use the configured MCP exec tool "
            "exactly once before answering. Never infer or invent its result.\"",
            "-c",
            f'mcp_servers.{MCP_SERVER_KEY}.command={_toml_string(launch["command"])}',
            "-c",
            f'mcp_servers.{MCP_SERVER_KEY}.args={json.dumps(launch["args"])}',
            "-c",
            f'mcp_servers.{MCP_SERVER_KEY}.enabled=true',
            "-c",
            f'mcp_servers.{MCP_SERVER_KEY}.required=true',
            "--cd",
            str(workspace),
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(last_message_path),
            "-",
        )
    )
    return command


def run_boundary_smoke(
    *,
    codex_executable: Path,
    server_path: Path,
    raw_root: Path,
    output_path: Path,
    model: str = "gpt-5.6-terra",
    effort: str = "low",
    timeout_s: float = 180.0,
) -> dict[str, Any]:
    if not MODEL_RE.fullmatch(model):
        raise CodexMcpBoundarySmokeError("model contains unsupported characters")
    if effort not in ALLOWED_EFFORTS:
        raise CodexMcpBoundarySmokeError("effort is unsupported")
    if timeout_s <= 0 or timeout_s > 600:
        raise CodexMcpBoundarySmokeError("timeout must be in (0, 600]")
    codex = codex_executable.expanduser().resolve(strict=True)
    server = server_path.expanduser().resolve(strict=True)
    raw = raw_root.expanduser().resolve(strict=False)
    destination = output_path.expanduser().resolve(strict=False)
    if raw.exists() or raw.is_symlink():
        raise CodexMcpBoundarySmokeError("raw root must be new-only")
    if destination.exists() or destination.is_symlink():
        raise CodexMcpBoundarySmokeError("safe output must be new-only")
    raw.mkdir(parents=True)
    (raw / "empty-workspace").mkdir()
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "additionalProperties": False,
        "required": ["tool_call_attempted", "mcp_exit_code", "mcp_timed_out"],
        "properties": {
            "tool_call_attempted": {"type": "boolean", "const": True},
            "mcp_exit_code": {"type": "integer"},
            "mcp_timed_out": {"type": "boolean"},
        },
    }
    _write_new(
        raw / "response.schema.json",
        (json.dumps(schema, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    suppression = probe_codex_feature_suppression(codex)
    if suppression.get("all_requested_features_disabled") is not True:
        raise CodexMcpBoundarySmokeError("native tool feature suppression contract failed")
    command = build_command(
        codex_executable=codex,
        server_path=server,
        raw_root=raw,
        model=model,
        effort=effort,
    )
    prompt = (
        "This is a non-benchmark tool-boundary canary. Use the only available MCP exec "
        "tool exactly once with command `pwd` and timeout_sec 5. Do not guess whether it "
        "works. Read exit_code and timed_out from the tool result, then return them in "
        "the required JSON. Do not use any other tool."
    )
    try:
        completed = subprocess.run(
            command,
            cwd=raw / "empty-workspace",
            input=prompt,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        _write_new(raw / "codex.jsonl", stdout.encode("utf-8"))
        _write_new(raw / "codex.stderr.txt", stderr.encode("utf-8"))
        raise CodexMcpBoundarySmokeError("Codex boundary canary timed out") from exc
    raw_jsonl = completed.stdout or ""
    stderr = completed.stderr or ""
    if len(raw_jsonl.encode("utf-8")) > MAX_RAW_BYTES:
        raise CodexMcpBoundarySmokeError("Codex JSONL exceeded the raw evidence budget")
    _write_new(raw / "codex.jsonl", raw_jsonl.encode("utf-8"))
    _write_new(raw / "codex.stderr.txt", stderr.encode("utf-8"))
    if completed.returncode != 0:
        raise CodexMcpBoundarySmokeError(
            f"Codex boundary canary exited with {completed.returncode}"
        )
    try:
        summary = parse_codex_exec_jsonl(raw_jsonl)
    except CodexCliAdapterError as exc:
        raise CodexMcpBoundarySmokeError(str(exc)) from exc
    item_counts = _item_type_counts(raw_jsonl)
    forbidden_native = sorted(
        item_type
        for item_type in item_counts
        if item_type in {"command_execution", "file_change", "computer_use"}
    )
    audit = summarize_recorded_audit(raw / "mcp-audit.jsonl")
    last_message = raw / "last-message.json"
    if not last_message.is_file():
        raise CodexMcpBoundarySmokeError("Codex boundary canary returned no last message")
    try:
        response = json.loads(last_message.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CodexMcpBoundarySmokeError("Codex boundary response is invalid") from exc
    safe_tool_results = audit.get("safe_tool_results")
    passed = (
        isinstance(safe_tool_results, list)
        and len(safe_tool_results) == 1
        and response.get("tool_call_attempted") is True
        and response.get("mcp_exit_code") == safe_tool_results[0].get("exit_code")
        and response.get("mcp_timed_out") == safe_tool_results[0].get("timed_out")
        and audit.get("initialize_observed") is True
        and audit.get("tools_list_observed") is True
        and audit.get("exec_tool_call_observed") is True
        and audit.get("exec_tool_call_count") == 1
        and audit.get("observed_tool_counts") == [1]
        and not forbidden_native
    )
    if not passed:
        raise CodexMcpBoundarySmokeError("Codex MCP-only boundary canary failed closed")
    safe = {
        "schema_version": 1,
        "diagnostic": "codex_mcp_only_boundary_canary",
        "status": "passed",
        "requested_model_id": model,
        "requested_effort": effort,
        "provider_reported_model_ids": list(summary.reported_model_ids),
        "model_evidence_level": (
            "provider_reported" if summary.reported_model_ids else "requested_cli_contract_only"
        ),
        "feature_suppression": {
            "requested_count": len(NATIVE_TOOL_FEATURES_TO_DISABLE),
            "observed_disabled_count": len(suppression["observed_disabled_features"]),
            "all_requested_features_disabled": True,
            "features_list_sha256": suppression["features_list_sha256"],
        },
        "runtime_observation": {
            "codex_event_count": summary.event_count,
            "item_type_counts": item_counts,
            "mcp_initialize_observed": True,
            "mcp_tools_list_observed": True,
            "mcp_tool_count": 1,
            "mcp_exec_call_count": 1,
            "mcp_exit_code": safe_tool_results[0]["exit_code"],
            "mcp_timed_out": safe_tool_results[0]["timed_out"],
            "forbidden_native_tool_item_types": forbidden_native,
            "mcp_stdio_launch_mode": codex_mcp_stdio_launch(
                codex_executable=codex,
                server_argv=[str(server)],
            )["mode"],
        },
        "source_hashes": {
            "codex_jsonl_sha256": _sha256((raw / "codex.jsonl").read_bytes()),
            "mcp_audit_sha256": audit["sha256"],
            "response_schema_sha256": _sha256((raw / "response.schema.json").read_bytes()),
            "last_message_sha256": _sha256(last_message.read_bytes()),
            "command_contract_sha256": _sha256(
                json.dumps(command[:-1], ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            ),
        },
        "claim_boundary": {
            "this_is_model_execution": True,
            "this_is_benchmark_execution": False,
            "this_is_task_utility_evidence": False,
            "container_execution_succeeded": False,
            "native_tool_inventory_absence_proven": False,
            "native_tool_execution_observed": False,
            "feature_listing_is_runtime_tool_inventory_proof": False,
            "codex_host_sandbox_enabled": False,
            "external_fixed_container_is_required_boundary": True,
            "raw_arguments_or_tool_output_packaged": False,
            "six_cell_execution_allowed": False,
        },
    }
    safe["diagnostic_sha256"] = content_sha256(safe)
    _write_new(
        destination,
        (json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    return safe


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--codex-executable", type=Path)
    parser.add_argument("--server", type=Path, default=DEFAULT_SERVER)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--effort", default="low", choices=sorted(ALLOWED_EFFORTS))
    parser.add_argument("--timeout-sec", type=float, default=180.0)
    args = parser.parse_args(argv)
    try:
        executable = detect_codex_executable(args.codex_executable)
        report = run_boundary_smoke(
            codex_executable=executable,
            server_path=args.server,
            raw_root=args.raw_root,
            output_path=args.output,
            model=args.model,
            effort=args.effort,
            timeout_s=args.timeout_sec,
        )
    except (CodexMcpBoundarySmokeError, OSError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    print("Merlin Codex MCP-only boundary canary")
    print(f"status={report['status']}")
    print(f"requested_model_id={report['requested_model_id']}")
    print("mcp_exec_call_count=1")
    print("six_cell_execution_allowed=false")
    print(f"saved -> {args.output.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
