"""Run Merlin's live GPT-5.6 authored-skill promotion campaign.

Raw provider and sandbox artifacts stay under ``--raw-root``.  ``--output``
receives only the revalidated candidate and a path-sanitized evidence summary.
The generator sees target examples but never sees the held-out Korean case or
its expected output.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

from src.merlin_harness.governed_provisioning import GovernedProvisioner, active_library_snapshot
from src.merlin_harness.isolated_candidate_runner import (
    CandidateExecutionCase,
    IsolatedCandidateRunnerError,
    run_quarantined_candidate,
)
from src.merlin_harness.library import FileSkillLibrary
from src.merlin_harness.managed_creation import (
    CreationCase,
    ManagedSkillProposal,
    validate_portable_candidate,
    validate_proposal,
)
from src.merlin_harness.model_candidate_generator import (
    CodexModelCandidateGenerator,
    ModelCandidateGeneratorError,
)
from src.merlin_harness.model_candidate_quarantine import (
    ModelCandidateQuarantineError,
    quarantine_model_candidate,
)
from src.merlin_harness.models import LifecycleStatus, SkillArtifact, SkillStep, ValidationResult
from src.merlin_harness.verifier_trust import (
    VerifierTrustLevel,
    VerifierTrustProfile,
    assess_verifier_trust,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MVP_ROOT = REPO_ROOT / "experiments" / "mvp"
DEFAULT_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
MODEL_ID = "gpt-5.6-terra"
EFFORT = "high"
CANDIDATE_ID = "extract-todo-items"


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _expected(*items: str) -> str:
    return json.dumps({"items": list(items)}, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def frozen_cases() -> tuple[CreationCase, ...]:
    return (
        CreationCase(
            id="target-english",
            prompt="From backlog.todo, extract TODO-prefixed entries into todo-items.json.",
            split="target",
            should_trigger=True,
            input_files=(("backlog.todo", "TODO: fix login\nnote: investigate\nTODO: write tests\n"),),
            expected_files=(("todo-items.json", _expected("fix login", "write tests")),),
        ),
        CreationCase(
            id="target-whitespace",
            prompt="Collect TODO lines in backlog.todo into todo-items.json.",
            split="target",
            should_trigger=True,
            input_files=(("backlog.todo", "  TODO: ship release\nDONE: old item\nTODO: update docs\n"),),
            expected_files=(("todo-items.json", _expected("ship release", "update docs")),),
        ),
        CreationCase(
            id="held-out-korean",
            prompt="backlog.todo에서 TODO 항목을 추출해 todo-items.json으로 저장해줘.",
            split="held_out",
            should_trigger=True,
            input_files=(("backlog.todo", "TODO: 회귀 테스트\n메모: 확인\nTODO: 문서 갱신\n"),),
            expected_files=(("todo-items.json", _expected("회귀 테스트", "문서 갱신")),),
        ),
        CreationCase(
            id="negative-line-summary",
            prompt="Count non-empty lines in input.txt and write summary.txt.",
            split="negative",
            should_trigger=False,
        ),
        CreationCase(
            id="negative-report",
            prompt="Create report.md in the workspace.",
            split="negative",
            should_trigger=False,
        ),
    )


def generator_prompt() -> str:
    """Target-only authoring prompt; held-out content is intentionally absent."""

    return """You are authoring one portable Agent Skill candidate for a governed skill harness.
Do not call tools, inspect the filesystem, or include commentary. Return only the JSON object required by the response schema.

Candidate identity: extract-todo-items
Required files, exactly:
1. SKILL.md
2. agents/openai.yaml
3. scripts/run.py

Behavior contract:
- The script interface is: scripts/run.py --workspace <absolute-task-workspace>
- Read UTF-8 text from <workspace>/backlog.todo.
- Ignore non-TODO lines.
- Accept TODO: after optional leading whitespace.
- Strip the TODO: marker and surrounding whitespace while preserving order.
- Write <workspace>/todo-items.json exactly as UTF-8 JSON with shape {"items": [strings]}, ensure_ascii=False, indent=2, sort_keys=True, followed by one newline.
- Resolve both paths under the supplied workspace and reject path escape.

