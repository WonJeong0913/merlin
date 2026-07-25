"""Run exactly one minimal real Codex CLI trace-contract smoke.

The smoke proves provider execution provenance and the Merlin adapter/verifier
boundary.  It intentionally does *not* claim actual skill invocation: Codex
``exec --json`` does not expose provider-native Merlin skill-body loads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from src.merlin_harness.codex_adapter import CodexCliAdapter, CodexCliAdapterError
from src.merlin_harness.metrics import trace_to_invocation_observation
from src.merlin_harness.models import AgentRunContract, TaskSpec, VerifierSpec
from src.merlin_harness.runner import run_agent_adapter_once
from src.merlin_harness.traces import AGENT_TRACE_EVIDENCE_KEY, FileTraceStore


DEFAULT_CODEX_EXECUTABLE = "/Applications/ChatGPT.app/Contents/Resources/codex"
DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_CLI_VERSION = "codex-cli 0.145.0-alpha.18"
_SECRET_PATTERNS = (
    re.compile(r"(?i)authorization\s*[:=]"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)(openai|api)[_-]?key\s*[:=]\s*[^\s]+"),
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _scan_for_secrets(paths: list[Path]) -> list[str]:
    hits: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in _SECRET_PATTERNS):
                hits.append(f"{path.name}:{line_number}")
    return hits


def run_smoke(
    *,
    output_dir: Path,
    workspace_root: Path,
    executable: str,
    model_id: str,
    cli_version: str,
) -> dict[str, object]:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise ValueError(f"refusing to overwrite existing smoke output: {output_dir}")
    output_dir.mkdir(parents=True)
    workspace = workspace_root.resolve()
    workspace.mkdir(parents=True, exist_ok=False)
    raw_root = output_dir / "raw"
    run_id = f"codex-gpt56-smoke-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    task = TaskSpec(
        id="codex-gpt56-exact-answer-smoke",
        instruction="Reply with exactly MERLIN_GPT56_SMOKE_OK and nothing else. Do not call tools or modify files.",
        verifier=VerifierSpec(name="exact_merlin_harness_gpt56_smoke", kind="exact_match", expected="MERLIN_GPT56_SMOKE_OK"),
    )
    contract = AgentRunContract(
        run_id=run_id,
        task_id=task.id,
        condition="codex-cli-gpt56-smoke",
        workspace_root=str(workspace),
        raw_trace_root=str(raw_root),
        agent_id="codex-cli",
        agent_version=cli_version,
        backend="openai-codex-cli-chatgpt",
        model_id=model_id,
        effort="low",
        budget_id="one-minimal-gpt56-smoke-20260718",
        library_snapshot_id="no-merlin-skills",
        library_snapshot_sha256=_sha256(b"no-merlin-skills"),
        verifier_id=task.verifier.name,
    )
    adapter = CodexCliAdapter(executable=executable, cli_version=cli_version, timeout_s=120)
    store = FileTraceStore(output_dir / "traces")
    trace = run_agent_adapter_once(
        task=task,
        workspace=workspace,
        condition=contract.condition,
        contract=contract,
        adapter=adapter,
        provisioned_skills=[],
        trace_id=run_id,
        trace_store=store,
    )
    stored = store.load(trace.id)
    evidence = stored.metadata[AGENT_TRACE_EVIDENCE_KEY]
    metrics_status = "unexpectedly_available"
    try:
        trace_to_invocation_observation(stored)
    except ValueError as exc:
        metrics_status = str(exc)
    raw_path = raw_root / evidence["raw_trace"]["pointer"]
    last_message_paths = sorted(raw_root.glob("*.last-message.txt"))
    secret_hits = _scan_for_secrets([raw_path, *last_message_paths])
    if secret_hits:
        raise RuntimeError("secret-like content detected in smoke raw artifacts: " + ", ".join(secret_hits))
    adapter_metadata = stored.metadata["agent_adapter_metadata"]
    report: dict[str, object] = {
        "schema_version": 1,
        "scope": "one real Codex CLI answer-only smoke requested with gpt-5.6-terra; not a benchmark or skill-invocation claim",
        "run_id": contract.run_id,
        "thread_id": adapter_metadata["thread_id"],
        "turn_id": adapter_metadata["turn_id"],
        "requested_model_id": contract.model_id,
        "provider_reported_model_ids": adapter_metadata["provider_reported_model_ids"],
        "cli_version": adapter_metadata["cli_version"],
        "effort": contract.effort,
        "budget_id": contract.budget_id,
        "verifier_id": contract.verifier_id,
        "verifier_passed": bool(stored.invocation and stored.invocation.success),
        "verifier_count": len(stored.validation),
        "actual_invocation_evidence_complete": evidence["actual_invocation_evidence_complete"],
        "actual_invocation_event_count": len(evidence["invocation_events"]),
        "paper_metric_status": metrics_status,
        "raw_trace": evidence["raw_trace"],
        "raw_event_count": adapter_metadata["raw_event_count"],
        "raw_event_types": adapter_metadata["raw_event_types"],
        "raw_secret_scan": {"passed": True, "hits": []},
        "feedback_session_id": None,
        "feedback_session_id_note": "Not inferred from Codex CLI thread_id or run_id; obtain separately with /feedback in the primary build session.",
    }
    (output_dir / "smoke_summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one real GPT-5.6 Codex CLI Merlin adapter smoke.")
    parser.add_argument(
        "--output",
        default="experiments/mvp/results/agent_trace_contract",
        help="New output directory for excluded raw/provider evidence.",
    )
    parser.add_argument(
        "--workspace",
        default=f"/private/tmp/merlin-codex-gpt56-smoke-{uuid.uuid4().hex[:8]}",
        help="New isolated task workspace under /private/tmp.",
    )
    parser.add_argument("--executable", default=DEFAULT_CODEX_EXECUTABLE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--cli-version", default=DEFAULT_CLI_VERSION)
    args = parser.parse_args(argv)
    try:
        report = run_smoke(
            output_dir=Path(args.output),
            workspace_root=Path(args.workspace),
            executable=args.executable,
            model_id=args.model,
            cli_version=args.cli_version,
        )
    except (CodexCliAdapterError, RuntimeError, ValueError) as exc:
        print(f"Codex GPT-5.6 smoke failed before verifier promotion: {exc}")
        return 1
    print("Merlin real Codex GPT-5.6 smoke")
    print(f"run_id={report['run_id']}")
    print(f"thread_id={report['thread_id']}")
    print(f"requested_model_id={report['requested_model_id']}")
    print(f"provider_reported_model_ids={report['provider_reported_model_ids']}")
    print(f"verifier_passed={report['verifier_passed']}")
    print(f"actual_invocation_evidence_complete={report['actual_invocation_evidence_complete']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
