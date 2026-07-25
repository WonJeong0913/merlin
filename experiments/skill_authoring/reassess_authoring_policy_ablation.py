"""Reassess the frozen 12-run ablation without new provider calls.

The original exact-byte report is preserved. This audit re-hashes every
candidate and execution workspace, accepts safe YAML plain scalars, uses
semantic JSON equality where the task contract specified JSON content but not
Unicode escaping, and excludes the under-specified Markdown top-level shape
from primary behavioral comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from experiments.skill_authoring.run_live_authoring_policy_ablation import (
    FrozenTaskSuite,
    frozen_task_suites,
)
from src.merlin_harness.managed_creation import validate_portable_candidate
from src.merlin_harness.skill_authoring_policy import CONTROL_ARM, POLICY_ARM


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _workspace_manifest(root: Path) -> list[dict[str, object]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path.read_bytes()),
        }
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    ]


def _verify_candidate(run_root: Path, safe_run: dict[str, object]) -> Path:
    quarantine_root = run_root / "quarantine"
    manifest_path = quarantine_root / "quarantine_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_manifest = safe_run["candidate"]["manifest_sha256"]
    if manifest.get("manifest_sha256") != expected_manifest:
        raise ValueError("candidate manifest differs from safe report")
    body = {key: value for key, value in manifest.items() if key not in {"schema_version", "manifest_sha256"}}
    if _sha256(_canonical_json(body).encode("utf-8")) != expected_manifest:
        raise ValueError("candidate manifest content hash is invalid")
    candidate_root = quarantine_root / "candidate" / safe_run["task"]
    expected_paths = set()
    for record in manifest["files"]:
        path = candidate_root / record["path"]
        raw = path.read_bytes()
        if len(raw) != record["bytes"] or _sha256(raw) != record["sha256"]:
            raise ValueError("candidate file differs from quarantine manifest")
        expected_paths.add(record["path"])
    actual_paths = {
        path.relative_to(candidate_root).as_posix()
        for path in candidate_root.rglob("*")
        if path.is_file()
    }
    if actual_paths != expected_paths:
        raise ValueError("candidate file set differs from quarantine manifest")
    if _sha256((candidate_root / "SKILL.md").read_bytes()) != safe_run["candidate"]["skill_md_sha256"]:
        raise ValueError("candidate SKILL.md differs from safe report")
    return candidate_root


def _semantic_case_results(
    run_root: Path,
    suite: FrozenTaskSuite,
) -> tuple[list[dict[str, object]], str]:
    execution_path = run_root / "execution" / "isolated_execution_report.json"
    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    recorded = {item["case_id"]: item for item in execution["cases"]}
    results = []
    for case in suite.cases:
        workspace = run_root / "execution" / "cases" / case.case_id / "workspace"
        manifest = _workspace_manifest(workspace)
        if _sha256(_canonical_json(manifest).encode("utf-8")) != recorded[case.case_id]["workspace_manifest_sha256"]:
            raise ValueError("execution workspace differs from original report")
        expected_path, expected_text = case.expected_files[0]
        actual_path = workspace / expected_path
        try:
            actual_value = json.loads(actual_path.read_text(encoding="utf-8"))
            expected_value = json.loads(expected_text)
            semantic_match = actual_value == expected_value
            actual_top_level = type(actual_value).__name__
            expected_top_level = type(expected_value).__name__
        except (OSError, UnicodeError, json.JSONDecodeError):
            semantic_match = False
            actual_top_level = "invalid_or_missing"
            expected_top_level = "unknown"
        results.append(
            {
                "case_id": case.case_id,
                "split": case.split,
                "semantic_json_match": semantic_match,
                "actual_top_level": actual_top_level,
                "expected_top_level": expected_top_level,
                "workspace_manifest_sha256": recorded[case.case_id]["workspace_manifest_sha256"],
            }
        )
    return results, _sha256(execution_path.read_bytes())


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _aggregate(runs: list[dict[str, object]]) -> dict[str, object]:
    by_arm = {}
    for arm in (CONTROL_ARM, POLICY_ARM):
        selected = [item for item in runs if item["arm"] == arm]
        eligible = [item for item in selected if item["contract_eligible"]]
        by_arm[arm] = {
            "run_count": len(selected),
            "eligible_run_count": len(eligible),
            "excluded_contract_run_count": len(selected) - len(eligible),
            "format_pass_rate": _mean([float(item["metrics"]["format_gate"]) for item in selected]),
            "safety_pass_rate": _mean([float(item["metrics"]["safety_gate"]) for item in selected]),
            "mean_target_pass_rate": _mean([float(item["metrics"]["target_pass_rate"]) for item in eligible]),
            "mean_held_out_pass_rate": _mean([float(item["metrics"]["held_out_pass_rate"]) for item in eligible]),
            "mean_negative_route_accuracy": _mean([float(item["metrics"]["negative_route_accuracy"]) for item in eligible]),
            "promotion_rate": _mean([float(item["metrics"]["promotion"]) for item in eligible]),
            "mean_input_tokens": _mean([float(item["generation"]["input_tokens"]) for item in selected]),
            "mean_output_tokens": _mean([float(item["generation"]["output_tokens"]) for item in selected]),
            "mean_generation_latency_seconds": _mean([float(item["generation"]["generation_latency_seconds"]) for item in selected]),
            "mean_candidate_bytes": _mean([float(item["candidate"]["bytes"]) for item in selected]),
        }
    pairs = []
    indexed = {(item["repeat"], item["task"], item["arm"]): item for item in runs}
    for key, control in sorted(indexed.items()):
        repeat, task, arm = key
        if arm != CONTROL_ARM or not control["contract_eligible"]:
            continue
        treatment = indexed[(repeat, task, POLICY_ARM)]
        pairs.append(
            {
                "repeat": repeat,
                "task": task,
                "promotion_delta": int(treatment["metrics"]["promotion"]) - int(control["metrics"]["promotion"]),
                "target_pass_rate_delta": treatment["metrics"]["target_pass_rate"] - control["metrics"]["target_pass_rate"],
                "held_out_pass_rate_delta": treatment["metrics"]["held_out_pass_rate"] - control["metrics"]["held_out_pass_rate"],
                "negative_route_accuracy_delta": treatment["metrics"]["negative_route_accuracy"] - control["metrics"]["negative_route_accuracy"],
            }
        )
    return {"by_arm": by_arm, "paired_deltas": pairs}


def reassess(original_report: Path, raw_root: Path) -> dict[str, object]:
    original_bytes = original_report.read_bytes()
    original = json.loads(original_bytes)
    if original.get("recorded_runs") != 12 or len(original.get("runs", [])) != 12:
        raise ValueError("original report is not the frozen 12-run result")
    suites = {item.contract.candidate_skill_id: item for item in frozen_task_suites()}
    runs = []
    for safe_run in original["runs"]:
        run_root = raw_root / safe_run["run_id"]
        suite = suites[safe_run["task"]]
        candidate_root = _verify_candidate(run_root, safe_run)
        portable = validate_portable_candidate(candidate_root, safe_run["task"])
        format_pass = next(item for item in portable if item.name == "G1_format").passed
        safety_pass = next(item for item in portable if item.name == "G2_safety").passed
        quarantine = json.loads((run_root / "quarantine" / "quarantine_report.json").read_text(encoding="utf-8"))
        safety_pass = safety_pass and all(item["passed"] for item in quarantine["gates"])
        cases, execution_sha256 = _semantic_case_results(run_root, suite)
        contract_eligible = safe_run["task"] != "extract-markdown-links"
        contract_issue = None if contract_eligible else "top_level_json_shape_not_frozen_before_generation"
        target = [item for item in cases if item["split"] == "target"]
        held_out = [item for item in cases if item["split"] == "held_out"]
        target_rate = _mean([float(item["semantic_json_match"]) for item in target])
        held_out_rate = _mean([float(item["semantic_json_match"]) for item in held_out])
        original_metrics = safe_run["metrics"]
        promotion = bool(
            contract_eligible
            and format_pass
            and safety_pass
            and target_rate == 1.0
            and held_out_rate == 1.0
            and original_metrics["positive_route_accuracy"] == 1.0
            and original_metrics["negative_route_accuracy"] == 1.0
            and original_metrics["off_task_artifact_count"] == 0
        )
        runs.append(
            {
                "run_id": safe_run["run_id"],
                "repeat": safe_run["repeat"],
                "task": safe_run["task"],
                "arm": safe_run["arm"],
                "task_contract_sha256": safe_run["task_contract_sha256"],
                "prompt_sha256": safe_run["prompt_sha256"],
                "policy_sha256": safe_run["policy_sha256"],
                "contract_eligible": contract_eligible,
                "contract_issue": contract_issue,
                "generation": safe_run["generation"],
                "candidate": safe_run["candidate"],
                "metrics": {
                    "format_gate": format_pass,
                    "safety_gate": safety_pass,
                    "target_pass_rate": target_rate,
                    "held_out_pass_rate": held_out_rate,
                    "positive_route_accuracy": original_metrics["positive_route_accuracy"],
                    "negative_route_accuracy": original_metrics["negative_route_accuracy"],
                    "off_task_artifact_count": original_metrics["off_task_artifact_count"],
                    "promotion": promotion,
                },
                "semantic_cases": cases,
                "execution_report_sha256": execution_sha256,
            }
        )
    return {
        "schema_version": 1,
        "experiment": "authoring-policy-ablation-v1-reassessed",
        "original_report_sha256": _sha256(original_bytes),
        "original_recorded_runs": 12,
        "new_provider_calls": 0,
        "runs": runs,
        "aggregate": _aggregate(runs),
        "contract_findings": {
            "frontmatter": "safe YAML plain scalars are valid Agent Skills input; the previous quote-only validator was over-restrictive",
            "unicode": "task contracts did not require ensure_ascii=False, so semantic JSON equality replaces literal Unicode byte equality",
            "markdown_links": "top-level JSON shape was not frozen; all four runs are excluded from primary behavioral comparison",
        },
        "evidence_boundary": {
            "candidate_and_workspace_hashes_reverified": True,
            "candidate_regenerated": False,
            "candidate_reexecuted": False,
            "new_provider_calls": False,
            "post_hoc_contract_relaxation_hidden": False,
            "broad_generalization_claim": False,
            "live_library_mutated": False,
            "submission_package_mutated": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-report", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.expanduser().resolve(strict=False)
    if output.exists():
        parser.error(f"refusing to overwrite output: {output}")
    report = reassess(
        args.original_report.expanduser().resolve(strict=True),
        args.raw_root.expanduser().resolve(strict=True),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print("Merlin authoring-policy ablation reassessment")
    print("new_provider_calls=0")
    print(f"reassessed_runs={len(report['runs'])}")
    print(f"saved={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
