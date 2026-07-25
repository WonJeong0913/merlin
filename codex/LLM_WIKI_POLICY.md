> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/codex/LLM_WIKI_POLICY.md`

---

# LLM Wiki Policy

This file defines how Codex manages the user's Obsidian vault as an LLM Wiki.

## Core Rule

The Obsidian vault is the user-facing research wiki. It should contain research knowledge, not Codex operating instructions.

Codex operating rules live in this repository under:

```text
/Users/jeong-won/Documents/The king/codex
```

The vault lives at:

```text
/Users/jeong-won/Documents/Obsidian Vault/The king
```

## Layer Model

### Raw Sources

Raw sources are immutable source-of-truth materials.

Examples:

- PDFs in `/Users/jeong-won/Downloads`
- source links
- screenshots
- raw excerpts
- legacy generated outputs used only as source material

Rules:

- Do not overwrite raw PDFs.
- Do not cite generated summaries as source papers.
- Mark unverified paper claims.

### Obsidian Wiki

The vault is for user-facing research notes:

- `00_Index.md`
- `00_Main-Idea.md`
- `01_Project-Plan.md`
- `02_Reading-Roadmap.md`
- `04_Paper-Synthesis.md`
- `05_AI-Ingest-Core-Summary.md`
- `papers/*.md`
- `concepts/*.md`
- `design/*.md`
- `experiments/*.md`

Rules:

- Keep Obsidian clean and readable.
- Update existing canonical notes before creating new pages.
- Keep claims source-grounded.
- Link papers, concepts, architecture, and experiments.
- Keep AI-ingest notes compact.
- Do not store Codex control policy in the vault.

### Codex Control Layer

Codex policy and state live in the project repository:

- `AGENTS.md`
- `codex/CHAT_POLICY.md`
- `codex/LLM_WIKI_POLICY.md`
- `codex/MANAGER_STATE.md`
- `codex/VAULT_MANIFEST.md`
- `codex/LEGACY_SOURCES.md`

This layer tells Codex how to maintain the vault.

## Trigger Rule

When the user asks Codex to "옵시디언에 올려", "옵시디언에 정리", "vault에 반영", "노트 업데이트", "정리해줘", "논문 정리", or any equivalent request, Codex must treat it as an LLM Wiki maintenance operation.

Required behavior:

1. Read this policy, `codex/VAULT_MANIFEST.md`, and `codex/MANAGER_STATE.md`.
2. Read the relevant Obsidian canonical notes.
3. Classify the update as paper ingest, query capture, thesis, architecture, experiment, related work, concept, seminar, or implementation.
4. Update existing canonical notes before creating new pages.
5. Keep raw sources immutable and mark unverified claims.
6. Update synthesis, AI-ingest summary, reading roadmap, and manager state when affected.
7. Add or update links so new knowledge is reachable from `00_Index.md` or another canonical page.
8. Report changed files to the user.

## Operations

### Ingest

When a new source arrives:

1. Identify source type and path.
2. Extract key claims, formulas, results, and limitations.
3. Create or update the relevant paper/source note.
4. Update related concept pages.
5. Update `04_Paper-Synthesis.md`.
6. Update `05_AI-Ingest-Core-Summary.md` only with compact essentials.
7. Update `00_Index.md` if a new important note appears.
8. Update `codex/MANAGER_STATE.md` if direction changes.

### Query Capture

When an answer becomes reusable project knowledge:

1. Save it to the relevant canonical note.
2. Add links to related paper, concept, architecture, or experiment notes.
3. Update manager state if the project direction changes.

### Lint

Periodically check:

- broken Obsidian links
- duplicate concept pages
- orphan paper notes
- stale thesis statements
- outdated AI-ingest summary
- formulas missing from paper notes
- claims not connected to source papers
- mismatch between Obsidian notes and Codex manager state

## Boundary

LLM Wiki is the project-management workflow.

The KING research contribution remains:

- self-generated skill validation
- task-conditioned skill provisioning
- skill selection management
- lifecycle repair, merge, quarantine, retirement
- harness policy update through gates

Do not present the LLM Wiki workflow as The KING's technical contribution.
