"""Compose the existing model-free governance slices into one local preflight.

This runner performs no provider or network call. It reuses the Build Week
lifecycle recovery, the controlled management-policy comparison, and the
typed HarnessX runtime demo, then binds their artifacts into one new-only
evidence envelope. The output is a readiness artifact for a later bounded
account-auth canary, not provider-backed performance evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.merlin_harness.account_resource_governance import AccountResourceLedger

from .run_harnessx_typed_runtime_demo import run_harnessx_typed_runtime_demo
from .run_lifecycle_recovery_demo import run_lifecycle_recovery_demo
from .run_management_policy_comparison import build_controlled_comparison


class ManagedLibraryLoopValidationError(ValueError):
    """Raised when a persisted Managed Library Loop artifact is not trustworthy."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _file_record(path: Path, root: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _check(name: str, passed: bool, evidence: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "evidence": evidence}


def _require(
    condition: bool,
    message: str,
) -> None:
    if not condition:
        raise ManagedLibraryLoopValidationError(message)


def validate_managed_library_loop(output_dir: str | Path) -> dict[str, Any]:
    """Validate a persisted loop without executing providers or changing files."""

    output = Path(output_dir).expanduser().resolve()
    report_path = output / "managed_library_loop.json"
    _require(report_path.is_file(), f"missing report: {report_path}")
    _require(not report_path.is_symlink(), "report must not be a symbolic link")

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManagedLibraryLoopValidationError(
            f"unreadable report: {report_path}"
        ) from exc
    _require(isinstance(report, dict), "report root must be an object")

    declared_report_hash = report.get("report_sha256")
    _require(
        isinstance(declared_report_hash, str) and len(declared_report_hash) == 64,
        "report_sha256 must be a 64-character string",
    )
    unhashed = dict(report)
    unhashed.pop("report_sha256", None)
    actual_report_hash = hashlib.sha256(_canonical_bytes(unhashed)).hexdigest()
    _require(
        declared_report_hash == actual_report_hash,
        "report_sha256 mismatch",
    )

    execution = report.get("execution")
    _require(
        execution
        == {
            "mode": "model_free",
            "provider_calls_performed": 0,
            "api_calls_performed": 0,
            "credentials_read": False,
        },
        "execution safety contract mismatch",
    )

    components = report.get("components")
    _require(
        isinstance(components, list) and len(components) == 3,
        "exactly three component records are required",
    )
    seen_paths: set[str] = set()
    for index, component in enumerate(components):
        _require(isinstance(component, dict), f"component[{index}] must be an object")
        relative = component.get("path")
        _require(
            isinstance(relative, str) and bool(relative),
            f"component[{index}].path must be a non-empty string",
        )
        relative_path = Path(relative)
        _require(
            not relative_path.is_absolute() and ".." not in relative_path.parts,
            f"component[{index}] path escapes output directory",
        )
        _require(relative not in seen_paths, f"duplicate component path: {relative}")
        seen_paths.add(relative)

        target = output / relative_path
        try:
            resolved_target = target.resolve(strict=True)
        except OSError as exc:
            raise ManagedLibraryLoopValidationError(
                f"missing component: {relative}"
            ) from exc
        _require(not target.is_symlink(), f"component must not be a symlink: {relative}")
        _require(
            resolved_target.is_relative_to(output),
            f"component path escapes output directory: {relative}",
        )
        _require(resolved_target.is_file(), f"component is not a file: {relative}")
        data = resolved_target.read_bytes()
        _require(
            component.get("bytes") == len(data),
            f"component byte count mismatch: {relative}",
        )
        _require(
            component.get("sha256") == hashlib.sha256(data).hexdigest(),
            f"component sha256 mismatch: {relative}",
        )

    checks = report.get("readiness_checks")
    _require(isinstance(checks, list) and bool(checks), "readiness_checks are required")
    check_names: set[str] = set()
    for index, check in enumerate(checks):
        _require(isinstance(check, dict), f"readiness_check[{index}] must be an object")
        name = check.get("name")
        _require(
            isinstance(name, str) and bool(name),
            f"readiness_check[{index}].name must be a non-empty string",
        )
        _require(name not in check_names, f"duplicate readiness check: {name}")
        check_names.add(name)
        _require(
            isinstance(check.get("passed"), bool),
            f"readiness_check[{index}].passed must be boolean",
        )
    ready = all(check["passed"] for check in checks)
    _require(
        report.get("ready_for_account_auth_canary") is ready,
        "ready_for_account_auth_canary does not match readiness checks",
    )

    ledger = report.get("account_resource_ledger")
    _require(isinstance(ledger, dict), "account_resource_ledger must be an object")
    decision = ledger.get("decision")
    _require(isinstance(decision, dict), "resource decision must be an object")
    _require(
        ledger.get("unit") == "provider_turn"
        and ledger.get("cash_cost_claimed") is False
        and decision.get("authorized_provider_turns") == 0
        and decision.get("observation_count") == 0,
        "empty account resource ledger must fail closed",
    )

    canary = report.get("account_auth_canary_contract")
    _require(isinstance(canary, dict), "account_auth_canary_contract must be an object")
    _require(
        canary.get("status") == "planned_not_executed"
        and canary.get("auth_mode") == "account"
        and canary.get("api_key_allowed") is False
        and canary.get("initial_max_provider_turns") == 2
        and canary.get("cash_savings_claim_allowed") is False,
        "account-auth canary safety contract mismatch",
    )

    return {
        "valid": True,
        "report_sha256": actual_report_hash,
        "component_count": len(components),
        "ready_for_account_auth_canary": ready,
        "provider_calls_performed": 0,
    }


