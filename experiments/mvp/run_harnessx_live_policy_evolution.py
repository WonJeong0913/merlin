"""Run and independently validate one deterministic live-policy evolution round."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.merlin_harness.harnessx_policy_evolution import (
    evolve_live_tool_policy,
    validate_live_tool_policy_evolution,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evolve the typed HarnessX live tool policy.")
    parser.add_argument(
        "--output",
        default="experiments/mvp/results/harnessx_live_policy_evolution_v1",
    )
    args = parser.parse_args(argv)
    report = evolve_live_tool_policy(args.output)
    validation = validate_live_tool_policy_evolution(args.output)
    print("Merlin HarnessX live-policy evolution")
    print(f"parent={report['parent_variant_sha256']}")
    print(
        "rejected="
        f"{report['rejected_revision']['gate']['resolution']}"
    )
    print(
        "promoted="
        f"{report['promoted_revision']['gate']['resolution']}"
    )
    print(f"resolved={validation['resolved_variant_sha256']}")
    print(f"evidence_sha256={validation['evidence_sha256']}")
    print(f"saved -> {Path(args.output) / 'evolution-report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

