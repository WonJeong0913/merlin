"""New-only quarantine for model-authored multi-file skill candidates."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .models import ValidationResult


SAFE_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
MAX_CANDIDATE_FILES = 32
MAX_FILE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 256 * 1024
MAX_MODEL_RESPONSE_BYTES = 384 * 1024
ALLOWED_REFERENCE_SUFFIXES = {".md", ".txt", ".json"}
BANNED_IMPORT_ROOTS = {
    "_posixsubprocess",
    "asyncio",
    "builtins",
    "concurrent",
    "ctypes",
    "http",
    "importlib",
    "marshal",
    "multiprocessing",
    "os",
    "pickle",
    "posix",
    "pty",
    "requests",
    "resource",
    "shutil",
    "signal",
    "socket",
    "subprocess",
    "sys",
    "threading",
    "urllib",
}
BANNED_CALL_NAMES = {
    "__import__",
    "compile",
    "dir",
    "eval",
    "exec",
    "getattr",
    "globals",
    "hasattr",
    "locals",
    "open",
    "setattr",
    "vars",
}
BANNED_ATTRIBUTE_CALLS = {
    "connect",
    "execv",
    "execve",
    "fork",
    "forkpty",
    "kill",
    "killpg",
    "popen",
    "setsid",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
    "system",
    "urlopen",
}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~-]{20,}\b", re.IGNORECASE),
)


class ModelCandidateQuarantineError(ValueError):
    """Raised before any unsafe or ambiguous model artifact is accepted."""


@dataclass(frozen=True, slots=True)
class ModelCandidateFile:
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class ModelCandidateEnvelope:
    candidate_skill_id: str
    generator_backend: str
    generator_model: str
    generator_effort: str
    generator_prompt_sha256: str
    generator_response_sha256: str
    files: tuple[ModelCandidateFile, ...]
    generator_provider_reported_model_ids: tuple[str, ...] = ()
    generator_cli_version: str | None = None
    generator_raw_trace_sha256: str | None = None
    generator_thread_id: str | None = None
    generator_turn_id: str | None = None


@dataclass(frozen=True, slots=True)
class QuarantinedFileRecord:
    path: str
    bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ModelCandidateQuarantineResult:
    candidate_skill_id: str
    quarantine_root: str
    manifest_sha256: str
    files: tuple[QuarantinedFileRecord, ...]
    gates: tuple[ValidationResult, ...]
    execution_allowed: bool = False
    promotion_allowed: bool = False
    lifecycle_status: str = "quarantined"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "candidate_skill_id": self.candidate_skill_id,
            "quarantine_root": self.quarantine_root,
            "manifest_sha256": self.manifest_sha256,
            "files": [asdict(item) for item in self.files],
            "gates": [asdict(item) for item in self.gates],
            "execution_allowed": self.execution_allowed,
            "promotion_allowed": self.promotion_allowed,
            "lifecycle_status": self.lifecycle_status,
            "evidence_boundary": {
                "model_authored_artifact_received": True,
                "host_execution": False,
                "isolated_execution": False,
                "target_verifier_passed": False,
                "held_out_verifier_passed": False,
                "provider_native_invocation": False,
                "adopted": False,
            },
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_candidate_path(raw: str) -> PurePosixPath:
    if not raw or "\x00" in raw or "\\" in raw:
        raise ModelCandidateQuarantineError("candidate file path is empty or non-portable")
    path = PurePosixPath(raw)
    if path.is_absolute() or raw != path.as_posix() or any(part in {"", ".", ".."} for part in path.parts):
        raise ModelCandidateQuarantineError(f"unsafe candidate file path: {raw!r}")
    if any(part.startswith(".") for part in path.parts):
        raise ModelCandidateQuarantineError(f"hidden candidate path is not allowed: {raw!r}")
    if raw == "SKILL.md":
        return path
    if raw == "agents/openai.yaml":
        return path
    if len(path.parts) == 2 and path.parts[0] == "scripts" and path.suffix == ".py":
        return path
    if (
        len(path.parts) == 2
        and path.parts[0] == "references"
        and path.suffix in ALLOWED_REFERENCE_SUFFIXES
    ):
        return path
    raise ModelCandidateQuarantineError(f"candidate path is outside the portable allowlist: {raw!r}")


def _frontmatter_name(skill_md: str) -> str:
    lines = skill_md.splitlines()
    if len(lines) < 4 or lines[0].strip() != "---":
        raise ModelCandidateQuarantineError("SKILL.md requires YAML frontmatter")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise ModelCandidateQuarantineError("SKILL.md frontmatter is not closed") from exc
    values: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line:
            raise ModelCandidateQuarantineError("SKILL.md frontmatter line is malformed")
        key, raw = line.split(":", 1)
        value = raw.strip().strip("\"").strip("'")
        if key.strip() in values:
            raise ModelCandidateQuarantineError("SKILL.md frontmatter contains duplicate keys")
        values[key.strip()] = value
    if set(values) != {"name", "description"}:
        raise ModelCandidateQuarantineError(
            "SKILL.md frontmatter must contain exactly name and description"
        )
    if not values["description"].strip() or not any(
        token in values["description"].lower() for token in ("use ", "when ")
    ):
        raise ModelCandidateQuarantineError("SKILL.md description must say when to use the skill")
    if not "\n".join(lines[end + 1 :]).strip():
        raise ModelCandidateQuarantineError("SKILL.md body is empty")
    return values["name"]


def _yaml_string_scalar(raw: str, *, label: str) -> str:
    scalar = raw.strip()
    if not scalar:
        raise ModelCandidateQuarantineError(f"{label} must be a non-empty string")
    if scalar.startswith('"'):
        try:
            value = json.loads(scalar)
        except json.JSONDecodeError as exc:
            raise ModelCandidateQuarantineError(f"{label} has malformed quoting") from exc
        if not isinstance(value, str):
            raise ModelCandidateQuarantineError(f"{label} must be a string")
        return value
    if scalar.startswith("'"):
        if len(scalar) < 2 or not scalar.endswith("'"):
            raise ModelCandidateQuarantineError(f"{label} has malformed quoting")
        return scalar[1:-1].replace("''", "'")
    if scalar[0] in "-?:,[]{}#&*!|>'\"%@`" or " #" in scalar:
        raise ModelCandidateQuarantineError(f"{label} has an ambiguous YAML scalar")
    if any(ord(char) < 32 for char in scalar):
        raise ModelCandidateQuarantineError(f"{label} contains control characters")
    return scalar


def _validate_openai_interface(content: str, candidate_skill_id: str) -> None:
    """Validate the minimal OpenAI skill interface without a YAML dependency."""

    lines = content.splitlines()
    if not lines or lines[0] != "interface:":
        raise ModelCandidateQuarantineError(
            "agents/openai.yaml requires one top-level interface mapping"
        )
    values: dict[str, str] = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        if not line.startswith("  ") or line.startswith("   ") or ":" not in line[2:]:
            raise ModelCandidateQuarantineError(
                "agents/openai.yaml interface fields must use two-space indentation"
            )
        key, raw = line[2:].split(":", 1)
        if key in values:
            raise ModelCandidateQuarantineError(
                f"agents/openai.yaml contains duplicate interface field: {key}"
            )
        values[key] = _yaml_string_scalar(
            raw,
            label=f"agents/openai.yaml interface.{key}",
        )
    required = {"display_name", "short_description", "default_prompt"}
    if set(values) != required:
        raise ModelCandidateQuarantineError(
            "agents/openai.yaml interface must contain exactly display_name, short_description, and default_prompt"
        )
    if f"${candidate_skill_id}" not in values["default_prompt"]:
        raise ModelCandidateQuarantineError(
            "agents/openai.yaml default_prompt must explicitly mention the candidate skill"
        )


def _validate_python_script(path: str, content: str) -> None:
    try:
        tree = ast.parse(content, filename=path)
    except SyntaxError as exc:
        raise ModelCandidateQuarantineError(f"candidate script is invalid Python: {path}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".", 1)[0] for alias in node.names}
            banned = roots & BANNED_IMPORT_ROOTS
            if banned:
                raise ModelCandidateQuarantineError(
                    f"candidate script imports quarantined modules: {path}: {sorted(banned)}"
                )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root in BANNED_IMPORT_ROOTS:
                raise ModelCandidateQuarantineError(
                    f"candidate script imports quarantined module: {path}: {root}"
                )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in BANNED_CALL_NAMES:
                raise ModelCandidateQuarantineError(
                    f"candidate script uses quarantined call: {path}: {node.func.id}"
                )
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ModelCandidateQuarantineError(
                f"candidate script uses private or dunder attribute: {path}: {node.attr}"
            )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in BANNED_ATTRIBUTE_CALLS:
                raise ModelCandidateQuarantineError(
                    f"candidate script uses quarantined attribute call: {path}: {node.func.attr}"
                )


def _validate_envelope(envelope: ModelCandidateEnvelope) -> tuple[ValidationResult, ...]:
    if not SAFE_ID_RE.fullmatch(envelope.candidate_skill_id):
        raise ModelCandidateQuarantineError("candidate skill ID is not portable kebab-case")
    for label, value in (
        ("generator_backend", envelope.generator_backend),
        ("generator_model", envelope.generator_model),
        ("generator_effort", envelope.generator_effort),
    ):
        if not value.strip() or len(value) > 200 or "\x00" in value:
            raise ModelCandidateQuarantineError(f"{label} is invalid")
    if not SHA256_RE.fullmatch(envelope.generator_prompt_sha256):
        raise ModelCandidateQuarantineError("generator prompt SHA-256 is invalid")
    if not SHA256_RE.fullmatch(envelope.generator_response_sha256):
        raise ModelCandidateQuarantineError("generator response SHA-256 is invalid")
    if envelope.generator_raw_trace_sha256 is not None and not SHA256_RE.fullmatch(
        envelope.generator_raw_trace_sha256
    ):
        raise ModelCandidateQuarantineError("generator raw trace SHA-256 is invalid")
    for model_id in envelope.generator_provider_reported_model_ids:
        if not model_id.strip() or len(model_id) > 200 or "\x00" in model_id:
            raise ModelCandidateQuarantineError("provider-reported generator model ID is invalid")
    for label, value in (
        ("generator_cli_version", envelope.generator_cli_version),
        ("generator_thread_id", envelope.generator_thread_id),
        ("generator_turn_id", envelope.generator_turn_id),
    ):
        if value is not None and (not value.strip() or len(value) > 256 or "\x00" in value):
            raise ModelCandidateQuarantineError(f"{label} is invalid")
    if not 1 <= len(envelope.files) <= MAX_CANDIDATE_FILES:
        raise ModelCandidateQuarantineError("candidate file count is outside the quarantine budget")
    seen: set[str] = set()
    total = 0
    skill_md: str | None = None
    openai_yaml: str | None = None
    has_run_script = False
    for item in envelope.files:
        path = _safe_candidate_path(item.path).as_posix()
        if path in seen:
            raise ModelCandidateQuarantineError(f"duplicate candidate file path: {path}")
        seen.add(path)
        if not isinstance(item.content, str) or "\x00" in item.content:
            raise ModelCandidateQuarantineError(f"candidate file is not bounded UTF-8 text: {path}")
        if any(pattern.search(item.content) for pattern in SECRET_PATTERNS):
            raise ModelCandidateQuarantineError(f"candidate file contains secret-like material: {path}")
        size = len(item.content.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            raise ModelCandidateQuarantineError(f"candidate file exceeds size budget: {path}")
        total += size
        if path == "SKILL.md":
            skill_md = item.content
        if path == "agents/openai.yaml":
            openai_yaml = item.content
        if path == "scripts/run.py":
            has_run_script = True
        if path.startswith("scripts/"):
            _validate_python_script(path, item.content)
    if total > MAX_TOTAL_BYTES:
        raise ModelCandidateQuarantineError("candidate bundle exceeds total size budget")
    if skill_md is None:
        raise ModelCandidateQuarantineError("candidate bundle requires SKILL.md")
    if _frontmatter_name(skill_md) != envelope.candidate_skill_id:
        raise ModelCandidateQuarantineError("SKILL.md name differs from candidate skill ID")
    if openai_yaml is None:
        raise ModelCandidateQuarantineError("candidate bundle requires agents/openai.yaml")
    _validate_openai_interface(openai_yaml, envelope.candidate_skill_id)
    if not has_run_script:
        raise ModelCandidateQuarantineError("candidate bundle requires scripts/run.py")
    return (
        ValidationResult("Q0_provenance", True, evidence="prompt/response hashes and model contract present"),
        ValidationResult("Q1_paths", True, evidence=f"portable allowlist accepted {len(seen)} files"),
        ValidationResult("Q2_size", True, score=float(total), evidence=f"total_bytes={total}"),
        ValidationResult("Q3_static_python", True, evidence="Python AST quarantine policy passed"),
        ValidationResult("Q4_execution_block", True, evidence="host execution and promotion remain disabled"),
        ValidationResult(
            "Q5_portable_interface",
            True,
            evidence="required three-file core and explicit OpenAI skill interface passed",
        ),
    )


def parse_model_candidate_response(
    *,
    raw_response: str,
    generator_backend: str,
    generator_model: str,
    generator_effort: str,
    generator_prompt_sha256: str,
    generator_provider_reported_model_ids: tuple[str, ...] = (),
    generator_cli_version: str | None = None,
    generator_raw_trace_sha256: str | None = None,
    generator_thread_id: str | None = None,
    generator_turn_id: str | None = None,
) -> ModelCandidateEnvelope:
    """Parse a strict provider response and bind its exact bytes to an envelope."""

    if not isinstance(raw_response, str) or "\x00" in raw_response:
        raise ModelCandidateQuarantineError("model candidate response is not bounded UTF-8 text")
    encoded = raw_response.encode("utf-8")
    if len(encoded) > MAX_MODEL_RESPONSE_BYTES:
        raise ModelCandidateQuarantineError("model candidate response exceeds size budget")
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise ModelCandidateQuarantineError("model candidate response is not strict JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"candidate_skill_id", "files"}:
        raise ModelCandidateQuarantineError(
            "model candidate response must contain exactly candidate_skill_id and files"
        )
    if not isinstance(payload["candidate_skill_id"], str) or not isinstance(payload["files"], list):
        raise ModelCandidateQuarantineError("model candidate response has invalid field types")
    files: list[ModelCandidateFile] = []
    for index, item in enumerate(payload["files"]):
        if not isinstance(item, dict) or set(item) != {"path", "content"}:
            raise ModelCandidateQuarantineError(
                f"model candidate file {index} must contain exactly path and content"
            )
        if not isinstance(item["path"], str) or not isinstance(item["content"], str):
            raise ModelCandidateQuarantineError(
                f"model candidate file {index} has invalid field types"
            )
        files.append(ModelCandidateFile(path=item["path"], content=item["content"]))
    envelope = ModelCandidateEnvelope(
        candidate_skill_id=payload["candidate_skill_id"],
        generator_backend=generator_backend,
        generator_model=generator_model,
        generator_effort=generator_effort,
        generator_prompt_sha256=generator_prompt_sha256,
        generator_response_sha256=_sha256_bytes(encoded),
        files=tuple(files),
        generator_provider_reported_model_ids=tuple(generator_provider_reported_model_ids),
        generator_cli_version=generator_cli_version,
        generator_raw_trace_sha256=generator_raw_trace_sha256,
        generator_thread_id=generator_thread_id,
        generator_turn_id=generator_turn_id,
    )
    _validate_envelope(envelope)
    return envelope


def quarantine_model_candidate(
    *,
    envelope: ModelCandidateEnvelope,
    output_root: Path,
) -> ModelCandidateQuarantineResult:
    """Persist an inert model-authored bundle and content-addressed manifest.

    This function never imports or executes candidate code. A later isolated
    runner must produce target, held-out, and regression evidence before the
    candidate can enter the managed-creation promotion path.
    """

    gates = _validate_envelope(envelope)
    output_root = output_root.expanduser().resolve(strict=False)
    if output_root.exists():
        raise ModelCandidateQuarantineError(
            f"refusing to overwrite model candidate quarantine: {output_root}"
        )
    candidate_root = output_root / "candidate" / envelope.candidate_skill_id
    candidate_root.mkdir(parents=True)
    records: list[QuarantinedFileRecord] = []
    by_path = sorted(envelope.files, key=lambda item: item.path)
    for item in by_path:
        relative = _safe_candidate_path(item.path)
        target = candidate_root.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = item.content.encode("utf-8")
        target.write_bytes(encoded)
        records.append(
            QuarantinedFileRecord(
                path=relative.as_posix(),
                bytes=len(encoded),
                sha256=_sha256_bytes(encoded),
            )
        )
    manifest_body = {
        "candidate_skill_id": envelope.candidate_skill_id,
        "generator_backend": envelope.generator_backend,
        "generator_model": envelope.generator_model,
        "generator_effort": envelope.generator_effort,
        "generator_prompt_sha256": envelope.generator_prompt_sha256,
        "generator_response_sha256": envelope.generator_response_sha256,
        "generator_provider_reported_model_ids": list(
            envelope.generator_provider_reported_model_ids
        ),
        "generator_model_evidence_level": (
            "provider_reported"
            if envelope.generator_provider_reported_model_ids
            else "requested_cli_contract_only"
        ),
        "generator_cli_version": envelope.generator_cli_version,
        "generator_raw_trace_sha256": envelope.generator_raw_trace_sha256,
        "generator_thread_id": envelope.generator_thread_id,
        "generator_turn_id": envelope.generator_turn_id,
        "files": [asdict(item) for item in records],
        "execution_allowed": False,
        "promotion_allowed": False,
    }
    manifest_sha256 = _sha256_bytes(_canonical_json(manifest_body).encode("utf-8"))
    manifest = {"schema_version": 1, **manifest_body, "manifest_sha256": manifest_sha256}
    (output_root / "quarantine_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result = ModelCandidateQuarantineResult(
        candidate_skill_id=envelope.candidate_skill_id,
        quarantine_root=output_root.name,
        manifest_sha256=manifest_sha256,
        files=tuple(records),
        gates=gates,
    )
    (output_root / "quarantine_report.json").write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result
