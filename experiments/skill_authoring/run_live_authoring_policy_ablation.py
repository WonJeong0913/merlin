"""Execute the frozen control-vs-policy skill-authoring ablation.

This command performs twelve external requested-model calls by default. It is
fail-closed behind an exact approval phrase and writes raw provider/candidate
artifacts only under ``--raw-root`` outside the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from experiments.skill_authoring.run_authoring_policy_ablation import (
    DEFAULT_POLICY,
    frozen_tasks,
)
from src.merlin_harness.governed_provisioning import GovernedProvisioner
from src.merlin_harness.isolated_candidate_runner import (
    CandidateExecutionCase,
    PYTHON_EXECUTABLE,
    SANDBOX_EXECUTABLE,
    run_quarantined_candidate,
)
from src.merlin_harness.library import FileSkillLibrary
from src.merlin_harness.managed_creation import validate_portable_candidate
from src.merlin_harness.model_candidate_generator import CodexModelCandidateGenerator
from src.merlin_harness.model_candidate_quarantine import quarantine_model_candidate
from src.merlin_harness.models import LifecycleStatus, SkillArtifact, SkillStep
from src.merlin_harness.skill_authoring_policy import (
    CONTROL_ARM,
    POLICY_ARM,
    AuthoringTaskContract,
    build_authoring_prompt,
    load_authoring_policy,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MVP_ROOT = REPO_ROOT / "experiments" / "mvp"
DEFAULT_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
APPROVAL_PHRASE = "I_APPROVE_12_GPT56_AUTHORING_CALLS"
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")
BULLET_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$")


@dataclass(frozen=True, slots=True)
class FrozenTaskSuite:
    contract: AuthoringTaskContract
    positive_prompts: tuple[str, ...]
    negative_prompts: tuple[str, ...]
    input_path: str
    output_path: str
    cases: tuple[CandidateExecutionCase, ...]


def _json_file(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def frozen_task_suites() -> tuple[FrozenTaskSuite, ...]:
    contracts = {item.candidate_skill_id: item for item in frozen_tasks()}
    return (
        FrozenTaskSuite(
            contract=contracts["extract-todo-items"],
            positive_prompts=(
                "Extract TODO entries from backlog.todo into todo-items.json.",
                "backlog.todo의 TODO 항목을 todo-items.json으로 정리해줘.",
            ),
            negative_prompts=(
                "Count non-empty lines in input.txt and write summary.txt.",
                "Create report.md in the workspace.",
            ),
            input_path="backlog.todo",
            output_path="todo-items.json",
            cases=(
                CandidateExecutionCase(
                    "todo-target-basic",
                    "target",
                    (("backlog.todo", "TODO: fix login\nnote: later\nTODO: write tests\n"),),
                    (("todo-items.json", _json_file({"items": ["fix login", "write tests"]})),),
                ),
                CandidateExecutionCase(
                    "todo-target-spacing",
                    "target",
                    (("backlog.todo", "  TODO: ship release\nDONE: old\nTODO: update docs\n"),),
                    (("todo-items.json", _json_file({"items": ["ship release", "update docs"]})),),
                ),
                CandidateExecutionCase(
                    "todo-held-korean",
                    "held_out",
                    (("backlog.todo", "TODO: 회귀 테스트\n메모: 확인\nTODO: 문서 갱신\n"),),
                    (("todo-items.json", _json_file({"items": ["회귀 테스트", "문서 갱신"]})),),
                ),
            ),
        ),
        FrozenTaskSuite(
            contract=contracts["extract-markdown-links"],
            positive_prompts=(
                "Extract inline Markdown links from notes.md into links.json.",
                "notes.md의 마크다운 링크를 links.json으로 추출해줘.",
            ),
            negative_prompts=(
                "Extract Markdown headings from README.md.",
                "Download every URL from a webpage and save the responses.",
            ),
            input_path="notes.md",
            output_path="links.json",
            cases=(
                CandidateExecutionCase(
                    "links-target-basic",
                    "target",
                    (("notes.md", "See [OpenAI](https://openai.com).\n"),),
                    (("links.json", _json_file({"links": [{"text": "OpenAI", "url": "https://openai.com"}]})),),
                ),
                CandidateExecutionCase(
                    "links-target-ignore-image",
                    "target",
                    (("notes.md", "![logo](logo.png) and [guide](guide.md)\n"),),
                    (("links.json", _json_file({"links": [{"text": "guide", "url": "guide.md"}]})),),
                ),
                CandidateExecutionCase(
                    "links-held-unicode",
                    "held_out",
                    (("notes.md", "[문서](docs/시작.md) 다음 [API](api.md)\n"),),
                    (("links.json", _json_file({"links": [{"text": "문서", "url": "docs/시작.md"}, {"text": "API", "url": "api.md"}]})),),
                ),
            ),
        ),
        FrozenTaskSuite(
            contract=contracts["parse-key-value-config"],
            positive_prompts=(
                "Parse settings.conf into config.json without executing values.",
                "settings.conf의 키와 값을 config.json으로 변환해줘.",
            ),
            negative_prompts=(
                "Parse data.json and pretty-print it.",
                "Execute settings.conf as shell environment variables.",
            ),
            input_path="settings.conf",
            output_path="config.json",
            cases=(
                CandidateExecutionCase(
                    "config-target-basic",
                    "target",
                    (("settings.conf", "host = localhost\nport=8080\n"),),
                    (("config.json", _json_file({"host": "localhost", "port": "8080"})),),
                ),
                CandidateExecutionCase(
                    "config-target-first-equals",
                    "target",
                    (("settings.conf", "# ignored\ntoken=a=b\nempty = \n"),),
                    (("config.json", _json_file({"empty": "", "token": "a=b"})),),
                ),
                CandidateExecutionCase(
                    "config-held-unicode",
                    "held_out",
                    (("settings.conf", "이름 = 더킹\n경로=문서/자료\n"),),
                    (("config.json", _json_file({"경로": "문서/자료", "이름": "더킹"})),),
                ),
            ),
        ),
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _skill_parts(skill_markdown: str) -> tuple[str, str]:
    lines = skill_markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("generated SKILL.md has no frontmatter")
    end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    values = {}
    for line in lines[1:end]:
        key, raw = line.split(":", 1)
        values[key.strip()] = raw.strip().strip('"').strip("'")
    if set(values) != {"name", "description"}:
        raise ValueError("generated SKILL.md frontmatter differs")
    return values["description"], "\n".join(lines[end + 1 :]).strip()


def _body_exclusions(body: str) -> tuple[str, ...]:
    """Extract bounded bullet exclusions from an explicit abstention section."""

    collecting = False
    values = []
    for line in body.splitlines():
        heading = HEADING_RE.match(line)
        if heading:
            normalized = heading.group(1).lower()
            collecting = any(
                marker in normalized
                for marker in ("do not use", "when not to use", "abstain", "exclusion")
            )
            continue
        if not collecting:
            continue
        bullet = BULLET_RE.match(line)
        if bullet:
            value = bullet.group(1).strip()
            if 1 <= len(value) <= 256 and value not in values:
                values.append(value)
        elif line.strip() and values:
            collecting = False
        if len(values) == 8:
            break
    return tuple(values)


def _cli_version(executable: Path) -> str:
    result = subprocess.run(
        [str(executable), "--version"],
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError("unable to resolve Codex CLI version")
    return result.stdout.strip()


def _usage(raw_jsonl: Path) -> dict[str, int | None]:
    observed: dict[str, int | None] = {
        "input_tokens": None,
        "cached_input_tokens": None,
        "output_tokens": None,
    }
    for line in raw_jsonl.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("type") != "turn.completed" or not isinstance(event.get("usage"), dict):
            continue
        usage = event["usage"]
        for key in observed:
            value = usage.get(key)
            if isinstance(value, int) and value >= 0:
                observed[key] = value
    return observed


def _candidate_artifact(
    suite: FrozenTaskSuite,
    *,
    description: str,
    exclusions: tuple[str, ...],
    prompt_sha256: str,
) -> SkillArtifact:
    return SkillArtifact(
        id=suite.contract.candidate_skill_id,
        name=suite.contract.candidate_skill_id.replace("-", " ").title(),
        description=description,
        trigger=description,
        do_not_use_when=list(exclusions),
        steps=[
            SkillStep(
                id="run-generated-candidate",
                description="Run the quarantined candidate in the task workspace.",
                kind="script",
                inputs=[suite.input_path],
                outputs=[suite.output_path],
                script_path="scripts/run.py",
            )
        ],
        validators=[f"exact_file:{suite.output_path}"],
        expected_artifacts=[suite.output_path],
        failure_modes=["input missing", "invalid UTF-8", "workspace path escape"],
        status=LifecycleStatus.ACTIVE,
        metadata={"generator_prompt_sha256": prompt_sha256},
    )


def _run_one(
    suite: FrozenTaskSuite,
    *,
    arm: str,
    repeat: int,
    policy: object,
    generator: CodexModelCandidateGenerator,
    raw_root: Path,
) -> dict[str, object]:
    built = build_authoring_prompt(
        suite.contract,
        arm=arm,
        policy=policy if arm == POLICY_ARM else None,
    )
    run_id = f"r{repeat}-{suite.contract.candidate_skill_id}-{arm}"
    run_root = raw_root / run_id
    started = time.monotonic()
    generation = generator.generate(
        candidate_skill_id=suite.contract.candidate_skill_id,
        prompt=built.prompt,
        run_root=run_root / "generator",
    )
    generation_latency = time.monotonic() - started
    quarantine = quarantine_model_candidate(
        envelope=generation.envelope,
        output_root=run_root / "quarantine",
    )
    candidate_root = run_root / "quarantine" / "candidate" / suite.contract.candidate_skill_id
    portable = validate_portable_candidate(candidate_root, suite.contract.candidate_skill_id)
    format_pass = next(item for item in portable if item.name == "G1_format").passed
    safety_pass = all(item.passed for item in quarantine.gates) and next(
        item for item in portable if item.name == "G2_safety"
    ).passed
    description, body = _skill_parts((candidate_root / "SKILL.md").read_text(encoding="utf-8"))
    exclusions = _body_exclusions(body)
    execution = run_quarantined_candidate(
        quarantine_root=run_root / "quarantine",
        expected_manifest_sha256=quarantine.manifest_sha256,
        cases=suite.cases,
        output_root=run_root / "execution",
    )
    existing = tuple(FileSkillLibrary(MVP_ROOT / "skills").list())
    artifact = _candidate_artifact(
        suite,
        description=description,
        exclusions=exclusions,
        prompt_sha256=built.prompt_sha256,
    )
    provisioner = GovernedProvisioner(exposure_budget=1)
    positive = [
        provisioner.decide(prompt, (*existing, artifact)).primary_id == artifact.id
        for prompt in suite.positive_prompts
    ]
    negative = [
        provisioner.decide(prompt, (*existing, artifact)).primary_id != artifact.id
        for prompt in suite.negative_prompts
    ]
    target_cases = [item for item in execution.cases if item.split == "target"]
    hidden_cases = [item for item in execution.cases if item.split == "held_out"]
    off_task_count = sum(len(item.off_task_files) for item in execution.cases)
    target_rate = sum(item.passed for item in target_cases) / len(target_cases)
    hidden_rate = sum(item.passed for item in hidden_cases) / len(hidden_cases)
    route_accuracy = (sum(positive) + sum(negative)) / (len(positive) + len(negative))
    promoted = (
        format_pass
        and safety_pass
        and target_rate == 1.0
        and hidden_rate == 1.0
        and route_accuracy == 1.0
        and off_task_count == 0
    )
    candidate_files = [path for path in candidate_root.rglob("*") if path.is_file()]
    return {
        "run_id": run_id,
        "repeat": repeat,
        "task": suite.contract.candidate_skill_id,
        "arm": arm,
        "task_contract_sha256": built.task_contract_sha256,
        "prompt_sha256": built.prompt_sha256,
        "policy_sha256": built.policy_sha256,
        "generation": {
            "requested_model_id": generation.requested_model_id,
            "provider_reported_model_ids": list(generation.provider_reported_model_ids),
            "model_evidence_level": generation.model_evidence_level,
            "response_sha256": generation.response_sha256,
            "raw_trace_sha256": generation.raw_trace_sha256,
            "generation_latency_seconds": generation_latency,
            **_usage(run_root / "generator" / "provider.codex.jsonl"),
        },
        "candidate": {
            "manifest_sha256": quarantine.manifest_sha256,
            "file_count": len(candidate_files),
            "bytes": sum(path.stat().st_size for path in candidate_files),
            "skill_md_sha256": _sha256((candidate_root / "SKILL.md").read_bytes()),
            "skill_md_lines": len((candidate_root / "SKILL.md").read_text(encoding="utf-8").splitlines()),
            "description_chars": len(description),
            "exclusion_count": len(exclusions),
        },
        "metrics": {
            "format_gate": format_pass,
            "safety_gate": safety_pass,
            "target_pass_rate": target_rate,
            "held_out_pass_rate": hidden_rate,
            "negative_route_accuracy": sum(negative) / len(negative),
            "positive_route_accuracy": sum(positive) / len(positive),
            "combined_route_accuracy": route_accuracy,
            "off_task_artifact_count": off_task_count,
            "promotion": promoted,
        },
        "evidence_boundary": {
            "provider_call_observed": True,
            "candidate_executed_in_macos_confinement": True,
            "live_library_mutated": False,
            "provider_native_skill_invocation": False,
        },
    }


def _aggregate(runs: list[dict[str, object]]) -> dict[str, object]:
    by_arm: dict[str, dict[str, object]] = {}
    for arm in (CONTROL_ARM, POLICY_ARM):
        selected = [item for item in runs if item.get("arm") == arm and "metrics" in item]
        metrics = [item["metrics"] for item in selected]
        by_arm[arm] = {
            "completed_runs": len(selected),
            "rejected_before_metrics": sum(
                item.get("arm") == arm and "metrics" not in item for item in runs
            ),
            "promotion_rate": (
                sum(bool(item["promotion"]) for item in metrics) / len(metrics) if metrics else None
            ),
            "mean_target_pass_rate": (
                sum(float(item["target_pass_rate"]) for item in metrics) / len(metrics)
                if metrics
                else None
            ),
            "mean_held_out_pass_rate": (
                sum(float(item["held_out_pass_rate"]) for item in metrics) / len(metrics)
                if metrics
                else None
            ),
            "mean_negative_route_accuracy": (
                sum(float(item["negative_route_accuracy"]) for item in metrics) / len(metrics)
                if metrics
                else None
            ),
        }
    pairs = []
    indexed = {(item.get("repeat"), item.get("task"), item.get("arm")): item for item in runs}
    repeats = sorted({int(item["repeat"]) for item in runs})
    tasks = sorted({str(item["task"]) for item in runs})
    for repeat in repeats:
        for task in tasks:
            control = indexed.get((repeat, task, CONTROL_ARM))
            treatment = indexed.get((repeat, task, POLICY_ARM))
            if not control or not treatment or "metrics" not in control or "metrics" not in treatment:
                continue
            control_metrics = control["metrics"]
            treatment_metrics = treatment["metrics"]
            pairs.append(
                {
                    "repeat": repeat,
                    "task": task,
                    "promotion_delta": int(bool(treatment_metrics["promotion"])) - int(bool(control_metrics["promotion"])),
                    "target_pass_rate_delta": float(treatment_metrics["target_pass_rate"]) - float(control_metrics["target_pass_rate"]),
                    "held_out_pass_rate_delta": float(treatment_metrics["held_out_pass_rate"]) - float(control_metrics["held_out_pass_rate"]),
                    "negative_route_accuracy_delta": float(treatment_metrics["negative_route_accuracy"]) - float(control_metrics["negative_route_accuracy"]),
                }
            )
    return {"by_arm": by_arm, "paired_deltas": pairs}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--codex", type=Path, default=DEFAULT_CODEX)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--effort", default="high")
    parser.add_argument("--repeats", type=int, default=2, choices=range(1, 6))
    parser.add_argument("--approval", required=True)
    args = parser.parse_args(argv)

    expected_calls = len(frozen_task_suites()) * 2 * args.repeats
    if args.approval != APPROVAL_PHRASE or expected_calls != 12:
        parser.error(
            f"live execution requires --approval {APPROVAL_PHRASE} and the frozen 12-call design"
        )
    raw_root = args.raw_root.expanduser().resolve(strict=False)
    output = args.output.expanduser().resolve(strict=False)
    if raw_root.exists() or output.exists():
        parser.error("raw root and output must both be new")
    if raw_root.is_relative_to(REPO_ROOT):
        parser.error("raw provider artifacts must stay outside the repository")
    policy = load_authoring_policy(args.policy)
    if not args.codex.expanduser().is_file():
        parser.error("Codex executable is unavailable")
    if not SANDBOX_EXECUTABLE.is_file() or not PYTHON_EXECUTABLE.is_file():
        parser.error("pinned macOS candidate-isolation runtime is unavailable")
    # Build every prompt before the first provider call so a policy/task drift
    # cannot consume only one arm's budget.
    for suite in frozen_task_suites():
        build_authoring_prompt(suite.contract, arm=CONTROL_ARM)
        build_authoring_prompt(suite.contract, arm=POLICY_ARM, policy=policy)
    raw_root.mkdir(parents=True)
    generator = CodexModelCandidateGenerator(
        executable=args.codex,
        cli_version=_cli_version(args.codex),
        model_id=args.model,
        effort=args.effort,
        timeout_s=300,
    )
    runs: list[dict[str, object]] = []
    suites = frozen_task_suites()
    ordinal = 0
    print("Merlin live authoring-policy ablation starting", flush=True)
    print(f"planned_calls={expected_calls}", flush=True)
    for repeat in range(1, args.repeats + 1):
        arm_order = (
            (CONTROL_ARM, POLICY_ARM)
            if repeat % 2 == 1
            else (POLICY_ARM, CONTROL_ARM)
        )
        for suite in suites:
            for arm in arm_order:
                ordinal += 1
                run_label = f"r{repeat}-{suite.contract.candidate_skill_id}-{arm}"
                print(f"[{ordinal}/{expected_calls}] start {run_label}", flush=True)
                try:
                    result = _run_one(
                        suite,
                        arm=arm,
                        repeat=repeat,
                        policy=policy,
                        generator=generator,
                        raw_root=raw_root,
                    )
                except Exception as exc:
                    result = {
                        "run_id": f"r{repeat}-{suite.contract.candidate_skill_id}-{arm}",
                        "repeat": repeat,
                        "task": suite.contract.candidate_skill_id,
                        "arm": arm,
                        "error_class": type(exc).__name__,
                        "evidence_boundary": {
                            "performance_metrics_available": False,
                            "live_library_mutated": False,
                        },
                    }
                runs.append(result)
                status = (
                    "promoted"
                    if result.get("metrics", {}).get("promotion") is True
                    else "completed"
                    if "metrics" in result
                    else f"rejected:{result.get('error_class', 'unknown')}"
                )
                progress = {
                    "schema_version": 1,
                    "expected_provider_calls": expected_calls,
                    "recorded_runs": len(runs),
                    "last_run_id": run_label,
                    "last_status": status,
                }
                progress_tmp = raw_root / "progress.tmp.json"
                progress_path = raw_root / "progress.json"
                progress_tmp.write_text(
                    json.dumps(progress, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                progress_tmp.replace(progress_path)
                print(f"[{ordinal}/{expected_calls}] {status} {run_label}", flush=True)
    report = {
        "schema_version": 1,
        "experiment": "authoring-policy-ablation-v1",
        "requested_model_id": args.model,
        "effort": args.effort,
        "expected_provider_calls": expected_calls,
        "recorded_runs": len(runs),
        "policy": policy.to_safe_dict(),
        "runs": runs,
        "aggregate": _aggregate(runs),
        "evidence_boundary": {
            "provider_execution_attempted": True,
            "raw_artifacts_packaged": False,
            "live_library_mutated": False,
            "submission_package_mutated": False,
            "broad_generalization_claim": False,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print("Merlin live authoring-policy ablation")
    print(f"recorded_runs={len(runs)}/{expected_calls}")
    print(f"safe_report={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
