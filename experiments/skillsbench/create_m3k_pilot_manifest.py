"""Create a six-trajectory, pilot-only subset of a ready M3-K manifest.

The pilot keeps one held-in task, all three frozen trials, and both harness
variants.  It is an operator handoff for validating the external executor; it
is never a substitute for the 522-trajectory full-87 result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from experiments.skillsbench.bind_m3k_proposal_manifest import (
    M3KProposalBindingError,
    validate_bound_manifest,
)
from src.merlin_harness.management import content_sha256


class M3KPilotManifestError(ValueError):
    """Raised when a bounded pilot cannot be derived safely."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise M3KPilotManifestError(f"{label} must be a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise M3KPilotManifestError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise M3KPilotManifestError(f"{label} must be a JSON object")
    return value


def _pilot_cells(bound_manifest: dict[str, Any], task_id: str | None) -> tuple[str, list[dict[str, Any]]]:
    cells = bound_manifest.get("paired_cells")
    if not isinstance(cells, list) or len(cells) != 522:
        raise M3KPilotManifestError("bound M3-K manifest must contain 522 trajectories")
    held_in_tasks = sorted(
        {
            cell.get("task_id")
            for cell in cells
            if isinstance(cell, dict)
            and cell.get("split") == "held_in"
            and isinstance(cell.get("task_id"), str)
        }
    )
    if not held_in_tasks:
        raise M3KPilotManifestError("bound M3-K manifest has no held-in task")
    selected_task = task_id or held_in_tasks[0]
    if selected_task not in held_in_tasks:
        raise M3KPilotManifestError("pilot task must be a scheduled held-in task")
    selected = [
        dict(cell)
        for cell in cells
        if isinstance(cell, dict)
        and cell.get("task_id") == selected_task
        and cell.get("split") == "held_in"
    ]
    selected.sort(key=lambda cell: (cell.get("trial_index"), cell.get("variant_role")))
    if len(selected) != 6:
        raise M3KPilotManifestError("pilot must contain exactly 3 trials x 2 variants")
    if {cell.get("trial_index") for cell in selected} != {1, 2, 3}:
        raise M3KPilotManifestError("pilot must preserve all three frozen trials")
    pair_roles: dict[str, set[str]] = {}
    for cell in selected:
        pair_id = cell.get("pair_id")
        role = cell.get("variant_role")
        if not isinstance(pair_id, str) or role not in {"parent", "candidate"}:
            raise M3KPilotManifestError("pilot trajectory pair/role is invalid")
        pair_roles.setdefault(pair_id, set()).add(role)
        if cell.get("library_arm_id") != "full-209" or cell.get("library_size") != 209:
            raise M3KPilotManifestError("pilot trajectory is not bound to the full-209 arm")
    if len(pair_roles) != 3 or any(roles != {"parent", "candidate"} for roles in pair_roles.values()):
        raise M3KPilotManifestError("each pilot trial must pair parent and candidate")
    return selected_task, selected


def build_pilot_manifest(
    *,
    bound_manifest: dict[str, Any],
    bound_manifest_file_sha256: str,
    task_id: str | None = None,
) -> dict[str, Any]:
    try:
        validate_bound_manifest(bound_manifest)
    except M3KProposalBindingError as exc:
        raise M3KPilotManifestError(str(exc)) from exc
    if bound_manifest.get("execution_gate", {}).get("execution_allowed") is not True:
        raise M3KPilotManifestError("pilot requires a ready, execution-allowed M3-K manifest")
    selected_task, cells = _pilot_cells(bound_manifest, task_id)
    payload = {
        "schema_version": 1,
        "pilot_id": f"m3k-six-cell:{selected_task}",
        "status": "not_run",
        "scope": "pilot_only",
        "source_full87": {
            "bound_manifest_sha256": bound_manifest["manifest_sha256"],
            "bound_manifest_file_sha256": bound_manifest_file_sha256,
            "evaluation_contract_sha256": bound_manifest["evaluation_contract_sha256"],
            "expected_full87_trajectories": 522,
        },
        "task_id": selected_task,
        "split": "held_in",
        "trial_indices": [1, 2, 3],
        "variant_roles": ["parent", "candidate"],
        "expected_trajectories": 6,
        "trajectories": cells,
        "execution_gate": {
            "execution_allowed": True,
            "purpose": "strict_executor_handshake_and_small_model_backed_pilot",
            "promotion_decision_allowed": False,
        },
        "claim_boundary": {
            "pilot_manifest_is_model_execution": False,
            "pilot_manifest_is_trajectory_result": False,
            "pilot_can_claim_full87": False,
            "pilot_can_promote_candidate": False,
            "all_six_external_records_required_for_pilot_completion": True,
        },
    }
    payload["pilot_manifest_sha256"] = content_sha256(payload)
    validate_pilot_manifest(payload, bound_manifest=bound_manifest)
    return payload


