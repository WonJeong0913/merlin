"""Seal retained model-authored lifecycle inputs into a private audit pack.

The resulting tar is deliberately unsuitable for public release: it contains
raw provider text, thread/session material, commands, sandbox profiles, and
case workspaces.  It exists so a trusted reviewer can reproduce the public
hash-only 15/15 chain audit from exact retained bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from experiments.mvp.audit_model_authored_skill_chain import (
    ModelAuthoredSkillChainAuditError,
    validate_model_authored_skill_chain_audit,
)
from src.merlin_harness.management import content_sha256


LIVE_RUN_FILES = (
    "generator/candidate-response.schema.json",
    "generator/generation_report.json",
    "generator/provider.codex.jsonl",
    "generator/provider.last-message.json",
    "quarantine/candidate/extract-todo-items/SKILL.md",
    "quarantine/candidate/extract-todo-items/agents/openai.yaml",
    "quarantine/candidate/extract-todo-items/scripts/run.py",
    "quarantine/quarantine_manifest.json",
    "quarantine/quarantine_report.json",
    "execution/isolated_execution_report.json",
    *tuple(
        f"execution/cases/{case}/{name}"
        for case in ("target-english", "target-whitespace", "held-out-korean")
        for name in (
            "sandbox.sb",
            "stderr.bin",
            "stdout.bin",
            "workspace/backlog.todo",
            "workspace/todo-items.json",
        )
    ),
)

SAFE_EVIDENCE_FILES = (
    "model_authored_skill_evidence.json",
    "model_authored_skill_chain_audit.json",
    "promoted_chat_smoke.json",
    "promoted_chat_smoke_v2.json",
    "provisional_library.json",
    "quarantine/candidate/extract-todo-items/SKILL.md",
    "quarantine/candidate/extract-todo-items/agents/openai.yaml",
    "quarantine/candidate/extract-todo-items/scripts/run.py",
    "quarantine/quarantine_manifest.json",
    "quarantine/quarantine_report.json",
)

PROMOTED_SESSION_FILES = (
    "library-overlay-manifest.json",
    "library-overlay/extract-todo-items.json",
    "library-overlay/file-artifact-basic.json",
    "library-overlay/line-summary.json",
    "promoted-bundles/extract-todo-items/SKILL.md",
    "promoted-bundles/extract-todo-items/agents/openai.yaml",
    "promoted-bundles/extract-todo-items/scripts/run.py",
    "turn-0001.codex.jsonl",
    "turn-0001.last-message.txt",
    "turn-0001.meta.json",
)

WORKSPACE_FILES = ("backlog.todo", "todo-items.json")
MAX_FILE_BYTES = 4_000_000
MAX_PACK_BYTES = 32_000_000
MANIFEST_NAME = "PRIVATE_EVIDENCE_MANIFEST.json"
SESSION_PACK_PREFIX = "promoted-chat/workspace/.merlin/chat/session-private"
EXPECTED_PACK_PATHS = frozenset(
    {f"live-run/{name}" for name in LIVE_RUN_FILES}
    | {f"safe-evidence/{name}" for name in SAFE_EVIDENCE_FILES}
    | {f"promoted-chat/workspace/{name}" for name in WORKSPACE_FILES}
    | {f"{SESSION_PACK_PREFIX}/{name}" for name in PROMOTED_SESSION_FILES}
    | {"fresh-reaudit/model_authored_skill_chain_audit.json"}
)


class PrivateModelAuthoredEvidenceError(ValueError):
    """Raised when private model-authored evidence cannot be sealed safely."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _regular_file(root: Path, relative: str, *, label: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or "." in pure.parts or ".." in pure.parts:
        raise PrivateModelAuthoredEvidenceError(f"unsafe {label} path")
    if root.is_symlink() or not root.is_dir():
        raise PrivateModelAuthoredEvidenceError(f"{label} root is missing or unsafe")
    candidate = root.joinpath(*pure.parts)
    if candidate.is_symlink():
        raise PrivateModelAuthoredEvidenceError(f"{label} file is a symlink: {relative}")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise PrivateModelAuthoredEvidenceError(
            f"{label} file is missing or escapes its root: {relative}"
        ) from exc
    if not resolved.is_file():
        raise PrivateModelAuthoredEvidenceError(f"{label} member is not a regular file")
    if resolved.stat().st_size > MAX_FILE_BYTES:
        raise PrivateModelAuthoredEvidenceError(f"{label} file exceeds private-pack budget")
    return resolved


