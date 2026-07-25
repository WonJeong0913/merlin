"""Frozen longitudinal campaign for Merlin's real personal workload.

The task contracts in this module are future workload templates grounded in
the operator's active projects.  They are not completed-task claims.  Results
become evidence only after a matched baseline/managed observation is appended
to the hash-chained ledger and passes the frozen verifier contract.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .account_resource_governance import (
    AccountAuthResourceObservation,
    AccountReinvestmentPolicy,
    AccountResourceLedger,
)
from .skill_body_invocation import (
    HarnessInvocationSigner,
    SkillBodyInvocationError,
    SkillBodyInvocationEvent,
    skill_body_invocation_event_from_dict,
    validate_skill_body_invocation_event,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_ISO_UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)


class PersonalWorkloadCampaignError(ValueError):
    """Raised when a frozen contract or longitudinal record is invalid."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _require_safe_id(label: str, value: str) -> None:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        raise PersonalWorkloadCampaignError(f"{label} must be a safe ID")


def _require_sha256(label: str, value: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise PersonalWorkloadCampaignError(
            f"{label} must be a lowercase SHA-256 digest"
        )


def _require_non_negative_int(label: str, value: int | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PersonalWorkloadCampaignError(
            f"{label} must be a non-negative integer"
        )


def _require_non_negative_number(label: str, value: float | None) -> None:
    if value is None:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise PersonalWorkloadCampaignError(
            f"{label} must be a finite non-negative number"
        )


@dataclass(frozen=True, slots=True)
class WorkloadVerifierProfile:
    verifier_id: str
    mode: str
    automatic_checks: tuple[str, ...]
    human_review_required: bool
    evidence_required: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_safe_id("verifier_id", self.verifier_id)
        _require_safe_id("mode", self.mode)
        if not self.automatic_checks:
            raise PersonalWorkloadCampaignError(
                "verifier profile needs at least one automatic check"
            )
        if not self.evidence_required:
            raise PersonalWorkloadCampaignError(
                "verifier profile needs explicit evidence"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verifier_id": self.verifier_id,
            "mode": self.mode,
            "automatic_checks": list(self.automatic_checks),
            "human_review_required": self.human_review_required,
            "evidence_required": list(self.evidence_required),
        }


VERIFIER_PROFILES = (
    WorkloadVerifierProfile(
        verifier_id="markdown-evidence-v1",
        mode="structure-and-human",
        automatic_checks=(
            "required headings present",
            "referenced local artifacts exist",
            "forbidden completion claims absent",
        ),
        human_review_required=True,
        evidence_required=(
            "output sha256",
            "claim-to-evidence checklist",
            "human review decision",
        ),
    ),
    WorkloadVerifierProfile(
        verifier_id="code-test-v1",
        mode="command-and-artifact",
        automatic_checks=(
            "targeted test command exits zero",
            "changed files are inside task scope",
            "result artifact is non-empty",
        ),
        human_review_required=False,
        evidence_required=(
            "output sha256",
            "test command sha256",
            "test result sha256",
        ),
    ),
    WorkloadVerifierProfile(
        verifier_id="visual-human-v1",
        mode="visual-and-human",
        automatic_checks=(
            "rendered artifact exists",
            "expected dimensions or page count match",
            "rendered artifact sha256 recorded",
        ),
        human_review_required=True,
        evidence_required=(
            "source sha256",
            "render sha256",
            "human visual review decision",
        ),
    ),
    WorkloadVerifierProfile(
        verifier_id="apple-build-v1",
        mode="build-and-runtime",
        automatic_checks=(
            "configured build exits zero",
            "bundle or test result exists",
            "runtime evidence pointer is recorded",
        ),
        human_review_required=True,
        evidence_required=(
            "source snapshot sha256",
            "build result sha256",
            "runtime or screenshot sha256",
        ),
    ),
    WorkloadVerifierProfile(
        verifier_id="experiment-schema-v1",
        mode="schema-and-metric",
        automatic_checks=(
            "experiment command exits zero",
            "result schema validates",
            "seed and configuration hashes are recorded",
        ),
        human_review_required=False,
        evidence_required=(
            "configuration sha256",
            "result sha256",
            "metric summary sha256",
        ),
    ),
    WorkloadVerifierProfile(
        verifier_id="automation-dry-run-v1",
        mode="integration-dry-run",
        automatic_checks=(
            "dry run exits zero",
            "no external send or destructive mutation occurred",
            "idempotency evidence validates",
        ),
        human_review_required=True,
        evidence_required=(
            "input fixture sha256",
            "dry-run result sha256",
            "human approval decision",
        ),
    ),
)

_VERIFIER_BY_ID = {profile.verifier_id: profile for profile in VERIFIER_PROFILES}


@dataclass(frozen=True, slots=True)
class PersonalWorkloadTask:
    task_id: str
    family: str
    project_id: str
    title: str
    request_contract: str
    input_contract: tuple[str, ...]
    output_contract: tuple[str, ...]
    verifier_id: str
    risk_tier: str
    privacy_class: str
    cadence: str
    source_basis: str = "operator-confirmed-active-project"

    def __post_init__(self) -> None:
        for label, value in (
            ("task_id", self.task_id),
            ("family", self.family),
            ("project_id", self.project_id),
            ("verifier_id", self.verifier_id),
            ("risk_tier", self.risk_tier),
            ("privacy_class", self.privacy_class),
            ("cadence", self.cadence),
        ):
            _require_safe_id(label, value)
        if self.verifier_id not in _VERIFIER_BY_ID:
            raise PersonalWorkloadCampaignError(
                f"unknown verifier profile: {self.verifier_id}"
            )
        if not self.title.strip() or not self.request_contract.strip():
            raise PersonalWorkloadCampaignError(
                "task title and request contract must be non-empty"
            )
        if not self.input_contract or not self.output_contract:
            raise PersonalWorkloadCampaignError(
                "task input and output contracts must be non-empty"
            )
        if self.risk_tier not in {"low", "medium", "high"}:
            raise PersonalWorkloadCampaignError("unsupported risk tier")
        if self.privacy_class not in {
            "local-private",
            "project-private",
            "public-candidate",
        }:
            raise PersonalWorkloadCampaignError("unsupported privacy class")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["input_contract"] = list(self.input_contract)
        payload["output_contract"] = list(self.output_contract)
        payload["contract_sha256"] = _sha256_json(payload)
        return payload


def _task(
    task_id: str,
    family: str,
    project_id: str,
    title: str,
    request_contract: str,
    verifier_id: str,
    *,
    inputs: tuple[str, ...] = ("frozen source snapshot", "bounded request"),
    outputs: tuple[str, ...] = ("requested artifact", "verification evidence"),
    risk: str = "medium",
    privacy: str = "project-private",
    cadence: str = "weekly",
) -> PersonalWorkloadTask:
    return PersonalWorkloadTask(
        task_id=task_id,
        family=family,
        project_id=project_id,
        title=title,
        request_contract=request_contract,
        input_contract=inputs,
        output_contract=outputs,
        verifier_id=verifier_id,
        risk_tier=risk,
        privacy_class=privacy,
        cadence=cadence,
    )


PERSONAL_WORKLOAD_50_TASKS = (
    # Research and documentation: 12
    _task("pw-rd-01", "research-docs", "merlin", "Current-state evidence report",
          "Reconcile implementation, tests, evidence boundaries, and readiness in a dated report.",
          "markdown-evidence-v1", privacy="public-candidate"),
    _task("pw-rd-02", "research-docs", "merlin", "Paper method revision",
          "Revise one method section while preserving frozen definitions and implementation mappings.",
          "markdown-evidence-v1", privacy="public-candidate"),
    _task("pw-rd-03", "research-docs", "merlin", "Metric and equation consistency audit",
          "Check symbols, denominators, and implementation references across the paper notes.",
          "markdown-evidence-v1", privacy="public-candidate"),
    _task("pw-rd-04", "research-docs", "merlin", "Related-work comparison matrix",
          "Update a bounded comparison without turning design differences into empirical claims.",
          "markdown-evidence-v1", privacy="public-candidate"),
    _task("pw-rd-05", "research-docs", "merlin", "Claim-to-evidence boundary audit",
          "Find unsupported completion, superiority, and invocation claims and propose exact corrections.",
          "markdown-evidence-v1", privacy="public-candidate"),
    _task("pw-rd-06", "research-docs", "merlin", "README status synchronization",
          "Synchronize current verified capabilities, limitations, and reproduction commands.",
          "markdown-evidence-v1", privacy="public-candidate"),
    _task("pw-rd-07", "research-docs", "merlin", "Experiment preregistration",
          "Freeze hypotheses, matched conditions, endpoints, exclusions, and stop conditions.",
          "markdown-evidence-v1", risk="high", privacy="public-candidate"),
    _task("pw-rd-08", "research-docs", "merlin", "Research critique response",
          "Answer one substantive critique with evidence, limitation, and a falsifiable follow-up.",
          "markdown-evidence-v1", privacy="public-candidate"),
    _task("pw-rd-09", "research-docs", "merlin", "Engineering handoff update",
          "Produce a concise handoff with changed files, verification, blockers, and next action.",
          "markdown-evidence-v1"),
    _task("pw-rd-10", "research-docs", "merlin", "Canonical wiki synchronization",
          "Reflect one classified thesis, architecture, experiment, or implementation update in canonical notes.",
          "markdown-evidence-v1", risk="high", privacy="local-private"),
    _task("pw-rd-11", "research-docs", "merlin", "Judge-package consistency audit",
          "Compare a frozen package against current claims without modifying the package or remote repository.",
          "markdown-evidence-v1", privacy="public-candidate"),
    _task("pw-rd-12", "research-docs", "merlin", "Weekly research synthesis",
          "Summarize observed progress, failed hypotheses, evidence gaps, and next experiments.",
          "markdown-evidence-v1", privacy="local-private"),

    # Merlin code and experiments: 10
    _task("pw-ke-01", "king-engineering", "merlin", "Focused regression repair",
          "Diagnose and repair a bounded failing test set, then rerun only relevant regression checks.",
          "code-test-v1"),
    _task("pw-ke-02", "king-engineering", "merlin", "Full-suite regression audit",
          "Run the available test suite and classify failures without merging separate run scopes.",
          "code-test-v1", risk="high"),
    _task("pw-ke-03", "king-engineering", "merlin", "Typed hook processor change",
          "Implement one bounded HarnessX processor contract with fail-closed validation.",
          "code-test-v1", risk="high"),
    _task("pw-ke-04", "king-engineering", "merlin", "Skill repair candidate",
          "Generate a quarantined skill-contract repair from verifier-backed failure evidence.",
          "code-test-v1", risk="high"),
    _task("pw-ke-05", "king-engineering", "merlin", "Promotion-gate replay",
          "Evaluate a candidate under same-verifier acceptance criteria and record the decision.",
          "experiment-schema-v1", risk="high"),
    _task("pw-ke-06", "king-engineering", "merlin", "Rollback replay",
          "Reproduce one rejected or regressed candidate and verify copy-on-write rollback.",
          "experiment-schema-v1", risk="high"),
    _task("pw-ke-07", "king-engineering", "merlin", "Trace-ingestion validation",
          "Normalize a bounded trace fixture and verify false-deny and false-allow boundaries.",
          "code-test-v1", risk="high"),
    _task("pw-ke-08", "king-engineering", "merlin", "Evolution-ledger update",
          "Append and validate one evidence-bound harness-evolution observation.",
          "experiment-schema-v1", risk="high"),
    _task("pw-ke-09", "king-engineering", "merlin", "Campaign artifact validation",
          "Replay and hash-validate one frozen campaign artifact bundle.",
          "experiment-schema-v1"),
    _task("pw-ke-10", "king-engineering", "merlin", "CLI and documentation contract",
          "Add or repair one operator command and verify its documented output contract.",
          "code-test-v1"),

    # Figures and presentations: 8
    _task("pw-fp-01", "figures-presentations", "merlin", "Method architecture figure",
          "Render the current self-managing harness control flow as a publication-ready figure.",
          "visual-human-v1", privacy="public-candidate"),
    _task("pw-fp-02", "figures-presentations", "merlin", "G-over-S longitudinal figure",
          "Render cumulative governance spend and verified savings without imputing missing values.",
          "visual-human-v1", risk="high", privacy="public-candidate"),
    _task("pw-fp-03", "figures-presentations", "merlin", "Ablation comparison chart",
          "Render matched condition results with denominators, uncertainty, and evidence state.",
          "visual-human-v1", privacy="public-candidate"),
    _task("pw-fp-04", "figures-presentations", "merlin", "Lifecycle flow diagram",
          "Render create, quarantine, validate, promote, repair, hide, retire, and rollback transitions.",
          "visual-human-v1", privacy="public-candidate"),
    _task("pw-fp-05", "figures-presentations", "merlin", "Seminar slide revision",
          "Revise one academic slide while preserving claim hierarchy and readable typography.",
          "visual-human-v1", privacy="public-candidate"),
    _task("pw-fp-06", "figures-presentations", "merlin", "Demo or judge deck revision",
          "Revise one proof-oriented slide using only frozen implementation evidence.",
          "visual-human-v1", privacy="public-candidate"),
    _task("pw-fp-07", "figures-presentations", "merlin", "Figure-caption audit",
          "Align a figure, caption, legend, and source note with the underlying data contract.",
          "markdown-evidence-v1", privacy="public-candidate"),
    _task("pw-fp-08", "figures-presentations", "merlin", "Deck export visual QA",
          "Export and inspect a deck montage for clipping, overflow, font, and layout regressions.",
          "visual-human-v1", privacy="public-candidate"),

    # Apple app work: 8
    _task("pw-ap-01", "apple-apps", "dynamicnotch", "DynamicNotch build smoke",
          "Build the configured macOS target and capture the exact build result.",
          "apple-build-v1"),
    _task("pw-ap-02", "apple-apps", "dynamicnotch", "DynamicNotch visual alignment",
          "Change one bounded notch-alignment detail and visually verify the accepted baseline.",
          "apple-build-v1"),
    _task("pw-ap-03", "apple-apps", "dynamicnotch", "DynamicNotch runtime diagnosis",
          "Reproduce one runtime issue, inspect logs, and report the evidence-backed cause.",
          "apple-build-v1"),
    _task("pw-ap-04", "apple-apps", "dynamicnotch", "DynamicNotch release smoke",
          "Run a local release-configuration smoke without publishing or notarizing.",
          "apple-build-v1", risk="high"),
    _task("pw-ap-05", "apple-apps", "vocabmaster-ios", "Small-iPhone layout regression",
          "Build and visually verify the target screen on a compact simulator.",
          "apple-build-v1"),
    _task("pw-ap-06", "apple-apps", "vocabmaster-ios", "Wrong-word review flow",
          "Exercise the wrong-word review flow and verify stored-state transitions.",
          "apple-build-v1"),
    _task("pw-ap-07", "apple-apps", "scanfree", "Scanner pipeline smoke",
          "Build and exercise one scanner input-to-result path with a controlled fixture.",
          "apple-build-v1", risk="high", privacy="local-private"),
    _task("pw-ap-08", "apple-apps", "scanfree", "Physical-device install check",
          "Build, install, and launch on the explicitly selected device without changing signing scope.",
          "apple-build-v1", risk="high", privacy="local-private"),

    # ML and data experiments: 6
    _task("pw-ml-01", "ml-data", "sam3-scm", "Experiment configuration freeze",
          "Freeze dataset split, seed, model configuration, and evaluation metric hashes.",
          "experiment-schema-v1", risk="high"),
    _task("pw-ml-02", "ml-data", "sam3-scm", "Ablation execution",
          "Run one declared ablation and validate the result schema and denominators.",
          "experiment-schema-v1", risk="high"),
    _task("pw-ml-03", "ml-data", "sam3-scm", "Metric consistency audit",
          "Recompute one reported metric from frozen predictions and compare exact values.",
          "experiment-schema-v1"),
    _task("pw-ml-04", "ml-data", "sam3-scm", "Qualitative result figure",
          "Render a bounded qualitative comparison without cherry-picking undocumented samples.",
          "visual-human-v1", privacy="public-candidate"),
    _task("pw-ml-05", "ml-data", "rpi5-ppe", "TFLite inference regression",
          "Run a fixed input corpus and validate output schema, latency, and failure counts.",
          "experiment-schema-v1", risk="high"),
    _task("pw-ml-06", "ml-data", "rpi5-ppe", "Vest-association regression",
          "Replay a fixed association case set and report identity-switch and miss counts.",
          "experiment-schema-v1", risk="high"),

    # Backend and automation: 6
    _task("pw-ba-01", "backend-automation", "spring-learning", "CRUD endpoint regression",
          "Run bounded create, read, update, and delete tests against an isolated local database.",
          "code-test-v1"),
    _task("pw-ba-02", "backend-automation", "spring-learning", "Hibernate identity diagnosis",
          "Reproduce one persistence failure and verify the mapping or seed-data correction.",
          "code-test-v1"),
    _task("pw-ba-03", "backend-automation", "spring-learning", "API response contract",
          "Validate status codes and response schema for one controller family.",
          "code-test-v1"),
    _task("pw-ba-04", "backend-automation", "inquiry-automation", "Draft-only reply dry run",
          "Generate reply drafts from fixtures while proving that no message was sent.",
          "automation-dry-run-v1", risk="high", privacy="local-private"),
    _task("pw-ba-05", "backend-automation", "inquiry-automation", "Message deduplication replay",
          "Replay repeated message IDs and verify that later distinct inbound messages remain eligible.",
          "automation-dry-run-v1", risk="high", privacy="local-private"),
    _task("pw-ba-06", "backend-automation", "inquiry-automation", "Template and config validation",
          "Validate runtime templates, required properties, and safe missing-config behavior.",
          "automation-dry-run-v1", risk="high", privacy="local-private"),
)


EXPECTED_FAMILY_COUNTS = {
    "apple-apps": 8,
    "backend-automation": 6,
    "figures-presentations": 8,
    "king-engineering": 10,
    "ml-data": 6,
    "research-docs": 12,
}

if len(PERSONAL_WORKLOAD_50_TASKS) != 50:
    raise RuntimeError("personal workload campaign must contain exactly 50 tasks")


def personal_workload_manifest_payload() -> dict[str, Any]:
    family_counts: dict[str, int] = {}
    for task in PERSONAL_WORKLOAD_50_TASKS:
        family_counts[task.family] = family_counts.get(task.family, 0) + 1
    return {
        "schema_version": "merlin-personal-workload-manifest-v1",
        "campaign_id": "merlin-personal-workload-50-longitudinal-v1",
        "frozen_on": "2026-07-24",
        "execution_window": {
            "minimum_elapsed_days": 14,
            "target_elapsed_days": 28,
            "phase_1_matched_pairs": 50,
            "phase_2_matched_pairs": 50,
            "target_matched_pairs": 100,
        },
        "conditions": {
            "baseline": "same agent without Merlin managed skill-harness assistance",
            "managed": "same agent with Merlin provisioning, lifecycle, and HarnessX controls",
            "provider_mode": "account-auth",
            "api_billing_required": False,
            "same_provider_model_effort_required": True,
            "same_verifier_epoch_required": True,
            "clean_input_snapshot_required": True,
            "actual_invocation_evidence_required": True,
            "low_cost_model_comparison_included": False,
        },
        "primary_endpoints": [
            "matched verifier pass rate",
            "verified provider-turn savings",
            "governance provider turns",
            "governance-to-savings ratio",
            "selection and invocation error rates",
            "lifecycle promotions rollbacks and regressions",
        ],
        "level_7_acceptance": {
            "unique_task_contracts_completed": 50,
            "minimum_elapsed_days": 14,
            "target_matched_observations": 100,
            "minimum_lifecycle_changes": 10,
            "promotion_observed": True,
            "rollback_observed": True,
            "actual_invocation_evidence_complete": True,
            "matched_baseline_required": True,
            "g_over_s_or_turn_equivalent_required": True,
        },
        "family_counts": dict(sorted(family_counts.items())),
        "verifier_profiles": [
            profile.to_dict() for profile in VERIFIER_PROFILES
        ],
        "tasks": [task.to_dict() for task in PERSONAL_WORKLOAD_50_TASKS],
        "evidence_boundary": {
            "task_contracts_frozen": True,
            "task_executions_completed_at_freeze": 0,
            "matched_observations_at_freeze": 0,
            "level_7_achieved_at_freeze": False,
            "future_results_must_be_appended": True,
        },
    }


PERSONAL_WORKLOAD_MANIFEST_SHA256 = _sha256_json(
    personal_workload_manifest_payload()
)


def personal_workload_schedule_payload() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for repetition in (1, 2):
        for ordinal, task in enumerate(PERSONAL_WORKLOAD_50_TASKS, start=1):
            first_is_baseline = (ordinal + repetition) % 2 == 0
            order = (
                ["baseline", "managed"]
                if first_is_baseline
                else ["managed", "baseline"]
            )
            rows.append(
                {
                    "pair_id": f"{task.task_id}-r{repetition}",
                    "task_id": task.task_id,
                    "task_contract_sha256": task.to_dict()["contract_sha256"],
                    "repetition": repetition,
                    "phase": repetition,
                    "arm_order": order,
                    "clean_input_snapshot_required": True,
                    "carryover_prohibited": True,
                }
            )
    return {
        "schema_version": "merlin-personal-workload-schedule-v1",
        "campaign_id": "merlin-personal-workload-50-longitudinal-v1",
        "manifest_sha256": PERSONAL_WORKLOAD_MANIFEST_SHA256,
        "pair_count": len(rows),
        "ordering": "balanced two-period crossover by task ordinal and repetition",
        "pairs": rows,
    }


PERSONAL_WORKLOAD_SCHEDULE_SHA256 = _sha256_json(
    personal_workload_schedule_payload()
)


@dataclass(frozen=True, slots=True)
class WorkloadArmEvidence:
    success: bool
    verifier_passed: bool
    execution_turns: int
    trace_sha256: str
    output_sha256: str
    model_request_sha256: str
    verifier_result_sha256: str
    actual_invocation_evidence_complete: bool
    selected_skill_ids: tuple[str, ...] = ()
    invoked_skill_ids: tuple[str, ...] = ()
    invocation_events: tuple[SkillBodyInvocationEvent, ...] = ()
    total_tokens: int | None = None
    latency_s: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.success, bool) or not isinstance(
            self.verifier_passed, bool
        ):
            raise PersonalWorkloadCampaignError(
                "arm success fields must be booleans"
            )
        if self.success != self.verifier_passed:
            raise PersonalWorkloadCampaignError(
                "arm success must equal the frozen verifier result"
            )
        _require_non_negative_int("execution_turns", self.execution_turns)
        _require_non_negative_int("total_tokens", self.total_tokens)
        _require_non_negative_number("latency_s", self.latency_s)
        _require_sha256("trace_sha256", self.trace_sha256)
        _require_sha256("output_sha256", self.output_sha256)
        _require_sha256("model_request_sha256", self.model_request_sha256)
        _require_sha256("verifier_result_sha256", self.verifier_result_sha256)
        if not isinstance(self.actual_invocation_evidence_complete, bool):
            raise PersonalWorkloadCampaignError(
                "actual_invocation_evidence_complete must be boolean"
            )
        for collection in (self.selected_skill_ids, self.invoked_skill_ids):
            if len(collection) != len(set(collection)):
                raise PersonalWorkloadCampaignError(
                    "skill evidence contains duplicate IDs"
                )
            for skill_id in collection:
                _require_safe_id("skill_id", skill_id)
        event_skill_ids = tuple(event.selected_skill_id for event in self.invocation_events)
        if len(event_skill_ids) != len(set(event_skill_ids)):
            raise PersonalWorkloadCampaignError(
                "invocation evidence contains duplicate skill IDs"
            )
        if self.actual_invocation_evidence_complete:
            if self.selected_skill_ids != self.invoked_skill_ids:
                raise PersonalWorkloadCampaignError(
                    "complete invocation evidence requires selected and invoked skill IDs to match"
                )
            if set(event_skill_ids) != set(self.invoked_skill_ids):
                raise PersonalWorkloadCampaignError(
                    "complete invocation evidence requires one event per invoked skill"
                )
        if not self.invoked_skill_ids and self.invocation_events:
            raise PersonalWorkloadCampaignError(
                "skill invocation event cannot exist without an invoked skill"
            )
        for event in self.invocation_events:
            if (
                event.model_request_sha256 != self.model_request_sha256
                or event.execution_trace_sha256 != self.trace_sha256
                or event.verifier_result_sha256 != self.verifier_result_sha256
                or event.verifier_passed != self.verifier_passed
            ):
                raise PersonalWorkloadCampaignError(
                    "skill invocation event does not bind this arm evidence"
                )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["selected_skill_ids"] = list(self.selected_skill_ids)
        payload["invoked_skill_ids"] = list(self.invoked_skill_ids)
        payload["invocation_events"] = [
            event.to_dict() for event in self.invocation_events
        ]
        return payload


@dataclass(frozen=True, slots=True)
class MatchedWorkloadObservation:
    observation_id: str
    pair_id: str
    task_id: str
    repetition: int
    arm_order: tuple[str, str]
    observed_at_utc: str
    manifest_sha256: str
    task_contract_sha256: str
    verifier_epoch_id: str
    quota_window_id: str
    provider_id: str
    model_id: str
    effort: str
    input_snapshot_sha256: str
    baseline: WorkloadArmEvidence
    managed: WorkloadArmEvidence
    governance_turns: int
    governance_total_tokens: int | None = None
    governance_latency_s: float | None = None
    lifecycle_action_ids: tuple[str, ...] = ()
    lifecycle_action_kinds: tuple[str, ...] = ()
    selection_error_count: int = 0
    invocation_error_count: int = 0
    regression_count: int = 0
    human_review_passed: bool | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("observation_id", self.observation_id),
            ("pair_id", self.pair_id),
            ("task_id", self.task_id),
            ("verifier_epoch_id", self.verifier_epoch_id),
            ("quota_window_id", self.quota_window_id),
            ("provider_id", self.provider_id),
            ("model_id", self.model_id),
            ("effort", self.effort),
        ):
            _require_safe_id(label, value)
        if self.repetition not in {1, 2}:
            raise PersonalWorkloadCampaignError(
                "repetition must be phase 1 or phase 2"
            )
        if self.arm_order not in {
            ("baseline", "managed"),
            ("managed", "baseline"),
        }:
            raise PersonalWorkloadCampaignError(
                "arm_order must contain baseline and managed exactly once"
            )
        if not isinstance(self.observed_at_utc, str) or not _ISO_UTC_RE.fullmatch(
            self.observed_at_utc
        ):
            raise PersonalWorkloadCampaignError(
                "observed_at_utc must be an ISO UTC timestamp ending in Z"
            )
        for label, value in (
            ("manifest_sha256", self.manifest_sha256),
            ("task_contract_sha256", self.task_contract_sha256),
            ("input_snapshot_sha256", self.input_snapshot_sha256),
        ):
            _require_sha256(label, value)
        for label, value in (
            ("governance_turns", self.governance_turns),
            ("governance_total_tokens", self.governance_total_tokens),
            ("selection_error_count", self.selection_error_count),
            ("invocation_error_count", self.invocation_error_count),
            ("regression_count", self.regression_count),
        ):
            _require_non_negative_int(label, value)
        _require_non_negative_number(
            "governance_latency_s", self.governance_latency_s
        )
        if self.human_review_passed is not None and not isinstance(
            self.human_review_passed, bool
        ):
            raise PersonalWorkloadCampaignError(
                "human_review_passed must be boolean or null"
            )
        if len(self.lifecycle_action_ids) != len(set(self.lifecycle_action_ids)):
            raise PersonalWorkloadCampaignError(
                "lifecycle_action_ids contains duplicates"
            )
        if len(self.lifecycle_action_ids) != len(self.lifecycle_action_kinds):
            raise PersonalWorkloadCampaignError(
                "lifecycle action IDs and kinds must have equal length"
            )
        for action_id in self.lifecycle_action_ids:
            _require_safe_id("lifecycle_action_id", action_id)
        allowed_action_kinds = {
            "create",
            "promote",
            "repair",
            "hide",
            "retire",
            "rollback",
            "provisioning-repair",
            "harness-update",
        }
        for action_kind in self.lifecycle_action_kinds:
            if action_kind not in allowed_action_kinds:
                raise PersonalWorkloadCampaignError(
                    "unsupported lifecycle action kind"
                )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["arm_order"] = list(self.arm_order)
        payload["baseline"] = self.baseline.to_dict()
        payload["managed"] = self.managed.to_dict()
        payload["lifecycle_action_ids"] = list(self.lifecycle_action_ids)
        payload["lifecycle_action_kinds"] = list(self.lifecycle_action_kinds)
        return payload

    def to_account_resource_observation(
        self,
    ) -> AccountAuthResourceObservation:
        return AccountAuthResourceObservation(
            observation_id=self.observation_id,
            task_id=self.task_id,
            evaluation_contract_sha256=self.task_contract_sha256,
            verifier_epoch_id=self.verifier_epoch_id,
            quota_window_id=self.quota_window_id,
            provider_id=self.provider_id,
            model_id=self.model_id,
            effort=self.effort,
            baseline_success=self.baseline.success,
            managed_success=self.managed.success,
            baseline_execution_turns=self.baseline.execution_turns,
            managed_execution_turns=self.managed.execution_turns,
            governance_turns=self.governance_turns,
            baseline_total_tokens=self.baseline.total_tokens,
            managed_total_tokens=self.managed.total_tokens,
            governance_total_tokens=self.governance_total_tokens,
            baseline_latency_s=self.baseline.latency_s,
            managed_latency_s=self.managed.latency_s,
            governance_latency_s=self.governance_latency_s,
        )


