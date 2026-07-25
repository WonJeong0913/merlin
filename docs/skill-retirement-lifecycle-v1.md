> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/skill-retirement-lifecycle-v1.md`

---

# Bounded Skill Retirement Lifecycle v1

## Status

Implemented and model-free verified on 2026-07-20.

This contract closes a conservative `hide → observe → retire or rollback`
slice. It does not delete files. A promoted retirement retains the skill as an
auditable `RETIRED` tombstone.

## Why retirement is separate from hide

`HIDDEN` is a reversible routing action: the skill remains in the library but
cannot be provisioned. `RETIRED` is a stronger lifecycle conclusion that the
hidden skill has remained unused while the protected library behavior stayed
healthy. The stronger action therefore requires stronger evidence.

## Inputs

- an immutable library tuple;
- exactly one target skill already in `HIDDEN` state;
- at least two independent observation windows;
- each window bound to the exact library snapshot and a distinct raw-trace
  SHA-256;
- complete invocation evidence reporting zero target selection and invocation;
- the same ordered protected cases and verifier IDs in every window;
- deterministic behavioral or hidden-oracle verifier trust profiles;
- a library evaluator that can run the frozen cases before and after staging.

## Ordered gates

| Gate | Requirement |
| --- | --- |
| R0 `retirement_eligibility` | target exists and is already hidden |
| R1 `independent_observation_windows` | at least two distinct windows and traces |
| R2 `complete_zero_use_evidence` | complete invocation evidence; selection=0 and invocation=0 |
| R3 `verifier_trust` | promotion-grade deterministic/hidden verifier profiles |
| R4 `baseline_library_clean` | all protected cases pass before retirement |
| R5 `same_verifier_non_regression` | identical case/verifier coverage passes without score loss after staging |
| R6 `copy_on_write_isolation` | only target lifecycle status changes in the provisional copy |

All seven gates must pass. Otherwise the resolved library is the original
hidden parent and the recommended action is `retain_hidden`.

## Fail-closed boundaries

- active, rejected, candidate, repair, or already-retired skills cannot skip
  the hidden observation stage;
- duplicate windows or raw traces are rejected;
- snapshot, case coverage, verifier ID, score, or trust drift is rejected;
- incomplete invocation evidence is a failed retirement gate, not proof of
  zero use;
- physical deletion is outside this v1 contract;
- the API does not claim provider-native invocation when the provider did not
  expose it;
- this is a bounded deterministic lifecycle contract, not a long-term
  production retention policy or empirical retirement-quality result.

## Implementation and tests

- `src/the_king/skill_retirement.py`
- `tests/test_skill_retirement.py`

The focused suite covers successful COW retirement, same-verifier rollback,
incomplete invocation evidence, snapshot/trace/trust tamper, and illegal state
entry.
