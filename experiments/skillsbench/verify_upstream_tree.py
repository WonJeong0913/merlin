"""Compare vendored SkillsBench task files with a pinned upstream Git tree.

This verifies every regular Git blob by Git object ID. Gitlink entries are
reported separately because a filesystem copy cannot preserve submodule object
identity without materializing the submodule checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
EXPECTED_COMMIT = "5433cf15c343f0da5fb942b80dc7dcb7c76506df"


def _run_git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args])


def _upstream_entries(repo: Path, commit: str) -> tuple[dict[str, str], dict[str, str]]:
    output = _run_git(repo, "ls-tree", "-r", "-z", "--full-tree", commit, "tasks")
    blobs: dict[str, str] = {}
    gitlinks: dict[str, str] = {}
    for raw_entry in output.split(b"\0"):
        if not raw_entry:
            continue
        metadata, raw_path = raw_entry.split(b"\t", 1)
        mode, object_type, object_id = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8", errors="surrogateescape")
        relative = path.removeprefix("tasks/")
        if object_type == "blob":
            blobs[relative] = object_id
        elif object_type == "commit" or mode == "160000":
            gitlinks[relative] = object_id
    return blobs, gitlinks


def _git_blob_id(path: Path) -> str:
    return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()


def _manifest_sha256(entries: dict[str, str]) -> str:
    payload = "".join(f"{entries[path]} {path}\n" for path in sorted(entries))
    return hashlib.sha256(payload.encode("utf-8", errors="surrogateescape")).hexdigest()


def verify_upstream_tree(
    *,
    upstream_repo: Path,
    tasks_root: Path,
    commit: str = EXPECTED_COMMIT,
) -> dict[str, Any]:
    resolved_commit = _run_git(upstream_repo, "rev-parse", commit).decode().strip()
    expected_blobs, gitlinks = _upstream_entries(upstream_repo, resolved_commit)
    local_files = {
        path.relative_to(tasks_root).as_posix(): _git_blob_id(path)
        for path in sorted(tasks_root.rglob("*"))
        if path.is_file()
    }
    expected_paths = set(expected_blobs)
    local_paths = set(local_files)
    missing = sorted(expected_paths - local_paths)
    extra = sorted(local_paths - expected_paths)
    mismatched = [
        {
            "path": path,
            "expected_blob": expected_blobs[path],
            "local_blob": local_files[path],
        }
        for path in sorted(expected_paths & local_paths)
        if expected_blobs[path] != local_files[path]
    ]
    gitlink_status = [
        {
            "path": path,
            "object_id": object_id,
            "placeholder_exists": (tasks_root / path).is_dir(),
            "materialized_file_count": sum(
                1 for item in (tasks_root / path).rglob("*") if item.is_file()
            )
            if (tasks_root / path).is_dir()
            else 0,
        }
        for path, object_id in sorted(gitlinks.items())
    ]
    regular_blobs_exact = not missing and not extra and not mismatched
    gitlink_placeholders_present = all(item["placeholder_exists"] for item in gitlink_status)
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "upstream_commit": resolved_commit,
        "expected_commit": commit,
        "regular_blob_count": len(expected_blobs),
        "local_regular_file_count": len(local_files),
        "regular_blobs_exact": regular_blobs_exact,
        "expected_manifest_sha256": _manifest_sha256(expected_blobs),
        "local_manifest_sha256": _manifest_sha256(local_files),
        "missing": missing,
        "extra": extra,
        "mismatched": mismatched,
        "gitlinks": gitlink_status,
        "gitlink_placeholders_present": gitlink_placeholders_present,
        "claim": (
            "all regular task blobs match the pinned upstream commit; gitlinks are reported separately"
            if regular_blobs_exact and gitlink_placeholders_present
            else "upstream tree mismatch"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-repo", type=Path, required=True)
    parser.add_argument("--tasks-root", type=Path, default=ROOT / "tasks")
    parser.add_argument("--commit", default=EXPECTED_COMMIT)
    parser.add_argument("--output", type=Path, default=ROOT / "corpus-provenance.json")
    args = parser.parse_args(argv)

    result = verify_upstream_tree(
        upstream_repo=args.upstream_repo,
        tasks_root=args.tasks_root,
        commit=args.commit,
    )
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "upstream_commit",
        "regular_blob_count",
        "local_regular_file_count",
        "regular_blobs_exact",
        "gitlink_placeholders_present",
    )}, sort_keys=True))
    return 0 if result["regular_blobs_exact"] and result["gitlink_placeholders_present"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
