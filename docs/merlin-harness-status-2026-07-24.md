# Merlin Harness Lab Status — 2026-07-24

## Current level

Merlin is now the migrated self-managing harness codebase. The previous
OpenClaw-derived TypeScript product has been replaced locally and archived in
an adjacent recovery directory.

The active core package is:

```text
src.merlin_harness
```

## Implemented

- complete Python skill-harness core;
- generation, provisioning, selection, validation, and lifecycle contracts;
- repair, promotion, hide, merge, retirement, and rollback;
- eight typed HarnessX hooks;
- live pre/post tool hook bridge;
- trace ingestion and centralized action controller;
- bounded AEGIS evolution and same-verifier replay;
- durable evolution and account-auth resource ledgers;
- frozen 50-task personal-workload protocol;
- balanced 100-pair baseline/managed schedule.

## New Merlin evidence

The newly generated Merlin campaign contains:

- tasks: `50`;
- scheduled pairs: `100`;
- observations: `0`;
- verified direct savings: `0`;
- `G/S`: unavailable;
- Level 7: not achieved.

Manifest SHA-256:

```text
a82244246c0cfa2a3f125805119bf753fa7508e5e25afb12c78d430e857fe46c
```

Schedule SHA-256:

```text
d4e1aa3654eb6aa3bfdb078d3f968e828b4d554af3ef62b0f134028f391fa037
```

## Legacy boundary

Build Week packages, account-auth pilot reports, provider traces, videos, and
large SkillsBench workspaces remain in the preserved legacy source archive.
Their hashes are not renamed or counted as Merlin evidence.

One small model-free scripted AEGIS fixture is retained unchanged solely to
replay a regression test.

## Current invocation-evidence state

Prompt exposure still cannot establish actual skill invocation. Merlin now has
a trusted, harness-signed skill-body invocation event that binds:

```text
task
-> selected skill ID
-> exact SKILL.md body hash
-> model request hash
-> execution trace
-> verifier result
```

The event uses caller-injected HMAC-SHA-256 harness authentication and stores
only hashes, not private skill bodies, requests, traces, or verifier results.
The campaign ledger fails closed when a skill event is absent, does not match
the arm's hashes, or cannot be verified by the supplied trusted signer.

The remaining blocker is provider-native collection of the bound execution
trace for the public-safe two-pair account-auth rerun. No Merlin field
observation has been recorded yet.

## Next work

1. rerun the public-safe two-pair account-auth pilot as Merlin;
3. promote only complete matched observations;
4. build the new Merlin product shell around the harness;
5. start the 50-task longitudinal campaign.
