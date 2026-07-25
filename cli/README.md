# Merlin operator CLI

Read-only terminal view over the Python harness. No desktop shell, no provider
session, no account required.

```bash
cd merlin
python3 cli/merlin_cli.py governance
```

## Commands

| Command | Reports |
|---|---|
| `governance` | everything below, in one view |
| `campaign` | longitudinal campaign standing, revalidated on read |
| `evolution` | harness-evolution ledger summary |
| `skills` | active skill library (`--skills-root` to point elsewhere) |

Every command takes `--json` to emit the raw payload instead of the text view.

## Exit status

| Code | Meaning |
|---|---|
| `0` | state was read; every artifact that exists passed revalidation |
| `1` | an artifact failed revalidation, or a required one is absent |
| `2` | usage error |

An **empty** ledger is exit `0`. Zero observations is the honest state of a
campaign that has not run yet, and the validator accepts it. A ledger that
exists and fails its hash chain is exit `1`.

## One source of truth

Governance comes from `src.merlin_harness.governance_view`, which is the same
module the desktop shell reads through `bridge/merlin_bridge.py`. The terminal
and the app cannot report different facts about the same files —
`tests/test_merlin_cli.py` asserts the two payloads are equal.

The CLI performs no lifecycle change, starts no provider turn, and writes no
artifact. A missing skills root is reported, never created.
