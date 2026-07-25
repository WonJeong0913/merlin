> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/library-scale-execution-ledger-v1.md`

---

# Full-87 library-scale execution ledger v1

## Purpose

The statistical library-scale stack already freezes nested libraries, validates
actual invocation evidence, computes `n/m/o` event curves and the More Skills
decomposition, and derives task-clustered paired bootstrap intervals. The
missing operational contract was safe restart across 1,305 or 1,566 expensive
model-backed cells.

This ledger closes that orchestration gap without claiming that the cells have
run.

## Frozen schedules

The base schedule is:

- 87 tasks
- 3 paired trials per task
- 5 nested arms: curated, +10, +50, +100, full-209
- 1,305 total cells
- 261 task/trial pair groups

After separately validated empirical-oracle evidence is bound, the same planner
accepts the six-arm schedule:

- oracle-only plus the five base arms
- 1,566 total cells
- exact base-manifest and oracle-evidence file hashes required

The current retained plan is
`experiments/skillsbench/library-scale-batch-plan.json`, with plan hash
`9125d0ae9f32488142edccf297f607b2c61b68a020d78e380f1d216566db19f6`.
It schedules 1,305 pending cells and is not execution evidence.

## Frozen 435-cell confirmatory subset

The Build Week confirmatory execution uses
`experiments/skillsbench/library-scale-trial1-plan.json`. It is derived from
the retained 1,305-cell plan by one fixed structural predicate only:

- keep all 87 tasks in source order;
- keep `trial_index == 1`;
- keep all five arms in the canonical order;
- read no outcome field and permit no cherry-picking.

The resulting denominator is `87 x 1 x 5 = 435`. Its semantic plan SHA-256 is
`4bc47c7aa1d8cfccbebfcba159bdc66e99b003dd2833250ceb8505ca7323cb5f`.
The plan binds both the source batch-plan semantic hash and exact file hash,
plus the canonical manifest file/semantic hashes. Its ordinals are new
`1..435` execution ordinals while every cell retains its original
`source_ordinal` from the 1,305 plan.

This subset is large enough to compare all five library sizes across every
task once, but it cannot establish three-trial uncertainty or claim the full
1,305 schedule is complete.

## Evidence-derived progress

`experiments/skillsbench/library_scale_progress.py` reconstructs progress from
the frozen plan, materialized cell bundles, and immutable normalized traces. A
cell becomes `sealed_validated_trace` only after the existing aggregator
revalidates:

- manifest and cell identity;
- staged skill bytes and presentation order;
- raw provider-trace hash;
- actual invocation completeness;
- unchanged verifier contract and staged verifier tree;
- agent/backend/model/effort/budget/harness consistency;
- verifier outcome and numeric reward policy.

An untraced materialization is not resumed silently. It becomes
`materialized_without_validated_trace`, stops the automatic frontier, and
requires operator audit. Unknown directories, unknown trace records, symlinks,
extra trace-root files, filename/trace-ID drift, rehashed plan drift, and
rehashed progress inflation fail closed.

`experiments/skillsbench/run_library_scale_trial1_supervisor.py` adds the
execution-order gate. It accepts an argv-only child executor, never a shell
command, and appends the frozen plan/cell/root arguments itself. After each
child exits it reconstructs progress from the cell and trace roots and requires
the sealed prefix to advance by exactly one. It stores only child output byte
counts and hashes in the safe event ledger, not raw provider output.

The `canary` phase is exactly the first frozen task at trial 1 across
`curated`, `plus-10`, `plus-50`, `plus-100`, and `full-209`. Only sealed 5/5
evidence can create the hash-bound canary admission. The `full` phase refuses
to start without revalidating that report against the current immutable trace
hashes. Canary admission is an execution-safety gate, not a generalization or
435-cell result.

There is an earlier expansion gate as well. The executor capability initially
permits ordinal 1 only. The supervisor must reconstruct and validate that
trace, then create a hash-bound first-cell admission before ordinals 2--5 are
eligible. The child executor independently revalidates this report and the
exact sealed frontier; the supervisor's command alone cannot bypass it.

The live runtime contract binds the exact plan, source snapshot, external
corpus, requested model and effort, timeouts, executor capability, and a
governed metadata-first exposure budget. The live child stages task-visible
files and hidden verifier bytes separately, exposes only the governed skill
subset, runs Codex through a fixed network-disabled Docker MCP boundary, and
normalizes candidate-library, prompt-exposure, and actual MCP-invocation
evidence into an immutable trace.

The MCP server is configured as `required=true`, so a startup failure must stop
before a tool-less model run can be mistaken for evidence. A native POSIX Codex
binary launches the WSL/POSIX Python server directly. When an authenticated
Windows Codex executable is invoked from WSL, the command uses
`wsl.exe --exec` so the stdio server, source paths, Docker client, and audit
file remain in the admitted WSL environment.

## Current boundary

The live trial-1 execution stopped at an outcome-independent exact prefix of
`75/435`: 15 source-ordered tasks × trial 1 × all five canonical arms. The
legacy base-manifest aggregator maps those traces to `75/1,305` observed and
1,230 missing; that is a different denominator and must not be called 75/435
completion of the three-trial plan.

All five arms record `0/15` verifier pass, mean reward `0.0`, and zero
server-audited MCP exec calls. The historical `15/15 no-invocation` field is a
runner label, not a model-intent claim: later structural audit found model-side
MCP call attempts rejected before `tools/call` reached the server. Wrong/mixed
invocation and shadowing remain unavailable because no empirical oracle mapping
is bound and the full denominator is incomplete. Ordinal 76 then stopped in an
external task Docker build before provider/Codex/MCP/verifier execution; the 75
sealed traces remain valid and unchanged. See
`docs/library-scale-trial1-prefix75-result-2026-07-21.md`.

A separate fresh-runtime ordinal-1 canary also sealed one verifier-reached
trace, with ten model-side MCP attempts but zero server-audited `tools/call` and
no skill-ID evidence. It failed closed without retry and is not part of this
75-cell ledger.

This is a negative executor diagnostic and evidence-contract result. It does
not prove a full-87 result, a library-size curve, GPT-5.6 performance,
provider-resolved model identity, shadowing, generalization, or statistical
significance.

## Commands

The immutable DESKTOP source intentionally excludes the upstream task corpus.
Before any plan, runtime-contract, supervisor, progress, or aggregation command,
bind the separately admitted pinned checkout for that process:

```bash
export THE_KING_SKILLSBENCH_TASKS_ROOT=/path/to/pinned-external-skillsbench/tasks
```

The path must be absolute and its parent checkout must already have passed the
2,160-blob external-corpus admission. Omitting this binding fails closed; it
must never be replaced by copying or symlinking tasks into the source snapshot.

```bash
PYTHONPATH=. python3 -m experiments.skillsbench.create_library_scale_batch_plan \
  --manifest experiments/skillsbench/library-scale-manifest.json \
  --plan experiments/skillsbench/library-scale-batch-plan.json

