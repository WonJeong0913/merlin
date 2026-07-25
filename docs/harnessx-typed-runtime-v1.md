# HarnessX-inspired typed runtime v1

## Status and evidence boundary

This is a deterministic next-version implementation for Merlin's harness
policy layer. It is derived from the local paper source
the locally archived HarnessX paper and is intentionally separate from
the frozen 435-cell DESKTOP evaluation.

It proves that a bounded harness variant can be represented, reconstructed,
executed through typed hooks, changed through an explicit manifest, evaluated
through deterministic gates, promoted when low-risk, retained for approval
when high-risk, and rolled back to an exact parent hash. It does **not** prove
HarnessX's model co-evolution results or improve the currently running
library-scale benchmark.

## Implemented paper-derived contracts

| Hook | Typed event | Permitted mutation | Default processor |
|---|---|---|---|
| `task_start` | `TaskStartEvent` | system prompt | append bounded policy text |
| `step_start` | `StepStartEvent` | history | retain a bounded recent window |
| `before_model` | `BeforeModelEvent` | last user content, appended message | cap model input content |
| `after_model` | `ModelResponseEvent` | response content, tool calls | filter tool calls through an allowlist |
| `before_tool` | `ToolCallEvent` | tool input, approval flag | apply selective tool approval |
| `after_tool` | `ToolResultEvent` | tool result | cap returned tool content |
| `step_end` | `StepEndEvent` | none | immutable audit observation |
| `task_end` | `TaskEndEvent` | none | immutable audit observation |

Processor execution is asynchronous and records one of four first-class
outcomes: pass-through, transform, split, or intercept. `HarnessXInterrupt` is
kept separate from processor failure so the agent loop can distinguish an
intentional control signal from a crash.

Composition supports processor order, soft dependencies, and global singleton
groups. The runtime rejects dependency cycles, duplicate names, same-group
conflicts, cross-hook singleton conflicts, event type drift, unauthorized field
mutation, event re-identification, excessive split fanout, excessive event
counts, oversized events, and processor timeouts.

## Variant and change governance

A `HarnessXVariantSpec` is JSON-serializable and content-hashable. Processor
code is never loaded from a manifest: reconstruction uses an explicit local
registry, and the factory's rebuilt manifest must exactly match the requested
entry.

Every `HarnessXChangeManifest` binds:

- the exact parent variant hash;
- the exact rollback variant hash;
- evidence trace IDs;
- expected improvement and regression task IDs;
- one or more typed insert, replace, or remove edits;
- a `D1..D9` change dimension;
- a low, medium, high, or critical risk tier.

The shipping gate checks manifest completeness, lineage, exact
candidate-to-manifest binding, smoke-test status, and a strict seesaw
non-regression set. The approval policy is selective:

- low-risk, reversible, regression-free edits may promote automatically;
- high/critical-risk edits require user approval;
- declared regressions require approval;
- repeated same-dimension changes escalate to approval to limit cumulative
  sub-threshold drift;
- any deterministic failure retains the exact parent variant.

This matches Merlin's product rule: routine bounded maintenance can be
autonomous, while destructive, persistent, policy-elevating, or otherwise
high-risk changes stop for permission.

## Security and reliability hardening

- Only registered processor implementations can be reconstructed.
- The runtime deep-copies caller events and processor outputs, preventing
  in-place mutation from escaping validation.
- Hook-specific mutation allowlists fail closed.
- Tool arguments and tool-call events require JSON objects, not merely valid
  JSON values.
- Tool allowlists reject scalar strings, duplicate values, empty values, and
  non-string members.
- Event size, processor-config size, change-manifest size, split fanout,
  pipeline size, and processor execution time are bounded.
- JSON metadata/configuration rejects cyclic containers, nesting beyond 64
  levels, and more than 65,536 values before canonical serialization.
- Candidate metadata binds the exact change-manifest hash and rollback hash.
- Previously passing tasks must all be present and remain passing before
  promotion.

These are targeted code hardening checks, not an exhaustive Codex Security
workspace scan. The latter requires an interactive desktop Start action and
was not used as evidence for this implementation.

