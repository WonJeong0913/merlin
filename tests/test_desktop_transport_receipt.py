from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.desktop_snapshot_manifest import create_manifest
from tools.validate_desktop_transport_receipt import (
    DesktopTransportReceiptError,
    receive_transport,
    validate_transport_root,
)


class DesktopTransportReceiptTests(unittest.TestCase):
    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _fixture(self, root: Path, *, unsafe_archive: bool = False) -> tuple[Path, Path]:
        carrier = root / "carrier"
        transport = carrier / "transport"
        transport.mkdir(parents=True)
        source = root / "source"
        source.mkdir()
        (source / "runner.py").write_text("print('ok')\n", encoding="utf-8")
        provenance = source / "experiments" / "skillsbench" / "corpus-provenance.json"
        provenance.parent.mkdir(parents=True)
        provenance.write_text(
            json.dumps(
                {
                    "upstream_commit": "c" * 40,
                    "expected_commit": "c" * 40,
                    "regular_blobs_exact": True,
                    "regular_blob_count": 2160,
                    "expected_manifest_sha256": "d" * 64,
                }
            ),
            encoding="utf-8",
        )
        manifest_path = source / "DESKTOP_SNAPSHOT_MANIFEST.json"
        manifest = create_manifest(
            root=source,
            corpus_provenance_path=provenance,
            ignored_paths=[manifest_path.name],
        )
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        archive_path = transport / "source.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(source, arcname=".")
            if unsafe_archive:
                raw = b"escape"
                member = tarfile.TarInfo("../escape.txt")
                member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))
        private_pack = transport / "private.tar"
        private_pack.write_bytes(b"private-pack-fixture")
        records = [
            {
                "path": "safe-evidence/model_authored_skill_chain_audit.json",
                "sha256": "e" * 64,
            },
            {
                "path": "live-run/generator/provider.codex.jsonl",
                "sha256": "f" * 64,
            },
        ]
        transfer = {
            "schema_version": 1,
            "transport_role": "private-github-byte-preserving-mac-to-desktop-handoff",
            "repository": "WonJeong0913/merlin-desktop-sync",
            "branch": "codex/desktop-m3k-handoff",
            "payload_source_commit": "a" * 40,
            "payload": {
                "path": "transport/source.tar.gz",
                "bytes": archive_path.stat().st_size,
                "sha256": self._sha256(archive_path),
            },
            "snapshot_manifest": {
                "internal_path": manifest_path.name,
                "file_sha256": self._sha256(manifest_path),
                "entry_count": manifest["entry_count"],
                "entries_sha256": manifest["entries_sha256"],
            },
            "external_task_corpus": {
                "repository": "benchflow-ai/skillsbench",
                "commit": "c" * 40,
                "regular_blob_count": 2160,
            },
            "claim_boundary": {
                "payload_is_model_execution": False,
                "payload_is_benchmark_result": False,
                "clone_root_is_execution_root": False,
                "extracted_verified_payload_is_execution_source": True,
            },
        }
        (transport / "TRANSFER.json").write_text(
            json.dumps(transfer), encoding="utf-8"
        )
        private_metadata = {
            "schema_version": 1,
            "artifact_role": "private-requested-gpt56-model-authored-lifecycle-reproduction",
            "path": "transport/private.tar",
            "bytes": private_pack.stat().st_size,
            "sha256": self._sha256(private_pack),
            "record_count": 2,
            "chain_audit_semantic_sha256": "b" * 64,
            "chain_audit_file_sha256": "e" * 64,
            "authoring_raw_trace_sha256": "f" * 64,
            "reproduction_entrypoint": "experiments.mvp.audit_model_authored_skill_chain",
            "claim_boundary": {
                "private_only_do_not_publish": True,
                "raw_provider_text_included": True,
                "provider_thread_or_session_material_included": True,
                "requested_model_is_provider_resolved_model": False,
                "provider_native_skill_invocation_claimed": False,
                "full_benchmark_result": False,
            },
        }
        (transport / "PRIVATE_MODEL_AUTHORED_EVIDENCE.json").write_text(
            json.dumps(private_metadata), encoding="utf-8"
        )
        private_manifest = {
            "record_count": 2,
            "chain_audit_sha256": "b" * 64,
            "reproduction_entrypoint": "experiments.mvp.audit_model_authored_skill_chain",
            "records": records,
        }
        return carrier, private_manifest

    def test_receive_validates_both_artifacts_and_publishes_new_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            carrier, private_manifest = self._fixture(root)
            destination = root / "received-source"
            target = (
                "tools.validate_desktop_transport_receipt."
                "validate_private_model_authored_evidence_pack"
            )
            with patch(target, return_value=private_manifest):
                receipt = receive_transport(
                    transport_root=carrier, destination=destination
                )
            self.assertTrue(receipt["verified"])
            self.assertFalse(
                receipt["claim_boundary"]["receipt_is_model_execution"]
            )
            self.assertTrue((destination / "runner.py").is_file())
            self.assertFalse((root / "escape.txt").exists())
            with patch(target, return_value=private_manifest):
                with self.assertRaisesRegex(
                    DesktopTransportReceiptError, "new-only"
                ):
                    receive_transport(
                        transport_root=carrier, destination=destination
                    )

    def test_source_tamper_and_private_publication_boundary_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            carrier, private_manifest = self._fixture(root)
            archive = carrier / "transport" / "source.tar.gz"
            archive.write_bytes(archive.read_bytes() + b"tamper")
            target = (
                "tools.validate_desktop_transport_receipt."
                "validate_private_model_authored_evidence_pack"
            )
            with patch(target, return_value=private_manifest):
                with self.assertRaisesRegex(
                    DesktopTransportReceiptError, "byte size drifted"
                ):
                    validate_transport_root(carrier)

            carrier, private_manifest = self._fixture(root / "second")
            metadata_path = (
                carrier / "transport" / "PRIVATE_MODEL_AUTHORED_EVIDENCE.json"
            )
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["claim_boundary"]["private_only_do_not_publish"] = False
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            with patch(target, return_value=private_manifest):
                with self.assertRaisesRegex(
                    DesktopTransportReceiptError, "publication boundary"
                ):
                    validate_transport_root(carrier)

    def test_unsafe_tar_path_is_rejected_without_publishing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            carrier, private_manifest = self._fixture(root, unsafe_archive=True)
            destination = root / "received-source"
            target = (
                "tools.validate_desktop_transport_receipt."
                "validate_private_model_authored_evidence_pack"
            )
            with patch(target, return_value=private_manifest):
                with self.assertRaisesRegex(
                    DesktopTransportReceiptError, "unsafe path"
                ):
                    receive_transport(
                        transport_root=carrier, destination=destination
                    )
            self.assertFalse(destination.exists())
            self.assertFalse((root / "escape.txt").exists())


if __name__ == "__main__":
    unittest.main()
