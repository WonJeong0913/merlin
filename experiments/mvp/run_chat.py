"""Launch Merlin's chat-based Codex CLI agent beta."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from pathlib import PurePosixPath
from typing import Callable

from src.merlin_harness.chat_session import ChatSessionError, TheKingChatSession
from src.merlin_harness.harnessx_chat_shadow import HarnessXChatShadow
from src.merlin_harness.harnessx_runtime import make_default_harnessx_runtime
from src.merlin_harness.consent_governor import (
    ConsentGatedHarnessGovernor,
    ConsentGovernorError,
)
from src.merlin_harness.chat_lifecycle import (
    ChatLifecycleEvidenceError,
    assess_lifecycle_eligibility,
    load_chat_lifecycle_observation,
)
from src.merlin_harness.codex_chat import (
    ALLOWED_EFFORTS,
    CodexChatBackend,
    CodexChatBackendError,
    HarnessXLiveHookConfig,
)
from src.merlin_harness.library import FileSkillLibrary
from src.merlin_harness.governed_provisioning import active_library_snapshot
from src.merlin_harness.models import LifecycleStatus
from src.merlin_harness.semantic_router import CodexCliSemanticRouter
from src.merlin_harness.terminal_ui import TerminalUI
from experiments.mvp.lifecycle_session import LifecycleRecoverySession, LifecycleSessionError
from experiments.mvp.reporting import render_control_room


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SKILLS_ROOT = REPO_ROOT / "experiments" / "mvp" / "skills"
DEFAULT_PROMOTION_EVIDENCE = (
    REPO_ROOT
    / "experiments"
    / "mvp"
    / "results"
    / "model_authored_skill_live_v1"
    / "model_authored_skill_evidence.json"
)
DEFAULT_REPAIR_EVIDENCE = (
    REPO_ROOT
    / "experiments"
    / "mvp"
    / "results"
    / "model_authored_skill_repair_live_v1"
    / "model_authored_skill_repair_evidence.json"
)
DEFAULT_REPAIR_FAMILY2_EVIDENCE = (
    REPO_ROOT
    / "experiments"
    / "mvp"
    / "results"
    / "model_authored_skill_repair_family2_live_v1"
    / "model_authored_skill_repair_family2_evidence.json"
)
APP_CODEX_EXECUTABLE = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
HELP = """Commands:
  /help                 Show this help.
  /status               Show thread, provisioning, feedback, and lifecycle health.
  /skills               List the current library and lifecycle statuses.
  /trace                Show safe metadata for the most recent completed turn.
  /feedback pass|fail   Record outcome evidence for the latest turn; no automatic hide.
  /diagnose             Integrity-check the latest trace/feedback for observe-only lifecycle review.
  /learn NEED           Live beta: author, quarantine, verify, and activate the supported TODO skill.
  /creation status      Show the verified model-authored skill promotion loaded into this session.
  /creation gates       Show its quarantine, hidden-verifier, and adoption gates.
  /repair status        Show the primary audited model-authored v1→v2 repair.
  /repair gates         Show its target, hidden, regression, and COW gates.
  /repair portfolio     Show both bounded audited repair families and totals.
  /demo recovery        Run the full controlled overload→trace→COW recovery and print a compact result.
  /demo golden          Show the guided no-model-call judging flow.
  /demo golden json     Print the same hash-bound judging evidence as JSON.
  /governance ACTION    Run the controlled verifier lane: status, reset, load, reference,
                        overload, diagnose, stage, verify, or report.
  /new                  Start a new provider thread on the next message.
  /quit                 Exit the chat beta.
