"""Verify the external pinned SkillsBench task corpus for DESKTOP admission."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from experiments.skillsbench.verify_upstream_tree import verify_upstream_tree
from src.merlin_harness.management import content_sha256


LOWER_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class ExternalCorpusAdmissionError(ValueError):
    """Raised when the external task corpus is not the pinned clean tree."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ExternalCorpusAdmissionError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
        raw = resolved.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExternalCorpusAdmissionError(f"{label} is missing or invalid") from exc
    if not isinstance(value, dict):
        raise ExternalCorpusAdmissionError(f"{label} must be a JSON object")
    return value, raw


def _regular_directory(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ExternalCorpusAdmissionError(f"{label} must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise ExternalCorpusAdmissionError(f"{label} is missing") from exc
    if not resolved.is_dir():
        raise ExternalCorpusAdmissionError(f"{label} must be a directory")
    return resolved


def _git_text(repo: Path, *arguments: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), *arguments],
            stderr=subprocess.PIPE,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ExternalCorpusAdmissionError("cannot inspect external upstream Git tree") from exc


def validate_external_corpus_report(report: Mapping[str, Any]) -> None:
    """Validate the stable evidence schema embedded into an admission audit."""

    if set(report) != {
        "schema_version",
        "diagnostic",
        "source_snapshot_manifest_sha256",
        "corpus_provenance_file_sha256",
        "upstream_commit",
        "upstream_head",
        "regular_blob_count",
        "expected_manifest_sha256",
        "local_manifest_sha256",
        "gitlinks",
        "tasks_root_path_sha256",
        "verification",
        "claim_boundary",
        "report_sha256",
    }:
        raise ExternalCorpusAdmissionError("external corpus report fields drifted")
    if (
        report.get("schema_version") != 1
        or report.get("diagnostic") != "external_task_corpus_admission"
        or not all(
            LOWER_SHA256_RE.fullmatch(str(report.get(field, "")))
            for field in (
                "source_snapshot_manifest_sha256",
                "corpus_provenance_file_sha256",
                "expected_manifest_sha256",
                "local_manifest_sha256",
                "tasks_root_path_sha256",
                "report_sha256",
            )
        )
        or not re.fullmatch(r"[0-9a-f]{40}", str(report.get("upstream_commit", "")))
        or report.get("upstream_head") != report.get("upstream_commit")
        or not isinstance(report.get("regular_blob_count"), int)
        or report["regular_blob_count"] < 1
        or report.get("expected_manifest_sha256")
        != report.get("local_manifest_sha256")
        or not isinstance(report.get("gitlinks"), list)
        or report.get("verification")
        != {
            "regular_blobs_exact": True,
            "gitlink_placeholders_present": True,
            "task_tree_has_no_symlinks": True,
            "task_tree_is_outside_source_snapshot": True,
        }
        or report.get("claim_boundary")
        != {
            "corpus_verification_is_model_execution": False,
            "corpus_verification_is_benchmark_result": False,
            "external_task_bytes_are_not_source_snapshot_entries": True,
            "materializer_must_use_this_external_tasks_root": True,
        }
    ):
        raise ExternalCorpusAdmissionError("external corpus report contract drifted")
    for gitlink in report["gitlinks"]:
        if (
            not isinstance(gitlink, dict)
            or set(gitlink)
            != {
                "path",
                "object_id",
                "placeholder_exists",
                "materialized_file_count",
            }
            or not isinstance(gitlink.get("path"), str)
            or not gitlink["path"]
            or PurePosixPath(gitlink["path"]).is_absolute()
            or ".." in PurePosixPath(gitlink["path"]).parts
            or not re.fullmatch(r"[0-9a-f]{40}", str(gitlink.get("object_id", "")))
            or gitlink.get("placeholder_exists") is not True
            or gitlink.get("materialized_file_count") != 0
        ):
            raise ExternalCorpusAdmissionError("external corpus gitlink report drifted")
    unhashed = dict(report)
    recorded = unhashed.pop("report_sha256")
    if recorded != content_sha256(unhashed):
        raise ExternalCorpusAdmissionError("external corpus report hash drifted")


def verify_external_task_corpus_admission(
    *,
    snapshot_root: Path,
    snapshot_manifest_path: Path,
    corpus_provenance_path: Path,
    upstream_repo: Path,
    tasks_root: Path,
) -> dict[str, Any]:
    """Re-hash every external task blob and bind it to the source snapshot."""

    source_root = _regular_directory(snapshot_root, label="source snapshot root")
    repo = _regular_directory(upstream_repo, label="external upstream repository")
    tasks = _regular_directory(tasks_root, label="external tasks root")
    if tasks != repo / "tasks":
        raise ExternalCorpusAdmissionError(
            "external tasks root must be the pinned repository's tasks directory"
        )
    if tasks.is_relative_to(source_root) or repo.is_relative_to(source_root):
        raise ExternalCorpusAdmissionError(
            "external task corpus must remain outside the immutable source snapshot"
        )
    if any(path.is_symlink() for path in tasks.rglob("*")):
        raise ExternalCorpusAdmissionError("external task tree must not contain symlinks")

    snapshot, snapshot_bytes = _load_json(
        snapshot_manifest_path, label="source snapshot manifest"
    )
    provenance, provenance_bytes = _load_json(
        corpus_provenance_path, label="corpus provenance"
    )
    external = snapshot.get("external_pinned_corpus")
    if not isinstance(external, dict):
        raise ExternalCorpusAdmissionError("source snapshot external corpus binding is missing")
    expected_commit = external.get("upstream_commit")
    expected_manifest = external.get("expected_manifest_sha256")
    expected_count = external.get("regular_blob_count")
    if (
        external.get("source") != "benchflow-ai/skillsbench"
        or external.get("overlay_excludes") != ["experiments/skillsbench/tasks"]
        or not re.fullmatch(r"[0-9a-f]{40}", str(expected_commit or ""))
        or not LOWER_SHA256_RE.fullmatch(str(expected_manifest or ""))
        or not isinstance(expected_count, int)
        or expected_count < 1
        or external.get("corpus_provenance_file_sha256") != _sha256(provenance_bytes)
    ):
        raise ExternalCorpusAdmissionError("source snapshot external corpus contract drifted")
    if (
        provenance.get("upstream_commit") != expected_commit
        or provenance.get("expected_commit") != expected_commit
        or provenance.get("regular_blob_count") != expected_count
        or provenance.get("local_regular_file_count") != expected_count
        or provenance.get("expected_manifest_sha256") != expected_manifest
        or provenance.get("local_manifest_sha256") != expected_manifest
        or provenance.get("regular_blobs_exact") is not True
        or provenance.get("gitlink_placeholders_present") is not True
    ):
        raise ExternalCorpusAdmissionError("canonical corpus provenance drifted")

    head = _git_text(repo, "rev-parse", "HEAD")
    if head != expected_commit:
        raise ExternalCorpusAdmissionError("external upstream checkout HEAD is not pinned")
    verified = verify_upstream_tree(
        upstream_repo=repo,
        tasks_root=tasks,
        commit=expected_commit,
    )
    if (
        verified.get("upstream_commit") != expected_commit
        or verified.get("expected_commit") != expected_commit
        or verified.get("regular_blob_count") != expected_count
        or verified.get("local_regular_file_count") != expected_count
        or verified.get("expected_manifest_sha256") != expected_manifest
        or verified.get("local_manifest_sha256") != expected_manifest
        or verified.get("regular_blobs_exact") is not True
        or verified.get("gitlink_placeholders_present") is not True
    ):
        raise ExternalCorpusAdmissionError("external task blobs differ from pinned corpus")
    gitlinks = verified.get("gitlinks")
    if not isinstance(gitlinks, list) or any(
        not isinstance(item, dict)
        or item.get("placeholder_exists") is not True
        or item.get("materialized_file_count") != 0
        for item in gitlinks
    ):
        raise ExternalCorpusAdmissionError("external task gitlink placeholders drifted")

    report: dict[str, Any] = {
        "schema_version": 1,
        "diagnostic": "external_task_corpus_admission",
        "source_snapshot_manifest_sha256": _sha256(snapshot_bytes),
        "corpus_provenance_file_sha256": _sha256(provenance_bytes),
        "upstream_commit": expected_commit,
        "upstream_head": head,
        "regular_blob_count": expected_count,
        "expected_manifest_sha256": expected_manifest,
        "local_manifest_sha256": verified["local_manifest_sha256"],
        "gitlinks": gitlinks,
        "tasks_root_path_sha256": _sha256(str(tasks).encode("utf-8")),
        "verification": {
            "regular_blobs_exact": True,
            "gitlink_placeholders_present": True,
            "task_tree_has_no_symlinks": True,
            "task_tree_is_outside_source_snapshot": True,
        },
        "claim_boundary": {
            "corpus_verification_is_model_execution": False,
            "corpus_verification_is_benchmark_result": False,
            "external_task_bytes_are_not_source_snapshot_entries": True,
            "materializer_must_use_this_external_tasks_root": True,
        },
    }
    report["report_sha256"] = content_sha256(report)
    validate_external_corpus_report(report)
    return report


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    expanded = path.expanduser().resolve(strict=False)
    if expanded.exists() or expanded.is_symlink():
        raise ExternalCorpusAdmissionError("external corpus report output must be new-only")
    expanded.parent.mkdir(parents=True, exist_ok=True)
    with expanded.open("xb") as handle:
        handle.write(
            (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            )
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--snapshot-manifest", type=Path, required=True)
    parser.add_argument("--corpus-provenance", type=Path, required=True)
    parser.add_argument("--external-upstream-repo", type=Path, required=True)
    parser.add_argument("--external-tasks-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = verify_external_task_corpus_admission(
            snapshot_root=args.snapshot_root,
            snapshot_manifest_path=args.snapshot_manifest,
            corpus_provenance_path=args.corpus_provenance,
            upstream_repo=args.external_upstream_repo,
            tasks_root=args.external_tasks_root,
        )
        if args.output is not None:
            _write_new_json(args.output, report)
    except ExternalCorpusAdmissionError as exc:
        parser.error(str(exc))
    print("external_corpus_valid=true")
    print(f"upstream_commit={report['upstream_commit']}")
    print(f"regular_blob_count={report['regular_blob_count']}")
    print(f"task_manifest_sha256={report['local_manifest_sha256']}")
    print("model_execution_performed=false")
    print("benchmark_executed=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
