# SkillsBench Curated Skills (Vendored)

Downloaded: 2026-07-08
Source: https://github.com/benchflow-ai/skillsbench @ `5433cf15`
License: Apache-2.0 (upstream). Paper: https://arxiv.org/abs/2602.12670

## Contents

```text
tasks/                exact upstream task mirror from the pinned commit
skills/               209 indexed skill variants (195 names), plus 1 licenses helper
skills-index.json     skill -> tasks mapping, task taxonomy, curated-bundle mapping
audit_readiness.py    static 87-task readiness manifest builder
readiness-87.json     current static readiness manifest for all 87 tasks
create_split_manifest.py deterministic adaptation/held-out/regression splitter
split-manifest.json   pre-registered 87-task split manifest
create_library_scale_manifest.py deterministic full-87 nested library schedule
library-scale-manifest.json 87 tasks x 3 trials x 5 arms (1,305 cells)
bind_empirical_oracle_manifest.py derive a frozen oracle-only + 5-arm schedule
create_library_scale_batch_plan.py freeze exact 1,305/1,566-cell execution order
library-scale-batch-plan.json retained 1,305-cell new-only execution ledger
derive_library_scale_trial1_plan.py freeze the outcome-blind 87x1x5 subset
library-scale-trial1-plan.json retained 435-cell derived execution plan
library_scale_progress.py derive restart frontier only from revalidated evidence
create_library_scale_trial1_runtime_contract.py bind live model/harness/corpus capability
run_library_scale_codex_mcp_cell.py execute one metadata-first fixed-container MCP cell
run_library_scale_trial1_supervisor.py enforce first-cell/five-cell gates and exact-prefix resume
materialize_library_scale_cell.py stage one base or oracle-bound cell byte-for-byte
aggregate_library_scale_results.py validate traces and compute n/m/o plus decomposition
probe_codex_mcp_capability.py model-free fail-closed Codex/MCP execution gate
management_lifecycle_reevaluation.py M2-K COW route-policy apply/re-evaluate/rollback gate
run_m2k_lifecycle_reevaluation_demo.py controlled synthetic actual-event loop
harness_policy_evaluation.py internal paired M3-K promotion/rollback engine
run_m3k_policy_evaluation_demo.py real deterministic HarnessRuntime M3-K fixture
create_m3k_evaluation_manifest.py freeze/verify the 87x3x2 M3-K schedule
m3k-evaluation-manifest.json 522-trajectory unbound/not-run evaluation contract
create_m3k_policy_proposal_bundle.py create the held-out-clean canonical M3-K proposal
m3k-full87-policy-proposal.json frozen parent-budget10/candidate-budget3 hypothesis
bind_m3k_proposal_manifest.py bind proposal hashes and strict executor capability
m3k_external_evidence.py seal/replay exact 522 external trajectory evidence
verify_corpus.py      local corpus consistency check
verify_upstream_tree.py pinned Git blob comparator
corpus-provenance.json last pinned-tree comparison result
```

`tasks/` matches all 2,160 regular upstream task blobs at the pinned commit. It
keeps the paper-style task directories, environment payloads, oracle files,
verifiers, and per-task `environment/skills/` layout. One upstream gitlink,
`simpo-code-reproduction/.../alignment-handbook`, is represented by an empty
placeholder directory and is recorded separately in `corpus-provenance.json`.

`skills/` is a derived, deduplicated pool extracted from
`tasks/*/environment/skills/` for retrieval and shadowing experiments. When the
same skill name ships with different content across tasks, the most-used
variant keeps the plain name and the others are stored as `<name>@<hash12>`.

## Numbers

- 87 upstream task directories.
- 234 immediate per-task `environment/skills/*` directories; 232 contain a `SKILL.md`.
- The 2 non-`SKILL.md` directories are `licenses/` helper directories in seismic tasks.
- 209 deduplicated Skill variants with `SKILL.md` → 195 unique names.
- `skills/licenses/` is preserved as an upstream helper but is not indexed as a Skill.
- Typical skill: a single `SKILL.md`, median 6.3 KB. 75 variants are multi-file (largest: pptx with OOXML schemas, 1.2 MB).
- Most reused: `xlsx` (10 tasks), `pdf` (7), `power-flow-data` (3).
- Divergent-content names (same name, different body): `pdf` ×5, `xlsx` ×4, `pptx` ×3, `docx`/`browser-testing`/`obj-exporter`/`pddl-skills`/`react-best-practices` ×2.

## Curated bundle vs. empirical oracle

`skills-index.json` `tasks[].curated_skill_variants` lists, per task, the skill
variants vendored with that task upstream. This is the authored curated bundle,
not More Skills' empirical oracle set `S*(q)`.
The exact per-task copies are also preserved under
`tasks/<task>/environment/skills/`. Frontmatter `required_skills` exists for
only 12/87 tasks and is kept as auxiliary metadata.

An empirical oracle set must be estimated separately from isolated no-skill
uplift and keyed by task, model, backend, harness, threshold, candidate-pool
hash, and repeat count. Code must not use the curated mapping as that result.

## Library-scale execution ledger

The frozen 1,305-cell base schedule now has a content-addressed execution plan.
The same planner validates the 1,566-cell oracle-bound derivative when its base
manifest and empirical-oracle evidence are supplied. Progress is reconstructed
from cell bytes and immutable traces rather than a mutable completed flag.
Partial materializations stop at an operator-audit state; unknown or rehashed
evidence cannot advance the frontier. See
`docs/library-scale-execution-ledger-v1.md`.

This is execution infrastructure, not a model-backed result. The retained plan
starts at `0/1,305` sealed cells and cannot expose a shadowing curve until the
full denominator and actual invocation evidence are complete.

For the bounded Build Week confirmatory run,
`library-scale-trial1-plan.json` retains exactly all 87 tasks, trial index 1,
and all five arms in source order: 435 cells. It binds the source manifest and
1,305-cell plan hashes, records an empty outcome-field read set, and prohibits
outcome-based selection and cherry-picking. Completing this subset can support
a predeclared trial-1 confirmatory result; it is not completion of all 1,305
cells or a substitute for the three-trial paper schedule.

