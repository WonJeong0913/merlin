> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/skill-merge-lifecycle-v1.md`

---

# Bounded duplicate-skill merge lifecycle v1

## Scope

The KING merge v1 handles one conservative case: two active skills have the
same routing scope and produce exactly the same verifier-bound outputs, so one
can remain active while the other becomes a non-provisionable alias tombstone.

It does not ask a model to synthesize a combined skill body. Semantic fusion of
partially overlapping skills remains deferred.

## Frozen evidence

Before staging a merge, the manager requires:

- two distinct active skill IDs in one hash-bound library snapshot;
- at least two distinct raw-trace hashes;
- complete invocation evidence;
- non-zero overlapping selection and invocation observations;
- matching trigger, do-not-use constraints, validators, and expected artifacts;
- disjoint equivalence and protected library-regression cases;
- promotion-grade deterministic or hidden verifiers.

Both skills run against the same equivalence cases. A case is equivalent only
when both skills pass with the same verifier ID, score, and exact output hash.

## Copy-on-write transition

The provisional library keeps the canonical artifact byte-identical. The
redundant artifact keeps its original content but changes to `RETIRED` and
receives one hash-bound `merge_tombstone` record containing:

- canonical skill ID and artifact hash;
- redundant pre-merge artifact hash;
- diagnosis hash;
- distinct evidence trace hashes;
- equivalence case IDs.

No artifact is physically deleted. Unrelated skills must remain byte-identical.

## Promotion and rollback

Nine gates must all pass:

1. two-active-skill eligibility;
2. complete overlapping trace evidence;
3. compatible routing scope;
4. trusted equivalence and regression verifiers;
5. exact behavioral equivalence;
6. clean baseline library;
7. same-verifier exact non-regression;
8. canonical artifact identity;
9. copy-on-write tombstone isolation.

If any gate fails, `resolved_library` is a deep copy of the original library,
both skills remain active, and the lifecycle action is `rollback`.

## Retained controlled evidence

`experiments/mvp/results/skill_merge_v1/skill_merge.json` records a controlled
two-case equivalence and two-case regression fixture. It passes `9/9`, leaves
`json-report-writer` active, and turns `json-report-exporter` into a retired
alias tombstone.

This is a deterministic lifecycle-contract demonstration. It is not actual
provider-trace evidence, a general merge success rate, semantic skill fusion,
or a full-87 result.
