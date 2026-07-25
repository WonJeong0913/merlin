"""Run a precommitted GPT-5.6 skill campaign that may promote or roll back.

The model sees the behavioral contract and two visible target examples.  It
does not see the exact held-out Markdown fence case or its expected output.
Target and held-out execution are separate immutable phases.  A target-passing
candidate that fails the hidden verifier is rolled back by retaining the exact
original library snapshot.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from src.merlin_harness.governed_provisioning import GovernedProvisioner, active_library_snapshot
from src.merlin_harness.isolated_candidate_runner import (
    CandidateExecutionCase,
    IsolatedCandidateRunnerError,
    run_quarantined_candidate_phase,
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
CANDIDATE_ID = "extract-markdown-headings"
CAMPAIGN_ID = "live-gpt56-hidden-rollback-v1"


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _expected(*headings: tuple[int, str]) -> str:
    return (
        json.dumps(
            {"headings": [{"level": level, "text": text} for level, text in headings]},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def frozen_cases() -> tuple[CreationCase, ...]:
    """Return the exact precommitted target, hidden, and routing cases."""

    return (
        CreationCase(
            id="target-basic-headings",
            prompt="Extract Markdown headings from notes.md into markdown-headings.json.",
            split="target",
            should_trigger=True,
            input_files=(("notes.md", "# Project\ntext\n## Status\n"),),
            expected_files=(("markdown-headings.json", _expected((1, "Project"), (2, "Status"))),),
        ),
        CreationCase(
            id="target-simple-fence",
            prompt="List real headings in notes.md while ignoring fenced code headings.",
            split="target",
            should_trigger=True,
            input_files=(
                (
                    "notes.md",
                    "# Real\n```python\n## Fake\n```\n### End ###\n",
                ),
            ),
            expected_files=(("markdown-headings.json", _expected((1, "Real"), (3, "End"))),),
        ),
        CreationCase(
            id="held-out-long-fence",
            prompt="notes.md의 실제 제목만 markdown-headings.json으로 추출해줘.",
            split="held_out",
            should_trigger=True,
            input_files=(
                (
                    "notes.md",
                    "# Outside\n````markdown\n## Hidden fake\n```\n### Still fake\n````\n## Visible ##\n",
                ),
            ),
            expected_files=(("markdown-headings.json", _expected((1, "Outside"), (2, "Visible"))),),
        ),
        CreationCase(
            id="negative-line-count",
            prompt="Count non-empty lines in notes.md and write count.txt.",
            split="negative",
            should_trigger=False,
        ),
        CreationCase(
            id="negative-freeform-report",
            prompt="Write a general project report.md.",
            split="negative",
            should_trigger=False,
        ),
    )


def generator_prompt() -> str:
    """Return the target-only authoring prompt; the exact hidden case is absent."""

    return """You are authoring one portable Agent Skill candidate for a governed skill harness.
Do not call tools, inspect the filesystem, or include commentary. Return only the JSON object required by the response schema.

Candidate identity: extract-markdown-headings
Required files, exactly:
1. SKILL.md
2. agents/openai.yaml
3. scripts/run.py

Behavior contract:
- The script interface is: scripts/run.py --workspace <absolute-task-workspace>
- Read UTF-8 Markdown from <workspace>/notes.md.
- Extract ATX headings with one through six # markers and whitespace after the markers.
- Permit up to three leading spaces before an ATX heading.
- Ignore heading-looking lines while inside Markdown fenced code blocks. Backtick and tilde fences are both in scope.
- Remove surrounding whitespace and an optional trailing closing-# sequence from heading text while preserving order.
- Write <workspace>/markdown-headings.json exactly as UTF-8 JSON with shape {"headings": [{"level": integer, "text": string}]}, ensure_ascii=False, indent=2, sort_keys=True, followed by one newline.
- Resolve both paths under the supplied workspace and reject path escape.

Visible target examples:
A. # Project / text / ## Status -> headings [(1, "Project"), (2, "Status")]
B. # Real / triple-backtick python fence containing ## Fake / closing triple-backtick / ### End ### -> headings [(1, "Real"), (3, "End")]

There is a frozen hidden Markdown edge case. Do not guess or mention its exact content; implement the general contract.

