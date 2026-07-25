#!/usr/bin/env python3
"""Run and independently replay one bounded HarnessX AEGIS round."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.mvp.run_chat import REPO_ROOT, detect_codex_runtime
from src.merlin_harness.harnessx_aegis import (
    CodexAegisStageAgent,
    ScriptedAegisStageAgent,
    default_scripted_aegis_responses,
    run_harnessx_aegis_round,
    validate_harnessx_aegis_round,
)
from src.merlin_harness.harnessx_verifier_suites import (
    DEFAULT_TOOL_POLICY_VERIFIER_SUITE,
    FROZEN_50_TOOL_POLICY_VERIFIER_SUITE,
)


DEFAULT_OUTPUT_ROOT = (
    REPO_ROOT / "experiments" / "mvp" / "results" / "harnessx_aegis_scripted_v1"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run Digester -> Planner -> Evolver -> Critic while retaining typed-builder "
            "and deterministic-gate shipping authority."
        )
    )
    parser.add_argument("--mode", choices=("scripted", "codex"), default="scripted")
    parser.add_argument(
        "--suite",
        choices=("legacy-6", "frozen-50"),
        default="legacy-6",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--revision", action="store_true")
    parser.add_argument("--executable")
    parser.add_argument("--cli-version")
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument(
        "--effort",
        choices=("low", "medium", "high", "xhigh", "max", "ultra"),
        default="low",
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    verifier_suite = (
        FROZEN_50_TOOL_POLICY_VERIFIER_SUITE
        if args.suite == "frozen-50"
        else DEFAULT_TOOL_POLICY_VERIFIER_SUITE
    )
    if args.mode == "scripted":
        stage_agent = ScriptedAegisStageAgent(
            default_scripted_aegis_responses(revision=args.revision)
        )
        runtime = {
            "mode": "scripted",
            "provider_calls_expected": 0,
        }
    else:
        if args.revision:
            raise SystemExit("--revision is available only in scripted mode")
        executable, cli_version = detect_codex_runtime(
            args.executable,
            version_override=args.cli_version,
        )
        stage_agent = CodexAegisStageAgent(
            executable=executable,
            cli_version=cli_version,
            model_id=args.model,
            effort=args.effort,
            timeout_s=args.timeout,
        )
        runtime = {
            "mode": "codex_account_auth",
            "executable": str(executable),
            "cli_version": cli_version,
            "requested_model": args.model,
            "requested_effort": args.effort,
        }

    report = run_harnessx_aegis_round(
        output_dir=args.output,
        stage_agent=stage_agent,
        verifier_suite=verifier_suite,
    )
    validation = validate_harnessx_aegis_round(args.output)
    print(
        json.dumps(
            {
                "runtime": runtime,
                "output": str(args.output.resolve()),
                "stage_sequence": report["stage_sequence"],
                "revision_used": report["revision_used"],
                "provider_call_count": report["provider_call_count"],
                "verifier_suite_id": report["verifier_suite_id"],
                "verifier_suite_sha256": report["verifier_suite_sha256"],
                "verifier_task_count": report["verifier_task_count"],
                "verifier_category_counts": report["verifier_category_counts"],
                "promoted": report["promoted"],
                "resolved_variant_id": report["resolved_variant_id"],
                "resolved_variant_sha256": report["resolved_variant_sha256"],
                "evidence_sha256": report["evidence_sha256"],
                "replay_valid": validation["valid"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
