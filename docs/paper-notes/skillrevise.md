> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/paper-notes/skillrevise.md`

---

# SkillRevise: Trace-Conditioned Skill Revision

Source: https://arxiv.org/abs/2606.01139

Read status: detailed extraction, 2026-07-07.

## Core Claim

SkillRevise addresses cold-start skill improvement: the system begins with an imperfect LLM-authored skill, executes it, diagnoses skill-level defects from trace and verifier evidence, retrieves reusable repair principles, edits the skill, re-executes candidates, and selects the first verifier-passing skill within a bounded revision budget.

For The KING, this is the closest direct method source for the repair loop.

## Key Objects

Diagnosis:

```text
D_i = (V_i, A_i, K_i)
```

where:

- `V_i`: verifier specification and observable acceptance contract.
- `A_i`: failure attribution, probable cause, defect label, and whether the skill contributed to failure.
- `K_i`: preservation constraints: already-passing checks and behaviors that should not be broken.

Revision operator:

```text
(S_hat_{i+1}, z_i) = R_phi(S_i, D_i, P_i)
```

where:

- `S_i`: current skill.
- `D_i`: current diagnosis.
- `P_i`: bound repair principles.
- `S_hat_{i+1}`: candidate revised skill.
- `z_i`: revision trace linking evidence to edit.

Execution result:

```text
e_i = (tau_i, v_i, r_i, c_i) = Phi(T, S_i, pi_theta)
```

where:

- `tau_i`: trajectory.
- `v_i`: verifier feedback.
- `r_i`: reward or pass/fail outcome.
- `c_i`: costs such as tokens, tool calls, steps, and latency.

Repair-principle retrieval:

```text
q_i = Q(T, D_i)
C_i = Retrieve_m(q_i, M)
score(p) = w_s / (kappa + rank_s(p)) + w_d / (kappa + rank_d(p))
P_i = Bind(C_i, D_i)
```

Selection:

```text
H_<=B = {S_0} union S_<=B
P_<=B = {S in H_<=B : succ(S,T)=1}

S*_<=B =
  arg min_{S in P_<=B} idx(S), if P_<=B is non-empty
  arg max_{S in H_<=B} U(S,T), otherwise
```

Utility fallback:

```text
U(S,T) =
  alpha Delta_succ(S,T)
  + beta g_succ(S,T) Delta_eff(S,T)
  + gamma Delta_trans(S,F)
  - lambda C_intf(S,F)
```

with:

```text
g_succ(S,T) = 1[succ(S,T)=1]
```

This prevents cheap but failed candidates from looking good just because they stopped early.

## Empirical Result

The headline SkillsBench result:

```text
No skill success: 31/86 = 36.05%
Revision v3 success: 53/86 = 61.63%
```

Across 206 evaluated tasks, Revision v3 improves over no-skill execution for all five executor families reported:

```text
GPT-5.5:          79/206 -> 115/206
Opus-4.7:         55/206 -> 100/206
Kimi-2.6:         47/206 -> 86/206
Qwen-3.6-Plus:    33/206 -> 77/206
DeepSeek-V4-Pro:  49/206 -> 95/206
```

The ablation result matters for The KING:

```text
No ablation:          53/86
w/o Diagnosis:        28/86
w/o Preserve Ledger:  42/86
w/o Execution Anchors:44/86
w/o Principles:       45/86
Free-form Revision:   52/86
```

Diagnosis is the dominant component. Free-form revision can still succeed, but structured traces make the edit attributable and inspectable.

## The KING Use

The KING should adopt this principle:

```text
Do not deploy the newest revision by default.
Deploy the first verifier-passing revision within budget.
If none pass, use utility fallback or no-skill fallback.
```

The direct The KING module mapping:

- `Diagnosis` -> Failure Analyzer.
- `Principle Memory` -> Repair Principle Store.
- `Revision Operator` -> Skill Candidate Generator/Reviser.
- `Preserve Ledger` -> Regression Gate input.
- `Execution Anchors` -> Validator/action checkpoints.
- `Success-prioritized selection` -> Lifecycle adoption rule.

The KING should extend SkillRevise in two places:

1. Add no-skill fallback when skill-conditioned execution harms a task.
2. Add provisioning and shadowing control across a library, because SkillRevise mainly repairs one candidate for one task setting.

## Limits

- Strong dependence on verifier quality.
- More model calls and tool use are required during revision.
- The reported `maxrev3` setup does not fully solve no-skill fallback.
- Larger library routing, dynamic provisioning, and changing environments are future work in the paper. Those are central for The KING.

## Implementation Difficulty

MVP difficulty: medium-high.

The expensive part is not data structures; it is trace capture, verifier design, and repeat execution. A first The KING prototype can implement a simplified SkillRevise loop with:

```text
trace -> diagnosis JSON -> candidate edit -> verifier -> first-pass selection
```

