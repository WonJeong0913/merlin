from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from experiments.mvp.run_harnessx_typed_runtime_demo import (
    run_harnessx_typed_runtime_demo,
)


class HarnessXTypedRuntimeDemoTests(unittest.TestCase):
    def test_demo_covers_hooks_and_enforces_selective_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = run_harnessx_typed_runtime_demo(temporary)
            output = Path(temporary) / "harnessx_typed_runtime.json"
            persisted = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(report, persisted)
        self.assertEqual(report["hook_coverage_count"], 8)
        self.assertEqual(len(report["hook_coverage"]), 8)
        self.assertEqual(
            report["low_risk_reversible_change"]["resolution"],
            "candidate_harness_promoted",
        )
        self.assertEqual(
            report["high_risk_change"]["resolution"],
            "approval_required_parent_retained",
        )
        self.assertFalse(report["frozen_435_execution_included"])

        declared = report["evidence_sha256"]
        unhashed = dict(report)
        del unhashed["evidence_sha256"]
        canonical = json.dumps(
            unhashed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(declared, hashlib.sha256(canonical).hexdigest())


if __name__ == "__main__":
    unittest.main()