## Reproduction

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  python3 -m experiments.mvp.run_harnessx_typed_runtime_demo

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_harnessx_runtime \
  tests.test_harnessx_typed_runtime_demo \
  tests.test_harness_policy_evaluation
```

The deterministic demonstration writes
`experiments/mvp/results/harnessx_typed_runtime_v1/harnessx_typed_runtime.json`.
Its report must show `8/8` hook coverage, automatic promotion for the low-risk
reversible edit, and `approval_required_parent_retained` for the high-risk
tool-policy edit.

## Provider-backed chat shadow bridge

The terminal chat entrypoint now attaches the typed runtime to each actual
provider-backed turn through `src/merlin_harness/harnessx_chat_shadow.py`. Every
completed turn emits the six hooks observable at the chat-session boundary:
`task_start`, `step_start`, `before_model`, `after_model`, `step_end`, and
`task_end`.

The Codex CLI bridge additionally reopens the retained JSONL and strictly pairs
each `item.started` / `item.completed` record whose item type is
`command_execution`. A complete pair replays `before_tool` and `after_tool`
through the typed runtime. Missing starts, incomplete commands, changed command
text, duplicate IDs or JSON keys, inconsistent status/exit codes, trace hash
drift, and size/count overflow fail closed.

This is post-execution shadow replay, not pre-execution control. Commands have
already run by the time the retained provider trace is parsed. The report must
therefore say `replayed_after_provider_execution=true` and
`pre_execution_control_available=false`. Processor outputs are evaluated and
hashed but never replace the live provider prompt, command, tool result, or
user-visible answer.

Command text and tool output are not copied into the HarnessX envelope. It
retains only hashes, character counts, paired event indexes, status, exit code,
and a hash of the content-redacted observation list.

Each successful turn retains a new-only
`harnessx-turn-NNNN.shadow.json` envelope. The chat turn ledger binds its file
hash and semantic report hash. The lifecycle reader verifies:

- exact turn, answer, and raw-provider-trace bindings;
- the six boundary hooks plus every strictly paired command-tool hook;
- independent reconstruction of the same redacted command observations from
  the bound raw provider trace;
- per-processor outcome, output count, interception, and change counts;
- a fail-closed claim boundary forbidding provider-native invocation,
  synthesized or pre-execution tool-control claims, applied candidate output,
  or promoted-harness claims.

Provider failures retain a content-redacted five-hook failure envelope with
only the exception class. A failed provider turn still produces no completed
turn ledger.

## Live pre-execution Codex adapter

The optional `--harnessx-live-hooks` path now uses Codex's supported
`PreToolUse` and `PostToolUse` boundary. This is an external adapter, not a
replacement type system:

```text
Codex PreToolUse JSON
  -> ToolCallEvent(before_tool)
  -> reconstructed HarnessX processor pipeline
  -> pass-through => allow
  -> intercept    => deny before execution
```

The stored live contract follows the paper rather than saving a standalone
command allowlist:

- `H=(M,C)` keeps the requested main-model binding separate from the harness;
- `C=(P,S)` stores a hashable `HarnessXVariantSpec`;
- `P(before_tool)` contains the registered
  `ExactToolInputPolicyProcessor`;
- `S` names the tool registry, tracer, workspace, sandbox provider, and plugin
  list;
- the variant declares D4 tool-ecosystem, D7 control/safety, and D8
  observability scope;
- reconstruction uses the explicit processor registry and requires the rebuilt
  manifest to match exactly.

The current canary policy admits only exact `pwd` and `/bin/pwd` Bash inputs
and intercepts every other Bash input plus `apply_patch`. It is intentionally a
small enforcement proof, not the final user policy.

The audit is append-only and hash-chained. It stores decision, variant and
policy hashes, processor outcome, event identifier hashes, command and response
hashes, and bounded character counts. It does not copy raw command or tool
response text.

The actual account-auth canary used Codex CLI `0.146.0-alpha.3` with requested
`gpt-5.6-terra`, low effort. It observed:

- `pwd`: pre-hook pass-through, allow, then one post-hook observation;
- `touch harnessx-blocked.txt`: pre-hook intercept and deny;
- no post-hook record for the denied call;
- no `harnessx-blocked.txt` file after the turn;
- three valid chained audit records;
- harness configuration SHA-256
  `094e7aed4a06c328d427ef8546e96405b7c9fa0949da6a89c80eb3154dff96b4`.

Reproduce the model-bearing canary with the already authenticated local Codex
CLI:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m \
  experiments.mvp.run_harnessx_live_hook_canary
```

