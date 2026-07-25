from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from experiments.mvp.run_managed_library_loop import (
    ManagedLibraryLoopValidationError,
    run_managed_library_loop,
    validate_managed_library_loop,
)


class ManagedLibraryLoopTests(unittest.TestCase):
    def test_model_free_loop_binds_existing_governance_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "loop"
            report = run_managed_library_loop(output)
            persisted = json.loads(
                (output / "managed_library_loop.json").read_text(encoding="utf-8")
            )
            component_paths = [
                output / item["path"] for item in report["components"]
            ]

            self.assertEqual(report, persisted)
            self.assertTrue(report["ready_for_account_auth_canary"])
            self.assertEqual(report["execution"]["provider_calls_performed"], 0)
            self.assertEqual(report["execution"]["api_calls_performed"], 0)
            self.assertFalse(report["execution"]["credentials_read"])
            self.assertEqual(
                report["headline_controlled_result"]["management_arms"],
                ["M0", "M1", "M2-H", "M2-K"],
            )
            self.assertEqual(
                report["headline_controlled_result"]["harnessx_hook_count"],
                8,
            )
            self.assertEqual(
                report["account_resource_ledger"]["decision"][
                    "authorized_provider_turns"
                ],
                0,
            )
            self.assertEqual(
                report["account_auth_canary_contract"]["status"],
                "planned_not_executed",
            )
            self.assertTrue(all(path.is_file() for path in component_paths))
            for item, path in zip(report["components"], component_paths, strict=True):
                self.assertEqual(
                    item["sha256"],
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )

            unhashed = dict(report)
            declared = unhashed.pop("report_sha256")
            canonical = json.dumps(
                unhashed,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            self.assertEqual(declared, hashlib.sha256(canonical).hexdigest())
            validation = validate_managed_library_loop(output)
            self.assertTrue(validation["valid"])
            self.assertEqual(validation["component_count"], 3)
            self.assertEqual(validation["report_sha256"], declared)

    def test_output_is_new_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "existing"
            output.mkdir()
            with self.assertRaises(FileExistsError):
                run_managed_library_loop(output)

    def test_validator_rejects_component_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "loop"
            report = run_managed_library_loop(output)
            component = output / report["components"][0]["path"]
            component.write_bytes(component.read_bytes() + b"\n")

            with self.assertRaisesRegex(
                ManagedLibraryLoopValidationError,
                "component byte count mismatch",
            ):
                validate_managed_library_loop(output)

    def test_validator_rejects_report_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "loop"
            run_managed_library_loop(output)
            report_path = output / "managed_library_loop.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["ready_for_account_auth_canary"] = False
            report_path.write_text(json.dumps(report), encoding="utf-8")

            with self.assertRaisesRegex(
                ManagedLibraryLoopValidationError,
                "report_sha256 mismatch",
            ):
                validate_managed_library_loop(output)

    def test_validator_rejects_component_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "loop"
            run_managed_library_loop(output)
            report_path = output / "managed_library_loop.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["components"][0]["path"] = "../outside.json"
            unhashed = dict(report)
            unhashed.pop("report_sha256")
            report["report_sha256"] = hashlib.sha256(
                json.dumps(
                    unhashed,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            report_path.write_text(json.dumps(report), encoding="utf-8")

            with self.assertRaisesRegex(
                ManagedLibraryLoopValidationError,
                "path escapes output directory",
            ):
                validate_managed_library_loop(output)


if __name__ == "__main__":
    unittest.main()
