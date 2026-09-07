# Merlin

**A self-managing skill harness that turns execution experience into controlled
skill repair and lifecycle decisions.**

Merlin is an independent research-engineering project exploring how an agent
can generate, select, use, validate and evolve reusable skills around a fixed
language model. The emphasis is the management layer, not model training or
simply accumulating more prompt text.

[한국어 포트폴리오](docs/portfolio.md) ·
[Research status & evidence](docs/research-status.md) ·
[Python core](src/merlin_harness) · [Tests](tests)

> **Research prototype — documentation snapshot: 2026-09-07.**
> End-to-end development runs are underway. General benchmark superiority and
> reliable performance improvement have **not** been established. This update
> publishes documentation, not the complete latest experimental implementation
> or a self-contained reproduction package.

## The question

Can a harness improve an agent by managing the contracts, dependencies and
execution history of its skills—not merely by adding more skills?

Merlin investigates three linked problems:

- Selecting a useful, compatible skill set for the public task requirements.
- Repairing failed skills while preserving their required contracts and lineage.
- Deciding when evidence supports a trial, promotion, quarantine or retirement.

## How the development system works

```text
G-generated seed library + permitted prior-epoch experience
                         ↓
          AEGIS diagnosis → plan → candidate repair
                         ↓
          quarantine + public contract validation
                         ↓
          HSEG dependency/compatibility planning
                         ↓
          provision skills → bind exact request bytes
                         ↓
          solve → public checks → bounded correction
                         ↓
          official verifier → lifecycle / next-epoch experience
```

AEGIS is the diagnosis-and-repair controller. HSEG represents skill contracts
and relationships for planning. Typed runtime hooks connect those decisions
to request and execution evidence. A public check, request inclusion, native
skill invocation and official task success are different observations.

The current development lineage starts with **87 G-generated skills**, not
the official C1 skill library, and carries eligible Merlin repair versions.
At least one selected skill is required on this managed execution path.
New candidates enter quarantine/trial; structural validity alone does not
earn official-success promotion.

## Current snapshot

| Area | Evidence at this snapshot |
| --- | --- |
| AEGIS/HSEG integration | Actual repair, selection and contract-binding records observed; repair efficacy remains unproven |
| Exact skill delivery | Selected raw bodies bound to serialized requests; not relabeled as native invocation |
| Public feedback | Same-session corrective turn bounded by the original deadline; timeout remains `UNKNOWN` |
| Prior run, v66 | 3/3 official results; rewards `0 / 0.16 / 0`; strict pass `0/3` |
| Latest completed run, v67 | 3/3 official results; rewards `0 / 0 / 0`; one corrective turn per task; no earned promotion |
| Current development, v68 | More specific bounded public diagnostics for correction and next-epoch learning; integration incomplete, not an actual result |
| Wider evaluation | Corrected Full87 run not launched; performance recovery remains an open goal |

The v67 focused protocol, runtime-plane and adapter suites passed **17 tests**
in the development workspace, with independent re-execution. This is a focused
regression result, not full-suite coverage or evidence of task success.

See [research status](docs/research-status.md) for denominators, evidence hashes,
comparison limits, publication boundaries and the next milestones.

## Engineering focus

- Python orchestration with typed contracts and explicit state transitions.
- Isolated task execution through Docker and a BenchFlow/ACP integration.
- Content-addressed skill versions, exact request binding and append-only history.
- Public failure-experience transfer without hidden verifier material or raw reasoning traces.
- Accounting that separates official zero, missing results, partial reward and strict success.
- Forward-version fixes that preserve previous results instead of rewriting them.

## Repository and reproduction boundary

The active Python namespace is `src.merlin_harness`. The repository also retains
historical CLI/UI and earlier research components; they should not be confused
with the latest development runner. Older dated roadmaps describe their own
milestones; [research status](docs/research-status.md) is the current summary.

The September experimental runner, pinned benchmark inputs and operational
artifacts are not all published in this documentation update. A fresh clone
therefore does **not** reproduce v66/v67 by itself. Some historical tests depend
on intentionally excluded fixtures. Do not interpret this repository as a
turnkey benchmark launcher or the latest full test suite as verified here.

Credentials, private provider logs, raw trajectories, hidden test material and
answer artifacts are excluded from this update. No performance/SOTA badge or
leaderboard-replication claim is made.
