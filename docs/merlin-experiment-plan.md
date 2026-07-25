# Merlin Experiment Plan

Created: 2026-07-08

## Purpose

This plan defines the first defensible experiment sequence for Merlin.

The goal is not to reproduce every model or harness setting from the SkillsBench
paper. For SkillsBench-based claims, however, Merlin should use the full
public 87-task corpus rather than a cherry-picked 10-20 task subset. The goal is
to borrow the strongest paired-evaluation structure and test Merlin's claim:

```text
self-generated skill failure is not only a skill-content problem;
it is a skill-harness management problem.
```

## 2026-07-13 Research Priority Correction

The main paper treatment is now harness governance, not generator quality:

```text
80%: fixed-library management, shadowing control, lifecycle, and harness evolution
20%: one fixed minimal contract-conformant skill-supply protocol
```

This is a claim, experiment-budget, implementation-order, and paper-space rule;
it is not a literal source-line ratio. The full rationale and Hermes v0.18
novelty audit are in `docs/research-direction-2026-07-13.md`.

The causal unit changes accordingly. After candidate generation, freeze one
skill-library snapshot and reuse it in every management arm:

```text
same model + same tasks + same generated skills + same verifier + same budget
naive exposure
vs usage/recency management
vs Merlin evidence-driven management
vs Merlin gated harness evolution
```

The existing C0/C1 full-87 run remains a required calibration and curated-skill
anchor. The priority correction begins after C0/C1; it does not alter or pool
the frozen run.

Before restarting C0/C1 on a successor host, validate SkillsBench itself using
the exact upstream repository and its original harness. This gate precedes The
KING formulas, custom runner work, and any new model inference. The benchmark
must first demonstrate coherent source, task, oracle, verifier, score, failure,
and denominator contracts in model-free tests.

## Critical Corrections

### Correction 1: benchmark role

SkillsBench is not automatically the final best benchmark for Merlin. It is
the best starting format because it provides:

- task instructions,
- deterministic verifiers,
- expert-curated skill bundles,
- no-skill vs with-skill comparison structure.

Merlin should use the full public SkillsBench task set first, then expand if
the paper needs broader task diversity or stronger agentic interaction. Smaller
task sets are allowed only for executor debugging, not for paper-level claims.

### Correction 2: corpus identity

There are three separate corpora:

| Corpus | Path | Role |
|---|---|---|
| SkillsBench exact corpus | `experiments/skillsbench/tasks/` | 87 public tasks, task-local curated skill bundles, oracle/verifier files. |
| SkillsBench dedup skill pool | `experiments/skillsbench/skills/` | 209 indexed Skill variants with `SKILL.md` for retrieval/shadowing/library experiments; `licenses/` is a preserved helper, not a Skill. |
| Merlin MVP synthetic corpus | `experiments/mvp/tasks/` | Small deterministic harness-debug corpus; not a paper-level benchmark claim. |

Verified SkillsBench mirror:

```text
commit=5433cf15c343f0da5fb942b80dc7dcb7c76506df
tasks=87
task.md=87
per-task SKILL.md=232
dedup skill variants with SKILL.md=209
unique skill names=195
```

### Correction 3: curated skill is not always oracle skill

SkillsBench provides expert-curated task skill bundles. For More Skills-style
shadowing experiments, the oracle set must be empirical:

```text
S*(q) = {s in S | p(q,{s}) - p(q,emptyset) >= tau}
```

So Merlin should use two labels:

- curated skill: provided by the benchmark task bundle.
- restricted empirical oracle skill: passes the isolated uplift threshold for
  that task inside a predeclared candidate pool.

Do not collapse these labels in final claims.

For the main experiment, Merlin estimates a restricted empirical oracle set
per task. This is a methodological control over the candidate skill pool, not a
reduction of the 87-task benchmark:

```text
S*_restricted(q)
= {s in C(q) | p(q,{s}) - p(q,emptyset) >= tau}
```

Where `C(q)` is restricted to:

- the task's curated skill bundle,
- generated skills for that task family,
- retrieval top-k candidates,
- a small predeclared distractor set.

Main cap:

```text
|C(q)| <= 10
base repeats per (q,s) = 3
max repeats per (q,s) = 10, allocated adaptively near tau
tau in {0.1, 0.2, 0.3} only after repeat resolution can distinguish them
```

An exhaustive all-209-skill sweep across all 87 tasks can be added as an
appendix or robustness run if the runner cost is acceptable, but the main claim
should still keep the task corpus at 87.

### Correction 4: growth is harness growth, not model-weight growth

In the MVP, Merlin does not train model weights. Growth means:

- generated skills are added,
- skills change lifecycle state,
- provisioning/selection policy changes,
- shadowing thresholds and exposure budgets update through gates.

Use "harness growth" or "skill-harness growth", not "model growth", unless a
later version actually updates model weights.

### Correction 5: one or two models are enough

The paper does not need 18 model-harness configurations. For Merlin's first
claim, one stable account-auth CLI model (`B_cli`) is enough; two models are
better for robustness. API-key execution is not the active experiment backend.
Every condition must use the same model, task set, tools, verifier, and budget.

