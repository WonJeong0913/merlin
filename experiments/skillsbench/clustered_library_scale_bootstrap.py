"""Task-clustered paired bootstrap for oracle-bound library-scale results.

This module is deliberately research-only.  The Build Week package freezes the
shared runtime under ``src/merlin_harness``; statistical post-processing for the
1,566-cell evaluation stays beside the SkillsBench aggregator so it cannot
silently expand the judged product surface.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

from src.merlin_harness.library_scale_results import ValidatedLibraryScaleCell
from src.merlin_harness.metrics import (
    InvocationObservation,
    more_skills_decomposition,
    oracle_invocation_event_summary,
)


T = TypeVar("T")
BOOTSTRAP_ITERATIONS = 2000
BOOTSTRAP_CONFIDENCE = 0.95
BOOTSTRAP_SEED = 20260719


class ClusteredBootstrapError(ValueError):
    """Raised when paired clusters or decomposition evidence drift."""


def _quantile_sorted(values: Sequence[float], q: float) -> float:
    if not values:
        raise ClusteredBootstrapError("bootstrap sample is empty")
    if q <= 0.0:
        return values[0]
    if q >= 1.0:
        return values[-1]
    position = q * (len(values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] * (1.0 - fraction) + values[upper] * fraction


def clustered_paired_bootstrap_cis(
    clusters: Mapping[str, Sequence[T]],
    statistic: Callable[[Sequence[T]], Mapping[str, float]],
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    confidence: float = BOOTSTRAP_CONFIDENCE,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Resample task clusters, then paired trial trajectories within task."""

    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 1:
        raise ClusteredBootstrapError("iterations must be an integer >= 1")
    if not 0.0 < confidence < 1.0:
        raise ClusteredBootstrapError("confidence must be between 0 and 1")
    if not clusters:
        raise ClusteredBootstrapError("clusters must not be empty")
    if any(not isinstance(cluster_id, str) or not cluster_id for cluster_id in clusters):
        raise ClusteredBootstrapError("cluster IDs must be non-empty strings")
    ordered_ids = sorted(clusters)
    normalized: dict[str, tuple[T, ...]] = {}
    for cluster_id in ordered_ids:
        rows = tuple(clusters[cluster_id])
        if not rows:
            raise ClusteredBootstrapError(
                f"cluster {cluster_id!r} must contain at least one trajectory"
            )
        normalized[cluster_id] = rows

    def evaluate(rows: Sequence[T]) -> dict[str, float]:
        raw = statistic(rows)
        if not isinstance(raw, Mapping) or not raw:
            raise ClusteredBootstrapError("statistic must return a non-empty mapping")
        result: dict[str, float] = {}
        for name, value in raw.items():
            if not isinstance(name, str) or not name:
                raise ClusteredBootstrapError("statistic names must be non-empty strings")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ClusteredBootstrapError(f"statistic {name!r} must be numeric")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ClusteredBootstrapError(f"statistic {name!r} must be finite")
            result[name] = numeric
        return result

    original_rows = [row for cluster_id in ordered_ids for row in normalized[cluster_id]]
    estimates = evaluate(original_rows)
    metric_names = tuple(sorted(estimates))
    samples = {name: [] for name in metric_names}
    rng = random.Random(seed)
    cluster_count = len(ordered_ids)
    for _ in range(iterations):
        resample: list[T] = []
        for _cluster_draw in range(cluster_count):
            cluster_id = ordered_ids[rng.randrange(cluster_count)]
            rows = normalized[cluster_id]
            resample.extend(rows[rng.randrange(len(rows))] for _ in range(len(rows)))
        values = evaluate(resample)
        if tuple(sorted(values)) != metric_names:
            raise ClusteredBootstrapError(
                "statistic keys changed across bootstrap resamples"
            )
        for name in metric_names:
            samples[name].append(values[name])

    alpha = (1.0 - confidence) / 2.0
    intervals: dict[str, dict[str, float]] = {}
    for name in metric_names:
        ordered = sorted(samples[name])
        intervals[name] = {
            "estimate": estimates[name],
            "low": _quantile_sorted(ordered, alpha),
            "high": _quantile_sorted(ordered, 1.0 - alpha),
        }
    return {
        "method": "two_stage_task_cluster_paired_percentile_bootstrap",
        "confidence": confidence,
        "iterations": iterations,
        "seed": seed,
        "cluster_count": cluster_count,
        "trajectory_count": len(original_rows),
        "resampling_units": {
            "stage_1": "task_cluster",
            "stage_2": "paired_trial_trajectory_within_task",
        },
        "intervals": intervals,
    }


