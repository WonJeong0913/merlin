"""Reclassify an oracle-readiness run without mutating its raw summary.

The first ONE full-corpus run parsed only integer reward strings. This tool
re-reads ``reward.txt`` as a float, preserves the source artifact by hash, and
writes a derived summary suitable for scheduling. The default strict policy
also rejects a full reward when captured pytest output contains failed tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from experiments.skillsbench.run_oracle_readiness import (
    CommandReport,
    classify_verifier_result,
    parse_reward,
)


PYTEST_FAILURE_MARKERS = (
    re.compile(r"(?m)^.*\sFAILED\s+\[\s*\d+%\]$"),
    re.compile(r"(?m)^=+\s+\d+ failed(?:,|\s)", re.IGNORECASE),
)


def source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verifier_has_failed_assertions(report: dict[str, Any] | None) -> bool:
    if not report:
        return False
    output = "\n".join(
        str(report.get(key) or "")
        for key in ("stdout_tail", "stderr_tail")
    )
    return any(pattern.search(output) for pattern in PYTEST_FAILURE_MARKERS)


def locate_reward_text(source_path: Path, record: dict[str, Any]) -> tuple[str | None, str | None]:
    candidates: list[Path] = []
    logs_dir = record.get("logs_dir")
    if logs_dir:
        candidates.append(Path(logs_dir) / "verifier" / "reward.txt")
    candidates.append(
        source_path.parent / "tasks" / record["task_id"] / "logs" / "verifier" / "reward.txt"
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8", errors="replace").strip(), str(candidate)
    return None, None


def _report_from_record(record: dict[str, Any]) -> CommandReport | None:
    raw = record.get("commands", {}).get("verifier")
    if not raw:
        return None
    return CommandReport(**raw)


def _summarize_dicts(records: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    for record in records:
        status = record["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    observed_rewards = [float(record["reward"]) for record in records if record.get("reward") is not None]
    return {
        "task_count": len(records),
        "passed": sum(bool(record.get("passed")) for record in records),
        "reward_observed": len(observed_rewards),
        "mean_observed_reward": sum(observed_rewards) / len(observed_rewards) if observed_rewards else None,
        "status_counts": dict(sorted(status_counts.items())),
    }


def reclassify_summary(
    source_path: Path,
    *,
    policy: str = "strict",
) -> dict[str, Any]:
    if policy not in {"strict", "reward-authoritative"}:
        raise ValueError(f"Unsupported policy: {policy}")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    corrected_records: list[dict[str, Any]] = []
    audit_records: list[dict[str, Any]] = []

    for raw_record in source.get("records", []):
        record = deepcopy(raw_record)
        reward_text, reward_path = locate_reward_text(source_path, record)
        observed_reward = parse_reward(reward_text) if reward_text is not None else None
        report = _report_from_record(record)
        failed_assertions = verifier_has_failed_assertions(
            record.get("commands", {}).get("verifier")
        )

        reward_status = record.get("status", "unknown")
        reward_passed = bool(record.get("passed"))
        if report is not None:
            reward_status, reward_passed = classify_verifier_result(
                report,
                observed_reward,
                strict_assertions=False,
            )

        strict_status = reward_status
        strict_passed = reward_passed
        if reward_passed and failed_assertions:
            strict_status = "verifier_contract_inconsistent"
            strict_passed = False

        if policy == "strict":
            record["status"] = strict_status
            record["passed"] = strict_passed
        else:
            record["status"] = reward_status
            record["passed"] = reward_passed
        record["reward"] = observed_reward
        record["reclassification"] = {
            "source_status": raw_record.get("status"),
            "source_passed": bool(raw_record.get("passed")),
            "source_reward": raw_record.get("reward"),
            "reward_text": reward_text,
            "reward_path": reward_path,
            "reward_authoritative_status": reward_status,
            "strict_status": strict_status,
            "verifier_failed_assertions_in_captured_output": failed_assertions,
        }
        corrected_records.append(record)
        audit_records.append(
            {
                "task_id": record["task_id"],
                **record["reclassification"],
            }
        )

    reward_view = []
    strict_view = []
    for record in corrected_records:
        reward_copy = deepcopy(record)
        strict_copy = deepcopy(record)
        reward_copy["status"] = record["reclassification"]["reward_authoritative_status"]
        reward_copy["passed"] = reward_copy["status"] == "passed"
        strict_copy["status"] = record["reclassification"]["strict_status"]
        strict_copy["passed"] = strict_copy["status"] == "passed"
        reward_view.append(reward_copy)
        strict_view.append(strict_copy)

    return {
        "run_id": f"{source.get('run_id', source_path.parent.name)}-reclassified-{policy}",
        "derived_artifact": True,
        "source_summary": str(source_path),
        "source_summary_sha256": source_sha256(source_path),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "policy": policy,
        "policy_note": (
            "strict requires full reward and no failed pytest assertions in captured verifier output"
            if policy == "strict"
            else "reward-authoritative follows verifier reward.txt in [0,1]"
        ),
        "summary": _summarize_dicts(corrected_records),
        "alternative_summaries": {
            "strict": _summarize_dicts(strict_view),
            "reward_authoritative": _summarize_dicts(reward_view),
        },
        "records": corrected_records,
        "audit_records": audit_records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--policy", choices=("strict", "reward-authoritative"), default="strict")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    output = reclassify_summary(args.source, policy=args.policy)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(output["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