The trial-1 supervisor is a fail-closed orchestrator, not a model executor. It
first opens ordinal 1 only. That trace must produce a hash-bound first-cell
admission before the remaining four canary arms open. It then revalidates an
exact one-cell evidence increment after every child exit and writes a second
hash-bound 5/5 canary admission before `--phase full` can open. The live child
mounts only a governed metadata-first subset of the frozen candidate library,
keeps the verifier hidden until the model exits, and distinguishes candidate
library, prompt exposure, and skill-associated MCP invocation. A zero child
exit without a new immutable trace, an out-of-order trace, or an unsealed
materialization stops the frontier.

## Why the divergent variants matter

Same-name/different-body variants are natural material for Merlin:

- distractors for shadowing experiments (near-identical triggers, different procedures),
- merge/dedup lifecycle-action candidates,
- version-governance examples for the library health narrative.

## Verification

```bash
python3 experiments/skillsbench/verify_corpus.py
python3 -m experiments.skillsbench.verify_upstream_tree \
  --upstream-repo /path/to/pinned/skillsbench/clone
```

## Readiness audit

```bash
python3 experiments/skillsbench/audit_readiness.py
```

Current static result:

```text
task_count=87
status_counts={'needs_infrastructure_review': 87}
infrastructure_flag_counts={'docker': 87, 'node': 2, 'workspace_seed': 6}
```

This is a static file/dependency audit, not an executor run. All 87 tasks are
kept in the manifest. Docker/runtime issues must be repaired or registered as
infrastructure exceptions; they are not a reason to silently shrink the
SkillsBench evaluation set.

## Executable oracle readiness

```bash
python3 experiments/skillsbench/run_oracle_readiness.py --limit 1
python3 experiments/skillsbench/run_oracle_readiness.py
```

This builds each task's `environment/Dockerfile`, runs the upstream
`oracle/solve.sh`, then runs `verifier/test.sh` in the same container and reads
`/logs/verifier/reward.txt`. It is the executable B3 check that the task image,
oracle, and verifier are runnable before account-auth CLI (`B_cli`) experiments. Run artifacts
are written under `experiments/skillsbench/runs/oracle-readiness/` and are
ignored by git.

On WSL systems without systemd, the script starts a child `dockerd` process for
the duration of the run. If Docker fails on nftables, it switches `iptables`
and `ip6tables` to the legacy alternatives before starting Docker.

The raw first executable result is preserved, but its integer-only reward parser
misclassified decimal rewards. The non-destructive strict reclassification is:

```text
run_id=one-full-87-20260708-r2
worker=ONE / WSL Ubuntu-24.04 / Docker Engine
task_count=87
strict_passed=79
reward_authoritative_passed=80
reconciled_strict_status_counts={build_failed: 1, oracle_failed: 3, passed: 79, reward_failed: 2, reward_partial: 1, verifier_contract_inconsistent: 1}
```

`civ6-adjacency-optimizer` writes reward `1.000` despite two captured pytest
failures, so it remains an upstream verifier-contract exception under the
strict policy. `scheduling-manifest.json` is generated from the strict derived
artifact; the raw summary is never overwritten.

A corrected targeted rerun resolved the remaining log-contract ambiguities:
`fix-build-agentops=0.0`, `fix-build-google-auto=0.0`, and
`debug-trl-grpo=0.6`. The reconciled artifact is
`runs/oracle-readiness/strict-reconciled-20260710/summary.json`.

## Model-backed harness status

`run_model_c0_c1_pilot.py` implements `H_paper_cli_mcp_v1` for the active
Claude account-auth backend. The sole user message is the exact `task.md` body;
C0 receives no task-local skill bundle, while C1 receives the complete bundle
through the provider-native project path and a read-only container mount. The
host account CLI can reach only a fixed-container MCP `exec` tool, and a
control barrier requires that tool to be connected before the task is sent.
The verifier is copied in and executed once only after the model exits.

The completed frozen three-task pilot is under
`runs/model-c0-c1-pilot/paper-cli-body-{court-form-filling,weighted-gdp,earthquake-plate-calculation}-*`.
Its combined strict aggregate and freeze manifest are under
`runs/model-c0-c1-pilot/paper-cli-body-fixed3-3trial-20260710/`. All nine pairs
are valid and data-contract complete: `C0=1/9`, `C1=8/9`, mean paired delta
`+7/9` (`+0.7778`). Task deltas are `+1/3` for court-form filling and `+1.0`
for each of weighted GDP and earthquake/plate calculation. This pilot is not
paper eligible; see the freeze manifest for exact provenance and limitations.

`run_model_c0_c1_scripted_solver.py` is a bounded Merlin engineering harness,
not a paper-faithful SkillsBench C0/C1 executor. Its C1 path prompt-injects a
size-bounded subset of text skill content and logs truncation explicitly. Main
evaluation uses zero verifier-feedback repairs; repair iterations are labeled
adaptation-only. Account credentials stay in the ONE host CLI session and are
never forwarded into task containers.

### Codex MCP capability gate

The historical `H_paper_cli_mcp_v1` runner has a Claude-specific native-tool
and control-barrier contract. It must not be relabeled as Codex merely because
Codex can initialize the same MCP server. Before any GPT-5.6 six-cell or wider
SkillsBench execution, run the separate model-free diagnostic:

```bash
python3 -m experiments.skillsbench.probe_codex_mcp_capability \
  --codex-executable /path/to/codex \
  --app-server-schema-dir /path/to/generated/codex-schema \
  --recorded-mcp-audit /path/to/metadata-only-mcp-audit.jsonl \
  --container-id ACTUAL_TASK_CONTAINER \
  --output /path/to/codex-mcp-capability.json
```

The probe directly performs only `initialize`,
`notifications/initialized`, and `tools/list` against
`container_exec_mcp.py`; it never calls `exec`, submits a task, or invokes a
model. It separately hashes local CLI help, checks per-run config and isolation
flags, summarizes an optional metadata-only audit, and inspects (but does not
execute inside) an explicitly named container. It writes new-only output and
never copies tool arguments or output.

