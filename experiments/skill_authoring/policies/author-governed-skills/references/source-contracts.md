# Source Contracts

This policy distills process principles; it does not copy upstream implementation code or claim that GitHub popularity proves effectiveness. The experiment must establish its own paired evidence.

Snapshot date: 2026-07-20

| Source | Frozen HEAD | Role in this policy | Copy boundary |
|---|---|---|---|
| `agentskills/agentskills` | `38a2ff82958afee88dadf4831509e6f7e9d8ef4e` | Portable folder, `SKILL.md`, metadata, bundled resources, progressive disclosure | Specification and documentation are referenced; no code copied |
| `openai/skills` | `49f948faa9258a0c61caceaf225e179651397431` | Concrete examples, resource planning, initialization, concise authoring, validation, iteration | Principles paraphrased from `skill-creator`; no source text copied |
| `obra/superpowers-skills` | `cdcd624ad3fd8026deb692e565351854569798dd` | Skill TDD: baseline failure, minimal skill, re-test, close observed loopholes | Process idea only; no templates or code copied |
| `anthropics/skills` | `fa0fa64bdc967915dc8399e803be67759e1e62b8` | Realistic test prompts, should/should-not-trigger evaluation, held-out description optimization, qualitative review | Process idea only; no evaluator code or assets copied |
| `multica-ai/andrej-karpathy-skills` | `2c606141936f1eeef17fa3043a72095b4765b9c2` | Coding-candidate heuristic: understand first, prefer simple solutions, make surgical changes, verify | Community adaptation, not an official Karpathy skill-generation standard |

Authoritative format requirements come from Agent Skills. OpenAI and Anthropic authoring workflows inform process. Superpowers contributes the test-first discipline. Karpathy-inspired rules apply only to coding-oriented candidate quality and never override the task contract, safety policy, or verifier.

Before updating this policy, resolve new upstream revisions, record the new commit hashes, review license implications, and re-run the authoring-policy ablation. Star counts may be recorded as context but must not be used as a promotion weight.
