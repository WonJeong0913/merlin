"""Read-only governance view over the on-disk campaign and ledger artifacts.

Single source of truth for the desktop shell (via the JSONL bridge) and the
operator CLI, so both report the same facts about the same files.

Two rules govern everything here:

- artifacts are **revalidated on read** rather than trusted from their stored
  summary, so ledger drift surfaces instead of being reported as healthy;
- an absent artifact reports as absent. Nothing substitutes a zero or a default
  that would read as a positive claim.

The view reports availability. It performs no lifecycle change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .harness_evolution_ledger import (
    HarnessEvolutionLedger,
    HarnessEvolutionLedgerError,
    load_and_validate_harness_evolution_ledger,
)
from . import provider_rollout_evidence
from .personal_workload_campaign import (
    PersonalWorkloadCampaignError,
    personal_workload_manifest_payload,
    personal_workload_schedule_payload,
    validate_personal_workload_campaign,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_DIAGNOSTIC_CHARS = 800

# Governance artifacts are read from disk. Absent artifacts report as absent —
# they are never substituted with defaults that would read as a positive claim.
CAMPAIGN_DIR = "experiments/mvp/results/merlin_personal_workload_50_longitudinal_v1"
EVOLUTION_LEDGER = "experiments/mvp/results/harness_evolution_longitudinal_v1/evolution.jsonl"


def _clip(value: object, limit: int = MAX_DIAGNOSTIC_CHARS) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _campaign_governance() -> dict[str, Any]:
    """Report the longitudinal campaign's own summary, revalidated on read.

    `validated` is the outcome of re-running the campaign validator, not a
    stored flag, so ledger drift surfaces in the UI instead of being trusted.
    """
    root = REPO_ROOT / CAMPAIGN_DIR
    if not (root / "summary.json").is_file():
        return {"artifacts_present": False, "validated": False, "validation_error": None}
    try:
        summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {
            "artifacts_present": True,
            "validated": False,
            "validation_error": "summary cannot be decoded",
        }
    validation_error: str | None = None
    try:
        validate_personal_workload_campaign(root)
    except (PersonalWorkloadCampaignError, OSError) as exc:
        validation_error = _clip(str(exc), MAX_DIAGNOSTIC_CHARS)
    manifest = personal_workload_manifest_payload()
    schedule = personal_workload_schedule_payload()
    return {
        "artifacts_present": True,
        "validated": validation_error is None,
        "validation_error": validation_error,
        "campaign_id": summary.get("campaign_id"),
        "manifest_sha256": summary.get("manifest_sha256"),
        "schedule_sha256": summary.get("schedule_sha256"),
        "task_count": len(manifest.get("tasks", [])),
        "pair_count": len(schedule.get("pairs", [])),
        "matched_observation_count": summary.get("matched_observation_count"),
        "lifecycle_change_count": summary.get("lifecycle_change_count"),
        "lifecycle_action_kind_counts": summary.get("lifecycle_action_kind_counts", {}),
        "g_over_s": summary.get("g_over_s"),
        "g_over_s_status": summary.get("g_over_s_status"),
        "level_7_achieved": bool(summary.get("level_7_achieved")),
        "level_7_status": summary.get("level_7_status"),
        "level_7_checks": summary.get("level_7_checks", {}),
        "unmet_level_7_checks": summary.get("unmet_level_7_checks", []),
    }


def _evolution_governance() -> dict[str, Any]:
    """Summarize the harness-evolution ledger if one exists on disk."""
    path = REPO_ROOT / EVOLUTION_LEDGER
    if not path.is_file():
        return {
            "ledger_present": False,
            "ledger_path": EVOLUTION_LEDGER,
            "reason": "no harness-evolution ledger has been generated yet",
        }
    try:
        records = load_and_validate_harness_evolution_ledger(path)
        ledger = HarnessEvolutionLedger.load(path)
        summary = ledger.summarize()
    except (HarnessEvolutionLedgerError, OSError) as exc:
        return {
            "ledger_present": True,
            "ledger_path": EVOLUTION_LEDGER,
            "validated": False,
            "validation_error": _clip(str(exc), MAX_DIAGNOSTIC_CHARS),
        }
    return {
        "ledger_present": True,
        "ledger_path": EVOLUTION_LEDGER,
        "validated": True,
        "validation_error": None,
        "record_count": len(records),
        "observation_count": summary.observation_count,
        "candidate_count": summary.candidate_count,
        "promotion_count": summary.promotion_count,
        "rollback_count": summary.rollback_count,
        "regression_count": summary.regression_count,
        "governance_to_savings_ratio": summary.governance_to_savings_ratio,
        "ratio_reason": summary.ratio_reason,
    }


def _invocation_evidence_governance(campaign: Mapping[str, Any]) -> dict[str, Any]:
    """State the P0 gate exactly as the harness enforces it.

    `skill_body_invocation` binds task → skill ID → SKILL.md body SHA-256 →
    request hash → trace hash → verifier result under an HMAC signature. That
    covers the harness side. Provider-native proof that the body was actually
    loaded and invoked is what is still missing, and until it exists matched
    observations cannot be promoted. The refusal is the designed behavior.
    """
    checks = campaign.get("level_7_checks") or {}
    complete = bool(checks.get("actual_invocation_evidence_complete"))
    return {
        "harness_signed_events_available": True,
        "provider_native_evidence_complete": complete,
        "blocking_reason": (
            None if complete else "provider_native_skill_invocation_evidence_incomplete"
        ),
        "consequence": (
            None
            if complete
            else "matched observations cannot be promoted; prompt exposure is not invocation evidence"
        ),
        "corroboration_tiers": _corroboration_tiers(),
    }


def _corroboration_tiers() -> list[dict[str, Any]]:
    """Rank the evidence tiers by who wrote the artifact.

    Availability here means the *mechanism* exists, not that any observation has
    been corroborated. Whether a given observation clears the gate stays with
    `provider_native_evidence_complete`, which this must never flip.
    """
    sessions_root = provider_rollout_evidence.DEFAULT_SESSIONS_ROOT
    return [
        {
            "tier": "harness_signed",
            "available": True,
            "self_attested": True,
            "establishes": (
                "the harness bound task, skill ID, body hash, request hash, "
                "trace hash and verifier result under its own HMAC signature"
            ),
            "limit": "a harness that lied would emit an equally valid signature",
        },
        {
            "tier": "provider_cli_rollout",
            "available": sessions_root.is_dir(),
            "self_attested": False,
            "establishes": (
                "the exact skill body was present in the request the Codex CLI "
                "recorded against the same thread, in a file Merlin does not write"
            ),
            "limit": (
                "presence in a request is not use, and a compromised CLI or an "
                "edited rollout would defeat it"
            ),
            "reason": (
                None
                if sessions_root.is_dir()
                else "no Codex sessions root on this machine"
            ),
        },
        {
            "tier": "provider_server_attested",
            "available": False,
            "self_attested": False,
            "establishes": (
                "the provider's own service confirms which skill body it received "
                "and acted on"
            ),
            "limit": "no artifact on the current Codex CLI surface carries this",
            "reason": "not emitted by any observed Codex CLI output",
        },
    ]


def _lifecycle_governance(
    campaign: Mapping[str, Any], invocation: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """List each lifecycle operation with an honest availability reason.

    Repair, merge and retirement are evaluator-backed batch campaigns rather
    than one-click mutations, so the desktop surface reports them instead of
    offering a button that would fabricate a decision.
    """
    # This surface has a campaign-wide summary, not a concrete candidate's
    # frozen verifier bundle, replay window, or rollout corroboration record.
    # It may therefore report prerequisite state but must never imply that the
    # next arbitrary candidate is promotable.
    promotable, promotion_reason = _promotion_availability(campaign, invocation)
    kinds = campaign.get("lifecycle_action_kind_counts") or {}
    promotions = int(kinds.get("promote", 0))
    return [
        {
            "kind": "promote",
            "available": promotable,
            "observed_count": promotions,
            "reason": promotion_reason,
        },
        {
            "kind": "rollback",
            "available": promotions > 0,
            "observed_count": int(kinds.get("rollback", 0)),
            "reason": (
                "an exact rollback target exists"
                if promotions > 0
                else "no promotion has been recorded, so there is nothing to roll back"
            ),
        },
        {
            "kind": "repair",
            "available": False,
            "observed_count": int(kinds.get("repair", 0)),
            "reason": "evaluator-backed batch campaign; run it from experiments, not from the shell",
        },
        {
            "kind": "merge",
            "available": False,
            "observed_count": int(kinds.get("merge", 0)),
            "reason": "requires a behavioral-equivalence diagnosis over a case split",
        },
        {
            "kind": "hide",
            "available": False,
            "observed_count": int(kinds.get("hide", 0)),
            "reason": "requires a validated selection observation window",
        },
        {
            "kind": "retire",
            "available": False,
            "observed_count": int(kinds.get("retire", 0)),
            "reason": "requires a non-regression retirement window over the active library",
        },
    ]


def _promotion_availability(
    campaign: Mapping[str, Any], invocation: Mapping[str, Any]
) -> tuple[bool, str]:
    """Return a conservative availability verdict for the read-only shell.

    Campaign-level evidence can establish prerequisites, but promotion is an
    individual candidate decision.  The shell does not hold that candidate's
    deterministic verifier inputs, so even a healthy campaign ends at the
    explicit candidate-specific gate rather than presenting a false positive
    action affordance.
    """

    if not campaign.get("artifacts_present"):
        return False, "campaign artifacts are absent"
    if not campaign.get("validated"):
        return False, "campaign artifacts failed deterministic revalidation"
    observations = campaign.get("matched_observation_count")
    if not isinstance(observations, int) or isinstance(observations, bool) or observations <= 0:
        return False, "no matched observation is available for promotion review"
    if not invocation.get("provider_native_evidence_complete"):
        return False, "blocked by provider_native_skill_invocation_evidence_incomplete"
    checks = campaign.get("level_7_checks")
    if not isinstance(checks, Mapping):
        return False, "campaign deterministic gate summary is absent"
    for check in (
        "actual_invocation_evidence_complete",
        "matched_baseline_required",
        "g_over_s_or_turn_equivalent_required",
    ):
        if checks.get(check) is not True:
            return False, f"campaign deterministic gate is unmet: {check}"
    return False, "candidate-specific gate required"


def harness_governance_summary() -> dict[str, Any]:
    """The read-only governance view behind the `harness.governance` command."""
    campaign = _campaign_governance()
    invocation = _invocation_evidence_governance(campaign)
    return {
        "campaign": campaign,
        "evolution": _evolution_governance(),
        "invocation_evidence": invocation,
        "lifecycle_operations": _lifecycle_governance(campaign, invocation),
        "evidence_boundary": {
            "prompt_exposure_is_not_invocation_evidence": True,
            "failed_or_unverifiable_arms_create_no_savings": True,
            "legacy_evidence_is_not_relabeled_as_merlin_evidence": True,
        },
    }

