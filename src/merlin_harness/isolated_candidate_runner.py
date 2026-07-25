"""Bounded macOS execution for an immutable quarantined candidate.

The runner re-hashes every quarantined file, creates one fresh workspace per
case, and runs ``scripts/run.py --workspace`` through ``sandbox-exec``.  The
profile denies network access, permits candidate/task reads, and permits writes
only inside the current task.  This is a documented confinement layer, not a
claim of perfect hostile-code isolation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import resource
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Literal


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_CASE_FILES = 16
MAX_CASE_BYTES = 256 * 1024
MAX_LOG_BYTES = 256 * 1024
MAX_FILE_BYTES = 1024 * 1024
SANDBOX_APPLY_ERROR_PREFIX = b"sandbox-exec: sandbox_apply:"
SANDBOX_EXECUTABLE = Path("/usr/bin/sandbox-exec")
PYTHON_EXECUTABLE = Path(
    "/Applications/Xcode.app/Contents/Developer/Library/Frameworks/"
    "Python3.framework/Versions/3.9/bin/python3.9"
)
PYTHON_INNER_EXECUTABLE = Path(
    "/Applications/Xcode.app/Contents/Developer/Library/Frameworks/"
    "Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python"
)


class IsolatedCandidateRunnerError(ValueError):
    """Raised before an untrusted or unverifiable execution can be accepted."""


@dataclass(frozen=True, slots=True)
class CandidateExecutionCase:
    case_id: str
    split: Literal["target", "held_out", "library_regression"]
    input_files: tuple[tuple[str, str], ...]
    expected_files: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class BoundedProcessResult:
    return_code: int | None
    timed_out: bool
    latency_s: float


@dataclass(frozen=True, slots=True)
class CandidateCaseExecution:
    case_id: str
    split: str
    passed: bool
    return_code: int | None
    timed_out: bool
    latency_s: float
    exact_match_count: int
    expected_file_count: int
    off_task_files: tuple[str, ...]
    stdout_sha256: str
    stderr_sha256: str
    workspace_manifest_sha256: str
    sandbox_profile_sha256: str


@dataclass(frozen=True, slots=True)
class IsolatedCandidateExecutionResult:
    candidate_skill_id: str
    quarantine_manifest_sha256: str
    runner_id: str
    cases: tuple[CandidateCaseExecution, ...]
    all_passed: bool
    target_passed: bool
    held_out_passed: bool
    regression_passed: bool | None
    evidence_boundary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, **asdict(self)}


@dataclass(frozen=True, slots=True)
class IsolatedCandidatePhaseResult:
    """One frozen repair phase executed without exposing another split."""

    candidate_skill_id: str
    quarantine_manifest_sha256: str
    runner_id: str
    phase: Literal["target", "held_out", "library_regression"]
    cases: tuple[CandidateCaseExecution, ...]
    all_passed: bool
    evidence_boundary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, **asdict(self)}


ProcessRunner = Callable[[list[str], Path, Path, Path, float], BoundedProcessResult]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_path(raw: str) -> PurePosixPath:
    if not raw or "\x00" in raw or "\\" in raw:
        raise IsolatedCandidateRunnerError("file path is empty or non-portable")
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or raw != path.as_posix()
        or any(part in {"", ".", ".."} or part.startswith(".") for part in path.parts)
    ):
        raise IsolatedCandidateRunnerError(f"unsafe file path: {raw!r}")
    return path


def _validate_cases(
    cases: tuple[CandidateExecutionCase, ...],
    *,
    required_splits: frozenset[str] | None = frozenset({"target", "held_out"}),
) -> None:
    if not cases:
        raise IsolatedCandidateRunnerError("execution requires frozen cases")
    ids: set[str] = set()
    splits: set[str] = set()
    for case in cases:
        if not SAFE_ID_RE.fullmatch(case.case_id) or case.case_id in ids:
            raise IsolatedCandidateRunnerError("case IDs must be safe and unique")
        ids.add(case.case_id)
        splits.add(case.split)
        if not case.input_files or not case.expected_files:
            raise IsolatedCandidateRunnerError("every case requires input and expected files")
        for entries in (case.input_files, case.expected_files):
            if len(entries) > MAX_CASE_FILES:
                raise IsolatedCandidateRunnerError("case file count exceeds budget")
            seen: set[str] = set()
            for raw, content in entries:
                normalized = _safe_path(raw).as_posix()
                if normalized in seen or not isinstance(content, str) or "\x00" in content:
                    raise IsolatedCandidateRunnerError("case file contract is invalid")
                if len(content.encode("utf-8")) > MAX_CASE_BYTES:
                    raise IsolatedCandidateRunnerError("case file exceeds byte budget")
                seen.add(normalized)
    if required_splits is not None and not required_splits.issubset(splits):
        raise IsolatedCandidateRunnerError("target and held-out splits are required")


def _verify_quarantine(root: Path, expected_sha256: str) -> tuple[dict[str, Any], Path]:
    if not SHA256_RE.fullmatch(expected_sha256):
        raise IsolatedCandidateRunnerError("manifest SHA-256 is invalid")
    try:
        manifest = json.loads((root / "quarantine_manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IsolatedCandidateRunnerError("quarantine manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("manifest_sha256") != expected_sha256:
        raise IsolatedCandidateRunnerError("quarantine manifest identity differs")
    body = {k: v for k, v in manifest.items() if k not in {"schema_version", "manifest_sha256"}}
    if _sha256(_canonical_json(body).encode("utf-8")) != expected_sha256:
        raise IsolatedCandidateRunnerError("quarantine manifest content hash is invalid")
    candidate_id = manifest.get("candidate_skill_id")
    records = manifest.get("files")
    if not isinstance(candidate_id, str) or not SAFE_ID_RE.fullmatch(candidate_id):
        raise IsolatedCandidateRunnerError("candidate ID is invalid")
    if not isinstance(records, list) or not records:
        raise IsolatedCandidateRunnerError("quarantine has no file records")
    candidate_root = (root / "candidate" / candidate_id).resolve()
    expected_paths: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
            raise IsolatedCandidateRunnerError("quarantine file record is malformed")
        relative = _safe_path(record["path"])
        target = candidate_root.joinpath(*relative.parts)
        if target.is_symlink() or not target.is_file():
            raise IsolatedCandidateRunnerError(f"quarantine file missing or linked: {relative}")
        payload = target.read_bytes()
        if len(payload) != record["bytes"] or _sha256(payload) != record["sha256"]:
            raise IsolatedCandidateRunnerError(f"quarantine file drifted: {relative}")
        expected_paths.add(relative.as_posix())
    actual_paths = {
        path.relative_to(candidate_root).as_posix()
        for path in candidate_root.rglob("*")
        if path.is_file()
    }
    if actual_paths != expected_paths or not (candidate_root / "scripts" / "run.py").is_file():
        raise IsolatedCandidateRunnerError("candidate has unmanifested files or no run script")
    return manifest, candidate_root


def _quoted(path: Path) -> str:
    return json.dumps(str(path.resolve()), ensure_ascii=False)


def build_macos_sandbox_profile(*, candidate_root: Path, workspace: Path) -> str:
    roots = (candidate_root.resolve(), workspace.resolve(), Path("/Applications/Xcode.app"))
    ancestors: set[Path] = set()
    for root in roots:
        current = root.parent
        while current != current.parent:
            ancestors.add(current)
            current = current.parent
    metadata = " ".join(
        f"(literal {_quoted(path)})"
        for path in sorted(ancestors, key=lambda value: (len(value.parts), str(value)))
    )
    return (
        '(version 1)\n(deny default)\n(import "system.sb")\n(deny network*)\n'
        '(allow process-exec\n'
        f'  (literal {_quoted(PYTHON_EXECUTABLE)})\n'
        f'  (literal {_quoted(PYTHON_INNER_EXECUTABLE)}))\n'
        '(allow file-read* file-test-existence file-map-executable\n'
        f'  (subpath {_quoted(Path("/Applications/Xcode.app"))}))\n'
        f'(allow file-read-metadata file-test-existence {metadata})\n'
        '(allow file-read* file-test-existence\n'
        f'  (subpath {_quoted(candidate_root)})\n'
        f'  (subpath {_quoted(workspace)}))\n'
        f'(allow file-write* (subpath {_quoted(workspace)}))\n'
    )


def _set_limits() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))


def _run_process(
    command: list[str], workspace: Path, stdout_path: Path, stderr_path: Path, timeout_s: float
) -> BoundedProcessResult:
    started = time.monotonic()
    with stdout_path.open("xb") as stdout_handle, stderr_path.open("xb") as stderr_handle:
        process = subprocess.Popen(
            command,
            cwd=workspace,
            env={
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONHASHSEED": "0",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            start_new_session=True,
            preexec_fn=_set_limits,
        )
        try:
            return_code = process.wait(timeout=timeout_s)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out, return_code = True, None
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    return BoundedProcessResult(return_code, timed_out, time.monotonic() - started)


def _manifest(root: Path) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path.read_bytes()),
        }
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    )


def _run_case(
    case: CandidateExecutionCase,
    *,
    candidate_root: Path,
    cases_root: Path,
    timeout_s: float,
    process_runner: ProcessRunner,
) -> CandidateCaseExecution:
    case_root = cases_root / case.case_id
    workspace = case_root / "workspace"
    workspace.mkdir(parents=True)
    for raw, content in case.input_files:
        target = workspace.joinpath(*_safe_path(raw).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    before = {item["path"] for item in _manifest(workspace)}
    profile = build_macos_sandbox_profile(candidate_root=candidate_root, workspace=workspace)
    profile_path = case_root / "sandbox.sb"
    profile_path.write_text(profile, encoding="utf-8")
    stdout_path, stderr_path = case_root / "stdout.bin", case_root / "stderr.bin"
    command = [
        str(SANDBOX_EXECUTABLE), "-f", str(profile_path), str(PYTHON_EXECUTABLE),
        "-I", "-B", str(candidate_root / "scripts" / "run.py"),
        "--workspace", str(workspace),
    ]
    process = process_runner(command, workspace, stdout_path, stderr_path, timeout_s)
    if not stdout_path.exists():
        stdout_path.write_bytes(b"")
    if not stderr_path.exists():
        stderr_path.write_bytes(b"")
    if max(stdout_path.stat().st_size, stderr_path.stat().st_size) > MAX_LOG_BYTES:
        raise IsolatedCandidateRunnerError("candidate log exceeded evidence budget")
    stderr_bytes = stderr_path.read_bytes()
    if (
        not process.timed_out
        and process.return_code != 0
        and stderr_bytes.startswith(SANDBOX_APPLY_ERROR_PREFIX)
    ):
        raise IsolatedCandidateRunnerError(
            "macOS sandbox runtime could not apply confinement; "
            "candidate verifier outcome is unavailable"
        )
    exact = 0
    for raw, expected in case.expected_files:
        output = workspace.joinpath(*_safe_path(raw).parts)
        if output.is_file() and output.read_text(encoding="utf-8") == expected:
            exact += 1
    after_manifest = _manifest(workspace)
    after = {item["path"] for item in after_manifest}
    expected_paths = {raw for raw, _ in case.expected_files}
    off_task = tuple(sorted((after - before) - expected_paths))
    passed = (
        not process.timed_out and process.return_code == 0
        and exact == len(case.expected_files) and not off_task
    )
    return CandidateCaseExecution(
        case.case_id, case.split, passed, process.return_code, process.timed_out,
        process.latency_s, exact, len(case.expected_files), off_task,
        _sha256(stdout_path.read_bytes()), _sha256(stderr_bytes),
        _sha256(_canonical_json(after_manifest).encode("utf-8")),
        _sha256(profile.encode("utf-8")),
    )


def run_quarantined_candidate(
    *,
    quarantine_root: Path,
    expected_manifest_sha256: str,
    cases: tuple[CandidateExecutionCase, ...],
    output_root: Path,
    timeout_s: float = 5.0,
    process_runner: ProcessRunner = _run_process,
) -> IsolatedCandidateExecutionResult:
    """Run target and hidden held-out cases without exposing expected outputs."""

    if timeout_s <= 0 or timeout_s > 30:
        raise IsolatedCandidateRunnerError("timeout must be in (0, 30]")
    if not SANDBOX_EXECUTABLE.is_file() or not PYTHON_EXECUTABLE.is_file():
        raise IsolatedCandidateRunnerError("pinned macOS isolation runtime is unavailable")
    _validate_cases(cases)
    quarantine_root = quarantine_root.expanduser().resolve()
    manifest, candidate_root = _verify_quarantine(quarantine_root, expected_manifest_sha256)
    output_root = output_root.expanduser().resolve(strict=False)
    if output_root.exists():
        raise IsolatedCandidateRunnerError(f"refusing to overwrite execution output: {output_root}")
    cases_root = output_root / "cases"
    cases_root.mkdir(parents=True)
    outcomes = tuple(
        _run_case(
            case, candidate_root=candidate_root, cases_root=cases_root,
            timeout_s=timeout_s, process_runner=process_runner,
        )
        for case in cases
    )

    def split_passed(name: str) -> bool | None:
        selected = [item for item in outcomes if item.split == name]
        return all(item.passed for item in selected) if selected else None

    result = IsolatedCandidateExecutionResult(
        candidate_skill_id=manifest["candidate_skill_id"],
        quarantine_manifest_sha256=expected_manifest_sha256,
        runner_id="macos-sandbox-exec-xcode-python-v1",
        cases=outcomes,
        all_passed=all(item.passed for item in outcomes),
        target_passed=bool(split_passed("target")),
        held_out_passed=bool(split_passed("held_out")),
        regression_passed=split_passed("library_regression"),
        evidence_boundary={
            "quarantine_manifest_reverified": True,
            "candidate_bytes_immutable_at_execution": True,
            "candidate_host_execution": False,
            "candidate_isolated_execution": True,
            "network_allowed": False,
            "candidate_read_scope": "candidate_and_current_case_plus_platform_runtime",
            "candidate_write_scope": "current_case_only",
            "expected_outputs_visible_to_candidate": False,
            "resource_limits": {
                "cpu_seconds": 3, "file_bytes": MAX_FILE_BYTES,
                "open_files": 32, "wall_timeout_seconds": timeout_s,
            },
            "perfect_hostile_code_isolation_claim": False,
            "provider_native_skill_invocation": False,
            "promoted": False,
        },
    )
    (output_root / "isolated_execution_report.json").write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def run_quarantined_candidate_phase(
    *,
    quarantine_root: Path,
    expected_manifest_sha256: str,
    phase: Literal["target", "held_out", "library_regression"],
    cases: tuple[CandidateExecutionCase, ...],
    output_root: Path,
    timeout_s: float = 5.0,
    process_runner: ProcessRunner = _run_process,
) -> IsolatedCandidatePhaseResult:
    """Execute exactly one repair split against an immutable candidate.

    A repair reviser may inspect only target feedback.  This entry point makes
    that information boundary executable: target cases can run before a model
    proposal, while hidden and library-regression cases remain untouched until
    the target-passing candidate has been selected.
    """

    if phase not in {"target", "held_out", "library_regression"}:
        raise IsolatedCandidateRunnerError("unsupported isolated execution phase")
    if timeout_s <= 0 or timeout_s > 30:
        raise IsolatedCandidateRunnerError("timeout must be in (0, 30]")
    if not SANDBOX_EXECUTABLE.is_file() or not PYTHON_EXECUTABLE.is_file():
        raise IsolatedCandidateRunnerError("pinned macOS isolation runtime is unavailable")
    _validate_cases(cases, required_splits=frozenset({phase}))
    if any(case.split != phase for case in cases):
        raise IsolatedCandidateRunnerError("phase execution may contain only its declared split")

    quarantine_root = quarantine_root.expanduser().resolve()
    manifest, candidate_root = _verify_quarantine(
        quarantine_root, expected_manifest_sha256
    )
    output_root = output_root.expanduser().resolve(strict=False)
    if output_root.exists():
        raise IsolatedCandidateRunnerError(
            f"refusing to overwrite execution output: {output_root}"
        )
    cases_root = output_root / "cases"
    cases_root.mkdir(parents=True)
    outcomes = tuple(
        _run_case(
            case,
            candidate_root=candidate_root,
            cases_root=cases_root,
            timeout_s=timeout_s,
            process_runner=process_runner,
        )
        for case in cases
    )
    result = IsolatedCandidatePhaseResult(
        candidate_skill_id=manifest["candidate_skill_id"],
        quarantine_manifest_sha256=expected_manifest_sha256,
        runner_id="macos-sandbox-exec-v1",
        phase=phase,
        cases=outcomes,
        all_passed=all(item.passed for item in outcomes),
        evidence_boundary={
            "candidate_isolated_execution": True,
            "executed_split": phase,
            "other_splits_executed": False,
            "network_allowed": False,
            "host_filesystem_write_allowed": False,
            "provider_native_invocation": False,
        },
    )
    report_path = output_root / f"isolated_{phase}_report.json"
    report_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return result