"""

JUDGE_GOLDEN_PROMPTS = frozenset(
    {
        "diagnose and safely recover this overloaded skill library",
        "diagnose and safely recover this overloaded skill library.",
        "recover the overloaded skill library safely",
        "recover the overloaded skill library safely.",
        "스킬 과부하를 진단하고 안전하게 복구해줘",
        "스킬 과부하를 진단하고 안전하게 복구해 줘",
    }
)


class OfflineJudgeBackend:
    """Fail closed if ordinary chat is attempted in account-free judge mode."""

    def run_turn(
        self, *, prompt: str, turn_number: int, thread_id: str | None
    ) -> None:
        del prompt, turn_number, thread_id
        raise CodexChatBackendError(
            "offline judge mode supports evidence commands only; restart without "
            "--judge for live GPT-5.6 chat turns"
        )


def _candidate_codex_executables(requested: str | None = None) -> list[Path]:
    candidates: list[Path] = []
    if requested:
        candidates.append(Path(requested).expanduser())
    # The desktop app's bundled CLI is the stable default for ChatGPT account
    # authentication. PATH may contain a stale npm shim whose wrapper exists
    # while its platform binary has already been removed. An explicit user
    # selection still has highest priority.
    candidates.append(APP_CODEX_EXECUTABLE)
    if discovered := shutil.which("codex"):
        candidates.append(Path(discovered))
    return list(dict.fromkeys(candidate.resolve() for candidate in candidates))


def detect_codex_runtime(
    requested: str | None = None,
    *,
    version_override: str | None = None,
) -> tuple[Path, str]:
    failures: list[str] = []
    for resolved in _candidate_codex_executables(requested):
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            failures.append(f"{resolved}: not an executable file")
            continue
        try:
            detected_version = detect_codex_version(resolved)
        except ValueError as exc:
            failures.append(f"{resolved}: {exc}")
            continue
        version = version_override.strip() if version_override else detected_version
        if not version:
            raise ValueError("--cli-version must be non-empty")
        return resolved, version
    detail = "; ".join(failures) if failures else "no candidates"
    raise ValueError(f"no working Codex executable was found ({detail}); pass --executable PATH")


def detect_codex_executable(requested: str | None = None) -> Path:
    """Return the first candidate that successfully executes ``--version``."""

    executable, _version = detect_codex_runtime(requested)
    return executable


def detect_codex_version(executable: Path) -> str:
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("failed to auto-detect Codex CLI version") from exc
    version = completed.stdout.strip()
    if completed.returncode != 0 or not version:
        raise ValueError("Codex CLI --version did not return a usable version")
    return version


def resolve_chat_workspace(
    requested: Path | None,
    *,
    default_parent: Path = Path("/private/tmp"),
) -> tuple[Path, bool]:
    """Resolve a caller workspace or create one private, persistent demo workspace."""

    if requested is not None:
        try:
            workspace = requested.expanduser().resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError("--workspace must exist") from exc
        if not workspace.is_dir():
            raise ValueError("--workspace must be a directory")
        return workspace, False

    try:
        parent = default_parent.expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError("default workspace parent does not exist") from exc
    if not parent.is_dir():
        raise ValueError("default workspace parent must be a directory")
    workspace = parent / f"merlin-chat-{uuid.uuid4().hex}"
    workspace.mkdir(mode=0o700, parents=False, exist_ok=False)
    return workspace.resolve(strict=True), True


def load_verified_promotion_overlay(
    *,
    base_library: FileSkillLibrary,
    evidence_path: Path,
    overlay_root: Path,
) -> tuple[FileSkillLibrary, dict]:
    """Materialize a read-only-at-source session overlay from promotion evidence."""

    evidence_path = evidence_path.expanduser().resolve(strict=True)
    if evidence_path.is_symlink() or not evidence_path.is_file():
        raise ValueError("--promotion-evidence must be a regular JSON file")
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("promotion evidence is not valid UTF-8 JSON") from exc
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 1:
        raise ValueError("promotion evidence schema is unsupported")
    boundary = evidence.get("evidence_boundary")
    if (
        evidence.get("adopted") is not True
        or not isinstance(boundary, dict)
        or boundary.get("copy_on_write_promoted") is not True
        or boundary.get("live_library_mutated") is not False
        or boundary.get("hidden_held_out_verifier_passed") is not True
    ):
        raise ValueError("promotion evidence does not authorize an adopted session overlay")
    if not all(isinstance(gate, dict) and gate.get("passed") is True for gate in evidence.get("gates", [])):
        raise ValueError("promotion evidence contains a missing or failed gate")
    base_skills = tuple(base_library.list())
    base_snapshot = active_library_snapshot(base_skills)[1]
    if evidence.get("original_library_snapshot_sha256") != base_snapshot:
        raise ValueError("base library drifted from the promotion evidence")
    provisional_path = evidence_path.with_name("provisional_library.json")
    try:
        provisional_data = json.loads(provisional_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("provisional library is missing or invalid") from exc
    if not isinstance(provisional_data, list) or not provisional_data:
        raise ValueError("provisional library must be a non-empty list")
    try:
        provisional_skills = tuple(FileSkillLibrary._from_dict(item) for item in provisional_data)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("provisional library contains an invalid skill") from exc
    if active_library_snapshot(provisional_skills)[1] != evidence.get(
        "provisional_library_snapshot_sha256"
    ):
        raise ValueError("provisional library hash differs from promotion evidence")
    base_by_id = {skill.id: skill.to_dict() for skill in base_skills}
    provisional_by_id = {skill.id: skill.to_dict() for skill in provisional_skills}
    if len(provisional_by_id) != len(provisional_skills) or any(
        provisional_by_id.get(skill_id) != payload for skill_id, payload in base_by_id.items()
    ):
        raise ValueError("provisional library rewrites or drops an existing skill")
    candidate_id = evidence.get("candidate_skill_id")
    candidate = next((skill for skill in provisional_skills if skill.id == candidate_id), None)
    if candidate is None or candidate.status != LifecycleStatus.ACTIVE:
        raise ValueError("promoted candidate is not active in the provisional library")
    if overlay_root.exists():
        raise ValueError("refusing to overwrite session library overlay")
    overlay = FileSkillLibrary(overlay_root)
    for skill in provisional_skills:
        overlay.save(skill)
    quarantine_root = evidence_path.parent / "quarantine"
    try:
        quarantine_manifest = json.loads(
            (quarantine_root / "quarantine_manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("promoted candidate quarantine manifest is missing or invalid") from exc
    expected_manifest_sha256 = evidence.get("quarantine", {}).get("manifest_sha256")
    if quarantine_manifest.get("manifest_sha256") != expected_manifest_sha256:
        raise ValueError("promoted candidate quarantine identity differs from evidence")
    manifest_body = {
        key: value
        for key, value in quarantine_manifest.items()
        if key not in {"schema_version", "manifest_sha256"}
    }
    manifest_sha256 = hashlib.sha256(
        json.dumps(
            manifest_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if manifest_sha256 != expected_manifest_sha256:
        raise ValueError("promoted candidate quarantine manifest hash is invalid")
    source_bundle = quarantine_root / "candidate" / candidate_id
    expected_bundle_paths: set[str] = set()
    for record in quarantine_manifest.get("files", []):
        if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
            raise ValueError("promoted candidate file record is malformed")
        relative = PurePosixPath(record["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("promoted candidate file path is unsafe")
        source = source_bundle.joinpath(*relative.parts)
        if source.is_symlink() or not source.is_file():
            raise ValueError("promoted candidate file is missing or linked")
        payload = source.read_bytes()
        if len(payload) != record["bytes"] or hashlib.sha256(payload).hexdigest() != record["sha256"]:
            raise ValueError("promoted candidate file differs from quarantine evidence")
        expected_bundle_paths.add(relative.as_posix())
    actual_bundle_paths = {
        path.relative_to(source_bundle).as_posix()
        for path in source_bundle.rglob("*")
        if path.is_file()
    }
    if actual_bundle_paths != expected_bundle_paths:
        raise ValueError("promoted candidate bundle contains unmanifested files")
    staged_bundle = overlay_root.parent / "promoted-bundles" / candidate_id
    shutil.copytree(source_bundle, staged_bundle, symlinks=False)
    overlay.verified_bundle_paths = {candidate_id: staged_bundle}
    safe_summary = {
        "schema_version": 1,
        "campaign_id": evidence.get("campaign_id"),
        "candidate_skill_id": candidate_id,
        "adopted": True,
        "target_pass_rate": [
            evidence.get("baseline_target_pass_rate"),
            evidence.get("candidate_target_pass_rate"),
        ],
        "hidden_held_out_verifier_passed": True,
        "gate_count": len(evidence["gates"]),
        "gates": evidence["gates"],
        "model_evidence_level": boundary.get("model_evidence_level"),
        "requested_model_id": boundary.get("requested_model_id"),
        "provider_reported_model_ids": boundary.get("provider_reported_model_ids"),
        "source_evidence_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "session_overlay_snapshot_sha256": active_library_snapshot(provisional_skills)[1],
        "verified_candidate_bundle_staged": True,
        "candidate_bundle_manifest_sha256": expected_manifest_sha256,
        "boundary": {
            "source_library_mutated": False,
            "session_overlay_only": True,
            "prompt_provisioning_is_provider_native_invocation": False,
        },
    }
    chain_audit_path = evidence_path.with_name("model_authored_skill_chain_audit.json")
    if chain_audit_path.exists():
        if chain_audit_path.is_symlink() or not chain_audit_path.is_file():
            raise ValueError("model-authored chain audit must be a regular file")
        try:
            chain_audit = json.loads(chain_audit_path.read_text(encoding="utf-8"))
            from experiments.mvp.audit_model_authored_skill_chain import (
                validate_model_authored_skill_chain_audit,
            )

            validate_model_authored_skill_chain_audit(chain_audit)
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("model-authored chain audit is invalid") from exc
        safe_summary["chain_audit"] = {
            "status": chain_audit["status"],
            "checks_passed": len(chain_audit["checks"]),
            "checks_total": len(chain_audit["checks"]),
            "audit_sha256": chain_audit["audit_sha256"],
            "fresh_revalidation": chain_audit["fresh_revalidation"],
            "claim_boundary": chain_audit["claim_boundary"],
        }
    (overlay_root.parent / "library-overlay-manifest.json").write_text(
        json.dumps(safe_summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return overlay, safe_summary


class LiveLearningError(ValueError):
    """Safe terminal error for the bounded live-learning beta."""


class GoldenPassEvidenceError(ValueError):
    """Raised when the safe, recorded promoted-chat evidence is not compatible."""


def load_verified_repair_summary(evidence_path: Path) -> dict:
    """Load a hash-bound repair result for read-only terminal inspection."""

    evidence_path = evidence_path.expanduser()
    if evidence_path.is_symlink():
        raise ValueError("model-authored repair evidence must not be a symlink")
    evidence_path = evidence_path.resolve(strict=True)
    if not evidence_path.is_file():
        raise ValueError("model-authored repair evidence must be a regular file")
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("model-authored repair evidence/audit is invalid") from exc
    campaign_id = evidence.get("campaign_id") if isinstance(evidence, dict) else None
    if campaign_id == "live-gpt56-model-authored-repair-v1":
        audit_path = evidence_path.with_name("model_authored_skill_repair_chain_audit.json")
        authored_boundary_key = "model_authored_repair"
        from experiments.mvp.audit_model_authored_repair_chain import (
            validate_model_authored_repair_chain_audit as validate_audit,
        )
    elif campaign_id == "live-gpt56-model-authored-repair-family2-v1":
        audit_path = evidence_path.with_name(
            "model_authored_skill_repair_family2_chain_audit.json"
        )
        authored_boundary_key = "candidate_model_authored_repair"
        from experiments.mvp.audit_model_authored_repair_family2_chain import (
            validate_model_authored_repair_family2_chain_audit as validate_audit,
        )
    else:
        raise ValueError("model-authored repair campaign is unsupported")
    if audit_path.is_symlink() or not audit_path.is_file():
        raise ValueError("model-authored repair chain audit is missing")
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        validate_audit(audit)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("model-authored repair evidence/audit is invalid") from exc
    boundary = evidence.get("evidence_boundary")
    repair = evidence.get("repair_result")
    model = evidence.get("model_repair")
    gates = repair.get("gates") if isinstance(repair, dict) else None
    if (
        evidence.get("schema_version") != 1
        or evidence.get("adopted") is not True
        or repair.get("lifecycle_action") != "adopt"
        or not isinstance(boundary, dict)
        or boundary.get(authored_boundary_key) is not True
        or boundary.get("copy_on_write_promoted") is not True
        or boundary.get("live_library_mutated") is not False
        or not isinstance(model, dict)
        or not isinstance(gates, list)
        or len(gates) != 6
        or not all(isinstance(gate, dict) and gate.get("passed") is True for gate in gates)
        or audit["source_hashes"]["repair_evidence_file_sha256"]
        != hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    ):
        raise ValueError("model-authored repair promotion boundary is invalid")
    return {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "skill_id": evidence["skill_id"],
        "version": [evidence["baseline_version"], evidence["candidate_version"]],
        "adopted": True,
        "lifecycle_action": "adopt",
        "requested_model_id": boundary["requested_model_id"],
        "model_evidence_level": boundary["model_evidence_level"],
        "provider_reported_model_ids": boundary["provider_reported_model_ids"],
        "gates": gates,
        "gate_count": len(gates),
        "target": {
            "baseline": _repair_pass_fraction(repair["baseline_target_results"]),
            "candidate": _repair_pass_fraction(
                repair["candidate_evaluations"][0]["target_results"]
            ),
        },
        "hidden_held_out": {
            "baseline": _repair_pass_fraction(repair["baseline_held_out_results"]),
            "candidate": _repair_pass_fraction(repair["candidate_held_out_results"]),
        },
        "library_regression": {
            "baseline": _repair_pass_fraction(repair["baseline_library_results"]),
            "candidate": _repair_pass_fraction(repair["provisional_library_results"]),
        },
        "baseline_model_authored": boundary.get("baseline_model_authored"),
        "live_library_mutated": False,
        "audit": {
            "status": audit["status"],
            "checks_passed": len(audit["checks"]),
            "checks_total": len(audit["checks"]),
            "audit_sha256": audit["audit_sha256"],
            "fresh_revalidation": audit["fresh_revalidation"],
        },
    }


def _repair_pass_fraction(results: object) -> list[int]:
    if not isinstance(results, list) or not results:
        raise ValueError("model-authored repair result split is invalid")
    if not all(isinstance(item, dict) and isinstance(item.get("passed"), bool) for item in results):
        raise ValueError("model-authored repair result split is invalid")
    return [sum(item["passed"] is True for item in results), len(results)]


def build_verified_repair_portfolio(repairs: tuple[dict, ...]) -> dict:
    """Aggregate distinct audited runs without claiming population performance."""

    if not repairs:
        raise ValueError("repair portfolio requires at least one audited family")
    campaign_ids = [repair.get("campaign_id") for repair in repairs]
    skill_ids = [repair.get("skill_id") for repair in repairs]
    if len(set(campaign_ids)) != len(repairs) or len(set(skill_ids)) != len(repairs):
        raise ValueError("repair portfolio contains duplicate campaigns or skills")
    if not all(repair.get("adopted") is True for repair in repairs):
        raise ValueError("repair portfolio contains an unaudited lifecycle outcome")
    gate_count = sum(int(repair["gate_count"]) for repair in repairs)
    audit_checks = sum(int(repair["audit"]["checks_total"]) for repair in repairs)
    return {
        "schema_version": 1,
        "scope": "bounded_retained_runs_only",
        "family_count": len(repairs),
        "promoted_count": sum(repair["adopted"] is True for repair in repairs),
        "gate_totals": {"passed": gate_count, "total": gate_count},
        "audit_totals": {"passed": audit_checks, "total": audit_checks},
        "families": list(repairs),
        "claim_boundary": {
            "broad_repair_generalization": False,
            "provider_resolved_model_identity": False,
            "provider_native_skill_invocation": False,
            "success_rate_inference_allowed": False,
        },
    }


def _json_bytes(payload: dict) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def write_golden_judge_artifacts(
    *,
    output_root: Path,
    golden_summary: dict,
    lifecycle_report: dict,
) -> dict:
    """Seal one golden chat run into a new-only, hash-bound judge bundle."""

    output_root = output_root.expanduser().resolve(strict=False)
    if output_root.exists():
        raise GoldenPassEvidenceError("golden artifact output already exists")
    if not output_root.parent.is_dir():
        raise GoldenPassEvidenceError("golden artifact output parent must exist")

    payloads = {
        "golden-pass.json": _json_bytes(golden_summary),
        "controlled-lifecycle.json": _json_bytes(lifecycle_report),
        "golden-report.html": _render_golden_judge_report(golden_summary).encode("utf-8"),
        "controlled-lifecycle-control-room.html": render_control_room(
            lifecycle_report
        ).encode("utf-8"),
    }
    created_files: list[Path] = []
    try:
        output_root.mkdir(mode=0o700, parents=False, exist_ok=False)
        records = []
        for name, payload in payloads.items():
            target = output_root / name
            with target.open("xb") as handle:
                handle.write(payload)
            created_files.append(target)
            records.append(
                {
                    "path": name,
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
        manifest_body = {
            "artifact_set": "Merlin Build Week golden judge bundle",
            "source_command": "python3 -m experiments.mvp.run_chat --judge --golden",
            "artifacts": records,
            "evidence_boundary": golden_summary["evidence_boundary"],
        }
        manifest = {
            "schema_version": 1,
            **manifest_body,
            "manifest_sha256": hashlib.sha256(_json_bytes(manifest_body)).hexdigest(),
        }
        manifest_path = output_root / "ARTIFACTS.json"
        with manifest_path.open("xb") as handle:
            handle.write(_json_bytes(manifest))
        created_files.append(manifest_path)
    except Exception:
        for path in reversed(created_files):
            path.unlink(missing_ok=True)
        if output_root.exists():
            output_root.rmdir()
        raise
    return manifest


def _load_recorded_promoted_chat_evidence(
    path: Path,
    *,
    creation_evidence: dict,
) -> dict:
    """Load only the safe summary that binds a promoted chat smoke to its overlay.

    The resulting data is intentionally a review of an already-recorded provider
    turn.  It never replays the provider or upgrades trace-observed script
    execution into provider-native skill invocation.
    """

    try:
        resolved = path.expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise GoldenPassEvidenceError("recorded promoted-chat evidence is missing") from exc
    if resolved.is_symlink() or not resolved.is_file():
        raise GoldenPassEvidenceError("recorded promoted-chat evidence must be a regular JSON file")
    try:
        evidence = json.loads(resolved.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GoldenPassEvidenceError("recorded promoted-chat evidence is invalid JSON") from exc
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 1:
        raise GoldenPassEvidenceError("recorded promoted-chat evidence schema is unsupported")

    candidate_id = creation_evidence.get("candidate_skill_id")
    if not isinstance(candidate_id, str) or evidence.get("candidate_skill_id") != candidate_id:
        raise GoldenPassEvidenceError("recorded chat evidence candidate differs from the loaded overlay")
    if evidence.get("promotion_evidence_sha256") != creation_evidence.get("source_evidence_sha256"):
        raise GoldenPassEvidenceError("recorded chat evidence is not bound to the loaded promotion")

    provider = evidence.get("provider")
    routing = evidence.get("routing")
    trace_observation = evidence.get("trace_observation")
    verifier = evidence.get("verifier")
    boundary = evidence.get("evidence_boundary")
    if not all(
        isinstance(value, dict)
        for value in (provider, routing, trace_observation, verifier, boundary)
    ):
        raise GoldenPassEvidenceError("recorded promoted-chat evidence is incomplete")
    if (
        provider.get("requested_model_id") != creation_evidence.get("requested_model_id")
        or provider.get("model_evidence_level") != creation_evidence.get("model_evidence_level")
        or provider.get("provider_reported_model_ids")
        != creation_evidence.get("provider_reported_model_ids")
    ):
        raise GoldenPassEvidenceError("recorded chat provider contract differs from the loaded promotion")
    if (
        routing.get("provisioned_skill_ids") != [candidate_id]
        or routing.get("harness_primary_skill_id") != candidate_id
        or routing.get("exact_artifact_anchor") is not True
        or routing.get("exact_input_anchor") is not True
    ):
        raise GoldenPassEvidenceError("recorded chat did not route only the promoted skill")
    if (
        trace_observation.get("successful_promoted_script_execution_count") != 1
        or verifier.get("passed") is not True
        or not isinstance(verifier.get("id"), str)
    ):
        raise GoldenPassEvidenceError("recorded chat execution or frozen verifier is not accepted")
    if (
        boundary.get("promoted_bundle_script_execution_observed_in_provider_trace") is not True
        or boundary.get("deterministic_utility_verifier_passed") is not True
        or boundary.get("provider_native_skill_invocation_event") is not False
        or boundary.get("actual_invocation_evidence_complete_under_provider_native_taxonomy")
        is not False
        or boundary.get("model_quality_or_full_benchmark_claim") is not False
        or boundary.get("raw_provider_trace_packaged") is not False
    ):
        raise GoldenPassEvidenceError("recorded chat evidence boundary is unsafe")

    return {
        "candidate_skill_id": candidate_id,
        "provider": {
            "requested_model_id": provider["requested_model_id"],
            "requested_effort": provider.get("requested_effort"),
            "model_evidence_level": provider["model_evidence_level"],
            "provider_reported_model_ids": provider["provider_reported_model_ids"],
        },
        "routing": {
            "policy": routing.get("policy"),
            "provisioned_skill_ids": [candidate_id],
            "harness_primary_skill_id": candidate_id,
            "exact_artifact_anchor": True,
            "exact_input_anchor": True,
        },
        "trace_observation": {
            "successful_skill_body_read_count": trace_observation.get(
                "successful_skill_body_read_count"
            ),
            "successful_promoted_script_execution_count": 1,
        },
        "verifier": {
            "id": verifier["id"],
            "passed": True,
            "item_count": verifier.get("item_count"),
        },
        "evidence_boundary": {
            "promoted_bundle_script_execution_observed_in_provider_trace": True,
            "provider_native_skill_invocation_event": False,
            "actual_invocation_evidence_complete_under_provider_native_taxonomy": False,
            "model_quality_or_full_benchmark_claim": False,
            "raw_provider_trace_packaged": False,
        },
    }


def _run_controlled_recovery(governance: LifecycleRecoverySession) -> dict:
    """Run the existing controlled lifecycle state machine once and summarize it."""

    governance.reset()
    governance.load_sample()
    governance.run_reference()
    governance.run_overloaded()
    governance.diagnose()
    governance.stage_hide()
    governance.verify_and_promote()
    report = governance.final_report()
    conditions = report["conditions"]
    overloaded = conditions["Overloaded library"]
    recovered_name = next(name for name in conditions if name.startswith("Lifecycle "))
    recovered = conditions[recovered_name]
    return {
        "schema_version": 1,
        "demo": "trace-backed skill-overload recovery",
        "before": {
            "passed": overloaded["passed"],
            "task_count": overloaded["task_count"],
            "shadowing_rate": overloaded["pi_m"],
        },
        "intervention": {
            "action": "copy_on_write_hide",
            "skill_ids": [item["skill_id"] for item in report["lifecycle_decisions"]],
            "trace_backed": True,
        },
        "after": {
            "passed": recovered["passed"],
            "task_count": recovered["task_count"],
            "shadowing_rate": recovered["pi_m"],
        },
        "same_verifier_promotion": report["promotion"]["accepted"],
        "live_original_mutated_before_gate": False,
        "scope": "controlled deterministic ten-task demo; not a full benchmark",
    }


def _build_golden_pass_summary(
    governance: LifecycleRecoverySession,
    *,
    creation_evidence: dict,
    recorded_promoted_chat: dict,
) -> dict:
    """Join two bounded evidence lanes without representing them as one result."""

    recovery = _run_controlled_recovery(governance)
    chain_audit = creation_evidence.get("chain_audit")
    if (
        not isinstance(chain_audit, dict)
        or chain_audit.get("status") != "passed"
        or chain_audit.get("checks_passed") != 15
        or chain_audit.get("checks_total") != 15
    ):
        raise GoldenPassEvidenceError("the packaged model-authored evidence chain is not verified")
    return {
        "schema_version": 1,
        "demo": "Merlin judging golden pass",
        "judging_flow": [
            {
                "step": 1,
                "kind": "controlled_overload_problem",
                "result": recovery["before"],
            },
            {
                "step": 2,
                "kind": "The_KING_trace_backed_intervention",
                "result": recovery["intervention"],
            },
            {
                "step": 3,
                "kind": "same_verifier_recovery",
                "result": {
                    **recovery["after"],
                    "promotion_accepted": recovery["same_verifier_promotion"],
                },
            },
            {
                "step": 4,
                "kind": "requested_GPT_5_6_candidate_quarantine_and_promotion",
                "result": {
                    "candidate_skill_id": creation_evidence["candidate_skill_id"],
                    "requested_model_id": creation_evidence["requested_model_id"],
                    "requested_effort": recorded_promoted_chat["provider"][
                        "requested_effort"
                    ],
                    "model_evidence_level": creation_evidence[
                        "model_evidence_level"
                    ],
                    "provider_reported_model_ids": creation_evidence[
                        "provider_reported_model_ids"
                    ],
                    "target_pass_rate_before": creation_evidence[
                        "target_pass_rate"
                    ][0],
                    "target_pass_rate_after": creation_evidence[
                        "target_pass_rate"
                    ][1],
                    "hidden_held_out_verifier_passed": creation_evidence[
                        "hidden_held_out_verifier_passed"
                    ],
                    "promotion_gates_passed": creation_evidence["gate_count"],
                    "promotion_gates_total": creation_evidence["gate_count"],
                    "copy_on_write_adopted": creation_evidence["adopted"],
                    "chain_audit": chain_audit,
                },
            },
            {
                "step": 5,
                "kind": "recorded_model_authored_skill_chat_use",
                "result": recorded_promoted_chat,
            },
        ],
        "build_week_scorecard": {
            "technological_implementation": {
                "claim": "A requested-GPT-5.6 candidate is quarantined, tested on visible and hidden cases, promoted copy-on-write, then observed executing under a frozen verifier.",
                "evidence_steps": [2, 3, 4, 5],
            },
            "design": {
                "claim": "One natural-language chat request produces a five-step incident timeline plus self-contained inspectable artifacts.",
                "evidence_steps": [1, 2, 3, 4, 5],
            },
            "potential_impact": {
                "claim": "Agent platform and infrastructure teams can make growing skill-library changes auditable before promotion.",
                "production_impact_measured": False,
            },
            "quality_of_idea": {
                "claim": "Skill failure is managed as a harness lifecycle problem across routing, validation, recovery, and adoption—not only as skill generation.",
                "implemented_vertical_slice_only": True,
            },
        },
        "evidence_boundary": {
            "this_command_makes_no_model_call": True,
            "controlled_recovery_and_recorded_chat_use_are_distinct_lanes": True,
            "controlled_recovery_scope": recovery["scope"],
            "recorded_chat_is_hash_bound_to_the_loaded_copy_on_write_promotion": True,
            "provider_native_skill_invocation_event": False,
            "actual_invocation_evidence_complete_under_provider_native_taxonomy": False,
            "model_quality_or_full_benchmark_claim": False,
        },
    }


def _percent(value: float) -> str:
    return f"{round(float(value) * 100):d}%"


def _is_judge_golden_prompt(value: str) -> bool:
    """Recognize only the documented account-free natural-language demo intent."""

    normalized = " ".join(value.casefold().split())
    return normalized in JUDGE_GOLDEN_PROMPTS


def _render_golden_pass(summary: dict) -> str:
    """Render the bounded evidence as one judge-readable incident timeline."""

    steps = {item["kind"]: item["result"] for item in summary["judging_flow"]}
    before = steps["controlled_overload_problem"]
    intervention = steps["The_KING_trace_backed_intervention"]
    after = steps["same_verifier_recovery"]
    creation = steps["requested_GPT_5_6_candidate_quarantine_and_promotion"]
    chain = creation["chain_audit"]
    use = steps["recorded_model_authored_skill_chat_use"]
    trace = use["trace_observation"]
    verifier = use["verifier"]
    hidden_ids = ", ".join(intervention["skill_ids"])
    return "\n".join(
        (
            "╔══════════════════════════════════════════════════════════════╗",
            "║ THE KING · BUILD WEEK GOLDEN RUN                            ║",
            "║ Self-managing safety for growing agent skill libraries      ║",
            "╚══════════════════════════════════════════════════════════════╝",
            f"[1/5] PROBLEM   overloaded library · {before['passed']}/{before['task_count']} pass · {_percent(before['shadowing_rate'])} shadowing",
            f"[2/5] DIAGNOSE  repeated harmful routes → hide {hidden_ids}",
            "                 trace-backed · copy-on-write · source library unchanged",
            f"[3/5] RECOVER   same verifier · {after['passed']}/{after['task_count']} pass · {_percent(after['shadowing_rate'])} shadowing · gate PASS",
            f"[4/5] CREATE    requested {creation['requested_model_id']}/{creation['requested_effort']} → {creation['candidate_skill_id']}",
            f"                 quarantine + target {_percent(creation['target_pass_rate_before'])}→{_percent(creation['target_pass_rate_after'])} + hidden PASS + COW {creation['promotion_gates_passed']}/{creation['promotion_gates_total']} · chain audit {chain['checks_passed']}/{chain['checks_total']}",
            f"[5/5] USE       staged body reads {trace['successful_skill_body_read_count']} · promoted script runs {trace['successful_promoted_script_execution_count']} · verifier {'PASS' if verifier['passed'] else 'FAIL'} ({verifier['item_count']} items)",
            "",
            "EVIDENCE  live controlled state machine + hash-bound recorded provider turn",
            "BOUNDARY  this replay makes no model call; provider-native Skill event is not claimed",
        )
    )


def _render_golden_judge_report(summary: dict, *, server_mode: bool = False) -> str:
    """Render the five-step chat incident as a self-contained judge overview."""

    steps = {item["kind"]: item["result"] for item in summary["judging_flow"]}
    before = steps["controlled_overload_problem"]
    intervention = steps["The_KING_trace_backed_intervention"]
    after = steps["same_verifier_recovery"]
    creation = steps["requested_GPT_5_6_candidate_quarantine_and_promotion"]
    chain = creation["chain_audit"]
    use = steps["recorded_model_authored_skill_chat_use"]
    trace = use["trace_observation"]
    verifier = use["verifier"]
    hidden_ids = ", ".join(intervention["skill_ids"])
    esc = lambda value: html.escape(str(value), quote=True)
    links = (
        '<a class="primary" href="/control-room">Open technical Control Room</a>'
        '<a href="/download/golden.json">Download golden JSON</a>'
        '<a href="/download/lifecycle.json">Download lifecycle JSON</a>'
        if server_mode
        else '<a class="primary" href="controlled-lifecycle-control-room.html">Open technical Control Room</a>'
        '<a href="golden-pass.json">Inspect golden JSON</a>'
        '<a href="controlled-lifecycle.json">Inspect lifecycle JSON</a>'
        '<a href="ARTIFACTS.json">Verify artifact hashes</a>'
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,">
<title>Merlin — Build Week Golden Report</title>
<style>
:root{{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;background:#06101d;color:#edf7ff}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 12% -10%,#17445b 0,transparent 38rem),#06101d}}main{{width:min(1180px,calc(100% - 32px));margin:auto;padding:36px 0 54px}}.eyebrow{{color:#79e7f2;font-size:.76rem;font-weight:850;letter-spacing:.14em;text-transform:uppercase}}h1{{font-size:clamp(2.4rem,7vw,5.5rem);letter-spacing:-.065em;line-height:.92;margin:10px 0}}.king{{color:#72e9f4}}.lede{{color:#b7cad8;font-size:1.08rem;line-height:1.55;max-width:800px}}.boundary,.card,.metric,.fit-card{{background:rgba(12,29,45,.92);border:1px solid #2a5069;border-radius:16px}}.boundary{{color:#c6d8e5;margin:22px 0;padding:14px 16px}}.boundary strong{{color:#ffd275}}.metrics{{display:grid;gap:12px;grid-template-columns:repeat(3,1fr);margin:16px 0}}.metric{{padding:18px}}.metric span,.fit-card span{{color:#9db4c6;font-size:.72rem;font-weight:800;letter-spacing:.08em;text-transform:uppercase}}.metric strong{{display:block;font-size:clamp(1.8rem,4vw,3rem);margin-top:8px}}.good{{color:#79efbd}}.steps{{display:grid;gap:12px;grid-template-columns:repeat(5,1fr);margin-top:18px}}.card{{min-width:0;padding:16px}}.num{{align-items:center;background:#16465e;border-radius:50%;color:#a9f4fb;display:flex;font-size:.78rem;font-weight:900;height:28px;justify-content:center;width:28px}}h2{{font-size:1.02rem;margin:14px 0 8px}}.card p{{color:#aec3d3;font-size:.82rem;line-height:1.48;margin:0}}.card code{{color:#8feefa;overflow-wrap:anywhere}}.fit-title{{font-size:1.35rem;margin:28px 0 12px}}.fit{{display:grid;gap:12px;grid-template-columns:repeat(4,1fr)}}.fit-card{{padding:16px}}.fit-card p{{color:#c1d5e3;font-size:.86rem;line-height:1.5;margin:10px 0 0}}.actions{{display:flex;flex-wrap:wrap;gap:10px;margin-top:20px}}a{{background:#12334a;border:1px solid #4b829d;border-radius:10px;color:#effbff;font-weight:750;padding:11px 14px;text-decoration:none}}a.primary{{background:#0b766f;border-color:#64e6d8}}footer{{color:#91aabd;font-size:.78rem;line-height:1.5;margin-top:22px}}@media(max-width:900px){{.steps,.fit{{grid-template-columns:1fr 1fr}}}}@media(max-width:620px){{.metrics,.steps,.fit{{grid-template-columns:1fr}}main{{width:min(100% - 22px,1180px);padding-top:24px}}}}
</style></head><body><main>
<div class="eyebrow">OpenAI Build Week · Developer Tools</div>
<h1>The <span class="king">KING</span></h1>
<p class="lede">A chat-based control plane that diagnoses harmful skill routing, recovers with a copy-on-write lifecycle action, and admits a model-authored skill only after quarantine and verifier gates.</p>
<div class="boundary"><strong>Evidence boundary.</strong> The controlled overload recovery and the recorded GPT-5.6 authoring/use run are two hash-bound evidence lanes. This report joins their product story; it does not claim that this offline replay makes a model call or that the provider emitted a native Skill event.</div>
<section class="metrics" aria-label="Observed recovery metrics">
<div class="metric"><span>Task pass</span><strong>{before['passed']}/{before['task_count']} → <b class="good">{after['passed']}/{after['task_count']}</b></strong></div>
<div class="metric"><span>Shadowing</span><strong>{_percent(before['shadowing_rate'])} → <b class="good">{_percent(after['shadowing_rate'])}</b></strong></div>
<div class="metric"><span>Evidence chain</span><strong class="good">{chain['checks_passed']}/{chain['checks_total']} PASS</strong></div>
</section>
<section class="steps" aria-label="Five-step golden incident">
<article class="card"><div class="num">1</div><h2>Observe overload</h2><p>A controlled growing library falls to <strong>{before['passed']}/{before['task_count']}</strong> with <strong>{_percent(before['shadowing_rate'])}</strong> shadowing.</p></article>
<article class="card"><div class="num">2</div><h2>Diagnose routes</h2><p>Repeated harmful traces identify only <code>{esc(hidden_ids)}</code> for a narrow hide.</p></article>
<article class="card"><div class="num">3</div><h2>Recover safely</h2><p>The source library stays unchanged until the same verifier re-run reaches <strong>{after['passed']}/{after['task_count']}</strong> and the gate passes.</p></article>
<article class="card"><div class="num">4</div><h2>Govern creation</h2><p>A recorded <code>{esc(creation['requested_model_id'])}/{esc(creation['requested_effort'])}</code> run authored <code>{esc(creation['candidate_skill_id'])}</code>; {creation['promotion_gates_passed']}/{creation['promotion_gates_total']} gates and the sealed {chain['checks_passed']}/{chain['checks_total']} end-to-end chain audit passed.</p></article>
<article class="card"><div class="num">5</div><h2>Verify use</h2><p>The recorded chat trace observed {trace['successful_skill_body_read_count']} staged body read and {trace['successful_promoted_script_execution_count']} promoted script run; the frozen verifier <strong>{'passed' if verifier['passed'] else 'failed'}</strong> with {verifier['item_count']} items.</p></article>
</section>
<h2 class="fit-title">Why this is a Developer Tool</h2>
<section class="fit" aria-label="Build Week judge scorecard">
<article class="fit-card"><span>Implementation</span><p>Real requested-GPT-5.6 authoring, quarantine, hidden verification, copy-on-write promotion, and trace-observed script execution.</p></article>
<article class="fit-card"><span>Design</span><p>One natural-language request, one five-step incident, and one self-contained evidence bundle a reviewer can inspect offline.</p></article>
<article class="fit-card"><span>Potential impact</span><p>Agent platform teams can audit routing regressions and risky library changes before they reach the active harness.</p></article>
<article class="fit-card"><span>Quality of idea</span><p>Manage the whole skill harness—routing, validation, recovery, and adoption—not only the generated skill body.</p></article>
</section>
<nav class="actions" aria-label="Judge artifact links">{links}</nav>
<footer>Self-contained and dependency-free. No external font, CDN, network request, raw provider transcript, credential, or local workspace content is embedded.</footer>
</main></body></html>"""


