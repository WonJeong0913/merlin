"""Executor contracts for deterministic and model-backed task attempts."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .models import SkillArtifact, TaskSpec
from .provider_runtime import (
    OpenAICompatibleChatCompletionsClient,
    OpenAICompatibleProviderConfig,
    ProviderPricing,
)


@dataclass(slots=True)
class ExecutionRequest:
    task: TaskSpec
    workspace: Path
    condition: str
    provisioned_skills: list[SkillArtifact] = field(default_factory=list)
    selected_skill: SkillArtifact | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionResult:
    answer: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskExecutor(Protocol):
    name: str

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Attempt a task inside an already materialized workspace."""


class ResponseClient(Protocol):
    def create_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a model response and return the decoded provider payload."""


class NoSkillExecutor:
    name = "no_skill"

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(metadata={"executor": self.name})


class RecipeSkillExecutor:
    name = "recipe_skill"

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        skill = request.selected_skill
        if skill is None:
            return ExecutionResult(metadata={"executor": self.name, "selected_skill": None})

        recipe = skill.metadata.get("solves", {}).get(request.task.id)
        if recipe is None:
            return ExecutionResult(metadata={"executor": self.name, "selected_skill": skill.id})

        events: list[dict[str, Any]] = []
        answer: str | None = None
        if "answer" in recipe:
            answer = recipe["answer"]
            events.append({"type": "TOOL", "action": "answer", "skill": skill.id})
        if "write_file" in recipe:
            spec = recipe["write_file"]
            (request.workspace / spec["path"]).write_text(spec.get("content", ""), encoding="utf-8")
            events.append({"type": "WRITE", "path": spec["path"], "skill": skill.id})
        if "count_nonempty_lines" in recipe:
            spec = recipe["count_nonempty_lines"]
            source = request.workspace / spec["input"]
            count = sum(1 for line in source.read_text(encoding="utf-8").splitlines() if line.strip())
            (request.workspace / spec["output"]).write_text(f"{count}\n", encoding="utf-8")
            events.append({"type": "WRITE", "path": spec["output"], "skill": skill.id})

        return ExecutionResult(
            answer=answer,
            events=events,
            metadata={"executor": self.name, "selected_skill": skill.id},
        )


@dataclass(slots=True)
class ApiModelConfig:
    model: str
    provider: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    protocol: str = "responses"
    api_key_env: str | None = None
    timeout_s: float = 120.0
    max_output_tokens: int = 2048
    max_workspace_chars: int = 12000
    max_skill_chars: int = 8000
    pricing: ProviderPricing | None = None
    max_request_cost_usd: float | None = None
    allow_local_http: bool = False
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CliModelConfig:
    command: list[str]
    backend_name: str
    model: str | None = None
    effort: str | None = None
    runtime_effort: str | None = None
    auth_mode: str = "account"
    prompt_mode: str = "stdin"
    timeout_s: float = 300.0
    max_workspace_chars: int = 12000
    max_skill_chars: int = 8000
    env: dict[str, str] = field(default_factory=dict)


class OpenAIResponsesClient:
    """Small standard-library client for OpenAI Responses API calls."""

    def __init__(self, *, api_key: str | None = None, base_url: str = "https://api.openai.com/v1", timeout_s: float = 120.0) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def create_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required for OpenAIResponsesClient.")

        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/responses",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI Responses API request failed: HTTP {exc.code}: {detail}") from exc


def _extract_response_text(response: dict[str, Any]) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str):
        return output_text

    parts: list[str] = []
    for item in response.get("output", []) if isinstance(response.get("output"), list) else []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).strip()


def _safe_workspace_target(workspace: Path, relative_path: str) -> Path:
    target = (workspace / relative_path).resolve()
    workspace_root = workspace.resolve()
    try:
        target.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(f"Model attempted to write outside workspace: {relative_path}") from exc
    return target


_BLOCKED_WORKSPACE_PARTS = frozenset(
    {
        ".aws",
        ".git",
        ".gnupg",
        ".ssh",
        "__pycache__",
        "node_modules",
    }
)
_BLOCKED_WORKSPACE_NAMES = frozenset(
    {
        ".env",
        ".netrc",
        "credentials",
        "credentials.json",
        "id_dsa",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
    }
)
_BLOCKED_WORKSPACE_SUFFIXES = frozenset(
    {
        ".der",
        ".key",
        ".p12",
        ".pem",
        ".pfx",
    }
)


def _workspace_snapshot_path_is_safe(path: Path, workspace: Path) -> bool:
    if path.is_symlink():
        return False
    try:
        relative = path.resolve().relative_to(workspace.resolve())
    except (OSError, ValueError):
        return False
    lower_parts = tuple(part.lower() for part in relative.parts)
    if any(part in _BLOCKED_WORKSPACE_PARTS for part in lower_parts):
        return False
    name = relative.name.lower()
    if name in _BLOCKED_WORKSPACE_NAMES or name.startswith(".env."):
        return False
    if path.suffix.lower() in _BLOCKED_WORKSPACE_SUFFIXES:
        return False
    return True


def _read_workspace_snapshot(workspace: Path, max_chars: int) -> list[dict[str, str]]:
    remaining = max_chars
    files: list[dict[str, str]] = []
    for path in sorted(item for item in workspace.rglob("*") if item.is_file()):
        if remaining <= 0:
            break
        if not _workspace_snapshot_path_is_safe(path, workspace):
            continue
        relative = path.relative_to(workspace).as_posix()
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = "<binary file omitted>"
        snippet = content[:remaining]
        remaining -= len(snippet)
        files.append({"path": relative, "content": snippet})
    return files


def _skill_snapshot(skills: list[SkillArtifact], max_chars: int) -> list[dict[str, Any]]:
    remaining = max_chars
    snapshot: list[dict[str, Any]] = []
    for skill in skills:
        if remaining <= 0:
            break
        steps = [step.description for step in skill.steps]
        item = {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "trigger": skill.trigger,
            "status": skill.status.value,
            "steps": steps,
        }
        encoded = json.dumps(item, ensure_ascii=False)
        remaining -= len(encoded)
        snapshot.append(item)
    return snapshot


def _build_model_prompt(
    *,
    request: ExecutionRequest,
    max_workspace_chars: int,
    max_skill_chars: int,
) -> str:
    workspace_files = _read_workspace_snapshot(request.workspace, max_workspace_chars)
    skill_context = _skill_snapshot(request.provisioned_skills, max_skill_chars)
    selected_skill = request.selected_skill.id if request.selected_skill else None
    payload = {
        "task_id": request.task.id,
        "condition": request.condition,
        "instruction": request.task.instruction,
        "selected_skill_id": selected_skill,
        "provisioned_skills": skill_context,
        "workspace_files": workspace_files,
        "response_contract": {
            "answer": "string or null",
            "files": [{"path": "relative/path", "content": "file content"}],
        },
    }
    return (
        "You are the execution model for a benchmark task. "
        "Use the selected/provisioned skills only when they are relevant. "
        "Return strict JSON only. Do not include markdown fences. "
        "For file-writing tasks, include files with workspace-relative paths.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _parse_model_json_or_answer(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return {"answer": stripped, "files": []}
    if not isinstance(parsed, dict):
        return {"answer": str(parsed), "files": []}
    parsed.setdefault("files", [])
    return parsed


def _apply_model_result_actions(
    *,
    parsed: dict[str, Any],
    request: ExecutionRequest,
    events: list[dict[str, Any]],
) -> str | None:
    for file_spec in parsed.get("files", []):
        if not isinstance(file_spec, dict):
            continue
        relative_path = file_spec.get("path")
        content = file_spec.get("content", "")
        if not isinstance(relative_path, str) or not isinstance(content, str):
            continue
        target = _safe_workspace_target(request.workspace, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        events.append({"type": "WRITE", "path": relative_path, "skill": request.selected_skill.id if request.selected_skill else None})

    answer = parsed.get("answer")
    if answer is not None and not isinstance(answer, str):
        answer = str(answer)
    if answer is not None:
        events.append({"type": "ANSWER", "answer": answer})
    return answer


def _extract_cli_model_text_and_metadata(stdout: str) -> tuple[str, dict[str, Any]]:
    stripped = stdout.strip()
    try:
        wrapper = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped, {}
    if not isinstance(wrapper, dict):
        return stripped, {}
    for key in ("result", "output_text", "text", "message"):
        value = wrapper.get(key)
        if isinstance(value, str):
            metadata = {k: v for k, v in wrapper.items() if k != key}
            return value.strip(), metadata
    return stripped, wrapper


class CliModelExecutor:
    """Account-auth CLI executor for Codex/Claude/GLM-style benchmark runs."""

    name = "cli_model"

    def __init__(self, config: CliModelConfig) -> None:
        if not config.command:
            raise ValueError("CliModelConfig.command must not be empty.")
        if config.prompt_mode not in {"stdin", "arg", "file"}:
            raise ValueError(f"Unsupported prompt_mode: {config.prompt_mode}")
        self.config = config

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        prompt = _build_model_prompt(
            request=request,
            max_workspace_chars=self.config.max_workspace_chars,
            max_skill_chars=self.config.max_skill_chars,
        )
        command = list(self.config.command)
        stdin: str | None = None
        temp_path: str | None = None
        if self.config.prompt_mode == "stdin":
            stdin = prompt
        elif self.config.prompt_mode == "arg":
            command.append(prompt)
        else:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".prompt.txt", delete=False) as handle:
                handle.write(prompt)
                temp_path = handle.name
            command = [part.replace("{prompt_file}", temp_path) for part in command]

        env = os.environ.copy()
        env.update(self.config.env)
        try:
            completed = subprocess.run(
                command,
                input=stdin,
                cwd=request.workspace,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_s,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"CLI backend timed out after {exc.timeout}s: {self.config.backend_name}") from exc
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink()
                except FileNotFoundError:
                    pass

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode != 0:
            raise RuntimeError(
                f"CLI backend failed ({self.config.backend_name}, exit={completed.returncode}): "
                f"{stderr[-2000:] or stdout[-2000:]}"
            )

        model_text, cli_metadata = _extract_cli_model_text_and_metadata(stdout)
        parsed = _parse_model_json_or_answer(model_text)
        events: list[dict[str, Any]] = [
            {
                "type": "MODEL_CALL",
                "provider": self.config.backend_name,
                "model": self.config.model,
                "effort": self.config.effort,
                "runtime_effort": self.config.runtime_effort,
                "auth_mode": self.config.auth_mode,
                "command": command[:4],
            }
        ]
        answer = _apply_model_result_actions(parsed=parsed, request=request, events=events)
        return ExecutionResult(
            answer=answer,
            events=events,
            metadata={
                "executor": self.name,
                "provider": self.config.backend_name,
                "model": self.config.model,
                "effort": self.config.effort,
                "runtime_effort": self.config.runtime_effort,
                "auth_mode": self.config.auth_mode,
                "returncode": completed.returncode,
                "cli_metadata": cli_metadata,
                "stderr_preview": stderr[-1000:],
                "raw_output_preview": model_text[:1000],
            },
        )


def make_claude_cli_executor(*, model: str = "sonnet", effort: str = "high", timeout_s: float = 300.0) -> CliModelExecutor:
    return CliModelExecutor(
        CliModelConfig(
            command=[
                "claude",
                "-p",
                "--output-format",
                "json",
                "--no-session-persistence",
                "--tools",
                "",
                "--model",
                model,
                "--effort",
                effort,
            ],
            backend_name="claude-code",
            model=model,
            effort=effort,
            runtime_effort=effort,
            auth_mode="account",
            prompt_mode="stdin",
            timeout_s=timeout_s,
        )
    )


def make_codex_cli_executor(*, model: str | None = None, effort: str = "high", timeout_s: float = 600.0) -> CliModelExecutor:
    command = [
        "codex",
        "exec",
        "-c",
        f'model_reasoning_effort="{effort}"',
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--ignore-rules",
        "--ephemeral",
    ]
    if model and model != "default":
        command.extend(["--model", model])
    command.append("-")
    return CliModelExecutor(
        CliModelConfig(
            command=command,
            backend_name="codex-cli",
            model=model or "default",
            effort=effort,
            runtime_effort=effort,
            auth_mode="account",
            prompt_mode="stdin",
            timeout_s=timeout_s,
        )
    )


class ApiModelExecutor:
    """Model-backed executor for E2+ runs.

    The first API executor intentionally keeps the action surface narrow: the
    model returns JSON with an optional scalar answer and optional file writes.
    Rich terminal/tool agents can be added later without changing the runner
    contract.
    """

    name = "api_model"

    def __init__(
        self,
        *,
        model: str,
        provider: str = "openai",
        client: ResponseClient | None = None,
        config: ApiModelConfig | None = None,
    ) -> None:
        self.config = config or ApiModelConfig(model=model, provider=provider)
        self.model = self.config.model
        self.provider = self.config.provider
        if client is not None:
            self.client = client
        elif self.config.protocol == "responses" and self.provider == "openai":
            self.client = OpenAIResponsesClient(base_url=self.config.base_url, timeout_s=self.config.timeout_s)
        elif self.config.protocol == "chat_completions":
            self.client = OpenAICompatibleChatCompletionsClient(
                OpenAICompatibleProviderConfig(
                    provider_id=self.provider,
                    model=self.model,
                    base_url=self.config.base_url,
                    api_key_env=self.config.api_key_env,
                    timeout_s=self.config.timeout_s,
                    max_output_tokens=self.config.max_output_tokens,
                    pricing=self.config.pricing,
                    max_request_cost_usd=self.config.max_request_cost_usd,
                    allow_local_http=self.config.allow_local_http,
                    extra_headers=self.config.extra_headers,
                    extra_body=self.config.extra_body,
                )
            )
        else:
            raise ValueError(
                f"Unsupported API provider/protocol: {self.provider}/{self.config.protocol}"
            )

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        prompt = self._build_prompt(request)
        payload = {
            "model": self.model,
            "input": prompt,
            "max_output_tokens": self.config.max_output_tokens,
        }
        response = self.client.create_response(payload)
        text = _extract_response_text(response)
        parsed = self._parse_model_text(text)

        events: list[dict[str, Any]] = [
            {
                "type": "MODEL_CALL",
                "provider": self.provider,
                "model": self.model,
                "response_id": response.get("id"),
            }
        ]
        for file_spec in parsed.get("files", []):
            if not isinstance(file_spec, dict):
                continue
            relative_path = file_spec.get("path")
            content = file_spec.get("content", "")
            if not isinstance(relative_path, str) or not isinstance(content, str):
                continue
            target = _safe_workspace_target(request.workspace, relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            events.append({"type": "WRITE", "path": relative_path, "skill": request.selected_skill.id if request.selected_skill else None})

        answer = parsed.get("answer")
        if answer is not None and not isinstance(answer, str):
            answer = str(answer)
        if answer is not None:
            events.append({"type": "ANSWER", "answer": answer})

        return ExecutionResult(
            answer=answer,
            events=events,
            metadata={
                "executor": self.name,
                "provider": self.provider,
                "model": self.model,
                "response_id": response.get("id"),
                "usage": response.get("usage"),
                "provider_metadata": response.get("_merlin_harness_provider"),
                "raw_output_preview": text[:1000],
            },
        )

    def _build_prompt(self, request: ExecutionRequest) -> str:
        return _build_model_prompt(
            request=request,
            max_workspace_chars=self.config.max_workspace_chars,
            max_skill_chars=self.config.max_skill_chars,
        )

    def _parse_model_text(self, text: str) -> dict[str, Any]:
        return _parse_model_json_or_answer(text)