`six_cell_execution_allowed=true` requires every strict condition: the direct
single-`exec` MCP contract, per-run config, user-config/rules suppression,
ephemeral JSON read-only controls, native tool allowlist and denylist, strict
MCP configuration, an audit-observed `tools/call(exec)`, and a real inspected
container. A handshake alone is never benchmark evidence.

The local 2026-07-19 diagnostic for `codex-cli 0.145.0-alpha.18` passed the
direct MCP handshake and exposed one bounded `exec` tool, but correctly kept
the six-cell gate closed: native tool allow/deny and strict-MCP flags were not
present, the recorded Codex audit contained `initialize`/`tools/list` but no
`tools/call(exec)`, and no local Docker runtime existed. This is a capability
diagnostic, not a GPT-5.6 or SkillsBench result.

The follow-up feature-suppression probe records whether the local Codex build
accepts per-invocation disabling for 20 tool-bearing feature families,
including `shell_tool`, `unified_exec`, browser, apps, computer use, plugins,
skills, and workspace dependencies. The local build reported all 20 disabled
under that command contract. This feature listing is not a runtime tool
inventory and does not replace native-tool allow/deny proof, exact MCP call
observation, or an inspected container. A bounded requested-GPT-5.6 canary
selected `merlin_harness_task.exec` once, but the Codex exec-mode approval layer
canceled it before the MCP server received `tools/call`; other low-effort
attempts returned without a tool call. None is a task or benchmark result, and
the six-cell gate remains closed.

The refreshed auto-discovery probe also found a stale executable npm shim whose
packaged vendor binary was missing. Discovery now requires a candidate to
answer `--version`, skips broken PATH shims, and selected
`/Applications/ChatGPT.app/Contents/Resources/codex` without an explicit path.
The real gate remained closed for the same five missing requirements: native
tool allowlist, native tool denylist, strict MCP configuration, an observed
`tools/call(exec)`, and an inspected container. This repair changes discovery
reliability only; it does not relax executor admission.

The optional app-server schema inspection is also fail-closed. For the bundled
`0.145.0-alpha.18` binary, `dynamicTools` exists only on thread start as an
additive host-tool specification; the generated config schema exposes only
`web_search` beneath `tools`. No declared property provides a native-tool
allowlist, native-tool denylist, or strict-MCP-only configuration. The report
stores only schema hashes and bounded property-name summaries, never the raw
schemas, and schema presence can never open the execution gate by itself.

## M2-K lifecycle re-evaluation foundation

The common management layer emits read-only M0/M1/M2-H/M2-K plans. The
research-only M2-K continuation now applies an eligible task/skill route guard
as a copy-on-write policy layer, requires every provisional trace to bind the
exact policy hash and staged task exposure, re-runs the unchanged frozen
agent/model/tools/verifier/budget contract, and promotes or rolls back through
fixed checks. It never turns a route-local failure into a global skill hide.

Run the model-free contract demonstration with a fresh output path:

```bash
python3 -m experiments.skillsbench.run_m2k_lifecycle_reevaluation_demo \
  --output /private/tmp/merlin-m2k-reevaluation-UNIQUE
```

The controlled fixture stages one `management-wrong × distractor` guard. The
same three trajectories then move verifier pass `2/3 -> 3/3`, `pi_o 0.5 ->
1.0`, and `pi_m 0.5 -> 0.0`, passing all eight lineage, denominator, guarded
invocation, non-regression, and improvement checks. Separate tests prove
rollback on metric regression or a stricter threshold and fail closed on
policy-lineage, exposure, or invocation bypass. These are synthetic
skill-body-load events; this is execution-contract evidence, not a GPT-5.6 or
full-87 management result.

## M3-K internal harness-policy evaluation

The older core `evaluate_harness_evolution()` remains an explicit scaffold that
accepts caller-computed deltas. It is not used as M3-K result evidence. The
research-only `harness_policy_evaluation.py` instead freezes reconstructable
parent/candidate variant hashes before evaluation, constructs every paired
task/trial cell, and launches a fresh executor for each variant. It rejects
split, task, verifier, instruction, variant, contract, trace, or raw-byte
lineage drift and never accepts external deltas.

Promotion recomputes pass-rate, shadowing, and cost by split. The candidate
must be non-regressive on held-in and hidden held-out, improve at least one,
preserve the t0-passing regression subset, preserve shadowing on all splits,
stay within the cost guardrail, and provide complete actual-invocation evidence.
All regression-candidate tasks are still executed; final regression eligibility
is defined only after the parent passes every repeat, matching the canonical
split policy.

Run the real deterministic vertical slice with a fresh path:

```bash
python3 -m experiments.skillsbench.run_m3k_policy_evaluation_demo \
  --output /private/tmp/merlin-m3k-policy-UNIQUE
```

It executes 6 tasks × 2 repeats × 2 variants through the actual
`HarnessRuntime`, reconstructed processor manifests, `RecipeSkillExecutor`,
fresh workspaces, stored traces, and deterministic verifiers. The bounded
negative-constraint policy candidate moves held-in and held-out pass delta by
`+1.0` each, keeps regression at `0.0`, reduces shadowing by `-1.0` on both
primary splits, and passes `10/10` gates. This is model-free implementation
evidence, not a GPT-5.6 result.

The full schedule is frozen separately:

```bash
python3 -m experiments.skillsbench.create_m3k_evaluation_manifest verify \
  --manifest experiments/skillsbench/m3k-evaluation-manifest.json
```

It binds canonical split/task/verifier/instruction hashes for 87 tasks × 3
repeats × parent/candidate = 522 expected trajectories. Parent/candidate hashes
and strict executor evidence are intentionally unbound, so the manifest remains
`not_run` with `execution_allowed=false`. It is a full-denominator execution
foundation, not a full-87 result.

