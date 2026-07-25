"""Standalone, dependency-free presentation report for the Build Week demo.

The report is intentionally a *viewer* for the deterministic lifecycle output.
It never recomputes the experiment, mutates the library, or reaches the
network.  Keeping rendering separate from ``run_lifecycle_recovery_demo``
makes the product surface maintainable while keeping the judge path standard
library only.
"""

from __future__ import annotations

import copy
import json
from typing import Any


_PRIVATE_VALUE_PREFIXES = ("/Users/", "/private/", "file://")
_PRIVATE_KEYS = {
    "raw_trace",
    "raw_jsonl",
    "provider_jsonl",
    "provider_raw_data",
    "workspace",
    "workspace_root",
    "source_path",
    "local_path",
}
_PRIVATE_KEY_FRAGMENTS = ("credential", "provider", "raw_trace", "raw_jsonl", "workspace", "local_path")


def _public_report_value(value: Any, *, key: str | None = None) -> Any:
    """Return a JSON-safe public view without filesystem or raw-trace values.

    The lifecycle report currently contains only public task, routing, and
    verifier evidence.  This defensive pass keeps that contract true if a
    future runtime adds adapter metadata before the report is rendered.
    """

    if key and (
        key.casefold() in _PRIVATE_KEYS
        or any(fragment in key.casefold() for fragment in _PRIVATE_KEY_FRAGMENTS)
    ):
        return "redacted from standalone report"
    if isinstance(value, dict):
        return {str(item_key): _public_report_value(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_public_report_value(item) for item in value]
    if isinstance(value, str) and value.startswith(_PRIVATE_VALUE_PREFIXES):
        return "redacted from standalone report"
    return value


def _embedded_json(report: dict[str, Any]) -> str:
    """Encode data for an inert JSON script element without closing it early."""

    safe_report = _public_report_value(copy.deepcopy(report))
    payload = json.dumps(safe_report, ensure_ascii=False, separators=(",", ":"))
    return payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


_STYLE = r"""
:root {
  color-scheme: dark;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #07111f;
  color: #edf6ff;
  font-synthesis: none;
}
* { box-sizing: border-box; }
html { background: #07111f; overflow-x: hidden; }
body { margin: 0; min-width: 320px; overflow-x: hidden; background: radial-gradient(circle at 12% -10%, #17335a 0, transparent 34rem), #07111f; }
button { font: inherit; }
button:focus-visible, a:focus-visible { outline: 3px solid #7ee7ff; outline-offset: 3px; }
.shell { width: min(1440px, calc(100% - 32px)); margin: 0 auto; padding: 24px 0 44px; }
.eyebrow { color: #8fe8fa; font-size: .74rem; font-weight: 800; letter-spacing: .14em; margin: 0 0 8px; text-transform: uppercase; }
.masthead { align-items: end; display: flex; gap: 24px; justify-content: space-between; margin-bottom: 18px; }
h1 { font-size: clamp(2rem, 4.2vw, 4rem); letter-spacing: -.055em; line-height: .96; margin: 0; }
.accent { color: #79e7ff; }
.lede { color: #b9c9dc; font-size: clamp(.98rem, 1.6vw, 1.16rem); line-height: 1.45; margin: 12px 0 0; max-width: 760px; }
.top-actions { align-items: center; display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
.button { background: #14263c; border: 1px solid #355676; border-radius: 9px; color: #eaf5ff; cursor: pointer; padding: 9px 12px; }
.button:hover { background: #1b3553; }
.button.primary { background: #0e758d; border-color: #5ee0f6; color: #f3feff; font-weight: 750; }
.button[disabled] { cursor: not-allowed; opacity: .62; }
.card { background: linear-gradient(145deg, rgba(20, 39, 62, .95), rgba(11, 27, 45, .95)); border: 1px solid #294b6d; border-radius: 16px; box-shadow: 0 18px 48px rgba(0, 0, 0, .16); }
.disclosure { color: #b9c9dc; font-size: .84rem; line-height: 1.45; margin: 0 0 16px; padding: 12px 14px; }
.disclosure strong { color: #f2c879; }
.stepper { display: grid; gap: 10px; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 0 0 14px; }
.stage-button { align-items: center; background: #0c1c2e; border: 1px solid #294b6d; border-radius: 14px; color: #acc1d8; cursor: pointer; display: flex; gap: 11px; min-height: 74px; padding: 12px; text-align: left; transition: background .18s ease, border-color .18s ease, transform .18s ease; }
.stage-button:hover { background: #122b44; transform: translateY(-1px); }
.stage-button[aria-pressed="true"] { background: linear-gradient(135deg, #0d6179, #133c62); border-color: #79e7ff; color: #f5fdff; }
.stage-number { align-items: center; background: #233e5b; border-radius: 50%; display: inline-flex; flex: 0 0 auto; font-size: .8rem; font-weight: 800; height: 28px; justify-content: center; width: 28px; }
.stage-button[aria-pressed="true"] .stage-number { background: #a5f1ff; color: #082033; }
.stage-copy { display: block; font-size: .79rem; margin-top: 3px; opacity: .83; }
.stage-title { font-size: .98rem; font-weight: 800; }
.status-line { align-items: center; color: #b9c9dc; display: flex; font-size: .86rem; gap: 9px; justify-content: space-between; margin: 0 2px 14px; }
.status-line strong { color: #eaf6ff; }
.status-dot { background: #54deaa; border-radius: 50%; display: inline-block; height: 8px; margin-right: 6px; width: 8px; }
.kpis { display: grid; gap: 12px; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.kpi { min-height: 134px; padding: 18px; position: relative; overflow: hidden; }
.kpi::after { background: linear-gradient(135deg, transparent 40%, rgba(121, 231, 255, .08)); content: ""; inset: 0; pointer-events: none; position: absolute; }
.kpi-label { color: #9fb3c8; font-size: .78rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.kpi-value { font-size: clamp(1.75rem, 3.4vw, 2.7rem); font-weight: 850; letter-spacing: -.05em; line-height: 1; margin-top: 10px; }
.kpi-delta { font-size: .86rem; font-weight: 750; margin-top: 10px; }
.delta-good { color: #75edb5; }
.delta-bad { color: #ff9eae; }
.delta-neutral { color: #b6c8d9; }
.governance-grid { display: grid; gap: 9px; grid-template-columns: repeat(5, minmax(0, 1fr)); }
.governance-stage { background: #0b1d30; border: 1px solid #294b6d; border-radius: 12px; min-width: 0; padding: 11px; }
.governance-stage-head { align-items: flex-start; display: flex; gap: 7px; justify-content: space-between; }
.governance-index { align-items: center; background: #233e5b; border-radius: 50%; color: #d8f9ff; display: inline-flex; flex: 0 0 auto; font-size: .67rem; font-weight: 850; height: 20px; justify-content: center; width: 20px; }
.governance-title { color: #f0f8ff; font-size: .8rem; font-weight: 850; line-height: 1.2; }
.governance-status { border-radius: 999px; font-size: .61rem; font-weight: 850; letter-spacing: .04em; padding: 4px 6px; text-transform: uppercase; }
.governance-status.observed, .governance-status.provisional_applied { background: rgba(126, 231, 255, .14); color: #9cefff; }
.governance-status.accepted { background: rgba(64, 188, 136, .18); color: #88f4bd; }
.governance-status.rejected { background: rgba(240, 98, 122, .16); color: #ffabb8; }
.governance-evidence { color: #aec2d5; font-size: .71rem; line-height: 1.35; margin: 9px 0 0; }
.governance-ids { color: #d5eafa; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: .64rem; line-height: 1.35; margin: 7px 0 0; overflow-wrap: anywhere; }
.governance-keys { color: #8ea9c4; font-size: .61rem; line-height: 1.28; margin: 8px 0 0; overflow-wrap: anywhere; }
.governance-keys code { color: #9ce7f5; }
.governance-note { color: #9fb5ca; font-size: .74rem; line-height: 1.4; margin: 10px 0 0; }
.scope-grid { display: grid; gap: 12px; grid-template-columns: 1fr 1fr; }
.scope-box { background: #0b1d30; border: 1px solid #294b6d; border-radius: 12px; min-width: 0; padding: 12px; }
.scope-label { color: #dff5ff; display: block; font-size: .75rem; font-weight: 850; letter-spacing: .06em; text-transform: uppercase; }
.scope-list { color: #aec2d5; font-size: .76rem; line-height: 1.42; margin: 8px 0 0; padding-left: 18px; }
.scope-note { color: #c4d6e6; font-size: .76rem; line-height: 1.45; margin: 11px 0 0; }
.section { margin-top: 16px; padding: 18px; }
.section-heading { align-items: baseline; display: flex; flex-wrap: wrap; gap: 8px 12px; justify-content: space-between; margin: 0 0 14px; }
h2 { font-size: 1.2rem; letter-spacing: -.025em; margin: 0; }
.section-subtitle { color: #a5b9ce; font-size: .87rem; line-height: 1.4; margin: 0; }
.matrix { border: 1px solid #284764; border-radius: 12px; overflow: clip; }
.matrix-header, .matrix-row { display: grid; grid-template-columns: 1.15fr 1.55fr 1.45fr 1.05fr .78fr; gap: 8px; min-width: 0; padding: 10px 12px; }
.matrix-header { background: #0a1a2b; color: #92aac3; font-size: .7rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.matrix-row { align-items: center; border-top: 1px solid #1d3852; font-size: .8rem; }
.matrix-row:nth-child(odd) { background: rgba(12, 30, 48, .52); }
.matrix-cell { min-width: 0; overflow-wrap: anywhere; }
.mono { color: #d8e7f5; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: .75rem; }
.route, .result, .tag { border-radius: 999px; display: inline-block; font-size: .68rem; font-weight: 800; line-height: 1; padding: 5px 7px; white-space: nowrap; }
.route-oracle_only, .result-pass, .tag-pass { background: rgba(64, 188, 136, .18); color: #7bf1b8; }
.route-wrong, .route-mixed, .result-fail, .tag-fail { background: rgba(240, 98, 122, .16); color: #ffabb8; }
.route-empty_no_oracle, .route-empty, .route-spurious { background: rgba(242, 194, 91, .15); color: #ffd574; }
.details-grid { display: grid; gap: 16px; grid-template-columns: 1.18fr .82fr; margin-top: 16px; }
.decision-list, .safety-list { display: grid; gap: 10px; }
.decision { border: 1px solid #294b6d; border-radius: 12px; padding: 13px; }
.decision-head { align-items: center; display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; }
.decision-title { color: #f1f7ff; font-weight: 800; overflow-wrap: anywhere; }
.decision-reason { color: #b8c8d9; font-size: .84rem; line-height: 1.42; margin: 8px 0; }
.trace-label { color: #8ea8c2; display: block; font-size: .7rem; font-weight: 800; letter-spacing: .08em; margin-bottom: 5px; text-transform: uppercase; }
.trace-id { background: #0a1b2c; border: 1px solid #294b6d; border-radius: 5px; color: #c7e5fa; display: inline-block; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: .66rem; margin: 2px 3px 2px 0; max-width: 100%; overflow-wrap: anywhere; padding: 4px 5px; }
.copy-flow { display: grid; gap: 8px; grid-template-columns: 1fr auto 1fr; margin: 13px 0 0; }
.copy-state { background: #0a1b2c; border: 1px solid #294b6d; border-radius: 10px; min-width: 0; padding: 11px; }
.copy-arrow { align-self: center; color: #79e7ff; font-size: 1.25rem; }
.copy-label { color: #91a9c0; display: block; font-size: .68rem; font-weight: 800; letter-spacing: .07em; margin-bottom: 6px; text-transform: uppercase; }
.copy-value { color: #f2f7ff; font-size: .78rem; line-height: 1.38; overflow-wrap: anywhere; }
.promotion-summary { background: rgba(72, 202, 146, .12); border: 1px solid rgba(101, 231, 171, .48); border-radius: 12px; color: #dffded; font-size: .85rem; line-height: 1.45; margin: 12px 0; padding: 12px; }
.promotion-summary.rejected { background: rgba(242, 98, 122, .12); border-color: rgba(255, 147, 164, .5); color: #ffe5e9; }
.safety-check { align-items: flex-start; border-bottom: 1px solid #29435c; display: flex; gap: 9px; padding: 9px 0; }
.safety-check:last-child { border-bottom: 0; }
.check-icon { align-items: center; border-radius: 50%; display: inline-flex; flex: 0 0 auto; font-size: .68rem; font-weight: 900; height: 20px; justify-content: center; width: 20px; }
.check-icon.pass { background: #1d704e; color: #caffdf; }
.check-icon.fail { background: #8d3549; color: #ffe5e9; }
.check-name { color: #eef7ff; display: block; font-size: .84rem; font-weight: 800; }
.check-evidence { color: #a9bdd0; display: block; font-size: .76rem; line-height: 1.35; margin-top: 3px; }
.delta-strip { background: linear-gradient(90deg, #103e5d, #0d664d); border: 1px solid #3f9da0; border-radius: 12px; display: grid; gap: 8px; grid-template-columns: repeat(3, 1fr); margin-top: 16px; padding: 13px; }
.delta-item { color: #cdf8fb; font-size: .79rem; }
.delta-item strong { color: #fff; display: block; font-size: 1.28rem; letter-spacing: -.03em; margin-top: 3px; }
.provenance { color: #b4c4d5; font-size: .82rem; line-height: 1.48; margin-top: 16px; padding: 14px 16px; }
.provenance h2 { margin-bottom: 7px; }
.provenance strong { color: #e9f6ff; }
.footer { color: #92aac0; font-size: .75rem; line-height: 1.45; margin: 16px 2px 0; }
body.presentation .shell { width: min(1280px, calc(100% - 28px)); padding-top: 14px; }
body.presentation .masthead { margin-bottom: 12px; }
body.presentation .lede { display: none; }
body.presentation .disclosure { margin-bottom: 10px; padding-block: 9px; }
body.presentation .section { margin-top: 12px; }
body.presentation .provenance, body.presentation .footer { display: none; }
@media (max-width: 820px) {
  .masthead { align-items: flex-start; flex-direction: column; }
  .top-actions { justify-content: flex-start; }
  .details-grid { grid-template-columns: 1fr; }
  .governance-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .matrix-header { display: none; }
  .matrix-row { gap: 6px 10px; grid-template-columns: 1fr 1fr; padding: 12px; }
  .matrix-cell::before { color: #8ea6bf; content: attr(data-label); display: block; font-size: .65rem; font-weight: 800; letter-spacing: .06em; margin-bottom: 3px; text-transform: uppercase; }
  .matrix-cell.task { grid-column: span 2; }
}
@media (max-width: 620px) {
  .shell { width: min(100% - 20px, 1440px); padding-top: 14px; }
  .stepper, .kpis, .delta-strip, .governance-grid, .scope-grid { grid-template-columns: 1fr; }
  .stage-button { min-height: 62px; }
  .copy-flow { grid-template-columns: 1fr; }
  .copy-arrow { justify-self: center; transform: rotate(90deg); }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .001ms !important; scroll-behavior: auto !important; transition-duration: .001ms !important; }
}
"""


_SCRIPT = r"""
(() => {
  "use strict";
  const dataNode = document.getElementById("report-data");
  const report = JSON.parse(dataNode.textContent);
  const $ = (selector) => document.querySelector(selector);
  const app = $("#control-room");
  const conditionNames = Object.keys(report.conditions || {});
  const recoveredName = conditionNames.find((name) => name !== "Curated reference" && name !== "Overloaded library") || "Lifecycle recovered";
  const stages = [
    { id: "reference", label: "Reference", condition: "Curated reference", copy: "Curated skills establish the 9/10 ceiling." },
    { id: "overloaded", label: "Overloaded", condition: "Overloaded library", copy: "Plausible distractors win routing and fail." },
    { id: "recovered", label: "Recovered", condition: recoveredName, copy: "Trace-backed hide passes the same verifier." },
  ];
  const checkLabels = {
    same_task_coverage: "Same task coverage",
    same_verifier_contract: "Same verifier contract",
    pass_rate_non_regression: "Pass-rate non-regression",
    clean_oracle_routing_non_regression: "Clean-routing improvement",
    shadowing_reduction: "Shadowing reduction",
  };
  const matrixLabels = ["Task", "Selected skill", "Oracle skill", "Route event", "Verifier"];
  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  let currentStage = "overloaded";
  let playbackTimer = null;
  let playbackIndex = 0;

  function conditionFor(stage) { return report.conditions[stage.condition] || { tasks: [], task_count: 0, passed: 0, pass_rate: 0, pi_o: 0, pi_m: 0 }; }
  function percent(value) { return `${Math.round(Number(value || 0) * 100)}%`; }
  function points(value) { return `${value >= 0 ? "+" : ""}${Math.round(value * 100)} pts`; }
  function text(value) { return value && value.length ? value.join(", ") : "—"; }
  function make(tag, className, value) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value !== undefined) element.textContent = value;
    return element;
  }
  function previousCondition(stage) {
    const index = stages.findIndex((item) => item.id === stage.id);
    return index > 0 ? conditionFor(stages[index - 1]) : null;
  }
  function kpiDelta(stage, metric) {
    const current = conditionFor(stage);
    const previous = previousCondition(stage);
    if (!previous) return { label: "→ baseline", style: "delta-neutral" };
    const delta = Number(current[metric] || 0) - Number(previous[metric] || 0);
    const good = metric === "pi_m" ? delta < 0 : delta > 0;
    const unchanged = delta === 0;
    return { label: `${delta >= 0 ? "↑" : "↓"} ${points(delta)}`, style: unchanged ? "delta-neutral" : good ? "delta-good" : "delta-bad" };
  }
  function renderKpis(stage) {
    const data = conditionFor(stage);
    const cards = [
      ["Pass", `${data.passed}/${data.task_count}`, "pass_rate"],
      ["Clean routing", percent(data.pi_o), "pi_o"],
      ["Shadowing", percent(data.pi_m), "pi_m"],
    ];
    const target = $("#kpis");
    target.replaceChildren();
    cards.forEach(([label, value, metric]) => {
      const card = make("article", "card kpi");
      const delta = kpiDelta(stage, metric);
      card.append(make("div", "kpi-label", label), make("div", "kpi-value", value), make("div", `kpi-delta ${delta.style}`, delta.label));
      target.append(card);
    });
  }
  function governanceFacts(stage) {
    const evidence = stage.evidence || {};
    const facts = [];
    if (evidence.task_count !== undefined) facts.push(`${evidence.task_count} task records`);
    if (evidence.selection_count !== undefined) facts.push(`${evidence.selection_count} selections`);
    if (evidence.trace_count !== undefined) facts.push(`${evidence.trace_count} traces`);
    if (evidence.route_risk_trace_count !== undefined) facts.push(`${evidence.route_risk_trace_count} route-risk traces`);
    if (evidence.decision_count !== undefined) facts.push(`${evidence.decision_count} hide decisions`);
    if (evidence.evidence_trace_count !== undefined) facts.push(`${evidence.evidence_trace_count} decision traces`);
    if (evidence.re_run_task_count !== undefined) facts.push(`${evidence.re_run_task_count} re-run tasks`);
    if (evidence.verifier_id_count !== undefined) facts.push(`${evidence.verifier_id_count} verifier IDs`);
    if (evidence.promotion_check_count !== undefined) facts.push(`${evidence.passed_promotion_check_count}/${evidence.promotion_check_count} promotion gates`);
    if (evidence.unique_skill_count !== undefined) facts.push(`${evidence.unique_skill_count} unique skill IDs`);
    return facts.join(" · ") || "No recorded evidence";
  }
  function renderGovernanceLoop() {
    const loop = report.governance_loop || {};
    const target = $("#governance-loop");
    target.replaceChildren();
    (loop.stages || []).forEach((stage, index) => {
      const card = make("article", "governance-stage");
      card.setAttribute("data-governance-stage", stage.id || "unknown");
      const head = make("div", "governance-stage-head");
      const title = make("div", "governance-title");
      title.append(make("span", "governance-index", String(index + 1)), document.createTextNode(` ${stage.label || stage.id || "Stage"}`));
      head.append(title, make("span", `governance-status ${stage.status || "observed"}`, stage.status || "observed"));
      card.append(head, make("p", "governance-evidence", governanceFacts(stage)));
      const ids = stage.evidence && (stage.evidence.target_skill_ids || stage.evidence.skill_ids);
      if (ids && ids.length) card.append(make("p", "governance-ids", `IDs: ${ids.join(", ")}`));
      const keys = stage.evidence_keys || [];
      if (keys.length) {
        const keyBlock = make("p", "governance-keys", "Evidence: ");
        keys.forEach((key, keyIndex) => {
          if (keyIndex) keyBlock.append(document.createTextNode(" · "));
          keyBlock.append(make("code", "", key));
        });
        card.append(keyBlock);
      }
      target.append(card);
    });
    $("#governance-note").textContent = loop.selection_evidence_note || "Selection evidence note unavailable.";
  }
  function renderScopeBoundary() {
    const boundary = report.scope_boundary || {};
    const renderList = (target, entries) => {
      target.replaceChildren();
      (entries || []).forEach((entry) => target.append(make("li", "", entry)));
    };
    renderList($("#scope-active"), boundary.active_in_this_demo);
    renderList($("#scope-deferred"), boundary.deferred);
    $("#scope-note").textContent = [boundary.actual_invocation_boundary, boundary.system_claim].filter(Boolean).join(" ");
  }
  function routeClass(route) { return `route route-${String(route || "empty").replace(/[^a-z_]/g, "_")}`; }
  function renderMatrix(stage) {
    const target = $("#route-matrix");
    target.replaceChildren();
    const header = make("div", "matrix-header");
    matrixLabels.forEach((label) => header.append(make("div", "matrix-cell", label)));
    target.append(header);
    conditionFor(stage).tasks.forEach((task) => {
      const row = make("div", "matrix-row");
      const values = [
        [task.task_id, "task mono"],
        [text(task.selected_skill_ids), "mono"],
        [text(task.oracle_skill_ids), "mono"],
        [task.route_event, ""],
        [task.success ? "PASS" : "FAIL", ""],
      ];
      values.forEach(([value, className], index) => {
        const cell = make("div", `matrix-cell ${className}${index === 0 ? " task" : ""}`, value);
        cell.dataset.label = matrixLabels[index];
        if (index === 3) {
          cell.textContent = "";
          cell.append(make("span", routeClass(task.route_event), task.route_event));
        }
        if (index === 4) {
          cell.textContent = "";
          cell.append(make("span", task.success ? "result result-pass" : "result result-fail", value));
        }
        row.append(cell);
      });
      target.append(row);
    });
  }
  function statusText(stage) {
    if (stage.id === "reference") return "Curated reference: two correct skills; one no-oracle control task remains outside recovery.";
    if (stage.id === "overloaded") return "Overload observed: two purpose-built distractors create repeated wrong routes.";
    return report.promotion && report.promotion.accepted ? "Promotion accepted: provisional hide became the live library state after all fixed gates passed." : "Promotion rejected: the original library remains live.";
  }
  function renderStatus(stage) {
    $("#stage-label").textContent = `${stage.label} · ${stage.condition}`;
    $("#stage-summary").textContent = statusText(stage);
    $("#route-stage-name").textContent = stage.label;
    $("#matrix-caption").textContent = `${conditionFor(stage).task_count} task traces; selected and oracle columns are routing evidence, not provider-native invocation claims.`;
  }
  function renderDecisions() {
    const target = $("#decision-list");
    target.replaceChildren();
    (report.lifecycle_decisions || []).forEach((decision) => {
      const box = make("article", "decision");
      const head = make("div", "decision-head");
      head.append(make("div", "decision-title mono", decision.skill_id), make("span", "tag tag-fail", String(decision.action || "hide").toUpperCase()));
      box.append(head, make("p", "decision-reason", decision.reason));
      const evidence = make("div");
      evidence.append(make("span", "trace-label", "Contributing route trace IDs"));
      (decision.evidence_trace_ids || []).forEach((traceId) => evidence.append(make("code", "trace-id", traceId)));
      box.append(evidence);
      target.append(box);
    });
  }
  function statusFor(skillId, source) {
    const statuses = source || {};
    return statuses[skillId] || "not recorded";
  }
  function lifecycleSummary() {
    const decisions = report.lifecycle_decisions || [];
    const provisional = report.provisional_change || {};
    const resolution = report.library_resolution || {};
    const sourceNames = decisions.map((item) => item.skill_id).join(", ");
    const original = decisions.map((item) => `${item.skill_id}: ${statusFor(item.skill_id, provisional.original_statuses)}`).join(" · ");
    const proposed = decisions.map((item) => `${item.skill_id}: ${statusFor(item.skill_id, provisional.provisional_statuses)}`).join(" · ");
    const final = decisions.map((item) => `${item.skill_id}: ${statusFor(item.skill_id, resolution.final_statuses)}`).join(" · ");
    $("#copy-original").textContent = original || "No lifecycle decisions";
    $("#copy-provisional").textContent = proposed || "No provisional changes";
    $("#copy-final").textContent = final || "No final status";
    $("#copy-caption").textContent = `Copy-on-write isolates ${sourceNames || "the proposed change"}: the original library remains untouched while the verifier re-runs.`;
    $("#copy-final-label").textContent = resolution.mode === "provisional_promoted" ? "Live after promotion" : "Live after rollback";
  }
  function renderPromotion() {
    const promotion = report.promotion || { accepted: false, reason: "Promotion evidence unavailable", checks: [] };
    const summary = $("#promotion-summary");
    summary.classList.toggle("rejected", !promotion.accepted);
    summary.textContent = `${promotion.accepted ? "PROMOTED" : "ROLLED BACK"} — ${promotion.reason}`;
    const target = $("#safety-list");
    target.replaceChildren();
    (promotion.checks || []).forEach((check) => {
      const row = make("div", "safety-check");
      row.append(make("span", `check-icon ${check.passed ? "pass" : "fail"}`, check.passed ? "✓" : "×"));
      const body = make("div");
      body.append(make("span", "check-name", checkLabels[check.name] || check.name), make("span", "check-evidence", check.evidence));
      row.append(body);
      target.append(row);
    });
  }
  function renderRecoveryDelta() {
    const delta = report.recovery_delta || {};
    const entries = [
      ["Pass recovery", points(Number(delta.pass_rate_gain || 0))],
      ["Clean-routing recovery", points(Number(delta.pi_o_gain || 0))],
      ["Shadowing change", points(Number(delta.pi_m_change || 0))],
    ];
    const target = $("#recovery-delta");
    target.replaceChildren();
    entries.forEach(([label, value]) => {
      const item = make("div", "delta-item", label);
      item.append(make("strong", "", value));
      target.append(item);
    });
  }
  function setStage(id, { announce = true } = {}) {
    const stage = stages.find((item) => item.id === id) || stages[0];
    currentStage = stage.id;
    app.dataset.stage = stage.id;
    document.querySelectorAll("[data-stage-button]").forEach((button) => {
      const active = button.dataset.stageButton === stage.id;
      button.setAttribute("aria-pressed", String(active));
    });
    renderStatus(stage);
    renderKpis(stage);
    renderMatrix(stage);
    if (announce) $("#announcer").textContent = `${stage.label} stage shown. ${statusText(stage)}`;
  }
  function stopPlayback(message = "Playback paused.") {
    if (playbackTimer !== null) window.clearTimeout(playbackTimer);
    playbackTimer = null;
    $("#pause-playback").disabled = true;
    $("#pause-playback").textContent = "Paused";
    $("#announcer").textContent = message;
  }
  function advancePlayback() {
    if (playbackIndex >= stages.length) {
      stopPlayback("Playback complete. Recovered stage remains selected.");
      return;
    }
    setStage(stages[playbackIndex].id);
    playbackIndex += 1;
    playbackTimer = window.setTimeout(advancePlayback, motionQuery.matches ? 1 : 2600);
  }
  function startPlayback() {
    if (playbackTimer !== null) window.clearTimeout(playbackTimer);
    if (motionQuery.matches) {
      setStage("reference");
      stopPlayback("Reduced motion is enabled; automatic playback is disabled. Use the three stage controls to inspect the evidence.");
      return;
    }
    playbackIndex = 0;
    $("#pause-playback").disabled = false;
    $("#pause-playback").textContent = "Pause playback";
    $("#announcer").textContent = "Three-stage playback started.";
    advancePlayback();
  }
  function downloadJson() {
    const blob = new Blob([JSON.stringify(report, null, 2) + "\n"], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "merlin-lifecycle-recovery.json";
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(link.href), 0);
  }
  document.querySelectorAll("[data-stage-button]").forEach((button) => button.addEventListener("click", () => {
    stopPlayback("Playback stopped; manual stage selected.");
    setStage(button.dataset.stageButton);
  }));
  $("#replay").addEventListener("click", startPlayback);
  $("#pause-playback").addEventListener("click", () => stopPlayback());
  $("#download-json").addEventListener("click", downloadJson);
  $("#presentation-mode").addEventListener("click", async () => {
    document.body.classList.toggle("presentation");
    const enabled = document.body.classList.contains("presentation");
    $("#presentation-mode").setAttribute("aria-pressed", String(enabled));
    $("#presentation-mode").textContent = enabled ? "Exit presentation" : "Presentation mode";
    if (enabled && document.documentElement.requestFullscreen && !document.fullscreenElement) {
      try { await document.documentElement.requestFullscreen(); } catch (_) { /* Browser may block fullscreen; compact mode still works. */ }
    }
    if (!enabled && document.fullscreenElement && document.exitFullscreen) await document.exitFullscreen();
  });
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement && document.body.classList.contains("presentation")) {
      document.body.classList.remove("presentation");
      $("#presentation-mode").setAttribute("aria-pressed", "false");
      $("#presentation-mode").textContent = "Presentation mode";
    }
  });
  motionQuery.addEventListener("change", () => {
    app.dataset.reducedMotion = String(motionQuery.matches);
    if (motionQuery.matches && playbackTimer !== null) stopPlayback("Playback paused because reduced motion was enabled.");
  });
  app.dataset.reducedMotion = String(motionQuery.matches);
  renderDecisions();
  lifecycleSummary();
  renderGovernanceLoop();
  renderScopeBoundary();
  renderPromotion();
  renderRecoveryDelta();
  setStage(currentStage, { announce: false });
})();
"""


def render_control_room(report: dict[str, Any]) -> str:
    """Render a standalone interactive Control Room without external assets."""

    payload = _embedded_json(report)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Merlin Control Room: deterministic trace-governed skill recovery demonstration.">
  <link rel="icon" href="data:,">
  <title>Merlin Control Room — Lifecycle Recovery</title>
  <style>{_STYLE}</style>
</head>
<body>
  <main class="shell" id="control-room" data-stage="overloaded" data-reduced-motion="false">
    <header class="masthead">
      <div>
        <p class="eyebrow">Merlin · Trace-governed skill recovery</p>
        <h1>The <span class="accent">KING</span> Control Room</h1>
        <p class="lede">Inspect how a controlled skill overload routes work incorrectly, then verify a narrow copy-on-write lifecycle recovery with the same deterministic verifiers.</p>
      </div>
      <div class="top-actions" aria-label="Report actions">
        <button class="button" id="download-json" type="button">Download report JSON</button>
        <button class="button" id="presentation-mode" type="button" aria-pressed="false">Presentation mode</button>
      </div>
    </header>

    <p class="card disclosure"><strong>Controlled deterministic demo.</strong> The two distractors are purpose-built to expose shadowing. This is a reproducible runtime demonstration, not a full SkillsBench or production-model claim. The 9/10 ceiling is intentional: one control task has no oracle skill and is outside the recovery claim.</p>

    <nav class="stepper" aria-label="Lifecycle stages">
      <button class="stage-button" data-stage-button="reference" aria-pressed="false" type="button"><span class="stage-number">1</span><span><span class="stage-title">Reference</span><span class="stage-copy">Curated skills</span></span></button>
      <button class="stage-button" data-stage-button="overloaded" aria-pressed="true" type="button"><span class="stage-number">2</span><span><span class="stage-title">Overloaded</span><span class="stage-copy">Wrong routing observed</span></span></button>
      <button class="stage-button" data-stage-button="recovered" aria-pressed="false" type="button"><span class="stage-number">3</span><span><span class="stage-title">Recovered</span><span class="stage-copy">Same verifier re-run</span></span></button>
    </nav>

    <div class="status-line" aria-live="polite"><span><span class="status-dot"></span><strong id="stage-label">Overloaded</strong></span><span id="stage-summary">Loading stage evidence.</span></div>
    <section class="kpis" id="kpis" aria-label="Stage KPIs"></section>

    <section class="card section" aria-labelledby="governance-title">
      <div class="section-heading"><div><h2 id="governance-title">Harness Governance Loop</h2><p class="section-subtitle">Five executed controls, each linked to evidence in this exported report.</p></div></div>
      <div class="governance-grid" id="governance-loop" aria-label="Harness governance loop evidence"></div>
      <p class="governance-note" id="governance-note"></p>
    </section>

    <section class="card section" aria-labelledby="route-title">
      <div class="section-heading">
        <div><h2 id="route-title"><span id="route-stage-name">Overloaded</span> route matrix</h2><p class="section-subtitle" id="matrix-caption">Loading task evidence.</p></div>
        <div class="top-actions"><button class="button primary" id="replay" type="button">Replay 3 stages</button><button class="button" id="pause-playback" type="button" disabled>Paused</button></div>
      </div>
      <div class="matrix" id="route-matrix" aria-label="Ten-task route matrix"></div>
    </section>

    <div class="details-grid">
      <section class="card section" aria-labelledby="decision-title"><div class="section-heading"><div><h2 id="decision-title">Trace-backed lifecycle decisions</h2><p class="section-subtitle">Only the two repeatedly harmful skills are proposed for hide.</p></div></div><div class="decision-list" id="decision-list"></div></section>
      <section class="card section" aria-labelledby="copy-title"><div class="section-heading"><div><h2 id="copy-title">Copy-on-write library state</h2><p class="section-subtitle" id="copy-caption">Loading lifecycle state.</p></div></div><div class="copy-flow"><div class="copy-state"><span class="copy-label">Live original</span><span class="copy-value" id="copy-original"></span></div><span class="copy-arrow" aria-hidden="true">→</span><div class="copy-state"><span class="copy-label">Provisional re-run</span><span class="copy-value" id="copy-provisional"></span></div></div><div class="copy-state" style="margin-top:8px"><span class="copy-label" id="copy-final-label">Live after promotion</span><span class="copy-value" id="copy-final"></span></div></section>
    </div>

    <section class="card section" aria-labelledby="promotion-title"><div class="section-heading"><div><h2 id="promotion-title">Promotion safety checks</h2><p class="section-subtitle">The provisional state may become live only after every pre-registered condition passes.</p></div></div><div class="promotion-summary" id="promotion-summary"></div><div class="safety-list" id="safety-list"></div><div class="delta-strip" id="recovery-delta" aria-label="Observed recovery delta"></div></section>

    <section class="card section" id="scope-boundary" aria-labelledby="scope-title"><div class="section-heading"><div><h2 id="scope-title">Demo scope boundary</h2><p class="section-subtitle">This report names what was executed here and what remains future work.</p></div></div><div class="scope-grid"><div class="scope-box"><span class="scope-label">Active in this demo</span><ul class="scope-list" id="scope-active"></ul></div><div class="scope-box"><span class="scope-label">Deferred</span><ul class="scope-list" id="scope-deferred"></ul></div></div><p class="scope-note" id="scope-note"></p></section>

    <section class="card provenance" aria-labelledby="provenance-title"><h2 id="provenance-title">GPT-5.6 development provenance</h2><p>Requested <strong>gpt-5.6-terra</strong> Codex smoke verifier: <strong>passed</strong>. Actual provider-native skill invocation evidence: <strong>incomplete</strong>. The recorded smoke thread is <strong>not</strong> a Codex <strong>/feedback</strong> Session ID. This card documents development evidence; it does not recast this deterministic result as a live model-performance claim.</p></section>
    <p class="footer">The report is self-contained: no network request, external font, CDN, or embedded raw provider transcript is used. JSON export contains the same public lifecycle evidence shown here.</p>
    <p class="visually-hidden" id="announcer" aria-live="assertive"></p>
  </main>
  <style>.visually-hidden{{clip:rect(0 0 0 0);clip-path:inset(50%);height:1px;overflow:hidden;position:absolute;white-space:nowrap;width:1px;}}</style>
  <script id="report-data" type="application/json">{payload}</script>
  <script>{_SCRIPT}</script>
</body>
</html>
"""