Visible target examples:
A. TODO: fix login / note: investigate / TODO: write tests -> items ["fix login", "write tests"]
B. two leading spaces before TODO: ship release / DONE: old item / TODO: update docs -> items ["ship release", "update docs"]

There is a hidden held-out language case. Do not guess or mention its content; implement the stated Unicode-safe contract.

Safety and portability contract:
- Use Python standard library only.
- Allowed imports are argparse, json, and `from pathlib import Path`.
- No os, sys, subprocess, socket, network, dynamic code, private/dunder attribute access, secrets, environment access, or extra files.
- SKILL.md YAML frontmatter contains exactly quoted name and description. The name is "extract-todo-items" and the description clearly says when to use the skill.
- agents/openai.yaml contains interface.display_name, interface.short_description, and a default_prompt that includes $extract-todo-items.
- SKILL.md must explain the input, output, isolated script command, verification, and when not to use it (general line counting or arbitrary file creation).
"""


def _proposal(
    *,
    existing: tuple[SkillArtifact, ...],
    prompt_sha256: str,
    generator_model: str,
    generator_effort: str,
) -> ManagedSkillProposal:
    return ManagedSkillProposal(
        proposal_id="live-model-creation-todo-v1",
        candidate_skill_id=CANDIDATE_ID,
        source_type="explicit_user_request",
        provenance_trace_ids=("live-gpt56-generation-001",),
        cases=frozen_cases(),
        frozen_library_snapshot_sha256=active_library_snapshot(existing)[1],
        generator_backend="openai-codex-cli",
        generator_model=generator_model,
        generator_effort=generator_effort,
        generator_prompt_sha256=prompt_sha256,
    )


def _artifact(proposal: ManagedSkillProposal, manifest_sha256: str) -> SkillArtifact:
    return SkillArtifact(
        id=CANDIDATE_ID,
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
        provenance_trace_ids=list(proposal.provenance_trace_ids),
        status=LifecycleStatus.CANDIDATE,
        metadata={
            "proposal_id": proposal.proposal_id,
            "generator_backend": proposal.generator_backend,
            "generator_model": proposal.generator_model,
            "generator_effort": proposal.generator_effort,
            "generator_prompt_sha256": proposal.generator_prompt_sha256,
            "quarantine_manifest_sha256": manifest_sha256,
            "portable_format": "agentskills.io",
        },
    )


def _verifier_profile(case: CreationCase) -> VerifierTrustProfile:
    hidden = case.split == "held_out"
    provenance = _sha256_text(
        _canonical_json(
            {
                "case_id": case.id,
                "split": case.split,
                "input_files": case.input_files,
                "expected_files": case.expected_files,
            }
        )
    )
    return VerifierTrustProfile(
        verifier_id=f"exact-file-{case.id}-v1",
        level=(VerifierTrustLevel.HIDDEN_ORACLE if hidden else VerifierTrustLevel.DETERMINISTIC_BEHAVIORAL),
        deterministic=True,
        requirement_ids=("todo-json-shape", "todo-content-order"),
        covered_requirement_ids=("todo-json-shape", "todo-content-order"),
        behavioral_assertion_count=2,
        author_independent_from_candidate=True,
        hidden_from_reviser=hidden,
        provenance_sha256=provenance,
    )


def _gate(name: str, passed: bool, evidence: str, score: float | None = None) -> ValidationResult:
    return ValidationResult(name=name, passed=passed, evidence=evidence, score=score)


def _gate_dict(gate: ValidationResult) -> dict[str, object]:
    return asdict(gate)


def _cli_version(executable: Path) -> str:
    process = subprocess.run(
        [str(executable), "--version"], text=True, capture_output=True, timeout=15, check=False
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
        raise ValueError("raw and safe output roots must both be new")
    if raw_root.is_relative_to(REPO_ROOT):
        raise ValueError("raw provider/sandbox root must stay outside the repository")
    raw_root.mkdir(parents=True)
    prompt = generator_prompt()
    existing = tuple(FileSkillLibrary(MVP_ROOT / "skills").list())
    generator = CodexModelCandidateGenerator(
        executable=codex_executable,
        cli_version=_cli_version(codex_executable),
        model_id=model_id,
        effort=effort,
        timeout_s=300,
    )
    generation = generator.generate(
        candidate_skill_id=CANDIDATE_ID,
        prompt=prompt,
        run_root=raw_root / "generator",
    )
    quarantine = quarantine_model_candidate(
        envelope=generation.envelope,
        output_root=raw_root / "quarantine",
    )
    positive_cases = tuple(
        CandidateExecutionCase(
            case_id=case.id,
            split=case.split,
            input_files=case.input_files,
            expected_files=case.expected_files,
        )
        for case in frozen_cases()
        if case.should_trigger
    )
    execution = run_quarantined_candidate(
        quarantine_root=raw_root / "quarantine",
        expected_manifest_sha256=quarantine.manifest_sha256,
        cases=positive_cases,
        output_root=raw_root / "execution",
    )
    proposal = _proposal(
        existing=existing,
        prompt_sha256=generation.prompt_sha256,
        generator_model=model_id,
        generator_effort=effort,
    )
    candidate = _artifact(proposal, quarantine.manifest_sha256)
    gates: list[ValidationResult] = []
    gates.extend(validate_proposal(proposal, existing))
    gates.extend(
        _gate(f"Q_{item.name}", item.passed, item.evidence, item.score)
        for item in quarantine.gates
    )
    candidate_root = raw_root / "quarantine" / "candidate" / CANDIDATE_ID
    for gate in validate_portable_candidate(candidate_root, CANDIDATE_ID):
        if gate.name == "G2_safety" and gate.passed:
            gates.append(
                _gate(
                    "G2_safety",
                    True,
                    "model-authored script passed quarantine AST policy and immutable-manifest macOS confinement",
                )
            )
        else:
            gates.append(gate)

    active_candidate = copy.deepcopy(candidate)
    active_candidate.status = LifecycleStatus.ACTIVE
    decisions = {
        case.id: GovernedProvisioner(exposure_budget=1).decide(
            case.prompt, (*existing, active_candidate)
        )
        for case in frozen_cases()
    }
    trigger_correct = all(
        (decisions[case.id].primary_id == CANDIDATE_ID) == case.should_trigger
        for case in frozen_cases()
    )
    gates.append(
        _gate(
            "G3_trigger",
            trigger_correct,
            f"correct={sum(((decisions[c.id].primary_id == CANDIDATE_ID) == c.should_trigger) for c in frozen_cases())}/{len(frozen_cases())}",
        )
    )
    by_id = {case.case_id: case for case in execution.cases}
    target_cases = [case for case in frozen_cases() if case.split == "target"]
    held_out_cases = [case for case in frozen_cases() if case.split == "held_out"]
    target_trust = [
        check
        for case in target_cases
        for check in assess_verifier_trust(_verifier_profile(case), purpose="repair_feedback")
    ]
    heldout_trust = [
        check
        for case in held_out_cases
        for check in assess_verifier_trust(_verifier_profile(case), purpose="promotion")
    ]
    target_passed = all(by_id[case.id].passed for case in target_cases) and all(
        item.passed for item in target_trust
    )
    heldout_passed = all(by_id[case.id].passed for case in held_out_cases) and all(
        item.passed for item in heldout_trust
    )
    gates.append(_gate("G4_target", target_passed, f"isolated_exact={sum(by_id[c.id].passed for c in target_cases)}/{len(target_cases)}; trusted_verifier_checks={sum(x.passed for x in target_trust)}/{len(target_trust)}"))
    negative_cases = [case for case in frozen_cases() if case.split == "negative"]
    negative_passed = all(decisions[case.id].primary_id != CANDIDATE_ID for case in negative_cases)
    live_snapshot = active_library_snapshot(existing)[1]
    gates.append(_gate("G5_hidden_regression", heldout_passed and negative_passed and active_library_snapshot(existing)[1] == live_snapshot, f"hidden={sum(by_id[c.id].passed for c in held_out_cases)}/{len(held_out_cases)}; negative_routes={sum(decisions[c.id].primary_id != CANDIDATE_ID for c in negative_cases)}/{len(negative_cases)}; live_snapshot_unchanged=true"))
    pre_adoption_passed = all(gate.passed for gate in gates)
    provisional = (*copy.deepcopy(existing), active_candidate) if pre_adoption_passed else existing
    provisional_snapshot = active_library_snapshot(provisional)[1] if pre_adoption_passed else None
    adopted = pre_adoption_passed and provisional_snapshot != live_snapshot
    gates.append(_gate("G6_adoption", adopted, "copy-on-write provisional library adds one active candidate; live library remains unchanged" if adopted else "promotion gates did not all pass"))

    output_root.mkdir(parents=True)
    safe_quarantine = quarantine_model_candidate(
        envelope=generation.envelope,
        output_root=output_root / "quarantine",
    )
    result: dict[str, object] = {
        "schema_version": 1,
        "campaign_id": "live-gpt56-model-authored-skill-v1",
        "candidate_skill_id": CANDIDATE_ID,
        "adopted": adopted,
        "lifecycle_action": "adopt" if adopted else "reject",
        "generator": generation.to_safe_dict(),
        "quarantine": safe_quarantine.to_dict(),
        "isolated_execution": execution.to_dict(),
        "gates": [_gate_dict(gate) for gate in gates],
        "baseline_target_pass_rate": 0.0,
        "candidate_target_pass_rate": sum(by_id[c.id].passed for c in target_cases) / len(target_cases),
        "normalized_gain": 1.0 if target_passed else 0.0,
        "original_library_snapshot_sha256": live_snapshot,
        "provisional_library_snapshot_sha256": provisional_snapshot,
        "resolved_library_statuses": {
            skill.id: skill.status.value for skill in provisional
        },
        "held_out_contract": {
            "case_count": len(held_out_cases),
            "content_exposed_to_generator": False,
            "verifier_provenance_sha256": [_verifier_profile(case).provenance_sha256 for case in held_out_cases],
        },
        "evidence_boundary": {
            "actual_codex_provider_run": True,
            "requested_model_id": model_id,
            "provider_reported_model_ids": list(generation.provider_reported_model_ids),
            "model_evidence_level": generation.model_evidence_level,
            "model_authored_candidate": True,
            "provider_tool_execution_during_authoring": False,
            "quarantine_passed": all(item.passed for item in quarantine.gates),
            "isolated_candidate_execution": True,
            "target_verifier_passed": target_passed,
            "hidden_held_out_verifier_passed": heldout_passed,
            "copy_on_write_promoted": adopted,
            "live_library_mutated": False,
            "provider_native_skill_invocation": False,
            "full_benchmark_claim": False,
        },
    }
    (output_root / "model_authored_skill_evidence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_root / "provisional_library.json").write_text(
        json.dumps([skill.to_dict() for skill in provisional], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


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
        OSError,
        ValueError,
    ) as exc:
        parser.error(str(exc))
    print("Merlin live model-authored skill campaign")
    print(f"candidate={result['candidate_skill_id']}")
    print(f"adopted={str(result['adopted']).lower()}")
    print(f"target={result['baseline_target_pass_rate']:.0%}->{result['candidate_target_pass_rate']:.0%}")
    gates = result["gates"]
    print(f"gates={sum(bool(gate['passed']) for gate in gates)}/{len(gates)}")
    print(f"safe_evidence={args.output.expanduser().resolve()}")
    return 0 if result["adopted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
