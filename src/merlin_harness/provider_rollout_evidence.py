"""Fail-closed corroboration of a signed skill-body invocation event.

The harness event is necessary but self-attested: it binds task → selected
skill → body hash → request hash → trace → verifier result under a harness HMAC.
This module binds that event to a *single*, CLI-written rollout request.  It
does not combine every user message in a session, and it never turns prompt
exposure into a claim that a provider used a skill.

The caller must supply the trusted harness signer, the provider thread ID, and
the exact provider turn ID.  A corroboration succeeds only when all of these
are true:

* the event signature validates under that exact trusted signer;
* the rollout filename and ``session_meta.id`` both equal the supplied thread;
* one ``turn_context`` and one user request are bound to the supplied turn;
* the event request hash equals the SHA-256 of that one canonical request; and
* the exact on-disk ``SKILL.md`` bytes hash-match the event and occur in that
  one request.

Any missing, ambiguous, malformed, or mismatching binding raises rather than
returning a permissive partial result.  Raw request text never leaves this
module.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from .skill_body_invocation import (
    HarnessInvocationSigner,
    SkillBodyInvocationEvent,
    SkillBodyInvocationError,
    load_skill_body_sha256,
    validate_skill_body_invocation_event,
)

SCHEMA_VERSION = "merlin-provider-rollout-corroboration-v2"
DEFAULT_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"

_THREAD_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_SAFE_TURN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
MAX_ROLLOUT_BYTES = 64 * 1024 * 1024


class ProviderRolloutError(ValueError):
    """Raised when rollout evidence is absent, ambiguous, or malformed."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_thread_id(value: object) -> str:
    if not isinstance(value, str) or not _THREAD_ID_RE.fullmatch(value):
        raise ProviderRolloutError("thread_id must be a lowercase UUID")
    return value


