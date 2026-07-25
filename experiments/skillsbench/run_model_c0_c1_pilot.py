"""Run account-auth SkillsBench C0/C1 agentic pilot trials.

The provider CLI stays on the host so it can use the user's logged-in account.
The model receives no host shell or host filesystem tool: its only execution
surface is a one-container MCP bridge.  The verifier and reward directory are
created only after the agent has exited and the container is quiescent.

C0 and C1 use the same image, prompt, tools, limits, and verifier.  The only
treatment difference is the presence of the complete curated bundle through
the provider-native project skill directory and a read-only container mount.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.skillsbench.run_oracle_readiness import (
    CommandReport,
    classify_verifier_result,
    docker_env_args,
    docker_resource_args,
    ensure_docker,
    prepare_skill_free_build_context,
    read_reward,
    run_command,
    safe_name,
    stop_process,
    task_phase_env,
    task_section_number,
    tail_text,
)
from experiments.skillsbench.analyze_claude_stream_trace import analyze_trace


TASKS_ROOT = ROOT / "tasks"
DEFAULT_MATRIX = ROOT.parent / "model_backends" / "backend-matrix.json"
DEFAULT_ORACLE_SUMMARY = ROOT / "runs" / "oracle-readiness" / "one-full-87-20260708-r2" / "summary.json"
DEFAULT_RUNS_ROOT = ROOT / "runs" / "model-c0-c1-pilot"
DEFAULT_TASKS = [
    "court-form-filling",
    "weighted-gdp-calc",
    "earthquake-plate-calculation",
]
DEFAULT_HARNESS_MODE = "H_paper_cli_mcp_v1"
PAPER_CLI_MCP_PROMPT_CONTRACT: dict[str, Any] = {
    "task_user_message": "task_md_body_only",
    "execution_contract_source": "provider_tool_schema",
    "prompt_equals_task_instruction": True,
}
BENCHMARK_INELIGIBILITY_REASONS = [
    "temperature=0 is unavailable in the provider CLI",
    "the paper's 8K token cap and storage cap are not frozen in this harness",
    "the user-account model-harness combination is a distinct evaluation cell from the paper's published model-harness cells",
]
TAIL_CHARS = 6000
MODEL_NONCOMPLETION_SCORE_SOURCE = "model_noncompletion_timeout_zero"
CLAUDE_INITIALIZE_REQUEST_ID = "theking_initialize_1"
CLAUDE_MCP_STATUS_REQUEST_PREFIX = "theking_mcp_status_"
CLAUDE_CONTROL_BARRIER_TIMEOUT_SEC = 60
CLAUDE_CONTROL_BARRIER_FAILURE_EXIT_CODE = 78
_STREAM_EOF = object()
CONTROLLED_CLAUDE_SETTINGS: dict[str, Any] = {
    # Explicit account-auth MCP configs are otherwise left in a noninteractive
    # "pending approval" state. This approves only the strict config supplied
    # on this command; no user/global MCP configuration is loaded.
    "enableAllProjectMcpServers": True,
}


@dataclass(slots=True)
class PilotRecord:
    task_id: str
    condition_id: str
    arm: str
    harness_mode: str
    model_id: str
    backend: str
    effort: str
    runtime_effort: str
    status: str
    passed: bool
    backend_type: str = "B_cli"
    auth_mode: str = "user_owned_account"
    credential_forwarded_to_container: bool = False
    trial_index: int = 1
    trial_id: str | None = None
    seed_control: str = "provider_cli_unavailable"
    temperature_control: str = "provider_default_or_unset"
    reward: float | None = None
    score_source: str | None = None
    wall_time_sec: float | None = None
    account_usage: dict[str, Any] = field(default_factory=dict)
    agent_output_path: str | None = None
    agent_output_sha256: str | None = None
    task_instruction_sha256: str | None = None
    prompt_sha256: str | None = None
    skill_delivery: dict[str, Any] = field(default_factory=dict)
    workspace: str | None = None
    container_workdir: str | None = None
    execution_bridge: str = "host_account_cli_to_mcp_bound_task_container"
    provider_project: str | None = None
    workspace_manifest_pre_verifier: dict[str, Any] = field(default_factory=dict)
    container_exposure: dict[str, Any] = field(default_factory=dict)
    tool_trace: dict[str, Any] = field(default_factory=dict)
    control_barrier: dict[str, Any] = field(default_factory=dict)
    configuration_audit: dict[str, Any] = field(default_factory=dict)
    logs_dir: str | None = None
    commands: dict[str, CommandReport] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def classify_agent_noncompletion(
    report: CommandReport,
) -> tuple[str, float | None, str | None, list[str]]:
    """Keep frozen-budget timeouts in the denominator without calling them infra."""

    if report.timed_out and report.exit_code == 124:
        return (
            "agent_timeout",
            0.0,
            MODEL_NONCOMPLETION_SCORE_SOURCE,
            [
                "The model exhausted the frozen agent timeout before verifier "
                "execution; count as a denominator non-pass with reward 0.0."
            ],
        )
    return ("agent_failed", None, None, [])


def copy_environment(src: Path, dst: Path, *, include_skills: bool) -> None:
    if dst.exists():
        shutil.rmtree(dst)

    def ignore(_dir: str, names: list[str]) -> set[str]:
        if include_skills:
            return set()
        return {"skills"} if "skills" in names else set()

    shutil.copytree(src, dst, ignore=ignore)


def read_task_prompt(task_dir: Path) -> str:
    return (task_dir / "task.md").read_text(encoding="utf-8", errors="replace")


def task_instruction_body(task_text: str) -> str:
    if not task_text.startswith("---"):
        return task_text
    parts = task_text.split("---", 2)
    return parts[2].lstrip("\r\n") if len(parts) >= 3 else task_text


def install_native_skill_view(provider_project: Path, source: Path, backend: str) -> None:
    """Copy C1 skills into an isolated provider-native project directory.

    This directory is deliberately separate from the bind-mounted task
    workspace.  The host CLI may discover project skills, but it has no host
    filesystem tools and the task container cannot modify the provider view.
    """

    native_root = provider_project / (".claude" if backend == "claude" else ".agents")
    native_root.mkdir(parents=True, exist_ok=True)
    native_skills = native_root / "skills"
    if native_skills.exists():
        shutil.rmtree(native_skills)
    if source.exists():
        shutil.copytree(source, native_skills, symlinks=True)


def directory_manifest(path: Path) -> dict[str, Any]:
    files = sorted(item for item in path.rglob("*") if item.is_file()) if path.exists() else []
    hasher = hashlib.sha256()
    total_bytes = 0
    for file_path in files:
        relative = file_path.relative_to(path).as_posix().encode("utf-8")
        content = file_path.read_bytes()
        hasher.update(len(relative).to_bytes(8, "big"))
        hasher.update(relative)
        hasher.update(len(content).to_bytes(8, "big"))
        hasher.update(content)
        total_bytes += len(content)
    return {
        "file_count": len(files),
        "total_bytes": total_bytes,
        "sha256": hasher.hexdigest(),
    }


def bundle_skill_names(path: Path) -> list[str]:
    names: list[str] = []
    if not path.exists():
        return names
    for skill_file in sorted(path.rglob("SKILL.md")):
        match = re.search(
            r"(?m)^name:\s*[\"']?([^\n\"']+?)[\"']?\s*$",
            skill_file.read_text(encoding="utf-8", errors="replace"),
        )
        if match:
            names.append(match.group(1).strip())
    return sorted(set(names))


def probe_backend_version(condition: dict[str, Any]) -> CommandReport:
    executable = "claude" if condition["backend"] == "claude" else "codex"
    return run_command([executable, "--version"], timeout_sec=30)


def build_agent_prompt(
    *,
    task_id: str,
    task_text: str,
    container_workdir: str = "/root",
) -> str:
    """Return the task body verbatim for the sole user-model message.

    ``task_id`` and ``container_workdir`` remain in the call signature so the
    C0/C1 runner API stays stable, but execution guidance belongs exclusively
    to the provider tool schema rather than an experiment-authored wrapper.
    """

    _ = task_id, container_workdir
    return task_text


def make_agent_command(
    condition: dict[str, Any],
    *,
    mcp_config: dict[str, Any] | str,
    settings: dict[str, Any] | str | Path,
    debug_file: Path | None = None,
) -> list[str]:
    backend = condition["backend"]
    model = condition["model_id"]
    effort = condition["effort"]
    runtime_effort = condition.get("runtime_effort", effort)
    if backend == "claude":
        config_text = mcp_config if isinstance(mcp_config, str) else json.dumps(mcp_config)
        settings_text = (
            json.dumps(settings)
            if isinstance(settings, dict)
            else str(settings)
        )
        command = [
            "claude",
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--replay-user-messages",
            "--verbose",
            "--include-hook-events",
            "--no-session-persistence",
            "--no-chrome",
            "--setting-sources",
            "project",
            "--settings",
            settings_text,
            "--strict-mcp-config",
            "--mcp-config",
            config_text,
            "--tools",
            "mcp__task_container__exec,Skill",
            "--allowedTools",
            "mcp__task_container__exec,Skill",
            "--disallowedTools",
            "Bash,Edit,Read,Write,WebFetch,WebSearch,NotebookEdit,Agent",
            "--model",
            model,
            "--effort",
            runtime_effort,
            "--permission-mode",
            "dontAsk",
        ]
        if debug_file is not None:
            command.extend(["--debug-file", str(debug_file)])
        return command
    if backend == "codex":
        raise ValueError("secure agentic container bridge is not yet implemented for Codex CLI")
    raise ValueError(f"Unsupported backend: {backend}")


class ClaudeControlBarrierError(RuntimeError):
    """Raised before task submission when the required MCP surface is not ready."""


def _write_claude_stream_event(
    process: subprocess.Popen[str],
    payload: dict[str, Any],
) -> None:
    if process.stdin is None:
        raise ClaudeControlBarrierError("Claude stdin is unavailable")
    process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    process.stdin.flush()


def _wait_for_json_event(
    event_queue: queue.Queue[object],
    process: subprocess.Popen[str],
    *,
    deadline: float,
    predicate: Any,
    description: str,
) -> dict[str, Any]:
    """Wait for one matching stdout JSONL event while continuously draining pipes."""

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for {description}")
        try:
            item = event_queue.get(timeout=min(0.25, remaining))
        except queue.Empty:
            if process.poll() is not None:
                raise RuntimeError(
                    f"Claude exited before emitting {description} (exit={process.returncode})"
                )
            continue
        if item is _STREAM_EOF:
            raise RuntimeError(f"Claude stdout closed before emitting {description}")
        try:
            event = json.loads(str(item))
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and predicate(event):
            return event


def _matching_control_response(event: dict[str, Any], request_id: str) -> bool:
    response = event.get("response")
    return bool(
        event.get("type") == "control_response"
        and isinstance(response, dict)
        and response.get("request_id") == request_id
    )


def _summarize_mcp_status_response(event: dict[str, Any]) -> dict[str, Any]:
    """Return only non-identity MCP readiness fields from a control response."""

    wrapper = event.get("response") if isinstance(event.get("response"), dict) else {}
    payload = wrapper.get("response") if isinstance(wrapper.get("response"), dict) else {}
    raw_servers = payload.get("mcpServers") if isinstance(payload.get("mcpServers"), list) else []
    servers: list[dict[str, Any]] = []
    for item in raw_servers:
        if not isinstance(item, dict):
            continue
        raw_tools = item.get("tools") if isinstance(item.get("tools"), list) else []
        tool_names = sorted(
            str(tool.get("name"))
            for tool in raw_tools
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        )
        server_info = item.get("serverInfo") if isinstance(item.get("serverInfo"), dict) else {}
        servers.append(
            {
                "name": str(item.get("name", "")),
                "status": str(item.get("status", "")),
                "tool_names": tool_names,
                "server_name": server_info.get("name"),
                "server_version": server_info.get("version"),
            }
        )
    return {
        "response_subtype": wrapper.get("subtype"),
        "servers": servers,
    }


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    """Stop the Claude process tree without calling communicate on drained pipes."""

    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass


def run_agent(
    condition: dict[str, Any],
    prompt: str,
    *,
    provider_project: Path,
    timeout_sec: int,
    mcp_config: dict[str, Any] | str,
    settings: dict[str, Any] | str | Path,
    raw_output_path: Path | None = None,
    debug_file: Path | None = None,
    env: dict[str, str] | None = None,
    barrier_evidence: dict[str, Any] | None = None,
) -> CommandReport:
    """Run one task after a non-model MCP readiness control barrier.

    Claude Code does not emit its ``system/init`` event until the first user
    message.  Sending that message immediately can therefore race a dynamic
    MCP registration.  Stream input lets the runner initialize the control
    protocol and require a connected ``task_container.exec`` first, without a
    warm-up model turn or any change to the real task prompt.
    """

    command = make_agent_command(
        condition,
        mcp_config=mcp_config,
        settings=settings,
        debug_file=debug_file,
    )
    start = time.monotonic()
    overall_deadline = start + timeout_sec
    barrier_deadline = min(overall_deadline, start + CLAUDE_CONTROL_BARRIER_TIMEOUT_SEC)
    evidence: dict[str, Any] = {
        "protocol": "claude_code_stream_json_control",
        "required_server": "task_container",
        "required_tool": "exec",
        "initialize": {
            "request_id": CLAUDE_INITIALIZE_REQUEST_ID,
            "sent": False,
            "success": False,
        },
        "mcp_status": {"attempt_count": 0, "attempts": []},
        "task_event": {
            "sent": False,
            "count": 0,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        },
        "warmup_model_turn_count": 0,
        "first_model_input_is_task": False,
        "result_seen": False,
        "passed": False,
        "failure_reason": None,
    }

    def publish_evidence() -> None:
        if barrier_evidence is not None:
            barrier_evidence.clear()
            barrier_evidence.update(evidence)

    process_env = dict(os.environ if env is None else env)
    # Force the account-auth path even if the caller's shell happens to export
    # an API key. Credentials remain host-only and are never Docker env flags.
    process_env.pop("ANTHROPIC_API_KEY", None)
    process_env.pop("OPENAI_API_KEY", None)
    process = subprocess.Popen(
        command,
        cwd=provider_project,
        env=process_env,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    if process.stdout is None or process.stderr is None:
        _terminate_process_group(process)
        raise RuntimeError("Claude stdout/stderr pipes are unavailable")

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    stdout_events: queue.Queue[object] = queue.Queue()
    raw_handle = None
    if raw_output_path is not None:
        raw_output_path.parent.mkdir(parents=True, exist_ok=True)
        raw_handle = raw_output_path.open("w", encoding="utf-8")

    def drain_stdout() -> None:
        try:
            for line in process.stdout:
                stdout_lines.append(line)
                if raw_handle is not None:
                    raw_handle.write(line)
                    raw_handle.flush()
                stdout_events.put(line)
        finally:
            stdout_events.put(_STREAM_EOF)

    def drain_stderr() -> None:
        for line in process.stderr:
            stderr_lines.append(line)

    stdout_thread = threading.Thread(target=drain_stdout, name="claude-stdout", daemon=True)
    stderr_thread = threading.Thread(target=drain_stderr, name="claude-stderr", daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    exit_code_override: int | None = None
    timed_out = False
    task_sent = False
    try:
        initialize_request = {
            "type": "control_request",
            "request_id": CLAUDE_INITIALIZE_REQUEST_ID,
            "request": {"subtype": "initialize", "hooks": None},
        }
        _write_claude_stream_event(process, initialize_request)
        evidence["initialize"]["sent"] = True
        initialize_event = _wait_for_json_event(
            stdout_events,
            process,
            deadline=barrier_deadline,
            predicate=lambda event: _matching_control_response(
                event, CLAUDE_INITIALIZE_REQUEST_ID
            ),
            description="initialize control response",
        )
        initialize_wrapper = initialize_event.get("response", {})
        initialize_subtype = (
            initialize_wrapper.get("subtype")
            if isinstance(initialize_wrapper, dict)
            else None
        )
        evidence["initialize"].update(
            {
                "response_received": True,
                "response_subtype": initialize_subtype,
                "latency_sec": round(time.monotonic() - start, 3),
            }
        )
        if initialize_subtype != "success":
            raise ClaudeControlBarrierError("initialize control request did not succeed")
        evidence["initialize"]["success"] = True

        status_attempt = 0
        while True:
            status_attempt += 1
            request_id = f"{CLAUDE_MCP_STATUS_REQUEST_PREFIX}{status_attempt}"
            _write_claude_stream_event(
                process,
                {
                    "type": "control_request",
                    "request_id": request_id,
                    "request": {"subtype": "mcp_status"},
                },
            )
            status_event = _wait_for_json_event(
                stdout_events,
                process,
                deadline=barrier_deadline,
                predicate=lambda event, expected=request_id: _matching_control_response(
                    event, expected
                ),
                description="MCP status control response",
            )
            status_summary = _summarize_mcp_status_response(status_event)
            evidence["mcp_status"]["attempt_count"] = status_attempt
            evidence["mcp_status"]["attempts"].append(
                {"request_id": request_id, **status_summary}
            )
            if status_summary.get("response_subtype") != "success":
                raise ClaudeControlBarrierError("MCP status control request did not succeed")
            task_server = next(
                (
                    server
                    for server in status_summary.get("servers", [])
                    if server.get("name") == "task_container"
                ),
                None,
            )
            if (
                task_server is not None
                and task_server.get("status") == "connected"
                and "exec" in task_server.get("tool_names", [])
            ):
                evidence["mcp_status"].update(
                    {
                        "connected": True,
                        "required_tool_present": True,
                        "ready_server": task_server,
                    }
                )
                break
            status = task_server.get("status") if task_server is not None else "missing"
            if status not in {"pending", "connecting", "starting", "missing", ""}:
                raise ClaudeControlBarrierError(
                    f"task_container MCP entered non-ready status {status!r}"
                )
            remaining = barrier_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for task_container MCP readiness")
            time.sleep(min(0.1, remaining))

        evidence["passed"] = True
        task_event = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            },
        }
        _write_claude_stream_event(process, task_event)
        task_sent = True
        evidence["task_event"].update({"sent": True, "count": 1})
        evidence["first_model_input_is_task"] = True

        _wait_for_json_event(
            stdout_events,
            process,
            deadline=overall_deadline,
            predicate=lambda event: event.get("type") == "result",
            description="final result event",
        )
        evidence["result_seen"] = True
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        remaining = overall_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Claude exceeded the agent timeout after its result event")
        process.wait(timeout=remaining)
    except ClaudeControlBarrierError as exc:
        evidence["failure_reason"] = str(exc)
        exit_code_override = CLAUDE_CONTROL_BARRIER_FAILURE_EXIT_CODE
        _terminate_process_group(process)
    except (TimeoutError, subprocess.TimeoutExpired) as exc:
        evidence["failure_reason"] = str(exc)
        timed_out = True
        exit_code_override = 124
        _terminate_process_group(process)
    except (BrokenPipeError, OSError, RuntimeError) as exc:
        evidence["failure_reason"] = str(exc)
        exit_code_override = (
            1 if task_sent else CLAUDE_CONTROL_BARRIER_FAILURE_EXIT_CODE
        )
        _terminate_process_group(process)
    finally:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        if process.poll() is None:
            _terminate_process_group(process)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        if raw_handle is not None:
            raw_handle.close()
        publish_evidence()

    stdout = "".join(stdout_lines)
    stderr = "".join(stderr_lines)
    exit_code = (
        exit_code_override
        if exit_code_override is not None
        else (process.returncode if process.returncode is not None else 1)
    )
    return CommandReport(
        argv=command,
        exit_code=exit_code,
        duration_sec=round(time.monotonic() - start, 3),
        timed_out=timed_out,
        stdout_tail=tail_text(stdout, TAIL_CHARS),
        stderr_tail=tail_text(stderr, TAIL_CHARS),
    )


def make_container_mcp_config(
    *,
    container: str,
    container_workdir: str,
    timeout_sec: int,
    audit_path: Path | None = None,
) -> dict[str, Any]:
    """Build the immutable MCP process configuration passed to Claude."""

    args = [
        str((ROOT / "container_exec_mcp.py").resolve()),
        "--container",
        container,
        "--workdir",
        container_workdir,
        "--timeout-sec",
        str(timeout_sec),
    ]
    if audit_path is not None:
        args.extend(["--audit-log", str(audit_path)])
    return {
        "mcpServers": {
            "task_container": {
                "type": "stdio",
                "command": sys.executable,
                "args": args,
            }
        }
    }


def summarize_mcp_protocol_audit(path: Path) -> dict[str, Any]:
    events, malformed = _stream_events(path)
    methods = [str(event["method"]) for event in events if isinstance(event.get("method"), str)]
    requested = [
        str(event["requested_protocol_version"])
        for event in events
        if isinstance(event.get("requested_protocol_version"), str)
    ]
    negotiated = [
        str(event["negotiated_protocol_version"])
        for event in events
        if isinstance(event.get("negotiated_protocol_version"), str)
    ]
    return {
        "present": path.exists(),
        "event_count": len(events),
        "malformed_line_count": malformed,
        "methods": methods,
        "requested_protocol_versions": requested,
        "negotiated_protocol_versions": negotiated,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
        "contains_tool_arguments_or_output": False,
    }


def _stream_events(path: Path) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    malformed = 0
    if not path.exists():
        return events, malformed
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(event, dict):
            events.append(event)
        else:
            malformed += 1
    return events, malformed


def _tool_names_in_value(value: Any) -> list[str]:
    names: list[str] = []
    if isinstance(value, dict):
        if value.get("type") == "tool_use" and isinstance(value.get("name"), str):
            names.append(value["name"])
        for nested in value.values():
            names.extend(_tool_names_in_value(nested))
    elif isinstance(value, list):
        for nested in value:
            names.extend(_tool_names_in_value(nested))
    return names


def extract_agent_trace(path: Path) -> dict[str, Any]:
    """Extract auditable tool/configuration evidence from Claude stream JSONL."""

    events, malformed = _stream_events(path)
    tool_names: list[str] = []
    hook_events = 0
    init_tools: list[str] = []
    init_mcp_servers: list[str] = []
    init_mcp_server_statuses: dict[str, str] = {}
    init_plugins: list[str] = []
    init_skills: list[str] = []
    result_subtype: str | None = None
    control_response_ids: list[str] = []
    control_response_subtypes: list[str] = []
    replayed_user_message_count = 0
    system_init_count = 0
    result_event_count = 0
    assistant_models: set[str] = set()
    rejected_assistant_model_count = 0
    for event in events:
        tool_names.extend(_tool_names_in_value(event))
        event_type = str(event.get("type", ""))
        event_subtype = str(event.get("subtype", ""))
        if "hook" in event_type.lower() or "hook" in event_subtype.lower():
            hook_events += 1
        if event_type == "system" and event_subtype == "init":
            system_init_count += 1
            tools_value = event.get("tools", [])
            if isinstance(tools_value, list):
                init_tools = [str(item) for item in tools_value]
            mcp_value = event.get("mcp_servers", [])
            if isinstance(mcp_value, list):
                for item in mcp_value:
                    if isinstance(item, dict):
                        name = str(item.get("name", ""))
                        init_mcp_servers.append(name)
                        init_mcp_server_statuses[name] = str(item.get("status", ""))
                    else:
                        init_mcp_servers.append(str(item))
            plugins_value = event.get("plugins", [])
            if isinstance(plugins_value, list):
                for item in plugins_value:
                    if isinstance(item, dict):
                        init_plugins.append(str(item.get("name", "")))
                    else:
                        init_plugins.append(str(item))
            skills_value = event.get("skills", [])
            if isinstance(skills_value, list):
                init_skills = [str(item) for item in skills_value]
        if event_type == "control_response":
            response_value = event.get("response")
            if isinstance(response_value, dict):
                request_id = response_value.get("request_id")
                response_subtype = response_value.get("subtype")
                if isinstance(request_id, str):
                    control_response_ids.append(request_id)
                if isinstance(response_subtype, str):
                    control_response_subtypes.append(response_subtype)
        if event_type == "user" and event.get("isReplay") is True:
            replayed_user_message_count += 1
        if event_type == "assistant":
            message_value = event.get("message")
            raw_model = (
                message_value.get("model")
                if isinstance(message_value, dict)
                else None
            )
            if isinstance(raw_model, str) and re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", raw_model
            ):
                assistant_models.add(raw_model)
            else:
                rejected_assistant_model_count += 1
        if event_type == "result":
            result_event_count += 1
            result_subtype = event_subtype or None

    allowed = {"mcp__task_container__exec", "Skill"}
    unexpected_calls = sorted({name for name in tool_names if name not in allowed})
    unexpected_advertised = sorted({name for name in init_tools if name not in allowed})
    return {
        "event_count": len(events),
        "malformed_line_count": malformed,
        "tool_call_count": len(tool_names),
        "tool_call_names": tool_names,
        "mcp_exec_call_count": tool_names.count("mcp__task_container__exec"),
        "skill_call_count": tool_names.count("Skill"),
        "unexpected_tool_calls": unexpected_calls,
        "advertised_tools": init_tools,
        "unexpected_advertised_tools": unexpected_advertised,
        "mcp_servers": init_mcp_servers,
        "mcp_server_statuses": init_mcp_server_statuses,
        "plugins": init_plugins,
        "advertised_skills": init_skills,
        "assistant_models": sorted(assistant_models),
        "rejected_assistant_model_count": rejected_assistant_model_count,
        "hook_event_count": hook_events,
        "control_response_ids": control_response_ids,
        "control_response_subtypes": control_response_subtypes,
        "replayed_user_message_count": replayed_user_message_count,
        "system_init_count": system_init_count,
        "result_event_count": result_event_count,
        "result_subtype": result_subtype,
    }


def assistant_models_match_request(
    tool_trace: dict[str, Any],
    requested_model_id: str,
) -> bool:
    """Require every sanitized assistant model to resolve to the requested ID."""

    return bool(
        tool_trace.get("assistant_models") == [requested_model_id]
        and tool_trace.get("rejected_assistant_model_count", 0) == 0
    )


def audit_debug_log(path: Path, *, original_home: Path) -> dict[str, Any]:
    """Flag user-level plugin/hook/skill/settings loads without retaining them."""

    if not path.exists():
        return {"present": False, "sha256": None, "suspicious_line_count": None}
    content = path.read_text(encoding="utf-8", errors="replace")
    home_config = str((original_home / ".claude").resolve())
    suspicious_patterns = re.compile(r"(?i)(plugin|hook|skill|setting|memory|claude\.md)")
    suspicious_count = sum(
        1
        for line in content.splitlines()
        if home_config in line and suspicious_patterns.search(line)
    )
    return {
        "present": True,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "suspicious_line_count": suspicious_count,
    }


def prepare_isolated_claude_environment(root: Path) -> tuple[dict[str, str], dict[str, Any]]:
    """Create a fresh HOME while linking only host account-auth credentials."""

    original_home = Path.home().resolve()
    original_config = Path(os.environ.get("CLAUDE_CONFIG_DIR", original_home / ".claude"))
    isolated_home = root / "home"
    isolated_config = isolated_home / ".claude"
    isolated_config.mkdir(parents=True, exist_ok=True)
    linked: list[str] = []
    for filename in (".credentials.json", "credentials.json"):
        source = original_config / filename
        destination = isolated_config / filename
        if source.is_file():
            destination.symlink_to(source)
            linked.append(filename)
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(isolated_home),
            "CLAUDE_CONFIG_DIR": str(isolated_config),
            "XDG_CONFIG_HOME": str(isolated_home / ".config"),
            "XDG_CACHE_HOME": str(isolated_home / ".cache"),
            "XDG_DATA_HOME": str(isolated_home / ".local" / "share"),
        }
    )
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    return env, {
        "fresh_home": True,
        "credential_link_count": len(linked),
        "credential_content_copied": False,
        "api_key_env_removed": True,
        "original_home": str(original_home),
    }


def probe_claude_account_auth(
    *,
    cwd: Path,
    env: dict[str, str],
) -> tuple[dict[str, Any], CommandReport]:
    """Verify account login while redacting identity and organization fields."""

    start = time.monotonic()
    completed = subprocess.run(
        ["claude", "auth", "status", "--json"],
        cwd=cwd,
        env=env,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {}
    sanitized = {
        "logged_in": bool(payload.get("loggedIn")),
        "auth_method": payload.get("authMethod"),
        "api_provider": payload.get("apiProvider"),
        "subscription_type": payload.get("subscriptionType"),
    }
    report = CommandReport(
        argv=["claude", "auth", "status", "--json"],
        exit_code=completed.returncode,
        duration_sec=round(time.monotonic() - start, 3),
        stdout_tail=json.dumps(sanitized, ensure_ascii=False),
        stderr_tail=tail_text(completed.stderr),
    )
    return sanitized, report


def probe_claude_plugins(*, cwd: Path, env: dict[str, str]) -> tuple[int | None, CommandReport]:
    """Count enabled/installed plugins in the isolated CLI home without storing details."""

    start = time.monotonic()
    completed = subprocess.run(
        ["claude", "plugin", "list", "--json"],
        cwd=cwd,
        env=env,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    try:
        payload = json.loads(completed.stdout)
        count = len(payload) if isinstance(payload, list) else None
    except json.JSONDecodeError:
        count = None
    report = CommandReport(
        argv=["claude", "plugin", "list", "--json"],
        exit_code=completed.returncode,
        duration_sec=round(time.monotonic() - start, 3),
        stdout_tail=json.dumps({"plugin_count": count}),
        stderr_tail=tail_text(completed.stderr),
    )
    return count, report


def extract_account_usage(report: CommandReport) -> dict[str, Any]:
    text = (report.stdout_tail or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    usage: dict[str, Any] = {}
    for key in ("total_cost_usd", "duration_ms", "num_turns", "usage", "session_id", "model"):
        if key in payload:
            usage[key] = payload[key]
    return usage


def extract_account_usage_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    events, _ = _stream_events(path)
    for payload in reversed(events):
        if payload.get("type") != "result":
            continue
        usage: dict[str, Any] = {}
        for key in ("total_cost_usd", "duration_ms", "duration_api_ms", "num_turns", "usage", "model"):
            if key in payload:
                usage[key] = payload[key]
        if usage:
            return usage
    report = CommandReport(
        argv=[],
        exit_code=0,
        duration_sec=0.0,
        stdout_tail=path.read_text(encoding="utf-8", errors="replace"),
    )
    return extract_account_usage(report)


def normalize_container_workdir(value: str | None) -> str:
    workdir = (value or "").strip() or "/root"
    return workdir if workdir.startswith("/") else f"/{workdir}"


def detect_image_workdir(image: str) -> tuple[str, CommandReport]:
    report = run_command(
        ["docker", "image", "inspect", "-f", "{{.Config.WorkingDir}}", image],
        timeout_sec=30,
    )
    return normalize_container_workdir(report.stdout_tail if report.exit_code == 0 else None), report


def materialize_image_workspace(
    *,
    image: str,
    workspace: Path,
    container_workdir: str,
    task_id: str,
) -> dict[str, CommandReport]:
    """Copy the built image workdir to a host bind workspace before agent use."""

    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    container = f"theking-sb-materialize-{safe_name(task_id)}-{uuid.uuid4().hex[:8]}"
    commands: dict[str, CommandReport] = {}
    commands["workspace_container_create"] = run_command(
        [
            "docker",
            "create",
            "--name",
            container,
            "--entrypoint",
            "/bin/sh",
            image,
            "-lc",
            "true",
        ],
        timeout_sec=60,
    )
    if commands["workspace_container_create"].exit_code != 0:
        return commands
    try:
        commands["workspace_copy_from_image"] = run_command(
            ["docker", "cp", f"{container}:{container_workdir}/.", str(workspace.resolve())],
            timeout_sec=180,
        )
    finally:
        commands["workspace_container_cleanup"] = run_command(
            ["docker", "rm", "-f", container],
            timeout_sec=60,
        )
    return commands


def prepare_provider_project(
    *,
    provider_project: Path,
    task_dir: Path,
    backend: str,
    include_skills: bool,
) -> Path | None:
    """Create an opaque, repository-independent CLI project for one arm."""

    if provider_project.exists():
        shutil.rmtree(provider_project)
    provider_project.mkdir(parents=True)
    if not include_skills:
        return None
    source = task_dir / "environment" / "skills"
    install_native_skill_view(provider_project, source, backend)
    return provider_project / (".claude" if backend == "claude" else ".agents") / "skills"


def start_bound_task_container(
    *,
    image: str,
    workspace: Path,
    task_id: str,
    task_text: str,
    container_workdir: str,
    skills_source: Path | None = None,
    native_skill_container_path: Path | None = None,
) -> tuple[str, CommandReport]:
    container = f"theking-sb-agent-{safe_name(task_id)}-{uuid.uuid4().hex[:8]}"
    mounts = ["-v", f"{workspace.resolve()}:{container_workdir}"]
    if skills_source is not None:
        # Provider instructions commonly mention either project-relative
        # ``skills/...`` paths or the provider-native absolute base directory.
        # Both resolve to the same immutable source inside the task container.
        relative_target = f"{container_workdir.rstrip('/')}/skills"
        mounts.extend(["-v", f"{skills_source.resolve()}:{relative_target}:ro"])
        if native_skill_container_path is not None:
            mounts.extend(
                ["-v", f"{skills_source.resolve()}:{native_skill_container_path.as_posix()}:ro"]
            )
    report = run_command(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container,
            *docker_resource_args(task_text),
            *docker_env_args(task_phase_env(task_text, "environment")),
            *mounts,
            "--entrypoint",
            "/bin/sh",
            image,
            "-lc",
            "exec sh -lc 'while :; do sleep 3600; done'",
        ],
        timeout_sec=60,
    )
    return container, report


def sanitize_container_control_paths(container: str) -> CommandReport:
    """Remove evaluation control paths before the model can run."""

    return run_command(
        [
            "docker",
            "exec",
            "-u",
            "0",
            container,
            "sh",
            "-lc",
            "rm -rf /verifier /oracle /logs",
        ],
        timeout_sec=30,
    )


def scan_container_pre_agent(
    *,
    container: str,
    container_workdir: str,
    expected_skill_roots: list[Path] | None,
) -> tuple[dict[str, Any], CommandReport]:
    """Fail-closed scan for verifier/oracle leakage and C0 skill pollution."""

    roots = [
        "/root/.claude/skills",
        "/root/.agents/skills",
        "/home",
        "/app/skills",
        "/workspace/skills",
        f"{container_workdir.rstrip('/')}/skills",
    ]
    roots.extend(root.as_posix() for root in (expected_skill_roots or []))
    roots = list(dict.fromkeys(roots))
    script = "\n".join(
        [
            "set -u",
            "for p in /verifier /oracle /logs; do",
            "  if [ -e \"$p\" ]; then printf 'CONTROL\\t%s\\n' \"$p\"; fi",
            "done",
            *[
                f"if [ -e {shlex.quote(root)} ]; then find {shlex.quote(root)} -type f -name SKILL.md -print 2>/dev/null; fi"
                for root in roots
            ],
            "if ! timeout --signal=TERM --kill-after=2s 1s true >/dev/null 2>&1; then printf 'MISSING\\tgnu-timeout\\n'; fi",
        ]
    )
    report = run_command(
        ["docker", "exec", container, "bash", "-lc", script],
        timeout_sec=60,
    )
    lines = [line.strip() for line in report.stdout_tail.splitlines() if line.strip()]
    control_paths = [line.split("\t", 1)[1] for line in lines if line.startswith("CONTROL\t")]
    missing = [line.split("\t", 1)[1] for line in lines if line.startswith("MISSING\t")]
    skill_files = sorted(
        line for line in lines if not line.startswith(("CONTROL\t", "MISSING\t"))
    )
    expected_prefixes = [root.as_posix().rstrip("/") for root in (expected_skill_roots or [])]
    unexpected_skills = [
        path
        for path in skill_files
        if not any(path.startswith(f"{prefix}/") for prefix in expected_prefixes)
    ]
    missing_expected_roots = [
        prefix
        for prefix in expected_prefixes
        if not any(path.startswith(f"{prefix}/") for path in skill_files)
    ]
    audit = {
        "control_paths_present": control_paths,
        "skill_files": skill_files,
        "unexpected_skill_files": unexpected_skills,
        "missing_expected_skill_roots": missing_expected_roots,
        "missing_required_commands": missing,
        "passed": report.exit_code == 0
        and not control_paths
        and not unexpected_skills
        and not missing_expected_roots
        and not missing,
    }
    return audit, report


def container_process_snapshot(container: str) -> tuple[dict[str, Any], CommandReport]:
    report = run_command(
        ["docker", "top", container, "-eo", "pid,ppid,comm,args"],
        timeout_sec=30,
    )
    processes: list[dict[str, str]] = []
    for line in report.stdout_tail.splitlines()[1:]:
        parts = line.split(None, 3)
        if len(parts) == 4:
            processes.append(
                {"pid": parts[0], "ppid": parts[1], "comm": parts[2], "args": parts[3]}
            )
    unexpected = [item for item in processes if item["comm"] not in {"sh", "sleep"}]
    return {
        "processes": processes,
        "unexpected_processes": unexpected,
        "passed": report.exit_code == 0 and not unexpected and len(processes) in {1, 2},
    }, report


def stage_and_run_verifier(
    *,
    container: str,
    task_dir: Path,
    task_text: str,
    logs_dir: Path,
    timeout_sec: int,
) -> tuple[dict[str, CommandReport], float | None]:
    """Add the verifier after agent exit, execute once, then export logs."""

    commands: dict[str, CommandReport] = {}
    verifier_dir = task_dir / "verifier"
    if logs_dir.exists():
        shutil.rmtree(logs_dir)
    logs_dir.mkdir(parents=True)
    commands["evaluation_paths_prepare"] = run_command(
        [
            "docker",
            "exec",
            "-u",
            "0",
            container,
            "sh",
            "-lc",
            "rm -rf /verifier /logs /tests && mkdir -p /verifier /logs/verifier /tests && chmod 0777 /logs /logs/verifier /tests",
        ],
        timeout_sec=30,
    )
    if commands["evaluation_paths_prepare"].exit_code != 0:
        return commands, None
    commands["verifier_copy"] = run_command(
        ["docker", "cp", f"{verifier_dir.resolve()}/.", f"{container}:/verifier/"],
        timeout_sec=120,
    )
    if commands["verifier_copy"].exit_code != 0:
        return commands, None
    commands["tests_copy"] = run_command(
        ["docker", "cp", f"{verifier_dir.resolve()}/.", f"{container}:/tests/"],
        timeout_sec=120,
    )
    if commands["tests_copy"].exit_code != 0:
        return commands, None
    commands["verifier"] = run_command(
        [
            "docker",
            "exec",
            *docker_env_args(task_phase_env(task_text, "verifier")),
            container,
            "bash",
            "/verifier/test.sh",
        ],
        timeout_sec=timeout_sec,
    )
    commands["logs_copy"] = run_command(
        ["docker", "cp", f"{container}:/logs/.", str(logs_dir.resolve())],
        timeout_sec=120,
    )
    return commands, read_reward(logs_dir)


def load_conditions(matrix_path: Path, only: list[str] | None) -> list[dict[str, Any]]:
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    selected = set(only or [])
    conditions = [
        condition
        for condition in matrix["conditions"]
        if not selected or condition["id"] in selected
    ]
    if selected and len(conditions) != len(selected):
        found = {condition["id"] for condition in conditions}
        missing = sorted(selected - found)
        raise ValueError(f"Unknown condition ids: {', '.join(missing)}")
    return conditions


def ready_task_ids(summary_path: Path) -> set[str]:
    return {
        task_id
        for task_id, record in load_readiness_records(summary_path).items()
        if record.get("passed") is True
    }


def load_readiness_records(summary_path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        record["task_id"]: record
        for record in data.get("records", [])
        if isinstance(record, dict) and isinstance(record.get("task_id"), str)
    }


def validate_readiness_selection(
    task_ids: list[str],
    records: dict[str, dict[str, Any]],
    *,
    policy: str,
) -> list[str]:
    missing = [task_id for task_id in task_ids if task_id not in records]
    if missing:
        raise ValueError("Tasks are absent from readiness evidence: " + ", ".join(missing))
    exceptions = [task_id for task_id in task_ids if records[task_id].get("passed") is not True]
    if policy == "passed" and exceptions:
        raise ValueError(
            "Tasks are not in executable readiness passed set: " + ", ".join(exceptions)
        )
    if policy != "all":
        return []
    return exceptions


def summarize(records: list[PilotRecord]) -> dict[str, Any]:
    by_key: dict[str, dict[str, Any]] = {}
    for record in records:
        key = f"{record.condition_id}:{record.arm}"
        slot = by_key.setdefault(
            key,
            {"n": 0, "passed": 0, "reward_observed": 0, "reward_sum": 0.0},
        )
        slot["n"] += 1
        slot["passed"] += int(record.passed)
        if record.reward is not None:
            slot["reward_observed"] += 1
            slot["reward_sum"] += record.reward
    for slot in by_key.values():
        observed = slot["reward_observed"]
        slot["mean_observed_reward"] = slot.pop("reward_sum") / observed if observed else None
    return {
        "record_count": len(records),
        "by_condition_arm": by_key,
    }


def append_record_jsonl(path: Path, record: PilotRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run SkillsBench C0/C1 model pilot.")
    parser.add_argument("--task", action="append", help="Task id. May be repeated.")
    parser.add_argument("--limit-tasks", type=int, default=None)
    parser.add_argument("--condition", action="append", help="Backend matrix condition id. May be repeated.")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--oracle-summary", type=Path, default=DEFAULT_ORACLE_SUMMARY)
    parser.add_argument(
        "--readiness-policy",
        choices=("passed", "all"),
        default="passed",
        help="Use 'all' only for a pre-registered full-denominator run that records readiness exceptions.",
    )
    parser.add_argument("--run-id", default=time.strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument("--agent-timeout-sec", type=int, default=900)
    parser.add_argument("--build-timeout-sec", type=int, default=1200)
    parser.add_argument("--verifier-timeout-sec", type=int, default=900)
    parser.add_argument("--harness-mode", default=DEFAULT_HARNESS_MODE)
    parser.add_argument("--trial-index", type=int, default=1)
    parser.add_argument("--keep-image", action="store_true")
    parser.add_argument(
        "--discard-workspace",
        action="store_true",
        help="Retain manifests, traces, prompts, and verifier logs but not the potentially large task workspace.",
    )
    args = parser.parse_args(argv)

    run_start = time.monotonic()
    run_root = args.runs_root / args.run_id
    run_root.mkdir(parents=True, exist_ok=True)
    records_jsonl = run_root / "records.jsonl"
    records_jsonl.write_text("", encoding="utf-8")
    task_ids = args.task or list(DEFAULT_TASKS)
    if args.limit_tasks is not None:
        task_ids = task_ids[: args.limit_tasks]
    readiness_records = load_readiness_records(args.oracle_summary)
    try:
        readiness_exceptions = validate_readiness_selection(
            task_ids,
            readiness_records,
            policy=args.readiness_policy,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    conditions = load_conditions(args.matrix, args.condition)
    unsupported = [condition["id"] for condition in conditions if condition["backend"] != "claude"]
    if unsupported:
        raise SystemExit(
            "Secure account-auth agentic bridge currently supports Claude CLI only: "
            + ", ".join(unsupported)
        )
    backend_versions = {
        condition["id"]: probe_backend_version(condition)
        for condition in conditions
    }
    isolated_workspace_root = Path(tempfile.gettempdir()) / f"theking-pilot-{safe_name(args.run_id)}"
    if isolated_workspace_root.exists():
        shutil.rmtree(isolated_workspace_root)
    isolated_workspace_root.mkdir(parents=True)
    dockerd_proc = None
    records: list[PilotRecord] = []
    try:
        dockerd_proc = ensure_docker(run_root)
        for task_id in task_ids:
            task_dir = TASKS_ROOT / task_id
            task_source = read_task_prompt(task_dir)
            task_text = task_instruction_body(task_source)
            task_instruction_sha = hashlib.sha256(task_text.encode("utf-8")).hexdigest()
            effective_build_timeout = int(
                task_section_number(task_source, "environment", "build_timeout_sec")
                or args.build_timeout_sec
            )
            effective_agent_timeout = int(
                task_section_number(task_source, "agent", "timeout_sec")
                or args.agent_timeout_sec
            )
            effective_verifier_timeout = int(
                task_section_number(task_source, "verifier", "timeout_sec")
                or args.verifier_timeout_sec
            )
            image = f"theking-skillsbench-model-{safe_name(task_id)}:latest"
            with tempfile.TemporaryDirectory(prefix=f"build-{safe_name(task_id)}-", dir=run_root) as tmp:
                build_context = prepare_skill_free_build_context(
                    task_dir / "environment",
                    Path(tmp) / "environment",
                )
                build = run_command(
                    ["docker", "build", "-t", image, str(build_context)],
                    timeout_sec=effective_build_timeout,
                )
            if build.exit_code != 0:
                for condition in conditions:
                    for arm in ("C0", "C1"):
                        record = PilotRecord(
                            task_id=task_id,
                            condition_id=condition["id"],
                            arm=arm,
                            harness_mode=args.harness_mode,
                            model_id=condition["model_id"],
                            backend=condition["backend"],
                            effort=condition["effort"],
                            runtime_effort=condition.get("runtime_effort", condition["effort"]),
                            status="build_failed",
                            passed=False,
                            trial_index=args.trial_index,
                            trial_id=f"{args.run_id}:{task_id}:{condition['id']}:{arm}:trial-{args.trial_index}",
                            wall_time_sec=build.duration_sec,
                            commands={
                                "build": build,
                                "backend_version": backend_versions[condition["id"]],
                            },
                        )
                        records.append(record)
                        append_record_jsonl(records_jsonl, record)
                continue

            try:
                image_id_report = run_command(
                    ["docker", "image", "inspect", "-f", "{{.Id}}", image],
                    timeout_sec=30,
                )
                container_workdir, workdir_report = detect_image_workdir(image)
                for condition in conditions:
                    arm_order = ("C0", "C1") if args.trial_index % 2 else ("C1", "C0")
                    common_prompt = build_agent_prompt(
                        task_id=task_id,
                        task_text=task_text,
                        container_workdir=container_workdir,
                    )
                    common_prompt_sha = hashlib.sha256(common_prompt.encode("utf-8")).hexdigest()
                    if common_prompt_sha != task_instruction_sha:
                        raise RuntimeError(
                            "body-only prompt contract violated: prompt hash differs from task instruction hash"
                        )
                    for arm_order_position, arm in enumerate(arm_order, start=1):
                        trial_start = time.monotonic()
                        condition_dir = run_root / "tasks" / task_id / condition["id"] / arm
                        if condition_dir.exists():
                            shutil.rmtree(condition_dir)
                        condition_dir.mkdir(parents=True)
                        opaque_root = isolated_workspace_root / uuid.uuid4().hex
                        workspace = opaque_root / "workspace"
                        provider_project = opaque_root / "project"
                        auth_root = opaque_root / "auth"
                        debug_file = opaque_root / "provider-debug.log"
                        mcp_protocol_audit_path = opaque_root / "mcp-protocol-audit.jsonl"
                        persisted_workspace = condition_dir / "workspace"
                        logs_dir = condition_dir / "logs"
                        agent_output_path = condition_dir / "agent-output.jsonl"
                        commands = {
                            "build": build,
                            "image_id": image_id_report,
                            "image_workdir": workdir_report,
                            "backend_version": backend_versions[condition["id"]],
                        }
                        materialize_commands = materialize_image_workspace(
                            image=image,
                            workspace=workspace,
                            container_workdir=container_workdir,
                            task_id=task_id,
                        )
                        commands.update(materialize_commands)
                        reward: float | None = None
                        score_source: str | None = None
                        record_notes: list[str] = []
                        status = "workspace_materialization_failed"
                        passed = False
                        prompt = common_prompt
                        container: str | None = None
                        native_skill_path: Path | None = None
                        workspace_manifest_pre_verifier: dict[str, Any] = {}
                        container_exposure: dict[str, Any] = {}
                        tool_trace: dict[str, Any] = {}
                        control_barrier: dict[str, Any] = {}
                        configuration_audit: dict[str, Any] = {
                            "arm_order_position": arm_order_position,
                            "arm_order": list(arm_order),
                            "prompt_shared_across_arms": True,
                            "prompt_equals_task_instruction": True,
                            "readiness": {
                                "policy": args.readiness_policy,
                                "passed": readiness_records[task_id].get("passed") is True,
                                "execution_ready": readiness_records[task_id].get(
                                    "execution_ready"
                                ),
                                "status": readiness_records[task_id].get("status"),
                                "reward": readiness_records[task_id].get("reward"),
                            },
                        }
                        skill_source = task_dir / "environment" / "skills"
                        task_bundle_skill_names = bundle_skill_names(skill_source)
                        source_skill_manifest = (
                            directory_manifest(skill_source)
                            if arm == "C1"
                            else {"file_count": 0, "total_bytes": 0, "sha256": None}
                        )
                        workspace_copy = materialize_commands.get("workspace_copy_from_image")
                        if workspace_copy is not None and workspace_copy.exit_code == 0:
                            native_skill_path = prepare_provider_project(
                                provider_project=provider_project,
                                task_dir=task_dir,
                                backend=condition["backend"],
                                include_skills=arm == "C1",
                            )
                            provider_env, isolation = prepare_isolated_claude_environment(auth_root)
                            auth_status, auth_report = probe_claude_account_auth(
                                cwd=provider_project,
                                env=provider_env,
                            )
                            plugin_count, plugin_report = probe_claude_plugins(
                                cwd=provider_project,
                                env=provider_env,
                            )
                            commands["account_auth"] = auth_report
                            commands["plugin_preflight"] = plugin_report
                            configuration_audit.update(
                                {
                                    "isolated_environment": isolation,
                                    "account_auth": auth_status,
                                    "plugin_count": plugin_count,
                                }
                            )
                            auth_ok = (
                                auth_report.exit_code == 0
                                and auth_status["logged_in"]
                                and auth_status["auth_method"] == "claude.ai"
                                and auth_status["api_provider"] == "firstParty"
                                and plugin_count == 0
                            )
                            if not auth_ok:
                                status = "account_isolation_preflight_failed"
                            else:
                                container, container_start = start_bound_task_container(
                                    image=image,
                                    workspace=workspace,
                                    task_id=task_id,
                                    task_text=task_source,
                                    container_workdir=container_workdir,
                                    skills_source=skill_source if arm == "C1" else None,
                                    native_skill_container_path=native_skill_path,
                                )
                                commands["container_start"] = container_start
                                if container_start.exit_code != 0:
                                    status = "container_start_failed"
                                else:
                                    try:
                                        sanitize = sanitize_container_control_paths(container)
                                        commands["container_control_sanitize"] = sanitize
                                        relative_skill_root = Path(
                                            f"{container_workdir.rstrip('/')}/skills"
                                        )
                                        expected_roots = (
                                            [relative_skill_root, native_skill_path]
                                            if arm == "C1" and native_skill_path is not None
                                            else []
                                        )
                                        container_exposure, exposure_report = scan_container_pre_agent(
                                            container=container,
                                            container_workdir=container_workdir,
                                            expected_skill_roots=expected_roots,
                                        )
                                        commands["container_exposure_scan"] = exposure_report
                                        native_manifest = (
                                            directory_manifest(native_skill_path)
                                            if native_skill_path is not None
                                            else {"file_count": 0, "total_bytes": 0, "sha256": None}
                                        )
                                        manifest_match = native_manifest == source_skill_manifest
                                        container_exposure["source_native_manifest_match"] = manifest_match
                                        container_exposure["native_manifest"] = native_manifest
                                        container_exposure["passed"] = bool(
                                            sanitize.exit_code == 0
                                            and container_exposure.get("passed")
                                            and manifest_match
                                        )
                                        if not container_exposure["passed"]:
                                            status = "container_exposure_preflight_failed"
                                        else:
                                            mcp_config = make_container_mcp_config(
                                                container=container,
                                                container_workdir=container_workdir,
                                                timeout_sec=effective_agent_timeout,
                                                audit_path=mcp_protocol_audit_path,
                                            )
                                            (condition_dir / "prompt.txt").write_text(
                                                prompt,
                                                encoding="utf-8",
                                            )
                                            agent_report = run_agent(
                                                condition,
                                                prompt,
                                                provider_project=provider_project,
                                                timeout_sec=effective_agent_timeout,
                                                mcp_config=mcp_config,
                                                settings=CONTROLLED_CLAUDE_SETTINGS,
                                                raw_output_path=agent_output_path,
                                                debug_file=debug_file,
                                                env=provider_env,
                                                barrier_evidence=control_barrier,
                                            )
                                            commands["agent"] = agent_report
                                            tool_trace = extract_agent_trace(agent_output_path)
                                            safe_trace_audit = analyze_trace(agent_output_path)
                                            tool_trace["skill_calls"] = safe_trace_audit[
                                                "tool_calls"
                                            ]["skill_calls"]
                                            mcp_protocol_audit = summarize_mcp_protocol_audit(
                                                mcp_protocol_audit_path
                                            )
                                            if mcp_protocol_audit_path.exists():
                                                shutil.copy2(
                                                    mcp_protocol_audit_path,
                                                    condition_dir / "mcp-protocol-audit.jsonl",
                                                )
                                            debug_audit = audit_debug_log(
                                                debug_file,
                                                original_home=Path(isolation["original_home"]),
                                            )
                                            if debug_file.exists():
                                                debug_file.unlink()
                                            configuration_audit.update(
                                                {
                                                    "debug": debug_audit,
                                                    "raw_debug_retained": False,
                                                    "tool_surface_command_identical": True,
                                                    "mcp_protocol": mcp_protocol_audit,
                                                    "control_barrier_passed": bool(
                                                        control_barrier.get("passed")
                                                    ),
                                                }
                                            )
                                            mcp_names = set(tool_trace.get("mcp_servers", []))
                                            mcp_statuses = tool_trace.get(
                                                "mcp_server_statuses",
                                                {},
                                            )
                                            observed_skills = set(
                                                tool_trace.get("advertised_skills", [])
                                            )
                                            visible_task_skills = sorted(
                                                observed_skills.intersection(task_bundle_skill_names)
                                            )
                                            task_skill_visibility_ok = (
                                                not visible_task_skills
                                                if arm == "C0"
                                                else set(task_bundle_skill_names).issubset(
                                                    observed_skills
                                                )
                                            )
                                            resolved_assistant_models = tool_trace.get(
                                                "assistant_models", []
                                            )
                                            resolved_model_matches_request = assistant_models_match_request(
                                                tool_trace,
                                                condition["model_id"],
                                            )
                                            configuration_audit.update(
                                                {
                                                    "controlled_settings": CONTROLLED_CLAUDE_SETTINGS,
                                                    "mcp_server_statuses": mcp_statuses,
                                                    "expected_task_skill_names": (
                                                        task_bundle_skill_names
                                                        if arm == "C1"
                                                        else []
                                                    ),
                                                    "visible_task_skill_names": visible_task_skills,
                                                    "provider_builtin_skills": sorted(
                                                        observed_skills.difference(
                                                            task_bundle_skill_names
                                                        )
                                                    ),
                                                    "expected_model_id": condition["model_id"],
                                                    "resolved_assistant_models": resolved_assistant_models,
                                                    "resolved_model_matches_request": resolved_model_matches_request,
                                                }
                                            )
                                            configuration_ok = bool(
                                                not tool_trace.get("unexpected_advertised_tools")
                                                and not tool_trace.get("plugins")
                                                and tool_trace.get("hook_event_count") == 0
                                                and tool_trace.get("malformed_line_count") == 0
                                                and (
                                                    not mcp_names
                                                    or mcp_names == {"task_container"}
                                                )
                                                and mcp_protocol_audit.get("present")
                                                and mcp_protocol_audit.get("malformed_line_count") == 0
                                                and control_barrier.get("passed") is True
                                                and control_barrier.get("task_event", {}).get("count") == 1
                                                and control_barrier.get("warmup_model_turn_count") == 0
                                                and CLAUDE_INITIALIZE_REQUEST_ID
                                                in tool_trace.get("control_response_ids", [])
                                                and any(
                                                    request_id.startswith(
                                                        CLAUDE_MCP_STATUS_REQUEST_PREFIX
                                                    )
                                                    for request_id in tool_trace.get(
                                                        "control_response_ids", []
                                                    )
                                                )
                                                and tool_trace.get("replayed_user_message_count") == 1
                                                and tool_trace.get("system_init_count") == 1
                                                and tool_trace.get("result_event_count") == 1
                                                and "initialize" in mcp_protocol_audit.get("methods", [])
                                                and "tools/list" in mcp_protocol_audit.get("methods", [])
                                                and "tools/call" in mcp_protocol_audit.get("methods", [])
                                                and tool_trace.get("mcp_exec_call_count", 0) > 0
                                                and task_skill_visibility_ok
                                                and resolved_model_matches_request
                                                and debug_audit.get("suspicious_line_count") == 0
                                            )
                                            configuration_audit["passed"] = configuration_ok
                                            if not control_barrier.get("passed"):
                                                status = "mcp_control_barrier_failed"
                                            elif agent_report.exit_code != 0:
                                                (
                                                    status,
                                                    reward,
                                                    score_source,
                                                    noncompletion_notes,
                                                ) = classify_agent_noncompletion(
                                                    agent_report
                                                )
                                                record_notes.extend(noncompletion_notes)
                                            elif not configuration_ok:
                                                status = "configuration_contaminated"
                                            else:
                                                process_audit, process_report = container_process_snapshot(
                                                    container
                                                )
                                                commands["container_process_snapshot"] = process_report
                                                configuration_audit["process_quiescence"] = process_audit
                                                if not process_audit["passed"]:
                                                    status = "container_not_quiescent"
                                                else:
                                                    workspace_manifest_pre_verifier = directory_manifest(
                                                        workspace
                                                    )
                                                    verifier_commands, reward = stage_and_run_verifier(
                                                        container=container,
                                                        task_dir=task_dir,
                                                        task_text=task_source,
                                                        logs_dir=logs_dir,
                                                        timeout_sec=effective_verifier_timeout,
                                                    )
                                                    commands.update(verifier_commands)
                                                    verifier = verifier_commands.get("verifier")
                                                    if verifier is None:
                                                        status = "verifier_staging_failed"
                                                    else:
                                                        status, passed = classify_verifier_result(
                                                            verifier,
                                                            reward,
                                                        )
                                                        configuration_audit[
                                                            "verifier_invocation_count"
                                                        ] = 1
                                    finally:
                                        commands["container_cleanup"] = run_command(
                                            ["docker", "rm", "-f", container],
                                            timeout_sec=60,
                                        )
                        if workspace.exists() and not args.discard_workspace:
                            if persisted_workspace.exists():
                                shutil.rmtree(persisted_workspace)
                            shutil.copytree(workspace, persisted_workspace, symlinks=True)
                        record = PilotRecord(
                            task_id=task_id,
                            condition_id=condition["id"],
                            arm=arm,
                            harness_mode=args.harness_mode,
                            model_id=condition["model_id"],
                            backend=condition["backend"],
                            effort=condition["effort"],
                            runtime_effort=condition.get("runtime_effort", condition["effort"]),
                            status=status,
                            passed=passed,
                            trial_index=args.trial_index,
                            trial_id=f"{args.run_id}:{task_id}:{condition['id']}:{arm}:trial-{args.trial_index}",
                            reward=reward,
                            score_source=score_source,
                            wall_time_sec=round(time.monotonic() - trial_start, 3),
                            account_usage=extract_account_usage_file(agent_output_path),
                            agent_output_path=str(agent_output_path),
                            agent_output_sha256=(
                                hashlib.sha256(agent_output_path.read_bytes()).hexdigest()
                                if agent_output_path.exists()
                                else None
                            ),
                            task_instruction_sha256=task_instruction_sha,
                            prompt_sha256=common_prompt_sha,
                            skill_delivery={
                                "mode": "none"
                                if arm == "C0"
                                else "complete_bundle_provider_native_and_container_read_only",
                                "native_path": None
                                if arm == "C0"
                                else (".claude/skills" if condition["backend"] == "claude" else ".agents/skills"),
                                **source_skill_manifest,
                            },
                            workspace=str(persisted_workspace) if persisted_workspace.exists() else None,
                            container_workdir=container_workdir,
                            provider_project="ephemeral_opaque_outside_repository",
                            workspace_manifest_pre_verifier=workspace_manifest_pre_verifier,
                            container_exposure=container_exposure,
                            tool_trace=tool_trace,
                            control_barrier=control_barrier,
                            configuration_audit=configuration_audit,
                            logs_dir=str(logs_dir),
                            commands=commands,
                            notes=record_notes,
                        )
                        records.append(record)
                        append_record_jsonl(records_jsonl, record)
                        print(json.dumps(asdict(record), ensure_ascii=False), flush=True)
            finally:
                if not args.keep_image:
                    run_command(["docker", "rmi", "-f", image], timeout_sec=120)
    finally:
        stop_process(dockerd_proc)
        if isolated_workspace_root.exists():
            shutil.rmtree(isolated_workspace_root)

    output = {
        "run_id": args.run_id,
        "harness_mode": args.harness_mode,
        "prompt_contract": dict(PAPER_CLI_MCP_PROMPT_CONTRACT),
        "benchmark_eligibility": {
            "skillsbench_paper_c0_c1": False,
            "paper_aligned_agentic_pilot": True,
            "reasons": list(BENCHMARK_INELIGIBILITY_REASONS),
        },
        "backend_contract": {
            "type": "B_cli",
            "auth_mode": "user_owned_account",
            "api_keys_required": False,
            "credentials_forwarded_to_container": False,
        },
        "configuration_isolation": {
            "workspace_outside_repository": True,
            "opaque_provider_cwd_without_arm_label": True,
            "fresh_home_per_arm": True,
            "claude_setting_sources": ["project"],
            "strict_explicit_mcp_config": True,
            "host_tools_exposed": [],
            "container_tools_exposed": ["mcp__task_container__exec", "Skill"],
            "parent_repository_instructions_visible": False,
            "plugin_hook_global_skill_audit_per_arm": True,
        },
        "image_contract": {
            "build_context_task_skills_replaced_with_empty_directory": True,
            "agent_phase_verifier_present": False,
            "agent_phase_reward_logs_present": False,
            "verifier_copied_after_agent_exit": True,
            "same_container_agent_and_verifier": True,
            "credentials_embedded": False,
            "keep_image_after_run": args.keep_image,
            "workspace_persisted_after_run": not args.discard_workspace,
        },
        "readiness_contract": {
            "source": str(args.oracle_summary),
            "source_sha256": hashlib.sha256(args.oracle_summary.read_bytes()).hexdigest(),
            "policy": args.readiness_policy,
            "requested_task_count": len(task_ids),
            "exception_task_ids": readiness_exceptions,
        },
        "trial_control": {
            "trial_index": args.trial_index,
            "seed_control": "provider_cli_unavailable",
            "temperature_control": "provider_default_or_unset",
            "paper_required_trials_per_cell": 3,
        },
        "task_ids": task_ids,
        "condition_ids": [condition["id"] for condition in conditions],
        "backend_versions": {
            condition_id: asdict(report)
            for condition_id, report in backend_versions.items()
        },
        "timeouts": {
            "generation_timeout_sec": args.agent_timeout_sec,
            "script_execution_timeout_sec": None,
            "verifier_timeout_sec": args.verifier_timeout_sec,
            "build_timeout_sec": args.build_timeout_sec,
            "task_frontmatter_budget_overrides": True,
            "task_frontmatter_exposed_to_model": False,
        },
        "wall_time_sec": round(time.monotonic() - run_start, 3),
        "records": [asdict(record) for record in records],
        "summary": summarize(records),
    }
    (run_root / "summary.json").write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(output["summary"], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
