from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.desktop_snapshot_manifest import (
    SnapshotManifestError,
    create_manifest,
    verify_manifest,
)


class DesktopSnapshotManifestTests(unittest.TestCase):
    def test_manifest_binds_files_modes_symlinks_and_external_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            root.mkdir()
            script = root / "runner.py"
            script.write_text("print('ok')\n", encoding="utf-8")
            script.chmod(0o755)
            (root / "runner-link").symlink_to("runner.py")
            provenance_path = root / "corpus-provenance.json"
            provenance_path.write_text(
                json.dumps(
                    {
                        "upstream_commit": "a" * 40,
                        "expected_commit": "a" * 40,
                        "regular_blobs_exact": True,
                        "regular_blob_count": 2160,
                        "expected_manifest_sha256": "b" * 64,
                    }
                ),
                encoding="utf-8",
            )

            manifest = create_manifest(
                root=root,
                corpus_provenance_path=provenance_path,
                ignored_paths=["DESKTOP_SNAPSHOT_MANIFEST.json"],
            )
            verify_manifest(root=root, manifest=manifest)

            entries = {entry["path"]: entry for entry in manifest["entries"]}
            self.assertEqual(entries["runner.py"]["mode"], "0755")
            self.assertEqual(entries["runner-link"]["type"], "symlink")
            self.assertEqual(entries["runner-link"]["target"], "runner.py")
            self.assertEqual(
                manifest["external_pinned_corpus"]["upstream_commit"], "a" * 40
            )

            external_tasks = root / "experiments" / "skillsbench" / "tasks"
            external_tasks.mkdir(parents=True)
            with self.assertRaisesRegex(
                SnapshotManifestError, "must remain outside"
            ):
                verify_manifest(root=root, manifest=manifest)
            external_tasks.rmdir()

            script.write_text("print('drift')\n", encoding="utf-8")
            with self.assertRaisesRegex(SnapshotManifestError, "manifest drifted"):
                verify_manifest(root=root, manifest=manifest)

    def test_manifest_rejects_unproven_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provenance_path = root / "corpus-provenance.json"
            provenance_path.write_text(
                json.dumps(
                    {
                        "upstream_commit": "a" * 40,
                        "expected_commit": "c" * 40,
                        "regular_blobs_exact": False,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SnapshotManifestError, "not pinned and exact"):
                create_manifest(
                    root=root,
                    corpus_provenance_path=provenance_path,
                    ignored_paths=["DESKTOP_SNAPSHOT_MANIFEST.json"],
                )

    def test_manifest_rejects_runtime_cache_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provenance_path = root / "corpus-provenance.json"
            provenance_path.write_text(
                json.dumps(
                    {
                        "upstream_commit": "a" * 40,
                        "expected_commit": "a" * 40,
                        "regular_blobs_exact": True,
                        "regular_blob_count": 2160,
                        "expected_manifest_sha256": "b" * 64,
                    }
                ),
                encoding="utf-8",
            )
            cache = root / "skill" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "module.pyc").write_bytes(b"generated")
            with self.assertRaisesRegex(SnapshotManifestError, "runtime artifact"):
                create_manifest(
                    root=root,
                    corpus_provenance_path=provenance_path,
                    ignored_paths=["DESKTOP_SNAPSHOT_MANIFEST.json"],
                )


if __name__ == "__main__":
    unittest.main()
