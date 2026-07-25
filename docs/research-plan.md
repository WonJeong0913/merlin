# Research Plan

## Working Title

Merlin: Self-Managing Skill-Harness Agents for Reliable Self-Generated Skills

## Thesis

Self-generated skill failure is not only a skill-content problem; it is a skill-harness management problem.

The 2026-07-13 priority decision makes this operational:

```text
80%: evidence-driven harness governance and harness evolution
20%: minimal contract-conformant skill supply
```

This ratio governs research claims, experiment budget, implementation order,
and paper space. It is not a literal source-line ratio. See
`docs/research-direction-2026-07-13.md`.

The 20% does not optimize skill prose or generator quality as a research
track. It supplies a fixed Create-Skill-style `SKILL.md`-centered artifact that
satisfies the skill contract. Repair, promotion, retirement, and rollback are
governed lifecycle operations in the 80% harness track.

The 2026-07-23 long-term economic mechanism is:

```text
verified skill-reuse savings
-> bounded reinvestment in validation, routing, repair, and harness evolution
-> safer and more useful skill reuse
```

Only matched, verifier-backed direct savings can authorize automatic
reinvestment. Estimated avoided-failure value is reported separately and
verifier updates require an independent high-risk gate. The complete equation
crosswalk and experiment contract are in
`docs/cost-reinvestment-flywheel.md`.

## Research Questions

Primary:

How can an agent turn an expanding, noisy, self-generated skill library into
reliable held-out improvement through evidence-driven admission, provisioning,
selection, lifecycle, and gated harness evolution?

Secondary:

Does a bounded self-generation and repair loop provide incremental benefit once
the harness, rather than the generator, owns deployment decisions?

## Hypothesis

A self-managing skill harness that uses task-outcome, invocation, shadowing, and
regression evidence to validate, provision, select, monitor, and retire skills
will outperform both naive accumulation and usage/recency-only lifecycle
management, especially as the skill library grows.

The decisive comparison is the cross-term:

```text
naive library + the same generated skills
vs
Merlin managed library + the same generated skills
```

If the same generated skills are harmful or neutral under naive accumulation but
useful under managed provisioning, lifecycle, and gated harness evolution, the
main failure mode is skill-harness management rather than skill content alone.

## System Definition

Merlin manages a skill lifecycle:

1. Observe task traces, invocation events, verifier outcomes, and regressions.
2. Admit curated or generated candidates only through explicit contracts and
   deterministic evidence gates.
3. Provision a bounded task-conditioned candidate set at inference time.
4. Monitor clean, wrong, mixed, empty, and spurious invocation events.
5. Repair, merge, hide, retire, or add validators when evidence identifies a
   skill-local problem.
6. Change routing, exposure, thresholds, or processor composition when evidence
   identifies a harness-local problem.
7. Promote harness variants only through isolated held-in and held-out
   evaluation with rollback.
8. Use one bounded self-generation protocol to supply new candidates; do not
   optimize the generator as the primary research contribution.

## Skill Artifact

A Merlin skill should contain:

- name
- description
- trigger and do-not-use conditions
- graph-like steps
- typed step inputs and outputs
- operation or procedure
- expected artifacts or outputs
- validator
- failure modes
- examples
- provenance from traces
- adoption status
- version and lifecycle metadata

This follows the direction of the SkillOps contract `s = (P, O, A, V, F)` and the AIP direction of schema-validated, addressable skill graphs, but extends both toward provisioning and selection.

## Architecture

Core modules:

- Trace Collector: stores task, context, chosen tools or skills, outcome, and failure notes.
- Harness Runtime: `harnessx_runtime.py` now implements all eight typed lifecycle hooks, hook-specific mutation contracts, bounded async processor outcomes, audit records, allowlisted reconstruction, and a deterministic 8/8-hook demonstration. The real provider/model/tool loop adapter remains to be wired.
- Harness Evolution Manager: typed change manifests, exact parent/rollback hashes, smoke and strict seesaw regression gates, selective approval, candidate promotion, and rollback are implemented for registered processors. Autonomous model-written processor evolution and large-scale policy evaluation remain deferred.
- Skill Candidate Generator: proposes new skills or repairs from traces.
- Contract Builder: converts a candidate into an AIP-lite structured skill artifact.
- Failure Analyzer: converts trace/verifier evidence into a SkillRevise-style diagnosis.
- Validation Gate: runs unit-style examples, task tests, and regression checks.
- Provisioner: retrieves a small task-conditioned candidate set instead of exposing the whole library.
- Selector/Invoker: chooses whether to use a provisioned skill and records the decision.
- Shadowing Monitor: detects wrong-skill invocation, no-skill invocation, distractor use, CTA-lite behavior deltas, and performance drops.
- Lifecycle Manager: performs repair, merge, retire, add-validator, and adapter actions.
- Harness Policy Manager: updates routing, processor composition, and gate policies through Self-Harness-style held-in and held-out gates.

## Baselines

Initial baselines should be simple and defensible:

- No-skill baseline.
- Naive self-generated skill accumulation.
- Hermes-Curator-inspired usage/recency lifecycle policy, clearly labeled as a
  public-rule reimplementation unless the actual Hermes runtime is executed.
- Curated or manually validated skill library.
- Merlin with validation only.
- Merlin with validation plus task-conditioned provisioning.
- Merlin with full lifecycle management.

## Metrics

Task performance:

- success rate
- normalized gain over vanilla
- cost
- latency

Harness quality:

- skill adoption pass rate
- validation failure rate
- regression failure rate
- correct skill invocation rate
- wrong skill invocation rate
- no-skill invocation when a useful skill exists
- distractor invocation rate
- library-induced drop
- skill shadowing rate
- CTA-lite behavior divergence count
- skill-induced cost ratio
- first verifier-passing revision rate
- library health score

## Expected Contribution

The expected contribution is not that Merlin writes better skill text. The
contribution is a closed-loop, evidence-driven harness that controls how skills
enter, appear in, affect, and leave the agent's decision process, and that can
promote or roll back bounded changes to its own routing and processor policy.

The clean causal unit is a fixed skill-library snapshot evaluated under
different management policies. Skill generation is a supporting input channel,
not the main treatment.

## Current Experiment Plan

The corrected experiment plan is maintained in:

```text
docs/merlin-experiment-plan.md
```

It separates SkillsBench exact corpus, Merlin MVP synthetic corpus, curated skills, empirical oracle skills, and harness-growth evaluation.

## Risks

- The implementation may look like a retrieval system unless the lifecycle and validation loop are explicit.
- If the experiment only shows better prompts, the contribution becomes weak.
- If exact claims from related papers are not verified from PDFs, the literature framing may overreach.
- If the benchmark is too small, skill shadowing may not appear clearly.
