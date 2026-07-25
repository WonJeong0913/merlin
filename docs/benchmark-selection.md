> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/benchmark-selection.md`

---

# Benchmark Selection

Created: 2026-07-07

## Decision

Use **SkillsBench-style tasks** as The KING's primary benchmark format. For
paper-level SkillsBench claims, use the full public 87-task SkillsBench corpus,
not a cherry-picked 10-20 task subset.

The project should not start with Terminal-Bench or SWE-only tasks. Those are useful later, but SkillsBench is the closest match to The KING's research question because it already treats skills as first-class artifacts and compares matched skill conditions.

Small synthetic or 10-20 task slices are allowed only for harness debugging,
executor smoke tests, and cost-free development. They must not be used as the
headline SkillsBench result.

## Why SkillsBench First

SkillsBench gives The KING the right experimental skeleton:

```text
same task
same model/harness
same verifier
no skills vs curated/self-generated skills
```

It also gives the core metric:

```text
g = (p_skill - p_vanilla) / (1 - p_vanilla)
```

Most importantly, SkillsBench task packaging already matches The KING's planned structure:

```text
task instruction
task data
skills/
oracle/reference solution
deterministic verifier
```

## Why Not The Others First

| Benchmark | Use Later For | Why Not First |
|---|---|---|
| SWE-Skills-Bench | software-engineering trace auditing and CTA-like paired traces | too narrow for The KING's full skill-harness lifecycle thesis |
| Terminal-Bench-2.0 | harness policy edits and Self-Harness-style evaluation | harness optimization oriented, not skill-library/provisioning oriented |
| ALFWorld | memory/procedural policy transfer | environment-specific and less aligned with file-backed skill artifacts |

## Adopted Task Taxonomy

Borrow the SkillsBench taxonomy:

### Domain

- Software Engineering
- Industrial & Physical Systems
- Natural Science
- Office & White Collar
- Finance & Economics
- Mathematics & OR
- Cybersecurity
- Media & Content Production

### Capability

- Reasoning
- Agentic Coding
- Multimodal
- Tool Use
- Search & Research

### Difficulty

- `C`: Core, under 60 minutes for a domain specialist
- `X`: Extended, 1-4 hours
- `E`: Extreme, over 4 hours

## The KING Extensions

SkillsBench taxonomy is not enough for the shadowing/provisioning claim. Add these fields:

### skill_dependency

```text
none | low | medium | high
```

Meaning: how much the task should benefit from a correct skill.

### shadowing_role

```text
control | oracle_target | distractor_candidate | regression_probe
```

Meaning:

- `control`: should not need a skill.
- `oracle_target`: has a useful skill or should have one.
- `distractor_candidate`: useful for testing wrong-skill exposure.
- `regression_probe`: should not regress when skills or policy change.

### mvp_tier

```text
smoke | mvp | extended
```

Meaning: how soon the task belongs in the experiment.

## Current Implementation

Task JSON metadata now uses:

```json
{
  "benchmark_family": "SkillsBench-style",
  "domain": "Office & White Collar",
  "capability": "Tool Use",
  "difficulty": "C",
  "skill_dependency": "medium",
  "shadowing_role": "oracle_target",
  "mvp_tier": "smoke"
}
```

Validation lives in:

```text
src/the_king/taxonomy.py
```
