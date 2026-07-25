"""Run a second bounded requested-GPT-5.6 model-authored repair family.

This campaign starts from a deterministic, hash-bound portable skill fixture
and asks the model to repair only ``scripts/run.py`` using target feedback.
Hidden and library-regression cases remain undisclosed until authorship is
complete.  Either promotion or copy-on-write rollback is a valid lifecycle
outcome; the script fails only when the evidence chain itself is incomplete.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from experiments.mvp.run_live_model_skill_repair import (
    DEFAULT_CODEX,
    EFFORT,
    MODEL_ID,
    FrozenRepairCase,
    IsolatedRepairEvaluator,
)
from src.merlin_harness.isolated_candidate_runner import (
    IsolatedCandidateRunnerError,
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
    RepairDiagnosis,
    SkillRepairError,
    run_skill_repair,
    skill_library_snapshot_sha256,
)
from src.merlin_harness.verifier_trust import (
    VerifierTrustLevel,
    VerifierTrustProfile,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_ID = "parse-key-value-config"
CAMPAIGN_ID = "live-gpt56-model-authored-repair-family2-v1"

SKILL_MD = """---
name: "parse-key-value-config"
description: "Use when app.env key-value records must become config.json."
---

# Parse Key Value Config

Read `app.env` inside the supplied workspace and write deterministic
`config.json`. Ignore blank lines and comment-only lines. Preserve the first
value for a repeated key and preserve literal `=` or `#` characters in values.
"""

OPENAI_YAML = """interface:
  display_name: Parse Key Value Config
  short_description: Convert app.env into config.json
  default_prompt: Use $parse-key-value-config for an explicit app.env conversion task.
"""

SCRIPT_V1 = """import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--workspace", required=True)
args = parser.parse_args()
root = Path(args.workspace).resolve()
source = root / "app.env"
target = root / "config.json"
values = {}
for raw_line in source.read_text(encoding="utf-8").splitlines():
    if not raw_line or raw_line.startswith("#"):
        continue
    key, separator, value = raw_line.partition("=")
    if separator and key not in values:
        values[key] = value
