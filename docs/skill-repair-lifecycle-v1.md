> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/skill-repair-lifecycle-v1.md`

---

# Skill Repair Lifecycle v1

Classification: architecture, experiment, implementation, related-work

Status: bounded deterministic and two requested-GPT-5.6 model-authored family closures implemented

## Outcome

The KING now has an executable path from a verifier-backed `REPAIR` diagnosis
to a new skill version. It is not a status-only transition:

```text
reproduce skill-local failure
  -> pass target feedback only to a bounded reviser
  -> evaluate ordered candidate versions
  -> select the first target-passing version
  -> run held-out and whole-library regression cases
  -> promote a copy-on-write library or roll back
```

The implementation is `src/the_king/skill_repair.py`. The real task/verifier
adapter and reproducible fixture are in
`experiments/mvp/run_skill_repair_demo.py` and
`experiments/mvp/results/skill_repair_trust_v1/skill_repair.json`. The earlier
`skill_repair/` result is retained as historical evidence from before the
verifier-trust gate was introduced.

The model adapter is `src/the_king/model_skill_reviser.py`; phased confinement
is `run_quarantined_candidate_phase` in
`src/the_king/isolated_candidate_runner.py`. The actual retained campaign and
independent chain audit are:

- `experiments/mvp/run_live_model_skill_repair.py`
- `experiments/mvp/audit_model_authored_repair_chain.py`
- `experiments/mvp/results/model_authored_skill_repair_live_v1/`
- `experiments/mvp/run_live_model_skill_repair_family2.py`
- `experiments/mvp/audit_model_authored_repair_family2_chain.py`
- `experiments/mvp/results/model_authored_skill_repair_family2_live_v1/`

## Failure-scope boundary

| Observed failure | Allowed action | Reason |
| --- | --- | --- |
| skill-local, reproduced by a frozen verifier | repair content in a new version | the selected skill itself failed its contract |
| route-local wrong or mixed selection | hide, merge, or update provisioning | rewriting content would confound routing with execution |
| missing or incomplete verifier evidence | add or recover a verifier | an unscored rewrite cannot be promoted |

This boundary preserves the project's main thesis: self-generated skill
failure is a skill-harness management problem, and different failure scopes
require different harness actions.

## Formal gate

For an original skill `s_0`, ordered candidate versions
`C = (s_1, ..., s_B)`, frozen target set `T`, held-out set `H`, and library
regression set `R`, select:

```text
j* = min { j : pass(s_j, T) = 1 }
```

The candidate may replace the live version only when:

```text
Promote(s_j*) =
  pass(s_j*, T)
  and NR(s_0, s_j*, H)
  and NR(L_0, L_j*, R)
  and COW(L_0, L_j*)
```

`NR` preserves every baseline-passing case and does not reduce total passed
cases. `COW` requires the original snapshot and every unrelated skill artifact
to remain byte-content equivalent under canonical hashing. Missing evidence is
a failed gate.

The reviser API receives only the reproduced target results. It cannot receive
held-out or library-regression outcomes from the lifecycle orchestrator. This
is a deliberate information barrier, not just a reporting convention.

## Research mapping

- SkillRevise motivates diagnosis plus bounded version selection rather than
  deploying the latest rewrite.
- EvoSkills motivates verifier-grounded iterative repair and informational
  separation between author and evaluation. The KING keeps the final hidden
  gate opaque to the reviser and adds whole-library rollback.
- SkillLearnBench reports that external feedback can support iterative
  improvement while self-feedback alone can drift. The KING therefore requires
  external verifier evidence and does not accept reflection-only repair.
- Self-Harness motivates held-in/held-out non-regression and copy-on-write
  promotion.
- MUSE-Autoskill demonstrates a broad creation-memory-management-evaluation-
  refinement lifecycle, but also notes that generating and re-evaluating on the
  same task can overstate gains. The KING makes a disjoint held-out split
  mandatory.
- SWE-Skills-Bench motivates acceptance-criteria-traceable executable tests and
  explicitly rejects file-existence-only verification as sufficient for
  behavioral claims.

These papers define methods and competitors. Their reported performance is not
evidence that The KING's bounded repair generalizes.

## Recorded v1 result

The deterministic demo injects a skill-local bug into `line-summary` version 1:
the target task `summarize-lines` writes `5` instead of computing the required
non-empty-line count. The repair operator produces version 2, then the existing
`RecipeSkillExecutor` and task verifier path re-run the frozen cases.

| Gate | Baseline | Candidate v2 |
| --- | --- | --- |
| target `summarize-lines` | fail | pass |
| held-out `count-errors` | pass | pass |
| library regression `count-records` | pass | pass |
| unrelated artifact isolation | unchanged | unchanged |
| final promotion checks | n/a | 6/6 pass |

The selected version is `line-summary@v2`; the original tuple remains
unchanged. Failure-path tests cover held-out regression, library regression,
route-local redirection, missing verifier evidence, snapshot drift, and routing
contract mutation.

## Evidence boundary and next work

### Recorded live model-authored repairs

One requested `gpt-5.6-terra`, high-effort Codex run repaired the existing
model-authored `extract-todo-items` bundle from v1 to v2. The reviser saw the
immutable bundle and one target-only failure: optional horizontal whitespace
between `TODO` and `:`. It did not receive the hidden Unicode case or the
library-regression outcome. The mutation firewall required byte-identical
`SKILL.md` and `agents/openai.yaml`; only `scripts/run.py` changed.

| Phase | v1 | model-authored v2 |
| --- | ---: | ---: |
| target marker-spacing | 0/1 | 1/1 |
| hidden Unicode-spacing | 0/1 | 1/1 |
| original-format library regression | 1/1 | 1/1 |
| COW repair promotion gates | n/a | 6/6 |
| raw-chain audit | n/a | 13/13 |

The audit reconstructs the exact target-only prompt, hashes the raw provider
JSONL and response, rebuilds the quarantine records, verifies script-only
mutation, freshly re-executes all six baseline/candidate phases under macOS
confinement, and replays the promotion decision. The report contains hashes,
denominators, and claim boundaries only.

The second family starts from a deterministic, hash-bound
`parse-key-value-config@v1` fixture, so baseline authorship is not attributed to
the model. The requested model sees only a spacing target failure and the
immutable three-file bundle. It does not see the hidden first-value/literal
case or the library regression.

| Phase | deterministic v1 | model-authored v2 |
| --- | ---: | ---: |
| target horizontal spacing | 0/1 | 1/1 |
| hidden first-value and literal preservation | 1/1 | 1/1 |
| original-format library regression | 1/1 | 1/1 |
| COW repair promotion gates | n/a | 6/6 |
| independent raw-chain audit | n/a | 14/14 |

The two model-authored repair candidates belong to distinct skill families and
both passed target, hidden, regression, and COW gates. This is stronger than a
single anecdote, but `n=2` is still not a repair success-rate or generalization
claim.

This milestone proves two bounded model-authored repair lifecycle closures. It
does not yet prove:

- broad multi-family or repeated model-authored repair quality;
- surrogate-verifier quality;
- provider-native skill-body invocation;
- natural shadowing recovery;
- full-87 or multi-model generalization;
- merge or retire execution.

The next research step is to repeat this contract across several skill
families and rejected repair candidates, then run target/held-out cases over
the full SkillsBench-compatible execution surface with repeated trials and
paired or task-cluster bootstrap intervals. Merge and retire remain separate
route/library actions and must not be smuggled into content repair.
