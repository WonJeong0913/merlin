# Self-managing harness v2

## Scope

This local milestone moves Merlin from isolated lifecycle functions toward a
single evidence-routed management substrate. It prioritizes harness governance
and evolution; low-cost model comparison is explicitly outside this campaign.

Implemented flow:

```text
live PreToolUse audit or completed shadow trace
  -> hash-bound trace ingestion
  -> failure ownership decision
     -> harness false deny: bounded AEGIS
     -> skill-local failure: copy-on-write skill repair lane
     -> route-local failure: provisioning repair lane
     -> hidden + unused + complete windows: retirement lane
     -> safety false allow: human review
  -> same-verifier deterministic gate
  -> promote, retain parent, or rollback
  -> append longitudinal observation
```

## Multi-round AEGIS

`harnessx_aegis.py` now supports an explicit action space, multiple candidates
per round, an arbitrary current parent variant, and a bounded campaign of
monotonic verified transitions.

The frozen model-free campaign uses:

- suite: `live-policy-multitarget-50-v1`;
- suite SHA-256:
  `3de80f97f6e573a71db9f197fe83ca05b72e81fb00aab3f0da2d1086e1580e53`;
- action-space SHA-256:
  `63cf3bf2fe16d97561fcf5efef7d2835279942da220e606bdb9257cc7152b844`;
- failures: `3 -> 2 -> 1 -> 0`;
- rounds: `3`;
- provider calls: `0`;
- final verifier result: `50/50`;
- final variant SHA-256:
  `edd283773faa2ab4965ed2daf7a6cecb3b5d1764821bf1a5338217640ca4e85c`;
- campaign evidence SHA-256:
  `b32167246d809d6aa7e4bc02065e1550e81302f04ac096b2af74a6a40eec0370`.

Every saved round is replayed against the same suite and action-space hashes.
A round is accepted only when its verified failure set strictly decreases.

The first account-auth multi-round attempt exposed a Critic contract bug and
did not promote. The corrected retry was blocked by the tenant security policy
before execution. It is therefore not reported as completed provider-backed
multi-round evidence.

## Automatic trace ingestion and controller

`harnessx_trace_ingestion.py` validates the complete live-hook audit hash chain,
matches only frozen `(tool name, command hash, command length)` tuples, and
never stores raw commands. A trace-backed false deny may nominate AEGIS, but a
false allow always stops for human review. Completed chat shadow reports remain
post-execution observations and cannot nominate a policy change alone.

`self_managing_controller.py` owns failure routing:

- `HARNESS_EVOLVE`
- `SKILL_REPAIR`
- `PROVISIONING_REPAIR`
- `SKILL_RETIRE`
- `HUMAN_REVIEW`
- `OBSERVE`

Retirement requires an already-hidden skill, at least two independent complete
invocation windows, and a trusted verifier. Registered executors must return
the lane-specific boolean result (`promoted`, `adopted`, `applied`, or
`retired`) before the controller accepts the result shape.

## Exact multi-tool pre-execution mediation

`ExactToolCallPolicyProcessor` generalizes the original exact Bash-command
processor to multiple tools. It admits only an exact canonical JSON
`(tool_name, tool_input)` pair. Unknown tools, changed paths/patterns, composed
commands, and explicit write/edit classes are intercepted.

The live adapter can reconstruct either the legacy exact-command processor or
the new registered exact-call processor from a hash-bound variant. This is
tested locally for exact `Read`, `Grep`, `Glob`, and `Bash` calls plus denied
write/unknown cases. It is not a claim that every Codex-hosted tool has been
canaried against a live provider.

## Frozen 50-task governance campaign

The new `self-managing-governance-50-v1` campaign contains 50 independent
deterministic management tasks that invoke production modules:

| Family | Tasks |
|---|---:|
| Skill/provisioning lifecycle routing | 10 |
| Central controller dispatch | 6 |
| Verifier-upgrade gate | 8 |
| Account-auth resource reinvestment | 8 |
| Exact multi-tool mediation | 10 |
| Trace-to-harness action routing | 8 |
| **Total** | **50** |

Observed result:

- `50/50` passed;
- suite SHA-256:
  `1cbcb7a85a6af4167070e0d5d21e992de974de212c090788ddc934666b377595`;
- evidence SHA-256:
  `105df93bc42c809f94f4d5d5ac7f656f0538975d85da86a01be5464d60baf264`;
- independent replay: valid;
- provider/model calls: `0`.

These are actual governance-code tasks, not SkillsBench tasks and not 50
provider-model prompts.

Reproduce:

```bash
PYTHONPATH=. python3 -m experiments.mvp.run_self_managing_50_campaign
```

## Longitudinal evolution ledger

`harness_evolution_ledger.py` adds an append-only hash-chained JSONL record for:

- candidate and promotion counts;
- rollback counts;
- previously-passing regression exposure and regression counts;
- verifier epoch and suite hash;
- parent/resolved state hashes;
- governance spend;
- independently evidenced direct savings.

Promotion, rollback, and regression rates remain reportable independently of
economics. `G/S` is available only when one window has the same verifier epoch,
suite, resource unit, resource dimension, and accounting window, and the
denominator contains independently hashed direct-savings evidence. Mixed
dimensions or zero savings return `null`, not infinity or a compound-growth
claim.

The current three-round model-free ledger records:

- promotions: `3/3`;
- rollbacks: `0`;
- regression exposure: `144`;
- regressions: `0`;
- G/S: unavailable because verified direct savings were not observed.

This is the correct bounded result: it proves the measurement path, not the
economic flywheel.

## Evidence boundary

This milestone does not claim:

- full HarnessX or Self-Harness reproduction;
- model-written processor code;
- provider-backed multi-round AEGIS;
- universal live tool mediation;
- longitudinal personal-workload savings;
- a measured `G/S < 1` compounding result;
- a low-cost model comparison.
