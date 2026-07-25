"""Run a verifier-backed conversation lifecycle recovery campaign.

The experiment intentionally measures prompt exposure, not provider-native
skill invocation.  Raw Codex traces stay under an explicit external run root;
the optional summary contains hashes and relative pointers only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Callable, Protocol

from experiments.mvp.run_chat import detect_codex_runtime
from src.merlin_harness.chat_campaign import (
    ChatCampaignPromotionCriteria,
    ChatCampaignTurnEvidence,
    ChatLifecycleCampaign,
)
from src.merlin_harness.chat_lifecycle import load_chat_lifecycle_observation
from src.merlin_harness.chat_session import ChatTurnBackend, TheKingChatSession
from src.merlin_harness.codex_chat import ALLOWED_EFFORTS, CodexChatBackend
from src.merlin_harness.library import FileSkillLibrary
from src.merlin_harness.models import SkillArtifact, TaskSpec
from src.merlin_harness.task_io import load_task
from src.merlin_harness.tasks import materialize_task_workspace, run_verifier


REPO_ROOT = Path(__file__).resolve().parents[2]
MVP_ROOT = REPO_ROOT / "experiments" / "mvp"
DEFAULT_TASK_IDS = (
    "create-audit-log",
    "create-report-md",
    "count-errors",
    "count-items",
)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class BackendFactory(Protocol):
    def __call__(self, *, workspace: Path, trace_root: Path) -> ChatTurnBackend: ...


def _sha256_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_frozen_campaign_inputs(
    *,
    task_ids: tuple[str, ...] = DEFAULT_TASK_IDS,
    skills_root: Path = MVP_ROOT / "skills",
    distractors_root: Path = MVP_ROOT / "distractors",
) -> tuple[tuple[TaskSpec, ...], tuple[SkillArtifact, ...]]:
    """Load the pre-registered ordered task and active-library snapshots."""

    tasks = tuple(load_task(MVP_ROOT / "tasks" / f"{task_id}.json") for task_id in task_ids)
    skills = tuple(FileSkillLibrary(skills_root).list() + FileSkillLibrary(distractors_root).list())
    return tasks, skills


class ProviderChatCampaignExecutor:
    """Adapt fresh provider-backed chat turns to immutable campaign evidence."""

    def __init__(self, *, run_root: Path, backend_factory: BackendFactory) -> None:
        self.run_root = run_root.expanduser().resolve()
        if self.run_root.exists():
            if not self.run_root.is_dir() or any(self.run_root.iterdir()):
                raise ValueError("run_root must be absent or an empty directory")
        else:
            self.run_root.mkdir(parents=True)
        self.backend_factory = backend_factory
        self.provider_reported_model_ids: set[str] = set()

    def run_turn(
        self,
        *,
        task: TaskSpec,
        skills: tuple[SkillArtifact, ...],
        arm: str,
        ordinal: int,
    ) -> ChatCampaignTurnEvidence:
        if not _SAFE_ID_RE.fullmatch(task.id) or not _SAFE_ID_RE.fullmatch(arm):
            raise ValueError("campaign task and arm IDs must be path-safe")
        workspace = self.run_root / arm / f"{ordinal:02d}-{task.id}"
        materialize_task_workspace(task, workspace)
        library = FileSkillLibrary(workspace / ".merlin" / "library")
        for skill in skills:
            library.save(skill)
        trace_root = workspace / ".merlin" / "chat" / "session"
        backend = self.backend_factory(workspace=workspace, trace_root=trace_root)
        session = TheKingChatSession(
            workspace=workspace,
            library=library,
            backend=backend,
            trace_root=trace_root,
            top_k=1,
            routing_mode="controlled_lexical",
        )
        response = session.send(task.instruction)
        validation = run_verifier(task, workspace, answer=response.answer)
        session.record_feedback("pass" if validation.passed else "fail")
        trace = session.last_trace()
        if trace is None:
            raise ValueError("completed chat turn has no immutable metadata")
        reported = trace.get("backend_metadata", {}).get(
            "provider_reported_model_ids", []
        )
        if isinstance(reported, list):
            self.provider_reported_model_ids.update(
                value for value in reported if isinstance(value, str) and value
            )
        observation = load_chat_lifecycle_observation(trace_root, turn_number=1)
        return ChatCampaignTurnEvidence(
            task_id=task.id,
            verifier_id=validation.name,
            verifier_passed=validation.passed,
            exposure_skill_ids=observation.exposure_skill_ids,
            oracle_skill_ids=tuple(task.oracle_skill_ids),
            raw_trace_pointer=observation.raw_trace_pointer,
            raw_trace_sha256=observation.raw_trace_sha256,
            actual_invocation_evidence_complete=False,
        )


def run_campaign(
    *,
    run_root: Path,
    backend_factory: BackendFactory,
    requested_model_id: str,
    requested_effort: str,
    cli_version: str,
) -> dict[str, object]:
    tasks, skills = load_frozen_campaign_inputs()
    executor = ProviderChatCampaignExecutor(
        run_root=run_root,
        backend_factory=backend_factory,
    )
    campaign = ChatLifecycleCampaign(
        tasks=tasks,
        library_snapshot=skills,
        executor=executor,
        exposure_budget=1,
        promotion_criteria=ChatCampaignPromotionCriteria(),
    )
    baseline = campaign.run_baseline()
    decisions = campaign.diagnose_route_local(min_route_risk_events=2)
    campaign.stage_copy_on_write()
    promotion = campaign.run_provisional_and_promote()
    reported_model_ids = sorted(executor.provider_reported_model_ids)
    return {
        "schema_version": 1,
        "title": "Merlin provider-backed prompt-exposure lifecycle campaign",
        "scope": (
            "Four frozen tasks, controlled naive lexical overload, verifier-backed outcomes, "
            "route-local copy-on-write hide, and the same ordered task/verifier re-run."
        ),
        "evidence_boundary": {
            "measured": "prompt exposure and deterministic verifier outcome",
            "not_measured": "provider-native skill-body invocation",
            "actual_invocation_evidence_complete": False,
            "raw_traces": "external run root only; summary stores relative pointer and SHA-256",
        },
        "runtime_contract": {
            "requested_model_id": requested_model_id,
            "requested_effort": requested_effort,
            "cli_version": cli_version,
            "provider_reported_model_ids": reported_model_ids,
            "model_evidence_level": (
                "provider_reported" if reported_model_ids else "requested_cli_contract_only"
            ),
            "routing_mode": "controlled_lexical",
            "top_k": 1,
        },
        "frozen_contract": {
            "ordered_task_ids": [task.id for task in tasks],
            "verifier_ids": [task.verifier.name for task in tasks],
            "library_skill_ids": [skill.id for skill in skills],
            "task_snapshot_sha256": _sha256_json([task.id for task in tasks]),
            "library_snapshot_sha256": campaign.library_snapshot_sha256,
        },
        "baseline": baseline.to_dict(),
        "lifecycle_decisions": [
            {
                "skill_id": decision.skill_id,
                "action": decision.action.value,
                "reason": decision.reason,
                "evidence_trace_ids": list(decision.evidence_trace_ids),
                "scope": "route-local prompt-exposure guard; no skill-content blame",
            }
            for decision in decisions
        ],
        "provisional": promotion.provisional.to_dict(),
        "promotion": {
            "accepted": promotion.accepted,
            "rollback_required": promotion.rollback_required,
            "reason": promotion.reason,
            "library_resolution": promotion.library_resolution,
            "checks": [
                {
                    "name": check.name,
                    "passed": check.passed,
                    "score": check.score,
                    "evidence": check.evidence,
                }
                for check in promotion.checks
            ],
        },
        "resolved_library_statuses": {
            skill.id: skill.status.value for skill in campaign.resolved_library()
        },
    }


def _write_new_summary(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--effort", choices=sorted(ALLOWED_EFFORTS), default="low")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--executable")
    parser.add_argument("--cli-version")
    args = parser.parse_args(argv)

    try:
        executable, cli_version = detect_codex_runtime(
            args.executable,
            version_override=args.cli_version,
        )
        factory: BackendFactory = lambda *, workspace, trace_root: CodexChatBackend(
            executable=executable,
            cli_version=cli_version,
            workspace=workspace,
            trace_root=trace_root,
            model_id=args.model,
            effort=args.effort,
            timeout_s=args.timeout,
        )
        payload = run_campaign(
            run_root=args.run_root,
            backend_factory=factory,
            requested_model_id=args.model,
            requested_effort=args.effort,
            cli_version=cli_version,
        )
        if args.summary:
            _write_new_summary(args.summary.expanduser().resolve(), payload)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if bool(payload["promotion"]["accepted"]) else 1  # type: ignore[index]


if __name__ == "__main__":
    raise SystemExit(main())
