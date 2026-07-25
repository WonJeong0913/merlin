"""Render the packaged chat lifecycle campaign evidence as a safe HTML review.

The renderer consumes only the bounded public evidence schema. It never reads
provider JSONL, task prompts, credentials, or workspace contents.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE = REPO_ROOT / "docs" / "evidence" / "gpt56-chat-lifecycle-campaign.json"
EXPECTED_CHECKS = (
    "same_task_coverage",
    "same_verifier_contract",
    "pass_rate_non_regression",
    "clean_oracle_exposure_non_regression",
    "exposure_shadowing_reduction",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class CampaignReportError(ValueError):
    """Raised when public campaign evidence does not satisfy the review schema."""


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CampaignReportError(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise CampaignReportError(f"{label} must be an array")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CampaignReportError(f"{label} must be a non-empty string")
    return value


def _safe_ids(value: Any, label: str) -> list[str]:
    values = _sequence(value, label)
    if not values:
        raise CampaignReportError(f"{label} must not be empty")
    result = []
    for index, item in enumerate(values):
        item = _string(item, f"{label}[{index}]")
        if not SAFE_ID_RE.fullmatch(item):
            raise CampaignReportError(f"{label}[{index}] is not a safe ID")
        result.append(item)
    if len(result) != len(set(result)):
        raise CampaignReportError(f"{label} contains duplicates")
    return result


def _rate(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CampaignReportError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise CampaignReportError(f"{label} must be between zero and one")
    return result


def _arm(
    value: Any,
    *,
    label: str,
    ordered_task_ids: list[str],
) -> dict[str, Any]:
    arm = _mapping(value, label)
    task_count = arm.get("task_count")
    passed = arm.get("passed")
    if isinstance(task_count, bool) or not isinstance(task_count, int) or task_count != len(ordered_task_ids):
        raise CampaignReportError(f"{label}.task_count does not match frozen coverage")
    if isinstance(passed, bool) or not isinstance(passed, int) or not 0 <= passed <= task_count:
        raise CampaignReportError(f"{label}.passed is invalid")
    pass_rate = _rate(arm.get("pass_rate"), f"{label}.pass_rate")
    clean_rate = _rate(
        arm.get("clean_oracle_exposure_rate"),
        f"{label}.clean_oracle_exposure_rate",
    )
    shadowing_rate = _rate(
        arm.get("exposure_shadowing_rate"),
        f"{label}.exposure_shadowing_rate",
    )
    routes = _sequence(arm.get("routes"), f"{label}.routes")
    if len(routes) != task_count:
        raise CampaignReportError(f"{label}.routes does not match frozen coverage")
    normalized_routes: list[dict[str, Any]] = []
    for index, raw_route in enumerate(routes):
        route = _mapping(raw_route, f"{label}.routes[{index}]")
        task_id = _string(route.get("task_id"), f"{label}.routes[{index}].task_id")
        if task_id != ordered_task_ids[index]:
            raise CampaignReportError(f"{label}.routes must preserve frozen task order")
        exposures = _safe_ids(
            route.get("exposure_skill_ids"),
            f"{label}.routes[{index}].exposure_skill_ids",
        )
        if len(exposures) != 1:
            raise CampaignReportError(f"{label}.routes[{index}] must expose exactly one skill")
        route_class = _string(route.get("route_class"), f"{label}.routes[{index}].route_class")
        if route_class not in {"wrong", "mixed", "oracle_only"}:
            raise CampaignReportError(f"{label}.routes[{index}] has unsupported route class")
        if route.get("verifier_passed") is not True:
            raise CampaignReportError(f"{label}.routes[{index}] verifier must be explicitly true")
        trace_hash = _string(
            route.get("raw_trace_sha256"),
            f"{label}.routes[{index}].raw_trace_sha256",
        )
        if not SHA256_RE.fullmatch(trace_hash):
            raise CampaignReportError(f"{label}.routes[{index}] has invalid trace hash")
        normalized_routes.append(
            {
                "task_id": task_id,
                "skill_id": exposures[0],
                "route_class": route_class,
                "trace_hash": trace_hash,
            }
        )
    calculated_pass_rate = passed / task_count
    calculated_clean = sum(route["route_class"] == "oracle_only" for route in normalized_routes) / task_count
    calculated_shadowing = sum(route["route_class"] in {"wrong", "mixed"} for route in normalized_routes) / task_count
    for actual, expected, metric in (
        (pass_rate, calculated_pass_rate, "pass_rate"),
        (clean_rate, calculated_clean, "clean_oracle_exposure_rate"),
        (shadowing_rate, calculated_shadowing, "exposure_shadowing_rate"),
    ):
        if not math.isclose(actual, expected, abs_tol=1e-12):
            raise CampaignReportError(f"{label}.{metric} disagrees with route evidence")
    return {
        "passed": passed,
        "task_count": task_count,
        "pass_rate": pass_rate,
        "clean_rate": clean_rate,
        "shadowing_rate": shadowing_rate,
        "routes": normalized_routes,
    }


def validate_evidence(value: Any) -> dict[str, Any]:
    """Validate and normalize the bounded public campaign evidence."""

    root = _mapping(value, "evidence")
    if root.get("schema_version") != 1:
        raise CampaignReportError("evidence.schema_version must be 1")
    runtime = _mapping(root.get("runtime_contract"), "runtime_contract")
    boundary = _mapping(root.get("evidence_boundary"), "evidence_boundary")
    frozen = _mapping(root.get("frozen_contract"), "frozen_contract")
    task_ids = _safe_ids(frozen.get("ordered_task_ids"), "frozen_contract.ordered_task_ids")
    verifier_ids = _safe_ids(frozen.get("verifier_ids"), "frozen_contract.verifier_ids")
    if len(verifier_ids) != len(task_ids):
        raise CampaignReportError("frozen verifier coverage does not match task coverage")
    snapshot_hash = _string(
        frozen.get("library_snapshot_sha256"),
        "frozen_contract.library_snapshot_sha256",
    )
    if not SHA256_RE.fullmatch(snapshot_hash):
        raise CampaignReportError("library snapshot hash is invalid")
    reported_models = _sequence(runtime.get("provider_reported_model_ids"), "runtime_contract.provider_reported_model_ids")
    if any(not isinstance(item, str) or not item for item in reported_models):
        raise CampaignReportError("provider-reported model IDs must be non-empty strings")
    if boundary.get("actual_invocation_evidence_complete") is not False:
        raise CampaignReportError("public campaign must preserve incomplete invocation evidence")
    model_evidence = _string(runtime.get("model_evidence_level"), "runtime_contract.model_evidence_level")
    expected_model_evidence = "provider_reported" if reported_models else "requested_cli_contract_only"
    if model_evidence != expected_model_evidence:
        raise CampaignReportError("model evidence level disagrees with provider-reported model IDs")
    baseline = _arm(root.get("baseline"), label="baseline", ordered_task_ids=task_ids)
    provisional = _arm(root.get("provisional"), label="provisional", ordered_task_ids=task_ids)
    decisions = _sequence(root.get("lifecycle_decisions"), "lifecycle_decisions")
    normalized_decisions = []
    for index, raw_decision in enumerate(decisions):
        decision = _mapping(raw_decision, f"lifecycle_decisions[{index}]")
        skill_id = _string(decision.get("skill_id"), f"lifecycle_decisions[{index}].skill_id")
        if not SAFE_ID_RE.fullmatch(skill_id) or decision.get("action") != "hide":
            raise CampaignReportError("lifecycle decisions must be safe route-local hides")
        events = decision.get("route_risk_events")
        if isinstance(events, bool) or not isinstance(events, int) or events < 2:
            raise CampaignReportError("each lifecycle decision requires repeated route-risk evidence")
        normalized_decisions.append({"skill_id": skill_id, "events": events})
    route_risk_counts = Counter(
        route["skill_id"]
        for route in baseline["routes"]
        if route["route_class"] in {"wrong", "mixed"}
    )
    expected_decisions = {
        skill_id: count for skill_id, count in route_risk_counts.items() if count >= 2
    }
    actual_decisions = {item["skill_id"]: item["events"] for item in normalized_decisions}
    if actual_decisions != expected_decisions:
        raise CampaignReportError("lifecycle decisions disagree with repeated baseline route risk")
    recovered_skills = {route["skill_id"] for route in provisional["routes"]}
    if recovered_skills & set(actual_decisions):
        raise CampaignReportError("provisional routes still expose a hidden route-risk skill")
    promotion = _mapping(root.get("promotion"), "promotion")
    checks = _mapping(promotion.get("checks"), "promotion.checks")
    if tuple(checks) != EXPECTED_CHECKS or any(value is not True for value in checks.values()):
        raise CampaignReportError("all five ordered promotion checks must pass")
    if promotion.get("accepted") is not True or promotion.get("rollback_required") is not False:
        raise CampaignReportError("packaged campaign must contain an accepted non-rollback promotion")
    if promotion.get("library_resolution") != "provisional_promoted":
        raise CampaignReportError("accepted campaign must promote the provisional library")
    if [route["task_id"] for route in baseline["routes"]] != [route["task_id"] for route in provisional["routes"]]:
        raise CampaignReportError("baseline and provisional task order differs")
    if provisional["pass_rate"] < baseline["pass_rate"]:
        raise CampaignReportError("provisional pass rate regressed")
    if provisional["clean_rate"] < baseline["clean_rate"]:
        raise CampaignReportError("provisional clean oracle exposure regressed")
    if provisional["shadowing_rate"] >= baseline["shadowing_rate"]:
        raise CampaignReportError("provisional exposure shadowing did not decrease")
    return {
        "title": _string(root.get("title"), "title"),
        "model": _string(runtime.get("requested_model_id"), "runtime_contract.requested_model_id"),
        "effort": _string(runtime.get("requested_effort"), "runtime_contract.requested_effort"),
        "cli_version": _string(runtime.get("cli_version"), "runtime_contract.cli_version"),
        "model_evidence": model_evidence,
        "provider_models": reported_models,
        "measured": _string(boundary.get("measured"), "evidence_boundary.measured"),
        "not_measured": _string(boundary.get("not_measured"), "evidence_boundary.not_measured"),
        "task_ids": task_ids,
        "verifier_ids": verifier_ids,
        "snapshot_hash": snapshot_hash,
        "baseline": baseline,
        "provisional": provisional,
        "decisions": normalized_decisions,
        "checks": checks,
    }


def _percent(value: float) -> str:
    return f"{round(value * 100):d}%"


def render_report(evidence: Any) -> str:
    """Return a standalone, escaped, no-network HTML campaign review."""

    data = validate_evidence(evidence)
    esc = lambda value: html.escape(str(value), quote=True)
    baseline = data["baseline"]
    provisional = data["provisional"]
    route_rows = "".join(
        f"""<tr><td><strong>{esc(before['task_id'])}</strong><small>{esc(data['verifier_ids'][index])}</small></td>
        <td><span class="pill danger">wrong</span><code>{esc(before['skill_id'])}</code></td>
        <td class="arrow">→</td><td><span class="pill good">oracle only</span><code>{esc(after['skill_id'])}</code></td>
        <td><span class="pill good">pass → pass</span></td></tr>"""
        for index, (before, after) in enumerate(zip(baseline["routes"], provisional["routes"], strict=True))
    )
    decision_cards = "".join(
        f"<article class='decision'><span>route-local HIDE</span><strong>{esc(item['skill_id'])}</strong><small>{item['events']} repeated route-risk traces · copy-on-write</small></article>"
        for item in data["decisions"]
    )
    check_rows = "".join(
        f"<li><span>✓</span>{esc(name.replace('_', ' '))}</li>" for name in data["checks"]
    )
    provider_models = ", ".join(data["provider_models"]) or "none reported"
    evidence_digest = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:">
<title>Merlin — Chat Lifecycle Review</title><style>
:root{{--bg:#07101d;--panel:#101d2d;--line:#26384c;--text:#f4f7fb;--muted:#9db0c6;--cyan:#6ee7f2;--green:#4ade80;--red:#fb7185;--amber:#fbbf24}}
*{{box-sizing:border-box}}html{{background:var(--bg)}}body{{margin:0;color:var(--text);font:15px/1.45 ui-sans-serif,system-ui,-apple-system;background:radial-gradient(circle at 15% 0,#153253 0,transparent 35%),var(--bg)}}
main{{max-width:1180px;margin:auto;padding:34px 28px 48px}}header{{display:flex;justify-content:space-between;gap:24px;align-items:flex-start}}.eyebrow{{color:var(--cyan);font-weight:800;letter-spacing:.16em;text-transform:uppercase}}h1{{font-size:38px;line-height:1.05;margin:8px 0 10px}}h1 em{{color:var(--cyan);font-style:normal}}p{{color:var(--muted);max-width:760px}}.boundary{{max-width:330px;padding:14px 16px;border:1px solid #7c6220;background:#2a2412;border-radius:14px}}.boundary strong{{color:var(--amber);display:block}}nav{{display:flex;gap:8px;margin:28px 0 18px}}button{{color:var(--muted);background:#0b1725;border:1px solid var(--line);padding:10px 15px;border-radius:999px;font-weight:750;cursor:pointer}}button.active{{color:#06202a;background:var(--cyan);border-color:var(--cyan)}}
.stage{{display:none}}.stage.active{{display:block}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.card,.panel{{background:linear-gradient(145deg,#132338,#0e1928);border:1px solid var(--line);border-radius:16px;padding:18px}}.metric{{font-size:32px;font-weight:850}}.metric.danger{{color:var(--red)}}.metric.good{{color:var(--green)}}small{{display:block;color:var(--muted);margin-top:4px}}h2{{font-size:20px;margin:0 0 14px}}.delta{{color:var(--cyan);font-weight:800}}.decisions{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.decision span{{font-size:12px;color:var(--amber);font-weight:800;letter-spacing:.08em}}.decision strong{{display:block;font-size:18px;margin-top:7px}}ul{{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:1fr 1fr;gap:8px}}li{{padding:10px 12px;background:#0b1725;border-radius:10px;color:var(--muted)}}li span{{color:var(--green);font-weight:900;margin-right:8px}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:12px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em}}code{{display:block;color:#cbd9e8;font-size:12px;margin-top:5px}}.pill{{display:inline-block;padding:3px 8px;border-radius:999px;font-size:11px;font-weight:850;text-transform:uppercase}}.pill.danger{{color:#fecdd3;background:#4c1422}}.pill.good{{color:#bbf7d0;background:#123d2b}}.arrow{{color:var(--cyan);font-size:20px}}.meta{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.mono{{font-family:ui-monospace,SFMono-Regular,monospace;word-break:break-all}}footer{{color:#6f839a;margin-top:18px;font-size:12px}}
@media(max-width:760px){{header{{display:block}}.boundary{{max-width:none}}.grid,.decisions,.meta{{grid-template-columns:1fr}}main{{padding:24px 16px}}table{{font-size:12px}}th,td{{padding:9px 5px}}h1{{font-size:30px}}}}
</style></head><body><main>
<header><div><div class="eyebrow">Merlin · Chat Lifecycle Review</div><h1>Trace the route. <em>Repair the harness.</em></h1><p>Four frozen tasks expose a routing defect that task success alone cannot reveal. Merlin stages route-local hides in a copy-on-write library, reruns the identical verifier contract, and promotes only after all gates pass.</p></div><aside class="boundary"><strong>Evidence boundary</strong>{esc(data['measured'])}<small>Not measured: {esc(data['not_measured'])}</small></aside></header>
<nav aria-label="Review stages"><button class="active" data-target="baseline">1 · Overloaded</button><button data-target="intervention">2 · COW intervention</button><button data-target="recovered">3 · Recovered</button></nav>
<section id="baseline" class="stage active"><div class="grid"><article class="card"><small>Verifier pass rate</small><div class="metric good">{baseline['passed']}/{baseline['task_count']}</div><small>Task output looks healthy</small></article><article class="card"><small>Clean oracle exposure</small><div class="metric danger">{_percent(baseline['clean_rate'])}</div><small>Correct skill never reaches the prompt</small></article><article class="card"><small>Exposure shadowing</small><div class="metric danger">{_percent(baseline['shadowing_rate'])}</div><small>Wrong route on every frozen task</small></article></div></section>
<section id="intervention" class="stage"><div class="panel"><h2>Repeated route-risk diagnosis</h2><div class="decisions">{decision_cards}</div><p>The live library stays untouched while the provisional copy is verified. These are routing guards, not skill-content blame.</p></div></section>
<section id="recovered" class="stage"><div class="grid"><article class="card"><small>Same-verifier pass rate</small><div class="metric good">{provisional['passed']}/{provisional['task_count']}</div><small class="delta">no regression</small></article><article class="card"><small>Clean oracle exposure</small><div class="metric good">{_percent(provisional['clean_rate'])}</div><small class="delta">+{_percent(provisional['clean_rate']-baseline['clean_rate'])}</small></article><article class="card"><small>Exposure shadowing</small><div class="metric good">{_percent(provisional['shadowing_rate'])}</div><small class="delta">−{_percent(baseline['shadowing_rate']-provisional['shadowing_rate'])}</small></article></div><div class="panel" style="margin-top:12px"><h2>Promotion gates</h2><ul>{check_rows}</ul></div></section>
<section class="panel" style="margin-top:16px"><h2>Frozen route comparison</h2><table><thead><tr><th>Task / verifier</th><th>Overloaded exposure</th><th></th><th>Provisional exposure</th><th>Outcome</th></tr></thead><tbody>{route_rows}</tbody></table></section>
<section class="meta" style="margin-top:16px"><article class="panel"><h2>Requested runtime contract</h2><p><strong>{esc(data['model'])}</strong> · effort {esc(data['effort'])}<br>{esc(data['cli_version'])}</p><small>Model evidence: {esc(data['model_evidence'])}<br>Provider-reported model IDs: {esc(provider_models)}</small></article><article class="panel"><h2>Immutable evidence</h2><small>Library snapshot</small><div class="mono">{esc(data['snapshot_hash'])}</div><small>Public evidence digest</small><div class="mono">{evidence_digest}</div></article></section>
<footer>Self-contained offline report · no external assets · no raw provider trace · prompt exposure is not provider-native invocation</footer>
</main><script>const buttons=[...document.querySelectorAll('button[data-target]')];const stages=[...document.querySelectorAll('.stage')];buttons.forEach(b=>b.addEventListener('click',()=>{{buttons.forEach(x=>x.classList.toggle('active',x===b));stages.forEach(x=>x.classList.toggle('active',x.id===b.dataset.target));}}));</script></body></html>"""


def load_evidence(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CampaignReportError(f"invalid evidence JSON: {exc}") from exc


def write_report(evidence_path: Path, output_path: Path) -> Path:
    evidence_path = evidence_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve(strict=False)
    if not evidence_path.is_file():
        raise CampaignReportError(f"evidence file does not exist: {evidence_path}")
    if output_path.exists():
        raise CampaignReportError(f"refusing to overwrite existing report: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_report(load_evidence(evidence_path)), encoding="utf-8")
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--open", action="store_true", dest="open_report")
    args = parser.parse_args(argv)
    try:
        output = write_report(args.evidence, args.output)
    except (CampaignReportError, OSError) as exc:
        parser.error(str(exc))
    print(f"saved -> {output}")
    if args.open_report:
        subprocess.run(["open", str(output)], check=False)
        print("opened -> chat lifecycle review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
