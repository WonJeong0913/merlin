# Merlin decision lineage — carried over from The KING

> **Origin: The KING (pre-Merlin).** Reconstructed from the Codex session that produced the migration.
> Numbers below are **legacy provenance**. They record what was decided and what was demonstrated
> under the former namespace, and must not be relabeled as Merlin evidence.

## Thread chain

| Thread | Window (KST) | Turns | What happened |
|---|---|---|---|
| `019f8e8c-1802-7fa0-af1a-8870425901a1` | 2026-07-23 19:36 → 07-24 16:29 | 42 user turns, 136 patches | The core research session. Everything below was decided and built here. |
| `019f9304-da0e-71a3-8446-4e9da172e607` | 2026-07-24 16:26 → 17:58 | 13 user turns | Delegated continuation. Signed invocation event, Merlin UI, logo, Control Room. |

Both raw transcripts are preserved beside this file.

---

## Standing constraints

- **GitHub frozen until the award announcement.** No push, PR, release, tag, issue or README
  propagation, no repository setting changes. Only an explicit "the award is announced, unfreeze"
  from the user lifts this.
- The Build Week judge ZIP stays byte-identical.
- The former TypeScript/OpenClaw product is recovery-only; do not modify it unsupervised.

## The thesis, in the user\'s own framing

Others treat a skill library as a fixed asset — either "write better skills" or "use fewer skills."
This project treats skills as an evolving population, the harness as the selection pressure acting on
it, and the cost saved by skill reuse as the fuel for that evolution.

Stated as a measurable claim: with `S` = verified per-task saving from reuse and `G` = governance
overhead (validation, repair, retirement, audit), **`G/S < 1` means the system compounds; `G/S > 1`
means governance is a luxury.** The curve of `G/S` over time is the intended killer figure.

Two guards were attached to the thesis at the time it was adopted:

1. **Goodhart guard.** Evolution needs a selection signal, and that signal is the verifier. A weak
   verifier evolves skills toward passing the verifier rather than doing the task. Frozen verifier +
   same-verifier promotion handles the short term; long term, verifier replacement is classified as a
   **high-risk change requiring a human approval gate** — a case of the existing governance, not a new
   system.
2. **Do not spend estimated value.** Only verified savings become spendable reinvestment budget.
   Estimated failure-avoidance value is recorded as outcome but never spent as currency.

## The 80/20 split (and its correction)

- **80% — harness governance and evolution:** provisioning, selection/abstention, validation, actual
  invocation measurement, shadowing diagnosis and reduction, library health, promotion, quarantine,
  hide, repair, merge, retire, rollback, and the safe evolution of the typed hook/processor layer itself.
- **20% — minimal contract-conformant skill supply:** basic create-skill, `SKILL.md`-centered candidate
  generation, satisfying the `s=(P,O,A,V,F)` contract fields and validator interface.

**Correction made mid-session:** 20% is *not* "skill content improvement research." Iterating on skill
prose or generation prompts is explicitly out of scope — it was judged a waste of resources. A skill is
a **contract object the harness manages**, not a well-written document. Repair therefore belongs to the
80% lifecycle: detect contract violation → bounded candidate patch → same-verifier check → promote or
rollback exactly.

Success is **not** "we shrank the library." Success is: the library keeps growing while `π_m` stays
suppressed, `π_o` and verification pass rates hold or rise, and the library and harness reorganize
themselves on evidence.

## Scope removed

Low-cost / Chinese model price comparison was **dropped from scope** (2026-07-24). The dollar ledger
remains in the code, inactive, for a possible later ablation. Experiments run on **account
authentication, not paid API** — so cost is measured in provider turns, tokens, wall time,
invocation counts, retries and quota consumption, never in dollars.

---

## What was built, in order

1. **Provider runtime + cost governance.** Provider-neutral OpenAI-compatible layer with per-request
   cost preflight; keys never written to files or logs; calls blocked before dispatch if the estimated
   cost exceeds the cap. Verified-savings → reinvestment-budget ledger with a separate verifier
   replacement gate.
2. **Account resource governance.** Subscription experiments are never converted to dollars. Only
   provider turns saved under identical provider / model / effort / quota window / verifier count as
   reinvestment budget. Mixed or failed conditions are not admitted as budget.
3. **Managed Library Loop v1.** Bound the three previously separate evidence lanes — 10-task lifecycle
   recovery, `M0/M1/M2-H/M2-K` management comparison, 8-hook HarnessX — into one SHA-256 evidence
   envelope. Controlled recovery `1/10 → 9/10`, `π_m 89% → 0%`. Zero API calls. Independent
   `--verify-existing` validator; artifact tampering, report tampering, path escape and output-path
   reuse all rejected.
4. **HarnessX typed runtime wired to real chat, shadow-only.** Six genuinely observable hooks recorded
   on live turns (`task_start → step_start → before_model → after_model → step_end → task_end`).
   `before_tool` / `after_tool` were left explicitly **unobserved** rather than fabricated. Processor
   output never applied to the real prompt or the user-visible answer at this stage.
5. **Real tool observation.** Parsing Codex JSONL `item.started → item.completed` command pairs gave a
   genuine `8/8` hook record on a real account-auth turn. Raw commands and outputs are not stored —
   only hashes, status and exit codes. The report is forced to state that this is **post-execution
   shadow replay, not live enforcement** (`pre_execution_control_available=false`).
