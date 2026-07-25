"""Minimal MCP bridge for executing commands in one bound task container.

The account-auth model CLI runs on the host, while this server exposes exactly
one model-facing operation: execute a shell command inside a container chosen
when the server starts.  The container identifier and working directory never
appear in the tool arguments, so a caller cannot redirect execution to the host
or to another container.

Messages use MCP's newline-delimited JSON-RPC stdio transport.  Protocol data is
written only to stdout; this module deliberately has no routine stdout logging.
The task image must provide GNU-compatible ``timeout`` and ``bash`` binaries;
the experiment runner is responsible for checking that contract before use.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence, TextIO


PROTOCOL_VERSION = "2024-11-05"
SUPPORTED_PROTOCOL_VERSIONS = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    PROTOCOL_VERSION,
)
SERVER_NAME = "merlin-task-container-exec"
SERVER_VERSION = "1.1.0"
TOOL_NAME = "exec"
DEFAULT_TIMEOUT_SEC = 900
MAX_COMMAND_CHARS = 65_536
MAX_OUTPUT_CHARS = 65_536
HOST_TIMEOUT_GRACE_SEC = 5
TIMEOUT_KILL_AFTER_SEC = 2

CONTAINER_ENV = "TASK_CONTAINER_ID"
WORKDIR_ENV = "TASK_CONTAINER_WORKDIR"
TIMEOUT_ENV = "TASK_CONTAINER_TIMEOUT_SEC"
AUDIT_LOG_ENV = "TASK_CONTAINER_MCP_AUDIT_LOG"
ALLOWED_SKILL_IDS_FILE_ENV = "TASK_CONTAINER_ALLOWED_SKILL_IDS_FILE"

_CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._-]{0,255}$")


@dataclass(frozen=True)
class ServerConfig:
    """Immutable model-inaccessible execution boundary."""

    container: str
    workdir: str
    timeout_sec: int
    audit_log: str | None = None
    allowed_skill_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _CONTAINER_RE.fullmatch(self.container):
            raise ValueError("container must be a Docker container id or name")
        if "\x00" in self.workdir or "\n" in self.workdir or "\r" in self.workdir:
            raise ValueError("workdir contains an invalid character")
        if not self.workdir.startswith("/"):
            raise ValueError("workdir must be an absolute container path")
        normalized = posixpath.normpath(self.workdir)
        if normalized != self.workdir:
            raise ValueError(f"workdir must be normalized (expected {normalized!r})")
        if isinstance(self.timeout_sec, bool) or not isinstance(self.timeout_sec, int):
            raise ValueError("timeout_sec must be an integer")
        if self.timeout_sec < 1:
            raise ValueError("timeout_sec must be at least 1")
        if self.audit_log is not None and (
            "\x00" in self.audit_log or "\n" in self.audit_log or "\r" in self.audit_log
        ):
            raise ValueError("audit_log contains an invalid character")
        if len(set(self.allowed_skill_ids)) != len(self.allowed_skill_ids):
            raise ValueError("allowed_skill_ids must not contain duplicates")
        if any(not _SKILL_ID_RE.fullmatch(item) for item in self.allowed_skill_ids):
            raise ValueError("allowed_skill_ids contains an unsafe skill ID")


@dataclass(frozen=True)
class ExecutionReport:
    exit_code: int
    duration_sec: float
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


Executor = Callable[[ServerConfig, str, int], ExecutionReport]


def normalize_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def cap_output(text: str, limit: int = MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    """Keep a bounded head and tail while reporting whether text was truncated."""

    if limit < 1:
        raise ValueError("output limit must be at least 1")
    if len(text) <= limit:
        return text, False
    marker = "\n...[output truncated]...\n"
    if limit <= len(marker):
        return marker[:limit], True
    available = limit - len(marker)
    head_chars = available // 2
    tail_chars = available - head_chars
    return text[:head_chars] + marker + text[-tail_chars:], True


def docker_subprocess_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the minimal host environment needed by the Docker client."""

    source = os.environ if environ is None else environ
    result = {"PATH": source.get("PATH", os.defpath)}
    docker_host = source.get("DOCKER_HOST")
    if docker_host:
        result["DOCKER_HOST"] = docker_host
    return result


