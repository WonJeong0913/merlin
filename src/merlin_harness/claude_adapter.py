"""Claude Code CLI adapter with conservative actual-invocation semantics.

`claude -p --output-format stream-json` emits provider-native JSONL events.
Like the Codex adapter, this one never turns prompt exposure or ordinary model
text into an invocation claim.

One difference from Codex is worth stating precisely, because it is the reason
this adapter exists as more than a second model option. Claude Code has a
native `Skill` tool, and when the model invokes a skill the stream carries a
`tool_use` block naming it. That is the **provider's own record of the model
invoking a skill**, not the harness asserting that it put a body in a prompt.

It is still not a body-level claim:

- the event carries a skill **name**, not a `SKILL.md` body SHA-256;
- binding name → body requires the harness's own provisioning record, which is
  the self-attested side again.

So this adapter reports skill tool calls as a distinct, named signal and leaves
it to the evidence layer to decide what a name-level provider event is worth.
It does not set any completeness flag on its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .agent_adapter import AgentContractError

MAX_SKILL_TOOL_CALLS = 512


class ClaudeCliAdapterError(AgentContractError):
    """Raised when Claude CLI output cannot support a trusted adapter result."""


@dataclass(frozen=True, slots=True)
class ClaudeSkillToolCall:
    """One provider-recorded `Skill` tool invocation.

    `skill` is whatever the model passed. It is not validated against the
    active library here — that comparison belongs to the evidence layer, which
    knows what was provisioned.
    """

    skill: str | None
    tool_use_id: str | None
    args_present: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "tool_use_id": self.tool_use_id,
            "args_present": self.args_present,
        }


@dataclass(frozen=True, slots=True)
class ClaudeStreamSummary:
    """Safe, normalized facts extracted from one Claude stream-json run."""

    session_id: str | None
    final_message: str | None
    event_types: tuple[str, ...]
    reported_model_ids: tuple[str, ...]
    event_count: int
    skill_tool_calls: tuple[ClaudeSkillToolCall, ...]
    exposed_skills: tuple[str, ...]
    is_error: bool

    @property
    def invoked_skill_names(self) -> tuple[str, ...]:
        seen = dict.fromkeys(
            call.skill for call in self.skill_tool_calls if call.skill is not None
        )
        return tuple(seen)


def _nonempty_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _content_blocks(event: dict[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _assistant_text(event: dict[str, Any]) -> str | None:
    parts = [
        block["text"]
        for block in _content_blocks(event)
        if block.get("type") == "text" and isinstance(block.get("text"), str)
    ]
    joined = "".join(parts).strip()
    return joined or None


def parse_claude_stream_jsonl(raw_text: str) -> ClaudeStreamSummary:
    """Strictly parse Claude stream JSONL while accepting only safe facts.

    Strictness matches the Codex adapter: every line must be a JSON object
    carrying a non-empty `type`, and conflicting session IDs are a failure
    rather than a value to pick between.
    """

    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ClaudeCliAdapterError("Claude stream output is empty")

    session_id: str | None = None
    final_message: str | None = None
    last_assistant_text: str | None = None
    event_types: list[str] = []
    reported_model_ids: list[str] = []
    skill_tool_calls: list[ClaudeSkillToolCall] = []
    exposed_skills: tuple[str, ...] = ()
    event_count = 0
    is_error = False

    for line_number, line in enumerate(raw_text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ClaudeCliAdapterError(
                f"malformed Claude stream JSONL at line {line_number}: {exc.msg}"
            ) from exc
        if not isinstance(event, dict):
            raise ClaudeCliAdapterError(
                f"Claude stream event at line {line_number} must be an object"
            )
        event_type = _nonempty_string(event.get("type"))
        if event_type is None:
            raise ClaudeCliAdapterError(
                f"Claude stream event at line {line_number} has no non-empty type"
            )
        event_count += 1
        event_types.append(event_type)

        candidate_session = _nonempty_string(event.get("session_id"))
        if candidate_session is not None:
            if session_id is not None and session_id != candidate_session:
                raise ClaudeCliAdapterError(
                    "Claude stream contains conflicting session_id values"
                )
            session_id = candidate_session

        message = event.get("message")
        if isinstance(message, dict):
            if model := _nonempty_string(message.get("model")):
                reported_model_ids.append(model)

        if event_type == "assistant":
            if text := _assistant_text(event):
                last_assistant_text = text
            for block in _content_blocks(event):
                if block.get("type") != "tool_use" or block.get("name") != "Skill":
                    continue
                if len(skill_tool_calls) >= MAX_SKILL_TOOL_CALLS:
                    raise ClaudeCliAdapterError(
                        "Claude stream exceeds the skill tool call bound"
                    )
                tool_input = block.get("input")
                tool_input = tool_input if isinstance(tool_input, dict) else {}
                skill_tool_calls.append(
                    ClaudeSkillToolCall(
                        skill=_nonempty_string(tool_input.get("skill")),
                        tool_use_id=_nonempty_string(block.get("id")),
                        args_present="args" in tool_input,
                    )
                )
        elif event_type == "system" and event.get("subtype") == "init":
            # The provider states which skills it exposed for this run. That is
            # the provider's account of provisioning, not the harness's, so it
            # is kept separate from whatever the harness believes it provided.
            declared = event.get("skills")
            if isinstance(declared, list):
                names = [
                    name
                    for name in (
                        _nonempty_string(item)
                        if isinstance(item, str)
                        else _nonempty_string((item or {}).get("name"))
                        if isinstance(item, dict)
                        else None
                        for item in declared
                    )
                    if name is not None
                ]
                exposed_skills = tuple(dict.fromkeys(names))
        elif event_type == "result":
            if event.get("is_error") is True:
                is_error = True
            if text := _nonempty_string(event.get("result")):
                final_message = text

    if event_count == 0:
        raise ClaudeCliAdapterError("Claude stream output contains no events")
    return ClaudeStreamSummary(
        session_id=session_id,
        final_message=final_message or last_assistant_text,
        event_types=tuple(event_types),
        reported_model_ids=tuple(dict.fromkeys(reported_model_ids)),
        event_count=event_count,
        skill_tool_calls=tuple(skill_tool_calls),
        exposed_skills=exposed_skills,
        is_error=is_error,
    )
