"""Run account-auth CLI smoke tests for every condition in backend-matrix.json."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_MATRIX = ROOT / "backend-matrix.json"
DEFAULT_TASK = Path("experiments/mvp/tasks/answer-yes.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run backend matrix CLI smoke tests.")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--task", type=Path, default=DEFAULT_TASK)
    parser.add_argument("--only", nargs="*", default=None, help="Optional condition ids to run.")
    parser.add_argument("--timeout-s", type=float, default=600.0)
    args = parser.parse_args(argv)

    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    selected = set(args.only or [])
    conditions = [
        condition
        for condition in matrix["conditions"]
        if not selected or condition["id"] in selected
    ]
    if selected and len(conditions) != len(selected):
        found = {condition["id"] for condition in conditions}
        missing = sorted(selected - found)
        parser.error(f"Unknown condition ids: {', '.join(missing)}")

    aggregate_dir = ROOT / "results" / f"matrix-smoke-{time.strftime('%Y%m%d-%H%M%S')}"
    aggregate_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    script = ROOT / "run_cli_smoke.py"
    for condition in conditions:
        run_id = f"{condition['id']}-{time.strftime('%Y%m%d-%H%M%S')}"
        command = [
            sys.executable,
            str(script),
            "--backend",
            condition["backend"],
            "--model",
            condition["model_id"],
            "--effort",
            condition["effort"],
            "--task",
            str(args.task),
            "--timeout-s",
            str(args.timeout_s),
            "--run-id",
            run_id,
        ]
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        record = {
            "condition_id": condition["id"],
            "run_id": run_id,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr[-2000:],
        }
        results.append(record)
        print(json.dumps(record, ensure_ascii=False))

    summary = {
        "matrix": str(args.matrix),
        "task": str(args.task),
        "conditions": [condition["id"] for condition in conditions],
        "results": results,
    }
    (aggregate_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if all(result["returncode"] == 0 for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