def docker_exec_argv(
    config: ServerConfig,
    command: str,
    timeout_sec: int,
) -> list[str]:
    """Build an argv-only Docker invocation; no host shell is involved."""

    effective_timeout = min(timeout_sec, config.timeout_sec)
    return [
        "docker",
        "exec",
        "-w",
        config.workdir,
        config.container,
        "timeout",
        "--signal=TERM",
        f"--kill-after={TIMEOUT_KILL_AFTER_SEC}s",
        f"{effective_timeout}s",
        "bash",
        "-lc",
        command,
    ]


def execute_in_container(
    config: ServerConfig,
    command: str,
    timeout_sec: int,
) -> ExecutionReport:
    """Execute a command through ``docker exec`` with captured output."""

    effective_timeout = min(timeout_sec, config.timeout_sec)
    start = time.monotonic()
    try:
        completed = subprocess.run(
            docker_exec_argv(config, command, effective_timeout),
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=effective_timeout + HOST_TIMEOUT_GRACE_SEC,
            check=False,
            env=docker_subprocess_env(),
        )
        container_timed_out = completed.returncode in {124, 137}
        return ExecutionReport(
            exit_code=completed.returncode,
            duration_sec=round(time.monotonic() - start, 3),
            stdout=normalize_text(completed.stdout),
            stderr=normalize_text(completed.stderr),
            timed_out=container_timed_out,
        )
    except subprocess.TimeoutExpired as exc:
        return ExecutionReport(
            exit_code=124,
            duration_sec=round(time.monotonic() - start, 3),
            stdout=normalize_text(exc.stdout),
            stderr=normalize_text(exc.stderr),
            timed_out=True,
        )
    except FileNotFoundError:
        return ExecutionReport(
            exit_code=127,
            duration_sec=round(time.monotonic() - start, 3),
            stderr="docker executable not found",
        )
    except OSError:
        return ExecutionReport(
            exit_code=126,
            duration_sec=round(time.monotonic() - start, 3),
            stderr="docker exec failed",
        )


def tool_definition(config: ServerConfig) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "command": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_COMMAND_CHARS,
            "description": "Shell command evaluated by bash -lc inside the task container.",
        },
        "timeout_sec": {
            "type": "integer",
            "minimum": 1,
            "maximum": config.timeout_sec,
            "description": (
                "Optional per-command timeout. Values above the server cap "
                f"are reduced to {config.timeout_sec} seconds."
            ),
        },
    }
    if config.allowed_skill_ids:
        properties["skill_id"] = {
            "type": "string",
            "enum": list(config.allowed_skill_ids),
            "description": (
                "Set only when this command applies one provisioned skill. "
                "Omit for general task work or no-skill fallback."
            ),
        }
    return {
        "name": TOOL_NAME,
        "description": (
            "Execute one shell command inside the task container's fixed working "
            "directory. The command cannot select a container or host path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": ["command"],
            "additionalProperties": False,
        },
    }


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(
    request_id: Any,
    code: int,
    message: str,
    *,
    data: Any | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        payload["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": payload}


def _tool_call(
    request_id: Any,
    params: Any,
    config: ServerConfig,
    executor: Executor,
) -> dict[str, Any]:
    if not isinstance(params, dict):
        return _error(request_id, -32602, "tools/call params must be an object")
    if params.get("name") != TOOL_NAME:
        return _error(request_id, -32602, f"unknown tool: {params.get('name')!r}")

    arguments = params.get("arguments", {})
    if not isinstance(arguments, dict):
        return _error(request_id, -32602, "tool arguments must be an object")
    unexpected = sorted(set(arguments) - {"command", "timeout_sec", "skill_id"})
    if unexpected:
        return _error(
            request_id,
            -32602,
            "unexpected tool arguments",
            data={"unexpected": unexpected},
        )

    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return _error(request_id, -32602, "command must be a non-empty string")
    if len(command) > MAX_COMMAND_CHARS:
        return _error(
            request_id,
            -32602,
            f"command exceeds the {MAX_COMMAND_CHARS}-character limit",
        )
    if "\x00" in command:
        return _error(request_id, -32602, "command contains an invalid null byte")

    skill_id = arguments.get("skill_id")
    if skill_id is not None:
        if not isinstance(skill_id, str) or skill_id not in config.allowed_skill_ids:
            return _error(request_id, -32602, "skill_id is not provisioned for this run")

    requested_timeout = arguments.get("timeout_sec", config.timeout_sec)
    if isinstance(requested_timeout, bool) or not isinstance(requested_timeout, int):
        return _error(request_id, -32602, "timeout_sec must be an integer")
    if requested_timeout < 1:
        return _error(request_id, -32602, "timeout_sec must be at least 1")
    effective_timeout = min(requested_timeout, config.timeout_sec)

    try:
        report = executor(config, command, effective_timeout)
    except Exception:  # pragma: no cover - defensive boundary for injected executors
        return _error(request_id, -32603, "container execution failed")

    stdout, stdout_truncated = cap_output(report.stdout)
    stderr, stderr_truncated = cap_output(report.stderr)
    payload = {
        "stdout": stdout,
        "stdout_truncated": stdout_truncated,
        "stderr": stderr,
        "stderr_truncated": stderr_truncated,
        "exit_code": report.exit_code,
        "duration_sec": report.duration_sec,
        "timed_out": report.timed_out,
        "timeout_sec": effective_timeout,
        "timeout_capped": requested_timeout > effective_timeout,
    }
    return _result(
        request_id,
        {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                }
            ],
            "isError": report.timed_out or report.exit_code in {125, 126, 127},
        },
    )


