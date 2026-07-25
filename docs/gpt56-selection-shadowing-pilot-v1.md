> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/gpt56-selection-shadowing-pilot-v1.md`

---

# GPT-5.6 selection-only library-scale pilot v1

## Question

Does a larger presented skill catalog change which skill a requested GPT-5.6
selector chooses, before any skill body is invoked or any benchmark task is
executed?

This is a bounded exploratory selection pilot. It is not the full-87 execution
result and does not measure task utility.

## Frozen design

- six SkillsBench tasks from distinct domains
- exactly one upstream curated reference variant per task
- six distinct reference skills in the smallest catalog
- nested catalog sizes: 6, 16, 56, and 209
- two separate provider turns per size with different deterministic presentation orders
- eight provider turns and 48 selection decisions total
- strict JSON output, read-only empty workspace, and no provider tool use
- requested model contract: `gpt-5.6-terra`, effort `medium`
- provider stream did not report resolved model IDs

The selector received task instructions and skill IDs/descriptions. The prompt
did not label or expose the frozen reference mapping.

## Observed result

| Arm | Catalog size | Turns | Correct | Wrong variant | Abstain | Exact-reference accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| oracle-6 | 6 | 2 | 12/12 | 0/12 | 0/12 | 100.0% |
| plus-10 | 16 | 2 | 12/12 | 0/12 | 0/12 | 100.0% |
| plus-50 | 56 | 2 | 11/12 | 1/12 | 0/12 | 91.7% |
| full-209 | 209 | 2 | 12/12 | 0/12 | 0/12 | 100.0% |

The one mismatch occurred for `offer-letter-generator` in the second 56-skill
presentation. The frozen curated reference was `docx`; the model selected
`docx@d3cfe519dca2`. Both bundles declare the frontmatter name `docx` and both
describe Word-document operations, but their bytes and variant IDs differ.

This is an exact-reference selection mismatch. Because no task was executed,
the alternate variant may still have been useful. The result therefore cannot
be called a harmful utility failure.

## Interpretation

The observed curve is not monotonic: 100%, 100%, 91.7%, then 100%. This pilot
does not support a claim that more skills always make selection worse. It does
show that a single presentation of a mid-sized library can redirect selection
to a competing same-name variant even when the smaller and full catalogs do
not.

That observation supports a narrower The KING design requirement:

- library size alone is not the control variable;
- composition, presentation order, aliases, and duplicate variants matter;
- exact reference selection and actual utility must remain separate evidence;
- merge/retire/hide actions require behavioral equivalence or same-verifier
  evidence rather than name similarity alone.

## Evidence

- frozen plan SHA-256:
  `fb925d84df8eb0167333e757ba2cea7b0fa3900516b74d3fed69c109932e72c6`
- safe report content SHA-256:
  `b3f602415981368d2428b247321e8121ee3bbe54d882551a7266499273d1c78e`
- internal report SHA-256:
  `e7676bdfdeb82c0defb855d8418c8048e42d37aadd2dcd06dec05126a98d2af5`
- safe raw-chain audit file SHA-256:
  `49a3bbc84b783228e961468118820b84789b4a6d124d062f3fd88115efc900e2`
- raw-chain audit: 10/10 across all eight cells and 48 decisions

Repository evidence:

- `experiments/skillsbench/results/gpt56-selection-shadowing-pilot-v1.json`
- `experiments/skillsbench/results/gpt56-selection-shadowing-pilot-v1-audit.json`

Raw provider JSONL remains outside the repository under the private
content-addressed run root.

## Claim boundary

Supported:

- eight actual Codex provider turns occurred under the requested GPT-5.6 CLI contract;
- no provider tool execution was observed;
- the frozen 6/16/56/209 catalogs produced the exact selections above;
- one exact-reference variant mismatch occurred in one of two 56-skill turns;
- all raw prompts, schemas, traces, responses, and selections revalidated 10/10.

Not supported:

- provider-resolved model identity;
- provider-native skill invocation;
- task execution or utility degradation;
- monotonic degradation with library size;
- statistical significance or a population-level success rate;
- a full-87 or 1,305-cell model-backed result.
