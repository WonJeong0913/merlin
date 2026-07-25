from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from experiments.mvp.run_managed_skill_creation_demo import first_managed_creation_contract
from src.merlin_harness.managed_creation import (
    ManagedCreationError,
    ManagedSkillDraft,
    run_managed_creation,
    validate_portable_candidate,
    validate_proposal,
)
from src.merlin_harness.models import LifecycleStatus, ValidationResult


class ManagedCreationTests(unittest.TestCase):
    def test_first_candidate_passes_g0_through_g6_in_copy_on_write_library(self) -> None:
        proposal, draft, existing = first_managed_creation_contract()
        original_statuses = {skill.id: skill.status for skill in existing}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "creation"
            result = run_managed_creation(
                proposal=proposal,
                draft=draft,
                existing_skills=existing,
                output_root=output,
                external_validator=lambda _root: ValidationResult(
                    "skills-ref-0.1.1", True, evidence="pinned fixture"
                ),
            )

            self.assertTrue(result.adopted)
            self.assertEqual(result.lifecycle_action, "adopt")
            self.assertEqual(result.baseline_target_pass_rate, 0.0)
            self.assertEqual(result.candidate_target_pass_rate, 1.0)
            self.assertEqual(result.normalized_gain, 1.0)
            self.assertEqual(result.resolved_library_statuses["extract-todo-items"], "active")
            self.assertTrue(all(gate["passed"] for gate in result.gates))
            self.assertEqual(len(result.gates), 9)
            self.assertTrue((output / "candidate" / "extract-todo-items" / "SKILL.md").is_file())
            self.assertTrue((output / "candidate" / "extract-todo-items" / "agents" / "openai.yaml").is_file())
            self.assertTrue((output / "managed_creation_report.json").is_file())
            report = json.loads((output / "managed_creation_report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["evidence_boundary"]["selected"])
            self.assertFalse(report["evidence_boundary"]["provider_agent_selected"])
            self.assertFalse(report["evidence_boundary"]["actual_invocation_evidence_complete"])
            self.assertFalse(report["evidence_boundary"]["model_quality_claim"])
        self.assertEqual({skill.id: skill.status for skill in existing}, original_statuses)

    def test_failed_target_is_rejected_and_original_library_is_preserved(self) -> None:
        proposal, draft, existing = first_managed_creation_contract()
        cases = list(proposal.cases)
        target = cases[0]
        cases[0] = target.__class__(
            id=target.id,
            prompt=target.prompt,
            split=target.split,
            should_trigger=target.should_trigger,
            input_files=target.input_files,
            expected_files=(("todo-items.json", "wrong\n"),),
        )
        proposal = replace(proposal, cases=tuple(cases))
        with tempfile.TemporaryDirectory() as temporary:
            result = run_managed_creation(
                proposal=proposal,
                draft=draft,
                existing_skills=existing,
                output_root=Path(temporary) / "creation",
            )
        self.assertFalse(result.adopted)
        self.assertEqual(result.resolved_library_statuses["extract-todo-items"], "rejected")
        self.assertIsNone(result.provisional_library_snapshot_sha256)
        self.assertFalse(next(gate for gate in result.gates if gate["name"] == "G4_target")["passed"])
        self.assertTrue(all(skill.status == LifecycleStatus.ACTIVE for skill in existing))

    def test_existing_skill_coverage_fails_need_gate(self) -> None:
        proposal, _draft, existing = first_managed_creation_contract()
        cases = list(proposal.cases)
        case = cases[0]
        cases[0] = case.__class__(
            id=case.id,
            prompt="Create report.md in the workspace.",
            split=case.split,
            should_trigger=case.should_trigger,
            input_files=case.input_files,
            expected_files=case.expected_files,
        )
        proposal = replace(proposal, cases=tuple(cases))

        checks = validate_proposal(proposal, existing)

        self.assertFalse(checks[0].passed)
        self.assertIn("already cover", checks[0].evidence)

    def test_draft_rejects_path_traversal_and_skill_id_drift(self) -> None:
        proposal, draft, existing = first_managed_creation_contract()
        unsafe = ManagedSkillDraft(
            skill_id="other-skill",
            display_name=draft.display_name,
            description=draft.description,
            trigger=draft.trigger,
            do_not_use_when=draft.do_not_use_when,
            operation_id=draft.operation_id,
            input_path="../secret.txt",
            output_path=draft.output_path,
            prefix=draft.prefix,
            default_prompt=draft.default_prompt,
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "creation"
            with self.assertRaises(ManagedCreationError):
                run_managed_creation(
                    proposal=proposal,
                    draft=unsafe,
                    existing_skills=existing,
                    output_root=output,
                )
            rejection = json.loads(
                (output / "managed_creation_rejection.json").read_text(encoding="utf-8")
            )
            self.assertEqual(rejection["phase"], "preflight")
            self.assertFalse(rejection["adopted"])
            self.assertIsNone(rejection["provisional_library_snapshot_sha256"])
            self.assertTrue((output / "proposal.json").is_file())

    def test_portable_validator_rejects_unexpected_file_and_script_tamper(self) -> None:
        proposal, draft, existing = first_managed_creation_contract()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "creation"
            run_managed_creation(
                proposal=proposal,
                draft=draft,
                existing_skills=existing,
                output_root=output,
            )
            root = output / "candidate" / "extract-todo-items"
            (root / "README.md").write_text("extra", encoding="utf-8")
            checks = validate_portable_candidate(root, "extract-todo-items")
            self.assertFalse(all(check.passed for check in checks))
            (root / "README.md").unlink()
            with (root / "scripts" / "run.py").open("a", encoding="utf-8") as handle:
                handle.write("\nimport subprocess\n")
            checks = validate_portable_candidate(root, "extract-todo-items")
            self.assertFalse(all(check.passed for check in checks))

    def test_portable_validator_accepts_safe_plain_yaml_scalars(self) -> None:
        proposal, draft, existing = first_managed_creation_contract()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "creation"
            run_managed_creation(
                proposal=proposal,
                draft=draft,
                existing_skills=existing,
                output_root=output,
            )
            root = output / "candidate" / "extract-todo-items"
            skill_path = root / "SKILL.md"
            skill_md = skill_path.read_text(encoding="utf-8")
            skill_md = skill_md.replace('name: "extract-todo-items"', 'name: extract-todo-items')
            skill_md = skill_md.replace(
                'description: "Extract TODO-prefixed items from backlog.todo into todo-items.json. Use when a task explicitly requests TODO extraction into that JSON artifact."',
                'description: Extract TODO-prefixed items from backlog.todo into todo-items.json. Use when a task explicitly requests TODO extraction into that JSON artifact.',
            )
            skill_path.write_text(skill_md, encoding="utf-8")

            checks = validate_portable_candidate(root, "extract-todo-items")

            self.assertTrue(all(check.passed for check in checks))

    def test_portable_validator_keeps_format_and_safety_independent(self) -> None:
        proposal, draft, existing = first_managed_creation_contract()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "creation"
            run_managed_creation(
                proposal=proposal,
                draft=draft,
                existing_skills=existing,
                output_root=output,
            )
            root = output / "candidate" / "extract-todo-items"
            metadata_path = root / "agents" / "openai.yaml"
            metadata_path.write_text(
                "interface:\n  display_name: Extract TODO Items\n",
                encoding="utf-8",
            )

            checks = {item.name: item for item in validate_portable_candidate(root, "extract-todo-items")}

            self.assertFalse(checks["G1_format"].passed)
            self.assertTrue(checks["G2_safety"].passed)

    def test_output_is_new_only(self) -> None:
        proposal, draft, existing = first_managed_creation_contract()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "creation"
            output.mkdir()
            with self.assertRaises(ManagedCreationError):
                run_managed_creation(
                    proposal=proposal,
                    draft=draft,
                    existing_skills=existing,
                    output_root=output,
                )


if __name__ == "__main__":
    unittest.main()
