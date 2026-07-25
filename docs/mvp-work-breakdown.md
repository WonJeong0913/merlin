> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/mvp-work-breakdown.md`

---

# MVP Work Breakdown

Created: 2026-07-07

The KING should be built divide-and-conquer. The current rule is:

```text
knowledge source: PDFs + Obsidian synthesis
implementation source: small tested modules under src/the_king
```

Reference systems are summarized in `docs/competitor-structure-reference.md`. They are decomposition references, not product templates.

Benchmark choice and task taxonomy are summarized in `docs/benchmark-selection.md`.

## Priority Reset: 2026-07-13

The implementation order now follows an 80:20 research allocation:

```text
80%: lifecycle, provisioning, selection, monitoring, and harness evolution
20%: one fixed minimal contract-conformant skill supplier
```

This does not remove self-generation. It prevents generator optimization from
becoming the main project. After C0/C1 calibration, use one reproducible basic
`SKILL.md`-centered candidate supplier, freeze its skill snapshot, and spend the
main experiment budget comparing management policies over that same snapshot.
Repair belongs to the lifecycle track, not to skill-prose optimization.

Required management policies:

- naive expanded exposure,
- fixed top-k provisioning,
- Hermes-Curator-inspired usage/recency lifecycle,
- The KING outcome/shadowing lifecycle,
- The KING lifecycle plus gated processor/policy evolution.

## Phase 0: Structural Skeleton

Status: started.

Purpose: create the interfaces that let later agentic pieces plug in safely.

Implemented modules:

| Module | File | Role |
|---|---|---|
| Core models | `src/the_king/models.py` | Skill, task, verifier, trace, lifecycle, policy, behavior-delta dataclasses. |
| Executors | `src/the_king/executors.py` | Pluggable task-attempt contract with no-skill, recipe-skill, API stub, and active account-auth CLI executors. |
| Skill library | `src/the_king/library.py` | File-backed JSON skill store. |
| Metrics | `src/the_king/metrics.py` | Normalized gain, shadowing metrics, SkillRevise-style selection, Self-Harness gate. |
| Task verifier | `src/the_king/tasks.py` | Deterministic exact/file/command verifiers. |
| Trace store | `src/the_king/traces.py` | File-backed run trace storage. |
| Harness runtime | `src/the_king/harness.py` | HarnessX-inspired hooks, processors, event audit trail, variant manifests, and evolution gates. |
| Provisioning | `src/the_king/provisioning.py` | First lexical top-k task-conditioned provisioner. |
| Lifecycle | `src/the_king/lifecycle.py` | AIP-lite structure gate, target/regression gate, status transition. |
| CTA-lite | `src/the_king/cta_lite.py` | Trace behavior delta and initial warning labels. |

Verification:

```text
python3 -m unittest discover -s tests
```

## Phase 1: Task And Verifier Corpus

Status: started.

Goal: define the first 10-20 deterministic synthetic smoke tasks.

Need:

- `TaskSpec` JSON/YAML format.
- workspace setup convention.
- verifier command convention.
- SkillsBench-style taxonomy fields: domain, capability, difficulty.
- The KING taxonomy fields: skill_dependency, shadowing_role, mvp_tier.
- oracle skill ids for tasks where we seed skills.
- regression group labels.

Do not use broad benchmarks for the harness smoke test. Start with small
file/data/CLI tasks that can be inspected. This is separate from the paper-level
SkillsBench plan, which targets all 87 public tasks.

Current seed tasks:

- `experiments/mvp/tasks/answer-yes.json`
- `experiments/mvp/tasks/count-errors.json`
- `experiments/mvp/tasks/count-items.json`
- `experiments/mvp/tasks/count-records.json`
- `experiments/mvp/tasks/create-audit-log.json`
- `experiments/mvp/tasks/create-notes-file.json`
- `experiments/mvp/tasks/create-output-json.json`
- `experiments/mvp/tasks/create-report-md.json`
- `experiments/mvp/tasks/create-result-file.json`
- `experiments/mvp/tasks/summarize-lines.json`

## Phase 2: Baseline Runner

Status: started.

Goal: run tasks and log traces without generated skills.

Need:

- no-skill condition runner.
- run id and library snapshot id.
- event schema for READ, WRITE, TOOL, VALIDATION, THINK-like events.
- cost and latency fields.

This creates the baseline traces required by CTA-lite and later skill generation.

Current runner:

```text
python3 -m src.the_king.runner \
  --tasks experiments/mvp/tasks \
  --workspaces experiments/mvp/workspaces/no_skill \
  --traces experiments/mvp/runs/no_skill
