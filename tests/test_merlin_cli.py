from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = REPO_ROOT / "cli" / "merlin_cli.py"
SPEC = importlib.util.spec_from_file_location("merlin_cli", CLI_PATH)
assert SPEC is not None and SPEC.loader is not None
merlin_cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = merlin_cli
SPEC.loader.exec_module(merlin_cli)

from src.merlin_harness import governance_view  # noqa: E402


def run(argv: list[str]) -> tuple[int, str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = merlin_cli.main(argv)
    return code, buffer.getvalue()


class GovernanceCommandTests(unittest.TestCase):
    def test_governance_reports_the_same_payload_the_app_receives(self) -> None:
        code, output = run(["governance", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(output), governance_view.harness_governance_summary()
        )

    def test_text_output_states_the_blocking_reason(self) -> None:
        _code, output = run(["governance"])
        summary = governance_view.harness_governance_summary()
        blocking = summary["invocation_evidence"]["blocking_reason"]
        if blocking is not None:
            self.assertIn(blocking, output)

    def test_an_empty_ledger_is_healthy_but_a_failed_one_is_not(self) -> None:
        code, _output = run(["campaign"])
        self.assertEqual(code, 0)
        broken = dict(governance_view._campaign_governance())
        broken["validated"] = False
        broken["validation_error"] = "ledger chain drift at line 2"
        with patch.object(
            governance_view, "_campaign_governance", return_value=broken
        ):
            code, output = run(["campaign"])
        self.assertEqual(code, 1)
        self.assertIn("ledger chain drift", output)

    def test_absent_campaign_artifacts_exit_non_zero(self) -> None:
        with patch.object(governance_view, "CAMPAIGN_DIR", "does/not/exist"):
            code, output = run(["campaign"])
        self.assertEqual(code, 1)
        self.assertIn("absent", output)

    def test_governance_exits_non_zero_for_an_existing_invalid_evolution_ledger(self) -> None:
        summary = governance_view.harness_governance_summary()
        broken_summary = dict(summary)
        broken_summary["evolution"] = {
            "ledger_present": True,
            "ledger_path": "experiments/mvp/results/evolution.jsonl",
            "validated": False,
            "validation_error": "hash chain drift at line 2",
        }
        with patch.object(
            governance_view, "harness_governance_summary", return_value=broken_summary
        ):
            code, output = run(["governance"])
        self.assertEqual(code, 1)
        self.assertIn("hash chain drift", output)


class EvolutionCommandTests(unittest.TestCase):
    def test_absent_ledger_is_reported_without_failing(self) -> None:
        with patch.object(governance_view, "EVOLUTION_LEDGER", "does/not/exist.jsonl"):
            code, output = run(["evolution"])
        self.assertEqual(code, 0)
        self.assertIn("absent", output)

    def test_a_ledger_that_fails_revalidation_exits_non_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "evolution.jsonl"
            broken.write_text("not a ledger record\n", encoding="utf-8")
            relative = broken.relative_to(broken.anchor)
            with patch.object(governance_view, "REPO_ROOT", Path(broken.anchor)), patch.object(
                governance_view, "EVOLUTION_LEDGER", str(relative)
            ):
                code, output = run(["evolution"])
        self.assertEqual(code, 1)
        self.assertIn("FAILED", output)


class SkillsCommandTests(unittest.TestCase):
    def test_skills_lists_the_active_library(self) -> None:
        code, output = run(["skills", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["count"], len(payload["skills"]))
        for skill in payload["skills"]:
            self.assertIn("id", skill)
            self.assertIn("status", skill)

    def test_absent_skills_root_exits_non_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "nope"
            stderr = io.StringIO()
            with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
                code = merlin_cli.main(["skills", "--skills-root", str(missing)])
        self.assertEqual(code, 1)
        self.assertIn("absent", stderr.getvalue())
        # The CLI is read-only: a missing root is reported, never created.
        self.assertFalse(missing.exists())


class UsageTests(unittest.TestCase):
    def test_an_unknown_subcommand_is_a_usage_error(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            with redirect_stderr(io.StringIO()):
                merlin_cli.main(["nope"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
