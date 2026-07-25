"""Bounded, verifier-gated creation of portable Agent Skills candidates.

The first generator deliberately does not execute arbitrary model-authored
code. A draft selects one registered operation and supplies bounded metadata;
Merlin compiles that operation to a trusted script template, validates the
portable folder, evaluates frozen target/held-out cases, and adopts only into a
copy-on-write library snapshot.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Literal

from .governed_provisioning import GovernedProvisioner, active_library_snapshot
from .lifecycle import (
    all_passed,
    decide_candidate_lifecycle,
    stage_provisional_lifecycle_change,
    validate_aip_lite_skill,
)
from .models import (
    LifecycleAction,
    LifecycleStatus,
    SkillArtifact,
    SkillStep,
    ValidationResult,
)


SKILLS_REF_VERSION = "0.1.1"
SAFE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
SAFE_PROPOSAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_RELATIVE_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
MAX_TEXT_CHARS = 20_000
MAX_CASE_FILES = 16
MAX_CASE_FILE_CHARS = 100_000
MAX_SKILL_MD_LINES = 500
MAX_SCRIPT_SECONDS = 5.0


class ManagedCreationError(ValueError):
    """Raised when a proposal, candidate, or evaluation violates its contract."""


@dataclass(frozen=True, slots=True)
class CreationCase:
    id: str
    prompt: str
    split: Literal["target", "held_out", "negative"]
    should_trigger: bool
    input_files: tuple[tuple[str, str], ...] = ()
    expected_files: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ManagedSkillProposal:
    proposal_id: str
    candidate_skill_id: str
    source_type: Literal[
        "explicit_user_request",
        "repeated_verifier_failure",
        "capability_gap",
        "identity_breaking_repair",
    ]
    provenance_trace_ids: tuple[str, ...]
    cases: tuple[CreationCase, ...]
    frozen_library_snapshot_sha256: str
    generator_backend: str
    generator_model: str
    generator_effort: str
    generator_prompt_sha256: str


@dataclass(frozen=True, slots=True)
class ManagedSkillDraft:
    skill_id: str
    display_name: str
    description: str
    trigger: str
    do_not_use_when: tuple[str, ...]
    operation_id: Literal["extract-prefixed-lines-to-json"]
    input_path: str
    output_path: str
    prefix: str
    default_prompt: str


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    case_id: str
    split: str
    should_trigger: bool
    candidate_primary: bool
    baseline_passed: bool | None
    candidate_passed: bool | None
    off_task_files: tuple[str, ...]
    latency_s: float | None
    stderr_sha256: str | None


@dataclass(frozen=True, slots=True)
class ManagedCreationResult:
    proposal_id: str
    candidate_skill_id: str
    adopted: bool
    lifecycle_action: str
    original_library_snapshot_sha256: str
    provisional_library_snapshot_sha256: str | None
    external_validator: dict[str, Any]
    gates: tuple[dict[str, Any], ...]
    case_outcomes: tuple[CaseOutcome, ...]
    baseline_target_pass_rate: float
    candidate_target_pass_rate: float
    normalized_gain: float | None
    candidate_folder_sha256: str
    resolved_library_statuses: dict[str, str]
    evidence_boundary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["schema_version"] = 1
        return value


ExternalValidator = Callable[[Path], ValidationResult]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_relative_path(value: str, *, label: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or any(part in {"", "."} for part in path.parts)
        or any(part.startswith(".") for part in path.parts)
        or not SAFE_RELATIVE_PATH_RE.fullmatch(value)
    ):
        raise ManagedCreationError(f"{label} must be a visible safe relative path")
    return path.as_posix()


def _validate_case(case: CreationCase) -> None:
    if not SAFE_PROPOSAL_ID_RE.fullmatch(case.id):
        raise ManagedCreationError("creation case has an unsafe ID")
    if not isinstance(case.prompt, str) or not case.prompt.strip() or len(case.prompt) > MAX_TEXT_CHARS:
        raise ManagedCreationError(f"creation case {case.id} has an invalid prompt")
    expected_trigger = case.split != "negative"
    if case.should_trigger is not expected_trigger:
        raise ManagedCreationError(f"creation case {case.id} split/trigger contract disagrees")
    if len(case.input_files) > MAX_CASE_FILES or len(case.expected_files) > MAX_CASE_FILES:
        raise ManagedCreationError(f"creation case {case.id} exceeds the file-count budget")
    for label, entries in (("input", case.input_files), ("expected", case.expected_files)):
        seen: set[str] = set()
        for path, content in entries:
            path = _safe_relative_path(path, label=f"case {case.id} {label} path")
            if path in seen:
                raise ManagedCreationError(f"creation case {case.id} has duplicate {label} paths")
            if not isinstance(content, str) or len(content) > MAX_CASE_FILE_CHARS or "\x00" in content:
                raise ManagedCreationError(f"creation case {case.id} has invalid {label} content")
            seen.add(path)
    if expected_trigger and (not case.input_files or not case.expected_files):
        raise ManagedCreationError(f"positive creation case {case.id} needs input and expected files")
    if not expected_trigger and (case.input_files or case.expected_files):
        raise ManagedCreationError(f"negative creation case {case.id} must be routing-only")


def validate_proposal(
    proposal: ManagedSkillProposal,
    existing_skills: tuple[SkillArtifact, ...],
) -> list[ValidationResult]:
    """G0: validate immutable need evidence and prove no active skill covers it."""

    checks: list[ValidationResult] = []
    try:
        if not SAFE_PROPOSAL_ID_RE.fullmatch(proposal.proposal_id):
            raise ManagedCreationError("proposal ID is unsafe")
        if not SAFE_ID_RE.fullmatch(proposal.candidate_skill_id):
            raise ManagedCreationError("candidate skill ID violates Agent Skills naming")
        if not proposal.provenance_trace_ids or len(set(proposal.provenance_trace_ids)) != len(proposal.provenance_trace_ids):
            raise ManagedCreationError("proposal requires unique provenance trace IDs")
        if any(not SAFE_PROPOSAL_ID_RE.fullmatch(value) for value in proposal.provenance_trace_ids):
            raise ManagedCreationError("proposal provenance trace ID is unsafe")
        if not proposal.cases:
            raise ManagedCreationError("proposal requires frozen cases")
        for case in proposal.cases:
            _validate_case(case)
        case_ids = [case.id for case in proposal.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ManagedCreationError("proposal contains duplicate case IDs")
        splits = {case.split for case in proposal.cases}
        if not {"target", "held_out", "negative"}.issubset(splits):
            raise ManagedCreationError("proposal requires target, held-out, and negative cases")
        _snapshot_id, snapshot_sha256, _active_ids = active_library_snapshot(existing_skills)
        if snapshot_sha256 != proposal.frozen_library_snapshot_sha256:
            raise ManagedCreationError("proposal library snapshot drifted")
        if proposal.candidate_skill_id in {skill.id for skill in existing_skills}:
            raise ManagedCreationError("candidate ID already exists in the library")
        if not re.fullmatch(r"[0-9a-f]{64}", proposal.generator_prompt_sha256):
            raise ManagedCreationError("generator prompt hash is invalid")
        if any(
            not isinstance(value, str) or not value.strip() or len(value) > 200
            for value in (
                proposal.generator_backend,
                proposal.generator_model,
                proposal.generator_effort,
            )
        ):
            raise ManagedCreationError("generator contract is incomplete")
        provisioner = GovernedProvisioner(exposure_budget=1)
        covered = {
            decision.primary_id
            for case in proposal.cases
            if case.should_trigger
            for decision in (provisioner.decide(case.prompt, existing_skills),)
            if decision.primary_id is not None
            and decision.candidate(decision.primary_id).exact_anchor_evidence
        }
        if covered:
            raise ManagedCreationError(
                "existing active skills already cover positive cases: " + ", ".join(sorted(covered))
            )
    except (ManagedCreationError, ValueError) as exc:
        checks.append(ValidationResult("G0_need", False, evidence=str(exc)))
    else:
        checks.append(
            ValidationResult(
                "G0_need",
                True,
                evidence=(
                    f"{proposal.source_type}; {len(proposal.provenance_trace_ids)} provenance IDs; "
                    f"{len(proposal.cases)} frozen cases; no reusable exact-contract active route"
                ),
            )
        )
    return checks


def _quote_yaml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _operation_script(draft: ManagedSkillDraft) -> str:
    if draft.operation_id != "extract-prefixed-lines-to-json":
        raise ManagedCreationError("draft selected an unregistered operation")
    input_path = json.dumps(draft.input_path)
    output_path = json.dumps(draft.output_path)
    prefix = json.dumps(draft.prefix)
    return f'''#!/usr/bin/env python3
"""Trusted Merlin operation: extract prefixed lines to JSON."""

import argparse
import json
from pathlib import Path


def inside(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    candidate.relative_to(root)
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    root = args.workspace.resolve()
    source = inside(root, {input_path})
    destination = inside(root, {output_path})
    marker = {prefix}
    normalized_lines = [
        line.lstrip()
        for line in source.read_text(encoding="utf-8").splitlines()
    ]
    items = [
        line[len(marker):].strip()
        for line in normalized_lines
        if line.startswith(marker)
    ]
    destination.write_text(
        json.dumps({{"items": items}}, ensure_ascii=False, indent=2, sort_keys=True) + "\\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def validate_draft(proposal: ManagedSkillProposal, draft: ManagedSkillDraft) -> list[ValidationResult]:
    checks: list[ValidationResult] = []
    try:
        if draft.skill_id != proposal.candidate_skill_id or not SAFE_ID_RE.fullmatch(draft.skill_id):
            raise ManagedCreationError("draft skill ID does not match the proposal")
        for label, value, limit in (
            ("display_name", draft.display_name, 80),
            ("description", draft.description, 1024),
            ("trigger", draft.trigger, 1024),
            ("prefix", draft.prefix, 80),
            ("default_prompt", draft.default_prompt, 500),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
                raise ManagedCreationError(f"draft {label} is invalid")
        if "use " not in draft.description.lower() and "when " not in draft.description.lower():
            raise ManagedCreationError("description must state when to use the skill")
        if not draft.default_prompt.startswith(f"Use ${draft.skill_id} "):
            raise ManagedCreationError("default prompt must explicitly mention the $skill-name")
        if len(draft.do_not_use_when) < 1 or any(not value.strip() for value in draft.do_not_use_when):
            raise ManagedCreationError("draft requires should-not-trigger guidance")
        _safe_relative_path(draft.input_path, label="draft input path")
        _safe_relative_path(draft.output_path, label="draft output path")
        if draft.input_path == draft.output_path:
            raise ManagedCreationError("draft input and output paths must differ")
        _operation_script(draft)
    except ManagedCreationError as exc:
        checks.append(ValidationResult("draft_schema", False, evidence=str(exc)))
    else:
        checks.append(
            ValidationResult(
                "draft_schema",
                True,
                evidence="bounded metadata and registered operation contract passed",
            )
        )
    return checks


def compile_candidate(
    proposal: ManagedSkillProposal,
    draft: ManagedSkillDraft,
    candidate_root: Path,
) -> SkillArtifact:
    """Compile one bounded draft to a portable folder and candidate artifact."""

    if candidate_root.exists():
        raise ManagedCreationError(f"refusing to overwrite candidate folder: {candidate_root}")
    if not all_passed(validate_draft(proposal, draft)):
        raise ManagedCreationError("candidate draft failed schema validation")
    (candidate_root / "scripts").mkdir(parents=True)
    (candidate_root / "agents").mkdir()
    skill_md = (
        "---\n"
        f"name: {_quote_yaml(draft.skill_id)}\n"
        f"description: {_quote_yaml(draft.description)}\n"
        "---\n\n"
        f"# {draft.display_name}\n\n"
        "1. Confirm the declared input file exists inside the task workspace.\n"
        f"2. Run `scripts/run.py --workspace <workspace>` to execute `{draft.operation_id}`.\n"
        f"3. Verify `{draft.output_path}` before reporting completion.\n"
        "4. Stop and report the missing or malformed input instead of inventing output.\n"
    )
    (candidate_root / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (candidate_root / "scripts" / "run.py").write_text(_operation_script(draft), encoding="utf-8")
    openai_yaml = (
        "interface:\n"
        f"  display_name: {_quote_yaml(draft.display_name)}\n"
        f"  short_description: {_quote_yaml(draft.description[:64])}\n"
        f"  default_prompt: {_quote_yaml(draft.default_prompt)}\n"
    )
    (candidate_root / "agents" / "openai.yaml").write_text(openai_yaml, encoding="utf-8")
    return SkillArtifact(
        id=draft.skill_id,
        name=draft.display_name,
        description=draft.description,
        trigger=draft.trigger,
        do_not_use_when=list(draft.do_not_use_when),
        steps=[
            SkillStep(
                id="extract-prefixed-lines",
                description=f"Extract lines beginning with {draft.prefix!r} from {draft.input_path} into {draft.output_path}.",
                kind="script",
                inputs=[draft.input_path],
                outputs=[draft.output_path],
                script_path="scripts/run.py",
            )
        ],
        validators=[f"exact_file:{draft.output_path}"],
        expected_artifacts=[draft.output_path],
        failure_modes=["declared input missing", "input is not UTF-8 text", "workspace path escapes root"],
        provenance_trace_ids=list(proposal.provenance_trace_ids),
        status=LifecycleStatus.CANDIDATE,
        metadata={
            "proposal_id": proposal.proposal_id,
            "source_type": proposal.source_type,
            "generator_backend": proposal.generator_backend,
            "generator_model": proposal.generator_model,
            "generator_effort": proposal.generator_effort,
            "generator_prompt_sha256": proposal.generator_prompt_sha256,
            "operation_id": draft.operation_id,
            "portable_format": "agentskills.io",
            "skills_ref_version": SKILLS_REF_VERSION,
        },
    )


def _parse_frontmatter(skill_md: str) -> tuple[dict[str, str], str]:
    lines = skill_md.splitlines()
    if not lines or lines[0] != "---":
        raise ManagedCreationError("SKILL.md frontmatter is missing")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ManagedCreationError("SKILL.md frontmatter is not closed") from exc
    values: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line:
            raise ManagedCreationError("SKILL.md frontmatter line is malformed")
        key, raw = line.split(":", 1)
        key = key.strip()
        if key in values:
            raise ManagedCreationError("SKILL.md frontmatter contains duplicate keys")
        scalar = raw.strip()
        if not scalar:
            raise ManagedCreationError("SKILL.md frontmatter values must be non-empty strings")
        if scalar.startswith('"'):
            try:
                value = json.loads(scalar)
            except json.JSONDecodeError as exc:
                raise ManagedCreationError("SKILL.md quoted frontmatter value is malformed") from exc
            if not isinstance(value, str):
                raise ManagedCreationError("SKILL.md frontmatter values must be strings")
        elif scalar.startswith("'"):
            if len(scalar) < 2 or not scalar.endswith("'"):
                raise ManagedCreationError("SKILL.md quoted frontmatter value is malformed")
            value = scalar[1:-1].replace("''", "'")
        else:
            # Agent Skills uses YAML, where ordinary plain string scalars are
            # valid. Keep this parser dependency-free and fail closed on YAML
            # collections, tags, anchors, block scalars, comments, or controls.
            if scalar[0] in "-?:,[]{}#&*!|>'\"%@`" or " #" in scalar:
                raise ManagedCreationError("SKILL.md plain frontmatter scalar is ambiguous")
            if any(ord(char) < 32 for char in scalar):
                raise ManagedCreationError("SKILL.md frontmatter value contains control characters")
            value = scalar
        values[key] = value
    return values, "\n".join(lines[end + 1 :]).strip()


def validate_portable_candidate(candidate_root: Path, expected_skill_id: str) -> list[ValidationResult]:
    """G1/G2: validate Agent Skills shape and Merlin's stricter safety policy."""

    checks: list[ValidationResult] = []
    entries: list[str] = []
    expected_entries = [
        "SKILL.md",
        "agents",
        "agents/openai.yaml",
        "scripts",
        "scripts/run.py",
    ]
    try:
        if candidate_root.name != expected_skill_id:
            raise ManagedCreationError("candidate folder name differs from skill ID")
        entries = sorted(path.relative_to(candidate_root).as_posix() for path in candidate_root.rglob("*"))
        if entries != expected_entries:
            raise ManagedCreationError("candidate folder contains an unexpected file or directory")
        skill_md = (candidate_root / "SKILL.md").read_text(encoding="utf-8")
        if len(skill_md.splitlines()) > MAX_SKILL_MD_LINES:
            raise ManagedCreationError("SKILL.md exceeds progressive-disclosure line limit")
        frontmatter, body = _parse_frontmatter(skill_md)
        if set(frontmatter) != {"name", "description"}:
            raise ManagedCreationError("SKILL.md frontmatter must contain only name and description")
        if frontmatter["name"] != expected_skill_id or not SAFE_ID_RE.fullmatch(frontmatter["name"]):
            raise ManagedCreationError("SKILL.md name violates Agent Skills naming")
        if not 1 <= len(frontmatter["description"]) <= 1024:
            raise ManagedCreationError("SKILL.md description length is invalid")
        if not body:
            raise ManagedCreationError("SKILL.md body is empty")
        openai_yaml = (candidate_root / "agents" / "openai.yaml").read_text(encoding="utf-8")
        if "interface:\n" not in openai_yaml or f"${expected_skill_id}" not in openai_yaml:
            raise ManagedCreationError("agents/openai.yaml interface metadata is incomplete")
    except (ManagedCreationError, OSError, UnicodeError) as exc:
        checks.append(ValidationResult("G1_format", False, evidence=str(exc)))
    else:
        checks.append(
            ValidationResult(
                "G1_format",
                True,
                evidence="portable SKILL.md, progressive disclosure, and OpenAI interface metadata passed",
            )
        )

    try:
        # Safety is intentionally independent from presentation metadata. A
        # missing interface wrapper or skill mention is a format defect, not
        # evidence that otherwise bounded code is unsafe.
        if entries != expected_entries:
            raise ManagedCreationError("candidate safety allowlist differs from the portable bundle")
        if any(path.is_symlink() for path in candidate_root.rglob("*")):
            raise ManagedCreationError("candidate folder cannot contain symlinks")
        script = (candidate_root / "scripts" / "run.py").read_text(encoding="utf-8")
        forbidden = (
            "subprocess",
            "socket",
            "requests",
            "urllib",
            "http://",
            "https://",
            "eval(",
            "exec(",
            "__import__",
            "shell=True",
        )
        hit = next((token for token in forbidden if token in script), None)
        if hit:
            raise ManagedCreationError(f"trusted script contains forbidden token: {hit}")
        compile(script, str(candidate_root / "scripts" / "run.py"), "exec")
    except (ManagedCreationError, OSError, SyntaxError, UnicodeError) as exc:
        checks.append(ValidationResult("G2_safety", False, evidence=str(exc)))
    else:
        checks.append(
            ValidationResult(
                "G2_safety",
                True,
                evidence="trusted registered operation only; no symlink, network, shell, or dynamic-code surface",
            )
        )
    return checks


