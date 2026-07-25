> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/gpt56-name-collision-ablation-protocol-v1.md`

---

# GPT-5.6 Same-Name Collision Ablation Protocol v1

Status: **frozen before provider execution; no result claimed**

This is an adaptive confirmatory follow-up to
`gpt56-selection-shadowing-pilot-v1`. The exploratory pilot produced one
exact-variant mismatch at library size 56: `docx` was the frozen reference and
`docx@d3cfe519dca2` was selected. Both variants declare the same frontmatter
name, `docx`. That observation motivated this ablation and is disclosed rather
than presented as a preregistered discovery.

## Frozen question

At a fixed 56-skill library size, does deterministic name-unique provisioning
avoid increasing exact-reference selection error relative to the raw catalog?

The primary in-sample criterion is:

`error(name-unique-56) <= error(raw-56)`

Strict improvement is recorded separately. No significance, population,
task-utility, or full-87 claim follows from this small fixed-task experiment.

## Frozen design

- Tasks: the same six tasks used in the exploratory pilot.
- Conditions: `raw-56` and `name-unique-56`.
- Provider turns: four fresh turns per condition, eight total.
- Decisions: six per turn, 48 total.
- Requested provider contract: `gpt-5.6-terra`, reasoning effort `medium`.
- Execution boundary: read-only empty workspace, strict JSON schema, no tools,
  selection only.
- Frozen plan SHA-256:
  `f797c4aa8e95a1dfc046c129f50204ac34450927bc2150bf5ff5185af5f54b9c`.

The raw catalog contains 56 variants, 53 declared names, two duplicate-name
groups (`docx`, `pdf`), and three excess variants. The managed catalog keeps 56
variants and 56 distinct declared names.

## Provisioning policy

For each declared name, the policy chooses a canonical variant without reading
task oracle labels:

1. prefer a variant ID exactly equal to the declared frontmatter name;
2. then prefer an unversioned ID;
3. then choose lexically.

Removed duplicate-name variants are replaced with the next deterministic
unique-name entries from the same canonical 209-skill pool. The source library
is not mutated, all six frozen reference skills remain present, and library size
is held constant.

## Evidence contract

The runner refuses overwrite, stores raw provider JSONL outside the repository,
and writes safe summaries only after all cells pass validation. The audit then
reopens every raw cell and reconstructs the prompt and schema before replaying
the response. It verifies 48 decisions and preserves the distinction between:

- exact variant match;
- declared-name match;
- task execution and utility, which are not measured;
- requested model identity and provider-reported model identity.

Implementation: `experiments/skillsbench/run_gpt56_name_collision_ablation.py`

Audit/test contract: `tests/test_gpt56_name_collision_ablation.py`

## Pending execution boundary

The first external-provider launch attempt on 2026-07-20 was rejected before
process creation by the local approval policy because the approximately 21 KB
prompt contains workspace benchmark text. No raw directory or result artifact
was created. Execution therefore remains pending explicit user approval of
sending the six public benchmark instructions and 56 skill IDs/descriptions to
the configured Codex provider.