def _load_hash_bound_promoted_chat_evidence(
    promotion_evidence_path: Path,
    *,
    creation_evidence: dict,
) -> dict:
    """Prefer the newest compatible summary, never an unbound recorded trace."""

    failures: list[str] = []
    for filename in ("promoted_chat_smoke_v2.json", "promoted_chat_smoke.json"):
        try:
            recorded = _load_recorded_promoted_chat_evidence(
                promotion_evidence_path.with_name(filename),
                creation_evidence=creation_evidence,
            )
        except GoldenPassEvidenceError as exc:
            failures.append(f"{filename}: {exc}")
            continue
        return {"recorded_evidence_file": filename, **recorded}
    raise GoldenPassEvidenceError(
        "no hash-bound recorded promoted-chat evidence is available ("
        + "; ".join(failures)
        + ")"
    )


class LiveSkillCreationController:
    """Connect the fixed verifier-backed creation campaign to one chat session."""

    def __init__(
        self,
        *,
        session: TheKingChatSession,
        base_library: FileSkillLibrary,
        codex_executable: Path,
        model_id: str,
        effort: str,
    ) -> None:
        self.session = session
        self.base_library = base_library
        self.codex_executable = codex_executable
        self.model_id = model_id
        self.effort = effort

    def learn(self, need: str) -> dict:
        need = need.strip()
        if not need or "\x00" in need or len(need) > 2_000:
            raise LiveLearningError("/learn need must be non-empty and at most 2000 characters")
        lowered = need.lower()
        anchors = ("todo", "backlog.todo", "todo-items.json")
        if not all(anchor in lowered for anchor in anchors):
            raise LiveLearningError(
                "this beta supports the verifier-backed TODO contract only; include TODO, backlog.todo, and todo-items.json"
            )
        if any(
            skill.id == "extract-todo-items" and skill.status == LifecycleStatus.ACTIVE
            for skill in self.session.library.list()
        ):
            raise LiveLearningError("extract-todo-items is already active in this session")
        run_id = uuid.uuid4().hex
        raw_root = Path("/private/tmp") / f"merlin-live-learn-{run_id}"
        safe_root = self.session.trace_root / "creation-runs" / run_id
        try:
            from experiments.mvp.run_live_model_skill_creation import run_campaign

            result = run_campaign(
                raw_root=raw_root,
                output_root=safe_root,
                codex_executable=self.codex_executable,
                model_id=self.model_id,
                effort=self.effort,
            )
            if result.get("adopted") is not True:
                raise LiveLearningError("candidate was rejected by the promotion gates")
            overlay, summary = load_verified_promotion_overlay(
                base_library=self.base_library,
                evidence_path=safe_root / "model_authored_skill_evidence.json",
                overlay_root=self.session.trace_root / f"library-overlay-learn-{run_id}",
            )
        except LiveLearningError:
            raise
        except (OSError, ValueError) as exc:
            raise LiveLearningError(str(exc)) from exc
        self.session.install_verified_library_overlay(
            library=overlay,
            skill_bundle_paths=overlay.verified_bundle_paths,
        )
        return {
            **summary,
            "learn_request_sha256": hashlib.sha256(need.encode("utf-8")).hexdigest(),
            "learn_request_stored": False,
            "status": "active_in_current_chat_session",
            "raw_provider_and_sandbox_artifacts_packaged": False,
        }


