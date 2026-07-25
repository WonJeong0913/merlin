"""Freeze the control-vs-policy GPT-5.6 skill-authoring experiment plan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.merlin_harness.skill_authoring_policy import (
    AuthoringTaskContract,
    build_ablation_plan,
    load_authoring_policy,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = (
    REPO_ROOT
    / "experiments"
    / "skill_authoring"
    / "policies"
    / "author-governed-skills"
)


def frozen_tasks() -> tuple[AuthoringTaskContract, ...]:
    return (
        AuthoringTaskContract(
            candidate_skill_id="extract-todo-items",
            behavior_contract=(
                "Read UTF-8 backlog.todo under the workspace.",
                "Collect TODO: entries after optional leading whitespace in source order.",
                "Write todo-items.json as an indented JSON object with an items string array and one trailing newline.",
                "Do not handle general line counting or arbitrary file creation.",
            ),
            visible_examples=(
                "TODO: fix login / note / TODO: write tests -> [fix login, write tests]",
                "two leading spaces before TODO: ship release / DONE / TODO: update docs -> [ship release, update docs]",
            ),
            allowed_imports=("argparse", "json", "pathlib.Path"),
        ),
        AuthoringTaskContract(
            candidate_skill_id="extract-markdown-links",
            behavior_contract=(
                "Read UTF-8 notes.md under the workspace.",
                "Collect inline Markdown links in source order while ignoring images.",
                "Write links.json with objects containing text and url, indented and followed by one newline.",
                "Do not handle Markdown heading extraction, line counting, or arbitrary downloads.",
            ),
            visible_examples=(
                "See [OpenAI](https://openai.com) -> one text/url object",
                "Ignore ![logo](logo.png) but collect [guide](guide.md)",
            ),
            allowed_imports=("argparse", "json", "pathlib.Path", "re"),
        ),
        AuthoringTaskContract(
            candidate_skill_id="parse-key-value-config",
            behavior_contract=(
                "Read UTF-8 settings.conf under the workspace.",
                "Parse the first equals sign on non-empty, non-comment lines and trim key/value edges.",
                "Write config.json as a sorted indented JSON object followed by one newline.",
                "Do not parse JSON, YAML, shell expansion, or execute configuration values.",
            ),
            visible_examples=(
                "host = localhost / port=8080 -> host and port strings",
                "token=a=b preserves a=b as the value",
            ),
            allowed_imports=("argparse", "json", "pathlib.Path"),
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--effort", default="high")
    args = parser.parse_args(argv)

    output = args.output.expanduser().resolve(strict=False)
    if output.exists():
        parser.error(f"refusing to overwrite output: {output}")
    policy = load_authoring_policy(args.policy)
    report = build_ablation_plan(
        frozen_tasks(),
        policy=policy,
        repeats=args.repeats,
        model_id=args.model,
        effort=args.effort,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print("Merlin authoring-policy ablation plan")
    print(f"tasks={report['task_count']}")
    print(f"arms={report['arm_count']}")
    print(f"repeats={report['repeats']}")
    print(f"expected_provider_calls={report['expected_provider_calls']}")
    print("provider_calls_executed=false")
    print(f"saved={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
