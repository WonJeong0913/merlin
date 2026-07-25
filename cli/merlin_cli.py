#!/usr/bin/env python3
"""Operator-facing CLI over the Merlin Python harness.

Reports governance state without launching the desktop shell or a provider
session. It reads the same `src.merlin_harness.governance_view` the app reads
through the JSONL bridge, so the terminal and the app cannot disagree about
what is on disk.

The CLI is read-only. It performs no lifecycle change, starts no provider turn,
and writes no artifact. Exit status is the operator-usable signal:

    0  the requested state was read and every validated artifact checked out
    1  an artifact exists but failed revalidation, or a required one is absent
    2  usage error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.merlin_harness import governance_view  # noqa: E402
from src.merlin_harness.library import FileSkillLibrary  # noqa: E402

DEFAULT_SKILLS_ROOT = REPO_ROOT / "experiments" / "mvp" / "skills"

EXIT_OK = 0
EXIT_UNHEALTHY = 1


def _emit(payload: Mapping[str, Any], *, as_json: bool, lines: Sequence[str]) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        for line in lines:
            print(line)


def _ratio(value: float | None, fallback: str | None) -> str:
    if value is None:
        return fallback or "unavailable"
    return f"{value:.3f}"


def _campaign_lines(campaign: Mapping[str, Any]) -> list[str]:
    if not campaign.get("artifacts_present"):
        return ["campaign          absent — no artifacts on disk"]
    lines = [
        f"campaign          {campaign.get('campaign_id')}",
        f"  manifest        {campaign.get('manifest_sha256')}",
        f"  schedule        {campaign.get('schedule_sha256')}",
        f"  observations    {campaign.get('matched_observation_count')} / {campaign.get('pair_count')} pairs"
        f"  ({campaign.get('task_count')} task contracts)",
        f"  lifecycle       {campaign.get('lifecycle_change_count')} changes",
        f"  G/S             {_ratio(campaign.get('g_over_s'), campaign.get('g_over_s_status'))}",
        f"  level 7         {'achieved' if campaign.get('level_7_achieved') else campaign.get('level_7_status')}",
    ]
    unmet = campaign.get("unmet_level_7_checks") or []
    if unmet:
        lines.append(f"  unmet           {', '.join(unmet)}")
    if campaign.get("validation_error"):
        lines.append(f"  VALIDATION      FAILED: {campaign['validation_error']}")
    return lines


def _evolution_lines(evolution: Mapping[str, Any]) -> list[str]:
    if not evolution.get("ledger_present"):
        return [
            "evolution         absent",
            f"  reason          {evolution.get('reason')}",
        ]
    lines = [
        f"evolution         {evolution.get('observation_count')} observations",
        f"  promotions      {evolution.get('promotion_count')}",
        f"  rollbacks       {evolution.get('rollback_count')}",
        f"  regressions     {evolution.get('regression_count')}",
        f"  ratio           {evolution.get('governance_to_savings_ratio')}"
        f" ({evolution.get('ratio_reason')})",
    ]
    if evolution.get("validation_error"):
        lines.append(f"  VALIDATION      FAILED: {evolution['validation_error']}")
    return lines


def _invocation_lines(invocation: Mapping[str, Any]) -> list[str]:
    complete = invocation.get("provider_native_evidence_complete")
    lines = [f"invocation        {'complete' if complete else 'incomplete'}"]
    if invocation.get("blocking_reason"):
        lines.append(f"  blocked by      {invocation['blocking_reason']}")
    if invocation.get("consequence"):
        lines.append(f"  consequence     {invocation['consequence']}")
    for tier in invocation.get("corroboration_tiers") or []:
        mark = "o" if tier.get("available") else "x"
        attribution = "self-attested" if tier.get("self_attested") else "independent"
        lines.append(f"  {mark} {tier.get('tier'):<24} {attribution}")
        lines.append(f"      establishes {tier.get('establishes')}")
        lines.append(f"      limit       {tier.get('limit')}")
    return lines


def _lifecycle_lines(operations: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = ["lifecycle"]
    for operation in operations:
        mark = "o" if operation.get("available") else "x"
        lines.append(
            f"  {mark} {operation.get('kind'):<9} {operation.get('observed_count'):>3}"
            f"  {operation.get('reason')}"
        )
    return lines


def _campaign_is_healthy(campaign: Mapping[str, Any]) -> bool:
    """Absent artifacts and failed revalidation are both operator problems.

    An empty ledger is *not* a problem: zero observations is the honest state
    of a campaign that has not run yet, and the validator accepts it.
    """
    return bool(campaign.get("artifacts_present")) and bool(campaign.get("validated"))


def _evolution_is_healthy(evolution: Mapping[str, Any]) -> bool:
    """An absent ledger is neutral; an existing invalid one is unhealthy."""

    return not evolution.get("ledger_present") or bool(evolution.get("validated"))


def command_governance(args: argparse.Namespace) -> int:
    summary = governance_view.harness_governance_summary()
    lines = [
        *_campaign_lines(summary["campaign"]),
        "",
        *_invocation_lines(summary["invocation_evidence"]),
        "",
        *_evolution_lines(summary["evolution"]),
        "",
        *_lifecycle_lines(summary["lifecycle_operations"]),
    ]
    _emit(summary, as_json=args.json, lines=lines)
    healthy = _campaign_is_healthy(summary["campaign"]) and _evolution_is_healthy(
        summary["evolution"]
    )
    return EXIT_OK if healthy else EXIT_UNHEALTHY


def command_campaign(args: argparse.Namespace) -> int:
    campaign = governance_view._campaign_governance()
    _emit(campaign, as_json=args.json, lines=_campaign_lines(campaign))
    return EXIT_OK if _campaign_is_healthy(campaign) else EXIT_UNHEALTHY


def command_evolution(args: argparse.Namespace) -> int:
    evolution = governance_view._evolution_governance()
    _emit(evolution, as_json=args.json, lines=_evolution_lines(evolution))
    # An absent ledger is a legitimate state, not a failure. A ledger that
    # exists and fails its hash chain is.
    if evolution.get("ledger_present") and not evolution.get("validated"):
        return EXIT_UNHEALTHY
    return EXIT_OK


def command_skills(args: argparse.Namespace) -> int:
    root = Path(args.skills_root).expanduser()
    if not root.is_dir():
        print(f"skills root is absent: {root}", file=sys.stderr)
        return EXIT_UNHEALTHY
    skills = FileSkillLibrary(root).list()
    payload = {
        "skills_root": str(root),
        "count": len(skills),
        "skills": [
            {
                "id": skill.id,
                "name": skill.name,
                "status": skill.status.value,
                "version": skill.version,
                "trigger": skill.trigger,
                "validators": list(skill.validators),
                "step_count": len(skill.steps),
            }
            for skill in skills
        ],
    }
    lines = [f"skills            {len(skills)} in {root}"]
    for skill in skills:
        lines.append(
            f"  {skill.status.value:<10} v{skill.version:<3} {skill.id:<28} {skill.name}"
        )
    _emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="merlin",
        description="Read-only operator view over the Merlin harness.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str, handler: Any) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--json", action="store_true", help="emit the raw payload")
        sub.set_defaults(handler=handler)
        return sub

    add("governance", "full governance state", command_governance)
    add("campaign", "longitudinal campaign standing, revalidated", command_campaign)
    add("evolution", "harness-evolution ledger summary", command_evolution)
    skills = add("skills", "active skill library", command_skills)
    skills.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
