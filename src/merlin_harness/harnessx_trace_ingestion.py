"""Hash-bound ingestion of live-hook and shadow traces for AEGIS nomination."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .harnessx_live_hook import load_and_validate_live_hook_audit
from .harnessx_verifier_suites import ToolPolicyVerifierSuite


class HarnessXTraceIngestionError(ValueError):
    """Raised when an observed trace cannot support a bounded AEGIS signal."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class HarnessXTraceSignal:
    case_id: str
    tool_name: str
    command_sha256: str
    command_chars: int
    expected_decision: str
    observed_decision: str
    signal_kind: str
    source_record_sha256: str


@dataclass(frozen=True, slots=True)
class HarnessXTraceIngestion:
    source_kind: str
    source_sha256: str
    source_record_count: int
    verifier_suite_id: str
    verifier_suite_sha256: str
    matched_signals: tuple[HarnessXTraceSignal, ...]
    unknown_record_count: int
    eligible_for_aegis: bool
    blockers: tuple[str, ...]
    parent_variant_sha256: str | None
    evidence_boundary: Mapping[str, bool]

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "merlin-harnessx-trace-ingestion-v1",
            "source_kind": self.source_kind,
            "source_sha256": self.source_sha256,
            "source_record_count": self.source_record_count,
            "verifier_suite_id": self.verifier_suite_id,
            "verifier_suite_sha256": self.verifier_suite_sha256,
            "matched_signals": [asdict(signal) for signal in self.matched_signals],
            "unknown_record_count": self.unknown_record_count,
            "eligible_for_aegis": self.eligible_for_aegis,
            "blockers": list(self.blockers),
            "parent_variant_sha256": self.parent_variant_sha256,
            "evidence_boundary": dict(self.evidence_boundary),
        }

    @property
    def sha256(self) -> str:
        return _sha256_json(self.canonical_payload())

    @property
    def actionable_case_ids(self) -> tuple[str, ...]:
        return tuple(
            signal.case_id
            for signal in self.matched_signals
            if signal.signal_kind == "false_deny"
        )


def _case_index(
    suite: ToolPolicyVerifierSuite,
) -> dict[tuple[str, str, int], str]:
    index: dict[tuple[str, str, int], str] = {}
    for case in suite.cases:
        command_sha256 = hashlib.sha256(case.command.encode("utf-8")).hexdigest()
        key = (case.tool_name, command_sha256, len(case.command))
        if key in index:
            raise HarnessXTraceIngestionError(
                "verifier suite contains observationally ambiguous cases"
            )
        index[key] = case.case_id
    return index


def _expected_by_id(suite: ToolPolicyVerifierSuite) -> dict[str, str]:
    return {case.case_id: case.expected_decision for case in suite.cases}


def ingest_live_hook_audit(
    audit_path: str | Path,
    *,
    verifier_suite: ToolPolicyVerifierSuite,
) -> HarnessXTraceIngestion:
    """Convert validated pre-execution decisions into verifier-bound signals."""

    path = Path(audit_path).expanduser().resolve(strict=True)
    records = load_and_validate_live_hook_audit(path)
    pre_records = [record for record in records if record.get("phase") == "pre_tool_use"]
    if not pre_records:
        raise HarnessXTraceIngestionError("live-hook audit has no pre-execution records")
    index = _case_index(verifier_suite)
    expected = _expected_by_id(verifier_suite)
    variants = {
        record.get("harness_configuration_sha256") for record in pre_records
    }
    if len(variants) != 1 or not all(isinstance(value, str) for value in variants):
        raise HarnessXTraceIngestionError("live-hook audit mixes parent variants")
    parent_variant_sha256 = next(iter(variants))
    signals: list[HarnessXTraceSignal] = []
    unknown = 0
    seen_record_hashes: set[str] = set()
    for record in pre_records:
        record_sha256 = record.get("record_sha256")
        if not isinstance(record_sha256, str) or record_sha256 in seen_record_hashes:
            raise HarnessXTraceIngestionError("live-hook source record binding is invalid")
        seen_record_hashes.add(record_sha256)
        key = (
            record.get("tool_name"),
            record.get("command_sha256"),
            record.get("command_chars"),
        )
        case_id = index.get(key)
        if case_id is None:
            unknown += 1
            continue
        observed = record.get("decision")
        if observed not in {"allow", "deny"}:
            raise HarnessXTraceIngestionError("live-hook decision is invalid")
        expected_decision = expected[case_id]
        if expected_decision == observed:
            kind = "confirmed"
        elif expected_decision == "allow":
            kind = "false_deny"
        else:
            kind = "false_allow"
        signals.append(
            HarnessXTraceSignal(
                case_id=case_id,
                tool_name=key[0],
                command_sha256=key[1],
                command_chars=key[2],
                expected_decision=expected_decision,
                observed_decision=observed,
                signal_kind=kind,
                source_record_sha256=record_sha256,
            )
        )
    false_denies = [signal for signal in signals if signal.signal_kind == "false_deny"]
    false_allows = [signal for signal in signals if signal.signal_kind == "false_allow"]
    blockers: list[str] = []
    if not false_denies:
        blockers.append("no_trace_backed_false_deny")
    if false_allows:
        blockers.append("safety_false_allow_requires_human_review")
    return HarnessXTraceIngestion(
        source_kind="codex_live_pre_tool_use_audit",
        source_sha256=_sha256_file(path),
        source_record_count=len(records),
        verifier_suite_id=verifier_suite.suite_id,
        verifier_suite_sha256=verifier_suite.sha256,
        matched_signals=tuple(signals),
        unknown_record_count=unknown,
        eligible_for_aegis=not blockers,
        blockers=tuple(blockers),
        parent_variant_sha256=parent_variant_sha256,
        evidence_boundary={
            "pre_execution_decision_observed": True,
            "raw_command_ingested": False,
            "verifier_oracle_required": True,
            "trace_alone_authorizes_promotion": False,
            "safety_false_allow_auto_repair": False,
        },
    )


