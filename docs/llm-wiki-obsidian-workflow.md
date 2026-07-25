> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/llm-wiki-obsidian-workflow.md`

---

# LLM Wiki and Obsidian Workflow

This document records how LLM Wiki, Obsidian, and Codex fit into The KING project.

## Core Distinction

LLM Wiki is the working method, not the research contribution.

The KING's research contribution is the self-managing skill-harness agent:

- skill generation
- skill provisioning
- skill selection
- skill validation
- lifecycle maintenance
- harness policy update

The wiki workflow exists to help the user and Codex manage papers, ideas, plans, and implementation state. Obsidian should stay as the user-facing research wiki; Codex operating rules should live in the repository control layer.

## Three Layers

### 1. Raw Sources

Raw sources are immutable.

Examples:

- local PDFs in `/Users/jeong-won/Downloads`
- source links
- raw paper screenshots
- original generated outputs from prior projectless threads

Rules:

- never overwrite source PDFs
- do not treat summaries as substitutes for papers
- mark unverified paper claims clearly

### 2. Obsidian Vault

Path:

```text
/Users/jeong-won/Documents/Obsidian Vault/The king
```

The vault is the user-facing wiki.

Use it for:

- paper notes
- concept notes
- synthesis notes
- architecture notes
- experiment notes
- seminar notes

Important notes:

- `00_Index.md`
- `00_Main-Idea.md`
- `01_Project-Plan.md`
- `02_Reading-Roadmap.md`
- `04_Paper-Synthesis.md`
- `05_AI-Ingest-Core-Summary.md`
- `design/The-KING-Architecture.md`
- `experiments/MVP-Experiment-Plan.md`
- `papers/SkillsBench.md`
- `papers/SkillOps.md`
- `papers/More-Skills-Worse-Agents.md`
- `concepts/Self-Generated-Skills.md`
- `concepts/Skill-Provisioning.md`
- `concepts/Skill-Shadowing.md`

### 3. Codex Control Layer

Current path:

```text
/Users/jeong-won/Documents/The king/codex
```

This layer stores:

- chat policy
- LLM Wiki policy
- manager state
- vault manifest
- update protocol
- open gaps
- links to legacy sources

## Update Protocol

When the user says "옵시디언에 올려", "옵시디언에 정리", "vault에 반영", "노트 업데이트", "논문 정리", or an equivalent request, treat it as an LLM Wiki maintenance operation. Codex should read `codex/LLM_WIKI_POLICY.md`, classify the update, modify the correct canonical note, update related synthesis or index pages when needed, update manager state when needed, and report changed files.

When the user gives a new paper:

1. Read the source or note.
2. Extract problem, method, equations, results, limitations.
3. Add or update `papers/<Paper-Name>.md` in the vault.
4. Update `04_Paper-Synthesis.md`.
5. Update `05_AI-Ingest-Core-Summary.md` only with compact essentials.
6. Update `codex/MANAGER_STATE.md` if the paper changes the direction.

When the user gives a new idea:

1. Classify it as thesis, architecture, experiment, related work, concept, seminar, or implementation.
2. Update the relevant user-facing note or repo file.
3. Update `codex/MANAGER_STATE.md` if the direction changes.

When the user asks for seminar material:

1. Start from `00_Main-Idea.md`.
2. Use `04_Paper-Synthesis.md` for literature flow.
3. Use `05_AI-Ingest-Core-Summary.md` for compact context.
4. Use `design/The-KING-Architecture.md` for system design.
5. Use `experiments/MVP-Experiment-Plan.md` for evaluation plan.

## Lint Checks

Periodically check for:

- broken links
- duplicate concept pages
- orphan paper notes
- stale thesis statements
- outdated AI-ingest summary
- formulas missing from paper notes
- paper claims not connected to source material
- mismatch between manager state and Obsidian notes

Do not delete files without explicit user approval.

## Required Preflight

Before major Obsidian maintenance, Codex should read:

- `/Users/jeong-won/Documents/Obsidian Vault/The king/00_Index.md`
- `/Users/jeong-won/Documents/The king/codex/LLM_WIKI_POLICY.md`
- `/Users/jeong-won/Documents/The king/codex/VAULT_MANIFEST.md`
- `/Users/jeong-won/Documents/The king/codex/MANAGER_STATE.md`
