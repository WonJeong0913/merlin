"""Execute SkillsBench oracle solutions inside Docker and record readiness.

This is the executable half of E1/B3. It checks whether each vendored
SkillsBench task can be built, solved by its upstream oracle script, and
validated by its upstream verifier in the local Docker runtime.

Run from the repository root:

    python3 experiments/skillsbench/run_oracle_readiness.py --limit 1

For WSL setups without systemd, the script can start a foreground child
``dockerd`` process for the duration of the run.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import signal
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
TASKS_ROOT = ROOT / "tasks"
DEFAULT_RUNS_ROOT = ROOT / "runs" / "oracle-readiness"
TAIL_CHARS = 6000
ENVIRONMENT_CONTROL_FIELDS = {
    "network_mode",
    "build_timeout_sec",
    "os",
    "cpus",
    "memory_mb",
    "storage_mb",
    "gpus",
    "workdir",
}


@dataclass(slots=True)
class CommandReport:
    argv: list[str]
    exit_code: int
    duration_sec: float
    timed_out: bool = False
    stdout_tail: str = ""
    stderr_tail: str = ""


@dataclass(slots=True)
class OracleReadinessRecord:
    task_id: str
    status: str
    passed: bool
    image: str
    container: str
    reward: float | None = None
    logs_dir: str | None = None
    commands: dict[str, CommandReport] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def normalize_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def tail_text(text: str, limit: int = TAIL_CHARS) -> str:
    return text[-limit:] if len(text) > limit else text


def run_command(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout_sec: int,
) -> CommandReport:
    start = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_sec,
        )
        return CommandReport(
            argv=argv,
            exit_code=completed.returncode,
            duration_sec=round(time.monotonic() - start, 3),
            stdout_tail=tail_text(completed.stdout),
            stderr_tail=tail_text(completed.stderr),
        )
    except subprocess.TimeoutExpired as exc:
        return CommandReport(
            argv=argv,
            exit_code=124,
            duration_sec=round(time.monotonic() - start, 3),
            timed_out=True,
            stdout_tail=tail_text(normalize_text(exc.stdout)),
            stderr_tail=tail_text(normalize_text(exc.stderr)),
        )


def docker_info_ok(timeout_sec: int = 10) -> bool:
    result = run_command(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        timeout_sec=timeout_sec,
    )
    return result.exit_code == 0


def configure_iptables_legacy() -> None:
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        return
    commands = [
        ["update-alternatives", "--set", "iptables", "/usr/sbin/iptables-legacy"],
        ["update-alternatives", "--set", "ip6tables", "/usr/sbin/ip6tables-legacy"],
    ]
    for argv in commands:
        run_command(argv, timeout_sec=10)


def ensure_docker(run_root: Path) -> subprocess.Popen[str] | None:
    if docker_info_ok():
        return None

    configure_iptables_legacy()
    log_path = run_root / "dockerd.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")
    proc = subprocess.Popen(
        ["dockerd", "--host=unix:///var/run/docker.sock"],
        text=True,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    for _ in range(60):
        if docker_info_ok(timeout_sec=5):
            return proc
        if proc.poll() is not None:
            break
        time.sleep(1)

    log_file.close()
    tail = tail_text(log_path.read_text(encoding="utf-8", errors="replace"))
    raise RuntimeError(f"Docker daemon did not become ready. dockerd log tail:\n{tail}")


def stop_process(proc: subprocess.Popen[str] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass


def safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "-", value.lower()).strip("-")[:80]


def load_task_ids(root: Path = ROOT) -> list[str]:
    index = json.loads((root / "skills-index.json").read_text(encoding="utf-8"))
    return sorted(task["id"] for task in index.get("tasks", []))


def parse_reward(text: str) -> float | None:
    """Parse the SkillsBench verifier reward without discarding fractional scores."""

    try:
        reward = float(text.strip())
    except ValueError:
        return None
    if not math.isfinite(reward) or not 0.0 <= reward <= 1.0:
        return None
    return reward


def read_reward(logs_dir: Path) -> float | None:
    reward_path = logs_dir / "verifier" / "reward.txt"
    if not reward_path.exists():
        return None
    text = reward_path.read_text(encoding="utf-8", errors="replace").strip()
    return parse_reward(text)


def reward_is_full(reward: float | None) -> bool:
    return reward is not None and math.isclose(reward, 1.0, rel_tol=0.0, abs_tol=1e-9)


def verifier_has_failed_assertions(report: CommandReport) -> bool:
    output = f"{report.stdout_tail}\n{report.stderr_tail}"
    return bool(
        re.search(r"(?m)^.*\sFAILED\s+\[\s*\d+%\]$", output)
        or re.search(r"(?mi)^=+\s+\d+ failed(?:,|\s)", output)
    )


def classify_verifier_result(
    report: CommandReport,
    reward: float | None,
    *,
    strict_assertions: bool = True,
) -> tuple[str, bool]:
    """Keep benchmark score failures distinct from verifier infrastructure failures."""

    if report.timed_out:
        return "verifier_timeout", False
    if reward_is_full(reward) and strict_assertions and (
        report.exit_code != 0 or verifier_has_failed_assertions(report)
    ):
        return "verifier_contract_inconsistent", False
    if reward_is_full(reward):
        return "passed", True
    if reward is not None:
        if math.isclose(reward, 0.0, rel_tol=0.0, abs_tol=1e-9):
            return "reward_failed", False
        return "reward_partial", False
    if report.exit_code != 0:
        return "verifier_command_failed", False
    return "reward_missing", False


def task_frontmatter(task_text: str) -> str:
    if not task_text.startswith("---"):
        return ""
    parts = task_text.split("---", 2)
    return parts[1] if len(parts) >= 3 else ""


def _yaml_scalar(value: str) -> str:
    rendered = value.strip()
    if len(rendered) >= 2 and rendered[0] == rendered[-1] and rendered[0] in {"'", '"'}:
        return rendered[1:-1]
    return rendered


def task_section_scalars(task_text: str, section: str) -> dict[str, str]:
    """Read immediate scalar fields from one top-level task section."""

    lines = task_frontmatter(task_text).splitlines()
    section_index: int | None = None
    for index, line in enumerate(lines):
        if line == f"{section}:":
            section_index = index
            break
    if section_index is None:
        return {}

    values: dict[str, str] = {}
    for line in lines[section_index + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            break
        if indent != 2:
            continue
        match = re.match(r"^  ([A-Za-z_][A-Za-z0-9_]*):\s*(.*?)\s*$", line)
        if match and match.group(2):
            values[match.group(1)] = _yaml_scalar(match.group(2))
    return values


def task_section_mapping(task_text: str, section: str, field: str) -> dict[str, str]:
    """Read a scalar mapping nested under ``section.field``."""

    lines = task_frontmatter(task_text).splitlines()
    section_index: int | None = None
    for index, line in enumerate(lines):
        if line == f"{section}:":
            section_index = index
            break
    if section_index is None:
        return {}

    field_index: int | None = None
    for index in range(section_index + 1, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            break
        if indent == 2 and line.strip() == f"{field}:":
            field_index = index
            break
    if field_index is None:
        return {}

    values: dict[str, str] = {}
    for line in lines[field_index + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= 2:
            break
        if indent != 4:
            continue
        match = re.match(r"^    ([A-Za-z_][A-Za-z0-9_]*):\s*(.*?)\s*$", line)
        if match and match.group(2):
            values[match.group(1)] = _yaml_scalar(match.group(2))
    return values


def task_section_value(task_text: str, section: str, field: str) -> str | None:
    return task_section_scalars(task_text, section).get(field)


def task_phase_env(task_text: str, phase: str) -> dict[str, str]:
    """Return public benchmark-declared env for a container execution phase."""

    environment = {
        key: value
        for key, value in task_section_scalars(task_text, "environment").items()
        if key not in ENVIRONMENT_CONTROL_FIELDS
    }
    environment.update(task_section_mapping(task_text, "environment", "env"))
    if phase != "environment":
        environment.update(task_section_mapping(task_text, phase, "env"))
    return dict(sorted(environment.items()))


def docker_env_args(environment: dict[str, str]) -> list[str]:
    args: list[str] = []
    for key, value in sorted(environment.items()):
        args.extend(["-e", f"{key}={value}"])
    return args


def task_section_number(task_text: str, section: str, field: str) -> float | None:
    value = task_section_value(task_text, section, field)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def docker_resource_args(task_text: str) -> list[str]:
    """Translate declared SkillsBench environment limits into docker run flags."""

    args: list[str] = []
    cpus = task_section_number(task_text, "environment", "cpus")
    memory_mb = task_section_number(task_text, "environment", "memory_mb")
    network_mode = task_section_value(task_text, "environment", "network_mode")
    if cpus is not None and cpus > 0:
        args.extend(["--cpus", f"{cpus:g}"])
    if memory_mb is not None and memory_mb > 0:
        args.extend(["--memory", f"{int(memory_mb)}m"])
    if network_mode == "no-network":
        args.extend(["--network", "none"])
    return args


def prepare_skill_free_build_context(environment_dir: Path, destination: Path) -> Path:
    """Copy an environment build context while replacing task-local skills with an empty directory.

    Some upstream Dockerfiles copy ``environment/skills`` into provider-native
    paths. Model-backed C0/C1 runs build from this neutral context so the task
    image itself cannot leak curated skills into either arm; C1 skills are
    supplied separately by the selected harness.
    """

    if destination.exists():
        shutil.rmtree(destination)
    source_root = environment_dir.resolve()

    def ignore(path: str, names: list[str]) -> set[str]:
        if Path(path).resolve() == source_root and "skills" in names:
            return {"skills"}
        return set()

    shutil.copytree(environment_dir, destination, ignore=ignore)
    (destination / "skills").mkdir(exist_ok=True)
    return destination


def run_task(
    task_id: str,
    *,
    run_root: Path,
    build_timeout_sec: int,
    step_timeout_sec: int,
    no_cache: bool,
    keep_container: bool,
    keep_image: bool,
) -> OracleReadinessRecord:
    task_dir = TASKS_ROOT / task_id
    env_dir = task_dir / "environment"
    oracle_dir = task_dir / "oracle"
    verifier_dir = task_dir / "verifier"
    record_root = run_root / "tasks" / task_id
    logs_dir = record_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "verifier").mkdir(parents=True, exist_ok=True)

    task_text = (task_dir / "task.md").read_text(encoding="utf-8", errors="replace")
    declared_build_timeout = task_section_number(task_text, "environment", "build_timeout_sec")
    declared_oracle_timeout = task_section_number(task_text, "oracle", "timeout_sec")
    declared_agent_timeout = task_section_number(task_text, "agent", "timeout_sec")
    declared_verifier_timeout = task_section_number(task_text, "verifier", "timeout_sec")
    effective_build_timeout = int(declared_build_timeout or build_timeout_sec)
    effective_oracle_timeout = int(declared_oracle_timeout or declared_agent_timeout or step_timeout_sec)
    effective_verifier_timeout = int(declared_verifier_timeout or step_timeout_sec)

    image = f"theking-skillsbench-oracle-{safe_name(task_id)}:latest"
    container = f"theking-sb-{safe_name(task_id)}-{uuid.uuid4().hex[:8]}"
    record = OracleReadinessRecord(
        task_id=task_id,
        status="started",
        passed=False,
        image=image,
        container=container,
        logs_dir=str(logs_dir),
    )

    missing = [
        str(path.relative_to(task_dir))
        for path in [env_dir / "Dockerfile", oracle_dir / "solve.sh", verifier_dir / "test.sh"]
        if not path.exists()
    ]
    if missing:
        record.status = "missing_files"
        record.notes.extend(f"missing:{item}" for item in missing)
        return record

    build_argv = ["docker", "build", "-t", image]
    if no_cache:
        build_argv.append("--no-cache")
    build_argv.append(str(env_dir))
    record.commands["build"] = run_command(build_argv, timeout_sec=effective_build_timeout)
    if record.commands["build"].exit_code != 0:
        record.status = "build_failed"
        return record

    run_argv = [
        "docker",
        "run",
        "-d",
        "--name",
        container,
        *docker_resource_args(task_text),
        *docker_env_args(task_phase_env(task_text, "environment")),
        "-v",
        f"{oracle_dir.resolve()}:/oracle:ro",
        "-v",
        f"{verifier_dir.resolve()}:/verifier:ro",
        "-v",
        f"{verifier_dir.resolve()}:/tests:ro",
        "-v",
        f"{logs_dir.resolve()}:/logs",
        image,
        "sh",
        "-lc",
        "while true; do sleep 3600; done",
    ]
    record.commands["container_start"] = run_command(run_argv, timeout_sec=60)
    if record.commands["container_start"].exit_code != 0:
        record.status = "container_start_failed"
        if not keep_image:
            record.commands["image_cleanup"] = run_command(["docker", "rmi", "-f", image], timeout_sec=60)
        return record

    try:
        record.commands["oracle"] = run_command(
            [
                "docker",
                "exec",
                *docker_env_args(task_phase_env(task_text, "oracle")),
                container,
                "bash",
                "/oracle/solve.sh",
            ],
            timeout_sec=effective_oracle_timeout,
        )
        if record.commands["oracle"].exit_code != 0:
            record.status = "oracle_failed"
            return record

        record.commands["verifier"] = run_command(
            [
                "docker",
                "exec",
                *docker_env_args(task_phase_env(task_text, "verifier")),
                container,
                "bash",
                "/verifier/test.sh",
            ],
            timeout_sec=effective_verifier_timeout,
        )
        record.reward = read_reward(logs_dir)
        record.status, record.passed = classify_verifier_result(
            record.commands["verifier"],
            record.reward,
        )
    finally:
        if not keep_container:
            record.commands["container_cleanup"] = run_command(
                ["docker", "rm", "-f", container],
                timeout_sec=60,
            )
        if not keep_image:
            record.commands["image_cleanup"] = run_command(
                ["docker", "rmi", "-f", image],
                timeout_sec=120,
            )

    return record


def summarize(records: list[OracleReadinessRecord]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    for record in records:
        status_counts[record.status] = status_counts.get(record.status, 0) + 1
    observed_rewards = [record.reward for record in records if record.reward is not None]
    return {
        "task_count": len(records),
        "passed": sum(1 for record in records if record.passed),
        "reward_observed": len(observed_rewards),
        "mean_observed_reward": sum(observed_rewards) / len(observed_rewards) if observed_rewards else None,
        "status_counts": dict(sorted(status_counts.items())),
    }


def load_existing_records(path: Path) -> list[OracleReadinessRecord]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    records: list[OracleReadinessRecord] = []
    for item in data.get("records", []):
        record = OracleReadinessRecord(
            task_id=item["task_id"],
            status=item["status"],
            passed=bool(item["passed"]),
            image=item["image"],
            container=item["container"],
            reward=item.get("reward"),
            logs_dir=item.get("logs_dir"),
            notes=list(item.get("notes", [])),
        )
        record.commands = {
            name: CommandReport(**report)
            for name, report in item.get("commands", {}).items()
        }
        records.append(record)
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run SkillsBench oracle Docker readiness.")
    parser.add_argument("--task", action="append", help="Task id to run. May be repeated.")
    parser.add_argument("--limit", type=int, help="Limit number of tasks after filtering.")
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    parser.add_argument("--build-timeout-sec", type=int, default=900)
    parser.add_argument("--step-timeout-sec", type=int, default=900)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--keep-container", action="store_true")
    parser.add_argument("--keep-image", action="store_true")
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Skip task ids already present in summary.json.")
    args = parser.parse_args(argv)

    task_ids = args.task or load_task_ids(ROOT)
    if args.limit is not None:
        task_ids = task_ids[: args.limit]

    run_root = Path(args.runs_root) / args.run_id
    run_root.mkdir(parents=True, exist_ok=True)

    dockerd_proc: subprocess.Popen[str] | None = None
    records = load_existing_records(run_root / "summary.json") if args.resume else []
    completed_task_ids = {record.task_id for record in records}
    try:
        dockerd_proc = ensure_docker(run_root)
        for task_id in task_ids:
            if task_id in completed_task_ids:
                print(f"SKIP {task_id}", flush=True)
                continue
            print(f"RUN {task_id}", flush=True)
            record = run_task(
                task_id,
                run_root=run_root,
                build_timeout_sec=args.build_timeout_sec,
                step_timeout_sec=args.step_timeout_sec,
                no_cache=args.no_cache,
                keep_container=args.keep_container,
                keep_image=args.keep_image,
            )
            records.append(record)
            print(f"RESULT {task_id} {record.status} reward={record.reward}", flush=True)
            summary = {
                "run_id": args.run_id,
                "run_root": str(run_root),
                "summary": summarize(records),
                "records": [asdict(item) for item in records],
            }
            (run_root / "summary.json").write_text(
                json.dumps(summary, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            if args.stop_on_failure and not record.passed:
                break
    finally:
        stop_process(dockerd_proc)

    final_summary = summarize(records)
    print(json.dumps(final_summary, sort_keys=True))
    return 0 if records and all(record.passed for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
