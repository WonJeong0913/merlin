from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.validate_full87_contract import main


HISTORICAL_CONTAINER_EXEC_SHA256 = (
    "039c3e17e858872df393aed0046f1771e6c6fc3ad2f986f3bb8b67fd84483193"
)


class Full87ValidateOnlyTests(unittest.TestCase):
    def test_validate_only_rejects_historical_frozen_input_drift_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "historical-full87-manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "task_ids": [f"historical-task-{index:02d}" for index in range(87)],
                        "trial_indices": [1, 2, 3],
                        "arms": ["C0", "C1"],
                        "expected_cells": 522,
                        "frozen_inputs": {
                            "experiments/skillsbench/container_exec_mcp.py": (
                                HISTORICAL_CONTAINER_EXEC_SHA256
                            )
                        },
                        "manifest_sha256": "self",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            original_manifest = manifest_path.read_bytes()
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                with self.assertRaisesRegex(
                    ValueError,
                    "frozen input hash mismatch: experiments/skillsbench/container_exec_mcp.py",
                ):
                    main(
                        [
                            "--manifest",
                            str(manifest_path),
                        ]
                    )
            self.assertEqual(list(root.iterdir()), [manifest_path])
            self.assertEqual(manifest_path.read_bytes(), original_manifest)
            self.assertEqual(stdout.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
