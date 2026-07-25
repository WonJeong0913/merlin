from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.run_m3k_policy_evaluation_demo import run_demo


class M3KPolicyEvaluationDemoTests(unittest.TestCase):
    def test_real_harness_runtime_promotes_and_binds_full87_not_run_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "m3k-evidence"
            report = run_demo(output)

            self.assertTrue(report["accepted"])
            self.assertFalse(report["rollback_required"])
            self.assertEqual(report["resolution"], "candidate_harness_promoted")
            self.assertEqual(len(report["checks"]), 10)
            self.assertTrue(all(item["passed"] for item in report["checks"]))
            self.assertEqual(len(report["parent_trajectories"]), 12)
            self.assertEqual(len(report["candidate_trajectories"]), 12)
            self.assertEqual(report["regression_candidate_task_count"], 2)
            self.assertEqual(
                report["regression_eligible_task_ids"],
                ["regression-a", "regression-b"],
            )

            deltas = {item["split"]: item for item in report["deltas"]}
            self.assertEqual(deltas["held_in"]["pass_rate_delta"], 1.0)
            self.assertEqual(deltas["held_out"]["pass_rate_delta"], 1.0)
            self.assertEqual(deltas["regression"]["pass_rate_delta"], 0.0)
            self.assertEqual(deltas["held_in"]["shadowing_rate_delta"], -1.0)
            self.assertEqual(deltas["held_out"]["shadowing_rate_delta"], -1.0)
            self.assertEqual(deltas["regression"]["shadowing_rate_delta"], 0.0)

            readiness = report["full87_contract_readiness"]
            self.assertEqual(readiness["execution_status"], "not_run")
            self.assertEqual(readiness["task_count"], 87)
            self.assertEqual(readiness["cells_per_variant"], 261)
            self.assertEqual(readiness["paired_variant_cells"], 522)
            self.assertEqual(
                readiness["split_task_counts"],
                {"held_in": 35, "held_out": 30, "regression": 22},
            )
            self.assertTrue((output / "m3k_policy_evaluation.json").is_file())

            with self.assertRaises(FileExistsError):
                run_demo(output)


if __name__ == "__main__":
    unittest.main()
