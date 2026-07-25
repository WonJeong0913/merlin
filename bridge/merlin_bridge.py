#!/usr/bin/env python3
"""JSONL bridge for a local chat-first coding-agent desktop client.

The desktop UI owns presentation and the PTY-based account connection screen.
This process owns the existing Codex-backed chat session, skill provisioning,
bounded autonomy, and safe response envelopes.  It never reads or returns an
API key, access token, or Codex credential file.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_ROOT = Path(__file__).resolve().parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from experiments.mvp.run_chat import (  # noqa: E402
    DEFAULT_SKILLS_ROOT,
    detect_codex_runtime,
)
from src.merlin_harness.chat_session import (  # noqa: E402
    ChatResponse,
    ChatSessionError,
    # The core class still carries its pre-migration name. Aliasing keeps the
    # product surface Merlin-named without editing the frozen harness core.
    TheKingChatSession as MerlinChatSession,
)
from src.merlin_harness.codex_chat import (  # noqa: E402
    ALLOWED_EFFORTS,
    CodexChatBackend,
    CodexChatBackendError,
)
from src.merlin_harness.consent_governor import (  # noqa: E402
    AutonomyAdoption,
    ConsentGatedHarnessGovernor,
    ConsentGovernorError,
)
from src.merlin_harness.governance_view import (  # noqa: E402
    harness_governance_summary,
)
from src.merlin_harness.library import FileSkillLibrary  # noqa: E402
from src.merlin_harness.semantic_router import CodexCliSemanticRouter  # noqa: E402
from codex_model_catalog import CodexModelCatalogError, query_codex_models  # noqa: E402
from src.merlin_harness.harnessx_runtime import HarnessXHook, PERMITTED_MUTATIONS  # noqa: E402


SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 64 * 1024
MAX_DIAGNOSTIC_CHARS = 800
DEFAULT_EFFORT = "high"
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SUPPORTED_COMMANDS = frozenset(
    {
        "bridge.hello",
        "account.status",
        "account.connect_spec",
        "account.models",
        "session.start",
        "session.restart",
        "session.update_settings",
        "session.status",
        "session.new_thread",
        "session.resume_thread",
        "chat.send",
        "approval.resolve",
        "feedback.record",
        "harness.governance",
    }
)

def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def recorded_evidence_summaries() -> list[dict[str, Any]]:
    """Return small, hash-bound summaries of retained evidence.

    These records are never mixed with the live session graph. Missing or
    malformed files are omitted rather than upgraded into an evidence claim.
    Raw provider text and task answers are never returned.
    """

    records: list[dict[str, Any]] = []

    def load(relative: str) -> tuple[Path, dict[str, Any]] | None:
        path = REPO_ROOT / relative
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return (path, value) if isinstance(value, dict) else None

    creation = load(
        "experiments/mvp/results/model_authored_skill_live_v1/"
        "model_authored_skill_evidence.json"
    )
    if creation is not None:
        path, data = creation
        gates = data.get("gates")
        boundary = data.get("evidence_boundary")
        if (
            data.get("adopted") is True
            and isinstance(gates, list)
            and gates
            and all(isinstance(item, dict) and item.get("passed") is True for item in gates)
            and isinstance(boundary, dict)
        ):
            records.append({
                "id": "recorded-model-authored-generation-v1",
                "title": "Extract TODO Items",
                "kind": "model-authored generation",
                "status": "promoted",
                "role": "Requested-model candidate passed quarantine, target, hidden, negative-route, and copy-on-write adoption gates.",
                "lifecycle": ["generated", "quarantined", "validated", "promoted"],
                "gates_passed": len(gates),
                "gates_total": len(gates),
                "requested_model": boundary.get("requested_model_id"),
                "model_evidence_level": boundary.get("model_evidence_level"),
                "actual_provider_run": boundary.get("actual_codex_provider_run") is True,
                "provider_native_invocation": boundary.get("provider_native_skill_invocation") is True,
                "source_path": str(path.relative_to(REPO_ROOT)),
                "source_sha256": _sha256_file(path),
            })

    repair = load(
        "experiments/mvp/results/model_authored_skill_repair_live_v1/"
        "model_authored_skill_repair_evidence.json"
    )
    if repair is not None:
        path, data = repair
        result = data.get("repair_result")
        boundary = data.get("evidence_boundary")
        gates = result.get("gates") if isinstance(result, dict) else None
        if (
            data.get("adopted") is True
            and isinstance(gates, list)
            and gates
            and all(isinstance(item, dict) and item.get("passed") is True for item in gates)
            and isinstance(boundary, dict)
        ):
            records.append({
                "id": "recorded-model-authored-repair-v1",
                "title": "Extract TODO Items v1 → v2",
                "kind": "model-authored repair",
                "status": "promoted",
                "role": "Target-only feedback produced a quarantined repair that passed target, hidden, library-regression, and copy-on-write gates.",
                "lifecycle": ["failure diagnosed", "repaired", "revalidated", "promoted"],
                "gates_passed": len(gates),
                "gates_total": len(gates),
                "requested_model": boundary.get("requested_model_id"),
                "model_evidence_level": boundary.get("model_evidence_level"),
                "actual_provider_run": boundary.get("actual_codex_provider_run") is True,
                "provider_native_invocation": boundary.get("provider_native_skill_invocation") is True,
                "source_path": str(path.relative_to(REPO_ROOT)),
                "source_sha256": _sha256_file(path),
            })

    rollback = load(
        "experiments/mvp/results/model_authored_hidden_rollback_live_v1/"
        "model_authored_hidden_rollback_evidence.json"
    )
    if rollback is not None:
        path, data = rollback
        gates = data.get("gates")
        boundary = data.get("evidence_boundary")
        if (
            data.get("adopted") is False
            and isinstance(gates, list)
            and isinstance(boundary, dict)
        ):
            passed = sum(
                1 for item in gates
                if isinstance(item, dict) and item.get("passed") is True
            )
            records.append({
                "id": "recorded-routing-shadowing-rollback-v1",
                "title": "Markdown Headings Candidate",
                "kind": "post-execution rollback",
                "status": "rolled back",
                "role": "A candidate that passed execution checks was rejected after its trigger polluted governed routing.",
                "lifecycle": ["generated", "quarantined", "executed", "routing shadow detected", "rolled back"],
                "gates_passed": passed,
                "gates_total": len(gates),
                "requested_model": boundary.get("requested_model_id"),
                "model_evidence_level": boundary.get("model_evidence_level"),
                "actual_provider_run": boundary.get("actual_codex_provider_run") is True,
                "provider_native_invocation": boundary.get("provider_native_skill_invocation") is True,
                "source_path": str(path.relative_to(REPO_ROOT)),
                "source_sha256": _sha256_file(path),
            })

    merge = load("experiments/mvp/results/skill_merge_v1/skill_merge.json")
    if merge is not None:
        path, data = merge
        summary = data.get("summary")
        boundary = data.get("claim_boundary")
        if data.get("status") == "pass" and isinstance(summary, dict) and isinstance(boundary, dict):
            records.append({
                "id": "recorded-controlled-merge-v1",
                "title": "Duplicate Skill Merge",
                "kind": "controlled lifecycle fixture",
                "status": "merged",
                "role": "An exact-equivalent duplicate became a retired alias tombstone while the canonical artifact stayed byte-identical.",
                "lifecycle": ["duplicate detected", "equivalence verified", "merged", "redundant alias retired"],
                "gates_passed": summary.get("gates_passed", 0),
                "gates_total": summary.get("gates_total", 0),
                "requested_model": None,
                "model_evidence_level": "controlled_deterministic_fixture",
                "actual_provider_run": False,
                "provider_native_invocation": False,
                "source_path": str(path.relative_to(REPO_ROOT)),
                "source_sha256": _sha256_file(path),
            })

    recovery = load("experiments/mvp/results/lifecycle_recovery/lifecycle_recovery.json")
    if recovery is not None:
        path, data = recovery
        conditions = data.get("conditions")
        promotion = data.get("promotion")
        if isinstance(conditions, dict) and isinstance(promotion, dict):
            overloaded = conditions.get("Overloaded library")
            recovered = conditions.get("Lifecycle recovered")
            checks = promotion.get("checks")
            if (
                isinstance(overloaded, dict)
                and isinstance(recovered, dict)
                and overloaded.get("passed") == 1
                and recovered.get("passed") == 9
                and overloaded.get("pi_m") == 0.8888888888888888
                and recovered.get("pi_m") == 0.0
                and promotion.get("accepted") is True
                and isinstance(checks, list)
                and checks
                and all(isinstance(item, dict) and item.get("passed") is True for item in checks)
            ):
                records.append({
                    "id": "recorded-controlled-shadowing-recovery-v1",
                    "title": "Shadowing Recovery",
                    "kind": "controlled lifecycle recovery",
                    "status": "promoted",
                    "role": "A controlled 10-task fixture recovered from 1/10 to 9/10 while measured skill shadowing fell from 89% to 0% after trace-backed hide and the same-verifier gate.",
                    "lifecycle": ["overload observed", "shadowing traced", "two distractors hidden", "same verifiers rerun", "change promoted"],
                    "gates_passed": len(checks),
                    "gates_total": len(checks),
                    "requested_model": None,
                    "model_evidence_level": "controlled_deterministic_fixture",
                    "actual_provider_run": False,
                    "provider_native_invocation": False,
                    "source_path": str(path.relative_to(REPO_ROOT)),
                    "source_sha256": _sha256_file(path),
                })

    provider_campaign = load("docs/evidence/gpt56-chat-lifecycle-campaign.json")
    if provider_campaign is not None:
        path, data = provider_campaign
        runtime = data.get("runtime_contract")
        boundary = data.get("evidence_boundary")
        baseline = data.get("baseline")
        provisional = data.get("provisional")
        promotion = data.get("promotion")
        checks = promotion.get("checks") if isinstance(promotion, dict) else None
        if (
            data.get("schema_version") == 1
            and isinstance(runtime, dict)
            and runtime.get("requested_model_id") == "gpt-5.6-terra"
            and runtime.get("model_evidence_level") == "requested_cli_contract_only"
            and runtime.get("provider_reported_model_ids") == []
            and isinstance(boundary, dict)
            and boundary.get("actual_invocation_evidence_complete") is False
            and isinstance(baseline, dict)
            and baseline.get("passed") == 4
            and baseline.get("task_count") == 4
            and baseline.get("exposure_shadowing_rate") == 1.0
            and isinstance(provisional, dict)
            and provisional.get("passed") == 4
            and provisional.get("task_count") == 4
            and provisional.get("exposure_shadowing_rate") == 0.0
            and isinstance(promotion, dict)
            and promotion.get("accepted") is True
            and isinstance(checks, dict)
            and checks
            and all(value is True for value in checks.values())
        ):
            records.append({
                "id": "recorded-requested-model-route-recovery-v1",
                "title": "Requested-Model Route Recovery",
                "kind": "requested-model prompt-exposure campaign",
                "status": "promoted · invocation unobserved",
                "role": "Eight requested-model turns retained 4/4 verifier passes before and after while wrong prompt exposure moved from 4/4 to 0/4. The provider emitted no resolved model identity or native skill-invocation event.",
                "lifecycle": ["four frozen tasks run", "wrong exposures traced", "two route-local hides staged", "same verifiers rerun", "exposure recovery promoted"],
                "gates_passed": sum(1 for value in checks.values() if value is True),
                "gates_total": len(checks),
                "requested_model": runtime.get("requested_model_id"),
                "model_evidence_level": runtime.get("model_evidence_level"),
                "actual_provider_run": True,
                "provider_native_invocation": False,
                "source_path": str(path.relative_to(REPO_ROOT)),
                "source_sha256": _sha256_file(path),
            })

    selection_pilot = load("docs/evidence/gpt56-selection-shadowing-pilot-v1.json")
    if selection_pilot is not None:
        path, data = selection_pilot
        arms = data.get("arms")
        audit = data.get("audit")
        boundary = data.get("claim_boundary")
        arm_sizes = [item.get("library_size") for item in arms] if isinstance(arms, list) else []
        correct_total = sum(item.get("correct", 0) for item in arms if isinstance(item, dict)) if isinstance(arms, list) else 0
        full_arm = next((item for item in arms if isinstance(item, dict) and item.get("arm_id") == "full-209"), None) if isinstance(arms, list) else None
        if (
            data.get("schema_version") == 1
            and data.get("experiment_type") == "selection_only_library_scale_pilot"
            and data.get("requested_model_id") == "gpt-5.6-terra"
            and data.get("provider_turns") == 8
            and data.get("decision_count") == 48
            and arm_sizes == [6, 16, 56, 209]
            and correct_total == 47
            and isinstance(full_arm, dict)
            and full_arm.get("correct") == 12
            and isinstance(audit, dict)
            and audit.get("checks_passed") == audit.get("checks_total") == 10
            and isinstance(boundary, dict)
            and boundary.get("selection_only") is True
            and boundary.get("provider_resolved_model_identity") is False
            and boundary.get("provider_native_skill_invocation") is False
            and boundary.get("task_execution") is False
            and boundary.get("statistical_significance_claim") is False
        ):
            records.append({
                "id": "recorded-selection-scale-pilot-v1",
                "title": "Selection Scale Pilot",
                "kind": "requested-model selection-only pilot",
                "status": "47/48 · selection only",
                "role": "Across 6, 16, 56, and 209-skill catalogs, 47/48 exact frozen references were selected, including 12/12 at 209. The sole mismatch was a same-name variant; no task utility or monotonic degradation claim is made.",
                "lifecycle": ["nested catalogs frozen", "eight provider turns run", "48 selections audited", "same-name mismatch isolated", "runtime name governance derived"],
                "gates_passed": audit.get("checks_passed"),
                "gates_total": audit.get("checks_total"),
                "requested_model": data.get("requested_model_id"),
                "model_evidence_level": "requested_cli_contract_only; selection_only",
                "actual_provider_run": True,
                "provider_native_invocation": False,
                "source_path": str(path.relative_to(REPO_ROOT)),
                "source_sha256": _sha256_file(path),
            })

    harnessx = load(
        "experiments/mvp/results/harnessx_typed_runtime_v1/"
        "harnessx_typed_runtime.json"
    )
    if harnessx is not None:
        path, data = harnessx
        low = data.get("low_risk_reversible_change")
        high = data.get("high_risk_change")
        if (
            data.get("hook_coverage_count") == 8
            and data.get("processor_manifest_count") == 8
            and isinstance(low, dict)
            and isinstance(high, dict)
            and low.get("accepted") is True
            and low.get("requires_approval") is False
            and high.get("accepted") is False
            and high.get("requires_approval") is True
        ):
            low_checks = low.get("checks") if isinstance(low.get("checks"), list) else []
            records.append({
                "id": "recorded-harnessx-typed-runtime-v1",
                "title": "HarnessX Typed Runtime",
                "kind": "deterministic local implementation",
                "status": "verified",
                "role": "Eight typed hooks constrain permitted mutations; a reversible low-risk candidate promoted automatically while a high-risk candidate stopped for human approval.",
                "lifecycle": ["parent frozen", "candidate derived", "typed hooks validated", "low-risk promoted", "high-risk approval required"],
                "gates_passed": sum(1 for item in low_checks if isinstance(item, dict) and item.get("passed") is True),
                "gates_total": len(low_checks),
                "requested_model": None,
                "model_evidence_level": data.get("evidence_class"),
                "actual_provider_run": False,
                "provider_native_invocation": False,
                "source_path": str(path.relative_to(REPO_ROOT)),
                "source_sha256": _sha256_file(path),
            })

    desktop = load(
        "evidence/desktop-partial75-v3/bundle/semantic-summary-v3.json"
    )
    if desktop is not None:
        path, data = desktop
        receipt_loaded = load(
            "evidence/desktop-partial75-v3/"
            "partial-prefix75-safe-20260722-v3-r2.receipt.json"
        )
        archive_path = (
            REPO_ROOT
            / "evidence/desktop-partial75-v3/"
            / "partial-prefix75-safe-20260722-v3-r2.tar.gz"
        )
        receipt = receipt_loaded[1] if receipt_loaded is not None else None
        metrics = data.get("trace_metrics")
        by_arm = data.get("by_arm")
        boundary = data.get("claim_boundary")
        summary_ref = receipt.get("semantic_summary") if isinstance(receipt, dict) else None
        archive_ref = receipt.get("archive") if isinstance(receipt, dict) else None
        integrity_checks = [
            data.get("schema_version") == 3,
            data.get("normalized_trace_count") == 75,
            isinstance(by_arm, dict)
            and set(by_arm) == {"curated", "plus-10", "plus-50", "plus-100", "full-209"}
            and sum(item.get("n", 0) for item in by_arm.values() if isinstance(item, dict)) == 75,
            isinstance(metrics, dict)
            and metrics.get("verifier_passed") == 0
            and metrics.get("selected_skill_nonempty") == 10
            and metrics.get("provider_native_invocation") == 0,
            isinstance(boundary, dict)
            and boundary.get("full_435_claim") is False
            and boundary.get("generalization_claim") is False,
            isinstance(summary_ref, dict)
            and summary_ref.get("sha256") == _sha256_file(path),
            isinstance(archive_ref, dict)
            and archive_path.is_file()
            and archive_ref.get("sha256") == _sha256_file(archive_path),
        ]
        if all(integrity_checks):
            records.append({
                "id": "recorded-desktop-partial75-v3",
                "title": "DESKTOP 75-Cell Exact Prefix",
                "kind": "corrected partial provider evaluation",
                "status": "sealed · no verifier success",
                "role": "Seventy-five provider cells across 15 tasks and five library arms were hash-bound and semantically rechecked. The bundle observes 0/75 verifier passes, 10/75 nonempty selections, and 0/75 native invocations; it supports evaluation plumbing, not a generalization or performance claim.",
                "lifecycle": ["435 cells scheduled", "75-cell exact prefix sealed", "source results bound to traces", "semantic aggregate recomputed", "incorrect v2 claims retracted", "v3 fail-closed verifier passed"],
                "gates_passed": len(integrity_checks),
                "gates_total": len(integrity_checks),
                "requested_model": None,
                "model_evidence_level": "partial_exact_prefix_provider_run; provider_resolved_identity_unavailable",
                "actual_provider_run": True,
                "provider_native_invocation": False,
                "source_path": str(path.relative_to(REPO_ROOT)),
                "source_sha256": _sha256_file(path),
            })
    return records


def declared_harnessx_hook_contracts() -> list[dict[str, Any]]:
    """Return source-declared HarnessX hook permissions, never live execution claims."""

    return [
        {
            "hook": hook.value,
            "permitted_mutations": sorted(PERMITTED_MUTATIONS[hook]),
            "declared_runtime_contract": True,
        }
        for hook in HarnessXHook
    ]


class BridgeError(RuntimeError):
    """Safe error intended for a local app client."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _clip(value: object, limit: int = MAX_DIAGNOSTIC_CHARS) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _payload(request: Mapping[str, Any]) -> Mapping[str, Any]:
    value = request.get("payload", {})
    if not isinstance(value, dict):
        raise BridgeError("payload must be a JSON object")
    return value


