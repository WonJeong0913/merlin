from __future__ import annotations

import copy
import json
import unittest

from experiments.skillsbench.run_gpt56_selection_shadowing_pilot import (
    ALLOWED_ITEM_TYPES,
    ARM_SIZES,
    TASK_IDS,
    TRIAL_INDICES,
    SelectionPilotError,
    _item_types,
    _metrics,
    build_plan,
    build_prompt,
    parse_response,
    response_schema,
    validate_report,
)
from experiments.skillsbench.audit_gpt56_selection_shadowing_pilot import (
    SelectionPilotAuditError,
    _require_hash,
)


class Gpt56SelectionShadowingPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = build_plan()

    def test_plan_binds_six_tasks_and_nested_209_skill_pool(self) -> None:
        self.assertEqual(self.plan["task_count"], 6)
        self.assertEqual(self.plan["skill_pool_count"], 209)
        self.assertEqual([item["task_id"] for item in self.plan["tasks"]], list(TASK_IDS))
        self.assertEqual(
            [(item["arm_id"], item["library_size"]) for item in self.plan["arms"]],
            list(ARM_SIZES),
        )
        prior: set[str] = set()
        for arm in self.plan["arms"]:
            current = set(arm["skill_ids"])
            self.assertTrue(prior.issubset(current))
            self.assertEqual(len(current), arm["library_size"])
            prior = current
        self.assertEqual(len(self.plan["plan_sha256"]), 64)

    def test_prompt_contains_catalog_and_tasks_without_oracle_labels(self) -> None:
        prompt = build_prompt(self.plan, arm_id="plus-10", trial_index=1)
        self.assertIn("CATALOG (16 skills", prompt)
        self.assertIn("offer-letter-generator", prompt)
        self.assertNotIn("oracle_skill_id", prompt)
        self.assertNotIn("reference_skill_variants", prompt)
        self.assertLessEqual(len(prompt), 90_000)
        schema = response_schema(self.plan, arm_id="plus-10")
        self.assertEqual(schema["properties"]["selections"]["minItems"], 6)

    def test_strict_response_parser_orders_tasks_and_rejects_unknown_skill(self) -> None:
        arm = self.plan["arms"][0]
        payload = {
            "selections": [
                {"task_id": task["task_id"], "selected_skill_id": task["oracle_skill_id"]}
                for task in reversed(self.plan["tasks"])
            ]
        }
        parsed = parse_response(
            json.dumps(payload),
            task_ids=TASK_IDS,
            allowed_skill_ids=frozenset(arm["skill_ids"]),
        )
        self.assertEqual([item["task_id"] for item in parsed], list(TASK_IDS))
        payload["selections"][0]["selected_skill_id"] = "not-presented"
        with self.assertRaisesRegex(SelectionPilotError, "outside the presented catalog"):
            parse_response(
                json.dumps(payload),
                task_ids=TASK_IDS,
                allowed_skill_ids=frozenset(arm["skill_ids"]),
            )

    def test_provider_item_parser_rejects_tool_execution(self) -> None:
        safe = '\n'.join(
            json.dumps(item)
            for item in (
                {"type": "thread.started", "thread_id": "t"},
                {"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}},
            )
        )
        self.assertEqual(set(_item_types(safe)), {"agent_message"})
        unsafe = safe + '\n' + json.dumps(
            {"type": "item.completed", "item": {"type": "command_execution"}}
        )
        with self.assertRaisesRegex(SelectionPilotError, "provider used a tool"):
            _item_types(unsafe)

    def test_raw_audit_hash_contract_is_strict(self) -> None:
        self.assertEqual(_require_hash("a" * 64, label="fixture"), "a" * 64)
        for invalid in ("a" * 63, "A" * 64, None, 64):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(SelectionPilotAuditError, "is not a SHA-256"):
                    _require_hash(invalid, label="fixture")

    def test_metrics_and_report_validation_detect_tampering(self) -> None:
        cells = []
        for arm in self.plan["arms"]:
            for trial in TRIAL_INDICES:
                decisions = [
                    {
                        "task_id": task["task_id"],
                        "selected_skill_id": task["oracle_skill_id"],
                        "oracle_skill_id": task["oracle_skill_id"],
                        "outcome": "correct",
                    }
                    for task in self.plan["tasks"]
                ]
                cells.append(
                    {
                        "cell_id": f"{arm['arm_id']}__t{trial}",
                        "arm_id": arm["arm_id"],
                        "library_size": arm["library_size"],
                        "trial_index": trial,
                        "provider_tool_execution_observed": False,
                        "item_types": [next(iter(ALLOWED_ITEM_TYPES))],
                        "prompt_sha256": "1" * 64,
                        "schema_sha256": "2" * 64,
                        "raw_trace_sha256": "3" * 64,
                        "response_sha256": "4" * 64,
                        "decisions": decisions,
                    }
                )
        body = {
            "schema_version": 1,
            "pilot_id": self.plan["pilot_id"],
            "plan_sha256": self.plan["plan_sha256"],
            "cells": cells,
            "metrics": _metrics(cells),
        }
        import hashlib

        canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        report = {**body, "report_sha256": hashlib.sha256(canonical.encode()).hexdigest()}
        self.assertTrue(validate_report(report, plan=self.plan)["passed"])
        tampered = copy.deepcopy(report)
        tampered["cells"][0]["decisions"][0]["outcome"] = "wrong_skill"
        with self.assertRaisesRegex(SelectionPilotError, "decision outcome drifted"):
            validate_report(tampered, plan=self.plan)

    def test_compact_package_evidence_matches_retained_report_and_audit(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        compact = json.loads(
            (root / "docs" / "evidence" / "gpt56-selection-shadowing-pilot-v1.json").read_text()
        )
        report = json.loads(
            (root / "experiments" / "skillsbench" / "results" / "gpt56-selection-shadowing-pilot-v1.json").read_text()
        )
        audit = json.loads(
            (root / "experiments" / "skillsbench" / "results" / "gpt56-selection-shadowing-pilot-v1-audit.json").read_text()
        )
        self.assertEqual(compact["provider_turns"], len(report["cells"]))
        self.assertEqual(compact["decision_count"], 48)
        for compact_arm in compact["arms"]:
            retained = report["metrics"]["arms"][compact_arm["arm_id"]]
            self.assertEqual(compact_arm["correct"], retained["counts"]["correct"])
            self.assertEqual(compact_arm["wrong_skill"], retained["counts"]["wrong_skill"])
            self.assertEqual(compact_arm["selection_accuracy"], retained["selection_accuracy"])
        self.assertEqual(compact["audit"]["audit_sha256"], audit["audit_sha256"])
        self.assertEqual(compact["audit"]["checks_passed"], audit["checks_passed"])


if __name__ == "__main__":
    unittest.main()
