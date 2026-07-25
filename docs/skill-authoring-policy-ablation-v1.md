> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/skill-authoring-policy-ablation-v1.md`

---

# Skill Authoring Policy Ablation v1

Status: 12/12 requested-model calls completed; hash-bound reassessment complete

## Question

Does injecting a frozen, source-traceable authoring `SKILL.md` improve generated-skill quality over the current target-contract-only prompt under the same requested model, effort, task contracts, and verifiers?

## Arms

- `target-contract-only`: the current minimal portable authoring instructions plus the task contract.
- `governed-authoring-policy`: the byte-identical task contract plus `author-governed-skills/SKILL.md`.

The policy distills Agent Skills, OpenAI skill-creator, Superpowers skill TDD, Anthropic skill evaluation, and bounded Karpathy-inspired coding heuristics. Source revisions are frozen in `references/source-contracts.md`; no upstream code is copied and star count is not a weight.

## Frozen design

- Requested model: `gpt-5.6-terra`
- Effort: `high`
- Tasks: `extract-todo-items`, `extract-markdown-links`, `parse-key-value-config`
- Repeats: two independent calls per task and arm
- Total planned calls: 12
- Order: control-first in repeat 1 and policy-first in repeat 2
- Candidate bundle: the same portable three-file shape in both arms
- Execution: new-only quarantine and isolated workspaces; no active-library writes

## Metrics

Primary promotion metrics are format, safety, target pass rate, held-out pass rate, near-miss negative-route accuracy, off-task artifacts, and promotion rate. Secondary efficiency metrics are candidate bytes, provider input/output tokens, and latency.

The comparison must report paired task-level deltas and retain failures. A plausible-looking `SKILL.md`, a lower token count, or prompt exposure is not utility evidence.

The live executor is `experiments/skill_authoring/run_live_authoring_policy_ablation.py`. It refuses to start unless the caller supplies the exact frozen 12-call approval phrase, keeps raw provider and candidate artifacts outside the repository, and records only bounded hashes, counts, verifier outcomes, routing outcomes, and efficiency metrics in the safe report.

## Execution evidence

- Completed calls: 12/12, with no recorded execution error
- Requested model contract: `gpt-5.6-terra`, effort `high`
- Model evidence level: `requested_cli_contract_only`
- Provider-reported model IDs: none
- Original exact-byte report: `experiments/skill_authoring/results/authoring-policy-ablation-live-v1.json`
- Original report SHA-256: `9d30e8b81825f1bae046da526229c343194c5108beab74ca98cc7d722efddef5`
- Canonical reassessment: `experiments/skill_authoring/results/authoring-policy-ablation-live-v1-reassessed-v2.json`
- Reassessment SHA-256: `d5b29d573ba97ee0ab57c8629fb6750a556544609c8b67533e555342aa228ce2`
- New calls made during reassessment: 0

The request proves what the local CLI was asked to run, not a provider-resolved backend identity. The report therefore must not state that a provider independently confirmed GPT-5.6.

## Reassessment corrections

The original report is preserved rather than rewritten. Its exact-byte metrics exposed three evaluator-contract defects:

1. The portable validator incorrectly required JSON-quoted YAML scalars and coupled a format failure to the safety gate. Safe plain YAML is now accepted, and `G1_format` and `G2_safety` are independent.
2. TODO and config task contracts required JSON content but did not freeze Unicode byte escaping. Reassessment uses semantic JSON equality without regenerating or re-executing candidates.
3. The Markdown-links contract did not freeze whether the top-level JSON value was a list or an object. All four Markdown runs are excluded from the primary behavioral comparison instead of being relabeled post hoc.

Every reassessed candidate manifest and execution workspace was re-hashed against the original evidence. No candidate was regenerated, re-executed, or written into the live library.

## Pilot result

| Metric | Target contract only | Governed policy | Interpretation |
|---|---:|---:|---|
| Portable format gate | 0/6 | 5/6 | Policy strongly improved OpenAI bundle metadata compliance in this pilot |
| Static safety gate | 6/6 | 6/6 | No arm advantage |
| Eligible target cases | 100% | 100% | No task-performance advantage |
| Eligible held-out cases | 100% | 100% | No held-out advantage |
| Mean negative-route accuracy | 50% | 50% | Authoring policy did not fix config-skill over-triggering |
| Eligible promotion | 0/4 | 1/4 | One governed TODO repeat passed all gates; insufficient for a broad claim |
| Mean input tokens | 14,056.2 | 14,930.0 | Policy cost +6.2% |
| Mean output tokens | 628.5 | 811.5 | Policy cost +29.1% |
| Mean generation latency | 15.26 s | 20.93 s | Policy cost +37.2% |
| Mean candidate bytes | 2,278.0 | 2,867.2 | Policy produced +25.9% larger bundles |

The control bundles consistently omitted the required explicit `$<skill-name>` interface invocation. Five governed bundles followed that contract; one governed TODO bundle omitted the `interface` wrapper. This is a real structural benefit, but it did not improve task correctness or routing in the eligible pairs.

## Conclusion

This pilot does not establish that a longer expert-derived `SKILL.md` improves generated-skill utility. It establishes a narrower result: authoring policy improved bundle conformance while adding cost, and it could not compensate for incomplete task schemas or weak routing boundaries.

That result supports The KING's central thesis. Skill creation instructions are useful, but generated skills still require harness-level contract freezing, independent validation gates, routing evaluation, quarantine, and lifecycle promotion. With only two repeats and two contract-eligible task families, this is diagnostic pilot evidence, not a general performance claim. The sealed Build Week v5 submission package remains unchanged.

## Product action

The long policy is not installed as an unconditional production prompt. Its demonstrated structural benefit is enforced deterministically instead: model-candidate quarantine now requires the executable three-file core and validates `interface.display_name`, `interface.short_description`, and a `default_prompt` containing `$<skill-name>` before any candidate can be written or executed. Format and static safety remain independent gates. This closes the observed conformance gap without paying the policy prompt's recurring token and latency overhead.
