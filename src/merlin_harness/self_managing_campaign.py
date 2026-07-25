"""Frozen 50-task campaign for Merlin's local governance substrate.

These are heterogeneous deterministic management tasks, not 50 provider-model
prompts.  Each task invokes production decision or enforcement code and has a
frozen expected outcome.  The campaign is designed to exercise the harness
while account-auth experiments are unavailable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .account_resource_governance import (
    AccountAuthResourceObservation,
    AccountReinvestmentPolicy,
    AccountResourceLedger,
)
from .cost_governance import VerifierUpgradeEvidence, gate_verifier_upgrade
from .harnessx_runtime import (
    ExactToolCallPolicyProcessor,
    HarnessXRuntime,
    ToolCallEvent,
)
from .harnessx_trace_ingestion import HarnessXTraceIngestion, HarnessXTraceSignal
from .self_managing_controller import (
    ControllerDecision,
    ManagedAction,
    SelfManagingHarnessController,
    SkillLifecycleSignal,
    decide_skill_action,
    decide_trace_action,
)


class SelfManagingCampaignError(ValueError):
    """Raised when a frozen campaign or its result artifacts drift."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SelfManagingTask:
    task_id: str
    category: str
    operation: str
    inputs: Mapping[str, Any]
    expected: Mapping[str, Any]

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "category": self.category,
            "operation": self.operation,
            "inputs": dict(self.inputs),
            "expected": dict(self.expected),
        }


def _skill_tasks() -> list[SelfManagingTask]:
    rows = (
        ("skill-repair-eligible", "skill_local", True, True, False, 0, "skill_repair", True),
        ("skill-repair-untrusted", "skill_local", False, True, False, 0, "skill_repair", False),
        ("skill-repair-no-invocation", "skill_local", True, False, False, 0, "skill_repair", False),
        ("route-repair-eligible", "route_local", True, True, False, 0, "provisioning_repair", True),
        ("route-repair-untrusted", "route_local", False, True, False, 0, "provisioning_repair", False),
        ("retire-eligible", "retirement", True, True, True, 2, "skill_retire", True),
        ("retire-visible-blocked", "retirement", True, True, False, 2, "skill_retire", False),
        ("retire-one-window", "retirement", True, True, True, 1, "skill_retire", False),
        ("retire-incomplete-traces", "retirement", True, False, True, 3, "skill_retire", False),
        ("unknown-observe", "unknown", True, True, False, 0, "observe", False),
    )
    tasks: list[SelfManagingTask] = []
    for index, row in enumerate(rows, start=1):
        (
            suffix,
            kind,
            trusted,
            complete,
            hidden,
            windows,
            action,
            automatic,
        ) = row
        tasks.append(
            SelfManagingTask(
                task_id=f"skill-{index:02d}-{suffix}",
                category="skill_lifecycle_routing",
                operation="skill_decision",
                inputs={
                    "signal_kind": kind,
                    "verifier_trusted": trusted,
                    "actual_invocation_evidence_complete": complete,
                    "already_hidden": hidden,
                    "independent_window_count": windows,
                },
                expected={"action": action, "automatic": automatic},
            )
        )
    return tasks


def _dispatch_tasks() -> list[SelfManagingTask]:
    rows = (
        ("repair-run", "skill_repair", True, True),
        ("repair-blocked", "skill_repair", False, False),
        ("provision-run", "provisioning_repair", True, True),
        ("retire-run", "skill_retire", True, True),
        ("harness-run", "harness_evolve", True, True),
        ("human-stop", "human_review", False, False),
    )
    return [
        SelfManagingTask(
            task_id=f"dispatch-{index:02d}-{suffix}",
            category="controller_dispatch",
            operation="controller_dispatch",
            inputs={"action": action, "automatic": automatic},
            expected={"executed": executed},
        )
        for index, (suffix, action, automatic, executed) in enumerate(rows, start=1)
    ]