Safety and portability contract:
- Use Python standard library only.
- Allowed imports are argparse, json, re, and `from pathlib import Path`.
- No os, sys, subprocess, socket, network, dynamic code, private/dunder attribute access, secrets, environment access, or extra files.
- SKILL.md YAML frontmatter contains exactly quoted name and description. The name is "extract-markdown-headings" and the description clearly says when to use the skill.
- agents/openai.yaml contains interface.display_name, interface.short_description, and a default_prompt that includes $extract-markdown-headings.
- SKILL.md must explain input, output, isolated script command, verification, and when not to use it (line counting or general report writing).
"""


def campaign_contract() -> dict[str, Any]:
    cases = frozen_cases()
    return {
        "schema_version": 1,
        "campaign_id": CAMPAIGN_ID,
        "candidate_skill_id": CANDIDATE_ID,
        "generator_prompt_sha256": _sha256_text(generator_prompt()),
        "case_commitments": [
            {
                "case_id": case.id,
                "split": case.split,
                "should_trigger": case.should_trigger,
                "content_sha256": _sha256_text(
                    _canonical_json(
                        {
                            "prompt": case.prompt,
                            "input_files": case.input_files,
                            "expected_files": case.expected_files,
                        }
                    )
                ),
            }
            for case in cases
        ],
        "outcome_policy": {
            "adopt": "all quarantine, target, hidden, routing, trust, and COW gates pass",
            "rollback": "quarantine and target pass but hidden or negative routing fails",
            "reject": "candidate fails before completing the target-qualified boundary",
        },
        "hidden_content_exposed_to_generator": False,
        "target_and_hidden_execute_in_separate_phases": True,
    }


def _proposal(
    *, existing: tuple[SkillArtifact, ...], prompt_sha256: str, model_id: str, effort: str
) -> ManagedSkillProposal:
    return ManagedSkillProposal(
        proposal_id="live-model-markdown-hidden-v1",
        candidate_skill_id=CANDIDATE_ID,
        source_type="explicit_user_request",
        provenance_trace_ids=("live-gpt56-markdown-generation-001",),
        cases=frozen_cases(),
        frozen_library_snapshot_sha256=active_library_snapshot(existing)[1],
        generator_backend="openai-codex-cli",
        generator_model=model_id,
        generator_effort=effort,
        generator_prompt_sha256=prompt_sha256,
    )


def _artifact(proposal: ManagedSkillProposal, manifest_sha256: str) -> SkillArtifact:
    return SkillArtifact(
        id=CANDIDATE_ID,
        name="Extract Markdown Headings",
        description=(
            "Extract real ATX headings from notes.md into markdown-headings.json while "
            "ignoring fenced code. Use only for that explicit Markdown-heading task."
        ),
        trigger="Use when notes.md headings must become markdown-headings.json.",
        do_not_use_when=[
            "Do not use for line counting.",
            "Do not use for general project report writing.",
        ],
        steps=[
            SkillStep(
                id="model-authored-markdown-heading-extractor",
                description="Run the quarantined model-authored Markdown heading extractor.",
                kind="script",
                inputs=["notes.md"],
                outputs=["markdown-headings.json"],
                script_path="scripts/run.py",
            )
        ],
        validators=["exact_file:markdown-headings.json"],
        expected_artifacts=["markdown-headings.json"],
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
            "campaign_contract_sha256": _sha256_text(_canonical_json(campaign_contract())),
        },
    )


def _verifier_profile(case: CreationCase) -> VerifierTrustProfile:
    hidden = case.split == "held_out"
    return VerifierTrustProfile(
        verifier_id=f"exact-markdown-headings-{case.id}-v1",
        level=(
            VerifierTrustLevel.HIDDEN_ORACLE
            if hidden
            else VerifierTrustLevel.DETERMINISTIC_BEHAVIORAL
        ),
        deterministic=True,
        requirement_ids=("heading-level-text", "fenced-code-exclusion"),
        covered_requirement_ids=("heading-level-text", "fenced-code-exclusion"),
        behavioral_assertion_count=2,
        author_independent_from_candidate=True,
        hidden_from_reviser=hidden,
        provenance_sha256=_sha256_text(
            _canonical_json(
                {
                    "case_id": case.id,
                    "split": case.split,
                    "input_files": case.input_files,
                    "expected_files": case.expected_files,
                }
            )
        ),
    )


def _gate(name: str, passed: bool, evidence: str) -> ValidationResult:
    return ValidationResult(name=name, passed=passed, evidence=evidence)


def _cli_version(executable: Path) -> str:
    process = subprocess.run(
        [str(executable), "--version"], text=True, capture_output=True, timeout=15, check=False
    )
    if process.returncode != 0 or not process.stdout.strip():
        raise ModelCandidateGeneratorError("unable to resolve Codex CLI version")
    return process.stdout.strip()


def resolve_lifecycle_outcome(
    *, pre_hidden_passed: bool, hidden_passed: bool, negative_passed: bool
) -> Literal["adopt", "rollback", "reject"]:
    if not pre_hidden_passed:
        return "reject"
    if hidden_passed and negative_passed:
        return "adopt"
    return "rollback"


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
        raise ValueError("raw and safe output roots must both be new")
    if raw_root.is_relative_to(REPO_ROOT):
        raise ValueError("raw provider/sandbox root must stay outside the repository")
    raw_root.mkdir(parents=True)

    contract = campaign_contract()
    prompt = generator_prompt()
    existing = tuple(FileSkillLibrary(MVP_ROOT / "skills").list())
    live_snapshot = active_library_snapshot(existing)[1]
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
    if generation.prompt_sha256 != contract["generator_prompt_sha256"]:
        raise ValueError("generated prompt differs from precommitted campaign contract")
    quarantine = quarantine_model_candidate(
        envelope=generation.envelope,
        output_root=raw_root / "quarantine",
    )
    proposal = _proposal(
        existing=existing,
        prompt_sha256=generation.prompt_sha256,
        model_id=model_id,
        effort=effort,
    )
    candidate = _artifact(proposal, quarantine.manifest_sha256)
    candidate_root = raw_root / "quarantine" / "candidate" / CANDIDATE_ID

    gates: list[ValidationResult] = list(validate_proposal(proposal, existing))
    gates.extend(
        _gate(f"Q_{item.name}", item.passed, item.evidence) for item in quarantine.gates
    )
    for item in validate_portable_candidate(candidate_root, CANDIDATE_ID):
        evidence = item.evidence
        if item.name == "G2_safety" and item.passed:
            evidence = "quarantine AST policy plus immutable-manifest macOS confinement"
        gates.append(_gate(item.name, item.passed, evidence))

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

    target_cases = tuple(case for case in frozen_cases() if case.split == "target")
    heldout_cases = tuple(case for case in frozen_cases() if case.split == "held_out")
    target_phase = run_quarantined_candidate_phase(
        quarantine_root=raw_root / "quarantine",
        expected_manifest_sha256=quarantine.manifest_sha256,
        phase="target",
        cases=tuple(
            CandidateExecutionCase(case.id, "target", case.input_files, case.expected_files)
            for case in target_cases
        ),
        output_root=raw_root / "execution-target",
    )
    target_trust = tuple(
        check
        for case in target_cases
        for check in assess_verifier_trust(_verifier_profile(case), purpose="repair_feedback")
    )
    target_passed = target_phase.all_passed and all(item.passed for item in target_trust)
    gates.append(
        _gate(
            "G4_target",
            target_passed,
            f"isolated_exact={sum(item.passed for item in target_phase.cases)}/{len(target_phase.cases)}; trusted_verifier_checks={sum(item.passed for item in target_trust)}/{len(target_trust)}",
        )
    )
    pre_hidden_passed = all(item.passed for item in gates)

    heldout_phase = None
    heldout_trust: tuple[ValidationResult, ...] = ()
    hidden_passed = False
    if pre_hidden_passed:
        heldout_phase = run_quarantined_candidate_phase(
            quarantine_root=raw_root / "quarantine",
            expected_manifest_sha256=quarantine.manifest_sha256,
            phase="held_out",
            cases=tuple(
                CandidateExecutionCase(case.id, "held_out", case.input_files, case.expected_files)
                for case in heldout_cases
            ),
            output_root=raw_root / "execution-held-out",
        )
        heldout_trust = tuple(
            check
            for case in heldout_cases
            for check in assess_verifier_trust(_verifier_profile(case), purpose="promotion")
        )
        hidden_passed = heldout_phase.all_passed and all(
            item.passed for item in heldout_trust
        )
    negative_cases = tuple(case for case in frozen_cases() if case.split == "negative")
    negative_passed = all(
        decisions[case.id].primary_id != CANDIDATE_ID for case in negative_cases
    )
    gates.append(
        _gate(
            "G5_hidden_regression",
            hidden_passed and negative_passed and active_library_snapshot(existing)[1] == live_snapshot,
            f"hidden={0 if heldout_phase is None else sum(item.passed for item in heldout_phase.cases)}/{len(heldout_cases)}; negative_routes={sum(decisions[c.id].primary_id != CANDIDATE_ID for c in negative_cases)}/{len(negative_cases)}; live_snapshot_unchanged=true",
        )
    )

    outcome = resolve_lifecycle_outcome(
        pre_hidden_passed=pre_hidden_passed,
        hidden_passed=hidden_passed,
        negative_passed=negative_passed,
    )
    if outcome == "adopt":
        resolved = (*copy.deepcopy(existing), active_candidate)
        provisional_snapshot = active_library_snapshot(resolved)[1]
        cow_passed = provisional_snapshot != live_snapshot
        cow_evidence = "all gates passed; new active candidate exists only in COW snapshot"
    else:
        resolved = copy.deepcopy(existing)
        provisional_snapshot = active_library_snapshot(resolved)[1]
        cow_passed = provisional_snapshot == live_snapshot
        cow_evidence = (
            "hidden/routing gate failed; candidate absent and original library snapshot retained"
            if outcome == "rollback"
            else "pre-hidden qualification failed; candidate rejected and original snapshot retained"
        )
    gates.append(_gate("G6_cow_resolution", cow_passed, cow_evidence))

    output_root.mkdir(parents=True)
    safe_quarantine = quarantine_model_candidate(
        envelope=generation.envelope,
        output_root=output_root / "quarantine",
    )
    result: dict[str, Any] = {
        "schema_version": 1,
        "campaign_id": CAMPAIGN_ID,
        "campaign_contract": contract,
        "campaign_contract_sha256": _sha256_text(_canonical_json(contract)),
        "candidate_skill_id": CANDIDATE_ID,
        "adopted": outcome == "adopt",
        "lifecycle_action": outcome,
        "generator": generation.to_safe_dict(),
        "quarantine": safe_quarantine.to_dict(),
        "isolated_execution": {
            "target_phase": target_phase.to_dict(),
            "held_out_phase": None if heldout_phase is None else heldout_phase.to_dict(),
        },
        "gates": [asdict(item) for item in gates],
        "baseline_target_pass_rate": 0.0,
        "candidate_target_pass_rate": sum(item.passed for item in target_phase.cases)
        / len(target_phase.cases),
        "original_library_snapshot_sha256": live_snapshot,
        "resolved_library_snapshot_sha256": provisional_snapshot,
        "resolved_library_statuses": {skill.id: skill.status.value for skill in resolved},
        "held_out_contract": {
            "case_count": len(heldout_cases),
            "content_exposed_to_generator": False,
            "executed_only_after_target_qualification": pre_hidden_passed,
            "verifier_provenance_sha256": [
                _verifier_profile(case).provenance_sha256 for case in heldout_cases
            ],
        },
        "evidence_boundary": {
            "actual_codex_provider_run": True,
            "requested_model_id": model_id,
            "provider_reported_model_ids": list(generation.provider_reported_model_ids),
            "model_evidence_level": generation.model_evidence_level,
            "model_authored_candidate": True,
            "provider_tool_execution_during_authoring": False,
            "quarantine_passed": all(item.passed for item in quarantine.gates),
            "isolated_target_execution": True,
            "isolated_held_out_execution": heldout_phase is not None,
            "target_verifier_passed": target_passed,
            "hidden_held_out_verifier_passed": hidden_passed,
            "copy_on_write_promoted": outcome == "adopt",
            "copy_on_write_rolled_back": outcome == "rollback" and cow_passed,
            "live_library_mutated": False,
            "provider_native_skill_invocation": False,
            "full_benchmark_claim": False,
        },
    }
    evidence_path = output_root / "model_authored_hidden_rollback_evidence.json"
    evidence_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_root / "resolved_library.json").write_text(
        json.dumps([skill.to_dict() for skill in resolved], ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
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
    parser.add_argument(
        "--require-outcome",
        choices=("adopt", "rollback", "reject", "any"),
        default="rollback",
    )
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
    outcome = str(result["lifecycle_action"])
    print("Merlin live model-authored hidden-verifier campaign")
    print(f"candidate={result['candidate_skill_id']}")
    print(f"outcome={outcome}")
    print(f"target_pass_rate={result['candidate_target_pass_rate']:.0%}")
    gates = result["gates"]
    print(f"gates={sum(bool(gate['passed']) for gate in gates)}/{len(gates)}")
    print(f"safe_evidence={args.output.expanduser().resolve()}")
    return 0 if args.require_outcome in {"any", outcome} else 2


if __name__ == "__main__":
    raise SystemExit(main())
