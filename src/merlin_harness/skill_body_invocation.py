"""Harness-authenticated evidence that an exact skill body was invoked.

The event deliberately stores hashes rather than a skill body, request, trace,
or verifier result.  A signer is injected by the caller so this module never
persists a harness secret or treats prompt exposure as invocation evidence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
SCHEMA_VERSION = "merlin-harness-skill-body-invocation-v1"
SIGNATURE_ALGORITHM = "hmac-sha256-v1"


class SkillBodyInvocationError(ValueError):
    """Raised when invocation evidence is malformed or cannot be authenticated."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_safe_id(label: str, value: object) -> str:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        raise SkillBodyInvocationError(f"{label} must be a safe ID")
    return value


def _require_sha256(label: str, value: object) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise SkillBodyInvocationError(f"{label} must be a lowercase SHA-256 digest")
    return value


def load_skill_body_sha256(path: str | Path) -> str:
    """Read one exact ``SKILL.md`` body and return its raw-byte SHA-256 hash.

    The caller may use the body to assemble a model request, but only the hash
    enters the attestation. Symlinks are rejected so the loaded body is the
    regular file the harness resolved at invocation time.
    """

    requested = Path(path).expanduser()
    if requested.name != "SKILL.md" or requested.is_symlink():
        raise SkillBodyInvocationError("skill body must be a regular SKILL.md file")
    try:
        resolved = requested.resolve(strict=True)
        if not resolved.is_file():
            raise SkillBodyInvocationError("skill body must be a regular SKILL.md file")
        body = resolved.read_bytes()
    except OSError as exc:
        raise SkillBodyInvocationError("skill body cannot be read") from exc
    if not body or len(body) > 1_048_576:
        raise SkillBodyInvocationError("skill body size is outside the allowed bounds")
    try:
        body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillBodyInvocationError("skill body must be UTF-8") from exc
    return hashlib.sha256(body).hexdigest()


@dataclass(frozen=True, slots=True)
class HarnessInvocationSigner:
    """A caller-owned HMAC signer for local harness event authentication."""

    signer_id: str
    secret: bytes

    def __post_init__(self) -> None:
        _require_safe_id("signer_id", self.signer_id)
        if not isinstance(self.secret, bytes) or len(self.secret) < 32:
            raise SkillBodyInvocationError("signer secret must contain at least 32 bytes")

    def sign_payload(self, payload: Mapping[str, Any]) -> str:
        return hmac.new(
            self.secret,
            _canonical_json(payload).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify_payload(self, payload: Mapping[str, Any], signature: str) -> bool:
        return hmac.compare_digest(self.sign_payload(payload), signature)


@dataclass(frozen=True, slots=True)
class SkillBodyInvocationEvent:
    """One signed binding from a task-selected skill body through verification."""

    event_id: str
    task_id: str
    task_contract_sha256: str
    selected_skill_id: str
    skill_body_sha256: str
    model_request_sha256: str
    execution_trace_sha256: str
    verifier_result_sha256: str
    verifier_passed: bool
    harness_policy_sha256: str
    signer_id: str
    signature: str
    schema_version: str = SCHEMA_VERSION
    signature_algorithm: str = SIGNATURE_ALGORITHM

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise SkillBodyInvocationError("invocation event schema is unsupported")
        if self.signature_algorithm != SIGNATURE_ALGORITHM:
            raise SkillBodyInvocationError("invocation signature algorithm is unsupported")
        for label in ("event_id", "task_id", "selected_skill_id", "signer_id"):
            _require_safe_id(label, getattr(self, label))
        for label in (
            "task_contract_sha256",
            "skill_body_sha256",
            "model_request_sha256",
            "execution_trace_sha256",
            "verifier_result_sha256",
            "harness_policy_sha256",
            "signature",
        ):
            _require_sha256(label, getattr(self, label))
        if not isinstance(self.verifier_passed, bool):
            raise SkillBodyInvocationError("verifier_passed must be boolean")

    def unsigned_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "signature_algorithm": self.signature_algorithm,
            "event_id": self.event_id,
            "task_id": self.task_id,
            "task_contract_sha256": self.task_contract_sha256,
            "selected_skill_id": self.selected_skill_id,
            "skill_body_sha256": self.skill_body_sha256,
            "model_request_sha256": self.model_request_sha256,
            "execution_trace_sha256": self.execution_trace_sha256,
            "verifier_result_sha256": self.verifier_result_sha256,
            "verifier_passed": self.verifier_passed,
            "harness_policy_sha256": self.harness_policy_sha256,
            "signer_id": self.signer_id,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_payload(), "signature": self.signature}


def create_skill_body_invocation_event(
    *,
    event_id: str,
    task_id: str,
    task_contract_sha256: str,
    selected_skill_id: str,
    skill_body_sha256: str,
    model_request_sha256: str,
    execution_trace_sha256: str,
    verifier_result_sha256: str,
    verifier_passed: bool,
    harness_policy_sha256: str,
    signer: HarnessInvocationSigner,
) -> SkillBodyInvocationEvent:
    """Create a canonical event after all body/request/trace/result hashes exist."""

    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "event_id": event_id,
        "task_id": task_id,
        "task_contract_sha256": task_contract_sha256,
        "selected_skill_id": selected_skill_id,
        "skill_body_sha256": skill_body_sha256,
        "model_request_sha256": model_request_sha256,
        "execution_trace_sha256": execution_trace_sha256,
        "verifier_result_sha256": verifier_result_sha256,
        "verifier_passed": verifier_passed,
        "harness_policy_sha256": harness_policy_sha256,
        "signer_id": signer.signer_id,
    }
    # Construct once before signing so malformed source values fail deterministically.
    provisional = SkillBodyInvocationEvent(**unsigned, signature="0" * 64)
    return SkillBodyInvocationEvent(
        **provisional.unsigned_payload(),
        signature=signer.sign_payload(provisional.unsigned_payload()),
    )


def validate_skill_body_invocation_event(
    event: SkillBodyInvocationEvent,
    *,
    signer: HarnessInvocationSigner,
) -> None:
    """Fail closed unless the event is signed by the expected harness signer."""

    if event.signer_id != signer.signer_id:
        raise SkillBodyInvocationError("invocation signer does not match the trusted harness")
    if not signer.verify_payload(event.unsigned_payload(), event.signature):
        raise SkillBodyInvocationError("invocation signature verification failed")


def skill_body_invocation_event_from_dict(
    payload: Mapping[str, Any],
) -> SkillBodyInvocationEvent:
    try:
        return SkillBodyInvocationEvent(**dict(payload))
    except TypeError as exc:
        raise SkillBodyInvocationError("invocation event payload does not match schema") from exc