The canonical full-87 proposal is pre-registered separately. Its M2-K parent
uses the runner's bounded 10-skill ceiling and its M3-K candidate clamps
model-visible exposure to 3. Construction is bound to the frozen controlled
overload diagnostic (`1/10`, `pi_m=8/9`) and eight route-risk trace IDs. The
bundle records that this is a model-free hypothesis, uses no full-87 held-out
task/verifier/oracle, and claims neither improvement nor promotion. Regenerate
only to a fresh path when auditing the checked-in artifact:

```bash
python3 -m experiments.skillsbench.create_m3k_policy_proposal_bundle \
  --output /path/to/fresh/m3k-full87-policy-proposal.json
```

`bind_m3k_proposal_manifest.py` rejects legacy or arbitrary proposal JSON and
turns the schedule into a schema-2 execution contract only after reconstructing
and hashing this exact canonical parent/candidate proposal. It also requires
the canonical library-scale manifest and binds
every parent/candidate trajectory to the same matching `full-209` cell. The
ordered 209 IDs, order hash, snapshot hash, trial seed, source semantic hash,
and source file hash are part of the immutable contract; a result cannot swap
the skill set or ordering after proposal binding. A supplied Codex/MCP
capability schema-v3 record must combine three independently retained sources:
the model-free CLI/MCP preflight, one live non-benchmark requested-model canary
that actually calls the single `exec` tool, and `docker inspect` bytes for one
running container. Feature suppression, strict config, user/rules suppression,
one fixed MCP surface, forbidden native-item absence, and the inspected runtime
must all pass. Even then the capability authorizes only pilot ordinal 1; it can
never authorize all six cells by itself.

Create those artifacts before proposal binding. The inspected capability
container is a disposable runtime proof, not the later task container; remove
it before host admission so the admission scan sees no stale Merlin or
SkillsBench container.

```bash
python3 -m experiments.skillsbench.probe_codex_mcp_capability \
  --output /path/to/executor/preflight.json

python3 -m experiments.skillsbench.run_codex_mcp_boundary_smoke \
  --raw-root /path/to/executor/canary-raw \
  --output /path/to/executor/boundary-canary.json \
  --model gpt-5.6-terra \
  --effort high

docker inspect CAPABILITY_CONTAINER \
  > /path/to/executor/capability-container-inspect.json

python3 -m experiments.skillsbench.compose_codex_mcp_executor_capability \
  --preflight /path/to/executor/preflight.json \
  --boundary-canary /path/to/executor/boundary-canary.json \
  --container-inspect /path/to/executor/capability-container-inspect.json \
  --model gpt-5.6-terra \
  --effort high \
  --output /path/to/executor/eligible-one-cell-capability.json
```

Each output is new-only. A failed canary or capability composition must be
retained as diagnostic evidence and replaced with a fresh path rather than
edited or overwritten.

```bash
python3 -m experiments.skillsbench.bind_m3k_proposal_manifest \
  --schedule experiments/skillsbench/m3k-evaluation-manifest.json \
  --library-scale-manifest experiments/skillsbench/library-scale-manifest.json \
  --proposal-bundle experiments/skillsbench/m3k-full87-policy-proposal.json \
  --executor-capability /path/to/executor/eligible-one-cell-capability.json \
  --output /path/to/ready-bound-m3k.json
```

Before paying for 522 trajectories, derive the exact six-run held-in pilot and
materialize one trajectory. Both outputs are new-only and remain `not_run`:

```bash
python3 -m experiments.skillsbench.create_m3k_pilot_manifest \
  --bound-manifest /path/to/ready-bound-m3k.json \
  --task-id 3d-scan-calc \
  --output /path/to/m3k-six-cell-pilot.json

python3 -m experiments.skillsbench.materialize_m3k_external_cell \
  --bound-manifest /path/to/ready-bound-m3k.json \
  --library-scale-manifest experiments/skillsbench/library-scale-manifest.json \
  --trajectory-id 'parent:held_in:3d-scan-calc:t1' \
  --output /path/to/operator-cells/3d-scan-parent-t1
```

The materializer copies the exact 209 skill packages in presentation order,
selects the correct parent/candidate harness bytes, exposes only `task.md` and
the task environment, keeps the verifier in a separate hidden tree, and never
copies the oracle. Its attestation and runtime-audit files are invalid-safe
templates until an eligible executor supplies real invocation, verifier, raw
trace, container, and isolation evidence. The pilot is explicitly unable to
claim full-87 completion or promote a candidate.

Immediately before handing a cell to the external executor, reopen the bundle
and verify every staged skill package, task instruction/environment, hidden
verifier, harness variant, proposal bundle, and fail-closed template against
the sealed execution contract:

```bash
python3 -m experiments.skillsbench.materialize_m3k_external_cell \
  --validate-existing /path/to/operator-cells/3d-scan-parent-t1 \
  --expected-contract-sha256 EXPECTED_SHA256
```

The revalidator rejects root membership changes, symlinks, staged byte drift,
contract identity drift, oracle/verifier leakage into the task-visible tree,
and templates that were pre-completed before the model run. A successful
revalidation still reports `execution_status=not_run` and is not model or
benchmark evidence.

Prepare all six pilot trajectories atomically instead of repeating the single
cell command by hand:

```bash
python3 -m experiments.skillsbench.prepare_m3k_pilot_operator_bundle prepare \
  --bound-manifest /path/to/ready-bound-m3k.json \
  --pilot-manifest /path/to/m3k-six-cell-pilot.json \
  --library-scale-manifest experiments/skillsbench/library-scale-manifest.json \
  --bundle /path/to/m3k-six-cell-operator

python3 -m experiments.skillsbench.prepare_m3k_pilot_operator_bundle validate \
  --bound-manifest /path/to/ready-bound-m3k.json \
  --pilot-manifest /path/to/m3k-six-cell-pilot.json \
  --library-scale-manifest experiments/skillsbench/library-scale-manifest.json \
  --bundle /path/to/m3k-six-cell-operator
```

The operator manifest freezes the exact pilot order, six unique cell pointers,
each execution-contract file/hash, and invalid-safe progress template. Creation
uses a temporary sibling and atomic rename, so a failed cell cannot leave a
plausible partial bundle. Validation reopens all six full-209 trees. The bundle
still has `status=not_run`; it reduces operator error but supplies no model
trajectory evidence.