PYTHONPATH=. python3 -m experiments.skillsbench.library_scale_progress \
  --plan experiments/skillsbench/library-scale-batch-plan.json \
  --manifest experiments/skillsbench/library-scale-manifest.json \
  --cell-root /path/to/new-only/cells \
  --trace-root /path/to/immutable/traces \
  --output /path/to/new-progress-snapshot.json

PYTHONPATH=. python3 -m experiments.skillsbench.derive_library_scale_trial1_plan \
  --source-plan experiments/skillsbench/library-scale-batch-plan.json \
  --manifest experiments/skillsbench/library-scale-manifest.json \
  --plan experiments/skillsbench/library-scale-trial1-plan.json

PYTHONPATH=. python3 -m experiments.skillsbench.library_scale_progress \
  --plan experiments/skillsbench/library-scale-trial1-plan.json \
  --source-plan experiments/skillsbench/library-scale-batch-plan.json \
  --manifest experiments/skillsbench/library-scale-manifest.json \
  --cell-root /path/to/new-only/435-cells \
  --trace-root /path/to/immutable/435-traces \
  --output /path/to/new-435-progress-snapshot.json
```

Run the exact five-arm canary through a child executor whose CLI implements the
appended `--plan`, `--source-plan`, `--manifest`, `--cell-id`, `--cell-root`,
`--trace-root`, and `--first-cell-admission` contract. Create the bound runtime
contract once after DESKTOP capability and source admission are sealed:

```bash
PYTHONPATH=. python3 -m experiments.skillsbench.create_library_scale_trial1_runtime_contract \
  --plan experiments/skillsbench/library-scale-trial1-plan.json \
  --source-plan experiments/skillsbench/library-scale-batch-plan.json \
  --manifest experiments/skillsbench/library-scale-manifest.json \
  --executor-capability /path/to/executor-capability.json \
  --source-snapshot-manifest DESKTOP_SNAPSHOT_MANIFEST.json \
  --model gpt-5.6-terra --effort high --exposure-budget 3 \
  --output /path/to/new-run/runtime-contract.json
```

```bash
PYTHONPATH=. python3 -m experiments.skillsbench.run_library_scale_trial1_supervisor \
  --phase canary \
  --plan experiments/skillsbench/library-scale-trial1-plan.json \
  --source-plan experiments/skillsbench/library-scale-batch-plan.json \
  --manifest experiments/skillsbench/library-scale-manifest.json \
  --cell-root /path/to/new-run/cells \
  --trace-root /path/to/new-run/traces \
  --progress-root /path/to/new-run/progress \
  --first-cell-admission /path/to/new-run/first-cell-admission.json \
  --canary-admission /path/to/new-run/canary-admission.json \
  -- python3 -m experiments.skillsbench.run_library_scale_codex_mcp_cell \
    --runtime-contract /path/to/new-run/runtime-contract.json \
    --executor-capability /path/to/executor-capability.json \
    --admission-start-audit /path/to/admission-start.json \
    --source-snapshot-manifest DESKTOP_SNAPSHOT_MANIFEST.json \
    --raw-root /path/to/new-run/raw \
    --tasks-root /path/to/pinned-external-skillsbench/tasks
```

After the 5/5 canary admission exists, use the same exact child executor
contract with `--phase full`. `--max-cells N` may bound one supervisor turn;
restarting reconstructs the frontier from evidence rather than trusting the
previous process or a mutable counter.