def ingest_chat_shadow_report(
    shadow_path: str | Path,
    *,
    verifier_suite: ToolPolicyVerifierSuite,
) -> HarnessXTraceIngestion:
    """Index post-execution shadow observations without authorizing evolution."""

    path = Path(shadow_path).expanduser().resolve(strict=True)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessXTraceIngestionError("shadow report is invalid") from exc
    if not isinstance(report, dict) or report.get("schema_version") != (
        "merlin-harnessx-chat-shadow-v2"
    ):
        raise HarnessXTraceIngestionError("shadow report schema is unsupported")
    declared = report.get("report_sha256")
    body = {key: value for key, value in report.items() if key != "report_sha256"}
    if declared != _sha256_json(body):
        raise HarnessXTraceIngestionError("shadow report hash mismatch")
    if report.get("status") != "completed":
        raise HarnessXTraceIngestionError("shadow report is not completed")
    claim = report.get("claim_boundary")
    if (
        not isinstance(claim, dict)
        or claim.get("tool_hooks_replayed_after_provider_execution") is not True
        or claim.get("tool_policy_enforced_before_execution") is not False
        or claim.get("harness_candidate_promoted") is not False
    ):
        raise HarnessXTraceIngestionError("shadow claim boundary is invalid")
    observations = report.get("tool_observation", {}).get("observations")
    if not isinstance(observations, list):
        raise HarnessXTraceIngestionError("shadow observations are invalid")
    index = _case_index(verifier_suite)
    expected = _expected_by_id(verifier_suite)
    signals: list[HarnessXTraceSignal] = []
    unknown = 0
    for observation in observations:
        if not isinstance(observation, dict):
            raise HarnessXTraceIngestionError("shadow observation is invalid")
        key = (
            "Bash",
            observation.get("command_sha256"),
            observation.get("command_chars"),
        )
        case_id = index.get(key)
        if case_id is None:
            unknown += 1
            continue
        signals.append(
            HarnessXTraceSignal(
                case_id=case_id,
                tool_name="Bash",
                command_sha256=key[1],
                command_chars=key[2],
                expected_decision=expected[case_id],
                observed_decision="executed_before_shadow_replay",
                signal_kind="post_execution_observation",
                source_record_sha256=observation.get("item_id_sha256", ""),
            )
        )
    return HarnessXTraceIngestion(
        source_kind="codex_chat_shadow_v2",
        source_sha256=_sha256_file(path),
        source_record_count=len(observations),
        verifier_suite_id=verifier_suite.suite_id,
        verifier_suite_sha256=verifier_suite.sha256,
        matched_signals=tuple(signals),
        unknown_record_count=unknown,
        eligible_for_aegis=False,
        blockers=("post_execution_shadow_cannot_nominate_policy_change_alone",),
        parent_variant_sha256=None,
        evidence_boundary={
            "pre_execution_decision_observed": False,
            "raw_command_ingested": False,
            "verifier_oracle_required": True,
            "trace_alone_authorizes_promotion": False,
            "safety_false_allow_auto_repair": False,
        },
    )


def write_trace_ingestion_report(
    path: str | Path,
    ingestion: HarnessXTraceIngestion,
) -> Path:
    output = Path(path).expanduser().resolve()
    payload = ingestion.canonical_payload()
    payload["ingestion_sha256"] = ingestion.sha256
    try:
        with output.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise HarnessXTraceIngestionError(
            "refusing to overwrite trace ingestion report"
        ) from exc
    return output


__all__ = [
    "HarnessXTraceIngestion",
    "HarnessXTraceIngestionError",
    "HarnessXTraceSignal",
    "ingest_chat_shadow_report",
    "ingest_live_hook_audit",
    "write_trace_ingestion_report",
]
