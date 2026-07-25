# MVP Experiment

Goal: test whether Merlin's managed skill harness can reduce failures from naive generated-skill accumulation.

## Merlin 앱: Control Room

승인된 핑크·라일락 리퀴드 글래스 플라워 마크를 사용하는 로컬 전용
`Merlin Control Room`입니다. 현재 50-task 종단 캠페인의 검증된 manifest와
스케줄 상태를 읽어 보여 주고, 별도로 결정론적 lifecycle recovery sandbox를
직접 조작할 수 있습니다.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m experiments.mvp.run_console --open
```

앱은 `127.0.0.1`만 수신하며 외부 네트워크, provider 호출, 계정 인증을 하지
않습니다. 표시되는 `0 / 100 observations`와 `Level 7 not yet qualified`는 실제
Merlin campaign artifact의 현 상태이며, sandbox의 성공 결과는 provider-native
성능 주장으로 사용하지 않습니다.

## Managed Library Loop v1

The first post-Build-Week integration preflight composes the existing
controlled lifecycle recovery, frozen `M0/M1/M2-H/M2-K` management comparison,
and eight-hook HarnessX runtime into one hash-bound, new-only evidence envelope:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m experiments.mvp.run_managed_library_loop \
  --output /private/tmp/merlin-managed-library-loop-v1
```

Validate the persisted report, all three component hashes, path containment,
readiness state, and the zero-call/account-auth safety contract without
executing or modifying the run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m experiments.mvp.run_managed_library_loop \
  --verify-existing /private/tmp/merlin-managed-library-loop-v1
```

This command performs zero provider/API calls and reads no credential. It
creates an account-auth canary contract for a later maximum-two-turn matched
run, but does not execute that canary. Subscription-backed experiments account
for provider turns, reported tokens, latency, retries, and quota window—not an
invented per-task USD price.

The three component lanes remain explicitly separate controlled fixtures. The
envelope proves integration readiness, not one shared causal trajectory or
provider-native skill invocation.

## Conditions

```text
C0: no skills
C1: naive generated skill accumulation
C2: validation gate only
C3: validation + task-conditioned provisioning
C4: validation + provisioning + lifecycle actions
```

## Initial Task Requirements

Use tasks with deterministic verifiers. Each task should have:

- task id
- natural-language instruction
- expected artifact or answer
- verifier command or Python verifier
- allowed tools
- optional oracle skill ids
- regression group

Task classification follows the SkillsBench-style taxonomy plus Merlin extensions:

```text
domain
capability
difficulty
skill_dependency
shadowing_role
mvp_tier
```

## Run Record

Each run must store:

```text
task_id
condition
library_snapshot_id
provisioned_skill_ids
selected_skill_ids
trace_events
success
score
cost
latency
failure_label
```

## Metrics

Task metrics:

```text
success_rate
normalized_gain
cost
latency
```

Provisioning and selection metrics:

```text
clean_oracle_invocation_rate
shadowing_rate
no_skill_when_oracle_rate
distractor_invocation_rate
```

Lifecycle metrics:

```text
candidate_count
adopted_count
rejected_count
hidden_count
retired_count
regression_failure_rate
repair_success_rate
```

## First CTA-Lite Signals

Start simple:

- new files created only by with-skill run
- validation commands run or skipped
- selected skill vs oracle skill
- token/tool cost ratio
- final success delta
- repeated distractor invocation
- premature stop after following skill procedure

## First Lifecycle Rules

```text
candidate -> active:
  passes target verifier and regression gate

active -> hidden:
  repeated shadowing or high cost with no gain

hidden -> active:
  repaired and passes regression gate

active/hidden -> retired:
  duplicate, obsolete, or repeatedly harmful

candidate -> rejected:
  fails validation or copies task-specific answer
```

## Current Implementation Skeleton

The first implementation layer now exists under `src/merlin_harness/`.

Implemented:

- task/verifier dataclasses
- exact-match, file-exists, and command verifier runner
- file-backed skill library
- file-backed trace store
- lexical top-k provisioner
- AIP-lite structure gate
- candidate lifecycle decision
- Self-Harness-style policy gate
- CTA-lite trace delta
- seeded-skill runner
- controlled distractor pool for first shadowing measurements

Next:

1. Expand the deterministic corpus beyond smoke tasks.
2. Add lifecycle/policy intervention experiments after shadowing is observed.
3. Add LLM-generated candidates behind the existing gates.

## Current Task Corpus

Seed tasks live in `experiments/mvp/tasks/`.

Current files:

- `answer-yes.json`
- `count-errors.json`
- `count-items.json`
- `count-records.json`
- `create-audit-log.json`
- `create-notes-file.json`
- `create-output-json.json`
- `create-report-md.json`
- `create-result-file.json`
- `summarize-lines.json`

All current seed tasks are classified as `SkillsBench-style`; they are smoke-tier tasks, not final benchmark claims.

Run the no-skill baseline:

```bash
python3 -m src.merlin_harness.runner \
  --tasks experiments/mvp/tasks \
  --workspaces experiments/mvp/workspaces/no_skill \
  --traces experiments/mvp/runs/no_skill
```

Run the seeded library-scaling experiment:

```bash
python3 experiments/mvp/run_library_scaling.py
```

Current key signal:

```text
oracle-only: pi_o=1.00, pi_m=0.00, pass=0.90
controlled:  pi_o=0.11, pi_m=0.89, pass=0.10
```

The controlled condition is intentionally adversarial. Its purpose is to create
a small, reproducible shadowing failure before testing lifecycle or harness
policy recovery.
