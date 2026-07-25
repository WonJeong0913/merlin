# Merlin Personal Workload 50 — Longitudinal Protocol v1

Status date: 2026-07-24  
State: **frozen protocol; execution not started**

## 1. Purpose

This campaign is the first field test of the claim that verified savings from
skill reuse can fund bounded skill-harness governance and evolution.

It does not test whether a deterministic governance fixture passes. It tests
whether Merlin improves real work under a matched account-auth comparison
while retaining success, invocation, lifecycle, regression, and governance
evidence.

## 2. Frozen workload

The manifest contains 50 operator-workload contracts grounded in active
projects:

| Family | Tasks |
|---|---:|
| Merlin research and documentation | 12 |
| Merlin engineering and experiments | 10 |
| Figures and presentations | 8 |
| Apple app build and debugging | 8 |
| ML and data experiments | 6 |
| Backend and automation | 6 |
| **Total** | **50** |

Freezing a contract is not completing a task. At freeze time:

- completed task executions: `0`;
- matched observations: `0`;
- lifecycle changes: `0`;
- verified direct savings: `0`;
- `G/S`: unavailable;
- Level 7: not achieved.

## 3. Matched design

Each task has two scheduled repetitions:

1. phase 1: 50 baseline/managed pairs;
2. phase 2: the same 50 contracts with the arm order reversed.

The 100-pair schedule uses a balanced two-period crossover. Every pair requires
the same provider, model, effort, verifier epoch, and clean input snapshot.
Carryover between arms is prohibited.

The baseline is the same agent without Merlin managed skill-harness
assistance. The managed arm uses Merlin provisioning, lifecycle, and HarnessX
controls. This is an account-auth experiment and requires no API billing.
Low-cost-model comparison is excluded.

## 4. Evidence contract

A record is countable only when both arms contain:

- the frozen task and verifier contract hashes;
- verifier success or failure;
- provider execution turns;
- trace and output hashes;
- complete actual-invocation evidence, including evidence that no skill was
  invoked when that is the observed state;
- optional token and latency diagnostics;
- governance turns;
- selection and invocation errors;
- lifecycle action IDs and kinds;
- required human review for semantic, visual, Apple-runtime, and automation
  tasks.

The observation ledger is append-only and SHA-256 chained. Duplicate
observation IDs, duplicate scheduled pairs, chain drift, manifest drift,
missing human review, and incomplete actual-invocation evidence fail closed.

## 5. Primary outputs

- matched verifier pass rate;
- verified provider-turn savings;
- governance provider turns;
- turn-based `G/S`;
- selection and invocation error rates;
- promotions, rollbacks, repairs, hides, retirements, and harness updates;
- regression count;
- bounded reinvestment authorization.

`G/S` remains null until there is verified direct savings from matched
successful arms. A cheaper failed arm cannot create spendable savings.

## 6. Level 7 gate

Field-validated research beta requires all of:

- all 50 unique task contracts observed;
- at least 14 elapsed days;
- 100 matched observations;
- at least 10 distinct lifecycle changes;
- at least one real promotion;
- at least one real rollback;
- complete actual-invocation evidence;
- matched baseline evidence;
- a computable `G/S` or turn-based equivalent.

The summary derives this gate from the ledger; it is not a manually editable
status.

## 7. Artifacts and commands

Frozen artifacts:

- `experiments/mvp/results/merlin_personal_workload_50_longitudinal_v1/manifest.json`
- `experiments/mvp/results/merlin_personal_workload_50_longitudinal_v1/schedule.json`
- `experiments/mvp/results/merlin_personal_workload_50_longitudinal_v1/observations.jsonl`
- `experiments/mvp/results/merlin_personal_workload_50_longitudinal_v1/summary.json`

Manifest SHA-256:

```text
a82244246c0cfa2a3f125805119bf753fa7508e5e25afb12c78d430e857fe46c
```

Schedule SHA-256:

```text
d4e1aa3654eb6aa3bfdb078d3f968e828b4d554af3ef62b0f134028f391fa037
```

Create a new campaign directory:

```bash
PYTHONPATH=. python3 -m experiments.mvp.run_personal_workload_campaign \
  --output experiments/mvp/results/merlin_personal_workload_50_longitudinal_v1
```

Append one already completed matched observation:

```bash
PYTHONPATH=. python3 -m experiments.mvp.record_personal_workload_observation \
  --campaign experiments/mvp/results/merlin_personal_workload_50_longitudinal_v1 \
  --observation /path/to/bounded-observation.json
```

The append command does not run a provider. It accepts only a completed,
bounded observation artifact and refreshes the derived summary.

## 8. Current verdict

The Level 7 protocol and evidence substrate now exist. The field claim does
not. The next valid progress is the first real scheduled matched pair, not
another simulated success record.

## 9. Pre-migration pilot boundary

A two-pair account-auth pilot existed in the legacy project and correctly
failed closed on incomplete provider-native invocation evidence. Its report
hash belongs to the preserved legacy archive and is not a Merlin result.

The Merlin pilot must be rerun from this package after a trusted skill-body
load/invocation event is implemented.