```

Current implementation:

- `TaskExecutor` contract: `ExecutionRequest -> ExecutionResult`.
- `NoSkillExecutor`: explicit no-skill baseline executor.
- `RecipeSkillExecutor`: deterministic smoke executor for seeded AIP-lite recipes.
- `ApiModelExecutor`: E2+ interface stub only; provider calls and Docker/task-environment handling still need implementation.

## Phase 3: Seeded Skill Flow

Status: implemented 2026-07-08.

Implemented:

- seed oracle skills as AIP-lite JSON: `experiments/mvp/skills/`.
- controlled shadowing distractors as AIP-lite JSON: `experiments/mvp/distractors/`.
- deterministic seeded runner: `run_seeded_condition` in `src/the_king/runner.py` (hooked task start -> provision -> select -> recipe execution -> verify -> trace).
- executor injection for deterministic/API replacement without changing the harness path.
- HarnessX-inspired processor path: `SkillStateProcessor`, `ExposureBudgetProcessor`, `DoNotUseConstraintProcessor`, `ShadowingMonitorProcessor`, `ShadowingLifecycleProcessor`.
- Harness evolution scaffold: `HarnessVariantSpec`, `HarnessEvolutionProposal`, `HarnessEvolutionResult`, `snapshot_harness_variant`, `build_runtime_from_variant`, `evaluate_harness_evolution`.
- greedy selector with stopword-filtered lexical scoring: `select_best_skill` in `src/the_king/provisioning.py`.
- SkillsBench distractor pool: `src/the_king/skillsbench_adapter.py` over `experiments/skillsbench/` (195 indexed curated skill names / 209 content variants, vendored).
- library-scaling experiment: `experiments/mvp/run_library_scaling.py` -> `experiments/mvp/results/library_scaling.json`.

First measured result (3 tasks, exposure budget 3): pi_o stays 1.0 up to library size 212, but the control task starts spuriously invoking `sb/ac-branch-pi-model` from library size 12 (spurious rate 1.0 over no-oracle tasks). Shadowing on oracle tasks did not appear in the 3-task smoke corpus.

Second measured result (10 tasks, exposure budget 3): oracle-only library keeps `pi_o=1.00`, `pi_m=0.00`, pass rate `0.90`. Adding the 2 controlled distractors collapses selection to `pi_o=0.11`, `pi_m=0.89`, pass rate `0.10`. This gives the first reproducible MVP shadowing signal.

Goal: test the harness before automatic skill generation.

Need:

- manually seed AIP-lite skill artifacts.
- validate structure with `validate_aip_lite_skill`.
- provision active skills with `LexicalProvisioner`.
- compare no-skill vs with-skill traces.
- compute clean oracle invocation and shadowing rates.

This proves the harness can govern skills before asking an LLM to write them.

## Phase 4: Skill Candidate Generation

Goal: add one fixed, reproducible candidate-supply path from failures. This is
a supporting 20% component, not an open-ended generator-improvement track.

Need:

- failure summary prompt.
- candidate skill prompt.
- AIP-lite contract builder.
- leakage checks.
- provenance trace attachment.

Keep generation behind gates. A generated skill starts as `candidate`, never `active`.

Freeze the accepted and rejected candidate snapshot before comparing management
policies. Do not regenerate different skill content per management arm.

## Phase 5: SkillRevise-Lite

Goal: revise only weak skills diagnosed as skill-local failures. Do not use
revision to repair route-local failures that belong to provisioning or
selection policy.

Need:

```text
trace + verifier feedback
-> D_i=(V_i,A_i,K_i)
-> candidate repair
-> re-execute
-> first verifier-passing selection
```

Do not deploy the newest revision by default.

## Phase 6: Lifecycle And Harness Policy

Goal: deliver the primary research treatment: activate, hide, repair, retire,
or change policy based on task-outcome, invocation, shadowing, and regression
evidence.

Need:

- repeated shadowing threshold.
- high-cost no-gain threshold.
- retirement rule.
- hook-level processors for `before_provision`, `before_select`, `after_select`, `after_verify`, and `policy_review`.
- apply `ShadowingLifecycleProcessor` decisions to the actual skill library with `apply_lifecycle_decision`.
- Self-Harness-style held-in/held-out gate for provisioning policy changes.
- Hermes-Curator-inspired usage/recency policy as a matched management baseline.
- explicit skill-local versus route-local diagnosis before action.

Editable policy surfaces stay narrow:

- exposure budget
- lexical/embedding retrieval weights
- selector instruction
- lifecycle threshold
- validation threshold

## Phase 7: Harness Co-Evolution

Goal: deliver the strongest primary contribution by making the harness itself
evolve, not only the skill library.

Need:

- candidate harness variants that can change processor composition and policy values,
- reconstructable processor manifests that include each processor's config,
- isolated evaluation for each candidate variant,
- held-in and held-out promotion gate,
- rollback to the parent harness when held-out regresses,
- later automatic processor generation or repair behind the same gate.

This is the staged path toward full HarnessX-style co-evolution. The first
version evolves narrow processor manifests and policy surfaces; later versions
can expand to code-level processor generation and cross-harness evaluation.
