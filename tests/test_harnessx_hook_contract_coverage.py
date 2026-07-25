"""Structural invariants that must hold for every declared HarnessX hook.

These are cheap and boring on purpose. Adding a ninth hook to the enum without
an event type or a mutation allowlist would otherwise surface as a `KeyError`
somewhere inside a live pre-execution gate, which is the worst place to learn
about it.
"""

from __future__ import annotations

import unittest

from src.merlin_harness.harnessx_live_hook import (
    HarnessXLiveHookError,
    LiveToolPolicy,
    run_pre_tool_use,
)
from src.merlin_harness.harnessx_runtime import (
    EVENT_TYPES,
    PERMITTED_MUTATIONS,
    ExactToolCallPolicyProcessor,
    ExactToolInputPolicyProcessor,
    HarnessXHook,
    HarnessXVariantSpec,
)


class HookMapCoverageTests(unittest.TestCase):
    def test_every_hook_declares_an_event_type(self) -> None:
        self.assertEqual(set(EVENT_TYPES), set(HarnessXHook))

    def test_every_hook_declares_a_mutation_allowlist(self) -> None:
        self.assertEqual(set(PERMITTED_MUTATIONS), set(HarnessXHook))

    def test_terminal_hooks_permit_no_mutation(self) -> None:
        # STEP_END and TASK_END are observers. If either ever gains a writable
        # field, that is a deliberate contract change, not a refactor.
        self.assertEqual(PERMITTED_MUTATIONS[HarnessXHook.STEP_END], frozenset())
        self.assertEqual(PERMITTED_MUTATIONS[HarnessXHook.TASK_END], frozenset())

    def test_no_hook_permits_mutating_identity_fields(self) -> None:
        for hook, allowed in PERMITTED_MUTATIONS.items():
            self.assertNotIn("task_id", allowed, hook.value)
            self.assertNotIn("event_id", allowed, hook.value)
            self.assertNotIn("metadata", allowed, hook.value)


class LiveGateProcessorInvariantTests(unittest.TestCase):
    """The live gate is only a gate if a `before_tool` processor actually runs."""

    def test_both_admitted_policy_processors_are_bound_to_before_tool(self) -> None:
        for processor in (ExactToolInputPolicyProcessor, ExactToolCallPolicyProcessor):
            self.assertEqual(processor.hook, HarnessXHook.BEFORE_TOOL, processor.name)

    def test_a_policy_with_no_processor_cannot_produce_an_allow(self) -> None:
        # `load_live_tool_policy` already rejects this shape, so it is not
        # reachable through the CLI. This pins the behaviour one layer lower:
        # a hand-constructed gate that governs nothing must never return
        # "allow" and must never write an audit record claiming it gated.
        spec = HarnessXVariantSpec(
            id="empty-v1", parent_id=None, summary="no processors", processors=()
        )
        policy = LiveToolPolicy(
            policy_id="empty",
            model_id="gpt-5.6-terra",
            variant=spec,
            variant_sha256="b" * 64,
            sha256="a" * 64,
        )
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "shell",
            "tool_input": {"command": "rm -rf /"},
            "tool_use_id": "t1",
            "session_id": "s1",
            "turn_id": "u1",
        }
        with self.assertRaises(HarnessXLiveHookError):
            run_pre_tool_use(payload, policy=policy, audit_path="/dev/null")


if __name__ == "__main__":
    unittest.main()
