from __future__ import annotations

import unittest
import asyncio
from dataclasses import replace
from typing import Any, Mapping

from src.merlin_harness.harnessx_runtime import (
    BeforeModelEvent,
    ExactToolCallPolicyProcessor,
    ExactToolInputPolicyProcessor,
    HarnessRiskTier,
    HarnessXApprovalPolicy,
    HarnessXChangeManifest,
    HarnessXContractError,
    HarnessXEditKind,
    HarnessXHook,
    HarnessXInterrupt,
    HarnessXProcessorEdit,
    HarnessXProcessorRegistry,
    HarnessXRuntime,
    ModelResponseEvent,
    ProcessorManifestEntry,
    ProcessorOrder,
    ProcessorOutcome,
    StepEndEvent,
    StepStartEvent,
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolCall,
    ToolResultEvent,
    apply_harnessx_change_manifest,
    build_harnessx_runtime_from_variant,
    gate_harnessx_candidate,
    make_default_harnessx_registry,
    make_default_harnessx_runtime,
    processor_manifest_entry,
    snapshot_harnessx_variant,
)


class PromptSuffixProcessor:
    name = "prompt_suffix"
    hook = HarnessXHook.TASK_START
    singleton_group = "system_prompt_policy"
    order = ProcessorOrder.NORMAL
    after: tuple[str, ...] = ()

    def __init__(self, suffix: str = " [safe]") -> None:
        self.suffix = suffix

    async def process(self, event):
        yield replace(event, system_prompt=event.system_prompt + self.suffix)

    def config(self) -> Mapping[str, Any]:
        return {"suffix": self.suffix}


class PromptPrefixProcessor:
    name = "prompt_prefix"
    hook = HarnessXHook.TASK_START
    singleton_group = "prompt_prefix"
    order = ProcessorOrder.PRE
    after: tuple[str, ...] = ()

    async def process(self, event):
        yield replace(event, system_prompt="prefix " + event.system_prompt)

    def config(self):
        return {}


class PromptAfterSuffixProcessor:
    name = "prompt_after_suffix"
    hook = HarnessXHook.TASK_START
    singleton_group = "prompt_after"
    order = ProcessorOrder.PRE
    after = ("system_prompt_policy",)

    async def process(self, event):
        yield replace(event, system_prompt=event.system_prompt + " after")

    def config(self):
        return {}


class PromptInterceptProcessor:
    name = "prompt_intercept"
    hook = HarnessXHook.TASK_START
    singleton_group = "prompt_intercept"
    order = ProcessorOrder.POST
    after: tuple[str, ...] = ()

    async def process(self, event):
        if False:
            yield event

    def config(self):
        return {}


class HistorySplitProcessor:
    name = "history_split"
    hook = HarnessXHook.STEP_START
    singleton_group = "history_split"
    order = ProcessorOrder.NORMAL
    after: tuple[str, ...] = ()

    async def process(self, event):
        yield replace(event, event_id=f"{event.event_id}:a", history=event.history + ("a",))
        yield replace(event, event_id=f"{event.event_id}:b", history=event.history + ("b",))

    def config(self):
        return {}


class ForbiddenMutationProcessor:
    name = "forbidden_mutation"
    hook = HarnessXHook.TASK_START
    singleton_group = "forbidden"
    order = ProcessorOrder.NORMAL
    after: tuple[str, ...] = ()

    async def process(self, event):
        yield replace(event, task_id="different")

    def config(self):
        return {}


class ReadOnlyMutationProcessor:
    name = "read_only_mutation"
    hook = HarnessXHook.STEP_END
    singleton_group = "read_only"
    order = ProcessorOrder.NORMAL
    after: tuple[str, ...] = ()

    async def process(self, event):
        yield replace(event, status="changed")

    def config(self):
        return {}


class ApprovalProcessor:
    name = "approval_policy"
    hook = HarnessXHook.BEFORE_TOOL
    singleton_group = "tool_approval"
    order = ProcessorOrder.NORMAL
    after: tuple[str, ...] = ()

    def __init__(self, risky_tools: tuple[str, ...]) -> None:
        self.risky_tools = risky_tools

    async def process(self, event):
        yield replace(event, approval_required=event.tool_name in self.risky_tools)

    def config(self):
        return {"risky_tools": list(self.risky_tools)}