target.write_text(
    json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True) + "\\n",
    encoding="utf-8",
)
"""

BASELINE_FILES = {
    "SKILL.md": SKILL_MD,
    "agents/openai.yaml": OPENAI_YAML,
    "scripts/run.py": SCRIPT_V1,
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_file(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_fixture_quarantine(root: Path) -> str:
    """Write one new-only deterministic baseline without model-authorship claims."""

    root = root.expanduser().resolve(strict=False)
    if root.exists() or root.is_symlink():
        raise SkillRepairError("fixture quarantine root must be new")
    candidate_root = root / "candidate" / SKILL_ID
    candidate_root.mkdir(parents=True)
    records: list[dict[str, Any]] = []
    for relative, content in sorted(BASELINE_FILES.items()):
        target = candidate_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = content.encode("utf-8")
        target.write_bytes(raw)
        records.append(
            {"path": relative, "bytes": len(raw), "sha256": _sha256_bytes(raw)}
        )
    body = {
        "candidate_skill_id": SKILL_ID,
        "artifact_authorship": "deterministic_fixture",
        "files": records,
        "execution_allowed": False,
        "promotion_allowed": False,
    }
    manifest_sha256 = _sha256_bytes(_canonical_json(body).encode("utf-8"))
    manifest = {"schema_version": 1, **body, "manifest_sha256": manifest_sha256}
    (root / "quarantine_manifest.json").write_text(
        _json_file(manifest), encoding="utf-8"
    )
    return manifest_sha256


def frozen_cases() -> tuple[FrozenRepairCase, ...]:
    return (
        FrozenRepairCase(
            RepairCase("target-horizontal-spacing", "target", "exact-config-spacing-v1"),
            (
                (
                    "app.env",
                    "# deployment\nhost = api.example.com\nport\t=\t443\n\nmode = strict\n",
                ),
            ),
            (
                (
                    "config.json",
                    _json_file(
                        {
                            "host": "api.example.com",
                            "mode": "strict",
                            "port": "443",
                        }
                    ),
                ),
            ),
        ),
        FrozenRepairCase(
            RepairCase(
                "held-out-first-value-and-literals",
                "held_out",
                "exact-config-hidden-v1",
            ),
            (
                (
                    "app.env",
                    "token=alpha=beta\ntoken=ignored\nliteral=#not-comment\n# ignored\n",
                ),
            ),
            (
                (
                    "config.json",
                    _json_file({"literal": "#not-comment", "token": "alpha=beta"}),
                ),
            ),
        ),
        FrozenRepairCase(
            RepairCase(
                "library-regression-original-format",
                "library_regression",
                "exact-config-regression-v1",
            ),
            (("app.env", "host=localhost\nport=8080\n"),),
            (("config.json", _json_file({"host": "localhost", "port": "8080"})),),
        ),
    )


def _original_skill(manifest_sha256: str) -> SkillArtifact:
    return SkillArtifact(
        id=SKILL_ID,
        name="Parse Key Value Config",
        description=(
            "Parse app.env key-value records into deterministic config.json while "
            "preserving first-value and literal-value behavior."
        ),
        trigger="Use when app.env records must become config.json.",
        do_not_use_when=[
            "Do not use for shell evaluation or environment mutation.",
            "Do not use for unrelated file formats.",
        ],
        steps=[
            SkillStep(
                id="parse-config",
                description="Run the quarantined parser in the task workspace.",
                kind="script",
                inputs=["app.env"],
                outputs=["config.json"],
                script_path="scripts/run.py",
            )
        ],
        validators=["exact_file:config.json"],
        expected_artifacts=["config.json"],
        failure_modes=["input missing", "invalid UTF-8", "workspace path escape"],
        provenance_trace_ids=["family2-deterministic-baseline-v1"],
        status=LifecycleStatus.ACTIVE,
        version=1,
        metadata={
            "baseline_authorship": "deterministic_fixture",
            "quarantine_manifest_sha256": manifest_sha256,
            "portable_format": "agentskills.io",
        },
    )


def _profile(item: FrozenRepairCase) -> VerifierTrustProfile:
    hidden = item.case.split != "target"
    requirements = (
        "horizontal-separator-spacing",
        "first-value-wins",
        "literal-value-preservation",
    )
    return VerifierTrustProfile(
        verifier_id=item.case.verifier_id,
        level=(
            VerifierTrustLevel.HIDDEN_ORACLE
            if item.case.split == "held_out"
            else VerifierTrustLevel.DETERMINISTIC_BEHAVIORAL
        ),
        deterministic=True,
        requirement_ids=requirements,
        covered_requirement_ids=requirements,
        behavioral_assertion_count=3,
        author_independent_from_candidate=True,
        hidden_from_reviser=hidden,
        provenance_sha256=_sha256_bytes(
            _canonical_json(
                {
                    "case": asdict(item.case),
                    "input_files": item.input_files,
                    "expected_files": item.expected_files,
                }
            ).encode("utf-8")
        ),
    )


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


def _safe_candidate_quarantine(
    *, binding: Any, output_root: Path
) -> dict[str, Any]:
    raw_manifest = json.loads(
        (binding.quarantine_root / "quarantine_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    candidate_root = binding.quarantine_root / "candidate" / SKILL_ID
    envelope = ModelCandidateEnvelope(
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
    safe = quarantine_model_candidate(envelope=envelope, output_root=output_root)
    if safe.manifest_sha256 != binding.quarantine_manifest_sha256:
        raise SkillRepairError("safe family-2 quarantine differs from raw binding")
    return safe.to_dict()


def run_campaign(
    *,
    raw_root: Path,
    output_root: Path,
    codex_executable: Path = DEFAULT_CODEX,
    model_id: str = MODEL_ID,
    effort: str = EFFORT,
) -> dict[str, Any]:
    raw_root = raw_root.expanduser().resolve(strict=False)
    output_root = output_root.expanduser().resolve(strict=False)
    if raw_root.exists() or output_root.exists():
        raise SkillRepairError("raw and safe family-2 roots must both be new")
    if raw_root.is_relative_to(REPO_ROOT):
        raise SkillRepairError("raw provider/sandbox root must stay outside the repository")
    raw_root.mkdir(parents=True)

    baseline_root = raw_root / "baseline-quarantine"
    baseline_manifest_sha256 = write_fixture_quarantine(baseline_root)
    original = _original_skill(baseline_manifest_sha256)
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
        original_quarantine_root=baseline_root,
        original_manifest_sha256=baseline_manifest_sha256,
    )
    evaluator = IsolatedRepairEvaluator(
        raw_root=raw_root,
        baseline_manifest_sha256=baseline_manifest_sha256,
        baseline_quarantine_root=baseline_root,
        skill_id=SKILL_ID,
        reviser=reviser,
        case_specs=cases,
    )
    diagnosis = RepairDiagnosis(
        skill_id=SKILL_ID,
        failure_kind="skill_local",
        trace_ids=("family2-target-failure-001",),
        failed_target_case_ids=tuple(case.case_id for case in target_cases),
        verifier_feedback=(
            "Allow horizontal whitespace around the first key-value separator and "
            "ignore blank or comment-only lines; preserve existing first-value and "
            "literal-value behavior.",
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
    if len(reviser.bindings) != 1:
        raise SkillRepairError("family-2 repair produced no unique bundle binding")
    binding = next(iter(reviser.bindings.values()))

    output_root.mkdir(parents=True)
    safe_baseline_sha256 = write_fixture_quarantine(
        output_root / "baseline_quarantine"
    )
    if safe_baseline_sha256 != baseline_manifest_sha256:
        raise SkillRepairError("safe family-2 baseline differs from raw baseline")
    safe_candidate = _safe_candidate_quarantine(
        binding=binding, output_root=output_root / "candidate_quarantine"
    )
    decision = "promote" if result.adopted else "rollback"
    report: dict[str, Any] = {
        "schema_version": 1,
        "campaign_id": CAMPAIGN_ID,
        "skill_id": SKILL_ID,
        "baseline_version": 1,
        "candidate_version": binding.version,
        "decision": decision,
        "adopted": result.adopted,
        "repair_result": result.to_dict(),
        "baseline_bundle": {
            "authorship": "deterministic_fixture",
            "manifest_sha256": baseline_manifest_sha256,
            "model_authorship_claim": False,
        },
        "model_repair": binding.to_safe_dict(),
        "candidate_quarantine": safe_candidate,
        "isolated_phase_executions": [
            item.to_dict() for item in evaluator.executions
        ],
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
            "baseline_model_authored": False,
            "candidate_model_authored_repair": True,
            "provider_tool_execution_during_repair": False,
            "target_only_feedback_to_reviser": True,
            "candidate_hidden_held_out_executed_after_candidate_authorship": True,
            "copy_on_write_promoted": result.adopted,
            "copy_on_write_rolled_back": not result.adopted,
            "live_library_mutated": False,
            "provider_native_skill_invocation": False,
            "full_benchmark_claim": False,
        },
    }
    (output_root / "model_authored_skill_repair_family2_evidence.json").write_text(
        _json_file(report), encoding="utf-8"
    )
    (output_root / "resolved_library.json").write_text(
        _json_file([skill.to_dict() for skill in result.resolved_library]),
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
        report = run_campaign(
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
    repair = report["repair_result"]
    print("Merlin live model-authored skill repair family 2")
    print(f"skill={report['skill_id']}")
    print(f"decision={report['decision']}")
    print(f"version={report['baseline_version']}->{report['candidate_version']}")
    print(
        "gates="
        f"{sum(bool(gate['passed']) for gate in repair['gates'])}/"
        f"{len(repair['gates'])}"
    )
    print(f"safe_evidence={args.output.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
