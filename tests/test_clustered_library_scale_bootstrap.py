from __future__ import annotations

import unittest

from experiments.skillsbench.clustered_library_scale_bootstrap import (
    ClusteredBootstrapError,
    clustered_paired_bootstrap_cis,
)


class ClusteredLibraryScaleBootstrapTests(unittest.TestCase):
    def test_two_stage_bootstrap_is_deterministic_and_preserves_pairs(self) -> None:
        clusters = {
            "task-a": [("task-a", 1, 1, 0), ("task-a", 2, 0, 0), ("task-a", 3, 1, 1)],
            "task-b": [("task-b", 1, 0, 0), ("task-b", 2, 1, 0), ("task-b", 3, 1, 1)],
        }

        def statistics(rows):
            self.assertTrue(
                all(oracle >= library for _task, _trial, oracle, library in rows)
            )
            return {
                "oracle_pass_rate": sum(row[2] for row in rows) / len(rows),
                "observed_drop": sum(row[2] - row[3] for row in rows) / len(rows),
            }

        result = clustered_paired_bootstrap_cis(
            clusters,
            statistics,
            iterations=200,
            seed=17,
        )
        again = clustered_paired_bootstrap_cis(
            clusters,
            statistics,
            iterations=200,
            seed=17,
        )

        self.assertEqual(result, again)
        self.assertEqual(result["cluster_count"], 2)
        self.assertEqual(result["trajectory_count"], 6)
        self.assertEqual(
            result["resampling_units"]["stage_2"],
            "paired_trial_trajectory_within_task",
        )
        drop = result["intervals"]["observed_drop"]
        self.assertAlmostEqual(drop["estimate"], 1 / 3)
        self.assertLessEqual(drop["low"], drop["estimate"])
        self.assertGreaterEqual(drop["high"], drop["estimate"])

    def test_invalid_clusters_and_nonfinite_statistics_fail_closed(self) -> None:
        with self.assertRaisesRegex(ClusteredBootstrapError, "clusters must not be empty"):
            clustered_paired_bootstrap_cis({}, lambda _rows: {"x": 0.0})
        with self.assertRaisesRegex(ClusteredBootstrapError, "at least one trajectory"):
            clustered_paired_bootstrap_cis(
                {"task": []},
                lambda _rows: {"x": 0.0},
            )
        with self.assertRaisesRegex(ClusteredBootstrapError, "finite"):
            clustered_paired_bootstrap_cis(
                {"task": [(1, 1)]},
                lambda _rows: {"x": float("nan")},
                iterations=1,
            )


if __name__ == "__main__":
    unittest.main()
