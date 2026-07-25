"""Task and verifier primitives for deterministic MVP experiments."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .models import TaskSpec, ValidationResult


def materialize_task_workspace(task: TaskSpec, workspace: str | Path) -> Path:
    """Create the static files a deterministic task needs before execution."""

    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    for relative_path, content in task.setup_files.items():
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def run_verifier(task: TaskSpec, workspace: str | Path, answer: str | None = None) -> ValidationResult:
    """Run a deterministic verifier for a task.

    This is intentionally narrow. API-model execution comes later; the first
    milestone needs reliable task scoring before it needs richer agents.
    """

    root = Path(workspace)
    verifier = task.verifier

    if verifier.kind == "exact_match":
        actual = "" if answer is None else answer.strip()
        expected = "" if verifier.expected is None else verifier.expected.strip()
        passed = actual == expected
        return ValidationResult(
            name=verifier.name,
            passed=passed,
            score=1.0 if passed else 0.0,
            evidence=f"expected={expected!r} actual={actual!r}",
        )

    if verifier.kind == "file_exists":
        if not verifier.target_path:
            return ValidationResult(verifier.name, False, 0.0, "target_path is required")
        target = root / verifier.target_path
        passed = target.exists()
        return ValidationResult(
            name=verifier.name,
            passed=passed,
            score=1.0 if passed else 0.0,
            evidence=f"path={target}",
        )

    if verifier.kind == "command":
        if not verifier.command:
            return ValidationResult(verifier.name, False, 0.0, "command is required")
        try:
            completed = subprocess.run(
                verifier.command,
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                timeout=verifier.timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            return ValidationResult(
                name=verifier.name,
                passed=False,
                score=0.0,
                evidence=f"timeout after {exc.timeout}s",
            )
        evidence = "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part)
        return ValidationResult(
            name=verifier.name,
            passed=completed.returncode == 0,
            score=1.0 if completed.returncode == 0 else 0.0,
            evidence=evidence,
        )

    raise ValueError(f"Unsupported verifier kind: {verifier.kind}")