### Correction 6: final claims need held-out power

The 10-20 task MVP or smoke subset is enough for engineering decisions, but not
enough for a SkillsBench claim. SkillsBench headline claims should run on all 87
public tasks, with any failed environment setup reported as an infrastructure
failure or pre-registered exclusion rather than silently removed. Headline
claims require:

```text
held-out metrics only
full 87-task SkillsBench evaluation where applicable
n_heldout_tasks * n_seeds >= 100 per condition
paired bootstrap confidence interval excludes 0
```

The adaptation set may guide skill generation and policy updates, but it must
not be used for the headline PL/SRR/OSR claim. When using SkillsBench, split
adaptation/held-out/regression over the 87 tasks before skill generation or
harness updates.

## Experimental Axes

### Axis A: skill source

```text
A0: no skill
A1: curated/expert skill bundle
A2: generated skill
A3: generated skill after revision
```

Axis A is a supporting 20% supply axis. Use one pre-registered
Create-Skill-style supplier and require the same basic skill contract. Do not
tune the generator, skill prose, or revision prompt independently for each
management condition. Any repair is evaluated as a harness lifecycle action.

### Axis B: library state over time

```text
t0: initial harness, no generated skill memory
t1: after adaptation tasks create generated skills
t2: after lifecycle/provisioning/harness policy updates
```

For t-axis growth claims, hold the task set, model, tools, verifier, budget,
and split fixed. `t1` changes the library content by adding generated skills.
`t2` changes harness management state through validation, lifecycle,
provisioning, and gated policy or processor variants. Headline t2 growth must
be evaluated on held-out tasks, not on the traces used to create or revise the
skills.

### Axis C: library exposure

```text
L0: no exposed skill
L1: curated/oracle-only library
L2: expanded naive library
L3: Merlin managed library
```

### Axis D: evaluation split

```text
adaptation set: traces used to create or revise skills
held-out set: tasks not used for skill generation or policy update
regression set: tasks passed by the t0 harness that must not degrade
```

Current pre-registered SkillsBench split:

```text
experiments/skillsbench/split-manifest.json
adaptation=35
held_out=30
regression=22
held_out_min_seeds_for_100_trials=4
```

The regression split is a candidate pool. The final regression set is the
subset passed by the t0 harness.

Headline metrics must be computed on the held-out set. Adaptation-set metrics
are allowed for debugging but must be labeled as adaptation-only.

### Axis E: management policy

All policies below receive the same frozen generated-skill snapshot:

```text
M0: naive expanded exposure
M1: fixed top-k provisioning
M2-H: Hermes-Curator-inspired usage/recency lifecycle
M2-K: Merlin outcome/shadowing/regression lifecycle
M3-K: M2-K + held-out-gated processor/policy evolution
```

`M2-H` is a matched policy baseline, not automatically an exact Hermes system
result. It may use only usage, view, patch, and recency evidence. Because a
short benchmark does not naturally span 30/90 wall-clock days, its active-skill
budget and transition thresholds must be pre-registered on the adaptation set
and matched to `M2-K`; the held-out split cannot tune them. Label it
`Hermes-Curator-inspired policy reimplementation` unless the actual Hermes
runtime is executed under the same model, task, tool, verifier, and budget
contract.

## Model Backend Axis

The primary experimental conditions C0-C10 are about skill and harness
management, not about a single vendor credential path. Merlin will therefore
log the model execution backend as an explicit blocking factor:

```text
B_api: direct API-key backend
B_cli: user-owned account-auth CLI backend
B_connector: user-owned account/connector backend
```

Initial implementation can use any backend that supports non-interactive
prompt input, deterministic model/version logging, JSON output capture, trace
storage, and repeated execution. User-owned account backends such as Codex,
Claude Code, GLM, or similar CLIs are valid benchmark backends when they meet
that contract. Final tables must report backend name, auth mode, CLI/app
version when available, model label, and any usage/session limitations.

Claims about Merlin should be made within a backend first. Cross-backend
comparisons are secondary robustness checks, not substitutes for C0-C10
within-backend comparisons.

Initial account-auth comparison group:

```text
B_claude_sonnet5_high = Claude Sonnet 5 via Claude Code CLI, effort=high
B_claude_opus48_high  = Claude Opus 4.8 via Claude Code CLI, effort=high
B_claude_opus47_high  = Claude Opus 4.7 via Claude Code CLI, effort=high
B_codex_gpt55_high    = GPT-5.5 via Codex CLI, effort=high
```

The first executable backend table should run C0/C1 smoke and then the ready
SkillsBench subset separately for each backend/model condition above. The
comparison should not claim that one vendor is intrinsically better; it should
test whether Merlin's skill/harness deltas are stable across account-auth
execution backends and model families. Model effort is a fixed control and must
be reported because earlier related work often listed model names without
reasoning-effort settings.

Runtime adapter note: both Claude Code and Codex CLI are run with high effort.
Merlin records both the normalized `effort=high` and provider runtime effort
values in traces.

## Harness Mode Axis

