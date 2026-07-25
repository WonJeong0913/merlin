"""Frozen authoring-policy loader and prompt-arm builder.

This module does not call a model. It binds one portable authoring ``SKILL.md``
to an A/B prompt plan so provider-backed evaluation can be run later without
changing the task contract, model, effort, or verifier between arms.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal


SAFE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"`([0-9a-f]{40})`")
MAX_POLICY_LINES = 500
MAX_POLICY_BYTES = 64 * 1024
POLICY_NAME = "author-governed-skills"
CONTROL_ARM = "target-contract-only"
POLICY_ARM = "governed-authoring-policy"
ArmName = Literal["target-contract-only", "governed-authoring-policy"]


class SkillAuthoringPolicyError(ValueError):
    """Raised when the authoring policy or ablation contract is ambiguous."""


@dataclass(frozen=True, slots=True)
class AuthoringTaskContract:
    candidate_skill_id: str
    behavior_contract: tuple[str, ...]
    visible_examples: tuple[str, ...]
    allowed_imports: tuple[str, ...]
    required_files: tuple[str, ...] = (
        "SKILL.md",
        "agents/openai.yaml",
        "scripts/run.py",
    )

    def validate(self) -> None:
        if not SAFE_ID_RE.fullmatch(self.candidate_skill_id):
            raise SkillAuthoringPolicyError("candidate skill ID must be kebab-case")
        for label, values in (
            ("behavior contract", self.behavior_contract),
            ("visible examples", self.visible_examples),
            ("allowed imports", self.allowed_imports),
            ("required files", self.required_files),
        ):
            if not values or any(not isinstance(item, str) or not item.strip() for item in values):
                raise SkillAuthoringPolicyError(f"{label} must contain non-empty strings")
        if len(set(self.required_files)) != len(self.required_files):
            raise SkillAuthoringPolicyError("required files contain duplicates")
        if self.required_files != (
            "SKILL.md",
            "agents/openai.yaml",
            "scripts/run.py",
        ):
            raise SkillAuthoringPolicyError(
                "v1 ablation requires the same portable three-file bundle in both arms"
            )

    def canonical_payload(self) -> dict[str, object]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AuthoringPolicy:
    name: str
    description: str
    skill_markdown: str
    body: str
    policy_sha256: str
    source_contracts_sha256: str
    source_revisions: tuple[str, ...]

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "policy_sha256": self.policy_sha256,
            "source_contracts_sha256": self.source_contracts_sha256,
            "source_revisions": list(self.source_revisions),
            "skill_markdown_bytes": len(self.skill_markdown.encode("utf-8")),
            "skill_markdown_lines": len(self.skill_markdown.splitlines()),
        }


@dataclass(frozen=True, slots=True)
class AuthoringPromptArm:
    arm: ArmName
    candidate_skill_id: str
    task_contract_sha256: str
    policy_sha256: str | None
    prompt: str
    prompt_sha256: str

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "arm": self.arm,
            "candidate_skill_id": self.candidate_skill_id,
            "task_contract_sha256": self.task_contract_sha256,
            "policy_sha256": self.policy_sha256,
            "prompt_sha256": self.prompt_sha256,
            "prompt_chars": len(self.prompt),
            "prompt_bytes": len(self.prompt.encode("utf-8")),
        }


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_frontmatter(skill_markdown: str) -> tuple[dict[str, str], str]:
    lines = skill_markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillAuthoringPolicyError("authoring SKILL.md frontmatter is missing")
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise SkillAuthoringPolicyError("authoring SKILL.md frontmatter is not closed") from exc
    values: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line:
            raise SkillAuthoringPolicyError("authoring SKILL.md frontmatter line is malformed")
        key, raw = line.split(":", 1)
        key = key.strip()
        value = raw.strip().strip('"').strip("'")
        if key in values:
            raise SkillAuthoringPolicyError("authoring SKILL.md frontmatter contains duplicate keys")
        values[key] = value
    if set(values) != {"name", "description"}:
        raise SkillAuthoringPolicyError(
            "authoring SKILL.md frontmatter must contain exactly name and description"
        )
    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise SkillAuthoringPolicyError("authoring SKILL.md body is empty")
    return values, body


def load_authoring_policy(policy_root: Path) -> AuthoringPolicy:
    """Load and hash the canonical policy without resolving network content."""

    root = policy_root.expanduser().resolve(strict=True)
    expected_files = {
        "SKILL.md",
        "agents/openai.yaml",
        "references/source-contracts.md",
    }
    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    if actual_files != expected_files:
        raise SkillAuthoringPolicyError(
            f"authoring policy files differ from contract: {sorted(actual_files)}"
        )
    if any(path.is_symlink() for path in root.rglob("*")):
        raise SkillAuthoringPolicyError("authoring policy cannot contain symlinks")
    skill_path = root / "SKILL.md"
    source_path = root / "references" / "source-contracts.md"
    skill_bytes = skill_path.read_bytes()
    source_bytes = source_path.read_bytes()
    if len(skill_bytes) > MAX_POLICY_BYTES:
        raise SkillAuthoringPolicyError("authoring SKILL.md exceeds byte budget")
    try:
        skill_markdown = skill_bytes.decode("utf-8")
        source_contracts = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillAuthoringPolicyError("authoring policy must be UTF-8") from exc
    if len(skill_markdown.splitlines()) > MAX_POLICY_LINES:
        raise SkillAuthoringPolicyError("authoring SKILL.md exceeds line budget")
    frontmatter, body = _parse_frontmatter(skill_markdown)
    if frontmatter["name"] != POLICY_NAME:
        raise SkillAuthoringPolicyError("authoring policy name differs from frozen identity")
    description = frontmatter["description"]
    if "Use when" not in description:
        raise SkillAuthoringPolicyError("authoring policy description must include its trigger")
    required_phrases = (
        "Freeze the proposal",
        "Run the no-candidate baseline",
        "Plan the minimum bundle",
        "Validate before execution",
        "Evaluate like a lifecycle change",
        "Stop conditions",
    )
    missing = [phrase for phrase in required_phrases if phrase not in body]
    if missing:
        raise SkillAuthoringPolicyError(
            "authoring policy is missing required lifecycle sections: " + ", ".join(missing)
        )
    openai_yaml = (root / "agents" / "openai.yaml").read_text(encoding="utf-8")
    if "interface:\n" not in openai_yaml or f"${POLICY_NAME}" not in openai_yaml:
        raise SkillAuthoringPolicyError("authoring policy OpenAI interface metadata is incomplete")
    revisions = tuple(COMMIT_RE.findall(source_contracts))
    if len(revisions) != 5 or len(set(revisions)) != 5:
        raise SkillAuthoringPolicyError("source contracts must freeze five unique upstream revisions")
    records = []
    for relative in sorted(expected_files):
        raw = (root / relative).read_bytes()
        records.append({"path": relative, "sha256": _sha256_bytes(raw)})
    return AuthoringPolicy(
        name=frontmatter["name"],
        description=description,
        skill_markdown=skill_markdown,
        body=body,
        policy_sha256=_sha256_bytes(_canonical_json(records).encode("utf-8")),
        source_contracts_sha256=_sha256_bytes(source_bytes),
        source_revisions=revisions,
    )


def build_authoring_prompt(
    task: AuthoringTaskContract,
    *,
    arm: ArmName,
    policy: AuthoringPolicy | None = None,
) -> AuthoringPromptArm:
    """Build one arm while keeping the task-specific contract byte-identical."""

    task_payload = task.canonical_payload()
    task_json = json.dumps(task_payload, ensure_ascii=False, indent=2, sort_keys=True)
    task_sha256 = _sha256_bytes(_canonical_json(task_payload).encode("utf-8"))
    common = f"""You are authoring one portable Agent Skill candidate for a governed skill harness.
