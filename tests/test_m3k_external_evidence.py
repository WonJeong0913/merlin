from __future__ import annotations

import copy
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from experiments.skillsbench.bind_m3k_proposal_manifest import (
    REQUIRED_CAPABILITY_CHECKS,
    bind_manifest,
)
from experiments.skillsbench.create_m3k_evaluation_manifest import build_manifest
from experiments.skillsbench.m3k_policy_proposal import (
    EVIDENCE_SOURCE_PATH,
    build_canonical_bundle,
)
from experiments.skillsbench.m3k_external_evidence import (
    EXECUTION_ARTIFACT_NAMES,
    M3KExternalEvidenceError,
    assemble_m3k_external_evidence,
    execution_pack_pointer_for_sha256,
    record_m3k_external_trajectory,
    record_pointer_for_trajectory,
    requested_model_contract,
    sha256_file,
    validate_m3k_external_evidence_subset,
)
from experiments.skillsbench.probe_codex_mcp_capability import (
    NATIVE_TOOL_FEATURES_TO_DISABLE,
)


SPLIT = Path("experiments/skillsbench/split-manifest.json")
SCALE = Path("experiments/skillsbench/library-scale-manifest.json")
LOCAL_CODEX_CAPABILITY = Path(
    "experiments/skillsbench/results/codex-mcp-capability-local-20260719.json"
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _executor_capability() -> dict:
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


def _proposal_bundle() -> dict:
    path = Path(EVIDENCE_SOURCE_PATH)
    return build_canonical_bundle(
        json.loads(path.read_text(encoding="utf-8")),
        evidence_file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


class M3KExternalEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # This makes a schedule fixture only.  No provider/model executor is
        # invoked by this test suite.
        cls.schedule = build_manifest(split_manifest=SPLIT, library_scale_manifest=SCALE)
        cls.library_scale_manifest = json.loads(SCALE.read_text(encoding="utf-8"))
        cls.library_scale_file_sha256 = hashlib.sha256(SCALE.read_bytes()).hexdigest()

    def _write_bound_manifest(self, root: Path) -> tuple[Path, dict]:
        bundle = _proposal_bundle()
        bound = bind_manifest(
            schedule=copy.deepcopy(self.schedule),
            schedule_file_sha256=_sha("synthetic-schedule-file"),
            library_scale_manifest=copy.deepcopy(self.library_scale_manifest),
            library_scale_file_sha256=self.library_scale_file_sha256,
            bundle=bundle,
            bundle_file_sha256=_sha("synthetic-proposal-bundle-file"),
            capability=_executor_capability(),
            capability_file_sha256=hashlib.sha256(b"{}\n").hexdigest(),
        )
        path = root / "bound-m3k.json"
        path.write_text(
            json.dumps(bound, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path, bound

    def test_actual_local_codex_capability_keeps_m3k_execution_blocked(self) -> None:
        capability_bytes = LOCAL_CODEX_CAPABILITY.read_bytes()
        capability = json.loads(capability_bytes)
        bound = bind_manifest(
            schedule=copy.deepcopy(self.schedule),
            schedule_file_sha256=_sha("synthetic-schedule-file"),
            library_scale_manifest=copy.deepcopy(self.library_scale_manifest),
            library_scale_file_sha256=self.library_scale_file_sha256,
            bundle=_proposal_bundle(),
            bundle_file_sha256=_sha("synthetic-proposal-bundle-file"),
            capability=capability,
            capability_file_sha256=hashlib.sha256(capability_bytes).hexdigest(),
        )
        self.assertEqual(bound["status"], "proposal_bound_not_ready")
        self.assertFalse(bound["execution_gate"]["execution_allowed"])
        failures = set(bound["executor_capability"]["failed_required_checks"])
        self.assertIn("native_tool_allowlist_available", failures)
        self.assertIn("native_tool_denylist_available", failures)
        self.assertIn("strict_mcp_config_available", failures)
        self.assertFalse(bound["claim_boundary"]["model_execution"])

    @staticmethod
    def _attestation(
        *,
        bound: dict,
        bound_path: Path,
        scheduled: dict,
        passed: bool,
        invocation_complete: bool,
    ) -> dict:
        binding = bound["proposal_binding"]
        role = scheduled["variant_role"]
        prefix = "parent" if role == "parent" else "candidate"
        return {
            "schema_version": 1,
            "bound_manifest_sha256": bound["manifest_sha256"],
            "bound_manifest_file_sha256": sha256_file(bound_path),
            "trajectory_id": scheduled["trajectory_id"],
            "pair_id": scheduled["pair_id"],
            "cell_id": scheduled["cell_id"],
            "variant_role": role,
            "variant_id": binding[f"{prefix}_variant_id"],
            "variant_sha256": binding[f"{prefix}_variant_sha256"],
            "proposal_id": binding["proposal_id"],
            "proposal_sha256": binding["proposal_sha256"],
            "evaluation_contract_sha256": bound["evaluation_contract_sha256"],
            "task_id": scheduled["task_id"],
            "split": scheduled["split"],
            "trial_index": scheduled["trial_index"],
            "verifier_id": scheduled["verifier_id"],
            "task_instruction_sha256": scheduled["task_instruction_sha256"],
            "library_arm_id": scheduled["library_arm_id"],
            "library_size": scheduled["library_size"],
            "library_snapshot_sha256": scheduled["library_snapshot_sha256"],
            "library_order_sha256": scheduled["library_order_sha256"],
            "actual_invocation_evidence_complete": invocation_complete,
            "invoked_skill_ids": ["oracle"] if invocation_complete else [],
            "oracle_skill_ids": ["oracle"],
            "verifier_passed": passed,
            "verifier_score": 1.0 if passed else 0.0,
            "cost": 1.0,
        }

    @staticmethod
    def _runtime_audit(bound: dict, trajectory_id: str, raw_sha256: str) -> dict:
        return {
            "schema_version": 2,
            "bound_manifest_sha256": bound["manifest_sha256"],
            "executor_capability_file_sha256": bound["executor_capability"]["file_sha256"],
            "trajectory_id": trajectory_id,
            "raw_provider_trace_sha256": raw_sha256,
            "requested_model_contract": requested_model_contract(bound),
            "tool_feature_suppression_enforced": True,
            "feature_suppression_sha256": _sha("synthetic-feature-suppression"),
            "strict_config_enforced": True,
            "user_config_suppressed": True,
            "rules_suppressed": True,
            "per_run_mcp_isolation": True,
            "host_native_tool_event_observed": False,
            "exec_tool_call_observed": True,
            "inspected_container_id": "synthetic-isolated-container",
            "inspected_container_sha256": _sha("synthetic-container-inspect"),
            "inspected_image_id": "synthetic-pinned-image",
            "inspected_image_sha256": _sha("synthetic-image-inspect"),
            "run_config_sha256": _sha("synthetic-run-config"),
            "audit_event_sha256": _sha(f"synthetic-audit-event:{trajectory_id}"),
        }

    def _execution_input(
        self,
        root: Path,
        *,
        bound: dict,
        attestation: dict,
        raw_payload: dict | None = None,
    ) -> tuple[Path, Path, Path, Path]:
        artifact_root = root
        artifact_root.mkdir(parents=True, exist_ok=True)
        raw_path = artifact_root / "codex.jsonl"
        raw_path.write_text(
            json.dumps(
                raw_payload
                or {
                    "synthetic_fixture": True,
                    "trajectory_id": attestation["trajectory_id"],
                    "provider": "not-a-live-provider",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        invoked = list(attestation["invoked_skill_ids"])
        mcp_event = {"method": "tools/call", "tool_name": "exec"}
        if invoked:
            mcp_event["skill_id"] = invoked[0]
        verifier_stdout = b"synthetic verifier stdout\n"
        verifier_stderr = b""
        initial: dict[str, bytes] = {
            "allowed-skill-ids.json": b'["oracle"]\n',
            "codex.jsonl": raw_path.read_bytes(),
            "codex.stderr.txt": b"",
            "container-inspect.json": b"[]\n",
            "desktop-admission-start.json": b"{}\n",
            "docker-build.stderr.txt": b"",
            "docker-build.stdout.txt": b"synthetic build\n",
            "executor-capability.json": b"{}\n",
            "feature-suppression.json": (
                json.dumps(
                    {
                        "provided": True,
                        "requested_disabled_features": list(
                            NATIVE_TOOL_FEATURES_TO_DISABLE
                        ),
                        "observed_disabled_features": list(
                            NATIVE_TOOL_FEATURES_TO_DISABLE
                        ),
                        "all_requested_features_disabled": True,
                        "features_list_sha256": _sha("synthetic-feature-list"),
                        "feature_listing_is_runtime_tool_inventory_proof": False,
                        "feature_listing_is_model_execution": False,
                    },
                    sort_keys=True,
                )
                + "\n"
            ).encode(),
            "image-inspect.json": b"[]\n",
            "mcp-audit.jsonl": (json.dumps(mcp_event, sort_keys=True) + "\n").encode(),
            "provisioning.json": b"{}\n",
            "source-snapshot-manifest.json": b"{}\n",
            "verifier.stderr.txt": verifier_stderr,
            "verifier.stdout.txt": verifier_stdout,
        }
        verifier = {
            "schema_version": 1,
            "exit_code": 0 if attestation["verifier_passed"] else 1,
            "reward": attestation["verifier_score"],
            "passed": attestation["verifier_passed"],
            "stdout_sha256": hashlib.sha256(verifier_stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(verifier_stderr).hexdigest(),
            "hidden_verifier_tree_sha256": _sha("synthetic-hidden-verifier"),
        }
        initial["verifier-result.json"] = (
            json.dumps(verifier, sort_keys=True) + "\n"
        ).encode()
        run_config = {
            "schema_version": 1,
            "trajectory_id": attestation["trajectory_id"],
            "executor_capability_file_sha256": hashlib.sha256(
                initial["executor-capability.json"]
            ).hexdigest(),
            "container_id": "synthetic-isolated-container",
            "image_id": "synthetic-pinned-image",
            "desktop_admission": {
                "admission_start_sha256": hashlib.sha256(
                    initial["desktop-admission-start.json"]
                ).hexdigest(),
                "source_snapshot_manifest_sha256": hashlib.sha256(
                    initial["source-snapshot-manifest.json"]
                ).hexdigest(),
            },
        }
        initial["run-config.json"] = (json.dumps(run_config, sort_keys=True) + "\n").encode()
        self.assertEqual(set(initial), set(EXECUTION_ARTIFACT_NAMES))
        for name, raw in initial.items():
            (artifact_root / name).write_bytes(raw)
        artifact_hashes = {
            name: hashlib.sha256(initial[name]).hexdigest()
            for name in EXECUTION_ARTIFACT_NAMES
        }
        event = {
            "schema_version": 1,
            "trajectory_id": attestation["trajectory_id"],
            "raw_artifact_hashes": artifact_hashes,
            "mcp_exec_call_count": 1,
            "invoked_skill_ids": invoked,
            "provider_reported_model_ids": [],
            "forbidden_native_item_types": [],
        }
        event_path = artifact_root / "execution-event.json"
        event_path.write_text(json.dumps(event, sort_keys=True) + "\n", encoding="utf-8")
        audit = self._runtime_audit(
            bound,
            attestation["trajectory_id"],
            artifact_hashes["codex.jsonl"],
        )
        audit.update(
            {
                "executor_capability_file_sha256": artifact_hashes[
                    "executor-capability.json"
                ],
                "feature_suppression_sha256": artifact_hashes[
                    "feature-suppression.json"
                ],
                "inspected_container_sha256": artifact_hashes["container-inspect.json"],
                "inspected_image_sha256": artifact_hashes["image-inspect.json"],
                "run_config_sha256": artifact_hashes["run-config.json"],
                "audit_event_sha256": sha256_file(event_path),
            }
        )
        audit_path = artifact_root / "runtime-audit.json"
        audit_path.write_text(json.dumps(audit, sort_keys=True) + "\n", encoding="utf-8")
        return raw_path, audit_path, event_path, artifact_root

    def _record_fixture(
        self,
        root: Path,
        *,
        incomplete: bool = False,
        rollback: bool = False,
        limit: int | None = None,
    ) -> tuple[Path, dict, Path]:
        bound_path, bound = self._write_bound_manifest(root)
        incoming = root / "synthetic-external-input"
        evidence_root = root / "sealed-evidence"
        cells = bound["paired_cells"] if limit is None else bound["paired_cells"][:limit]
        incomplete_trajectory_id = next(
            (
                item["trajectory_id"]
                for item in cells
                if item["variant_role"] == "candidate"
            ),
            None,
        )
        for index, scheduled in enumerate(cells):
            role = scheduled["variant_role"]
            split = scheduled["split"]
            if role == "parent":
                passed = split == "regression" or (rollback and split == "held_out")
            else:
                passed = not (rollback and split == "held_out")
            invocation_complete = not (
                incomplete and scheduled["trajectory_id"] == incomplete_trajectory_id
            )
            attestation = self._attestation(
                bound=bound,
                bound_path=bound_path,
                scheduled=scheduled,
                passed=passed,
                invocation_complete=invocation_complete,
            )
            artifact_root = incoming / f"{index}-pack"
            attestation_path = incoming / f"{index}.attestation.json"
            raw_path, audit_path, event_path, artifact_root = self._execution_input(
                artifact_root,
                bound=bound,
                attestation=attestation,
            )
            attestation_path.parent.mkdir(parents=True, exist_ok=True)
            attestation_path.write_text(
                json.dumps(attestation, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            record_m3k_external_trajectory(
                bound_manifest_path=bound_path,
                attestation_path=attestation_path,
                raw_provider_trace_path=raw_path,
                runtime_audit_path=audit_path,
                execution_event_path=event_path,
                raw_artifact_root=artifact_root,
                evidence_root=evidence_root,
            )
        return bound_path, bound, evidence_root

    def test_complete_synthetic_522_fixture_replays_without_live_result_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path, bound, evidence_root = self._record_fixture(root)
            output_root = root / "portable-report"
            report_path = assemble_m3k_external_evidence(
                bound_manifest_path=bound_path,
                evidence_root=evidence_root,
                output_root=output_root,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["coverage"]["expected_trajectories"], 522)
            self.assertEqual(report["coverage"]["recorded_trajectories"], 522)
            self.assertTrue(report["coverage"]["complete"])
            self.assertTrue(report["promotion_report"]["accepted"])
            self.assertEqual(
                report["promotion_report"]["resolution"],
                "candidate_harness_promoted",
            )
            self.assertFalse(report["claim_boundary"]["assembly_is_live_model_execution"])
            self.assertFalse(report["claim_boundary"]["full87_result_claimed_by_assembly"])
            self.assertEqual(bound["summary"]["expected_trajectories"], 522)

    def test_partial_coverage_and_bad_isolation_attestation_fail_before_assembly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path, bound, evidence_root = self._record_fixture(root, limit=1)
            with self.assertRaisesRegex(M3KExternalEvidenceError, "missing=521"):
                assemble_m3k_external_evidence(
                    bound_manifest_path=bound_path,
                    evidence_root=evidence_root,
                    output_root=root / "partial-output",
                )
            self.assertFalse((root / "partial-output").exists())

            scheduled = bound["paired_cells"][1]
            duplicate = root / "duplicate-attempt"
            duplicate.mkdir()
            valid_attestation_path = duplicate / "valid-attestation.json"
            valid_attestation_path.write_text(
                json.dumps(
                    self._attestation(
                        bound=bound,
                        bound_path=bound_path,
                        scheduled=scheduled,
                        passed=True,
                        invocation_complete=True,
                    )
                ),
                encoding="utf-8",
            )
            first_pack = root / "synthetic-external-input/0-pack"
            first_raw = first_pack / "codex.jsonl"
            first_audit = first_pack / "runtime-audit.json"
            first_event = first_pack / "execution-event.json"
            unique_raw = duplicate / "unique-raw.jsonl"
            unique_raw.write_text("new synthetic raw\n", encoding="utf-8")
            raw_reuse_audit = duplicate / "raw-reuse.audit.json"
            raw_reuse_audit.write_text(
                json.dumps(
                    self._runtime_audit(
                        bound,
                        scheduled["trajectory_id"],
                        sha256_file(first_raw),
                    )
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(M3KExternalEvidenceError, "raw provider trace evidence is already reused"):
                record_m3k_external_trajectory(
                    bound_manifest_path=bound_path,
                    attestation_path=valid_attestation_path,
                    raw_provider_trace_path=first_raw,
                    runtime_audit_path=raw_reuse_audit,
                    execution_event_path=first_event,
                    raw_artifact_root=first_pack,
                    evidence_root=evidence_root,
                )
            with self.assertRaisesRegex(M3KExternalEvidenceError, "runtime audit trajectory_id drifted"):
                record_m3k_external_trajectory(
                    bound_manifest_path=bound_path,
                    attestation_path=valid_attestation_path,
                    raw_provider_trace_path=unique_raw,
                    runtime_audit_path=first_audit,
                    execution_event_path=first_event,
                    raw_artifact_root=first_pack,
                    evidence_root=evidence_root,
                )

            incoming = root / "bad-attestation"
            incoming.mkdir()
            raw_path = incoming / "raw.jsonl"
            audit_path = incoming / "audit.json"
            attestation_path = incoming / "attestation.json"
            raw_path.write_text("synthetic raw\n", encoding="utf-8")
            bad_audit = self._runtime_audit(
                bound,
                scheduled["trajectory_id"],
                sha256_file(raw_path),
            )
            bad_audit["host_native_tool_event_observed"] = True
            audit_path.write_text(json.dumps(bad_audit), encoding="utf-8")
            attestation_path.write_text(
                json.dumps(
                    self._attestation(
                        bound=bound,
                        bound_path=bound_path,
                        scheduled=scheduled,
                        passed=True,
                        invocation_complete=True,
                    )
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                M3KExternalEvidenceError,
                "host_native_tool_event_observed=false",
            ):
                record_m3k_external_trajectory(
                    bound_manifest_path=bound_path,
                    attestation_path=attestation_path,
                    raw_provider_trace_path=raw_path,
                    runtime_audit_path=audit_path,
                    execution_event_path=first_event,
                    raw_artifact_root=first_pack,
                    evidence_root=root / "bad-evidence",
                )
            self.assertFalse((root / "bad-evidence").exists())

    def test_tamper_and_reused_raw_trace_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path, bound, evidence_root = self._record_fixture(root)
            first, second = bound["paired_cells"][:2]
            first_record_path = evidence_root / record_pointer_for_trajectory(first["trajectory_id"])
            original = first_record_path.read_bytes()
            tampered = json.loads(original)
            tampered["library_order_sha256"] = "0" * 64
            first_record_path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(M3KExternalEvidenceError, "library_order_sha256 drifted"):
                assemble_m3k_external_evidence(
                    bound_manifest_path=bound_path,
                    evidence_root=evidence_root,
                    output_root=root / "library-tampered-output",
                )
            self.assertFalse((root / "library-tampered-output").exists())

            first_record_path.write_bytes(original)
            tampered = json.loads(original)
            tampered["task_instruction_sha256"] = "0" * 64
            first_record_path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(M3KExternalEvidenceError, "task_instruction_sha256 drifted"):
                assemble_m3k_external_evidence(
                    bound_manifest_path=bound_path,
                    evidence_root=evidence_root,
                    output_root=root / "tampered-output",
                )
            self.assertFalse((root / "tampered-output").exists())

            first_record_path.write_bytes(original)
            first_record = json.loads(original)
            second_record_path = evidence_root / record_pointer_for_trajectory(second["trajectory_id"])
            second_record = json.loads(second_record_path.read_text(encoding="utf-8"))
            first_raw = evidence_root / first_record["raw_provider_trace_pointer"]
            second_raw = evidence_root / second_record["raw_provider_trace_pointer"]
            second_raw.unlink()
            second_record["raw_provider_trace_sha256"] = sha256_file(first_raw)
            second_record["raw_provider_trace_pointer"] = first_record[
                "raw_provider_trace_pointer"
            ]
            second_record_path.write_text(json.dumps(second_record), encoding="utf-8")
            with self.assertRaisesRegex(M3KExternalEvidenceError, "runtime audit raw provider trace drifted"):
                assemble_m3k_external_evidence(
                    bound_manifest_path=bound_path,
                    evidence_root=evidence_root,
                    output_root=root / "reused-output",
                )
            self.assertFalse((root / "reused-output").exists())

    def test_execution_pack_rehash_cannot_hide_original_artifact_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path, bound, evidence_root = self._record_fixture(root, limit=1)
            scheduled = bound["paired_cells"][0]
            baseline = validate_m3k_external_evidence_subset(
                bound_manifest_path=bound_path,
                evidence_root=evidence_root,
                trajectory_ids=[scheduled["trajectory_id"]],
            )
            self.assertEqual(baseline["unique_execution_pack_count"], 1)
            record_path = evidence_root / record_pointer_for_trajectory(
                scheduled["trajectory_id"]
            )
            record = json.loads(record_path.read_text(encoding="utf-8"))
            old_pack = evidence_root / record["execution_pack_pointer"]
            members: dict[str, bytes] = {}
            with tarfile.open(old_pack, mode="r:") as archive:
                for member in archive.getmembers():
                    handle = archive.extractfile(member)
                    self.assertIsNotNone(handle)
                    members[member.name] = handle.read()  # type: ignore[union-attr]
            members["container-inspect.json"] = b'[{"tampered":true}]\n'
            candidate = root / "rehash-attempt.tar"
            with tarfile.open(candidate, mode="w:") as archive:
                for name in sorted(members):
                    raw = members[name]
                    info = tarfile.TarInfo(name)
                    info.size = len(raw)
                    archive.addfile(info, io.BytesIO(raw))
            new_sha = sha256_file(candidate)
            new_pointer = execution_pack_pointer_for_sha256(new_sha)
            destination = evidence_root / new_pointer
            destination.parent.mkdir(parents=True, exist_ok=True)
            candidate.replace(destination)
            old_pack.unlink()
            record["execution_pack_sha256"] = new_sha
            record["execution_pack_pointer"] = new_pointer
            record_path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(
                M3KExternalEvidenceError,
                "execution artifact hash drifted: container-inspect.json",
            ):
                validate_m3k_external_evidence_subset(
                    bound_manifest_path=bound_path,
                    evidence_root=evidence_root,
                    trajectory_ids=[scheduled["trajectory_id"]],
                )

    def test_runtime_audit_binds_capability_trajectory_and_raw_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path, bound = self._write_bound_manifest(root)
            scheduled = bound["paired_cells"][0]
            incoming = root / "audit-binding"
            attestation = self._attestation(
                bound=bound,
                bound_path=bound_path,
                scheduled=scheduled,
                passed=True,
                invocation_complete=True,
            )
            raw_path, valid_audit_path, event_path, artifact_root = self._execution_input(
                incoming / "pack",
                bound=bound,
                attestation=attestation,
            )
            attestation_path = incoming / "attestation.json"
            attestation_path.write_text(
                json.dumps(attestation),
                encoding="utf-8",
            )
            base_audit = json.loads(valid_audit_path.read_text(encoding="utf-8"))
            cases = (
                (
                    "capability",
                    "executor_capability_file_sha256",
                    "0" * 64,
                    "executor capability drifted",
                ),
                (
                    "trajectory",
                    "trajectory_id",
                    "wrong-trajectory",
                    "trajectory_id drifted",
                ),
                (
                    "raw",
                    "raw_provider_trace_sha256",
                    "0" * 64,
                    "raw provider trace drifted",
                ),
            )
            for label, key, value, error in cases:
                with self.subTest(label=label):
                    audit = copy.deepcopy(base_audit)
                    audit[key] = value
                    audit_path = incoming / f"{label}.audit.json"
                    audit_path.write_text(json.dumps(audit), encoding="utf-8")
                    with self.assertRaisesRegex(M3KExternalEvidenceError, error):
                        record_m3k_external_trajectory(
                            bound_manifest_path=bound_path,
                            attestation_path=attestation_path,
                            raw_provider_trace_path=raw_path,
                            runtime_audit_path=audit_path,
                            execution_event_path=event_path,
                            raw_artifact_root=artifact_root,
                            evidence_root=root / f"{label}-evidence",
                        )
                    self.assertFalse((root / f"{label}-evidence").exists())

    def test_incomplete_invocation_and_held_out_regression_replay_to_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bound_path, _, evidence_root = self._record_fixture(
                root,
                incomplete=True,
                rollback=True,
            )
            report_path = assemble_m3k_external_evidence(
                bound_manifest_path=bound_path,
                evidence_root=evidence_root,
                output_root=root / "rollback-report",
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            replay = report["promotion_report"]
            self.assertFalse(replay["accepted"])
            self.assertTrue(replay["rollback_required"])
            self.assertEqual(replay["resolution"], "candidate_harness_rolled_back")
            failed = {check["name"] for check in replay["checks"] if not check["passed"]}
            self.assertIn("actual_invocation_complete", failed)
            self.assertIn("held_out_non_regression", failed)


if __name__ == "__main__":
    unittest.main()
