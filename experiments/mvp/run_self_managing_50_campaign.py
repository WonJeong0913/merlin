from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.merlin_harness.self_managing_campaign import (
    run_self_managing_50_campaign,
    validate_self_managing_50_campaign,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Merlin's frozen 50-task local governance campaign."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "experiments/mvp/results/self_managing_governance_50_v1"
        ),
    )
    args = parser.parse_args()
    report = run_self_managing_50_campaign(args.output)
    validation = validate_self_managing_50_campaign(args.output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "task_count": report["task_count"],
                "pass_count": report["pass_count"],
                "fail_count": report["fail_count"],
                "suite_sha256": report["suite_sha256"],
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
