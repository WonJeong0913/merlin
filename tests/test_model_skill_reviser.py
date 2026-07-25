from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from src.merlin_harness.model_candidate_generator import ModelCandidateGenerationResult
from src.merlin_harness.model_candidate_quarantine import (
    ModelCandidateEnvelope,
    ModelCandidateFile,
    quarantine_model_candidate,
)
from src.merlin_harness.model_skill_reviser import CodexModelSkillReviser
from src.merlin_harness.models import LifecycleStatus, SkillArtifact, SkillStep
from src.merlin_harness.skill_repair import (
    RepairCaseResult,
    RepairDiagnosis,
    SkillRepairError,
    skill_library_snapshot_sha256,
)


SKILL_MD = """---
name: \"extract-todo-items\"
description: \"Use when TODO lines must be extracted from backlog.todo.\"
---

# Extract TODO Items

Run the isolated script.
"""
OPENAI_YAML = """interface:
  display_name: Extract TODO Items
  short_description: Extract TODO items
  default_prompt: Use $extract-todo-items.
"""
SCRIPT_V1 = """import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--workspace', required=True)
args = parser.parse_args()
root = Path(args.workspace).resolve()
items = [line[5:].strip() for line in (root / 'backlog.todo').read_text().splitlines() if line.startswith('TODO:')]
(root / 'todo-items.json').write_text(json.dumps({'items': items}, ensure_ascii=False, indent=2, sort_keys=True) + '\\n')
"""
SCRIPT_V2 = SCRIPT_V1.replace(
    "if line.startswith('TODO:')",
    "if line.lstrip().startswith('TODO:')",
)


def _envelope(script: str, *, skill_md: str = SKILL_MD) -> ModelCandidateEnvelope:
    return ModelCandidateEnvelope(
        candidate_skill_id="extract-todo-items",
        generator_backend="openai-codex-cli",
        generator_model="gpt-5.6-terra",
        generator_effort="high",
        generator_prompt_sha256="a" * 64,
        generator_response_sha256="b" * 64,
        generator_raw_trace_sha256="c" * 64,
        files=(
            ModelCandidateFile("SKILL.md", skill_md),
            ModelCandidateFile("agents/openai.yaml", OPENAI_YAML),
            ModelCandidateFile("scripts/run.py", script),
        ),
    )


def _skill() -> SkillArtifact:
    return SkillArtifact(
        id="extract-todo-items",
        name="Extract TODO Items",
        description="Extract TODO-prefixed items from backlog.todo.",
        trigger="Use when backlog.todo TODO entries must become todo-items.json.",
        do_not_use_when=["Do not use for unrelated files."],
        steps=[
            SkillStep(
                id="extract",
                description="Run the isolated extractor.",
                kind="script",
                outputs=["todo-items.json"],
                script_path="scripts/run.py",
            )
        ],
        validators=["exact_file:todo-items.json"],
        expected_artifacts=["todo-items.json"],
        provenance_trace_ids=["baseline-model-trace"],
        status=LifecycleStatus.ACTIVE,
        version=1,
        metadata={"baseline": True},
    )


class FakeGenerator:
    def __init__(self, envelope: ModelCandidateEnvelope) -> None:
        self.envelope = envelope
        self.prompt = ""

    def generate(self, *, candidate_skill_id, prompt, run_root):
        del run_root
        self.prompt = prompt
        if candidate_skill_id != self.envelope.candidate_skill_id:
            raise AssertionError("candidate mismatch")
        return ModelCandidateGenerationResult(
            envelope=self.envelope,
            requested_model_id="gpt-5.6-terra",
            provider_reported_model_ids=(),
            model_evidence_level="requested_cli_contract_only",
            effort="high",
            cli_version="codex-cli test",
            thread_id="thread-test",
            turn_id=None,
            raw_trace_pointer="provider.codex.jsonl",
            raw_trace_sha256="c" * 64,
            response_sha256="b" * 64,
            schema_sha256="d" * 64,
            prompt_sha256="a" * 64,
            event_count=1,
            item_types=("agent_message",),
        )


class ModelSkillReviserTests(unittest.TestCase):
    def _diagnosis(self, skill: SkillArtifact) -> RepairDiagnosis:
        return RepairDiagnosis(
            skill_id=skill.id,
            failure_kind="skill_local",
            trace_ids=("target-failure-trace",),
            failed_target_case_ids=("target-spacing",),
            verifier_feedback=("accept optional leading whitespace before TODO:",),
            library_snapshot_sha256=skill_library_snapshot_sha256((skill,)),
        )

    def test_model_repair_is_target_only_and_script_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = quarantine_model_candidate(
                envelope=_envelope(SCRIPT_V1), output_root=root / "original"
            )
            generator = FakeGenerator(_envelope(SCRIPT_V2))
            reviser = CodexModelSkillReviser(
                generator=generator,
                run_root=root / "repair",
                original_quarantine_root=root / "original",
                original_manifest_sha256=original.manifest_sha256,
            )
            skill = _skill()
            candidates = reviser.propose(
                skill,
                self._diagnosis(skill),
                (
                    RepairCaseResult(
                        "target-spacing",
                        "exact-target-v1",
                        False,
                        score=0.0,
                        evidence="expected two items; observed zero",
                    ),
                ),
                1,
            )

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].version, 2)
            self.assertEqual(candidates[0].status, LifecycleStatus.CANDIDATE)
            self.assertIn("target-spacing", generator.prompt)
            self.assertNotIn("held-out-korean", generator.prompt)
            self.assertNotIn("regression-case-1", generator.prompt)
            binding = reviser.bindings["extract-todo-items@v2"]
            self.assertFalse(binding.to_safe_dict()["evidence_boundary"]["held_out_visible_to_reviser"])
            self.assertTrue((binding.quarantine_root / "quarantine_manifest.json").is_file())
            self.assertEqual(skill.version, 1)
            self.assertEqual(skill.metadata, {"baseline": True})

    def test_model_repair_cannot_change_routing_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = quarantine_model_candidate(
                envelope=_envelope(SCRIPT_V1), output_root=root / "original"
            )
            changed_doc = SKILL_MD.replace("TODO lines", "every file")
            reviser = CodexModelSkillReviser(
                generator=FakeGenerator(_envelope(SCRIPT_V2, skill_md=changed_doc)),
                run_root=root / "repair",
                original_quarantine_root=root / "original",
                original_manifest_sha256=original.manifest_sha256,
            )
            skill = _skill()
            with self.assertRaisesRegex(SkillRepairError, "immutable routing"):
                reviser.propose(
                    skill,
                    self._diagnosis(skill),
                    (
                        RepairCaseResult(
                            "target-spacing", "exact-target-v1", False
                        ),
                    ),
                    1,
                )

    def test_original_bundle_drift_fails_before_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = quarantine_model_candidate(
                envelope=_envelope(SCRIPT_V1), output_root=root / "original"
            )
            script = (
                root
                / "original/candidate/extract-todo-items/scripts/run.py"
            )
            script.write_text(script.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
            generator = FakeGenerator(_envelope(SCRIPT_V2))
            reviser = CodexModelSkillReviser(
                generator=generator,
                run_root=root / "repair",
                original_quarantine_root=root / "original",
                original_manifest_sha256=original.manifest_sha256,
            )
            skill = _skill()
            with self.assertRaisesRegex(SkillRepairError, "bytes drifted"):
                reviser.propose(
                    skill,
                    self._diagnosis(skill),
                    (RepairCaseResult("target-spacing", "exact-target-v1", False),),
                    1,
                )
            self.assertEqual(generator.prompt, "")


if __name__ == "__main__":
    unittest.main()
