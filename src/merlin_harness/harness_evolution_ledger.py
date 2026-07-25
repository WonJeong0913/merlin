"""Durable, evidence-bound longitudinal metrics for harness evolution.

The ledger deliberately keeps lifecycle quality metrics separate from resource
economics.  Promotion, rollback, and regression rates can always be reported
from frozen verifier evidence.  A governance-to-savings ratio (G/S) is exposed
only when every observation in the window uses the same verifier epoch,
resource unit, resource dimension, and accounting window, and when direct
savings have their own evidence hash.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .harnessx_aegis import validate_harnessx_aegis_campaign
from .harnessx_verifier_suites import get_tool_policy_verifier_suite


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA_VERSION = "merlin-harness-evolution-ledger-v1"
MAX_LEDGER_BYTES = 16_777_216
MAX_LEDGER_RECORDS = 100_000


class HarnessEvolutionLedgerError(ValueError):
    """Raised when longitudinal evidence is malformed or cannot be pooled."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise HarnessEvolutionLedgerError(f"{name} must be a non-empty string")


def _require_sha(name: str, value: str | None, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise HarnessEvolutionLedgerError(
            f"{name} must be a lowercase SHA-256 digest"
        )


def _require_count(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HarnessEvolutionLedgerError(
            f"{name} must be a non-negative integer"
        )


def _require_amount(name: str, value: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise HarnessEvolutionLedgerError(
            f"{name} must be a finite non-negative number"
        )


@dataclass(frozen=True, slots=True)
class HarnessEvolutionObservation:
    """One verifier-bound harness or lifecycle change observation."""

    observation_id: str
    campaign_id: str
    round_index: int
    change_kind: str
    verifier_epoch_id: str
    verifier_suite_sha256: str
    evidence_sha256: str
    parent_state_sha256: str
    resolved_state_sha256: str
    candidate_count: int
    promotion_count: int
    rollback_count: int
    regression_exposure_count: int
    regression_count: int
    resource_unit: str
    resource_dimension_id: str
    resource_window_id: str
    governance_spend: float
    verified_direct_savings: float
    savings_evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "observation_id",
            "campaign_id",
            "change_kind",
            "verifier_epoch_id",
            "resource_unit",
            "resource_dimension_id",
            "resource_window_id",
        ):
            _require_text(name, getattr(self, name))
        for name in (
            "verifier_suite_sha256",
            "evidence_sha256",
            "parent_state_sha256",
            "resolved_state_sha256",
        ):
            _require_sha(name, getattr(self, name))
        _require_sha(
            "savings_evidence_sha256",
            self.savings_evidence_sha256,
            optional=True,
        )
        for name in (
            "round_index",
            "candidate_count",
            "promotion_count",
            "rollback_count",
            "regression_exposure_count",
            "regression_count",
        ):
            _require_count(name, getattr(self, name))
        if self.round_index < 1:
            raise HarnessEvolutionLedgerError("round_index must be at least 1")
        if self.promotion_count > self.candidate_count:
            raise HarnessEvolutionLedgerError(
                "promotion_count cannot exceed candidate_count"
            )
        if self.regression_count > self.regression_exposure_count:
            raise HarnessEvolutionLedgerError(
                "regression_count cannot exceed regression_exposure_count"
            )
        _require_amount("governance_spend", self.governance_spend)
        _require_amount("verified_direct_savings", self.verified_direct_savings)
        if self.verified_direct_savings > 0 and self.savings_evidence_sha256 is None:
            raise HarnessEvolutionLedgerError(
                "positive verified_direct_savings requires savings evidence"
            )
        if self.verified_direct_savings == 0 and self.savings_evidence_sha256 is not None:
            raise HarnessEvolutionLedgerError(
                "zero verified_direct_savings must not carry savings evidence"
            )

    def canonical_payload(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class HarnessEvolutionSummary:
    observation_count: int
    candidate_count: int
    promotion_count: int
    rollback_count: int
    regression_exposure_count: int
    regression_count: int
    promotion_rate: float | None
    rollback_rate: float | None
    regression_rate: float | None
    governance_spend: float
    verified_direct_savings: float
    governance_to_savings_ratio: float | None
    comparison_dimension_count: int
    ratio_reason: str


def _observation_from_payload(payload: object) -> HarnessEvolutionObservation:
    if not isinstance(payload, dict):
        raise HarnessEvolutionLedgerError("ledger observation must be an object")
    expected = set(HarnessEvolutionObservation.__dataclass_fields__)
    if set(payload) != expected:
        raise HarnessEvolutionLedgerError("ledger observation keys are invalid")
    try:
        return HarnessEvolutionObservation(**payload)
    except TypeError as exc:
        raise HarnessEvolutionLedgerError("ledger observation is invalid") from exc


class HarnessEvolutionLedger:
    """Append-only JSONL ledger with a replayable SHA-256 chain."""

    def __init__(self, observations: Iterable[HarnessEvolutionObservation] = ()) -> None:
        self._observations: list[HarnessEvolutionObservation] = []
        self._ids: set[str] = set()
        for observation in observations:
            self.append_in_memory(observation)

    @property
    def observations(self) -> tuple[HarnessEvolutionObservation, ...]:
        return tuple(self._observations)

    def append_in_memory(self, observation: HarnessEvolutionObservation) -> None:
        if observation.observation_id in self._ids:
            raise HarnessEvolutionLedgerError(
                f"duplicate observation_id: {observation.observation_id}"
            )
        self._observations.append(observation)
        self._ids.add(observation.observation_id)

    def summarize(self, *, rolling_observations: int | None = None) -> HarnessEvolutionSummary:
        if rolling_observations is not None:
            if (
                isinstance(rolling_observations, bool)
                or not isinstance(rolling_observations, int)
                or rolling_observations < 1
            ):
                raise HarnessEvolutionLedgerError(
                    "rolling_observations must be a positive integer"
                )
            window = self._observations[-rolling_observations:]
        else:
            window = self._observations
        candidate_count = sum(item.candidate_count for item in window)
        promotion_count = sum(item.promotion_count for item in window)
        rollback_count = sum(item.rollback_count for item in window)
        regression_exposure_count = sum(
            item.regression_exposure_count for item in window
        )
        regression_count = sum(item.regression_count for item in window)
        governance_spend = sum(item.governance_spend for item in window)
        verified_savings = sum(item.verified_direct_savings for item in window)
        dimensions = {
            (
                item.verifier_epoch_id,
                item.verifier_suite_sha256,
                item.resource_unit,
                item.resource_dimension_id,
                item.resource_window_id,
            )
            for item in window
        }
        if not window:
            ratio = None
            ratio_reason = "no longitudinal observations"
        elif len(dimensions) != 1:
            ratio = None
            ratio_reason = (
                "mixed verifier or resource dimensions cannot produce one G/S ratio"
            )
        elif verified_savings <= 0:
            ratio = None
            ratio_reason = "no verified direct savings in the selected window"
        else:
            ratio = governance_spend / verified_savings
            ratio_reason = "G/S computed from one matched evidence dimension"
        return HarnessEvolutionSummary(
            observation_count=len(window),
            candidate_count=candidate_count,
            promotion_count=promotion_count,
            rollback_count=rollback_count,
            regression_exposure_count=regression_exposure_count,
            regression_count=regression_count,
            promotion_rate=(
                promotion_count / candidate_count if candidate_count else None
            ),
            rollback_rate=(
                rollback_count / promotion_count if promotion_count else None
            ),
            regression_rate=(
                regression_count / regression_exposure_count
                if regression_exposure_count
                else None
            ),
            governance_spend=governance_spend,
            verified_direct_savings=verified_savings,
            governance_to_savings_ratio=ratio,
            comparison_dimension_count=len(dimensions),
            ratio_reason=ratio_reason,
        )

    @classmethod
    def load(cls, path: str | Path) -> "HarnessEvolutionLedger":
        records = load_and_validate_harness_evolution_ledger(path)
        return cls(
            _observation_from_payload(record["observation"])
            for record in records
        )


def _validated_records(raw: bytes) -> tuple[dict[str, object], ...]:
    if len(raw) > MAX_LEDGER_BYTES:
        raise HarnessEvolutionLedgerError("ledger exceeds the byte bound")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise HarnessEvolutionLedgerError("ledger is not valid UTF-8") from exc
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > MAX_LEDGER_RECORDS:
        raise HarnessEvolutionLedgerError("ledger exceeds the record bound")
    previous_sha: str | None = None
    seen: set[str] = set()
    records: list[dict[str, object]] = []
    for sequence, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HarnessEvolutionLedgerError("ledger contains malformed JSON") from exc
        if not isinstance(record, dict):
            raise HarnessEvolutionLedgerError("ledger record must be an object")
        expected = {
            "schema_version",
            "sequence",
            "previous_record_sha256",
            "observation",
            "record_sha256",
        }
        if set(record) != expected:
            raise HarnessEvolutionLedgerError("ledger record keys are invalid")
        observation = _observation_from_payload(record["observation"])
        record_sha = record["record_sha256"]
        body = {key: value for key, value in record.items() if key != "record_sha256"}
        if (
            record["schema_version"] != _SCHEMA_VERSION
            or record["sequence"] != sequence
            or record["previous_record_sha256"] != previous_sha
            or not isinstance(record_sha, str)
            or record_sha != _sha256_json(body)
        ):
            raise HarnessEvolutionLedgerError("ledger hash chain validation failed")
        if observation.observation_id in seen:
            raise HarnessEvolutionLedgerError(
                f"duplicate observation_id: {observation.observation_id}"
            )
        seen.add(observation.observation_id)
        previous_sha = record_sha
        records.append(record)
    return tuple(records)


def load_and_validate_harness_evolution_ledger(
    path: str | Path,
) -> tuple[dict[str, object], ...]:
    requested = Path(path).expanduser()
    if requested.is_symlink():
        raise HarnessEvolutionLedgerError("ledger must be a regular file")
    resolved = requested.resolve(strict=True)
    if not resolved.is_file():
        raise HarnessEvolutionLedgerError("ledger must be a regular file")
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise HarnessEvolutionLedgerError("ledger cannot be read") from exc
    return _validated_records(raw)


def append_harness_evolution_observation(
    path: str | Path,
    observation: HarnessEvolutionObservation,
) -> dict[str, object]:
    """Atomically append one validated observation to a hash-chained JSONL file."""

    ledger_path = Path(path).expanduser()
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    flags = (
        fcntl.LOCK_EX
    )
    try:
        with ledger_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), flags)
            handle.seek(0)
            raw = handle.read()
            records = _validated_records(raw)
            if any(
                record["observation"]["observation_id"] == observation.observation_id
                for record in records
            ):
                raise HarnessEvolutionLedgerError(
                    f"duplicate observation_id: {observation.observation_id}"
                )
            previous_sha = (
                records[-1]["record_sha256"] if records else None
            )
            body: dict[str, object] = {
                "schema_version": _SCHEMA_VERSION,
                "sequence": len(records) + 1,
                "previous_record_sha256": previous_sha,
                "observation": observation.canonical_payload(),
            }
            record = {**body, "record_sha256": _sha256_json(body)}
            encoded = (_canonical_json(record) + "\n").encode("utf-8")
            if len(raw) + len(encoded) > MAX_LEDGER_BYTES:
                raise HarnessEvolutionLedgerError("ledger exceeds the byte bound")
            handle.seek(0, 2)
            handle.write(encoded)
            handle.flush()
            return record
    except OSError as exc:
        raise HarnessEvolutionLedgerError("ledger cannot be appended") from exc


def observations_from_aegis_campaign(
    campaign_dir: str | Path,
    *,
    campaign_id: str,
    verifier_epoch_id: str,
    resource_unit: str,
    resource_dimension_id: str,
    resource_window_id: str,
    verified_savings_by_round: Mapping[int, tuple[float, str]] | None = None,
) -> tuple[HarnessEvolutionObservation, ...]:
    """Convert a validated AEGIS campaign into longitudinal observations."""

    for name, value in (
        ("campaign_id", campaign_id),
        ("verifier_epoch_id", verifier_epoch_id),
        ("resource_unit", resource_unit),
        ("resource_dimension_id", resource_dimension_id),
        ("resource_window_id", resource_window_id),
    ):
        _require_text(name, value)
    root = Path(campaign_dir).expanduser().resolve(strict=True)
    validation = validate_harnessx_aegis_campaign(root)
    try:
        report = json.loads(
            (root / "aegis-campaign-report.json").read_text(encoding="utf-8")
        )
        suite = get_tool_policy_verifier_suite(report["verifier_suite_id"])
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, ValueError) as exc:
        raise HarnessEvolutionLedgerError("AEGIS campaign cannot be read") from exc
    if (
        not validation["valid"]
        or report["verifier_suite_sha256"] != suite.sha256
    ):
        raise HarnessEvolutionLedgerError("AEGIS campaign validation failed")
    savings = dict(verified_savings_by_round or {})
    round_indices = {record["round_index"] for record in report["rounds"]}
    if not set(savings).issubset(round_indices):
        raise HarnessEvolutionLedgerError("savings references an unknown round")
    case_ids = {case.case_id for case in suite.cases}
    observations: list[HarnessEvolutionObservation] = []
    for record in report["rounds"]:
        round_index = record["round_index"]
        round_report = json.loads(
            (root / f"round-{round_index:02d}" / "aegis-round-report.json").read_text(
                encoding="utf-8"
            )
        )
        before_failures = set(record["failure_case_ids_before"])
        after_failures = set(record["failure_case_ids_after"])
        previously_passing = case_ids - before_failures
        regressions = previously_passing & after_failures
        saving_amount = 0.0
        saving_sha: str | None = None
        if round_index in savings:
            saving_amount, saving_sha = savings[round_index]
        observations.append(
            HarnessEvolutionObservation(
                observation_id=f"{campaign_id}:round-{round_index:02d}",
                campaign_id=campaign_id,
                round_index=round_index,
                change_kind="harness_policy",
                verifier_epoch_id=verifier_epoch_id,
                verifier_suite_sha256=suite.sha256,
                evidence_sha256=record["round_evidence_sha256"],
                parent_state_sha256=record["parent_variant_sha256"],
                resolved_state_sha256=record["resolved_variant_sha256"],
                candidate_count=len(round_report["gate_records"]),
                promotion_count=int(round_report["promoted"]),
                rollback_count=0,
                regression_exposure_count=len(previously_passing),
                regression_count=len(regressions),
                resource_unit=resource_unit,
                resource_dimension_id=resource_dimension_id,
                resource_window_id=resource_window_id,
                governance_spend=float(record["provider_call_count"]),
                verified_direct_savings=float(saving_amount),
                savings_evidence_sha256=saving_sha,
            )
        )
    return tuple(observations)


__all__ = [
    "HarnessEvolutionLedger",
    "HarnessEvolutionLedgerError",
    "HarnessEvolutionObservation",
    "HarnessEvolutionSummary",
    "append_harness_evolution_observation",
    "load_and_validate_harness_evolution_ledger",
    "observations_from_aegis_campaign",
]
