from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.mvp.run_personal_workload_account_pilot import (
    PILOT_TASKS,
    _parse_raw_trace,
    _schedule_row,
)


class PersonalWorkloadAccountPilotTests(unittest.TestCase):
    def test_pilot_uses_two_frozen_phase_one_pairs_with_opposite_orders(self) -> None:
        self.assertEqual([task.task_id for task in PILOT_TASKS], ["pw-ke-09", "pw-ke-08"])
        rows = [_schedule_row(task.task_id) for task in PILOT_TASKS]
        self.assertEqual(rows[0]["arm_order"], ["baseline", "managed"])
        self.assertEqual(rows[1]["arm_order"], ["managed", "baseline"])
        self.assertTrue(all(row["repetition"] == 1 for row in rows))

    def test_raw_trace_parser_requires_expected_successful_command(self) -> None:
        command = "python3 verify.py"
        events = (
            {"type": "thread.started", "thread_id": "thread"},
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": f"/bin/zsh -lc '{command}'",
                    "aggregated_output": "OK",
                    "exit_code": 0,
                },
            },
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 50,
                    "output_tokens": 10,
                },
            },
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trace.jsonl"
            path.write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )
            parsed = _parse_raw_trace(path, expected_command=command)
        self.assertTrue(parsed["expected_command_observed"])
        self.assertFalse(parsed["write_like_command_observed"])
        self.assertEqual(parsed["usage"]["input_tokens"], 100)


if __name__ == "__main__":
    unittest.main()
