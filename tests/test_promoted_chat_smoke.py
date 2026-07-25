from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from experiments.mvp.package_promoted_chat_smoke import (
    PromotedChatEvidenceError,
    package_promoted_chat_smoke,
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class PromotedChatSmokeTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        workspace = root / "workspace"
        session = workspace / ".merlin" / "chat" / "session-test"
        session.mkdir(parents=True)
        promotion_path = root / "model_authored_skill_evidence.json"
        promotion = {"schema_version": 1, "adopted": True, "candidate_skill_id": "extract-todo-items"}
        _write_json(promotion_path, promotion)
        _write_json(
            session / "library-overlay-manifest.json",
            {
                "adopted": True,
                "candidate_skill_id": "extract-todo-items",
                "source_evidence_sha256": hashlib.sha256(promotion_path.read_bytes()).hexdigest(),
                "candidate_bundle_manifest_sha256": "a" * 64,
                "session_overlay_snapshot_sha256": "b" * 64,
            },
        )
        raw_events = [
            {"type": "thread.started", "thread_id": "thread-1"},
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "sed promoted-bundles/extract-todo-items/SKILL.md",
                    "exit_code": 0,
                    "status": "completed",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "python promoted-bundles/extract-todo-items/scripts/run.py --workspace /task",
                    "exit_code": 0,
                    "status": "completed",
                },
            },
        ]
        raw = "\n".join(json.dumps(item) for item in raw_events) + "\n"
        raw_path = session / "turn-0001.codex.jsonl"
        raw_path.write_text(raw, encoding="utf-8")
        _write_json(
            session / "turn-0001.meta.json",
            {
                "raw_trace": {
                    "pointer": raw_path.name,
                    "sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                },
                "provisioned_skills": [{"skill_id": "extract-todo-items"}],
                "routing_decision": {
                    "routing_source": "deterministic",
                    "active_skill_count": 3,
                    "final_provisioned_ids": ["extract-todo-items"],
                },
                "deterministic_reference_decision": {
                    "harness_primary_id": "extract-todo-items"
                },
                "backend_metadata": {
                    "return_code": 0,
                    "provider": "openai-codex-cli",
                    "cli_version": "codex-cli test",
                    "model_id": "gpt-5.6-terra",
                    "effort": "high",
                    "provider_reported_model_ids": [],
                    "event_count": len(raw_events),
                },
            },
        )
        expected = json.dumps(
            {"items": ["회귀 테스트", "데모 문서 갱신"]},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        (workspace / "todo-items.json").write_text(expected, encoding="utf-8")
        return workspace, session, promotion_path

    def test_packages_script_execution_without_upgrading_native_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, session, promotion = self._fixture(root)
            result = package_promoted_chat_smoke(
                workspace=workspace,
                session_root=session,
                promotion_evidence_path=promotion,
                output_path=root / "safe.json",
            )
            self.assertTrue(result["verifier"]["passed"])
            self.assertEqual(
                result["trace_observation"]["successful_promoted_script_execution_count"], 1
            )
            self.assertTrue(
                result["evidence_boundary"][
                    "promoted_bundle_script_execution_observed_in_provider_trace"
                ]
            )
            self.assertFalse(
                result["evidence_boundary"]["provider_native_skill_invocation_event"]
            )

    def test_raw_trace_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, session, promotion = self._fixture(root)
            raw = session / "turn-0001.codex.jsonl"
            raw.write_text(raw.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
            with self.assertRaisesRegex(PromotedChatEvidenceError, "hash"):
                package_promoted_chat_smoke(
                    workspace=workspace,
                    session_root=session,
                    promotion_evidence_path=promotion,
                    output_path=root / "safe.json",
                )


if __name__ == "__main__":
    unittest.main()
