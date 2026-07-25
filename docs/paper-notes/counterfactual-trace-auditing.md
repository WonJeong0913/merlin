> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/paper-notes/counterfactual-trace-auditing.md`

---

# Counterfactual Trace Auditing of LLM Agent Skills

Source: https://arxiv.org/abs/2605.11946

Read status: method-focused extraction, 2026-07-07.

## Core Claim

Pass rate can miss most of what a skill changes. A skill may reshape reads, writes, reasoning, token use, validation behavior, or off-task artifact creation while final pass rate stays unchanged.

For The KING, CTA is the measurement paper. It gives The KING a way to decide whether a generated skill produced a meaningful behavioral delta rather than just a final pass/fail change.

## Key Objects

Paired trace bundle:

```text
B_tau = (q_tau, T_tau^+, T_tau^-, S_tau, r_tau^+, r_tau^-)
```

where:

- `q_tau`: task specification.
- `T_tau^+`: trace with skill.
- `T_tau^-`: trace without skill.
- `S_tau`: skill document.
- `r_tau^+`, `r_tau^-`: final task pass rates.

Typed event:

```text
e = (t, type, reasoning, tool_input, tool_output)
type in {READ, WRITE, EXECUTE, SEARCH, THINK}
```

Pass-rate delta:

```text
Delta P_tau = r_tau^+ - r_tau^-
```

Phase alignment:

```text
d(phi_i^+, phi_j^-) = 1[type(phi_i^+) != type(phi_j^-)]
```

CTA uses dynamic time warping for phase alignment, then TF-IDF cosine similarity over reasoning text for intent-window alignment with threshold:

```text
delta = 0.5
```

Skill Influence Pattern labels:

```text
L(D_k) subseteq {PS, EP, RE, SA, CB}
c_l(D_k) >= theta
theta = 0.50
```

The labels are:

- `PS`: procedural scaffolding, constructive.
- `EP`: edge-case prompting, constructive.
- `RE`: redundant exploration, neutral/costly.
- `SA`: surface anchoring, destructive.
- `CB`: concept bleed, destructive.

## Empirical Result

CTA evaluates 49 SWE-Skills-Bench paired bundles.

```text
Mean Delta P: +0.34 percentage points
Behavioral divergences: 696
SIP instances: 522
Mean SIP/task: 10.7
Mean token overhead: 1.91x
```

Baseline bucket result:

```text
Ceiling tasks (baseline >= 0.9):
  n = 37
  Delta P = -0.5 pp
  SIP = 415/522
  token overhead = 1.64x

Mid tasks (0.5 <= baseline < 0.9):
  n = 10
  Delta P = +3.6 pp
  token overhead = 2.77x

Floor tasks (baseline < 0.5):
  n = 2
  Delta P = 0.0 pp
  token overhead = 2.60x
```

The key warning: final pass rate can be silent while the skill changes behavior heavily.

## The KING Use

CTA should become The KING's skill influence monitor:

```text
no-skill trace
with-skill trace
-> phase/intent/action alignment
-> divergence records
-> skill influence labels
-> lifecycle decision
```

The first MVP can use CTA-lite:

- Compare selected skill vs no-skill traces for the same task family.
- Record file/tool/action deltas.
- Detect wrong-skill, no-skill, distractor, redundant-exploration, and surface-copy signals.
- Use pass rate plus cost and behavior delta, not pass rate alone.

CTA also supports the user-facing thesis: The KING's generated skills must show meaningful change. The change should not be only "there is a skill file"; it must be visible as positive behavior, cost reduction, recovery, validation, or artifact quality improvement.

## Limits

- Single repetition per condition in the reported experiment.
- Single model and benchmark.
- Rule-based SIP detectors are not human-gold validated.
- It does not directly solve adoption or repair; it measures behavior.

## Implementation Difficulty

MVP difficulty: medium.

Full CTA is nontrivial, but CTA-lite can start with deterministic trace deltas:

```text
read/write/tool count
new artifacts
validation commands
cost
skill invocation
wrong-skill or distractor invocation
```