def _verifier_tasks() -> list[SelfManagingTask]:
    rows = (
        ("complete", 200, 0, True, True, True),
        ("minimum-exact", 100, 0, True, True, True),
        ("too-small", 99, 0, True, True, False),
        ("one-regression", 200, 1, True, True, False),
        ("oracle-missing", 200, 0, False, True, False),
        ("approval-missing", 200, 0, True, False, False),
        ("all-blockers", 20, 1, False, False, False),
        ("large-replay-safe", 1000, 0, True, True, True),
    )
    return [
        SelfManagingTask(
            task_id=f"verifier-{index:02d}-{suffix}",
            category="verifier_upgrade_gate",
            operation="verifier_upgrade",
            inputs={
                "replay_case_count": cases,
                "replay_regression_count": regressions,
                "independent_oracle_passed": oracle,
                "human_approved": approval,
                "minimum_replay_cases": 100,
            },
            expected={"promote": promote},
        )
        for index, (
            suffix,
            cases,
            regressions,
            oracle,
            approval,
            promote,
        ) in enumerate(rows, start=1)
    ]


def _account_tasks() -> list[SelfManagingTask]:
    rows = (
        ("positive", [4], [1], ["dim-a"], 0.5, 1),
        ("break-even", [2], [1], ["dim-a"], 0.5, 0),
        ("zero-savings", [0], [0], ["dim-a"], 1.0, 0),
        ("two-observations", [4, 4], [1, 1], ["dim-a", "dim-a"], 0.75, 4),
        ("mixed-model", [4, 4], [0, 0], ["dim-a", "dim-b"], 1.0, 0),
        ("governance-expensive", [2], [3], ["dim-a"], 1.0, 0),
        ("fraction-zero", [8], [0], ["dim-a"], 0.0, 0),
        ("cap-two", [8], [0], ["dim-a"], 1.0, 2),
    )
    tasks: list[SelfManagingTask] = []
    for index, (
        suffix,
        savings,
        governance,
        dimensions,
        fraction,
        authorized,
    ) in enumerate(rows, start=1):
        inputs: dict[str, Any] = {
            "turn_savings": savings,
            "governance_turns": governance,
            "dimensions": dimensions,
            "reinvestment_fraction": fraction,
        }
        if suffix == "cap-two":
            inputs["per_decision_cap_turns"] = 2
        tasks.append(
            SelfManagingTask(
                task_id=f"resource-{index:02d}-{suffix}",
                category="account_resource_governance",
                operation="account_resource",
                inputs=inputs,
                expected={"authorized_provider_turns": authorized},
            )
        )
    return tasks


def _tool_tasks() -> list[SelfManagingTask]:
    rows = (
        ("read-exact", "Read", {"file_path": "/private/tmp/a.txt"}, True),
        ("read-changed", "Read", {"file_path": "/private/tmp/b.txt"}, False),
        ("grep-exact", "Grep", {"pattern": "TODO", "path": "/private/tmp/ws"}, True),
        ("grep-pattern-drift", "Grep", {"pattern": "FIXME", "path": "/private/tmp/ws"}, False),
        ("glob-exact", "Glob", {"pattern": "**/*.py", "path": "/private/tmp/ws"}, True),
        ("glob-path-drift", "Glob", {"pattern": "**/*.py", "path": "/private/tmp/other"}, False),
        ("bash-exact", "Bash", {"command": "pwd"}, True),
        ("bash-composed", "Bash", {"command": "pwd; touch blocked"}, False),
        ("write-denied", "Write", {"file_path": "/private/tmp/a.txt"}, False),
        ("unknown-denied", "Computer", {"action": "click"}, False),
    )
    return [
        SelfManagingTask(
            task_id=f"tool-{index:02d}-{suffix}",
            category="exact_multitool_mediation",
            operation="tool_mediation",
            inputs={"tool_name": tool_name, "tool_input": tool_input},
            expected={"allowed": allowed},
        )
        for index, (suffix, tool_name, tool_input, allowed) in enumerate(rows, start=1)
    ]


