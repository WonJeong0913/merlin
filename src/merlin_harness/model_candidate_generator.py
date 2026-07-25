"""Account-authenticated Codex generator for inert skill candidates.

The generator is deliberately narrower than a general coding agent.  It runs
in an empty read-only workspace, requires a strict JSON schema, rejects any
provider item that indicates tool execution, and persists immutable raw JSONL
before the response can enter Merlin's quarantine boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .codex_adapter import CodexCliAdapterError, parse_codex_exec_jsonl
from .model_candidate_quarantine import (
    MAX_CANDIDATE_FILES,
    MAX_FILE_BYTES,
    ModelCandidateEnvelope,
    ModelCandidateQuarantineError,
    parse_model_candidate_response,
)


Runner = Callable[..., subprocess.CompletedProcess[str]]
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "ultra"})
MAX_GENERATOR_PROMPT_CHARS = 60_000
MAX_GENERATOR_STDERR_CHARS = 20_000
ALLOWED_NON_TOOL_ITEM_TYPES = frozenset({"agent_message", "reasoning"})


class ModelCandidateGeneratorError(ValueError):
    """Raised when a provider run cannot support model-authorship evidence."""


@dataclass(frozen=True, slots=True)
class ModelCandidateGenerationResult:
    envelope: ModelCandidateEnvelope
    requested_model_id: str
    provider_reported_model_ids: tuple[str, ...]
    model_evidence_level: str
    effort: str
    cli_version: str
    thread_id: str | None
    turn_id: str | None
    raw_trace_pointer: str
    raw_trace_sha256: str
    response_sha256: str
    schema_sha256: str
    prompt_sha256: str
    event_count: int
    item_types: tuple[str, ...]

    def to_safe_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("envelope")
        value["schema_version"] = 1
        value["candidate_skill_id"] = self.envelope.candidate_skill_id
        value["candidate_file_count"] = len(self.envelope.files)
        value["candidate_response_bytes"] = sum(
            len(item.content.encode("utf-8")) for item in self.envelope.files
        )
        value["evidence_boundary"] = {
            "provider_run_observed": True,
            "requested_model_is_resolved_model": bool(self.provider_reported_model_ids),
            "provider_tool_execution_observed": False,
            "candidate_quarantined": False,
            "candidate_executed": False,
            "candidate_promoted": False,
        }
        return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _bounded_diagnostic(value: object, *, prompt: str) -> str:
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    elif isinstance(value, str):
        text = value
    else:
        text = ""
    text = text.replace(prompt, "<prompt-redacted>")
    if len(text) > MAX_GENERATOR_STDERR_CHARS:
        return text[:MAX_GENERATOR_STDERR_CHARS] + "\n[stderr truncated by Merlin]\n"
    return text


def _write_new_text(path: Path, value: str) -> None:
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(value)
    except FileExistsError as exc:
        raise ModelCandidateGeneratorError(f"refusing to overwrite generator artifact: {path.name}") from exc


def _response_schema(candidate_skill_id: str) -> dict[str, Any]:
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "additionalProperties": False,
        "required": ["candidate_skill_id", "files"],
        "properties": {
            "candidate_skill_id": {"type": "string", "const": candidate_skill_id},
            "files": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_CANDIDATE_FILES,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path", "content"],
                    "properties": {
                        "path": {"type": "string", "minLength": 1, "maxLength": 256},
                        "content": {
                            "type": "string",
                            "maxLength": MAX_FILE_BYTES,
                        },
                    },
                },
            },
        },
    }


def _provider_item_types(raw_jsonl: str) -> tuple[str, ...]:
    item_types: list[str] = []
    for line_number, line in enumerate(raw_jsonl.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ModelCandidateGeneratorError(
                f"malformed generator JSONL at line {line_number}"
            ) from exc
        if not isinstance(event, dict):
            raise ModelCandidateGeneratorError(
                f"generator JSONL event at line {line_number} is not an object"
            )
        if event.get("type") not in {"item.started", "item.completed"}:
            continue
        item = event.get("item")
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise ModelCandidateGeneratorError(
                f"generator item event at line {line_number} has no typed item"
            )
        item_types.append(item["type"])
    unexpected = sorted(set(item_types) - ALLOWED_NON_TOOL_ITEM_TYPES)
    if unexpected:
        raise ModelCandidateGeneratorError(
            "generator used a provider tool or unsupported item type: " + ", ".join(unexpected)
        )
    return tuple(item_types)


class CodexModelCandidateGenerator:
    """Generate one strict, inert candidate through the account-auth Codex CLI."""

    def __init__(
        self,
        *,
        executable: str | Path,
        cli_version: str,
        model_id: str = "gpt-5.6-terra",
        effort: str = "high",
        timeout_s: float = 300.0,
        runner: Runner = subprocess.run,
    ) -> None:
        executable_path = Path(executable).expanduser().resolve()
        if not executable_path.is_file():
            raise ValueError("Codex executable must be an existing file")
        if not MODEL_RE.fullmatch(model_id):
            raise ValueError("model_id contains unsupported characters")
        if effort not in ALLOWED_EFFORTS:
            raise ValueError(f"effort must be one of: {', '.join(sorted(ALLOWED_EFFORTS))}")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        if not cli_version.strip():
            raise ValueError("cli_version must be non-empty")
        self.executable = str(executable_path)
        self.cli_version = cli_version.strip()
        self.model_id = model_id
        self.effort = effort
        self.timeout_s = timeout_s
        self._runner = runner

    def _command(
        self,
        *,
        workspace: Path,
        schema_path: Path,
        last_message_path: Path,
    ) -> list[str]:
        return [
            self.executable,
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--model",
            self.model_id,
            "-c",
            f'model_reasoning_effort="{self.effort}"',
            "--output-schema",
            str(schema_path),
            "--cd",
            str(workspace),
            "--output-last-message",
            str(last_message_path),
            "-",
        ]

    def generate(
        self,
        *,
        candidate_skill_id: str,
        prompt: str,
        run_root: Path,
    ) -> ModelCandidateGenerationResult:
        if not SAFE_ID_RE.fullmatch(candidate_skill_id):
            raise ModelCandidateGeneratorError("candidate skill ID must be portable kebab-case")
        if (
            not isinstance(prompt, str)
            or not prompt.strip()
            or "\x00" in prompt
            or len(prompt) > MAX_GENERATOR_PROMPT_CHARS
        ):
            raise ModelCandidateGeneratorError(
                f"generator prompt must be non-empty and at most {MAX_GENERATOR_PROMPT_CHARS} characters"
            )
        run_root = run_root.expanduser().resolve(strict=False)
        if run_root.exists():
            raise ModelCandidateGeneratorError(f"refusing to overwrite generator run: {run_root}")
        workspace = run_root / "empty-workspace"
        workspace.mkdir(parents=True)
        schema_path = run_root / "candidate-response.schema.json"
        raw_path = run_root / "provider.codex.jsonl"
        last_message_path = run_root / "provider.last-message.json"
        stderr_path = run_root / "provider.stderr.txt"
        report_path = run_root / "generation_report.json"
        schema_text = json.dumps(
            _response_schema(candidate_skill_id), ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
        _write_new_text(schema_path, schema_text)
        prompt_sha256 = _sha256_bytes(prompt.encode("utf-8"))
        command = self._command(
            workspace=workspace,
            schema_path=schema_path,
            last_message_path=last_message_path,
        )
        try:
            completed = self._runner(
                command,
                cwd=workspace,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            _write_new_text(raw_path, partial)
            _write_new_text(stderr_path, _bounded_diagnostic(exc.stderr, prompt=prompt))
            raise ModelCandidateGeneratorError(
                f"Codex candidate generation timed out after {self.timeout_s:g}s"
            ) from exc
        _write_new_text(raw_path, completed.stdout)
        if completed.returncode != 0:
            _write_new_text(stderr_path, _bounded_diagnostic(completed.stderr, prompt=prompt))
            raise ModelCandidateGeneratorError(
                f"Codex candidate generation exited with {completed.returncode}; raw evidence retained"
            )
        try:
            summary = parse_codex_exec_jsonl(completed.stdout)
            item_types = _provider_item_types(completed.stdout)
        except (CodexCliAdapterError, ModelCandidateGeneratorError) as exc:
            _write_new_text(stderr_path, _bounded_diagnostic(completed.stderr, prompt=prompt))
            raise ModelCandidateGeneratorError(str(exc)) from exc
        if summary.reported_model_ids and self.model_id not in summary.reported_model_ids:
            raise ModelCandidateGeneratorError(
                "provider-reported generator model does not match requested model: "
                f"requested={self.model_id!r} reported={list(summary.reported_model_ids)!r}"
            )
        raw_response = summary.final_message
        if raw_response is None and last_message_path.is_file():
            raw_response = last_message_path.read_text(encoding="utf-8")
        if not raw_response:
            raise ModelCandidateGeneratorError("Codex generator returned no final candidate response")
        try:
            envelope = parse_model_candidate_response(
                raw_response=raw_response.strip(),
                generator_backend="openai-codex-cli",
                generator_model=self.model_id,
                generator_effort=self.effort,
                generator_prompt_sha256=prompt_sha256,
                generator_provider_reported_model_ids=summary.reported_model_ids,
                generator_cli_version=self.cli_version,
                generator_raw_trace_sha256=_sha256_bytes(raw_path.read_bytes()),
                generator_thread_id=summary.thread_id,
                generator_turn_id=summary.turn_id,
            )
        except ModelCandidateQuarantineError as exc:
            raise ModelCandidateGeneratorError(str(exc)) from exc
        result = ModelCandidateGenerationResult(
            envelope=envelope,
            requested_model_id=self.model_id,
            provider_reported_model_ids=summary.reported_model_ids,
            model_evidence_level=(
                "provider_reported" if summary.reported_model_ids else "requested_cli_contract_only"
            ),
            effort=self.effort,
            cli_version=self.cli_version,
            thread_id=summary.thread_id,
            turn_id=summary.turn_id,
            raw_trace_pointer=raw_path.name,
            raw_trace_sha256=_sha256_bytes(raw_path.read_bytes()),
            response_sha256=envelope.generator_response_sha256,
            schema_sha256=_sha256_bytes(schema_text.encode("utf-8")),
            prompt_sha256=prompt_sha256,
            event_count=summary.event_count,
            item_types=item_types,
        )
        _write_new_text(
            report_path,
            json.dumps(result.to_safe_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        return result