SkillsBench can be executed through more than one harness mode. This must be
reported as a separate blocking factor, just like model backend:

```text
H_agentic_workspace = account-auth CLI agent runs freely in a host workspace,
                      then artifacts are copied into Docker for verification.
H_paper_cli_mcp_v1  = account-auth CLI approximation with a byte-identical
                      task.md body user message, complete C1 bundle at
                      provider-native project paths, a fixed MCP-bound task
                      container, pre-task MCP readiness barrier, zero verifier
                      feedback, and repeated paired trials.
H_scripted_solver   = model generates a bounded solve script, the script runs
                      inside the task Docker container, then verifier/test.sh
                      scores the result.
H_agentic_subset    = later robustness-only agentic run on a small fixed subset.
```

`H_scripted_solver` is not a paper-faithful SkillsBench C0/C1 executor: it uses
bounded inspection and size-limited prompt injection rather than native full
bundle discovery and free container interaction. It is engineering evidence
only. `H_paper_cli_mcp_v1` is the current E2 target mode; any residual
deviations from the paper must be listed explicitly. Merlin claims compare
C0-C10 only within the same `backend x model x effort x harness_mode` cell.

`H_scripted_solver` may be used to debug response contracts, Docker execution,
and verifier logging, but must not be promoted to the headline E2 denominator.
Its `skill_used` field is model self-report, not invocation evidence.

Scripted solver protocol:

```text
0. record wall time and account usage for every run
1. build task Docker image
2. run bounded automatic inspection inside the container
   - ls/find tree under /root
   - file/type inventory
   - small text heads
   - schema dumps for csv/json/xlsx/sqlite/pdf metadata where safe
3. prompt model with task.md body only + C0/C1 skill context + inspection report
4. require exactly one solve artifact, usually /root/solve.sh or /root/solution.py
5. copy/generated script into the container and execute it
6. run upstream verifier/test.sh
7. use zero verifier-feedback repair for evaluation; any verifier-feedback
   retry is a separate `H_scripted_repair` adaptation-only condition
```

Timeouts are independent and must be logged separately:

```text
generation_timeout_sec: CLI model response timeout
script_execution_timeout_sec: solve.sh / solution.py execution timeout,
                              defaulting to task frontmatter agent.timeout_sec
verifier_timeout_sec: verifier frontmatter verifier.timeout_sec
build_timeout_sec: Docker build timeout
inspection_timeout_sec: bounded container inspection timeout
```

Do not delete the agentic mode. Close E2 under `H_paper_cli_mcp_v1`; retain
scripted results as a separately labeled engineering appendix. Never pool
scripted and agentic trajectories.

## Main Conditions

### Stage 1: bounded skill-supply calibration (20%)

Purpose: show whether a fixed generation protocol creates candidate skills with
enough utility to exercise the management loop. This stage is not the main
novelty claim.

```text
C0: no-skill
C1: curated skill bundle
C2: generated skill from no-skill failure traces
C3: generated skill + validation/regression gate
```

Primary comparison:

```text
no-skill vs curated skill vs generated skill
```

This answers:

- Does a skill help at all?
- Does a generated skill beat no-skill?
- How close is generated skill performance to curated skill performance?

After C3, freeze a content-addressed skill snapshot. C5-C10 and every management
baseline must consume this same snapshot. Regeneration per arm is prohibited.

### Stage 2: shadowing under library expansion

Purpose: show whether larger libraries harm selection.

```text
C4: empirical oracle-only library
C5: expanded naive library
C6: expanded library with controlled distractors
```

Definition:

```text
C4 = per-task S*_restricted(q) exposure only.
C5 = curated skills + generated(t1) skills + natural distractors from the
     expanded SkillsBench pool, exposed naively.
C6 = C5 + predeclared controlled distractors.
```

This follows the More Skills oracle-only condition. Do not replace C4 with the
union of all held-out oracle skills, because `union_q S*(q)` can reintroduce
distractors from other tasks.

The expanded library must include the same `generated(t1)` skills used in the
Merlin-managed conditions. Otherwise the experiment only shows generic
distractor management, not whether harness management turns accumulated
self-generated skills from harmful or neutral artifacts into useful ones.

Primary comparison:

```text
oracle-only vs expanded naive library
```

This answers:

- Does clean oracle-only invocation collapse?
- Does `pi_m` rise?
- Does pass rate drop even when useful skills exist?

### Stage 3: management and harness intervention (80%)

Purpose: show that harness management recovers selection and performance.

```text
C7: expanded naive library
C8: Merlin managed provisioning
C8-H: Hermes-Curator-inspired usage/recency lifecycle baseline
C9: Merlin managed provisioning + lifecycle actions
C10: Merlin managed provisioning + lifecycle + gated policy update
```

Condition boundaries:

| Condition | Intervention surface | Fixed versus changing |
|---|---|---|
| C8 | managed provisioning only | fixed top-k, fixed exposure budget, fixed selector threshold |
| C8-H | usage/recency lifecycle baseline | same active-library budget as C9; no task-outcome, shadowing, or regression evidence in lifecycle decisions |
| C9 | C8 + lifecycle actions | hide, repair, retire, or merge skills based on observed evidence |
| C10 | C9 + gated policy updates | threshold, exposure budget, and routing-rule changes only after held-in and held-out gates |