class InterruptProcessor:
    name = "interrupt"
    hook = HarnessXHook.TASK_START
    singleton_group = "interrupt"
    order = ProcessorOrder.NORMAL
    after: tuple[str, ...] = ()

    async def process(self, event):
        raise HarnessXInterrupt("stop")
        yield event

    def config(self):
        return {}


def registry() -> HarnessXProcessorRegistry:
    value = HarnessXProcessorRegistry()
    value.register("prompt_suffix", lambda config: PromptSuffixProcessor(str(config["suffix"])))
    value.register("prompt_prefix", lambda config: PromptPrefixProcessor())
    value.register("prompt_after_suffix", lambda config: PromptAfterSuffixProcessor())
    value.register(
        "approval_policy",
        lambda config: ApprovalProcessor(tuple(str(item) for item in config["risky_tools"])),
    )
    return value


def task_start() -> TaskStartEvent:
    return TaskStartEvent(
        event_id="event-1",
        task_id="task-1",
        system_prompt="system",
    )


class HarnessXRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_event_and_processor_types_fail_closed(self) -> None:
        with self.assertRaisesRegex(HarnessXContractError, "tool call name is required"):
            ToolCall(1)  # type: ignore[arg-type]
        with self.assertRaisesRegex(HarnessXContractError, "event_id is required"):
            await HarnessXRuntime().emit(
                TaskStartEvent(event_id=1, task_id="task", system_prompt="system")  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(HarnessXContractError, "non-negative integer"):
            await HarnessXRuntime().emit(
                StepStartEvent(event_id="step", task_id="task", step_index=True)
            )
        with self.assertRaisesRegex(HarnessXContractError, "tuple of strings"):
            await HarnessXRuntime().emit(
                StepStartEvent(
                    event_id="step",
                    task_id="task",
                    step_index=1,
                    history=["not", "a", "tuple"],  # type: ignore[arg-type]
                )
            )

        class BadDependency(PromptPrefixProcessor):
            name = "bad_dependency"
            singleton_group = "bad_dependency"
            after = "not-a-tuple"

        with self.assertRaisesRegex(HarnessXContractError, "tuple of non-empty strings"):
            HarnessXRuntime([BadDependency()])

    async def test_cyclic_and_excessively_nested_metadata_fail_closed(self) -> None:
        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        with self.assertRaisesRegex(HarnessXContractError, "cyclic container"):
            await HarnessXRuntime().emit(
                TaskStartEvent(
                    event_id="event-cycle",
                    task_id="task-cycle",
                    system_prompt="system",
                    metadata=cyclic,
                )
            )

        nested: dict[str, object] = {}
        cursor = nested
        for _ in range(66):
            child: dict[str, object] = {}
            cursor["child"] = child
            cursor = child
        with self.assertRaisesRegex(HarnessXContractError, "maximum JSON depth"):
            await HarnessXRuntime().emit(
                TaskStartEvent(
                    event_id="event-depth",
                    task_id="task-depth",
                    system_prompt="system",
                    metadata=nested,
                )
            )

    async def test_tool_contracts_reject_non_object_json_and_string_allowlist(self) -> None:
        with self.assertRaisesRegex(HarnessXContractError, "must encode an object"):
            ToolCall("shell", "[]")
        with self.assertRaisesRegex(HarnessXContractError, "must encode an object"):
            ToolCallEvent(
                event_id="tool",
                task_id="task",
                step_index=1,
                tool_name="shell",
                tool_input_json="[]",
            )
        with self.assertRaisesRegex(HarnessXContractError, "sequence of strings"):
            make_default_harnessx_runtime(
                system_prompt_suffix=" policy",
                allowed_tool_calls="shell",
            )

    async def test_exact_multitool_policy_matches_canonical_json_only(self) -> None:
        processor = ExactToolCallPolicyProcessor(
            allowed_tool_inputs=(
                {
                    "tool_name": "Read",
                    "tool_input": {"file_path": "/private/tmp/input.txt"},
                },
                {
                    "tool_name": "Grep",
                    "tool_input": {
                        "pattern": "TODO",
                        "path": "/private/tmp/workspace",
                    },
                },
            ),
            denied_tools=("Write", "Edit"),
        )
        runtime = HarnessXRuntime((processor,))
        allowed = await runtime.emit(
            ToolCallEvent(
                event_id="read-1",
                task_id="task",
                step_index=1,
                tool_name="Read",
                tool_input_json='{"file_path":"/private/tmp/input.txt"}',
            )
        )
        changed = await runtime.emit(
            ToolCallEvent(
                event_id="read-2",
                task_id="task",
                step_index=1,
                tool_name="Read",
                tool_input_json='{"file_path":"/private/tmp/other.txt"}',
            )
        )
        denied = await runtime.emit(
            ToolCallEvent(
                event_id="write-1",
                task_id="task",
                step_index=1,
                tool_name="Write",
                tool_input_json='{"file_path":"/private/tmp/input.txt"}',
            )
        )
        self.assertFalse(allowed.intercepted)
        self.assertTrue(changed.intercepted)
        self.assertTrue(denied.intercepted)
        rebuilt = build_harnessx_runtime_from_variant(
            snapshot_harnessx_variant(
                runtime,
                variant_id="exact-multitool-v1",
                summary="test",
            ),
            make_default_harnessx_registry(),
        )
        self.assertEqual(rebuilt.manifest(), runtime.manifest())

    async def test_typed_transform_and_audit(self) -> None:
        runtime = HarnessXRuntime([PromptSuffixProcessor()])
        result = await runtime.emit(task_start())
        self.assertEqual(result.events[0].system_prompt, "system [safe]")
        self.assertEqual(result.audit[0].outcome, ProcessorOutcome.TRANSFORM)
        self.assertEqual(result.audit[0].hook, HarnessXHook.TASK_START)

    async def test_split_and_intercept_are_first_class_outcomes(self) -> None:
        split = await HarnessXRuntime([HistorySplitProcessor()]).emit(
            StepStartEvent(event_id="step", task_id="task", step_index=1)
        )
        self.assertEqual([event.event_id for event in split.events], ["step:a", "step:b"])
        self.assertEqual(split.audit[0].outcome, ProcessorOutcome.SPLIT)

        intercepted = await HarnessXRuntime([PromptInterceptProcessor()]).emit(task_start())
        self.assertTrue(intercepted.intercepted)
        self.assertEqual(intercepted.audit[0].outcome, ProcessorOutcome.INTERCEPT)

    async def test_hook_mutation_contracts_fail_closed(self) -> None:
        with self.assertRaisesRegex(HarnessXContractError, "forbidden fields: task_id"):
            await HarnessXRuntime([ForbiddenMutationProcessor()]).emit(task_start())
        with self.assertRaisesRegex(HarnessXContractError, "forbidden fields: status"):
            await HarnessXRuntime([ReadOnlyMutationProcessor()]).emit(
                StepEndEvent(event_id="end", task_id="task", step_index=1, status="ok")
            )

    async def test_input_mutation_cannot_corrupt_caller_event(self) -> None:
        class NestedMutation:
            name = "nested_mutation"
            hook = HarnessXHook.TASK_START
            singleton_group = "nested"
            order = ProcessorOrder.NORMAL
            after: tuple[str, ...] = ()

            async def process(self, event):
                event.metadata["unsafe"] = True
                yield event

            def config(self):
                return {}

        original = replace(task_start(), metadata={"safe": True})
        with self.assertRaisesRegex(HarnessXContractError, "forbidden fields: metadata"):
            await HarnessXRuntime([NestedMutation()]).emit(original)
        self.assertEqual(original.metadata, {"safe": True})

    async def test_before_tool_can_set_approval_only(self) -> None:
        runtime = HarnessXRuntime([ApprovalProcessor(("shell",))])
        result = await runtime.emit(
            ToolCallEvent(
                event_id="tool",
                task_id="task",
                step_index=1,
                tool_name="shell",
                tool_input_json="{}",
            )
        )
        self.assertTrue(result.events[0].approval_required)

    async def test_exact_live_tool_input_policy_allows_only_literal_contract(self) -> None:
        runtime = HarnessXRuntime(
            [
                ExactToolInputPolicyProcessor(
                    allowed_commands=("pwd", "/bin/pwd"),
                    denied_tools=("apply_patch",),
                )
            ]
        )
        allowed = await runtime.emit(
            ToolCallEvent(
                event_id="allowed",
                task_id="task",
                step_index=1,
                tool_name="Bash",
                tool_input_json='{"command":"pwd"}',
            )
        )
        denied_shell = await runtime.emit(
            ToolCallEvent(
                event_id="denied-shell",
                task_id="task",
                step_index=1,
                tool_name="Bash",
                tool_input_json='{"command":"pwd; touch blocked.txt"}',
            )
        )
        denied_patch = await runtime.emit(
            ToolCallEvent(
                event_id="denied-patch",
                task_id="task",
                step_index=1,
                tool_name="apply_patch",
                tool_input_json='{"command":"*** Begin Patch"}',
            )
        )
        self.assertFalse(allowed.intercepted)
        self.assertEqual(allowed.audit[0].outcome, ProcessorOutcome.PASS_THROUGH)
        self.assertTrue(denied_shell.intercepted)
        self.assertEqual(denied_shell.audit[0].outcome, ProcessorOutcome.INTERCEPT)
        self.assertTrue(denied_patch.intercepted)

    async def test_interrupt_propagates_as_control_signal(self) -> None:
        with self.assertRaisesRegex(HarnessXInterrupt, "stop"):
            await HarnessXRuntime([InterruptProcessor()]).emit(task_start())

    async def test_processor_timeout_and_event_size_are_bounded(self) -> None:
        class HangingProcessor(PromptPrefixProcessor):
            name = "hanging"
            singleton_group = "hanging"

            async def process(self, event):
                await asyncio.sleep(0.05)
                yield event

        with self.assertRaisesRegex(RuntimeError, "exceeded timeout"):
            await HarnessXRuntime(
                [HangingProcessor()], processor_timeout_sec=0.001
            ).emit(task_start())
        with self.assertRaisesRegex(HarnessXContractError, "event exceeds configured byte bound"):
            await HarnessXRuntime(max_event_bytes=32).emit(task_start())

    async def test_single_output_cannot_reidentify_event(self) -> None:
        class Reidentify(PromptPrefixProcessor):
            name = "reidentify"
            singleton_group = "reidentify"

            async def process(self, event):
                yield replace(event, event_id="different")

        with self.assertRaisesRegex(HarnessXContractError, "cannot change event_id"):
            await HarnessXRuntime([Reidentify()]).emit(task_start())

    async def test_order_dependency_and_singleton_contracts(self) -> None:
        runtime = HarnessXRuntime(
            [PromptAfterSuffixProcessor(), PromptSuffixProcessor(), PromptPrefixProcessor()]
        )
        self.assertEqual(
            [processor.name for processor in runtime.processors(HarnessXHook.TASK_START)],
            ["prompt_prefix", "prompt_suffix", "prompt_after_suffix"],
        )
        result = await runtime.emit(task_start())
        self.assertEqual(result.events[0].system_prompt, "prefix system [safe] after")

        with self.assertRaisesRegex(HarnessXContractError, "singleton processor conflict"):
            HarnessXRuntime([PromptSuffixProcessor(" one"), PromptSuffixProcessor(" two")])

    async def test_dependency_cycle_is_rejected(self) -> None:
        class A(PromptPrefixProcessor):
            name = "a"
            singleton_group = "a"
            after = ("b",)

        class B(PromptPrefixProcessor):
            name = "b"
            singleton_group = "b"
            after = ("a",)

        with self.assertRaisesRegex(HarnessXContractError, "dependency cycle"):
            HarnessXRuntime([A(), B()])

    async def test_singleton_group_is_global_across_hooks(self) -> None:
        class CrossHook(ApprovalProcessor):
            singleton_group = "system_prompt_policy"

        with self.assertRaisesRegex(HarnessXContractError, "global singleton processor conflict"):
            HarnessXRuntime([PromptSuffixProcessor(), CrossHook(("shell",))])

    async def test_default_runtime_covers_all_eight_typed_hooks(self) -> None:
        runtime = make_default_harnessx_runtime(
            system_prompt_suffix=" policy",
            max_history_messages=2,
            max_user_content_chars=4,
            allowed_tool_calls=("read",),
            auto_approve_tools=("read",),
            always_require_approval_tools=("shell",),
            max_tool_result_chars=3,
        )
        self.assertEqual({entry.hook for entry in runtime.manifest()}, set(HarnessXHook))

        start = await runtime.emit(task_start())
        self.assertEqual(start.events[0].system_prompt, "system policy")
        step = await runtime.emit(
            StepStartEvent(
                event_id="step",
                task_id="task",
                step_index=1,
                history=("a", "b", "c"),
            )
        )
        self.assertEqual(step.events[0].history, ("b", "c"))
        before_model = await runtime.emit(
            BeforeModelEvent(
                event_id="model-in",
                task_id="task",
                step_index=1,
                model_role="main",
                last_user_content="abcdef",
            )
        )
        self.assertEqual(before_model.events[0].last_user_content, "abcd")
        after_model = await runtime.emit(
            ModelResponseEvent(
                event_id="model-out",
                task_id="task",
                step_index=1,
                response_content="response",
                tool_calls=(ToolCall("read"), ToolCall("shell")),
            )
        )
        self.assertEqual([call.name for call in after_model.events[0].tool_calls], ["read"])
        before_tool = await runtime.emit(
            ToolCallEvent(
                event_id="tool-in",
                task_id="task",
                step_index=1,
                tool_name="shell",
                tool_input_json="{}",
            )
        )
        self.assertTrue(before_tool.events[0].approval_required)
        after_tool = await runtime.emit(
            ToolResultEvent(
                event_id="tool-out",
                task_id="task",
                step_index=1,
                tool_name="read",
                tool_result="abcdef",
            )
        )
        self.assertEqual(after_tool.events[0].tool_result, "abc")
        step_end = await runtime.emit(
            StepEndEvent(event_id="step-end", task_id="task", step_index=1, status="ok")
        )
        task_end = await runtime.emit(
            TaskEndEvent(event_id="task-end", task_id="task", status="ok")
        )
        self.assertEqual(step_end.audit[0].outcome, ProcessorOutcome.PASS_THROUGH)
        self.assertEqual(task_end.audit[0].outcome, ProcessorOutcome.PASS_THROUGH)

    def test_default_runtime_round_trip_uses_built_in_allowlist(self) -> None:
        runtime = make_default_harnessx_runtime(
            system_prompt_suffix=" policy",
            allowed_tool_calls=("read",),
            auto_approve_tools=("read",),
        )
        spec = snapshot_harnessx_variant(
            runtime,
            variant_id="default-v1",
            summary="all typed hooks",
        )
        restored = build_harnessx_runtime_from_variant(spec, make_default_harnessx_registry())
        self.assertEqual(restored.manifest(), runtime.manifest())


class HarnessXVariantTests(unittest.TestCase):
    def test_variant_round_trip_is_hash_stable_and_allowlisted(self) -> None:
        runtime = HarnessXRuntime([PromptSuffixProcessor(), ApprovalProcessor(("shell",))])
        spec = snapshot_harnessx_variant(
            runtime,
            variant_id="parent",
            summary="parent harness",
            slots={"sandbox": "read-only"},
        )
        restored = build_harnessx_runtime_from_variant(spec, registry())
        self.assertEqual(restored.manifest(), spec.processors)
        rebuilt = snapshot_harnessx_variant(
            restored,
            variant_id="parent",
            summary="parent harness",
            slots={"sandbox": "read-only"},
        )
        self.assertEqual(rebuilt.sha256, spec.sha256)

    def test_unregistered_processor_code_cannot_load(self) -> None:
        spec = snapshot_harnessx_variant(
            HarnessXRuntime([HistorySplitProcessor()]),
            variant_id="unknown",
            summary="unknown processor",
        )
        with self.assertRaisesRegex(HarnessXContractError, "unregistered processor code"):
            build_harnessx_runtime_from_variant(spec, registry())

    def test_change_manifest_applies_typed_replace_with_rollback_hash(self) -> None:
        parent = snapshot_harnessx_variant(
            HarnessXRuntime([PromptSuffixProcessor(" one")]),
            variant_id="parent",
            summary="parent",
        )
        replacement = processor_manifest_entry(PromptSuffixProcessor(" two"))
        manifest = HarnessXChangeManifest(
            id="change-1",
            candidate_variant_id="candidate",
            parent_variant_sha256=parent.sha256,
            rollback_variant_sha256=parent.sha256,
            rationale="trace-backed prompt policy repair",
            evidence_trace_ids=("trace-1",),
            expected_improve_task_ids=("task-a",),
            expected_regress_task_ids=(),
            risk_tier=HarnessRiskTier.LOW,
            edits=(
                HarnessXProcessorEdit(
                    kind=HarnessXEditKind.REPLACE,
                    hook=HarnessXHook.TASK_START,
                    singleton_group="system_prompt_policy",
                    dimension="D2",
                    processor=replacement,
                ),
            ),
        )
        candidate = apply_harnessx_change_manifest(
            parent,
            manifest,
            registry(),
            summary="candidate",
        )
        self.assertEqual(candidate.parent_id, parent.id)
        self.assertEqual(candidate.processors[0].config["suffix"], " two")
        self.assertEqual(candidate.metadata["rollback_variant_sha256"], parent.sha256)

    def test_manifest_parent_hash_and_edit_slot_fail_closed(self) -> None:
        parent = snapshot_harnessx_variant(
            HarnessXRuntime([PromptSuffixProcessor()]),
            variant_id="parent",
            summary="parent",
        )
        manifest = HarnessXChangeManifest(
            id="bad",
            candidate_variant_id="candidate",
            parent_variant_sha256="0" * 64,
            rollback_variant_sha256=parent.sha256,
            rationale="bad lineage",
            evidence_trace_ids=("trace",),
            expected_improve_task_ids=(),
            expected_regress_task_ids=(),
            risk_tier=HarnessRiskTier.LOW,
            edits=(
                HarnessXProcessorEdit(
                    kind=HarnessXEditKind.REMOVE,
                    hook=HarnessXHook.TASK_START,
                    singleton_group="system_prompt_policy",
                    dimension="D2",
                ),
            ),
        )
        with self.assertRaisesRegex(HarnessXContractError, "parent hash mismatch"):
            apply_harnessx_change_manifest(parent, manifest, registry(), summary="candidate")

    def test_gate_promotes_low_risk_and_rolls_back_regression(self) -> None:
        parent, candidate, manifest = self._gate_fixture(HarnessRiskTier.LOW)
        promoted = gate_harnessx_candidate(
            parent=parent,
            candidate=candidate,
            manifest=manifest,
            smoke_passed=True,
            previously_passing_task_ids=("task-a",),
            candidate_task_outcomes={"task-a": True},
        )
        self.assertTrue(promoted.accepted)
        self.assertEqual(promoted.resolved_variant_id, candidate.id)

        rollback = gate_harnessx_candidate(
            parent=parent,
            candidate=candidate,
            manifest=manifest,
            smoke_passed=True,
            previously_passing_task_ids=("task-a",),
            candidate_task_outcomes={"task-a": False},
        )
        self.assertFalse(rollback.accepted)
        self.assertEqual(rollback.resolved_variant_id, parent.id)
        self.assertIn("candidate_rejected", rollback.resolution)

    def test_high_risk_and_accumulated_same_dimension_require_approval(self) -> None:
        parent, candidate, high = self._gate_fixture(HarnessRiskTier.HIGH)
        waiting = gate_harnessx_candidate(
            parent=parent,
            candidate=candidate,
            manifest=high,
            smoke_passed=True,
            previously_passing_task_ids=("task-a",),
            candidate_task_outcomes={"task-a": True},
        )
        self.assertFalse(waiting.accepted)
        self.assertTrue(waiting.requires_approval)
        self.assertEqual(waiting.resolution, "approval_required_parent_retained")

        approved = gate_harnessx_candidate(
            parent=parent,
            candidate=candidate,
            manifest=high,
            smoke_passed=True,
            previously_passing_task_ids=("task-a",),
            candidate_task_outcomes={"task-a": True},
            approval_granted=True,
        )
        self.assertTrue(approved.accepted)

        _, candidate_low, low = self._gate_fixture(HarnessRiskTier.LOW)
        accumulated = gate_harnessx_candidate(
            parent=parent,
            candidate=candidate_low,
            manifest=low,
            smoke_passed=True,
            previously_passing_task_ids=("task-a",),
            candidate_task_outcomes={"task-a": True},
            approval_policy=HarnessXApprovalPolicy(max_consecutive_same_dimension_edits=3),
            recent_shipped_dimensions=("D2", "D2", "D2"),
        )
        self.assertTrue(accumulated.requires_approval)

    def test_candidate_must_bind_exact_manifest_and_rollback_hash(self) -> None:
        parent, candidate, manifest = self._gate_fixture(HarnessRiskTier.LOW)
        forged = replace(candidate, metadata={})
        decision = gate_harnessx_candidate(
            parent=parent,
            candidate=forged,
            manifest=manifest,
            smoke_passed=True,
            previously_passing_task_ids=("task-a",),
            candidate_task_outcomes={"task-a": True},
        )
        self.assertFalse(decision.accepted)
        failed = {check.name for check in decision.checks if not check.passed}
        self.assertIn("candidate_manifest_binding", failed)

    def test_gate_rejects_malformed_candidate_and_non_boolean_controls(self) -> None:
        parent, candidate, manifest = self._gate_fixture(HarnessRiskTier.LOW)
        malformed_entry = replace(candidate.processors[0], config=[])  # type: ignore[arg-type]
        malformed = replace(candidate, processors=(malformed_entry,))
        decision = gate_harnessx_candidate(
            parent=parent,
            candidate=malformed,
            manifest=manifest,
            smoke_passed=True,
            previously_passing_task_ids=("task-a",),
            candidate_task_outcomes={"task-a": True},
        )
        self.assertFalse(decision.accepted)
        failed = {check.name for check in decision.checks if not check.passed}
        self.assertIn("candidate_structure", failed)

        with self.assertRaisesRegex(HarnessXContractError, "must be boolean"):
            gate_harnessx_candidate(
                parent=parent,
                candidate=candidate,
                manifest=manifest,
                smoke_passed="yes",  # type: ignore[arg-type]
                previously_passing_task_ids=("task-a",),
                candidate_task_outcomes={"task-a": True},
            )

    @staticmethod
    def _gate_fixture(risk: HarnessRiskTier):
        parent = snapshot_harnessx_variant(
            HarnessXRuntime([PromptSuffixProcessor(" one")]),
            variant_id="parent",
            summary="parent",
        )
        manifest = HarnessXChangeManifest(
            id=f"change-{risk.name.lower()}",
            candidate_variant_id="candidate",
            parent_variant_sha256=parent.sha256,
            rollback_variant_sha256=parent.sha256,
            rationale="trace-backed repair",
            evidence_trace_ids=("trace-1",),
            expected_improve_task_ids=("task-a",),
            expected_regress_task_ids=(),
            risk_tier=risk,
            edits=(
                HarnessXProcessorEdit(
                    kind=HarnessXEditKind.REPLACE,
                    hook=HarnessXHook.TASK_START,
                    singleton_group="system_prompt_policy",
                    dimension="D2",
                    processor=processor_manifest_entry(PromptSuffixProcessor(" two")),
                ),
            ),
        )
        candidate = apply_harnessx_change_manifest(
            parent,
            manifest,
            registry(),
            summary="candidate",
        )
        return parent, candidate, manifest


if __name__ == "__main__":
    unittest.main()
