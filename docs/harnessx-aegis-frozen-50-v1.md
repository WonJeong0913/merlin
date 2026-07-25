# HarnessX AEGIS frozen-50 verifier v1

## Scope

This campaign expands the bounded AEGIS live-policy slice from six verifier
cases to 50 frozen policy-verifier tasks. It is not a 50-task SkillsBench run,
50 independent user tasks, full HarnessX AEGIS, or model co-evolution.

The contribution is a wider deterministic non-regression surface around one
trace-supported D4 policy improvement. The language-model roles still cannot
write processor code or authorize shipping.

## Frozen suite

Suite ID: `live-policy-frozen-50-v1`

Suite SHA-256:
`d5473a4320104c12fa9cf005f015181abdbd9ab5d8bc2affaeabe630f2dbe8e8`

| Category | Tasks | Purpose |
|---|---:|---|
| Prior allow | 2 | Preserve exact `pwd` and `/bin/pwd` behavior |
| Target allow | 1 | Repair the observed exact `ls -1` false deny |
| Filesystem mutation | 17 | Reject writes, destructive operations, and redirection |
| Shell composition | 12 | Reject chaining, substitution, pipes, environment prefixes, and widened forms |
| Network | 4 | Reject network-capable shell commands |
| Package install | 3 | Reject dependency installation |
| Process control | 2 | Reject privilege/process mutation |
| Git mutation | 4 | Reject staging, committing, pushing, and checkout mutation |
| Non-Bash tool | 5 | Reject patch/write/edit/notebook/computer tool classes |
| **Total** | **50** | **3 expected allows, 47 expected denials** |

The suite is declared in
`src/merlin_harness/harnessx_verifier_suites.py`. The suite hash covers every case
ID, tool name, exact command, expected decision, and category. Runtime evidence
stores the suite ID/hash and redacted evaluation records; the account-auth
model input receives case IDs, tool names, command hashes/lengths, and
expected/observed outcomes rather than the new raw command strings.

## Suite-bound AEGIS contract

`run_harnessx_aegis_round` now accepts a typed `ToolPolicyVerifierSuite`.
The initial trace, final trace store, report, candidate archive, and replay
validator bind:

- suite ID and SHA-256;
- task count and category counts;
- exact parent evaluation;
- every stage artifact hash;
- every candidate's same-suite evaluation;
- deterministic gate decision;
- resolved variant.

`validate_harnessx_aegis_round` resolves only a locally registered suite,
recomputes the parent and candidate evaluations, reconstructs each typed
candidate, reruns the gate, and requires exact resolved-variant equality.
Legacy six-case evidence remains replayable.

## Model-free result

Artifact:
`experiments/mvp/results/harnessx_aegis_scripted_50_v1/`

- provider calls: `0`;
- parent: `49/50`;
- actionable failure: `directory-list-read`;
- candidate: `50/50`;
- promoted: yes;
- independent replay: valid;
- resolved variant SHA-256:
  `663c6280c3258f90601d6875bbddf581b433da661f9da872249805e24744757b`;
- evidence SHA-256:
  `c0fa249ba0e59ddf32079986d880705776d2d154aab5f209605a336da631d2e5`.

## Account-auth result

Artifact:
`experiments/mvp/results/harnessx_aegis_codex_50_v1/`

- Codex CLI: `0.146.0-alpha.3`;
- requested model/effort: `gpt-5.6-terra` / `low`;
- provider-resolved model ID: absent;
- stage calls: `4`;
- provider item types: four `agent_message` items, no tool items;
- revision used: no;
- total reported usage: 70,979 input, 766 output, 349 reasoning tokens;
- Digester: one D4 false deny;
- Planner: bounded `add_exact_command`;
- Evolver: add exact `ls -1`, remove nothing;
- Critic: evidence-supported `ship`;
- parent: `49/50`;
- candidate: `50/50`;
- deterministic gate checks: `7/7`;
- independent replay bindings: `14/14`;
- resolved variant:
  `aegis-add-ls-1-directory-read`;
- resolved variant SHA-256:
  `d3ed4a099a6f341780005329558f9a6cc850e11febaf04554c2439066030d3e6`;
- evidence SHA-256:
  `443e75a872f610a9aa826e048a2a0f8b32ede23db8bbf6541aeab7e25151b6d1`.

## Reproduction

Model-free:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m \
  experiments.mvp.run_harnessx_aegis \
  --suite frozen-50 \
  --output experiments/mvp/results/harnessx_aegis_scripted_50_v1
```

Account-auth, after bounded-data export approval:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m \
  experiments.mvp.run_harnessx_aegis \
  --mode codex \
  --suite frozen-50 \
  --output experiments/mvp/results/harnessx_aegis_codex_50_v1
```

## Evidence boundary

This result proves one provider-backed, suite-bound, bounded AEGIS policy
repair with a 50-task deterministic regression surface. It does not establish
provider-resolved model identity, arbitrary shell safety, universal Codex tool
mediation, open-ended harness edits, automatic processor-code generation,
large-scale longitudinal harness evolution, or full-paper model co-evolution.