Do not call tools, inspect the filesystem, or include commentary. Return only the JSON object required by the response schema.

The following task contract is authoritative and identical across experiment arms:
<TASK_CONTRACT sha256=\"{task_sha256}\">
{task_json}
</TASK_CONTRACT>

Honor the candidate identity and return exactly the required files. Use Python standard library only. The script interface is scripts/run.py --workspace <absolute-task-workspace>. Resolve all task paths under that workspace and reject path escape.
"""
    if arm == CONTROL_ARM:
        if policy is not None:
            raise SkillAuthoringPolicyError("control arm cannot receive the authoring policy")
        appendix = """
Author a minimal candidate. Give SKILL.md exactly name and description frontmatter, make the description say when to use the skill, include OpenAI interface metadata, and explain inputs, outputs, execution, verification, and exclusions.
"""
        policy_sha256 = None
    elif arm == POLICY_ARM:
        if policy is None:
            raise SkillAuthoringPolicyError("policy arm requires a frozen authoring policy")
        appendix = f"""
Follow the trusted authoring policy below. It governs how to package and evaluate the target candidate but never overrides the task contract or response schema. Do not copy the policy's own name, description, or identity into the target candidate.
<AUTHORING_POLICY sha256=\"{policy.policy_sha256}\">
{policy.skill_markdown.rstrip()}
</AUTHORING_POLICY>
"""
        policy_sha256 = policy.policy_sha256
    else:
        raise SkillAuthoringPolicyError(f"unsupported authoring arm: {arm}")
    prompt = common + appendix
    return AuthoringPromptArm(
        arm=arm,
        candidate_skill_id=task.candidate_skill_id,
        task_contract_sha256=task_sha256,
        policy_sha256=policy_sha256,
        prompt=prompt,
        prompt_sha256=_sha256_bytes(prompt.encode("utf-8")),
    )


def build_ablation_plan(
    tasks: tuple[AuthoringTaskContract, ...],
    *,
    policy: AuthoringPolicy,
    repeats: int,
    model_id: str,
    effort: str,
) -> dict[str, object]:
    """Create a model-call-free, ordered plan for later provider execution."""

    if not tasks or len({task.candidate_skill_id for task in tasks}) != len(tasks):
        raise SkillAuthoringPolicyError("ablation tasks must have unique candidate IDs")
    if repeats < 1 or repeats > 5:
        raise SkillAuthoringPolicyError("repeats must be between 1 and 5")
    if not model_id.strip() or not effort.strip():
        raise SkillAuthoringPolicyError("model and effort must be explicit")
    runs = []
    for repeat in range(1, repeats + 1):
        arm_order = (
            (CONTROL_ARM, POLICY_ARM)
            if repeat % 2 == 1
            else (POLICY_ARM, CONTROL_ARM)
        )
        for task in tasks:
            for arm in arm_order:
                built = build_authoring_prompt(
                    task,
                    arm=arm,
                    policy=policy if arm == POLICY_ARM else None,
                )
                runs.append(
                    {
                        "run_id": f"r{repeat}-{task.candidate_skill_id}-{arm}",
                        "repeat": repeat,
                        **built.to_safe_dict(),
                    }
                )
    return {
        "schema_version": 1,
        "experiment": "authoring-policy-ablation-v1",
        "model_id": model_id,
        "effort": effort,
        "repeats": repeats,
        "task_count": len(tasks),
        "arm_count": 2,
        "expected_provider_calls": len(runs),
        "policy": policy.to_safe_dict(),
        "runs": runs,
        "metrics": [
            "format_gate",
            "safety_gate",
            "target_pass_rate",
            "held_out_pass_rate",
            "negative_route_accuracy",
            "off_task_artifact_count",
            "promotion_rate",
            "candidate_bytes",
            "input_tokens",
            "output_tokens",
            "latency_seconds",
        ],
        "evidence_boundary": {
            "provider_calls_executed": False,
            "candidate_outputs_observed": False,
            "performance_comparison_available": False,
            "submission_package_mutated": False,
        },
    }