def _account_method(output: str) -> str | None:
    lowered = output.lower()
    if "chatgpt" in lowered:
        return "chatgpt"
    if "api key" in lowered or "api-key" in lowered:
        return "api_key"
    if "access token" in lowered:
        return "access_token"
    return "unknown" if "logged in" in lowered else None


def account_status(
    requested_executable: str | None = None,
    *,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Return normalized login state without returning raw auth output."""

    try:
        executable, version = detect_codex_runtime(requested_executable)
    except ValueError:
        return {
            "state": "cli_missing",
            "connected": False,
            "executable": None,
            "cli_version": None,
            "auth_method": None,
        }
    try:
        completed = runner(
            [str(executable), "login", "status"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "state": "check_failed",
            "connected": False,
            "executable": str(executable),
            "cli_version": version,
            "auth_method": None,
        }
    combined = f"{completed.stdout}\n{completed.stderr}"
    connected = completed.returncode == 0 and "logged in" in combined.lower()
    return {
        "state": "connected" if connected else "logged_out",
        "connected": connected,
        "executable": str(executable),
        "cli_version": version,
        "auth_method": _account_method(combined) if connected else None,
    }


@dataclass(slots=True)
class RuntimeState:
    session: MerlinChatSession | None = None
    governor: ConsentGatedHarnessGovernor | None = None
    workspace: Path | None = None
    model: str | None = None
    effort: str = DEFAULT_EFFORT
    routing_mode: str = "semantic"
    autonomy_mode: str = "managed"
    trace_root: Path | None = None


class MerlinBridge:
    def __init__(self) -> None:
        self.state = RuntimeState()

    def _require_session(self) -> tuple[MerlinChatSession, ConsentGatedHarnessGovernor]:
        if self.state.session is None or self.state.governor is None:
            raise BridgeError("start a session before using this command")
        return self.state.session, self.state.governor

    def dispatch(self, request: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        command = request.get("command")
        if not isinstance(command, str) or command not in SUPPORTED_COMMANDS:
            raise BridgeError("unsupported command")
        payload = _payload(request)
        if command == "bridge.hello":
            return "bridge.hello", {
                "role": "chat_first_coding_agent",
                "management_surface": "background_events_and_approval_cards",
                "commands": sorted(SUPPORTED_COMMANDS),
            }
        if command == "harness.governance":
            # Disk-backed and session-independent: the operator can inspect
            # governance state before connecting an account.
            return "harness.governance", harness_governance_summary()
        if command == "account.status":
            requested = payload.get("executable")
            if requested is not None and not isinstance(requested, str):
                raise BridgeError("executable must be a string")
            return "account.status", account_status(requested)
        if command == "account.connect_spec":
            requested = payload.get("executable")
            if requested is not None and not isinstance(requested, str):
                raise BridgeError("executable must be a string")
            try:
                executable, version = detect_codex_runtime(requested)
            except ValueError as exc:
                raise BridgeError("Codex CLI is not available") from exc
            return "account.connect_spec", {
                "transport": "pty",
                "executable": str(executable),
                "arguments": ["login", "--device-auth"],
                "cli_version": version,
                "output_policy": "display_transiently_do_not_persist",
                "completion_check": "call account.status after process exit",
            }
        if command == "account.models":
            requested = payload.get("executable")
            if requested is not None and not isinstance(requested, str):
                raise BridgeError("executable must be a string")
            status = account_status(requested)
            if not status["connected"] or not status["executable"]:
                return "account.models", {"available": False, "models": []}
            try:
                catalog = query_codex_models(status["executable"])
            except CodexModelCatalogError:
                return "account.models", {"available": False, "models": []}
            return "account.models", {
                "available": True,
                "models": [
                    {
                        "id": item["model"],
                        "display_name": item["display_name"],
                        "description": item["description"],
                        "is_default": item["is_default"],
                        "default_effort": item["default_effort"],
                        "supported_efforts": item["supported_efforts"],
                    }
                    for item in catalog["models"]
                ],
            }
        if command == "session.start":
            return "session.started", self._start_session(payload)
        if command == "session.restart":
            return "session.restarted", self._restart_session(payload)
        if command == "session.update_settings":
            return "session.settings_updated", self._update_session_settings(payload)
        if command == "session.status":
            session, governor = self._require_session()
            return "session.status", {
                **session.status(),
                "hook_contracts": declared_harnessx_hook_contracts(),
                "recorded_evidence": recorded_evidence_summaries(),
                "declared_runtime_contract": True,
                "harness_autonomy": governor.status(),
                "workspace": str(self.state.workspace),
                "model": self.state.model,
                "effort": self.state.effort,
                "routing_mode": self.state.routing_mode,
                "autonomy_mode": self.state.autonomy_mode,
            }
        if command == "session.new_thread":
            session, _governor = self._require_session()
            session.start_new_thread()
            return "session.new_thread", {"started": True}
        if command == "session.resume_thread":
            session, _governor = self._require_session()
            thread_id = payload.get("thread_id")
            if not isinstance(thread_id, str):
                raise BridgeError("thread_id must be a string")
            session.prepare_thread_resume(thread_id)
            return "session.resume_thread", {
                "prepared": True,
                "thread_id": thread_id,
                "provider_resume_verified": False,
                "verification_boundary": "next_completed_provider_turn",
            }
        if command == "chat.send":
            text = payload.get("text")
            if not isinstance(text, str):
                raise BridgeError("text must be a string")
            return self._send(text)
        if command == "approval.resolve":
            approved = payload.get("approved")
            if not isinstance(approved, bool):
                raise BridgeError("approved must be a boolean")
            return self._resolve_approval(approved)
        if command == "feedback.record":
            outcome = payload.get("outcome")
            if outcome not in {"pass", "fail"}:
                raise BridgeError("outcome must be pass or fail")
            session, _governor = self._require_session()
            return "feedback.recorded", session.record_feedback(outcome)
        raise BridgeError("unsupported command")

    def _start_session(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self.state.session is not None:
            raise BridgeError("a session is already active; restart the bridge to replace it")
        return self._create_fresh_session(payload)

    def _restart_session(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Replace the runtime with a new backend/governor and trace root.

        Existing session trace directories are never deleted or reused, so
        switching a next-session model remains independently auditable.
        """

        if self.state.session is None or self.state.trace_root is None:
            raise BridgeError("start a session before restarting it")
        previous_trace_root = self.state.trace_root
        data = self._create_fresh_session(payload)
        data["previous_trace_root"] = str(previous_trace_root)
        return data

    def _update_session_settings(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Update the next-turn contract without replacing the provider thread."""

        session, governor = self._require_session()
        if governor.pending is not None:
            raise BridgeError("resolve the pending approval before changing settings")
        model = payload.get("model")
        effort = payload.get("effort", self.state.effort)
        routing_mode = payload.get("routing_mode", self.state.routing_mode)
        autonomy_mode = payload.get("autonomy_mode", self.state.autonomy_mode)
        if model is not None and (
            not isinstance(model, str) or not MODEL_ID_RE.fullmatch(model)
        ):
            raise BridgeError("model must be a 1-128 character safe model ID")
        if effort not in ALLOWED_EFFORTS:
            raise BridgeError("unsupported reasoning effort")
        if routing_mode not in {"semantic", "deterministic"}:
            raise BridgeError("routing_mode must be semantic or deterministic")
        if autonomy_mode not in {"managed", "strict"}:
            raise BridgeError("autonomy_mode must be managed or strict")
        if not isinstance(session.backend, CodexChatBackend):
            raise BridgeError("the active provider backend cannot update settings")
        if self.state.trace_root is None or self.state.workspace is None:
            raise BridgeError("the active session has no trace contract")

        session.backend.model_id = model
        session.backend.effort = effort
        session.routing_mode = routing_mode
        session.semantic_router = (
            CodexCliSemanticRouter(
                executable=Path(session.backend.executable),
                cli_version=session.backend.cli_version,
                workspace=self.state.workspace,
                trace_root=self.state.trace_root,
                model_id=model,
                effort="low",
            )
            if routing_mode == "semantic"
            else None
        )
        governor.approval_mode = autonomy_mode
        self.state.model = model
        self.state.effort = effort
        self.state.routing_mode = routing_mode
        self.state.autonomy_mode = autonomy_mode
        return {
            "model": model,
            "effort": effort,
            "routing_mode": routing_mode,
            "autonomy_mode": autonomy_mode,
            "provider_thread_preserved": session.thread_id is not None,
            "trace_root_preserved": True,
            "applies_from": "next_provider_turn",
        }

    def _create_fresh_session(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        workspace_value = payload.get("workspace")
        if not isinstance(workspace_value, str) or not workspace_value.strip():
            raise BridgeError("workspace must be an existing directory path")
        try:
            workspace = Path(workspace_value).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise BridgeError("workspace does not exist") from exc
        if not workspace.is_dir():
            raise BridgeError("workspace must be a directory")

        model = payload.get("model")
        effort = payload.get("effort", DEFAULT_EFFORT)
        routing_mode = payload.get("routing_mode", "semantic")
        autonomy_mode = payload.get("autonomy_mode", "managed")
        requested_executable = payload.get("executable")
        if requested_executable is not None and not isinstance(requested_executable, str):
            raise BridgeError("executable must be a string")
        if model is not None and (
            not isinstance(model, str) or not MODEL_ID_RE.fullmatch(model)
        ):
            raise BridgeError("model must be a 1-128 character safe model ID")
        if effort not in ALLOWED_EFFORTS:
            raise BridgeError("unsupported reasoning effort")
        if routing_mode not in {"semantic", "deterministic"}:
            raise BridgeError("routing_mode must be semantic or deterministic")
        if autonomy_mode not in {"managed", "strict"}:
            raise BridgeError("autonomy_mode must be managed or strict")

        status = account_status(requested_executable)
        if not status["connected"]:
            raise BridgeError("connect a Codex account before starting a live session")
        executable = Path(str(status["executable"]))
        cli_version = str(status["cli_version"])
        trace_root = (
            workspace
            / ".merlin"
            / "desktop-app"
            / f"session-{uuid.uuid4().hex}"
        )
        backend = CodexChatBackend(
            executable=executable,
            cli_version=cli_version,
            workspace=workspace,
            trace_root=trace_root,
            model_id=model,
            effort=effort,
        )
        semantic_router = (
            CodexCliSemanticRouter(
                executable=executable,
                cli_version=cli_version,
                workspace=workspace,
                trace_root=trace_root,
                model_id=model,
                effort="low",
            )
            if routing_mode == "semantic"
            else None
        )
        session = MerlinChatSession(
            workspace=workspace,
            library=FileSkillLibrary(DEFAULT_SKILLS_ROOT),
            backend=backend,
            trace_root=trace_root,
            top_k=3,
            routing_mode=routing_mode,
            semantic_router=semantic_router,
        )
        governor = ConsentGatedHarnessGovernor(
            trace_root=trace_root,
            approval_mode=autonomy_mode,
        )
        self.state = RuntimeState(
            session=session,
            governor=governor,
            workspace=workspace,
            model=model,
            effort=effort,
            routing_mode=routing_mode,
            autonomy_mode=autonomy_mode,
            trace_root=trace_root,
        )
        return {
            "workspace": str(workspace),
            "model": model,
            "effort": effort,
            "routing_mode": routing_mode,
            "autonomy_mode": autonomy_mode,
            "trace_root": str(trace_root),
            "fresh_trace_root": True,
            "primary_surface": "chat",
        }

    def _apply_adoption(
        self,
        adoption: AutonomyAdoption,
        *,
        original_request: str,
    ) -> tuple[ChatResponse, dict[str, Any]]:
        session, _governor = self._require_session()
        if (
            adoption.status != "adopted"
            or adoption.library is None
            or adoption.skill_bundle_paths is None
        ):
            raise BridgeError(adoption.reason or "harness change was not adopted")
        merged_paths = dict(session.skill_bundle_paths)
        merged_paths.update(adoption.skill_bundle_paths)
        session.install_verified_library_overlay(
            library=adoption.library,
            skill_bundle_paths=merged_paths,
        )
        response = session.send(original_request)
        action = {
            "status": adoption.status,
            "proposal": adoption.proposal.to_safe_dict(),
            "gate_count": len((adoption.creation_evidence or {}).get("gates", [])),
            "source_library_unchanged": True,
            "scope": "copy_on_write_session_overlay",
        }
        return response, action

    def _send(self, text: str) -> tuple[str, dict[str, Any]]:
        session, governor = self._require_session()
        if governor.pending is not None:
            raise BridgeError("resolve the pending approval before sending another request")
        if text.lstrip().startswith("/skill"):
            match = re.fullmatch(
                r"\s*/skill\s+([A-Za-z0-9][A-Za-z0-9._-]{0,127})\s+(.+?)\s*",
                text,
                flags=re.DOTALL,
            )
            if match is None:
                raise BridgeError("usage: /skill <active-skill-id> <request>")
            skill_id, request = match.groups()
            if not SKILL_ID_RE.fullmatch(skill_id):
                raise BridgeError("skill id has an unsafe format")
            response = session.send(request, explicit_skill_id=skill_id)
            return "chat.completed", self._chat_payload(response, [])
        proposal = governor.consider(text, session.library)
        harness_actions: list[dict[str, Any]] = []
        if proposal is not None and proposal.permission_required:
            return "approval.required", {
                "proposal": proposal.to_safe_dict(),
                "message": governor.render_permission_request(),
                "original_request_resumes_after_approval": True,
            }
        if proposal is not None:
            adoption = governor.authorize_managed(session.library)
            response, action = self._apply_adoption(adoption, original_request=text)
            harness_actions.append(action)
        else:
            response = session.send(text)
        return "chat.completed", self._chat_payload(response, harness_actions)

    def _resolve_approval(self, approved: bool) -> tuple[str, dict[str, Any]]:
        session, governor = self._require_session()
        original_request = governor.pending_original_request
        if original_request is None:
            raise BridgeError("no approval is pending")
        adoption = governor.resolve_permission(
            "yes" if approved else "no",
            session.library,
        )
        if adoption.status == "declined":
            return "approval.declined", {
                "proposal": adoption.proposal.to_safe_dict(),
                "original_request_executed": False,
            }
        response, action = self._apply_adoption(
            adoption,
            original_request=original_request,
        )
        return "chat.completed", self._chat_payload(response, [action])

    @staticmethod
    def _chat_payload(
        response: ChatResponse,
        harness_actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "answer": response.answer,
            "thread_id": response.thread_id,
            "turn_id": response.turn_id,
            "turn_number": response.turn_number,
            "provisioned_skills": [item.to_dict() for item in response.provisioned_skills],
            "routing_decision": dict(response.routing_decision),
            "harness_actions": harness_actions,
            "evidence": {
                "raw_trace_pointer": response.raw_trace_pointer,
                "prompt_provisioning_is_native_invocation": False,
            },
        }


def _response(
    *,
    request_id: str | None,
    ok: bool,
    event: str,
    data: Mapping[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "ok": ok,
        "event": event,
    }
    if data is not None:
        payload["data"] = dict(data)
    if error is not None:
        payload["error"] = _clip(error)
    return payload


def serve(stdin: Any = sys.stdin, stdout: Any = sys.stdout) -> int:
    bridge = MerlinBridge()
    for raw_line in stdin:
        request_id: str | None = None
        try:
            if len(raw_line.encode("utf-8")) > MAX_REQUEST_BYTES:
                raise BridgeError("request exceeds the JSONL byte limit")
            request = json.loads(raw_line)
            if not isinstance(request, dict):
                raise BridgeError("request must be a JSON object")
            raw_id = request.get("request_id")
            if raw_id is not None and not isinstance(raw_id, str):
                raise BridgeError("request_id must be a string")
            request_id = raw_id
            event, data = bridge.dispatch(request)
            response = _response(
                request_id=request_id,
                ok=True,
                event=event,
                data=data,
            )
        except (
            BridgeError,
            ChatSessionError,
            CodexChatBackendError,
            ConsentGovernorError,
            json.JSONDecodeError,
            OSError,
            ValueError,
        ) as exc:
            response = _response(
                request_id=request_id,
                ok=False,
                event="error.safe_stop",
                error=str(exc),
            )
        stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
