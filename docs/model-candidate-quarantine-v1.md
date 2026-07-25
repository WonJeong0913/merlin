> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/model-candidate-quarantine-v1.md`

---

# Model-authored Candidate Quarantine v1

Classification: architecture, implementation, experiment  
Status: inert intake implemented; separately gated live execution/promotion now recorded

This quarantine remains inert by design. It is now followed by a distinct
manifest-reverified macOS execution and copy-on-write promotion path; see
`docs/live-model-authored-skill-v1.md`.

## Purpose

Open-ended model generation must not write directly into the active skill
library or execute model-authored code on the host. This quarantine is the
intake boundary between a provider response and The KING's managed-creation
gates.

```text
strict provider JSON
  -> exact response SHA-256
  -> path / size / secret / Python AST quarantine checks
  -> new-only inert candidate folder
  -> content-addressed manifest
  -> execution_allowed=false, promotion_allowed=false
```

`src/the_king/model_candidate_quarantine.py` accepts a multi-file bundle with
`SKILL.md`, optional `agents/openai.yaml`, Python scripts, and bounded reference
files. It does not import or run candidate code.

## Fail-closed rules

- candidate IDs must be portable kebab-case and match `SKILL.md` frontmatter;
- provider JSON contains exactly `candidate_skill_id` and `files`;
- prompt and exact raw response are bound by SHA-256;
- absolute, traversal, hidden, duplicate, unknown, and non-portable paths fail;
- file count, per-file bytes, and total bytes are bounded;
- NUL content and common private-key/API-token patterns fail;
- Python syntax is parsed without execution;
- process, network, dynamic import, `eval`, `exec`, and `compile` surfaces fail;
- an existing quarantine directory is never overwritten;
- reports contain hashes and sizes, not model-authored file bodies;
- host execution, isolated execution, verifier success, native invocation, and
  adoption all remain explicitly false.

## Research boundary

EvoSkills demonstrates that model-authored, multi-file skills can be improved
with iterative surrogate verification. The KING does not claim novelty from
receiving such files. Its immediate contribution is that the artifact cannot
bypass library governance: candidate intake, execution evidence, verifier
trust, held-out checks, and final adoption remain different states.

This milestone does not yet prove that GPT-5.6 authored a useful skill. A real
provider adapter must preserve the raw response pointer/hash and resolved model
evidence. A separate isolated runner must then execute the candidate under
frozen target cases. Only its output artifacts may reach trusted target,
held-out, and whole-library verifiers. The existing repair and verifier-trust
gates remain the final promotion authority.