Possible interventions:

- hide repeated distractor skills,
- reduce exposure budget,
- improve selector threshold,
- add do-not-use constraints,
- route through empirical oracle score,
- send weak skills to repair,
- retire duplicate or harmful variants.

Primary comparison:

```text
expanded naive library vs Merlin managed library
```

Strongest competitor-aware comparison:

```text
C8-H usage/recency management
vs
C9/C10 outcome + shadowing + regression management
```

Headline cross-term comparison:

```text
naive library + generated(t1) skills
vs
Merlin managed library + the same generated(t1) skills
```

This is the direct test of the thesis. If the same generated skills are harmful
or neutral under naive accumulation but useful under Merlin management, the
failure mode is a skill-harness management problem rather than only a
skill-content problem.

This answers:

- Does `pi_m` decrease?
- Does `pi_o` recover?
- Does pass rate improve?
- Does held-out performance avoid regression?

## Required Comparisons

| Comparison | Purpose | Split rule |
|---|---|---|
| C0 vs C1 | Curated skill upper anchor against no-skill. | Held-out for claim; adaptation allowed for debugging only. |
| C0 vs C2/C3 | Generated skill gain over no-skill. | Held-out for claim. |
| C2/C3 vs C1 | How close generated skills get to curated skills. | Held-out for claim. |
| generated v0 vs revised v1 | Whether SkillRevise-style repair improves skill quality. | Compare on held-out same-family tasks not used for revision. |
| C4 vs C5/C6 | Whether expanded libraries create oracle-selection collapse and shadowing. | Held-out for claim. |
| C7 vs C8 | Managed provisioning effect. | Held-out for claim. |
| C8-H vs C9 | Usage/recency lifecycle versus Merlin evidence-conditioned lifecycle under a matched active-library budget. | Main novelty comparison; held-out only. |
| C8 vs C9 | Lifecycle action effect. | Held-out for claim. |
| C9 vs C10 | Gated harness evolution effect. | Held-out for claim. |
| C5/C7 + generated(t1) vs C8/C9/C10 + same generated(t1) | Cross-term: same generated skills under naive accumulation versus Merlin management. | Main headline comparison; held-out only. |

Do not report `v0 -> v1` repair gains on the same task traces used to select or
revise `v1` as a headline result. Those are adaptation diagnostics and must be
labeled as such.

## Metrics

### Pass rate

```text
p_c(E) = mean task success under condition c on evaluation set E
```

### Normalized skill gain

```text
G_skill(c,E) = (p_c(E) - p_no_skill(E)) / (1 - p_no_skill(E))
```

If `p_no_skill(E)=1`, use raw delta:

```text
G_skill(c,E) = p_c(E) - p_no_skill(E)
```

This keeps regressions visible when the baseline is saturated.

### Generated skill gain

```text
G_gen(t,E) = (p_gen_t(E) - p_no_skill(E)) / (1 - p_no_skill(E))
```

### Merlin management gain

```text
G_king(t,E) = (p_king_t(E) - p_naive_t(E)) / (1 - p_naive_t(E))
```

If the denominator saturates, use raw delta.

### Clean oracle selection

```text
pi_o = Pr(emptyset != I subseteq S*_restricted(q) | S*_restricted(q) != emptyset)
```

### Shadowing rate

```text
pi_m = Pr(I contains at least one non-oracle skill | S*_restricted(q) != emptyset)
```

With the mutually exclusive route-event split:

```text
pi_m = pi_wrong + pi_mixed
```

Where:

- `pi_wrong`: only non-oracle skills selected.
- `pi_mixed`: at least one oracle skill and at least one non-oracle skill selected.

### Shadowing reduction rate

```text
SRR = (pi_m_naive - pi_m_king) / pi_m_naive
```

Only report SRR when `pi_m_naive > 0`.

### Oracle selection recovery

```text
OSR = pi_o_king - pi_o_naive
```

### Pass-rate lift

```text
PL = p_king - p_naive
```

### Cost-no-gain rate

```text
cost_no_gain
= Pr(cost_ratio >= 1.5 and success_delta <= 0)
```

This is the rate of CTA-lite `cost_increase_without_gain` labels. It is already
normalized to `[0,1]`; the `1.5` threshold is a policy surface and must be
predeclared before evaluation.

### Route risk

Merlin should separate skill-local risk from route/harness risk. The first
MVP route-risk score should use mutually exclusive oracle-task events:

```text
R_route(t)
= a*pi_wrong(t)
+ b*pi_mixed(t)
+ c*pi_empty(t)
+ d*spurious(t)
+ e*cost_no_gain(t)
```

Where:

- weights are non-negative and sum to 1,
- all terms are normalized to `[0,1]`,
- `pi_wrong` is non-empty invocation with no oracle skill selected, measured on
  tasks where `S*_restricted(q) != emptyset`,
- `pi_mixed` is oracle-plus-distractor invocation, measured on tasks where
  `S*_restricted(q) != emptyset`,
