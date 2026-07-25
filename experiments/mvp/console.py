"""Secure, dependency-free localhost Console for Merlin beta demo."""

from __future__ import annotations

import json
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from src.merlin_harness.personal_workload_campaign import (
    PersonalWorkloadCampaignError,
    validate_personal_workload_campaign,
)

from .lifecycle_session import LifecycleRecoverySession, LifecycleSessionError
from .reporting import render_control_room


MAX_JSON_BODY_BYTES = 4096
LOOPBACK_HOST = "127.0.0.1"
_MVP_ROOT = Path(__file__).resolve().parent
_CAMPAIGN_ROOT = _MVP_ROOT / "results" / "merlin_personal_workload_50_longitudinal_v1"
_LOGO_PATH = _MVP_ROOT / "assets" / "merlin-flower-liquid-glass.png"


def merlin_product_status() -> dict[str, Any]:
    """Return only public, current Merlin campaign state for the local UI."""

    try:
        validation = validate_personal_workload_campaign(_CAMPAIGN_ROOT)
        summary = json.loads((_CAMPAIGN_ROOT / "summary.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, PersonalWorkloadCampaignError):
        return {
            "campaign_valid": False,
            "task_count": 0,
            "pair_count": 0,
            "observation_count": 0,
            "level_7_status": "artifact-unavailable",
            "invocation_evidence_complete": False,
            "g_over_s_status": "unavailable",
            "manifest_sha256_prefix": None,
        }
    return {
        "campaign_valid": validation["valid"],
        "task_count": validation["task_count"],
        "pair_count": validation["pair_count"],
        "observation_count": validation["observation_count"],
        "level_7_status": summary["level_7_status"],
        "invocation_evidence_complete": summary["level_7_checks"]["actual_invocation_evidence_complete"],
        "g_over_s_status": summary["g_over_s_status"],
        "manifest_sha256_prefix": validation["manifest_sha256"][:12],
    }


_STYLE = r"""
:root { color-scheme: dark; font-family: ui-rounded, "SF Pro Rounded", Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
* { box-sizing: border-box; }
body { background: #130a25; color: #fff8ff; margin: 0; min-width: 280px; overflow-x: hidden; }
body::before, body::after { border-radius: 999px; content: ""; filter: blur(56px); opacity: .48; pointer-events: none; position: fixed; z-index: -1; }
body::before { background: #ff75c8; height: 380px; right: -90px; top: -135px; width: 380px; }
body::after { background: #8562ff; bottom: -185px; height: 440px; left: -125px; width: 440px; }
button, input { font: inherit; }
button, .link { backdrop-filter: blur(18px); background: rgba(255,255,255,.11); border: 1px solid rgba(255,255,255,.27); border-radius: 999px; color: #fff8ff; cursor: pointer; min-height: 42px; padding: 9px 14px; transition: background .18s ease, border-color .18s ease, transform .18s ease; }
button:hover:not(:disabled), .link:hover { background: rgba(255,255,255,.2); border-color: rgba(255,205,241,.75); transform: translateY(-1px); }
button:focus-visible, input:focus-visible, .link:focus-visible { outline: 3px solid #ffc2ea; outline-offset: 3px; }
button:disabled { cursor: not-allowed; opacity: .4; transform: none; }
.primary { background: linear-gradient(135deg, #f46bc1, #a56ff0); border-color: rgba(255,223,248,.55); box-shadow: 0 12px 34px rgba(238,89,184,.3); font-weight: 800; }
.shell { margin: 0 auto; max-width: 1240px; padding: 30px 24px 48px; }
.glass { backdrop-filter: blur(24px) saturate(125%); background: linear-gradient(145deg, rgba(255,255,255,.18), rgba(255,255,255,.06)); border: 1px solid rgba(255,255,255,.22); box-shadow: inset 0 1px 0 rgba(255,255,255,.24), 0 18px 50px rgba(32,8,68,.26); }
.top { align-items: center; display: flex; gap: 18px; justify-content: space-between; }
.brand { align-items: center; display: flex; gap: 16px; }
.brand-mark { filter: drop-shadow(0 10px 18px rgba(246,110,201,.3)); height: 74px; mix-blend-mode: multiply; width: 74px; }
.eyebrow { color: #ffd7f2; font-size: .72rem; font-weight: 800; letter-spacing: .12em; margin: 0 0 6px; text-transform: uppercase; }
h1 { font-size: clamp(2.2rem, 5vw, 4.2rem); letter-spacing: -.07em; line-height: .9; margin: 0; }
.lede { color: #eadcf2; line-height: 1.5; margin: 11px 0 0; max-width: 690px; }
.badge { border-radius: 999px; color: #ffe3f5; font-size: .75rem; font-weight: 700; padding: 9px 12px; white-space: nowrap; }
.disclosure { border-radius: 18px; color: #f2e8fa; font-size: .87rem; line-height: 1.5; margin: 24px 0 16px; padding: 14px 16px; }
.campaign { display: grid; gap: 12px; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 16px 0 28px; }
.stat { border-radius: 20px; min-width: 0; padding: 17px; }
.stat span { color: #eacdec; display: block; font-size: .72rem; font-weight: 750; letter-spacing: .08em; text-transform: uppercase; }
.stat strong { display: block; font-size: clamp(1.55rem, 3vw, 2.25rem); letter-spacing: -.06em; margin-top: 8px; overflow-wrap: anywhere; }
.stat small { color: #d8c8e4; display: block; font-size: .73rem; line-height: 1.4; margin-top: 8px; }
.workspace { border-radius: 24px; padding: 19px; }
.workspace-head { align-items: flex-start; display: flex; gap: 16px; justify-content: space-between; margin-bottom: 16px; }
.workspace-head h2 { font-size: 1.1rem; margin: 0; }
.workspace-head p { color: #e4d5eb; font-size: .82rem; line-height: 1.45; margin: 5px 0 0; max-width: 710px; }
.workflow { display: grid; gap: 8px; grid-template-columns: repeat(7, minmax(0, 1fr)); }
.workflow button { font-size: .75rem; line-height: 1.2; overflow-wrap: anywhere; width: 100%; }
.threshold { align-items: center; display: flex; flex-wrap: wrap; gap: 8px; margin-top: 13px; }
.threshold label { color: #eadbed; font-size: .78rem; }
.threshold input { background: rgba(23,8,44,.45); border: 1px solid rgba(255,255,255,.3); border-radius: 12px; color: #fff; min-height: 40px; padding: 8px; width: 72px; }
.status { align-items: center; border-radius: 16px; display: flex; gap: 10px; justify-content: space-between; margin: 15px 0; padding: 12px 14px; }
.status strong { color: #ffe1f3; }
.status-copy { color: #e4d6eb; font-size: .82rem; text-align: right; }
.metrics { display: grid; gap: 12px; grid-template-columns: repeat(3, 1fr); }
.panel { border-radius: 20px; min-width: 0; padding: 16px; }
.metric h2 { color: #f2d9f0; font-size: .75rem; letter-spacing: .08em; margin: 0; text-transform: uppercase; }
.numbers { display: grid; gap: 7px; grid-template-columns: repeat(3, 1fr); margin-top: 11px; }
.number { background: rgba(30,10,55,.35); border: 1px solid rgba(255,255,255,.1); border-radius: 13px; min-width: 0; padding: 9px; }
.number span { color: #d9c7e3; display: block; font-size: .64rem; }
.number strong { display: block; font-size: 1.05rem; margin-top: 3px; overflow-wrap: anywhere; }
.evidence { display: grid; gap: 12px; grid-template-columns: 1.05fr .95fr; margin-top: 12px; }
.panel h2 { font-size: 1rem; margin: 0 0 10px; }
.decision, .check, .log { border-top: 1px solid rgba(255,255,255,.14); font-size: .77rem; line-height: 1.4; padding: 8px 0; }
.decision:first-child, .check:first-child, .log:first-child { border-top: 0; }
.decision code, .trace { color: #ffb7e2; overflow-wrap: anywhere; }
.muted, .empty { color: #d3c4da; }
.pass { color: #e8c4ff; }
.fail { color: #ffb4d4; }
.report-actions { align-items: center; display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.link { align-items: center; display: inline-flex; text-decoration: none; }
.link[aria-disabled="true"] { display: none; }
.error { color: #ffc0d9; min-height: 1.3em; }
.visually-hidden { clip: rect(0 0 0 0); clip-path: inset(50%); height: 1px; overflow: hidden; position: absolute; white-space: nowrap; width: 1px; }
@media (max-width: 880px) { .campaign, .metrics { grid-template-columns: repeat(2, 1fr); } .workflow { grid-template-columns: repeat(3, 1fr); } .evidence { grid-template-columns: 1fr; } }
@media (max-width: 620px) { .shell { padding: 18px 14px 32px; } .top { align-items: flex-start; flex-direction: column; } .brand-mark { height: 57px; width: 57px; } .badge { display: inline-block; } .campaign, .metrics { grid-template-columns: 1fr; } .workflow { grid-template-columns: 1fr 1fr; } .workspace-head, .status { align-items: flex-start; flex-direction: column; } .status-copy { text-align: left; } .numbers { gap: 5px; } }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; } }
"""


_SCRIPT = r"""
(() => {
  "use strict";
  const token = __TOKEN__;
  const actions = ["load_sample", "run_reference", "run_overloaded", "diagnose", "stage_hide", "verify_and_promote"];
  const labels = {
    empty: "Ready", loaded: "Sample loaded", reference_complete: "Reference complete",
    overloaded_complete: "Overload observed", diagnosed: "Trace diagnosis complete",
    staged: "Copy-on-write hide staged", verified: "Same-verifier gate complete"
  };
  const $ = (selector) => document.querySelector(selector);
  const pct = (value) => `${Math.round(Number(value || 0) * 100)}%`;

  function renderMetric(id, name, value) {
    const target = $(id);
    if (!value) {
      target.innerHTML = `<h2>${name}</h2><p class="empty">Pending — run the required step.</p>`;
      return;
    }
    target.innerHTML = `<h2>${name}</h2><div class="numbers">
      <div class="number"><span>Pass</span><strong>${value.passed}/${value.task_count}</strong></div>
      <div class="number"><span>Clean route</span><strong>${pct(value.pi_o)}</strong></div>
      <div class="number"><span>Shadowing</span><strong>${pct(value.pi_m)}</strong></div>
    </div>`;
  }

  function renderCampaign(campaign) {
    $("#campaign-tasks").textContent = campaign.task_count || "—";
    $("#campaign-pairs").textContent = campaign.pair_count || "—";
    $("#campaign-observations").textContent = `${campaign.observation_count || 0}/${campaign.pair_count || 0}`;
    $("#campaign-level").textContent = campaign.level_7_status === "not-yet-qualified" ? "Not yet" : campaign.level_7_status || "Unavailable";
    $("#campaign-proof").textContent = campaign.invocation_evidence_complete ? "Complete" : "Pending";
    $("#campaign-hash").textContent = campaign.manifest_sha256_prefix ? `manifest ${campaign.manifest_sha256_prefix}…` : "campaign artifact unavailable";
  }

  function render(state) {
    $("#stage").textContent = labels[state.stage] || state.stage;
    $("#status-copy").textContent = `Evidence is ${state.report_status}; next actions are enforced by the server.`;
    $("#threshold").value = state.min_shadowing_events;
    $("#threshold").disabled = state.threshold_frozen || state.stage === "empty";
    $("#configure_threshold").disabled = !state.next_actions.includes("configure_threshold");
    actions.forEach((action) => { $(`#${action}`).disabled = !state.next_actions.includes(action); });
    $("#reset").disabled = false;
    renderMetric("#reference", "Curated reference", state.metrics.reference);
    renderMetric("#overloaded", "Overloaded library", state.metrics.overloaded);
    renderMetric("#provisional", "Provisional verification", state.metrics.provisional);

    const decisions = $("#decisions");
    decisions.replaceChildren();
    if (!state.decisions.length) decisions.innerHTML = '<p class="empty">Pending — diagnose overloaded traces.</p>';
    state.decisions.forEach((item) => {
      const row = document.createElement("div"); row.className = "decision";
      const name = document.createElement("code"); name.textContent = `${item.action} ${item.skill_id}`;
      const reason = document.createElement("div"); reason.textContent = item.reason;
      const traces = document.createElement("div"); traces.className = "trace"; traces.textContent = `${item.evidence_trace_ids.length} contributing traces`;
      row.append(name, reason, traces); decisions.append(row);
    });

    const promotion = $("#promotion"); promotion.replaceChildren();
    if (!state.promotion) promotion.innerHTML = '<p class="empty">Pending — stage a hide, then run the same verifier.</p>';
    else state.promotion.checks.forEach((item) => {
      const row = document.createElement("div"); row.className = `check ${item.passed ? "pass" : "fail"}`;
      row.textContent = `${item.passed ? "PASS" : "FAIL"} · ${item.name}`; promotion.append(row);
    });

    const logs = $("#logs"); logs.replaceChildren();
    state.logs.forEach((item) => {
      const row = document.createElement("div"); row.className = "log";
      row.textContent = `${String(item.sequence).padStart(2, "0")} · ${item.message}`; logs.append(row);
    });
    const ready = state.report_status === "ready";
    $("#open-report").setAttribute("aria-disabled", String(!ready));
    $("#download-report").setAttribute("aria-disabled", String(!ready));
    $("#announcer").textContent = `${labels[state.stage] || state.stage}.`;
  }

  async function invoke(action) {
    $("#error").textContent = "";
    const payload = { action };
    if (action === "load_sample" || action === "configure_threshold") {
      payload.min_shadowing_events = Number($("#threshold").value);
    }
    try {
      const response = await fetch("/api/action", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Merlin-Token": token },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.message || `Request failed (${response.status})`);
      render(data.state);
    } catch (error) { $("#error").textContent = error.message; }
  }
  document.querySelectorAll("[data-action]").forEach((button) => button.addEventListener("click", () => invoke(button.dataset.action)));
  Promise.all([
    fetch("/api/state").then((response) => response.json()),
    fetch("/api/merlin-status").then((response) => response.json()),
  ]).then(([session, campaign]) => {
    render(session.state); renderCampaign(campaign);
  }).catch((error) => { $("#error").textContent = error.message; });
})();
"""


def render_console(*, token: str, nonce: str) -> str:
    script = _SCRIPT.replace("__TOKEN__", json.dumps(token))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Merlin Console controlled skill-harness recovery beta.">
  <link rel="icon" href="data:,">
  <title>Merlin Console — Lifecycle Recovery</title>
  <style nonce="{nonce}">{_STYLE}</style>
</head>
<body>
  <main class="shell">
    <header class="top"><div class="brand"><img class="brand-mark" src="/assets/merlin-flower-liquid-glass.png" alt="Merlin flower mark"><div><p class="eyebrow">Self-managing skill harness</p><h1>Merlin</h1><p class="lede">Evidence-led governance for skill generation, provisioning, validation, lifecycle, and bounded harness evolution.</p></div></div><span class="glass badge">Local research beta</span></header>
    <p class="glass disclosure"><strong>Current evidence boundary.</strong> The 50-task campaign is frozen, but has no Merlin field observations yet. The lifecycle workspace below is a deterministic, controlled recovery sandbox—not a provider-native performance claim.</p>
    <section class="campaign" aria-label="Current Merlin campaign status"><article class="glass stat"><span>Frozen tasks</span><strong id="campaign-tasks">—</strong><small>personal-workload contracts</small></article><article class="glass stat"><span>Scheduled pairs</span><strong id="campaign-pairs">—</strong><small>balanced baseline / managed</small></article><article class="glass stat"><span>Observed pairs</span><strong id="campaign-observations">—</strong><small id="campaign-hash">loading campaign artifact</small></article><article class="glass stat"><span>Level 7</span><strong id="campaign-level">—</strong><small>invocation evidence <span id="campaign-proof">—</span></small></article></section>
    <section class="glass workspace" aria-labelledby="workflow-title"><div class="workspace-head"><div><p class="eyebrow">Lifecycle sandbox</p><h2 id="workflow-title">Trace → diagnose → validate</h2><p>Run the fixed controlled sample one state at a time. The server enforces the order, preserves the evidence boundary, and never mutates a live skill library.</p></div><span class="badge glass">127.0.0.1 only</span></div><div class="workflow">
      <button id="reset" data-action="reset" type="button">Reset</button>
      <button id="load_sample" data-action="load_sample" class="primary" type="button">Load sample</button>
      <button id="run_reference" data-action="run_reference" type="button" disabled>Reference</button>
      <button id="run_overloaded" data-action="run_overloaded" type="button" disabled>Stress library</button>
      <button id="diagnose" data-action="diagnose" type="button" disabled>Diagnose</button>
      <button id="stage_hide" data-action="stage_hide" type="button" disabled>Stage hide</button>
      <button id="verify_and_promote" data-action="verify_and_promote" type="button" disabled>Validate gate</button>
    </div><div class="threshold"><label for="threshold">Route-risk threshold (2–5, frozen at diagnosis)</label><input id="threshold" type="number" min="2" max="5" step="1" value="2" disabled><button id="configure_threshold" data-action="configure_threshold" type="button" disabled>Apply threshold</button></div></section>
    <section class="glass status" aria-live="polite"><strong id="stage">Ready</strong><span class="status-copy" id="status-copy">Loading state.</span></section>
    <p class="error" id="error" role="alert"></p>
    <section class="metrics" aria-label="Accumulated condition metrics"><article class="glass panel metric" id="reference"></article><article class="glass panel metric" id="overloaded"></article><article class="glass panel metric" id="provisional"></article></section>
    <section class="evidence"><article class="glass panel"><h2>Trace-backed decisions</h2><div id="decisions"></div></article><article class="glass panel"><h2>Fixed promotion gates</h2><div id="promotion"></div></article></section>
    <section class="glass panel" style="margin-top:12px"><h2>Session log</h2><div id="logs"></div><div class="report-actions" aria-label="Final report actions"><a class="link" id="open-report" href="/report" target="_blank" rel="noopener" aria-disabled="true">Open controlled report</a><a class="link" id="download-report" href="/download/report.json" download aria-disabled="true">Download JSON</a></div></section>
    <p class="visually-hidden" id="announcer" aria-live="assertive"></p>
  </main>
  <script nonce="{nonce}">{script}</script>
</body>
</html>"""


class ConsoleHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, server_address: tuple[str, int]) -> None:
        self.session = LifecycleRecoverySession()
        self.session_lock = threading.RLock()
        self.csrf_token = secrets.token_urlsafe(32)
        self.csp_nonce = secrets.token_urlsafe(18)
        super().__init__(server_address, ConsoleRequestHandler)

    @property
    def base_url(self) -> str:
        return f"http://{LOOPBACK_HOST}:{self.server_address[1]}"

    def server_close(self) -> None:
        with self.session_lock:
            self.session.close()
        super().server_close()


class ConsoleRequestHandler(BaseHTTPRequestHandler):
    server: ConsoleHTTPServer

    def log_message(self, _format: str, *args: object) -> None:
        return

    def _security_headers(self, *, content_type: str, content_length: int, disposition: str | None = None) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if disposition:
            self.send_header("Content-Disposition", disposition)

    def _host_is_valid(self) -> bool:
        return self.headers.get("Host") == f"{LOOPBACK_HOST}:{self.server.server_address[1]}"

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        *,
        content_type: str,
        disposition: str | None = None,
        csp: str | None = None,
    ) -> None:
        self.send_response(status)
        self._security_headers(content_type=content_type, content_length=len(body), disposition=disposition)
        if csp:
            self.send_header("Content-Security-Policy", csp)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        self._send_bytes(status, body, content_type="application/json; charset=utf-8")

    def _error(self, status: HTTPStatus, code: str, message: str) -> None:
        self._send_json(status, {"error": {"code": code, "message": message}})

    def _reject_bad_host(self) -> bool:
        if self._host_is_valid():
            return False
        self._error(HTTPStatus.BAD_REQUEST, "invalid_host", "Host must match the active loopback server.")
        return True

    def do_GET(self) -> None:  # noqa: N802
        if self._reject_bad_host():
            return
        if self.path == "/":
            body = render_console(token=self.server.csrf_token, nonce=self.server.csp_nonce).encode("utf-8")
            nonce = self.server.csp_nonce
            csp = (
                "default-src 'none'; "
                f"style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; "
                "connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
            )
            self._send_bytes(HTTPStatus.OK, body, content_type="text/html; charset=utf-8", csp=csp)
            return
        if self.path == "/assets/merlin-flower-liquid-glass.png":
            try:
                body = _LOGO_PATH.read_bytes()
            except OSError:
                self._error(HTTPStatus.NOT_FOUND, "asset_missing", "Merlin logo asset is unavailable.")
                return
            self._send_bytes(HTTPStatus.OK, body, content_type="image/png")
            return
        if self.path == "/api/state":
            with self.server.session_lock:
                state = self.server.session.public_state()
            self._send_json(HTTPStatus.OK, {"state": state})
            return
        if self.path == "/api/merlin-status":
            self._send_json(HTTPStatus.OK, merlin_product_status())
            return
        if self.path in {"/api/report", "/download/report.json", "/report"}:
            try:
                with self.server.session_lock:
                    report = self.server.session.final_report()
            except LifecycleSessionError as exc:
                self._error(HTTPStatus.CONFLICT, exc.code, str(exc))
                return
            if self.path == "/report":
                body = render_control_room(report).encode("utf-8")
                self._send_bytes(
                    HTTPStatus.OK,
                    body,
                    content_type="text/html; charset=utf-8",
                    csp="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'; base-uri 'none'",
                )
            else:
                body = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                disposition = "attachment; filename=merlin-lifecycle-recovery.json" if self.path.startswith("/download/") else None
                self._send_bytes(HTTPStatus.OK, body, content_type="application/json; charset=utf-8", disposition=disposition)
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found", "Unknown Console path.")

    def do_POST(self) -> None:  # noqa: N802
        if self._reject_bad_host():
            return
        if self.path != "/api/action":
            self._error(HTTPStatus.NOT_FOUND, "not_found", "Unknown Console path.")
            return
        if self.headers.get("Origin") != self.server.base_url:
            self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "A same-origin Console request is required.")
            return
        if not secrets.compare_digest(self.headers.get("X-Merlin-Token", ""), self.server.csrf_token):
            self._error(HTTPStatus.FORBIDDEN, "invalid_token", "A valid Console action token is required.")
            return
        if self.headers.get("Content-Type") != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "invalid_content_type", "Content-Type must be application/json.")
            return
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            self._error(HTTPStatus.LENGTH_REQUIRED, "content_length_required", "Content-Length is required.")
            return
        try:
            length = int(length_header)
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_content_length", "Content-Length must be a non-negative integer.")
            return
        if length < 0:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_content_length", "Content-Length must be a non-negative integer.")
            return
        if length > MAX_JSON_BODY_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body_too_large", "JSON request body exceeds the limit.")
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_json", "Request body must be valid UTF-8 JSON.")
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("action"), str):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_action", "JSON requires a string action field.")
            return
        action = payload["action"]
        threshold_actions = {"load_sample", "configure_threshold"}
        allowed_keys = {"action", "min_shadowing_events"} if action in threshold_actions else {"action"}
        if set(payload) - allowed_keys:
            self._error(HTTPStatus.BAD_REQUEST, "unexpected_fields", "Request contains unsupported fields.")
            return
        if action in threshold_actions and "min_shadowing_events" not in payload:
            self._error(HTTPStatus.BAD_REQUEST, "threshold_required", "min_shadowing_events is required for this action.")
            return
        try:
            with self.server.session_lock:
                if action == "reset":
                    state = self.server.session.reset()
                elif action == "load_sample":
                    state = self.server.session.load_sample(min_shadowing_events=payload["min_shadowing_events"])
                elif action == "configure_threshold":
                    state = self.server.session.configure_threshold(payload["min_shadowing_events"])
                elif action == "run_reference":
                    state = self.server.session.run_reference()
                elif action == "run_overloaded":
                    state = self.server.session.run_overloaded()
                elif action == "diagnose":
                    state = self.server.session.diagnose()
                elif action == "stage_hide":
                    state = self.server.session.stage_hide()
                elif action == "verify_and_promote":
                    state = self.server.session.verify_and_promote()
                else:
                    self._error(HTTPStatus.BAD_REQUEST, "unknown_action", "Unknown Console action.")
                    return
        except LifecycleSessionError as exc:
            status = HTTPStatus.BAD_REQUEST if exc.code == "invalid_threshold" else HTTPStatus.CONFLICT
            self._error(status, exc.code, str(exc))
            return
        self._send_json(HTTPStatus.OK, {"state": state})

    def do_HEAD(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PUT(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def _method_not_allowed(self) -> None:
        if self._reject_bad_host():
            return
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed", "HTTP method is not allowed.")


def create_console_server(*, host: str = LOOPBACK_HOST, port: int = 0) -> ConsoleHTTPServer:
    """Create a loopback-only server; port 0 asks the OS for an empty port."""

    if host != LOOPBACK_HOST:
        raise ValueError("Merlin Console beta only binds to 127.0.0.1.")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be an integer from 0 through 65535")
    return ConsoleHTTPServer((host, port))
