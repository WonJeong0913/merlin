from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from experiments.mvp.run_chat import (
    build_verified_repair_portfolio,
    detect_codex_executable,
    detect_codex_runtime,
    detect_codex_version,
    LiveLearningError,
    LiveSkillCreationController,
    load_verified_promotion_overlay,
    load_verified_repair_summary,
    main,
    OfflineJudgeBackend,
    resolve_chat_workspace,
    run_repl,
    write_golden_judge_artifacts,
)
from src.merlin_harness.chat_session import ChatSessionError, TheKingChatSession
from src.merlin_harness.consent_governor import ConsentGatedHarnessGovernor
from src.merlin_harness.codex_chat import (
    CodexChatBackend,
    CodexChatBackendError,
    CodexChatTurnResult,
    HarnessXLiveHookConfig,
)
from src.merlin_harness.governed_provisioning import active_library_snapshot
from src.merlin_harness.library import FileSkillLibrary
from src.merlin_harness.models import LifecycleStatus
from src.merlin_harness.provisioning import LexicalProvisioner, make_single_step_skill
from src.merlin_harness.semantic_router import (
    SemanticRouterError,
    SemanticRouterErrorCode,
    SemanticRouterResult,
)


def json_line(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"))


def first_jsonl(answer: str = "FIRST") -> str:
    return "\n".join(
        [
            json_line({"type": "thread.started", "thread_id": "thread-123", "model": "gpt-5.6-terra"}),
            json_line({"type": "turn.started", "turn_id": "turn-1"}),
            json_line({"type": "item.completed", "item": {"type": "agent_message", "text": answer}}),
            json_line({"type": "turn.completed"}),
        ]
    ) + "\n"


def resumed_jsonl(answer: str = "SECOND", *, thread_id: str | None = None) -> str:
    events = []
    if thread_id is not None:
        events.append({"type": "thread.started", "thread_id": thread_id})
    events.extend(
        [
            {"type": "turn.started", "turn_id": "turn-2"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": answer}},
            {"type": "turn.completed"},
        ]
    )
    return "\n".join(json_line(event) for event in events) + "\n"


def make_promotion_fixture(root: Path, base: FileSkillLibrary) -> Path:
    candidate = make_single_step_skill(
        skill_id="extract-todo-items",
        name="Extract TODO Items",
        description="Extract TODO items from backlog.todo into todo-items.json when requested.",
        trigger="backlog.todo TODO items todo-items.json",
        step_description="Run the promoted TODO extractor.",
        status=LifecycleStatus.ACTIVE,
    )
    candidate.expected_artifacts = ["todo-items.json"]
    candidate.steps[0].inputs = ["backlog.todo"]
    candidate.steps[0].outputs = ["todo-items.json"]
    candidate.steps[0].kind = "script"
    candidate.steps[0].script_path = "scripts/run.py"
    base_skills = tuple(base.list())
    provisional = (*base_skills, candidate)
    evidence_root = root / "promotion"
    bundle = evidence_root / "quarantine" / "candidate" / candidate.id
    (bundle / "scripts").mkdir(parents=True)
    (bundle / "SKILL.md").write_text(
        "---\nname: extract-todo-items\ndescription: Use when TODO items must be extracted.\n---\n",
        encoding="utf-8",
    )
    (bundle / "scripts" / "run.py").write_text("print('fixture')\n", encoding="utf-8")
    records = []
    for path in sorted(item for item in bundle.rglob("*") if item.is_file()):
        payload = path.read_bytes()
        records.append(
            {
                "path": path.relative_to(bundle).as_posix(),
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    manifest_body = {"candidate_skill_id": candidate.id, "files": records}
    manifest_sha256 = hashlib.sha256(
        json.dumps(manifest_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    (evidence_root / "quarantine" / "quarantine_manifest.json").write_text(
        json.dumps(
            {"schema_version": 1, **manifest_body, "manifest_sha256": manifest_sha256},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (evidence_root / "provisional_library.json").write_text(
        json.dumps([skill.to_dict() for skill in provisional], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    evidence_path = evidence_root / "model_authored_skill_evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "campaign_id": "fixture",
                "candidate_skill_id": candidate.id,
                "adopted": True,
                "baseline_target_pass_rate": 0.0,
                "candidate_target_pass_rate": 1.0,
                "original_library_snapshot_sha256": active_library_snapshot(base_skills)[1],
                "provisional_library_snapshot_sha256": active_library_snapshot(provisional)[1],
                "gates": [
                    {"name": f"gate-{index}", "passed": True} for index in range(12)
                ],
                "quarantine": {"manifest_sha256": manifest_sha256},
                "evidence_boundary": {
                    "copy_on_write_promoted": True,
                    "live_library_mutated": False,
                    "hidden_held_out_verifier_passed": True,
                    "model_evidence_level": "requested_cli_contract_only",
                    "requested_model_id": "gpt-5.6-terra",
                    "provider_reported_model_ids": [],
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return evidence_path


class SequencedRunner:
    def __init__(self, outputs: list[subprocess.CompletedProcess[str]]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict] = []

    def __call__(self, command, **kwargs):
        self.calls.append({"command": list(command), **kwargs})
        return self.outputs.pop(0)


class CodexChatBackendTests(unittest.TestCase):
    def make_backend(self, root: Path, runner, **overrides) -> CodexChatBackend:
        workspace = root / "workspace"
        workspace.mkdir()
        return CodexChatBackend(
            executable="/Applications/ChatGPT.app/Contents/Resources/codex",
            cli_version="codex-cli test",
            workspace=workspace,
            trace_root=workspace / ".merlin" / "chat" / "session-test",
            model_id=overrides.get("model_id", "gpt-5.6-terra"),
            effort=overrides.get("effort", "high"),
            timeout_s=overrides.get("timeout_s", 10),
            live_hook_config=overrides.get("live_hook_config"),
            runner=runner,
        )

    def test_first_and_resume_turn_use_provider_thread_and_stdin_prompt(self) -> None:
        runner = SequencedRunner(
            [
                subprocess.CompletedProcess([], 0, first_jsonl(), ""),
                subprocess.CompletedProcess([], 0, resumed_jsonl(), ""),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            backend = self.make_backend(Path(temporary), runner)
            first = backend.run_turn(prompt="create the file", turn_number=1, thread_id=None)
            second = backend.run_turn(prompt="now edit it", turn_number=2, thread_id=first.thread_id)

            self.assertFalse(first.resumed)
            self.assertTrue(second.resumed)
            self.assertEqual(second.thread_id, "thread-123")
            first_command = runner.calls[0]["command"]
            resume_command = runner.calls[1]["command"]
            self.assertEqual(first_command[1:3], ["exec", "--json"])
            self.assertEqual(resume_command[1:4], ["exec", "resume", "--json"])
            self.assertIn("workspace-write", first_command)
            self.assertNotIn("--ephemeral", first_command)
            self.assertNotIn("--ephemeral", resume_command)
            self.assertIn("thread-123", resume_command)
            self.assertNotIn("--color", resume_command)
            self.assertNotIn("--sandbox", resume_command)
            self.assertNotIn("--cd", resume_command)
            self.assertNotIn("create the file", first_command)
            self.assertNotIn("now edit it", resume_command)
            self.assertEqual(runner.calls[0]["input"], "create the file")
            self.assertEqual(runner.calls[1]["input"], "now edit it")
            self.assertEqual(first.metadata["command"][-1], "<prompt-via-stdin>")
            self.assertFalse(first.metadata["actual_invocation_evidence_complete"])

    def test_raw_traces_are_new_only_and_hashed(self) -> None:
        runner = SequencedRunner([subprocess.CompletedProcess([], 0, first_jsonl(), "")])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backend = self.make_backend(root, runner)
            result = backend.run_turn(prompt="hello", turn_number=1, thread_id=None)
            raw = backend.trace_root / result.raw_trace_pointer
            self.assertEqual(result.raw_trace_sha256, hashlib.sha256(raw.read_bytes()).hexdigest())
            with self.assertRaisesRegex(CodexChatBackendError, "overwrite"):
                backend.run_turn(prompt="again", turn_number=1, thread_id=None)

    def test_nonzero_and_malformed_output_fail_closed_after_raw_save(self) -> None:
        cases = (
            (subprocess.CompletedProcess([], 2, first_jsonl(), "bad hello"), "exited with 2"),
            (subprocess.CompletedProcess([], 0, "{broken\n", ""), "malformed Codex JSONL"),
        )
        for index, (completed, message) in enumerate(cases):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temporary:
                backend = self.make_backend(Path(temporary), SequencedRunner([completed]))
                with self.assertRaisesRegex(CodexChatBackendError, message):
                    backend.run_turn(prompt="hello", turn_number=index + 1, thread_id=None)
                self.assertEqual(len(list(backend.trace_root.glob("*.codex.jsonl"))), 1)
                stderr_files = list(backend.trace_root.glob("*.stderr.txt"))
                self.assertEqual(len(stderr_files), 1)
                self.assertNotIn("hello", stderr_files[0].read_text(encoding="utf-8"))

    def test_timeout_saves_partial_raw_and_does_not_advance(self) -> None:
        def timeout_runner(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(
                cmd=["codex"],
                timeout=1,
                output=b'{"type":"turn.started"}\n',
                stderr=b"provider timeout detail",
            )

        with tempfile.TemporaryDirectory() as temporary:
            backend = self.make_backend(Path(temporary), timeout_runner)
            with self.assertRaisesRegex(CodexChatBackendError, "timed out"):
                backend.run_turn(prompt="hello", turn_number=1, thread_id=None)
            self.assertIn("turn.started", (backend.trace_root / "turn-0001.codex.jsonl").read_text())
            self.assertIn(
                "provider timeout detail",
                (backend.trace_root / "turn-0001.stderr.txt").read_text(),
            )

    def test_thread_and_model_contract_mismatches_are_rejected(self) -> None:
        cases = (
            (resumed_jsonl(thread_id="different-thread"), "conflicting provider thread_id", "thread-123"),
            (first_jsonl().replace("gpt-5.6-terra", "other-model"), "provider-reported model", None),
            (resumed_jsonl(), "first Codex chat turn returned no provider thread_id", None),
        )
        for raw, message, thread_id in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temporary:
                backend = self.make_backend(
                    Path(temporary), SequencedRunner([subprocess.CompletedProcess([], 0, raw, "")])
                )
                with self.assertRaisesRegex(CodexChatBackendError, message):
                    backend.run_turn(prompt="hello", turn_number=1, thread_id=thread_id)

    def test_workspace_trace_and_argument_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            with self.assertRaisesRegex(ValueError, "inside workspace"):
                CodexChatBackend(
                    executable="/bin/echo",
                    cli_version="test",
                    workspace=workspace,
                    trace_root=root / "outside",
                    effort="high",
                )
            with self.assertRaisesRegex(ValueError, "effort"):
                CodexChatBackend(
                    executable="/bin/echo",
                    cli_version="test",
                    workspace=workspace,
                    trace_root=workspace / "trace",
                    effort="auto",
                )

    def test_live_hooks_are_opt_in_and_injected_into_first_and_resume(self) -> None:
        runner = SequencedRunner(
            [
                subprocess.CompletedProcess([], 0, first_jsonl(), ""),
                subprocess.CompletedProcess([], 0, resumed_jsonl(), ""),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            backend = self.make_backend(
                Path(temporary),
                runner,
                live_hook_config=HarnessXLiveHookConfig(
                    project_root=Path(__file__).resolve().parents[1],
                    python_executable=Path("/usr/bin/python3"),
                ),
            )
            first = backend.run_turn(prompt="pwd", turn_number=1, thread_id=None)
            backend.run_turn(prompt="pwd again", turn_number=2, thread_id=first.thread_id)

            for call in runner.calls:
                command = call["command"]
                self.assertIn("--enable", command)
                self.assertIn("hooks", command)
                self.assertIn("--strict-config", command)
                self.assertIn("--dangerously-bypass-hook-trust", command)
                overrides = [
                    command[index + 1]
                    for index, item in enumerate(command[:-1])
                    if item == "-c"
                ]
                self.assertTrue(any(item.startswith("hooks.PreToolUse=") for item in overrides))
                self.assertTrue(any(item.startswith("hooks.PostToolUse=") for item in overrides))
            self.assertTrue(first.metadata["harnessx_live_pre_execution_control"])
            self.assertEqual(
                first.metadata["harnessx_live_enforcement_scope"],
                ["Bash", "apply_patch"],
            )
            self.assertTrue((backend.trace_root / "harnessx-live-tool-policy.json").is_file())


class FakeChatBackend:
    def __init__(self, *, trace_root: Path) -> None:
        self.calls: list[dict] = []
        self.trace_root = trace_root

    def run_turn(self, *, prompt: str, turn_number: int, thread_id: str | None) -> CodexChatTurnResult:
        self.calls.append({"prompt": prompt, "turn_number": turn_number, "thread_id": thread_id})
        active_thread = thread_id or f"thread-{turn_number}"
        raw_trace = str(turn_number)
        (self.trace_root / f"turn-{turn_number:04d}.codex.jsonl").write_text(
            raw_trace,
            encoding="utf-8",
        )
        return CodexChatTurnResult(
            turn_number=turn_number,
            resumed=thread_id is not None,
            thread_id=active_thread,
            turn_id=f"provider-turn-{turn_number}",
            answer=f"answer-{turn_number}",
            raw_trace_pointer=f"turn-{turn_number:04d}.codex.jsonl",
            raw_trace_sha256=hashlib.sha256(raw_trace.encode()).hexdigest(),
            metadata={
                "command": ["codex", "exec", "<prompt-via-stdin>"],
                "actual_invocation_evidence_complete": False,
            },
        )


class FakeSemanticRouter:
    model_id = "gpt-5.6-terra"
    effort = "low"

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def route(self, *, query, skills, exposure_budget, turn_number):
        self.calls.append({"query": query, "skill_ids": [skill.id for skill in skills], "budget": exposure_budget, "turn_number": turn_number})
        if self.error:
            raise self.error
        return self.result


class ChatSessionTests(unittest.TestCase):
    def make_session(self, root: Path, *, skill_status: LifecycleStatus = LifecycleStatus.ACTIVE):
        workspace = root / "workspace"
        workspace.mkdir()
        trace_root = workspace / ".merlin" / "chat" / "session-test"
        trace_root.mkdir(parents=True)
        library = FileSkillLibrary(root / "skills")
        library.save(
            make_single_step_skill(
                skill_id="report-writer",
                name="Report writer",
                description="Create a markdown report file with a concise summary",
                trigger="write report markdown file",
                step_description="Write report.md in the workspace",
                status=skill_status,
            )
        )
        backend = FakeChatBackend(trace_root=trace_root)
        return TheKingChatSession(
            workspace=workspace,
            library=library,
            backend=backend,
            trace_root=trace_root,
            top_k=2,
            per_skill_context_chars=400,
            total_skill_context_chars=800,
        ), backend, trace_root

    def make_mvp_library_session(self, root: Path):
        workspace = root / "workspace"
        workspace.mkdir()
        trace_root = workspace / ".merlin" / "chat" / "session-mvp"
        trace_root.mkdir(parents=True)
        backend = FakeChatBackend(trace_root=trace_root)
        library = FileSkillLibrary(root / "mvp-skills")
        source = FileSkillLibrary(
            Path(__file__).resolve().parents[1] / "experiments" / "mvp" / "skills"
        )
        for skill in source.list():
            library.save(skill)
        return TheKingChatSession(
            workspace=workspace,
            library=library,
            backend=backend,
            trace_root=trace_root,
            top_k=3,
        ), backend

    def test_every_turn_provisions_active_top_k_and_records_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, backend, trace_root = self.make_session(Path(temporary))
            response = session.send("Please write a markdown report file")

            self.assertEqual([item.skill_id for item in response.provisioned_skills], ["report-writer"])
            self.assertIn("score=", response.provisioned_skills[0].why)
            prompt = backend.calls[0]["prompt"]
            self.assertIn("[MERLIN PROMPT PROVISIONING]", prompt)
            self.assertNotIn("[THE KING PROMPT PROVISIONING]", prompt)
            self.assertIn("SKILL ID: report-writer", prompt)
            self.assertIn("NOT provider-native skill-body invocation evidence", prompt)
            metadata = json.loads((trace_root / "turn-0001.meta.json").read_text())
            self.assertFalse(metadata["prompt_provisioning_is_provider_native_invocation"])
            self.assertFalse(metadata["actual_invocation_evidence_complete"])
            self.assertFalse(metadata["user_input_stored"])
            self.assertNotIn("Please write", json.dumps(metadata))
            decision = metadata["deterministic_reference_decision"]
            self.assertEqual(decision["policy_version"], "governed-provisioning-v2")
            self.assertEqual(decision["provisioned_ids"], ["report-writer"])
            self.assertEqual(decision["harness_primary_id"], "report-writer")
            self.assertFalse(decision["query_stored"])
            self.assertIsNone(
                decision["boundary"]["provider_native_invoked_skill_ids"]
            )
            self.assertFalse(
                decision["boundary"]["actual_invocation_evidence_complete"]
            )

    def test_explicit_skill_selection_bypasses_router_but_keeps_governance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, backend, trace_root = self.make_session(Path(temporary))
            response = session.send(
                "Use this contract for a deliberately unusual request.",
                explicit_skill_id="report-writer",
            )

            self.assertEqual(
                [item.skill_id for item in response.provisioned_skills],
                ["report-writer"],
            )
            self.assertIn("SKILL ID: report-writer", backend.calls[0]["prompt"])
            metadata = json.loads((trace_root / "turn-0001.meta.json").read_text())
            routing = metadata["routing_decision"]
            self.assertEqual(routing["routing_source"], "explicit_skill")
            self.assertEqual(routing["explicit_skill_id"], "report-writer")
            self.assertEqual(routing["final_provisioned_ids"], ["report-writer"])

    def test_explicit_skill_selection_rejects_unknown_or_inactive_skills(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, _backend, _trace = self.make_session(Path(temporary))
            with self.assertRaisesRegex(ChatSessionError, "does not exist"):
                session.send("request", explicit_skill_id="missing")
        with tempfile.TemporaryDirectory() as temporary:
            session, _backend, _trace = self.make_session(
                Path(temporary), skill_status=LifecycleStatus.HIDDEN
            )
            with self.assertRaisesRegex(ChatSessionError, "not active"):
                session.send("request", explicit_skill_id="report-writer")

    def test_exact_artifact_anchor_excludes_lexically_plausible_wrong_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, _backend = self.make_mvp_library_session(Path(temporary))
            response = session.send(
                "Create result.txt in the workspace opaque-user-token-9173"
            )

            self.assertEqual(
                [item.skill_id for item in response.provisioned_skills],
                ["file-artifact-basic"],
            )
            self.assertIn("artifact anchor match count=1", response.provisioned_skills[0].why)
            persisted = (session.trace_root / "turn-0001.meta.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("result.txt", persisted)
            self.assertNotIn("opaque-user-token-9173", persisted)

    def test_input_and_output_anchors_select_line_summary_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, _backend = self.make_mvp_library_session(Path(temporary))
            response = session.send("Read input.txt and write summary.txt")

            self.assertEqual(
                [item.skill_id for item in response.provisioned_skills],
                ["line-summary"],
            )
            self.assertIn("input anchor match count=1", response.provisioned_skills[0].why)
            self.assertIn("artifact anchor match count=1", response.provisioned_skills[0].why)

    def test_unknown_artifact_and_general_task_keep_lexical_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, _backend, _trace = self.make_session(Path(temporary))
            unknown = session.send("write report into unknown.zzz")
            general = session.send("write report")

            self.assertEqual(
                [item.skill_id for item in unknown.provisioned_skills],
                ["report-writer"],
            )
            self.assertNotIn("artifact anchor match", unknown.provisioned_skills[0].why)
            self.assertEqual(
                [item.skill_id for item in general.provisioned_skills],
                ["report-writer"],
            )

    def test_multi_turn_resume_and_new_thread_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, backend, _trace_root = self.make_session(Path(temporary))
            first = session.send("write report")
            second = session.send("edit report")
            session.start_new_thread()
            third = session.send("write report again")

            self.assertEqual(backend.calls[0]["thread_id"], None)
            self.assertEqual(backend.calls[1]["thread_id"], first.thread_id)
            self.assertEqual(backend.calls[2]["thread_id"], None)
            self.assertEqual(second.thread_id, first.thread_id)
            self.assertNotEqual(third.thread_id, first.thread_id)
            self.assertEqual(session.status()["new_thread_count"], 1)

    def test_feedback_ledger_is_new_only_and_health_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, _backend, trace_root = self.make_session(Path(temporary))
            with self.assertRaisesRegex(ChatSessionError, "no completed turn"):
                session.record_feedback("pass")
            session.send("write report")
            evidence = session.record_feedback("fail")
            self.assertFalse(evidence["automatic_lifecycle_change"])
            self.assertEqual(session.status()["feedback"], {"pass": 0, "fail": 1, "pending": 0})
            self.assertTrue((trace_root / "feedback-turn-0001.json").is_file())
            with self.assertRaisesRegex(ChatSessionError, "already recorded"):
                session.record_feedback("pass")

    def test_hidden_skill_and_unmatched_turn_are_not_provisioned(self) -> None:
        for status, query in (
            (LifecycleStatus.HIDDEN, "write report"),
            (LifecycleStatus.ACTIVE, "calculate astronomy orbit"),
        ):
            with self.subTest(status=status.value), tempfile.TemporaryDirectory() as temporary:
                session, backend, _trace = self.make_session(Path(temporary), skill_status=status)
                response = session.send(query)
                self.assertEqual(response.provisioned_skills, ())
                self.assertIn("No skill matched this turn", backend.calls[0]["prompt"])

    def test_user_input_and_feedback_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, _backend, _trace = self.make_session(Path(temporary))
            with self.assertRaises(ChatSessionError):
                session.send("\x00")
            with self.assertRaises(ChatSessionError):
                session.send("x" * 20_001)
            with self.assertRaises(ChatSessionError):
                session.record_feedback("maybe")

    def test_status_exposes_observed_health_without_fake_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, _backend, _trace = self.make_session(Path(temporary))
            session.send("write report")
            status = session.status()
            self.assertEqual(status["selection_health"], "observed_only")
            self.assertEqual(status["automatic_lifecycle_changes"], "deferred")
            self.assertEqual(status["skill_exposure_counts"], {"report-writer": 1})
            self.assertTrue(status["skill_contracts"])
            contract = status["skill_contracts"][0]
            self.assertEqual(contract["id"], "report-writer")
            self.assertEqual(contract["step_count"], 1)
            self.assertIn("validators", contract)

    def test_semantic_router_selects_korean_and_english_without_filename(self) -> None:
        for query in ("간결한 보고서를 작성해 줘", "Please prepare the concise report"):
            with self.subTest(query=query), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                session, backend, trace_root = self.make_session(root)
                router = FakeSemanticRouter(SemanticRouterResult(("report-writer",), (), False, "gpt-5.6-terra", "low"))
                session.routing_mode = "semantic"
                session.semantic_router = router
                response = session.send(query)
                self.assertEqual([item.skill_id for item in response.provisioned_skills], ["report-writer"])
                self.assertIn("semantic rank=1", response.provisioned_skills[0].why)
                metadata = json.loads((trace_root / "turn-0001.meta.json").read_text())
                self.assertEqual(metadata["routing_decision"]["routing_source"], "semantic")
                self.assertEqual(metadata["routing_decision"]["final_provisioned_ids"], ["report-writer"])
                self.assertNotIn(query, json.dumps(metadata, ensure_ascii=False))
                self.assertIn("SKILL ID: report-writer", backend.calls[0]["prompt"])

    def test_semantic_router_never_receives_suppressed_same_name_variant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, _backend, trace_root = self.make_session(Path(temporary))
            competing = make_single_step_skill(
                skill_id="report-writer@alternate",
                name="Report writer",
                description="Create a markdown report file with a concise summary",
                trigger="write report markdown file",
                step_description="Write report.md in the workspace",
                status=LifecycleStatus.ACTIVE,
            )
            session.library.save(competing)
            router = FakeSemanticRouter(
                SemanticRouterResult(
                    ("report-writer",), (), False, "gpt-5.6-terra", "low"
                )
            )
            session.routing_mode = "semantic"
            session.semantic_router = router

            response = session.send("write report")

            self.assertEqual(router.calls[0]["skill_ids"], ["report-writer"])
            self.assertEqual(
                [item.skill_id for item in response.provisioned_skills],
                ["report-writer"],
            )
            routing = json.loads(
                (trace_root / "turn-0001.meta.json").read_text()
            )["routing_decision"]
            self.assertEqual(routing["schema_version"], 2)
            self.assertEqual(routing["name_collision_group_count"], 1)
            self.assertEqual(
                routing["name_collision_suppressed_ids"],
                ["report-writer@alternate"],
            )

    def test_semantic_abstain_is_authoritative_and_does_not_leak_deterministic_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, backend, trace_root = self.make_session(Path(temporary))
            session.routing_mode = "semantic"
            session.semantic_router = FakeSemanticRouter(SemanticRouterResult((), ("report-writer",), True, "gpt-5.6-terra", "low"))
            response = session.send("write report")
            self.assertEqual(response.provisioned_skills, ())
            self.assertIn("No skill matched", backend.calls[0]["prompt"])
            metadata = json.loads((trace_root / "turn-0001.meta.json").read_text())
            self.assertEqual(metadata["routing_decision"]["final_provisioned_ids"], [])
            self.assertEqual(metadata["routing_decision"]["final_abstain_reason"], "semantic_router_abstained")

    def test_semantic_selection_still_obeys_deterministic_negative_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session, _backend, trace_root = self.make_session(root)
            item = next(skill for skill in session.library.list() if skill.id == "report-writer")
            item.do_not_use_when = ["secret data"]
            session.library.save(item)
            session.routing_mode = "semantic"
            session.semantic_router = FakeSemanticRouter(SemanticRouterResult(("report-writer",), (), False, "gpt-5.6-terra", "low"))
            response = session.send("secret data")
            self.assertEqual(response.provisioned_skills, ())
            routing = json.loads((trace_root / "turn-0001.meta.json").read_text())["routing_decision"]
            self.assertEqual(routing["deterministic_guard_excluded_ids"], ["report-writer"])
            self.assertEqual(routing["final_abstain_reason"], "all_semantic_ranked_ids_blocked_by_deterministic_guard")

    def test_invalid_router_result_falls_back_and_records_safe_error_class(self) -> None:
        cases = (
            (SemanticRouterResult(("missing",), (), False, "gpt-5.6-terra", "low"), "unknown_skill_id"),
            (SemanticRouterResult(("report-writer", "report-writer"), (), False, "gpt-5.6-terra", "low"), "duplicate_skill_id"),
        )
        for result, error_class in cases:
            with self.subTest(error=error_class), tempfile.TemporaryDirectory() as temporary:
                session, _backend, trace_root = self.make_session(Path(temporary))
                session.routing_mode = "semantic"
                session.semantic_router = FakeSemanticRouter(result)
                response = session.send("write report")
                self.assertEqual([item.skill_id for item in response.provisioned_skills], ["report-writer"])
                routing = json.loads((trace_root / "turn-0001.meta.json").read_text())["routing_decision"]
                self.assertEqual(routing["routing_source"], "deterministic_fallback")
                self.assertEqual(routing["fallback_error_class"], error_class)

    def test_no_active_skills_skip_semantic_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, _backend, trace_root = self.make_session(Path(temporary), skill_status=LifecycleStatus.HIDDEN)
            router = FakeSemanticRouter(SemanticRouterResult((), (), True, "gpt-5.6-terra", "low"))
            session.routing_mode = "semantic"
            session.semantic_router = router
            response = session.send("write report")
            self.assertEqual(response.provisioned_skills, ())
            self.assertEqual(router.calls, [])
            routing = json.loads((trace_root / "turn-0001.meta.json").read_text())["routing_decision"]
            self.assertTrue(routing["model_call_skipped_no_active_skills"])
            self.assertEqual(session.status()["routing_source_counts"], {"semantic_abstain": 1})

    def test_router_exception_falls_back_without_stopping_main_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, backend, trace_root = self.make_session(Path(temporary))
            session.routing_mode = "semantic"
            session.semantic_router = FakeSemanticRouter(error=SemanticRouterError(SemanticRouterErrorCode.TIMEOUT, "sensitive detail"))
            response = session.send("write report")
            self.assertEqual(response.answer, "answer-1")
            self.assertEqual(len(backend.calls), 1)
            persisted = (trace_root / "turn-0001.meta.json").read_text()
            self.assertIn('"fallback_error_class": "timeout"', persisted)
            self.assertNotIn("sensitive detail", persisted)

    def test_exact_anchor_conflict_is_explicit_and_falls_back_to_anchor_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, _backend, trace_root = self.make_session(Path(temporary))
            item = next(skill for skill in session.library.list() if skill.id == "report-writer")
            item.expected_artifacts = ["report.md"]
            session.library.save(item)
            session.routing_mode = "semantic"
            session.semantic_router = FakeSemanticRouter(
                SemanticRouterResult(("outside-anchor",), (), False, "gpt-5.6-terra", "low")
            )
            response = session.send("create report.md")
            self.assertEqual([item.skill_id for item in response.provisioned_skills], ["report-writer"])
            routing = json.loads((trace_root / "turn-0001.meta.json").read_text())["routing_decision"]
            self.assertEqual(routing["fallback_error_class"], "anchor_conflict")

    def test_controlled_lexical_mode_exposes_distractor_without_claiming_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, backend = self.make_mvp_library_session(Path(temporary))
            distractors = FileSkillLibrary(
                Path(__file__).resolve().parents[1]
                / "experiments"
                / "mvp"
                / "distractors"
            ).list()
            for skill in distractors:
                session.library.save(skill)
            session.routing_mode = "controlled_lexical"
            session.top_k = 1
            session.controlled_lexical_provisioner = LexicalProvisioner(
                exposure_budget=1
            )

            response = session.send("Create a file named audit.log in the workspace.")

            self.assertEqual(
                [item.skill_id for item in response.provisioned_skills],
                ["aa-file-artifact-distractor"],
            )
            self.assertIn("controlled-naive-lexical-v1", response.provisioned_skills[0].why)
            self.assertIn("SKILL ID: aa-file-artifact-distractor", backend.calls[0]["prompt"])
            trace = session.last_trace()
            assert trace is not None
            self.assertEqual(trace["routing_decision"]["routing_source"], "controlled_lexical")
            self.assertFalse(trace["actual_invocation_evidence_complete"])


class ChatReplAndDetectionTests(unittest.TestCase):
    def test_natural_request_gets_permission_then_cow_adoption_and_auto_resume(self) -> None:
        request = "backlog.todo에서 TODO 항목을 추출해 todo-items.json으로 저장해줘"
        with tempfile.TemporaryDirectory() as temporary:
            session, backend, trace_root = ChatSessionTests().make_session(Path(temporary))
            governor = ConsentGatedHarnessGovernor(
                trace_root=trace_root, approval_mode="strict"
            )
            source_snapshot = active_library_snapshot(tuple(session.library.list()))[1]
            inputs = iter([request, "왜 필요한데?", "/status", "네", "/quit"])
            output: list[str] = []

            result = run_repl(
                session,
                autonomy_governor=governor,
                input_fn=lambda _prompt: next(inputs),
                output_fn=output.append,
            )

            self.assertEqual(result, 0)
            self.assertEqual(len(backend.calls), 1)
            self.assertIn("SKILL ID: extract-todo-items", backend.calls[0]["prompt"])
            self.assertEqual(source_snapshot, active_library_snapshot(tuple(FileSkillLibrary(Path(temporary) / "skills").list()))[1])
            self.assertEqual(session.library.load("extract-todo-items").status, LifecycleStatus.ACTIVE)
            rendered = "\n".join(output)
            self.assertIn("May I compile and verify", rendered)
            self.assertIn("permission is still pending", rendered)
            self.assertIn('"mode": "strict"', rendered)
            self.assertIn("permission confirmed", rendered)
            self.assertIn("resuming the original request", rendered)
            self.assertIn("assistant> answer-1", rendered)

    def test_natural_request_decline_makes_no_turn_or_mutation(self) -> None:
        request = "Extract TODO from backlog.todo and create todo-items.json"
        with tempfile.TemporaryDirectory() as temporary:
            session, backend, trace_root = ChatSessionTests().make_session(Path(temporary))
            governor = ConsentGatedHarnessGovernor(
                trace_root=trace_root, approval_mode="strict"
            )
            inputs = iter([request, "아니요", "/quit"])
            output: list[str] = []

            result = run_repl(
                session,
                autonomy_governor=governor,
                input_fn=lambda _prompt: next(inputs),
                output_fn=output.append,
            )

            self.assertEqual(result, 0)
            self.assertEqual(backend.calls, [])
            self.assertFalse((trace_root / "autonomy").exists())
            self.assertNotIn("extract-todo-items", {skill.id for skill in session.library.list()})
            self.assertIn("no model call, file write, or library change", "\n".join(output))

    def test_managed_mode_auto_applies_low_risk_change_without_permission_turn(self) -> None:
        request = "Extract TODO from backlog.todo and create todo-items.json"
        with tempfile.TemporaryDirectory() as temporary:
            session, backend, trace_root = ChatSessionTests().make_session(Path(temporary))
            governor = ConsentGatedHarnessGovernor(
                trace_root=trace_root, approval_mode="managed"
            )
            inputs = iter([request, "/quit"])
            output: list[str] = []

            result = run_repl(
                session,
                autonomy_governor=governor,
                input_fn=lambda _prompt: next(inputs),
                output_fn=output.append,
            )

            self.assertEqual(result, 0)
            self.assertEqual(len(backend.calls), 1)
            self.assertIn("SKILL ID: extract-todo-items", backend.calls[0]["prompt"])
            rendered = "\n".join(output)
            self.assertIn("low-risk reversible changes auto-authorized", rendered)
            self.assertIn("managed policy auto-authorized", rendered)
            self.assertNotIn("May I compile and verify", rendered)
            self.assertIn("assistant> answer-1", rendered)

    def test_offline_judge_backend_fails_closed_for_ordinary_chat(self) -> None:
        backend = OfflineJudgeBackend()
        with self.assertRaisesRegex(
            CodexChatBackendError, "offline judge mode supports evidence commands only"
        ):
            backend.run_turn(prompt="hello", turn_number=1, thread_id=None)

    def test_workspace_is_optional_and_created_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace, created = resolve_chat_workspace(None, default_parent=parent)
            self.assertTrue(created)
            self.assertTrue(workspace.is_dir())
            self.assertEqual(workspace.parent, parent.resolve())
            self.assertTrue(workspace.name.startswith("merlin-chat-"))
            self.assertEqual(workspace.stat().st_mode & 0o777, 0o700)

            selected, selected_created = resolve_chat_workspace(workspace)
            self.assertEqual(selected, workspace)
            self.assertFalse(selected_created)

            with self.assertRaisesRegex(ValueError, "--workspace must exist"):
                resolve_chat_workspace(parent / "missing")

    def test_learn_command_updates_creation_status_with_injected_controller(self) -> None:
        class FakeLearningController:
            def __init__(self) -> None:
                self.needs: list[str] = []

            def learn(self, need: str) -> dict:
                self.needs.append(need)
                return {
                    "candidate_skill_id": "extract-todo-items",
                    "gate_count": 12,
                    "gates": [{"name": "G6_adoption", "passed": True}],
                    "hidden_held_out_verifier_passed": True,
                }

        with tempfile.TemporaryDirectory() as temporary:
            session, _backend, _trace = ChatSessionTests().make_session(Path(temporary))
            controller = FakeLearningController()
            inputs = iter(
                [
                    "/learn Extract TODO from backlog.todo into todo-items.json",
                    "/creation status",
                    "/quit",
                ]
            )
            output: list[str] = []
            result = run_repl(
                session,
                learning_controller=controller,
                input_fn=lambda _prompt: next(inputs),
                output_fn=output.append,
            )
            self.assertEqual(result, 0)
            self.assertEqual(
                controller.needs,
                ["Extract TODO from backlog.todo into todo-items.json"],
            )
            rendered = "\n".join(output)
            self.assertIn("authoring → quarantine", rendered)
            self.assertIn("gates 12/12", rendered)
            self.assertIn('"candidate_skill_id": "extract-todo-items"', rendered)

    def test_audited_model_repair_status_and_gates_are_exposed_read_only(self) -> None:
        evidence_path = (
            Path(__file__).resolve().parents[1]
            / "experiments/mvp/results/model_authored_skill_repair_live_v1/"
            "model_authored_skill_repair_evidence.json"
        )
        repair = load_verified_repair_summary(evidence_path)
        self.assertEqual(repair["version"], [1, 2])
        self.assertEqual(repair["gate_count"], 6)
        self.assertEqual(repair["audit"]["checks_passed"], 13)

        with tempfile.TemporaryDirectory() as temporary:
            session, _backend, _trace = ChatSessionTests().make_session(Path(temporary))
            inputs = iter(["/repair status", "/repair gates", "/quit"])
            output: list[str] = []
            result = run_repl(
                session,
                repair_evidence=repair,
                input_fn=lambda _prompt: next(inputs),
                output_fn=output.append,
            )
        self.assertEqual(result, 0)
        rendered = "\n".join(output)
        self.assertIn("Verified model-authored repair loaded", rendered)
        self.assertIn('"version": [', rendered)
        self.assertIn('"audit_sha256"', rendered)
        self.assertIn('"copy_on_write_isolation"', rendered)

    def test_two_audited_repair_families_are_exposed_as_bounded_portfolio(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        first = load_verified_repair_summary(
            repo
            / "experiments/mvp/results/model_authored_skill_repair_live_v1/"
            "model_authored_skill_repair_evidence.json"
        )
        second = load_verified_repair_summary(
            repo
            / "experiments/mvp/results/model_authored_skill_repair_family2_live_v1/"
            "model_authored_skill_repair_family2_evidence.json"
        )
        portfolio = build_verified_repair_portfolio((first, second))
        self.assertEqual(portfolio["family_count"], 2)
        self.assertEqual(portfolio["gate_totals"], {"passed": 12, "total": 12})
        self.assertEqual(portfolio["audit_totals"], {"passed": 27, "total": 27})
        self.assertFalse(portfolio["claim_boundary"]["success_rate_inference_allowed"])
        self.assertEqual(second["hidden_held_out"]["baseline"], [1, 1])
        self.assertFalse(second["baseline_model_authored"])

        with tempfile.TemporaryDirectory() as temporary:
            session, _backend, _trace = ChatSessionTests().make_session(Path(temporary))
            inputs = iter(["/repair portfolio", "/quit"])
            output: list[str] = []
            result = run_repl(
                session,
                repair_evidence=first,
                repair_portfolio=portfolio,
                input_fn=lambda _prompt: next(inputs),
                output_fn=output.append,
            )
        self.assertEqual(result, 0)
        rendered = "\n".join(output)
        self.assertIn("Verified repair portfolio loaded", rendered)
        self.assertIn('"family_count": 2', rendered)
        self.assertIn('"success_rate_inference_allowed": false', rendered)

    def test_live_learning_rejects_unsupported_or_already_active_need_before_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, _backend, _trace = ChatSessionTests().make_session(Path(temporary))
            controller = LiveSkillCreationController(
                session=session,
                base_library=session.library,
                codex_executable=Path("/bin/echo"),
                model_id="gpt-5.6-terra",
                effort="high",
            )
            with self.assertRaisesRegex(LiveLearningError, "supports"):
                controller.learn("make some useful skill")
            active = make_single_step_skill(
                skill_id="extract-todo-items",
                name="Extract TODO Items",
                description="Extract TODO items when requested",
                trigger="TODO backlog.todo todo-items.json",
                step_description="extract",
                status=LifecycleStatus.ACTIVE,
            )
            session.library.save(active)
            with self.assertRaisesRegex(LiveLearningError, "already active"):
                controller.learn("Extract TODO from backlog.todo into todo-items.json")

    def test_verified_model_authored_promotion_loads_as_session_overlay(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = FileSkillLibrary(repo / "experiments" / "mvp" / "skills")
            evidence = make_promotion_fixture(root, base)
            overlay, summary = load_verified_promotion_overlay(
                base_library=base,
                evidence_path=evidence,
                overlay_root=root / "overlay",
            )

            self.assertEqual(summary["candidate_skill_id"], "extract-todo-items")
            self.assertTrue(summary["hidden_held_out_verifier_passed"])
            self.assertEqual(summary["gate_count"], 12)
            promoted = overlay.load("extract-todo-items")
            self.assertEqual(promoted.status, LifecycleStatus.ACTIVE)
            self.assertTrue((root / "library-overlay-manifest.json").is_file())
            bundle = overlay.verified_bundle_paths["extract-todo-items"]
            self.assertTrue((bundle / "scripts" / "run.py").is_file())

    def test_promoted_bundle_is_exposed_only_when_its_skill_is_selected(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            trace_root = workspace / ".merlin" / "chat" / "session-overlay"
            trace_root.mkdir(parents=True)
            base = FileSkillLibrary(repo / "experiments" / "mvp" / "skills")
            evidence = make_promotion_fixture(root, base)
            overlay, _summary = load_verified_promotion_overlay(
                base_library=base,
                evidence_path=evidence,
                overlay_root=trace_root / "library-overlay",
            )
            backend = FakeChatBackend(trace_root=trace_root)
            session = TheKingChatSession(
                workspace=workspace,
                library=overlay,
                backend=backend,
                trace_root=trace_root,
                top_k=3,
                skill_bundle_paths=overlay.verified_bundle_paths,
            )

            response = session.send(
                "backlog.todo에서 TODO 항목을 추출해 todo-items.json으로 저장해줘."
            )
            self.assertEqual(
                [item.skill_id for item in response.provisioned_skills],
                ["extract-todo-items"],
            )
            prompt = backend.calls[0]["prompt"]
            self.assertIn("VERIFIED PORTABLE BUNDLE:", prompt)
            self.assertIn("VERIFIED EXECUTION COMMAND: python3", prompt)
            self.assertIn('--workspace "', prompt)
            self.assertIn("promoted-bundles/extract-todo-items", prompt)

    def test_creation_commands_render_loaded_promotion_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, _backend, _trace = ChatSessionTests().make_session(Path(temporary))
            creation = {
                "candidate_skill_id": "extract-todo-items",
                "gate_count": 2,
                "gates": [
                    {"name": "G4_target", "passed": True},
                    {"name": "G6_adoption", "passed": True},
                ],
            }
            inputs = iter(["/creation status", "/creation gates", "/quit"])
            output: list[str] = []
            result = run_repl(
                session,
                creation_evidence=creation,
                input_fn=lambda _prompt: next(inputs),
                output_fn=output.append,
            )
            self.assertEqual(result, 0)
            rendered = "\n".join(output)
            self.assertIn("Verified promotion overlay loaded", rendered)
            self.assertIn("extract-todo-items", rendered)
            self.assertIn("G6_adoption", rendered)

    def test_repl_commands_and_feedback_work_with_fake_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, _backend, _trace = ChatSessionTests().make_session(Path(temporary))
            inputs = iter(
                [
                    "/help",
                    "/status",
                    "/skills",
                    "/trace",
                    "/diagnose",
                    "write report",
                    "/feedback pass",
                    "/diagnose",
                    "/new",
                    "/quit",
                ]
            )
            output: list[str] = []
            result = run_repl(session, input_fn=lambda _prompt: next(inputs), output_fn=output.append)
            self.assertEqual(result, 0)
            rendered = "\n".join(output)
            self.assertIn("assistant> answer-1", rendered)
            self.assertIn("report-writer", rendered)
            self.assertIn("feedback recorded", rendered)
            self.assertIn("diagnose blocked: no completed turn trace yet", rendered)
            self.assertIn('"status": "verifier_missing"', rendered)
            self.assertIn('"action_allowed": false', rendered)
            self.assertIn("New provider thread requested", rendered)

    def test_repl_governance_commands_run_the_controlled_same_verifier_lane(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, _backend, _trace = ChatSessionTests().make_session(Path(temporary))
            inputs = iter(
                [
                    "/governance load",
                    "/governance reference",
                    "/governance overload",
                    "/governance diagnose",
                    "/governance stage",
                    "/governance verify",
                    "/governance report",
                    "/quit",
                ]
            )
            output: list[str] = []
            result = run_repl(session, input_fn=lambda _prompt: next(inputs), output_fn=output.append)

            self.assertEqual(result, 0)
            rendered = "\n".join(output)
            self.assertIn('"stage": "verified"', rendered)
            self.assertIn('"passed": 1', rendered)
            self.assertIn('"passed": 9', rendered)
            self.assertIn('"accepted": true', rendered)
            self.assertIn('"mode": "copy_on_write"', rendered)
            self.assertIn("Same-verifier promotion accepted", rendered)

    def test_compact_recovery_demo_runs_real_state_machine(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session, _backend, _trace = ChatSessionTests().make_session(Path(temporary))
            inputs = iter(["/demo recovery", "/quit"])
            output: list[str] = []
            result = run_repl(
                session,
                input_fn=lambda _prompt: next(inputs),
                output_fn=output.append,
            )
            self.assertEqual(result, 0)
            rendered = "\n".join(output)
            self.assertIn('"passed": 1', rendered)
            self.assertIn('"shadowing_rate": 0.8888888888888888', rendered)
            self.assertIn('"passed": 9', rendered)
            self.assertIn('"shadowing_rate": 0.0', rendered)
            self.assertIn('"same_verifier_promotion": true', rendered)
            self.assertIn('"live_original_mutated_before_gate": false', rendered)

    def test_golden_demo_joins_controlled_recovery_with_hash_bound_recorded_chat_use(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        promotion_evidence = (
            repo
            / "experiments"
            / "mvp"
            / "results"
            / "model_authored_skill_live_v1"
            / "model_authored_skill_evidence.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session, backend = ChatSessionTests().make_mvp_library_session(root)
            base = FileSkillLibrary(repo / "experiments" / "mvp" / "skills")
            overlay, creation = load_verified_promotion_overlay(
                base_library=base,
                evidence_path=promotion_evidence,
                overlay_root=session.trace_root / "library-overlay",
            )
            session.install_verified_library_overlay(
                library=overlay,
                skill_bundle_paths=overlay.verified_bundle_paths,
            )
            inputs = iter(["/demo golden json", "/quit"])
            output: list[str] = []

            result = run_repl(
                session,
                creation_evidence=creation,
                promotion_evidence_path=promotion_evidence,
                input_fn=lambda _prompt: next(inputs),
                output_fn=output.append,
            )

            self.assertEqual(result, 0)
            self.assertEqual(backend.calls, [])
            rendered = "\n".join(output)
            self.assertIn('"demo": "Merlin judging golden pass"', rendered)
            self.assertIn('"build_week_scorecard"', rendered)
            self.assertIn('"technological_implementation"', rendered)
            self.assertIn('"production_impact_measured": false', rendered)
            self.assertIn('"kind": "controlled_overload_problem"', rendered)
            self.assertIn('"kind": "The_KING_trace_backed_intervention"', rendered)
            self.assertIn('"kind": "same_verifier_recovery"', rendered)
            self.assertIn(
                '"kind": "requested_GPT_5_6_candidate_quarantine_and_promotion"',
                rendered,
            )
            self.assertIn('"promotion_gates_passed": 12', rendered)
            self.assertIn('"kind": "recorded_model_authored_skill_chat_use"', rendered)
            self.assertIn('"recorded_evidence_file": "promoted_chat_smoke.json"', rendered)
            self.assertIn('"requested_model_id": "gpt-5.6-terra"', rendered)
            self.assertIn('"successful_promoted_script_execution_count": 1', rendered)
            self.assertIn('"this_command_makes_no_model_call": true', rendered)
            self.assertIn(
                '"controlled_recovery_and_recorded_chat_use_are_distinct_lanes": true',
                rendered,
            )
            self.assertIn('"provider_native_skill_invocation_event": false', rendered)

    def test_golden_demo_default_is_a_judge_readable_five_step_incident(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        promotion_evidence = (
            repo
            / "experiments"
            / "mvp"
            / "results"
            / "model_authored_skill_live_v1"
            / "model_authored_skill_evidence.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session, backend = ChatSessionTests().make_mvp_library_session(root)
            base = FileSkillLibrary(repo / "experiments" / "mvp" / "skills")
            overlay, creation = load_verified_promotion_overlay(
                base_library=base,
                evidence_path=promotion_evidence,
                overlay_root=session.trace_root / "library-overlay",
            )
            session.install_verified_library_overlay(
                library=overlay,
                skill_bundle_paths=overlay.verified_bundle_paths,
            )
            output: list[str] = []
            inputs = iter(["/demo golden", "/quit"])
            artifact_root = root / "judge-artifacts"

            result = run_repl(
                session,
                creation_evidence=creation,
                promotion_evidence_path=promotion_evidence,
                judge_mode=True,
                judge_artifact_root=artifact_root,
                input_fn=lambda _prompt: next(inputs),
                output_fn=output.append,
            )

            self.assertEqual(result, 0)
            self.assertEqual(backend.calls, [])
            rendered = "\n".join(output)
            self.assertIn("OFFLINE JUDGE MODE", rendered)
            self.assertIn("[1/5] PROBLEM", rendered)
            self.assertIn("1/10 pass · 89% shadowing", rendered)
            self.assertIn("[3/5] RECOVER", rendered)
            self.assertIn("9/10 pass · 0% shadowing · gate PASS", rendered)
            self.assertIn("[4/5] CREATE", rendered)
            self.assertIn("requested gpt-5.6-terra/high", rendered)
            self.assertIn("COW 12/12", rendered)
            self.assertIn("chain audit 15/15", rendered)
            self.assertIn("[5/5] USE", rendered)
            self.assertIn("promoted script runs 1 · verifier PASS", rendered)
            self.assertIn("provider-native Skill event is not claimed", rendered)
            self.assertIn("ARTIFACT", rendered)
            self.assertEqual(
                {path.name for path in artifact_root.iterdir()},
                {
                    "ARTIFACTS.json",
                    "controlled-lifecycle-control-room.html",
                    "controlled-lifecycle.json",
                    "golden-pass.json",
                    "golden-report.html",
                },
            )
            manifest = json.loads(
                (artifact_root / "ARTIFACTS.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(len(manifest["artifacts"]), 4)
            for record in manifest["artifacts"]:
                payload = (artifact_root / record["path"]).read_bytes()
                self.assertEqual(record["bytes"], len(payload))
                self.assertEqual(record["sha256"], hashlib.sha256(payload).hexdigest())
            golden_report = (artifact_root / "golden-report.html").read_text()
            self.assertIn("Merlin", golden_report)
            self.assertIn("Open technical Control Room", golden_report)
            self.assertIn("gpt-5.6-terra/high", golden_report)
            self.assertIn("Why this is a Developer Tool", golden_report)
            self.assertIn("Potential impact", golden_report)
            self.assertIn("Manage the whole skill harness", golden_report)

            with self.assertRaisesRegex(ValueError, "already exists"):
                write_golden_judge_artifacts(
                    output_root=artifact_root,
                    golden_summary=json.loads(
                        (artifact_root / "golden-pass.json").read_text(encoding="utf-8")
                    ),
                    lifecycle_report=json.loads(
                        (artifact_root / "controlled-lifecycle.json").read_text(
                            encoding="utf-8"
                        )
                    ),
                )

    def test_judge_mode_accepts_documented_natural_language_incident_request(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        promotion_evidence = (
            repo
            / "experiments"
            / "mvp"
            / "results"
            / "model_authored_skill_live_v1"
            / "model_authored_skill_evidence.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session, backend = ChatSessionTests().make_mvp_library_session(root)
            base = FileSkillLibrary(repo / "experiments" / "mvp" / "skills")
            overlay, creation = load_verified_promotion_overlay(
                base_library=base,
                evidence_path=promotion_evidence,
                overlay_root=session.trace_root / "library-overlay",
            )
            session.install_verified_library_overlay(
                library=overlay,
                skill_bundle_paths=overlay.verified_bundle_paths,
            )
            output: list[str] = []
            inputs = iter(
                [
                    "Diagnose and safely recover this overloaded skill library.",
                    "/quit",
                ]
            )

            result = run_repl(
                session,
                creation_evidence=creation,
                promotion_evidence_path=promotion_evidence,
                judge_mode=True,
                input_fn=lambda _prompt: next(inputs),
                output_fn=output.append,
            )

            self.assertEqual(result, 0)
            self.assertEqual(backend.calls, [])
            rendered = "\n".join(output)
            self.assertIn('Try: "Diagnose and safely recover', rendered)
            self.assertIn("[1/5] PROBLEM", rendered)
            self.assertIn("[5/5] USE", rendered)
            self.assertIn("promoted script runs 1 · verifier PASS", rendered)

    @patch("experiments.mvp.run_chat.run_repl", return_value=0)
    @patch("experiments.mvp.run_chat.detect_codex_runtime")
    def test_cli_judge_mode_needs_no_codex_runtime_and_loads_default_evidence(
        self, detect_mock, repl_mock
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            result = main(["--judge", "--workspace", str(workspace)])

        self.assertEqual(result, 0)
        detect_mock.assert_not_called()
        session = repl_mock.call_args.args[0]
        self.assertIsInstance(session.backend, OfflineJudgeBackend)
        self.assertEqual(session.routing_mode, "deterministic")
        self.assertTrue(repl_mock.call_args.kwargs["judge_mode"])
        self.assertEqual(
            repl_mock.call_args.kwargs["creation_evidence"]["candidate_skill_id"],
            "extract-todo-items",
        )

    def test_cli_judge_golden_runs_natural_language_flow_and_exits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = StringIO()
            with redirect_stdout(output):
                result = main(
                    [
                        "--judge",
                        "--golden",
                        "--workspace",
                        temporary,
                    ]
                )
            artifact_roots = list(Path(temporary).glob("judge-artifacts-*"))
            self.assertEqual(len(artifact_roots), 1)
            self.assertTrue((artifact_roots[0] / "ARTIFACTS.json").is_file())
            self.assertTrue((artifact_roots[0] / "golden-report.html").is_file())

        self.assertEqual(result, 0)
        rendered = output.getvalue()
        self.assertIn(
            "you> Diagnose and safely recover this overloaded skill library.",
            rendered,
        )
        self.assertIn("[1/5] PROBLEM", rendered)
        self.assertIn("[5/5] USE", rendered)
        self.assertIn("ARTIFACT", rendered)
        self.assertIn("you> /quit", rendered)
        self.assertIn("bye", rendered)

    @patch("experiments.mvp.run_chat.subprocess.run")
    def test_cli_detection_uses_existing_executable_and_version(self, run_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess([], 0, "codex-cli test\n", "")
        executable = detect_codex_executable(str(Path("/bin/echo")))
        self.assertEqual(executable, Path("/bin/echo").resolve())
        self.assertEqual(detect_codex_version(executable), "codex-cli test")
        self.assertEqual(run_mock.call_args.args[0], [str(executable), "--version"])

    @patch("experiments.mvp.run_chat.APP_CODEX_EXECUTABLE")
    @patch("experiments.mvp.run_chat.shutil.which")
    @patch("experiments.mvp.run_chat.subprocess.run")
    def test_runtime_detection_skips_broken_explicit_candidate_and_prefers_app(
        self, run_mock, which_mock, app_mock
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            broken = root / "broken-codex"
            app = root / "app-codex"
            for candidate in (broken, app):
                candidate.write_text("#!/bin/sh\n", encoding="utf-8")
                candidate.chmod(0o700)
            which_mock.return_value = str(broken)
            app_mock.__fspath__.return_value = str(app)
            app_mock.resolve.return_value = app.resolve()
            run_mock.side_effect = (
                OSError("missing internal vendor binary"),
                subprocess.CompletedProcess([], 0, "codex-cli app\n", ""),
            )

            executable, version = detect_codex_runtime(str(broken))

            self.assertEqual(executable, app.resolve())
            self.assertEqual(version, "codex-cli app")
            self.assertEqual(run_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
