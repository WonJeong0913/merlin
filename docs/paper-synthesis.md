> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/paper-synthesis.md`

---

# Paper Synthesis

This file tracks how the currently discussed papers support The KING.

Important caveat: exact wording, tables, and experimental details should be checked against the PDFs before being cited in a draft.

## Synthesis Claim

The paper threads point to the same bottleneck from different angles:

- SkillsBench shows that self-generated skills can fail.
- SkillOps shows that accumulated skills create maintenance and quality debt.
- More Skills, Worse Agents? shows that larger libraries can actively hurt through skill shadowing.
- AIP shows that skills should be structured into schema-validated, addressable execution artifacts rather than left as fragile prose.
- SkillRevise shows that generated skills can be improved through trace-conditioned diagnosis, repair principles, and first-success verifier selection.
- CTA shows that final pass rate can hide large skill-induced behavior changes.
- Self-Harness shows how harness policy updates can be gated as empirical state transitions.

Together, they motivate a self-managing skill-harness agent.

## SkillsBench

Local source noted by prior conversation:

`/Users/jeong-won/Downloads/SkillsBench.pdf`

### Role in The KING

SkillsBench is the problem-setting paper for The KING. It supports the claim that automatically generated skills should not be trusted just because they are generated from experience.

### Current Takeaways

- Curated skills can help.
- Self-generated skills may provide little benefit or can underperform no-skill baselines.
- Failures may come from time spent generating skills, weak harness use, incorrect diagnosis by the skill creator, poor discovery/provisioning, and vague or incomplete skill content.
- Its current 87-task aggregate compares task-body-only no-skill against a
  complete curated `environment/skills/` bundle loaded natively before the task
  instruction, at temperature 0 and three trials per task-condition cell.
- Therefore a bounded script generator, YAML-frontmatter exposure, truncated
  skill prompt injection, verifier-feedback repair, or a single uncontrolled
  provider trial is a different harness condition and must be reported as such.
- Normalized gain:

`g = (p_skill - p_vanilla) / (1 - p_vanilla)`

### How The KING Uses It

The KING uses SkillsBench as evidence that skill generation needs validation and harness management. The response is not "generate more skills" but "govern the generated skill before and after adoption."

## SkillOps

Local source noted by prior conversation:

`/Users/jeong-won/Downloads/SkillOps.pdf`

### Role in The KING

SkillOps provides vocabulary for treating a skill library like a maintained software ecosystem.

### Current Takeaways

- Skills can be represented with a contract:

`s = (P, O, A, V, F)`

where:

- `P`: preconditions
- `O`: operation
- `A`: artifact
- `V`: validator
- `F`: failure modes

- A library can be represented as:

`L = (S, R)`

- Library health can be measured with a weighted health score:

`H(L)=1/|S| sum_s [w_U U(s)+w_R(1-R(s))+w_C C(s)+w_F(1-F(s))+w_G(1-G(s))]`

- Maintenance actions include merge, repair, retire, add-validator, and add-adapter.

Important limits from the paper must carry into The KING's claims: parts of the
evaluation rely on structured contracts, gold PDDL or half-synthetic settings;
semantic redundancy remains difficult; and CGPD produced invalid results in the
reported setup. SkillOps is an architectural anchor, not proof that The KING's
maintenance loop already works empirically.

### How The KING Uses It

The KING can inherit the idea that skills need contracts, validators, and lifecycle actions. The gap is that The KING connects these ideas to skill generation, task-conditioned provisioning, selection, and harness policy updates in one closed loop.

## More Skills, Worse Agents?

Local source noted by prior conversation:

`/Users/jeong-won/Downloads/More Skills, Worse Agents?.pdf`

### Role in The KING

This paper is the direct basis for The KING's provisioning and selection problem.

### Current Takeaways

- More skills can reduce performance rather than improve it.
- The key mechanism is skill shadowing.
- Wrong skill invocation and no skill invocation are central failure modes.
- In the current project framing, context overhead matters less than selection/provisioning failure.
- Invocation set `I` means skill bodies actually loaded/called in the trajectory,
  not retrieved, selected, prompt-exposed, or model-self-reported IDs.
- The paper's naive library condition exposes every entry's name and description;
  a top-k provisioner is already an intervention and cannot stand in for that baseline.
- `Delta_ctx` and `Delta_shd` require the n/m/o event probabilities and their
  conditional pass rates, with zero denominators reported as undefined.

Notation from prior thread:

- skill: `(n_i, d_i, b_i)`
- context: `C(q,S)=(q,D(S))`
- descriptors: `D(S)={(n_i,d_i)}`
- oracle set: `S*(q)={S_i in S | p(q,{S_i}) - p(q,emptyset) >= tau}`
- library-induced drop: `Delta(q,S)=p(q,S*(q))-p(q,S)`
- decomposition: `Delta = Delta_ctx + Delta_shd`
- empirical shadowing rate `pi_m`: fraction of trajectories invoking at least one distractor skill.

### How The KING Uses It

The KING should not present the whole library to the model. It should retrieve, filter, rank, and gate a small candidate set, then monitor whether skill invocation was useful or harmful.

## Gap Statement

Existing threads cover important pieces:

- SkillsBench: shows generated skill failure.
- SkillOps: manages skill-library quality and debt.
- More Skills: measures shadowing from large skill sets.
- AIP: structures a skill artifact.
- SkillRevise: repairs one imperfect skill from traces.
- CTA: measures behavior change from a skill.
- Self-Harness: gates harness policy updates.

The open gap for The KING:

No single system treats skill generation, provisioning, selection, validation, lifecycle management, and harness policy update as one closed-loop agentic system.

## The KING Positioning

The KING should be presented as:

- more than a skill generator
- more than a static skill library manager
- more than a shadowing measurement framework

It is a self-managing harness that controls how skills are created, admitted, exposed, invoked, evaluated, and retired.

## AIP

Source:

`https://arxiv.org/abs/2606.04781`

