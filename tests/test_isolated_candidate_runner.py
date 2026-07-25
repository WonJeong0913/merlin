from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.merlin_harness.isolated_candidate_runner import (
    BoundedProcessResult,
    CandidateExecutionCase,
    IsolatedCandidateRunnerError,
    build_macos_sandbox_profile,
    run_quarantined_candidate,
    run_quarantined_candidate_phase,
)
from src.merlin_harness.model_candidate_quarantine import (
    ModelCandidateEnvelope,
    ModelCandidateFile,
    quarantine_model_candidate,
)


def _expected(*items: str) -> str:
    return json.dumps({"items": list(items)}, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _envelope() -> ModelCandidateEnvelope:
    return ModelCandidateEnvelope(
        candidate_skill_id="extract-todo-items",
        generator_backend="openai-codex-cli",
        generator_model="gpt-5.6-terra",
        generator_effort="high",
        generator_prompt_sha256="a" * 64,
        generator_response_sha256="b" * 64,
        files=(
            ModelCandidateFile(
                "SKILL.md",
                "---\nname: extract-todo-items\n"
                "description: Use when TODO lines must be extracted from backlog.todo.\n"
                "---\n\n# Extract TODO Items\n\nRun the isolated script.\n",
            ),
            ModelCandidateFile(
                "agents/openai.yaml",
                "interface:\n"
                "  display_name: Extract TODO Items\n"
                "  short_description: Extract TODO lines into JSON.\n"
                "  default_prompt: Use $extract-todo-items to process backlog.todo.\n",
            ),
            ModelCandidateFile(
                "scripts/run.py",
                "import argparse\nimport json\nfrom pathlib import Path\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--workspace', required=True)\n"
                "args = parser.parse_args()\n"
                "root = Path(args.workspace).resolve()\n"
                "items = [line[5:].strip() for line in (root / 'backlog.todo').read_text().splitlines() if line.startswith('TODO:')]\n"
                "(root / 'todo-items.json').write_text(json.dumps({'items': items}, indent=2, sort_keys=True) + '\\n')\n",
            ),
        ),
    )


def _cases() -> tuple[CandidateExecutionCase, ...]:
    return (
        CandidateExecutionCase(
            "target-english",
            "target",
            (("backlog.todo", "TODO: fix login\nnote: ignore\nTODO: write tests\n"),),
            (("todo-items.json", _expected("fix login", "write tests")),),
        ),
        CandidateExecutionCase(
            "held-out-korean",
            "held_out",
            (("backlog.todo", "TODO: 회귀 테스트\n메모: 제외\nTODO: 문서 갱신\n"),),
            (("todo-items.json", _expected("회귀 테스트", "문서 갱신")),),
        ),
    )


def _fake_process(
    command: list[str], workspace: Path, stdout: Path, stderr: Path, timeout: float
) -> BoundedProcessResult:
    del command, timeout
    items = [
        line[5:].strip()
        for line in (workspace / "backlog.todo").read_text(encoding="utf-8").splitlines()
        if line.startswith("TODO:")
    ]
    (workspace / "todo-items.json").write_text(_expected(*items), encoding="utf-8")
    stdout.write_bytes(b"")
    stderr.write_bytes(b"")
    return BoundedProcessResult(return_code=0, timed_out=False, latency_s=0.01)


class IsolatedCandidateRunnerTests(unittest.TestCase):
    def test_manifest_bound_target_and_hidden_cases_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            quarantine_root = root / "quarantine"
            quarantine = quarantine_model_candidate(
                envelope=_envelope(), output_root=quarantine_root
            )
            result = run_quarantined_candidate(
                quarantine_root=quarantine_root,
                expected_manifest_sha256=quarantine.manifest_sha256,
                cases=_cases(),
                output_root=root / "execution",
                process_runner=_fake_process,
            )

            self.assertTrue(result.all_passed)
            self.assertTrue(result.target_passed)
            self.assertTrue(result.held_out_passed)
            self.assertTrue(result.evidence_boundary["candidate_isolated_execution"])
            self.assertFalse(result.evidence_boundary["network_allowed"])
            report = json.loads(
                (root / "execution" / "isolated_execution_report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(report["all_passed"])
            self.assertEqual(len(report["cases"]), 2)

    def test_candidate_drift_is_rejected_before_process_execution(self) -> None:
        called = False

        def must_not_run(*args: object, **kwargs: object) -> BoundedProcessResult:
            nonlocal called
            called = True
            raise AssertionError("process should not run")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            quarantine_root = root / "quarantine"
            quarantine = quarantine_model_candidate(
                envelope=_envelope(), output_root=quarantine_root
            )
            script = quarantine_root / "candidate" / "extract-todo-items" / "scripts" / "run.py"
            script.write_text(script.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
            with self.assertRaisesRegex(IsolatedCandidateRunnerError, "drifted"):
                run_quarantined_candidate(
                    quarantine_root=quarantine_root,
                    expected_manifest_sha256=quarantine.manifest_sha256,
                    cases=_cases(),
                    output_root=root / "execution",
                    process_runner=must_not_run,
                )
            self.assertFalse(called)

    def test_repair_phase_executes_only_declared_target_split(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            quarantine_root = root / "quarantine"
            quarantine = quarantine_model_candidate(
                envelope=_envelope(), output_root=quarantine_root
            )
            target = (_cases()[0],)
            result = run_quarantined_candidate_phase(
                quarantine_root=quarantine_root,
                expected_manifest_sha256=quarantine.manifest_sha256,
                phase="target",
                cases=target,
                output_root=root / "target-execution",
                process_runner=_fake_process,
            )

            self.assertTrue(result.all_passed)
            self.assertEqual(result.phase, "target")
            self.assertEqual(tuple(item.case_id for item in result.cases), ("target-english",))
            self.assertFalse(result.evidence_boundary["other_splits_executed"])
            self.assertTrue(
                (root / "target-execution" / "isolated_target_report.json").is_file()
            )

    def test_repair_phase_rejects_mixed_or_mislabeled_splits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            quarantine_root = root / "quarantine"
            quarantine = quarantine_model_candidate(
                envelope=_envelope(), output_root=quarantine_root
            )
            with self.assertRaisesRegex(
                IsolatedCandidateRunnerError, "declared split"
            ):
                run_quarantined_candidate_phase(
                    quarantine_root=quarantine_root,
                    expected_manifest_sha256=quarantine.manifest_sha256,
                    phase="target",
                    cases=_cases(),
                    output_root=root / "mixed-execution",
                    process_runner=_fake_process,
                )

    def test_profile_denies_network_and_limits_candidate_write_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, workspace = root / "candidate", root / "workspace"
            candidate.mkdir()
            workspace.mkdir()
            profile = build_macos_sandbox_profile(
                candidate_root=candidate, workspace=workspace
            )
            self.assertIn("(deny network*)", profile)
            self.assertIn(f'(subpath "{candidate.resolve()}")', profile)
            self.assertIn(f'(allow file-write* (subpath "{workspace.resolve()}"))', profile)
            self.assertNotIn(f'(allow file-write* (subpath "{candidate.resolve()}"))', profile)

    def test_sandbox_apply_failure_is_not_misclassified_as_candidate_failure(self) -> None:
        def unavailable_sandbox(
            command: list[str],
            workspace: Path,
            stdout: Path,
            stderr: Path,
            timeout: float,
        ) -> BoundedProcessResult:
            del command, workspace, timeout
            stdout.write_bytes(b"")
            stderr.write_bytes(
                b"sandbox-exec: sandbox_apply: Operation not permitted\n"
            )
            return BoundedProcessResult(
                return_code=71,
                timed_out=False,
                latency_s=0.01,
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            quarantine_root = root / "quarantine"
            quarantine = quarantine_model_candidate(
                envelope=_envelope(), output_root=quarantine_root
            )
            with self.assertRaisesRegex(
                IsolatedCandidateRunnerError,
                "could not apply confinement",
            ):
                run_quarantined_candidate(
                    quarantine_root=quarantine_root,
                    expected_manifest_sha256=quarantine.manifest_sha256,
                    cases=_cases(),
                    output_root=root / "execution",
                    process_runner=unavailable_sandbox,
                )
            self.assertFalse((root / "execution/isolated_execution_report.json").exists())


if __name__ == "__main__":
    unittest.main()
