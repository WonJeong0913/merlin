> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/library-scale-trial1-prefix75-result-2026-07-21.md`

---

# Library-scale trial-1 exact-prefix result — 75/435

## Decision

This result is retained as a **negative diagnostic**, not as a Build Week
performance headline. It is useful evidence that the frozen executor, resume
ledger, trace validator, failure boundaries, and content-addressed transport
worked across 75 live cells. It does not show skill benefit, a library-size
curve, or shadowing recovery.

## Frozen denominator and observed prefix

- Scheduled confirmatory subset: 87 tasks × trial 1 × 5 arms = 435 cells.
- Observed exact prefix: 15 tasks × trial 1 × 5 canonical arms = 75 cells.
- Arms: `curated`, `plus-10`, `plus-50`, `plus-100`, `full-209`.
- Prefix selection read no outcome fields and followed frozen source order.
- Sealed safe results/traces: 75/75.
- Safe failures inside the sealed prefix: 0.
- Full 435 complete: false.
- Full 1,305 complete: false.

The legacy full-manifest aggregator therefore reports 75 observed and 1,230
missing out of 1,305, while the execution supervisor correctly reports a
75/435 trial-1 prefix. These denominators must not be interchanged.

## Arm-level result

Every arm has the same bounded outcome:

| Arm | n | Verifier pass | Mean reward | Server-audited MCP exec | Runner `no_invocation` field |
|---|---:|---:|---:|---:|---:|
| curated | 15 | 0/15 | 0.0 | 0 | 15/15 |
| plus-10 | 15 | 0/15 | 0.0 | 0 | 15/15 |
| plus-50 | 15 | 0/15 | 0.0 | 0 | 15/15 |
| plus-100 | 15 | 0/15 | 0.0 | 0 | 15/15 |
| full-209 | 15 | 0/15 | 0.0 | 0 | 15/15 |

A later raw structural audit corrected the original interpretation. The Codex
JSONL contains model-side MCP tool-call attempts and failed call records, while
the trusted MCP server audit contains no `tools/call`. The client boundary
therefore stopped dispatch before the server; the runner's `no_invocation`
field describes missing server-audited execution, not model intent. These 75
cells are executor-compatibility failures and must not be scored as evidence
that the model abstained. Because no empirical oracle mapping is bound and no
server-audited invocation occurred, wrong/mixed counts and the shadowing curve
are unavailable.

## Corrected dispatch canary

A later runtime changed the Codex client from `read-only` to a fresh empty
per-cell `workspace-write` sandbox, disabled sandbox network access, retained
the native-tool feature denials, and failed closed on any native-tool item. A
fresh pristine ordinal-1 curated canary then produced one hash-sealed,
verifier-reached trace under Docker `network_mode=none`.

That canary still failed the required invocation gate: Codex JSONL recorded ten
MCP tool-call attempts, but the trusted server audit recorded
`tools/call(exec)=0`; skill-ID evidence was also empty. The verifier exited 0
and scored the task false. No retry, other four arms, or remaining 430 cells
were run. This proves that `read-only` alone was not the complete root cause;
the remaining pre-server dispatch difference is being diagnosed against the
successful boundary canary. This isolated one-cell runtime is not merged with
the earlier 75-cell prefix.

This result cannot support any of the following claims:

- GPT-5.6 task performance improvement;
- cross-task generalization;
- a monotonic or non-monotonic library-size effect;
- skill shadowing reduction;
- statistical significance;
- completion of 435 or 1,305 cells.

## Ordinal 76 stop

The next cell, `earthquake-phase-association__t1__curated`, failed during the
external task Docker image build before Codex/provider launch, MCP
initialization, verifier execution, or reward extraction. Its provider/Codex/
MCP/verifier call count is therefore exactly zero.

The immutable runtime did not yet seal pre-model Docker-build failures. Two
materialized orphans were byte-validated and atomically moved to distinct
immutable incident quarantine directories; neither was deleted or overwritten.
The 75-cell prefix and runtime identity remained unchanged. Continuing ordinal
77 would violate exact-prefix ordering, and another ordinal-76 attempt would
violate the no-retry rule.

A separate future carrier adds atomic `safe-failure.json` plus
`infrastructure_unscored` trace sealing for pre-model failures and an audited
orphan-quarantine tool. It was not injected into the 75-cell runtime.

## Runtime and transport identity

- Trial carrier: `9ac6b653a7d6080e409a01d85a125f7756f06d73`
- Payload source: `77c2d22c1e06ec739c39e825de89e969d02c58aa`
- Runtime identity: `884934e05debb2724598501ac0ff1837fcc8b398ba5f34b8292abed25a1fd4c2`
- Progress SHA-256: `791545dc29b6ee7f90d1c7f15d48b9c292a2d4b638d98cf39f8f643b4970f76e`
- Private transport repository: `WonJeong0913/the-king-desktop-sync`
- Branch: `codex/desktop-v26-safe-handoff-20260721`
- Corrected v3 commit: `e7324607d915282054f2c4bf070a259d9bcfcab0`
- Archive: `transport/handoff/trial1-partial-prefix75-safe-20260722-v3-r2/partial-prefix75-safe-20260722-v3-r2.tar.gz`
- Archive SHA-256: `c2cc4473382977b97b648250e866a475a671ce934374096579ac8bf6fbb1ced4`
- Manifest SHA-256: `0c64e58abbe43d60ada8e1ac709bf3758863fcd6f11c537216184242999ae8f0`
- Entries: 81; entries SHA-256:
  `4507f9e02b51d21a12d661764350675fee9dfa80323f16eb734428f19c5705f1`
- Semantic summary SHA-256:
  `4bfa05f98cd922426ba2a8985f6271a5045bf8a96c96255774cc8931fa68cc79`
- Receipt file SHA-256:
  `6be70875311e043513a3cb0bd69d0f465225f2d47d9795cc3fe9996110734c06`

## Independent Mac verification

The private branch was cloned on macOS and matched the full commit SHA. The
archive, manifest, and receipt hashes matched. After extraction, the bundled
verifier returned all true:

```json
{
  "archive_valid": true,
  "binding_valid": true,
  "files_hash_valid": true,
  "receipt_valid": true,
  "semantic_summary_valid": true
}
```

The corrected archive contains 75 normalized traces, source-safe-result hash
bindings, a recomputed semantic summary, and a self-contained verifier. It
explicitly retracts the unsupported v2 README values `8/75` and `741` and
excludes raw provider text, prompts, task answers, and secrets.

## Next research action

Do not spend more provider calls on either failed dispatch runtime. First close
the exact Codex pre-server dispatch difference model-free and independently
validate the external Docker environment. Only a new runtime identity whose
single canary proves server-audited MCP invocation may open a five-arm canary;
only that five-arm gate may authorize another 435-cell attempt. The current
controlled recovery, requested-model creation/rejection/repair, and
selection-only pilot remain the stronger submission evidence.
