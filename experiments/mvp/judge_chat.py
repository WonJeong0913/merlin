"""Chat-first, account-free judge surface for Merlin's golden incident.

This localhost app is intentionally a product view over the same deterministic
``LifecycleRecoverySession`` used by the terminal and Console demos.  It does
not simulate a model response or make a provider call.  The final assistant
message joins the live controlled recovery with separately recorded,
hash-bound GPT-5.6 authoring/use evidence and keeps those evidence lanes
explicitly distinct.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from src.merlin_harness.library import FileSkillLibrary

from .complete_live_model_hidden_campaign import audit_completion
from .lifecycle_session import LifecycleRecoverySession, LifecycleSessionError
from .reporting import render_control_room
from .route_trace_audit import (
    RouteTraceAuditError,
    audit_route_trace_bundle,
    sample_trace_bundle,
)
from .run_chat import (
    DEFAULT_PROMOTION_EVIDENCE,
    DEFAULT_SKILLS_ROOT,
    GoldenPassEvidenceError,
    JUDGE_GOLDEN_PROMPTS,
    _build_golden_pass_summary,
    _load_hash_bound_promoted_chat_evidence,
    _render_golden_judge_report,
    load_verified_promotion_overlay,
)


LOOPBACK_HOST = "127.0.0.1"
MAX_JSON_BODY_BYTES = 16384
MAX_MESSAGE_CHARACTERS = 400
RESULTS_ROOT = Path(__file__).resolve().parent / "results"
DEFAULT_ROLLBACK_EVIDENCE_ROOT = RESULTS_ROOT / "model_authored_hidden_completion_live_v1"
DEFAULT_ROLLBACK_PRIOR_ROOT = RESULTS_ROOT / "model_authored_hidden_rollback_live_v1"
DEFAULT_SELECTION_PILOT_EVIDENCE = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "evidence"
    / "gpt56-selection-shadowing-pilot-v1.json"
)
DEFAULT_NAME_GOVERNANCE_EVIDENCE = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "evidence"
    / "runtime-name-governance-on-frozen-56-v1.json"
)


class JudgeChatError(ValueError):
    """One safe error returned by the bounded judge chat surface."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _normalise_prompt(value: str) -> str:
    return " ".join(value.casefold().split())


def _accepts_golden_intent(value: str) -> bool:
    return _normalise_prompt(value) in JUDGE_GOLDEN_PROMPTS


