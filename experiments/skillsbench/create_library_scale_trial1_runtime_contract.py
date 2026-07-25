"""Bind the frozen 435 plan to one eligible DESKTOP executor and runtime."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from experiments.skillsbench.bind_m3k_proposal_manifest import (
    M3KProposalBindingError,
    validate_executor_capability,
)
from experiments.skillsbench.create_library_scale_manifest import (
    DEFAULT_CORPUS_PROVENANCE,
    DEFAULT_INDEX,
    DEFAULT_SKILLS_ROOT,
    sha256_file,
)
from experiments.skillsbench.derive_library_scale_trial1_plan import (
    LibraryScaleTrial1PlanError,
    validate_library_scale_trial1_plan,
)
from src.merlin_harness.management import content_sha256


MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ALLOWED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "ultra"})


class LibraryScaleRuntimeContractError(ValueError):
    """Raised when a live trial-1 runtime cannot be frozen or reproduced."""


def _load_json(path: Path, *, label: str) -> tuple[Path, dict[str, Any], bytes]:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise LibraryScaleRuntimeContractError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
        raw = resolved.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LibraryScaleRuntimeContractError(f"{label} is missing or invalid") from exc
    if not resolved.is_file() or not isinstance(value, dict):
        raise LibraryScaleRuntimeContractError(f"{label} must be a regular JSON object")
    return resolved, value, raw


def _source_snapshot_binding(path: Path) -> dict[str, Any]:
    resolved, snapshot, _raw = _load_json(path, label="source snapshot manifest")
    external = snapshot.get("external_pinned_corpus")
    if (
        snapshot.get("snapshot_role")
        != "mac-canonical-merlin-overlay-for-desktop-executor"
        or not isinstance(snapshot.get("entry_count"), int)
        or snapshot["entry_count"] < 1
        or not re.fullmatch(r"[0-9a-f]{64}", str(snapshot.get("entries_sha256", "")))
        or not isinstance(external, dict)
        or external.get("source") != "benchflow-ai/skillsbench"
        or not re.fullmatch(r"[0-9a-f]{40}", str(external.get("upstream_commit", "")))
        or not isinstance(external.get("regular_blob_count"), int)
    ):
        raise LibraryScaleRuntimeContractError("source snapshot contract is invalid")
    return {
        "file_sha256": sha256_file(resolved),
        "entry_count": snapshot["entry_count"],
        "entries_sha256": snapshot["entries_sha256"],
        "external_corpus": {
            "repository": external["source"],
            "commit": external["upstream_commit"],
            "regular_blob_count": external["regular_blob_count"],
            "expected_manifest_sha256": external["expected_manifest_sha256"],
            "corpus_provenance_file_sha256": external[
                "corpus_provenance_file_sha256"
            ],
        },
    }


def build_library_scale_trial1_runtime_contract(
    *,
    plan_path: Path,
    source_plan_path: Path,
    manifest_path: Path,
    executor_capability_path: Path,
    source_snapshot_manifest_path: Path,
    corpus_provenance_path: Path = DEFAULT_CORPUS_PROVENANCE,
    model: str = "gpt-5.6-terra",
    effort: str = "high",
    exposure_budget: int = 3,
    model_timeout_sec: int = 900,
    verifier_timeout_sec: int = 900,
    index_path: Path = DEFAULT_INDEX,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
) -> dict[str, Any]:
    if (
        not isinstance(model, str)
        or not MODEL_RE.fullmatch(model)
        or not isinstance(effort, str)
        or effort not in ALLOWED_EFFORTS
    ):
        raise LibraryScaleRuntimeContractError("requested model or effort is unsupported")
    if isinstance(exposure_budget, bool) or not 1 <= exposure_budget <= 10:
        raise LibraryScaleRuntimeContractError("exposure budget must be from 1 through 10")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3600
        for value in (model_timeout_sec, verifier_timeout_sec)
    ):
        raise LibraryScaleRuntimeContractError("runtime timeouts must be from 1 through 3600")
    try:
        plan = validate_library_scale_trial1_plan(
            plan_path=plan_path,
            source_plan_path=source_plan_path,
            manifest_path=manifest_path,
            index_path=index_path,
            skills_root=skills_root,
        )
    except LibraryScaleTrial1PlanError as exc:
        raise LibraryScaleRuntimeContractError(str(exc)) from exc
    capability_path, capability, capability_bytes = _load_json(
        executor_capability_path, label="executor capability"
    )
    try:
        eligible, failures, safe_summary = validate_executor_capability(capability)
    except M3KProposalBindingError as exc:
        raise LibraryScaleRuntimeContractError(str(exc)) from exc
    if not eligible or failures:
        raise LibraryScaleRuntimeContractError(
            "executor capability is not eligible: " + ",".join(failures)
        )
    if (
        safe_summary.get("one_cell_execution_allowed") is not True
        or safe_summary.get("additional_pilot_cells_require_validated_first_cell")
        is not True
    ):
        raise LibraryScaleRuntimeContractError(
            "executor capability does not require a validated first-cell gate"
        )
    _provenance_path, provenance, _ = _load_json(
        corpus_provenance_path, label="corpus provenance"
    )
    source_snapshot = _source_snapshot_binding(source_snapshot_manifest_path)
    external = source_snapshot["external_corpus"]
    if (
        provenance.get("upstream_commit") != external["commit"]
        or provenance.get("regular_blob_count") != external["regular_blob_count"]
        or provenance.get("expected_manifest_sha256")
        != external["expected_manifest_sha256"]
        or provenance.get("local_manifest_sha256")
        != external["expected_manifest_sha256"]
        or sha256_file(corpus_provenance_path)
        != external["corpus_provenance_file_sha256"]
    ):
        raise LibraryScaleRuntimeContractError(
            "source snapshot and corpus provenance bindings differ"
        )
    contract: dict[str, Any] = {
        "schema_version": 1,
        "contract_kind": "library-scale-trial1-live-runtime-v1",
        "batch_id": plan["batch_id"],
        "plan": {
            "semantic_sha256": plan["plan_sha256"],
            "file_sha256": sha256_file(plan_path),
            "source_semantic_sha256": plan["dependencies"][
                "source_batch_plan_sha256"
            ],
            "source_file_sha256": sha256_file(source_plan_path),
            "manifest_file_sha256": sha256_file(manifest_path),
            "scheduled_cells": 435,
        },
        "source_snapshot": source_snapshot,
        "executor_capability": {
            "file_sha256": sha256_file(capability_path),
            "bytes": len(capability_bytes),
            "eligible": True,
            "safe_summary_sha256": content_sha256(safe_summary),
            "one_cell_execution_allowed": True,
            "additional_cells_require_validated_first_cell": True,
        },
        "requested_model_contract": {
            "backend": "codex-cli-fixed-container-mcp",
            "model_id": model,
            "effort": effort,
            "tools": ["fixed-container-exec"],
        },
        "harness_contract": {
            "mode": "metadata-first-staged-body-v1",
            "provisioning_policy": "governed-provisioning-v2",
            "candidate_library_bound_by_materialized_snapshot": True,
            "exposure_budget": exposure_budget,
            "actual_invocation_source": "skill-associated-fixed-container-mcp-exec",
            "provider_native_skill_invocation_claimed": False,
        },
        "timeouts": {
            "model_sec": model_timeout_sec,
            "verifier_sec": verifier_timeout_sec,
        },
        "execution_policy": {
            "concurrency": 1,
            "canary_cells": 5,
            "full_phase_requires_canary_admission": True,
            "host_admission_required": True,
            "network_mode": "none",
        },
        "claim_boundary": {
            "contract_is_model_execution": False,
            "contract_is_benchmark_result": False,
            "full_435_completion_claimed": False,
            "full_1305_completion_claimed": False,
            "provider_resolved_model_identity_claimed": False,
        },
    }
    contract["contract_sha256"] = content_sha256(contract)
    return contract


def write_library_scale_trial1_runtime_contract(
    *, output_path: Path, **kwargs: Any
) -> dict[str, Any]:
    destination = output_path.expanduser().resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise LibraryScaleRuntimeContractError("runtime contract output must be new-only")
    contract = build_library_scale_trial1_runtime_contract(**kwargs)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(contract, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise LibraryScaleRuntimeContractError("runtime contract output must be new-only") from exc
    return contract


def validate_library_scale_trial1_runtime_contract(
    *, contract_path: Path, **kwargs: Any
) -> dict[str, Any]:
    _path, stored, _raw = _load_json(contract_path, label="runtime contract")
    stored_hash = stored.get("contract_sha256")
    unhashed = dict(stored)
    unhashed.pop("contract_sha256", None)
    if stored_hash != content_sha256(unhashed):
        raise LibraryScaleRuntimeContractError("runtime contract hash mismatch")
    expected = build_library_scale_trial1_runtime_contract(**kwargs)
    if stored != expected:
        raise LibraryScaleRuntimeContractError("runtime contract drifted from live inputs")
    return stored


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--source-plan", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--executor-capability", type=Path, required=True)
    parser.add_argument("--source-snapshot-manifest", type=Path, required=True)
    parser.add_argument("--corpus-provenance", type=Path, default=DEFAULT_CORPUS_PROVENANCE)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--effort", default="high", choices=sorted(ALLOWED_EFFORTS))
    parser.add_argument("--exposure-budget", type=int, default=3)
    parser.add_argument("--model-timeout-sec", type=int, default=900)
    parser.add_argument("--verifier-timeout-sec", type=int, default=900)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument("--contract", type=Path)
    args = parser.parse_args(argv)
    kwargs = {
        "plan_path": args.plan,
        "source_plan_path": args.source_plan,
        "manifest_path": args.manifest,
        "executor_capability_path": args.executor_capability,
        "source_snapshot_manifest_path": args.source_snapshot_manifest,
        "corpus_provenance_path": args.corpus_provenance,
        "model": args.model,
        "effort": args.effort,
        "exposure_budget": args.exposure_budget,
        "model_timeout_sec": args.model_timeout_sec,
        "verifier_timeout_sec": args.verifier_timeout_sec,
        "index_path": args.index,
        "skills_root": args.skills_root,
    }
    try:
        contract = (
            validate_library_scale_trial1_runtime_contract(
                contract_path=args.contract, **kwargs
            )
            if args.contract is not None
            else write_library_scale_trial1_runtime_contract(
                output_path=args.output, **kwargs
            )
        )
    except LibraryScaleRuntimeContractError as exc:
        parser.error(str(exc))
    print("Merlin 435-cell live runtime contract")
    print("status=revalidated" if args.contract is not None else "status=created")
    print(f"batch_id={contract['batch_id']}")
    print(f"model={contract['requested_model_contract']['model_id']}")
    print(f"exposure_budget={contract['harness_contract']['exposure_budget']}")
    print("model_execution=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
