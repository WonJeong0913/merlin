"""Codex CLI adapter with conservative actual-invocation semantics.

Codex ``exec --json`` emits provider-native JSONL progress events.  Those
events can prove that a Codex run occurred and can preserve a thread/turn ID,
but they do not, by themselves, prove that a Merlin skill body was loaded.
This adapter therefore never turns tool calls, prompt exposure, or model text
into ``SkillInvocationEvent`` objects.  Its actual-invocation evidence remains
explicitly incomplete until a provider surface exposes that stronger event.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_adapter import AgentContractError, AgentRunRequest, validate_agent_run_request
from .models import AgentRunResult, RawTraceReference


class CodexCliAdapterError(AgentContractError):
    """Raised when Codex CLI output cannot support a trusted adapter result."""


@dataclass(frozen=True, slots=True)
class CodexJsonlSummary:
    """Safe, normalized facts extracted from one Codex ``exec --json`` stream."""

    thread_id: str | None
    turn_id: str | None
    final_message: str | None
    event_types: tuple[str, ...]
    reported_model_ids: tuple[str, ...]
    event_count: int


def _nonempty_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _event_model_ids(event: dict[str, Any]) -> list[str]:
    """Read provider-reported model fields only; never infer from model text."""

    values: list[str] = []
    for candidate in (
        event.get("model"),
        event.get("model_id"),
        event.get("resolved_model"),
    ):
        if value := _nonempty_string(candidate):
            values.append(value)
    return values


def parse_codex_exec_jsonl(raw_text: str) -> CodexJsonlSummary:
    """Strictly parse Codex JSONL while accepting only documented-safe facts.

    The parser intentionally ignores every tool event for skill invocation.
    A tool call, self-report, or ordinary assistant message is not evidence
    that one of Merlin's skill bodies was loaded.
    """

    if not isinstance(raw_text, str) or not raw_text.strip():
        raise CodexCliAdapterError("Codex JSONL output is empty")

    thread_id: str | None = None
    turn_id: str | None = None
    final_message: str | None = None
    event_types: list[str] = []
    reported_model_ids: list[str] = []
    event_count = 0

    for line_number, line in enumerate(raw_text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CodexCliAdapterError(f"malformed Codex JSONL at line {line_number}: {exc.msg}") from exc
        if not isinstance(event, dict):
            raise CodexCliAdapterError(f"Codex JSONL event at line {line_number} must be an object")
        event_type = _nonempty_string(event.get("type"))
        if event_type is None:
            raise CodexCliAdapterError(f"Codex JSONL event at line {line_number} has no non-empty type")
        event_count += 1
        event_types.append(event_type)
        reported_model_ids.extend(_event_model_ids(event))

        if event_type == "thread.started":
            candidate = _nonempty_string(event.get("thread_id"))
            if candidate is None:
                raise CodexCliAdapterError("Codex thread.started event has no thread_id")
            if thread_id is not None and thread_id != candidate:
                raise CodexCliAdapterError("Codex JSONL contains conflicting thread_id values")
            thread_id = candidate
        elif event_type == "turn.started":
            candidate = _nonempty_string(event.get("turn_id"))
            if candidate is not None:
                if turn_id is not None and turn_id != candidate:
                    raise CodexCliAdapterError("Codex JSONL contains conflicting turn_id values")
                turn_id = candidate
        elif event_type == "item.completed":
            item = event.get("item")
            if not isinstance(item, dict):
                raise CodexCliAdapterError("Codex item.completed event has no item object")
            reported_model_ids.extend(_event_model_ids(item))
            if item.get("type") == "agent_message":
                candidate = _nonempty_string(item.get("text"))
                if candidate is not None:
                    final_message = candidate

    if event_count == 0:
        raise CodexCliAdapterError("Codex JSONL output contains no events")
    return CodexJsonlSummary(
        thread_id=thread_id,
        turn_id=turn_id,
        final_message=final_message,
        event_types=tuple(event_types),
        reported_model_ids=tuple(dict.fromkeys(reported_model_ids)),
        event_count=event_count,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_new_text(path: Path, value: str) -> None:
    """Persist captured stdout once without following a pre-existing artifact."""

    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(value)
    except FileExistsError as exc:
        raise CodexCliAdapterError(f"refusing to overwrite Codex raw artifact: {path}") from exc


class CodexCliAdapter:
    """Run one task through a pinned local Codex CLI executable.

    The adapter uses a read-only, ephemeral shell and records raw JSONL under
    the caller's pre-declared ``raw_trace_root``.  It supports answer-only
    smoke tasks; file-writing tasks must be added only after a separate
    sandbox/write-contract audit.
    """

    name = "codex-cli"

    def __init__(
        self,
        *,
        executable: str | Path,
        cli_version: str,
        timeout_s: float = 120.0,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.executable = str(Path(executable).expanduser().resolve())
        self.cli_version = cli_version.strip()
        if not self.cli_version:
            raise ValueError("cli_version must be non-empty")
        self.timeout_s = timeout_s

    def _command(
        self,
        request: AgentRunRequest,
        *,
        last_message_path: Path,
    ) -> tuple[list[str], list[str]]:
        effort = request.contract.effort
        if effort is None:
            raise CodexCliAdapterError("Codex CLI adapter requires an explicit effort in AgentRunContract")
        prompt = request.task.instruction
        command = [
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
            request.contract.model_id,
            "-c",
            f'model_reasoning_effort="{effort}"',
            "--cd",
            str(request.workspace.resolve()),
            "--output-last-message",
            str(last_message_path),
            prompt,
        ]
        redacted_command = ["<prompt-redacted>" if value == prompt else value for value in command]
        return command, redacted_command

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        validate_agent_run_request(request)
        if request.provisioned_skills:
            raise CodexCliAdapterError(
                "Codex CLI adapter cannot claim task-skill provisioning until a provider-native skill mount/load contract is audited"
            )
        workspace = request.workspace.resolve()
        raw_root = Path(request.contract.raw_trace_root).expanduser().resolve()
        raw_root.mkdir(parents=True, exist_ok=True)
        raw_path = raw_root / f"{request.contract.run_id}.codex.jsonl"
        last_message_path = raw_root / f"{request.contract.run_id}.last-message.txt"
        if raw_path.exists() or last_message_path.exists():
            raise CodexCliAdapterError("refusing to overwrite existing Codex raw smoke artifacts")
        command, redacted_command = self._command(request, last_message_path=last_message_path)
        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                text=True,
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            partial_stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            _write_new_text(raw_path, partial_stdout)
            raise CodexCliAdapterError(f"Codex CLI timed out after {self.timeout_s:g}s; raw partial output saved") from exc
        _write_new_text(raw_path, completed.stdout)
        if completed.returncode != 0:
            raise CodexCliAdapterError(
                f"Codex CLI exited with {completed.returncode}; raw JSONL saved and no verifier will run"
            )
        summary = parse_codex_exec_jsonl(completed.stdout)
        if summary.reported_model_ids and request.contract.model_id not in summary.reported_model_ids:
            raise CodexCliAdapterError(
                "Codex provider-reported model does not match AgentRunContract: "
                f"requested={request.contract.model_id!r} reported={list(summary.reported_model_ids)!r}"
            )

        answer = summary.final_message
        if answer is None and last_message_path.is_file():
            answer = last_message_path.read_text(encoding="utf-8").strip()
        if answer is None:
            raise CodexCliAdapterError("Codex JSONL has no agent_message and no output-last-message artifact")
        raw_sha256 = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        last_message_sha256 = (
            hashlib.sha256(last_message_path.read_bytes()).hexdigest() if last_message_path.is_file() else None
        )
        return AgentRunResult(
            contract=request.contract,
            workspace_root=str(workspace),
            raw_trace=RawTraceReference(
                pointer=raw_path.relative_to(raw_root).as_posix(),
                sha256=raw_sha256,
            ),
            # Codex exec events observed here do not express Merlin skill-body
            # loading or provider-native skill invocation.  Do not coerce the
            # empty list into evidence of no invocation.
            actual_invocation_evidence_complete=False,
            selected_skill_ids=[],
            invocation_events=[],
            answer=answer.strip(),
            metadata={
                "provider": "openai-codex-cli",
                "cli_version": self.cli_version,
                "command": redacted_command,
                "timeout_s": self.timeout_s,
                "return_code": completed.returncode,
                "stderr_sha256": _sha256_text(completed.stderr),
                "stderr_bytes": len(completed.stderr.encode("utf-8")),
                "raw_trace_format": "codex-exec-jsonl",
                "raw_event_count": summary.event_count,
                "raw_event_types": list(summary.event_types),
                "thread_id": summary.thread_id,
                "turn_id": summary.turn_id,
                "provider_reported_model_ids": list(summary.reported_model_ids),
                "last_message_sha256": last_message_sha256,
            },
        )
