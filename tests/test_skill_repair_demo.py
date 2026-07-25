from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.mvp.run_skill_repair_demo import run_skill_repair_demo


class SkillRepairDemoTests(unittest.TestCase):
    def test_real_task_verifier_path_repairs_and_promotes_v2(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "repair"
            report = run_skill_repair_demo(output)

            self.assertTrue(report["adopted"])
            self.assertEqual(report["lifecycle_action"], "adopt")
            self.assertEqual(report["selected_candidate_key"], "line-summary@v2")
            self.assertEqual(report["selected_candidate_version"], 2)
            self.assertFalse(report["baseline_target_results"][0]["passed"])
            self.assertTrue(
                report["candidate_evaluations"][0]["target_results"][0]["passed"]
            )
            self.assertTrue(report["baseline_held_out_results"][0]["passed"])
            self.assertTrue(report["candidate_held_out_results"][0]["passed"])
            self.assertTrue(report["baseline_library_results"][0]["passed"])
            self.assertTrue(report["provisional_library_results"][0]["passed"])
            self.assertTrue(all(item["passed"] for item in report["gates"]))
            self.assertEqual(
                report["experiment"]["claim"],
                "bounded repair lifecycle closure, not open-ended model repair",
            )
            self.assertTrue(
                next(item for item in report["gates"] if item["name"] == "verifier_trust")["passed"]
            )
            exported = json.loads(
                (output / "skill_repair.json").read_text(encoding="utf-8")
            )
            self.assertEqual(exported, report)


if __name__ == "__main__":
    unittest.main()
