# Merlin Cost-Reinvestment Flywheel

Status date: 2026-07-23  
Classification: thesis, architecture, experiment, implementation

## Decision

The long-term product and research plan is:

> Reuse and improve skills to reduce the verified cost of completing recurring
> work, then reinvest a bounded fraction of those realized savings into better
> validation, routing, repair, maintenance, and harness-policy evolution.

This is the dynamic answer to two static strategies:

- “write each skill better” treats skill content as the main object;
- “keep only a few skills” avoids library growth instead of managing it;
- Merlin treats the library and harness as an evolving governed system.

The existing `80/20` priority remains above this economic mechanism:

```text
80% harness governance and evolution:
  provisioning
  selection and abstention
  validation and actual-invocation evidence
  shadowing control
  promotion / quarantine / hide / repair / merge / retire / rollback
  typed HarnessX-style hook and processor evolution
  growing-library health and regression control

20% minimal contract-conformant skill supply:
  one fixed Create-Skill-style candidate path
  basic SKILL.md-centered portable artifact
  required contract fields and validator interface
```

Skill prose or generator quality is not an optimization target. Contract repair,
including diagnosis, bounded candidate amendment, evidence access, validation,
promotion, and rollback, is a lifecycle operation in the 80%. Cost
reinvestment is the fuel for that control plane, not a replacement research
thesis.

The plan does not replace the existing thesis. It makes its long time axis and
economic mechanism explicit:

```text
self-generated skill failure
is a skill-harness management problem

verified reuse savings
-> bounded harness investment
-> better admission / provisioning / selection / validation / lifecycle
-> safer and more useful skills
-> more verified reuse savings
```

## Existing Formal Core

The new flywheel must reuse, not overwrite, the project's existing equations.

### 1. Skill and library objects

From the current SkillOps-derived project notation:

```text
s = (P, O, A, V, F)
L = (S, R)
```

`P` is preconditions, `O` operation, `A` artifact, `V` validator, `F`
failure modes, `S` the skill set, and `R` cross-skill relations.

The existing diagnostic library-health summary is:

```text
H(L)
= 1/|S| sum_s [
    w_U U(s)
  + w_R (1-R(s))
  + w_C C(s)
  + w_F (1-F(s))
  + w_G (1-G(s))
]
```

This remains a diagnostic score. It is not a promotion gate and must not hide
which component improved or regressed.

### 2. Task utility

For condition `c` and evaluation set `E`:

```text
p_c(E) = mean task success under c on E

G_skill(c,E)
= (p_c(E) - p_no_skill(E))
  / (1 - p_no_skill(E))
```

If `p_no_skill(E)=1`, use the raw delta. Existing generated-skill and
management gains are instances of the same form:

```text
G_gen(t,E)
= (p_gen_t(E) - p_no_skill(E))
  / (1 - p_no_skill(E))

G_king(t,E)
= (p_king_t(E) - p_naive_t(E))
  / (1 - p_naive_t(E))
```

### 3. Library growth and shadowing

For actual skill invocation set `I` and restricted empirical oracle set
`S*_restricted(q)`:

```text
pi_o
= Pr(emptyset != I subseteq S*_restricted(q)
     | S*_restricted(q) != emptyset)

pi_m
= Pr(I contains at least one non-oracle skill
     | S*_restricted(q) != emptyset)

pi_m = pi_wrong + pi_mixed
```

The existing More Skills decomposition remains the causal diagnostic:

```text
Delta(q,S) = p(q,S*(q)) - p(q,S)
Delta = Delta_ctx + Delta_shd

Delta_ctx
= pi_n* (rho_n* - rho_n)
  + pi_o* (rho_o* - rho_o)

Delta_shd
= (pi_n* - pi_n) rho_n
  + (pi_o* - pi_o) rho_o
  - pi_m rho_m
```

Retrieved, exposed, or selected skill IDs are not substitutes for `I`.
Paper-grade `pi_o`, `pi_m`, and the decomposition require hash-valid actual
invocation evidence and explicit denominators.