def _regular_root(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise PrivateModelAuthoredEvidenceError(f"{label} root must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise PrivateModelAuthoredEvidenceError(f"{label} root is missing") from exc
    if not resolved.is_dir():
        raise PrivateModelAuthoredEvidenceError(f"{label} root must be a directory")
    return resolved


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PrivateModelAuthoredEvidenceError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise PrivateModelAuthoredEvidenceError(f"{label} must be a JSON object")
    return value


def _source_files(
    *,
    live_run_root: Path,
    safe_evidence_root: Path,
    promoted_chat_workspace: Path,
    promoted_chat_session_root: Path,
    fresh_audit_path: Path,
) -> dict[str, bytes]:
    roots = {
        "live-run": (_regular_root(live_run_root, label="live run"), LIVE_RUN_FILES),
        "safe-evidence": (
            _regular_root(safe_evidence_root, label="safe evidence"),
            SAFE_EVIDENCE_FILES,
        ),
        "promoted-chat/workspace": (
            _regular_root(promoted_chat_workspace, label="promoted chat workspace"),
            WORKSPACE_FILES,
        ),
        SESSION_PACK_PREFIX: (
            _regular_root(promoted_chat_session_root, label="promoted chat session"),
            PROMOTED_SESSION_FILES,
        ),
    }
    workspace_root = roots["promoted-chat/workspace"][0]
    session_root = roots[SESSION_PACK_PREFIX][0]
    if not session_root.is_relative_to(workspace_root):
        raise PrivateModelAuthoredEvidenceError(
            "promoted chat session must stay inside its workspace"
        )
    files: dict[str, bytes] = {}
    for prefix, (root, names) in roots.items():
        for relative in names:
            path = _regular_file(root, relative, label=prefix)
            files[f"{prefix}/{relative}"] = path.read_bytes()
    fresh_expanded = fresh_audit_path.expanduser()
    if fresh_expanded.is_symlink():
        raise PrivateModelAuthoredEvidenceError("fresh audit is missing or unsafe")
    try:
        fresh = fresh_expanded.resolve(strict=True)
    except OSError as exc:
        raise PrivateModelAuthoredEvidenceError("fresh audit is missing or unsafe") from exc
    if not fresh.is_file() or fresh.stat().st_size > MAX_FILE_BYTES:
        raise PrivateModelAuthoredEvidenceError("fresh audit is missing or unsafe")
    files["fresh-reaudit/model_authored_skill_chain_audit.json"] = fresh.read_bytes()
    return files


def _validate_chain_sources(files: Mapping[str, bytes]) -> dict[str, Any]:
    audit_bytes = files["safe-evidence/model_authored_skill_chain_audit.json"]
    fresh_bytes = files["fresh-reaudit/model_authored_skill_chain_audit.json"]
    if fresh_bytes != audit_bytes:
        raise PrivateModelAuthoredEvidenceError(
            "fresh audit bytes differ from the packaged hash-only audit"
        )
    try:
        audit = json.loads(audit_bytes)
        validate_model_authored_skill_chain_audit(audit)
    except (json.JSONDecodeError, ModelAuthoredSkillChainAuditError) as exc:
        raise PrivateModelAuthoredEvidenceError("hash-only audit is invalid") from exc
    hashes = audit["source_hashes"]
    expected = {
        "live-run/generator/provider.codex.jsonl": hashes[
            "authoring_raw_trace_sha256"
        ],
        "live-run/generator/provider.last-message.json": hashes[
            "authoring_response_sha256"
        ],
        "safe-evidence/model_authored_skill_evidence.json": hashes[
            "promotion_evidence_file_sha256"
        ],
        "safe-evidence/provisional_library.json": hashes[
            "provisional_library_file_sha256"
        ],
        "safe-evidence/quarantine/quarantine_manifest.json": hashes[
            "quarantine_manifest_file_sha256"
        ],
        "safe-evidence/quarantine/quarantine_report.json": hashes[
            "quarantine_report_file_sha256"
        ],
        "safe-evidence/promoted_chat_smoke.json": hashes[
            "packaged_promoted_chat_file_sha256"
        ],
        f"{SESSION_PACK_PREFIX}/turn-0001.codex.jsonl": hashes[
            "promoted_chat_raw_trace_sha256"
        ],
    }
    for name, expected_sha256 in expected.items():
        if _sha256(files[name]) != expected_sha256:
            raise PrivateModelAuthoredEvidenceError(
                f"private source hash differs from chain audit: {name}"
            )
    return audit


def build_private_model_authored_evidence_pack(
    *,
    live_run_root: Path,
    safe_evidence_root: Path,
    promoted_chat_workspace: Path,
    promoted_chat_session_root: Path,
    fresh_audit_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Create one deterministic new-only private evidence tar."""

    destination = output_path.expanduser().resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise PrivateModelAuthoredEvidenceError("private pack output must be new-only")
    files = _source_files(
        live_run_root=live_run_root,
        safe_evidence_root=safe_evidence_root,
        promoted_chat_workspace=promoted_chat_workspace,
        promoted_chat_session_root=promoted_chat_session_root,
        fresh_audit_path=fresh_audit_path,
    )
    audit = _validate_chain_sources(files)
    records = [
        {"path": name, "bytes": len(raw), "sha256": _sha256(raw)}
        for name, raw in sorted(files.items())
    ]
    manifest = {
        "schema_version": 1,
        "pack_role": "private-review-reproducibility-inputs",
        "candidate_skill_id": audit["candidate_skill_id"],
        "chain_audit_sha256": audit["audit_sha256"],
        "record_count": len(records),
        "records_sha256": content_sha256(records),
        "records": records,
        "reproduction_entrypoint": (
            "experiments.mvp.audit_model_authored_skill_chain"
        ),
        "claim_boundary": {
            "private_only_do_not_publish": True,
            "raw_provider_text_included": True,
            "raw_command_text_may_be_included": True,
            "provider_thread_or_session_material_included": True,
            "packaging_is_new_model_execution": False,
            "requested_model_is_provider_resolved_model": False,
            "provider_native_skill_invocation_claimed": False,
            "full_benchmark_result": False,
        },
    }
    manifest["manifest_sha256"] = content_sha256(manifest)
    members = {**files, MANIFEST_NAME: _json_bytes(manifest)}
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(destination, mode="x:", format=tarfile.USTAR_FORMAT) as archive:
            for name in sorted(members):
                raw = members[name]
                info = tarfile.TarInfo(name=name)
                info.size = len(raw)
                info.mode = 0o600
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                archive.addfile(info, io.BytesIO(raw))
    except FileExistsError as exc:
        raise PrivateModelAuthoredEvidenceError("private pack output must be new-only") from exc
    if destination.stat().st_size > MAX_PACK_BYTES:
        raise PrivateModelAuthoredEvidenceError("private pack exceeds the size budget")
    return validate_private_model_authored_evidence_pack(destination)


def validate_private_model_authored_evidence_pack(path: Path) -> dict[str, Any]:
    """Reopen one private pack and validate exact member bytes and chain bindings."""

    expanded = path.expanduser()
    if expanded.is_symlink():
        raise PrivateModelAuthoredEvidenceError("private pack must not be a symlink")
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise PrivateModelAuthoredEvidenceError("private pack is missing") from exc
    if not resolved.is_file() or resolved.stat().st_size > MAX_PACK_BYTES:
        raise PrivateModelAuthoredEvidenceError("private pack is unsafe or oversized")
    members: dict[str, bytes] = {}
    try:
        with tarfile.open(resolved, mode="r:") as archive:
            for member in archive.getmembers():
                pure = PurePosixPath(member.name)
                if (
                    not member.isfile()
                    or member.issym()
                    or member.islnk()
                    or pure.is_absolute()
                    or "." in pure.parts
                    or ".." in pure.parts
                    or member.name in members
                    or member.size > MAX_FILE_BYTES
                ):
                    raise PrivateModelAuthoredEvidenceError(
                        "private pack contains an unsafe member"
                    )
                handle = archive.extractfile(member)
                if handle is None:
                    raise PrivateModelAuthoredEvidenceError(
                        "private pack member cannot be read"
                    )
                raw = handle.read()
                if len(raw) != member.size:
                    raise PrivateModelAuthoredEvidenceError(
                        "private pack member size drifted"
                    )
                members[member.name] = raw
    except (tarfile.TarError, OSError) as exc:
        raise PrivateModelAuthoredEvidenceError("private pack is invalid") from exc
    if MANIFEST_NAME not in members:
        raise PrivateModelAuthoredEvidenceError("private pack manifest is missing")
    try:
        manifest = json.loads(members.pop(MANIFEST_NAME))
    except json.JSONDecodeError as exc:
        raise PrivateModelAuthoredEvidenceError("private pack manifest is invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise PrivateModelAuthoredEvidenceError("private pack manifest schema drifted")
    if set(manifest) != {
        "schema_version",
        "pack_role",
        "candidate_skill_id",
        "chain_audit_sha256",
        "record_count",
        "records_sha256",
        "records",
        "reproduction_entrypoint",
        "claim_boundary",
        "manifest_sha256",
    }:
        raise PrivateModelAuthoredEvidenceError("private pack manifest fields drifted")
    recorded_hash = manifest.get("manifest_sha256")
    unhashed = dict(manifest)
    unhashed.pop("manifest_sha256", None)
    if recorded_hash != content_sha256(unhashed):
        raise PrivateModelAuthoredEvidenceError("private pack manifest hash mismatch")
    records = manifest.get("records")
    if set(members) != EXPECTED_PACK_PATHS:
        raise PrivateModelAuthoredEvidenceError("private pack member coverage drifted")
    expected_records = [
        {"path": name, "bytes": len(raw), "sha256": _sha256(raw)}
        for name, raw in sorted(members.items())
    ]
    if (
        records != expected_records
        or manifest.get("record_count") != len(expected_records)
        or manifest.get("records_sha256") != content_sha256(expected_records)
    ):
        raise PrivateModelAuthoredEvidenceError("private pack record coverage drifted")
    try:
        audit = _validate_chain_sources(members)
    except KeyError as exc:
        raise PrivateModelAuthoredEvidenceError("private pack chain source is missing") from exc
    if manifest.get("chain_audit_sha256") != audit["audit_sha256"]:
        raise PrivateModelAuthoredEvidenceError("private pack chain audit identity drifted")
    boundary = manifest.get("claim_boundary")
    if (
        manifest.get("pack_role") != "private-review-reproducibility-inputs"
        or manifest.get("candidate_skill_id") != "extract-todo-items"
        or manifest.get("reproduction_entrypoint")
        != "experiments.mvp.audit_model_authored_skill_chain"
        or boundary
        != {
            "private_only_do_not_publish": True,
            "raw_provider_text_included": True,
            "raw_command_text_may_be_included": True,
            "provider_thread_or_session_material_included": True,
            "packaging_is_new_model_execution": False,
            "requested_model_is_provider_resolved_model": False,
            "provider_native_skill_invocation_claimed": False,
            "full_benchmark_result": False,
        }
    ):
        raise PrivateModelAuthoredEvidenceError("private pack publication boundary is missing")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--live-run-root", type=Path, required=True)
    create.add_argument("--safe-evidence-root", type=Path, required=True)
    create.add_argument("--promoted-chat-workspace", type=Path, required=True)
    create.add_argument("--promoted-chat-session-root", type=Path, required=True)
    create.add_argument("--fresh-audit", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--pack", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            manifest = build_private_model_authored_evidence_pack(
                live_run_root=args.live_run_root,
                safe_evidence_root=args.safe_evidence_root,
                promoted_chat_workspace=args.promoted_chat_workspace,
                promoted_chat_session_root=args.promoted_chat_session_root,
                fresh_audit_path=args.fresh_audit,
                output_path=args.output,
            )
            pack = args.output
        else:
            manifest = validate_private_model_authored_evidence_pack(args.pack)
            pack = args.pack
    except PrivateModelAuthoredEvidenceError as exc:
        parser.error(str(exc))
    print("Merlin private model-authored evidence pack")
    print("status=created" if args.command == "create" else "status=validated")
    print(f"records={manifest['record_count']}")
    print(f"chain_audit_sha256={manifest['chain_audit_sha256']}")
    print("private_only_do_not_publish=true")
    print(f"pack_sha256={hashlib.sha256(pack.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
