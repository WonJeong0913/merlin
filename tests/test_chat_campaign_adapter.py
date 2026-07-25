from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.mvp.run_chat_lifecycle_campaign import run_campaign
from src.merlin_harness.codex_chat import CodexChatTurnResult


class FakeTaskBackend:
    def __init__(self, *, workspace: Path, trace_root: Path) -> None:
        self.workspace = workspace
        self.trace_root = trace_root
        self.trace_root.mkdir(parents=True, exist_ok=False)

    def run_turn(self, *, prompt: str, turn_number: int, thread_id: str | None):
        request = prompt.rsplit("[USER REQUEST]", 1)[-1]
        mappings = {
            "audit.log": "audit complete\n",
            "report.md": "# Report\n",
            "errors.log": ("error-count.txt", "2\n"),
            "items.txt": ("item-count.txt", "4\n"),
        }
        for marker, result in mappings.items():
            if marker not in request:
                continue
            if isinstance(result, tuple):
                name, content = result
            else:
                name, content = marker, result
            (self.workspace / name).write_text(content, encoding="utf-8")
            break
        raw_path = self.trace_root / f"turn-{turn_number:04d}.codex.jsonl"
        raw_path.write_text(json.dumps({"type": "fake-provider-turn"}) + "\n", encoding="utf-8")
        import hashlib

        digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        return CodexChatTurnResult(
            turn_number=turn_number,
            resumed=False,
            thread_id=f"fake-{self.workspace.name}",
            turn_id=f"turn-{turn_number}",
            answer="done",
            raw_trace_pointer=raw_path.name,
            raw_trace_sha256=digest,
            metadata={
                "provider": "fake",
                "prompt_provisioning_is_provider_native_invocation": False,
                "actual_invocation_evidence_complete": False,
            },
        )


class ChatCampaignAdapterTests(unittest.TestCase):
    def test_real_session_adapter_recovers_exposure_with_same_verifiers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            payload = run_campaign(
                run_root=root,
                backend_factory=lambda *, workspace, trace_root: FakeTaskBackend(
                    workspace=workspace, trace_root=trace_root
                ),
                requested_model_id="fake-model",
                requested_effort="low",
                cli_version="fake-cli",
            )

            self.assertEqual(payload["baseline"]["passed"], 4)
            self.assertEqual(payload["baseline"]["exposure_shadowing_rate"], 1.0)
            self.assertEqual(payload["provisional"]["passed"], 4)
            self.assertEqual(payload["provisional"]["exposure_shadowing_rate"], 0.0)
            self.assertTrue(payload["promotion"]["accepted"])
            self.assertEqual(
                {item["skill_id"] for item in payload["lifecycle_decisions"]},
                {"aa-file-artifact-distractor", "aa-line-count-distractor"},
            )
            self.assertFalse(
                payload["evidence_boundary"]["actual_invocation_evidence_complete"]
            )
            self.assertTrue((root / "baseline" / "01-create-audit-log" / "audit.log").is_file())
            self.assertTrue((root / "provisional" / "04-count-items" / "item-count.txt").is_file())

    def test_packaged_live_evidence_preserves_model_and_invocation_boundaries(self) -> None:
        evidence_path = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "evidence"
            / "gpt56-chat-lifecycle-campaign.json"
        )
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["baseline"]["passed"], 4)
        self.assertEqual(payload["baseline"]["exposure_shadowing_rate"], 1.0)
        self.assertEqual(payload["provisional"]["passed"], 4)
        self.assertEqual(payload["provisional"]["exposure_shadowing_rate"], 0.0)
        self.assertTrue(payload["promotion"]["accepted"])
        self.assertEqual(
            payload["runtime_contract"]["model_evidence_level"],
            "requested_cli_contract_only",
        )
        self.assertEqual(payload["runtime_contract"]["provider_reported_model_ids"], [])
        self.assertFalse(
            payload["evidence_boundary"]["actual_invocation_evidence_complete"]
        )
        for arm in ("baseline", "provisional"):
            for route in payload[arm]["routes"]:
                self.assertRegex(route["raw_trace_sha256"], r"^[0-9a-f]{64}$")
        rendered = json.dumps(payload, sort_keys=True)
        self.assertNotIn("provider_thread_id", rendered)
        self.assertNotIn("/private/tmp/", rendered)

    def test_executor_refuses_nonempty_run_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "existing.txt").write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "empty directory"):
                run_campaign(
                    run_root=root,
                    backend_factory=lambda **kwargs: FakeTaskBackend(**kwargs),
                    requested_model_id="fake-model",
                    requested_effort="low",
                    cli_version="fake-cli",
                )


if __name__ == "__main__":
    unittest.main()