Run exactly one sealed cell through the requested Codex model and the fixed
Docker MCP bridge. The capability file must be the exact byte-hashed report
already bound into the ready manifest; model, effort, backend, and tool surface
must also match the frozen evaluation contract:

```bash
python3 -m experiments.skillsbench.run_m3k_codex_mcp_cell \
  --operator-bundle /path/to/m3k-six-cell-operator \
  --bound-manifest /path/to/ready-bound-m3k.json \
  --executor-capability /path/to/eligible-capability.json \
  --admission-start-audit /path/to/admission-one-cell/start.json \
  --source-snapshot-manifest /path/to/DESKTOP_SNAPSHOT_MANIFEST.json \
  --pilot-manifest /path/to/m3k-six-cell-pilot.json \
  --library-scale-manifest experiments/skillsbench/library-scale-manifest.json \
  --ordinal 1 \
  --raw-root /path/to/new/raw-parent-t1 \
  --evidence-root /path/to/sealed-six-cell-evidence \
  --model gpt-5.6-terra \
  --effort high
```

The runner must be the direct child of `desktop_host_admission.py`. It checks
the admission environment against the exact `start.json`, rebinds that audit to
the transferred snapshot manifest, and carries both byte hashes into the run
config and execution event. A copied or directly launched runner therefore
fails before Docker or model execution. It then reconstructs the bound harness,
retrieves from all 209 staged skill
records, mounts only the provisioned skill directories, disables every known
host tool-bearing Codex feature, and exposes one `exec` MCP tool tied to one
inspected networkless task container. An optional `skill_id` on that tool is
restricted to provisioned IDs and creates harness-associated invocation
evidence; it is not provider-native Skill invocation evidence. The hidden
verifier is copied only after the model process ends. Raw Codex JSONL, the
metadata-only MCP audit, capability/admission/snapshot bytes, container/image
inspect, run config, provisioning, verifier output/result, and build logs are
independently hashed. `cost` currently
records total tokens as a deterministic proxy, not currency spend. Each raw
root is new-only and one run can establish only one live trajectory. The runner
also calls the external-evidence recorder internally after reconstructing all
raw bindings; do not call `record` a second time for the same trajectory.

Immediately replay ordinal 1 from the sealed evidence root. This report opens
only ordinals 2 through 6:

```bash
python3 -m experiments.skillsbench.validate_m3k_first_cell_evidence \
  --bound-manifest /path/to/ready-bound-m3k.json \
  --pilot-manifest /path/to/m3k-six-cell-pilot.json \
  --evidence-root /path/to/sealed-six-cell-evidence \
  --output /path/to/m3k-first-cell-admission.json
```

Run ordinal 2 through the same `desktop_host_admission.py` wrapper and add the
exact first-cell report. Repeat with fresh raw roots for ordinals 3 through 6:

```bash
python3 -m experiments.skillsbench.run_m3k_codex_mcp_cell \
  --operator-bundle /path/to/m3k-six-cell-operator \
  --bound-manifest /path/to/ready-bound-m3k.json \
  --executor-capability /path/to/eligible-one-cell-capability.json \
  --admission-start-audit /path/to/admission-cell-2/start.json \
  --source-snapshot-manifest /path/to/DESKTOP_SNAPSHOT_MANIFEST.json \
  --pilot-manifest /path/to/m3k-six-cell-pilot.json \
  --first-cell-report /path/to/m3k-first-cell-admission.json \
  --library-scale-manifest experiments/skillsbench/library-scale-manifest.json \
  --ordinal 2 \
  --raw-root /path/to/new/raw-cell-2 \
  --evidence-root /path/to/sealed-six-cell-evidence \
  --model gpt-5.6-terra \
  --effort high
```

For an alternate external executor that does not use the integrated cell
runner, `m3k_external_evidence.py record` can seal one already-completed
scheduled trajectory at a time. The runtime audit must bind the exact manifest,
capability file, trajectory, requested model contract, raw-provider-trace hash,
container/image/config hashes, strict MCP controls, and the absence of host-tool
events. The recorder reopens all 16 required raw artifacts, recomputes MCP
calls and skill IDs, checks the verifier result and every cross-hash, and stores
a deterministic content-addressed execution-pack tar alongside the raw trace
and audit. All three evidence classes are new-only and cannot be reused. After
ordinal 1 has opened the remainder and exactly six pilot trajectories are
recorded, validate the
executor contract before expanding the schedule:

```bash
python3 -m experiments.skillsbench.validate_m3k_pilot_evidence \
  --bound-manifest /path/to/ready-bound-m3k.json \
  --pilot-manifest /path/to/m3k-six-cell-pilot.json \
  --evidence-root /path/to/sealed-six-cell-evidence \
  --output /path/to/m3k-six-cell-admission.json
```

Reopen the stored admission later against the same manifests and sealed
evidence root instead of trusting the JSON file or its self-reported hash:

```bash
python3 -m experiments.skillsbench.validate_m3k_pilot_evidence \
  --bound-manifest /path/to/ready-bound-m3k.json \
  --pilot-manifest /path/to/m3k-six-cell-pilot.json \
  --evidence-root /path/to/sealed-six-cell-evidence \
  --report /path/to/m3k-six-cell-admission.json
```

This gate requires exact `6/6` coverage, three parent/candidate pairs, complete
actual-invocation evidence, unique raw/runtime artifacts, and the frozen
bound-manifest file hash. A pass authorizes only operator-controlled expansion
to the 522-trajectory contract. It cannot promote a candidate or claim live
model identity, native Skill invocation, library-scale shadowing, or full-87
results. The focused synthetic tests cover success, `5/6` rejection,
invocation gaps, file-hash drift, new-only report output, report-hash tamper,
semantic tamper followed by attacker recomputation of `report_sha256`, and raw
provider-trace drift after report creation. Revalidation deterministically
reconstructs the expected report from all source evidence; a matching
self-hash alone is insufficient.

After the six-cell gate passes, freeze the one allowed expansion order before
adding more evidence:

