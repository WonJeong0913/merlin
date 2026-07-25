> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/paper-notes/aip.md`

---

# AIP: Graph Representation for Governing Agent Skills

Source: https://arxiv.org/abs/2606.04781

Read status: detailed extraction, 2026-07-07.

## Core Claim

AIP argues that prose-only skills force the agent to re-derive code, commands, and workflow structure every time it uses a skill. The proposed fix is to represent a skill as a directed execution graph with typed inputs and outputs, deterministic scripts where possible, natural-language nodes only where judgment is needed, and schema-validated YAML as the carrier format.

For The KING, this is the strongest artifact-format paper. It does not solve provisioning or shadowing, but it gives The KING a concrete way to make a generated skill testable, addressable, and governable.

## Method

AIP models a skill as a directed execution graph:

```text
SkillGraph = (N, E, B, M)
```

where:

- `N`: named step nodes.
- `E`: typed input/output or dependency edges.
- `B`: bindings from steps to scripts or references.
- `M`: metadata such as trigger, do-not-use conditions, anti-patterns, scenarios, modes, and integrations.

The paper's node/edge vocabulary is:

```text
Skill
Step(name, description, script?, inputs, outputs, depends_on, parallel?, one_of?)
inputs / outputs
depends_on
script
references
```

The important governance property is addressability:

```text
failure -> node/script/edge -> local edit -> schema validation -> re-evaluation
```

That is the part The KING should adopt. A generated skill should not be accepted as an opaque Markdown instruction. It should be compiled into a graph-like contract with runnable or inspectable units.

## Empirical Result

AIP evaluates on a 27-task SkillsBench sample.

```text
Mean task reward: 0.599 -> 0.705
Delta reward: +0.106
Pass rate: 53.3% -> 67.4%
Win / tie / loss: 12 / 13 / 2
Wilcoxon p: 0.011
```

The 24-task robustness subset keeps the same direction:

```text
Mean task reward: 0.567 -> 0.668
Delta reward: +0.101
Pass rate: 50.8% -> 63.3%
Wilcoxon p: 0.022
```

The key repair evidence is also useful for The KING: two failures were localized to scripts/spec behavior and repaired without corpus regressions. One task moved from `0/5` to `5/5`.

## The KING Use

AIP should become The KING's skill artifact direction:

```text
Generated prose candidate
-> graph/contract compilation
-> schema validation
-> node-level validators
-> adoption or rejection
```

The first MVP does not need a full AIP compiler. It should still store skills as structured artifacts:

- typed steps
- explicit trigger and do-not-use conditions
- expected artifacts
- validators
- provenance from traces
- lifecycle status
- versioned change records

This makes later SkillRevise-style repair possible because repair can target a step, validator, or trigger instead of rewriting a whole skill.

## Limits

- AIP is still closer to a specification than an enforced runtime protocol.
- The experiments use one solver family; cross-model generality is not yet established.
- AIP improves artifact executability, but does not solve which skills should be exposed to the agent.
- AIP does not directly measure skill shadowing or no-skill fallback.

## Implementation Difficulty

MVP difficulty: medium.

The KING can implement an AIP-lite schema quickly with dataclasses or JSON Schema. A real graph compiler, graph database, and runtime traversal enforcement should wait until the basic validation/provisioning loop works.

