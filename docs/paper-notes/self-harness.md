> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/paper-notes/self-harness.md`

---

# Self-Harness: Harnesses That Improve Themselves

Source: https://arxiv.org/abs/2606.09498

Read status: method-focused extraction, 2026-07-07.

## Core Claim

Self-Harness treats the harness around a fixed model as the object of improvement. The loop mines recurring failure patterns from traces, proposes bounded harness edits, evaluates candidate harness variants, and promotes only changes that improve at least one split without degrading the other.

For The KING, Self-Harness is the closest harness-update competitor. The KING should not copy the whole system at MVP stage, but it should adopt the acceptance-rule mindset.

## Key Objects

Fixed setup:

```text
M: fixed model
h_t: current harness
D_in: held-in split
D_ho: held-out split
E: evaluator
```

Trace record:

```text
r_i = (x_i, tau_i, y_i, z_i)
z_i = E(x_i, tau_i, y_i)
R_t = {r_i}_{i=1}^{|D_in|}
F_t = {r_i in R_t | z_i = fail}
```

Failure signature:

```text
phi(r_i) = (c_i, q_i, m_i)
C_phi = {r_i in F_t | phi(r_i) = phi}
```

where:

- `c_i`: terminal verifier-level cause.
- `q_i`: causal status of the relevant behavior.
- `m_i`: abstract agent mechanism exposed by the trace.

Proposal set:

```text
P_t = {(Delta_j, a_j)}_{j=1}^K
h_t^(j) = Delta_j(h_t)
```

where `a_j` is an audit record describing the target failure pattern, changed harness surface, expected effect, and regression risk.

Acceptance rule:

```text
Delta_in^(j) = P_in(h_t^(j)) - P_in(h_t)
Delta_ho^(j) = P_ho(h_t^(j)) - P_ho(h_t)

accept iff:
  Delta_in^(j) >= 0
  Delta_ho^(j) >= 0
  max(Delta_in^(j), Delta_ho^(j)) > 0
```

Accepted compatible edits can be merged. Rejected edits remain logged but do not change the active harness.

## Empirical Result

Self-Harness evaluates on Terminal-Bench-2.0 with three fixed model backends.

Held-out pass-rate gains:

```text
MiniMax M2.5:       40.5% -> 61.9%
Qwen3.5-35B-A3B:    23.8% -> 38.1%
GLM-5:              42.9% -> 57.1%
```

Held-in pass-rate gains:

```text
MiniMax M2.5:       43.0% -> 50.0%
Qwen3.5-35B-A3B:    15.1% -> 36.0%
GLM-5:              47.7% -> 57.0%
```

Accepted edits are small and auditable, such as creating required artifacts earlier, checking structured tool outputs, breaking stalled tool loops, artifact middleware, missing-file recovery, dependency prechecking, and moving from exploration to implementation.

## The KING Use

The KING should use Self-Harness as a policy-update pattern:

```text
failure clusters
-> proposed harness policy edit
-> held-in validation
-> held-out regression gate
-> accept/reject
-> logged lineage
```

In The KING, editable harness surfaces should be narrower than full Self-Harness:

- provisioning policy
- selector prompt/policy
- validation gate threshold
- lifecycle action rules
- shadowing detector threshold
- repair principle store

This keeps the MVP focused on skill-harness management instead of broad harness optimization.

## Limits

- The paper studies bounded harness edits under fixed benchmarks, not open-ended self-improvement.
- The method depends on verifier and trace quality.
- Pass-rate non-regression alone may be too weak for high-stakes harness changes.
- Benchmark-specific failure patterns may be accepted unless the held-out split is strong.

## Implementation Difficulty

MVP difficulty: medium-high.

The KING should start with manual or semi-automatic harness policy edits behind the Self-Harness acceptance rule. Fully automatic edit proposal and merging should come after the skill validation/provisioning loop is stable.