```bash
python3 -m experiments.skillsbench.create_m3k_full87_batch_plan \
  --bound-manifest /path/to/ready-bound-m3k.json \
  --library-scale-manifest experiments/skillsbench/library-scale-manifest.json \
  --pilot-manifest /path/to/m3k-six-cell-pilot.json \
  --pilot-report /path/to/m3k-six-cell-admission.json \
  --evidence-root /path/to/sealed-m3k-evidence \
  --output /path/to/m3k-full87-batch-plan.json
```

The immutable plan preserves bound-manifest order for all 522 trajectories,
marks only the exact six pilot records as sealed, and leaves 516 pending. It
binds the byte and semantic hashes of the full schedule, library-scale
manifest, pilot manifest, pilot report, and pilot records. A self-rehashed
status edit is rejected by dependency reconstruction. Once expansion begins,
the plan can still reopen the original six records within the larger evidence
root; this does not relax whole-root validation for progress or assembly.

Create a new evidence-derived progress snapshot after each admitted cell or
restart. Never edit an earlier snapshot to resume work:

```bash
python3 -m experiments.skillsbench.m3k_full87_progress \
  --plan /path/to/m3k-full87-batch-plan.json \
  --bound-manifest /path/to/ready-bound-m3k.json \
  --library-scale-manifest experiments/skillsbench/library-scale-manifest.json \
  --pilot-manifest /path/to/m3k-six-cell-pilot.json \
  --pilot-report /path/to/m3k-six-cell-admission.json \
  --evidence-root /path/to/sealed-m3k-evidence \
  --output /path/to/progress/progress-000007.json
```

The progress tool enumerates only scheduled record pointers and then reopens
every currently recorded trajectory together. Orphan raw/audit/pack files,
unknown records, reused evidence, inner-pack drift, missing pilot records, and
rehashed progress edits fail closed. The next pending cell is derived from the
frozen order. Even `all_evidence_sealed` is an evidence-coverage state, not a
benchmark result or promotion decision; the 522-record assembler remains the
only policy evaluation boundary.

For post-pilot execution, materialize the exact `next_pending.trajectory_id`
reported by that validated snapshot, then pass both artifacts back to the
one-cell runner. Do not pass an ordinal in this mode:

```bash
python3 -m experiments.skillsbench.materialize_m3k_external_cell \
  --bound-manifest /path/to/ready-bound-m3k.json \
  --library-scale-manifest experiments/skillsbench/library-scale-manifest.json \
  --trajectory-id EXACT_NEXT_PENDING_ID \
  --output /path/to/operator-cells/next-cell

python3 -m experiments.skillsbench.run_m3k_codex_mcp_cell \
  --materialized-cell /path/to/operator-cells/next-cell \
  --batch-plan /path/to/m3k-full87-batch-plan.json \
  --progress /path/to/progress/progress-000006.json \
  --pilot-report /path/to/m3k-six-cell-admission.json \
  --bound-manifest /path/to/ready-bound-m3k.json \
  --pilot-manifest /path/to/m3k-six-cell-pilot.json \
  --library-scale-manifest experiments/skillsbench/library-scale-manifest.json \
  --executor-capability /path/to/eligible-capability.json \
  --admission-start-audit /path/to/admission-next-cell/start.json \
  --source-snapshot-manifest /path/to/DESKTOP_SNAPSHOT_MANIFEST.json \
  --raw-root /path/to/new/raw-next-cell \
  --evidence-root /path/to/sealed-m3k-evidence \
  --model gpt-5.6-terra \
  --effort high
```

The post-pilot resolver reconstructs the progress snapshot, derives the
ordinal itself, and requires the materialized contract trajectory to match the
exact next pending work key. A caller cannot skip ahead by choosing another
valid cell or supplying a manual ordinal. The runner seals the plan/progress/
materialization identities into `run-config.json`; successful recording is
still one trajectory, not full-87 completion.

`assemble` requires exactly 522 records, revalidates every byte and
schedule and library field, and replays only normalized trajectories through the existing
M3-K promotion/rollback evaluator. A complete synthetic fixture validates the
contract, but the assembly explicitly claims neither live model execution nor
a full-87 result. The local capability gate still prevents an actual run.

## Full 87-task C0/C1 batch

The original full-denominator execution contract is frozen under
`runs/model-c0-c1-full87/paper-cli-full87-20260710/control/experiment-manifest.json`.
It contains all 87 public tasks, C0/C1, and trial indices `[1,2,3]`: `261`
paired runs and `522` cells. The model/backend contract is Claude Sonnet 5 at
high effort through the user's Claude.ai account-auth CLI. No API-key
environment variable or credential is forwarded into a task container.

The execution-readiness source is
`runs/oracle-readiness/full87-execution-readiness-20260710/summary.json`:

```text
task_count=87
strict_passed=81
execution_ready=84
full_denominator_included=87
explicit_nonpass=6
```

The six rows are retained in the denominator: `civ6-adjacency-optimizer`,
`debug-trl-grpo`, `fix-build-agentops`, `fix-druid-loophole-cve`,
`seismic-phase-picking`, and `setup-fuzzing`. The first three are executable
with non-perfect oracle rewards; the last three are preflight infrastructure
exceptions whose model-run outcomes must still be recorded.

That v1 run is preserved as an early-stopped diagnostic at `15/261` pairs. It
incorrectly treated frozen-budget `agent_timeout` outcomes as infrastructure,
so three consecutive `bike-rebalance` pairs triggered its guardrail. It will
not be resumed or mixed with corrected results.

The replacement contract is
`runs/model-c0-c1-full87/paper-cli-full87-v2-20260712/control/experiment-manifest.json`.
It restarts from pair zero with the same tasks, arms, trials, model, account
auth, prompt, skill exposure, timeouts, verifier, and resource guardrails.
Exit-124 agent-budget exhaustion is now emitted directly as an explicit
zero-score model non-completion and is excluded from infrastructure counts.
All other runner failures retain the existing unscored infrastructure policy.

