"""Run the deterministic duplicate-skill merge promotion/rollback demo."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from src.merlin_harness.management import content_sha256
from src.merlin_harness.models import LifecycleStatus, SkillArtifact, SkillStep
from src.merlin_harness.skill_merge import (
    MERGE_TOMBSTONE_KEY,
    MergeCase,
    MergeCaseResult,
    MergeDiagnosis,
    run_skill_merge,
)
from src.merlin_harness.skill_repair import skill_library_snapshot_sha256
from src.merlin_harness.verifier_trust import VerifierTrustLevel, VerifierTrustProfile


CANONICAL_ID = "json-report-writer"
REDUNDANT_ID = "json-report-exporter"
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent
    / "results"
    / "skill_merge_v1"
    / "skill_merge.json"
)


def _output_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _skill(skill_id: str, *, behavior: str) -> SkillArtifact:
    return SkillArtifact(
        id=skill_id,
        name=skill_id.replace("-", " ").title(),
        description=f"Write the governed JSON report via {skill_id}",
        trigger="write governed json report",
        do_not_use_when=["plain text requested"],
        steps=[SkillStep(id="run", description="write deterministic report")],
        validators=["report-contract-v1"],
        expected_artifacts=["report.json"],
        provenance_trace_ids=[f"fixture-origin-{skill_id}"],
        status=LifecycleStatus.ACTIVE,
        metadata={"controlled_fixture": True, "behavior": behavior},
    )


EQUIVALENCE_CASES = (
    MergeCase("ascii-report", "equivalence", "ascii-report-v1"),
    MergeCase("korean-report", "equivalence", "korean-report-v1"),
)
REGRESSION_CASES = (
    MergeCase("protected-summary", "library_regression", "protected-summary-v1"),
    MergeCase("protected-index", "library_regression", "protected-index-v1"),
)


class _DemoEvaluator:
    def evaluate_skill(self, target, cases):
        behavior = target.metadata["behavior"]
        return tuple(
            MergeCaseResult(
                case_id=case.case_id,
                verifier_id=case.verifier_id,
                passed=True,
                score=1.0,
                output_sha256=_output_hash(f"{case.case_id}:{behavior}"),
                evidence="controlled deterministic equivalence verifier",
            )
            for case in cases
        )

    def evaluate_library(self, skills, cases):
        return tuple(
            MergeCaseResult(
                case_id=case.case_id,
                verifier_id=case.verifier_id,
                passed=True,
                score=1.0,
                output_sha256=_output_hash(f"{case.case_id}:stable"),
                evidence="controlled same-verifier library regression",
            )
            for case in cases
        )


def _profiles() -> dict[str, VerifierTrustProfile]:
    return {
        case.verifier_id: VerifierTrustProfile(
            verifier_id=case.verifier_id,
            level=VerifierTrustLevel.HIDDEN_ORACLE,
            deterministic=True,
            requirement_ids=(case.case_id,),
            covered_requirement_ids=(case.case_id,),
            behavioral_assertion_count=2,
            author_independent_from_candidate=True,
            hidden_from_reviser=True,
            provenance_sha256=f"{index:x}" * 64,
        )
        for index, case in enumerate(EQUIVALENCE_CASES + REGRESSION_CASES, start=1)
    }


def build_skill_merge_demo_report() -> dict[str, Any]:
    library = (
        _skill(CANONICAL_ID, behavior="same-output"),
        _skill(REDUNDANT_ID, behavior="same-output"),
        _skill("unrelated-csv-writer", behavior="csv-output"),
    )
    diagnosis = MergeDiagnosis(
        canonical_skill_id=CANONICAL_ID,
        redundant_skill_id=REDUNDANT_ID,
        library_snapshot_sha256=skill_library_snapshot_sha256(library),
        raw_trace_sha256s=("a" * 64, "b" * 64),
        observed_task_ids=("ascii-report", "korean-report", "protected-summary"),
        overlapping_exposure_task_ids=("ascii-report", "korean-report"),
        overlap_selection_count=2,
        overlap_invocation_count=2,
        actual_invocation_evidence_complete=True,
    )
    result = run_skill_merge(
        diagnosis=diagnosis,
        library=library,
        equivalence_cases=EQUIVALENCE_CASES,
        regression_cases=REGRESSION_CASES,
        evaluator=_DemoEvaluator(),
        verifier_profiles=_profiles(),
    ).to_dict()
    body: dict[str, Any] = {
        "schema_version": 1,
        "demo_id": "controlled-duplicate-skill-merge-v1",
        "status": "pass" if result["merged"] else "fail",
        "result": result,
        "summary": {
            "canonical_skill_id": CANONICAL_ID,
            "redundant_skill_id": REDUNDANT_ID,
            "equivalence_cases": len(EQUIVALENCE_CASES),
            "regression_cases": len(REGRESSION_CASES),
            "gates_passed": sum(gate["passed"] for gate in result["gates"]),
            "gates_total": len(result["gates"]),
            "canonical_status": next(
                skill["status"]
                for skill in result["resolved_library"]
                if skill["id"] == CANONICAL_ID
            ),
            "redundant_status": next(
                skill["status"]
                for skill in result["resolved_library"]
                if skill["id"] == REDUNDANT_ID
            ),
            "tombstone_present": MERGE_TOMBSTONE_KEY
            in next(
                skill["metadata"]
                for skill in result["resolved_library"]
                if skill["id"] == REDUNDANT_ID
            ),
        },
        "claim_boundary": {
            "controlled_deterministic_fixture": True,
            "actual_provider_trace_evidence": False,
            "model_execution": False,
            "general_merge_success_rate": False,
            "physical_artifact_deletion": False,
            "copy_on_write_contract_exercised": True,
            "same_verifier_contract_exercised": True,
        },
    }
    return {**body, "report_sha256": content_sha256(body)}


def validate_skill_merge_demo_report(report: dict[str, Any]) -> None:
    if set(report) != {
        "schema_version",
        "demo_id",
        "status",
        "result",
        "summary",
        "claim_boundary",
        "report_sha256",
    }:
        raise ValueError("merge demo report schema is invalid")
    if (
        report["schema_version"] != 1
        or report["demo_id"] != "controlled-duplicate-skill-merge-v1"
        or report["status"] != "pass"
    ):
        raise ValueError("merge demo report identity or status is invalid")
    summary = report["summary"]
    if summary != {
        "canonical_skill_id": CANONICAL_ID,
        "redundant_skill_id": REDUNDANT_ID,
        "equivalence_cases": 2,
        "regression_cases": 2,
        "gates_passed": 9,
        "gates_total": 9,
        "canonical_status": "active",
        "redundant_status": "retired",
        "tombstone_present": True,
    }:
        raise ValueError("merge demo report summary is invalid")
    if report["claim_boundary"] != {
        "controlled_deterministic_fixture": True,
        "actual_provider_trace_evidence": False,
        "model_execution": False,
        "general_merge_success_rate": False,
        "physical_artifact_deletion": False,
        "copy_on_write_contract_exercised": True,
        "same_verifier_contract_exercised": True,
    }:
        raise ValueError("merge demo claim boundary is invalid")
    result = report["result"]
    if (
        not isinstance(result, dict)
        or result.get("merged") is not True
        or result.get("lifecycle_action") != "merge"
        or len(result.get("gates", [])) != 9
        or not all(gate.get("passed") is True for gate in result["gates"])
    ):
        raise ValueError("merge demo core result is invalid")
    body = {key: value for key, value in report.items() if key != "report_sha256"}
    if report["report_sha256"] != content_sha256(body):
        raise ValueError("merge demo report hash is invalid")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    output = args.output.expanduser().resolve(strict=False)
    if output.exists() or output.is_symlink():
        parser.error("merge demo output must be new-only")
    report = build_skill_merge_demo_report()
    validate_skill_merge_demo_report(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError:
        parser.error("merge demo output must be new-only")
    print("Merlin controlled duplicate-skill merge")
    print(f"merged={str(report['result']['merged']).lower()}")
    print("gates=9/9")
    print("canonical=active")
    print("redundant=retired_tombstone")
    print("actual_provider_trace_evidence=false")
    print(f"saved -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