def _folder_sha256(root: Path) -> str:
    records = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return _sha256_text(_canonical_json(records))


def _write_case_files(root: Path, entries: tuple[tuple[str, str], ...]) -> None:
    for relative, content in entries:
        path = root / _safe_relative_path(relative, label="case file")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _verify_expected(root: Path, entries: tuple[tuple[str, str], ...]) -> bool:
    return bool(entries) and all(
        (root / relative).is_file() and (root / relative).read_text(encoding="utf-8") == expected
        for relative, expected in entries
    )


def _manifest(root: Path) -> tuple[str, ...]:
    return tuple(sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()))


def _run_case(
    case: CreationCase,
    *,
    candidate: SkillArtifact,
    candidate_root: Path,
    existing_skills: tuple[SkillArtifact, ...],
) -> CaseOutcome:
    provisional_candidate = copy.deepcopy(candidate)
    provisional_candidate.status = LifecycleStatus.ACTIVE
    decision = GovernedProvisioner(exposure_budget=1).decide(
        case.prompt,
        (*existing_skills, provisional_candidate),
    )
    candidate_primary = decision.primary_id == candidate.id
    if not case.should_trigger:
        return CaseOutcome(
            case_id=case.id,
            split=case.split,
            should_trigger=False,
            candidate_primary=candidate_primary,
            baseline_passed=None,
            candidate_passed=None,
            off_task_files=(),
            latency_s=None,
            stderr_sha256=None,
        )
    with tempfile.TemporaryDirectory(prefix=f"merlin-creation-{case.id}-") as temporary:
        root = Path(temporary)
        _write_case_files(root, case.input_files)
        before = set(_manifest(root))
        baseline_passed = _verify_expected(root, case.expected_files)
        started = time.monotonic()
        process = subprocess.run(
            [
                sys.executable,
                "-I",
                str(candidate_root / "scripts" / "run.py"),
                "--workspace",
                str(root),
            ],
            cwd=root,
            env={"PYTHONHASHSEED": "0", "LANG": "C", "LC_ALL": "C"},
            text=True,
            capture_output=True,
            timeout=MAX_SCRIPT_SECONDS,
            check=False,
        )
        latency = time.monotonic() - started
        candidate_passed = process.returncode == 0 and _verify_expected(root, case.expected_files)
        after = set(_manifest(root))
        expected_outputs = {path for path, _content in case.expected_files}
        off_task = tuple(sorted((after - before) - expected_outputs))
        return CaseOutcome(
            case_id=case.id,
            split=case.split,
            should_trigger=True,
            candidate_primary=candidate_primary,
            baseline_passed=baseline_passed,
            candidate_passed=candidate_passed,
            off_task_files=off_task,
            latency_s=latency,
            stderr_sha256=_sha256_text(process.stderr),
        )