def negotiate_protocol_version(params: Any) -> str:
    """Select a protocol version without falsely echoing unknown versions.

    MCP clients may request a newer version than the original bridge used.  The
    protocol requires a server to echo a supported requested version and to
    fall back to a version it supports otherwise.
    """

    if isinstance(params, dict):
        requested = params.get("protocolVersion")
        if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
            return requested
    return PROTOCOL_VERSION


def _audit_write(stream: TextIO | None, event: Mapping[str, Any]) -> None:
    """Write one metadata-only audit event without affecting the MCP channel."""

    if stream is None:
        return
    payload = {"time_unix": round(time.time(), 6), **event}
    try:
        stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        stream.write("\n")
        stream.flush()
    except (OSError, ValueError):
        # Diagnostics must never corrupt or terminate the JSON-RPC transport.
        return


def _request_audit_event(message: Any) -> dict[str, Any]:
    """Summarize a request while deliberately excluding tool arguments."""

    if not isinstance(message, dict):
        return {"direction": "client_to_server", "event": "invalid_request"}
    event: dict[str, Any] = {
        "direction": "client_to_server",
        "event": "notification" if "id" not in message else "request",
        "method": message.get("method"),
    }
    if "id" in message and isinstance(message.get("id"), (str, int)):
        event["id"] = message["id"]
    if message.get("method") == "initialize" and isinstance(message.get("params"), dict):
        requested = message["params"].get("protocolVersion")
        if isinstance(requested, str):
            event["requested_protocol_version"] = requested
    if message.get("method") == "tools/call" and isinstance(message.get("params"), dict):
        tool_name = message["params"].get("name")
        if isinstance(tool_name, str):
            event["tool_name"] = tool_name
        arguments = message["params"].get("arguments")
        if isinstance(arguments, dict):
            skill_id = arguments.get("skill_id")
            if isinstance(skill_id, str) and _SKILL_ID_RE.fullmatch(skill_id):
                event["skill_id"] = skill_id
    return event