`run_full87_c0_c1_batch.py` resumes only pairs whose two arms both have numeric
evaluation scores. Partial or failed attempts are archived before retry. It
enforces the frozen provider-equivalent usage, free-disk, and consecutive
infrastructure-failure guardrails. `run_full87_one_manager.sh` adds a process
lock, PID/status files, and a durable log for ONE; a Windows scheduled task
keeps the WSL foreground process alive after SSH disconnects.

`apply_timeout_zero_scores.py` handles the separate case where a model exhausts
the frozen agent budget before any verifier invocation. It validates the
`agent_timeout` record, requires exit 124 and no verifier command, backs up raw
`summary.json`/`records.jsonl`, and writes an explicit denominator score `0.0`
with `score_source=model_noncompletion_timeout_zero`. It does not change the
runner or call the outcome a verifier reward. This avoids both silent exclusion
and indefinite retry while preserving the failure class for aggregation.

The v2 code passed `130/130` unit tests on both local and ONE, the full corpus
consistency check, frozen-input validation, and a bounded one-pair dry run. Its
first formal pair (`3d-scan-calc`, trial 1) passed `C0=1.0`, `C1=1.0`; C0 made
zero task-skill calls and C1 invoked `mesh-analysis` once. The durable
single-worker manager then resumed at trial 2, while its Windows task future
trigger was disabled to prevent duplication. Treat v1 as runner diagnostic
evidence, not a full-benchmark result.

The unchanged v2 contract can be checked without Docker, model calls, state
files, or other runtime writes:

```bash
python3 experiments/skillsbench/validate_full87_contract.py \
  --manifest experiments/skillsbench/runs/model-c0-c1-full87/paper-cli-full87-v2-20260712/control/experiment-manifest.json
```

This proves only the frozen `87 x 3 x C0/C1 = 522` execution contract and its
input hashes. It is not a completed full-87 result.

## Full-87 repeated library-scale contract

`create_library_scale_manifest.py` adds the missing large-library scheduling
substrate without inventing model results. For every one of the 87 tasks it
keeps the upstream curated task bundle present, adds deterministic nested
distractor prefixes of `0`, `10`, `50`, `100`, and the full 209-variant pool,
and repeats the matched schedule three times. A separate stable presentation
order avoids a curated-first ordering advantage.

```bash
python3 experiments/skillsbench/create_library_scale_manifest.py
python3 experiments/skillsbench/create_library_scale_manifest.py \
  --verify experiments/skillsbench/library-scale-manifest.json
```

```text
tasks=87
trials=3
arms_per_trial=5
cells=1305
skill_pool=209
manifest_sha256=e4b0f83e948a4602cbe22c842bcea04fd339f756672a09beab038cf0c85c7480
```

The manifest is deliberately fail-closed about claims. The curated bundle is
not an empirical oracle set, exposed or selected IDs are not actual invocation,
and headline shadowing metrics remain ineligible until a separately estimated
oracle manifest and complete raw-hash-verified invocation traces are attached.
The file is an execution and reproducibility foundation, not evidence that the
1,305 model cells have run.

One scheduled cell can be staged into a brand-new run directory without model
execution:

```bash
python3 experiments/skillsbench/materialize_library_scale_cell.py \
  --manifest experiments/skillsbench/library-scale-manifest.json \
  --cell-id 3d-scan-calc__t1__curated \
  --output /private/tmp/merlin-scale-cell
```

The materializer refuses an existing destination, revalidates the complete
manifest first, copies only the cell's ordered skill variants, and hashes every
source and staged package into `cell-contract.json`. This closes schedule to
runtime-byte drift, but it is still neither model execution nor invocation
evidence.

Completed normalized traces are aggregated through the same evidence boundary:

```bash
python3 experiments/skillsbench/aggregate_library_scale_results.py \
  --manifest experiments/skillsbench/library-scale-manifest.json \
  --cell-root /path/to/run/cells \
  --trace-root /path/to/run/traces \
  --output /path/to/run/library-scale-aggregate.json
```

The aggregator revalidates every still-staged skill byte tree, cell/manifest
binding, runtime model/backend/effort/budget/harness identity, same-verifier
tree, normalized verifier result, and raw provider trace hash. Selected skill
IDs remain separate from provider-observed body loads. Missing cells, unscored
infrastructure outcomes, or incomplete invocation evidence make shadowing
unavailable instead of silently reducing the denominator.

An optional empirical-oracle manifest must cover all 87 tasks. For each task,
its hashed evidence file must contain matched no-skill and every curated-skill
candidate with at least three reward trials, the same verifier/runtime
contracts, and unique hash-verified raw trace pointers. The loader recomputes
mean uplift against `tau`; it does not trust the declared oracle list. With
complete actual-invocation evidence this enables `n/m/o` curves. The canonical
1,305-cell manifest remains curated-reference-only, so decomposition is blocked
for that schedule.

The estimation denominator is now generated rather than hand-counted:

```bash
python3 experiments/skillsbench/create_empirical_oracle_estimation_manifest.py \
  --base-manifest experiments/skillsbench/library-scale-manifest.json \
  --model-id gpt-5.6-terra \
  --backend codex-cli-account-auth \
  --harness-mode single-skill-explicit-prompt-v1 \
  --tau 0.1 --repeats 3 \
  --output /path/to/empirical-oracle-estimation.json

python3 experiments/skillsbench/create_empirical_oracle_estimation_manifest.py \
  --base-manifest experiments/skillsbench/library-scale-manifest.json \
  --verify /path/to/empirical-oracle-estimation.json
```

It exactly revalidates the canonical base and freezes 87 no-skill plus 232
curated single-skill conditions across three paired trials: **319 conditions /
957 cells**. Every cell binds task/verifier trees, candidate bytes, stable seed,
and the six-field runtime contract. It contains no reward, model response,
invocation event, or oracle membership and is explicitly `schedule_only`.

Stage one cell into a new executor handoff bundle without running a model:

```bash
python3 experiments/skillsbench/materialize_empirical_oracle_estimation_cell.py \
  --manifest /path/to/empirical-oracle-estimation.json \
  --base-manifest experiments/skillsbench/library-scale-manifest.json \
  --cell-id '3d-scan-calc__single-skill:mesh-analysis__t1' \
  --output /path/to/cells/3d-scan-calc-mesh-t1
```

