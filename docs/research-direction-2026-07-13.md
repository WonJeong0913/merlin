> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/research-direction-2026-07-13.md`

---

# Research Direction Decision: 80% Harness Governance, 20% Skill Supply

Date: 2026-07-13

Classification: thesis, architecture, experiment, related-work, implementation

## Decision

The KING will allocate its primary research claim and experimental attention as:

```text
80%: evidence-driven skill-harness governance and harness evolution
20%: minimal contract-conformant skill supply
```

This is not a literal source-line ratio. It is a priority rule for claims,
ablation budget, implementation order, and paper space.

The main object of improvement is the harness state:

```text
H_t = (L_t, P_t, V_t, M_t)
```

Where:

- `L_t` is the versioned skill library and lifecycle state,
- `P_t` is provisioning and selection policy,
- `V_t` is validation and regression policy,
- `M_t` is the hook/processor manifest and its bounded configuration.

Skill supply remains necessary because The KING is a self-managing agent, but
the paper will not optimize or claim intrinsically superior skill prose. One
fixed Create-Skill-style path supplies a basic `SKILL.md`-centered contract
artifact. The harness decides whether, where, and for how long it is useful.
Diagnosing and repairing a contract, then promoting or rolling it back, is a
harness lifecycle operation rather than a separate skill-improvement track.

## Why Skill Improvement Is A Weak Main Thesis

### 1. The target is difficult to define independently

A skill can be clearer or more complete yet still reduce task success because
it is exposed to the wrong task, selected with a useful skill, or retained
after its environment changes. Text quality is not the same as deployed
utility.

### 2. Attribution is confounded

An observed gain from a revised skill combines at least four effects:

```text
candidate content
+ admission decision
+ task-conditioned exposure
+ invocation policy
```

If these change together, a better score cannot be attributed to skill-content
improvement alone.

### 3. The competitor baseline has moved

Hermes Agent now exposes explicit self-improvement and skill-management
surfaces. The latest checked tag is v0.18.2 (2026-07-07), a deployment patch;
the research-relevant feature release is v0.18.0 (2026-07-01). It added `/learn`, `/journey`, cheaper post-turn
self-improvement review, completion contracts, verification evidence, and a
`pre_verify` hook. Its Curator already tracks skill activity, moves skills
through `active -> stale -> archived`, supports optional LLM consolidation,
and provides backup, rollback, pin, and restore operations.

Therefore, "an agent creates, edits, and prunes its own skills" is no longer a
sufficient novelty claim.

Official references:

- Hermes v0.18.0 release: https://github.com/NousResearch/hermes-agent/releases/tag/v2026.7.1
- Hermes v0.18.2 latest checked patch: https://github.com/NousResearch/hermes-agent/releases/tag/v2026.7.7.2
- Hermes Curator documentation: https://hermes-agent.nousresearch.com/docs/user-guide/features/curator
- Hermes skills documentation: https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/

### 4. Harness management supports a cleaner causal test

The decisive experiment freezes skill content and varies only management:

```text
same model + same tasks + same generated skills + same budget
naive exposure
vs usage/recency curation
vs evidence-driven The KING governance
```

This directly tests whether management changes task performance, shadowing,
regression, and cost.

## Revised Thesis

Self-generated skill accumulation becomes reliable growth only when the harness
uses task-outcome, invocation, shadowing, and regression evidence to control
skill admission, exposure, lifecycle, and its own policy updates.

## Research Questions

```text
RQ1. Under a fixed skill set, does evidence-driven harness governance improve
     held-out task performance over naive accumulation?

RQ2. Does it outperform usage/recency-only lifecycle management when skills
     are relevant but confusable?

RQ3. Which layer creates the gain: admission, provisioning, lifecycle, or
     gated harness-policy evolution?

RQ4. Does a minimal contract-conformant skill-supply path provide usable
     candidates without increasing shadowing or regression?
