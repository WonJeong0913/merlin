#!/usr/bin/env python3
"""Measure deterministic provisioning against the SkillsBench oracle mapping.

Scope, stated up front because it bounds every number below:

- This measures `deterministic` routing only — the lexical `GovernedProvisioner`.
  It does not touch `semantic` mode, which routes through a provider-backed
  `SemanticSkillRouter`. The shipped default combines the two.
- It measures **selection**, not task success. Nothing is executed.

Ground truth is upstream, not ours: `curated_skill_variants` in
`experiments/skillsbench/readiness-87.json`. That field matches each task's
`per_task_skill_dirs` (`environment/skills/<name>`), i.e. the skills SkillsBench
ships inside the task environment, and `create_library_scale_manifest.py` uses
the same field as its reference set.

Two arms, because query authorship is the one thing we cannot take from upstream
(the task corpus is not vendored here — only the skill library is):

- **mechanical** — the query is the task_id with hyphens turned into spaces.
  No human judgement, all 87 tasks, reproducible by construction. This is the
  arm to quote.
- **handwritten** — six `easy` tasks with a locally written `cued` query naming
  the oracle's format/tool and an `uncued` paraphrase that does not. Small and
  author-biased, so it is reported as a probe with its statistics, not a result.

    python3 experiments/mvp/measure_deterministic_selection.py
    python3 experiments/mvp/measure_deterministic_selection.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.merlin_harness.governed_provisioning import (  # noqa: E402
    GovernedProvisioner,
)
from src.merlin_harness.skillsbench_adapter import (  # noqa: E402
    load_skillsbench_artifacts,
)

SKILLSBENCH_ROOT = REPO_ROOT / "experiments" / "skillsbench"
BUDGETS = (1, 3, 5, 10)

HANDWRITTEN: dict[str, dict[str, str]] = {
    "court-form-filling": {
        "cued": "Fill out a court form PDF with the provided applicant details.",
        "uncued": "Complete the applicant fields on the official legal document and save it.",
    },
    "dialogue-parser": {
        "cued": "Parse a dialogue transcript into a structured dialogue graph.",
        "uncued": (
            "Turn the conversation transcript into a structured representation "
            "of who said what to whom."
        ),
    },
    "offer-letter-generator": {
        "cued": "Generate an offer letter as a Word docx document.",
        "uncued": "Produce a formatted employment offer letter for the candidate.",
    },
    "powerlifting-coef-calc": {
        "cued": "Calculate powerlifting coefficients into an xlsx spreadsheet.",
        "uncued": "Work out the strength scores for each lifter and lay them out in a table.",
    },
    "fix-build-agentops": {
        "cued": "Fix the failing CI build for this Python project.",
        "uncued": "The automated pipeline is failing on this repository. Diagnose and repair it.",
    },
    "fix-build-google-auto": {
        "cued": "Fix the failing Maven build and its dependency configuration.",
        "uncued": "The Java project will not compile because of its dependency setup. Repair it.",
    },
}


def _exact_two_sided_sign_p(discordant_a: int, discordant_b: int) -> float:
    """Two-sided exact sign test over discordant pairs (McNemar, exact form)."""

    n = discordant_a + discordant_b
    if n == 0:
        return 1.0
    observed = min(discordant_a, discordant_b)
    tail = sum(
        len(list(combinations(range(n), k))) for k in range(observed + 1)
    ) / (2**n)
    return min(1.0, 2 * tail)


def _load() -> tuple[list[Any], dict[str, set[str]]]:
    skills = load_skillsbench_artifacts(SKILLSBENCH_ROOT)
    by_variant = {skill.metadata["variant"]: skill.id for skill in skills}
    tasks = json.loads(
        (SKILLSBENCH_ROOT / "readiness-87.json").read_text(encoding="utf-8")
    )["tasks"]
    oracle = {
        task["task_id"]: {
            by_variant[variant]
            for variant in task["curated_skill_variants"]
            if variant in by_variant
        }
        for task in tasks
    }
    return skills, oracle


def _score(
    queries: dict[str, str],
    oracle: dict[str, set[str]],
    skills: Sequence[Any],
    budget: int,
) -> dict[str, Any]:
    provisioner = GovernedProvisioner(exposure_budget=budget)
    rows = []
    for task_id, query in queries.items():
        decision = provisioner.decide(query, skills)
        chosen = [c.skill_id for c in decision.candidates if c.provisioned]
        matched = sorted(set(chosen) & oracle[task_id])
        rows.append(
            {
                "task_id": task_id,
                # The exact string handed to the provisioner, stored verbatim so
                # a reader can check the task was conveyed faithfully rather than
                # trusting a hash. See `query_storage_note` in the run record for
                # why this differs from the production path.
                "query": query,
                "oracle_skill_ids": sorted(oracle[task_id]),
                "provisioned_skill_ids": chosen,
                "matched_skill_ids": matched,
                "hit": bool(matched),
                "abstain_reason": decision.abstain_reason,
                "candidate_evidence": [
                    {
                        "skill_id": candidate.skill_id,
                        "rank": candidate.rank,
                        "positive_score": round(candidate.positive_score, 6),
                        "positive_trigger_score": round(
                            candidate.positive_trigger_score, 6
                        ),
                        "positive_description_score": round(
                            candidate.positive_description_score, 6
                        ),
                        "negative_score": round(candidate.negative_score, 6),
                        "exact_anchor_evidence": candidate.exact_anchor_evidence,
                        "is_oracle": candidate.skill_id in oracle[task_id],
                    }
                    for candidate in decision.candidates
                    if candidate.provisioned
                ],
                "active_library_snapshot_sha256": decision.active_library_snapshot_sha256,
            }
        )
    slots = sum(len(row["provisioned_skill_ids"]) for row in rows)
    true_positives = sum(len(row["matched_skill_ids"]) for row in rows)
    return {
        "rows": rows,
        "hits": sum(row["hit"] for row in rows),
        "total": len(rows),
        "true_positives": true_positives,
        "slots": slots,
        "recall_at_k": round(sum(row["hit"] for row in rows) / len(rows), 4),
        "precision_at_k": round(true_positives / slots, 4) if slots else None,
    }


def _oracle_manifest_sha256() -> str:
    import hashlib

    return hashlib.sha256(
        (SKILLSBENCH_ROOT / "readiness-87.json").read_bytes()
    ).hexdigest()


def measure() -> dict[str, Any]:
    skills, oracle = _load()

    mechanical_queries = {
        task_id: task_id.replace("-", " ") for task_id in oracle
    }
    mechanical = {
        str(budget): _score(mechanical_queries, oracle, skills, budget)
        for budget in BUDGETS
    }

    handwritten: dict[str, Any] = {}
    for arm in ("cued", "uncued"):
        queries = {task_id: pair[arm] for task_id, pair in HANDWRITTEN.items()}
        handwritten[arm] = _score(queries, oracle, skills, 3)

    cued_rows = {row["task_id"]: row["hit"] for row in handwritten["cued"]["rows"]}
    uncued_rows = {row["task_id"]: row["hit"] for row in handwritten["uncued"]["rows"]}
    cued_only = sum(
        1 for task_id in cued_rows if cued_rows[task_id] and not uncued_rows[task_id]
    )
    uncued_only = sum(
        1 for task_id in cued_rows if uncued_rows[task_id] and not cued_rows[task_id]
    )

    return {
        "schema_version": "merlin-deterministic-selection-measurement-v2",
        "routing_mode": "deterministic",
        "library_skill_count": len(skills),
        "oracle_source": "experiments/skillsbench/readiness-87.json:curated_skill_variants",
        # Anchors so a reader can tell which corpus and manifest produced this
        # run, and re-derive it, without trusting the surrounding prose.
        "oracle_manifest_sha256": _oracle_manifest_sha256(),
        "library_snapshot_sha256": mechanical["1"]["rows"][0][
            "active_library_snapshot_sha256"
        ],
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": sys.version.split()[0],
        "mechanical": {
            "query_rule": "task_id with hyphens replaced by spaces",
            "task_count": len(mechanical_queries),
            "by_budget": mechanical,
        },
        "handwritten_probe": {
            "task_count": len(HANDWRITTEN),
            "difficulty": "easy",
            "exposure_budget": 3,
            "arms": handwritten,
            "discordant_cued_only": cued_only,
            "discordant_uncued_only": uncued_only,
            "exact_two_sided_p": round(
                _exact_two_sided_sign_p(cued_only, uncued_only), 4
            ),
        },
        "boundary": {
            "measures_lexical_provisioner_only": True,
            "semantic_router_not_exercised": True,
            "selection_only_nothing_executed": True,
            "mechanical_queries_are_not_the_real_task_prompts": True,
            "handwritten_queries_authored_locally": True,
            "oracle_authored_upstream": True,
        },
        "query_storage_note": (
            "Every query is stored verbatim here so a reader can audit whether "
            "each task was conveyed faithfully. This is safe only because these "
            "queries are derived from public SkillsBench task IDs and contain no "
            "user content. The production provisioning path deliberately does the "
            "opposite: GovernedProvisioningDecision.to_safe_dict sets "
            "query_stored=False and keeps only query_sha256. Do not copy this "
            "record's behaviour into any path that handles real user queries."
        ),
    }


def _write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise SystemExit(
            f"refusing to overwrite an existing run artifact: {path}"
        ) from exc


def _queries_markdown(result: dict[str, Any]) -> str:
    """Human-readable audit surface: what was asked, and what came back."""

    lines = [
        "# Deterministic selection run — queries as sent",
        "",
        "Every query below is the exact string handed to `GovernedProvisioner`.",
        "Read this to check the task was conveyed faithfully; the oracle column",
        "is upstream ground truth, not ours.",
        "",
        f"- library: {result['library_skill_count']} skills",
        f"- oracle: `{result['oracle_source']}`",
        f"- routing mode: `{result['routing_mode']}` (lexical only)",
        "",
    ]
    mech = result["mechanical"]
    for budget, payload in mech["by_budget"].items():
        lines += [
            f"## mechanical, k={budget} — query rule: {mech['query_rule']}",
            "",
            "| task_id | query as sent | oracle | provisioned | hit |",
            "|---|---|---|---|---|",
        ]
        for row in payload["rows"]:
            oracle = ", ".join(item.split("/", 1)[-1] for item in row["oracle_skill_ids"])
            chosen = ", ".join(
                item.split("/", 1)[-1] for item in row["provisioned_skill_ids"]
            ) or "(none)"
            lines.append(
                f"| `{row['task_id']}` | `{row['query']}` | {oracle} | {chosen} | "
                f"{'yes' if row['hit'] else 'no'} |"
            )
        lines.append("")

    probe = result["handwritten_probe"]
    lines += [
        f"## handwritten probe, k={probe['exposure_budget']} — queries authored locally",
        "",
        "These were written by the same author who knew the oracle. That is the",
        "bias this file exists to make visible.",
        "",
        "| arm | task_id | query as sent | oracle | provisioned | hit |",
        "|---|---|---|---|---|---|",
    ]
    for arm, payload in probe["arms"].items():
        for row in payload["rows"]:
            oracle = ", ".join(item.split("/", 1)[-1] for item in row["oracle_skill_ids"])
            chosen = ", ".join(
                item.split("/", 1)[-1] for item in row["provisioned_skill_ids"]
            ) or "(none)"
            lines.append(
                f"| {arm} | `{row['task_id']}` | `{row['query']}` | {oracle} | "
                f"{chosen} | {'yes' if row['hit'] else 'no'} |"
            )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the raw payload")
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "experiments/mvp/results/deterministic_selection_v1",
        help="directory for the durable run record (refuses to overwrite)",
    )
    parser.add_argument(
        "--no-record", action="store_true", help="skip writing the run record"
    )
    args = parser.parse_args(argv)

    result = measure()

    if not args.no_record:
        payload = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False)
        _write_new(args.out / "run.json", payload + "\n")
        _write_new(args.out / "queries.md", _queries_markdown(result))
        print(f"recorded -> {args.out}")

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
        return 0

    print(
        f"library={result['library_skill_count']} skills   "
        f"mode={result['routing_mode']}   selection only, nothing executed"
    )
    mech = result["mechanical"]
    print(f"\n=== mechanical: {mech['task_count']} tasks, query = {mech['query_rule']} ===")
    print(f"  {'k':>3}  {'recall@k':>10}  {'precision@k':>12}")
    for budget, payload in mech["by_budget"].items():
        print(
            f"  {budget:>3}  {payload['hits']:>4}/{payload['total']:<5}"
            f"  {payload['true_positives']:>5}/{payload['slots']:<6}"
        )

    probe = result["handwritten_probe"]
    print(
        f"\n=== handwritten probe: {probe['task_count']} easy tasks, k={probe['exposure_budget']} ==="
    )
    for arm, payload in probe["arms"].items():
        print(
            f"  {arm:<7} recall={payload['hits']}/{payload['total']}"
            f"   precision={payload['true_positives']}/{payload['slots']}"
        )
    print(
        f"  discordant pairs: cued-only={probe['discordant_cued_only']}, "
        f"uncued-only={probe['discordant_uncued_only']}, "
        f"exact two-sided p={probe['exact_two_sided_p']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