def _trace_tasks() -> list[SelfManagingTask]:
    rows = (
        ("false-deny", ("false_deny",), True, (), "harness_evolve", True),
        ("false-allow", ("false_allow",), False, ("safety_false_allow_requires_human_review",), "human_review", False),
        ("confirmed", ("confirmed",), False, ("no_trace_backed_false_deny",), "observe", False),
        ("shadow", ("post_execution_observation",), False, ("post_execution_shadow_cannot_nominate_policy_change_alone",), "observe", False),
        ("empty", (), False, ("no_trace_backed_false_deny",), "observe", False),
        ("deny-plus-confirmed", ("false_deny", "confirmed"), True, (), "harness_evolve", True),
        ("deny-plus-false-allow", ("false_deny", "false_allow"), False, ("safety_false_allow_requires_human_review",), "human_review", False),
        ("unknown-records", ("confirmed",), False, ("no_trace_backed_false_deny",), "observe", False),
    )
    tasks: list[SelfManagingTask] = []
    for index, (
        suffix,
        kinds,
        eligible,
        blockers,
        action,
        automatic,
    ) in enumerate(rows, start=1):
        tasks.append(
            SelfManagingTask(
                task_id=f"trace-{index:02d}-{suffix}",
                category="trace_to_harness_action",
                operation="trace_decision",
                inputs={
                    "signal_kinds": list(kinds),
                    "eligible_for_aegis": eligible,
                    "blockers": list(blockers),
                    "unknown_record_count": 2 if suffix == "unknown-records" else 0,
                },
                expected={"action": action, "automatic": automatic},
            )
        )
    return tasks


SELF_MANAGING_50_TASKS = tuple(
    _skill_tasks()
    + _dispatch_tasks()
    + _verifier_tasks()
    + _account_tasks()
    + _tool_tasks()
    + _trace_tasks()
)

if len(SELF_MANAGING_50_TASKS) != 50:
    raise RuntimeError("self-managing campaign must contain exactly 50 tasks")


def self_managing_50_suite_payload() -> dict[str, Any]:
    return {
        "schema_version": "merlin-self-managing-task-suite-v1",
        "suite_id": "self-managing-governance-50-v1",
        "task_count": len(SELF_MANAGING_50_TASKS),
        "tasks": [task.canonical_payload() for task in SELF_MANAGING_50_TASKS],
        "evidence_boundary": {
            "provider_model_tasks": False,
            "deterministic_governance_tasks": True,
            "production_modules_invoked": True,
            "low_cost_model_comparison_included": False,
        },
    }


SELF_MANAGING_50_SUITE_SHA256 = _sha256_json(self_managing_50_suite_payload())


def _run_skill_decision(task: SelfManagingTask) -> dict[str, Any]:
    inputs = task.inputs
    signal = SkillLifecycleSignal(
        signal_id=task.task_id,
        signal_kind=inputs["signal_kind"],
        skill_id="managed-skill",
        evidence_sha256="a" * 64,
        verifier_trusted=inputs["verifier_trusted"],
        actual_invocation_evidence_complete=inputs[
            "actual_invocation_evidence_complete"
        ],
        already_hidden=inputs["already_hidden"],
        independent_window_count=inputs["independent_window_count"],
    )
    decision = decide_skill_action(signal)
    return {
        "action": decision.action.value,
        "automatic": decision.automatic_execution_allowed,
        "blockers": list(decision.blockers),
    }


def _run_controller_dispatch(task: SelfManagingTask) -> dict[str, Any]:
    action = ManagedAction(task.inputs["action"])
    decision = ControllerDecision(
        action=action,
        automatic_execution_allowed=task.inputs["automatic"],
        reason="frozen campaign dispatch",
        blockers=() if task.inputs["automatic"] else ("campaign_blocker",),
        target_id="target",
        evidence_sha256="b" * 64,
    )
    controller = SelfManagingHarnessController(
        {
            ManagedAction.SKILL_REPAIR: lambda _decision: {"adopted": True},
            ManagedAction.PROVISIONING_REPAIR: lambda _decision: {"applied": True},
            ManagedAction.SKILL_RETIRE: lambda _decision: {"retired": True},
            ManagedAction.HARNESS_EVOLVE: lambda _decision: {"promoted": True},
        }
    )
    result = controller.execute(decision)
    return {"executed": result["executed"]}


def _run_verifier_upgrade(task: SelfManagingTask) -> dict[str, Any]:
    inputs = task.inputs
    decision = gate_verifier_upgrade(
        VerifierUpgradeEvidence(
            incumbent_epoch_id="verifier-v1",
            candidate_epoch_id="verifier-v2",
            replay_case_count=inputs["replay_case_count"],
            replay_regression_count=inputs["replay_regression_count"],
            independent_oracle_passed=inputs["independent_oracle_passed"],
            human_approved=inputs["human_approved"],
        ),
        minimum_replay_cases=inputs["minimum_replay_cases"],
    )
    return {"promote": decision.promote, "reason_count": len(decision.reasons)}


