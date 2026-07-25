"""Claude Code CLI chat backend, mirroring the Codex backend's contract.

Same discipline as `codex_chat`: the prompt travels on stdin and never enters
turn metadata, raw stdout is written once to an immutable per-turn artifact, a
timeout still preserves the partial trace, and a non-zero exit refuses to claim
the provider thread advanced.

The reason this exists alongside the Codex backend is not model choice. Claude
Code reports two things Codex does not: the skills it exposed for a run
(`system/init.skills`) and the skills the model invoked (`Skill` tool calls).
Those are provider-side accounts of provisioning and invocation, so an arm run
through this backend can carry invocation evidence the Codex path cannot
produce. This module surfaces them; it does not decide what they are worth.

Authentication is the CLI's own. Merlin never reads or stores a token: on macOS
the Claude credentials live in the keychain, and a run with an account session
reports `apiKeySource: "none"`.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .claude_adapter import (
    ClaudeCliAdapterError,
    ClaudeStreamSummary,
    parse_claude_stream_jsonl,
)

Runner = Callable[..., subprocess.CompletedProcess[str]]

ALLOWED_EFFORTS = frozenset({"low", "medium", "high", "max"})
MAX_PROMPT_CHARS = 200_000
MAX_DIAGNOSTIC_CHARS = 2_000
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SESSION_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")

# `--model sonnet` resolves to whatever the install treats as current, which is
# not necessarily the model the operator meant. Callers pass explicit IDs so the
# contract check below compares like with like.
MODEL_ALIASES = frozenset({"sonnet", "opus", "haiku", "default"})


class ClaudeChatBackendError(RuntimeError):
    """Raised when a Claude chat turn cannot produce a trusted result."""


@dataclass(frozen=True, slots=True)
class ClaudeChatTurnResult:
    turn_number: int
    resumed: bool
    session_id: str
    answer: str
    raw_trace_pointer: str
    raw_trace_sha256: str
    exposed_skills: tuple[str, ...]
    invoked_skills: tuple[str, ...]
    metadata: dict[str, Any]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_new_text(path: Path, value: str) -> None:
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(value)
    except FileExistsError as exc:
        raise ClaudeChatBackendError(f"refusing to overwrite Claude artifact: {path}") from exc


def _bounded_diagnostic(value: str | None, *, sensitive_text: str) -> str:
    text = value or ""
    if sensitive_text and sensitive_text in text:
        text = text.replace(sensitive_text, "<prompt-redacted>")
    text = " ".join(text.split())
    if len(text) > MAX_DIAGNOSTIC_CHARS:
        text = text[: MAX_DIAGNOSTIC_CHARS - 1].rstrip() + "…"
    return text


def _safe_partial_stdout(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def detect_claude_runtime(executable: str | None = None) -> tuple[Path, str]:
    """Resolve the Claude CLI and its version without touching credentials."""

    candidate = executable or "claude"
    try:
        resolved = subprocess.run(
            [candidate, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ClaudeChatBackendError("Claude CLI is not available") from exc
    if resolved.returncode != 0:
        raise ClaudeChatBackendError("Claude CLI is not available")
    version = resolved.stdout.strip()
    if not version:
        raise ClaudeChatBackendError("Claude CLI reported no version")
    path = Path(candidate)
    if path.is_absolute():
        return path, version
    from shutil import which

    located = which(candidate)
    if located is None:
        raise ClaudeChatBackendError("Claude CLI is not on PATH")
    return Path(located), version


class ClaudeChatBackend:
    """One workspace-scoped Claude Code chat thread."""

    def __init__(
        self,
        *,
        executable: str | Path,
        cli_version: str,
        workspace: str | Path,
        trace_root: str | Path,
        model_id: str | None = None,
        effort: str = "high",
        timeout_s: float = 300.0,
        allowed_tools: tuple[str, ...] | None = None,
        skills_root: str | Path | None = None,
        runner: Runner = subprocess.run,
    ) -> None:
        workspace_path = Path(workspace).expanduser().resolve()
        if not workspace_path.is_dir():
            raise ValueError("workspace must be an existing directory")
        trace_path = Path(trace_root).expanduser().resolve()
        if not trace_path.is_relative_to(workspace_path):
            raise ValueError("trace_root must stay inside workspace")
        if trace_path.exists() and not trace_path.is_dir():
            raise ValueError("trace_root must be a directory")
        if model_id is not None:
            if not isinstance(model_id, str) or not _MODEL_RE.fullmatch(model_id):
                raise ValueError("model_id contains unsupported characters")
            if model_id in MODEL_ALIASES:
                raise ValueError(
                    "model_id must be an explicit model ID, not an alias; "
                    "an alias resolves to whatever the install considers current"
                )
        if effort not in ALLOWED_EFFORTS:
            raise ValueError(f"effort must be one of: {', '.join(sorted(ALLOWED_EFFORTS))}")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        version = cli_version.strip()
        if not version:
            raise ValueError("cli_version must be non-empty")

        self.executable = str(Path(executable).expanduser().resolve())
        self.cli_version = version
        self.workspace = workspace_path
        self.trace_root = trace_path
        self.trace_root.mkdir(parents=True, exist_ok=False)
        self.model_id = model_id
        self.effort = effort
        self.timeout_s = timeout_s
        self.allowed_tools = allowed_tools
        self.skills_root = (
            Path(skills_root).expanduser().resolve() if skills_root is not None else None
        )
        self._runner = runner

    def _base_command(self) -> list[str]:
        command = [
            self.executable,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if self.model_id is not None:
            command.extend(["--model", self.model_id])
        command.extend(["--effort", self.effort])
        if self.allowed_tools is not None:
            command.extend(["--allowed-tools", ",".join(self.allowed_tools)])
        return command

    def build_first_command(self) -> tuple[list[str], list[str]]:
        command = self._base_command()
        return command, [*command, "<prompt-via-stdin>"]

    def build_resume_command(self, *, session_id: str) -> tuple[list[str], list[str]]:
        if not _SESSION_RE.fullmatch(session_id):
            raise ClaudeChatBackendError("provider session_id has an unsafe format")
        command = [*self._base_command(), "--resume", session_id]
        return command, [*command, "<prompt-via-stdin>"]

    def _validate_summary(
        self, summary: ClaudeStreamSummary, *, requested_session_id: str | None
    ) -> str:
        if (
            self.model_id is not None
            and summary.reported_model_ids
            and self.model_id not in summary.reported_model_ids
        ):
            raise ClaudeChatBackendError(
                "Claude provider-reported model does not match chat contract: "
                f"requested={self.model_id!r} reported={list(summary.reported_model_ids)!r}"
            )
        if summary.is_error:
            raise ClaudeChatBackendError("Claude reported an error result for this turn")
        if requested_session_id is None:
            if summary.session_id is None:
                raise ClaudeChatBackendError("first Claude turn returned no provider session_id")
            return summary.session_id
        if summary.session_id is not None and summary.session_id != requested_session_id:
            # `--resume` may fork to a new session; that is a different thread,
            # so the caller is told rather than silently rebound.
            raise ClaudeChatBackendError(
                "resumed Claude turn returned a conflicting provider session_id"
            )
        return requested_session_id

    def run_turn(
        self,
        *,
        prompt: str,
        turn_number: int,
        session_id: str | None = None,
        thread_id: str | None = None,
    ) -> ClaudeChatTurnResult:
        # `thread_id` is accepted so this backend can stand in for the Codex one
        # wherever the caller speaks the older parameter name.
        session_id = session_id if session_id is not None else thread_id
        if not isinstance(prompt, str) or not prompt.strip():
            raise ClaudeChatBackendError("chat prompt must be non-empty")
        if "\x00" in prompt or len(prompt) > MAX_PROMPT_CHARS:
            raise ClaudeChatBackendError(
                f"chat prompt must be at most {MAX_PROMPT_CHARS} characters and contain no NUL"
            )
        if isinstance(turn_number, bool) or turn_number < 1:
            raise ClaudeChatBackendError("turn_number must be a positive integer")

        stem = f"turn-{turn_number:04d}"
        raw_path = self.trace_root / f"{stem}.claude.jsonl"
        stderr_path = self.trace_root / f"{stem}.stderr.txt"
        if raw_path.exists() or stderr_path.exists():
            raise ClaudeChatBackendError(f"refusing to overwrite chat turn {turn_number}")

        resumed = session_id is not None
        command, redacted_command = (
            self.build_resume_command(session_id=session_id)
            if resumed
            else self.build_first_command()
        )
        try:
            completed = self._runner(
                command,
                cwd=self.workspace,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            _write_new_text(raw_path, _safe_partial_stdout(exc.stdout))
            _write_new_text(
                stderr_path, _bounded_diagnostic(_safe_partial_stdout(exc.stderr), sensitive_text=prompt)
            )
            raise ClaudeChatBackendError(
                f"Claude chat turn timed out after {self.timeout_s:g}s; immutable partial trace "
                f"and diagnostic saved ({stderr_path.name})"
            ) from exc

        _write_new_text(raw_path, completed.stdout)
        if completed.returncode != 0:
            _write_new_text(
                stderr_path, _bounded_diagnostic(completed.stderr, sensitive_text=prompt)
            )
            raise ClaudeChatBackendError(
                f"Claude chat turn exited with {completed.returncode}; provider thread state was "
                f"not advanced; diagnostic saved ({stderr_path.name})"
            )

        try:
            summary = parse_claude_stream_jsonl(completed.stdout)
        except ClaudeCliAdapterError as exc:
            _write_new_text(
                stderr_path, _bounded_diagnostic(completed.stderr, sensitive_text=prompt)
            )
            raise ClaudeChatBackendError(str(exc)) from exc

        active_session_id = self._validate_summary(summary, requested_session_id=session_id)
        answer = summary.final_message
        if not answer:
            raise ClaudeChatBackendError("Claude chat turn returned no assistant message")
        raw_bytes = raw_path.read_bytes()
        metadata = {
            "provider": "anthropic-claude-code-cli",
            "cli_version": self.cli_version,
            "model_id": self.model_id,
            "effort": self.effort,
            "command": redacted_command,
            "prompt_sha256": _sha256_bytes(prompt.encode("utf-8")),
            "prompt_chars": len(prompt),
            "prompt_storage": "stdin_only_not_stored_in_turn_metadata",
            "timeout_s": self.timeout_s,
            "return_code": completed.returncode,
            "stderr_sha256": _sha256_bytes(completed.stderr.encode("utf-8")),
            "stderr_bytes": len(completed.stderr.encode("utf-8")),
            "event_count": summary.event_count,
            "event_types": list(summary.event_types),
            "provider_reported_model_ids": list(summary.reported_model_ids),
            # Provider-side accounts, kept apart from anything the harness claims.
            "provider_exposed_skills": list(summary.exposed_skills),
            "provider_invoked_skills": list(summary.invoked_skill_names),
            "provider_skill_tool_call_count": len(summary.skill_tool_calls),
            # Exposure is provisioning, not invocation. Only a `Skill` tool call
            # is the provider saying the model actually invoked one, and even
            # that names a skill rather than pinning a body hash.
            "prompt_provisioning_is_provider_native_invocation": False,
            "provider_native_skill_invocation_observed": bool(summary.skill_tool_calls),
            "provider_native_invocation_is_name_level_not_body_level": True,
        }
        return ClaudeChatTurnResult(
            turn_number=turn_number,
            resumed=resumed,
            session_id=active_session_id,
            answer=answer.strip(),
            raw_trace_pointer=raw_path.name,
            raw_trace_sha256=_sha256_bytes(raw_bytes),
            exposed_skills=summary.exposed_skills,
            invoked_skills=summary.invoked_skill_names,
            metadata=metadata,
        )
