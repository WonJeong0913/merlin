from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.external_corpus_admission import (
    ExternalCorpusAdmissionError,
    validate_external_corpus_report,
    verify_external_task_corpus_admission,
)
from experiments.skillsbench.verify_upstream_tree import verify_upstream_tree


class ExternalCorpusAdmissionTests(unittest.TestCase):
    @staticmethod
    def _git(repo: Path, *arguments: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(repo), *arguments], text=True
        ).strip()

    def _fixture(self, root: Path) -> dict[str, Path]:
        source = root / "source"
        source.mkdir(parents=True)
        repo = root / "skillsbench"
        task = repo / "tasks" / "alpha" / "task.md"
        task.parent.mkdir(parents=True)
        task.write_text("Do the task.\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        self._git(repo, "config", "user.email", "fixture@example.invalid")
        self._git(repo, "config", "user.name", "Fixture")
        self._git(repo, "add", "tasks")
        self._git(repo, "commit", "-qm", "fixture")
        commit = self._git(repo, "rev-parse", "HEAD")
        provenance = verify_upstream_tree(
            upstream_repo=repo, tasks_root=repo / "tasks", commit=commit
        )
        provenance_path = source / "experiments" / "skillsbench" / "corpus-provenance.json"
        provenance_path.parent.mkdir(parents=True)
        provenance_bytes = (
            json.dumps(provenance, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        provenance_path.write_bytes(provenance_bytes)
        snapshot_path = source / "DESKTOP_SNAPSHOT_MANIFEST.json"
        snapshot_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "external_pinned_corpus": {
                        "source": "benchflow-ai/skillsbench",
                        "upstream_commit": commit,
                        "expected_manifest_sha256": provenance[
                            "expected_manifest_sha256"
                        ],
                        "regular_blob_count": 1,
                        "corpus_provenance_file_sha256": hashlib.sha256(
                            provenance_bytes
                        ).hexdigest(),
                        "overlay_excludes": ["experiments/skillsbench/tasks"],
                    },
                }
            ),
            encoding="utf-8",
        )
        return {
            "snapshot_root": source,
            "snapshot_manifest_path": snapshot_path,
            "corpus_provenance_path": provenance_path,
            "upstream_repo": repo,
            "tasks_root": repo / "tasks",
        }

    def test_exact_external_tree_is_bound_without_entering_source_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(Path(temporary))
            report = verify_external_task_corpus_admission(**fixture)
            validate_external_corpus_report(report)
            self.assertEqual(report["regular_blob_count"], 1)
            self.assertTrue(
                report["verification"]["task_tree_is_outside_source_snapshot"]
            )
            self.assertFalse(
                report["claim_boundary"]["corpus_verification_is_model_execution"]
            )

    def test_task_tamper_and_source_nested_corpus_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self._fixture(root / "tamper")
            (fixture["tasks_root"] / "alpha" / "task.md").write_text(
                "tampered\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                ExternalCorpusAdmissionError, "differ from pinned"
            ):
                verify_external_task_corpus_admission(**fixture)

            fixture = self._fixture(root / "nested")
            nested_repo = fixture["snapshot_root"] / "external-skillsbench"
            fixture["upstream_repo"].rename(nested_repo)
            fixture["upstream_repo"] = nested_repo
            fixture["tasks_root"] = nested_repo / "tasks"
            with self.assertRaisesRegex(
                ExternalCorpusAdmissionError, "outside the immutable"
            ):
                verify_external_task_corpus_admission(**fixture)


if __name__ == "__main__":
    unittest.main()
