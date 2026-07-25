> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/managed-skill-creation-contract.md`

---

# The KING Managed Skill Creation Contract

Status: accepted implementation contract, 2026-07-18
Classification: architecture + implementation + experiment

## Decision

The KING does not treat a successful or memorable agent experience as an
automatic instruction to create and activate a skill.

Skill creation is a harness-managed proposal process:

```text
need evidence
  -> bounded creation proposal
  -> portable Agent Skills artifact
  -> deterministic validation
  -> paired target evaluation
  -> held-out regression evaluation
  -> candidate adoption or rejection
```

The first generator will use two public upstream contracts as its seed:

1. [Agent Skills specification](https://github.com/agentskills/agentskills) for
   the portable `SKILL.md` folder format, progressive disclosure, and
   `skills-ref` validation.
2. [OpenAI skill-creator](https://github.com/openai/skills/blob/main/skills/.system/skill-creator/SKILL.md)
   for the authoring sequence: collect concrete examples, plan reusable
   resources, initialize, write, validate, and iterate from real use.

Two additional authoring references sharpen, but do not replace, that seed:

3. [obra/superpowers writing-skills](https://github.com/obra/superpowers-skills/blob/main/skills/meta/writing-skills/SKILL.md)
   for skill TDD: observe baseline failure without the skill, add the minimum
   instruction, verify improved behavior, and refactor loopholes before
   deployment.
4. [Anthropic skill-creator](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md)
   for test-prompt evaluation, qualitative review, trigger-description tests,
   variance-aware comparison, and iterative improvement.

Karpathy-inspired coding rules such as
[multica-ai/andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills)
are behavior-quality inputs for coding-oriented candidates. They are not a
general skill-creation standard and are community adaptations rather than an
official Andrej Karpathy skill-generator implementation.

These are format and process references, not The KING's research contribution.
The KING contribution is the evidence and promotion harness around creation.
No upstream implementation code should be copied into this repository unless
its per-file license and attribution requirements have first been recorded.

## Research Definitions Applied to Creation

The authoring references above define how to produce a usable skill package.
The following research anchors define whether The KING is allowed to create,
repair, expose, or adopt that package.

| Research anchor | Definition imported into the managed-creation contract |
|---|---|
| SkillsBench | Always evaluate the candidate against a matched no-skill/no-candidate baseline. Report normalized gain `g = (p_skill - p_vanilla) / (1 - p_vanilla)` only when the conditions are comparable. |
| SkillOps | Compile each candidate toward contract `s = (P, O, A, V, F)`: preconditions, operation, artifact, validator, and failure modes. Admission and maintenance require positive health evidence, not file existence. |
| More Skills, Worse Agents? | Treat the invocation set as skill bodies actually loaded or called, not retrieved IDs or prompt exposure. Measure clean oracle-only and mixed/distractor invocation, no-skill fallback, and shadowing risk before adoption. |
| AIP | Represent the candidate as addressable graph/contract `SkillGraph = (N, E, B, M)` with named steps, typed edges, bindings, and metadata, so a failure can target a node, script, trigger, or edge. |
| SkillRevise | Diagnose with verifier, attribution, and preservation constraints `D_i = (V_i, A_i, K_i)`; revise with a bounded trace-linked operator; prefer the first verifier-passing candidate rather than the newest rewrite. |
| Counterfactual Trace Auditing | Preserve a matched bundle `B_tau = (q_tau, T_tau^+, T_tau^-, S_tau, r_tau^+, r_tau^-)` and evaluate action, artifact, validation, cost, and off-task deltas in addition to pass rate. |
| Self-Harness | Promote a bounded policy or lifecycle change only when held-in and held-out deltas are both non-negative and at least one is positive; rejected variants remain evidence and never change the live library. |
| SkillOS | Keep the base executor frozen and the skill repository external and versioned. Treat learned curation as a later isolated arm, not as part of the deterministic first generator. |

These papers are method anchors and competitor definitions. Their reported
results do not prove that a The KING-generated skill is useful; the local
candidate must produce its own matched evidence.

## Creation Trigger

A creation proposal may start only from one of these sources:

- an explicit user request for a reusable skill;
- repeated verifier-backed failures that share a task pattern;
- a capability gap where provisioning found no suitable active skill;
- a repair decision that cannot safely preserve the existing skill identity.

A single successful turn is not sufficient creation evidence. Before creating
a new candidate, the harness must check whether an active skill can be reused,
whether a hidden or repair-queue skill already covers the gap, and whether the
problem belongs in a tool, policy, or one-off answer instead of a skill.

## Required Proposal Bundle

Every generated candidate must carry:

- a stable proposal ID and candidate skill ID;
- source type and immutable provenance trace IDs;
- concrete positive examples and should-not-trigger examples;
- a short, verb-led name and a trigger-focused description;
- a `SKILL.md` body containing only essential procedure;
- only the required `scripts/`, `references/`, and `assets/` resources;
- declared inputs, expected outputs, tools, permissions, and failure modes;
- target verifier definitions;
- a frozen evaluation split and library snapshot ID;
- generator backend, model, effort, prompt hash, and artifact hashes.

The generated artifact always enters `candidate`. Generation never writes an
`active` skill directly.

## Promotion Gates

The first implementation must apply these gates in order:

| Gate | Requirement | Failure result |
|---|---|---|
| G0 Need | Evidence shows a reusable capability gap and no existing skill should be reused or repaired | reject proposal |
| G1 Format | Agent Skills structure, frontmatter, names, paths, and progressive-disclosure limits validate | repair candidate |
| G2 Safety | Scripts, paths, dependencies, permissions, secrets, and instruction boundaries pass static policy | reject or quarantine |
| G3 Trigger | Positive and negative trigger cases meet pre-registered precision and recall thresholds | repair description |
| G4 Target | Candidate beats or non-regresses against the frozen no-candidate baseline on target verifiers | repair or reject |
| G5 Regression | Held-out tasks, cost, latency, routing, and shadowing stay within fixed limits | reject and retain library |
| G6 Adoption | Every required gate has positive evidence and the copy-on-write library snapshot passes final verification | promote candidate to active |

Missing evidence is a failed gate, not a neutral result.

## First Generated Skill Procedure

The first real generated skill must follow this exact sequence:

1. Freeze the need-evidence bundle and evaluation split before generation.
2. Write target, pressure, should-trigger, and should-not-trigger cases.
3. Run the cases without the candidate and preserve the baseline failures.
4. Plan the minimum reusable scripts, references, and assets.
5. Initialize a new portable skill folder without touching the active library.
6. Generate the `SKILL.md` and only the planned resources.
7. Validate the artifact with the pinned `skills-ref` version and The KING's
   stricter safety checks.
8. Execute the same target and pressure cases with the candidate, using the
   same verifier, and repair only observed gaps.
9. Execute held-out trigger, regression, cost, and shadowing cases.
10. Stage a copy-on-write candidate-library snapshot.
11. Promote only when every pre-registered gate passes; otherwise retain the
    original library and preserve the rejected evidence.

The first generated skill is therefore an experiment in managed adoption, not
a demonstration that the model can write a plausible `SKILL.md`.

## Evidence Boundary

The following signals must remain separate:

- generated: an artifact was proposed;
- provisioned: the harness exposed it to a task;
- selected: the harness or agent chose it;
- loaded/invoked: the provider supplied native evidence that its body was used;
- useful: paired verifier evidence shows a beneficial causal effect;
- adopted: every target and regression gate passed.

Prompt inclusion alone proves provisioning, not native invocation or utility.

## Completed prerequisite: governed provisioning v2

The chat-runtime provisioning prerequisite is now an explicit harness policy,
`governed-provisioning-v2`, with this fixed order:

```text
active-only
  -> declared-name-unique read-only prompt projection
  -> exact declared artifact/input anchor pool when one exists
  -> trigger and description positive evidence
  -> bounded lexical do_not_use_when negative guard
  -> fixed minimum evidence or abstain