def run_managed_library_loop(
    output_dir: str | Path,
    *,
    account_backend: str = "codex-cli",
    account_model: str = "default",
    account_effort: str = "high",
) -> dict[str, Any]:
    """Run the zero-provider-call integration preflight and persist its report."""

    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)

    lifecycle_root = output / "lifecycle"
    management_root = output / "management"
    harnessx_root = output / "harnessx"
    lifecycle = run_lifecycle_recovery_demo(lifecycle_root)
    management = build_controlled_comparison(
        management_root,
        codex_smoke_trace=None,
    )
    harnessx = run_harnessx_typed_runtime_demo(harnessx_root)

    account_ledger = AccountResourceLedger()
    resource_decision = account_ledger.decide()

    comparison = management["comparison"]
    assert isinstance(comparison, dict)
    reports = comparison["reports"]
    arms = [report["output"]["arm"] for report in reports]
    recovered = lifecycle["conditions"].get("Lifecycle recovered")

    checks = [
        _check(
            "controlled_lifecycle_promoted",
            lifecycle["promotion"]["accepted"] is True
            and isinstance(recovered, dict)
            and recovered["passed"] == 9
            and recovered["pi_m"] == 0.0,
            "expected accepted COW recovery with 9/10 pass and pi_m=0",
        ),
        _check(
            "management_arms_share_frozen_contract",
            arms == ["M0", "M1", "M2-H", "M2-K"]
            and isinstance(comparison.get("common_contract_sha256"), str),
            f"arms={','.join(arms)}",
        ),
        _check(
            "harnessx_hook_contract_complete",
            harnessx["hook_coverage_count"] == 8
            and harnessx["variant_chain"]["round_trip_manifest_equal"] is True,
            (
                f"hooks={harnessx['hook_coverage_count']}; "
                f"round_trip={harnessx['variant_chain']['round_trip_manifest_equal']}"
            ),
        ),
        _check(
            "harnessx_selective_approval_boundary",
            harnessx["low_risk_reversible_change"]["resolution"]
            == "candidate_harness_promoted"
            and harnessx["high_risk_change"]["resolution"]
            == "approval_required_parent_retained",
            (
                f"low={harnessx['low_risk_reversible_change']['resolution']}; "
                f"high={harnessx['high_risk_change']['resolution']}"
            ),
        ),
        _check(
            "account_resource_budget_fails_closed_without_live_evidence",
            resource_decision.authorized_provider_turns == 0
            and resource_decision.observation_count == 0,
            resource_decision.reason,
        ),
        _check(
            "zero_provider_calls",
            True,
            "all three component runners are deterministic model-free fixtures",
        ),
    ]
    ready = all(check["passed"] for check in checks)

    component_files = [
        lifecycle_root / "lifecycle_recovery.json",
        management_root / "management_policy_comparison.json",
        harnessx_root / "harnessx_typed_runtime.json",
    ]
    report: dict[str, Any] = {
        "schema_version": 1,
        "title": "Merlin Managed Library Loop v1",
        "scope": (
            "Model-free integration preflight over three existing controlled "
            "evidence lanes. It is not one shared causal trajectory and is not "
            "provider-backed task-performance or actual-invocation evidence."
        ),
        "execution": {
            "mode": "model_free",
            "provider_calls_performed": 0,
            "api_calls_performed": 0,
            "credentials_read": False,
        },
        "components": [_file_record(path, output) for path in component_files],
        "readiness_checks": checks,
        "ready_for_account_auth_canary": ready,
        "account_resource_ledger": {
            "unit": "provider_turn",
            "cash_cost_claimed": False,
            "decision": asdict(resource_decision),
        },
        "account_auth_canary_contract": {
            "status": "planned_not_executed",
            "backend": account_backend,
            "auth_mode": "account",
            "model": account_model,
            "effort": account_effort,
            "api_key_allowed": False,
            "initial_max_provider_turns": 2,
            "same_model_effort_and_quota_window_required": True,
            "same_verifier_epoch_required": True,
            "actual_invocation_required_before_pi_metrics": True,
            "cash_savings_claim_allowed": False,
        },
        "headline_controlled_result": {
            "overloaded_passed": lifecycle["conditions"]["Overloaded library"]["passed"],
            "recovered_passed": recovered["passed"] if isinstance(recovered, dict) else None,
            "overloaded_pi_m": lifecycle["conditions"]["Overloaded library"]["pi_m"],
            "recovered_pi_m": recovered["pi_m"] if isinstance(recovered, dict) else None,
            "management_arms": arms,
            "harnessx_hook_count": harnessx["hook_coverage_count"],
        },
    }
    report["report_sha256"] = hashlib.sha256(_canonical_bytes(report)).hexdigest()
    (output / "managed_library_loop.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run Merlin model-free Managed Library Loop v1 preflight."
    )
    parser.add_argument(
        "--output",
        default="/private/tmp/merlin-managed-library-loop-v1",
        help="New output directory; existing paths are refused.",
    )
    parser.add_argument(
        "--verify-existing",
        help="Validate an existing output directory without changing it.",
    )
    parser.add_argument("--account-backend", default="codex-cli")
    parser.add_argument("--account-model", default="default")
    parser.add_argument("--account-effort", default="high")
    args = parser.parse_args(argv)
    if args.verify_existing:
        try:
            validation = validate_managed_library_loop(args.verify_existing)
        except ManagedLibraryLoopValidationError as exc:
            parser.error(str(exc))
        print("Merlin Managed Library Loop v1 validation")
        print("valid=True")
        print(f"components={validation['component_count']}")
        print("provider_calls=0")
        print(f"report_sha256={validation['report_sha256']}")
        return 0

    try:
        report = run_managed_library_loop(
            args.output,
            account_backend=args.account_backend,
            account_model=args.account_model,
            account_effort=args.account_effort,
        )
    except FileExistsError:
        parser.error(f"output path already exists: {Path(args.output).resolve()}")
    headline = report["headline_controlled_result"]
    print("Merlin Managed Library Loop v1")
    print("provider_calls=0")
    print(
        f"controlled_recovery={headline['overloaded_passed']}/10"
        f"->{headline['recovered_passed']}/10"
    )
    print(
        f"controlled_pi_m={headline['overloaded_pi_m']:.0%}"
        f"->{headline['recovered_pi_m']:.0%}"
    )
    print(f"ready_for_account_auth_canary={report['ready_for_account_auth_canary']}")
    print(f"saved -> {Path(args.output).resolve()}")
    return 0 if report["ready_for_account_auth_canary"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