def validate_pilot_manifest(payload: dict[str, Any], *, bound_manifest: dict[str, Any]) -> None:
    if payload.get("schema_version") != 1 or payload.get("status") != "not_run":
        raise M3KPilotManifestError("pilot manifest schema/status is invalid")
    stored_hash = payload.get("pilot_manifest_sha256")
    unhashed = dict(payload)
    unhashed.pop("pilot_manifest_sha256", None)
    if stored_hash != content_sha256(unhashed):
        raise M3KPilotManifestError("pilot manifest hash mismatch")
    source = payload.get("source_full87")
    if not isinstance(source, dict) or source.get("bound_manifest_sha256") != bound_manifest.get("manifest_sha256"):
        raise M3KPilotManifestError("pilot source full-87 manifest drifted")
    if source.get("evaluation_contract_sha256") != bound_manifest.get("evaluation_contract_sha256"):
        raise M3KPilotManifestError("pilot evaluation contract drifted")
    if source.get("expected_full87_trajectories") != 522:
        raise M3KPilotManifestError("pilot full-87 denominator drifted")
    selected_task, expected_cells = _pilot_cells(bound_manifest, payload.get("task_id"))
    if payload.get("task_id") != selected_task or payload.get("trajectories") != expected_cells:
        raise M3KPilotManifestError("pilot trajectory subset drifted")
    if payload.get("expected_trajectories") != 6 or payload.get("scope") != "pilot_only":
        raise M3KPilotManifestError("pilot denominator/scope drifted")
    gate = payload.get("execution_gate", {})
    if gate.get("execution_allowed") is not True or gate.get("promotion_decision_allowed") is not False:
        raise M3KPilotManifestError("pilot execution/promotion gate is unsafe")
    boundary = payload.get("claim_boundary", {})
    if any(
        boundary.get(key) is not False
        for key in (
            "pilot_manifest_is_model_execution",
            "pilot_manifest_is_trajectory_result",
            "pilot_can_claim_full87",
            "pilot_can_promote_candidate",
        )
    ):
        raise M3KPilotManifestError("pilot claim boundary is unsafe")


def write_pilot_manifest(
    *,
    bound_manifest_path: Path,
    output: Path,
    task_id: str | None = None,
) -> dict[str, Any]:
    manifest_path = bound_manifest_path.expanduser().resolve(strict=True)
    bound = _load_json(manifest_path, label="bound M3-K manifest")
    destination = output.expanduser().resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite M3-K pilot manifest: {destination}")
    payload = build_pilot_manifest(
        bound_manifest=bound,
        bound_manifest_file_sha256=_sha256_file(manifest_path),
        task_id=task_id,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bound-manifest", type=Path, required=True)
    parser.add_argument("--task-id")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = write_pilot_manifest(
        bound_manifest_path=args.bound_manifest,
        output=args.output,
        task_id=args.task_id,
    )
    print("Merlin M3-K six-trajectory pilot")
    print(f"task_id={payload['task_id']}")
    print(f"expected_trajectories={payload['expected_trajectories']}")
    print("scope=pilot_only")
    print(f"pilot_manifest_sha256={payload['pilot_manifest_sha256']}")
    print(f"saved -> {args.output.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
