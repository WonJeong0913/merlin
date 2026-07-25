> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/implementation-start.md`

---

# The KING Implementation Start

Created: 2026-07-07

## Decision

The KING can start now. The required concept boundary is clear enough:

```text
The KING is not a better skill text generator.
The KING is a self-managing skill-harness agent that governs skill generation, validation,
provisioning, selection, behavior measurement, lifecycle, and bounded harness policy updates.
```

Priority update (2026-07-13): allocate the main research claim and experiment
budget `80%` to evidence-driven harness governance/evolution and `20%` to one
bounded self-generation plus targeted-repair path. Freeze generated skill
content before management ablations so harness effects are identifiable.

The four newly extracted papers map cleanly into the first architecture:

| Paper | The KING Layer | Use |
|---|---|---|
| AIP | Skill artifact | Store generated skills as structured, testable, addressable graph-like artifacts. |
| SkillRevise | Repair/adoption loop | Diagnose trace evidence, revise candidates, deploy first verifier-passing version. |
| CTA | Behavior measurement | Measure what a skill changed, not only whether pass rate changed. |
| Self-Harness | Harness update gate | Promote policy edits only if held-in and held-out gates do not regress. |

## MVP Scope

Do this first:

```text
task set
-> no-skill baseline run
-> trace logging
-> generated skill candidate
-> AIP-lite structured skill artifact
-> validation and regression gate
-> active / rejected / hidden status
-> task-conditioned provisioning
-> invocation trace
-> CTA-lite behavior delta
-> lifecycle action
```

Do not start the first executable slice with:

- model-weight updates
- reinforcement learning
- graph database dependency
- complex UI
- automatic arbitrary code-level harness rewriting

Full HarnessX-style co-evolution remains a long-term target. The first slice
should build the hook/processor substrate, harness variant manifest, and
held-in/held-out promotion gate before attempting automatic processor or code
generation.

## Core Formulas To Preserve

Skill utility from SkillsBench:

```text
g = (p_skill - p_vanilla) / (1 - p_vanilla)
```

More Skills decomposition:

```text
Delta(q,S) = p(q,S*(q)) - p(q,S)
Delta = Delta_ctx + Delta_shd
```

Clean oracle-only selection:

```text
pi_o(q,S) = Pr(emptyset != I subseteq S*(q) | q,S)
delta_o(q,S) = pi_o*(q) - pi_o(q,S)
```

SkillRevise selection:

```text
H_<=B = {S_0} union S_<=B
P_<=B = {S in H_<=B : succ(S,T)=1}

S*_<=B =
  arg min_{S in P_<=B} idx(S), if P_<=B is non-empty
  arg max_{S in H_<=B} U(S,T), otherwise
```

CTA trace bundle:

```text
B_tau = (q_tau, T_tau^+, T_tau^-, S_tau, r_tau^+, r_tau^-)
Delta P_tau = r_tau^+ - r_tau^-
```

Self-Harness acceptance:

```text
Delta_in = P_in(h_candidate) - P_in(h_current)
Delta_ho = P_ho(h_candidate) - P_ho(h_current)

accept iff Delta_in >= 0 and Delta_ho >= 0 and max(Delta_in, Delta_ho) > 0
```

## First Implementation Modules

The first code skeleton starts in `src/the_king/`:

- `models.py`: core dataclasses for skill artifacts, steps, traces, validation, lifecycle decisions, and harness policy changes.
- `library.py`: file-backed JSON skill store for early experiments.
- `metrics.py`: normalized gain, shadowing/provisioning metrics, SkillRevise-style selection, and Self-Harness acceptance.
- `tasks.py`: deterministic exact/file/command verifiers.
- `traces.py`: file-backed trace storage.
- `provisioning.py`: first lexical top-k task-conditioned provisioner.
- `lifecycle.py`: AIP-lite structure gate and lifecycle status transition.
- `cta_lite.py`: minimal behavior delta between with-skill and no-skill traces.

The first experiment notes start in `experiments/mvp/README.md`.

The divide-and-conquer work plan is in `docs/mvp-work-breakdown.md`.

## Implementation Difficulty

| Area | Difficulty | Reason |
|---|---:|---|
| AIP-lite schema | Medium | Mostly data modeling and validation; full graph compiler can wait. |
| Trace logging | Medium | Needs consistent run records before fancy analysis. |
| SkillRevise-lite | Medium-high | Needs verifier feedback, repeated execution, and revision prompts. |
| CTA-lite | Medium | Deterministic deltas are easy; full SIP taxonomy is later. |
| Task-conditioned provisioning | Medium | Start with lexical/embedding retrieval, then add shadowing feedback. |
| Lifecycle manager | Medium | Rules are simple, but evidence quality matters. |
| Self-Harness policy updates | Medium-high | Keep editable surfaces narrow and gate every change. |
| Full HarnessX-style co-evolution | Very high | Required long-term target; stage through hook/processor manifests, isolated variants, and promotion gates. |

## First Milestone

Milestone 0 should prove that the harness can record and govern skill use before trying to beat every baseline:

1. Define 10-20 small synthetic smoke tasks with verifiers.
2. Run no-skill baseline and log traces.
3. Generate or manually seed a few candidate skills.
4. Convert candidates into AIP-lite artifacts.
5. Validate candidates and reject failures.
6. Provision only top-k candidate skills per task.
7. Measure clean invocation, distractor invocation, no-skill fallback, cost, and success.
8. Hide or retire skills that repeatedly shadow useful ones.

Success for Milestone 0:

```text
The system can explain why each skill is active, hidden, rejected, or retired.
```
