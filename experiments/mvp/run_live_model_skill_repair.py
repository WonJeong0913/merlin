"""Run a live requested-GPT-5.6 skill-local repair lifecycle.

The model sees the immutable v1 bundle and target-only verifier feedback.  It
may change only ``scripts/run.py``.  Hidden and library-regression cases are
executed later in separate macOS-confined phases; adoption is copy-on-write.
Raw provider and sandbox artifacts stay under ``--raw-root``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from src.merlin_harness.isolated_candidate_runner import (
    CandidateExecutionCase,
    IsolatedCandidatePhaseResult,
    IsolatedCandidateRunnerError,
    run_quarantined_candidate_phase,
)
from src.merlin_harness.model_candidate_generator import (
    CodexModelCandidateGenerator,
    ModelCandidateGeneratorError,
)
from src.merlin_harness.model_candidate_quarantine import (
    ModelCandidateEnvelope,
    ModelCandidateFile,
    ModelCandidateQuarantineError,
    quarantine_model_candidate,
)
from src.merlin_harness.model_skill_reviser import CodexModelSkillReviser
from src.merlin_harness.models import LifecycleStatus, SkillArtifact, SkillStep
from src.merlin_harness.skill_repair import (
    RepairCase,
    RepairCaseResult,
    RepairDiagnosis,
    RepairEvaluator,
    SkillRepairError,
    run_skill_repair,
    skill_library_snapshot_sha256,
)
from src.merlin_harness.verifier_trust import (
    VerifierTrustLevel,
    VerifierTrustProfile,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_QUARANTINE = (
    REPO_ROOT
    / "experiments/mvp/results/model_authored_skill_live_v1/quarantine"
)
DEFAULT_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
MODEL_ID = "gpt-5.6-terra"
EFFORT = "high"
SKILL_ID = "extract-todo-items"


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _expected(*items: str) -> str:
    return json.dumps(
        {"items": list(items)}, ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"


@dataclass(frozen=True, slots=True)
class FrozenRepairCase:
    case: RepairCase
    input_files: tuple[tuple[str, str], ...]
    expected_files: tuple[tuple[str, str], ...]


def frozen_cases() -> tuple[FrozenRepairCase, ...]:
    return (
        FrozenRepairCase(
            RepairCase("target-marker-spacing", "target", "exact-todo-spacing-v1"),
            (("backlog.todo", "TODO : fix parser\n  TODO\t: write tests\nDONE: ignore\n"),),
            (("todo-items.json", _expected("fix parser", "write tests")),),
        ),
        FrozenRepairCase(
            RepairCase("held-out-unicode-spacing", "held_out", "exact-todo-hidden-v1"),
            (("backlog.todo", "\tTODO   : 회귀 테스트\n메모: 제외\n TODO : 문서 갱신\n"),),
            (("todo-items.json", _expected("회귀 테스트", "문서 갱신")),),
        ),
        FrozenRepairCase(
            RepairCase("library-regression-original", "library_regression", "exact-todo-regression-v1"),
            (("backlog.todo", "TODO: fix login\nnote: ignore\n  TODO: update docs\n"),),
            (("todo-items.json", _expected("fix login", "update docs")),),
        ),
    )


def _baseline_manifest_sha256() -> str:
    try:
        payload = json.loads(
            (BASELINE_QUARANTINE / "quarantine_manifest.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SkillRepairError("baseline model-authored quarantine is unreadable") from exc
    digest = payload.get("manifest_sha256") if isinstance(payload, dict) else None
    if not isinstance(digest, str) or len(digest) != 64:
        raise SkillRepairError("baseline quarantine manifest SHA-256 is invalid")
    return digest


def _baseline_skill(manifest_sha256: str) -> SkillArtifact:
    return SkillArtifact(
        id=SKILL_ID,
        name="Extract TODO Items",
        description=(
            "Extract TODO-prefixed items from backlog.todo into todo-items.json. "
            "Use when a task explicitly requests TODO extraction into that JSON artifact."
        ),
        trigger="Use when backlog.todo TODO entries must become todo-items.json.",
        do_not_use_when=[
            "Do not use for general line counting or summaries.",
            "Do not use for arbitrary report or file creation.",
        ],
        steps=[
            SkillStep(
                id="model-authored-extract-todo",
                description="Run the quarantined model-authored extractor in the task workspace.",
                kind="script",
                inputs=["backlog.todo"],
                outputs=["todo-items.json"],
                script_path="scripts/run.py",
            )
        ],
        validators=["exact_file:todo-items.json"],
        expected_artifacts=["todo-items.json"],
        failure_modes=["input missing", "invalid UTF-8", "workspace path escape"],
        provenance_trace_ids=["live-gpt56-generation-001"],
        status=LifecycleStatus.ACTIVE,
        version=1,
        metadata={
            "generator_backend": "openai-codex-cli",
            "generator_model": MODEL_ID,
            "generator_effort": EFFORT,
            "quarantine_manifest_sha256": manifest_sha256,
            "portable_format": "agentskills.io",
        },
    )


def _profile(item: FrozenRepairCase) -> VerifierTrustProfile:
    hidden = item.case.split != "target"
    return VerifierTrustProfile(
        verifier_id=item.case.verifier_id,
        level=(
            VerifierTrustLevel.HIDDEN_ORACLE
            if item.case.split == "held_out"
            else VerifierTrustLevel.DETERMINISTIC_BEHAVIORAL
        ),
        deterministic=True,
        requirement_ids=("todo-marker-spacing", "todo-content-order"),
        covered_requirement_ids=("todo-marker-spacing", "todo-content-order"),
        behavioral_assertion_count=2,
        author_independent_from_candidate=True,
        hidden_from_reviser=hidden,
        provenance_sha256=_sha256(
            {
                "case_id": item.case.case_id,
                "split": item.case.split,
                "input_files": item.input_files,
                "expected_files": item.expected_files,
            }
        ),
    )


class IsolatedRepairEvaluator(RepairEvaluator):
    def __init__(
        self,
        *,
        raw_root: Path,
        baseline_manifest_sha256: str,
        reviser: CodexModelSkillReviser,
        case_specs: Sequence[FrozenRepairCase],
        baseline_quarantine_root: Path = BASELINE_QUARANTINE,
        skill_id: str = SKILL_ID,
    ) -> None:
        self.raw_root = raw_root
        self.baseline_manifest_sha256 = baseline_manifest_sha256
        self.baseline_quarantine_root = baseline_quarantine_root
        self.skill_id = skill_id
        self.reviser = reviser
        self.case_specs = {item.case.case_id: item for item in case_specs}
        self.executions: list[IsolatedCandidatePhaseResult] = []
        self._ordinal = 0

    def _binding(self, skill: SkillArtifact) -> tuple[Path, str]:
        if skill.version == 1:
            return self.baseline_quarantine_root, self.baseline_manifest_sha256
        key = f"{skill.id}@v{skill.version}"
        binding = self.reviser.bindings.get(key)
        if binding is None:
            raise SkillRepairError(f"no immutable bundle binding for {key}")
        return binding.quarantine_root, binding.quarantine_manifest_sha256

    def _evaluate(
        self, skill: SkillArtifact, cases: tuple[RepairCase, ...]
    ) -> tuple[RepairCaseResult, ...]:
        if not cases:
            raise SkillRepairError("isolated repair evaluator requires cases")
        phase = cases[0].split
        if any(case.split != phase for case in cases):
            raise SkillRepairError("isolated repair evaluator cannot mix phases")
        quarantine_root, manifest_sha256 = self._binding(skill)
        self._ordinal += 1
        execution_cases = tuple(
            CandidateExecutionCase(
                case_id=case.case_id,
                split=case.split,
                input_files=self.case_specs[case.case_id].input_files,
                expected_files=self.case_specs[case.case_id].expected_files,
            )
            for case in cases
        )
        execution = run_quarantined_candidate_phase(
            quarantine_root=quarantine_root,
            expected_manifest_sha256=manifest_sha256,
            phase=phase,
            cases=execution_cases,
            output_root=(
                self.raw_root
                / "execution"
                / f"{self._ordinal:02d}-v{skill.version}-{phase}"
            ),
        )
        self.executions.append(execution)
        by_id = {item.case_id: item for item in execution.cases}
        return tuple(
            RepairCaseResult(
                case_id=case.case_id,
                verifier_id=case.verifier_id,
                passed=by_id[case.case_id].passed,
                score=float(by_id[case.case_id].passed),
                evidence=(
                    "macos-confined exact-file verifier; "
                    f"return_code={by_id[case.case_id].return_code}; "
                    f"workspace_manifest_sha256={by_id[case.case_id].workspace_manifest_sha256}"
                ),
            )
            for case in cases
        )

    def evaluate_skill(
        self, skill: SkillArtifact, cases: tuple[RepairCase, ...]
    ) -> Sequence[RepairCaseResult]:
        return self._evaluate(skill, cases)

    def evaluate_library(
        self, skills: tuple[SkillArtifact, ...], cases: tuple[RepairCase, ...]
    ) -> Sequence[RepairCaseResult]:
        matches = [skill for skill in skills if skill.id == self.skill_id]
        if len(matches) != 1:
            raise SkillRepairError("repair regression library must contain the target once")
        return self._evaluate(matches[0], cases)


def _cli_version(executable: Path) -> str:
    process = subprocess.run(
        [str(executable), "--version"],
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if process.returncode != 0 or not process.stdout.strip():
        raise ModelCandidateGeneratorError("unable to resolve Codex CLI version")
    return process.stdout.strip()


def run_campaign(
    *,
    raw_root: Path,
    output_root: Path,
    codex_executable: Path = DEFAULT_CODEX,
    model_id: str = MODEL_ID,
    effort: str = EFFORT,
) -> dict[str, object]:
    raw_root = raw_root.expanduser().resolve(strict=False)
    output_root = output_root.expanduser().resolve(strict=False)
    if raw_root.exists() or output_root.exists():
        raise SkillRepairError("raw and safe repair output roots must both be new")
    if raw_root.is_relative_to(REPO_ROOT):
        raise SkillRepairError("raw provider/sandbox repair root must stay outside the repository")
    raw_root.mkdir(parents=True)

    manifest_sha256 = _baseline_manifest_sha256()
    original = _baseline_skill(manifest_sha256)
    library = (original,)
    cases = frozen_cases()
    target_cases = tuple(item.case for item in cases if item.case.split == "target")
    held_out_cases = tuple(item.case for item in cases if item.case.split == "held_out")
    regression_cases = tuple(
        item.case for item in cases if item.case.split == "library_regression"
    )
    generator = CodexModelCandidateGenerator(
        executable=codex_executable,
        cli_version=_cli_version(codex_executable),
        model_id=model_id,
        effort=effort,
        timeout_s=300,
    )
    reviser = CodexModelSkillReviser(
        generator=generator,
        run_root=raw_root / "model-repair",
        original_quarantine_root=BASELINE_QUARANTINE,
        original_manifest_sha256=manifest_sha256,
    )
    evaluator = IsolatedRepairEvaluator(
        raw_root=raw_root,
        baseline_manifest_sha256=manifest_sha256,
        reviser=reviser,
        case_specs=cases,
    )
    diagnosis = RepairDiagnosis(
        skill_id=SKILL_ID,
        failure_kind="skill_local",
        trace_ids=("live-repair-target-failure-001",),
        failed_target_case_ids=tuple(case.case_id for case in target_cases),
        verifier_feedback=(
            "TODO markers may contain horizontal whitespace between the TODO token and colon; preserve item text and order.",
        ),
        library_snapshot_sha256=skill_library_snapshot_sha256(library),
    )
    profiles = {item.case.verifier_id: _profile(item) for item in cases}
    result = run_skill_repair(
        diagnosis=diagnosis,
        library=library,
        target_cases=target_cases,
        held_out_cases=held_out_cases,
        regression_cases=regression_cases,
        evaluator=evaluator,
        reviser=reviser,
        verifier_profiles=profiles,
        max_candidates=1,
    )
    if not reviser.bindings:
        raise SkillRepairError("model repair produced no quarantined bundle binding")
    binding = next(iter(reviser.bindings.values()))
    output_root.mkdir(parents=True)
    # The safe copy is reconstructed from the exact generated envelope retained
    # by the quarantine manifest.  Read it from the generator result is not
    # available through the generic interface, so copy only the already hashed
    # candidate files and rewrite no raw trace.  The evidence report remains the
    # authority for provider claims.
    raw_manifest = json.loads(
        (binding.quarantine_root / "quarantine_manifest.json").read_text(encoding="utf-8")
    )
    candidate_root = binding.quarantine_root / "candidate" / SKILL_ID
    safe_envelope = ModelCandidateEnvelope(
        candidate_skill_id=SKILL_ID,
        generator_backend=raw_manifest["generator_backend"],
        generator_model=raw_manifest["generator_model"],
        generator_effort=raw_manifest["generator_effort"],
        generator_prompt_sha256=raw_manifest["generator_prompt_sha256"],
        generator_response_sha256=raw_manifest["generator_response_sha256"],
        generator_provider_reported_model_ids=tuple(
            raw_manifest["generator_provider_reported_model_ids"]
        ),
        generator_cli_version=raw_manifest["generator_cli_version"],
        generator_raw_trace_sha256=raw_manifest["generator_raw_trace_sha256"],
        generator_thread_id=raw_manifest["generator_thread_id"],
        generator_turn_id=raw_manifest["generator_turn_id"],
        files=tuple(
            ModelCandidateFile(
                record["path"],
                (candidate_root / record["path"]).read_text(encoding="utf-8"),
            )
            for record in raw_manifest["files"]
        ),
    )
    safe_quarantine = quarantine_model_candidate(
        envelope=safe_envelope, output_root=output_root / "quarantine"
    )
    if safe_quarantine.manifest_sha256 != binding.quarantine_manifest_sha256:
        raise SkillRepairError("safe repair quarantine bytes differ from raw binding")

    report: dict[str, object] = {
        "schema_version": 1,
        "campaign_id": "live-gpt56-model-authored-repair-v1",
        "skill_id": SKILL_ID,
        "baseline_version": 1,
        "candidate_version": binding.version,
        "adopted": result.adopted,
        "lifecycle_action": result.lifecycle_action,
        "repair_result": result.to_dict(),
        "model_repair": binding.to_safe_dict(),
        "quarantine": safe_quarantine.to_dict(),
        "isolated_phase_executions": [item.to_dict() for item in evaluator.executions],
        "held_out_contract": {
            "case_count": len(held_out_cases),
            "content_exposed_to_reviser": False,
            "verifier_provenance_sha256": [
                profiles[case.verifier_id].provenance_sha256
                for case in held_out_cases
            ],
        },
        "evidence_boundary": {
            "actual_codex_provider_run": True,
            "requested_model_id": model_id,
            "provider_reported_model_ids": list(binding.provider_reported_model_ids),
            "model_evidence_level": binding.model_evidence_level,
            "model_authored_repair": True,
            "provider_tool_execution_during_repair": False,
            "target_only_feedback_to_reviser": True,
            "candidate_hidden_held_out_executed_after_candidate_authorship": True,
            "copy_on_write_promoted": result.adopted,
            "live_library_mutated": False,
            "provider_native_skill_invocation": False,
            "full_benchmark_claim": False,
        },
    }
    (output_root / "model_authored_skill_repair_evidence.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_root / "resolved_library.json").write_text(
        json.dumps(
            [skill.to_dict() for skill in result.resolved_library],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--codex", type=Path, default=DEFAULT_CODEX)
    parser.add_argument("--model", default=MODEL_ID)
    parser.add_argument("--effort", default=EFFORT)
    args = parser.parse_args(argv)
    try:
        result = run_campaign(
            raw_root=args.raw_root,
            output_root=args.output,
            codex_executable=args.codex,
            model_id=args.model,
            effort=args.effort,
        )
    except (
        IsolatedCandidateRunnerError,
        ModelCandidateGeneratorError,
        ModelCandidateQuarantineError,
        SkillRepairError,
        OSError,
        ValueError,
    ) as exc:
        parser.error(str(exc))
    repair = result["repair_result"]
    print("Merlin live model-authored skill repair")
    print(f"skill={result['skill_id']}")
    print(f"adopted={str(result['adopted']).lower()}")
    print(f"version={result['baseline_version']}->{result['candidate_version']}")
    print(f"gates={sum(bool(gate['passed']) for gate in repair['gates'])}/{len(repair['gates'])}")
    print(f"safe_evidence={args.output.expanduser().resolve()}")
    return 0 if result["adopted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
