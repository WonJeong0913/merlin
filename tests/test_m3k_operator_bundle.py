from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.skillsbench.bind_m3k_proposal_manifest import (
    REQUIRED_CAPABILITY_CHECKS,
    bind_manifest,
)
from experiments.skillsbench.create_m3k_evaluation_manifest import build_manifest
from experiments.skillsbench.m3k_policy_proposal import (
    CANDIDATE_ID,
    EVIDENCE_SOURCE_PATH,
    PARENT_ID,
    build_canonical_bundle,
)
from experiments.skillsbench.create_m3k_pilot_manifest import (
    M3KPilotManifestError,
    build_pilot_manifest,
    validate_pilot_manifest,
)
from experiments.skillsbench.m3k_external_evidence import ATTESTATION_KEYS, RUNTIME_AUDIT_KEYS
from experiments.skillsbench.materialize_m3k_external_cell import (
    M3KMaterializationError,
    materialize_m3k_external_cell,
    validate_materialized_m3k_cell,
)
from experiments.skillsbench.prepare_m3k_pilot_operator_bundle import (
    M3KPilotOperatorBundleError,
    prepare_m3k_pilot_operator_bundle,
    validate_m3k_pilot_operator_bundle,
)
from src.merlin_harness.management import content_sha256


SPLIT = Path("experiments/skillsbench/split-manifest.json")
SCALE = Path("experiments/skillsbench/library-scale-manifest.json")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _capability() -> dict:
    return {
        "schema_version": 1,
        "diagnostic": "codex_mcp_capability",
        "readiness": {
            "checks": {name: True for name in REQUIRED_CAPABILITY_CHECKS},
            "strict_benchmark_bridge_eligible": True,
            "six_cell_execution_allowed": True,
            "this_probe_is_model_execution": False,
            "this_probe_is_benchmark_result": False,
            "handshake_only_is_benchmark_evidence": False,
        },
        "direct_mcp_server": {
            "tool_names": ["exec"],
            "tool_count": 1,
            "boundary_override_arguments_exposed": False,
        },
        "container_runtime": {
            "container_id_provided": True,
            "container_inspect_passed": True,
        },
    }


