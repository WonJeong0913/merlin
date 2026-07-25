"""Multi-turn Codex CLI backend for Merlin's chat-based agent beta.

The backend preserves Codex provider JSONL as immutable per-turn artifacts and
resumes the provider thread returned by the first ``codex exec --json`` call.
Prompt-provisioned skill context is deliberately *not* interpreted as
provider-native skill-body invocation evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .codex_adapter import CodexCliAdapterError, CodexJsonlSummary, parse_codex_exec_jsonl
from .harnessx_live_hook import (
    LiveToolPolicy,
    write_new_live_tool_policy,
    write_new_live_tool_policy_from_variant,
)
from .harnessx_runtime import HarnessXVariantSpec


Runner = Callable[..., subprocess.CompletedProcess[str]]
ALLOWED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "ultra"})
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_THREAD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
MAX_PROMPT_CHARS = 50_000
MAX_STDERR_CHARS = 20_000


class CodexChatBackendError(CodexCliAdapterError):
    """Raised when a chat turn cannot safely advance provider state."""


@dataclass(frozen=True, slots=True)
class CodexChatTurnResult:
    turn_number: int
    resumed: bool
    thread_id: str
    turn_id: str | None
    answer: str
    raw_trace_pointer: str
    raw_trace_sha256: str
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class HarnessXLiveHookConfig:
    """Opt-in live Codex hook boundary for exact tool-input enforcement."""

    project_root: Path
    python_executable: Path
    allowed_commands: tuple[str, ...] = ("pwd", "/bin/pwd")
    denied_tools: tuple[str, ...] = ("apply_patch",)
    hook_timeout_s: int = 10
    promoted_variant: HarnessXVariantSpec | None = None


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_new_text(path: Path, value: str) -> None:
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(value)
    except FileExistsError as exc:
        raise CodexChatBackendError(f"refusing to overwrite chat artifact: {path.name}") from exc


def _safe_partial_stdout(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else ""


def _bounded_diagnostic(value: object, *, sensitive_text: str | None = None) -> str:
    text = _safe_partial_stdout(value)
    if sensitive_text:
        text = text.replace(sensitive_text, "<prompt-redacted>")
    if len(text) <= MAX_STDERR_CHARS:
        return text
    return text[:MAX_STDERR_CHARS] + "\n[stderr truncated by Merlin]\n"


class CodexChatBackend:
    """Run Codex CLI turns with an optional explicit model contract."""

    def __init__(
        self,
        *,
        executable: str | Path,
        cli_version: str,
        workspace: str | Path,
        trace_root: str | Path,
        model_id: str | None = None,
        effort: str,
        timeout_s: float = 300.0,
        live_hook_config: HarnessXLiveHookConfig | None = None,
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
        if model_id is not None and (
            not isinstance(model_id, str) or not _MODEL_RE.fullmatch(model_id)
        ):
            raise ValueError("model_id contains unsupported characters")
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
        self._runner = runner
        self.live_hook_config = live_hook_config
        self.live_tool_policy: LiveToolPolicy | None = None
        self.live_hook_audit_path: Path | None = None
        self._live_hook_overrides: tuple[str, ...] = ()
        if live_hook_config is not None:
            self._configure_live_hooks(live_hook_config)

    def _configure_live_hooks(self, config: HarnessXLiveHookConfig) -> None:
        project_root = Path(config.project_root).expanduser().resolve()
        python_executable = Path(config.python_executable).expanduser().resolve()
        if not project_root.is_dir():
            raise ValueError("live hook project_root must be an existing directory")
        if not python_executable.is_file() or not os.access(python_executable, os.X_OK):
            raise ValueError("live hook python_executable must be executable")
        if (
            isinstance(config.hook_timeout_s, bool)
            or not isinstance(config.hook_timeout_s, int)
            or config.hook_timeout_s < 1
            or config.hook_timeout_s > 60
        ):
            raise ValueError("live hook timeout must be an integer from 1 to 60 seconds")

        policy_path = self.trace_root / "harnessx-live-tool-policy.json"
        audit_path = self.trace_root / "harnessx-live-hook-audit.jsonl"
        policy = (
            write_new_live_tool_policy_from_variant(
                policy_path,
                policy_id="promoted-live-variant-v1",
                variant=config.promoted_variant,
                model_id=self.model_id or "provider-default",
            )
            if config.promoted_variant is not None
            else write_new_live_tool_policy(
                policy_path,
                policy_id="read-only-shell-v1",
                allowed_commands=config.allowed_commands,
                denied_tools=config.denied_tools,
                model_id=self.model_id or "provider-default",
            )
        )

        def hook_command(phase: str) -> str:
            return shlex.join(
                [
                    "/usr/bin/env",
                    f"PYTHONPATH={project_root}",
                    str(python_executable),
                    "-m",
                    "src.merlin_harness.harnessx_live_hook",
                    "--phase",
                    phase,
                    "--policy",
                    str(policy_path),
                    "--audit",
                    str(audit_path),
                ]
            )

        matcher = "^(Bash|apply_patch)$"
        pre_hook = (
            "hooks.PreToolUse=[{matcher="
            + json.dumps(matcher)
            + ",hooks=[{type=\"command\",command="
            + json.dumps(hook_command("pre"))
            + f",timeout={config.hook_timeout_s}}}]}}]"
        )
        post_hook = (
            "hooks.PostToolUse=[{matcher="
            + json.dumps(matcher)
            + ",hooks=[{type=\"command\",command="
            + json.dumps(hook_command("post"))
            + f",timeout={config.hook_timeout_s}}}]}}]"
        )
        self.live_tool_policy = policy
        self.live_hook_audit_path = audit_path
        self._live_hook_overrides = (
            "--enable",
            "hooks",
            "--strict-config",
            "--dangerously-bypass-hook-trust",
            "-c",
            pre_hook,
            "-c",
            post_hook,
        )

    def _extend_live_hook_options(self, command: list[str]) -> None:
        command.extend(self._live_hook_overrides)

    def build_first_command(self, *, last_message_path: Path) -> tuple[list[str], list[str]]:
        command = [
            self.executable,
            "exec",
            "--json",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--color",
            "never",
        ]
        self._extend_live_hook_options(command)
        if self.model_id is not None:
            command.extend(["--model", self.model_id])
        command.extend([
            "-c",
            f'model_reasoning_effort="{self.effort}"',
            "--cd",
            str(self.workspace),
            "--output-last-message",
            str(last_message_path),
            "-",
        ])
        return command, [*command[:-1], "<prompt-via-stdin>"]

    def build_resume_command(
        self, *, thread_id: str, last_message_path: Path
    ) -> tuple[list[str], list[str]]:
        if not _THREAD_RE.fullmatch(thread_id):
            raise CodexChatBackendError("provider thread_id has an unsafe format")
        command = [
            self.executable,
            "exec",
            "resume",
            "--json",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
        ]
        self._extend_live_hook_options(command)
        if self.model_id is not None:
            command.extend(["--model", self.model_id])
        command.extend([
            "-c",
            f'model_reasoning_effort="{self.effort}"',
            "--output-last-message",
            str(last_message_path),
            thread_id,
            "-",
        ])
        return command, [*command[:-1], "<prompt-via-stdin>"]

    def _validate_summary(
        self,
        summary: CodexJsonlSummary,
        *,
        requested_thread_id: str | None,
    ) -> str:
        if (
            self.model_id is not None
            and summary.reported_model_ids
            and self.model_id not in summary.reported_model_ids
        ):
            raise CodexChatBackendError(
                "Codex provider-reported model does not match chat contract: "
                f"requested={self.model_id!r} reported={list(summary.reported_model_ids)!r}"
            )
        if requested_thread_id is None:
            if summary.thread_id is None:
                raise CodexChatBackendError("first Codex chat turn returned no provider thread_id")
            return summary.thread_id
        if summary.thread_id is not None and summary.thread_id != requested_thread_id:
            raise CodexChatBackendError("resumed Codex turn returned a conflicting provider thread_id")
        return requested_thread_id

    def run_turn(
        self,
        *,
        prompt: str,
        turn_number: int,
        thread_id: str | None,
    ) -> CodexChatTurnResult:
        if not isinstance(prompt, str) or not prompt.strip():
            raise CodexChatBackendError("chat prompt must be non-empty")
        if "\x00" in prompt or len(prompt) > MAX_PROMPT_CHARS:
            raise CodexChatBackendError(f"chat prompt must be at most {MAX_PROMPT_CHARS} characters and contain no NUL")
        if isinstance(turn_number, bool) or turn_number < 1:
            raise CodexChatBackendError("turn_number must be a positive integer")

        stem = f"turn-{turn_number:04d}"
        raw_path = self.trace_root / f"{stem}.codex.jsonl"
        last_message_path = self.trace_root / f"{stem}.last-message.txt"
        stderr_path = self.trace_root / f"{stem}.stderr.txt"
        if raw_path.exists() or last_message_path.exists() or stderr_path.exists():
            raise CodexChatBackendError(f"refusing to overwrite chat turn {turn_number}")
        resumed = thread_id is not None
        command, redacted_command = (
            self.build_resume_command(thread_id=thread_id, last_message_path=last_message_path)
            if resumed
            else self.build_first_command(last_message_path=last_message_path)
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
                stderr_path,
                _bounded_diagnostic(exc.stderr, sensitive_text=prompt),
            )
            raise CodexChatBackendError(
                f"Codex chat turn timed out after {self.timeout_s:g}s; immutable partial trace "
                f"and diagnostic saved ({stderr_path.name})"
            ) from exc
        _write_new_text(raw_path, completed.stdout)
        if completed.returncode != 0:
            _write_new_text(
                stderr_path,
                _bounded_diagnostic(completed.stderr, sensitive_text=prompt),
            )
            raise CodexChatBackendError(
                f"Codex chat turn exited with {completed.returncode}; provider thread state was not "
                f"advanced; diagnostic saved ({stderr_path.name})"
            )

        try:
            summary = parse_codex_exec_jsonl(completed.stdout)
        except CodexCliAdapterError as exc:
            _write_new_text(
                stderr_path,
                _bounded_diagnostic(completed.stderr, sensitive_text=prompt),
            )
            raise CodexChatBackendError(str(exc)) from exc
        active_thread_id = self._validate_summary(summary, requested_thread_id=thread_id)
        answer = summary.final_message
        if answer is None and last_message_path.is_file():
            answer = last_message_path.read_text(encoding="utf-8").strip()
        if not answer:
            raise CodexChatBackendError("Codex chat turn returned no assistant message")
        raw_bytes = raw_path.read_bytes()
        metadata = {
            "provider": "openai-codex-cli",
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
            "prompt_provisioning_is_provider_native_invocation": False,
            "actual_invocation_evidence_complete": False,
            "harnessx_live_pre_execution_control": self.live_tool_policy is not None,
            "harnessx_live_policy_sha256": (
                self.live_tool_policy.sha256 if self.live_tool_policy is not None else None
            ),
            "harnessx_live_variant_sha256": (
                self.live_tool_policy.variant_sha256
                if self.live_tool_policy is not None
                else None
            ),
            "harnessx_live_policy_pointer": (
                "harnessx-live-tool-policy.json"
                if self.live_tool_policy is not None
                else None
            ),
            "harnessx_live_audit_pointer": (
                self.live_hook_audit_path.name
                if self.live_hook_audit_path is not None
                else None
            ),
            "harnessx_live_enforcement_scope": (
                ["Bash", "apply_patch"] if self.live_tool_policy is not None else []
            ),
        }
        return CodexChatTurnResult(
            turn_number=turn_number,
            resumed=resumed,
            thread_id=active_thread_id,
            turn_id=summary.turn_id,
            answer=answer.strip(),
            raw_trace_pointer=raw_path.name,
            raw_trace_sha256=_sha256_bytes(raw_bytes),
            metadata=metadata,
        )
