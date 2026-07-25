"""Create or verify the frozen full-87 paired M3-K evaluation schedule."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from enum import Enum
from pathlib import Path

from experiments.skillsbench.harness_policy_evaluation import (
    VariantRole,
    build_cells,
    build_full87_m3k_contract,
)
from src.merlin_harness.management import content_sha256


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPLIT = REPO_ROOT / "experiments/skillsbench/split-manifest.json"
DEFAULT_SCALE = REPO_ROOT / "experiments/skillsbench/library-scale-manifest.json"


class M3KManifestError(ValueError):
    pass


def _json_ready(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_ready(item) for item in value]
    return value


def build_manifest(*, split_manifest: Path, library_scale_manifest: Path) -> dict:
    contract = build_full87_m3k_contract(
        split_manifest=split_manifest,
        library_scale_manifest=library_scale_manifest,
        experiment_id="m3k-full87-contract-v1",
        base_agent_id="merlin-agent",
        base_agent_version="1",
        backend="strict-container-agent-executor-unbound",
        model_id="gpt-5.6-terra",
        effort="high",
        tools=("fixed-container-exec",),
        budget_id="m3k-full87-budget-v1",
        repeats=3,
    )
    cells = build_cells(contract)
    paired_cells = [
        {
            "trajectory_id": f"{role.value}:{cell.cell_id}",
            "pair_id": cell.cell_id,
            "variant_role": role.value,
            **_json_ready(asdict(cell)),
        }
        for role in VariantRole
        for cell in cells
    ]
    split_counts = {
        split: sum(item["split"] == split for item in _json_ready([asdict(task) for task in contract.tasks]))
        for split in ("held_in", "held_out", "regression")
    }
    payload = {
        "schema_version": 1,
        "experiment_id": contract.experiment_id,
        "status": "not_run",
        "evaluation_contract": _json_ready(asdict(contract)),
        "evaluation_contract_sha256": contract.contract_sha256,
        "summary": {
            "task_count": len(contract.tasks),
            "split_task_counts": split_counts,
            "repeats": contract.repeats,
            "cells_per_variant": len(cells),
            "variant_count": len(VariantRole),
            "expected_trajectories": len(paired_cells),
        },
        "paired_cells": paired_cells,
        "proposal_binding": {
            "proposal_id": None,
            "parent_variant_id": None,
            "parent_variant_sha256": None,
            "candidate_variant_id": None,
            "candidate_variant_sha256": None,
            "binding_status": "required_before_execution",
        },
        "execution_gate": {
            "execution_allowed": False,
            "reasons": [
                "parent_and_candidate_variant_hashes_are_unbound",
                "strict_tool_controlled_container_executor_evidence_not_supplied",
            ],
            "required_result_contract": (
                "every parent/candidate trajectory must bind this evaluation contract, "
                "the later frozen variant hashes, verifier/task hashes, unique raw trace, "
                "and complete actual invocation evidence"
            ),
        },
        "regression_semantics": {
            "candidate_task_count": split_counts["regression"],
            "final_gate_subset": (
                "tasks whose parent harness passes on every repeat; candidate still runs "
                "the complete regression-candidate schedule"
            ),
        },
        "claim_boundary": {
            "schedule_only": True,
            "model_execution": False,
            "trajectory_results": False,
            "full87_result": False,
            "provider_native_invocation": False,
        },
    }
    payload["manifest_sha256"] = content_sha256(payload)
    return payload


def validate_manifest(payload: dict) -> None:
    if payload.get("schema_version") != 1 or payload.get("status") != "not_run":
        raise M3KManifestError("unsupported or non-not-run M3-K manifest")
    stored_hash = payload.get("manifest_sha256")
    without_hash = dict(payload)
    without_hash.pop("manifest_sha256", None)
    if stored_hash != content_sha256(without_hash):
        raise M3KManifestError("M3-K manifest hash mismatch")
    contract = payload.get("evaluation_contract")
    if not isinstance(contract, dict) or payload.get("evaluation_contract_sha256") != content_sha256(contract):
        raise M3KManifestError("M3-K evaluation contract hash mismatch")
    summary = payload.get("summary", {})
    if summary.get("task_count") != 87 or summary.get("cells_per_variant") != 261:
        raise M3KManifestError("M3-K manifest does not preserve the 87x3 denominator")
    if summary.get("variant_count") != 2 or summary.get("expected_trajectories") != 522:
        raise M3KManifestError("M3-K manifest does not preserve paired parent/candidate cells")
    cells = payload.get("paired_cells")
    if not isinstance(cells, list) or len(cells) != 522:
        raise M3KManifestError("M3-K paired cell list must contain 522 rows")
    trajectory_ids = [item.get("trajectory_id") for item in cells]
    if len(set(trajectory_ids)) != 522:
        raise M3KManifestError("M3-K trajectory IDs must be unique")
    pair_counts: dict[str, int] = {}
    for item in cells:
        pair_counts[item.get("pair_id")] = pair_counts.get(item.get("pair_id"), 0) + 1
    if len(pair_counts) != 261 or set(pair_counts.values()) != {2}:
        raise M3KManifestError("every M3-K cell must have one parent and one candidate trajectory")
    if payload.get("execution_gate", {}).get("execution_allowed") is not False:
        raise M3KManifestError("unbound M3-K schedule must remain execution-blocked")
    if payload.get("claim_boundary", {}).get("full87_result") is not False:
        raise M3KManifestError("not-run M3-K schedule cannot claim a full-87 result")


def write_manifest(output: Path, *, split_manifest: Path, library_scale_manifest: Path) -> dict:
    output = output.expanduser().resolve(strict=False)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite M3-K manifest: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = build_manifest(
        split_manifest=split_manifest,
        library_scale_manifest=library_scale_manifest,
    )
    validate_manifest(payload)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--split-manifest", type=Path, default=DEFAULT_SPLIT)
    create.add_argument("--library-scale-manifest", type=Path, default=DEFAULT_SCALE)
    create.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "create":
        payload = write_manifest(
            args.output,
            split_manifest=args.split_manifest,
            library_scale_manifest=args.library_scale_manifest,
        )
        path = args.output.resolve()
    else:
        path = args.manifest.resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        validate_manifest(payload)
    summary = payload["summary"]
    print("Merlin M3-K full-87 evaluation schedule")
    print(f"status={payload['status']}")
    print(f"tasks={summary['task_count']} repeats={summary['repeats']}")
    print(f"paired_trajectories={summary['expected_trajectories']}")
    print(f"manifest_sha256={payload['manifest_sha256']}")
    print(f"saved/verified -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