def _arm_clusters(
    *,
    cells: Sequence[ValidatedLibraryScaleCell],
    normalized_oracles: Mapping[str, Sequence[str]],
    trial_indices: Sequence[int],
    arm_id: str,
) -> dict[str, list[tuple[InvocationObservation, InvocationObservation]]]:
    oracle_cells: dict[tuple[str, int], ValidatedLibraryScaleCell] = {}
    arm_cells: dict[tuple[str, int], ValidatedLibraryScaleCell] = {}
    for cell in cells:
        key = (cell.task_id, cell.trial_index)
        if cell.arm_id == "oracle-only":
            target = oracle_cells
        elif cell.arm_id == arm_id:
            target = arm_cells
        else:
            continue
        if key in target:
            raise ClusteredBootstrapError(
                f"duplicate {cell.arm_id} task/trial trajectory: {cell.task_id} t{cell.trial_index}"
            )
        target[key] = cell
    if set(oracle_cells) != set(arm_cells):
        raise ClusteredBootstrapError(
            f"oracle-only/{arm_id} paired task/trial coverage differs"
        )
    expected_trials = tuple(trial_indices)
    if not expected_trials or any(
        isinstance(index, bool) or not isinstance(index, int) for index in expected_trials
    ):
        raise ClusteredBootstrapError("manifest trial_indices are invalid")
    task_ids = sorted(normalized_oracles)
    expected_keys = {
        (task_id, trial_index)
        for task_id in task_ids
        for trial_index in expected_trials
    }
    if set(oracle_cells) != expected_keys:
        raise ClusteredBootstrapError(
            f"oracle-only/{arm_id} bootstrap coverage is not every task x trial"
        )

    clusters: dict[
        str,
        list[tuple[InvocationObservation, InvocationObservation]],
    ] = {task_id: [] for task_id in task_ids}
    for task_id in task_ids:
        oracle_ids = tuple(normalized_oracles[task_id])
        for trial_index in expected_trials:
            oracle_cell = oracle_cells[(task_id, trial_index)]
            arm_cell = arm_cells[(task_id, trial_index)]
            clusters[task_id].append(
                (
                    InvocationObservation(
                        task_id=task_id,
                        invoked_skill_ids=oracle_cell.invoked_skill_ids,
                        oracle_skill_ids=oracle_ids,
                        success=oracle_cell.verifier_passed,
                    ),
                    InvocationObservation(
                        task_id=task_id,
                        invoked_skill_ids=arm_cell.invoked_skill_ids,
                        oracle_skill_ids=oracle_ids,
                        success=arm_cell.verifier_passed,
                    ),
                )
            )
    return clusters


def _decomposition_statistics(
    rows: Sequence[tuple[InvocationObservation, InvocationObservation]],
) -> Mapping[str, float]:
    oracle_summary = oracle_invocation_event_summary(row[0] for row in rows)
    arm_summary = oracle_invocation_event_summary(row[1] for row in rows)
    value = more_skills_decomposition(oracle_summary, arm_summary)
    if value.unavailable_reason is not None or value.invariant_holds is not True:
        raise ClusteredBootstrapError(
            value.unavailable_reason
            or "More Skills decomposition invariant failed in a bootstrap resample"
        )
    metrics = {
        "p_oracle": value.p_oracle,
        "p_library": value.p_library,
        "observed_drop": value.observed_drop,
        "delta_ctx": value.delta_ctx,
        "delta_shd": value.delta_shd,
        "total": value.total,
    }
    if any(metric is None for metric in metrics.values()):
        raise ClusteredBootstrapError(
            "bootstrap decomposition returned an undefined metric"
        )
    return {name: float(metric) for name, metric in metrics.items() if metric is not None}


