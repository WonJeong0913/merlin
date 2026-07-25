"""Library-scaling experiment: does selection quality degrade as the library grows?

Runs the seeded condition at increasing library sizes:
seed oracle skills only, then oracle + N SkillsBench distractors.

Usage (from repo root):
    python3 -m experiments.mvp.run_library_scaling
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.merlin_harness.library import FileSkillLibrary
from src.merlin_harness.metrics import (
    clean_oracle_invocation_rate,
    no_skill_when_oracle_rate,
    shadowing_rate,
    spurious_invocation_rate,
)
from src.merlin_harness.runner import run_seeded_condition
from src.merlin_harness.skillsbench_adapter import load_skillsbench_artifacts
from src.merlin_harness.task_io import load_tasks

MVP = REPO_ROOT / "experiments" / "mvp"
VENDORED = REPO_ROOT / "experiments" / "skillsbench"
SKILLSBENCH_DISTRACTOR_COUNTS = [10, 50, None]  # None = full vendored library


def experiment_arms(controlled_distractors: list, skillsbench_distractors: list) -> list[dict]:
    """Build library-scaling arms.

    The controlled arm is intentionally small and adversarial: it checks that
    the MVP can surface true shadowing before relying on a large public corpus
    to create the same failure accidentally.
    """

    arms = [
        {
            "name": "oracle-only",
            "controlled": [],
            "skillsbench": [],
        },
        {
            "name": "controlled",
            "controlled": controlled_distractors,
            "skillsbench": [],
        },
    ]
    for count in SKILLSBENCH_DISTRACTOR_COUNTS:
        sb = skillsbench_distractors if count is None else skillsbench_distractors[:count]
        suffix = "full" if count is None else str(count)
        arms.append(
            {
                "name": f"skillsbench-{suffix}",
                "controlled": [],
                "skillsbench": sb,
            }
        )
        arms.append(
            {
                "name": f"controlled+skillsbench-{suffix}",
                "controlled": controlled_distractors,
                "skillsbench": sb,
            }
        )
    return arms


def main() -> int:
    tasks = load_tasks(MVP / "tasks")
    seeds = FileSkillLibrary(MVP / "skills").list()
    controlled_distractors = FileSkillLibrary(MVP / "distractors").list()
    all_skillsbench_distractors = load_skillsbench_artifacts(VENDORED)

    run_id = time.strftime("%Y%m%d-%H%M%S")
    results = []
    for arm in experiment_arms(controlled_distractors, all_skillsbench_distractors):
        distractors = arm["controlled"] + arm["skillsbench"]
        library = seeds + distractors
        label = arm["name"]
        workspaces = MVP / "workspaces" / run_id / f"seeded-{label}"
        traces = MVP / "runs" / run_id / f"seeded-{label}"
        records = run_seeded_condition(
            tasks=tasks,
            skills=library,
            workspaces_root=workspaces,
            traces_root=traces,
            condition=f"seeded-{label}",
        )
        invocations = [r.invocation for r in records if r.invocation]
        passed = sum(1 for inv in invocations if inv.success)
        results.append(
            {
                "condition": label,
                "library_size": len(library),
                "distractors": len(distractors),
                "controlled_distractors": len(arm["controlled"]),
                "skillsbench_distractors": len(arm["skillsbench"]),
                "tasks": len(records),
                "pass_rate": passed / len(records),
                "pi_o": clean_oracle_invocation_rate(invocations),
                "pi_m": shadowing_rate(invocations),
                "spurious_rate": spurious_invocation_rate(invocations),
                "no_skill_fallback": no_skill_when_oracle_rate(invocations),
                "mean_exposure_cost": sum(inv.cost or 0 for inv in invocations) / len(invocations),
                "per_task": [
                    {
                        "task": inv.task_id,
                        "selected": inv.selected_skill_ids,
                        "oracle": inv.oracle_skill_ids,
                        "success": inv.success,
                    }
                    for inv in invocations
                ],
            }
        )

    out = MVP / "results" / "library_scaling.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    header = f"{'condition':<30} {'lib':>5} {'pass':>6} {'pi_o':>6} {'pi_m':>6} {'spur':>6} {'fall':>6} {'cost':>8}"
    print(header)
    for row in results:
        print(
            f"{row['condition']:<30} {row['library_size']:>5} {row['pass_rate']:>6.2f} {row['pi_o']:>6.2f} "
            f"{row['pi_m']:>6.2f} {row['spurious_rate']:>6.2f} {row['no_skill_fallback']:>6.2f} "
            f"{row['mean_exposure_cost']:>8.0f}"
        )
    print(f"saved -> {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