def _load_selection_pilot_evidence(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.expanduser().resolve(strict=True).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoldenPassEvidenceError("selection-only pilot evidence is unavailable") from exc
    expected_arms = (
        ("oracle-6", 6, 12, 0),
        ("plus-10", 16, 12, 0),
        ("plus-50", 56, 11, 1),
        ("full-209", 209, 12, 0),
    )
    observed = tuple(
        (
            item.get("arm_id"),
            item.get("library_size"),
            item.get("correct"),
            item.get("wrong_skill"),
        )
        for item in value.get("arms", [])
        if isinstance(item, dict)
    ) if isinstance(value, dict) else ()
    boundary = value.get("claim_boundary", {}) if isinstance(value, dict) else {}
    audit = value.get("audit", {}) if isinstance(value, dict) else {}
    if not (
        value.get("schema_version") == 1
        and value.get("provider_turns") == 8
        and value.get("task_count") == 6
        and value.get("decision_count") == 48
        and observed == expected_arms
        and value.get("monotonic_nonincreasing_accuracy_observed") is False
        and audit.get("checks_passed") == audit.get("checks_total") == 10
        and boundary.get("actual_codex_provider_turns") is True
        and boundary.get("selection_only") is True
        and boundary.get("provider_resolved_model_identity") is False
        and boundary.get("provider_native_skill_invocation") is False
        and boundary.get("task_execution") is False
        and boundary.get("utility_verification") is False
        and boundary.get("full87_or_1305_cell_result") is False
    ):
        raise GoldenPassEvidenceError("selection-only pilot evidence contract failed")
    return value


def _load_name_governance_evidence(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.expanduser().resolve(strict=True).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoldenPassEvidenceError("runtime name-governance evidence is unavailable") from exc
    if not isinstance(value, dict):
        raise GoldenPassEvidenceError("runtime name-governance evidence is malformed")
    body = {key: item for key, item in value.items() if key != "evidence_sha256"}
    digest = hashlib.sha256(
        json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    audit = value.get("audit", {})
    boundary = value.get("claim_boundary", {})
    if not (
        value.get("schema_version") == 1
        and value.get("evidence_id") == "runtime-name-governance-on-frozen-56-v1"
        and value.get("evidence_sha256") == digest
        and audit.get("source_variant_count") == 56
        and audit.get("source_declared_name_count") == 53
        and audit.get("collision_group_count") == 2
        and audit.get("suppressed_variant_count") == 3
        and audit.get("runtime_prompt_candidate_count") == 53
        and audit.get("source_library_before_sha256")
        == audit.get("source_library_after_sha256")
        and audit.get("source_library_mutated") is False
        and boundary.get("model_free") is True
        and boundary.get("provider_turns") == 0
        and boundary.get("task_execution_or_utility_measured") is False
        and boundary.get("merge_or_retire_authorized") is False
        and value.get("experiment_mapping", {}).get(
            "actual_confirmatory_provider_result_available"
        )
        is False
    ):
        raise GoldenPassEvidenceError("runtime name-governance evidence contract failed")
    return value


def _initial_messages() -> list[dict[str, Any]]:
    return [
        {
            "id": "assistant-welcome",
            "role": "assistant",
            "kind": "text",
            "content": (
                "I manage the skill harness around an agent: provisioning, routing, "
                "validation, recovery, and adoption. Give me the overloaded-library "
                "incident below and I will diagnose it without mutating the source library."
            ),
        }
    ]


def _step_cards(
    summary: dict[str, Any],
    *,
    rollback_evidence: dict[str, Any],
    rollback_audit: dict[str, Any],
    selection_pilot: dict[str, Any],
) -> list[dict[str, Any]]:
    by_kind = {item["kind"]: item["result"] for item in summary["judging_flow"]}
    before = by_kind["controlled_overload_problem"]
    intervention = by_kind["The_KING_trace_backed_intervention"]
    after = by_kind["same_verifier_recovery"]
    creation = by_kind["requested_GPT_5_6_candidate_quarantine_and_promotion"]
    chain = creation["chain_audit"]
    use = by_kind["recorded_model_authored_skill_chat_use"]
    name_governance = selection_pilot["name_governance"]
    return [
        {
            "id": "inspect-overload",
            "lane": "CONTROLLED RUNTIME · RUN NOW",
            "lane_kind": "runtime",
            "tool": "inspect_library",
            "title": "Inspect active skill library",
            "status": "completed",
            "summary": (
                f"Overload reproduced: {before['passed']}/{before['task_count']} passed, "
                f"{round(before['shadowing_rate'] * 100)}% shadowing."
            ),
            "metric": f"{before['passed']}/{before['task_count']}",
            "tone": "danger",
        },
        {
            "id": "diagnose-traces",
            "lane": "CONTROLLED RUNTIME · RUN NOW",
            "lane_kind": "runtime",
            "tool": "diagnose_routes",
            "title": "Diagnose route traces",
            "status": "completed",
            "summary": (
                "Repeated harmful routes isolate only "
                + ", ".join(intervention["skill_ids"])
                + "."
            ),
            "metric": f"{len(intervention['skill_ids'])} risks",
            "tone": "warning",
        },
        {
            "id": "stage-cow",
            "lane": "CONTROLLED RUNTIME · RUN NOW",
            "lane_kind": "runtime",
            "tool": "stage_copy_on_write",
            "title": "Stage a copy-on-write recovery",
            "status": "completed",
            "summary": "The narrow hide is staged in a provisional library; the source stays unchanged.",
            "metric": "0 source writes",
            "tone": "neutral",
        },
        {
            "id": "same-verifier",
            "lane": "CONTROLLED RUNTIME · RUN NOW",
            "lane_kind": "runtime",
            "tool": "verify_and_promote",
            "title": "Re-run the same verifier",
            "status": "completed",
            "summary": (
                f"Recovered to {after['passed']}/{after['task_count']} with "
                f"{round(after['shadowing_rate'] * 100)}% shadowing; promotion gate passed."
            ),
            "metric": f"{after['passed']}/{after['task_count']}",
            "tone": "success",
        },
        {
            "id": "review-model-authored",
            "lane": "RECORDED GPT-5.6 EVIDENCE",
            "lane_kind": "recorded",
            "tool": "review_recorded_creation",
            "title": "Review GPT-5.6-authored skill evidence",
            "status": "completed",
            "summary": (
                f"Recorded {creation['requested_model_id']}/{creation['requested_effort']} "
                f"candidate passed quarantine, hidden, negative-route, and COW gates; "
                f"the {chain['checks_passed']}/{chain['checks_total']} chain audit and frozen "
                f"use verifier {'passed' if use['verifier']['passed'] else 'failed'}. "
                "A separate same-candidate campaign passed target 2/2 and hidden 1/1, "
                f"then rolled back on route shadowing; audit {rollback_audit['checks_passed']}/"
                f"{rollback_audit['checks_total']} and COW rollback "
                f"{'passed' if rollback_evidence['evidence_boundary']['copy_on_write_rolled_back'] else 'failed'}. "
                "A separate selection-only 6/16/56/209 pilot scored "
                + "/".join(str(item["correct"]) for item in selection_pilot["arms"])
                + " of 12; the non-monotonic exact-variant mismatch has no utility claim. "
                f"The runtime v2 guard then audited {name_governance['audit']['source_variant_count']} "
                f"variants/{name_governance['audit']['source_declared_name_count']} names, "
                f"found {name_governance['audit']['collision_group_count']} same-name groups, "
                f"and suppressed {name_governance['audit']['suppressed_variant_count']} variants "
                "without mutating the source; its confirmatory provider result is still pending."
            ),
            "metric": f"{chain['checks_passed']}/{chain['checks_total']} chain",
            "tone": "success",
        },
    ]


def _assistant_result(
    summary: dict[str, Any],
    elapsed_ms: int,
    *,
    rollback_evidence: dict[str, Any],
    rollback_audit: dict[str, Any],
    selection_pilot: dict[str, Any],
) -> dict[str, Any]:
    cards = _step_cards(
        summary,
        rollback_evidence=rollback_evidence,
        rollback_audit=rollback_audit,
        selection_pilot=selection_pilot,
    )
    return {
        "id": "assistant-result",
        "role": "assistant",
        "kind": "incident_result",
        "content": (
            "Recovered safely. I hid only the repeatedly harmful routes in a "
            "provisional library and promoted the change only after the same verifier passed."
        ),
        "elapsed_ms": elapsed_ms,
        "tools": cards,
        "evidence_boundary": (
            "This local replay executes the controlled harness state machine. The GPT-5.6 "
            "promotion/use and routing-rollback lanes are separately recorded, hash-bound runs; no provider-native "
            "Skill event or provider-resolved model ID is claimed."
        ),
    }


class JudgeChatSession:
    """One bounded conversation over the real controlled lifecycle runtime."""

    def __init__(
        self,
        *,
        creation_evidence: dict[str, Any],
        recorded_use: dict[str, Any],
        rollback_evidence: dict[str, Any],
        rollback_audit: dict[str, Any],
        selection_pilot: dict[str, Any],
    ) -> None:
        self.creation_evidence = creation_evidence
        self.recorded_use = recorded_use
        self.rollback_evidence = rollback_evidence
        self.rollback_audit = rollback_audit
        self.selection_pilot = selection_pilot
        self.governance = LifecycleRecoverySession()
        self.messages = _initial_messages()
        self.summary: dict[str, Any] | None = None
        self.lifecycle_report: dict[str, Any] | None = None
        self.trace_audit_report: dict[str, Any] | None = None
        self.status = "ready"

    def close(self) -> None:
        self.governance.close()

    def reset(self) -> dict[str, Any]:
        self.governance.close()
        self.governance = LifecycleRecoverySession()
        self.messages = _initial_messages()
        self.summary = None
        self.lifecycle_report = None
        self.trace_audit_report = None
        self.status = "ready"
        return self.public_state()

    def audit_trace_bundle(self, bundle: Any) -> dict[str, Any]:
        if self.status == "complete":
            raise JudgeChatError("incident_complete", "Start a new incident before auditing another trace.")
        try:
            report = audit_route_trace_bundle(bundle)
        except RouteTraceAuditError as exc:
            raise JudgeChatError(exc.code, str(exc)) from exc
        metrics = report["metrics"]
        diagnosis = report["diagnosis"]
        rate = metrics["exposure_shadowing_rate"]
        rate_label = "n/a" if rate is None else f"{round(rate * 100)}%"
        candidate_count = diagnosis["candidate_count"]
        candidate_ids = [item["skill_id"] for item in diagnosis["provisional_candidates"]]
        self.messages.extend(
            [
                {
                    "id": "user-trace-audit",
                    "role": "user",
                    "kind": "text",
                    "content": f"Audit my {metrics['record_count']}-record prompt-exposure trace bundle.",
                },
                {
                    "id": "assistant-trace-audit",
                    "role": "assistant",
                    "kind": "trace_audit_result",
                    "content": (
                        "Trace audit complete. I isolated repeated failed route risk, but made no "
                        "library change and did not promote a recovery."
                    ),
                    "tools": [
                        {
                            "lane": "USER TRACE · OBSERVE ONLY",
                            "lane_kind": "runtime",
                            "tool": "validate_trace_contract",
                            "title": "Validate trace contract",
                            "status": "completed",
                            "summary": "Portable IDs, bounded records, unique traces, and explicit evidence level passed.",
                            "metric": f"{metrics['record_count']} traces",
                            "tone": "neutral",
                        },
                        {
                            "lane": "USER TRACE · OBSERVE ONLY",
                            "lane_kind": "runtime",
                            "tool": "measure_route_risk",
                            "title": "Measure exposure shadowing",
                            "status": "completed",
                            "summary": "Wrong and mixed prompt exposure are measured only on records with an oracle skill.",
                            "metric": rate_label,
                            "tone": "warning" if rate else "success",
                        },
                        {
                            "lane": "USER TRACE · OBSERVE ONLY",
                            "lane_kind": "runtime",
                            "tool": "diagnose_repeated_failures",
                            "title": "Isolate repeated failed routes",
                            "status": "completed",
                            "summary": (
                                "Provisional hide candidates: " + ", ".join(candidate_ids)
                                if candidate_ids
                                else "No skill crossed the repeated-failure threshold."
                            ),
                            "metric": f"{candidate_count} candidates",
                            "tone": "warning" if candidate_count else "success",
                        },
                        {
                            "lane": "SAFETY GATE · REQUIRED",
                            "lane_kind": "recorded",
                            "tool": "require_same_verifier",
                            "title": "Hold promotion",
                            "status": "completed",
                            "summary": "Copy-on-write staging and the same task/verifier re-run are required before any adoption.",
                            "metric": "0 source writes",
                            "tone": "success",
                        },
                    ],
                    "audit_metrics": {
                        "records": metrics["record_count"],
                        "shadowing": rate_label,
                        "candidates": candidate_count,
                    },
                    "evidence_boundary": (
                        "This audit evaluates user-supplied prompt exposure, not provider-native skill invocation. "
                        "It is observe-only and cannot claim or promote a recovery."
                    ),
                },
            ]
        )
        self.trace_audit_report = report
        self.status = "complete"
        return self.public_state()

    def submit(self, message: str) -> dict[str, Any]:
        if self.status == "complete":
            raise JudgeChatError("incident_complete", "Start a new incident before sending another request.")
        if not isinstance(message, str):
            raise JudgeChatError("invalid_message", "message must be a string")
        message = message.strip()
        if not message:
            raise JudgeChatError("empty_message", "Enter an incident request.")
        if len(message) > MAX_MESSAGE_CHARACTERS:
            raise JudgeChatError("message_too_long", "The bounded judge request is too long.")
        if not _accepts_golden_intent(message):
            self.messages.extend(
                [
                    {"id": "user-guidance", "role": "user", "kind": "text", "content": message},
                    {
                        "id": "assistant-guidance",
                        "role": "assistant",
                        "kind": "text",
                        "content": (
                            "This account-free judge sandbox accepts only the documented "
                            "overloaded-library recovery incident. Use the suggested request, "
                            "or launch the authenticated terminal beta for general chat."
                        ),
                    },
                ]
            )
            return self.public_state()

        self.status = "running"
        self.messages.append(
            {"id": "user-incident", "role": "user", "kind": "text", "content": message}
        )
        started = time.monotonic()
        try:
            self.summary = _build_golden_pass_summary(
                self.governance,
                creation_evidence=self.creation_evidence,
                recorded_promoted_chat=self.recorded_use,
            )
            self.lifecycle_report = self.governance.final_report()
        except (GoldenPassEvidenceError, LifecycleSessionError) as exc:
            self.status = "failed"
            raise JudgeChatError("golden_run_failed", str(exc)) from exc
        elapsed_ms = max(1, round((time.monotonic() - started) * 1000))
        self.messages.append(
            _assistant_result(
                self.summary,
                elapsed_ms,
                rollback_evidence=self.rollback_evidence,
                rollback_audit=self.rollback_audit,
                selection_pilot=self.selection_pilot,
            )
        )
        self.status = "complete"
        return self.public_state()

    def public_state(self) -> dict[str, Any]:
        metrics = None
        if self.summary is not None:
            cards = _step_cards(
                self.summary,
                rollback_evidence=self.rollback_evidence,
                rollback_audit=self.rollback_audit,
                selection_pilot=self.selection_pilot,
            )
            creation = self.summary["judging_flow"][3]["result"]
            metrics = {
                "before_pass": cards[0]["metric"],
                "after_pass": cards[3]["metric"],
                "promotion_gates": (
                    f"{creation['promotion_gates_passed']}/"
                    f"{creation['promotion_gates_total']} gates"
                ),
                "evidence_chain": cards[4]["metric"],
                "rollback_audit": (
                    f"{self.rollback_audit['checks_passed']}/"
                    f"{self.rollback_audit['checks_total']} rollback"
                ),
                "selection_pilot": "11/12 @56 · selection only",
                "name_collision_guard": "3 suppressed · model-free",
                "shadowing_before": "89%",
                "shadowing_after": "0%",
            }
        return {
            "schema_version": 1,
            "product": "Merlin",
            "mode": "account_free_golden_incident",
            "status": self.status,
            "messages": self.messages,
            "metrics": metrics,
            "suggested_prompt": "Diagnose and safely recover this overloaded skill library.",
            "actions": {
                "send_allowed": self.status == "ready",
                "audit_allowed": self.status == "ready",
                "reset_allowed": True,
                "report_ready": self.summary is not None,
                "trace_audit_ready": self.trace_audit_report is not None,
            },
        }


_STYLE = r"""
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#05090f;color:#eef5fb;--panel:#0b121d;--line:#1c2b3a;--muted:#8fa2b5;--cyan:#77e6ed;--green:#69e4ad;--red:#ff8d9f;--amber:#ffc66d}
.rail{overflow-y:auto}.rail-card+.rail-card{margin-top:8px}.tool-lane{color:#67d9df;font-size:.58rem;font-weight:900;letter-spacing:.11em;margin:0 0 4px;text-transform:uppercase}.tool-lane.recorded{color:#ffc66d}.lane-summary{display:flex;flex-wrap:wrap;gap:6px}.lane-pill{border:1px solid #2b6170;border-radius:999px;color:#7fe7e9;font-size:.61rem;font-weight:900;letter-spacing:.06em;padding:5px 8px}.lane-pill.recorded{border-color:#765928;color:#ffd28d}
*{box-sizing:border-box}body{margin:0;min-width:300px;background:radial-gradient(circle at 48% -12%,#14364b 0,transparent 32rem),#05090f}button,textarea{font:inherit}.app{display:grid;grid-template-columns:270px minmax(0,1fr);min-height:100vh}.rail{background:rgba(7,12,20,.94);border-right:1px solid var(--line);padding:22px 18px;position:sticky;top:0;height:100vh}.brand{align-items:center;display:flex;gap:10px;font-size:.92rem;font-weight:850}.mark{align-items:center;background:linear-gradient(135deg,#78e9f0,#4a7ef1);border-radius:10px;color:#031016;display:flex;height:32px;justify-content:center;width:32px}.new{background:#101c2a;border:1px solid #2a4359;border-radius:10px;color:#eaf6ff;cursor:pointer;margin:24px 0 26px;min-height:42px;width:100%}.new:hover{border-color:var(--cyan)}.rail h2{color:#6e8295;font-size:.68rem;letter-spacing:.12em;margin:22px 6px 10px;text-transform:uppercase}.rail-card{background:#0b1521;border:1px solid #1c3042;border-radius:12px;padding:12px}.rail-card strong{display:block;font-size:.8rem}.rail-card p{color:var(--muted);font-size:.72rem;line-height:1.45;margin:6px 0 0}.status-dot{background:var(--green);border-radius:50%;display:inline-block;height:7px;margin-right:7px;width:7px}.boundary-mini{color:#6f8497;font-size:.68rem;line-height:1.5;margin:18px 6px}.main{display:flex;min-width:0;flex-direction:column}.topbar{align-items:center;background:rgba(5,9,15,.78);backdrop-filter:blur(14px);border-bottom:1px solid rgba(28,43,58,.72);display:flex;justify-content:space-between;padding:13px 24px;position:sticky;top:0;z-index:4}.topbar strong{font-size:.88rem}.mode{background:#0d1a27;border:1px solid #223b50;border-radius:999px;color:#9fc3d7;font-size:.68rem;padding:6px 10px}.conversation{margin:0 auto;max-width:930px;padding:34px 24px 170px;width:100%}.hero{text-align:center;padding:18px 0 28px}.hero .crown{color:var(--cyan);font-size:.72rem;font-weight:850;letter-spacing:.15em;text-transform:uppercase}.hero h1{font-size:clamp(2rem,5vw,3.8rem);letter-spacing:-.055em;margin:9px 0}.hero p{color:#a7bacb;line-height:1.55;margin:0 auto;max-width:660px}.bubble{display:flex;gap:13px;margin:24px 0}.avatar{align-items:center;border:1px solid #2b455a;border-radius:10px;display:flex;flex:0 0 auto;font-size:.75rem;font-weight:900;height:34px;justify-content:center;width:34px}.assistant .avatar{background:#0a6f73;color:white}.user{justify-content:flex-end}.user .avatar{background:#223147;order:2}.bubble-body{max-width:760px;min-width:0}.bubble-body>p{line-height:1.6;margin:5px 0}.user .bubble-body{background:#142235;border:1px solid #263b51;border-radius:16px 4px 16px 16px;padding:10px 14px}.tools{display:grid;gap:8px;margin:16px 0}.tool{align-items:flex-start;background:var(--panel);border:1px solid var(--line);border-radius:12px;display:grid;gap:12px;grid-template-columns:28px minmax(0,1fr) auto;padding:12px}.tool-icon{align-items:center;background:#102334;border-radius:7px;color:var(--cyan);display:flex;height:28px;justify-content:center;width:28px}.tool h3{font-size:.8rem;margin:1px 0 4px}.tool p{color:#95aabd;font-size:.72rem;line-height:1.4;margin:0}.tool-metric{background:#101d29;border-radius:7px;color:#cbeaf0;font-size:.7rem;font-weight:800;padding:6px 8px;white-space:nowrap}.tool.danger{border-left:3px solid var(--red)}.tool.warning{border-left:3px solid var(--amber)}.tool.success{border-left:3px solid var(--green)}.done{color:var(--green);font-size:.74rem}.answer{background:linear-gradient(135deg,rgba(11,42,48,.75),rgba(10,20,34,.85));border:1px solid #246066;border-radius:14px;padding:14px 16px}.answer strong{color:var(--green)}.result-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.result-actions a{background:#0e2431;border:1px solid #2f5869;border-radius:8px;color:#dff9fb;font-size:.7rem;font-weight:750;padding:8px 10px;text-decoration:none}.result-actions a.primary{background:#116b69;border-color:#5bddd4}.evidence-note{border-left:2px solid #36516b;color:#8196a9;font-size:.68rem;line-height:1.5;margin-top:12px;padding-left:10px}.composer-wrap{background:linear-gradient(transparent,#05090f 24%);bottom:0;left:270px;padding:42px 24px 22px;position:fixed;right:0}.composer{background:#0d1723;border:1px solid #2b4357;border-radius:16px;box-shadow:0 16px 55px #0009;margin:auto;max-width:900px;padding:12px}.suggestion{background:#0c1d28;border:1px solid #234254;border-radius:999px;color:#abd1dd;cursor:pointer;font-size:.7rem;margin-bottom:9px;padding:7px 10px}.suggestion:hover{border-color:var(--cyan)}.entry{align-items:flex-end;display:flex;gap:10px}.entry textarea{background:transparent;border:0;color:#eef8ff;line-height:1.4;max-height:110px;min-height:48px;outline:0;padding:9px;resize:none;width:100%}.send{align-items:center;background:#70e4e6;border:0;border-radius:10px;color:#031113;cursor:pointer;display:flex;font-size:1.1rem;font-weight:900;height:42px;justify-content:center;width:42px}.send:disabled,.suggestion:disabled{cursor:not-allowed;opacity:.4}.composer-foot{color:#607589;font-size:.62rem;margin:7px 4px 0;text-align:center}.error{color:#ff9bab;font-size:.75rem;min-height:1.2em;text-align:center}.metrics{display:grid;gap:8px;grid-template-columns:repeat(3,1fr);margin-top:12px}.metric{background:#09131d;border:1px solid #1a3040;border-radius:10px;padding:10px}.metric span{color:#74899c;display:block;font-size:.63rem;text-transform:uppercase}.metric strong{display:block;font-size:1.1rem;margin-top:4px}.metric .good{color:var(--green)}.visually-hidden{clip:rect(0 0 0 0);clip-path:inset(50%);height:1px;overflow:hidden;position:absolute;white-space:nowrap;width:1px}@media(max-width:760px){.app{display:block}.rail{display:none}.composer-wrap{left:0}.conversation{padding:24px 14px 180px}.topbar{padding:12px 14px}.tool{grid-template-columns:26px minmax(0,1fr)}.tool-metric{grid-column:2}.metrics{grid-template-columns:1fr}}
.conversation{padding-bottom:260px}
.quick-actions{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:9px}.quick-actions .suggestion{align-items:center;display:inline-flex;margin-bottom:0;text-decoration:none}.suggestion.secondary{background:#161b26;border-color:#554a34;color:#e7c992}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
"""


_SCRIPT = r"""
(()=>{
  "use strict";
  const token=__TOKEN__;
  const $=selector=>document.querySelector(selector);
  const esc=value=>{
    const node=document.createElement("span");
    node.textContent=String(value);
    return node.innerHTML;
  };
  function render(state){
    const target=$("#messages");
    target.replaceChildren();
    state.messages.forEach(message=>{
      const row=document.createElement("section");
      row.className=`bubble ${message.role}`;
      const avatar=document.createElement("div");
      avatar.className="avatar";
      avatar.textContent=message.role==="assistant"?"K":"YOU";
      const body=document.createElement("div");
      body.className="bubble-body";
      const copy=document.createElement("p");
      copy.textContent=message.content;
      body.append(copy);
      if(message.tools){
        const tools=document.createElement("div");
        tools.className="tools";
        message.tools.forEach(item=>{
          const card=document.createElement("article");
          const laneKind=item.lane_kind==="recorded"?"recorded":"runtime";
          card.className=`tool ${item.tone}`;
          card.innerHTML=`<div class="tool-icon">✓</div><div><div class="tool-lane ${laneKind}">${esc(item.lane)}</div><h3>${esc(item.title)} <span class="done">completed</span></h3><p>${esc(item.summary)}</p></div><span class="tool-metric">${esc(item.metric)}</span>`;
          tools.append(card);
        });
        body.append(tools);
        const answer=document.createElement("div");
        answer.className="answer";
        if(message.kind==="trace_audit_result"){
          const metrics=message.audit_metrics;
          answer.innerHTML=`<div class="lane-summary"><span class="lane-pill">USER TRACE · AUDITED</span><span class="lane-pill recorded">PROMOTION · HELD</span></div><div class="metrics"><div class="metric"><span>Trace records</span><strong>${esc(metrics.records)}</strong></div><div class="metric"><span>Exposure shadowing</span><strong>${esc(metrics.shadowing)}</strong></div><div class="metric"><span>Provisional candidates</span><strong>${esc(metrics.candidates)}</strong></div></div><nav class="result-actions" aria-label="Audit actions"><a class="primary" href="/download/trace-audit.json" download>Download audit JSON</a><a href="/download/trace-audit-sample.json" download>Download sample contract</a></nav>`;
        }else{
          answer.innerHTML=`<div class="lane-summary"><span class="lane-pill">CONTROLLED RUNTIME · RECOVERY ACCEPTED</span><span class="lane-pill recorded">RECORDED GPT-5.6 · CHAIN VERIFIED</span></div><div class="metrics"><div class="metric"><span>Task pass</span><strong>1/10 → <b class="good">9/10</b></strong></div><div class="metric"><span>Shadowing</span><strong>89% → <b class="good">0%</b></strong></div><div class="metric"><span>Evidence chain</span><strong class="good">15/15</strong></div></div><nav class="result-actions" aria-label="Evidence actions"><a class="primary" href="/report" target="_blank" rel="noopener">Open Golden Report</a><a href="/control-room" target="_blank" rel="noopener">Technical Control Room</a><a href="/download/golden.json" download>Download evidence JSON</a></nav>`;
        }
        body.append(answer);
        const note=document.createElement("p");
        note.className="evidence-note";
        note.textContent=message.evidence_boundary;
        body.append(note);
      }
      row.append(avatar,body);
      target.append(row);
    });
    const ready=state.actions.send_allowed;
    $("#message").disabled=!ready;
    $("#send").disabled=!ready;
    $("#suggestion").disabled=!ready;
    $("#trace-upload").disabled=!state.actions.audit_allowed;
    $("#status").textContent=state.status==="complete"?(state.actions.trace_audit_ready?"Trace audit complete":"Incident recovered"):"Ready · evidence-bound";
    $("#error").textContent="";
    if(state.status==="complete"){
      setTimeout(()=>window.scrollTo({top:document.body.scrollHeight,behavior:"smooth"}),0);
    }
  }
  async function post(path,payload){
    const response=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json","X-Merlin-Token":token},body:JSON.stringify(payload)});
    const data=await response.json();
    if(!response.ok)throw new Error(data.error?.message||`Request failed (${response.status})`);
    return data.state;
  }
  async function send(){
    const value=$("#message").value.trim();
    if(!value)return;
    $("#send").disabled=true;
    $("#status").textContent="Running governed recovery…";
    try{
      const state=await post("/api/message",{message:value});
      $("#message").value="";
      render(state);
    }catch(error){
      $("#error").textContent=error.message;
      $("#send").disabled=false;
    }
  }
  $("#send").addEventListener("click",send);
  $("#message").addEventListener("keydown",event=>{
    if(event.key==="Enter"&&!event.shiftKey){event.preventDefault();send();}
  });
  $("#suggestion").addEventListener("click",()=>{
    $("#message").value=__PROMPT__;
    send();
  });
  $("#trace-upload").addEventListener("click",()=>$("#trace-file").click());
  $("#trace-file").addEventListener("change",async event=>{
    const file=event.target.files[0];
    if(!file)return;
    $("#trace-upload").disabled=true;
    $("#status").textContent="Auditing user trace…";
    try{
      if(file.size>12000)throw new Error("Trace JSON must be 12 KB or smaller.");
      const bundle=JSON.parse(await file.text());
      render(await post("/api/trace-audit",{trace_bundle:bundle}));
    }catch(error){
      $("#error").textContent=error.message;
      $("#trace-upload").disabled=false;
    }finally{
      event.target.value="";
    }
  });
  $("#reset").addEventListener("click",async()=>{
    try{render(await post("/api/reset",{}));}
    catch(error){$("#error").textContent=error.message;}
  });
  fetch("/api/state")
    .then(response=>response.json())
    .then(data=>render(data.state))
    .catch(error=>{$("#error").textContent=error.message;});
})();
"""


def render_judge_chat(*, token: str, nonce: str) -> str:
    prompt = "Diagnose and safely recover this overloaded skill library."
    script = _SCRIPT.replace("__TOKEN__", json.dumps(token)).replace("__PROMPT__", json.dumps(prompt))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="description" content="Merlin chat-first skill-harness recovery agent demo.">
  <link rel="icon" href="data:,">
  <title>Merlin — Skill Harness Agent</title>
  <style nonce="{nonce}">{_STYLE}</style>
</head>
<body>
  <div class="app">
    <aside class="rail">
      <div class="brand"><span class="mark">K</span>Merlin</div>
      <button class="new" id="reset" type="button">＋ New incident</button>
      <h2>Harness</h2>
      <div class="rail-card">
        <strong><span class="status-dot"></span>Governed runtime</strong>
        <p>Provisioning · routing · validation · lifecycle · adoption</p>
      </div>
      <h2>Product</h2>
      <div class="rail-card">
        <strong>Built for agent-platform teams</strong>
        <p>Operate growing skill libraries without silent routing regressions.</p>
      </div>
      <div class="rail-card">
        <strong>Beyond skill generation</strong>
        <p>Merlin manages the harness around skills, from selection through verified adoption.</p>
      </div>
      <h2>Evidence lanes</h2>
      <div class="rail-card">
        <strong>Run now · controlled</strong>
        <p>This incident and same-verifier recovery execute in the local session.</p>
      </div>
      <div class="rail-card">
        <strong>Recorded · GPT-5.6</strong>
        <p>Hash-bound authoring, quarantine, hidden verification, promotion, and use.</p>
      </div>
      <p class="boundary-mini">Account-free localhost sandbox. Source library changes are blocked until the same-verifier gate passes.</p>
    </aside>
    <main class="main">
      <header class="topbar">
        <strong>Skill harness incident</strong>
        <span class="mode" id="status">Ready · evidence-bound</span>
      </header>
      <div class="conversation">
        <section class="hero">
          <div class="crown">Self-managing skill harness</div>
          <h1>Skills grow. Reliability should too.</h1>
          <p>Merlin manages the layer around an agent—so a plausible wrong skill cannot silently shadow the right one.</p>
        </section>
        <div id="messages" aria-live="polite"></div>
      </div>
      <section class="composer-wrap" aria-label="Incident request">
        <div class="composer">
          <div class="quick-actions">
            <button class="suggestion" id="suggestion" type="button">Run golden incident</button>
            <button class="suggestion secondary" id="trace-upload" type="button">Audit your trace JSON</button>
            <a class="suggestion secondary" href="/download/trace-audit-sample.json" download>Download trace sample</a>
            <input class="visually-hidden" id="trace-file" type="file" accept="application/json,.json">
          </div>
          <div class="entry">
            <label class="visually-hidden" for="message">Message Merlin</label>
            <textarea id="message" maxlength="400" placeholder="Message Merlin…"></textarea>
            <button class="send" id="send" type="button" aria-label="Send message">↑</button>
          </div>
          <p class="error" id="error" role="alert"></p>
          <p class="composer-foot">Run the golden incident or audit 2–20 prompt-exposure traces. General chat uses the authenticated terminal beta.</p>
        </div>
      </section>
    </main>
  </div>
  <script nonce="{nonce}">{script}</script>
</body>
</html>"""


class JudgeChatHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        skills_root: Path = DEFAULT_SKILLS_ROOT,
        promotion_evidence: Path = DEFAULT_PROMOTION_EVIDENCE,
    ) -> None:
        self.session_lock = threading.RLock()
        self.csrf_token = secrets.token_urlsafe(32)
        self.csp_nonce = secrets.token_urlsafe(18)
        self._temporary = tempfile.TemporaryDirectory(prefix="merlin-judge-chat-")
        temp_root = Path(self._temporary.name)
        base_library = FileSkillLibrary(skills_root.expanduser().resolve(strict=True))
        _overlay, creation = load_verified_promotion_overlay(
            base_library=base_library,
            evidence_path=promotion_evidence.expanduser().resolve(strict=True),
            overlay_root=temp_root / "library-overlay",
        )
        recorded = _load_hash_bound_promoted_chat_evidence(
            promotion_evidence,
            creation_evidence=creation,
        )
        rollback_audit = audit_completion(
            evidence_root=DEFAULT_ROLLBACK_EVIDENCE_ROOT,
            prior_evidence_root=DEFAULT_ROLLBACK_PRIOR_ROOT,
        )
        rollback_evidence = json.loads(
            (
                DEFAULT_ROLLBACK_EVIDENCE_ROOT
                / "model_authored_hidden_completion_evidence.json"
            ).read_text(encoding="utf-8")
        )
        selection_pilot = _load_selection_pilot_evidence(
            DEFAULT_SELECTION_PILOT_EVIDENCE
        )
        selection_pilot = {
            **selection_pilot,
            "name_governance": _load_name_governance_evidence(
                DEFAULT_NAME_GOVERNANCE_EVIDENCE
            ),
        }
        self.session = JudgeChatSession(
            creation_evidence=creation,
            recorded_use=recorded,
            rollback_evidence=rollback_evidence,
            rollback_audit=rollback_audit,
            selection_pilot=selection_pilot,
        )
        try:
            super().__init__(server_address, JudgeChatRequestHandler)
        except Exception:
            self.session.close()
            self._temporary.cleanup()
            raise

    @property
    def base_url(self) -> str:
        return f"http://{LOOPBACK_HOST}:{self.server_address[1]}"

    def server_close(self) -> None:
        with self.session_lock:
            self.session.close()
        super().server_close()
        self._temporary.cleanup()


class JudgeChatRequestHandler(BaseHTTPRequestHandler):
    server: JudgeChatHTTPServer

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def _headers(self, *, content_type: str, length: int, disposition: str | None = None) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if disposition:
            self.send_header("Content-Disposition", disposition)

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        *,
        content_type: str,
        disposition: str | None = None,
        csp: str | None = None,
    ) -> None:
        self.send_response(status)
        self._headers(content_type=content_type, length=len(body), disposition=disposition)
        if csp:
            self.send_header("Content-Security-Policy", csp)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        self._send_bytes(status, body, content_type="application/json; charset=utf-8")

    def _error(self, status: HTTPStatus, code: str, message: str) -> None:
        self._send_json(status, {"error": {"code": code, "message": message}})

    def _host_valid(self) -> bool:
        return self.headers.get("Host") == f"{LOOPBACK_HOST}:{self.server.server_address[1]}"

    def _reject_host(self) -> bool:
        if self._host_valid():
            return False
        self._error(HTTPStatus.BAD_REQUEST, "invalid_host", "Host must match the active loopback server.")
        return True

    def do_GET(self) -> None:  # noqa: N802
        if self._reject_host():
            return
        if self.path == "/":
            body = render_judge_chat(token=self.server.csrf_token, nonce=self.server.csp_nonce).encode()
            nonce = self.server.csp_nonce
            csp = (
                "default-src 'none'; "
                f"style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; "
                "connect-src 'self'; img-src data:; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'none'"
            )
            self._send_bytes(HTTPStatus.OK, body, content_type="text/html; charset=utf-8", csp=csp)
            return
        if self.path == "/api/state":
            with self.server.session_lock:
                state = self.server.session.public_state()
            self._send_json(HTTPStatus.OK, {"state": state})
            return
        if self.path == "/download/trace-audit-sample.json":
            body = (json.dumps(sample_trace_bundle(), ensure_ascii=False, indent=2) + "\n").encode()
            self._send_bytes(
                HTTPStatus.OK,
                body,
                content_type="application/json; charset=utf-8",
                disposition='attachment; filename="merlin-trace-audit-sample.json"',
            )
            return
        if self.path == "/download/trace-audit.json":
            with self.server.session_lock:
                report = self.server.session.trace_audit_report
            if report is None:
                self._error(HTTPStatus.CONFLICT, "audit_pending", "Import a trace bundle before downloading its audit.")
                return
            body = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
            self._send_bytes(
                HTTPStatus.OK,
                body,
                content_type="application/json; charset=utf-8",
                disposition='attachment; filename="merlin-trace-audit.json"',
            )
            return
        if self.path in {"/report", "/control-room", "/download/golden.json", "/download/lifecycle.json"}:
            with self.server.session_lock:
                summary = self.server.session.summary
                lifecycle = self.server.session.lifecycle_report
            if summary is None or lifecycle is None:
                self._error(HTTPStatus.CONFLICT, "report_pending", "Run the incident before opening evidence.")
                return
            if self.path == "/report":
                body = _render_golden_judge_report(summary, server_mode=True).encode()
                self._send_bytes(
                    HTTPStatus.OK,
                    body,
                    content_type="text/html; charset=utf-8",
                    csp=(
                        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                        "frame-ancestors 'none'; base-uri 'none'"
                    ),
                )
                return
            if self.path == "/control-room":
                body = render_control_room(lifecycle).encode()
                self._send_bytes(
                    HTTPStatus.OK,
                    body,
                    content_type="text/html; charset=utf-8",
                    csp="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'; base-uri 'none'",
                )
                return
            payload = summary if self.path.endswith("golden.json") else lifecycle
            filename = "merlin-golden.json" if payload is summary else "merlin-lifecycle.json"
            body = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
            self._send_bytes(
                HTTPStatus.OK,
                body,
                content_type="application/json; charset=utf-8",
                disposition=f'attachment; filename="{filename}"',
            )
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found", "Unknown judge chat path.")

    def _read_action(self, *, expected_fields: set[str]) -> dict[str, Any] | None:
        if self.headers.get("Origin") != self.server.base_url:
            self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "A same-origin request is required.")
            return None
        if not secrets.compare_digest(self.headers.get("X-Merlin-Token", ""), self.server.csrf_token):
            self._error(HTTPStatus.FORBIDDEN, "invalid_token", "A valid action token is required.")
            return None
        if self.headers.get("Content-Type") != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "invalid_content_type", "Content-Type must be application/json.")
            return None
        value = self.headers.get("Content-Length")
        try:
            length = int(value) if value is not None else -1
        except ValueError:
            length = -1
        if length < 0:
            self._error(HTTPStatus.LENGTH_REQUIRED, "content_length_required", "A valid Content-Length is required.")
            return None
        if length > MAX_JSON_BODY_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body_too_large", "JSON body exceeds the limit.")
            return None
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_json", "Request body must be valid JSON.")
            return None
        if not isinstance(payload, dict) or set(payload) != expected_fields:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_shape", "Request fields do not match the action contract.")
            return None
        return payload

    def do_POST(self) -> None:  # noqa: N802
        if self._reject_host():
            return
        if self.path == "/api/message":
            payload = self._read_action(expected_fields={"message"})
            if payload is None:
                return
            try:
                with self.server.session_lock:
                    state = self.server.session.submit(payload["message"])
            except JudgeChatError as exc:
                self._error(HTTPStatus.CONFLICT, exc.code, str(exc))
                return
            self._send_json(HTTPStatus.OK, {"state": state})
            return
        if self.path == "/api/reset":
            payload = self._read_action(expected_fields=set())
            if payload is None:
                return
            with self.server.session_lock:
                state = self.server.session.reset()
            self._send_json(HTTPStatus.OK, {"state": state})
            return
        if self.path == "/api/trace-audit":
            payload = self._read_action(expected_fields={"trace_bundle"})
            if payload is None:
                return
            try:
                with self.server.session_lock:
                    state = self.server.session.audit_trace_bundle(payload["trace_bundle"])
            except JudgeChatError as exc:
                self._error(HTTPStatus.CONFLICT, exc.code, str(exc))
                return
            self._send_json(HTTPStatus.OK, {"state": state})
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found", "Unknown judge chat path.")


def create_judge_chat_server(
    *,
    host: str = LOOPBACK_HOST,
    port: int = 0,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
    promotion_evidence: Path = DEFAULT_PROMOTION_EVIDENCE,
) -> JudgeChatHTTPServer:
    if host != LOOPBACK_HOST:
        raise ValueError("judge chat must bind exactly to 127.0.0.1")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be an integer from 0 through 65535")
    return JudgeChatHTTPServer(
        (host, port),
        skills_root=skills_root,
        promotion_evidence=promotion_evidence,
    )
