from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.merlin_harness.personal_workload_campaign import (
    run_personal_workload_campaign,
    validate_personal_workload_campaign,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze Merlin's 50-task personal-workload longitudinal campaign."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "experiments/mvp/results/merlin_personal_workload_50_longitudinal_v1"
        ),
    )
    args = parser.parse_args()
    summary = run_personal_workload_campaign(args.output)
    validation = validate_personal_workload_campaign(args.output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "task_count": validation["task_count"],
                "pair_count": validation["pair_count"],
                "observation_count": validation["observation_count"],
                "manifest_sha256": validation["manifest_sha256"],
                "schedule_sha256": validation["schedule_sha256"],
                "g_over_s_status": summary["g_over_s_status"],
                "valid": validation["valid"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