def _gate_dict(result: ValidationResult) -> dict[str, Any]:
    return {
        "name": result.name,
        "passed": result.passed,
        "score": result.score,
        "evidence": result.evidence,
    }


def _write_rejection_report(
    output_root: Path,
    *,
    proposal: ManagedSkillProposal,
    original_snapshot_sha256: str,
    phase: str,
    gates: list[ValidationResult],
    reason: str,
) -> None:
    """Preserve a machine-readable rejection without mutating the live library."""

    report = {
        "schema_version": 1,
        "proposal_id": proposal.proposal_id,
        "candidate_skill_id": proposal.candidate_skill_id,
        "adopted": False,
        "lifecycle_action": LifecycleAction.REJECT.value,
        "phase": phase,
        "reason": reason,
        "original_library_snapshot_sha256": original_snapshot_sha256,
        "provisional_library_snapshot_sha256": None,
        "gates": [_gate_dict(gate) for gate in gates],
        "evidence_boundary": {
            "candidate_executed": False,
            "provider_native_loaded_or_invoked": False,
            "actual_invocation_evidence_complete": False,
            "adopted": False,
            "model_quality_claim": False,
        },
    }
    (output_root / "managed_creation_rejection.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_managed_creation(
    *,
    proposal: ManagedSkillProposal,
    draft: ManagedSkillDraft,
    existing_skills: tuple[SkillArtifact, ...],
    output_root: Path,
    external_validator: ExternalValidator | None = None,
) -> ManagedCreationResult:
    """Run G0-G6 and preserve the candidate plus copy-on-write evidence."""

    output_root = output_root.expanduser().resolve(strict=False)
    if output_root.exists():
        raise ManagedCreationError(f"refusing to overwrite creation output: {output_root}")
    output_root.mkdir(parents=True)
    candidate_root = output_root / "candidate" / draft.skill_id
    original_snapshot = active_library_snapshot(existing_skills)[1]
    (output_root / "proposal.json").write_text(
        json.dumps(asdict(proposal), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    gates = validate_proposal(proposal, existing_skills) + validate_draft(proposal, draft)
    if not all_passed(gates):
        reason = "G0 need or draft schema gate failed before candidate compilation"
        _write_rejection_report(
            output_root,
            proposal=proposal,
            original_snapshot_sha256=original_snapshot,
            phase="preflight",
            gates=gates,
            reason=reason,
        )
        raise ManagedCreationError(reason)
    try:
        candidate = compile_candidate(proposal, draft, candidate_root)
    except Exception as exc:
        _write_rejection_report(
            output_root,
            proposal=proposal,
            original_snapshot_sha256=original_snapshot,
            phase="candidate_compilation",
            gates=gates,
            reason=str(exc),
        )
        raise
    gates.extend(validate_portable_candidate(candidate_root, draft.skill_id))
    external = {
        "name": f"skills-ref-{SKILLS_REF_VERSION}",
        "status": "not_run_optional_cross_check",
        "passed": None,
        "evidence": "built-in offline Agent Skills format gate is authoritative for this run",
    }
    if external_validator is not None:
        try:
            external_result = external_validator(candidate_root)
        except Exception as exc:
            _write_rejection_report(
                output_root,
                proposal=proposal,
                original_snapshot_sha256=original_snapshot,
                phase="external_validation",
                gates=gates,
                reason=str(exc),
            )
            raise
        external = {
            "name": external_result.name,
            "status": "completed",
            "passed": external_result.passed,
            "evidence": external_result.evidence,
        }
        gates.append(
            ValidationResult(
                "G1_external_skills_ref",
                external_result.passed,
                evidence=external_result.evidence,
            )
        )
    if not all_passed(gates):
        reason = "candidate format or safety gate failed before execution"
        _write_rejection_report(
            output_root,
            proposal=proposal,
            original_snapshot_sha256=original_snapshot,
            phase="portable_validation",
            gates=gates,
            reason=reason,
        )
        raise ManagedCreationError(reason)
    outcomes = tuple(
        _run_case(
            case,
            candidate=candidate,
            candidate_root=candidate_root,
            existing_skills=existing_skills,
        )
        for case in proposal.cases
    )
    positives = [outcome for outcome in outcomes if outcome.should_trigger]
    negatives = [outcome for outcome in outcomes if not outcome.should_trigger]
    trigger_total = len(positives) + len(negatives)
    trigger_correct = sum(outcome.candidate_primary for outcome in positives) + sum(
        not outcome.candidate_primary for outcome in negatives
    )
    trigger_passed = trigger_total > 0 and trigger_correct == trigger_total
    gates.append(
        ValidationResult(
            "G3_trigger",
            trigger_passed,
            score=trigger_correct / trigger_total if trigger_total else 0.0,
            evidence=f"{trigger_correct}/{trigger_total} positive/negative frozen trigger cases correct",
        )
    )
    target = [outcome for outcome in outcomes if outcome.split == "target"]
    held_out = [outcome for outcome in outcomes if outcome.split == "held_out"]
    baseline_target_passed = sum(outcome.baseline_passed is True for outcome in target)
    candidate_target_passed = sum(outcome.candidate_passed is True for outcome in target)
    baseline_rate = baseline_target_passed / len(target)
    candidate_rate = candidate_target_passed / len(target)
    normalized_gain = (
        (candidate_rate - baseline_rate) / (1.0 - baseline_rate)
        if baseline_rate < 1.0
        else None
    )
    target_gate = ValidationResult(
        "G4_target",
        bool(target)
        and candidate_target_passed == len(target)
        and candidate_rate >= baseline_rate
        and candidate_rate > baseline_rate,
        score=candidate_rate - baseline_rate,
        evidence=(
            f"same frozen verifier: baseline={baseline_target_passed}/{len(target)}, "
            f"candidate={candidate_target_passed}/{len(target)}"
        ),
    )
    gates.append(target_gate)
    held_out_passed = bool(held_out) and all(
        outcome.candidate_primary
        and outcome.candidate_passed is True
        and not outcome.off_task_files
        and outcome.latency_s is not None
        and outcome.latency_s <= MAX_SCRIPT_SECONDS
        for outcome in held_out
    )
    regression_gate = ValidationResult(
        "G5_regression",
        held_out_passed and all(not outcome.candidate_primary for outcome in negatives),
        evidence=(
            f"held_out={sum(outcome.candidate_passed is True for outcome in held_out)}/{len(held_out)}; "
            f"negative_abstain={sum(not outcome.candidate_primary for outcome in negatives)}/{len(negatives)}; "
            "no off-task artifacts"
        ),
    )
    gates.append(regression_gate)
    structure_results = validate_aip_lite_skill(candidate)
    lifecycle = decide_candidate_lifecycle(
        candidate,
        structure_results,
        [target_gate, gates[-3]],
        [regression_gate],
    )
    pre_adoption = all_passed(gates) and lifecycle.action == LifecycleAction.ADOPT
    if pre_adoption:
        provisional, _change = stage_provisional_lifecycle_change(
            [*existing_skills, candidate],
            [lifecycle],
        )
        original_statuses = {skill.id: skill.status for skill in existing_skills}
        preserved = all(
            next(item for item in provisional if item.id == skill_id).status == status
            for skill_id, status in original_statuses.items()
        )
        adopted_candidate = next(item for item in provisional if item.id == candidate.id)
        g6_passed = preserved and adopted_candidate.status == LifecycleStatus.ACTIVE
    else:
        provisional = [*copy.deepcopy(existing_skills), copy.deepcopy(candidate)]
        g6_passed = False
    gates.append(
        ValidationResult(
            "G6_adoption",
            g6_passed,
            evidence=(
                "copy-on-write candidate promoted; existing statuses preserved"
                if g6_passed
                else "candidate retained outside the live library"
            ),
        )
    )
    adopted = all_passed(gates)
    resolved = provisional if adopted else [*copy.deepcopy(existing_skills), copy.deepcopy(candidate)]
    if not adopted:
        next(item for item in resolved if item.id == candidate.id).status = LifecycleStatus.REJECTED
    provisional_snapshot = active_library_snapshot(resolved)[1] if adopted else None
    library_root = output_root / "provisional-library"
    library_root.mkdir()
    for skill in sorted(resolved, key=lambda item: item.id):
        (library_root / f"{skill.id}.json").write_text(
            json.dumps(skill.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    result = ManagedCreationResult(
        proposal_id=proposal.proposal_id,
        candidate_skill_id=candidate.id,
        adopted=adopted,
        lifecycle_action=LifecycleAction.ADOPT.value if adopted else LifecycleAction.REJECT.value,
        original_library_snapshot_sha256=original_snapshot,
        provisional_library_snapshot_sha256=provisional_snapshot,
        external_validator=external,
        gates=tuple(_gate_dict(gate) for gate in gates),
        case_outcomes=outcomes,
        baseline_target_pass_rate=baseline_rate,
        candidate_target_pass_rate=candidate_rate,
        normalized_gain=normalized_gain,
        candidate_folder_sha256=_folder_sha256(candidate_root),
        resolved_library_statuses={skill.id: skill.status.value for skill in resolved},
        evidence_boundary={
            "generated": True,
            "provisioned": trigger_passed,
            "selected": trigger_passed,
            "selection_scope": "deterministic provisional-library routing on frozen cases",
            "provider_agent_selected": False,
            "provider_native_loaded_or_invoked": False,
            "actual_invocation_evidence_complete": False,
            "useful": target_gate.passed and regression_gate.passed,
            "adopted": adopted,
            "utility_scope": "trusted registered operation under deterministic file verifiers",
            "model_quality_claim": False,
        },
    )
    (output_root / "managed_creation_report.json").write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result