- `pi_empty` is no-skill fallback when `S*_restricted(q) != emptyset`,
- `spurious` is skill invocation when `S*_restricted(q) == emptyset`; it has a
  different denominator and must be reported separately beside the route-risk
  score.

Interpretation:

- high `R_skill`: repair/hide/retire the skill.
- high `R_route`: change provisioning/selector/exposure policy.

Do not include `Avg R_skill` inside `R_route`. Skill-local risk and route risk
must stay separable so lifecycle actions and routing-policy actions remain
diagnosable.

### Harness growth index

For a compact growth summary:

```text
HGI_t
= alpha * (p_king_t - p_naive_t)
+ beta  * (pi_o_king_t - pi_o_naive_t)
+ gamma * (pi_m_naive_t - pi_m_king_t)
- delta * max(0, cost_king_t - cost_naive_t)
- eta   * regression_t
```

`HGI_t` is a dashboard summary only. It must not be the main paper claim.
Weights must be predeclared; the MVP default is equal weights after normalizing
each component to `[0,1]`.

The paper claim should use the individual components:

```text
PL, SRR, OSR, held-out regression
```

## Meaningful Harness Growth Criteria

The first provisional threshold:

```text
Pass Rate Lift >= +5 percentage points
Shadowing Reduction Rate >= 20%
Oracle Selection Recovery >= +10 percentage points
held-out regression <= 0 percentage points
cost increase is bounded or explicitly justified
```

For MVP debugging, these are engineering thresholds, not final statistical
claims. A final claim requires:

```text
n_heldout_tasks * n_seeds >= 100 per condition
paired bootstrap CI excludes 0 for the headline metric
headline metrics computed on held-out tasks
```

The trial-count rule uses the held-out split, not the full 87-task corpus. For
example, if the held-out split has 30 tasks, use at least 4 seeds/repeats per
condition (`30*4=120`). A single-seed 87-task run is still useful as a coverage
run, but it is not a powered held-out claim.

## Proposed Execution Plan

### Phase E0: corpus verification

Status: done for local mirror.

Run:

```bash
python3 experiments/skillsbench/verify_corpus.py
```

Purpose:

- verify 87 task mirror,
- verify task names match index,
- verify dedup skill pool count.

### Phase E1: full 87-task readiness audit

Status: executable full-denominator readiness contract frozen. Static and raw
artifacts are preserved; the current execution artifact reports `81/87`
strict pass, `84/87` execution-ready, and `87/87` included in the next model
run.

Audit all 87 SkillsBench tasks and classify runner readiness before any
paper-level run. The target is 87/87 coverage, not task selection.

Readiness criteria:

- deterministic verifier works,
- task runtime is manageable,
- curated skill bundle exists,
- no extreme external service dependency,
- Docker-only service dependencies are either supported by the local runner or
  marked as explicit infrastructure gaps to repair,
- at least some tasks share near-overlapping skill descriptions.
- each task receives `shadowing_role` metadata where possible: control,
  oracle_target, distractor_candidate, regression_probe.

Output:

```text
experiments/skillsbench/readiness-87.json
experiments/skillsbench/split-manifest.json
```

Current static audit result:

```text
task_count=87
status_counts={'needs_infrastructure_review': 87}
infrastructure_flag_counts={'docker': 87, 'node': 2, 'workspace_seed': 6}
```

A smaller 10-20 task slice may still be created for CLI/executor smoke tests,
but it must be labeled `smoke_only` and excluded from final claims.

For a new DESKTOP restart, E1 begins with the upstream harness rather than a
new Merlin harness:

1. inspect the pinned upstream README, packaging, CLI entry points, tests, and
   condition definitions without modifying the clone;
2. run upstream unit tests and a model-free 87-task corpus contract check;
3. verify fractional rewards, oracle/verifier agreement, pytest/reward
   consistency, timeout/failure classification, and denominator behavior;
4. perform bounded representative Docker/oracle/verifier self-checks, followed
   by an explicit 87-task readiness audit;
5. only then run a one-task C0/C1 account-auth smoke under a new empty run ID.

If account-auth Claude CLI is not supported by the upstream harness, document
the exact minimal backend-adapter seam and its deviation before implementing
it. Do not silently replace the original harness with a custom runner.

DESKTOP model-free validation result (`20260716-upstream-structure-validation-v1`):

```text
locked environment: PASS (uv 0.11.29, Python 3.12.3, BenchFlow 0.6.3)
upstream CI:         3/3 PASS
bench tasks check:  87/87 PASS
source integrity:   PASS; exact clean tree retained
c0_definition:      WARN; absent external mirror, upstream generators present
simpo_gitlink:       BLOCKED; gitlink has no .gitmodules mapping
```

Phase 3A resolution audit (`20260716-c0-simpo-resolution-audit-v1`):

```text
c0_definition:      BLOCKED; three upstream generators are not equivalent
c0_mirror:          NOT_CREATED
c0 pairwise/checks: 87/87 explicit NOT_RUN rows in each ledger
simpo_provenance:   PASS; huggingface/alignment-handbook@ae3f44fc...
simpo materialize:  NOT_RUN
source integrity:   PASS; exact clean tree retained
handoff integrity:  25 files, checksum verified
```

