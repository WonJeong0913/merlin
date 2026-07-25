# Merlin Harness Lab Instructions

These instructions apply to the Merlin repository.

## Identity

- The project and product name is **Merlin**.
- The Python research package is `src.merlin_harness`.
- Do not reintroduce the former project name as a current product name.
- Historical legacy artifacts may be referenced only as immutable provenance.

## Research position

Merlin is a self-managing skill-harness agent. Its contribution is the
management layer across skill generation, provisioning, selection, validation,
lifecycle, and bounded harness-policy evolution.

Keep the 80/20 priority:

- 80% harness governance and evolution;
- 20% minimal contract-conformant skill supply.

Do not turn this into a skill-writing-quality project or a foundation-model
training project.

## Evidence policy

- Deterministic validation gates own promotion.
- Prompt exposure is not actual invocation evidence.
- Failed or unverifiable arms cannot create spendable savings.
- Keep verifier epochs, provider/model/effort, input snapshots, and arm order
  matched.
- Preserve raw historical evidence without rebranding or recomputing its hash.
- New Merlin claims require newly generated Merlin-namespaced evidence.

## Repository boundary

- The Python harness is the active Merlin core.
- The adjacent legacy TypeScript product is recovery-only and must not be
  modified unless the operator explicitly requests restoration or mining.
- Build new product surfaces around the validated harness contracts.
- Keep generated private/provider traces out of public docs and packages.
- Do not mutate GitHub remotes until the operator explicitly lifts the existing
  publication freeze.
- Do not create local commits either. Do not run `git add`, `git commit`,
  `git stash`, or any command that mutates the index or the tracked work tree.
  The tree holds roughly 6,300 uncommitted migration changes and that state is
  intentional until the freeze is lifted.
- Documents restored from the pre-Merlin workspace carry an origin banner. Keep
  the banner on any file you move, split, or rewrite.

## Testing

- Run focused Python tests after each harness change.
- Use `PYTHONDONTWRITEBYTECODE=1` for clean verification.
- Label local deterministic, sandbox, account-auth, and remote results
  separately.
- A passing test subset is not a full product-runtime claim.
