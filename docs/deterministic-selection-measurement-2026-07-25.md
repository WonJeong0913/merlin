# Deterministic selection measurement — 2026-07-25

Classification: experiment, result

First measurement of Merlin's selection layer against a skill library and an
oracle mapping it did not author.

```bash
python3 experiments/mvp/measure_deterministic_selection.py
```

## Run record

Every run writes a durable record to
`experiments/mvp/results/deterministic_selection_v1/` and refuses to overwrite
one, so a result can always be traced to the inputs that produced it.

| File | Contents |
|---|---|
| `queries.md` | **the exact query sent for every task**, next to its oracle, what was provisioned, and hit/miss — readable, for auditing whether each task was conveyed faithfully |
| `run.json` | the same plus per-candidate evidence: rank, positive/trigger/description/negative scores, anchor evidence, `is_oracle` |

Anchored by `oracle_manifest_sha256`, `library_snapshot_sha256`,
`library_skill_count`, `recorded_at_utc` and `python_version`.

Queries are stored **verbatim** here. That is safe only because they derive from
public SkillsBench task IDs and carry no user content. The production path does
the opposite on purpose — `GovernedProvisioningDecision.to_safe_dict` sets
`query_stored: False` and keeps only `query_sha256`. This record's behaviour must
not be copied into any path handling real user queries; the run record says so in
its own `query_storage_note`.

## What is and is not measured

- **Is**: `deterministic` routing — the lexical `GovernedProvisioner` — scoring
  209 SkillsBench skills against 87 task oracles.
- **Is not**: `semantic` routing. The shipped default pairs governed
  provisioning with a provider-backed `SemanticSkillRouter`; only the lexical
  half runs here. Nothing below is evidence about the router.
- **Is not**: task success. Nothing is executed. This is retrieval only.

## Ground truth

`curated_skill_variants` from `experiments/skillsbench/readiness-87.json`,
authored upstream. Verified as the oracle rather than assumed: for every task it
matches `per_task_skill_dirs` (`environment/skills/<name>`), the skills
SkillsBench ships inside the task environment, and
`create_library_scale_manifest.py:230` uses the same field as its reference set.
A separate `required_skills` field exists and is empty for 75 of 87 tasks; it is
not the oracle and is not used.

## Primary result — mechanical queries, n = 87

The query is the `task_id` with hyphens replaced by spaces. No human judgement
enters, so the arm is reproducible by construction and covers all 87 tasks.

| k | recall@k | precision@k |
|---:|---:|---:|
| 1 | **42 / 87** (48.3%) | 42 / 83 |
| 3 | **52 / 87** (59.8%) | 80 / 219 |
| 5 | 55 / 87 (63.2%) | 92 / 327 |
| 10 | 63 / 87 (72.4%) | 116 / 503 |

A 209-skill library gives a chance baseline near 0.5–2% per task depending on
oracle size, so lexical retrieval is doing real work: roughly half the tasks
surface an oracle skill in the single top slot.

Precision falls as k grows, as expected — at k=10 about three quarters of the
exposed slots are not oracle skills, and every one of those spends prompt
context. At k=1 only 83 slots are filled across 87 tasks: four queries provision
nothing rather than guess, which is the guard behaving correctly.

**The caveat that bounds this number**: the `task_id` is not the real task
prompt. The task corpus is not vendored here (only the skill library is), so no
run in this document uses SkillsBench's own prompts. A `task_id` is short and
keyword-dense, which may flatter lexical matching relative to a real request, or
penalise it by omitting context. The direction of that bias is not established.

## Secondary probe — handwritten queries, n = 6

Six `easy` tasks, k=3, two locally written queries each: `cued` names the
oracle's format or tool, `uncued` paraphrases the work without naming it.

| Arm | recall@3 | precision@3 |
|---|---|---|
| `cued` | 5 / 6 | 6 / 18 |
| `uncued` | 1 / 6 | 1 / 17 |

Four tasks flip from hit to miss, none flip the other way. **Exact two-sided
sign test: p = 0.125.** That does not reach significance at any conventional
threshold, and with n=6 it cannot: four concordant flips is the most extreme
result available and still only reaches 0.125.

So this probe is **suggestive and underpowered, not a finding**. It is
consistent with the lexical layer depending heavily on the query naming the
tool, which is what the mechanism would predict, but it does not establish it.
Both query sets were written by the same author who knew the oracle, which is
the exact bias a benchmark must not carry. Quote the mechanical arm instead.

## Adapter facts that bound the ceiling

- `skill_artifact_from_variant` sets `trigger = description`. All 209 skills
  have identical trigger and description text, and `governed_provisioning`
  scores `max(trigger_score, description_score)` — so the two positive-evidence
  channels carry one signal, not two.
- 8 of 209 skills have no sections in their `SKILL.md`; the adapter synthesizes
  a single `Follow SKILL.md` step. The structure gate then records
  `has_steps: passed` on a step the loader invented.
- `validate_aip_lite_skill` passes **209/209**. Not a quality signal: the
  adapter fills every field the gate checks, so for this corpus the structure
  gate cannot fail and discriminates nothing.

## Next

1. Measure `semantic` routing on the same 87 oracles. The gap between the two is
   the actual claim the harness wants to make, and only one half exists today.
2. Restore the task corpus so real prompts replace `task_id` strings. That
   removes the largest caveat above and needs a clone of
   `benchflow-ai/skillsbench @ 5433cf15`.
3. Execution-based scoring needs Docker; 87 of 87 tasks require it.