The next step is a read-only source-authority audit across the upstream paper,
official documentation, repository history, and original runner/config call
sites. It must determine whether one C0 transformation is canonical. If no
single source is authoritative, the baseline must pre-register the least
deviating C0 condition as an explicit replication deviation rather than calling
it the original contract. Bounded Docker/oracle verification remains gated;
passing metadata checks alone is not evidence that task images and verifiers
work.

Phase 3B source-authority result
(`20260716-c0-source-authority-audit-v1`):

```text
paper snapshot C0: 86 task trees; Dockerfiles differ; skill sources retained
later generators:  skill sources removed plus provider-specific transforms
native C0 meaning: same task directory, omit runtime skill injection
canonical generator: NOT_PROVEN
decision:            BLOCKED (high evidence strength)
Docker/smoke gate:   CLOSED
source integrity:    PASS; 5433cf15... / 740e4169...69d6ab
```

The distinction is now explicit: the paper fixes the conceptual no-skill arm,
the historical snapshot partially materializes it, and current native
documentation defines a runtime injection contrast. These do not establish one
byte-level C0 generator for the current 87-task corpus. The next phase may only
prepare a replication-deviation decision packet comparing causal isolation,
paper fidelity, current-harness fidelity, contamination risk, and feasibility.
It may recommend a contract, but must not execute it before pre-registration.

Phase 3C recommendation
(`20260716-c0-replication-deviation-decision-v1`):

```text
A current-native: rejected as primary; radar-vital-signs exposes 7 skill COPYs
B paper extrapolation: underdetermined; historical/current overlap is 74 tasks
C strict isolation: recommended; explicit non-paper-identical deviation
next gate: approval required
```

If C is approved, C0 may differ from C1 only by removal of the task-local skill
artifacts and the Docker instructions that copy those exact artifacts into the
image. `task.md`, oracle, verifier, and every non-skill environment file must be
byte-identical. Provider-specific Docker changes are forbidden. The initial
gate is one `radar-vital-signs` pair only: static hash/diff audit, structure
check, one build per arm, one oracle per arm, and one verifier per arm. No model
or Claude call, account-auth adapter, full87 run, or frozen baseline manifest is
part of that gate.

Approval status: the user approved C on 2026-07-16. The bounded Phase 3D gate
uses run ID `20260716-radar-c0c1-model-free-gate-v1`. It freezes its manifest
before creating the derived C0 and stops before Docker if any difference beyond
the task-local skill directory and exact skill-copy instructions is observed.
This approval does not authorize a model smoke or expansion beyond
`radar-vital-signs`.

Phase 3D result (`20260716-radar-c0c1-model-free-gate-v1`):

```text
manifest:          96d876...c5eb (frozen before materialization)
static contract:   PASS; skills tree + 7 exact COPY instructions only
structure:         C0 PASS / C1 PASS
oracle/verifier:   C0 1.0 / C1 1.0
CTRF:              each arm 5 passed / 0 failed
runtime exposure:  C0 none / C1 task_bundled
source/checksums:  PASS
warning:           images removed on exit; cache/history not measured
model calls:       0
```

This opens consideration of a model-free 87-task readiness audit, not permission
to run it automatically. That expansion may require up to 174 sequential arm
self-checks and substantial network/build time. It must have a separately
frozen manifest, per-task failure ledger, disk/network guardrails, and explicit
user approval. Account-auth smoke and full87 remain later gates.

Phase 3E approval and contract (2026-07-17):

```text
run_id:             20260717-full87-c0c1-model-free-readiness-v1
tasks:              87, frozen sorted order
arms:               C0 then C1, at most one first attempt each
maximum rows:       174 including BLOCKED/unscored rows
worker:             1 sequential durable manager
C0 contract:        approved strict causal-isolation deviation
retry policy:       none
disk guardrail:     35GB
infra stop:         3 consecutive identical infrastructure failures
model/Claude calls: prohibited
```

Every C0 transform must pass a byte/hash/mode ledger before its Docker call.
The unresolved SimPO materialization remains an explicit blocker rather than a
silent exclusion. Completion of readiness still does not authorize the
account-auth smoke or full87 model experiment.

### Phase E2: 87-task paired no-skill vs curated-skill evaluation

Current execution status:

```text
headline_harness_mode = H_paper_cli_mcp_v1 (fixed three-task pilot complete;
                                            frozen 87-task x 3 evaluation running)
engineering_harness_mode = H_scripted_solver (working, non-headline)
```

The earlier nine-task scripted aggregate is one uncontrolled provider trial per
cell, not `seed=1`, and cannot be reported as a SkillsBench benchmark result.
Before E2 expansion, require task-body-only prompts, C0 skill-free images,
complete C1 curated-bundle exposure through the provider-native skill delivery
path, zero verifier feedback, task resource limits,
trial indices, provider/CLI versions, prompt and skill hashes, and at least three
paired trials per task-condition cell.

