from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from src.merlin_harness.harness_evolution_ledger import (
    HarnessEvolutionLedger,
    append_harness_evolution_observation,
    load_and_validate_harness_evolution_ledger,
    observations_from_aegis_campaign,
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize Merlin's longitudinal harness-evolution ledger."
    )
    parser.add_argument(
        "--campaign",
        type=Path,
        default=Path(
            "experiments/mvp/results/harnessx_aegis_multiround_scripted_v1"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "experiments/mvp/results/harness_evolution_longitudinal_v1"
        ),
    )
    args = parser.parse_args()
    root = args.output.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    observations = observations_from_aegis_campaign(
        args.campaign,
        campaign_id="harnessx-aegis-multiround-scripted-v1",
        verifier_epoch_id="live-policy-multitarget-50-v1",
        resource_unit="provider_turns",
        resource_dimension_id="model-free",
        resource_window_id="offline-scripted-v1",
    )
    ledger_path = root / "evolution.jsonl"
    for observation in observations:
        append_harness_evolution_observation(ledger_path, observation)
    records = load_and_validate_harness_evolution_ledger(ledger_path)
    summary = HarnessEvolutionLedger(observations).summarize()
    report = {
        "schema_version": "merlin-harness-evolution-summary-v1",
        "record_count": len(records),
        "ledger_tail_sha256": records[-1]["record_sha256"],
        "summary": asdict(summary),
        "evidence_boundary": {
            "model_free_campaign": True,
            "verified_direct_savings_observed": False,
            "gs_ratio_claimed": False,
            "provider_model_comparison_included": False,
        },
    }
    report["evidence_sha256"] = _sha256_json(report)
    with (root / "summary.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
