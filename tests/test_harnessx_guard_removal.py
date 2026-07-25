"""What happens when a policy-evolution candidate removes its own guard.

A self-managing harness must not be able to evolve away the mechanism that
makes it safe. These tests pin the current, verified behaviour of that path —
including one gap that is a design decision rather than a bug, so it is
documented here rather than silently asserted as correct.
"""

from __future__ import annotations

import unittest

from src.merlin_harness.harnessx_policy_evolution import (
    DEFAULT_VERIFIER_CASES,
    evaluate_live_tool_policy_variant,
    make_live_tool_policy_parent,
)
from src.merlin_harness.harnessx_runtime import (
    HarnessRiskTier,
    HarnessXChangeManifest,
    HarnessXEditKind,
    HarnessXHook,
    HarnessXProcessorEdit,
    apply_harnessx_change_manifest,
    gate_harnessx_candidate,
    make_default_harnessx_registry,
)


def _guard_removal_manifest(parent):
    entry = parent.processors[0]
    return HarnessXChangeManifest(
        id="mf-remove-guard",
        candidate_variant_id="cand-remove-guard",
        parent_variant_sha256=parent.sha256,
        rollback_variant_sha256=parent.sha256,
        # Self-declared as harmless. Nothing derives risk from what the edit does.
        risk_tier=HarnessRiskTier.LOW,
        rationale="remove the tool policy processor",
        evidence_trace_ids=("trace-1",),
        expected_improve_task_ids=("t1",),
        expected_regress_task_ids=(),
        edits=(
            HarnessXProcessorEdit(
                kind=HarnessXEditKind.REMOVE,
                hook=HarnessXHook.BEFORE_TOOL,
                singleton_group=entry.singleton_group,
                dimension="D1",
            ),
        ),
    )


class GuardRemovalVerifierTests(unittest.TestCase):
    def test_a_guard_removed_variant_fails_the_deny_cases(self) -> None:
        parent = make_live_tool_policy_parent()
        manifest = _guard_removal_manifest(parent)
        candidate = apply_harnessx_change_manifest(
            parent, manifest, make_default_harnessx_registry(), summary="guard removed"
        )
        self.assertEqual(len(candidate.processors), 0)

        results = evaluate_live_tool_policy_variant(candidate, DEFAULT_VERIFIER_CASES)
        by_case = {item["case_id"]: item for item in results}
        denies = [
            item for item in results if item["expected_decision"] == "deny"
        ]
        self.assertTrue(denies, "the case set must contain deny cases to be meaningful")
        for item in denies:
            self.assertEqual(item["observed_decision"], "allow", item["case_id"])
            self.assertFalse(item["passed"], item["case_id"])
            self.assertEqual(item["processor_outcome"], "not_run", item["case_id"])
        # The smoke result the pipeline would feed the gate.
        self.assertFalse(all(item["passed"] for item in results))
        self.assertIn("write-touch", by_case)

    def test_the_parent_is_safe_but_incomplete(self) -> None:
        """The baseline denies everything it should and allows too little.

        Widening the allowlist is what the evolution exists to do, so the
        parent failing an `allow` case is intended. What must never slip is the
        other direction: every `deny` case has to hold before evolution starts,
        otherwise a later "improvement" would be measured against an already
        unsafe baseline.
        """
        parent = make_live_tool_policy_parent()
        results = evaluate_live_tool_policy_variant(parent, DEFAULT_VERIFIER_CASES)
        denies = [item for item in results if item["expected_decision"] == "deny"]
        self.assertTrue(all(item["passed"] for item in denies))
        failed = [item["case_id"] for item in results if not item["passed"]]
        self.assertEqual(failed, ["directory-list-read"])


class GuardRemovalGateTests(unittest.TestCase):
    def test_a_truthful_smoke_result_rejects_the_candidate(self) -> None:
        parent = make_live_tool_policy_parent()
        manifest = _guard_removal_manifest(parent)
        candidate = apply_harnessx_change_manifest(
            parent, manifest, make_default_harnessx_registry(), summary="guard removed"
        )
        results = evaluate_live_tool_policy_variant(candidate, DEFAULT_VERIFIER_CASES)
        decision = gate_harnessx_candidate(
            parent=parent,
            candidate=candidate,
            manifest=manifest,
            smoke_passed=all(item["passed"] for item in results),
            previously_passing_task_ids=("t1",),
            candidate_task_outcomes={"t1": True},
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.resolution, "candidate_rejected_rollback_parent")
        self.assertEqual(decision.resolved_variant_id, parent.id)

    def test_KNOWN_GAP_the_gate_alone_promotes_a_guard_removal(self) -> None:
        """Documented gap, not an endorsement.

        With `smoke_passed=True` supplied by a caller, every gate check passes
        for a candidate that deleted its only guard, and no human approval is
        required. Three things combine to allow it:

        1. `seesaw_regression` measures task outcomes only. Removing a deny
           guard allows *more*, so nothing regresses and the check is blind to
           safety weakening.
        2. `risk_tier` is self-declared by the manifest; nothing derives it
           from what the edit actually does.
        3. `_validate_variant` accepts an empty processor set.

        The live loader still refuses to load a zero-processor policy, so the
        realistic outcome is a broken gate rather than a silently ungoverned
        one. Closing this properly is a design decision about the gate, so this
        test records the behaviour instead of asserting it is correct.
        """
        parent = make_live_tool_policy_parent()
        manifest = _guard_removal_manifest(parent)
        candidate = apply_harnessx_change_manifest(
            parent, manifest, make_default_harnessx_registry(), summary="guard removed"
        )
        decision = gate_harnessx_candidate(
            parent=parent,
            candidate=candidate,
            manifest=manifest,
            smoke_passed=True,
            previously_passing_task_ids=("t1",),
            candidate_task_outcomes={"t1": True},
        )
        self.assertTrue(decision.accepted)
        self.assertFalse(decision.requires_approval)
        self.assertTrue(all(check.passed for check in decision.checks))


if __name__ == "__main__":
    unittest.main()
