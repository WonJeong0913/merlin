from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.compose_codex_mcp_executor_capability import (
    compose_executor_capability,
    write_executor_capability,
)
from experiments.skillsbench.create_library_scale_manifest import sha256_file
from experiments.skillsbench.create_library_scale_trial1_runtime_contract import (
    LibraryScaleRuntimeContractError,
    validate_library_scale_trial1_runtime_contract,
    write_library_scale_trial1_runtime_contract,
)
from tests.test_codex_mcp_executor_capability import (
    CodexMcpExecutorCapabilityTests,
)


ROOT = Path(__file__).resolve().parents[1]
SB = ROOT / "experiments" / "skillsbench"
PLAN = SB / "library-scale-trial1-plan.json"
SOURCE_PLAN = SB / "library-scale-batch-plan.json"
MANIFEST = SB / "library-scale-manifest.json"
PROVENANCE = SB / "corpus-provenance.json"


class LibraryScaleTrial1RuntimeContractTests(unittest.TestCase):
    def _inputs(self, root: Path) -> tuple[Path, Path]:
        capability_fixture = CodexMcpExecutorCapabilityTests()
        preflight, canary, inspect = capability_fixture._sources(root)
        capability = compose_executor_capability(
            preflight_path=preflight,
            boundary_canary_path=canary,
            container_inspect_path=inspect,
            requested_model_id="gpt-5.6-terra",
            requested_effort="low",
        )
        capability_path = root / "capability.json"
        write_executor_capability(capability_path, capability)
        provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))
        snapshot = {
            "schema_version": 1,
            "snapshot_role": "mac-canonical-merlin-overlay-for-desktop-executor",
            "entry_count": 100,
            "entries_sha256": "a" * 64,
            "external_pinned_corpus": {
                "source": "benchflow-ai/skillsbench",
                "upstream_commit": provenance["upstream_commit"],
                "regular_blob_count": provenance["regular_blob_count"],
                "expected_manifest_sha256": provenance["expected_manifest_sha256"],
                "corpus_provenance_file_sha256": sha256_file(PROVENANCE),
            },
        }
        snapshot_path = root / "snapshot.json"
        snapshot_path.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
        return capability_path, snapshot_path

    def _kwargs(self, capability: Path, snapshot: Path) -> dict:
        return {
            "plan_path": PLAN,
            "source_plan_path": SOURCE_PLAN,
            "manifest_path": MANIFEST,
            "executor_capability_path": capability,
            "source_snapshot_manifest_path": snapshot,
            "corpus_provenance_path": PROVENANCE,
            "model": "gpt-5.6-terra",
            "effort": "high",
            "exposure_budget": 3,
        }

    def test_contract_binds_plan_snapshot_capability_and_metadata_first_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capability, snapshot = self._inputs(root)
            output = root / "runtime.json"
            contract = write_library_scale_trial1_runtime_contract(
                output_path=output, **self._kwargs(capability, snapshot)
            )
            self.assertEqual(contract["plan"]["scheduled_cells"], 435)
            self.assertEqual(
                contract["harness_contract"]["mode"],
                "metadata-first-staged-body-v1",
            )
            self.assertEqual(contract["harness_contract"]["exposure_budget"], 3)
            self.assertTrue(contract["executor_capability"]["eligible"])
            self.assertEqual(
                validate_library_scale_trial1_runtime_contract(
                    contract_path=output, **self._kwargs(capability, snapshot)
                ),
                contract,
            )

    def test_contract_is_new_only_and_capability_drift_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capability, snapshot = self._inputs(root)
            output = root / "runtime.json"
            kwargs = self._kwargs(capability, snapshot)
            write_library_scale_trial1_runtime_contract(output_path=output, **kwargs)
            with self.assertRaisesRegex(LibraryScaleRuntimeContractError, "new-only"):
                write_library_scale_trial1_runtime_contract(output_path=output, **kwargs)
            changed = json.loads(capability.read_text(encoding="utf-8"))
            changed["readiness"]["one_cell_execution_allowed"] = False
            capability.write_text(json.dumps(changed) + "\n", encoding="utf-8")
            with self.assertRaises(LibraryScaleRuntimeContractError):
                validate_library_scale_trial1_runtime_contract(
                    contract_path=output, **kwargs
                )


if __name__ == "__main__":
    unittest.main()