The frozen three-task pilot is now complete under the user's logged-in
Claude.ai account. Each task has three paired trials. Across nine valid pairs,
`C0=1/9`, `C1=8/9`, and mean paired delta is `+7/9` (`+0.7778`). The per-task
paired deltas are `+1/3` for `court-form-filling`, `+1.0` for
`weighted-gdp-calc`, and `+1.0` for `earthquake-plate-calculation`. Every C1
cell invoked the expected SkillsBench curated skill once (`pdf`, `xlsx`, or
`geospatial-analysis`) through the provider-native `Skill` interface, and C0
made no task-skill call. Court-form filling
included one negative pair (`[-1,+1,+1]`), so the result is variable pilot
evidence rather than an “always helps” claim. The aggregate is data-contract
complete but not paper eligible: temperature 0, the paper's 8K token/storage
caps, and the fixed 87-task macro denominator are not satisfied. Do not
generalize this three-task result to SkillsBench or Merlin's full loop.

Run all 87 SkillsBench tasks under:

```text
C0 no-skill
C1 curated skill bundle
```

If a task fails because the local environment is incomplete, treat it as a
readiness failure to repair or a pre-registered infrastructure exception. Do not
silently shrink the evaluation set.

This establishes:

- no-skill baseline,
- curated skill upper anchor,
- task difficulty,
- whether the local harness is faithful enough.

The one-task contract gate and fixed three-task pilot are complete. The next
denominator is now pre-registered as all 87 tasks, three paired trials per
task, C0/C1 (`261` pairs, `522` cells), with a `300 USD` provider-equivalent
usage guardrail, `35 GiB` free-disk floor, and three-pair infrastructure-failure
stop. All readiness exceptions remain in the denominator. The batch is running
sequentially on ONE historically, but the last preserved monitor state was
`47/261` pairs (`94/522` cells) before that host became unreachable. That v2
run must not be resumed or mixed on a successor host. `DESKTOP-8FLI4IL` is a
new execution workspace under setup; no source/raw transfer or new inference
may begin there until the same source snapshot is synchronized, tests and
corpus verification pass, and a new frozen-manifest audit is recorded. No
full-run metric may be reported until all cells finish and strict aggregation
passes. For every pilot and full run, record run-level wall time and account
usage; those values feed the E4 budget freeze and the practical "runs per day"
estimate.

The three-trial choice is a replication decision, not an upstream constant.
At the pinned commit, batch YAML uses five attempts while the OpenCode ablation
plans one task/arm job. Preserve `trials=3` only as a clearly pre-registered
variance-reduction choice for the distinct `B_cli` model-harness cell.

### Phase E3: 87-task generated-skill evaluation

For tasks where no-skill fails or underperforms:

```text
failure trace -> candidate skill -> AIP-lite artifact -> validation gate
```

Run:

```text
C2 generated skill
C3 generated skill + gate
```

This tests whether generated skills produce meaningful deltas.

E3 is deliberately bounded:

```text
one fixed generator prompt and model/backend contract
one candidate per eligible failure family before optional repair
fixed maximum revision budget
content-addressed accepted/rejected snapshot
no per-management-arm regeneration
```

The purpose is candidate supply and admission calibration, not a claim that The
KING is a superior skill writer.

### Phase E4: 87-task shadowing evaluation

Build per-task restricted empirical oracle sets:

```text
C(q) = curated bundle + generated skills + retrieval top-k + predeclared distractors
|C(q)| <= 10
run each candidate skill in C(q) in isolation
start with 3 repeats per (q,s), then allocate up to max_repeats near tau
S*_restricted(q) = skills with uplift >= tau
tau sensitivity: run only when the repeat grid can distinguish the thresholds
```

Budget upper bound for oracle estimation:

```text
87 tasks * <=10 candidate skills/task * max_repeats
```

With `max_repeats=10`, the actual worst-case cap is 8,700 isolated skill runs
plus matched no-skill estimates. `>=3` is a lower bound and must never be used
to claim a 2,610-run upper bound.

This is the dominant run-cost term. After the E1 readiness audit provides task
runtime estimates, freeze a run budget before executing E4. If the budget is
too high, use this pre-registered reduction order:

1. Keep all 87 tasks in the benchmark.
2. Reduce `|C(q)|` by lowering retrieval top-k, never by dropping tasks.
3. Use `repeats=3` only when `|C(q)| >= 2` or when the curated skill fails to
   clearly beat no-skill in a pilot run.
4. For tasks with exactly one curated candidate and stable verifier behavior,
   allow a labeled `curated_as_oracle_pilot` assumption; exclude those rows from
   tau-sensitivity claims unless isolated repeats are later completed.
5. Report the exact run budget, skipped isolated repeats, and reason codes.

When sweeping `tau`, report the denominator for each value:

```text
N_oracle(tau) = |{q : S*_restricted_tau(q) != emptyset}|
```

If `tau` removes all oracle skills for a task, that task leaves the oracle-task
population for `pi_o`, `pi_wrong`, `pi_mixed`, and `pi_empty`; no-oracle skill
use is reported through `spurious`.