def _run_account_resource(task: SelfManagingTask) -> dict[str, Any]:
    inputs = task.inputs
    policy = AccountReinvestmentPolicy(
        reinvestment_fraction=inputs["reinvestment_fraction"],
        per_decision_cap_turns=inputs.get("per_decision_cap_turns"),
    )
    ledger = AccountResourceLedger(policy=policy)
    for index, (saving, governance, dimension) in enumerate(
        zip(
            inputs["turn_savings"],
            inputs["governance_turns"],
            inputs["dimensions"],
        ),
        start=1,
    ):
        ledger.append(
            AccountAuthResourceObservation(
                observation_id=f"{task.task_id}-{index}",
                task_id=f"task-{index}",
                evaluation_contract_sha256="c" * 64,
                verifier_epoch_id="verifier-v1",
                quota_window_id="window-v1",
                provider_id="codex-cli",
                model_id=dimension,
                effort="low",
                baseline_success=True,
                managed_success=True,
                baseline_execution_turns=saving + 1,
                managed_execution_turns=1,
                governance_turns=governance,
            )
        )
    decision = ledger.decide()
    return {
        "authorized_provider_turns": decision.authorized_provider_turns,
        "comparison_dimension_count": decision.comparison_dimension_count,
    }


def _run_tool_mediation(task: SelfManagingTask) -> dict[str, Any]:
    processor = ExactToolCallPolicyProcessor(
        allowed_tool_inputs=(
            {"tool_name": "Read", "tool_input": {"file_path": "/private/tmp/a.txt"}},
            {
                "tool_name": "Grep",
                "tool_input": {"pattern": "TODO", "path": "/private/tmp/ws"},
            },
            {
                "tool_name": "Glob",
                "tool_input": {"pattern": "**/*.py", "path": "/private/tmp/ws"},
            },
            {"tool_name": "Bash", "tool_input": {"command": "pwd"}},
        ),
        denied_tools=("Write", "Edit", "apply_patch"),
    )
    emission = HarnessXRuntime((processor,)).emit_sync(
        ToolCallEvent(
            event_id=task.task_id,
            task_id=task.task_id,
            step_index=1,
            tool_name=task.inputs["tool_name"],
            tool_input_json=_canonical_json(task.inputs["tool_input"]),
        )
    )
    return {"allowed": not emission.intercepted}


def _run_trace_decision(task: SelfManagingTask) -> dict[str, Any]:
    signals = tuple(
        HarnessXTraceSignal(
            case_id=f"case-{index}",
            tool_name="Bash",
            command_sha256=str(index + 1) * 64,
            command_chars=index + 1,
            expected_decision="allow" if kind == "false_deny" else "deny",
            observed_decision="deny" if kind == "false_deny" else "allow",
            signal_kind=kind,
            source_record_sha256=chr(97 + index) * 64,
        )
        for index, kind in enumerate(task.inputs["signal_kinds"])
    )
    ingestion = HarnessXTraceIngestion(
        source_kind="campaign",
        source_sha256="d" * 64,
        source_record_count=len(signals),
        verifier_suite_id="campaign-suite",
        verifier_suite_sha256="e" * 64,
        matched_signals=signals,
        unknown_record_count=task.inputs["unknown_record_count"],
        eligible_for_aegis=task.inputs["eligible_for_aegis"],
        blockers=tuple(task.inputs["blockers"]),
        parent_variant_sha256="f" * 64,
        evidence_boundary={
            "pre_execution_decision_observed": True,
            "raw_command_ingested": False,
            "verifier_oracle_required": True,
            "trace_alone_authorizes_promotion": False,
            "safety_false_allow_auto_repair": False,
        },
    )
    decision = decide_trace_action(ingestion)
    return {
        "action": decision.action.value,
        "automatic": decision.automatic_execution_allowed,
    }


