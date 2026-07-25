from __future__ import annotations

import re
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "verify.yml"


class BuildWeekWorkflowContractTests(unittest.TestCase):
    def test_offline_macos_judge_workflow_has_required_contract(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

        required_fragments = (
            "permissions:\n  contents: read",
            "runs-on: macos-latest",
            "uses: actions/checkout@v5",
            "uses: actions/setup-python@v6",
            'python-version: "3.11"',
            "set -euo pipefail",
            "PYTHONDONTWRITEBYTECODE=1",
            "python3 -m unittest discover -s tests -p 'test_*.py' -v",
            'output_dir="$RUNNER_TEMP/merlin-lifecycle-recovery"',
            "python3 -m experiments.mvp.run_lifecycle_recovery_demo",
            'provisioning_dir="$RUNNER_TEMP/merlin-governed-provisioning"',
            "python3 -m experiments.mvp.evaluate_provisioning",
            'provisioning_evaluation.json',
            'data["acceptance_passed"] is True',
            'campaign_review="$RUNNER_TEMP/merlin-chat-lifecycle-review.html"',
            "python3 -m experiments.mvp.render_chat_campaign_report",
            'grep -q "Merlin · Chat Lifecycle Review" "$campaign_review"',
            'managed_dir="$RUNNER_TEMP/merlin-managed-creation"',
            "python3 -m experiments.mvp.run_managed_skill_creation_demo",
            'managed_creation_report.json',
            'data["adopted"] is True',
            'all(gate["passed"] for gate in data["gates"])',
            'data["evidence_boundary"]["actual_invocation_evidence_complete"] is False',
            'judge_output="$RUNNER_TEMP/merlin-offline-judge.txt"',
            "python3 -m experiments.mvp.run_chat --judge",
            "grep -q 'OFFLINE JUDGE MODE' \"$judge_output\"",
            "grep -q 'COW 12/12' \"$judge_output\"",
            "grep -q 'promoted script runs 1 · verifier PASS' \"$judge_output\"",
            "git archive HEAD | tar -x -C \"$archive_dir\"",
            "build_week_package.py\" check",
            "--package \"$archive_dir\"",
        )
        for fragment in required_fragments:
            self.assertIn(fragment, workflow)

        forbidden_fragments = (
            "actions/checkout@v4",
            "actions/setup-python@v5",
            "secrets.",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "run_codex_agent_trace_smoke",
            "codex exec",
            "gpt-5.6",
        )
        for fragment in forbidden_fragments:
            self.assertNotIn(fragment, workflow)

        judge_readme = (REPOSITORY_ROOT / "PRIVATE_JUDGE_REVIEW.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("python3 -m experiments.mvp.run_judge_chat --open", judge_readme)
        self.assertIn("python3 -m experiments.mvp.run_chat", judge_readme)
        self.assertIn("/demo golden", judge_readme)
        self.assertIn("docs/live-model-authored-skill-v1.md", judge_readme)
        self.assertIn("python3 -m experiments.mvp.render_chat_campaign_report", judge_readme)
        self.assertIn("python3 -m experiments.mvp.run_managed_skill_creation_demo", judge_readme)
        self.assertIn("## Primary native app path", judge_readme)
        self.assertIn("## 60-second account-free fallback", judge_readme)
        self.assertIn("./apps/merlin-macos/scripts/run-app.sh", judge_readme)
        self.assertIn("**Map**", judge_readme)
        self.assertIn("**Skills**", judge_readme)
        self.assertIn("**Evidence**", judge_readme)
        self.assertIn("CONTROLLED RUNTIME · RUN NOW", judge_readme)
        self.assertIn("RECORDED GPT-5.6 EVIDENCE", judge_readme)
        self.assertIn("five tool cards inline", judge_readme)
        self.assertIn("not provider-native\nskill-body invocation evidence", judge_readme)
        self.assertIn(
            "`27eb4b0..1b013e243a04033c9ff8b7b26829a49741843e50`",
            judge_readme,
        )
        self.assertIn("Reviewer access:** GitHub's live Collaborators page", judge_readme)
        self.assertIn("`build-week-event@openai.com`", judge_readme)
        self.assertIn("`testing@devpost.com` invitation", judge_readme)
        self.assertIn("acceptance remains under\nthe recipients' control", judge_readme)
        self.assertIn("agent platform and infrastructure\ndevelopers", judge_readme)

        build_readme = (REPOSITORY_ROOT / "BUILD_WEEK_README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("## Who it is for and why it matters", build_readme)
        self.assertIn("same-verifier gate helps lower diagnosis and rollback risk", build_readme)
        self.assertIn("python3 -m experiments.mvp.run_judge_chat --open", build_readme)
        self.assertIn("CONTROLLED RUNTIME · RUN NOW", build_readme)
        self.assertIn("RECORDED GPT-5.6 EVIDENCE", build_readme)
        self.assertIn("python3 -m experiments.mvp.run_chat", build_readme)
        self.assertIn("/demo golden", build_readme)
        self.assertIn("python3 -m experiments.mvp.render_chat_campaign_report", build_readme)
        self.assertIn("python3 -m experiments.mvp.run_managed_skill_creation_demo", build_readme)
        self.assertIn("Merlin Console debugger", build_readme)
        self.assertIn("docs/managed-skill-creation-contract.md", build_readme)
        self.assertIn("docs/live-model-authored-skill-v1.md", build_readme)

        devpost_copy = REPOSITORY_ROOT / "docs" / "build-week-devpost-copy.md"
        if devpost_copy.exists():
            devpost = devpost_copy.read_text(encoding="utf-8")
            self.assertIn(
                "https://github.com/WonJeong0913/merlin-build-week", devpost
            )
            self.assertIn(
                "`27eb4b0..1b013e243a04033c9ff8b7b26829a49741843e50`",
                devpost,
            )
            self.assertIn("## Who it is for / Potential impact", devpost)
            self.assertIn("## Why it is different", devpost)
            self.assertIn("Skill-generation approaches such as Hermes", devpost)
            self.assertIn("python3 -m experiments.mvp.run_chat", devpost)
            self.assertIn("/demo golden", devpost)
            self.assertIn("model-authored", devpost)
            self.assertNotIn("[PASTE PUBLIC OR JUDGE-SHARED REPOSITORY URL]", devpost)
            self.assertNotIn("[PASTE ONLY THE RELEVANT COMMIT IDS]", devpost)
            placeholders = set(re.findall(r"\[PASTE[^\]]+\]", devpost))
            self.assertSetEqual(
                placeholders,
                {
                    "[PASTE THE PRIMARY BUILD SESSION ID]",
                    "[PASTE PUBLIC <3-MINUTE YOUTUBE URL]",
                },
            )
