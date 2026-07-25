from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.create_scheduling_manifest import build_scheduling_manifest
from experiments.skillsbench.create_split_manifest import build_split_manifest
from experiments.skillsbench.run_model_c0_c1_scripted_solver import (
    MAX_GENERATED_SCRIPT_CHARS,
    RESPONSE_CONTRACT_VERSION,
    SCRIPT_RESPONSE_SCHEMA,
    ScriptedRecord,
    append_record_jsonl,
    generation_parse_failure_status,
    make_agent_command,
    normalize_container_workdir,
    parse_generated_script,
    task_instruction_body,
    task_timeouts,
    verifier_outcome_status,
)
from experiments.skillsbench.run_model_c0_c1_pilot import make_agent_command as make_pilot_agent_command
from experiments.skillsbench.run_model_c0_c1_pilot import (
    MODEL_NONCOMPLETION_SCORE_SOURCE,
    classify_agent_noncompletion,
)
from experiments.skillsbench.reclassify_oracle_readiness import reclassify_summary
from experiments.skillsbench.merge_readiness_summaries import merge_readiness_summaries
from experiments.skillsbench.aggregate_model_trials import aggregate_model_trials
from experiments.skillsbench.aggregate_full87_trials import aggregate_full87
from experiments.skillsbench.build_execution_readiness_manifest import (
    classify_execution_evidence,
)
from experiments.skillsbench.run_full87_c0_c1_batch import (
    expected_run_id,
    failed_pair_is_infrastructure,
    infrastructure_guardrail_reached,
    pair_is_scored,
    read_pair_summary,
    summarize_progress,
    validate_manifest,
)
from experiments.skillsbench.run_oracle_readiness import (
    CommandReport,
    classify_verifier_result,
    docker_env_args,
    docker_resource_args,
    normalize_text,
    parse_reward,
    prepare_skill_free_build_context,
    task_phase_env,
    task_section_mapping,
)
from src.merlin_harness.cta_lite import compare_traces
from src.merlin_harness.executors import (
    ApiModelExecutor,
    CliModelConfig,
    CliModelExecutor,
    ExecutionRequest,
    ExecutionResult,
    make_claude_cli_executor,
    make_codex_cli_executor,
)
from src.merlin_harness.harness import (
    DoNotUseConstraintProcessor,
    HarnessEvent,
    HarnessEvolutionProposal,
    HarnessRuntime,
    HarnessVariantSpec,
    Hook,
    ShadowingLifecycleProcessor,
    build_runtime_from_variant,
    evaluate_harness_evolution,
    make_default_harness_runtime,
    snapshot_harness_variant,
)
from src.merlin_harness.lifecycle import (
    apply_lifecycle_decision,
    decide_candidate_lifecycle,
    evaluate_policy_change,
    validate_aip_lite_skill,
)
from src.merlin_harness.library import FileSkillLibrary
from src.merlin_harness.metrics import (
    InvocationObservation,
    clean_oracle_invocation_rate,
    cost_no_gain_rate,
    mixed_skill_invocation_rate,
    more_skills_decomposition,
    no_skill_when_oracle_rate,
    normalized_gain,
    oracle_invocation_event_summary,
    oracle_invocation_event_rates,
    paired_bootstrap_ci,
    route_risk_components,
    route_risk_score,
    shadowing_rate,
    spurious_invocation_rate,
    wrong_skill_invocation_rate,
)
from src.merlin_harness.models import (
    BehaviorDelta,
    HarnessPolicyChange,
    InvocationRecord,
    LifecycleAction,
    LifecycleStatus,
    TaskSpec,
    TraceRecord,
    ValidationResult,
    VerifierSpec,
)
from src.merlin_harness.provisioning import LexicalProvisioner, make_single_step_skill, select_best_skill
from src.merlin_harness.runner import run_no_skill_baseline, run_seeded_condition, run_task_once
from src.merlin_harness.skillsbench_adapter import parse_skill_md
from src.merlin_harness.task_io import load_task, save_task
from src.merlin_harness.task_io import load_tasks
from src.merlin_harness.taxonomy import validate_task_taxonomy
from src.merlin_harness.tasks import run_verifier
from src.merlin_harness.traces import FileTraceStore


class StaticAnswerExecutor:
    name = "static_answer"

    def __init__(self, answer: str) -> None:
        self.answer = answer

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            answer=self.answer,
            events=[{"type": "TOOL", "action": "static_answer", "condition": request.condition}],
            metadata={"task_id": request.task.id},
        )


class FakeResponseClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.payloads: list[dict] = []

    def create_response(self, payload: dict) -> dict:
        self.payloads.append(payload)
        return {
            "id": "resp_fake",
            "output_text": self.text,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }


class CoreFlowTests(unittest.TestCase):
    def test_execution_readiness_distinguishes_scored_failure_from_build_failure(self) -> None:
        scored = {
            "passed": False,
            "reward": 0.0,
            "commands": {
                "build": {"exit_code": 0, "timed_out": False},
                "verifier": {"exit_code": 0, "timed_out": False},
            },
        }
        build_failed = {
            "passed": False,
            "reward": None,
            "commands": {"build": {"exit_code": 1, "timed_out": False}},
        }

        self.assertEqual(
            classify_execution_evidence(scored),
            (40, True, "build_and_verifier_scored"),
        )
        self.assertEqual(
            classify_execution_evidence(build_failed),
            (0, False, "no_successful_build_evidence"),
        )

    def test_full87_batch_manifest_and_resume_summary_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "manifest.json"
            task_ids = [f"task-{index:02d}" for index in range(87)]
            manifest = {
                "run_prefix": "full87-test",
                "condition_id": "claude_sonnet5_high",
                "task_ids": task_ids,
                "trial_indices": [1, 2, 3],
                "arms": ["C0", "C1"],
                "expected_cells": 522,
                "frozen_inputs": {},
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            validate_manifest(manifest_path, manifest)

            run_id = expected_run_id(manifest, task_ids[0], 1)
            summary_path = root / "runs" / run_id / "summary.json"
            summary_path.parent.mkdir(parents=True)
            summary = {
                "records": [
                    {
                        "task_id": task_ids[0],
                        "condition_id": "claude_sonnet5_high",
                        "trial_index": 1,
                        "arm": arm,
                        "reward": reward,
                        "passed": bool(reward),
                        "status": "passed" if reward else "reward_failed",
                        "account_usage": {"total_cost_usd": 0.25},
                    }
                    for arm, reward in (("C0", 0.0), ("C1", 1.0))
                ]
            }
            summary_path.write_text(json.dumps(summary), encoding="utf-8")

            self.assertIsNotNone(
                read_pair_summary(
                    summary_path,
                    task_id=task_ids[0],
                    trial_index=1,
                    condition_id="claude_sonnet5_high",
                )
            )
            self.assertTrue(pair_is_scored(summary))
            unscored = json.loads(json.dumps(summary))
            unscored["records"][0]["reward"] = None
            self.assertFalse(pair_is_scored(unscored))
            progress = summarize_progress(manifest, root / "runs")
            self.assertEqual(progress["completed_pairs"], 1)
            self.assertEqual(progress["completed_cells"], 2)
            self.assertEqual(progress["expected_cells"], 522)
            self.assertEqual(progress["provider_reported_usage_estimate_usd"], 0.5)
            diagnostic = aggregate_full87(manifest, root / "runs")
            self.assertEqual(diagnostic["coverage"]["observed_pairs"], 1)
            self.assertEqual(diagnostic["coverage"]["valid_reward_pairs"], 1)
            self.assertEqual(
                diagnostic["macro_over_fully_scored_tasks"]["task_count"],
                0,
            )

    def test_full87_timeout_zero_is_model_failure_not_infrastructure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_ids = [f"task-{index:02d}" for index in range(87)]
            manifest = {
                "run_prefix": "full87-timeout-test",
                "condition_id": "claude_sonnet5_high",
                "task_ids": task_ids,
                "trial_indices": [1, 2, 3],
                "arms": ["C0", "C1"],
                "expected_cells": 522,
            }
            run_id = expected_run_id(manifest, task_ids[0], 1)
            summary_path = root / run_id / "summary.json"
            summary_path.parent.mkdir(parents=True)
            summary_path.write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "task_id": task_ids[0],
                                "condition_id": "claude_sonnet5_high",
                                "trial_index": 1,
                                "arm": arm,
                                "reward": 0.0,
                                "passed": False,
                                "status": "agent_timeout",
                                "score_source": MODEL_NONCOMPLETION_SCORE_SOURCE,
                            }
                            for arm in ("C0", "C1")
                        ]
                    }
                ),
                encoding="utf-8",
            )

            progress = summarize_progress(manifest, root)
            self.assertEqual(progress["completed_pairs"], 1)
            self.assertEqual(progress["trailing_infrastructure_pairs"], 0)
            self.assertEqual(
                progress["model_noncompletion_status_counts"], {"agent_timeout": 2}
            )
            self.assertFalse(infrastructure_guardrail_reached(progress, 1))

    def test_agent_timeout_is_scored_but_other_agent_failure_is_unscored(self) -> None:
        timeout = CommandReport(
            argv=["claude"],
            exit_code=124,
            duration_sec=900.0,
            timed_out=True,
        )
        self.assertEqual(
            classify_agent_noncompletion(timeout)[:3],
            ("agent_timeout", 0.0, MODEL_NONCOMPLETION_SCORE_SOURCE),
        )
        failure = CommandReport(
            argv=["claude"], exit_code=1, duration_sec=1.0, timed_out=False
        )
        self.assertEqual(
            classify_agent_noncompletion(failure),
            ("agent_failed", None, None, []),
        )

    def test_infrastructure_guardrail_rechecks_resume_state(self) -> None:
        self.assertTrue(
            infrastructure_guardrail_reached(
                {"trailing_infrastructure_pairs": 3}, 3
            )
        )

    def test_unscored_pair_infrastructure_classification(self) -> None:
        summary = {
            "records": [
                {"status": "account_isolation_preflight_failed", "reward": None},
                {"status": "workspace_materialization_failed", "reward": None},
            ]
        }
        self.assertTrue(failed_pair_is_infrastructure(0, summary))
        self.assertTrue(failed_pair_is_infrastructure(1, None))
        model_failure = {
            "records": [
                {"status": "agent_timeout", "reward": 0.0},
                {"status": "reward_failed", "reward": 0.0},
            ]
        }
        self.assertFalse(failed_pair_is_infrastructure(0, model_failure))

    def test_oracle_readiness_timeout_output_is_json_safe_text(self) -> None:
        self.assertEqual(normalize_text(b"bad\xff"), "bad\ufffd")
        self.assertEqual(normalize_text("ok"), "ok")
        self.assertEqual(normalize_text(None), "")

    def test_oracle_reward_parser_preserves_fractional_scores(self) -> None:
        self.assertEqual(parse_reward("1.000"), 1.0)
        self.assertEqual(parse_reward("0.625"), 0.625)
        self.assertEqual(parse_reward("0"), 0.0)
        self.assertIsNone(parse_reward("nan"))
        self.assertIsNone(parse_reward("1.1"))

        report = CommandReport(argv=["verifier"], exit_code=1, duration_sec=1.0)
        self.assertEqual(classify_verifier_result(report, 0.625), ("reward_partial", False))
        self.assertEqual(
            classify_verifier_result(report, 1.0),
            ("verifier_contract_inconsistent", False),
        )
        self.assertEqual(
            classify_verifier_result(report, 1.0, strict_assertions=False),
            ("passed", True),
        )

    def test_model_prompt_strips_task_frontmatter_but_keeps_budget_parsing(self) -> None:
        source = """---
metadata:
  required_skills:
  - secret-skill-name
agent:
  timeout_sec: 1500
environment:
  build_timeout_sec: 3600
verifier:
  timeout_sec: 600
---
Solve the visible task.
"""

        body = task_instruction_body(source)
        timeouts = task_timeouts(source, build_default=900, script_default=900, verifier_default=900)

        self.assertEqual(body, "Solve the visible task.\n")
        self.assertNotIn("required_skills", body)
        self.assertEqual(timeouts["build_timeout_sec"], 3600)
        self.assertEqual(timeouts["script_execution_timeout_sec"], 1500)
        self.assertEqual(timeouts["verifier_timeout_sec"], 600)

    def test_model_build_context_replaces_curated_skills_with_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            destination = root / "destination"
            (source / "skills" / "oracle").mkdir(parents=True)
            (source / "skills" / "oracle" / "SKILL.md").write_text("secret", encoding="utf-8")
            (source / "Dockerfile").write_text("COPY skills /root/.claude/skills\n", encoding="utf-8")
            (source / "data.txt").write_text("task data", encoding="utf-8")

            prepare_skill_free_build_context(source, destination)

            self.assertTrue((destination / "skills").is_dir())
            self.assertEqual(list((destination / "skills").iterdir()), [])
            self.assertEqual((destination / "data.txt").read_text(encoding="utf-8"), "task data")

    def test_docker_resource_args_enforce_declared_limits(self) -> None:
        task_text = """---
environment:
  network_mode: no-network
  cpus: 2
  memory_mb: 4096
---
Task
"""
        self.assertEqual(
            docker_resource_args(task_text),
            ["--cpus", "2", "--memory", "4096m", "--network", "none"],
        )

    def test_task_phase_env_propagates_benchmark_declared_values(self) -> None:
        task_text = """---
environment:
  network_mode: public
  cpus: 4
  bugswarm_image_tag: example-image
oracle:
  env:
    REPO_ID: example/repo
verifier:
  env:
    REPO_ID: verifier/repo
    PORT: '8090'
---
Task
"""

        self.assertEqual(
            task_phase_env(task_text, "environment"),
            {"bugswarm_image_tag": "example-image"},
        )
        self.assertEqual(
            task_phase_env(task_text, "oracle"),
            {"REPO_ID": "example/repo", "bugswarm_image_tag": "example-image"},
        )
        self.assertEqual(
            task_phase_env(task_text, "verifier"),
            {
                "PORT": "8090",
                "REPO_ID": "verifier/repo",
                "bugswarm_image_tag": "example-image",
            },
        )
        self.assertEqual(
            task_section_mapping(task_text, "verifier", "env"),
            {"PORT": "8090", "REPO_ID": "verifier/repo"},
        )
        self.assertEqual(
            docker_env_args({"B": "two", "A": "one"}),
            ["-e", "A=one", "-e", "B=two"],
        )

    def test_oracle_reclassification_preserves_strict_verifier_inconsistency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = []
            for task_id, stdout in (("clean", "1 passed"), ("inconsistent", "case FAILED [ 50%]")):
                logs = root / "tasks" / task_id / "logs"
                (logs / "verifier").mkdir(parents=True)
                (logs / "verifier" / "reward.txt").write_text("1.000", encoding="utf-8")
                records.append(
                    {
                        "task_id": task_id,
                        "status": "reward_missing",
                        "passed": False,
                        "image": "image",
                        "container": "container",
                        "reward": None,
                        "logs_dir": str(logs),
                        "commands": {
                            "verifier": {
                                "argv": ["verifier"],
                                "exit_code": 0,
                                "duration_sec": 1.0,
                                "timed_out": False,
                                "stdout_tail": stdout,
                                "stderr_tail": "",
                            }
                        },
                    }
                )
            source = root / "summary.json"
            source.write_text(json.dumps({"run_id": "raw", "records": records}), encoding="utf-8")

            result = reclassify_summary(source, policy="strict")

        self.assertEqual(result["summary"]["passed"], 1)
        self.assertEqual(result["alternative_summaries"]["reward_authoritative"]["passed"], 2)
        by_id = {record["task_id"]: record for record in result["records"]}
        self.assertEqual(by_id["inconsistent"]["status"], "verifier_contract_inconsistent")

    def test_readiness_reconciliation_overlays_targeted_rerun_without_dropping_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base.json"
            overlay = root / "overlay.json"
            base.write_text(
                json.dumps(
                    {
                        "policy": "strict",
                        "records": [
                            {"task_id": "a", "status": "passed", "passed": True, "reward": 1.0},
                            {"task_id": "b", "status": "reward_missing", "passed": False, "reward": None},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            overlay.write_text(
                json.dumps(
                    {
                        "run_id": "targeted",
                        "records": [
                            {"task_id": "b", "status": "reward_partial", "passed": False, "reward": 0.6}
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = merge_readiness_summaries(base, [overlay])

        self.assertEqual(result["summary"]["task_count"], 2)
        self.assertEqual(result["summary"]["passed"], 1)
        self.assertEqual(result["summary"]["status_counts"], {"passed": 1, "reward_partial": 1})
        self.assertEqual(result["replacements"][0]["base_status"], "reward_missing")

    def test_model_trial_aggregation_keeps_paired_trial_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = []
            for trial_index, rewards in ((1, (0.0, 1.0)), (2, (1.0, 1.0)), (3, (0.0, 0.0))):
                path = root / f"trial-{trial_index}.json"
                path.write_text(
                    json.dumps(
                        {
                            "run_id": f"trial-{trial_index}",
                            "harness_mode": "agentic_workspace",
                            "records": [
                                {
                                    "task_id": "task-a",
                                    "condition_id": "claude",
                                    "arm": arm,
                                    "trial_index": trial_index,
                                    "harness_mode": "agentic_workspace",
                                    "backend": "claude",
                                    "model_id": "model",
                                    "effort": "high",
                                    "status": "passed" if reward == 1.0 else "reward_failed",
                                    "passed": reward == 1.0,
                                    "reward": reward,
                                    "wall_time_sec": 10.0,
                                }
                                for arm, reward in zip(("C0", "C1"), rewards)
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                paths.append(path)

            result = aggregate_model_trials(paths, expected_trials=3)

        self.assertTrue(result["complete"])
        self.assertEqual(result["paired_reward_delta_observed"], 3)
        self.assertAlmostEqual(result["paired_mean_reward_delta_c1_minus_c0"], 1 / 3)
        self.assertEqual(result["by_task_condition_arm"]["task-a:claude:C0"]["n"], 3)

    def test_file_library_round_trip_and_provisioning(self) -> None:
        skill = make_single_step_skill(
            skill_id="csv-clean",
            name="CSV Cleaner",
            description="Clean CSV files and normalize columns",
            trigger="Use for CSV cleaning tasks",
            step_description="Inspect headers, normalize columns, and validate row count.",
            status=LifecycleStatus.ACTIVE,
        )
        skill.metadata["source"] = "unit-test"

        with tempfile.TemporaryDirectory() as tmp:
            library = FileSkillLibrary(Path(tmp) / "skills")
            library.save(skill)
            loaded = library.load("csv-clean")

            provisioner = LexicalProvisioner(exposure_budget=1)
            provisioned = provisioner.provision("clean the csv columns", library.list())

        self.assertEqual(loaded.id, "csv-clean")
        self.assertEqual([item.id for item in provisioned], ["csv-clean"])

    def test_task_verifiers(self) -> None:
        exact = TaskSpec(
            id="answer",
            instruction="Return yes",
            verifier=VerifierSpec(name="exact", kind="exact_match", expected="yes"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(run_verifier(exact, tmp, answer="yes").passed)

            file_task = TaskSpec(
                id="file",
                instruction="Create result.txt",
                verifier=VerifierSpec(name="exists", kind="file_exists", target_path="result.txt"),
            )
            Path(tmp, "result.txt").write_text("ok", encoding="utf-8")
            self.assertTrue(run_verifier(file_task, tmp).passed)

    def test_task_json_and_no_skill_runner(self) -> None:
        task = TaskSpec(
            id="json-task",
            instruction="Return ok",
            verifier=VerifierSpec(name="exact", kind="exact_match", expected="ok"),
            metadata={
                "benchmark_family": "SkillsBench-style",
                "domain": "Office & White Collar",
                "capability": "Reasoning",
                "difficulty": "C",
                "skill_dependency": "none",
                "shadowing_role": "control",
                "mvp_tier": "smoke",
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            task_path = Path(tmp) / "tasks" / "json-task.json"
            save_task(task, task_path)
            loaded = load_task(task_path)
            trace = run_task_once(task=loaded, workspace=Path(tmp) / "workspace", condition="no_skill", answer="ok")
            records = run_no_skill_baseline(
                tasks=[loaded],
                workspaces_root=Path(tmp) / "workspaces",
                traces_root=Path(tmp) / "traces",
            )

        self.assertEqual(loaded.id, "json-task")
        self.assertTrue(trace.invocation.success if trace.invocation else False)
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0].invocation.success if records[0].invocation else True)
        self.assertEqual(records[0].metadata["executor"], "no_skill")

    def test_task_executor_contract_can_replace_deterministic_attempt(self) -> None:
        task = TaskSpec(
            id="executor-answer",
            instruction="Return ok",
            verifier=VerifierSpec(name="exact", kind="exact_match", expected="ok"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            trace = run_task_once(
                task=task,
                workspace=Path(tmp) / "workspace",
                condition="api-smoke",
                executor=StaticAnswerExecutor("ok"),
            )
            records = run_seeded_condition(
                tasks=[task],
                skills=[],
                workspaces_root=Path(tmp) / "seeded-ws",
                traces_root=Path(tmp) / "seeded-traces",
                condition="api-smoke-seeded",
                executor=StaticAnswerExecutor("ok"),
            )

        self.assertTrue(trace.invocation.success if trace.invocation else False)
        self.assertEqual(trace.metadata["executor"], "static_answer")
        self.assertTrue(records[0].invocation.success if records[0].invocation else False)
        self.assertEqual(records[0].metadata["executor"], "static_answer")

    def test_api_model_executor_parses_answer_and_writes_files(self) -> None:
        task = TaskSpec(
            id="api-write",
            instruction="Create result.txt with ok",
            verifier=VerifierSpec(name="exists", kind="file_exists", target_path="result.txt"),
        )
        client = FakeResponseClient('{"answer": "done", "files": [{"path": "result.txt", "content": "ok\\n"}]}')
        executor = ApiModelExecutor(model="fake-model", client=client)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            result = executor.execute(ExecutionRequest(task=task, workspace=workspace, condition="api"))

            self.assertEqual(result.answer, "done")
            self.assertEqual((workspace / "result.txt").read_text(encoding="utf-8"), "ok\n")
            self.assertEqual(client.payloads[0]["model"], "fake-model")
            self.assertIn("response_contract", client.payloads[0]["input"])

    def test_api_model_executor_blocks_path_escape(self) -> None:
        task = TaskSpec(
            id="api-escape",
            instruction="Create a file",
            verifier=VerifierSpec(name="exists", kind="file_exists", target_path="result.txt"),
        )
        client = FakeResponseClient('{"files": [{"path": "../escape.txt", "content": "bad"}]}')
        executor = ApiModelExecutor(model="fake-model", client=client)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            with self.assertRaises(ValueError):
                executor.execute(ExecutionRequest(task=task, workspace=workspace, condition="api"))

    def test_cli_model_executor_parses_plain_json_stdout(self) -> None:
        task = TaskSpec(
            id="cli-write",
            instruction="Create result.txt",
            verifier=VerifierSpec(name="exists", kind="file_exists", target_path="result.txt"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_cli.py"
            script.write_text(
                "import sys\n"
                "_ = sys.stdin.read()\n"
                "print('{\"answer\":\"done\",\"files\":[{\"path\":\"result.txt\",\"content\":\"ok\\\\n\"}]}')\n",
                encoding="utf-8",
            )
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            executor = CliModelExecutor(
                CliModelConfig(
                    command=["python3", str(script)],
                    backend_name="fake-cli",
                    model="fake-model",
                    prompt_mode="stdin",
                )
            )
            result = executor.execute(ExecutionRequest(task=task, workspace=workspace, condition="cli"))

            self.assertEqual(result.answer, "done")
            self.assertEqual((workspace / "result.txt").read_text(encoding="utf-8"), "ok\n")
            self.assertEqual(result.metadata["provider"], "fake-cli")

    def test_cli_model_executor_parses_wrapped_cli_json_result(self) -> None:
        task = TaskSpec(
            id="cli-answer",
            instruction="Return yes",
            verifier=VerifierSpec(name="exact", kind="exact_match", expected="yes"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_cli.py"
            script.write_text(
                "import json, sys\n"
                "_ = sys.stdin.read()\n"
                "print(json.dumps({'result': json.dumps({'answer': 'yes', 'files': []}), 'total_cost_usd': 0.01}))\n",
                encoding="utf-8",
            )
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            executor = CliModelExecutor(
                CliModelConfig(
                    command=["python3", str(script)],
                    backend_name="fake-cli",
                    model="fake-model",
                )
            )
            trace = run_task_once(task=task, workspace=workspace, condition="cli", executor=executor)

            self.assertTrue(trace.invocation.success if trace.invocation else False)
            self.assertEqual(trace.metadata["executor"], "cli_model")
            self.assertEqual(trace.metadata["executor_metadata"]["cli_metadata"]["total_cost_usd"], 0.01)

    def test_codex_cli_factory_uses_noninteractive_exec(self) -> None:
        executor = make_codex_cli_executor(model="default")

        self.assertEqual(executor.config.backend_name, "codex-cli")
        self.assertEqual(executor.config.effort, "high")
        self.assertEqual(executor.config.runtime_effort, "high")
        self.assertIn("exec", executor.config.command)
        self.assertIn('model_reasoning_effort="high"', executor.config.command)
        self.assertIn("--ephemeral", executor.config.command)
        self.assertEqual(executor.config.command[-1], "-")
        self.assertNotIn("--model", executor.config.command)

    def test_claude_cli_factory_uses_high_runtime_effort(self) -> None:
        executor = make_claude_cli_executor(model="claude-sonnet-5", effort="high")

        self.assertEqual(executor.config.effort, "high")
        self.assertEqual(executor.config.runtime_effort, "high")
        self.assertIn("--effort", executor.config.command)
        self.assertIn("high", executor.config.command)

    def test_task_taxonomy_validation(self) -> None:
        task = TaskSpec(
            id="classified",
            instruction="Create a file",
            verifier=VerifierSpec(name="exists", kind="file_exists", target_path="out.txt"),
            metadata={
                "benchmark_family": "SkillsBench-style",
                "domain": "Office & White Collar",
                "capability": "Tool Use",
                "difficulty": "C",
                "skill_dependency": "medium",
                "shadowing_role": "oracle_target",
                "mvp_tier": "smoke",
            },
        )
        self.assertTrue(all(result.passed for result in validate_task_taxonomy(task)))

    def test_seed_tasks_use_taxonomy(self) -> None:
        tasks = load_tasks(Path("experiments/mvp/tasks"))
        self.assertGreaterEqual(len(tasks), 3)
        for task in tasks:
            results = validate_task_taxonomy(task)
            self.assertTrue(all(result.passed for result in results), task.id)

    def test_trace_store_and_cta_lite(self) -> None:
        no_skill = TraceRecord(
            id="t-noskill",
            task_id="task-1",
            condition="no_skill",
            events=[{"type": "TOOL"}, {"type": "VALIDATION"}],
            invocation=InvocationRecord("task-1", [], [], ["skill-1"], success=False, score=0.0, cost=10.0),
        )
        with_skill = TraceRecord(
            id="t-skill",
            task_id="task-1",
            condition="with_skill",
            events=[{"type": "TOOL"}, {"type": "WRITE"}, {"type": "VALIDATION"}],
            invocation=InvocationRecord("task-1", ["skill-1"], ["skill-1"], ["skill-1"], success=True, score=1.0, cost=12.0),
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = FileTraceStore(Path(tmp) / "traces")
            store.save(no_skill)
            store.save(with_skill)
            loaded = store.list(task_id="task-1")

        delta = compare_traces(with_skill, no_skill)

        self.assertEqual(len(loaded), 2)
        self.assertEqual(delta.success_delta, 1.0)
        self.assertIn("positive_success_delta", delta.labels)

    def test_cta_lite_rejects_mismatched_task_ids(self) -> None:
        with_skill = TraceRecord(id="with", task_id="task-a", condition="with_skill")
        without_skill = TraceRecord(id="without", task_id="task-b", condition="no_skill")

        with self.assertRaisesRegex(ValueError, "matching task_id"):
            compare_traces(with_skill, without_skill)

    def test_cta_lite_rejects_one_sided_or_mismatched_pairing_metadata(self) -> None:
        paired_keys = (
            "model_id",
            "backend",
            "seed",
            "trial_index",
            "harness_mode",
            "verifier_id",
            "budget_id",
            "workspace_version",
        )
        for key in paired_keys:
            with self.subTest(key=key, case="one-sided"):
                with_skill = TraceRecord(
                    id="with",
                    task_id="task-1",
                    condition="with_skill",
                    metadata={key: "same"},
                )
                without_skill = TraceRecord(id="without", task_id="task-1", condition="no_skill")
                with self.assertRaisesRegex(ValueError, key):
                    compare_traces(with_skill, without_skill)

            with self.subTest(key=key, case="mismatch"):
                with_skill = TraceRecord(
                    id="with",
                    task_id="task-1",
                    condition="with_skill",
                    metadata={key: "left"},
                )
                without_skill = TraceRecord(
                    id="without",
                    task_id="task-1",
                    condition="no_skill",
                    metadata={key: "right"},
                )
                with self.assertRaisesRegex(ValueError, key):
                    compare_traces(with_skill, without_skill)

    def test_cta_lite_allows_different_conditions_for_a_valid_pair(self) -> None:
        metadata = {
            "model_id": "model-1",
            "backend": "api",
            "trial_index": 2,
            "harness_mode": "agentic",
            "verifier_id": "verifier-v1",
            "budget_id": "budget-v1",
            "workspace_version": "workspace-v1",
        }
        with_skill = TraceRecord(
            id="with",
            task_id="task-1",
            condition="curated_skill",
            metadata=dict(metadata),
        )
        without_skill = TraceRecord(
            id="without",
            task_id="task-1",
            condition="no_skill",
            metadata=dict(metadata),
        )

        delta = compare_traces(with_skill, without_skill)

        self.assertEqual(delta.task_id, "task-1")

    def test_cta_lite_reports_only_with_skill_added_unexpected_artifacts(self) -> None:
        paired = {
            "model_id": "model-1",
            "backend": "api",
            "seed": 7,
            "harness_mode": "agentic",
            "verifier_id": "verifier-v1",
            "budget_id": "budget-v1",
            "workspace_version": "workspace-v1",
            "expected_artifacts": ["result.json"],
        }
        with_skill = TraceRecord(
            id="with",
            task_id="task-1",
            condition="with_skill",
            metadata={
                **paired,
                "workspace_manifest_before": ["input.txt", "preexisting.tmp"],
                "workspace_manifest_after": [
                    "input.txt",
                    "preexisting.tmp",
                    "result.json",
                    "shared.log",
                    "scratch/debug.txt",
                ],
            },
        )
        without_skill = TraceRecord(
            id="without",
            task_id="task-1",
            condition="no_skill",
            metadata={
                **paired,
                "workspace_manifest_before": {"input.txt": {"sha256": "a"}},
                "workspace_manifest_after": {
                    "input.txt": {"sha256": "a"},
                    "result.json": {"sha256": "b"},
                    "shared.log": {"sha256": "c"},
                },
            },
        )

        delta = compare_traces(with_skill, without_skill)

        self.assertEqual(delta.off_task_artifacts, ["scratch/debug.txt"])

    def test_lifecycle_and_policy_gates(self) -> None:
        skill = make_single_step_skill(
            skill_id="skill-1",
            name="Skill One",
            description="Useful procedure",
            trigger="Use on matching tasks",
            step_description="Do the useful thing.",
        )
        skill.provenance_trace_ids.append("trace-1")
        structure = validate_aip_lite_skill(skill)
        target = [ValidationResult("target", True)]
        regression = [ValidationResult("regression", True)]

        decision = decide_candidate_lifecycle(skill, structure, target, regression)
        apply_lifecycle_decision(skill, decision)

        change = evaluate_policy_change(
            HarnessPolicyChange(
                id="policy-1",
                surface="provisioning",
                summary="increase precision",
                delta_in=0,
                delta_held_out=1,
                accepted=False,
            )
        )

        self.assertEqual(decision.action, LifecycleAction.ADOPT)
        self.assertEqual(skill.status, LifecycleStatus.ACTIVE)
        self.assertTrue(change.accepted)

    def test_empty_target_results_route_candidate_to_repair(self) -> None:
        skill = make_single_step_skill(
            skill_id="skill-missing-target-evidence",
            name="Missing Target Evidence",
            description="Structurally valid but not target-validated.",
            trigger="Use on matching tasks.",
            step_description="Do the thing.",
        )
        skill.provenance_trace_ids.append("trace-missing-target")

        decision = decide_candidate_lifecycle(
            skill,
            validate_aip_lite_skill(skill),
            [],
            [ValidationResult("regression", True)],
        )
        apply_lifecycle_decision(skill, decision)

        self.assertEqual(decision.action, LifecycleAction.REPAIR)
        self.assertEqual(decision.reason, "target validation evidence missing")
        self.assertEqual(skill.status, LifecycleStatus.REPAIR)

    def test_empty_regression_results_route_candidate_to_repair(self) -> None:
        skill = make_single_step_skill(
            skill_id="skill-missing-regression-evidence",
            name="Missing Regression Evidence",
            description="Structurally valid but not regression-validated.",
            trigger="Use on matching tasks.",
            step_description="Do the thing.",
        )
        skill.provenance_trace_ids.append("trace-missing-regression")

        decision = decide_candidate_lifecycle(
            skill,
            validate_aip_lite_skill(skill),
            [ValidationResult("target", True)],
            [],
        )
        apply_lifecycle_decision(skill, decision)

        self.assertEqual(decision.action, LifecycleAction.REPAIR)
        self.assertEqual(decision.reason, "regression validation evidence missing")
        self.assertEqual(skill.status, LifecycleStatus.REPAIR)

    def test_target_validation_failure_routes_candidate_to_repair(self) -> None:
        skill = make_single_step_skill(
            skill_id="skill-target-failure",
            name="Target Failure",
            description="Structurally valid but failing its target verifier.",
            trigger="Use on matching tasks.",
            step_description="Do the thing.",
        )
        skill.provenance_trace_ids.append("trace-target-failure")

        decision = decide_candidate_lifecycle(
            skill,
            validate_aip_lite_skill(skill),
            [ValidationResult("target", False)],
            [ValidationResult("regression", True)],
        )
        apply_lifecycle_decision(skill, decision)

        self.assertEqual(decision.action, LifecycleAction.REPAIR)
        self.assertEqual(decision.reason, "target validation failed")
        self.assertEqual(skill.status, LifecycleStatus.REPAIR)

    def test_selection_metrics(self) -> None:
        records = [
            InvocationRecord("t1", ["s1"], ["s1"], ["s1"]),
            InvocationRecord("t2", ["s1", "s2"], ["s2"], ["s1"]),
            InvocationRecord("t3", ["s1"], [], ["s1"]),
        ]
        self.assertAlmostEqual(clean_oracle_invocation_rate(records), 1 / 3)
        self.assertAlmostEqual(shadowing_rate(records), 1 / 3)
        self.assertAlmostEqual(no_skill_when_oracle_rate(records), 1 / 3)

    def test_oracle_invocation_event_split(self) -> None:
        records = [
            InvocationRecord("t1", ["s1"], ["s1"], ["s1"]),
            InvocationRecord("t2", ["s2"], ["s2"], ["s1"]),
            InvocationRecord("t3", ["s1", "s2"], ["s1", "s2"], ["s1"]),
            InvocationRecord("t4", ["s1"], [], ["s1"]),
            InvocationRecord("t5", ["s3"], ["s3"], []),
        ]
        rates = oracle_invocation_event_rates(records)
        self.assertAlmostEqual(rates["oracle_only"], 1 / 4)
        self.assertAlmostEqual(rates["wrong"], 1 / 4)
        self.assertAlmostEqual(rates["mixed"], 1 / 4)
        self.assertAlmostEqual(rates["empty"], 1 / 4)
        self.assertAlmostEqual(wrong_skill_invocation_rate(records), 1 / 4)
        self.assertAlmostEqual(mixed_skill_invocation_rate(records), 1 / 4)
        self.assertAlmostEqual(shadowing_rate(records), rates["wrong"] + rates["mixed"])

    def test_invocation_event_summary_reports_counts_denominators_and_rhos(self) -> None:
        observations = [
            InvocationObservation("t1", ("s1",), ("s1",), True),
            InvocationObservation("t2", ("s1",), ("s1",), False),
            InvocationObservation("t3", ("s2",), ("s1",), False),
            InvocationObservation("t4", (), ("s1",), True),
            InvocationObservation("t5", ("s3",), (), True),
        ]

        summary = oracle_invocation_event_summary(observations)

        self.assertEqual(summary.total_observations, 5)
        self.assertEqual(summary.eligible, 4)
        self.assertEqual(summary.excluded_no_oracle, 1)
        self.assertEqual(summary.counts, {"n": 1, "m": 1, "o": 2})
        self.assertEqual(summary.event_probabilities["o"].numerator, 2)
        self.assertEqual(summary.event_probabilities["o"].denominator, 4)
        self.assertAlmostEqual(summary.event_probabilities["o"].value or -1, 0.5)
        self.assertEqual(summary.pass_counts, {"n": 1, "m": 0, "o": 1})
        self.assertAlmostEqual(summary.conditional_pass_rates["n"].value or -1, 1.0)
        self.assertEqual(summary.conditional_pass_rates["m"].value, 0.0)
        self.assertAlmostEqual(summary.conditional_pass_rates["o"].value or -1, 0.5)
        self.assertAlmostEqual(summary.overall_pass_rate.value or -1, 0.5)

    def test_invocation_event_summary_keeps_zero_denominator_undefined(self) -> None:
        summary = oracle_invocation_event_summary(
            [InvocationObservation("t-no-oracle", ("s1",), (), True)]
        )

        self.assertEqual(summary.eligible, 0)
        for event in ("n", "m", "o"):
            estimate = summary.event_probabilities[event]
            self.assertEqual(estimate.numerator, 0)
            self.assertEqual(estimate.denominator, 0)
            self.assertIsNone(estimate.value)
            self.assertIsNone(summary.conditional_pass_rates[event].value)
        self.assertIsNone(summary.overall_pass_rate.value)

        # Historical wrappers retain their selected-skill/zero fallback contract.
        self.assertEqual(clean_oracle_invocation_rate([]), 0.0)
        self.assertEqual(shadowing_rate([]), 0.0)

    def test_more_skills_decomposition_matches_observed_drop(self) -> None:
        oracle_only = oracle_invocation_event_summary(
            [
                *[InvocationObservation(f"on-{i}", (), ("oracle",), i == 0) for i in range(2)],
                *[
                    InvocationObservation(f"oo-{i}", ("oracle",), ("oracle",), i < 6)
                    for i in range(8)
                ],
            ]
        )
        full_library = oracle_invocation_event_summary(
            [
                *[InvocationObservation(f"fn-{i}", (), ("oracle",), i == 0) for i in range(3)],
                *[
                    InvocationObservation(f"fm-{i}", ("distractor",), ("oracle",), i == 0)
                    for i in range(4)
                ],
                *[
                    InvocationObservation(f"fo-{i}", ("oracle",), ("oracle",), i < 2)
                    for i in range(3)
                ],
            ]
        )

        decomposition = more_skills_decomposition(oracle_only, full_library)

        self.assertAlmostEqual(decomposition.p_oracle or -1, 0.7)
        self.assertAlmostEqual(decomposition.p_library or -1, 0.4)
        self.assertAlmostEqual(decomposition.delta_ctx or -1, 0.1)
        self.assertAlmostEqual(decomposition.delta_shd or -1, 0.2)
        self.assertAlmostEqual(decomposition.total or -1, 0.3)
        self.assertAlmostEqual(decomposition.observed_drop or -1, 0.3)
        self.assertAlmostEqual(decomposition.invariant_error or 0.0, 0.0)
        self.assertTrue(decomposition.invariant_holds)
        self.assertIsNone(decomposition.unavailable_reason)

    def test_more_skills_decomposition_is_undefined_without_eligible_denominator(self) -> None:
        empty = oracle_invocation_event_summary([])
        result = more_skills_decomposition(empty, empty)

        self.assertIsNone(result.delta_ctx)
        self.assertIsNone(result.delta_shd)
        self.assertIsNone(result.total)
        self.assertIsNone(result.invariant_holds)
        self.assertEqual(result.unavailable_reason, "eligible oracle-task denominator is zero")

    def test_more_skills_decomposition_handles_zero_probability_events(self) -> None:
        oracle_only = oracle_invocation_event_summary(
            [
                InvocationObservation("t1", ("oracle",), ("oracle",), True),
                InvocationObservation("t2", ("oracle",), ("oracle",), True),
            ]
        )
        full_library = oracle_invocation_event_summary(
            [
                InvocationObservation("t1", ("oracle",), ("oracle",), True),
                InvocationObservation("t2", ("oracle",), ("oracle",), False),
            ]
        )

        result = more_skills_decomposition(oracle_only, full_library)

        self.assertIsNone(full_library.conditional_pass_rates["m"].value)
        self.assertAlmostEqual(result.delta_ctx or -1, 0.5)
        self.assertEqual(result.delta_shd, 0.0)
        self.assertAlmostEqual(result.total or -1, 0.5)
        self.assertTrue(result.invariant_holds)

    def test_more_skills_decomposition_requires_complete_success_outcomes(self) -> None:
        oracle_only = oracle_invocation_event_summary(
            [InvocationObservation("t1", ("oracle",), ("oracle",), True)]
        )
        full_library = oracle_invocation_event_summary(
            [InvocationObservation("t1", ("oracle",), ("oracle",), None)]
        )

        result = more_skills_decomposition(oracle_only, full_library)

        self.assertIsNone(result.total)
        self.assertEqual(
            result.unavailable_reason,
            "one or more eligible trajectories have no observed success outcome",
        )

    def test_more_skills_decomposition_rejects_invalid_oracle_only_arm(self) -> None:
        invalid_oracle_only = oracle_invocation_event_summary(
            [InvocationObservation("t1", ("distractor",), ("oracle",), False)]
        )
        full_library = oracle_invocation_event_summary(
            [InvocationObservation("t1", ("oracle",), ("oracle",), True)]
        )

        with self.assertRaisesRegex(ValueError, "oracle-only summary"):
            more_skills_decomposition(invalid_oracle_only, full_library)

    def test_no_oracle_records_do_not_dilute_pi_o_or_pi_m(self) -> None:
        records = [
            InvocationRecord("t1", ["s1"], ["s1"], []),
            InvocationRecord("t2", [], [], []),
            InvocationRecord("t3", ["s2"], ["s2"], ["s2"]),
        ]
        self.assertAlmostEqual(clean_oracle_invocation_rate(records), 1.0)
        self.assertAlmostEqual(shadowing_rate(records), 0.0)
        self.assertAlmostEqual(spurious_invocation_rate(records), 1 / 2)

    def test_normalized_gain_saturated_baseline_keeps_regression_visible(self) -> None:
        self.assertAlmostEqual(normalized_gain(1.0, 1.0), 0.0)
        self.assertAlmostEqual(normalized_gain(0.7, 1.0), -0.3)
        self.assertAlmostEqual(normalized_gain(0.5, 0.0), 0.5)

    def test_cost_no_gain_rate_uses_cta_label(self) -> None:
        deltas = [
            BehaviorDelta("t1", "with-1", "without-1", success_delta=0.0, cost_ratio=2.0, labels=["cost_increase_without_gain"]),
            BehaviorDelta("t2", "with-2", "without-2", success_delta=1.0, cost_ratio=2.0, labels=["positive_success_delta"]),
        ]
        self.assertAlmostEqual(cost_no_gain_rate(deltas), 0.5)

    def test_route_risk_score_uses_non_overlapping_components(self) -> None:
        records = [
            InvocationRecord("t1", ["s1"], ["s1"], ["s1"]),
            InvocationRecord("t2", ["s2"], ["s2"], ["s1"]),
            InvocationRecord("t3", ["s1", "s2"], ["s1", "s2"], ["s1"]),
            InvocationRecord("t4", ["s1"], [], ["s1"]),
            InvocationRecord("t5", ["s3"], ["s3"], []),
        ]
        deltas = [
            BehaviorDelta("t1", "with-1", "without-1", labels=["cost_increase_without_gain"]),
            BehaviorDelta("t2", "with-2", "without-2", labels=[]),
        ]
        components = route_risk_components(iter(records), deltas)
        self.assertAlmostEqual(components["wrong"], 1 / 4)
        self.assertAlmostEqual(components["mixed"], 1 / 4)
        self.assertAlmostEqual(components["empty"], 1 / 4)
        self.assertAlmostEqual(components["spurious"], 1.0)
        self.assertAlmostEqual(components["cost_no_gain"], 0.5)
        self.assertAlmostEqual(
            route_risk_score(
                records,
                deltas,
                weights={"wrong": 1, "mixed": 2, "empty": 3, "spurious": 4, "cost_no_gain": 5},
            ),
            8.0,
        )

    def test_paired_bootstrap_ci_is_deterministic(self) -> None:
        rows = [(1, 0), (1, 0), (0, 0), (1, 1)]

        def mean_delta(items: list[tuple[int, int]]) -> float:
            return sum(a - b for a, b in items) / len(items)

        ci = paired_bootstrap_ci(rows, mean_delta, iterations=200, seed=7)
        ci_again = paired_bootstrap_ci(rows, mean_delta, iterations=200, seed=7)
        self.assertEqual(ci, ci_again)
        self.assertAlmostEqual(ci["estimate"], 0.5)
        self.assertLessEqual(ci["low"], ci["estimate"])
        self.assertGreaterEqual(ci["high"], ci["estimate"])

    def test_regression_failure_routes_candidate_to_repair(self) -> None:
        skill = make_single_step_skill(
            skill_id="skill-2",
            name="Skill Two",
            description="Useful but regressing",
            trigger="Use on matching tasks",
            step_description="Do the thing.",
        )
        skill.provenance_trace_ids.append("trace-2")
        structure = validate_aip_lite_skill(skill)
        target = [ValidationResult("target", True)]
        regression = [ValidationResult("regression", False)]

        decision = decide_candidate_lifecycle(skill, structure, target, regression)
        apply_lifecycle_decision(skill, decision)

        self.assertEqual(decision.action, LifecycleAction.REPAIR)
        self.assertEqual(skill.status, LifecycleStatus.REPAIR)

    def test_parse_skill_md_frontmatter_and_sections(self) -> None:
        text = (
            "---\n"
            "name: xlsx\n"
            'description: "Spreadsheet creation and analysis. Use for xlsx files."\n'
            "---\n\n"
            "# Title\n\n## Reading Data\n\nbody\n\n## Creating Excel Files\n\nbody\n"
        )
        parsed = parse_skill_md(text)
        self.assertEqual(parsed["name"], "xlsx")
        self.assertTrue(parsed["description"].startswith("Spreadsheet creation"))
        self.assertEqual(parsed["sections"], ["Reading Data", "Creating Excel Files"])

    def test_seeded_condition_solves_oracle_task_and_fallbacks_on_control(self) -> None:
        oracle = make_single_step_skill(
            skill_id="file-artifact-basic",
            name="File Artifact Basic",
            description="Create a named file artifact result.txt in the workspace.",
            trigger="Use when the task asks to create a file in the workspace.",
            step_description="Write the requested file.",
            status=LifecycleStatus.ACTIVE,
        )
        oracle.metadata["solves"] = {
            "create-result-file": {"write_file": {"path": "result.txt", "content": "done\n"}}
        }
        tasks = [
            TaskSpec(
                id="create-result-file",
                instruction="Create a file named result.txt in the workspace.",
                verifier=VerifierSpec(name="exists", kind="file_exists", target_path="result.txt"),
                oracle_skill_ids=["file-artifact-basic"],
            ),
            TaskSpec(
                id="unrelated-question",
                instruction="Return exactly the word yes.",
                verifier=VerifierSpec(name="exact", kind="exact_match", expected="yes"),
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            records = run_seeded_condition(
                tasks=tasks,
                skills=[oracle],
                workspaces_root=Path(tmp) / "ws",
                traces_root=Path(tmp) / "tr",
            )
        by_task = {r.task_id: r.invocation for r in records}
        self.assertTrue(by_task["create-result-file"].success)
        self.assertEqual(by_task["create-result-file"].selected_skill_ids, ["file-artifact-basic"])
        self.assertEqual(by_task["unrelated-question"].selected_skill_ids, [])

    def test_select_best_skill_threshold(self) -> None:
        skill = make_single_step_skill(
            skill_id="s1",
            name="Totally Unrelated",
            description="quantum chromodynamics lattice",
            trigger="never",
            step_description="n/a",
            status=LifecycleStatus.ACTIVE,
        )
        self.assertIsNone(select_best_skill("create a file named result.txt", [skill]))

    def test_policy_gate_accepts_float_deltas(self) -> None:
        change = evaluate_policy_change(
            HarnessPolicyChange(
                id="policy-2",
                surface="provisioning",
                summary="fractional gains",
                delta_in=0.05,
                delta_held_out=0.0,
                accepted=False,
            )
        )
        self.assertTrue(change.accepted)

    def test_controlled_distractors_create_shadowing_signal(self) -> None:
        tasks = load_tasks(Path("experiments/mvp/tasks"))
        seeds = FileSkillLibrary(Path("experiments/mvp/skills")).list()
        distractors = FileSkillLibrary(Path("experiments/mvp/distractors")).list()

        with tempfile.TemporaryDirectory() as tmp:
            records = run_seeded_condition(
                tasks=tasks,
                skills=seeds + distractors,
                workspaces_root=Path(tmp) / "ws",
                traces_root=Path(tmp) / "tr",
                condition="controlled-distractor-test",
            )

        invocations = [record.invocation for record in records if record.invocation]
        self.assertGreaterEqual(len(tasks), 10)
        self.assertGreater(shadowing_rate(invocations), 0.0)

    def test_harness_processor_blocks_do_not_use_skill_before_selection(self) -> None:
        risky_skill = make_single_step_skill(
            skill_id="risky-yes",
            name="Exact Yes Helper",
            description="Return exactly yes for exact-answer tasks.",
            trigger="Use when the task asks to return exactly yes.",
            step_description="Return yes.",
            status=LifecycleStatus.ACTIVE,
        )
        risky_skill.do_not_use_when.append("return exactly yes")
        task = TaskSpec(
            id="exact-yes",
            instruction="Return exactly yes.",
            verifier=VerifierSpec(name="exact", kind="exact_match", expected="yes"),
        )
        runtime = HarnessRuntime([DoNotUseConstraintProcessor()])

        with tempfile.TemporaryDirectory() as tmp:
            records = run_seeded_condition(
                tasks=[task],
                skills=[risky_skill],
                workspaces_root=Path(tmp) / "ws",
                traces_root=Path(tmp) / "tr",
                condition="do-not-use-test",
                harness=runtime,
            )

        invocation = records[0].invocation
        self.assertEqual(invocation.selected_skill_ids if invocation else ["unexpected"], [])
        self.assertEqual(records[0].metadata["constraint_blocked_skill_ids"], ["risky-yes"])

    def test_do_not_use_processor_handles_verbose_constraints(self) -> None:
        risky_skill = make_single_step_skill(
            skill_id="verbose-risk",
            name="Verbose Risk",
            description="Return exactly yes for exact-answer tasks.",
            trigger="Use when the task asks to return exactly yes.",
            step_description="Return yes.",
            status=LifecycleStatus.ACTIVE,
        )
        risky_skill.do_not_use_when.append("The task asks to return exactly yes after checking previous output.")
        task = TaskSpec(
            id="exact-yes-verbose",
            instruction="Return exactly yes.",
            verifier=VerifierSpec(name="exact", kind="exact_match", expected="yes"),
        )

        event = HarnessRuntime([DoNotUseConstraintProcessor()]).emit(
            HarnessEvent(
                hook=Hook.BEFORE_SELECT,
                task=task,
                provisioned_skills=[risky_skill],
            )
        )

        self.assertEqual(event.provisioned_skills, [])
        self.assertEqual(event.metadata["constraint_blocked_skill_ids"], ["verbose-risk"])

    def test_default_harness_clamps_exposure_budget(self) -> None:
        first = make_single_step_skill(
            skill_id="a-file",
            name="A File Skill",
            description="Create a file in the workspace.",
            trigger="Use for file creation.",
            step_description="Create the file.",
            status=LifecycleStatus.ACTIVE,
        )
        second = make_single_step_skill(
            skill_id="b-file",
            name="B File Skill",
            description="Create a file in the workspace.",
            trigger="Use for file creation.",
            step_description="Create the file.",
            status=LifecycleStatus.ACTIVE,
        )
        task = TaskSpec(
            id="create-file",
            instruction="Create a file in the workspace.",
            verifier=VerifierSpec(name="exists", kind="file_exists", target_path="result.txt"),
        )

        with tempfile.TemporaryDirectory() as tmp:
            records = run_seeded_condition(
                tasks=[task],
                skills=[first, second],
                workspaces_root=Path(tmp) / "ws",
                traces_root=Path(tmp) / "tr",
                exposure_budget=3,
                harness=make_default_harness_runtime(max_exposure_budget=1),
            )

        invocation = records[0].invocation
        self.assertEqual(len(invocation.provisioned_skill_ids if invocation else []), 1)
        self.assertEqual(records[0].metadata["exposure_budget"], 1)

    def test_shadowing_lifecycle_processor_proposes_hide_decision(self) -> None:
        invocations = [
            InvocationRecord("t1", ["good", "bad"], ["bad"], ["good"]),
            InvocationRecord("t2", ["good", "bad"], ["bad"], ["good"]),
            InvocationRecord("t3", ["good"], ["good"], ["good"]),
        ]
        runtime = HarnessRuntime([ShadowingLifecycleProcessor(min_shadowing_events=2)])

        event = runtime.emit(
            HarnessEvent(
                hook=Hook.POLICY_REVIEW,
                metadata={"invocations": invocations},
            )
        )

        decisions = event.metadata["lifecycle_decisions"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].skill_id, "bad")
        self.assertEqual(decisions[0].action, LifecycleAction.HIDE)

    def test_harness_variant_snapshot_and_evolution_gate(self) -> None:
        runtime = make_default_harness_runtime(max_exposure_budget=2)
        base = snapshot_harness_variant(
            runtime,
            variant_id="h0",
            summary="baseline hook processor composition",
            policy={"exposure_budget": 2},
        )
        candidate = HarnessVariantSpec(
            id="h1",
            parent_id=base.id,
            summary="lower exposure budget",
            processor_manifest=base.processor_manifest,
            policy={"exposure_budget": 1},
        )
        proposal = HarnessEvolutionProposal(
            id="proposal-1",
            parent_variant_id=base.id,
            candidate=candidate,
            rationale="reduce wrong skill invocation after route-risk traces",
            changed_hooks=[Hook.BEFORE_PROVISION.value],
            evidence_trace_ids=["trace-risk-1"],
        )

        accepted = evaluate_harness_evolution(
            proposal,
            delta_in=0.1,
            delta_held_out=0.0,
            metrics={"pi_m_delta": -0.2},
        )
        rejected = evaluate_harness_evolution(proposal, delta_in=0.1, delta_held_out=-0.1)

        self.assertEqual(
            [entry["name"] for entry in base.processor_manifest[Hook.BEFORE_PROVISION.value]],
            ["skill_state_filter", "exposure_budget"],
        )
        self.assertEqual(
            base.processor_manifest[Hook.BEFORE_PROVISION.value][1]["config"],
            {"max_exposure_budget": 2},
        )
        self.assertEqual(
            [entry["name"] for entry in base.processor_manifest[Hook.BEFORE_SELECT.value]],
            ["do_not_use_constraints"],
        )
        self.assertTrue(accepted.accepted)
        self.assertFalse(rejected.accepted)

    def test_harness_variant_snapshot_is_reconstructable_with_processor_config(self) -> None:
        variant_one = snapshot_harness_variant(
            make_default_harness_runtime(max_exposure_budget=1),
            variant_id="h-budget-1",
        )
        variant_three = snapshot_harness_variant(
            make_default_harness_runtime(max_exposure_budget=3),
            variant_id="h-budget-3",
        )
        restored = build_runtime_from_variant(variant_one)
        policy_event = restored.emit(HarnessEvent(hook=Hook.TASK_START))
        explicit_event = restored.emit(HarnessEvent(hook=Hook.TASK_START, metadata={"exposure_budget": 7}))
        event = restored.emit(
            HarnessEvent(
                hook=Hook.BEFORE_PROVISION,
                metadata={"exposure_budget": 5},
            )
        )

        self.assertNotEqual(variant_one.processor_manifest, variant_three.processor_manifest)
        self.assertEqual(variant_one.policy, {"exposure_budget": 1})
        self.assertEqual(variant_three.policy, {"exposure_budget": 3})
        self.assertEqual(policy_event.metadata["exposure_budget"], 1)
        self.assertEqual(explicit_event.metadata["exposure_budget"], 7)
        self.assertEqual(
            variant_one.processor_manifest[Hook.BEFORE_PROVISION.value][1]["config"],
            {"max_exposure_budget": 1},
        )
        self.assertEqual(
            variant_three.processor_manifest[Hook.BEFORE_PROVISION.value][1]["config"],
            {"max_exposure_budget": 3},
        )
        self.assertEqual(event.metadata["exposure_budget"], 1)

    def test_harness_variant_rejects_unsupported_policy_keys(self) -> None:
        base = snapshot_harness_variant(
            make_default_harness_runtime(max_exposure_budget=2),
            variant_id="h-policy-base",
            summary="base",
        )
        unsupported = HarnessVariantSpec(
            id="h-unsupported-policy",
            parent_id=base.id,
            summary="unsupported policy",
            processor_manifest=base.processor_manifest,
            policy={"min_select_score": 0.2},
        )

        with self.assertRaisesRegex(ValueError, "unsupported harness policy keys: min_select_score"):
            build_runtime_from_variant(unsupported)

    def test_harness_evolution_preflight_rejects_incomplete_proposals(self) -> None:
        base = snapshot_harness_variant(
            make_default_harness_runtime(max_exposure_budget=2),
            variant_id="h-preflight-base",
            summary="base harness",
        )

        def candidate(*, summary: str = "candidate", parent_id: str | None = base.id, manifest=None) -> HarnessVariantSpec:
            return HarnessVariantSpec(
                id="h-preflight-candidate",
                parent_id=parent_id,
                summary=summary,
                processor_manifest=base.processor_manifest if manifest is None else manifest,
                policy={"exposure_budget": 1},
            )

        cases = [
            (
                "summary",
                HarnessEvolutionProposal(
                    "p-summary",
                    base.id,
                    candidate(summary=""),
                    "valid rationale",
                    [Hook.BEFORE_PROVISION.value],
                    ["trace-1"],
                ),
                "candidate summary is required",
            ),
            (
                "manifest",
                HarnessEvolutionProposal(
                    "p-manifest",
                    base.id,
                    candidate(manifest={}),
                    "valid rationale",
                    [Hook.BEFORE_PROVISION.value],
                    ["trace-1"],
                ),
                "candidate processor manifest is required",
            ),
            (
                "rationale",
                HarnessEvolutionProposal(
                    "p-rationale",
                    base.id,
                    candidate(),
                    "",
                    [Hook.BEFORE_PROVISION.value],
                    ["trace-1"],
                ),
                "proposal rationale is required",
            ),
            (
                "parent",
                HarnessEvolutionProposal(
                    "p-parent",
                    base.id,
                    candidate(parent_id="other-parent"),
                    "valid rationale",
                    [Hook.BEFORE_PROVISION.value],
                    ["trace-1"],
                ),
                "candidate parent_id must match proposal parent_variant_id",
            ),
            (
                "changed_hooks",
                HarnessEvolutionProposal(
                    "p-hooks",
                    base.id,
                    candidate(),
                    "valid rationale",
                    [],
                    ["trace-1"],
                ),
                "changed_hooks are required",
            ),
            (
                "evidence",
                HarnessEvolutionProposal(
                    "p-evidence",
                    base.id,
                    candidate(),
                    "valid rationale",
                    [Hook.BEFORE_PROVISION.value],
                    [],
                ),
                "evidence_trace_ids are required",
            ),
        ]

        for label, proposal, expected_error in cases:
            with self.subTest(label=label):
                result = evaluate_harness_evolution(proposal, delta_in=1.0, delta_held_out=1.0)
                self.assertFalse(result.accepted)
                self.assertIn(expected_error, result.evidence)

    def test_skillsbench_split_manifest_covers_all_87_tasks(self) -> None:
        index = {
            "source": "unit",
            "commit": "test",
            "license": "test",
            "tasks": [
                {
                    "id": f"task-{i:02d}",
                    "category": "cat-a" if i % 2 else "cat-b",
                    "difficulty": "hard" if i % 3 == 0 else "easy",
                    "required_skills": [],
                    "curated_skill_variants": [f"skill-{i:02d}"],
                }
                for i in range(87)
            ],
        }
        readiness = {
            "tasks": [
                {
                    "task_id": f"task-{i:02d}",
                    "static_status": "needs_infrastructure_review",
                    "infrastructure_flags": ["docker"],
                    "has_oracle": True,
                    "has_verifier": True,
                }
                for i in range(87)
            ]
        }

        manifest = build_split_manifest(index=index, readiness=readiness)
        counts = manifest["summary"]["counts"]
        assigned = [
            task["task_id"]
            for split_tasks in manifest["splits"].values()
            for task in split_tasks
        ]

        self.assertEqual(counts, {"adaptation": 35, "held_out": 30, "regression": 22})
        self.assertEqual(len(assigned), 87)
        self.assertEqual(len(set(assigned)), 87)
        self.assertEqual(manifest["summary"]["held_out_min_seeds_for_100_trials"], 4)

    def test_skillsbench_scheduling_manifest_preserves_document_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks_root = Path(tmp)
            for task_id, filename in [
                ("pdf-task", "input.pdf"),
                ("json-task", "data.json"),
                ("csv-task", "data.csv"),
            ]:
                env_dir = tasks_root / task_id / "environment"
                env_dir.mkdir(parents=True)
                (env_dir / filename).write_text("{}", encoding="utf-8")
                (tasks_root / task_id / "task.md").write_text(
                    "---\n"
                    "agent:\n"
                    "  timeout_sec: 900.0\n"
                    "verifier:\n"
                    "  timeout_sec: 600.0\n"
                    "environment:\n"
                    "  build_timeout_sec: 300.0\n"
                    "---\n"
                    "Test task.\n",
                    encoding="utf-8",
                )
                skills_dir = env_dir / "skills" / "ignored-pdf-skill"
                skills_dir.mkdir(parents=True)
                (skills_dir / "reference.pdf").write_text("ignored", encoding="utf-8")
            index = {
                "source": "unit",
                "commit": "test",
                "license": "test",
                "tasks": [
                    {
                        "id": "pdf-task",
                        "category": "office-white-collar",
                        "difficulty": "medium",
                        "curated_skill_variants": [],
                    },
                    {
                        "id": "json-task",
                        "category": "natural-science",
                        "difficulty": "medium",
                        "curated_skill_variants": [],
                    },
                    {
                        "id": "csv-task",
                        "category": "finance-economics",
                        "difficulty": "medium",
                        "curated_skill_variants": [],
                    },
                ],
            }
            split_manifest = {
                "splits": {
                    "adaptation": [{"task_id": "pdf-task"}, {"task_id": "json-task"}, {"task_id": "csv-task"}],
                    "held_out": [],
                    "regression": [],
                }
            }
            readiness = {
                "records": [
                    {"task_id": "pdf-task", "status": "passed", "passed": True},
                    {"task_id": "json-task", "status": "passed", "passed": True},
                    {"task_id": "csv-task", "status": "passed", "passed": True},
                ]
            }

            manifest = build_scheduling_manifest(
                index=index,
                split_manifest=split_manifest,
                readiness=readiness,
                tasks_root=tasks_root,
            )
            by_id = {task["task_id"]: task for task in manifest["tasks"]}

            self.assertEqual(manifest["summary"]["task_count"], 3)
            self.assertEqual(by_id["pdf-task"]["benchmark_stratum"], "document_pdf_pptx_docx")
            self.assertEqual(by_id["pdf-task"]["execution_bucket"], "long_running_document_media")
            self.assertEqual(by_id["pdf-task"]["agent_timeout_sec"], 900)
            self.assertEqual(by_id["pdf-task"]["verifier_timeout_sec"], 600)
            self.assertEqual(by_id["pdf-task"]["build_timeout_sec"], 300)
            self.assertEqual(by_id["json-task"]["execution_bucket"], "short_smoke_candidate")
            self.assertEqual(by_id["csv-task"]["execution_bucket"], "short_smoke_candidate")
            self.assertIn("pdf-task", by_id)
            self.assertIn("All 87 tasks remain", manifest["policy"]["final_coverage"])

    def test_scripted_solver_appends_record_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "records.jsonl"
            append_record_jsonl(
                path,
                ScriptedRecord(
                    task_id="task-a",
                    condition_id="claude_sonnet5_high",
                    arm="C1",
                    harness_mode="scripted_solver",
                    model_id="claude-sonnet-5",
                    backend="claude",
                    effort="high",
                    runtime_effort="high",
                    status="passed",
                    passed=True,
                    reward=1,
                    skill_used=["example/SKILL.md"],
                ),
            )

            lines = path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(lines), 1)
        self.assertIn('"task_id": "task-a"', lines[0])
        self.assertIn('"skill_used": ["example/SKILL.md"]', lines[0])
        self.assertIn(f'"response_contract_version": "{RESPONSE_CONTRACT_VERSION}"', lines[0])

    def test_scripted_solver_normalizes_container_workdir(self) -> None:
        self.assertEqual(normalize_container_workdir(""), "/root")
        self.assertEqual(normalize_container_workdir(None), "/root")
        self.assertEqual(normalize_container_workdir("app"), "/app")
        self.assertEqual(normalize_container_workdir("/app"), "/app")

    def test_scripted_solver_claude_command_uses_bounded_json_schema(self) -> None:
        command = make_agent_command(
            {
                "backend": "claude",
                "model_id": "claude-sonnet-5",
                "effort": "high",
                "runtime_effort": "high",
            }
        )

        schema_index = command.index("--json-schema") + 1
        schema = json.loads(command[schema_index])

        self.assertEqual(schema, SCRIPT_RESPONSE_SCHEMA)
        self.assertEqual(schema["properties"]["script"]["maxLength"], MAX_GENERATED_SCRIPT_CHARS)

    def test_agentic_pilot_claude_command_isolates_settings_and_disables_unneeded_tools(self) -> None:
        mcp_config = {
            "mcpServers": {
                "task_container": {
                    "type": "stdio",
                    "command": "/usr/bin/python3",
                    "args": ["container_exec_mcp.py", "--container", "fixed", "--workdir", "/root"],
                }
            }
        }
        command = make_pilot_agent_command(
            {
                "backend": "claude",
                "model_id": "claude-sonnet-5",
                "effort": "high",
                "runtime_effort": "high",
            },
            mcp_config=mcp_config,
            settings={"enableAllProjectMcpServers": True},
        )

        self.assertEqual(command[command.index("--setting-sources") + 1], "project")
        self.assertEqual(command[command.index("--tools") + 1], "mcp__task_container__exec,Skill")
        self.assertEqual(
            command[command.index("--allowedTools") + 1],
            "mcp__task_container__exec,Skill",
        )
        self.assertIn("--strict-mcp-config", command)
        self.assertIn("--no-chrome", command)
        self.assertIn("--no-session-persistence", command)
        self.assertNotIn("Bash(*)", command)
        self.assertEqual(command[command.index("--permission-mode") + 1], "dontAsk")

    def test_scripted_solver_prefers_claude_structured_output(self) -> None:
        wrapper = {
            "result": "provider display text",
            "structured_output": {
                "script_path": "solve.sh",
                "script": "#!/bin/sh\nprintf ok\n",
                "skill_used": [],
                "notes": "compact",
            },
        }
        report = CommandReport(
            argv=["claude"],
            exit_code=0,
            duration_sec=0.1,
            stdout_tail=json.dumps(wrapper),
        )

        script_name, script, skill_used, note = parse_generated_script(report)

        self.assertEqual(script_name, "solve.sh")
        self.assertEqual(script, "#!/bin/sh\nprintf ok\n")
        self.assertEqual(skill_used, [])
        self.assertEqual(note, "compact")

    def test_scripted_solver_classifies_provider_output_truncation(self) -> None:
        wrapper = {
            "result": "【已经写了 25156 字符,但是模型的输出长度限制强制截断了内容】",
            "structured_output": None,
        }

        status = generation_parse_failure_status(json.dumps(wrapper, ensure_ascii=False))

        self.assertEqual(status, "generation_output_truncated")

    def test_scripted_solver_rejects_oversize_script(self) -> None:
        wrapper = {
            "structured_output": {
                "script_path": "solve.sh",
                "script": "x" * (MAX_GENERATED_SCRIPT_CHARS + 1),
                "skill_used": [],
                "notes": "oversize",
            }
        }
        report = CommandReport(
            argv=["claude"],
            exit_code=0,
            duration_sec=0.1,
            stdout_tail=json.dumps(wrapper),
        )

        with self.assertRaisesRegex(ValueError, "Generated script exceeds"):
            parse_generated_script(report)

    def test_scripted_solver_keeps_wrapped_result_fallback(self) -> None:
        wrapper = {
            "result": json.dumps(
                {
                    "script_path": "solution.py",
                    "script": "print('ok')\n",
                    "skill_used": ["example/SKILL.md"],
                    "notes": "fallback",
                }
            )
        }
        report = CommandReport(
            argv=["claude"],
            exit_code=0,
            duration_sec=0.1,
            stdout_tail=json.dumps(wrapper),
        )

        script_name, _, skill_used, note = parse_generated_script(report)

        self.assertEqual(script_name, "solution.py")
        self.assertEqual(skill_used, ["example/SKILL.md"])
        self.assertEqual(note, "fallback")

    def test_scripted_solver_classifies_scored_failure_as_reward_failed(self) -> None:
        report = CommandReport(
            argv=["bash", "/verifier/test.sh"],
            exit_code=1,
            duration_sec=1.0,
        )

        status, passed = verifier_outcome_status(report, 0)

        self.assertEqual(status, "reward_failed")
        self.assertFalse(passed)

    def test_scripted_solver_keeps_verifier_infrastructure_failures_distinct(self) -> None:
        command_failure = CommandReport(
            argv=["bash", "/verifier/test.sh"],
            exit_code=2,
            duration_sec=1.0,
        )
        timeout = CommandReport(
            argv=["bash", "/verifier/test.sh"],
            exit_code=124,
            duration_sec=300.0,
            timed_out=True,
        )

        self.assertEqual(verifier_outcome_status(command_failure, None), ("verifier_command_failed", False))
        self.assertEqual(verifier_outcome_status(timeout, 0), ("verifier_timeout", False))


if __name__ == "__main__":
    unittest.main()
