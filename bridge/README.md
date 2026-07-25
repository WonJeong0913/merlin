# Merlin local JSONL bridge

> **Origin.** Ported on 2026-07-25 from the frozen pre-Merlin workspace at
> `../The king/handoff/claude-code-agent-app` (1,006-line backend). That tree is
> read-only immutable provenance and was not modified. Imports were repointed
> `src.the_king.*` → `src.merlin_harness.*`, `AppBridge` → `MerlinBridge`, the
> per-workspace trace directory `.the-king/` → `.merlin/`, and the repository
> root now resolves one level up instead of three.

One process, one request per line, one response per line, over stdio. The
desktop shell owns presentation and the account-connection screen; this process
owns the Codex-backed chat session, skill provisioning, bounded autonomy, and
safe response envelopes. It never reads or returns an API key, access token, or
Codex credential file.

## Files

| Path | Role |
|---|---|
| `merlin_bridge.py` | the bridge itself — dispatch, envelopes, session wiring |
| `codex_model_catalog.py` | Codex CLI model catalog query |
| `contracts/merlin-bridge.schema.json` | request/response schema, `schema_version: 1` |
| `contracts/examples.jsonl` | worked request/response examples |
| `tests/` | 15 tests, run directly with `python3` |

## Commands

`bridge.hello`, `account.status`, `account.connect_spec`, `account.models`,
`session.start`, `session.restart`, `session.update_settings`,
`session.status`, `session.new_thread`, `session.resume_thread`, `chat.send`,
`approval.resolve`, `feedback.record`, and:

**`harness.governance`** — added for Merlin. Session-independent and read-only.
It returns the campaign standing (manifest/schedule SHA-256, task and pair
counts, matched observations, `g_over_s`, Level 7 checks), the harness-evolution
ledger summary, the invocation-evidence gate, and every lifecycle operation with
the reason it is or is not currently available.

The bridge is a **transport** for this view, not an implementation of it. The
logic lives in `src.merlin_harness.governance_view`, shared with the operator
CLI at `cli/merlin_cli.py`, so the terminal and the app cannot disagree about
the same files. Behaviour is covered by `tests/test_governance_view.py`; the
bridge test only asserts that the served payload equals the core view.

Two properties matter and are covered by tests:

- the campaign is **revalidated on read** rather than trusting the stored
  summary, so ledger drift surfaces instead of being reported as healthy;
- absent artifacts report as absent. An evolution ledger that does not exist
  returns `ledger_present: false` with no counts, never a ledger with zero
  observations. A null `g_over_s` stays null and never collapses to `0`.

The command reports availability; it does not perform lifecycle changes.
Promotion is gated on provider-native invocation evidence, and repair, merge,
hide and retirement are evaluator-backed batch campaigns. Exposing buttons for
them here would manufacture a decision the harness has not earned.

## Run

```bash
cd merlin
PYTHONDONTWRITEBYTECODE=1 python3 bridge/tests/test_merlin_bridge.py
PYTHONDONTWRITEBYTECODE=1 python3 bridge/tests/test_codex_model_catalog.py
```

Manual round trip:

```bash
cd merlin
printf '%s\n' '{"request_id":"1","command":"bridge.hello","payload":{}}' \
  | PYTHONPATH=. python3 bridge/merlin_bridge.py
```

The Swift client at `apps/merlin-macos` locates this file at
`bridge/merlin_bridge.py` relative to the repository root, so that path is part
of the contract.

## Evidence boundary

`session.status` returns `recorded_evidence`. Under Merlin that list is
currently **empty and that is correct**: evidence deliberately did not carry
over from the pre-Merlin tree (`MIGRATION.md`, "Evidence reset"), and legacy
artifacts must not be relabeled as Merlin evidence. The ported test asserts the
shape of the list, not legacy artifact IDs. Restore per-artifact assertions once
real Merlin-namespaced evidence exists on disk.
