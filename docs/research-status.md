# Research status and evidence

Snapshot: **2026-09-07, 16:46 KST**. This is a dated development checkpoint,
not a live dashboard. [Project overview](../README.md) · [한국어 포트폴리오](portfolio.md)

## Publication scope

This update publishes three documentation files only. It does not upload the
latest experimental source tree, benchmark workspaces or private runtime data.
The implementation/test observations below were checked in the development
workspace; not all referenced versioned modules and inputs are in the public
checkout. This document is an evidence summary, not a complete reproducibility
package. SHA-256 identifiers support later comparison; hashes alone do not
provide the underlying evidence or independent attestation.

## Completed official results

These are the same three repeatedly used **development tasks**, not a held-out
sample or an official 87×3 benchmark reproduction. Model: `gpt-5.6-luna`,
reasoning effort: `medium`. Run state can inherit permitted previous-epoch
experience; versions are not independent repetitions.

| Development task | Ordinal | v63 reward | v65 reward | v66 reward |
| --- | ---: | ---: | ---: | ---: |
| fix-druid-loophole-cve | 30 | 0 | Unscored / blocked | 0 |
| setup-fuzzing-py | 73 | 0.16 | 0.50 | 0.16 |
| syzkaller-ppdev-syzlang | 80 | 0 | 0 | 0 |

For v66:

- Official verifier-complete results: **3/3**, finite rewards in `[0,1]`.
- Mean reward: **0.05333** (5.33 on a 0–100 display scale).
- Strict pass, `reward == 1`: **0/3**.
- Positive reward, `reward > 0`: **1/3**; valid official zeros: **2/3**.
- Missing/nonofficial results: **0/3**; earned promotion: **0**.

v66 did not improve the three-task mean over v63. On the shared scored tasks
73/80, v65 mean was 0.25 and v66 mean was 0.08: **−17 percentage points**.
The unscored v65 task30 is not treated as zero or silently added to that paired
comparison. These small, repeatedly observed development results cannot
establish general improvement or an isolated management-policy effect.

Historical C0/G results for these three tasks were zero, but full equivalence
of scorer/image/resources and total computation has not been established.
They are descriptive references, not proof that Merlin causally outperforms
C0/G. There is no current corrected Full87 result.

## v67: implementation verified, actual efficacy pending

v66 gave a corrective turn to completed public-check failures, but not to
observed timeout diagnostics. v67 changes that eligibility rule: a validated
public `CHECK_TIMEOUT` with observed diagnostic bytes can receive **at most one
same-session corrective turn**, within the original remaining deadline.

The timeout remains `UNKNOWN`; it does not become a public quality failure,
official failure explanation, successful result or promotion signal. No raw
diagnostic output or hidden verifier material is transferred by this protocol.
Public recipes, official scoring and the selection policy remain unchanged.

Verified development-workspace checks:

| Check | Observed result | What it does not prove |
| --- | --- | --- |
| Protocol + runtime planes | 10 focused tests passed; independently rerun | Actual task success |
| Fresh dev3 adapter | 7 focused tests passed; independently rerun | Full-suite coverage |
| Provider-free plan | Exit 0; deterministic plan matched | Provider dispatch or score |
| Source consistency | 241 pins checked, including 234 unchanged previous-version pins | Third-party attestation |
| Source handoff | 108 snapshots: G87 + 21 Merlin versions; 3 lessons and 1 retained bundle | All prior experience has proven utility |

Actual fresh run `full87-managed-growth-v67-dev3-r1` started with source epoch12
to13. At this checkpoint, ordinal30 has four observed AEGIS stage calls, a new
quarantined repair candidate, HSEG `READY_FULL` selection with one skill, and
an exact request-bound body verified by the first runtime hook.

That body occurs once in the serialized initial request. This is injection
evidence, not provider-native skill invocation or proof that every instruction
was followed. Public declaration/contract binding is not a completed
container-level functional check. Official v67 results are **pending**, not zero.
Full87 remains gated on development outcomes and readiness.

## Compact evidence index

| Artifact | SHA-256 |
| --- | --- |
| v66 final result, 134,670 bytes | `9147a39d944c641a185a37280d519b61164393b6a2cf4b74a16b003388c36ad3` |
| v66 ledger event tip, 170 events | `eec8237d88f635065c9a3f3839bb6f590167ce7864713cbccccdc698e4c0d0d0` |
| v66-to-v67 authenticated handoff | `bbf0a9ff82903231c3ea5c44ce6715cdc9aa2b58ab039c8e253f762191da5821` |
| v67 protocol source | `e03fe1c6525e2992345209a09fc3d40291ea1096bad649b1c240677fe6ff8c7a` |
| v67 runtime planes source | `f5de979136d8e78c068a2da7b17afa919ed526adecf6426950cef56d1c253af2` |
| v67 adapter source | `75606a42ed3bbfa39a7ff7e3c6b3bc7031dfb1ac5c63f17fffb1fb344f6e1c8c` |
| v67 provider-free plan, 196,492 bytes | `aad3992ca30a2e917d9c03885f2798544d28206b0fa53033e5624d3b0c0343b4` |
| v67 ordinal30 initial request, 13,465 bytes | `c0fad33abf4c7b0bf8b061edf1ae2e1f75aa181ee0994ffeae9223e45228cc2e` |
| v67 ordinal30 selected body, 6,116 bytes | `b3013a1e5ff34c6bc0e9ac992fde8ef050f6013bfc64333066db5ad00baff402` |

The selected body span is `[2760,8876)` in that request. The ledger event hash
and the byte hash of its containing JSON file are different digest domains.
Operational paths, credentials and raw requests are not published here.

## Reporting rules and remaining work

1. Preserve raw official results, including zeros. Missing is not zero.
2. Separate mean reward, positive reward, strict pass, public-check status and
   lifecycle status; do not turn one into another.
3. Attribute bundle results to bundles unless evidence supports individual
   credit. Do not promote a skill merely because a structural test passed.
4. Treat prior-epoch same-task experience as repeated-workload adaptation, not
   held-out transfer. Do not feed hidden tests, answers or raw reasoning traces
   into repair.
5. Measure whether public corrections improve actual official outcomes, then
   evaluate broader coverage, matched baselines and token/time overhead.
6. Publish a scoped reproduction package before claiming independent public
   reproducibility. Skill-shadowing experiments remain separate future work.

**Status: working integration with observable execution evidence; reliable
performance recovery is still unproven.**