def _bundle() -> dict:
    path = Path(EVIDENCE_SOURCE_PATH)
    return build_canonical_bundle(
        json.loads(path.read_text(encoding="utf-8")),
        evidence_file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


class M3KOperatorBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schedule = build_manifest(split_manifest=SPLIT, library_scale_manifest=SCALE)
        cls.library = json.loads(SCALE.read_text(encoding="utf-8"))
        cls.bound = bind_manifest(
            schedule=copy.deepcopy(cls.schedule),
            schedule_file_sha256=_sha("synthetic-operator-schedule"),
            library_scale_manifest=copy.deepcopy(cls.library),
            library_scale_file_sha256=hashlib.sha256(SCALE.read_bytes()).hexdigest(),
            bundle=_bundle(),
            bundle_file_sha256=_sha("synthetic-operator-proposal-file"),
            capability=_capability(),
            capability_file_sha256=_sha("synthetic-operator-capability-file"),
        )

    def _bound_path(self, root: Path) -> Path:
        path = root / "ready-bound-m3k.json"
        path.write_text(
            json.dumps(self.bound, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    def test_parent_and_candidate_materialize_exact_full_library_without_oracle_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path = self._bound_path(root)
            source_pair = [
                item
                for item in self.bound["paired_cells"]
                if item["task_id"] == "3d-scan-calc" and item["trial_index"] == 1
            ]
            self.assertEqual({item["variant_role"] for item in source_pair}, {"parent", "candidate"})
            for scheduled in source_pair:
                role = scheduled["variant_role"]
                output = root / f"bundle-{role}"
                contract = materialize_m3k_external_cell(
                    bound_manifest_path=bound_path,
                    library_scale_manifest_path=SCALE,
                    trajectory_id=scheduled["trajectory_id"],
                    output_root=output,
                )
                self.assertEqual(contract["execution_status"], "not_run")
                self.assertEqual(contract["trajectory"]["library_size"], 209)
                self.assertEqual(len(contract["staged_artifacts"]["variant_records"]), 209)
                self.assertEqual(contract["staged_artifacts"]["presentation_order"], scheduled["library_variant_ids"])
                self.assertFalse(contract["staged_artifacts"]["oracle_copied"])
                self.assertEqual(
                    contract["task_corpus_source"]["regular_blob_count"], 2160
                )
                self.assertTrue(
                    contract["task_corpus_source"]["runtime_admission_must_match"]
                )
                self.assertFalse((output / "task-visible/verifier").exists())
                self.assertFalse((output / "task-visible/oracle").exists())
                self.assertTrue((output / "verifier-hidden/test.sh").is_file())
                variant = json.loads((output / "harness-variant.json").read_text(encoding="utf-8"))
                self.assertEqual(
                    variant["id"], PARENT_ID if role == "parent" else CANDIDATE_ID
                )
                attestation = json.loads((output / "attestation.template.json").read_text(encoding="utf-8"))
                audit = json.loads((output / "runtime-audit.template.json").read_text(encoding="utf-8"))
                self.assertEqual(set(attestation), set(ATTESTATION_KEYS))
                self.assertEqual(set(audit), set(RUNTIME_AUDIT_KEYS))
                self.assertFalse(attestation["actual_invocation_evidence_complete"])
                self.assertFalse(audit["tool_feature_suppression_enforced"])
                reopened = validate_materialized_m3k_cell(
                    output,
                    expected_contract_sha256=contract["execution_contract_sha256"],
                )
                self.assertEqual(reopened, contract)

    def test_pre_execution_revalidator_rejects_staged_byte_and_template_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path = self._bound_path(root)
            scheduled = self.bound["paired_cells"][0]
            output = root / "bundle"
            contract = materialize_m3k_external_cell(
                bound_manifest_path=bound_path,
                library_scale_manifest_path=SCALE,
                trajectory_id=scheduled["trajectory_id"],
                output_root=output,
            )
            first_skill = contract["staged_artifacts"]["presentation_order"][0]
            skill_md = output / "skills" / first_skill / "SKILL.md"
            skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\nDRIFT\n", encoding="utf-8")
            with self.assertRaisesRegex(M3KMaterializationError, "skill bytes drifted"):
                validate_materialized_m3k_cell(output)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path = self._bound_path(root)
            scheduled = self.bound["paired_cells"][0]
            output = root / "bundle"
            materialize_m3k_external_cell(
                bound_manifest_path=bound_path,
                library_scale_manifest_path=SCALE,
                trajectory_id=scheduled["trajectory_id"],
                output_root=output,
            )
            audit_path = output / "runtime-audit.template.json"
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            audit["tool_feature_suppression_enforced"] = True
            audit_path.write_text(json.dumps(audit), encoding="utf-8")
            with self.assertRaisesRegex(M3KMaterializationError, "pre-completed"):
                validate_materialized_m3k_cell(output)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path = self._bound_path(root)
            scheduled = self.bound["paired_cells"][0]
            output = root / "bundle"
            materialize_m3k_external_cell(
                bound_manifest_path=bound_path,
                library_scale_manifest_path=SCALE,
                trajectory_id=scheduled["trajectory_id"],
                output_root=output,
            )
            verifier_file = next(
                member for member in (output / "verifier-hidden").rglob("*") if member.is_file()
            )
            verifier_file.write_bytes(verifier_file.read_bytes() + b"\nDRIFT\n")
            with self.assertRaisesRegex(M3KMaterializationError, "hidden verifier bytes drifted"):
                validate_materialized_m3k_cell(output)

    def test_materializer_is_new_only_and_bound_source_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path = self._bound_path(root)
            scheduled = self.bound["paired_cells"][0]
            existing = root / "existing"
            existing.mkdir()
            with self.assertRaisesRegex(M3KMaterializationError, "must not already exist"):
                materialize_m3k_external_cell(
                    bound_manifest_path=bound_path,
                    library_scale_manifest_path=SCALE,
                    trajectory_id=scheduled["trajectory_id"],
                    output_root=existing,
                )

            drifted = copy.deepcopy(self.bound)
            drifted["paired_cells"][0]["library_order_sha256"] = "0" * 64
            unhashed = dict(drifted)
            unhashed.pop("manifest_sha256", None)
            drifted["manifest_sha256"] = content_sha256(unhashed)
            drifted_path = root / "drifted.json"
            drifted_path.write_text(json.dumps(drifted), encoding="utf-8")
            rejected = root / "rejected"
            with self.assertRaisesRegex(M3KMaterializationError, "order hash drifted"):
                materialize_m3k_external_cell(
                    bound_manifest_path=drifted_path,
                    library_scale_manifest_path=SCALE,
                    trajectory_id=scheduled["trajectory_id"],
                    output_root=rejected,
                )
            self.assertFalse(rejected.exists())

            target = root / "symlink-target"
            target.mkdir()
            link = root / "symlink-output"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(M3KMaterializationError, "symlink"):
                materialize_m3k_external_cell(
                    bound_manifest_path=bound_path,
                    library_scale_manifest_path=SCALE,
                    trajectory_id=scheduled["trajectory_id"],
                    output_root=link,
                )

    def test_six_trajectory_pilot_is_exact_and_cannot_claim_full87_or_promotion(self) -> None:
        pilot = build_pilot_manifest(
            bound_manifest=copy.deepcopy(self.bound),
            bound_manifest_file_sha256=_sha("synthetic-ready-bound-file"),
            task_id="3d-scan-calc",
        )
        self.assertEqual(pilot["scope"], "pilot_only")
        self.assertEqual(pilot["expected_trajectories"], 6)
        self.assertEqual({item["trial_index"] for item in pilot["trajectories"]}, {1, 2, 3})
        self.assertEqual({item["variant_role"] for item in pilot["trajectories"]}, {"parent", "candidate"})
        self.assertFalse(pilot["execution_gate"]["promotion_decision_allowed"])
        self.assertFalse(pilot["claim_boundary"]["pilot_can_claim_full87"])
        validate_pilot_manifest(pilot, bound_manifest=self.bound)

        tampered = copy.deepcopy(pilot)
        tampered["scope"] = "full87"
        unhashed = dict(tampered)
        unhashed.pop("pilot_manifest_sha256", None)
        tampered["pilot_manifest_sha256"] = content_sha256(unhashed)
        with self.assertRaisesRegex(M3KPilotManifestError, "denominator/scope drifted"):
            validate_pilot_manifest(tampered, bound_manifest=self.bound)

    def test_six_cell_operator_bundle_is_atomic_reopenable_and_tamper_evident(self) -> None:
        def fake_materialize(**kwargs: object) -> dict:
            output = Path(kwargs["output_root"])
            trajectory_id = str(kwargs["trajectory_id"])
            output.mkdir()
            contract = {
                "trajectory": {"trajectory_id": trajectory_id},
                "execution_contract_sha256": _sha(trajectory_id),
            }
            (output / "execution-contract.json").write_text(
                json.dumps(contract, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return contract

        def fake_validate(
            bundle_root: Path,
            *,
            expected_contract_sha256: str | None = None,
        ) -> dict:
            contract = json.loads(
                (bundle_root / "execution-contract.json").read_text(encoding="utf-8")
            )
            if contract.get("execution_contract_sha256") != expected_contract_sha256:
                raise M3KMaterializationError("execution contract identity differs")
            return contract

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path = self._bound_path(root)
            pilot = build_pilot_manifest(
                bound_manifest=copy.deepcopy(self.bound),
                bound_manifest_file_sha256=hashlib.sha256(bound_path.read_bytes()).hexdigest(),
                task_id="3d-scan-calc",
            )
            pilot_path = root / "pilot.json"
            pilot_path.write_text(
                json.dumps(pilot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            bundle_root = root / "operator-bundle"
            with patch(
                "experiments.skillsbench.prepare_m3k_pilot_operator_bundle."
                "materialize_m3k_external_cell",
                side_effect=fake_materialize,
            ), patch(
                "experiments.skillsbench.prepare_m3k_pilot_operator_bundle."
                "validate_materialized_m3k_cell",
                side_effect=fake_validate,
            ):
                manifest = prepare_m3k_pilot_operator_bundle(
                    bound_manifest_path=bound_path,
                    pilot_manifest_path=pilot_path,
                    library_scale_manifest_path=SCALE,
                    output_root=bundle_root,
                )
                self.assertEqual(manifest["expected_trajectories"], 6)
                self.assertEqual(len(manifest["cells"]), 6)
                self.assertEqual(
                    [entry["trajectory_id"] for entry in manifest["cells"]],
                    [entry["trajectory_id"] for entry in pilot["trajectories"]],
                )
                with self.assertRaisesRegex(M3KPilotOperatorBundleError, "new-only"):
                    prepare_m3k_pilot_operator_bundle(
                        bound_manifest_path=bound_path,
                        pilot_manifest_path=pilot_path,
                        library_scale_manifest_path=SCALE,
                        output_root=bundle_root,
                    )

                first_cell = bundle_root / manifest["cells"][0]["cell_pointer"]
                contract_path = first_cell / "execution-contract.json"
                contract_path.write_text(
                    contract_path.read_text(encoding="utf-8") + " ",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    M3KPilotOperatorBundleError,
                    "contract file bytes drifted",
                ):
                    validate_m3k_pilot_operator_bundle(
                        bundle_root=bundle_root,
                        bound_manifest_path=bound_path,
                        pilot_manifest_path=pilot_path,
                        library_scale_manifest_path=SCALE,
                    )


if __name__ == "__main__":
    unittest.main()