```

`RQ1-RQ3` are the 80% primary contribution. `RQ4` is the 20% supporting claim.

## Main Hypothesis

For a fixed expanded library `L_t`:

```text
P(The KING(L_t)) > P(naive(L_t))
pi_m(The KING(L_t)) < pi_m(naive(L_t))
regression(The KING(L_t)) <= regression(naive(L_t))
```

The stronger competitor-aware hypothesis is:

```text
The KING outcome/shadowing policy
> Hermes-Curator-inspired usage/recency policy
```

This is a comparison against a public-policy reimplementation unless an actual
Hermes runtime is executed under a matched contract. It must not be labeled as
an exact Hermes system result otherwise.

## Hermes Gap That The KING Should Target

Hermes is now a serious lifecycle baseline, not merely a skill-generation
baseline. The publicly documented Curator uses activity telemetry, deterministic
age thresholds, and optional LLM review. Hermes also has task-completion
verification at the agent layer.

The public documentation does not establish that Curator lifecycle actions are
driven by per-skill counterfactual task utility, empirical oracle selection,
shadowing decomposition, or held-out promotion of routing/processor variants.
This is a bounded inference from the documented interface, not a claim that no
such behavior can exist anywhere in the codebase.

The KING should therefore target the missing connection:

```text
paired task evidence
-> route event classification
-> skill-local and route-local risk
-> lifecycle or provisioning intervention
-> isolated harness variant
-> held-in/held-out promotion or rollback
```

## Revised Comparison Stack

All management arms use the same frozen generated-skill snapshot.

| Arm | Management rule | Purpose |
|---|---|---|
| `M0` | Naive expanded exposure | Shows unmanaged accumulation. |
| `M1` | Fixed top-k provisioning | Isolates retrieval/exposure control. |
| `M2-H` | Hermes-Curator-inspired usage/recency lifecycle | Strong competitor-policy baseline. |
| `M2-K` | The KING outcome/shadowing lifecycle | Tests evidence-conditioned skill management. |
| `M3-K` | `M2-K` plus gated policy/processor evolution | Tests harness growth beyond library cleanup. |

The primary paper contrast is `M0 vs M2-K/M3-K`. The strongest novelty contrast
is `M2-H vs M2-K`.

## 80:20 Operational Allocation

### Research claims

- 80%: management causality, shadowing reduction, regression control, and
  gated harness evolution.
- 20%: whether one fixed Create-Skill-style path supplies contract-valid
  candidates for admission and management.

### Experiment budget after C0/C1 calibration

- About 20% for fixed candidate supply and contract conformance.
- About 80% for fixed-library stress, management ablations, repeated held-out
  evaluation, repair and other lifecycle actions, and harness-variant gates.

### Implementation priority

1. Finish the frozen C0/C1 calibration without changing its contract.
2. Implement one fixed, reproducible Create-Skill-style candidate supplier and
   AIP-lite admission gate. Do not optimize the generator or skill prose as a
   research track.
3. Freeze the generated library snapshot used by every management arm.
4. Implement the Hermes-Curator-inspired policy baseline from documented rules.
5. Connect route evidence to The KING lifecycle actions.
6. Execute fixed-library management ablations.
7. Execute isolated harness variants and held-in/held-out promotion or rollback.
8. Execute contract repair only as a diagnosed, verifier-gated lifecycle
   action owned by the harness.

## Claim Boundaries

The paper may claim:

- evidence-conditioned management improves a fixed skill library,
- management reduces measured shadowing and regression,
- gated harness changes outperform static management when supported by held-out
  evidence,
- self-generated skills can become useful inputs to a managed harness.

The paper must not claim, without separate evidence:

- The KING writes universally better skills,
- Hermes lacks skill lifecycle management or verification,
- usage/recency curation is equivalent to the full Hermes runtime,
- a positive composite index alone proves harness evolution.

## Effect On Existing Work

The current full-87 C0/C1 experiment remains useful. It calibrates the model,
harness mode, verifier, and curated-skill upper anchor. It is not invalidated by
this direction change.

The change begins after calibration: C2/C3 become a bounded candidate-supply
stage, while expanded-library management and harness evolution become the main
experimental body.
