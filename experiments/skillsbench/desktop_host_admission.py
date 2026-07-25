"""Run one DESKTOP executor command under a fail-closed host-global lease."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

from experiments.skillsbench.external_corpus_admission import (
    ExternalCorpusAdmissionError,
    verify_external_task_corpus_admission,
)
from src.merlin_harness.management import content_sha256
from tools.desktop_snapshot_manifest import verify_manifest


ACTIVE_STATES = frozenset({"active", "in_progress", "running", "starting"})
BLOCKED_CONTAINER_MARKERS = ("theking", "merlin", "skillsbench")


class DesktopAdmissionError(ValueError):
    """Raised when DESKTOP cannot prove exclusive, quiescent execution."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_json_bytes(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise DesktopAdmissionError(f"{label} must be a regular file")
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise DesktopAdmissionError(f"cannot read {label}") from exc
    if not isinstance(payload, dict):
        raise DesktopAdmissionError(f"{label} must be a JSON object")
    return payload, raw


def _pid_alive(pid: int) -> bool:
    if pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock_is_held(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        raise DesktopAdmissionError("legacy manager lock is missing or unsafe")
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)


def inspect_legacy_run_root(
    root: Path, *, acknowledged_stale_state_sha256: str | None = None
) -> dict[str, Any]:
    """Prove one legacy manager is idle without changing its files."""

    resolved = root.expanduser().resolve(strict=True)
    pid_path = resolved / "control" / "manager.pid"
    state_path = resolved / "control" / "state.json"
    lock_path = resolved / "manager.lock"
    if pid_path.is_symlink() or not pid_path.is_file():
        raise DesktopAdmissionError("legacy manager PID file is missing or unsafe")
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeError, ValueError) as exc:
        raise DesktopAdmissionError("legacy manager PID is invalid") from exc
    state, state_bytes = _load_json_bytes(state_path, label="legacy manager state")
    state_sha256 = _sha256_bytes(state_bytes)
    pid_alive = _pid_alive(pid)
    lock_held = _lock_is_held(lock_path)
    status = state.get("status")
    if not isinstance(status, str) or not status:
        raise DesktopAdmissionError("legacy manager state has no status")
    status_active = status.lower() in ACTIVE_STATES
    if pid_alive or lock_held:
        raise DesktopAdmissionError("legacy Phase 3E manager is still active or locked")
    stale_acknowledged = (
        status_active
        and acknowledged_stale_state_sha256 is not None
        and acknowledged_stale_state_sha256 == state_sha256
    )
    if status_active and not stale_acknowledged:
        raise DesktopAdmissionError(
            "legacy state still says running; exact state SHA acknowledgement is required"
        )
    if acknowledged_stale_state_sha256 is not None and not stale_acknowledged:
        raise DesktopAdmissionError("legacy stale-state acknowledgement does not match")
    return {
        "root_sha256": _sha256_bytes(str(resolved).encode("utf-8")),
        "pid": pid,
        "pid_alive": pid_alive,
        "lock_held": lock_held,
        "state_status": status,
        "state_file_sha256": state_sha256,
        "stale_active_state_acknowledged": stale_acknowledged,
    }


