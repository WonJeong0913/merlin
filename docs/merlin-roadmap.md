# Merlin Self-Managing Harness Roadmap

## P0 — Evidence-complete invocation

- [x] task-conditioned provisioning;
- [x] deterministic selection and validation;
- [x] prompt exposure evidence;
- [x] live pre/post tool-hook audit;
- [x] harness-signed skill-body load/invocation event;
- [ ] request-bound actual invocation event — **strict mechanism built,
      currently blocked by rollout schema**: `provider_rollout_evidence` v2
      requires a trusted harness signature; matching filename and
      `session_meta.id`; a caller-selected stable provider turn ID; the exact
      request hash; and exact body containment in that one request. Current
      observed Codex rollouts do not emit the required stable turn ID, so v2
      fails closed rather than guessing from session order. The earlier v1
      real-rollout probe is parser-format evidence, not a corroborated Merlin
      observation. This remains unchecked because no real harness turn has the
      required provider-side turn binding, and request presence is not model
      use. See `docs/provider-rollout-corroboration-v1.md`;
- [ ] first promoted matched observation.

Evidence tiers, ranked by who authored the artifact:

| Tier | Author | Status |
|---|---|---|
| `harness_signed` | Merlin | available, self-attested |
| `provider_cli_rollout` | local Codex CLI | parser available; strict corroboration blocked until a stable turn ID is emitted |
| `provider_server_attested` | provider service | **unavailable** — no observed Codex CLI output carries it |

Tier availability is a mechanism, not an observation. It does not move
`provider_native_evidence_complete` and does not unblock promotion.

## P1 — Longitudinal field validation

- [x] 50-task manifest;
- [x] balanced 100-pair schedule;
- [x] append-only observation ledger;
- [x] account-auth turn accounting;
- [ ] phase-1 real observations;
- [ ] phase-2 crossover observations;
- [ ] at least 10 lifecycle changes;
- [ ] real promotion and rollback;
- [ ] computable `G/S`.

## P2 — Merlin product rebuild

- [x] operator-facing CLI around the Python harness — `cli/merlin_cli.py`,
      read-only, exit-coded;
- [x] native or local UI for task, skill, verifier, and trace IDs —
      `apps/merlin-macos` over `bridge/merlin_bridge.py`;
- [ ] audited skill registry and per-project allowlists;
- [ ] user-visible lifecycle explanation — **partial**: the shell and the CLI
      both state every lifecycle operation and why it is or is not available.
      Undo is not built, and cannot be until a promotion exists to undo;
- [ ] persistent encrypted state and deletion controls.

The two shipped surfaces share `src.merlin_harness.governance_view`, so the
terminal and the app report the same facts about the same artifacts.

## P3 — Competitive evidence

- [ ] matched accumulation baseline;
- [ ] usage/recency management baseline;
- [ ] held-out library regression;
- [ ] equal-condition Hermes/OpenClaw comparison;
- [ ] public reproduction bundle after the publication freeze is lifted.

Model-price comparison remains outside the active scope. The research question
is harness governance, not model shopping.