def build_library_scale_clustered_bootstrap(
    *,
    manifest: Mapping[str, Any],
    cells: Sequence[ValidatedLibraryScaleCell],
    normalized_oracles: Mapping[str, Sequence[str]] | None,
    aggregate_summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind clustered CIs to an already validated core aggregation summary."""

    shadowing = aggregate_summary.get("shadowing_summary")
    if not isinstance(shadowing, Mapping):
        raise ClusteredBootstrapError("aggregate shadowing summary is missing")
    if shadowing.get("more_skills_decomposition_eligible") is not True:
        return {
            "status": "unavailable",
            "reason": shadowing.get("decomposition_blocker")
            or shadowing.get("reason")
            or "More Skills decomposition is ineligible",
            "comparisons": None,
        }
    if normalized_oracles is None:
        raise ClusteredBootstrapError(
            "eligible decomposition requires an empirical oracle mapping"
        )
    declared = shadowing.get("more_skills_decomposition")
    if not isinstance(declared, Mapping):
        raise ClusteredBootstrapError("declared decomposition payload is missing")
    manifest_cells = manifest.get("cells")
    trial_indices = manifest.get("trial_indices")
    if not isinstance(manifest_cells, list) or not isinstance(trial_indices, list):
        raise ClusteredBootstrapError("manifest cells or trial_indices are invalid")
    arm_ids: list[str] = []
    for cell in manifest_cells:
        arm_id = cell.get("arm_id") if isinstance(cell, Mapping) else None
        if not isinstance(arm_id, str):
            raise ClusteredBootstrapError("manifest arm ID is invalid")
        if arm_id not in arm_ids:
            arm_ids.append(arm_id)
    comparison_ids = [arm_id for arm_id in arm_ids if arm_id != "oracle-only"]
    if set(declared) != set(comparison_ids):
        raise ClusteredBootstrapError(
            "decomposition arms do not match the manifest comparisons"
        )

    comparisons: dict[str, Any] = {}
    unavailable: dict[str, str] = {}
    for arm_id in comparison_ids:
        clusters = _arm_clusters(
            cells=cells,
            normalized_oracles=normalized_oracles,
            trial_indices=trial_indices,
            arm_id=arm_id,
        )
        try:
            result = clustered_paired_bootstrap_cis(
                clusters,
                _decomposition_statistics,
            )
        except ClusteredBootstrapError as exc:
            unavailable[arm_id] = str(exc)
            comparisons[arm_id] = {
                "status": "unavailable",
                "reason": str(exc),
                "comparison": f"oracle-only vs {arm_id}",
                "paired_by": ["task_id", "trial_index"],
            }
            continue
        point = declared[arm_id]
        if not isinstance(point, Mapping):
            raise ClusteredBootstrapError(
                f"declared decomposition for {arm_id} is invalid"
            )
        for metric, interval in result["intervals"].items():
            declared_value = point.get(metric)
            if (
                isinstance(declared_value, bool)
                or not isinstance(declared_value, (int, float))
                or abs(float(declared_value) - interval["estimate"]) > 1e-12
            ):
                raise ClusteredBootstrapError(
                    f"bootstrap point estimate drifted from decomposition for {arm_id}:{metric}"
                )
        comparisons[arm_id] = {
            "status": "available",
            "reason": None,
            "comparison": f"oracle-only vs {arm_id}",
            "paired_by": ["task_id", "trial_index"],
            **result,
        }
    return {
        "status": "available" if not unavailable else "partially_unavailable",
        "reason": (
            None
            if not unavailable
            else "one or more bootstrap comparisons were undefined: "
            + ", ".join(sorted(unavailable))
        ),
        "comparisons": comparisons,
    }