Estimate the oracle set relative to each target library. If a controlled
candidate passes the uplift threshold, reclassify it as oracle for that task or
build separate C5/C6 oracle sets; otherwise `S*(q)` may not be a subset of the
library used in `Delta(q,S)`.

Then compare:

```text
C4 per-task S*_restricted(q) exposure only
C5 expanded naive library = curated + generated(t1) + natural distractors
C6 = C5 + controlled distractors
```

Measure:

- `pi_o`,
- `pi_m`,
- no-skill fallback,
- spurious invocation,
- pass-rate drop,
- exposure cost.

### Phase E5: 87-task Merlin intervention

Apply harness management:

```text
shadowing monitor -> lifecycle decision -> policy proposal -> held-in/held-out gate
```

Run:

```text
C7 expanded naive
C8 managed provisioning
C8-H Hermes-Curator-inspired usage/recency lifecycle
C9 managed provisioning + lifecycle
C10 managed + gated policy update
```

All C7-C10 conditions use the same generated(t1) skill set. The only intended
difference is harness management: provisioning, lifecycle actions, and gated
harness evolution.

`C8-H` receives the same skill set and active-library capacity as C9, but its
lifecycle decisions may use only usage/recency telemetry. C9 may use task
outcomes, route-event categories, shadowing, and regression evidence. This
isolates whether evidence quality, rather than merely pruning, creates the gain.

Pass condition:

- pass rate improves,
- shadowing decreases,
- oracle selection recovers,
- held-out tasks do not regress.

## Current MVP Evidence

The synthetic MVP corpus already proves that the metric pipeline can detect
shadowing:

```text
oracle-only: pi_o=1.00, pi_m=0.00, pass=0.90
controlled:  pi_o=0.11, pi_m=0.89, pass=0.10
```

This is not a paper-level benchmark result. It is a harness-debug result.

## Open Weak Points

### Weak point 1: deterministic MVP runner is not an LLM agent

Current seeded runner is useful for harness debugging but cannot prove real
agent behavior.

Repair:

- complete an account-auth CLI (`B_cli`) executor for the full 87-task corpus,
- keep deterministic verifiers,
- preserve identical conditions across arms.

### Weak point 2: controlled distractors are adversarial

They are good for first signal, but they overstate shadowing.

Repair:

- use natural SkillsBench same-name/different-body variants,
- use expanded libraries from the dedup pool,
- separately report controlled and natural distractor results.

### Weak point 3: curated bundle may not equal empirical oracle set

Repair:

- report curated skill results separately,
- estimate `S*_restricted(q)` through isolated skill evaluation over a capped
  candidate set,
- use empirical oracle only for `pi_o`, `pi_m`, and `Delta_shd`.

### Weak point 4: cost proxy is too rough

Current cost uses exposed description length.

Repair:

- for real account-auth CLI runs, log available prompt/completion usage, tool calls, CLI version, and wall time,
- keep description length only as a deterministic proxy for early tests.

### Weak point 5: growth can overfit adaptation tasks

Repair:

- split adaptation/held-out/regression sets before skill generation,
- accept harness updates only if held-out does not regress,
- report adaptation-only improvements separately.

### Weak point 6: final thresholds need enough trials

Small task subsets make percentage-point thresholds unstable and should not be
used for SkillsBench headline claims.

Repair:

- use MVP/smoke thresholds only as engineering gates,
- run SkillsBench headline claims on the full 87-task corpus,
- require `n_heldout_tasks * n_seeds >= 100` for final claims,
- use paired bootstrap confidence intervals for headline metrics.

## Immediate Next Build Tasks

1. Resolve the canonical C0 definition from upstream-authoritative paper,
   documentation, history, and call-site evidence. Do not select among the three
   non-equivalent materializers by convenience.
2. After C0 is resolved or an explicit replication deviation is pre-registered,
   continue the original-harness audit through bounded Docker/oracle/verifier
   and full-87 readiness gates. Do not implement Merlin formulas or a
   replacement harness during this phase.
3. After that audit, run one new account-auth C0/C1 smoke and decide whether a
   new empty 87-task, three-trial baseline is defensible. Preserve every failed
   or exceptional row and keep historical v1/v2 runs separate.
4. Audit actual native skill invocations, usage, wall time, verifier health,
   and exception classes before interpreting curated-skill lift.
5. Add one fixed generated-skill candidate path behind AIP-lite gates; freeze a
   content-addressed C3 skill snapshot and stop generator tuning.
6. Add restricted empirical oracle-set estimation and route-event evidence
   across the 87-task corpus.
7. Implement a common management-policy interface with `M0`, `M1`, and the
   matched `M2-H` usage/recency baseline.
7. Connect Merlin skill-local and route-local diagnoses to actual lifecycle
   and provisioning actions (`M2-K`).
8. Execute fixed-library C7/C8/C8-H/C9 ablations before adding policy evolution.
9. Execute reconstructable processor/policy variants through internal
   held-in/held-out evaluation and rollback (`M3-K` / C10).
10. Add powered held-out reporting with `n_heldout_tasks * n_seeds >= 100`.
