# SkillsBench staged protocol v1 — pre-registration

Date: 2026-07-25
Classification: protocol, pre-registration

Registered **before** any execution run. The point of writing it now is that
every parameter below is fixed while the outcome is still unknown; changing any
of them after seeing results converts this from a test into a fit.

## Why staged

The thesis is that the harness compounds skill quality through experience. A
single-timepoint measurement can neither support nor refute that. Three
horizons, each with its own falsification condition:

| Horizon | Question | Falsified if |
|---|---|---|
| **단기** — one round | Does provisioning help a single attempt at all? | managed ≤ baseline |
| **중단기** — R rounds on the same tasks | Does the management layer convert outcomes into change? | no lifecycle action fires, or actions fire without improving adaptation, or the regression set degrades |
| **중기** — held-out | Does the change *transfer*, or was it memorised? | adaptation improves while held-out does not |

Only the mid horizon can support the thesis. Short and short-mid can only refute
it or clear the way. An improvement that stops at 중단기 is overfitting to the
adaptation set, and the protocol is designed to say so out loud rather than
report it as success.

## Splits — reused, not re-drawn

`experiments/skillsbench/split-manifest.json`, seed `20260708`, commit
`5433cf15`, category-stratified:

| Split | n | Role |
|---|---|---|
| `adaptation` | 35 | the only tasks the harness may learn from |
| `held_out` | 30 | touched exactly once, at the end of 중기 |
| `regression` | 22 | seesaw guard, evaluated every round |

## Pre-flight: a stated confound

Deterministic routing recall@3 by split, measured 2026-07-25 before any
execution:

| Split | recall@3 |
|---|---|
| `adaptation` | 24/35 (68.6%) |
| `held_out` | 14/30 (46.7%) |
| `regression` | 14/22 (63.6%) |

**`held_out` is ~22 points harder to route than `adaptation`** (two-proportion
z ≈ 1.79, p ≈ 0.074 — underpowered, but the direction is large). The split was
stratified on category and difficulty, not on routing difficulty, so this is an
artifact of the seed.

The split is **kept as drawn**. Re-drawing it after seeing this number would be
exactly the post-hoc fitting this document exists to prevent.

Consequence for analysis, fixed now: 중기 compares **within-split before/after
deltas**, never adaptation's absolute score against held-out's. The baseline
difference cancels inside each split. Any cross-split statement must carry this
confound explicitly.

## Fixed parameters

Changing any of these after a run invalidates the run.

| Parameter | Value |
|---|---|
| Provider | Claude Code CLI, account auth |
| Model | `claude-sonnet-5` — the explicit ID, never the `sonnet` alias |
| Effort | `high` |
| Exposure budget `k` | 3 |
| Rounds `R` (중단기) | 3 |
| Arms | `baseline` (no skills provisioned) and `managed` (harness-provisioned top-k) |
| Arm order | per the existing schedule manifest; never re-ordered per task |
| Oracle | `curated_skill_variants` |
| Success | the task's own `verifier/test.sh`; never a locally authored check |
| Primary metric | task success rate, per split, per round |

## Stage detail

### 단기 — one round, library frozen

87 tasks × 2 arms. No lifecycle action may fire; the library is read-only for
the whole stage. Establishes whether provisioning helps before any adaptation
exists, and produces the baseline every later stage is measured against.

### 중단기 — R = 3 rounds on `adaptation`

Each round: run 35 adaptation tasks × 2 arms → feed verifier outcomes into the
lifecycle (repair / hide / promote / retire) under the existing gates → run the
22 regression tasks as the seesaw guard.

Reported per round: lifecycle action count by kind, adaptation success, and
regression success. A round that improves adaptation while degrading regression
is a failure, not a trade-off, and the seesaw gate should already refuse it —
if it does not, that is a finding about the gate.

### 중기 — `held_out`, once

30 held-out tasks × 2 arms, evaluated twice: against the initial library and
against the post-중단기 evolved library. This is the only comparison that can
support the thesis.

`held_out` is not read, provisioned against, or inspected before this stage.

## Reporting rules, fixed now

- A null result is reported. If held-out delta is zero, that is the finding.
- No metric is added after seeing results. Additional metrics may be recorded
  but not promoted to primary.
- Every run writes a durable record with the exact prompt sent per task, as
  `experiments/mvp/results/deterministic_selection_v1/` already does.
- Legacy evidence is not merged in; new claims need new Merlin-namespaced runs.

## Blocked, and by what

**All three stages need Docker: 87 of 87 tasks carry `infrastructure_flags:
["docker"]` and Docker is not installed on this machine.** The task corpus is
also not vendored here — only the 209-skill library is — so a clone of
`benchflow-ai/skillsbench @ 5433cf15` is required, pointed at via
`MERLIN_SKILLSBENCH_TASKS_ROOT`.

Runnable today without either: selection-only measurement, which is what the
pre-flight above is. It cannot substitute for any stage, because with no
executed outcome there is no verifier signal, so no lifecycle action, so nothing
to compound.

## Cost estimate

At `k=3`, R=3, both arms:

| Stage | Runs |
|---|---|
| 단기 | 87 × 2 = 174 |
| 중단기 | (35 × 2 + 22) × 3 = 276 |
| 중기 | 30 × 2 × 2 = 120 |
| **Total** | **≈ 570 task executions** |

Each is a Dockerised task with a provider turn. If that is too large for the
available quota, the pre-registered reduction is to restrict every stage to the
`easy` + `medium` tasks (59 of 87) — chosen now, not after seeing results.
