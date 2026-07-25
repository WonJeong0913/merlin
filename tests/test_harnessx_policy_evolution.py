from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.merlin_harness.harnessx_policy_evolution import (
    HarnessXPolicyEvolutionError,
    evolve_live_tool_policy,
    validate_live_tool_policy_evolution,
)
from src.merlin_harness.harnessx_runtime import (
    ToolCallEvent,
    build_harnessx_runtime_from_variant,
    harnessx_variant_from_payload,
    make_default_harnessx_registry,
)


class HarnessXPolicyEvolutionTests(unittest.TestCase):
    def test_regressing_revision_rolls_back_and_corrected_revision_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evolution"
            report = evolve_live_tool_policy(output)
            validation = validate_live_tool_policy_evolution(output)

            self.assertFalse(report["rejected_revision"]["gate"]["accepted"])
            self.assertEqual(
                report["rejected_revision"]["gate"]["resolution"],
                "candidate_rejected_rollback_parent",
            )
            self.assertTrue(report["promoted_revision"]["gate"]["accepted"])
            self.assertEqual(
                report["promoted_revision"]["gate"]["resolution"],
                "candidate_harness_promoted",
            )
            self.assertTrue(validation["valid"])
            self.assertEqual(
                validation["resolved_variant_sha256"],
                report["resolved_variant_sha256"],
            )

    def test_resolved_variant_allows_new_read_only_case_and_retains_denials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evolution"
            evolve_live_tool_policy(output)
            variant = harnessx_variant_from_payload(
                json.loads((output / "resolved-variant.json").read_text(encoding="utf-8"))
            )
            runtime = build_harnessx_runtime_from_variant(
                variant,
                make_default_harnessx_registry(),
            )

            def decision(command: str, *, tool_name: str = "Bash") -> str:
                emission = runtime.emit_sync(
                    ToolCallEvent(
                        event_id=f"case-{len(command)}",
                        task_id="test",
                        step_index=1,
                        tool_name=tool_name,
                        tool_input_json=json.dumps({"command": command}),
                    )
                )
                return "deny" if emission.intercepted else "allow"

            self.assertEqual(decision("ls -1"), "allow")
            self.assertEqual(decision("pwd"), "allow")
            self.assertEqual(decision("touch harnessx-blocked.txt"), "deny")
            self.assertEqual(decision("*** Begin Patch", tool_name="apply_patch"), "deny")

    def test_saved_evolution_artifact_tampering_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evolution"
            evolve_live_tool_policy(output)
            resolved_path = output / "resolved-variant.json"
            payload = json.loads(resolved_path.read_text(encoding="utf-8"))
            payload["id"] = "tampered"
            resolved_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                HarnessXPolicyEvolutionError,
                "validation failed",
            ):
                validate_live_tool_policy_evolution(output)


if __name__ == "__main__":
    unittest.main()