def _require_turn_id(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_TURN_ID_RE.fullmatch(value):
        raise ProviderRolloutError("turn_id must be a safe non-empty provider turn ID")
    return value


def canonical_model_request_sha256(request_text: str) -> str:
    """Return the v1 request hash used by both Merlin and rollout evidence.

    The contract is deliberately narrow: UTF-8 bytes of *one* recorded user
    message's ``input_text`` fragments concatenated in their emitted order, no
    separators, normalization, or wrapper reconstruction.  A harness event
    intended for Codex-rollout corroboration must hash these same bytes before
    signing.  Hashing a private prompt template, a whole session, or a wrapper
    around the request is a different contract and fails closed here.
    """

    if not isinstance(request_text, str):
        raise ProviderRolloutError("canonical model request must be text")
    return _sha256_bytes(request_text.encode("utf-8"))


def _canonical_request_bytes(payload: Mapping[str, Any]) -> bytes:
    """Extract the exact canonical bytes from one rollout user-message record."""

    if payload.get("type") != "message" or payload.get("role") != "user":
        raise ProviderRolloutError("turn request must be a user message record")
    content = payload.get("content")
    if not isinstance(content, list) or not content:
        raise ProviderRolloutError("turn request has no content")
    parts: list[str] = []
    for item in content:
        if (
            not isinstance(item, dict)
            or item.get("type") != "input_text"
            or not isinstance(item.get("text"), str)
        ):
            raise ProviderRolloutError(
                "turn request content must contain only text input fragments"
            )
        parts.append(item["text"])
    return "".join(parts).encode("utf-8")


def locate_rollout(
    thread_id: str,
    *,
    sessions_root: str | Path | None = None,
) -> Path:
    """Find the one non-symlink rollout whose filename carries ``thread_id``."""

    thread_id = _require_thread_id(thread_id)
    root = Path(sessions_root or DEFAULT_SESSIONS_ROOT).expanduser()
    if not root.is_dir():
        raise ProviderRolloutError("Codex sessions root is absent")
    matches = [
        path
        for path in root.rglob(f"*{thread_id}.jsonl")
        if path.is_file() and not path.is_symlink()
    ]
    if not matches:
        raise ProviderRolloutError("no rollout carries this thread ID")
    if len(matches) > 1:
        raise ProviderRolloutError("thread ID matches more than one rollout")
    return matches[0]


def _rollout_records(raw: str) -> Iterator[dict[str, Any]]:
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProviderRolloutError(
                f"malformed rollout record at line {line_number}"
            ) from exc
        if not isinstance(record, dict):
            raise ProviderRolloutError(
                f"rollout record at line {line_number} must be an object"
            )
        yield record


def _text_or_none(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _source_label(value: object) -> str:
    """Normalize ``source``, which is a bare string for exec and an object otherwise."""

    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return next(iter(value), "unknown")
    return "unknown"


@dataclass(frozen=True, slots=True)
class _ExactTurnRequest:
    session_id: str
    cli_version: str | None
    originator: str | None
    source: str | None
    turn_ids: tuple[str, ...]
    turn_id: str
    request_bytes: bytes


def _extract_exact_turn_request(
    records: Iterator[dict[str, Any]],
    *,
    thread_id: str,
    turn_id: str,
) -> _ExactTurnRequest:
    """Bind exactly one request to the caller-selected rollout turn.

    ``turn_context.payload.turn_id`` is intentionally required.  Some observed
    local Codex rollouts do not currently emit it; those rollouts are not
    evidence for this v2 contract.  Guessing a turn from session order would
    reintroduce the same-session false positive this module exists to prevent.
    """

    session_ids: list[str] = []
    cli_version: str | None = None
    originator: str | None = None
    source: str | None = None
    turn_ids: list[str] = []
    target_context_count = 0
    active_turn_id: str | None = None
    target_requests: list[bytes] = []

    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = record.get("type")
        if kind == "session_meta":
            session_id = _text_or_none(payload.get("id"))
            if session_id is None:
                raise ProviderRolloutError("session_meta.id is absent or malformed")
            session_ids.append(session_id)
            cli_version = cli_version or _text_or_none(payload.get("cli_version"))
            originator = originator or _text_or_none(payload.get("originator"))
            if source is None and payload.get("source") is not None:
                source = _source_label(payload["source"])
            continue
        if kind == "turn_context":
            current_turn = _text_or_none(payload.get("turn_id"))
            active_turn_id = current_turn
            if current_turn is not None:
                turn_ids.append(current_turn)
                if current_turn == turn_id:
                    target_context_count += 1
            continue
        if kind != "response_item":
            continue
        if payload.get("type") != "message" or payload.get("role") != "user":
            continue
        payload_turn = _text_or_none(payload.get("turn_id"))
        if payload_turn is not None and active_turn_id is not None and payload_turn != active_turn_id:
            raise ProviderRolloutError("user request turn ID conflicts with turn context")
        request_turn = payload_turn or active_turn_id
        if request_turn == turn_id:
            target_requests.append(_canonical_request_bytes(payload))

    if len(session_ids) != 1:
        raise ProviderRolloutError("rollout must contain exactly one session_meta record")
    if session_ids[0] != thread_id:
        raise ProviderRolloutError("session_meta.id does not match the requested thread")
    if target_context_count != 1:
        raise ProviderRolloutError("requested provider turn is absent or ambiguous")
    if len(target_requests) != 1:
        raise ProviderRolloutError(
            "requested provider turn must contain exactly one user request"
        )
    return _ExactTurnRequest(
        session_id=session_ids[0],
        cli_version=cli_version,
        originator=originator,
        source=source,
        turn_ids=tuple(turn_ids),
        turn_id=turn_id,
        request_bytes=target_requests[0],
    )


@dataclass(frozen=True, slots=True)
class ProviderRolloutCorroboration:
    """One fully bound CLI-rollout corroboration of a signed invocation event."""

    schema_version: str
    thread_id: str
    session_id: str
    turn_id: str
    rollout_name: str
    rollout_sha256: str
    cli_version: str | None
    originator: str | None
    source: str | None
    turn_ids: tuple[str, ...]
    trusted_signer_id: str
    invocation_signature_valid: bool
    session_bound: bool
    turn_bound: bool
    recorded_request_sha256: str
    recorded_request_chars: int
    harness_model_request_sha256: str
    request_hash_bound: bool
    selected_skill_id: str
    skill_body_sha256: str
    skill_body_hash_bound: bool
    skill_body_present_in_recorded_request: bool

    @property
    def corroborated(self) -> bool:
        return all(
            (
                self.invocation_signature_valid,
                self.session_bound,
                self.turn_bound,
                self.request_hash_bound,
                self.skill_body_hash_bound,
                self.skill_body_present_in_recorded_request,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "thread_id": self.thread_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "rollout_name": self.rollout_name,
            "rollout_sha256": self.rollout_sha256,
            "cli_version": self.cli_version,
            "originator": self.originator,
            "source": self.source,
            "turn_ids": list(self.turn_ids),
            "trusted_signer_id": self.trusted_signer_id,
            "invocation_signature_valid": self.invocation_signature_valid,
            "session_bound": self.session_bound,
            "turn_bound": self.turn_bound,
            "recorded_request_sha256": self.recorded_request_sha256,
            "recorded_request_chars": self.recorded_request_chars,
            "harness_model_request_sha256": self.harness_model_request_sha256,
            "request_hash_bound": self.request_hash_bound,
            "selected_skill_id": self.selected_skill_id,
            "skill_body_sha256": self.skill_body_sha256,
            "skill_body_hash_bound": self.skill_body_hash_bound,
            "skill_body_present_in_recorded_request": (
                self.skill_body_present_in_recorded_request
            ),
            "corroborated": self.corroborated,
            "evidence_boundary": {
                "artifact_written_by": "codex_cli_rollout",
                "provider_server_attested": False,
                "establishes": (
                    "one trusted-harness event and one exact CLI-recorded user "
                    "request are hash-bound to the same thread and turn, with "
                    "the exact skill body present in that request"
                ),
                "does_not_establish": (
                    "that the model read, followed, or benefited from the body; "
                    "presence in a request is not use"
                ),
                "residual_trust": (
                    "a compromised Codex CLI or an edited rollout would defeat "
                    "this check"
                ),
            },
        }


def corroborate_skill_body_invocation(
    event: SkillBodyInvocationEvent,
    *,
    trusted_signer: HarnessInvocationSigner,
    thread_id: str,
    turn_id: str,
    skill_body_path: str | Path,
    sessions_root: str | Path | None = None,
) -> ProviderRolloutCorroboration:
    """Fail closed unless a signed event binds to one exact rollout request.

    ``event.model_request_sha256`` must be the value from
    :func:`canonical_model_request_sha256` over the exact request sent for the
    requested turn.  This is intentionally stricter than checking whether a
    body happened to appear somewhere in a session.
    """

    thread_id = _require_thread_id(thread_id)
    turn_id = _require_turn_id(turn_id)
    validate_skill_body_invocation_event(event, signer=trusted_signer)

    body_path = Path(skill_body_path).expanduser()
    body_sha256 = load_skill_body_sha256(body_path)
    try:
        body_bytes = body_path.resolve(strict=True).read_bytes()
        body_text = body_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillBodyInvocationError("skill body cannot be re-read as UTF-8") from exc
    if _sha256_bytes(body_bytes) != body_sha256:
        raise SkillBodyInvocationError("skill body changed while corroboration was reading it")
    if body_sha256 != event.skill_body_sha256:
        raise SkillBodyInvocationError(
            "skill body on disk does not match the signed invocation event"
        )

    rollout = locate_rollout(thread_id, sessions_root=sessions_root)
    raw_bytes = rollout.read_bytes()
    if not raw_bytes or len(raw_bytes) > MAX_ROLLOUT_BYTES:
        raise ProviderRolloutError("rollout size is outside the allowed bounds")
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProviderRolloutError("rollout must be UTF-8") from exc

    exact = _extract_exact_turn_request(
        _rollout_records(raw), thread_id=thread_id, turn_id=turn_id
    )
    recorded_request_sha256 = _sha256_bytes(exact.request_bytes)
    if recorded_request_sha256 != event.model_request_sha256:
        raise ProviderRolloutError(
            "recorded request hash does not match the signed model request"
        )
    if body_text not in exact.request_bytes.decode("utf-8"):
        raise ProviderRolloutError(
            "exact skill body is absent from the requested provider turn"
        )

    return ProviderRolloutCorroboration(
        schema_version=SCHEMA_VERSION,
        thread_id=thread_id,
        session_id=exact.session_id,
        turn_id=turn_id,
        rollout_name=rollout.name,
        rollout_sha256=_sha256_bytes(raw_bytes),
        cli_version=exact.cli_version,
        originator=exact.originator,
        source=exact.source,
        turn_ids=exact.turn_ids,
        trusted_signer_id=trusted_signer.signer_id,
        invocation_signature_valid=True,
        session_bound=True,
        turn_bound=True,
        recorded_request_sha256=recorded_request_sha256,
        recorded_request_chars=len(exact.request_bytes.decode("utf-8")),
        harness_model_request_sha256=event.model_request_sha256,
        request_hash_bound=True,
        selected_skill_id=event.selected_skill_id,
        skill_body_sha256=body_sha256,
        skill_body_hash_bound=True,
        skill_body_present_in_recorded_request=True,
    )

