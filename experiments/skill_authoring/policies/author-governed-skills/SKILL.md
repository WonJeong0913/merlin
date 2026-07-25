---
name: author-governed-skills
description: Author minimal portable Agent Skills under frozen positive, negative, and held-out tests. Use when a reusable capability gap requires a new SKILL.md candidate or when an existing skill cannot be safely repaired.
---

# Author Governed Skills

Create a candidate only when evidence supports reuse across tasks. Treat generation as a proposal; never write directly into the active library.

## Freeze the proposal

1. Record the capability gap, source traces, active-library snapshot, and candidate ID.
2. Check whether an active, hidden, or repair-queue skill already covers the gap.
3. Prefer reuse or bounded repair when the existing identity and contract remain valid.
4. Reject one-off answers, ordinary tool calls, and unsupported speculative needs.
5. Freeze realistic should-trigger, near-miss should-not-trigger, target, and held-out cases before authoring.

## Run the no-candidate baseline

Run the frozen target and routing cases without the candidate. Preserve exact outputs, verifier results, routing decisions, cost, and latency. Do not create a skill when the baseline already satisfies the reusable contract.

## Plan the minimum bundle

Match freedom to fragility. Use concise instructions for judgment-heavy work, parameterized pseudocode for repeatable patterns, and a deterministic script for fragile transformations. Add only resources required by repeated use:

- Put the essential procedure in `SKILL.md`.
- Put deterministic repeated operations in `scripts/`.
- Put detailed or conditional knowledge in `references/`.
- Put copyable templates or media in `assets/`.
- Do not add `README.md`, changelogs, installation guides, or speculative helpers.

## Author the candidate

Use a short verb-led kebab-case name no longer than 64 characters. Make the folder name and frontmatter `name` identical.

Keep frontmatter to exactly `name` and `description`. In `description`, state both what the skill does and the concrete contexts in which it should trigger. Cover adjacent near-misses through precise scope instead of broad keywords.

Write the body in imperative form. Keep only non-obvious procedural knowledge and stay under 500 lines. State:

- required inputs and produced outputs;
- the ordered procedure and tool or script interface;
- verification and observable success criteria;
- permissions, dependencies, and failure modes;
- when to abstain, reuse another skill, or stop.

Create `agents/openai.yaml` from the finished skill. Keep its display name and short description consistent with `SKILL.md`; make `default_prompt` explicitly mention `$<skill-name>`.

## Validate before execution

Validate names, frontmatter, paths, progressive disclosure, interface metadata, sizes, secrets, dependencies, and executable surfaces. Quarantine model-authored files before running them. Treat missing evidence as failure.

Do not execute candidates containing undeclared network, process, shell, dynamic-code, secret, environment, path-escape, or overwrite behavior. Preserve rejected candidates as evidence without changing the live library.

## Evaluate like a lifecycle change

1. Run the same target cases and verifier used for the baseline.
2. Measure should-trigger recall and near-miss should-not-trigger precision.
3. Run hidden cases not exposed to the author.
4. Measure off-task artifacts, latency, cost, routing collisions, and shadowing.
5. Run whole-library regression against the frozen snapshot.
6. Repair only an observed, attributable failure and re-run the same gates.
7. Promote the first passing candidate into a copy-on-write library snapshot.

Keep generated, provisioned, selected, invoked, useful, and adopted as separate evidence states. Prompt exposure alone is not invocation or utility.

## Stop conditions

Reject or retain the original library when any required gate is missing, target behavior regresses, a hidden case fails, negative routing exceeds its threshold, safety is ambiguous, or provenance changes. Never activate a candidate because its `SKILL.md` merely looks plausible.

For an audit of the upstream principles summarized here, read `references/source-contracts.md`. Do not copy upstream implementation text or code without a separate license and attribution review.
