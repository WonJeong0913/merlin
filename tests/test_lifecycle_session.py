from __future__ import annotations

import unittest

from experiments.mvp.lifecycle_session import (
    LifecycleRecoverySession,
    LifecycleSessionError,
    SessionStage,
)
from src.merlin_harness.models import LifecyclePromotionCriteria


class LifecycleRecoverySessionTests(unittest.TestCase):
    def test_incremental_happy_path_accumulates_real_evidence(self) -> None:
        with LifecycleRecoverySession() as session:
            self.assertEqual(session.public_state()["stage"], "empty")
            session.load_sample()
            session.run_reference()
            self.assertEqual(session.public_state()["metrics"]["reference"]["passed"], 9)
            session.run_overloaded()
            overloaded = session.public_state()["metrics"]["overloaded"]
            self.assertEqual(overloaded["passed"], 1)
            self.assertAlmostEqual(overloaded["pi_m"], 8 / 9)
            session.diagnose()
            self.assertEqual(
                {item["skill_id"] for item in session.public_state()["decisions"]},
                {"aa-file-artifact-distractor", "aa-line-count-distractor"},
            )
            session.stage_hide()
            staged = session.public_state()["provisional_change"]
            self.assertEqual(staged["mode"], "copy_on_write")
            self.assertTrue(all(value == "active" for value in staged["original_statuses"].values()))
            session.verify_and_promote()

            report = session.final_report()
            recovered = report["conditions"]["Lifecycle recovered"]
            self.assertEqual(recovered["passed"], 9)
            self.assertEqual(recovered["pi_m"], 0.0)
            self.assertTrue(report["promotion"]["accepted"])
            self.assertEqual(session.public_state()["report_status"], "ready")

    def test_illegal_transitions_report_domain_errors_without_advancing(self) -> None:
        with LifecycleRecoverySession() as session:
            impossible = (
                session.run_reference,
                session.run_overloaded,
                session.diagnose,
                session.stage_hide,
                session.verify_and_promote,
                session.final_report,
            )
            for action in impossible:
                with self.subTest(action=action.__name__):
                    with self.assertRaises(LifecycleSessionError):
                        action()
                    self.assertEqual(session.stage, SessionStage.EMPTY)

            session.load_sample()
            with self.assertRaisesRegex(LifecycleSessionError, "expected 'reference_complete'"):
                session.run_overloaded()
            self.assertEqual(session.stage, SessionStage.LOADED)

    def test_threshold_is_user_settable_two_through_five_then_frozen(self) -> None:
        with LifecycleRecoverySession() as session:
            session.load_sample(min_shadowing_events=5)
            self.assertEqual(session.public_state()["min_shadowing_events"], 5)
            session.configure_threshold(3)
            session.run_reference()
            session.run_overloaded()
            session.configure_threshold(2)
            session.diagnose()
            self.assertTrue(session.public_state()["threshold_frozen"])
            with self.assertRaisesRegex(LifecycleSessionError, "only change"):
                session.configure_threshold(4)

        for invalid in (1, 6, True, 2.5, "2"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(LifecycleSessionError):
                    LifecycleRecoverySession(min_shadowing_events=invalid)  # type: ignore[arg-type]

    def test_reset_discards_evidence_and_restores_default_threshold(self) -> None:
        with LifecycleRecoverySession() as session:
            session.load_sample(min_shadowing_events=4)
            session.run_reference()
            state = session.reset()
            self.assertEqual(state["stage"], "empty")
            self.assertEqual(state["metrics"], {})
            self.assertEqual(state["decisions"], [])
            self.assertEqual(state["min_shadowing_events"], 2)
            self.assertEqual(state["next_actions"], ["reset", "load_sample"])
            session.load_sample()
            self.assertEqual(session.stage, SessionStage.LOADED)

    def test_rejected_promotion_discards_provisional_copy(self) -> None:
        criteria = LifecyclePromotionCriteria(min_pi_m_reduction=1.0)
        with LifecycleRecoverySession(promotion_criteria=criteria) as session:
            session.load_sample()
            session.run_reference()
            session.run_overloaded()
            session.diagnose()
            session.stage_hide()
            session.verify_and_promote()
            report = session.final_report()

            self.assertFalse(report["promotion"]["accepted"])
            self.assertTrue(report["promotion"]["rollback_required"])
            self.assertEqual(report["library_resolution"]["mode"], "original_retained")
            self.assertEqual(
                report["library_resolution"]["final_statuses"],
                report["provisional_change"]["original_statuses"],
            )
            self.assertIn("Lifecycle rejected — original retained", report["conditions"])


if __name__ == "__main__":
    unittest.main()
