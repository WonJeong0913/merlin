from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.merlin_harness.model_candidate_quarantine import (
    MAX_FILE_BYTES,
    ModelCandidateEnvelope,
    ModelCandidateFile,
    ModelCandidateQuarantineError,
    parse_model_candidate_response,
    quarantine_model_candidate,
)


def _envelope() -> ModelCandidateEnvelope:
    return ModelCandidateEnvelope(
        candidate_skill_id="extract-actions",
        generator_backend="codex-cli",
        generator_model="gpt-5.6-terra",
        generator_effort="high",
        generator_prompt_sha256="a" * 64,
        generator_response_sha256="b" * 64,
        files=(
            ModelCandidateFile(
                "SKILL.md",
                "---\n"
                "name: extract-actions\n"
                "description: Use when action lines must be extracted from notes.\n"
                "---\n\n"
                "# Extract Actions\n\n"
                "Run the bundled script inside an isolated task workspace.\n",
            ),
            ModelCandidateFile(
                "agents/openai.yaml",
                "interface:\n"
                "  display_name: Extract Actions\n"
                "  short_description: Extract action lines into JSON.\n"
                "  default_prompt: Use $extract-actions to process action lines.\n",
            ),
            ModelCandidateFile(
                "scripts/run.py",
                "from pathlib import Path\n"
                "import json\n\n"
                "root = Path.cwd()\n"
                "items = [line for line in (root / 'notes.txt').read_text().splitlines() if line.startswith('ACTION:')]\n"
                "(root / 'actions.json').write_text(json.dumps({'items': items}))\n",
            ),
            ModelCandidateFile(
                "references/contract.md",
                "Input: notes.txt. Output: actions.json.\n",
            ),
        ),
    )


class ModelCandidateQuarantineTests(unittest.TestCase):
    def test_safe_bundle_is_persisted_inert_with_content_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "quarantine"
            result = quarantine_model_candidate(envelope=_envelope(), output_root=output)

            self.assertFalse(result.execution_allowed)
            self.assertFalse(result.promotion_allowed)
            self.assertEqual(result.lifecycle_status, "quarantined")
            self.assertEqual(len(result.files), 4)
            self.assertTrue(all(gate.passed for gate in result.gates))
            self.assertEqual([gate.name for gate in result.gates][-1], "Q5_portable_interface")
            self.assertTrue(
                (output / "candidate" / "extract-actions" / "scripts" / "run.py").is_file()
            )
            manifest = json.loads(
                (output / "quarantine_manifest.json").read_text(encoding="utf-8")
            )
            report = json.loads(
                (output / "quarantine_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["manifest_sha256"], result.manifest_sha256)
            self.assertFalse(report["evidence_boundary"]["host_execution"])
            self.assertFalse(report["evidence_boundary"]["adopted"])
            self.assertNotIn("content", manifest["files"][0])

    def test_path_traversal_absolute_hidden_and_unknown_paths_are_rejected(self) -> None:
        for path in ("../escape.py", "/tmp/escape.py", ".hidden", "README.md"):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as temporary:
                envelope = replace(
                    _envelope(),
                    files=_envelope().files + (ModelCandidateFile(path, "x"),),
                )
                with self.assertRaisesRegex(ModelCandidateQuarantineError, "path"):
                    quarantine_model_candidate(
                        envelope=envelope,
                        output_root=Path(temporary) / "quarantine",
                    )

    def test_script_network_process_and_dynamic_code_are_rejected(self) -> None:
        scripts = (
            "import subprocess\nsubprocess.run(['true'])\n",
            "import socket\nsocket.socket()\n",
            "eval('1 + 1')\n",
        )
        for script in scripts:
            with self.subTest(script=script), tempfile.TemporaryDirectory() as temporary:
                files = tuple(
                    replace(item, content=script) if item.path == "scripts/run.py" else item
                    for item in _envelope().files
                )
                with self.assertRaisesRegex(ModelCandidateQuarantineError, "quarantined"):
                    quarantine_model_candidate(
                        envelope=replace(_envelope(), files=files),
                        output_root=Path(temporary) / "quarantine",
                    )

    def test_oversize_nul_duplicate_and_identity_drift_fail_before_writes(self) -> None:
        cases = [
            replace(
                _envelope(),
                files=_envelope().files
                + (ModelCandidateFile("references/large.txt", "x" * (MAX_FILE_BYTES + 1)),),
            ),
            replace(
                _envelope(),
                files=_envelope().files + (ModelCandidateFile("references/nul.txt", "x\x00y"),),
            ),
            replace(
                _envelope(),
                files=_envelope().files + (_envelope().files[0],),
            ),
            replace(_envelope(), candidate_skill_id="different-id"),
        ]
        for index, envelope in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "quarantine"
                with self.assertRaises(ModelCandidateQuarantineError):
                    quarantine_model_candidate(envelope=envelope, output_root=output)
                self.assertFalse(output.exists())

    def test_quarantine_is_new_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "quarantine"
            quarantine_model_candidate(envelope=_envelope(), output_root=output)

            with self.assertRaisesRegex(ModelCandidateQuarantineError, "refusing to overwrite"):
                quarantine_model_candidate(envelope=_envelope(), output_root=output)

    def test_strict_provider_response_is_hashed_and_extra_fields_are_rejected(self) -> None:
        payload = {
            "candidate_skill_id": _envelope().candidate_skill_id,
            "files": [
                {"path": item.path, "content": item.content} for item in _envelope().files
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        envelope = parse_model_candidate_response(
            raw_response=raw,
            generator_backend="codex-cli",
            generator_model="gpt-5.6-terra",
            generator_effort="high",
            generator_prompt_sha256="d" * 64,
        )

        self.assertEqual(
            envelope.generator_response_sha256,
            hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        )
        payload["rationale"] = "not allowed"
        with self.assertRaisesRegex(ModelCandidateQuarantineError, "exactly"):
            parse_model_candidate_response(
                raw_response=json.dumps(payload),
                generator_backend="codex-cli",
                generator_model="gpt-5.6-terra",
                generator_effort="high",
                generator_prompt_sha256="d" * 64,
            )

    def test_secret_like_material_is_rejected(self) -> None:
        files = tuple(
            replace(item, content="token = 'sk-abcdefghijklmnopqrstuvwxyz123456'")
            if item.path == "scripts/run.py"
            else item
            for item in _envelope().files
        )
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ModelCandidateQuarantineError, "secret-like"):
                quarantine_model_candidate(
                    envelope=replace(_envelope(), files=files),
                    output_root=Path(temporary) / "quarantine",
                )

    def test_missing_or_ambiguous_openai_interface_fails_before_writes(self) -> None:
        invalid_contents = (
            None,
            (
                "interface:\n"
                "  display_name: Extract Actions\n"
                "  short_description: Extract action lines into JSON.\n"
                "  default_prompt: Process action lines.\n"
            ),
            (
                "display_name: Extract Actions\n"
                "short_description: Extract action lines into JSON.\n"
                "default_prompt: Use $extract-actions.\n"
            ),
        )
        for index, content in enumerate(invalid_contents):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                files = tuple(
                    item
                    for item in _envelope().files
                    if item.path != "agents/openai.yaml"
                )
                if content is not None:
                    files += (ModelCandidateFile("agents/openai.yaml", content),)
                output = Path(temporary) / "quarantine"
                with self.assertRaisesRegex(ModelCandidateQuarantineError, "openai.yaml"):
                    quarantine_model_candidate(
                        envelope=replace(_envelope(), files=files),
                        output_root=output,
                    )
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
