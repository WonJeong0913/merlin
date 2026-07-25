> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/model-authored-routing-rollback-v1.md`

---

# Actual model-authored routing rollback v1

## Outcome

An account-authenticated Codex run requested `gpt-5.6-terra` at high effort
and authored the three-file `extract-markdown-headings` candidate. The provider
stream did not expose a resolved model ID, so this is a requested-model
contract, not a provider-resolved identity claim.

The candidate itself was good enough to pass every behavioral execution case:

- static quarantine: 5/5 gates
- portable format and safety: pass
- visible target execution: 2/2
- frozen hidden Markdown fence case: 1/1
- target verifier trust checks: 14/14
- candidate bytes and quarantine manifest remained immutable

It was still not promoted. The governed provisioner selected this skill for a
negative request to count lines in `notes.md`. The shared input filename anchor
overrode the intended task boundary, producing one wrong-skill route out of two
negative cases. The KING therefore retained the original active-library SHA-256
exactly and resolved the candidate as `rollback`.

This is the central research/product point in a concrete lifecycle: a useful
model-authored skill can still make the agent worse because the harness selects
it in the wrong context. Candidate code quality alone is not a promotion
criterion.

## Evidence chain

The campaign was precommitted in code before model generation:

- two visible target cases
- one exact held-out case hidden from the generator
- two negative routing cases
- closed-world `adopt | rollback | reject` outcome policy

The first retained record stopped at `reject` because the routing gate ran
before held-out execution. It was not deleted or rewritten. A completion pass
then used the exact same raw provider trace, response, prompt hash, candidate
manifest, candidate bytes, target report, and live-library snapshot. It made no
new model call and executed only the previously untouched held-out phase.

Final bindings:

- raw provider trace SHA-256:
  `cd811f5ebfc81cd6d551bb0f45852b7feb76355147646c60f239e0c712baa76c`
- prompt SHA-256:
  `8ee2a38072a62290c8b071fa33d7ee8903706f6cf975db482112b8ecda6de5b0`
- response SHA-256:
  `3a9bbba0ad30aff4fe39d723653a7d49e3356586a08a569e25f8078b899c7e4c`
- quarantine manifest SHA-256:
  `56efb3dff8765e2bbbb8f2d7e1fa692277a50d127702188e00ae59b8b6a5dc71`
- frozen campaign contract SHA-256:
  `6e077ebef803b32f529d277741b02a4d3f375e8e44312cef7b65a00b05212a0b`
- final safe evidence SHA-256:
  `fc8feb35eb5f35ab89d6021ac0a26f5267c2ef52318c9ef1baea53b3b06ca750`
- safe-chain audit: 9/9

Safe retained artifacts:

- `experiments/mvp/results/model_authored_hidden_rollback_live_v1/`
- `experiments/mvp/results/model_authored_hidden_completion_live_v1/`

Private raw authoring and sandbox workspaces stay outside the repository. The
safe package contains only the candidate bundle, sanitized evidence, hashes,
and audit report.

## Exact claim boundary

Supported:

- an actual Codex provider turn occurred under the requested GPT-5.6 CLI
  contract;
- the model-authored candidate was quarantined and run in isolated macOS
  workspaces;
- target and hidden behavioral verification passed;
- a negative routing verifier found input-anchor shadowing;
- copy-on-write rollback preserved the exact original active library;
- the safe retained chain passes 9/9 tamper-sensitive audit checks.

Not supported:

- provider-resolved model identity;
- provider-native Skill invocation;
- a claim that the model-authored code failed the hidden case;
- a broad skill-generation, routing, or rollback success rate;
- a full-87/full-209 model-backed benchmark result;
- production-grade hostile-code isolation.
