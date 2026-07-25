"""Create an execution-scheduling manifest for SkillsBench runs.

The scheduling manifest does not remove tasks from the benchmark. It assigns
each task to an execution bucket so pilots can validate throughput before
running expensive document/media tasks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
TASKS_ROOT = ROOT / "tasks"
DEFAULT_INDEX = ROOT / "skills-index.json"
DEFAULT_SPLIT = ROOT / "split-manifest.json"
DEFAULT_READINESS = ROOT / "runs" / "oracle-readiness" / "one-full-87-20260708-r2" / "summary.json"
DEFAULT_OUTPUT = ROOT / "scheduling-manifest.json"
DEFAULT_SEED = 20260709

DOCUMENT_SUFFIXES = {".pdf", ".doc", ".docx", ".ppt", ".pptx"}
SPREADSHEET_SUFFIXES = {".xls", ".xlsx", ".xlsm"}
MEDIA_SUFFIXES = {".mp3", ".mp4", ".wav", ".m4a", ".mov", ".avi"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
TABULAR_SUFFIXES = {".csv", ".tsv", ".parquet", ".sqlite", ".db"}
STRUCTURED_SUFFIXES = {".json", ".geojson", ".yaml", ".yml", ".xml"}
LONG_COMPUTE_TASK_TYPES = {
    "optimization",
    "planning",
    "control",
    "simulation",
    "proof",
    "debugging",
    "migration",
    "repair",
}
CODE_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".scala",
    ".go",
    ".rs",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".lean",
}


def read_task_text(task_dir: Path) -> str:
    path = task_dir / "task.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def frontmatter(task_text: str) -> str:
    if not task_text.startswith("---"):
        return ""
    parts = task_text.split("---", 2)
    return parts[1] if len(parts) >= 3 else ""


def section_numeric_field(task_text: str, section: str, field: str) -> int | None:
    fm = frontmatter(task_text)
    match = re.search(rf"(?ms)^{re.escape(section)}:\n(?P<body>(?:^[ \t]+.*\n?)*)", fm)
    if not match:
        return None
    value = re.search(rf"(?m)^[ \t]+{re.escape(field)}:\s*([0-9.]+)", match.group("body"))
    if not value:
        return None
    return max(1, int(float(value.group(1))))


def metadata_list_values(task_text: str, field: str) -> list[str]:
    values: list[str] = []
    lines = frontmatter(task_text).splitlines()
    for index, line in enumerate(lines):
        if not re.fullmatch(rf"[ \t]+{re.escape(field)}:\s*", line):
            continue
        base_indent = len(line) - len(line.lstrip())
        for child in lines[index + 1 :]:
            stripped = child.strip()
            if not stripped:
                continue
            indent = len(child) - len(child.lstrip())
            if stripped.startswith("- ") and indent >= base_indent:
                values.append(stripped[2:].strip())
                continue
            if indent <= base_indent:
                break
            break
        break
    return values


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def stable_key(task_id: str, seed: int) -> str:
    return hashlib.sha256(f"{seed}:{task_id}".encode("utf-8")).hexdigest()


def _task_id(task: dict[str, Any]) -> str:
    return task.get("id") or task["task_id"]


def readiness_by_id(readiness: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not readiness:
        return {}
    return {record["task_id"]: record for record in readiness.get("records", [])}


def split_by_task_id(split_manifest: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for split_name, entries in split_manifest.get("splits", {}).items():
        for entry in entries:
            result[entry["task_id"]] = split_name
    return result


def collect_environment_suffixes(task_dir: Path) -> dict[str, int]:
    env_dir = task_dir / "environment"
    counts: Counter[str] = Counter()
    if not env_dir.exists():
        return {}
    for path in env_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(env_dir)
        if rel.parts and rel.parts[0] == "skills":
            continue
        suffix = path.suffix.lower() or "<none>"
        counts[suffix] += 1
    return dict(sorted(counts.items()))


def detect_benchmark_stratum(
    task_id: str,
    category: str | None,
    suffix_counts: dict[str, int],
    modalities: list[str],
) -> str:
    suffixes = set(suffix_counts)
    modality_set = {value.lower() for value in modalities}
    if suffixes & MEDIA_SUFFIXES:
        return "media_audio_video"
    if suffixes & DOCUMENT_SUFFIXES:
        return "document_pdf_pptx_docx"
    if suffixes & IMAGE_SUFFIXES:
        return "image_or_ocr"
    if suffixes & SPREADSHEET_SUFFIXES:
        return "spreadsheet_office"
    if modality_set & {"pdf", "presentation", "document"} and not suffixes & (TABULAR_SUFFIXES | STRUCTURED_SUFFIXES):
        return "document_pdf_pptx_docx"
    if suffixes & TABULAR_SUFFIXES:
        return "tabular_data"
    if suffixes & STRUCTURED_SUFFIXES:
        return "structured_json_xml"
    if suffixes & CODE_SUFFIXES or category in {"software-engineering", "cybersecurity"}:
        return "code_or_security"
    if category in {"natural-science", "finance-economics", "industrial-physical-systems"}:
        return "scientific_or_numeric"
    return "general_workspace"


def detect_execution_bucket(
    benchmark_stratum: str,
    oracle_status: str | None,
    agent_timeout_sec: int | None,
    task_types: list[str],
) -> str:
    if oracle_status and oracle_status != "passed":
        return "repair_or_exception"
    if agent_timeout_sec and agent_timeout_sec > 1200:
        return "long_running_compute"
    if {value.lower() for value in task_types} & LONG_COMPUTE_TASK_TYPES:
        return "long_running_compute"
    if benchmark_stratum in {"tabular_data", "structured_json_xml", "scientific_or_numeric"}:
        return "short_smoke_candidate"
    if benchmark_stratum in {"document_pdf_pptx_docx", "media_audio_video", "image_or_ocr", "spreadsheet_office"}:
        return "long_running_document_media"
    return "standard"


def build_scheduling_manifest(
    *,
    index: dict[str, Any],
    split_manifest: dict[str, Any],
    readiness: dict[str, Any] | None,
    tasks_root: Path = TASKS_ROOT,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    split_map = split_by_task_id(split_manifest)
    ready_map = readiness_by_id(readiness)
    tasks: list[dict[str, Any]] = []

    for task in sorted(index.get("tasks", []), key=_task_id):
        task_id = _task_id(task)
        task_dir = tasks_root / task_id
        suffix_counts = collect_environment_suffixes(task_dir)
        task_text = read_task_text(task_dir)
        modalities = metadata_list_values(task_text, "modality")
        task_types = metadata_list_values(task_text, "task_type")
        agent_timeout_sec = section_numeric_field(task_text, "agent", "timeout_sec")
        verifier_timeout_sec = section_numeric_field(task_text, "verifier", "timeout_sec")
        build_timeout_sec = section_numeric_field(task_text, "environment", "build_timeout_sec")
        oracle_record = ready_map.get(task_id, {})
        benchmark_stratum = detect_benchmark_stratum(
            task_id,
            task.get("category"),
            suffix_counts,
            modalities,
        )
        execution_bucket = detect_execution_bucket(
            benchmark_stratum,
            oracle_record.get("status"),
            agent_timeout_sec,
            task_types,
        )
        tasks.append(
            {
                "task_id": task_id,
                "split": split_map.get(task_id),
                "category": task.get("category"),
                "difficulty": task.get("difficulty"),
                "benchmark_stratum": benchmark_stratum,
                "execution_bucket": execution_bucket,
                "oracle_readiness_status": oracle_record.get("status"),
                "oracle_readiness_passed": oracle_record.get("passed"),
                "agent_timeout_sec": agent_timeout_sec,
                "verifier_timeout_sec": verifier_timeout_sec,
                "build_timeout_sec": build_timeout_sec,
                "modalities": modalities,
                "task_types": task_types,
                "environment_suffix_counts": suffix_counts,
                "curated_skill_variants": task.get("curated_skill_variants", []),
            }
        )

    all_ids = [task["task_id"] for task in tasks]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("duplicate task ids in scheduling manifest")
    if len(tasks) != len(index.get("tasks", [])):
        raise ValueError("scheduling manifest does not cover all indexed tasks")

    short_candidates = [
        task
        for task in tasks
        if task["split"] == "adaptation" and task["execution_bucket"] == "short_smoke_candidate"
    ]
    short_candidates = sorted(short_candidates, key=lambda task: stable_key(task["task_id"], seed))
    short_smoke = [task["task_id"] for task in short_candidates[:3]]

    summary = {
        "task_count": len(tasks),
        "split_counts": dict(sorted(Counter(task["split"] for task in tasks).items())),
        "benchmark_stratum_counts": dict(sorted(Counter(task["benchmark_stratum"] for task in tasks).items())),
        "execution_bucket_counts": dict(sorted(Counter(task["execution_bucket"] for task in tasks).items())),
        "oracle_readiness_status_counts": dict(
            sorted(Counter(str(task["oracle_readiness_status"]) for task in tasks).items())
        ),
        "short_smoke_adaptation_ready": short_smoke,
    }
    return {
        "created": date.today().isoformat(),
        "source": index.get("source"),
        "commit": index.get("commit"),
        "license": index.get("license"),
        "seed": seed,
        "policy": {
            "final_coverage": "All 87 tasks remain in the final experiment.",
            "purpose": "Scheduling only: validate throughput on short tasks before long-running document/media strata.",
            "claim_rule": "Do not compare or claim only on the short_smoke_candidate bucket; final claims require all eligible strata or pre-registered infrastructure exceptions.",
        },
        "summary": summary,
        "tasks": tasks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create SkillsBench execution scheduling manifest.")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--split-manifest", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--readiness-summary", type=Path, default=DEFAULT_READINESS)
    parser.add_argument("--tasks-root", type=Path, default=TASKS_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)

    readiness = load_json(args.readiness_summary) if args.readiness_summary.exists() else None
    manifest = build_scheduling_manifest(
        index=load_json(args.index),
        split_manifest=load_json(args.split_manifest),
        readiness=readiness,
        tasks_root=args.tasks_root,
        seed=args.seed,
    )
    if readiness is not None:
        manifest["readiness_artifact"] = {
            "path": str(args.readiness_summary),
            "sha256": hashlib.sha256(args.readiness_summary.read_bytes()).hexdigest(),
            "derived_artifact": bool(readiness.get("derived_artifact", False)),
            "policy": readiness.get("policy", "source-record-status"),
            "source_summary_sha256": readiness.get("source_summary_sha256")
            or readiness.get("base_summary_sha256"),
            "overlay_summaries": readiness.get("overlay_summaries", []),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = manifest["summary"]
    print(
        f"wrote={args.output} task_count={summary['task_count']} "
        f"short_smoke={','.join(summary['short_smoke_adaptation_ready'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
