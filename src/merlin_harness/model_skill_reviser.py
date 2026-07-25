"""Strict model-authored repair adapter for immutable skill bundles.

The reviser is intentionally narrower than model-authored creation.  It sees
one already quarantined bundle plus target-only verifier feedback, may change
only ``scripts/run.py``, and returns a single versioned ``SkillArtifact``.
Hidden and library-regression cases are never accepted by this interface.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, Sequence

from .model_candidate_generator import ModelCandidateGenerationResult
from .model_candidate_quarantine import (
    ModelCandidateQuarantineResult,
    quarantine_model_candidate,
)
from .models import LifecycleStatus, SkillArtifact
from .skill_repair import RepairCaseResult, RepairDiagnosis, SkillRepairError


REPAIRABLE_PATH = "scripts/run.py"
REQUIRED_BUNDLE_PATHS = frozenset(
    {"SKILL.md", "agents/openai.yaml", REPAIRABLE_PATH}
)


class ModelBundleGenerator(Protocol):
    def generate(
        self, *, candidate_skill_id: str, prompt: str, run_root: Path
    ) -> ModelCandidateGenerationResult:
        """Return one strict full-bundle model response."""


@dataclass(frozen=True, slots=True)
class ModelRepairBundleBinding:
    candidate_key: str
    version: int
    quarantine_root: Path
    quarantine_manifest_sha256: str
    requested_model_id: str
    provider_reported_model_ids: tuple[str, ...]
    model_evidence_level: str
    effort: str
    raw_trace_sha256: str
    response_sha256: str
    prompt_sha256: str

    def to_safe_dict(self) -> dict[str, Any]:
        """Return hashes and claims only; never disclose the raw local root."""

        return {
            "candidate_key": self.candidate_key,
            "version": self.version,
            "quarantine_manifest_sha256": self.quarantine_manifest_sha256,
            "requested_model_id": self.requested_model_id,
            "provider_reported_model_ids": list(self.provider_reported_model_ids),
            "model_evidence_level": self.model_evidence_level,
            "effort": self.effort,
            "raw_trace_sha256": self.raw_trace_sha256,
            "response_sha256": self.response_sha256,
            "prompt_sha256": self.prompt_sha256,
            "evidence_boundary": {
                "provider_run_observed": True,
                "held_out_visible_to_reviser": False,
                "library_regression_visible_to_reviser": False,
                "only_script_body_change_allowed": True,
                "candidate_executed": False,
                "candidate_promoted": False,
            },
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_immutable_bundle(
    root: Path, *, expected_manifest_sha256: str, expected_skill_id: str
) -> dict[str, str]:
    root = root.expanduser().resolve(strict=True)
    manifest_path = root / "quarantine_manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise SkillRepairError("original repair bundle has no regular quarantine manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SkillRepairError("original repair bundle manifest is unreadable") from exc
    if not isinstance(manifest, dict):
        raise SkillRepairError("original repair bundle manifest must be an object")
    body = {
        key: value
        for key, value in manifest.items()
        if key not in {"schema_version", "manifest_sha256"}
    }
    if (
        manifest.get("manifest_sha256") != expected_manifest_sha256
        or _sha256_bytes(_canonical_json(body).encode("utf-8"))
        != expected_manifest_sha256
        or manifest.get("candidate_skill_id") != expected_skill_id
    ):
        raise SkillRepairError("original repair bundle identity or content hash drifted")
    records = manifest.get("files")
    if not isinstance(records, list):
        raise SkillRepairError("original repair bundle has no file records")
    candidate_root = root / "candidate" / expected_skill_id
    files: dict[str, str] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
            raise SkillRepairError("original repair bundle file record is invalid")
        path = PurePosixPath(record["path"])
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise SkillRepairError("original repair bundle contains an unsafe path")
        source = candidate_root.joinpath(*path.parts)
        if source.is_symlink() or not source.is_file():
            raise SkillRepairError("original repair bundle file is missing or linked")
        raw = source.read_bytes()
        if len(raw) != record["bytes"] or _sha256_bytes(raw) != record["sha256"]:
            raise SkillRepairError("original repair bundle file bytes drifted")
        try:
            files[path.as_posix()] = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillRepairError("original repair bundle file is not UTF-8") from exc
    actual = {
        path.relative_to(candidate_root).as_posix()
        for path in candidate_root.rglob("*")
        if path.is_file()
    }
    if set(files) != actual or set(files) != REQUIRED_BUNDLE_PATHS:
        raise SkillRepairError("model repair requires the exact portable three-file bundle")
    return files


def build_model_repair_prompt(
    *,
    original: SkillArtifact,
    original_files: dict[str, str],
    diagnosis: RepairDiagnosis,
    target_feedback: tuple[RepairCaseResult, ...],
    next_version: int,
) -> str:
    """Build a target-only prompt with an explicit information firewall."""

    visible = {
        "skill_id": diagnosis.skill_id,
        "next_version": next_version,
        "failed_target_case_ids": list(diagnosis.failed_target_case_ids),
        "verifier_feedback": list(diagnosis.verifier_feedback),
        "target_results": [asdict(item) for item in target_feedback],
    }
    files = [
        {"path": path, "content": original_files[path]}
        for path in sorted(original_files)
    ]
    return (
        "You are repairing one quarantined portable Agent Skill after a verified "
        "skill-local failure. Do not call tools, inspect the filesystem, or include "
        "commentary. Return only the JSON object required by the response schema.\n\n"
        f"Candidate identity: {original.id}\n"
        f"Target-only repair evidence:\n{json.dumps(visible, ensure_ascii=False, indent=2, sort_keys=True)}\n\n"
        "Information boundary:\n"
        "- No held-out case, expected held-out output, or library-regression result is provided.\n"
        "- Do not infer or mention hidden cases. Implement the general contract implied by the target feedback.\n\n"
        "Mutation boundary:\n"
        "- Return the complete bundle with exactly SKILL.md, agents/openai.yaml, and scripts/run.py.\n"
        "- SKILL.md and agents/openai.yaml must be byte-for-byte unchanged.\n"
        "- Only scripts/run.py may change. Preserve its CLI, output artifact, standard-library-only policy, path confinement, deterministic JSON formatting, and network prohibition.\n"
        "- Do not add imports outside argparse, json, re, and `from pathlib import Path`.\n\n"
        f"Frozen original bundle:\n{json.dumps(files, ensure_ascii=False, indent=2, sort_keys=True)}\n"
    )


class CodexModelSkillReviser:
    """One-candidate model reviser compatible with ``run_skill_repair``."""

    def __init__(
        self,
        *,
        generator: ModelBundleGenerator,
        run_root: Path,
        original_quarantine_root: Path,
        original_manifest_sha256: str,
    ) -> None:
        self.generator = generator
        self.run_root = run_root.expanduser().resolve(strict=False)
        self.original_quarantine_root = original_quarantine_root
        self.original_manifest_sha256 = original_manifest_sha256
        self.bindings: dict[str, ModelRepairBundleBinding] = {}

    def propose(
        self,
        original: SkillArtifact,
        diagnosis: RepairDiagnosis,
        target_feedback: tuple[RepairCaseResult, ...],
        max_candidates: int,
    ) -> Sequence[SkillArtifact]:
        if max_candidates < 1:
            raise SkillRepairError("model repair requires at least one candidate slot")
        if self.run_root.exists():
            raise SkillRepairError("refusing to overwrite model repair run root")
        original_files = _read_immutable_bundle(
            self.original_quarantine_root,
            expected_manifest_sha256=self.original_manifest_sha256,
            expected_skill_id=original.id,
        )
        next_version = original.version + 1
        prompt = build_model_repair_prompt(
            original=original,
            original_files=original_files,
            diagnosis=diagnosis,
            target_feedback=target_feedback,
            next_version=next_version,
        )
        self.run_root.mkdir(parents=True)
        generation = self.generator.generate(
            candidate_skill_id=original.id,
            prompt=prompt,
            run_root=self.run_root / "generator",
        )
        proposed_files = {item.path: item.content for item in generation.envelope.files}
        if set(proposed_files) != REQUIRED_BUNDLE_PATHS:
            raise SkillRepairError("model repair changed the frozen bundle file set")
        for path in REQUIRED_BUNDLE_PATHS - {REPAIRABLE_PATH}:
            if proposed_files[path] != original_files[path]:
                raise SkillRepairError(
                    f"model repair changed immutable routing/interface file: {path}"
                )
        if proposed_files[REPAIRABLE_PATH] == original_files[REPAIRABLE_PATH]:
            raise SkillRepairError("model repair returned an unchanged implementation")
        quarantine: ModelCandidateQuarantineResult = quarantine_model_candidate(
            envelope=generation.envelope,
            output_root=self.run_root / "quarantine",
        )

        candidate = copy.deepcopy(original)
        candidate.version = next_version
        candidate.status = LifecycleStatus.CANDIDATE
        candidate.metadata = {
            **candidate.metadata,
            "repair_generator_backend": generation.envelope.generator_backend,
            "repair_requested_model": generation.requested_model_id,
            "repair_model_evidence_level": generation.model_evidence_level,
            "repair_effort": generation.effort,
            "repair_prompt_sha256": generation.prompt_sha256,
            "repair_response_sha256": generation.response_sha256,
            "repair_raw_trace_sha256": generation.raw_trace_sha256,
            "repair_quarantine_manifest_sha256": quarantine.manifest_sha256,
            "repair_mutation_scope": REPAIRABLE_PATH,
        }
        key = f"{candidate.id}@v{candidate.version}"
        self.bindings[key] = ModelRepairBundleBinding(
            candidate_key=key,
            version=candidate.version,
            quarantine_root=self.run_root / "quarantine",
            quarantine_manifest_sha256=quarantine.manifest_sha256,
            requested_model_id=generation.requested_model_id,
            provider_reported_model_ids=generation.provider_reported_model_ids,
            model_evidence_level=generation.model_evidence_level,
            effort=generation.effort,
            raw_trace_sha256=generation.raw_trace_sha256,
            response_sha256=generation.response_sha256,
            prompt_sha256=generation.prompt_sha256,
        )
        return (candidate,)
