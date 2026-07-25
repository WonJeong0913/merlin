> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/competitor-structure-reference.md`

---

# Competitor Structure Reference

Created: 2026-07-07

Purpose: use competitor and related-system structures as decomposition references, not as templates to copy.

## Reference Rule

The KING should not be framed as "Hermes with a patch." Use each system to identify one module boundary, then keep The KING's contribution centered on skill-harness management:

```text
generation -> validation -> provisioning -> selection -> monitoring -> lifecycle -> harness policy gate
```

## Structure Map

| System | Observed Structure | What To Borrow | What To Avoid |
|---|---|---|---|
| Hermes Agent v0.18 | Built-in learning loop, `/learn`, `/journey`, cheaper post-turn self-improvement review, completion contracts, verification evidence, and a `pre_verify` hook. Source: https://github.com/NousResearch/hermes-agent/releases/tag/v2026.7.1 | Concrete baseline for visible, steerable self-improvement and verified task completion. | Do not claim novelty from skill creation, skill editing, learning history, or generic verification alone. |
| Hermes Curator | Usage telemetry; `active -> stale -> archived`; optional LLM patch/consolidation; pin, backup, rollback, and restore. Source: https://hermes-agent.nousresearch.com/docs/user-guide/features/curator | Strong lifecycle baseline and a direct reason to compare against usage/recency curation. | Do not equate inactivity with harmfulness, or a policy reimplementation with an exact Hermes system run. |
| Hermes Agent Self-Evolution | Reads current skill/prompt/tool, generates eval data, uses DSPy+GEPA, evaluates candidate variants, applies constraint gates, sends best variant to PR. Source: https://github.com/NousResearch/hermes-agent-self-evolution | Trace-informed variant generation plus tests/size/semantic gates. | Do not begin with evolutionary optimization; it is heavier than the MVP. |
| AIP | Skill as schema-validated execution graph with typed steps and local repair targets. | AIP-lite artifact schema. | Do not require full graph runtime in the first pass. |
| SkillRevise | Trace-conditioned diagnosis, repair principles, candidate re-execution, first verifier-passing selection. | Skill repair/adoption loop. | Do not ignore no-skill fallback or library-level shadowing. |
| Counterfactual Trace Auditing | Paired with-skill/without-skill traces and behavior-delta labels. | CTA-lite behavior monitor. | Do not make pass rate the only skill utility signal. |
| Self-Harness | Weakness mining, bounded harness proposals, held-in/held-out validation. | Conservative policy-update gate. | Do not allow arbitrary harness rewriting in MVP. |
| HarnessX | Typed hooks/processors, AEGIS loop, variant isolation, deterministic gates. | Hook/processor substrate, harness-change manifest, isolated candidate harness variants, and gated co-evolution path. | Do not start with GRPO or arbitrary code rewriting before deterministic gates exist. |

## The KING Decomposition

Divide the problem into seven separable layers:

1. Task and verifier layer
2. Trace layer
3. Skill artifact layer
4. Provisioning and selection layer
5. Validation and regression gate layer
6. Behavior monitoring and shadowing layer
7. Lifecycle and harness policy layer

Each layer must be testable without requiring the later layers.

## Novelty Boundary After Hermes v0.18

Hermes is no longer only a generation-and-memory reference. It is a real skill
lifecycle baseline. The KING must therefore establish a narrower and stronger
difference:

```text
Hermes-Curator public policy:
usage/recency telemetry -> stale/archive + optional LLM consolidation

The KING target:
paired task outcome + invocation category + shadowing + regression
-> skill-local or route-local diagnosis
-> lifecycle or harness-policy intervention
-> held-in/held-out promotion or rollback
```

Hermes v0.18 also records task-completion verification evidence. The defensible
gap is not "Hermes has no verification." It is that the public Curator
documentation does not establish per-skill counterfactual utility,
oracle/distractor invocation decomposition, or held-out promotion of routing
and processor variants. Treat that statement as a documentation-bounded
inference.

The required competitor-aware baseline is:

```text
M2-H: Hermes-Curator-inspired usage/recency policy
M2-K: The KING outcome/shadowing/regression policy
```

Use identical skill snapshots, tasks, model, verifier, and budget. Label `M2-H`
as a policy reimplementation unless the actual Hermes runtime is run under the
same contract.

## Today's Scope

Today should not attempt full agent generation. The first feasible slice is:

```text
task JSON
-> materialized workspace
-> deterministic verifier
-> no-skill trace record
-> trace store
```

Why this slice:

- It is the base for SkillsBench-style evaluation.
- It creates the no-skill counterfactual needed by CTA-lite.
- It gives SkillRevise-lite verifier feedback later.
- It gives lifecycle gates real evidence instead of prose plans.
