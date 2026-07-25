from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.merlin_harness.personal_workload_campaign import (
    append_personal_workload_observation,
    observation_from_dict,
    validate_personal_workload_campaign,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Append one completed matched observation to the frozen "
            "personal-workload ledger."
        )
    )
    parser.add_argument(
        "--campaign",
        type=Path,
        default=Path(
            "experiments/mvp/results/merlin_personal_workload_50_longitudinal_v1"
        ),
    )
    parser.add_argument("--observation", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.observation.read_text(encoding="utf-8"))
    observation = observation_from_dict(payload)
    summary = append_personal_workload_observation(
        args.campaign,
        observation,
    )
    validation = validate_personal_workload_campaign(args.campaign)
    print(
        json.dumps(
            {
                "observation_id": observation.observation_id,
                "matched_observation_count": summary[
                    "matched_observation_count"
                ],
                "verified_turn_savings": summary["verified_turn_savings"],
                "governance_turns_spent": summary[
                    "governance_turns_spent"
                ],
                "g_over_s": summary["g_over_s"],
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