def _task_lookup() -> dict[str, PersonalWorkloadTask]:
    return {task.task_id: task for task in PERSONAL_WORKLOAD_50_TASKS}


def _schedule_lookup() -> dict[str, Mapping[str, Any]]:
    return {
        row["pair_id"]: row
        for row in personal_workload_schedule_payload()["pairs"]
    }


def _validate_observation_contract(
    observation: MatchedWorkloadObservation,
    *,
    invocation_signer: HarnessInvocationSigner | None = None,
) -> None:
    if observation.manifest_sha256 != PERSONAL_WORKLOAD_MANIFEST_SHA256:
        raise PersonalWorkloadCampaignError(
            "observation does not match the frozen manifest"
        )
    row = _schedule_lookup().get(observation.pair_id)
    if row is None:
        raise PersonalWorkloadCampaignError(
            "observation pair is outside the frozen schedule"
        )
    if (
        row["task_id"] != observation.task_id
        or row["repetition"] != observation.repetition
        or tuple(row["arm_order"]) != observation.arm_order
        or row["task_contract_sha256"] != observation.task_contract_sha256
    ):
        raise PersonalWorkloadCampaignError(
            "observation task contract does not match its scheduled pair"
        )
    profile = _VERIFIER_BY_ID[_task_lookup()[observation.task_id].verifier_id]
    if profile.human_review_required and observation.human_review_passed is not True:
        raise PersonalWorkloadCampaignError(
            "human review is required by the frozen verifier"
        )
    if not (
        observation.baseline.actual_invocation_evidence_complete
        and observation.managed.actual_invocation_evidence_complete
    ):
        raise PersonalWorkloadCampaignError(
            "both arms require complete actual invocation evidence"
        )
    invocation_events = (
        *observation.baseline.invocation_events,
        *observation.managed.invocation_events,
    )
    if invocation_events and invocation_signer is None:
        raise PersonalWorkloadCampaignError(
            "trusted harness signer is required for skill invocation evidence"
        )
    for event in invocation_events:
        if (
            event.task_id != observation.task_id
            or event.task_contract_sha256 != observation.task_contract_sha256
        ):
            raise PersonalWorkloadCampaignError(
                "skill invocation event does not bind this task contract"
            )
        try:
            validate_skill_body_invocation_event(event, signer=invocation_signer)
        except SkillBodyInvocationError as exc:
            raise PersonalWorkloadCampaignError(
                "skill invocation event is not trusted"
            ) from exc


