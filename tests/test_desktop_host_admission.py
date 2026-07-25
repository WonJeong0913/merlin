from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.skillsbench.desktop_host_admission import (
    DesktopAdmissionError,
    inspect_docker,
    inspect_legacy_run_root,
    run_admitted,
)


def _legacy_root(root: Path, *, pid: int, status: str) -> tuple[Path, str]:
    legacy = root / "legacy"
    (legacy / "control").mkdir(parents=True)
    (legacy / "control" / "manager.pid").write_text(f"{pid}\n", encoding="utf-8")
    state_bytes = (json.dumps({"status": status}, sort_keys=True) + "\n").encode()
    (legacy / "control" / "state.json").write_bytes(state_bytes)
    (legacy / "manager.lock").write_text("", encoding="utf-8")
    return legacy, hashlib.sha256(state_bytes).hexdigest()


class _Report:
    returncode = 0
    stderr = ""

    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout


class DesktopHostAdmissionTests(unittest.TestCase):
    def test_stale_running_state_requires_exact_sha_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            legacy, state_sha = _legacy_root(Path(temporary), pid=999_999, status="running")
            with self.assertRaisesRegex(DesktopAdmissionError, "exact state SHA"):
                inspect_legacy_run_root(legacy)
            report = inspect_legacy_run_root(
                legacy, acknowledged_stale_state_sha256=state_sha
            )
        self.assertFalse(report["pid_alive"])
        self.assertFalse(report["lock_held"])
        self.assertTrue(report["stale_active_state_acknowledged"])

    def test_live_pid_or_held_lock_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live, _ = _legacy_root(root / "pid", pid=os.getpid(), status="running")
            with self.assertRaisesRegex(DesktopAdmissionError, "active or locked"):
                inspect_legacy_run_root(live)

            locked, _ = _legacy_root(root / "lock", pid=999_999, status="complete")
            descriptor = os.open(locked / "manager.lock", os.O_RDONLY)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(DesktopAdmissionError, "active or locked"):
                    inspect_legacy_run_root(locked)
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def test_docker_inspection_blocks_merlin_harness_container(self) -> None:
        row = json.dumps(
            {
                "ID": "abc123",
                "Names": "theking-phase3e-cell",
                "Image": "skillsbench-task:latest",
                "Labels": "",
            }
        )
        with patch(
            "experiments.skillsbench.desktop_host_admission.subprocess.run",
            return_value=_Report(row + "\n"),
        ):
            with self.assertRaisesRegex(DesktopAdmissionError, "containers are running"):
                inspect_docker()

    def test_admitted_command_holds_lock_and_writes_bounded_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy, _ = _legacy_root(root, pid=999_999, status="complete")
            audit = root / "audit"
            snapshot = root / "snapshot"
            snapshot.mkdir()
            snapshot_manifest = root / "snapshot-manifest.json"
            snapshot_manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "entries_sha256": "a" * 64,
                        "entry_count": 1,
                        "external_pinned_corpus": {"upstream_commit": "b" * 40},
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            def fake_run(argv, **kwargs):
                calls.append((list(argv), kwargs))
                return _Report("")

            with patch(
                "experiments.skillsbench.desktop_host_admission.subprocess.run",
                side_effect=fake_run,
            ), patch(
                "experiments.skillsbench.desktop_host_admission.verify_manifest"
            ) as verify, patch(
                "experiments.skillsbench.desktop_host_admission."
                "verify_external_task_corpus_admission",
                return_value={"report_sha256": "e" * 64},
            ) as verify_corpus:
                exit_code = run_admitted(
                    global_lock_path=root / "state" / "admission.lock",
                    legacy_run_roots=[legacy],
                    stale_acknowledgements={},
                    snapshot_root=snapshot,
                    snapshot_manifest_path=snapshot_manifest,
                    corpus_provenance_path=root / "corpus-provenance.json",
                    external_upstream_repo=root / "upstream",
                    external_tasks_root=root / "upstream" / "tasks",
                    audit_dir=audit,
                    command=["python3", "runner.py"],
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(calls[0][0][:2], ["docker", "ps"])
            self.assertEqual(calls[1][0], ["python3", "runner.py"])
            child_env = calls[1][1]["env"]
            verify.assert_called_once()
            verify_corpus.assert_called_once()
            start = json.loads((audit / "start.json").read_text(encoding="utf-8"))
            final = json.loads((audit / "final.json").read_text(encoding="utf-8"))
            self.assertFalse(start["command_recorded"])
            self.assertNotIn("runner.py", json.dumps(start))
            self.assertEqual(start["source_snapshot"]["entries_sha256"], "a" * 64)
            self.assertEqual(start["external_task_corpus"]["report_sha256"], "e" * 64)
            self.assertEqual(
                child_env["MERLIN_DESKTOP_ADMISSION_START"],
                str((audit / "start.json").resolve()),
            )
            self.assertEqual(
                child_env["MERLIN_DESKTOP_ADMISSION_START_SHA256"],
                hashlib.sha256((audit / "start.json").read_bytes()).hexdigest(),
            )
            self.assertEqual(
                child_env["MERLIN_DESKTOP_ADMITTED_COMMAND_SHA256"],
                start["command_sha256"],
            )
            self.assertTrue(final["global_lock_held_through_child_exit"])


if __name__ == "__main__":
    unittest.main()
