"""Emit a minimal, sanitized audit of a Claude stream-JSON task trace.

The output deliberately excludes prompts, shell commands, tool results, tool-use
IDs, request IDs, filesystem paths, usage/cost data, and session/account fields.
Only the fixed experiment contract signals needed for post-run analysis survive.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "theking.claude_trace_audit.v1"
_SAFE_SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")


def _events(path: Path) -> tuple[Iterable[dict[str, Any]], list[int]]:
    malformed = [0]

    def iterate() -> Iterable[dict[str, Any]]:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    malformed[0] += 1
                    continue
                if isinstance(event, dict):
                    yield event
                else:
                    malformed[0] += 1

    return iterate(), malformed


def _assistant_tool_uses(event: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield only model-authored tool_use blocks, never tool-result payloads."""

    if event.get("type") != "assistant":
        return
    message = event.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            yield block


def _is_task_user_event(event: dict[str, Any]) -> bool:
    # Under --replay-user-messages the actual submitted task is echoed with
    # isReplay=true.  Claude may also emit non-replayed textual user-role
    # command/skill notifications; those are not independent task inputs.
    if event.get("type") != "user" or event.get("isReplay") is not True:
        return False
    message = event.get("message")
    if not isinstance(message, dict) or message.get("role") != "user":
        return False
    content = message.get("content")
    if isinstance(content, str):
        return bool(content)
    if not isinstance(content, list):
        return False
    has_text = any(
        isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
        for block in content
    )
    has_tool_result = any(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    )
    return has_text and not has_tool_result


def _control_kind(request_id: Any, subtype: Any, payload: Any = None) -> str:
    if subtype in {"initialize", "mcp_status"}:
        return str(subtype)
    if isinstance(payload, dict) and isinstance(payload.get("mcpServers"), list):
        return "mcp_status"
    normalized = str(request_id or "").casefold().replace("-", "_")
    if "mcp_status" in normalized or ("mcp" in normalized and "status" in normalized):
        return "mcp_status"
    if "initialize" in normalized or normalized.endswith("_init") or "barrier_init" in normalized:
        return "initialize"
    return "other"


def _mcp_ready(payload: Any) -> tuple[bool, bool]:
    """Return connected/tool-present booleans without retaining server metadata."""

    if not isinstance(payload, dict):
        return False, False
    servers = payload.get("mcpServers")
    if not isinstance(servers, list):
        return False, False
    connected = False
    exec_present = False
    for server in servers:
        if not isinstance(server, dict) or server.get("name") != "task_container":
            continue
        connected = connected or server.get("status") == "connected"
        tools = server.get("tools")
        if isinstance(tools, list):
            exec_present = exec_present or any(
                isinstance(tool, dict) and tool.get("name") == "exec" for tool in tools
            )
    return connected, exec_present


def _safe_skill_name(value: Any) -> tuple[str | None, bool]:
    if isinstance(value, str) and _SAFE_SKILL_NAME.fullmatch(value):
        return value, True
    return None, False


def analyze_trace(path: Path) -> dict[str, Any]:
    """Analyze one raw trace and return only the allowlisted audit schema."""

    events, malformed = _events(path)
    event_count = 0
    mcp_exec_call_count = 0
    skill_calls: list[dict[str, Any]] = []
    task_user_event_count = 0
    replayed_task_user_event_count = 0
    result_event_count = 0
    request_counts = {"initialize": 0, "mcp_status": 0, "other": 0}
    response_counts = {"initialize": 0, "mcp_status": 0, "other": 0}
    response_success_counts = {"initialize": 0, "mcp_status": 0, "other": 0}
    mcp_connected_response_count = 0
    mcp_exec_present_response_count = 0
    mcp_ready_response_count = 0

    for event in events:
        event_count += 1
        for block in _assistant_tool_uses(event):
            name = block.get("name")
            if name == "mcp__task_container__exec":
                mcp_exec_call_count += 1
            elif name == "Skill":
                tool_input = block.get("input")
                tool_input = tool_input if isinstance(tool_input, dict) else {}
                skill_name, name_valid = _safe_skill_name(tool_input.get("skill"))
                skill_calls.append(
                    {
                        "skill": skill_name,
                        "skill_name_valid": name_valid,
                        "args_present": "args" in tool_input,
                    }
                )

        if _is_task_user_event(event):
            task_user_event_count += 1
            replayed_task_user_event_count += 1
        if event.get("type") == "result":
            result_event_count += 1

        if event.get("type") == "control_request":
            request = event.get("request")
            request = request if isinstance(request, dict) else {}
            kind = _control_kind(event.get("request_id"), request.get("subtype"))
            request_counts[kind] += 1

        if event.get("type") == "control_response":
            wrapper = event.get("response")
            wrapper = wrapper if isinstance(wrapper, dict) else {}
            payload = wrapper.get("response")
            kind = _control_kind(wrapper.get("request_id"), None, payload)
            response_counts[kind] += 1
            if wrapper.get("subtype") == "success":
                response_success_counts[kind] += 1
            if kind == "mcp_status":
                connected, exec_present = _mcp_ready(payload)
                if connected:
                    mcp_connected_response_count += 1
                if exec_present:
                    mcp_exec_present_response_count += 1
                if wrapper.get("subtype") == "success" and connected and exec_present:
                    mcp_ready_response_count += 1

    initialize_success = response_success_counts["initialize"] > 0
    mcp_ready = mcp_ready_response_count > 0
    return {
        "schema_version": SCHEMA_VERSION,
        "event_count": event_count,
        "malformed_line_count": malformed[0],
        "task_user_events": {
            "count": task_user_event_count,
            "replayed_count": replayed_task_user_event_count,
        },
        "tool_calls": {
            "mcp_exec_call_count": mcp_exec_call_count,
            "skill_call_count": len(skill_calls),
            "skill_calls": skill_calls,
        },
        "barrier": {
            "control_request_count": sum(request_counts.values()),
            "control_response_count": sum(response_counts.values()),
            "initialize": {
                "request_count": request_counts["initialize"],
                "response_count": response_counts["initialize"],
                "success_response_count": response_success_counts["initialize"],
            },
            "mcp_status": {
                "request_count": request_counts["mcp_status"],
                "response_count": response_counts["mcp_status"],
                "success_response_count": response_success_counts["mcp_status"],
                "task_container_connected_response_count": mcp_connected_response_count,
                "exec_tool_present_response_count": mcp_exec_present_response_count,
                "ready_response_count": mcp_ready_response_count,
            },
            "other_request_count": request_counts["other"],
            "other_response_count": response_counts["other"],
            "initialize_succeeded": initialize_success,
            "mcp_ready_before_task_evidence_present": mcp_ready,
            "passed_from_control_responses": initialize_success and mcp_ready,
        },
        "result_event_count": result_event_count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    args = parser.parse_args(argv)
    result = analyze_trace(args.trace)
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
