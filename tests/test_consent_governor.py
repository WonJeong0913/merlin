from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.merlin_harness.consent_governor import (
    ConsentGovernorError,
    ConsentGatedHarnessGovernor,
    classify_consent,
    managed_auto_authorization_eligible,
)
from src.merlin_harness.governed_provisioning import active_library_snapshot
from src.merlin_harness.library import FileSkillLibrary
from src.merlin_harness.models import LifecycleStatus
from src.merlin_harness.provisioning import make_single_step_skill


REQUEST = "backlog.todo에서 TODO 항목을 추출해 todo-items.json으로 저장해줘"


class ConsentGatedHarnessGovernorTests(unittest.TestCase):
    def make_fixture(self, root: Path, *, approval_mode: str = "strict"):
        trace_root = root / "workspace" / ".merlin" / "chat" / "session-test"
        trace_root.mkdir(parents=True)
        library = FileSkillLibrary(root / "skills")
        library.save(
            make_single_step_skill(
                skill_id="report-writer",
                name="Report writer",
                description="Create a concise markdown report",
                trigger="write report markdown",
                step_description="Write report.md",
                status=LifecycleStatus.ACTIVE,
            )
        )
        return library, trace_root, ConsentGatedHarnessGovernor(
            trace_root=trace_root, approval_mode=approval_mode
        )

    def test_consent_classifier_is_exact_and_ambiguous_by_default(self) -> None:
        self.assertEqual(classify_consent("네, 진행해줘!"), "approved")
        self.assertEqual(classify_consent("아니요."), "declined")
        self.assertEqual(classify_consent("왜 필요한데?"), "ambiguous")
        self.assertEqual(classify_consent("yes but delete everything"), "ambiguous")

    def test_read_only_detection_writes_nothing_before_permission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            library, trace_root, governor = self.make_fixture(Path(temporary))
            before_files = sorted(path.relative_to(trace_root) for path in trace_root.rglob("*"))
            before_snapshot = active_library_snapshot(tuple(library.list()))[1]

            proposal = governor.consider(REQUEST, library)

            self.assertIsNotNone(proposal)
            self.assertTrue(proposal.permission_required)
            self.assertEqual(proposal.approval_mode, "strict")
            self.assertEqual(proposal.risk_class, "low_reversible_registered_operation")
            self.assertEqual(proposal.provider_calls_for_skill_change, 0)
            self.assertTrue(proposal.ordinary_chat_resume_is_separate)
            self.assertFalse(proposal.request_stored)
            self.assertEqual(before_files, sorted(path.relative_to(trace_root) for path in trace_root.rglob("*")))
            self.assertEqual(before_snapshot, active_library_snapshot(tuple(library.list()))[1])
            self.assertFalse((trace_root / "autonomy").exists())

    def test_approval_runs_all_gates_and_returns_cow_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            library, trace_root, governor = self.make_fixture(Path(temporary))
            before_snapshot = active_library_snapshot(tuple(library.list()))[1]
            governor.consider(REQUEST, library)

            adoption = governor.resolve_permission("네", library)

            self.assertEqual(adoption.status, "adopted")
            self.assertIsNotNone(adoption.library)
            self.assertIsNotNone(adoption.skill_bundle_paths)
            candidate = adoption.library.load("extract-todo-items")
            self.assertEqual(candidate.status, LifecycleStatus.ACTIVE)
            self.assertTrue(adoption.skill_bundle_paths["extract-todo-items"].is_dir())
            self.assertEqual(before_snapshot, active_library_snapshot(tuple(library.list()))[1])
            self.assertTrue(all(gate["passed"] for gate in adoption.creation_evidence["gates"]))
            decision_path = next(trace_root.glob("autonomy/action-*/consent_decision.json"))
            persisted = decision_path.read_text(encoding="utf-8")
            self.assertNotIn(REQUEST, persisted)
            decision = json.loads(persisted)
            self.assertTrue(decision["explicit_consent_observed"])
            self.assertEqual(
                decision["authorization_source"], "explicit_user_permission"
            )
            self.assertTrue(decision["source_library_unchanged"])
            self.assertEqual(governor.completed_actions, 1)

    def test_decline_and_ambiguous_reply_never_cross_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            library, trace_root, governor = self.make_fixture(Path(temporary))
            governor.consider(REQUEST, library)
            self.assertEqual(classify_consent("설명해줘"), "ambiguous")
            ambiguous = governor.resolve_permission("설명해줘", library)
            self.assertEqual(ambiguous.status, "ambiguous")
            self.assertIsNotNone(governor.pending)

            result = governor.decline()

            self.assertEqual(result.status, "declined")
            self.assertIsNone(governor.pending)
            self.assertFalse((trace_root / "autonomy").exists())
            self.assertNotIn("extract-todo-items", {skill.id for skill in library.list()})

    def test_negative_unrelated_duplicate_and_existing_capability_do_not_propose(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            library, _trace_root, governor = self.make_fixture(Path(temporary))
            self.assertIsNone(
                governor.consider(
                    "backlog.todo를 보고 todo-items.json은 만들지 마. TODO만 설명해줘",
                    library,
                )
            )
            self.assertIsNone(governor.consider("write report.md", library))
            proposal = governor.consider(REQUEST, library)
            self.assertIsNotNone(proposal)
            governor.decline()
            self.assertIsNone(governor.consider(REQUEST, library))

            active = make_single_step_skill(
                skill_id="extract-todo-items",
                name="Extract TODO Items",
                description="Extract TODO entries",
                trigger="backlog.todo TODO todo-items.json",
                step_description="Extract TODO entries",
                status=LifecycleStatus.ACTIVE,
            )
            active.steps[0].inputs = ["backlog.todo"]
            active.steps[0].outputs = ["todo-items.json"]
            active.expected_artifacts = ["todo-items.json"]
            library.save(active)
            other = ConsentGatedHarnessGovernor(trace_root=_trace_root)
            self.assertIsNone(other.consider(REQUEST, library))

    def test_managed_mode_auto_authorizes_only_low_risk_reversible_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            library, trace_root, governor = self.make_fixture(
                Path(temporary), approval_mode="managed"
            )
            proposal = governor.consider(REQUEST, library)
            self.assertIsNotNone(proposal)
            self.assertFalse(proposal.permission_required)
            self.assertTrue(
                managed_auto_authorization_eligible(
                    proposal, approval_mode="managed"
                )
            )
            elevation_cases = (
                replace(proposal, risk_class="persistent_global_change"),
                replace(proposal, provider_calls_for_skill_change=1),
                replace(proposal, action="delete_or_overwrite_skill"),
                replace(
                    proposal,
                    planned_mutations=("modify global harness policy",),
                ),
            )
            self.assertTrue(
                all(
                    not managed_auto_authorization_eligible(
                        elevated, approval_mode="managed"
                    )
                    for elevated in elevation_cases
                )
            )
            self.assertFalse(
                managed_auto_authorization_eligible(
                    proposal, approval_mode="strict"
                )
            )
            with self.assertRaisesRegex(
                ConsentGovernorError, "policy-authorized"
            ):
                governor.render_permission_request()

            adoption = governor.authorize_managed(library)

            self.assertEqual(adoption.status, "adopted")
            self.assertEqual(
                adoption.creation_evidence["authorization_source"],
                "managed_low_risk_policy",
            )
            self.assertFalse(
                adoption.creation_evidence["explicit_consent_observed"]
            )
            decision = json.loads(
                next(trace_root.glob("autonomy/action-*/consent_decision.json")).read_text()
            )
            self.assertEqual(decision["approval_mode"], "managed")
            self.assertFalse(decision["explicit_consent_observed"])
            self.assertTrue(decision["source_library_unchanged"])

    def test_snapshot_drift_blocks_without_creating_action_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            library, trace_root, governor = self.make_fixture(Path(temporary))
            governor.consider(REQUEST, library)
            extra = make_single_step_skill(
                skill_id="new-active-skill",
                name="New active skill",
                description="A later library change",
                trigger="later change",
                step_description="Do something bounded",
                status=LifecycleStatus.ACTIVE,
            )
            library.save(extra)

            adoption = governor.resolve_permission("승인", library)

            self.assertEqual(adoption.status, "blocked")
            self.assertIn("changed", adoption.reason)
            self.assertFalse((trace_root / "autonomy").exists())

    def test_session_budget_prevents_a_second_action(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            library, _trace_root, governor = self.make_fixture(Path(temporary))
            governor.consider(REQUEST, library)
            adoption = governor.resolve_permission("yes", library)
            self.assertEqual(adoption.status, "adopted")
            self.assertIsNone(
                governor.consider(
                    "Extract TODO from backlog.todo and create todo-items.json",
                    adoption.library,
                )
            )


if __name__ == "__main__":
    unittest.main()