def observation_from_dict(
    payload: Mapping[str, Any],
    *,
    invocation_signer: HarnessInvocationSigner | None = None,
) -> MatchedWorkloadObservation:
    try:
        baseline = WorkloadArmEvidence(
            **{
                **payload["baseline"],
                "selected_skill_ids": tuple(
                    payload["baseline"].get("selected_skill_ids", ())
                ),
                "invoked_skill_ids": tuple(
                    payload["baseline"].get("invoked_skill_ids", ())
                ),
                "invocation_events": tuple(
                    skill_body_invocation_event_from_dict(item)
                    for item in payload["baseline"].get("invocation_events", ())
                ),
            }
        )
        managed = WorkloadArmEvidence(
            **{
                **payload["managed"],
                "selected_skill_ids": tuple(
                    payload["managed"].get("selected_skill_ids", ())
                ),
                "invoked_skill_ids": tuple(
                    payload["managed"].get("invoked_skill_ids", ())
                ),
                "invocation_events": tuple(
                    skill_body_invocation_event_from_dict(item)
                    for item in payload["managed"].get("invocation_events", ())
                ),
            }
        )
        observation = MatchedWorkloadObservation(
            **{
                **{
                    key: value
                    for key, value in payload.items()
                    if key
                    not in {
                        "baseline",
                        "managed",
                        "arm_order",
                        "lifecycle_action_ids",
                        "lifecycle_action_kinds",
                    }
                },
                "baseline": baseline,
                "managed": managed,
                "arm_order": tuple(payload["arm_order"]),
                "lifecycle_action_ids": tuple(
                    payload.get("lifecycle_action_ids", ())
                ),
                "lifecycle_action_kinds": tuple(
                    payload.get("lifecycle_action_kinds", ())
                ),
            }
        )
    except (KeyError, TypeError, SkillBodyInvocationError) as exc:
        raise PersonalWorkloadCampaignError(
            "observation payload does not match the frozen schema"
        ) from exc
    _validate_observation_contract(observation, invocation_signer=invocation_signer)
    return observation


