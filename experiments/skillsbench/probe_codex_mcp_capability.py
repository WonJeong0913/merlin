"""Model-free, fail-closed capability probe for a Codex SkillsBench bridge.

This diagnostic deliberately does not submit a task or call a model.  It keeps
four facts separate:

1. the fixed-container MCP server's direct protocol/tool contract;
2. Codex CLI configuration and isolation controls visible in local help;
3. an optional metadata-only audit showing whether Codex actually called exec;
4. the stricter prerequisites for opening a benchmark pilot.

An MCP initialize/tools-list handshake is useful transport evidence, but it is
never promoted into model tool-use or benchmark-result evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SERVER = ROOT / "experiments" / "skillsbench" / "container_exec_mcp.py"
DEFAULT_CODEX_CANDIDATES = (
    Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
    Path("/usr/local/bin/codex"),
    Path("/opt/homebrew/bin/codex"),
)
PROBE_PROTOCOL_VERSION = "2025-06-18"
MAX_HELP_BYTES = 1_000_000
MAX_AUDIT_BYTES = 2_000_000
MAX_SCHEMA_BYTES = 8_000_000
NATIVE_TOOL_FEATURES_TO_DISABLE = (
    "apps",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "code_mode_host",
    "computer_use",
    "hooks",
    "image_generation",
    "in_app_browser",
    "multi_agent",
    "plugins",
    "remote_plugin",
    "shell_snapshot",
    "shell_tool",
    "skill_mcp_dependency_install",
    "skill_search",
    "tool_call_mcp_elicitation",
    "tool_suggest",
    "unified_exec",
    "workspace_dependencies",
)


class CapabilityProbeError(ValueError):
    """Raised when probe inputs or subprocess evidence are malformed."""


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def detect_codex_executable(
    explicit: Path | None = None,
    *,
    runner: RunCommand = subprocess.run,
) -> Path:
    """Resolve one executable that can actually answer ``--version``.

    A PATH shim may remain executable after its packaged vendor binary has
    disappeared. File mode alone is therefore insufficient. Explicit paths
    fail closed; automatic discovery skips broken shims and continues to the
    bundled candidates.
    """

    if explicit is not None:
        candidates = [explicit.expanduser()]
    else:
        candidates = []
        located = shutil.which("codex")
        if located:
            candidates.append(Path(located))
        candidates.extend(DEFAULT_CODEX_CANDIDATES)
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        if not resolved.is_file() or not resolved.stat().st_mode & 0o111:
            continue
        try:
            completed = runner(
                [str(resolved), "--version"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                timeout=5.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0 and (completed.stdout or completed.stderr).strip():
            return resolved
    if explicit is not None:
        raise CapabilityProbeError("explicit Codex executable is not runnable")
    raise CapabilityProbeError("no runnable Codex executable was found")


def codex_mcp_stdio_launch(
    *, codex_executable: Path, server_argv: Sequence[str]
) -> dict[str, Any]:
    """Return a platform-correct stdio MCP launcher for the chosen Codex binary.

    A Windows Codex executable invoked from WSL cannot spawn a Linux Python
    path directly.  In that mixed boundary, ``wsl.exe --exec`` is the process
    bridge while the MCP server and Docker client remain inside the admitted
    WSL distribution.  Native Linux/macOS Codex launches the same argv
    directly through the current Python interpreter.
    """

    codex = codex_executable.expanduser().resolve(strict=False)
    if not server_argv or any(not isinstance(value, str) or not value for value in server_argv):
        raise CapabilityProbeError("MCP server argv must be non-empty strings")
    if codex.suffix.lower() == ".exe":
        return {
            "mode": "windows-codex-to-wsl-stdio-v1",
            "command": "wsl.exe",
            "args": ["--exec", sys.executable, *server_argv],
        }
    return {
        "mode": "native-posix-stdio-v1",
        "command": sys.executable,
        "args": list(server_argv),
    }


def _run_text(
    argv: Sequence[str],
    *,
    runner: RunCommand = subprocess.run,
    input_text: str | None = None,
    timeout: float = 15.0,
) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {
        "text": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "timeout": timeout,
        "check": False,
    }
    if input_text is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = input_text
    completed = runner(list(argv), **kwargs)
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if len(stdout.encode("utf-8")) > MAX_HELP_BYTES:
        raise CapabilityProbeError("subprocess stdout exceeded the probe limit")
    if len(stderr.encode("utf-8")) > MAX_HELP_BYTES:
        raise CapabilityProbeError("subprocess stderr exceeded the probe limit")
    return completed


def probe_codex_cli(
    executable: Path,
    *,
    runner: RunCommand = subprocess.run,
) -> dict[str, Any]:
    """Inspect local CLI contracts without loading MCPs or calling a model."""

    version_run = _run_text([str(executable), "--version"], runner=runner)
    exec_help_run = _run_text([str(executable), "exec", "--help"], runner=runner)
    mcp_help_run = _run_text([str(executable), "mcp", "list", "--help"], runner=runner)
    if version_run.returncode != 0:
        raise CapabilityProbeError("codex --version failed")
    if exec_help_run.returncode != 0:
        raise CapabilityProbeError("codex exec --help failed")
    if mcp_help_run.returncode != 0:
        raise CapabilityProbeError("codex mcp list --help failed")

    version = version_run.stdout.strip() or version_run.stderr.strip()
    exec_help = exec_help_run.stdout
    mcp_help = mcp_help_run.stdout
    flags = {
        "per_run_config_override": "--config" in exec_help or "-c," in exec_help,
        "strict_config": "--strict-config" in exec_help,
        "ignore_user_config": "--ignore-user-config" in exec_help,
        "ignore_rules": "--ignore-rules" in exec_help,
        "ephemeral": "--ephemeral" in exec_help,
        "json_events": "--json" in exec_help,
        "read_only_sandbox": "--sandbox" in exec_help and "read-only" in exec_help,
        "mcp_list_json": "--json" in mcp_help,
        "native_tool_allowlist": any(
            token in exec_help
            for token in ("--tools", "--allowedTools", "--allowed-tools")
        ),
        "native_tool_denylist": any(
            token in exec_help
            for token in ("--disallowedTools", "--disallowed-tools", "--deny-tools")
        ),
        "strict_mcp_config": any(
            token in exec_help for token in ("--strict-mcp-config", "--mcp-config")
        ),
    }
    return {
        "executable": str(executable),
        "version": version,
        "version_sha256": _sha256_bytes(version.encode("utf-8")),
        "exec_help_sha256": _sha256_bytes(exec_help.encode("utf-8")),
        "mcp_list_help_sha256": _sha256_bytes(mcp_help.encode("utf-8")),
        "capability_flags": flags,
    }


def probe_codex_feature_suppression(
    executable: Path,
    *,
    runner: RunCommand = subprocess.run,
) -> dict[str, Any]:
    """Prove only the CLI feature-disable contract, never runtime tool absence.

    Newer Codex builds can turn off the native shell and other tool-bearing
    feature families per invocation.  This is a useful path toward an exact
    MCP-only executor, but a feature listing is not a model-visible tool
    inventory and cannot open the benchmark gate by itself.
    """

    argv = [str(executable)]
    for feature in NATIVE_TOOL_FEATURES_TO_DISABLE:
        argv.extend(("--disable", feature))
    argv.extend(("features", "list"))
    completed = _run_text(argv, runner=runner)
    if completed.returncode != 0:
        return {
            "provided": False,
            "requested_disabled_features": list(NATIVE_TOOL_FEATURES_TO_DISABLE),
            "observed_disabled_features": [],
            "all_requested_features_disabled": False,
            "feature_listing_is_runtime_tool_inventory_proof": False,
            "feature_listing_is_model_execution": False,
        }
    states: dict[str, bool] = {}
    for line in completed.stdout.splitlines():
        columns = line.split()
        if len(columns) >= 3 and columns[-1] in {"true", "false"}:
            states[columns[0]] = columns[-1] == "true"
    observed = [
        feature
        for feature in NATIVE_TOOL_FEATURES_TO_DISABLE
        if states.get(feature) is False
    ]
    return {
        "provided": True,
        "requested_disabled_features": list(NATIVE_TOOL_FEATURES_TO_DISABLE),
        "observed_disabled_features": observed,
        "all_requested_features_disabled": len(observed)
        == len(NATIVE_TOOL_FEATURES_TO_DISABLE),
        "features_list_sha256": _sha256_bytes(completed.stdout.encode("utf-8")),
        "feature_listing_is_runtime_tool_inventory_proof": False,
        "feature_listing_is_model_execution": False,
    }


def _load_schema_object(path: Path) -> tuple[dict[str, Any], str, int]:
    raw = path.read_bytes()
    if len(raw) > MAX_SCHEMA_BYTES:
        raise CapabilityProbeError(f"app-server schema exceeded the probe limit: {path.name}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CapabilityProbeError(f"app-server schema is malformed: {path.name}") from exc
    if not isinstance(value, dict):
        raise CapabilityProbeError(f"app-server schema is not an object: {path.name}")
    return value, _sha256_bytes(raw), len(raw)


def _schema_property_names(value: Any) -> set[str]:
    """Collect declared JSON Schema property names, excluding prose and values."""

    names: set[str] = set()
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            names.update(str(name) for name in properties)
        for child in value.values():
            names.update(_schema_property_names(child))
    elif isinstance(value, list):
        for child in value:
            names.update(_schema_property_names(child))
    return names


def inspect_codex_app_server_schemas(schema_dir: Path | None) -> dict[str, Any]:
    """Inspect generated local protocol schemas without treating them as docs.

    The protocol can expose additive host ``dynamicTools`` while still lacking
    any field that removes Codex native tools.  This probe records that
    distinction and never upgrades a protocol field into executor eligibility.
    """

    if schema_dir is None:
        return {
            "provided": False,
            "raw_schemas_packaged": False,
            "native_tool_allowlist_schema_key": False,
            "native_tool_denylist_schema_key": False,
            "strict_mcp_config_schema_key": False,
        }
    root = schema_dir.expanduser().resolve(strict=True)
    required = {
        "config_read": root / "v2" / "ConfigReadResponse.json",
        "thread_start": root / "v2" / "ThreadStartParams.json",
        "turn_start": root / "v2" / "TurnStartParams.json",
    }
    loaded: dict[str, dict[str, Any]] = {}
    files: dict[str, dict[str, Any]] = {}
    for label, path in required.items():
        if not path.is_file():
            raise CapabilityProbeError(f"required app-server schema is missing: {label}")
        value, digest, byte_count = _load_schema_object(path)
        loaded[label] = value
        files[label] = {
            "relative_path": str(path.relative_to(root)),
            "bytes": byte_count,
            "sha256": digest,
        }

    config = loaded["config_read"].get("definitions", {}).get("Config", {})
    config_properties = config.get("properties", {})
    if not isinstance(config_properties, dict):
        raise CapabilityProbeError("ConfigReadResponse Config properties are missing")
    tools_v2 = loaded["config_read"].get("definitions", {}).get("ToolsV2", {})
    tool_properties = tools_v2.get("properties", {})
    if not isinstance(tool_properties, dict):
        raise CapabilityProbeError("ConfigReadResponse ToolsV2 properties are missing")
    thread_properties = loaded["thread_start"].get("properties", {})
    turn_properties = loaded["turn_start"].get("properties", {})
    if not isinstance(thread_properties, dict) or not isinstance(turn_properties, dict):
        raise CapabilityProbeError("thread or turn schema properties are missing")

    all_property_names: set[str] = set()
    for value in loaded.values():
        all_property_names.update(_schema_property_names(value))
    normalized = {
        "".join(character for character in name.lower() if character.isalnum())
        for name in all_property_names
    }
    allowlist_keys = {"allowedtools", "allowtools", "toolallowlist", "nativetoolallowlist"}
    denylist_keys = {
        "disallowedtools",
        "denytools",
        "tooldenylist",
        "nativetooldenylist",
    }
    strict_mcp_keys = {"strictmcpconfig", "mcpconfig"}
    return {
        "provided": True,
        "source": "locally generated Codex app-server JSON Schema",
        "raw_schemas_packaged": False,
        "files": files,
        "config_property_names": sorted(config_properties),
        "config_tools_property_names": sorted(tool_properties),
        "thread_start_has_dynamic_tools": "dynamicTools" in thread_properties,
        "turn_start_has_dynamic_tools": "dynamicTools" in turn_properties,
        "native_tool_allowlist_schema_key": bool(normalized & allowlist_keys),
        "native_tool_denylist_schema_key": bool(normalized & denylist_keys),
        "strict_mcp_config_schema_key": bool(normalized & strict_mcp_keys),
        "schema_is_executor_restriction_proof": False,
    }


def probe_direct_mcp_server(
    server_path: Path,
    *,
    python_executable: Path = Path(sys.executable),
    runner: RunCommand = subprocess.run,
) -> dict[str, Any]:
    """Perform initialize/tools-list only; never call the container exec tool."""

    server = server_path.expanduser().resolve(strict=True)
    requests = (
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": PROBE_PROTOCOL_VERSION},
        },
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )
    input_text = "".join(
        json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
        for item in requests
    )
    completed = _run_text(
        [
            str(python_executable),
            str(server),
            "--container",
            "merlin-capability-probe-missing-container",
            "--workdir",
            "/root/task",
            "--timeout-sec",
            "30",
        ],
        runner=runner,
        input_text=input_text,
    )
    if completed.returncode != 0:
        raise CapabilityProbeError("direct MCP server probe failed")
    try:
        responses = [json.loads(line) for line in completed.stdout.splitlines() if line]
    except json.JSONDecodeError as exc:
        raise CapabilityProbeError("direct MCP server returned malformed JSON") from exc
    if len(responses) != 2:
        raise CapabilityProbeError("direct MCP server returned an unexpected response count")
    initialize = responses[0]
    listed = responses[1]
    if initialize.get("id") != 1 or listed.get("id") != 2:
        raise CapabilityProbeError("direct MCP response IDs do not match")
    init_result = initialize.get("result")
    list_result = listed.get("result")
    if not isinstance(init_result, dict) or not isinstance(list_result, dict):
        raise CapabilityProbeError("direct MCP results are missing")
    tools = list_result.get("tools")
    if not isinstance(tools, list) or len(tools) != 1:
        raise CapabilityProbeError("direct MCP server must expose exactly one tool")
    tool = tools[0]
    if not isinstance(tool, dict) or tool.get("name") != "exec":
        raise CapabilityProbeError("direct MCP server must expose only exec")
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict):
        raise CapabilityProbeError("exec input schema is missing")
    properties = schema.get("properties")
    if not isinstance(properties, dict) or set(properties) != {"command", "timeout_sec"}:
        raise CapabilityProbeError("exec tool arguments drifted")
    if schema.get("additionalProperties") is not False:
        raise CapabilityProbeError("exec tool must reject additional arguments")
    if "container" in properties or "workdir" in properties:
        raise CapabilityProbeError("exec tool exposes a boundary override")
    server_info = init_result.get("serverInfo")
    if not isinstance(server_info, dict):
        raise CapabilityProbeError("MCP serverInfo is missing")
    return {
        "passed": True,
        "requested_protocol_version": PROBE_PROTOCOL_VERSION,
        "negotiated_protocol_version": init_result.get("protocolVersion"),
        "server_name": server_info.get("name"),
        "server_version": server_info.get("version"),
        "tool_count": 1,
        "tool_names": ["exec"],
        "tool_argument_names": ["command", "timeout_sec"],
        "boundary_override_arguments_exposed": False,
        "tools_call_performed": False,
    }


def summarize_recorded_audit(path: Path | None) -> dict[str, Any]:
    """Summarize metadata-only MCP audit events without copying arguments/output."""

    if path is None:
        return {
            "provided": False,
            "raw_audit_packaged": False,
            "initialize_observed": False,
            "tools_list_observed": False,
            "exec_tool_call_observed": False,
            "exec_tool_call_count": 0,
        }
    audit = path.expanduser().resolve(strict=True)
    raw = audit.read_bytes()
    if len(raw) > MAX_AUDIT_BYTES:
        raise CapabilityProbeError("recorded MCP audit exceeded the probe limit")
    events: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise CapabilityProbeError(
                f"recorded MCP audit line {line_number} is malformed"
            ) from exc
        if not isinstance(value, dict):
            raise CapabilityProbeError(
                f"recorded MCP audit line {line_number} is not an object"
            )
        events.append(value)
    methods = [event.get("method") for event in events]
    exec_calls = sum(
        1
        for event in events
        if event.get("method") == "tools/call" and event.get("tool_name") == "exec"
    )
    negotiated = sorted(
        {
            event["negotiated_protocol_version"]
            for event in events
            if isinstance(event.get("negotiated_protocol_version"), str)
        }
    )
    tool_counts = sorted(
        {
            event["tool_count"]
            for event in events
            if isinstance(event.get("tool_count"), int)
        }
    )
    safe_tool_results = [
        {
            "exit_code": event["tool_result_exit_code"],
            "timed_out": event["tool_result_timed_out"],
        }
        for event in events
        if isinstance(event.get("tool_result_exit_code"), int)
        and not isinstance(event.get("tool_result_exit_code"), bool)
        and isinstance(event.get("tool_result_timed_out"), bool)
    ]
    return {
        "provided": True,
        "raw_audit_packaged": False,
        "bytes": len(raw),
        "sha256": _sha256_bytes(raw),
        "event_count": len(events),
        "initialize_observed": "initialize" in methods,
        "tools_list_observed": "tools/list" in methods,
        "exec_tool_call_observed": exec_calls > 0,
        "exec_tool_call_count": exec_calls,
        "negotiated_protocol_versions": negotiated,
        "observed_tool_counts": tool_counts,
        "safe_tool_results": safe_tool_results,
        "raw_arguments_or_tool_output_copied": False,
    }


def probe_container_runtime(
    executable_name: str = "docker",
    *,
    container_id: str | None = None,
    runner: RunCommand = subprocess.run,
) -> dict[str, Any]:
    """Check runtime/container presence only; execute no task command."""

    located = shutil.which(executable_name)
    if located is None:
        return {
            "executable": executable_name,
            "executable_found": False,
            "container_id_provided": container_id is not None,
            "container_inspect_passed": False,
        }
    result = {
        "executable": str(Path(located).resolve(strict=False)),
        "executable_found": True,
        "container_id_provided": container_id is not None,
        "container_inspect_passed": False,
    }
    if container_id is None:
        return result
    completed = _run_text(
        [located, "inspect", "--type", "container", container_id],
        runner=runner,
        timeout=10.0,
    )
    result["container_inspect_passed"] = completed.returncode == 0
    return result


def compute_readiness(
    *,
    cli: Mapping[str, Any],
    direct_mcp: Mapping[str, Any],
    recorded_audit: Mapping[str, Any],
    container_runtime: Mapping[str, Any],
    app_server_schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    flags = cli.get("capability_flags")
    if not isinstance(flags, Mapping):
        raise CapabilityProbeError("CLI capability flags are missing")
    checks = {
        "mcp_server_ready": direct_mcp.get("passed") is True,
        "per_run_mcp_config_available": flags.get("per_run_config_override") is True,
        "user_config_suppression_available": flags.get("ignore_user_config") is True,
        "rules_suppression_available": flags.get("ignore_rules") is True,
        "ephemeral_json_read_only_controls_available": all(
            flags.get(name) is True
            for name in ("ephemeral", "json_events", "read_only_sandbox")
        ),
        "native_tool_allowlist_available": flags.get("native_tool_allowlist") is True,
        "native_tool_denylist_available": flags.get("native_tool_denylist") is True,
        "strict_mcp_config_available": flags.get("strict_mcp_config") is True,
        "model_exec_tool_call_observed": recorded_audit.get("exec_tool_call_observed")
        is True,
        "container_runtime_ready": container_runtime.get("container_inspect_passed")
        is True,
    }
    strict_required = (
        "mcp_server_ready",
        "per_run_mcp_config_available",
        "user_config_suppression_available",
        "rules_suppression_available",
        "ephemeral_json_read_only_controls_available",
        "native_tool_allowlist_available",
        "native_tool_denylist_available",
        "strict_mcp_config_available",
        "model_exec_tool_call_observed",
        "container_runtime_ready",
    )
    failed = [name for name in strict_required if not checks[name]]
    eligible = not failed
    schema = app_server_schema or {}
    return {
        "checks": checks,
        "strict_benchmark_bridge_eligible": eligible,
        "six_cell_execution_allowed": eligible,
        "failed_required_checks": failed,
        "handshake_only_is_benchmark_evidence": False,
        "this_probe_is_model_execution": False,
        "this_probe_is_benchmark_result": False,
        "app_server_schema_can_open_gate": False,
        "app_server_schema_missing_native_tool_restrictions": schema.get("provided")
        is True
        and not all(
            schema.get(name) is True
            for name in (
                "native_tool_allowlist_schema_key",
                "native_tool_denylist_schema_key",
                "strict_mcp_config_schema_key",
            )
        ),
    }


def build_report(
    *,
    codex_executable: Path,
    server_path: Path,
    recorded_audit_path: Path | None = None,
    container_id: str | None = None,
    app_server_schema_dir: Path | None = None,
    runner: RunCommand = subprocess.run,
) -> dict[str, Any]:
    cli = probe_codex_cli(codex_executable, runner=runner)
    feature_suppression = probe_codex_feature_suppression(
        codex_executable,
        runner=runner,
    )
    direct = probe_direct_mcp_server(server_path, runner=runner)
    audit = summarize_recorded_audit(recorded_audit_path)
    runtime = probe_container_runtime(container_id=container_id, runner=runner)
    app_server_schema = inspect_codex_app_server_schemas(app_server_schema_dir)
    return {
        "schema_version": 2,
        "diagnostic": "codex_mcp_capability",
        "scope": "model-free preflight; no task prompt, model call, or benchmark result",
        "codex_cli": cli,
        "native_tool_feature_suppression": feature_suppression,
        "direct_mcp_server": direct,
        "recorded_mcp_audit": audit,
        "container_runtime": runtime,
        "app_server_schema": app_server_schema,
        "readiness": compute_readiness(
            cli=cli,
            direct_mcp=direct,
            recorded_audit=audit,
            container_runtime=runtime,
            app_server_schema=app_server_schema,
        ),
    }


def write_report(path: Path, report: Mapping[str, Any]) -> None:
    destination = path.expanduser().resolve(strict=False)
    if destination.exists():
        raise CapabilityProbeError("output already exists")
    if not destination.parent.is_dir():
        raise CapabilityProbeError("output parent must exist")
    payload = _json_bytes(report)
    with destination.open("xb") as handle:
        handle.write(payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe Codex MCP benchmark eligibility without calling a model.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--codex-executable", type=Path)
    parser.add_argument("--container-exec-server", type=Path, default=DEFAULT_SERVER)
    parser.add_argument("--recorded-mcp-audit", type=Path)
    parser.add_argument("--container-id")
    parser.add_argument(
        "--app-server-schema-dir",
        type=Path,
        help="directory created by codex app-server generate-json-schema",
    )
    args = parser.parse_args(argv)
    try:
        executable = detect_codex_executable(args.codex_executable)
        report = build_report(
            codex_executable=executable,
            server_path=args.container_exec_server,
            recorded_audit_path=args.recorded_mcp_audit,
            container_id=args.container_id,
            app_server_schema_dir=args.app_server_schema_dir,
        )
        write_report(args.output, report)
    except (CapabilityProbeError, OSError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    readiness = report["readiness"]
    print(f"saved -> {args.output.expanduser().resolve(strict=False)}")
    print(
        "strict_benchmark_bridge_eligible="
        f"{str(readiness['strict_benchmark_bridge_eligible']).lower()}"
    )
    print(
        "six_cell_execution_allowed="
        f"{str(readiness['six_cell_execution_allowed']).lower()}"
    )
    if readiness["failed_required_checks"]:
        print("failed_required_checks=" + ",".join(readiness["failed_required_checks"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
