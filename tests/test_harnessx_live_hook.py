from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.merlin_harness.harnessx_live_hook import (
    HarnessXLiveHookError,
    append_live_hook_audit,
    load_and_validate_live_hook_audit,
    load_live_tool_policy,
    run_post_tool_use,
    run_pre_tool_use,
    write_new_exact_tool_call_policy,
    write_new_live_tool_policy,
)


def hook_payload(*, event: str, command: str, tool_name: str = "Bash") -> dict:
    payload = {
        "session_id": "session-sensitive-id",
        "turn_id": "turn-sensitive-id",
        "cwd": "/private/tmp/workspace",
        "hook_event_name": event,
        "tool_name": tool_name,
        "tool_use_id": f"use-{command}",
        "tool_input": {"command": command},
        "permission_mode": "default",
    }
    if event == "PostToolUse":
        payload["tool_response"] = {"output": "RAW-OUTPUT-MUST-NOT-BE-STORED", "exit_code": 0}
    return payload


class HarnessXLiveHookTests(unittest.TestCase):
    def make_policy(self, root: Path):
        path = root / "policy.json"
        policy = write_new_live_tool_policy(
            path,
            policy_id="test-read-only-v1",
            allowed_commands=("pwd", "/bin/pwd"),
        )
        return path, policy

    def test_pre_hook_allows_exact_command_and_denies_combined_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _path, policy = self.make_policy(root)
            audit = root / "audit.jsonl"

            allowed = run_pre_tool_use(
                hook_payload(event="PreToolUse", command="pwd"),
                policy=policy,
                audit_path=audit,
            )
            denied_command = "pwd; touch RAW-COMMAND-MUST-NOT-BE-STORED"
            denied = run_pre_tool_use(
                hook_payload(event="PreToolUse", command=denied_command),
                policy=policy,
                audit_path=audit,
            )

            self.assertEqual(
                allowed["hookSpecificOutput"]["permissionDecision"],
                "allow",
            )
            self.assertEqual(
                denied["hookSpecificOutput"]["permissionDecision"],
                "deny",
            )
            raw_audit = audit.read_text(encoding="utf-8")
            self.assertNotIn(denied_command, raw_audit)
            records = [json.loads(line) for line in raw_audit.splitlines()]
            self.assertEqual(tuple(records), load_and_validate_live_hook_audit(audit))
            self.assertEqual([record["sequence"] for record in records], [1, 2])
            self.assertEqual(records[1]["previous_record_sha256"], records[0]["record_sha256"])
            self.assertEqual(records[0]["processor_outcome"], "pass_through")
            self.assertEqual(records[1]["processor_outcome"], "intercept")

    def test_post_hook_hashes_response_without_persisting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _path, policy = self.make_policy(root)
            audit = root / "audit.jsonl"
            output = run_post_tool_use(
                hook_payload(event="PostToolUse", command="pwd"),
                policy=policy,
                audit_path=audit,
            )
            self.assertEqual(output, {})
            stored = audit.read_text(encoding="utf-8")
            self.assertNotIn("RAW-OUTPUT-MUST-NOT-BE-STORED", stored)
            record = json.loads(stored)
            self.assertEqual(record["phase"], "post_tool_use")
            self.assertFalse(record["raw_tool_response_stored"])

    def test_live_hook_enforces_exact_non_bash_tool_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = write_new_exact_tool_call_policy(
                root / "multitool-policy.json",
                policy_id="exact-multitool-v1",
                allowed_tool_inputs=(
                    {
                        "tool_name": "Read",
                        "tool_input": {"file_path": "/private/tmp/allowed.txt"},
                    },
                    {
                        "tool_name": "Grep",
                        "tool_input": {
                            "pattern": "TODO",
                            "path": "/private/tmp/workspace",
                        },
                    },
                ),
            )
            audit = root / "audit.jsonl"
            allowed_payload = hook_payload(
                event="PreToolUse",
                command="unused",
                tool_name="Read",
            )
            allowed_payload["tool_input"] = {
                "file_path": "/private/tmp/allowed.txt"
            }
            denied_payload = hook_payload(
                event="PreToolUse",
                command="unused",
                tool_name="Read",
            )
            denied_payload["tool_input"] = {
                "file_path": "/private/tmp/other.txt"
            }
            allowed = run_pre_tool_use(
                allowed_payload,
                policy=policy,
                audit_path=audit,
            )
            denied = run_pre_tool_use(
                denied_payload,
                policy=policy,
                audit_path=audit,
            )
            self.assertEqual(
                allowed["hookSpecificOutput"]["permissionDecision"],
                "allow",
            )
            self.assertEqual(
                denied["hookSpecificOutput"]["permissionDecision"],
                "deny",
            )
            records = load_and_validate_live_hook_audit(audit)
            self.assertEqual(records[0]["processor"], "exact_tool_call_policy")
            self.assertNotIn("/private/tmp/allowed.txt", audit.read_text())

    def test_policy_is_new_only_and_self_hashing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, policy = self.make_policy(root)
            self.assertEqual(load_live_tool_policy(path), policy)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 2)
            self.assertEqual(
                payload["harness_configuration"]["processors"][0]["hook"],
                "before_tool",
            )
            self.assertEqual(
                payload["harness_configuration"]["processors"][0]["singleton_group"],
                "live_tool_input_policy",
            )
            self.assertEqual(
                payload["harness_configuration"]["policy"]["dimensions"],
                ["D4", "D7", "D8"],
            )
            with self.assertRaisesRegex(HarnessXLiveHookError, "overwrite"):
                write_new_live_tool_policy(
                    path,
                    policy_id="replacement",
                    allowed_commands=("pwd",),
                )
            payload["harness_configuration"]["processors"][0]["config"][
                "allowed_commands"
            ].append("touch allowed.txt")
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(HarnessXLiveHookError, "SHA-256 mismatch"):
                load_live_tool_policy(path)

    def test_audit_tampering_fails_closed_before_append(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            audit = Path(temporary) / "audit.jsonl"
            append_live_hook_audit(audit, {"phase": "pre_tool_use", "decision": "deny"})
            record = json.loads(audit.read_text(encoding="utf-8"))
            record["decision"] = "allow"
            audit.write_text(json.dumps(record) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(HarnessXLiveHookError, "hash chain"):
                append_live_hook_audit(
                    audit,
                    {"phase": "pre_tool_use", "decision": "deny"},
                )
            with self.assertRaisesRegex(HarnessXLiveHookError, "hash chain"):
                load_and_validate_live_hook_audit(audit)


if __name__ == "__main__":
    unittest.main()