def execute_self_managing_task(task: SelfManagingTask) -> dict[str, Any]:
    handlers = {
        "skill_decision": _run_skill_decision,
        "controller_dispatch": _run_controller_dispatch,
        "verifier_upgrade": _run_verifier_upgrade,
        "account_resource": _run_account_resource,
        "tool_mediation": _run_tool_mediation,
        "trace_decision": _run_trace_decision,
    }
    try:
        return handlers[task.operation](task)
    except KeyError as exc:
        raise SelfManagingCampaignError(
            f"unknown task operation: {task.operation}"
        ) from exc


def evaluate_self_managing_50_tasks() -> tuple[dict[str, Any], ...]:
    results: list[dict[str, Any]] = []
    for task in SELF_MANAGING_50_TASKS:
        actual = execute_self_managing_task(task)
        expected = dict(task.expected)
        passed = all(actual.get(key) == value for key, value in expected.items())
        results.append(
            {
                "task_id": task.task_id,
                "category": task.category,
                "operation": task.operation,
                "passed": passed,
                "expected": expected,
                "actual": actual,
                "task_sha256": _sha256_json(task.canonical_payload()),
            }
        )
    return tuple(results)


def run_self_managing_50_campaign(output_dir: str | Path) -> dict[str, Any]:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    suite = self_managing_50_suite_payload()
    results = evaluate_self_managing_50_tasks()
    category_counts: dict[str, int] = {}
    for result in results:
        category_counts[result["category"]] = (
            category_counts.get(result["category"], 0) + 1
        )
    report: dict[str, Any] = {
        "schema_version": "merlin-self-managing-campaign-v1",
        "suite_id": suite["suite_id"],
        "suite_sha256": SELF_MANAGING_50_SUITE_SHA256,
        "task_count": len(results),
        "pass_count": sum(result["passed"] for result in results),
        "fail_count": sum(not result["passed"] for result in results),
        "category_counts": dict(sorted(category_counts.items())),
        "results": list(results),
        "evidence_boundary": suite["evidence_boundary"],
    }
    report["evidence_sha256"] = _sha256_json(report)
    for path, payload in (
        (root / "task-suite.json", {**suite, "suite_sha256": SELF_MANAGING_50_SUITE_SHA256}),
        (root / "campaign-report.json", report),
    ):
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    return report


def validate_self_managing_50_campaign(output_dir: str | Path) -> dict[str, Any]:
    root = Path(output_dir).expanduser().resolve(strict=True)
    try:
        suite = json.loads((root / "task-suite.json").read_text(encoding="utf-8"))
        report = json.loads((root / "campaign-report.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SelfManagingCampaignError("campaign artifacts are invalid") from exc
    expected_suite = self_managing_50_suite_payload()
    stored_suite_body = {
        key: value for key, value in suite.items() if key != "suite_sha256"
    }
    report_body = {
        key: value for key, value in report.items() if key != "evidence_sha256"
    }
    replay = evaluate_self_managing_50_tasks()
    checks = {
        "suite_exact": stored_suite_body == expected_suite,
        "suite_sha256": (
            suite.get("suite_sha256") == SELF_MANAGING_50_SUITE_SHA256
            and _sha256_json(stored_suite_body) == SELF_MANAGING_50_SUITE_SHA256
        ),
        "report_sha256": report.get("evidence_sha256") == _sha256_json(report_body),
        "task_count": report.get("task_count") == 50,
        "all_passed": report.get("pass_count") == 50 and report.get("fail_count") == 0,
        "replay_exact": report.get("results") == list(replay),
        "boundary": report.get("evidence_boundary")
        == expected_suite["evidence_boundary"],
    }
    if not all(checks.values()):
        raise SelfManagingCampaignError("campaign replay validation failed")
    return {
        "valid": True,
        "checks": checks,
        "task_count": 50,
        "pass_count": 50,
        "suite_sha256": SELF_MANAGING_50_SUITE_SHA256,
        "evidence_sha256": report["evidence_sha256"],
    }


__all__ = [
    "SELF_MANAGING_50_SUITE_SHA256",
    "SELF_MANAGING_50_TASKS",
    "SelfManagingCampaignError",
    "SelfManagingTask",
    "evaluate_self_managing_50_tasks",
    "execute_self_managing_task",
    "run_self_managing_50_campaign",
    "self_managing_50_suite_payload",
    "validate_self_managing_50_campaign",
]
