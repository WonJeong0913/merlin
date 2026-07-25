> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/verifier-trust-contract.md`

---

# Verifier Trust Contract v1

Classification: architecture, experiment, implementation  
Status: implemented for bounded skill repair

## Problem

A verifier's existence does not establish its trustworthiness. A file-existence
check can validate packaging, but cannot prove behavioral utility. A verifier
written by the same model that wrote the candidate can inherit its assumptions.
A held-out verifier exposed to the reviser can become training data.

The KING therefore treats verifier quality as explicit evidence, not a boolean
property.

## Levels

| Level | Allowed use |
| --- | --- |
| `structural` | format, path, file-presence, and packaging gates only |
| `deterministic_behavioral` | repair feedback and promotion when requirements, independence, hiding, and provenance pass |
| `independent_surrogate` | dense repair feedback only; never final promotion by itself |
| `hidden_oracle` | final promotion when deterministic, independent, requirement-covered, and hidden from the reviser |

Every `VerifierTrustProfile` records:

- stable verifier ID;
- deterministic or non-deterministic execution;
- declared and covered requirement IDs;
- behavioral assertion count;
- candidate-author independence;
- whether verifier content is hidden from the reviser;
- a provenance SHA-256.

## Policy

Repair feedback requires non-structural behavioral depth, full declared
requirement coverage, provenance, and either deterministic execution or an
independent verifier author.

Promotion is stricter. It requires a deterministic behavioral or hidden-oracle
verifier, full coverage, candidate-author independence, and hidden verifier
content. Independent surrogate feedback can improve a candidate but cannot
declare it accepted.

This implements the following research lessons without claiming their results:

- SWE-Skills-Bench maps explicit acceptance criteria to executable tests and
  rejects file-existence-only checks as sufficient behavioral verification.
- EvoSkills isolates its surrogate verifier from the generator and withholds
  hidden ground-truth test content.
- SkillLearnBench distinguishes specification quality, execution trajectory,
  and task outcome, and finds self-feedback-only iteration vulnerable to drift.

## Current integration and boundary

`src/the_king/verifier_trust.py` implements the profile and assessment checks.
`src/the_king/skill_repair.py` requires a trust profile for every target,
held-out, and library-regression verifier before any reviser runs. The recorded
repair fixture passes three profiles and a combined `verifier_trust` promotion
gate.

This v1 does not automatically derive requirement IDs from natural-language
specifications, audit a model-generated test for semantic correctness, or
calibrate probabilistic judges. Those remain research tasks. The current
contract makes such missing evidence explicit and fail-closed.