The cell materializer creates an empty `skills/` directory for no-skill or
copies exactly one verified bundle for single-skill. Its `cell-contract.json`
binds the manifest and cell hashes, task/verifier trees, source/staged skill
bytes, runtime contract, trial seed, and the assembler's hash-addressed result
pointer. It refuses existing output and still records `execution_status=not_run`.

After an external executor finishes one cell, normalize its evidence instead
of hand-writing a result:

```bash
python3 experiments/skillsbench/record_empirical_oracle_cell_result.py \
  --manifest /path/to/empirical-oracle-estimation.json \
  --cell-contract /path/to/materialized-cell/cell-contract.json \
  --raw-trace /path/to/external/provider-trace.jsonl \
  --condition-evidence /path/to/external/condition-evidence.json \
  --reward 1.0 \
  --results-root /path/to/empirical-oracle-results
```

The recorder revalidates the manifest, cell contract, still-staged skill bytes,
prompt-exposed condition IDs, candidate hashes, verifier/runtime contracts, and
native-invocation completeness boundary. It copies and re-hashes the raw trace,
rejects duplicate bytes or existing destinations, and writes exactly the schema
consumed by the assembler.

Before paying for all 957 cells, require a task-complete paired pilot. For a
one-candidate task this means no-skill and single-skill across three trials, or
six normalized results:

```bash
python3 experiments/skillsbench/validate_empirical_oracle_pilot.py \
  --estimation-manifest /path/to/empirical-oracle-estimation.json \
  --results-root /path/to/six-cell-pilot-results \
  --task-id earthquake-plate-calculation \
  --output /path/to/earthquake-pilot-report.json
```

The pilot gate checks the exact task denominator, scored results, prompt
condition bytes, verifier/runtime identity, and unique hash-valid raw traces.
It reports task-local mean uplift only. Passing authorizes contract expansion;
it is not a full empirical oracle, library-scale shadowing result, or proof of
provider-native invocation.

After an external executor has produced every hash-addressed cell result and a
unique raw trace, assemble portable evidence with:

```bash
python3 experiments/skillsbench/assemble_empirical_oracle_evidence.py \
  --estimation-manifest /path/to/empirical-oracle-estimation.json \
  --base-manifest experiments/skillsbench/library-scale-manifest.json \
  --results-root /path/to/empirical-oracle-results \
  --output-root /path/to/portable-empirical-oracle
```

The assembler requires exact 957-cell coverage, exact prompt-exposed condition
IDs and candidate byte hashes, scored rewards, matching verifier/runtime
contracts, and non-reused raw trace hashes. It copies traces into a new-only
portable root, derives membership from mean uplift against `tau`, then loads
the result through the existing empirical-oracle validator. A synthetic
end-to-end test carries the assembled 87-task evidence into the derived 1,566-
cell schedule. This proves the execution contract, not a real GPT-5.6 result;
the 957 model cells have not been run.

Once real empirical-oracle evidence exists, derive—not hand-edit—the paired
six-arm schedule:

```bash
python3 experiments/skillsbench/bind_empirical_oracle_manifest.py \
  --base-manifest experiments/skillsbench/library-scale-manifest.json \
  --empirical-oracle /path/to/empirical-oracle.json \
  --output /path/to/library-scale-oracle-bound.json
```

This produces `87 x 3 x 6 = 1,566` cells in fixed order:
`oracle-only`, `curated`, `+10`, `+50`, `+100`, and `full-209`. An empty
empirical oracle is valid and becomes a zero-skill oracle-only arm. Every task
and cell carries the recomputed oracle IDs, while the derived file hashes both
the unchanged base manifest and empirical-oracle evidence.

Materialization and aggregation must receive those dependencies again:

```bash
python3 experiments/skillsbench/materialize_library_scale_cell.py \
  --manifest /path/to/library-scale-oracle-bound.json \
  --base-manifest experiments/skillsbench/library-scale-manifest.json \
  --empirical-oracle /path/to/empirical-oracle.json \
  --cell-id 3d-scan-calc__t1__oracle-only \
  --output /path/to/run/cells/3d-scan-calc__t1__oracle-only

python3 experiments/skillsbench/aggregate_library_scale_results.py \
  --manifest /path/to/library-scale-oracle-bound.json \
  --base-manifest experiments/skillsbench/library-scale-manifest.json \
  --empirical-oracle /path/to/empirical-oracle.json \
  --cell-root /path/to/run/cells \
  --trace-root /path/to/run/traces \
  --output /path/to/run/library-scale-aggregate.json
```

The materializer revalidates both dependency hashes and records them in every
cell contract. The aggregator rejects selected or invoked IDs outside the
staged library, requires all 1,566 scored cells and complete actual-invocation
evidence, and then compares the oracle-only summary with every expanded arm.
It reports `Delta_ctx`, `Delta_shd`, their sum, the directly observed pass-rate
drop, and an invariant check. Every eligible oracle-only comparison also uses a
two-stage paired percentile bootstrap: sample 87 task clusters with replacement,
then sample the three oracle/library-paired trial trajectories inside each
selected task. The same resample jointly computes `p_oracle`, `p_library`,
observed drop, `Delta_ctx`, `Delta_shd`, and their total, using 2,000 iterations
at 95% confidence with a frozen seed. Partial denominators, missing invocation
evidence, a curated rather than oracle-only reference, or an undefined/non-finite
resample produce no CI instead of a reduced denominator. These code paths and a
synthetic complete 1,566-cell trajectory are tested; no real empirical-oracle
file or model result is currently claimed.

## Split manifest

```bash
python3 experiments/skillsbench/create_split_manifest.py
```

Current split:

```text
task_count=87
adaptation=35
held_out=30
regression=22
held_out_min_seeds=4
```

`split-manifest.json` is fixed before skill generation. The regression split is
a pre-registered candidate set; the final regression set is the subset that the
t0 harness passes.