### 4. Harness update gate

For harness candidate `h_t^(j)=Delta_j(h_t)`:

```text
Delta_in = P_in(h_t^(j)) - P_in(h_t)
Delta_ho = P_ho(h_t^(j)) - P_ho(h_t)

accept only if:
Delta_in >= 0
and Delta_ho >= 0
and max(Delta_in, Delta_ho) > 0
```

The implemented M3-K gate is stricter: complete paired execution, reconstructable
candidate state, actual-invocation completeness, held-in/held-out/regression
non-regression, positive primary improvement, shadowing non-regression, and a
cost-ratio ceiling must all pass. This hard gate remains authoritative.

## Unified System State

Use `X_t`, rather than overloading the existing `H(L)` health notation:

```text
X_t = (L_t, P_t, V_t, M_t, D_t, B_t)
```

Where:

- `L_t`: versioned skill library and lifecycle state;
- `P_t`: provisioning and selection policy;
- `V_t`: frozen verifier epoch and regression policy;
- `M_t`: typed hook/processor manifest;
- `D_t`: dated model-provider catalog and health/cost evidence;
- `B_t`: authorized reinvestment budget.

One evolution step is:

```text
candidate_t
= Propose(X_t, trace_t, verifier_feedback_t)

X_(t+1)
= Gate(candidate_t | frozen contract K_t)
   ? PromoteCOW(candidate_t)
   : RollbackExact(X_t)
```

No scalar score can bypass this gate. Weighted scores such as the existing
route risk or Harness Growth Index are dashboards for diagnosis and ranking
only.

## Economics

### Account-auth experiments first

The first experiments use authenticated Codex/Claude-style CLI accounts rather
than paid API keys. A subscription fee must not be divided into an invented
per-task cash price. The initial spendable unit is therefore a provider turn
under one frozen provider, model, effort, quota window, verifier epoch, and
evaluation contract:

```text
S_i^turn
= 1[y_i^0 = 1 and y_i^K = 1]
  * max(0, turns_i^0 - turns_i^K)

B_(t+1)^turn
= floor(rho * sum S_i^turn)
  - governance_turns
  - reserve_turns
```

The result is clamped to zero and capped by policy. Reported tokens and latency
remain diagnostic secondary measures. Mixed model, effort, quota-window, or
verifier-epoch observations cannot authorize one pooled turn budget. USD
economics below remain dormant until a dated API pricing contract is actually
used.

### 1. Matched task observation

For task observation `i` under one frozen evaluation contract:

```text
C_i^0       baseline execution cost
C_i^K       Merlin managed execution cost
G_i         governance cost
y_i^0       baseline verified success in {0,1}
y_i^K       managed verified success in {0,1}
A_i         estimated avoided-failure value, optional
```

Governance cost includes the extra model, tool, verifier, repair, replay, audit,
and maintenance work attributable to the harness decision. Report those
components separately when available.

### 2. Spendable direct savings

```text
S_i
= 1[y_i^0 = 1 and y_i^K = 1]
  * max(0, C_i^0 - C_i^K)
```

This is deliberately conservative:

- a cheaper failure is not savings;
- an unmatched task is not savings;
- a changed verifier or evaluation contract is not matched evidence;
- an estimated avoided failure is not cash available to spend.

### 3. Direct and estimated value

```text
N_i^direct = S_i - G_i
N_i^total  = S_i + A_i - G_i
```

`N_i^direct` is the auditable economic result. `N_i^total` is a separately
labeled impact estimate. `A_i` must never silently fund automatic actions.

### 4. Reinvestment budget

For rolling window `W_t`, reinvestment fraction `0 <= rho <= 1`, reserve `R`,
and per-decision cap `B_max`:

```text
B_(t+1)
= min(
    B_max,
    max(
      0,
      rho * sum_(i in W_t) S_i
      - sum_(i in W_t) G_i
      - R
    )
  )
```

