"""Materialize one ready M3-K trajectory into a new immutable operator bundle.

The bundle contains the exact ordered full-209 skill library, a model-visible
task view without oracle/verifier leakage, a separately staged verifier, the
selected parent/candidate harness variant, and fail-closed evidence templates.
Materialization is not model execution and never creates a benchmark result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.skillsbench.bind_m3k_proposal_manifest import (
    M3KProposalBindingError,
    validate_bound_manifest,
)
from experiments.skillsbench.create_library_scale_manifest import (
    DEFAULT_CORPUS_PROVENANCE,
    DEFAULT_INDEX,
    DEFAULT_SKILLS_ROOT,
    DEFAULT_TASKS_ROOT,
    LibraryScaleManifestError,
    sha256_file,
    sha256_json,
    tree_sha256,
    validate_library_scale_manifest,
)
from experiments.skillsbench.m3k_external_evidence import (
    ATTESTATION_KEYS,
    RUNTIME_AUDIT_KEYS,
    requested_model_contract,
)
from src.merlin_harness.management import content_sha256


DEFAULT_LIBRARY_SCALE = REPO_ROOT / "experiments/skillsbench/library-scale-manifest.json"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@:+-]{0,199}$")
_RUNTIME_ARTIFACT_DIRS = frozenset({"__pycache__", ".pytest_cache"})
_RUNTIME_ARTIFACT_NAMES = frozenset({".DS_Store"})
_RUNTIME_ARTIFACT_SUFFIXES = frozenset({".pyc", ".pyo"})
_BUNDLE_ROOT_ENTRIES = frozenset(
    {
        "skills",
        "task-visible",
        "verifier-hidden",
        "harness-variant.json",
        "proposal-bundle.json",
        "execution-contract.json",
        "attestation.template.json",
        "runtime-audit.template.json",
        "operator-template-contract.json",
    }
)


class M3KMaterializationError(ValueError):
    """Raised when a frozen M3-K trajectory cannot be staged safely."""


def _load_json(path: Path, *, label: str) -> tuple[Path, dict[str, Any]]:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise M3KMaterializationError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise M3KMaterializationError(f"cannot read {label}: {path}") from exc
    if not resolved.is_file() or not isinstance(value, dict):
        raise M3KMaterializationError(f"{label} must be a regular JSON object")
    return resolved, value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load_bundle_json(root: Path, filename: str) -> dict[str, Any]:
    path = root / filename
    if path.is_symlink() or not path.is_file():
        raise M3KMaterializationError(f"materialized bundle file is missing or unsafe: {filename}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise M3KMaterializationError(f"materialized bundle JSON is unreadable: {filename}") from exc
    if not isinstance(value, dict):
        raise M3KMaterializationError(f"materialized bundle JSON must be an object: {filename}")
    return value


def _validate_filtered_tree(
    root: Path,
    expected: dict[str, Any] | None,
    *,
    label: str,
) -> None:
    if expected is None:
        if root.exists() or root.is_symlink():
            raise M3KMaterializationError(f"unexpected {label} tree")
        return
    if not isinstance(expected, dict):
        raise M3KMaterializationError(f"{label} contract is malformed")
    records = _filtered_records(root)
    if expected.get("regular_file_count") != len(records):
        raise M3KMaterializationError(f"{label} file count drifted")
    if expected.get("records_sha256") != sha256_json(records):
        raise M3KMaterializationError(f"{label} bytes drifted")


def validate_materialized_m3k_cell(
    bundle_root: Path,
    *,
    expected_contract_sha256: str | None = None,
) -> dict[str, Any]:
    """Re-open and byte-verify one immutable operator bundle before execution."""

    expanded = bundle_root.expanduser()
    if expanded.is_symlink():
        raise M3KMaterializationError("materialized bundle root must not be a symlink")
    try:
        root = expanded.resolve(strict=True)
    except OSError as exc:
        raise M3KMaterializationError("materialized bundle root is missing") from exc
    if not root.is_dir():
        raise M3KMaterializationError("materialized bundle root must be a directory")
    entries = {entry.name for entry in root.iterdir()}
    if entries != set(_BUNDLE_ROOT_ENTRIES):
        raise M3KMaterializationError("materialized bundle root entries drifted")
    for member in root.rglob("*"):
        if member.is_symlink():
            raise M3KMaterializationError("materialized bundle contains a symlink")

    contract = _load_bundle_json(root, "execution-contract.json")
    stored_contract_sha256 = contract.get("execution_contract_sha256")
    unhashed = dict(contract)
    unhashed.pop("execution_contract_sha256", None)
    if stored_contract_sha256 != content_sha256(unhashed):
        raise M3KMaterializationError("execution contract hash mismatch")
    if expected_contract_sha256 is not None and stored_contract_sha256 != expected_contract_sha256:
        raise M3KMaterializationError("execution contract identity differs")
    if contract.get("schema_version") != 1 or contract.get("execution_status") != "not_run":
        raise M3KMaterializationError("execution contract schema/status is invalid")
    boundary = contract.get("evidence_boundary")
    if not isinstance(boundary, dict) or any(
        boundary.get(key) is not False
        for key in (
            "materialization_is_model_execution",
            "materialization_is_actual_invocation",
            "materialization_is_benchmark_result",
        )
    ):
        raise M3KMaterializationError("execution contract evidence boundary is unsafe")

    corpus = contract.get("task_corpus_source")
    if (
        not isinstance(corpus, dict)
        or set(corpus)
        != {
            "corpus_provenance_file_sha256",
            "upstream_commit",
            "regular_blob_count",
            "expected_manifest_sha256",
            "local_manifest_sha256",
            "tasks_root_path_sha256",
            "runtime_admission_must_match",
        }
        or not re.fullmatch(r"[0-9a-f]{40}", str(corpus.get("upstream_commit", "")))
        or not isinstance(corpus.get("regular_blob_count"), int)
        or corpus["regular_blob_count"] < 1
        or any(
            not re.fullmatch(r"[0-9a-f]{64}", str(corpus.get(field, "")))
            for field in (
                "corpus_provenance_file_sha256",
                "expected_manifest_sha256",
                "local_manifest_sha256",
                "tasks_root_path_sha256",
            )
        )
        or corpus.get("expected_manifest_sha256")
        != corpus.get("local_manifest_sha256")
        or corpus.get("runtime_admission_must_match") is not True
    ):
        raise M3KMaterializationError("task corpus source contract is invalid")

    staged = contract.get("staged_artifacts")
    trajectory = contract.get("trajectory")
    proposal = contract.get("proposal")
    if not isinstance(staged, dict) or not isinstance(trajectory, dict) or not isinstance(proposal, dict):
        raise M3KMaterializationError("execution contract sections are missing")
    if (
        staged.get("skill_root") != "skills"
        or staged.get("task_visible_root") != "task-visible"
        or staged.get("hidden_verifier_root") != "verifier-hidden"
    ):
        raise M3KMaterializationError("materialized bundle root pointers drifted")
    if trajectory.get("library_arm_id") != "full-209" or trajectory.get("library_size") != 209:
        raise M3KMaterializationError("materialized trajectory is not the full-209 arm")
    presentation_order = staged.get("presentation_order")
    variant_records = staged.get("variant_records")
    if (
        not isinstance(presentation_order, list)
        or not isinstance(variant_records, list)
        or len(presentation_order) != trajectory.get("library_size")
        or len(variant_records) != len(presentation_order)
    ):
        raise M3KMaterializationError("materialized skill presentation contract is malformed")
    if len(set(presentation_order)) != len(presentation_order):
        raise M3KMaterializationError("materialized skill presentation contains duplicates")
    if sha256_json(presentation_order) != trajectory.get("library_order_sha256"):
        raise M3KMaterializationError("materialized skill presentation order drifted")
    skills_root = root / "skills"
    if skills_root.is_symlink() or not skills_root.is_dir():
        raise M3KMaterializationError("materialized skill root is unsafe")
    if {entry.name for entry in skills_root.iterdir()} != set(presentation_order):
        raise M3KMaterializationError("materialized skill root membership drifted")
    rebuilt_records: list[dict[str, Any]] = []
    for ordinal, (variant_id, record) in enumerate(
        zip(presentation_order, variant_records, strict=True),
        start=1,
    ):
        if not isinstance(variant_id, str) or not _SAFE_ID.fullmatch(variant_id):
            raise M3KMaterializationError("materialized skill id is unsafe")
        if not isinstance(record, dict):
            raise M3KMaterializationError("materialized skill record is malformed")
        package = _safe_source_directory(skills_root / variant_id, label="materialized skill package")
        package_sha256 = tree_sha256(package)
        expected_record = {
            "ordinal": ordinal,
            "variant_id": variant_id,
            "source_tree_sha256": package_sha256,
            "staged_tree_sha256": package_sha256,
        }
        if record != expected_record:
            raise M3KMaterializationError(f"materialized skill bytes drifted: {variant_id}")
        rebuilt_records.append(expected_record)
    if staged.get("materialized_skill_bytes_sha256") != sha256_json(rebuilt_records):
        raise M3KMaterializationError("materialized skill-library hash drifted")

    visible = root / "task-visible"
    if visible.is_symlink() or not visible.is_dir():
        raise M3KMaterializationError("task-visible root is unsafe")
    expected_visible_entries = {"task.md"}
    if staged.get("task_environment") is not None:
        expected_visible_entries.add("environment")
    if {entry.name for entry in visible.iterdir()} != expected_visible_entries:
        raise M3KMaterializationError("task-visible root entries drifted")
    task_md = visible / "task.md"
    if task_md.is_symlink() or not task_md.is_file():
        raise M3KMaterializationError("task-visible instruction is unsafe")
    if sha256_file(task_md) != trajectory.get("task_instruction_sha256"):
        raise M3KMaterializationError("task-visible instruction bytes drifted")
    _validate_filtered_tree(
        visible / "environment",
        staged.get("task_environment"),
        label="task environment",
    )
    _validate_filtered_tree(
        root / "verifier-hidden",
        staged.get("hidden_verifier"),
        label="hidden verifier",
    )
    if staged.get("oracle_copied") is not False:
        raise M3KMaterializationError("materialized bundle claims oracle exposure")

    variant_payload = _load_bundle_json(root, "harness-variant.json")
    proposal_bundle = _load_bundle_json(root, "proposal-bundle.json")
    if content_sha256(variant_payload) != proposal.get("variant_sha256"):
        raise M3KMaterializationError("materialized harness variant bytes drifted")
    if content_sha256(proposal_bundle) != proposal.get("proposal_bundle_sha256"):
        raise M3KMaterializationError("materialized proposal bundle bytes drifted")

    attestation = _load_bundle_json(root, "attestation.template.json")
    runtime_audit = _load_bundle_json(root, "runtime-audit.template.json")
    operator = _load_bundle_json(root, "operator-template-contract.json")
    if set(attestation) != set(ATTESTATION_KEYS) or attestation.get("actual_invocation_evidence_complete") is not False:
        raise M3KMaterializationError("attestation template is unsafe or incomplete")
    if set(runtime_audit) != set(RUNTIME_AUDIT_KEYS) or any(
        runtime_audit.get(key) is not False
        for key in (
            "tool_feature_suppression_enforced",
            "strict_config_enforced",
            "user_config_suppressed",
            "rules_suppressed",
            "per_run_mcp_isolation",
            "exec_tool_call_observed",
        )
    ):
        raise M3KMaterializationError("runtime-audit template is unsafe or pre-completed")
    if runtime_audit.get("host_native_tool_event_observed") is not True:
        raise M3KMaterializationError("runtime-audit template must fail closed before execution")
    if operator.get("templates_are_evidence") is not False or operator.get(
        "templates_are_valid_completed_results"
    ) is not False:
        raise M3KMaterializationError("operator template contract is unsafe")
    return contract


def _find_trajectory(manifest: dict[str, Any], trajectory_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in manifest.get("paired_cells", [])
        if isinstance(item, dict) and item.get("trajectory_id") == trajectory_id
    ]
    if len(matches) != 1:
        raise M3KMaterializationError(f"trajectory id must resolve exactly once: {trajectory_id}")
    return matches[0]


def _find_library_cell(
    library_manifest: dict[str, Any],
    *,
    task_id: str,
    trial_index: int,
) -> dict[str, Any]:
    matches = [
        item
        for item in library_manifest.get("cells", [])
        if isinstance(item, dict)
        and item.get("task_id") == task_id
        and item.get("trial_index") == trial_index
        and item.get("arm_id") == "full-209"
    ]
    if len(matches) != 1:
        raise M3KMaterializationError("canonical full-209 library cell must resolve exactly once")
    return matches[0]


def _oracle_skill_ids(index_path: Path, *, task_id: str, presentation_order: list[str]) -> list[str]:
    """Resolve the task's curated skill variants without exposing them to the model.

    The normalized index is already an input to the library-scale manifest
    contract.  Re-deriving the IDs here keeps the external attestation from
    accepting caller-invented oracle membership while leaving the task-visible
    mount free of oracle labels.
    """

    _, index = _load_json(index_path, label="normalized skill index")
    skills = index.get("skills")
    if not isinstance(skills, list):
        raise M3KMaterializationError("normalized skill index has no skill rows")
    oracle_ids: list[str] = []
    for row in skills:
        if not isinstance(row, dict):
            raise M3KMaterializationError("normalized skill index row is malformed")
        used_by = row.get("used_by_tasks")
        variant = row.get("variant")
        if not isinstance(used_by, list) or any(not isinstance(item, str) for item in used_by):
            raise M3KMaterializationError("normalized skill index task binding is malformed")
        if task_id not in used_by:
            continue
        if not isinstance(variant, str) or variant not in presentation_order:
            raise M3KMaterializationError("task oracle skill is absent from the staged full library")
        oracle_ids.append(variant)
    if len(set(oracle_ids)) != len(oracle_ids):
        raise M3KMaterializationError("task oracle skill IDs are duplicated")
    return oracle_ids


def _safe_source_directory(path: Path, *, label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise M3KMaterializationError(f"{label} is missing or unsafe: {path}")
    for member in path.rglob("*"):
        if member.is_symlink():
            raise M3KMaterializationError(f"{label} contains a symlink: {member}")
    return path


def _is_runtime_artifact(relative: Path) -> bool:
    return (
        any(part in _RUNTIME_ARTIFACT_DIRS for part in relative.parts)
        or relative.name in _RUNTIME_ARTIFACT_NAMES
        or relative.suffix in _RUNTIME_ARTIFACT_SUFFIXES
    )


def _filtered_records(source: Path) -> list[dict[str, str]]:
    _safe_source_directory(source, label="source tree")
    records: list[dict[str, str]] = []
    for member in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
        if member.is_file():
            relative = member.relative_to(source)
            if not _is_runtime_artifact(relative):
                records.append({"path": relative.as_posix(), "sha256": sha256_file(member)})
    return records


def _copy_filtered_tree(source: Path, destination: Path, *, label: str) -> dict[str, Any]:
    source_records = _filtered_records(source)
    destination.mkdir(parents=True, exist_ok=False)
    for record in source_records:
        relative = Path(record["path"])
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
    staged_records = _filtered_records(destination)
    if staged_records != source_records:
        raise M3KMaterializationError(f"{label} staged byte records drifted")
    return {
        "regular_file_count": len(source_records),
        "records_sha256": sha256_json(source_records),
        "excluded_runtime_artifacts": ["__pycache__", ".pytest_cache", "*.pyc", "*.pyo", ".DS_Store"],
    }


def _copy_skill_library(
    *,
    variant_ids: list[str],
    skills_root: Path,
    destination: Path,
) -> tuple[list[dict[str, Any]], str]:
    destination.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []
    for ordinal, variant_id in enumerate(variant_ids, start=1):
        if not _SAFE_ID.fullmatch(variant_id):
            raise M3KMaterializationError(f"unsafe skill variant id: {variant_id}")
        source = _safe_source_directory(skills_root / variant_id, label="skill package")
        staged = destination / variant_id
        filtered = _copy_filtered_tree(source, staged, label=f"skill package {variant_id}")
        source_hash = filtered["records_sha256"]
        staged_hash = tree_sha256(staged)
        if staged_hash != source_hash:
            raise M3KMaterializationError(f"staged skill bytes drifted: {variant_id}")
        records.append(
            {
                "ordinal": ordinal,
                "variant_id": variant_id,
                "source_tree_sha256": source_hash,
                "staged_tree_sha256": staged_hash,
            }
        )
    return records, sha256_json(records)


def _assert_source_cell_matches(scheduled: dict[str, Any], source: dict[str, Any]) -> None:
    expected = {
        "library_arm_id": source.get("arm_id"),
        "library_size": source.get("library_size"),
        "library_snapshot_sha256": source.get("library_snapshot_sha256"),
        "library_variant_ids": source.get("library_variant_ids"),
        "library_order_sha256": sha256_json(source.get("library_variant_ids")),
        "library_trial_seed": source.get("trial_seed"),
    }
    for key, value in expected.items():
        if scheduled.get(key) != value:
            raise M3KMaterializationError(f"bound trajectory {key} differs from canonical library source")
    if scheduled.get("task_instruction_sha256") != source.get("task_instruction_sha256"):
        raise M3KMaterializationError("bound trajectory task instruction differs from canonical library source")
    if scheduled.get("verifier_id") != source.get("verifier_contract_sha256"):
        raise M3KMaterializationError("bound trajectory verifier differs from canonical library source")


def materialize_m3k_external_cell(
    *,
    bound_manifest_path: Path,
    library_scale_manifest_path: Path,
    trajectory_id: str,
    output_root: Path,
    index_path: Path = DEFAULT_INDEX,
    corpus_provenance_path: Path = DEFAULT_CORPUS_PROVENANCE,
    tasks_root: Path = DEFAULT_TASKS_ROOT,
    skills_root: Path = DEFAULT_SKILLS_ROOT,
) -> dict[str, Any]:
    """Stage one exact trajectory into a new-only portable directory."""

    manifest_path, manifest = _load_json(bound_manifest_path, label="bound M3-K manifest")
    library_path, library_manifest = _load_json(
        library_scale_manifest_path,
        label="canonical library-scale manifest",
    )
    try:
        validate_bound_manifest(manifest)
        validate_library_scale_manifest(
            library_manifest,
            index_path=index_path,
            corpus_provenance_path=corpus_provenance_path,
            tasks_root=tasks_root,
            skills_root=skills_root,
        )
    except (M3KProposalBindingError, LibraryScaleManifestError) as exc:
        raise M3KMaterializationError(str(exc)) from exc
    provenance_path, provenance = _load_json(
        corpus_provenance_path, label="corpus provenance"
    )
    task_corpus_source = {
        "corpus_provenance_file_sha256": sha256_file(provenance_path),
        "upstream_commit": provenance.get("upstream_commit"),
        "regular_blob_count": provenance.get("regular_blob_count"),
        "expected_manifest_sha256": provenance.get("expected_manifest_sha256"),
        "local_manifest_sha256": provenance.get("local_manifest_sha256"),
        "tasks_root_path_sha256": hashlib.sha256(
            str(tasks_root.expanduser().resolve(strict=True)).encode("utf-8")
        ).hexdigest(),
        "runtime_admission_must_match": True,
    }
    if (
        not re.fullmatch(r"[0-9a-f]{40}", str(task_corpus_source["upstream_commit"] or ""))
        or not isinstance(task_corpus_source["regular_blob_count"], int)
        or task_corpus_source["regular_blob_count"] < 1
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(task_corpus_source["expected_manifest_sha256"] or "")
        )
        or task_corpus_source["expected_manifest_sha256"]
        != task_corpus_source["local_manifest_sha256"]
    ):
        raise M3KMaterializationError("corpus provenance binding is invalid")
    if manifest.get("execution_gate", {}).get("execution_allowed") is not True:
        raise M3KMaterializationError("M3-K trajectory materialization requires execution_allowed=true")
    library_binding = manifest["library_binding"]
    if sha256_file(library_path) != library_binding["source_manifest_file_sha256"]:
        raise M3KMaterializationError("canonical library-scale file hash drifted")
    if sha256_json(library_manifest) != library_binding["source_manifest_semantic_sha256"]:
        raise M3KMaterializationError("canonical library-scale semantic hash drifted")

    scheduled = _find_trajectory(manifest, trajectory_id)
    task_id = scheduled.get("task_id")
    if not isinstance(task_id, str) or not _SAFE_ID.fullmatch(task_id):
        raise M3KMaterializationError("scheduled task id is unsafe")
    source_cell = _find_library_cell(
        library_manifest,
        task_id=task_id,
        trial_index=scheduled["trial_index"],
    )
    _assert_source_cell_matches(scheduled, source_cell)

    destination = output_root.expanduser()
    if destination.exists() or destination.is_symlink():
        raise M3KMaterializationError("output root must not already exist or be a symlink")
    destination = destination.resolve(strict=False)
    destination.mkdir(parents=True, exist_ok=False)
    try:
        variant_ids = list(scheduled["library_variant_ids"])
        variant_records, staged_library_sha256 = _copy_skill_library(
            variant_ids=variant_ids,
            skills_root=skills_root,
            destination=destination / "skills",
        )

        source_task_root = _safe_source_directory(tasks_root / task_id, label="task package")
        task_md = source_task_root / "task.md"
        if task_md.is_symlink() or not task_md.is_file():
            raise M3KMaterializationError("task instruction is missing or unsafe")
        if sha256_file(task_md) != scheduled["task_instruction_sha256"]:
            raise M3KMaterializationError("task instruction bytes drifted")
        visible = destination / "task-visible"
        visible.mkdir()
        shutil.copyfile(task_md, visible / "task.md")
        environment = source_task_root / "environment"
        environment_contract = None
        if environment.exists():
            environment_contract = _copy_filtered_tree(
                environment,
                visible / "environment",
                label="task environment",
            )
        verifier = source_task_root / "verifier"
        verifier_contract = _copy_filtered_tree(
            verifier,
            destination / "verifier-hidden",
            label="hidden verifier",
        )

        proposal_binding = manifest["proposal_binding"]
        bundle = proposal_binding["bundle"]
        role = scheduled["variant_role"]
        variant_payload = (
            bundle["parent_variant"]
            if role == "parent"
            else bundle["proposal"]["candidate"]
        )
        variant_prefix = "parent" if role == "parent" else "candidate"
        expected_variant_sha256 = proposal_binding[f"{variant_prefix}_variant_sha256"]
        if content_sha256(variant_payload) != expected_variant_sha256:
            raise M3KMaterializationError("selected harness variant hash drifted")
        _write_json(destination / "harness-variant.json", variant_payload)
        _write_json(destination / "proposal-bundle.json", bundle)

        bound_file_sha256 = sha256_file(manifest_path)
        contract = {
            "schema_version": 1,
            "execution_status": "not_run",
            "bound_manifest": {
                "manifest_sha256": manifest["manifest_sha256"],
                "file_sha256": bound_file_sha256,
                "source_filename": manifest_path.name,
            },
            "library_source": {
                "semantic_sha256": library_binding["source_manifest_semantic_sha256"],
                "file_sha256": library_binding["source_manifest_file_sha256"],
                "source_filename": library_path.name,
            },
            "task_corpus_source": task_corpus_source,
            "trajectory": {
                key: scheduled[key]
                for key in (
                    "trajectory_id",
                    "pair_id",
                    "cell_id",
                    "variant_role",
                    "task_id",
                    "split",
                    "trial_index",
                    "verifier_id",
                    "task_instruction_sha256",
                    "library_arm_id",
                    "library_size",
                    "library_snapshot_sha256",
                    "library_order_sha256",
                    "library_trial_seed",
                )
            },
            "proposal": {
                "proposal_id": proposal_binding["proposal_id"],
                "proposal_sha256": proposal_binding["proposal_sha256"],
                "variant_id": proposal_binding[f"{variant_prefix}_variant_id"],
                "variant_sha256": expected_variant_sha256,
                "proposal_bundle_sha256": proposal_binding["proposal_bundle_sha256"],
            },
            "requested_model_contract": requested_model_contract(manifest),
            "staged_artifacts": {
                "skill_root": "skills",
                "presentation_order": variant_ids,
                "variant_records": variant_records,
                "materialized_skill_bytes_sha256": staged_library_sha256,
                "task_visible_root": "task-visible",
                "task_environment": environment_contract,
                "hidden_verifier_root": "verifier-hidden",
                "hidden_verifier": verifier_contract,
                "oracle_copied": False,
            },
            "evidence_boundary": {
                "materialization_is_model_execution": False,
                "materialization_is_actual_invocation": False,
                "materialization_is_benchmark_result": False,
                "runtime_must_preserve_skill_presentation_order": True,
                "runtime_must_not_expose_verifier_or_oracle_to_model": True,
                "runtime_must_emit_unique_raw_trace_and_strict_audit": True,
            },
        }
        contract["execution_contract_sha256"] = content_sha256(contract)
        _write_json(destination / "execution-contract.json", contract)

        attestation = {
            "schema_version": 1,
            "bound_manifest_sha256": manifest["manifest_sha256"],
            "bound_manifest_file_sha256": bound_file_sha256,
            "trajectory_id": scheduled["trajectory_id"],
            "pair_id": scheduled["pair_id"],
            "cell_id": scheduled["cell_id"],
            "variant_role": role,
            "variant_id": proposal_binding[f"{variant_prefix}_variant_id"],
            "variant_sha256": expected_variant_sha256,
            "proposal_id": proposal_binding["proposal_id"],
            "proposal_sha256": proposal_binding["proposal_sha256"],
            "evaluation_contract_sha256": manifest["evaluation_contract_sha256"],
            "task_id": task_id,
            "split": scheduled["split"],
            "trial_index": scheduled["trial_index"],
            "verifier_id": scheduled["verifier_id"],
            "task_instruction_sha256": scheduled["task_instruction_sha256"],
            "library_arm_id": scheduled["library_arm_id"],
            "library_size": scheduled["library_size"],
            "library_snapshot_sha256": scheduled["library_snapshot_sha256"],
            "library_order_sha256": scheduled["library_order_sha256"],
            "actual_invocation_evidence_complete": False,
            "invoked_skill_ids": [],
            "oracle_skill_ids": _oracle_skill_ids(
                index_path,
                task_id=task_id,
                presentation_order=variant_ids,
            ),
            "verifier_passed": False,
            "verifier_score": 0.0,
            "cost": 0.0,
        }
        _write_json(destination / "attestation.template.json", attestation)
        runtime_audit = {
            "schema_version": 2,
            "bound_manifest_sha256": manifest["manifest_sha256"],
            "executor_capability_file_sha256": manifest["executor_capability"]["file_sha256"],
            "trajectory_id": scheduled["trajectory_id"],
            "raw_provider_trace_sha256": "",
            "requested_model_contract": requested_model_contract(manifest),
            "tool_feature_suppression_enforced": False,
            "feature_suppression_sha256": "",
            "strict_config_enforced": False,
            "user_config_suppressed": False,
            "rules_suppressed": False,
            "per_run_mcp_isolation": False,
            "host_native_tool_event_observed": True,
            "exec_tool_call_observed": False,
            "inspected_container_id": "",
            "inspected_container_sha256": "",
            "inspected_image_id": "",
            "inspected_image_sha256": "",
            "run_config_sha256": "",
            "audit_event_sha256": "",
        }
        _write_json(destination / "runtime-audit.template.json", runtime_audit)
        _write_json(
            destination / "operator-template-contract.json",
            {
                "schema_version": 1,
                "templates_are_evidence": False,
                "templates_are_valid_completed_results": False,
                "required_outputs": [
                    "unique raw provider trace",
                    "completed attestation copied from attestation.template.json",
                    "completed strict runtime audit copied from runtime-audit.template.json",
                ],
                "recorder": "experiments.skillsbench.m3k_external_evidence.record_m3k_external_trajectory",
            },
        )
        return contract
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bound-manifest", type=Path)
    parser.add_argument("--library-scale-manifest", type=Path, default=DEFAULT_LIBRARY_SCALE)
    parser.add_argument("--trajectory-id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate-existing", type=Path)
    parser.add_argument("--expected-contract-sha256")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--corpus-provenance", type=Path, default=DEFAULT_CORPUS_PROVENANCE)
    parser.add_argument("--tasks-root", type=Path, default=DEFAULT_TASKS_ROOT)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    args = parser.parse_args(argv)
    if args.validate_existing is not None:
        if any(value is not None for value in (args.bound_manifest, args.trajectory_id, args.output)):
            parser.error("--validate-existing cannot be combined with materialization arguments")
        contract = validate_materialized_m3k_cell(
            args.validate_existing,
            expected_contract_sha256=args.expected_contract_sha256,
        )
        print("Merlin M3-K external trajectory bundle revalidation")
        print(f"trajectory_id={contract['trajectory']['trajectory_id']}")
        print("execution_status=not_run")
        print(f"execution_contract_sha256={contract['execution_contract_sha256']}")
        print(f"verified -> {args.validate_existing.expanduser().resolve()}")
        return 0
    if args.expected_contract_sha256 is not None:
        parser.error("--expected-contract-sha256 requires --validate-existing")
    if args.bound_manifest is None or args.trajectory_id is None or args.output is None:
        parser.error("materialization requires --bound-manifest, --trajectory-id, and --output")
    contract = materialize_m3k_external_cell(
        bound_manifest_path=args.bound_manifest,
        library_scale_manifest_path=args.library_scale_manifest,
        trajectory_id=args.trajectory_id,
        output_root=args.output,
        index_path=args.index,
        corpus_provenance_path=args.corpus_provenance,
        tasks_root=args.tasks_root,
        skills_root=args.skills_root,
    )
    print("Merlin M3-K external trajectory bundle")
    print(f"trajectory_id={contract['trajectory']['trajectory_id']}")
    print(f"library_size={contract['trajectory']['library_size']}")
    print("execution_status=not_run")
    print(f"execution_contract_sha256={contract['execution_contract_sha256']}")
    print(f"saved -> {args.output.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