6. **Live pre-execution enforcement.** Codex\'s native `PreToolUse` / `PostToolUse` hook points were used
   as an adapter into typed HarnessX semantics — deliberately *not* a bare command allowlist. The live
   policy file carries the paper\'s `H=(M,C)`, `C=(P,S)` structure; `pass-through → allow`,
   `intercept → deny`. Canary: `pwd` allowed and observed, `touch harnessx-blocked.txt` blocked before
   execution, no post event for the denied call, the file never created.
   *Ownership note recorded at the time:* the hook **engine and hook points are Codex\'s**; the typed
   event conversion, processor, variant contract and audit chain are this project\'s.
7. **First harness policy evolution round.** Parent policy failed a `ls -1` requirement. Candidate v0
   fixed it but regressed `pwd` → rejected by the same verifier → exact parent rollback. Candidate v1
   preserved existing behavior and added only `ls -1` → `6/6` → promoted → loaded into the real Codex
   hook, with the promoted SHA matching the loaded SHA.
8. **AEGIS, four bounded roles.** `Digester → Planner → Evolver → Critic`, each isolated with an empty
   read-only workspace, no tool use, strict JSON schema. **The model proposes; only the typed builder
   constructs candidates and only the deterministic gate promotes.** Demonstrated explicitly: when the
   Critic ranked a regressing candidate as `ship`, the gate refused and rolled back to the parent.
9. **Frozen 50-case verifier suite.** Not padding — nine policy categories (prior allow 2, target allow
   1, filesystem mutation 17, shell composition 12, network 4, package install 3, process control 2,
   Git mutation 4, non-Bash tool 5). Parent `49/50` → AEGIS candidate `50/50`, suite-bound independent
   replay `14/14`.
10. **Multi-round, multi-candidate campaign.** Failures `3 → 2 → 1 → 0`, final `50/50`, each round bound
    to parent variant / suite / action-space hashes; no-progress candidates that re-propose an
    already-allowed command are rejected. Model-free rounds succeeded.
11. **Central controller and evolution ledger.** Trace ingestion routes a problem to its owner: harness
    fault → AEGIS, skill content fault → repair, routing fault → provisioning repair, long disuse →
    retirement, safety false-allow → **human review, never automatic repair**. Append-only hash ledger
    for promotions, regressions, rollbacks and `G/S`. Pre-execution policy extended past Bash to
    `Read`, `Grep`, `Glob`.
12. **50 independent governance tasks** running through production management code (10 skill/provisioning
    branches, 6 central controller, 8 verifier replacement gate, 8 account resource reinvestment,
    10 multi-tool pre-execution, 8 trace→evolution branch): `50/50`.
13. **Personal workload 50 longitudinal protocol.** Fifty real recurring work items frozen across six
    groups; Phase 1 = 50 baseline/managed pairs, Phase 2 = 50 pairs with the order reversed, 100 matched
    observations total. Identical provider / model / effort / verifier / input enforced; arm order stored
    per record and checked against the frozen schedule so the crossover is verifiable rather than nominal.
    Status on migration: **protocol complete, executions 0**.
14. **Account-auth pilot, 2 pairs.** 4 real provider turns, verifier `4/4`, managed arms `2/2` live
    `PreToolUse allow → PostToolUse observe`, input snapshots unchanged `4/4`. **Zero promoted to the
    long-term ledger** — `provider_native_skill_invocation_evidence_incomplete`, because Codex JSONL does
    not prove provider-native skill invocation. This is the direct cause of the next session\'s P0.

## Honest state at migration

- Evolution ledger: promotions `3/3`, regressions `0/144`, rollbacks `0`.
- `G/S` = **null**. Verified direct savings are 0, so no ratio is claimed. This was treated as the most
  valuable line in the status report, not a defect — the ledger has a governance-spend slot and still
  refused a denominator-free claim.
- Full suite: `679` tests, all verified, but annotated as **not** `679/679` in a single sandbox process —
  16 failures were `socket.bind` permission errors under sandbox loopback restriction and were re-verified
  green in a loopback-permitted run.
- Self-assessment: **6.5/10 overall, informally TRL 4–5.** Research idea/differentiation 8.5, harness
  architecture 8, safety governance 8, skill lifecycle 7.5, harness self-evolution 6.5, experimental
  evidence 6.5, product usability 5, production reliability 4.
- Versus Hermes/OpenClaw: **design advantage yes, empirical advantage not yet.**

## Open blockers carried into Merlin

- **Provider-backed multi-round AEGIS is blocked** by tenant security policy, which refused to send the
  new three-action project data outward. This was not bypassed. It is simultaneously a blocker and a
  demonstration that fail-closed works. The recommended resolution is to run it through the high-risk
  approval lane rather than around it — which would also make it a second instance of "verifier update is
  just another governance case."
- **No provider-native invocation proof.** Codex JSONL cannot establish that a specific skill body was
  loaded and invoked. This is why `harness-signed skill-body invocation event` became P0 in the successor
  thread.
- **Library-scale full run is environment-blocked**, not design-blocked: the npm `codex` install is missing
  its native binary, Docker is absent, and the strict-MCP capability gate is closed. The frozen plan is
  `87 tasks × 209 skills`; trial-1 is `435 cells`, of which ordinal `1–250` is exactly the first 50 tasks
  × 5 arms, and the full three-trial design is `1,305 cells`. The earlier 75-cell attempt failed entirely
  on executor diagnostics, so restarting at scale before fixing the executor would only burn quota.
- All current evidence is deterministic and model-free. **The governance machine is proven; the thesis —
  that the harness compounds skill quality through experience — has not yet been tested on live data.**
  Closing that gap is what the personal-workload-50 protocol exists for.