The narrow break-even ratio proposed in the conversation becomes:

```text
E_T = sum_(i<=T) G_i / sum_(i<=T) S_i
```

It is defined only when cumulative verified savings are positive. `E_T < 1`
means direct verified savings have covered governance spend over that horizon.
It does not include `A_i` and does not prove task-quality improvement.

## Verifier Drift and Goodhart Boundary

A verifier remains frozen inside an evaluation epoch:

```text
V_t = constant while skills or harness candidates compete
```

A candidate verifier cannot approve itself. Replacing `V_t` is a separate
high-risk transition and requires:

```text
new verifier epoch ID
and sufficient frozen-corpus replay
and zero disallowed replay regression
and an independent oracle or hidden evaluator pass
and explicit human approval
```

After a verifier upgrade, results from different epochs are not pooled without
an explicit bridge study. Old skills and harness variants are replayed against
the new epoch before new longitudinal claims are made.

## Longitudinal Hypothesis

The flywheel succeeds only when all three layers move together over time:

```text
utility:
  p_king non-regresses and held-out gain is positive

harness quality:
  pi_o rises, pi_m and regression fall,
  actual-invocation evidence remains complete

economics:
  cumulative S > cumulative G,
  B_t becomes positive without weakening the frozen gates
```

The claim is not “more maintenance is always better.” The claim is:

> A bounded, evidence-driven harness can make an expanding skill library
> economically self-supporting while preserving or improving verified utility.

The intended scaling result is not a permanently small library. It is a
library that may keep growing while bounded provisioning, lifecycle control,
and hook/processor evolution prevent growth from turning into uncontrolled
shadowing, pollution, and regression.

## Experiment Contract

Use the existing frozen-snapshot conditions and add cost accounting; do not
invent a disconnected benchmark:

```text
C0     no-skill
C1     curated skill
C3     generated skill + validation/regression
C5/C7  expanded naive accumulation
C8-H   usage/recency management
C9     Merlin provisioning + lifecycle
C10    Merlin + gated harness-policy update
```

The headline comparison remains:

```text
naive library + generated(t1) skills
vs
Merlin managed library + the same generated(t1) skills
```

For each matched cell, retain:

- common contract, task, library, provider/model, effort, verifier-epoch, and
  repeat identities;
- task success and exact verifier result;
- provisioned, selected, executed, and actually invoked skill evidence;
- input/output/cache tokens, tool cost, latency, and governance sub-cost;
- lifecycle or harness action, candidate/parent hashes, and rollback result;
- `S_i`, `G_i`, `N_i^direct`, optional `A_i`, and reinvestment decision.

Primary longitudinal reports:

- pass-rate lift, `G_king`, `pi_o`, `pi_m`, SRR, OSR;
- held-out and library regression;
- cumulative verified savings and governance spend;
- `E_T`, time to direct payback, and authorized `B_t`;
- provider-role mix and fallback rate;
- verifier-epoch changes and replay outcomes.

The existing 75 observed full-library cells cannot establish this flywheel:
they contain no verifier passes and no trusted actual invocation calls. A new
matched longitudinal campaign is required.

## Implementation Mapping

| Formal object | Current implementation | Next integration |
|---|---|---|
| `p`, normalized gain | `src/merlin_harness/metrics.py` | emit in longitudinal report |
| `pi_o`, `pi_m`, `Delta_ctx`, `Delta_shd` | `metrics.py`, `management.py` | require real provider invocation events |
| hard harness gate | `experiments/skillsbench/harness_policy_evaluation.py` | attach reinvestment authorization |
| provider usage and cost | `provider_runtime.py`, `executors.py` | persistent dated provider ledger |
| `S_i`, `G_i`, `B_t` | `cost_governance.py` | durable hash-bound evidence format |
| verifier-epoch gate | `cost_governance.py` | connect to HarnessX high-risk approval |
| personal product | current macOS beta | provider setup, memory, tools, scheduler |