```

An exact declared artifact/input anchor is deterministic positive contract
evidence independent of surrounding language. It can satisfy the minimum
positive-evidence gate even when lexical trigger overlap is zero; lexical score
then ranks anchored ties, with stable skill ID as the final tie-breaker.

The decision record contains policy version, active-library snapshot ID/hash,
same-name collision/suppression evidence, candidate IDs/statuses, anchor
matches, bounded scores, eligibility/exclusion
reasons, ranked/provisioned IDs, primary ID, and abstain reason. Its safe
serialization stores the request hash and length plus anchor hash/length/count
evidence instead of raw request text or filenames, and never stores a full
skill body.
It also keeps harness ranking, prompt exposure, and provider-native
loaded/invoked evidence as different fields; actual invocation remains
incomplete unless the provider supplies the stronger event.

The model-free evaluator fixes the current ten tasks, two curated skills, and
two controlled distractors to one snapshot. In that fixture, governed v1
reaches `9/9` clean oracle-only provisioning, abstains on the one no-skill
control, and has zero mixed/distractor exposures; naive lexical top-k exposes a
distractor on all nine oracle-bearing tasks. This is only a deterministic
prerequisite/acceptance result. It is deliberately not evidence that the
thresholds generalize, that a model invoked a skill, or that task success
improved.

The prerequisite also keeps its research mappings bounded: SkillsBench
normalized gain is `null` with a no-outcomes reason; SkillOps reports
`P/O/A/V/F` presence and read-only health/action; More Skills records only a
prompt-exposure shadowing proxy while provider loaded/invoked evidence remains
unavailable; and AIP contributes declared step/input/output/artifact anchors.
SkillRevise repair, Counterfactual Trace Auditing, Self-Harness held-out
promotion, and SkillOS learned curation remain deferred interfaces rather than
completed runtime claims.

## Semantic metadata router on the real chat path

The model-free governed v1 evaluator remains the deterministic control and
keeps its fixed `9/9` clean-only acceptance result. The real chat entrypoint
adds a provider-independent typed semantic router before the same deterministic
guard. The current adapter is one ephemeral, read-only Codex CLI turn with
`gpt-5.6-terra` and low effort; `--routing-mode deterministic` freezes the
control path. A semantic turn can use at most two model calls (router plus main
executor), while an empty active library skips the router. The extra semantic
call has explicit latency/cost tradeoffs.

The router receives only active skill ID, name, description, trigger,
`do_not_use_when`, declared inputs/artifacts, and `P/O/A/V/F` presence. It does
not receive the full body, step procedure, or scripts. The user query enters the
provider through stdin inside a canonical untrusted-data boundary. Persisted
routing evidence contains hashes, lengths, counts, IDs, enums, requested and
provider-reported model evidence, and immutable raw-trace pointer/hash. It never
stores the raw query, filename, body, or free-form model rationale.

The authoritative order is active-only, exact anchor pool when present,
semantic rank/exclusion/abstain, deterministic negative guard, then exposure
budget. Unknown, duplicate, inactive, over-budget, malformed, timeout,
model-contract, anchor-conflict, or raw-trace failures record a safe error class
and use deterministic fallback so the user turn may continue. Semantic ranking
is language-independent positive routing evidence, but is not native loading,
invocation, utility, or adoption evidence.

The router protocol belongs to The KING's workspace control plane and the main
agent belongs to the execution plane. Codex CLI is the current adapter. A
future/alternate adapter may use the OpenAI Responses API while the application
continues to own routing and branching. No API-key-backed success or Agents SDK
runtime is claimed, and no SDK dependency is introduced by this milestone.

## Completed First Bounded Implementation

`src/the_king/managed_creation.py` and
`experiments/mvp/run_managed_skill_creation_demo.py` now implement the first
bounded form of this contract. The accepted candidate is
`extract-todo-items`, sourced from an explicit need proposal. Its frozen bundle
contains two target cases, one Korean held-out case, and two negative routing
cases against an immutable active-library snapshot.

The draft cannot supply arbitrary executable code. It selects
`extract-prefixed-lines-to-json` from the registered operation contract, and
The KING compiles the trusted `scripts/run.py` template plus portable
`SKILL.md` and `agents/openai.yaml`. The run then applies G0 need/reuse,
draft-schema, G1 format, G2 safety, G3 trigger, G4 same-target-verifier, G5
held-out/regression, and G6 copy-on-write adoption checks. Preflight, compile,
or format/safety failures preserve a machine-readable rejection report and do
not mutate the active library.

The recorded acceptance moved the same target verifier from `0/2` to `2/2`,
passed held-out `1/1`, classified `5/5` trigger/negative cases, preserved both
existing active-skill statuses, and passed all nine recorded checks. A pinned
`skills-ref==0.1.1` cross-check passed in the recorded run. Safe evidence is
`docs/evidence/managed-skill-creation-v1.json`; the full generated candidate and
case workspaces remain outside the package.

This milestone proves one deterministic registered operation can be created,
validated, and provisionally adopted under the contract. It does not yet prove
open-ended LLM-authored code generation, provider-native loading/invocation,
automatic creation from ordinary chat feedback, repair/merge/retire, or
benchmark-wide generalization. `/diagnose` therefore remains observe-only, and
the fixed `/governance` lane remains separate from ordinary chat evidence.