This makes one real provider turn and must not be included in model-free test
counts.

## First typed live-policy evolution round

`src/merlin_harness/harnessx_policy_evolution.py` closes the next bounded loop:

```text
parent trace
  -> typed D4 replacement manifest
  -> isolated candidate
  -> same verifier
  -> regression rejection and exact parent rollback
  -> corrected candidate
  -> promotion
  -> exact promoted variant loaded by the Codex hook adapter
```

The frozen six-case verifier contains two prior read cases, one target
directory-list case, and three write/compound/patch negative cases. The parent
allows only `pwd` and `/bin/pwd`, so the target fails. The first candidate
allows only `ls -1`; it fixes the target but regresses the two prior read cases
and is rejected by the seesaw gate. The corrected candidate allows the two
prior commands plus exact `ls -1`; all six cases pass and its medium-risk D4
replacement promotes without widening to arbitrary shell parsing.

The independent artifact validator checks report hashing, parent and promoted
variant hashes, exact resolved/promoted equality, lineage, promotion
acceptance, regression rejection, and same-verifier use.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m \
  experiments.mvp.run_harnessx_live_policy_evolution
```

Observed artifact bindings:

- parent variant:
  `cb063379ee75a72bbe1bb0f357baa737a366ca664c7f134ffdb18524fe839b72`;
- rejected candidate resolution: `candidate_rejected_rollback_parent`;
- promoted candidate resolution: `candidate_harness_promoted`;
- resolved variant:
  `07f021483461b753e82346adc992e6ae43be5eff68a70bbc73ff0265ed693a81`;
- evolution evidence:
  `e903726aee61d7bac3503771328bdc3b3424ad0a8bc1c62a3b8153bd2b8d7436`.

The actual follow-up Codex canary loaded the exact resolved SHA. It observed
`pre allow -> post observe -> pre deny`: the newly admitted `ls -1` executed,
the write attempt had no post event, and the target file remained absent.
`experiments/mvp/results/harnessx_live_policy_evolution_v1/` retains the
model-free variants, report, and a bounded live-canary summary.

## Bounded AEGIS controller

`src/merlin_harness/harnessx_aegis.py` implements the paper-derived control sequence
without transferring shipping authority to a language model:

```text
shared initial trace + current parent harness
  -> Digester strict artifact
  -> Planner strict artifact
  -> Evolver bounded typed proposals
  -> local typed builder
  -> isolated candidate variants + same verifier
  -> Critic strict artifact
  -> optional one revision
  -> deterministic gate
  -> promote candidate or retain exact parent
```

The four roles can be backed by a deterministic scripted agent or by separate
account-auth Codex CLI calls. Provider-backed stages use an ephemeral empty
workspace, `read-only` sandbox, ignored user/project configuration, strict JSON
output schema, and a retained raw JSONL trace. Any provider item other than
reasoning or the final agent message fails closed, so the roles cannot use
tools.

The action space is deliberately narrower than full HarnessX AEGIS. The model
may propose one or two exact D4 configuration changes from a local allowlist.
It cannot emit Python, register processors, write variants, choose arbitrary
commands, or authorize promotion. The local builder owns manifests and
variants. The same six-case verifier evaluates every candidate, and
`gate_harnessx_candidate` owns the final seesaw regression decision.

Each candidate attempt is isolated under `candidate-attempts/attempt-N/` and
retains:

- the model/script proposal;
- the locally constructed typed change manifest;
- the canonical candidate variant;
- the same-verifier evaluation.

The final trace store binds every stage artifact hash, attempt count, gate
record, and resolved variant. `validate_harnessx_aegis_round` independently
reconstructs both candidate attempts from the saved model artifacts, reruns
the verifier and deterministic gate, and requires the reconstructed resolved
variant to equal the saved one. A scripted adversarial test makes the Critic
rank a regressing candidate as `ship`; the deterministic gate rejects it and
retains the exact parent.

Reproduce the provider-free round:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m \
  experiments.mvp.run_harnessx_aegis
```