def _read_ledger(
    path: Path,
    *,
    invocation_signer: HarnessInvocationSigner | None = None,
) -> list[dict[str, Any]]:
    if not path.exists():
        raise PersonalWorkloadCampaignError("observation ledger is missing")
    records: list[dict[str, Any]] = []
    previous_hash = "0" * 64
    try:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            envelope = json.loads(line)
            if envelope.get("sequence") != len(records) + 1:
                raise PersonalWorkloadCampaignError(
                    f"ledger sequence drift at line {line_number}"
                )
            if envelope.get("previous_record_sha256") != previous_hash:
                raise PersonalWorkloadCampaignError(
                    f"ledger chain drift at line {line_number}"
                )
            body = {
                "sequence": envelope["sequence"],
                "previous_record_sha256": envelope["previous_record_sha256"],
                "observation": envelope["observation"],
            }
            record_hash = _sha256_json(body)
            if envelope.get("record_sha256") != record_hash:
                raise PersonalWorkloadCampaignError(
                    f"ledger hash drift at line {line_number}"
                )
            observation_from_dict(
                envelope["observation"], invocation_signer=invocation_signer
            )
            records.append(envelope)
            previous_hash = record_hash
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PersonalWorkloadCampaignError(
            "observation ledger cannot be decoded"
        ) from exc
    ids = [record["observation"]["observation_id"] for record in records]
    pairs = [record["observation"]["pair_id"] for record in records]
    if len(ids) != len(set(ids)) or len(pairs) != len(set(pairs)):
        raise PersonalWorkloadCampaignError(
            "observation ledger contains duplicate IDs or pairs"
        )
    return records


