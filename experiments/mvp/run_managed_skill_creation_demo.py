"""Run Merlin's first bounded, verifier-gated portable skill creation.

This controlled acceptance creates ``extract-todo-items`` as a candidate,
compiles a registered operation to a trusted script, validates trigger and
file-verifier cases, and adopts only into a copy-on-write library snapshot.
It does not claim provider-native skill invocation or model-quality gain.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from src.merlin_harness.governed_provisioning import active_library_snapshot
from src.merlin_harness.library import FileSkillLibrary
from src.merlin_harness.managed_creation import (
    SKILLS_REF_VERSION,
    CreationCase,
    ManagedCreationError,
    ManagedSkillDraft,
    ManagedSkillProposal,
    run_managed_creation,
)
from src.merlin_harness.models import SkillArtifact, ValidationResult


REPO_ROOT = Path(__file__).resolve().parents[2]
MVP_ROOT = REPO_ROOT / "experiments" / "mvp"
SKILLS_REF_COMMAND = (
    "uvx",
    "--from",
    f"skills-ref=={SKILLS_REF_VERSION}",
    "agentskills",
    "validate",
)


def _expected(*items: str) -> str:
    return json.dumps({"items": list(items)}, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def first_managed_creation_contract() -> tuple[
    ManagedSkillProposal,
    ManagedSkillDraft,
    tuple[SkillArtifact, ...],
]:
    existing_skills = tuple(FileSkillLibrary(MVP_ROOT / "skills").list())
    snapshot_sha256 = active_library_snapshot(existing_skills)[1]
    cases = (
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
            id="negative-file-artifact",
            prompt="Create report.md in the workspace.",
            split="negative",
            should_trigger=False,
        ),
    )
    generator_prompt = (
        "Create the minimum portable skill for extracting TODO-prefixed lines "
        "from backlog.todo into todo-items.json using a registered operation."
    )
    proposal = ManagedSkillProposal(
        proposal_id="managed-creation-todo-v1",
        candidate_skill_id="extract-todo-items",
        source_type="explicit_user_request",
        provenance_trace_ids=("proposal-user-request-001",),
        cases=cases,
        frozen_library_snapshot_sha256=snapshot_sha256,
        generator_backend="merlin-bounded-template",
        generator_model="frozen-operation-registry-v1",
        generator_effort="deterministic",
        generator_prompt_sha256=hashlib.sha256(generator_prompt.encode("utf-8")).hexdigest(),
    )
    draft = ManagedSkillDraft(
        skill_id="extract-todo-items",
        display_name="Extract TODO Items",
        description=(
            "Extract TODO-prefixed action items from backlog.todo into todo-items.json. "
            "Use when a task asks to collect TODO lines or create a TODO-items JSON artifact."
        ),
        trigger="Use when backlog.todo must be converted into todo-items.json from TODO-prefixed lines.",
        do_not_use_when=(
            "Do not use for general line counting or summaries.",
            "Do not use for creating arbitrary files without TODO extraction.",
        ),
        operation_id="extract-prefixed-lines-to-json",
        input_path="backlog.todo",
        output_path="todo-items.json",
        prefix="TODO:",
        default_prompt="Use $extract-todo-items to turn backlog.todo TODO lines into todo-items.json.",
    )
    return proposal, draft, existing_skills


def skills_ref_validator(candidate_root: Path) -> ValidationResult:
    process = subprocess.run(
        [*SKILLS_REF_COMMAND, str(candidate_root)],
        text=True,
        capture_output=True,
        timeout=60.0,
        check=False,
    )
    summary = " ".join((process.stdout + " " + process.stderr).split())
    return ValidationResult(
        name=f"skills-ref-{SKILLS_REF_VERSION}",
        passed=process.returncode == 0,
        evidence=f"exit={process.returncode}; output_sha256={hashlib.sha256(summary.encode('utf-8')).hexdigest()}",
    )


def _write_safe_summary(path: Path, report: dict) -> None:
    path = path.expanduser().resolve(strict=False)
    if path.exists():
        raise ManagedCreationError(f"refusing to overwrite safe summary: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--safe-summary", type=Path)
    parser.add_argument("--with-skills-ref", action="store_true")
    args = parser.parse_args(argv)
    proposal, draft, existing_skills = first_managed_creation_contract()
    try:
        result = run_managed_creation(
            proposal=proposal,
            draft=draft,
            existing_skills=existing_skills,
            output_root=args.output,
            external_validator=skills_ref_validator if args.with_skills_ref else None,
        )
        report = result.to_dict()
        if args.safe_summary:
            _write_safe_summary(args.safe_summary, report)
    except (ManagedCreationError, OSError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    print("Merlin managed skill creation")
    print(f"candidate={result.candidate_skill_id}")
    print(f"adopted={str(result.adopted).lower()}")
    print(f"target_pass_rate={result.baseline_target_pass_rate:.0%}->{result.candidate_target_pass_rate:.0%}")
    print(f"normalized_gain={result.normalized_gain:.3f}" if result.normalized_gain is not None else "normalized_gain=null")
    print(f"gates={sum(gate['passed'] for gate in result.gates)}/{len(result.gates)}")
    print(f"saved -> {args.output.expanduser().resolve()}")
    return 0 if result.adopted else 1


if __name__ == "__main__":
    raise SystemExit(main())
