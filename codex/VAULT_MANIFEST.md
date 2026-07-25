> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/codex/VAULT_MANIFEST.md`

---

# The KING Vault Manifest

Vault path:

```text
/Users/jeong-won/Documents/Obsidian Vault/The king
```

## Role

The Obsidian vault is the user-facing research wiki for The KING. It is where the user reads paper notes, synthesis notes, architecture notes, experiment plans, and seminar material.

사용자에게 보이는 노트의 본문과 제목은 한국어를 기본으로 한다. 논문 고유명사,
조건 식별자, 코드 이름, 수학 기호처럼 원문과 대응해야 하는 표기만 영어를 유지한다.

The local repository at `/Users/jeong-won/Documents/The king` is the project workspace and Codex control layer. It preserves operating rules, implementation files, manager state, and repo-local research artifacts.

## File Map

### Root Notes

- `00_Index.md`: user-facing entry point.
- `00_Main-Idea.md`: project thesis and core interpretation.
- `01_Project-Plan.md`: current research plan.
- `02_Reading-Roadmap.md`: what to read next.
- `04_Paper-Synthesis.md`: connects papers to The KING.
- `05_AI-Ingest-Core-Summary.md`: compact AI-readable summary.

### Paper Notes

- `papers/SkillsBench.md`
- `papers/SkillOps.md`
- `papers/More-Skills-Worse-Agents.md`
- `papers/HarnessX.md`
- `papers/AIP.md`
- `papers/SkillRevise.md`
- `papers/Counterfactual-Trace-Auditing.md`
- `papers/Self-Harness.md`

### Concept Notes

- `concepts/Self-Generated-Skills.md`
- `concepts/Skill-Provisioning.md`
- `concepts/Skill-Shadowing.md`

### Design Notes

- `design/The-KING-Architecture.md`

### Experiment Notes

- `experiments/MVP-Experiment-Plan.md`

## Canonical Priority

If notes disagree, prefer in this order:

1. `00_Main-Idea.md`
2. `04_Paper-Synthesis.md`
3. `05_AI-Ingest-Core-Summary.md`
4. individual paper notes
5. concept notes
6. manager state, when it records a later accepted direction

## Required Maintenance

When changing the vault:

- preserve raw source material
- update synthesis when a paper's role changes
- update AI-ingest summary only with compact essentials
- update reading roadmap when reading status changes
- update `codex/MANAGER_STATE.md` when project direction changes
- read `codex/LLM_WIKI_POLICY.md` before major vault maintenance
- keep Codex operating policy out of the Obsidian vault
- remove stale goals and superseded experiment wording instead of preserving
  multiple conflicting plans in user-facing notes
- keep the active `80% harness governance / 20% bounded generation and repair`
  direction synchronized across the index, main idea, project plan, synthesis,
  architecture, experiment plan, and AI handoff summary

## Last Major Rewrite

Date: 2026-07-13

- Rewrote all 19 canonical vault notes in Korean.
- Preserved canonical filenames and Wikilink targets for compatibility.
- Removed duplicated explanations, stale implementation steps, and the old
  generation-centered positioning.
- Centered the vault on fixed-skill management comparisons `M0`, `M1`, `M2-H`,
  `M2-K`, and `M3-K`.
