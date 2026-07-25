"""Run Merlin's deterministic shadowing-diagnosis and recovery demo.

This command is the one-shot adapter over :mod:`lifecycle_session`.  The
localhost Console calls the same incremental service methods one action at a
time, so both surfaces execute the same harness runtime and promotion gates.

Usage:
    PYTHONPATH=. python3 -m experiments.mvp.run_lifecycle_recovery_demo
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from src.merlin_harness.models import LifecyclePromotionCriteria

from .lifecycle_session import MVP_ROOT, run_complete_session
from .reporting import render_control_room


def _write_html_report(report: dict[str, Any], destination: Path) -> None:
    destination.write_text(render_control_room(report), encoding="utf-8")


def open_generated_report(output_dir: str | Path) -> None:
    """Open an already-generated report on macOS without involving a shell."""

    report_path = Path(output_dir) / "lifecycle_recovery.html"
    subprocess.run(["open", str(report_path)], check=False)


def run_lifecycle_recovery_demo(
    output_dir: str | Path,
    *,
    promotion_criteria: LifecyclePromotionCriteria | None = None,
) -> dict[str, Any]:
    """Run the shared session end-to-end and persist its public report."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    report = run_complete_session(promotion_criteria=promotion_criteria)
    (destination / "lifecycle_recovery.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_html_report(report, destination / "lifecycle_recovery.html")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Merlin shadowing recovery demo.")
    parser.add_argument(
        "--output",
        default=str(MVP_ROOT / "results" / "lifecycle_recovery"),
        help="Directory for lifecycle_recovery.json and lifecycle_recovery.html.",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the generated HTML report with macOS open after the demo finishes.",
    )
    args = parser.parse_args(argv)
    report = run_lifecycle_recovery_demo(args.output)
    recovery = report["recovery_delta"]
    print("Merlin lifecycle recovery demo")
    print(f"decisions={len(report['lifecycle_decisions'])}")
    print(f"pass_rate_gain={recovery['pass_rate_gain']:+.0%}")
    print(f"pi_o_gain={recovery['pi_o_gain']:+.0%}")
    print(f"pi_m_change={recovery['pi_m_change']:+.0%}")
    print(f"saved -> {Path(args.output)}")
    if args.open:
        open_generated_report(args.output)
        print("opened -> lifecycle_recovery.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
