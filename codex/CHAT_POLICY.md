# Merlin Harness Lab Chat Policy

## Naming

- Use **Merlin** for the project and product.
- Use `src.merlin_harness` for the Python package.
- Mention the former project name only when identifying immutable historical
  provenance.

## Claims

- Separate local deterministic tests, sandbox tests, account-auth runs, and
  remote/product validation.
- Do not turn prompt exposure into actual skill invocation.
- Do not compute spendable savings from failed or unmatched arms.
- Do not relabel historical hashes as Merlin evidence.

## Work placement

- source: `src/merlin_harness/`;
- tests: `tests/`;
- experiment runners: `experiments/`;
- research and protocol docs: `docs/`;
- operating state: `codex/`.

## Safety

- Keep provider traces and credentials out of public documents.
- Preserve the existing Merlin TypeScript product and unrelated user changes.
- Do not mutate GitHub remotes without explicit authorization.
- Prefer deterministic gates around agentic changes.