def _summary_from_observations(
    observations: Iterable[MatchedWorkloadObservation],
) -> dict[str, Any]:
    items = tuple(observations)
    resource_ledger = AccountResourceLedger(
        policy=AccountReinvestmentPolicy(
            reinvestment_fraction=0.5,
            rolling_observations=100,
        )
    )
    resource_ledger.extend(
        item.to_account_resource_observation() for item in items
    )
    decision = resource_ledger.decide()
    verified_savings = decision.verified_turn_savings
    governance_spend = decision.governance_turns_spent
    g_over_s = (
        governance_spend / verified_savings
        if verified_savings > 0
        else None
    )
    completed_tasks = {item.task_id for item in items}
    lifecycle_ids = {
        action_id for item in items for action_id in item.lifecycle_action_ids
    }
    lifecycle_kinds = [
        action_kind
        for item in items
        for action_kind in item.lifecycle_action_kinds
    ]
    managed_passes = sum(item.managed.success for item in items)
    baseline_passes = sum(item.baseline.success for item in items)
    observed_dates = [
        datetime.fromisoformat(item.observed_at_utc.replace("Z", "+00:00"))
        for item in items
    ]
    elapsed_days = (
        (max(observed_dates) - min(observed_dates)).total_seconds() / 86_400
        if observed_dates
        else 0.0
    )
    level_7_checks = {
        "unique_task_contracts_completed": len(completed_tasks) == 50,
        "minimum_elapsed_days": elapsed_days >= 14,
        "target_matched_observations": len(items) >= 100,
        "minimum_lifecycle_changes": len(lifecycle_ids) >= 10,
        "promotion_observed": "promote" in lifecycle_kinds,
        "rollback_observed": "rollback" in lifecycle_kinds,
        "actual_invocation_evidence_complete": all(
            item.baseline.actual_invocation_evidence_complete
            and item.managed.actual_invocation_evidence_complete
            for item in items
        )
        and bool(items),
        "matched_baseline_required": all(
            item.baseline is not None and item.managed is not None
            for item in items
        )
        and bool(items),
        "g_over_s_or_turn_equivalent_required": g_over_s is not None,
    }
    level_7_achieved = all(level_7_checks.values())
    unmet_level_7_checks = [
        key for key, passed in level_7_checks.items() if not passed
    ]
    return {
        "schema_version": "merlin-personal-workload-summary-v1",
        "campaign_id": "merlin-personal-workload-50-longitudinal-v1",
        "manifest_sha256": PERSONAL_WORKLOAD_MANIFEST_SHA256,
        "schedule_sha256": PERSONAL_WORKLOAD_SCHEDULE_SHA256,
        "matched_observation_count": len(items),
        "unique_task_count_completed": len(completed_tasks),
        "observed_elapsed_days": elapsed_days,
        "phase_1_pair_count": sum(item.repetition == 1 for item in items),
        "phase_2_pair_count": sum(item.repetition == 2 for item in items),
        "baseline_pass_count": baseline_passes,
        "managed_pass_count": managed_passes,
        "baseline_pass_rate": baseline_passes / len(items) if items else None,
        "managed_pass_rate": managed_passes / len(items) if items else None,
        "verified_turn_savings": verified_savings,
        "governance_turns_spent": governance_spend,
        "g_over_s": g_over_s,
        "g_over_s_status": (
            "computable-from-verified-matched-success"
            if g_over_s is not None
            else "unavailable-no-verified-direct-savings"
        ),
        "authorized_reinvestment_turns": decision.authorized_provider_turns,
        "reinvestment_reason": decision.reason,
        "lifecycle_change_count": len(lifecycle_ids),
        "lifecycle_action_kind_counts": {
            kind: lifecycle_kinds.count(kind)
            for kind in sorted(set(lifecycle_kinds))
        },
        "selection_error_count": sum(
            item.selection_error_count for item in items
        ),
        "invocation_error_count": sum(
            item.invocation_error_count for item in items
        ),
        "regression_count": sum(item.regression_count for item in items),
        "level_7_checks": level_7_checks,
        "level_7_achieved": level_7_achieved,
        "unmet_level_7_checks": unmet_level_7_checks,
        "level_7_status": (
            "field-validated-research-beta"
            if level_7_achieved
            else "not-yet-qualified"
        ),
        "evidence_boundary": {
            "empty_ledger_is_valid": True,
            "task_contract_is_not_task_completion": True,
            "only_hash_chained_matched_observations_are_counted": True,
        },
    }