def inspect_docker(*, timeout_sec: int = 30) -> dict[str, Any]:
    """Reject a host with a running Merlin/SkillsBench container."""

    try:
        report = subprocess.run(
            ["docker", "ps", "--format", "{{json .}}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DesktopAdmissionError("cannot inspect Docker runtime") from exc
    if report.returncode != 0:
        raise DesktopAdmissionError("Docker runtime is unavailable")
    running: list[dict[str, str]] = []
    blocked: list[str] = []
    for line_number, line in enumerate(report.stdout.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DesktopAdmissionError(
                f"Docker ps row {line_number} is malformed"
            ) from exc
        if not isinstance(row, dict):
            raise DesktopAdmissionError("Docker ps row must be an object")
        container_id = str(row.get("ID", ""))
        name = str(row.get("Names", ""))
        image = str(row.get("Image", ""))
        labels = str(row.get("Labels", ""))
        summary = " ".join((name, image, labels)).lower()
        if any(marker in summary for marker in BLOCKED_CONTAINER_MARKERS):
            blocked.append(container_id or _sha256_bytes(summary.encode())[:12])
        running.append({"id": container_id, "name": name, "image": image})
    if blocked:
        raise DesktopAdmissionError(
            "existing Merlin/SkillsBench Docker containers are running: "
            + ",".join(blocked)
        )
    return {"running_container_count": len(running), "running_containers": running}


def _parse_acknowledgements(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        root, separator, digest = value.rpartition("=")
        if not separator or not root or len(digest) != 64:
            raise DesktopAdmissionError(
                "stale acknowledgement must be ABSOLUTE_RUN_ROOT=LOWERCASE_SHA256"
            )
        try:
            int(digest, 16)
        except ValueError as exc:
            raise DesktopAdmissionError("stale acknowledgement SHA is invalid") from exc
        result[str(Path(root).expanduser().resolve(strict=True))] = digest
    return result


def run_admitted(
    *,
    global_lock_path: Path,
    legacy_run_roots: Sequence[Path],
    stale_acknowledgements: dict[str, str],
    snapshot_root: Path,
    snapshot_manifest_path: Path,
    corpus_provenance_path: Path,
    external_upstream_repo: Path,
    external_tasks_root: Path,
    audit_dir: Path,
    command: Sequence[str],
) -> int:
    """Hold the host lease across preflight and exactly one child command."""

    if not command:
        raise DesktopAdmissionError("admitted command is missing")
    lock_path = global_lock_path.expanduser()
    if not lock_path.is_absolute() or str(lock_path).startswith("/mnt/"):
        raise DesktopAdmissionError("global admission lock must be on absolute WSL storage")
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DesktopAdmissionError("host-global admission lock is already held") from exc
        target_audit = audit_dir.expanduser().resolve(strict=False)
        if target_audit.exists() or target_audit.is_symlink():
            raise DesktopAdmissionError("admission audit directory must be new-only")
        snapshot_manifest, snapshot_manifest_bytes = _load_json_bytes(
            snapshot_manifest_path,
            label="DESKTOP source snapshot manifest",
        )
        try:
            verify_manifest(root=snapshot_root, manifest=snapshot_manifest)
        except ValueError as exc:
            raise DesktopAdmissionError(f"DESKTOP source snapshot is invalid: {exc}") from exc
        try:
            external_corpus = verify_external_task_corpus_admission(
                snapshot_root=snapshot_root,
                snapshot_manifest_path=snapshot_manifest_path,
                corpus_provenance_path=corpus_provenance_path,
                upstream_repo=external_upstream_repo,
                tasks_root=external_tasks_root,
            )
        except ExternalCorpusAdmissionError as exc:
            raise DesktopAdmissionError(
                f"DESKTOP external task corpus is invalid: {exc}"
            ) from exc
        legacy = [
            inspect_legacy_run_root(
                root,
                acknowledged_stale_state_sha256=stale_acknowledgements.get(
                    str(root.expanduser().resolve(strict=True))
                ),
            )
            for root in legacy_run_roots
        ]
        docker = inspect_docker()
        corpus = snapshot_manifest.get("external_pinned_corpus")
        if not isinstance(corpus, dict) or not isinstance(corpus.get("upstream_commit"), str):
            raise DesktopAdmissionError("snapshot pinned corpus binding is missing")
        target_audit.mkdir(parents=True, mode=0o700)
        started = {
            "schema_version": 1,
            "diagnostic": "desktop_host_admission",
            "started_unix": time.time(),
            "global_lock_path_sha256": _sha256_bytes(str(lock_path).encode("utf-8")),
            "legacy_runs": legacy,
            "docker": docker,
            "source_snapshot": {
                "manifest_file_sha256": _sha256_bytes(snapshot_manifest_bytes),
                "entries_sha256": snapshot_manifest.get("entries_sha256"),
                "entry_count": snapshot_manifest.get("entry_count"),
                "pinned_upstream_commit": corpus["upstream_commit"],
            },
            "external_task_corpus": external_corpus,
            "command_sha256": _sha256_bytes(
                json.dumps(list(command), separators=(",", ":")).encode("utf-8")
            ),
            "command_recorded": False,
        }
        (target_audit / "start.json").write_text(
            json.dumps(started, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        child_environment = dict(os.environ)
        child_environment.update(
            {
                "MERLIN_DESKTOP_ADMISSION_START": str(target_audit / "start.json"),
                "MERLIN_DESKTOP_ADMISSION_START_SHA256": _sha256_bytes(
                    (target_audit / "start.json").read_bytes()
                ),
                "MERLIN_DESKTOP_ADMITTED_COMMAND_SHA256": started["command_sha256"],
            }
        )
        report = subprocess.run(
            list(command),
            stdin=subprocess.DEVNULL,
            check=False,
            env=child_environment,
        )
        final = {
            "schema_version": 1,
            "diagnostic": "desktop_host_admission_result",
            "start_semantic_sha256": content_sha256(started),
            "exit_code": report.returncode,
            "completed_unix": time.time(),
            "global_lock_held_through_child_exit": True,
        }
        (target_audit / "final.json").write_text(
            json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return report.returncode
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--global-lock", type=Path, required=True)
    parser.add_argument("--legacy-run-root", type=Path, action="append", default=[])
    parser.add_argument("--acknowledge-stale-state", action="append", default=[])
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--snapshot-manifest", type=Path, required=True)
    parser.add_argument("--corpus-provenance", type=Path, required=True)
    parser.add_argument("--external-upstream-repo", type=Path, required=True)
    parser.add_argument("--external-tasks-root", type=Path, required=True)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    try:
        return run_admitted(
            global_lock_path=args.global_lock,
            legacy_run_roots=args.legacy_run_root,
            stale_acknowledgements=_parse_acknowledgements(
                args.acknowledge_stale_state
            ),
            snapshot_root=args.snapshot_root,
            snapshot_manifest_path=args.snapshot_manifest,
            corpus_provenance_path=args.corpus_provenance,
            external_upstream_repo=args.external_upstream_repo,
            external_tasks_root=args.external_tasks_root,
            audit_dir=args.audit_dir,
            command=command,
        )
    except (DesktopAdmissionError, OSError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
