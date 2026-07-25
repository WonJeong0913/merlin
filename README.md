# Merlin

Merlin is a self-managing skill-harness agent. This repository is now the
primary product and research codebase; the former TypeScript/OpenClaw-derived
product has been replaced locally.

The central claim is:

> Failures from self-generated skills are not only skill-content failures.
> They are skill-harness management failures across generation, provisioning,
> selection, validation, lifecycle, and harness-policy evolution.

## Scope

The lab keeps the established 80/20 priority:

- 80%: harness governance, typed hooks, trace ingestion, deterministic gates,
  repair, promotion, rollback, retirement, library management, and bounded
  harness evolution;
- 20%: minimal contract-conformant `SKILL.md` supply.

The core Python package is `src.merlin_harness`.

## Current state

Ported from the preserved legacy research workspace:

- full Python harness implementation;
- unit and contract tests;
- HarnessX typed runtime and live-hook bridge;
- AEGIS controllers and verifier suites;
- skill lifecycle and management logic;
- account-auth resource and reinvestment accounting;
- 50-task personal-workload protocol;
- account-auth pilot runner;
- active research and architecture notes;
- experiment runners without raw private/provider results.

One small model-free scripted AEGIS replay fixture is retained unchanged for
regression testing. Its legacy schema label is provenance, not current Merlin
branding.

Historical Build Week packages, raw provider traces, large SkillsBench task
workspaces, and hash-bound legacy evidence remain in the source archive. They
are provenance, not Merlin-branded evidence, and must not be silently renamed.

The replaced TypeScript Merlin product is preserved in the adjacent
`merlin-product-legacy-20260724` directory for recovery only. It is no longer
the active product.

## Verification

From this directory:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_self_managing_campaign \
  tests.test_harness_evolution_ledger \
  tests.test_personal_workload_campaign \
  tests.test_personal_workload_account_pilot \
  tests.test_account_resource_governance
```

Run all tests only after restoring any intentionally excluded large benchmark
fixtures.

## Next integration

1. Generate new Merlin-namespaced manifest and schedule artifacts.
2. Add a harness-signed skill-body load/invocation event.
3. Repeat the two-pair account-auth pilot.
4. Build the new Merlin product shell directly around the validated harness.
5. Start the 50-task longitudinal campaign only after invocation evidence is
   complete.
