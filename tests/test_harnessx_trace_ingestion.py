from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from src.merlin_harness.harnessx_chat_shadow import HarnessXChatShadow
from src.merlin_harness.harnessx_live_hook import (
    run_pre_tool_use,
    write_new_live_tool_policy,
)
from src.merlin_harness.harnessx_runtime import make_default_harnessx_runtime
from src.merlin_harness.harnessx_trace_ingestion import (
    HarnessXTraceIngestionError,
    ingest_chat_shadow_report,
    ingest_live_hook_audit,
    write_trace_ingestion_report,
)
from src.merlin_harness.harnessx_verifier_suites import (
    MULTITARGET_50_TOOL_POLICY_VERIFIER_SUITE,
)


def _hook_payload(command: str) -> dict:
    return {
        "session_id": "session",
        "turn_id": "turn",
        "cwd": "/private/tmp/workspace",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_use_id": f"use-{command}",
        "tool_input": {"command": command},
        "permission_mode": "default",
    }


def _command_trace(path: Path, command: str) -> str:
    events = [
        {
            "type": "item.started",
            "item": {
                "id": "command-1",
                "type": "command_execution",
                "command": command,
                "aggregated_output": "",
                "status": "in_progress",
                "exit_code": None,
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "command-1",
                "type": "command_execution",
                "command": command,
                "aggregated_output": "output",
                "status": "completed",
                "exit_code": 0,
            },
        },
    ]
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HarnessXTraceIngestionTests(unittest.TestCase):
    def test_live_false_deny_automatically_nominates_aegis(self) -> None:
        suite = MULTITARGET_50_TOOL_POLICY_VERIFIER_SUITE
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _policy_path = root / "policy.json"
            policy = write_new_live_tool_policy(
                _policy_path,
                policy_id="parent",
                allowed_commands=("pwd", "/bin/pwd"),
            )
            audit = root / "audit.jsonl"
            run_pre_tool_use(
                _hook_payload("pwd"),
                policy=policy,
                audit_path=audit,
            )
            run_pre_tool_use(
                _hook_payload("ls -1"),
                policy=policy,
                audit_path=audit,
            )
            ingestion = ingest_live_hook_audit(
                audit,
                verifier_suite=suite,
            )

            self.assertTrue(ingestion.eligible_for_aegis)
            self.assertEqual(ingestion.actionable_case_ids, ("directory-list-read",))
            self.assertEqual(
                {signal.signal_kind for signal in ingestion.matched_signals},
                {"confirmed", "false_deny"},
            )
            output = write_trace_ingestion_report(root / "ingestion.json", ingestion)
            stored = output.read_text(encoding="utf-8")
            self.assertNotIn('"command":', stored)
            with self.assertRaisesRegex(
                HarnessXTraceIngestionError,
                "overwrite",
            ):
                write_trace_ingestion_report(output, ingestion)

    def test_safety_false_allow_blocks_automatic_aegis(self) -> None:
        suite = MULTITARGET_50_TOOL_POLICY_VERIFIER_SUITE
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = write_new_live_tool_policy(
                root / "policy.json",
                policy_id="unsafe-parent",
                allowed_commands=("pwd", "/bin/pwd", "touch harnessx-blocked.txt"),
            )
            audit = root / "audit.jsonl"
            run_pre_tool_use(
                _hook_payload("touch harnessx-blocked.txt"),
                policy=policy,
                audit_path=audit,
            )
            ingestion = ingest_live_hook_audit(audit, verifier_suite=suite)

            self.assertFalse(ingestion.eligible_for_aegis)
            self.assertIn(
                "safety_false_allow_requires_human_review",
                ingestion.blockers,
            )

    def test_post_execution_shadow_is_indexed_but_cannot_authorize(self) -> None:
        suite = MULTITARGET_50_TOOL_POLICY_VERIFIER_SUITE
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "provider.jsonl"
            raw_sha = _command_trace(raw, "ls -1")
            shadow = HarnessXChatShadow(
                runtime=make_default_harnessx_runtime(
                    system_prompt_suffix="\nshadow",
                ),
                trace_root=root,
            )
            context = shadow.start(turn_number=1, prompt="inspect", resumed=False)
            reference = shadow.finish(
                context,
                answer="done",
                provider_turn_id="turn",
                raw_trace_pointer=raw.name,
                raw_trace_sha256=raw_sha,
            )
            ingestion = ingest_chat_shadow_report(
                root / reference.pointer,
                verifier_suite=suite,
            )

            self.assertFalse(ingestion.eligible_for_aegis)
            self.assertEqual(len(ingestion.matched_signals), 1)
            self.assertEqual(
                ingestion.matched_signals[0].signal_kind,
                "post_execution_observation",
            )
            self.assertIn(
                "post_execution_shadow_cannot_nominate_policy_change_alone",
                ingestion.blockers,
            )


if __name__ == "__main__":
    unittest.main()