def run_personal_workload_campaign(output_dir: str | Path) -> dict[str, Any]:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    manifest = personal_workload_manifest_payload()
    schedule = personal_workload_schedule_payload()
    manifest_artifact = {
        **manifest,
        "manifest_sha256": PERSONAL_WORKLOAD_MANIFEST_SHA256,
    }
    schedule_artifact = {
        **schedule,
        "schedule_sha256": PERSONAL_WORKLOAD_SCHEDULE_SHA256,
    }
    summary = _summary_from_observations(())
    for path, payload in (
        (root / "manifest.json", manifest_artifact),
        (root / "schedule.json", schedule_artifact),
        (root / "summary.json", summary),
    ):
        with path.open("x", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
    (root / "observations.jsonl").touch(exist_ok=False)
    return summary


def append_personal_workload_observation(
    output_dir: str | Path,
    observation: MatchedWorkloadObservation,
    *,
    invocation_signer: HarnessInvocationSigner | None = None,
) -> dict[str, Any]:
    root = Path(output_dir).expanduser().resolve(strict=True)
    validate_personal_workload_campaign(root, invocation_signer=invocation_signer)
    _validate_observation_contract(observation, invocation_signer=invocation_signer)
    ledger_path = root / "observations.jsonl"
    records = _read_ledger(ledger_path, invocation_signer=invocation_signer)
    existing_ids = {
        record["observation"]["observation_id"] for record in records
    }
    existing_pairs = {record["observation"]["pair_id"] for record in records}
    if observation.observation_id in existing_ids:
        raise PersonalWorkloadCampaignError("duplicate observation ID")
    if observation.pair_id in existing_pairs:
        raise PersonalWorkloadCampaignError("scheduled pair already recorded")
    previous_hash = (
        records[-1]["record_sha256"] if records else "0" * 64
    )
    body = {
        "sequence": len(records) + 1,
        "previous_record_sha256": previous_hash,
        "observation": observation.to_dict(),
    }
    envelope = {**body, "record_sha256": _sha256_json(body)}
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(_canonical_json(envelope) + "\n")
        handle.flush()
    all_observations = [
        observation_from_dict(
            record["observation"], invocation_signer=invocation_signer
        )
        for record in records
    ] + [observation]
    summary = _summary_from_observations(all_observations)
    summary_path = root / "summary.json"
    temporary = root / "summary.json.tmp"
    temporary.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(summary_path)
    return summary


def validate_personal_workload_campaign(
    output_dir: str | Path,
    *,
    invocation_signer: HarnessInvocationSigner | None = None,
) -> dict[str, Any]:
    root = Path(output_dir).expanduser().resolve(strict=True)
    try:
        manifest = json.loads(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
        schedule = json.loads(
            (root / "schedule.json").read_text(encoding="utf-8")
        )
        summary = json.loads(
            (root / "summary.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PersonalWorkloadCampaignError(
            "campaign artifacts cannot be decoded"
        ) from exc
    manifest_body = {
        key: value
        for key, value in manifest.items()
        if key != "manifest_sha256"
    }
    schedule_body = {
        key: value
        for key, value in schedule.items()
        if key != "schedule_sha256"
    }
    records = _read_ledger(
        root / "observations.jsonl", invocation_signer=invocation_signer
    )
    observations = [
        observation_from_dict(
            record["observation"], invocation_signer=invocation_signer
        )
        for record in records
    ]
    expected_summary = _summary_from_observations(observations)
    checks = {
        "manifest_exact": manifest_body == personal_workload_manifest_payload(),
        "manifest_sha256": (
            manifest.get("manifest_sha256")
            == PERSONAL_WORKLOAD_MANIFEST_SHA256
            and _sha256_json(manifest_body)
            == PERSONAL_WORKLOAD_MANIFEST_SHA256
        ),
        "schedule_exact": schedule_body
        == personal_workload_schedule_payload(),
        "schedule_sha256": (
            schedule.get("schedule_sha256")
            == PERSONAL_WORKLOAD_SCHEDULE_SHA256
            and _sha256_json(schedule_body)
            == PERSONAL_WORKLOAD_SCHEDULE_SHA256
        ),
        "task_count": len(PERSONAL_WORKLOAD_50_TASKS) == 50,
        "family_counts": manifest.get("family_counts")
        == EXPECTED_FAMILY_COUNTS,
        "pair_count": schedule.get("pair_count") == 100,
        "summary_exact": summary == expected_summary,
        "ledger_chain": True,
    }
    if not all(checks.values()):
        raise PersonalWorkloadCampaignError(
            "personal workload campaign validation failed"
        )
    return {
        "valid": True,
        "checks": checks,
        "task_count": 50,
        "pair_count": 100,
        "observation_count": len(observations),
        "manifest_sha256": PERSONAL_WORKLOAD_MANIFEST_SHA256,
        "schedule_sha256": PERSONAL_WORKLOAD_SCHEDULE_SHA256,
    }


__all__ = [
    "EXPECTED_FAMILY_COUNTS",
    "MatchedWorkloadObservation",
    "PERSONAL_WORKLOAD_50_TASKS",
    "PERSONAL_WORKLOAD_MANIFEST_SHA256",
    "PERSONAL_WORKLOAD_SCHEDULE_SHA256",
    "PersonalWorkloadCampaignError",
    "PersonalWorkloadTask",
    "VERIFIER_PROFILES",
    "WorkloadArmEvidence",
    "append_personal_workload_observation",
    "observation_from_dict",
    "personal_workload_manifest_payload",
    "personal_workload_schedule_payload",
    "run_personal_workload_campaign",
    "validate_personal_workload_campaign",
]
