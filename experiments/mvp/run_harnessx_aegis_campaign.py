#!/usr/bin/env python3
"""Run and replay the bounded multi-target, multi-round AEGIS campaign."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.mvp.run_chat import REPO_ROOT, detect_codex_runtime
from src.merlin_harness.harnessx_aegis import (
    MULTITARGET_AEGIS_ACTION_SPACE,
    CodexAegisStageAgent,
    ScriptedAegisStageAgent,
    run_harnessx_aegis_campaign,
    scripted_multitarget_aegis_responses,
    validate_harnessx_aegis_campaign,
)
from src.merlin_harness.harnessx_verifier_suites import (
    MULTITARGET_50_TOOL_POLICY_VERIFIER_SUITE,
)


DEFAULT_OUTPUT = (
    REPO_ROOT
    / "experiments"
    / "mvp"
    / "results"
    / "harnessx_aegis_multiround_scripted_v1"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run three bounded AEGIS rounds over a frozen 50-task suite."
    )
    parser.add_argument("--mode", choices=("scripted", "codex"), default="scripted")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-rounds", type=int, default=3)
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
    suite = MULTITARGET_50_TOOL_POLICY_VERIFIER_SUITE
    action_space = MULTITARGET_AEGIS_ACTION_SPACE
    if args.mode == "scripted":
        runtime = {"mode": "scripted", "provider_calls_expected": 0}

        def factory(_round_index, parent):
            return ScriptedAegisStageAgent(
                scripted_multitarget_aegis_responses(
                    parent=parent,
                    verifier_suite=suite,
                    action_space=action_space,
                )
            )

    else:
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

        def factory(_round_index, _parent):
            return stage_agent

    report = run_harnessx_aegis_campaign(
        output_dir=args.output,
        verifier_suite=suite,
        action_space=action_space,
        stage_agent_factory=factory,
        max_rounds=args.max_rounds,
    )
    validation = validate_harnessx_aegis_campaign(args.output)
    print(
        json.dumps(
            {
                "runtime": runtime,
                "output": str(args.output.resolve()),
                "suite_id": suite.suite_id,
                "suite_sha256": suite.sha256,
                "action_space_id": action_space.action_space_id,
                "action_space_sha256": action_space.sha256,
                "round_count": report["round_count"],
                "provider_call_count": report["provider_call_count"],
                "final_pass_count": report["final_pass_count"],
                "final_task_count": report["final_task_count"],
                "final_resolved_variant_id": report["final_resolved_variant_id"],
                "final_resolved_variant_sha256": report[
                    "final_resolved_variant_sha256"
                ],
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
