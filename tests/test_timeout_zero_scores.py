import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.apply_timeout_zero_scores import (
    SCORE_SOURCE,
    apply_timeout_zero_scores,
)


def timeout_record(arm: str) -> dict:
    return {
        "task_id": "bike-rebalance",
        "condition_id": "claude_sonnet5_high",
        "arm": arm,
        "trial_index": 2,
        "status": "agent_timeout",
        "passed": False,
        "reward": None,
        "notes": [],
        "commands": {
            "agent": {"exit_code": 124, "timed_out": True},
        },
    }


class TimeoutZeroScoresTests(unittest.TestCase):
    def make_run(self, records: list[dict]) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "summary.json").write_text(
            json.dumps({"run_id": "test", "records": records}),
            encoding="utf-8",
        )
        (root / "records.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        return temporary, root

    def test_scores_timeout_records_and_preserves_raw_backups(self) -> None:
        temporary, root = self.make_run([timeout_record("C0"), timeout_record("C1")])
        self.addCleanup(temporary.cleanup)

        result = apply_timeout_zero_scores(
            root,
            task_id="bike-rebalance",
            trial_index=2,
            label="20260712",
        )

        self.assertTrue(result["changed"])
        self.assertEqual(result["corrected_arms"], ["C0", "C1"])
        summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
        jsonl = [
            json.loads(line)
            for line in (root / "records.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        for record in summary["records"] + jsonl:
            self.assertEqual(record["reward"], 0.0)
            self.assertEqual(record["score_source"], SCORE_SOURCE)
            self.assertEqual(record["status"], "agent_timeout")
            self.assertFalse(record["passed"])
        self.assertEqual(len(summary["score_corrections"]), 2)
        for backup in result["raw_backups"]:
            self.assertTrue((root / backup).is_file())

        repeated = apply_timeout_zero_scores(
            root,
            task_id="bike-rebalance",
            trial_index=2,
            label="20260712",
        )
        self.assertFalse(repeated["changed"])
        self.assertEqual(repeated["already_scored"], 2)

    def test_rejects_non_timeout_missing_reward(self) -> None:
        record = timeout_record("C0")
        record["status"] = "agent_failed"
        temporary, root = self.make_run([record])
        self.addCleanup(temporary.cleanup)

        with self.assertRaisesRegex(ValueError, "no eligible"):
            apply_timeout_zero_scores(
                root,
                task_id="bike-rebalance",
                trial_index=2,
                label="20260712",
            )

    def test_rejects_timeout_after_verifier_invocation(self) -> None:
        record = timeout_record("C0")
        record["commands"]["verifier"] = {"exit_code": 1, "timed_out": False}
        temporary, root = self.make_run([record])
        self.addCleanup(temporary.cleanup)

        with self.assertRaisesRegex(ValueError, "no eligible"):
            apply_timeout_zero_scores(
                root,
                task_id="bike-rebalance",
                trial_index=2,
                label="20260712",
            )


if __name__ == "__main__":
    unittest.main()
