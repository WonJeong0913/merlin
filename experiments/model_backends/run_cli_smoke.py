"""Run a small account-auth CLI backend smoke test."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.merlin_harness.executors import CliModelConfig, CliModelExecutor, make_claude_cli_executor, make_codex_cli_executor
from src.merlin_harness.runner import run_task_once
from src.merlin_harness.task_io import load_task


ROOT = Path(__file__).resolve().parent
DEFAULT_TASK = Path("experiments/mvp/tasks/answer-yes.json")


def make_executor(args: argparse.Namespace) -> CliModelExecutor:
    if args.backend == "claude":
        return make_claude_cli_executor(model=args.model or "sonnet", effort=args.effort, timeout_s=args.timeout_s)
    if args.backend == "codex":
        return make_codex_cli_executor(model=args.model, effort=args.effort, timeout_s=args.timeout_s)
    return CliModelExecutor(
        CliModelConfig(
            command=args.command,
            backend_name=args.backend,
            model=args.model or "unspecified",
            effort=args.effort,
            auth_mode="account",
            prompt_mode=args.prompt_mode,
            timeout_s=args.timeout_s,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a tiny account-auth CLI backend smoke test.")
    parser.add_argument("--backend", default="claude", help="Backend label, e.g. claude, codex, glm.")
    parser.add_argument("--model", default=None, help="Backend model label. Defaults to sonnet for Claude and CLI default for Codex.")
    parser.add_argument("--effort", default="high", help="Reasoning/effort level recorded and passed when supported.")
    parser.add_argument("--task", type=Path, default=DEFAULT_TASK)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--timeout-s", type=float, default=300.0)
    parser.add_argument("--prompt-mode", choices=["stdin", "arg", "file"], default="stdin")
    parser.add_argument("--command", nargs="+", default=None, help="Command for custom backends.")
    args = parser.parse_args(argv)

    if args.backend not in {"claude", "codex"} and not args.command:
        parser.error("--command is required for custom CLI backends")

    run_id = args.run_id or f"{args.backend}-cli-smoke-{uuid.uuid4().hex[:8]}"
    result_root = ROOT / "results" / run_id
    workspace = ROOT / "workspaces" / run_id
    result_root.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)

    task = load_task(args.task)
    executor = make_executor(args)
    trace = run_task_once(
        task=task,
        workspace=workspace / task.id,
        condition=f"{args.backend}-cli-smoke",
        executor=executor,
    )
    summary = {
        "run_id": run_id,
        "backend": args.backend,
        "model": args.model or ("sonnet" if args.backend == "claude" else "default"),
        "effort": args.effort,
        "task_id": task.id,
        "success": trace.invocation.success if trace.invocation else None,
        "score": trace.invocation.score if trace.invocation else None,
        "condition": trace.condition,
        "trace": asdict(trace),
    }
    (result_root / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ["run_id", "backend", "model", "effort", "task_id", "success", "score"]}, ensure_ascii=False))
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
