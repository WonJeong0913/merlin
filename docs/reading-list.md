> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/reading-list.md`

---

# The KING Reading List

Purpose: decide what to read next for The KING without over-reading adjacent work.

The current thesis is:

> The KING is a self-managing skill-harness agent that validates whether self-generated skills create meaningful performance deltas, manages their lifecycle, and evolves the skill-harness policies that govern adoption, provisioning, selection, revision, and retirement.

## Already Core

These are the current backbone papers. Keep using them as anchors.

| Paper | Read Depth | Role in The KING |
|---|---:|---|
| SkillsBench | Already read | Measures whether skills actually improve performance. Anchor for `g` and generated-skill utility. |
| SkillOps | Already read | Treats skills as managed artifacts with contracts, validators, health, and lifecycle actions. |
| More Skills, Worse Agents? | Mostly read | Shows library growth can reduce clean oracle-only selection and create shadowing/no-skill failures. |
| HarnessX | Initial extract | Gives hook/processor composition, candidate harness variants, and gated co-evolution logic. Start with the harness-only subset, but keep full harness co-evolution as the long-term target. |
| AIP | Detailed extract | Gives The KING its AIP-lite structured skill artifact direction. |
| SkillRevise | Detailed extract | Gives The KING its trace-conditioned revision and first-success adoption rule. |
| Counterfactual Trace Auditing | Method extract | Gives The KING paired-trace behavior-delta metrics. |
| Self-Harness | Method extract | Gives The KING conservative harness policy promotion gates. |

## Must Read

These have now been extracted. Keep them as active design anchors.

| Priority | Paper | Link | Why It Matters | Read For |
|---:|---|---|---|---|
| 1 | AIP: A Graph Representation for Learning and Governing Agent Skills | https://arxiv.org/abs/2606.04781 | Directly informs The KING's skill artifact/schema. Moves beyond free-form prose skills. | Extracted in `docs/paper-notes/aip.md`. |
| 2 | SkillRevise: Improving LLM-Authored Agent Skills via Trace-Conditioned Skill Revision | https://arxiv.org/abs/2606.01139 | Direct competitor for trace-based skill repair and empirical utility selection. | Extracted in `docs/paper-notes/skillrevise.md`. |

## Method-Focused Read

Do not read every section. Extract equations, algorithm boxes, metrics, and limitations.

| Priority | Paper | Link | Why It Matters | Read For |
|---:|---|---|---|---|
| 3 | Counterfactual Trace Auditing of LLM Agent Skills | https://arxiv.org/abs/2605.11946 | Helps define how to measure what a skill changes in behavior, not just final pass rate. | Extracted in `docs/paper-notes/counterfactual-trace-auditing.md`. |
| 4 | Self-Harness: Harnesses That Improve Themselves | https://arxiv.org/abs/2606.09498 | Closest harness-self-improvement competitor to HarnessX. | Extracted in `docs/paper-notes/self-harness.md`. |

## Summary Level

Use these to fill related work and baseline positioning. Abstract, tables, method diagram, main result, and limitations are enough.

| Paper | Link | Use |
|---|---|---|
| SkillAxe: Sharpening LLM-Authored Agent Skills Through Evaluation-Guided Self-Refinement | https://arxiv.org/abs/2606.10546 | Skill refinement baseline; quality dimensions for skill improvement. |
| How Well Do Agentic Skills Work in the Wild | https://arxiv.org/abs/2604.04323 | Evidence that skill benefits become fragile in realistic settings. |
| SkillOpt: Executive Strategy for Self-Evolving Agent Skills | https://arxiv.org/pdf/2605.23904 | Add/delete/replace edits, held-out acceptance, rejected-edit buffers. |
| Agent Skills for Large Language Models: Architecture, Acquisition, Security, and the Path Forward | https://arxiv.org/abs/2602.12430 | Survey-level taxonomy and security/architecture framing. |
| SoK: Agentic Skills - Beyond Tool Use in LLM Agents | https://arxiv.org/html/2602.20867v1 | Skill-vs-tool distinction and terminology. |

## Optional Baselines

Read only if we need more baseline coverage.

| Paper/System | Link | Why Optional |
|---|---|---|
| Voyager | https://arxiv.org/abs/2305.16291 | Classic skill-library lifelong-learning baseline, but domain-specific to Minecraft. |
| SkillWeaver | https://arxiv.org/abs/2504.07079 | Web-agent skill acquisition baseline; useful for acquisition comparison. |
| CoEvoSkills | https://arxiv.org/html/2604.01687v2 | Structured multi-file skill evolution; useful if The KING needs a skill-package baseline. |

## Recommended Order From Here

1. SkillAxe
2. How Well Do Agentic Skills Work in the Wild
3. SkillOpt
4. One survey only: Agent Skills for LLMs or SoK

## What Each Paper Should Answer

For each must-read or method-focused paper, extract only:

- problem statement
- method summary
- key equations or algorithm
- main empirical result
- limitations
- how it changes The KING
- whether it is a baseline, component source, metric source, or related-work support

## Current Decision

Do not block The KING MVP on reading all papers.

Implementation has started after detailed AIP and SkillRevise extraction plus method-focused CTA and Self-Harness extraction. The MVP structure is:

```text
trace store
-> failure summary
-> skill candidate / repair
-> skill artifact schema
-> change manifest
-> deterministic validation and regression gate
-> task-conditioned provisioning
-> invocation and shadowing monitoring
-> lifecycle action
```
