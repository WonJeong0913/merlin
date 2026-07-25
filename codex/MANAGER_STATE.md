# Merlin Harness Lab Manager State

Last updated: 2026-07-24

## Current direction

Merlin is the active project identity. The harness lab manages the full
skill-harness lifecycle:

```text
generation
-> provisioning
-> selection
-> validation
-> repair / promotion / hide / merge / retire / rollback
-> bounded harness-policy evolution
```

The active priority is 80% harness governance and evolution, 20% minimal
contract-conformant skill supply.

## Current implementation

- typed HarnessX runtime and eight hook contracts;
- live Codex pre/post tool hook adapter;
- bounded AEGIS candidate and replay controllers;
- deterministic promotion and exact rollback;
- trace ingestion and centralized lifecycle dispatch;
- skill repair, merge, hide, retirement, and provisioning controls;
- durable harness evolution and account-auth resource ledgers;
- frozen 50-task personal-workload manifest and 100-pair crossover protocol;
- public-safe two-pair account-auth pilot runner.

## Evidence boundary

- Legacy evidence produced under the former project name remains immutable in
  the preserved source archive.
- New Merlin claims require newly generated Merlin-namespaced artifacts.
- Prompt skill exposure is not actual invocation evidence.
- The first new longitudinal observation cannot be promoted until a trusted
  harness-signed or provider-native skill-body invocation event exists.
- Low-cost model comparisons remain outside the active scope.

## Product boundary

The Python harness is the active Merlin core. The former TypeScript/OpenClaw
product has been replaced locally and preserved in an adjacent recovery
directory. New CLI, native UI, tools, and background execution should be built
around the validated Python contracts.

## Remote boundary

Do not push, publish, tag, open a PR, or otherwise mutate the GitHub remote
until the operator explicitly lifts the existing publication freeze.

## Next work

1. Generate and validate new Merlin-namespaced campaign artifacts.
2. Implement a harness-signed skill-body load/invocation event.
3. Repeat the two-pair account-auth pilot.
4. Build the new Merlin product shell around the Python harness.
5. Begin the 50-task longitudinal run only after invocation evidence is
   complete.

## New Merlin campaign

- manifest SHA-256:
  `a82244246c0cfa2a3f125805119bf753fa7508e5e25afb12c78d430e857fe46c`;
- schedule SHA-256:
  `d4e1aa3654eb6aa3bfdb078d3f968e828b4d554af3ef62b0f134028f391fa037`;
- task count: `50`;
- pair count: `100`;
- observation count: `0`;
- Level 7: not achieved.
