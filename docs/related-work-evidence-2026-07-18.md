> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/related-work-evidence-2026-07-18.md`

---

# The KING 관련 연구 증거 메모 — 2026-07-18

Classification: related-work, thesis, architecture, experiment, implementation  
Status: source-verified selection; no new empirical claim

## 결론

The KING에 지금 필요한 것은 스킬을 더 많이 만드는 논문 목록이 아니다. 다음 여섯 원문은 각각 **비교 가능한 평가 계약**, **라이브러리 계약**, **실제 호출 기반 shadowing 측정**, **trace 관찰성**, **비회귀 승격**, **강한 self-evolving curation 경쟁자**라는 서로 다른 빈칸을 채운다.

```text
fixed task/model/verifier/library snapshot
  -> provisioned / selected / actually-invoked evidence
  -> outcome + trace diagnosis
  -> copy-on-write lifecycle or narrow policy variant
  -> same-verifier or held-in/held-out gate
  -> promote or rollback
```

이 연결이 The KING의 연구 대상이다. 논문들은 이 연결의 일부를 지지하거나 경쟁한다. 어느 논문도 현재 MVP의 10-task 결과나 장차의 Hermes+The KING 성능을 대신 증명하지 않는다.

선별은 최대 6편으로 제한했다. 기존에 문서만 있던 `AIP`와 `SkillRevise`는 중요하지만, 현 P0인 **실제 agent invocation trace와 matched management policy**보다 뒤에 오는 P1/P2 작업이므로 이번 원문 묶음에는 넣지 않았다. 그 둘은 현재 `docs/paper-notes/`에 보존된 추출을 유지하고, P0 gate가 통과한 뒤에만 원문을 추가한다.

## 증거 사용 규칙

| 구분 | 이 문서에서 의미하는 것 |
| --- | --- |
| 직접 근거 | 아래 원문의 방법, 데이터셋, 수치, 또는 공식 Hermes 문서에 명시된 기능 |
| The KING inference | 직접 근거를 현재 제품 계약에 적용한 설계 제안. 논문 결과가 The KING에서 재현된다는 뜻이 아님 |
| 구현됨 | 이 저장소의 결정적 코드/테스트가 동작함. 실제 외부 agent 또는 Hermes에서 검증됐다는 뜻은 아님 |
| 비교 가능 | 같은 base agent/model/effort/tools/task/verifier/budget/repeats와 같은 frozen library snapshot을 유지한 arm만 비교 가능 |

특히 `selected_skill_ids`, retrieval 결과, prompt에 보인 description은 **actual invocation이 아니다**. paper-grade `pi_o`, `pi_m`, `Delta_shd`에는 provider-native invocation 또는 skill-body load의 무결성 검증된 event만 들어간다.

## 원문 보존 및 무결성

원문은 모두 공식 arXiv PDF에서 내려받았다. 저장소 내부에 기존 원문 PDF는 없었고, task fixture PDF와 중복되지 않는다. `file` magic과 `pdfinfo`로 PDF 형식·페이지 수를 확인했으며 SHA-256은 내려받은 바이트에 대한 값이다.

| ID / version | Raw PDF | Official source | bytes / pages | SHA-256 |
| --- | --- | --- | --- | --- |
| `2602.12670v4` | `docs/references/papers/skillsbench-2602.12670v4.pdf` | [abs](https://arxiv.org/abs/2602.12670) · [PDF](https://arxiv.org/pdf/2602.12670v4) | 1,769,852 / 42 | `e987ebc3f0084a1ffc8acbca58259e33120b95fcef3876abfad060e169cf210b` |
| `2605.13716v1` | `docs/references/papers/skillops-2605.13716v1.pdf` | [abs](https://arxiv.org/abs/2605.13716) · [PDF](https://arxiv.org/pdf/2605.13716v1) | 1,594,213 / 23 | `b56e5f031d1ee2a480e2b21d840755ad894cf7d10936d8bf0ac7f384cbdefbfc` |
| `2605.24050v2` | `docs/references/papers/more-skills-worse-agents-2605.24050v2.pdf` | [abs](https://arxiv.org/abs/2605.24050) · [PDF](https://arxiv.org/pdf/2605.24050v2) | 779,790 / 18 | `0239853e4d06dfe32f14856051b4984fc4bd5d284c267792864584fa8ee1b97b` |
| `2605.11946v2` | `docs/references/papers/counterfactual-trace-auditing-2605.11946v2.pdf` | [abs](https://arxiv.org/abs/2605.11946) · [PDF](https://arxiv.org/pdf/2605.11946v2) | 1,488,065 / 18 | `cbdeacfdf3ebfcd84ef25869107449561d384d6f1aa69fe2e4e0e9509c5638e3` |
| `2606.09498v1` | `docs/references/papers/self-harness-2606.09498v1.pdf` | [abs](https://arxiv.org/abs/2606.09498) · [PDF](https://arxiv.org/pdf/2606.09498v1) | 4,307,149 / 19 | `065712f5bc1caeed717ad94e68bc0a011420417e86ef9b919ddb5e44e41398ab` |
| `2605.06614v1` | `docs/references/papers/skillos-2605.06614v1.pdf` | [abs](https://arxiv.org/abs/2605.06614) · [PDF](https://arxiv.org/pdf/2605.06614v1) | 7,031,485 / 33 | `e55b880b9564c63f5f791c02f423d41bd356eecc07002f85c6babfce72ee39c6` |

Raw files are intentionally unmodified. Source version and hash must be copied into a future paper bibliography/experiment manifest before a claim relies on any of them.

### Repair/generation novelty addendum

The following five official arXiv PDFs were added after the initial six-paper
selection because the bounded repair milestone makes them direct competitors or
evaluation anchors. The raw files remain unmodified.

| ID / version | Raw PDF | Official source | bytes / pages | SHA-256 |
| --- | --- | --- | --- | --- |
| `2604.01687v1` | `docs/references/papers/evo-skills-2604.01687v1.pdf` | [abs](https://arxiv.org/abs/2604.01687) · [PDF](https://arxiv.org/pdf/2604.01687v1) | 5,064,212 / 22 | `70d5d2e192566deb66b3f44161924972f505130559b54aa9be11be79c70cedc4` |
| `2604.20087v1` | `docs/references/papers/skilllearnbench-2604.20087v1.pdf` | [abs](https://arxiv.org/abs/2604.20087) · [PDF](https://arxiv.org/pdf/2604.20087v1) | 2,028,189 / 46 | `bd8f4e62a721d6bc1e1c58e47a26f2593d1b1181ce65455e7688d69d778c5deb` |
| `2603.15401v1` | `docs/references/papers/swe-skills-bench-2603.15401v1.pdf` | [abs](https://arxiv.org/abs/2603.15401) · [PDF](https://arxiv.org/pdf/2603.15401v1) | 1,769,505 / 14 | `981c0df0f9d2fa8fc433d094392e1911fefac4223bbe245b8d6d9d1479586ba0` |
| `2605.27366v1` | `docs/references/papers/muse-autoskill-2605.27366v1.pdf` | [abs](https://arxiv.org/abs/2605.27366) · [PDF](https://arxiv.org/pdf/2605.27366v1) | 1,619,150 / 30 | `e095fb2e110ecd9ae8d17d7670273945c65f8c57a539075f1df1e97272c766af` |
| `2607.05297v1` | `docs/references/papers/metaskill-evolve-2607.05297v1.pdf` | [abs](https://arxiv.org/abs/2607.05297) · [PDF](https://arxiv.org/pdf/2607.05297v1) | 2,117,514 / 14 | `ac86f0d53b5fec3d9dac98df8209310cfef9b2e4cba67935511e468c57da91fc` |

Implementation consequences from the source audit:

- EvoSkills makes open generation a stronger competitor. The KING should not
  claim novelty from iterative verifier feedback alone; its defensible delta is
  evidence-level separation, failure-scope routing, copy-on-write library
  governance, and same-verifier/held-out promotion.
- SkillLearnBench evaluates skill specification, execution trajectory, and task
  outcome separately. It reports that repeated external feedback can improve
  skills while self-feedback alone can recursively drift. The KING must keep
  reflection-only evidence below verifier-backed evidence.
- SWE-Skills-Bench traces executable tests to explicit acceptance criteria and
  warns against file-existence-only checks. Verifier trust must therefore
  record requirement coverage, behavioral depth, provenance, and independence.
- MUSE-Autoskill is a direct full-lifecycle competitor with per-skill memory,
  unit-test-triggered refinement, merge, and pruning. Its own limitation notes
  that same-task generation/re-evaluation can overstate gain; The KING's repair
  path requires disjoint held-out and whole-library regression cases.
- MetaSkill-Evolve is a direct competitor for harness-policy evolution, using a
  fast task-skill loop and a slower meta-skill loop. It still fixes the five-agent
  pipeline roles/wiring and evaluates three curated benchmarks. The KING should
  compare against single-level evolution before claiming value from harness
  policy updates.

## 기존 anchor와 새 후보의 역할 관계

| 우선순위 | 논문 | The KING에서 답하는 질문 | 현재 설계와의 관계 |
| --- | --- | --- | --- |
| P0 | SkillsBench | 같은 skill condition을 어떻게 verifier로 공정하게 평가하는가? | benchmark/paired-run contract; 관리 효과의 증거는 아님 |
| P0 | More Skills, Worse Agents? | skill library가 커질 때 왜 실패하며, 무엇을 actual invocation으로 세는가? | shadowing 정의와 `n/m/o` 이벤트의 직접 anchor |
| P0 | Counterfactual Trace Auditing | pass/fail 밖에서 skill이 agent behavior를 어떻게 바꾸었는가? | trace schema·pairing·cost/behavior layer의 직접 anchor |
| P1 | SkillOps | library-time maintenance가 가져야 할 artifact contract와 health 관점은 무엇인가? | lifecycle action vocabulary와 metadata/validator 방향 |
| P1 | Self-Harness | harness-policy update를 어떻게 승격/rollback하는가? | held-in/held-out safety gate; full-code self-rewrite는 범위 밖 |
| P1 / 경쟁 | SkillOS | self-evolving curator가 frozen executor 위에서 무엇을 해야 하는가? | 강한 curation competitor; 지금은 RL을 복제하지 않고 deterministic `M2-K`를 먼저 고정 |

`SkillOps`, `SkillsBench`, `More Skills`는 기존 current anchors다. `CTA`, `Self-Harness`, `SkillOS`는 새로 정식 선별한 후보다. AIP와 SkillRevise는 각각 artifact compilation과 bounded repair의 보조 축으로 남지만, 아래 5개 변경을 앞지르지 않는다.

## 논문별 추출과 The KING delta

### P0 — SkillsBench

**정확한 서지.** Xiangyi Li et al. *SkillsBench: Benchmarking How Well Agent Skills Work Across Diverse Tasks*. arXiv:2602.12670v4, 2026. [Official abstract](https://arxiv.org/abs/2602.12670) · [official PDF](https://arxiv.org/pdf/2602.12670v4).

**직접 근거.** 최신 원문은 8개 domain의 87개 task, curated Skills, deterministic verifier, no-Skills 대 curated-Skills paired evaluation을 정의한다. 저자들의 최신 18 model-harness aggregate에서는 task-macro pass rate가 33.9%에서 50.5%로 바뀌고, curated Skills의 효과는 configuration별로 크게 달랐다. 각 pass cell은 task당 3-trial frame에 기초한다.

**핵심 방법/실험.** task는 instruction·environment·oracle·verifier·skill bundle을 분리하고, final environment를 test-script verifier로 채점한다. 그래서 model answer 자체가 아니라 동일 작업공간의 검증 결과를 condition 간 비교할 수 있다.

**한계.** 이는 curated skill efficacy benchmark이지 The KING lifecycle/policy effect benchmark가 아니다. self-generated skill의 효과, library overflow, production agent trace는 별도 질문이며 GUI/multi-agent 일반화도 직접 보장하지 않는다. The KING의 로컬 upstream C0 definition audit과 87-task readiness 상태는 이 논문 내용으로 해결되지 않는다.

**채택.** immutable run contract, condition별 동일 task/verifier/budget, verifier-once, explicit denominator, paired repetitions.

**채택하지 않음.** curated bundle C0/C1 결과를 `M2-K` 관리 효과 또는 self-generated skill 성공으로 재명명하지 않는다.

**구체적 delta.** 실제 adapter smoke부터 `AgentRunContract`에 task, agent/version, model/effort, budget, library snapshot hash, verifier를 함께 고정한다. 다음 management experiment는 `M0/M1/M2-H/M2-K/M3-K`에 같은 snapshot과 same-verifier record를 강제하고, 별도 historical full-87 run과 결과를 pool하지 않는다.

### P1 — SkillOps

**정확한 서지.** Hongji Pu, Xinyuan Song, and Liang Zhao. *SkillOps: Managing LLM Agent Skill Libraries as Self-Maintaining Software Ecosystems*. arXiv:2605.13716v1, 2026. [Official abstract](https://arxiv.org/abs/2605.13716) · [official PDF](https://arxiv.org/pdf/2605.13716v1).

**직접 근거.** 각 skill을 `(P, O, A, V, F)` typed contract로 표현하고, cross-skill 관계와 utility/compatibility/risk/validation health를 library-time에서 관리한다. 원문 ALFWorld 실험에서는 200-skill library의 standalone setting에서 79.5% task success, strongest baseline 대비 +8.8pp를 보고하며, plug-in setting의 gain은 +0.68~+2.90pp다.

**핵심 방법/실험.** task-time retrieval 외부에서 maintenance를 실행하도록 하여, executor 내부를 바꾸지 않고 maintained library를 주입한다. 원문은 scale/noise와 action ablation을 별도로 보고한다.

**한계.** typed contracts와 일부 gold PDDL-style argument에 의존하고, 평가 library가 half-synthetic이다. `CGPD` 결과는 무효/불안정한 부분이 있어 그대로 core mechanism으로 가져오지 않는다. 또한 outcome-conditioned shadowing과 held-out policy promotion을 직접 보이지 않는다.

**채택.** `SkillArtifact`에 precondition, operation, expected artifact, validator, known failure mode를 versioned metadata로 보존하고 lifecycle action의 근거/검증을 남기는 원칙.

**채택하지 않음.** HSEG/PDDL graph planner, gold argument의 가정, CGPD를 초기 runtime dependency로 만들지 않는다.

**구체적 delta.** P1에서 `SkillArtifact.metadata`의 자유 텍스트를 `SkillContractV1`로 좁힌다. `validate_skill_contract()`와 migration fixture를 추가하고, lifecycle decision이 `P/O/A/V/F` 중 어떤 field의 defect/route risk에 근거하는지 기록한다. 이는 AIP full graph runtime을 도입하는 작업이 아니다.

### P0 — More Skills, Worse Agents?

**정확한 서지.** Hongwen Song and Song Wei. *More Skills, Worse Agents? Skill Shadowing Degrades Performance When Expanding Skill Libraries*. arXiv:2605.24050v2, 2026. [Official abstract](https://arxiv.org/abs/2605.24050) · [official PDF](https://arxiv.org/pdf/2605.24050v2) · [official OpenReview PDF](https://openreview.net/pdf?id=RQuOPsCKnc).

**직접 근거.** helpful oracle skill set에서 202-skill library로 확장할 때 최대 21% pass-rate drop을 보고한다. 38 matched task-model cases(원문 기준 Haiku 4.5 21, Sonnet 4.6 17)에서 oracle-only / expanded libraries를 비교하고, library size가 커질수록 skill shadowing upper bound가 자라며 context-overhead bound는 작고 0과 구별되지 않는다는 결과를 제시한다. point estimate상 shadowing은 최대 68%의 drop을 설명한다.

**핵심 방법/실험.** empirical oracle은 `p(q,{s}) - p(q,empty) >= tau`를 isolated trial로 만족한 skill만 포함한다. actual invocation set `I`를 기준으로 no-oracle invocation `n`, mixed/distractor `m`, oracle-only `o`를 분리하고, pass-rate drop을 selection shift와 conditional execution quality 변화로 분해한다.

**한계.** oracle estimation과 event-conditional rate에는 반복 비용과 표본 불확실성이 크다. original task는 helpful/oracle bundle에 조건부로 선별됐고, 원문의 observation surface가 어떤 모든 provider에 자동 이식되는 것은 아니다. synthetic distractor recovery는 이 원문 natural-library result와 별개다.

**채택.** `provisioned != selected != actually invoked`, empirical oracle의 restricted estimation, `n/m/o` denominator 공개, oracle-only arm과 full-library arm의 분리, `pi_m`와 conditional pass rate의 병렬 보고.

**채택하지 않음.** lexical top-k provisioner를 naive-full-library baseline으로 부르지 않는다. selected ID나 self-report를 invocation 증거로 쓰지 않는다. controlled two-distractor MVP를 natural 202-skill finding이라고 주장하지 않는다.

**구체적 delta.** 현재 `InvocationObservation`, `trace_to_invocation_observation`, `more_skills_decomposition()`은 방향이 맞다. 다음은 (a) real adapter event로 그것을 채우고, (b) `M0` full descriptor exposure와 `M1` fixed top-k를 별도 arm으로 고정하며, (c) no-oracle task / missing outcome / incomplete raw evidence를 metric에서 제외하되 분모로 공개하는 experiment report다. cluster bootstrap CI와 task-family grouping은 paper claim 전 필수다.

### P0 — Counterfactual Trace Auditing (CTA)

**정확한 서지.** Xiaolin Zhou, Jinbo Liu, Li Li, Ryan A. Rossi, and Xiyang Hu. *Counterfactual Trace Auditing of LLM Agent Skills*. arXiv:2605.11946v2, 2026. [Official abstract](https://arxiv.org/abs/2605.11946) · [official PDF](https://arxiv.org/pdf/2605.11946v2).

**직접 근거.** CTA는 같은 task에서 with-skill/without-skill trace를 phase alignment하고 Skill Influence Pattern(SIP)을 산출한다. SWE-Skills-Bench 49 task와 Claude Sonnet 4.5에서 평균 pass-rate change는 약 +0.3pp인데 522개 SIP instance를 발견했다. 즉 final pass만으로 template copy, off-task artifact, excess planning, recovery 같은 변화를 놓칠 수 있다.

**핵심 방법/실험.** paired trace bundle에 task, two traces, skill document, two outcomes를 묶고, phase/intent/action divergence 및 constructive/destructive behavior label을 기록한다. baseline ceiling task와 mid-range task를 분리해 headroom의 영향을 보인다.

**한계.** 원문은 한 benchmark와 one paired trace per condition 중심이며, SIP detector가 human-gold annotation을 완전히 대체하지 않는다. full dynamic time warping와 text semantic alignment는 지금 The KING MVP의 적절한 P0 범위가 아니다.

**채택.** immutable raw-trace pointer + SHA-256, normalized trace와 raw transcript의 분리, with/without matched trace, outcome 외 cost/action/artifact delta를 lifecycle evidence로 남기는 원칙.

**채택하지 않음.** full CTA labeler, LLM-based intent judge, 또는 trace text를 무단으로 public package에 복제하는 행동.

**구체적 delta.** 현재 `BaseAgentAdapter`와 `AgentRunContract`가 fake adapter smoke에서 raw-pointer integrity, selected-vs-invoked, verifier-once를 검증한다. 다음 P0은 provider-specific adapter가 `READ/WRITE/EXECUTE/SEARCH`, `skill_body_loaded` 또는 `provider_skill_invocation`, tool-count/cost/latency를 normalized event로 전송하도록 하는 것이다. `cta_lite.compare_traces`는 그 real paired bundle에서만 lifecycle decision evidence가 될 수 있다.

### P1 — Self-Harness

**정확한 서지.** Hangfan Zhang, Shao Zhang, Kangcong Li, Chen Zhang, Yang Chen, Yiqun Zhang, Lei Bai, and Shuyue Hu. *Self-Harness: Harnesses That Improve Themselves*. arXiv:2606.09498v1, 2026. [Official abstract](https://arxiv.org/abs/2606.09498) · [official PDF](https://arxiv.org/pdf/2606.09498v1).

**직접 근거.** fixed model + minimal harness에서 verifier-grounded weakness mining, minimal proposal, regression validation을 반복한다. Terminal-Bench-2.0의 held-out pass rate는 보고된 세 model에서 40.5→61.9, 23.8→38.1, 42.9→57.1%로 증가했다. proposal은 held-in과 held-out 어느 쪽도 떨어뜨리지 않고 한쪽 이상 개선할 때만 promote된다.

**핵심 방법/실험.** failure를 verifier cause, relevant trace behavior, abstracted mechanism으로 cluster한 뒤, allowed harness surface에 대한 좁은 edit를 제안한다. held-out task trace는 proposer에게 노출하지 않고 candidate별 repeat, changed surface, accept/reject evidence를 기록한다.

**한계.** initial harness/model/benchmark에 특화된 preprint이며 verifier/trace 질에 의존한다. pass-rate non-regression만으로 고위험 정책 변경의 안전성을 충분히 보장하지 않는다. 따라서 원문의 전체 harness editing을 The KING이 그대로 계승할 수 없다.

**채택.** copy-on-write variant, pre-registered editable surface, held-in + held-out non-regression, proposal lineage, failed proposal rollback.

**채택하지 않음.** 임의 코드 전체 수정, model weight update, one successful task만으로 global policy promotion.

**구체적 delta.** 현재 lifecycle demo의 same-verifier gate는 **skill hide action**에 적합하지만 `M3-K` harness evolution 증거는 아니다. P1에서 `HarnessPolicyChange` caller가 실제 isolated configuration variant를 run하도록 하고, held-in/held-out run manifest, per-split sample count, baseline/candidate hashes, failed candidate artifact를 모두 저장한다. policy action은 exposure budget, abstain threshold, selector rule, lifecycle threshold, processor manifest로 한정한다.

### P1 / 경쟁 — SkillOS

**정확한 서지.** Siru Ouyang, Jun Yan, Yanfei Chen, Rujun Han, Zifeng Wang, Bhavana Dalvi Mishra, Rui Meng, Chun-Liang Li, Yizhu Jiao, Kaiwen Zha, Maohao Shen, Vishy Tirumalashetty, George Lee, Jiawei Han, Tomas Pfister, and Chen-Yu Lee. *SkillOS: Learning Skill Curation for Self-Evolving Agents*. arXiv:2605.06614v1, 2026. [Official abstract](https://arxiv.org/abs/2605.06614) · [official PDF](https://arxiv.org/pdf/2605.06614v1).

**직접 근거.** SkillOS는 frozen executor와 external `SkillRepo`, trainable curator를 분리하고, earlier trajectories가 repo를 update하며 later related tasks가 update를 평가하는 grouped task stream으로 RL curation policy를 학습한다. ALFWorld, WebShop, single-turn reasoning benchmark에서 memory-free 및 강한 memory baseline을 능가한다고 보고하며, frozen executor와 retrieval budget을 matched하게 유지한 evaluation도 제공한다.

**핵심 방법/실험.** curation action에 delayed downstream signal을 주고, effectiveness와 efficiency, skill utilization을 함께 본다. source task에서 tuned policy가 task streams/other executor에 일반화되는지 조사한다.

**한계.** RL reward/stream curriculum/learning budget 자체가 treatment라서 deterministic lifecycle rule과 같은 실험 단위가 아니다. reward weight를 held-out source subset에서 tuning하며, TaskRepo/SkillRepo stream assumptions가 SkillsBench one-shot paired setup과 직접 같지 않다. 따라서 `SkillOS > The KING` 또는 그 반대의 성능 주장은 현재 불가하다.

**채택.** frozen executor와 mutable external library의 분리, adaptation traces와 downstream held-out evaluation의 시간 방향, successful skill utilization을 final score와 별도로 보는 원칙.

**채택하지 않음.** P0에서 RL curator를 학습하거나 SkillOS composite reward/stream curriculum을 복제하지 않는다. 그것은 The KING의 outcome/shadowing/regression causality를 흐린다.

**구체적 delta.** `ManagementRoundInput/Output`을 immutable artifact로 도입한다: frozen snapshot, split, trace IDs, allowed actions, thresholds, policy version, resulting snapshot/parent. P0/P1는 deterministic `M2-K`만 실행하고, 나중의 RL curator는 별도 `M4-RL` robustness study로 격리한다.

## Hermes/Curator와의 공정 비교 경계

Hermes는 논문 raw source가 아니라 실행 substrate/competitor다. 다음은 공식 문서로 확인한 직접 사실이다.

- [Hermes Agent v0.18.0 release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.7.1)는 `/learn`, `/journey`, completion contract, verification evidence, `pre_verify` hook 및 background self-improvement를 설명한다.
- [Hermes Curator documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/curator)는 `view_count`, skill body가 prompt에 loaded될 때의 `use_count`, patch telemetry, `active → stale → archived`, backup/rollback/restore, optional consolidation을 명시한다.

따라서 The KING은 "Hermes에 lifecycle이나 verification이 없다"고 말할 수 없다. 반대로 공개 문서만으로 Curator가 per-skill counterfactual outcome, oracle/distractor `n/m/o`, shadowing decomposition, held-out routing/processor promotion을 사용한다고도 말할 수 없다. 후자는 **documentation-bounded inference**다.

공정한 비교는 둘 중 하나다.

1. `M2-H`: official Curator의 usage/view/patch/recency 규칙만 같은 active-library capacity에서 재구현한다. 이 arm은 `Hermes-Curator-inspired policy`, Hermes 전체 실행 결과가 아니다.
2. 실제 Hermes release/commit을 `BaseAgentAdapter`에 pin하고, non-interactive task execution, provider-observed skill load, same workspace/verifier/budget을 한 contract로 수집한다. 이 경우에만 `Hermes + The KING harness`라고 부른다.

## 구현 및 실험 변경: 최대 5개

| 우선순위 | 한 가지 변경 | 구체적 위치와 acceptance |
| --- | --- | --- |
| P0-1 | **실제 adapter smoke** | 새 provider adapter 하나를 `src/the_king/agent_adapter.py` contract에 맞춰 추가한다. `AgentRunResult`은 raw trace hash, complete flag, actual invocation event를 제출하고 `run_agent_adapter_once()`는 같은 verifier를 정확히 한 번 실행한다. selected-only와 incomplete evidence는 paper metric에서 실패/제외되어야 한다. |
| P0-2 | **actual-invocation management report** | `experiments/`에 frozen one-task → three-task smoke manifest를 만든다. `M0` full exposure, `M1` top-k, `M2-H`, `M2-K`의 provisioned/selected/invoked, `n/m/o`, no-oracle/incomplete denominators, verifier score, cost/latency를 같은 schema로 출력한다. |
| P1-3 | **SkillContractV1** | `src/the_king/models.py`와 validator에 `P/O/A/V/F` 최소 contract를 typed metadata로 추가하고 migration fixture/test를 만든다. lifecycle decision은 skill-content defect, route-local risk, absent evidence를 구분해야 한다. |
| P1-4 | **진짜 held-in/held-out policy gate** | `HarnessPolicyChange`에 frozen parent/candidate config hash, split manifest, allowed surface, repeat count, per-split verifier result를 요구한다. candidate variant는 외부 caller가 실제로 실행하며 accept/reject와 rollback artifact를 보존한다. |
| P2-5 | **bounded repair/admission** | P0~P1 evidence가 쌓인 뒤에만 AIP-lite/SkillRevise-lite 후보 수리 하나를 copy-on-write로 실행한다. first verifier-pass + no-skill fallback + regression ledger를 요구하고, generator 성능 자체를 headline contribution으로 삼지 않는다. |

이 순서에서 P0-1이 통과하기 전에는 full 87-task management arm, Hermes superiority, natural shadowing claim, autonomous repair promotion을 시작하지 않는다.

## 현재 상태에 대한 해석

현재 deterministic MVP는 controlled distractor와 lifecycle recovery에 대해 유용한 engineering proof를 제공한다. 최근 추가된 adapter boundary는 fake adapter test에서 다음을 이미 보장한다.

- contract/workspace/raw-trace hash mismatch는 verifier 실행 전에 거절된다.
- selected but not loaded는 actual invocation으로 바뀌지 않는다.
- oracle load와 distractor load는 서로 다른 invocation evidence로 보존된다.
- adapter path의 deterministic verifier는 한 번만 실행된다.

그러나 이 것은 real model/Hermes provider trace가 아니다. 논문 수준 `pi_o`, `pi_m`, CTA-lite, lifecycle action 결과는 실제 adapter가 같은 보장을 채운 data를 남기기 전까지 **implementation-ready but empirically unvalidated** 상태다.

## 불확실성 및 과장 방지

- 여섯 원문은 모두 2026 preprint 또는 최신 benchmark artifact다. 각 저자 수치는 해당 환경/버전/setting에서의 보고값이지 독립 재현 결과가 아니다.
- SkillsBench current public aggregate는 87 tasks를 말하지만, 로컬 upstream materialization/C0 methodology issue는 별개의 재현성 gate로 계속 유지한다.
- More Skills의 21% 및 68%는 원문 specific library/model/task selection에서의 결과다. The KING MVP의 `1/10 → 9/10` recovery와 합쳐 하나의 effect size로 말하면 안 된다.
- CTA의 SIP는 behavior observation layer이지 causal blame oracle이 아니다. lifecycle change는 same-verifier 또는 held-out regression gate를 통과해야 한다.
- Self-Harness가 broad harness evolution을 보였더라도 The KING의 permitted policy surface는 의도적으로 더 좁다.
- SkillOS가 learned curator를 보였더라도 The KING의 primary claim은 RL learner가 아니라 frozen library에서 outcome/invocation/shadowing/regression evidence를 사용하는 관리 정책의 인과효과다.

## `docs/the-king-product-contract.md`에 적용할 제안 diff (수정하지 않음)

이 문서는 제품 계약을 직접 수정하지 않는다. 다음 두 문장만 다음 계약 정리 때 반영한다.

```diff
- 실제 호출 공통화 | 아직 없음 | selection/provisioning과 actual skill-body load/tool invocation을 모두 보존하는 adapter-level trace converter가 필요하다.
+ 실제 호출 공통화 | fake-adapter contract까지 구현됨, real provider는 미검증 | AgentRunContract/BaseAgentAdapter가 raw hash, selected-vs-invoked, verifier-once를 강제한다. 실제 Hermes/B_cli provider event converter와 smoke evidence는 아직 없다.

- first vertical slice acceptance는 fake trace contract 완성이다.
+ first vertical slice 다음 gate는 한 provider의 real, non-secret raw trace pointer와 skill-body-load event를 가진 one-task same-verifier smoke다; selected ID만 있는 결과는 promotion/metric evidence가 아니다.
```

`SkillContractV1`, management-round manifest, M2-H/M2-K report는 이 문서의 5개 변경에 따라 별도 design/implementation diff로 진행한다.