### Role in The KING

AIP provides the artifact format direction. A generated skill should become a structured, addressable, testable artifact rather than a prose-only instruction.

### Current Takeaways

- A skill can be represented as a directed execution graph with named steps, typed input/output edges, and script/reference bindings.
- Schema validation turns skill creation into a checkable artifact-building step.
- Node-level addressability makes repair local.
- Empirical result on a 27-task SkillsBench sample:

`Mean reward 0.599 -> 0.705`, `pass rate 53.3% -> 67.4%`, `Wilcoxon p=0.011`.

### How The KING Uses It

The KING should implement an AIP-lite skill schema first. Full graph runtime enforcement can wait.

## SkillRevise

Source:

`https://arxiv.org/abs/2606.01139`

### Role in The KING

SkillRevise provides the trace-conditioned skill repair loop.

### Current Takeaways

Diagnosis:

`D_i=(V_i,A_i,K_i)`

Execution:

`e_i=(tau_i,v_i,r_i,c_i)=Phi(T,S_i,pi_theta)`

Revision:

`(S_hat_{i+1},z_i)=R_phi(S_i,D_i,P_i)`

Selection:

`H_<=B={S_0} union S_<=B`

`P_<=B={S in H_<=B: succ(S,T)=1}`

`S*_<=B = arg min_{S in P_<=B} idx(S)` if any candidate passes; otherwise choose by utility.

### How The KING Uses It

The KING should revise generated skills only through trace/verifier evidence and deploy the first verifier-passing candidate, not the latest rewrite. The KING extends this with no-skill fallback and library-level provisioning/shadowing controls.

## Counterfactual Trace Auditing

Source:

`https://arxiv.org/abs/2605.11946`

### Role in The KING

CTA provides the behavior-delta measurement layer.

### Current Takeaways

Paired trace bundle:

`B_tau=(q_tau,T_tau^+,T_tau^-,S_tau,r_tau^+,r_tau^-)`

Pass-rate delta:

`Delta P_tau=r_tau^+ - r_tau^-`

CTA found `+0.34 pp` mean pass-rate change but `696` divergences and `522` Skill Influence Pattern instances across 49 paired traces.

### How The KING Uses It

The KING should not accept a generated skill just because final pass rate is neutral. It should ask whether the skill produced positive behavior, harmful surface anchoring, concept bleed, redundant exploration, cost increase, or premature closure.

## Self-Harness

Source:

`https://arxiv.org/abs/2606.09498`

### Role in The KING

Self-Harness provides the conservative promotion rule for harness policy updates.

### Current Takeaways

Harness candidate:

`h_t^(j)=Delta_j(h_t)`

Split improvements:

`Delta_in=P_in(h_t^(j))-P_in(h_t)`

`Delta_ho=P_ho(h_t^(j))-P_ho(h_t)`

Acceptance:

`Delta_in>=0 and Delta_ho>=0 and max(Delta_in,Delta_ho)>0`

### How The KING Uses It

The KING should only update provisioning, selection, validation, lifecycle, or shadowing policy through a held-in/held-out non-regression gate. Full automatic harness rewriting remains out of MVP scope.
