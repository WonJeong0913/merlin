from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.mvp.package_private_model_authored_evidence import (
    LIVE_RUN_FILES,
    PROMOTED_SESSION_FILES,
    SAFE_EVIDENCE_FILES,
    WORKSPACE_FILES,
    PrivateModelAuthoredEvidenceError,
    build_private_model_authored_evidence_pack,
    validate_private_model_authored_evidence_pack,
)


class PrivateModelAuthoredEvidencePackTests(unittest.TestCase):
    @staticmethod
    def _write_files(root: Path, names: tuple[str, ...], prefix: str) -> None:
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"{prefix}:{name}\n".encode())

    def _fixture(self, root: Path) -> dict[str, Path]:
        live = root / "live"
        safe = root / "safe"
        workspace = root / "workspace"
        session = workspace / ".merlin" / "chat" / "session-private"
        self._write_files(live, LIVE_RUN_FILES, "live")
        self._write_files(safe, SAFE_EVIDENCE_FILES, "safe")
        self._write_files(workspace, WORKSPACE_FILES, "workspace")
        self._write_files(session, PROMOTED_SESSION_FILES, "session")
        fresh = root / "fresh-audit.json"
        fresh.write_bytes((safe / "model_authored_skill_chain_audit.json").read_bytes())
        return {
            "live_run_root": live,
            "safe_evidence_root": safe,
            "promoted_chat_workspace": workspace,
            "promoted_chat_session_root": session,
            "fresh_audit_path": fresh,
        }

    @staticmethod
    def _fake_audit(_files: object) -> dict[str, str]:
        return {
            "candidate_skill_id": "extract-todo-items",
            "audit_sha256": "a" * 64,
        }

    def test_deterministic_pack_round_trip_and_new_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kwargs = self._fixture(root)
            first = root / "first.tar"
            second = root / "second.tar"
            target = (
                "experiments.mvp.package_private_model_authored_evidence."
                "_validate_chain_sources"
            )
            with patch(target, side_effect=self._fake_audit):
                manifest = build_private_model_authored_evidence_pack(
                    output_path=first, **kwargs
                )
                build_private_model_authored_evidence_pack(
                    output_path=second, **kwargs
                )
                self.assertEqual(
                    validate_private_model_authored_evidence_pack(first), manifest
                )
                with self.assertRaisesRegex(
                    PrivateModelAuthoredEvidenceError, "new-only"
                ):
                    build_private_model_authored_evidence_pack(
                        output_path=first, **kwargs
                    )
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(manifest["record_count"], 48)
            self.assertTrue(
                manifest["claim_boundary"]["private_only_do_not_publish"]
            )

    def test_member_tamper_and_rehashed_manifest_boundary_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kwargs = self._fixture(root)
            original = root / "original.tar"
            target = (
                "experiments.mvp.package_private_model_authored_evidence."
                "_validate_chain_sources"
            )
            with patch(target, side_effect=self._fake_audit):
                build_private_model_authored_evidence_pack(
                    output_path=original, **kwargs
                )
            with tarfile.open(original, "r:") as archive:
                members = {
                    member.name: archive.extractfile(member).read()
                    for member in archive.getmembers()
                }

            tampered = root / "tampered.tar"
            changed_name = "live-run/generator/provider.codex.jsonl"
            members[changed_name] += b"tampered"
            with tarfile.open(tampered, "w:", format=tarfile.USTAR_FORMAT) as archive:
                for name, raw in sorted(members.items()):
                    info = tarfile.TarInfo(name)
                    info.size = len(raw)
                    info.mode = 0o600
                    archive.addfile(info, io.BytesIO(raw))
            with patch(target, side_effect=self._fake_audit):
                with self.assertRaisesRegex(
                    PrivateModelAuthoredEvidenceError, "record coverage drifted"
                ):
                    validate_private_model_authored_evidence_pack(tampered)

            with tarfile.open(original, "r:") as archive:
                members = {
                    member.name: archive.extractfile(member).read()
                    for member in archive.getmembers()
                }
            manifest = json.loads(members["PRIVATE_EVIDENCE_MANIFEST.json"])
            manifest["claim_boundary"]["private_only_do_not_publish"] = False
            from src.merlin_harness.management import content_sha256

            manifest.pop("manifest_sha256")
            manifest["manifest_sha256"] = content_sha256(manifest)
            members["PRIVATE_EVIDENCE_MANIFEST.json"] = (
                json.dumps(manifest, indent=2, sort_keys=True) + "\n"
            ).encode()
            boundary = root / "boundary.tar"
            with tarfile.open(boundary, "w:", format=tarfile.USTAR_FORMAT) as archive:
                for name, raw in sorted(members.items()):
                    info = tarfile.TarInfo(name)
                    info.size = len(raw)
                    info.mode = 0o600
                    archive.addfile(info, io.BytesIO(raw))
            with patch(target, side_effect=self._fake_audit):
                with self.assertRaisesRegex(
                    PrivateModelAuthoredEvidenceError, "publication boundary"
                ):
                    validate_private_model_authored_evidence_pack(boundary)


if __name__ == "__main__":
    unittest.main()