def _response_audit_event(response: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize a response without recording tool output or error data."""

    event: dict[str, Any] = {
        "direction": "server_to_client",
        "event": "error" if "error" in response else "response",
    }
    request_id = response.get("id")
    if isinstance(request_id, (str, int)):
        event["id"] = request_id
    error = response.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), int):
        event["error_code"] = error["code"]
    result = response.get("result")
    if isinstance(result, dict):
        version = result.get("protocolVersion")
        if isinstance(version, str):
            event["negotiated_protocol_version"] = version
        tools = result.get("tools")
        if isinstance(tools, list):
            event["tool_count"] = len(tools)
        content = result.get("content")
        if isinstance(content, list) and len(content) == 1:
            item = content[0]
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                try:
                    metadata = json.loads(item["text"])
                except json.JSONDecodeError:
                    metadata = None
                if isinstance(metadata, dict):
                    exit_code = metadata.get("exit_code")
                    timed_out = metadata.get("timed_out")
                    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
                        event["tool_result_exit_code"] = exit_code
                    if isinstance(timed_out, bool):
                        event["tool_result_timed_out"] = timed_out
    return event


def handle_message(
    message: Any,
    config: ServerConfig,
    *,
    executor: Executor = execute_in_container,
) -> dict[str, Any] | None:
    """Handle one decoded JSON-RPC message.

    Valid notifications (requests without an ``id``) never produce a response,
    including ``notifications/initialized`` and cancellation notifications.
    """

    if not isinstance(message, dict):
        return _error(None, -32600, "invalid JSON-RPC request")
    request_id = message.get("id")
    if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _error(request_id if "id" in message else None, -32600, "invalid JSON-RPC request")

    if "id" not in message:
        return None

    method = message["method"]
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": negotiate_protocol_version(message.get("params")),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": [tool_definition(config)]})
    if method == "tools/call":
        return _tool_call(request_id, message.get("params"), config, executor)
    return _error(request_id, -32601, f"method not found: {method}")


def serve(
    config: ServerConfig,
    *,
    instream: TextIO = sys.stdin,
    outstream: TextIO = sys.stdout,
    executor: Executor = execute_in_container,
    auditstream: TextIO | None = None,
) -> int:
    """Serve newline-delimited JSON-RPC until stdin reaches EOF."""

    _audit_write(
        auditstream,
        {
            "direction": "server",
            "event": "start",
            "server_name": SERVER_NAME,
            "server_version": SERVER_VERSION,
        },
    )
    for raw_line in instream:
        if not raw_line.strip():
            continue
        try:
            message = json.loads(raw_line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            _audit_write(
                auditstream,
                {
                    "direction": "client_to_server",
                    "event": "parse_error",
                    "input_chars": len(raw_line),
                },
            )
            response = _error(None, -32700, "parse error")
        else:
            _audit_write(auditstream, _request_audit_event(message))
            response = handle_message(message, config, executor=executor)
        if response is not None:
            _audit_write(auditstream, _response_audit_event(response))
            outstream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
            outstream.write("\n")
            outstream.flush()
    _audit_write(auditstream, {"direction": "server", "event": "eof"})
    return 0


def parse_config(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> ServerConfig:
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(
        description="Expose a fixed Docker task container as one MCP exec tool.",
    )
    parser.add_argument("--container", default=env.get(CONTAINER_ENV))
    parser.add_argument("--workdir", default=env.get(WORKDIR_ENV))
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=env.get(TIMEOUT_ENV, str(DEFAULT_TIMEOUT_SEC)),
        help="server-side timeout cap for every tool call",
    )
    parser.add_argument(
        "--audit-log",
        default=env.get(AUDIT_LOG_ENV),
        help=(
            "optional metadata-only JSONL protocol audit path; use '-' for stderr "
            f"(or set {AUDIT_LOG_ENV})"
        ),
    )
    parser.add_argument(
        "--allowed-skill-ids-file",
        default=env.get(ALLOWED_SKILL_IDS_FILE_ENV),
        help="optional JSON array of provisioned skill IDs accepted by exec calls",
    )
    args = parser.parse_args(argv)
    if not args.container:
        parser.error(f"--container or {CONTAINER_ENV} is required")
    if not args.workdir:
        parser.error(f"--workdir or {WORKDIR_ENV} is required")
    try:
        allowed_skill_ids: tuple[str, ...] = ()
        if args.allowed_skill_ids_file:
            path = os.path.abspath(os.path.expanduser(args.allowed_skill_ids_file))
            if os.path.islink(path) or not os.path.isfile(path):
                raise ValueError("allowed skill IDs file must be a regular non-symlink file")
            try:
                with open(path, encoding="utf-8") as handle:
                    value = json.load(handle)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError("cannot read allowed skill IDs file") from exc
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError("allowed skill IDs file must contain a JSON string array")
            allowed_skill_ids = tuple(value)
        return ServerConfig(
            container=args.container,
            workdir=args.workdir,
            timeout_sec=args.timeout_sec,
            audit_log=args.audit_log,
            allowed_skill_ids=allowed_skill_ids,
        )
    except ValueError as exc:
        parser.error(str(exc))
        raise AssertionError("argparse.error always exits") from exc


def main(argv: Sequence[str] | None = None) -> int:
    config = parse_config(argv)
    if config.audit_log is None:
        return serve(config)
    if config.audit_log == "-":
        return serve(config, auditstream=sys.stderr)
    try:
        descriptor = os.open(
            config.audit_log,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        with os.fdopen(descriptor, "a", encoding="utf-8", buffering=1) as auditstream:
            return serve(config, auditstream=auditstream)
    except OSError as exc:
        print(f"cannot open MCP audit log: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