def run_repl(
    session: TheKingChatSession,
    *,
    creation_evidence: dict | None = None,
    repair_evidence: dict | None = None,
    repair_portfolio: dict | None = None,
    promotion_evidence_path: Path | None = None,
    learning_controller: LiveSkillCreationController | None = None,
    autonomy_governor: ConsentGatedHarnessGovernor | None = None,
    judge_mode: bool = False,
    judge_artifact_root: Path | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> int:
    output_fn("Merlin chat agent beta")
    if judge_mode:
        output_fn("OFFLINE JUDGE MODE · no Codex account, model call, or rebuild required")
    else:
        output_fn("Frozen Codex backend · active-library provisioning before every turn")
        if autonomy_governor is not None:
            if autonomy_governor.approval_mode == "managed":
                output_fn(
                    "Managed autonomy · low-risk reversible changes auto-authorized; elevation requires permission"
                )
            else:
                output_fn(
                    "Strict autonomy · explicit permission required for every skill change"
                )
    output_fn("Prompt provisioning is not provider-native skill invocation evidence.")
    if creation_evidence is not None:
        output_fn(
            "Verified promotion overlay loaded · "
            f"{creation_evidence['candidate_skill_id']} · "
            f"gates {creation_evidence['gate_count']}/{creation_evidence['gate_count']}"
        )
    if repair_evidence is not None:
        output_fn(
            "Verified model-authored repair loaded · "
            f"{repair_evidence['skill_id']} · "
            f"v{repair_evidence['version'][0]}→v{repair_evidence['version'][1]} · "
            f"gates {repair_evidence['gate_count']}/{repair_evidence['gate_count']}"
        )
    if repair_portfolio is not None:
        output_fn(
            "Verified repair portfolio loaded · "
            f"{repair_portfolio['family_count']} bounded families · "
            f"gates {repair_portfolio['gate_totals']['passed']}/"
            f"{repair_portfolio['gate_totals']['total']} · "
            f"audits {repair_portfolio['audit_totals']['passed']}/"
            f"{repair_portfolio['audit_totals']['total']}"
        )
    output_fn(
        'Try: "Diagnose and safely recover this overloaded skill library."'
        if judge_mode
        else "Type /help for commands."
    )
    governance = LifecycleRecoverySession()

    def activate_adoption(adoption, *, original_request: str) -> str | None:
        nonlocal creation_evidence
        if (
            adoption.status != "adopted"
            or adoption.library is None
            or adoption.skill_bundle_paths is None
        ):
            output_fn(
                "autonomy blocked: "
                + (adoption.reason or "verification did not authorize adoption")
            )
            return None
        merged_bundle_paths = dict(session.skill_bundle_paths)
        merged_bundle_paths.update(adoption.skill_bundle_paths)
        session.install_verified_library_overlay(
            library=adoption.library,
            skill_bundle_paths=merged_bundle_paths,
        )
        creation_evidence = adoption.creation_evidence
        output_fn(
            "autonomy> active in this session · extract-todo-items · "
            f"gates {len(creation_evidence['gates'])}/{len(creation_evidence['gates'])} · "
            "source library unchanged"
        )
        output_fn("autonomy> resuming the original request")
        return original_request

    try:
        while True:
            try:
                value = input_fn("you> ")
            except EOFError:
                output_fn("bye")
                return 0
            except KeyboardInterrupt:
                output_fn("\nInterrupted. Type /quit to exit or continue chatting.")
                continue
            value = value.strip()
            if not value:
                continue
            if value == "/quit":
                output_fn("bye")
                return 0
            if value == "/help":
                output_fn(HELP.rstrip())
                continue
            if value == "/status":
                status = session.status()
                if autonomy_governor is not None:
                    status["harness_autonomy"] = autonomy_governor.status()
                output_fn(json.dumps(status, ensure_ascii=False, indent=2))
                continue
            if value == "/skills":
                skills = session.list_skills()
                if not skills:
                    output_fn("No skills in the current library.")
                for skill in skills:
                    output_fn(
                        f"- {skill['skill_id']} [{skill['status']}] {skill['name']} · trigger: {skill['trigger']}"
                    )
                continue
            if value == "/trace":
                trace = session.last_trace()
                output_fn(
                    json.dumps(trace, ensure_ascii=False, indent=2)
                    if trace is not None
                    else "No completed turn trace yet."
                )
                continue
            if value == "/new":
                session.start_new_thread()
                output_fn("New provider thread requested; the next message starts with codex exec.")
                continue
            if value.startswith("/feedback"):
                parts = value.split()
                if len(parts) != 2:
                    output_fn("Usage: /feedback pass|fail")
                    continue
                try:
                    evidence = session.record_feedback(parts[1])
                except ChatSessionError as exc:
                    output_fn(f"feedback error: {exc}")
                else:
                    output_fn(
                        f"feedback recorded: turn {evidence['turn_number']} = {evidence['outcome']} "
                        "(health evidence only; automatic lifecycle change deferred)"
                    )
                continue
            if value == "/diagnose":
                trace = session.last_trace()
                if trace is None:
                    output_fn("diagnose blocked: no completed turn trace yet")
                    continue
                try:
                    observation = load_chat_lifecycle_observation(
                        session.trace_root,
                        turn_number=int(trace["turn_number"]),
                    )
                    eligibility = assess_lifecycle_eligibility(observation)
                except (ChatLifecycleEvidenceError, TypeError, ValueError) as exc:
                    output_fn(f"diagnose blocked: {exc}")
                else:
                    output_fn(
                        json.dumps(
                            {
                                "schema_version": 1,
                                "turn_number": observation.turn_number,
                                "feedback_outcome": observation.feedback_outcome,
                                "evidence_level": observation.evidence_level,
                                "exposure_skill_ids": list(observation.exposure_skill_ids),
                                "actual_invocation_evidence_complete": (
                                    observation.actual_invocation_evidence_complete
                                ),
                                "lifecycle": {
                                    "observe_only": eligibility.observe_only,
                                    "action_allowed": eligibility.action_allowed,
                                    "status": eligibility.status,
                                    "blockers": list(eligibility.blockers),
                                    "evidence_boundary": eligibility.evidence_boundary,
                                },
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                continue
            if value == "/learn" or value.startswith("/learn "):
                if learning_controller is None:
                    output_fn("Live learning is unavailable for this library configuration.")
                    continue
                need = value[len("/learn") :].strip()
                output_fn(
                    "learning> authoring → quarantine → isolated target/hidden verification → COW adoption"
                )
                try:
                    creation_evidence = learning_controller.learn(need)
                except LiveLearningError as exc:
                    output_fn(f"learning blocked: {exc}")
                else:
                    output_fn(
                        "learning> active · "
                        f"{creation_evidence['candidate_skill_id']} · "
                        f"gates {creation_evidence['gate_count']}/{creation_evidence['gate_count']} · "
                        "hidden verifier passed"
                    )
                continue
            if value.startswith("/creation"):
                parts = value.split()
                if len(parts) != 2 or parts[1] not in {"status", "gates"}:
                    output_fn("Usage: /creation status|gates")
                    continue
                if creation_evidence is None:
                    output_fn("No verified model-authored promotion is loaded in this session.")
                    continue
                payload = (
                    creation_evidence
                    if parts[1] == "status"
                    else {
                        "candidate_skill_id": creation_evidence["candidate_skill_id"],
                        "gates": creation_evidence["gates"],
                    }
                )
                output_fn(json.dumps(payload, ensure_ascii=False, indent=2))
                continue
            if value.startswith("/repair"):
                parts = value.split()
                if len(parts) != 2 or parts[1] not in {"status", "gates", "portfolio"}:
                    output_fn("Usage: /repair status|gates|portfolio")
                    continue
                if parts[1] == "portfolio":
                    output_fn(
                        json.dumps(repair_portfolio, ensure_ascii=False, indent=2)
                        if repair_portfolio is not None
                        else "No audited repair portfolio is loaded in this session."
                    )
                    continue
                if repair_evidence is None:
                    output_fn("No audited model-authored repair is loaded in this session.")
                    continue
                payload = (
                    repair_evidence
                    if parts[1] == "status"
                    else {
                        "skill_id": repair_evidence["skill_id"],
                        "version": repair_evidence["version"],
                        "gates": repair_evidence["gates"],
                        "audit": repair_evidence["audit"],
                    }
                )
                output_fn(json.dumps(payload, ensure_ascii=False, indent=2))
                continue
            if value == "/demo recovery":
                try:
                    summary = _run_controlled_recovery(governance)
                except LifecycleSessionError as exc:
                    output_fn(f"demo blocked [{exc.code}]: {exc}")
                    continue
                output_fn(json.dumps(summary, ensure_ascii=False, indent=2))
                continue
            natural_judge_demo = judge_mode and _is_judge_golden_prompt(value)
            if value in {"/demo golden", "/demo golden json"} or natural_judge_demo:
                if creation_evidence is None or promotion_evidence_path is None:
                    output_fn(
                        "golden demo blocked: start with --promotion-evidence and its "
                        "hash-bound recorded promoted-chat evidence"
                    )
                    continue
                try:
                    recorded_promoted_chat = _load_hash_bound_promoted_chat_evidence(
                        promotion_evidence_path,
                        creation_evidence=creation_evidence,
                    )
                    summary = _build_golden_pass_summary(
                        governance,
                        creation_evidence=creation_evidence,
                        recorded_promoted_chat=recorded_promoted_chat,
                    )
                    artifact_manifest = (
                        write_golden_judge_artifacts(
                            output_root=judge_artifact_root,
                            golden_summary=summary,
                            lifecycle_report=governance.final_report(),
                        )
                        if judge_artifact_root is not None
                        else None
                    )
                except (GoldenPassEvidenceError, LifecycleSessionError) as exc:
                    output_fn(f"golden demo blocked: {exc}")
                    continue
                output_fn(
                    json.dumps(summary, ensure_ascii=False, indent=2)
                    if value.endswith(" json")
                    else _render_golden_pass(summary)
                )
                if artifact_manifest is not None:
                    output_fn(
                        "ARTIFACT  "
                        f"{judge_artifact_root / 'golden-report.html'} · "
                        f"manifest {artifact_manifest['manifest_sha256'][:12]}"
                    )
                continue
            if value.startswith("/governance"):
                parts = value.split()
                if len(parts) != 2:
                    output_fn(
                        "Usage: /governance status|reset|load|reference|overload|diagnose|stage|verify|report"
                    )
                    continue
                action = parts[1]
                actions = {
                    "status": governance.public_state,
                    "reset": governance.reset,
                    "load": governance.load_sample,
                    "reference": governance.run_reference,
                    "overload": governance.run_overloaded,
                    "diagnose": governance.diagnose,
                    "stage": governance.stage_hide,
                    "verify": governance.verify_and_promote,
                    "report": governance.final_report,
                }
                handler = actions.get(action)
                if handler is None:
                    output_fn(
                        "Usage: /governance status|reset|load|reference|overload|diagnose|stage|verify|report"
                    )
                    continue
                try:
                    state = handler()
                except LifecycleSessionError as exc:
                    output_fn(f"governance blocked [{exc.code}]: {exc}")
                else:
                    output_fn(json.dumps(state, ensure_ascii=False, indent=2))
                continue
            if value.startswith("/"):
                output_fn("Unknown command. Type /help.")
                continue
            if autonomy_governor is not None:
                pending_request = autonomy_governor.pending_original_request
                if pending_request is not None:
                    try:
                        adoption = autonomy_governor.resolve_permission(
                            value, session.library
                        )
                    except ConsentGovernorError as exc:
                        output_fn(f"autonomy blocked: {exc}")
                        continue
                    if adoption.status == "declined":
                        output_fn(
                            "autonomy> declined · no model call, file write, or library change"
                        )
                        continue
                    if adoption.status == "ambiguous":
                        output_fn(
                            "autonomy> permission is still pending. Reply yes/no (네/아니요); "
                            "no change has been made."
                        )
                        continue
                    output_fn(
                        "autonomy> permission confirmed · G0–G6 evaluated in a copy-on-write workspace"
                    )
                    resumed = activate_adoption(
                        adoption, original_request=pending_request
                    )
                    if resumed is None:
                        continue
                    value = resumed
                else:
                    proposal = autonomy_governor.consider(value, session.library)
                    if proposal is not None:
                        if proposal.permission_required:
                            output_fn(autonomy_governor.render_permission_request())
                            continue
                        pending_request = autonomy_governor.pending_original_request
                        output_fn(
                            "autonomy> managed policy auto-authorized a low-risk reversible registered change"
                        )
                        try:
                            adoption = autonomy_governor.authorize_managed(
                                session.library
                            )
                        except ConsentGovernorError as exc:
                            output_fn(f"autonomy blocked: {exc}")
                            continue
                        output_fn(
                            "autonomy> G0–G6 evaluated in a copy-on-write workspace"
                        )
                        resumed = activate_adoption(
                            adoption,
                            original_request=pending_request or value,
                        )
                        if resumed is None:
                            continue
                        value = resumed
            try:
                response = session.send(value)
            except (ChatSessionError, CodexChatBackendError) as exc:
                output_fn(f"turn failed safely: {exc}")
                continue
            if response.provisioned_skills:
                output_fn("provisioned:")
                for skill in response.provisioned_skills:
                    output_fn(f"  - {skill.skill_id}: {skill.why}")
            else:
                output_fn("provisioned: none (no active skill matched this turn)")
            output_fn(f"assistant> {response.answer}")
    finally:
        governance.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Merlin chat-based Codex CLI agent beta.")
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Existing workspace Codex may modify; omitted creates a private /private/tmp workspace.",
    )
    parser.add_argument("--model", default="gpt-5.6-terra", help="Frozen existing model ID.")
    parser.add_argument(
        "--effort",
        default="high",
        choices=sorted(ALLOWED_EFFORTS),
        help="Explicit model reasoning effort (default: high).",
    )
    parser.add_argument("--executable", help="Codex executable path; auto-detected when omitted.")
    parser.add_argument("--cli-version", help="Codex CLI version label; auto-detected when omitted.")
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    parser.add_argument(
        "--promotion-evidence",
        type=Path,
        help="Verified model-authored promotion evidence; loads its provisional library as a session overlay.",
    )
    parser.add_argument(
        "--repair-evidence",
        type=Path,
        default=DEFAULT_REPAIR_EVIDENCE,
        help="Primary audited model-authored repair exposed by /repair status|gates.",
    )
    parser.add_argument(
        "--repair-family2-evidence",
        type=Path,
        default=DEFAULT_REPAIR_FAMILY2_EVIDENCE,
        help="Second audited repair family included in /repair portfolio.",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help=(
            "Account-free judging mode: load the packaged hash-bound promotion, "
            "skip Codex runtime detection, and enable evidence commands only."
        ),
    )
    parser.add_argument(
        "--golden",
        action="store_true",
        help=(
            "With --judge, run the documented natural-language golden incident "
            "and exit without entering an interactive REPL."
        ),
    )
    parser.add_argument(
        "--judge-artifacts",
        type=Path,
        help=(
            "With --judge, write the first golden flow as a new-only hash-bound "
            "JSON and standalone HTML bundle. --golden defaults this inside its "
            "private workspace."
        ),
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--harnessx-live-hooks",
        action="store_true",
        help=(
            "Experimental live PreToolUse boundary: allow only exact pwd commands "
            "and deny other Bash/apply_patch inputs before execution."
        ),
    )
    parser.add_argument(
        "--routing-mode",
        choices=("semantic", "deterministic", "controlled_lexical"),
        default="semantic",
        help=(
            "Semantic GPT-5.6 metadata routing (default), governed deterministic v1, "
            "or the controlled naive-lexical overload experiment."
        ),
    )
    parser.add_argument(
        "--routing-effort",
        choices=sorted(ALLOWED_EFFORTS),
        default="low",
        help="Independent semantic router effort (default: low).",
    )
    parser.add_argument("--routing-timeout", type=float, default=60.0)
    parser.add_argument(
        "--ui",
        choices=("auto", "terminal", "plain"),
        default="auto",
        help=(
            "Terminal presentation mode. auto enables the styled chat layout on a TTY; "
            "plain preserves the machine-friendly legacy stream."
        ),
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Keep the terminal layout but disable ANSI color.",
    )
    parser.add_argument(
        "--autonomy-mode",
        choices=("managed", "strict", "consent", "off"),
        default="managed",
        help=(
            "Managed (default) auto-authorizes only low-risk reversible registered "
            "changes; strict requires permission for every skill write; consent is "
            "a backward-compatible alias for strict; off disables the governor."
        ),
    )
    args = parser.parse_args(argv)
    if args.golden and not args.judge:
        parser.error("--golden requires --judge")
    if args.judge_artifacts is not None and not args.judge:
        parser.error("--judge-artifacts requires --judge")
    if args.harnessx_live_hooks and args.judge:
        parser.error("--harnessx-live-hooks is available only for live chat")

    try:
        workspace, workspace_created = resolve_chat_workspace(args.workspace)
    except ValueError as exc:
        parser.error(str(exc))
    if args.judge_artifacts is not None:
        requested_artifacts = args.judge_artifacts.expanduser().resolve(strict=False)
        if requested_artifacts.exists():
            parser.error("--judge-artifacts must name a new path")
        if not requested_artifacts.parent.is_dir():
            parser.error("--judge-artifacts parent must exist")
        args.judge_artifacts = requested_artifacts
    skills_root = args.skills_root.expanduser().resolve()
    if not skills_root.is_dir():
        parser.error("--skills-root must be an existing directory")
    try:
        routing_mode = "deterministic" if args.judge else args.routing_mode
        session_id = f"session-{uuid.uuid4().hex}"
        trace_root = workspace / ".merlin" / "chat" / session_id
        if args.judge:
            trace_root.mkdir(parents=True, exist_ok=False)
            backend = OfflineJudgeBackend()
            semantic_router = None
            executable = None
        else:
            executable, cli_version = detect_codex_runtime(
                args.executable,
                version_override=args.cli_version,
            )
            backend = CodexChatBackend(
                executable=executable,
                cli_version=cli_version,
                workspace=workspace,
                trace_root=trace_root,
                model_id=args.model,
                effort=args.effort,
                timeout_s=args.timeout,
                live_hook_config=(
                    HarnessXLiveHookConfig(
                        project_root=REPO_ROOT,
                        python_executable=Path(sys.executable),
                    )
                    if args.harnessx_live_hooks
                    else None
                ),
            )
            semantic_router = (
                CodexCliSemanticRouter(
                    executable=executable,
                    cli_version=cli_version,
                    workspace=workspace,
                    trace_root=trace_root,
                    model_id=args.model,
                    effort=args.routing_effort,
                    timeout_s=args.routing_timeout,
                )
                if routing_mode == "semantic"
                else None
            )
        base_library = FileSkillLibrary(skills_root)
        library: FileSkillLibrary = base_library
        creation_evidence = None
        repair_evidence = None
        repair_portfolio = None
        promotion_evidence_path = None
        requested_promotion = (
            DEFAULT_PROMOTION_EVIDENCE if args.judge and args.promotion_evidence is None
            else args.promotion_evidence
        )
        if requested_promotion is not None:
            promotion_evidence_path = requested_promotion.expanduser().resolve(strict=True)
            library, creation_evidence = load_verified_promotion_overlay(
                base_library=library,
                evidence_path=promotion_evidence_path,
                overlay_root=trace_root / "library-overlay",
            )
        if args.repair_evidence is not None:
            repair_evidence = load_verified_repair_summary(args.repair_evidence)
        portfolio_repairs = tuple(
            repair
            for repair in (
                repair_evidence,
                load_verified_repair_summary(args.repair_family2_evidence)
                if args.repair_family2_evidence is not None
                else None,
            )
            if repair is not None
        )
        if portfolio_repairs:
            repair_portfolio = build_verified_repair_portfolio(portfolio_repairs)
        session = TheKingChatSession(
            workspace=workspace,
            library=library,
            backend=backend,
            trace_root=trace_root,
            top_k=args.top_k,
            routing_mode=routing_mode,
            semantic_router=semantic_router,
            skill_bundle_paths=getattr(library, "verified_bundle_paths", {}),
            harnessx_shadow=HarnessXChatShadow(
                runtime=make_default_harnessx_runtime(
                    system_prompt_suffix=(
                        "\nHarnessX shadow candidate: preserve evidence boundaries "
                        "and require gated lifecycle changes."
                    ),
                    max_user_content_chars=65_536,
                ),
                trace_root=trace_root,
            ),
        )
        learning_controller = (
            LiveSkillCreationController(
                session=session,
                base_library=base_library,
                codex_executable=executable,
                model_id=args.model,
                effort=args.effort,
            )
            if not args.judge
            and executable is not None
            and skills_root == DEFAULT_SKILLS_ROOT.resolve()
            else None
        )
        autonomy_governor = (
            ConsentGatedHarnessGovernor(
                trace_root=trace_root,
                approval_mode=(
                    "strict"
                    if args.autonomy_mode in {"strict", "consent"}
                    else "managed"
                ),
            )
            if not args.judge and args.autonomy_mode != "off"
            else None
        )
    except ValueError as exc:
        parser.error(str(exc))
    repl_kwargs: dict[str, object] = {}
    terminal_enabled = args.ui == "terminal" or (
        args.ui == "auto" and sys.stdin.isatty() and sys.stdout.isatty()
    )
    terminal_ui = None
    if terminal_enabled:
        terminal_ui = TerminalUI(
            model=args.model,
            effort=args.effort,
            mode="offline judge" if args.judge else "live",
            autonomy="off" if args.judge else args.autonomy_mode,
            workspace=str(workspace),
            color=not args.no_color and sys.stdout.isatty(),
        )
        repl_kwargs["output_fn"] = terminal_ui.output
        if not args.golden:
            repl_kwargs["input_fn"] = terminal_ui.input
    if workspace_created:
        if terminal_ui is not None:
            terminal_ui.output(f"workspace created · {workspace}")
        else:
            print(f"Created private chat workspace: {workspace}")
    if args.golden:
        scripted_inputs = iter(
            (
                "Diagnose and safely recover this overloaded skill library.",
                "/quit",
            )
        )

        def golden_input(prompt: str) -> str:
            value = next(scripted_inputs)
            print(f"{prompt}{value}")
            return value

        repl_kwargs["input_fn"] = golden_input
    judge_artifact_root = args.judge_artifacts
    if args.golden and judge_artifact_root is None:
        judge_artifact_root = workspace / f"judge-artifacts-{session_id.removeprefix('session-')[:12]}"
    return run_repl(
        session,
        creation_evidence=creation_evidence,
        repair_evidence=repair_evidence,
        repair_portfolio=repair_portfolio,
        promotion_evidence_path=promotion_evidence_path,
        learning_controller=learning_controller,
        autonomy_governor=autonomy_governor,
        judge_mode=args.judge,
        judge_artifact_root=judge_artifact_root,
        **repl_kwargs,
    )


if __name__ == "__main__":
    raise SystemExit(main())