Observed model-free bindings:

- stages: `digester, planner, evolver, critic`;
- provider calls: `0`;
- independent replay: valid;
- resolved variant:
  `eae32742a15deb44b7fe6de074906ce7f579a27ed22fd749396e5e75deca265c`;
- evidence:
  `4c1193bbee7a09fbbf65d3b3f00d9cf157e3414ebdf9f1a8b45a8f8272d0ffc7`.

After explicit approval to export the bounded verifier/policy payload, the
Codex-backed command completed one four-role account-auth round:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m \
  experiments.mvp.run_harnessx_aegis \
  --mode codex \
  --output experiments/mvp/results/harnessx_aegis_codex_v3
```

Observed provider-backed bindings:

- Codex CLI: `0.146.0-alpha.3`;
- requested model/effort: `gpt-5.6-terra` / `low`;
- provider-resolved model IDs: absent;
- stage calls: `4`;
- revision used: no;
- provider item types: four `agent_message` items and no tool items;
- total reported usage: 61,546 input, 525 output, and 139 reasoning tokens;
- Evolver proposal: add exact `ls -1`, remove nothing;
- same verifier: `6/6`;
- deterministic gate checks: `7/7`;
- resolution: `candidate_harness_promoted`;
- resolved variant:
  `b2251586c2bbb8cdd0358b8fa15fea452e56560b6e6e083a0780c8b4bf26851b`;
- independent replay checks: `13/13`;
- evidence:
  `b09d61741347fe8e51e602ddffb1543ddd795dca33409e3e29fc3211e3c768d8`.

Two non-promoting setup attempts are retained rather than overwritten. `v1`
failed before a model response because stage paths were relative to the empty
workspace. `v2` completed Digester, then the provider rejected the Planner
schema because `uniqueItems` is unsupported. Stage paths are now absolute, and
duplicate rejection remains in the local typed validator rather than relying
on provider schema support.

This proves a bounded provider-backed AEGIS control round and deterministic
shipping separation. It does not prove provider-resolved model identity,
automatic processor-code evolution, open-ended/full-paper AEGIS, or model
co-evolution.

The follow-up frozen-50 campaign generalizes this path from a hard-coded
six-case verifier to a suite ID/hash contract. The suite has 50 policy-verifier
tasks across nine categories; the parent passes `49/50` and the promoted
candidate passes `50/50`. One account-auth four-role round completed without
provider tool use and passed all 14 independent replay bindings. See
`docs/harnessx-aegis-frozen-50-v1.md` for the taxonomy, hashes, usage, and
claim boundary.

## Remaining gap

The shadow bridge still remains post-execution evidence. The new live adapter
can stop only the Codex tool families matched by its hook configuration. Codex
specialized or hosted tools that do not emit these hook events are outside this
boundary; universal mediation would require a narrower tool surface or owned
tool proxy. Transform and split remain runtime capabilities but are not yet
mapped into the live Codex adapter. Other tool item families must not be
inferred from Bash/apply_patch coverage.

The older `harness.py` runtime and the newer typed runtime remain separate
compatibility layers and should be consolidated behind one adapter contract.

The bounded four-role controller and one provider-backed round are complete,
but open-ended action generation, model-written processor code, cross-harness
GRPO, and large-scale harness-policy evolution remain deferred. Those
capabilities require a new frozen evaluation; they must not be inferred from
the deterministic local demonstration, the chat shadow envelope, the bounded
AEGIS round, or the separate 435-cell skill-library run.
