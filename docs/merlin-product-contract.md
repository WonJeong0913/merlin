# Merlin 제품 계약: 실행 에이전트 위의 스킬 하네스 거버넌스

Date: 2026-07-18  
Classification: thesis, architecture, experiment, implementation

## 1. 한 문장 정체성

**Merlin은 Hermes 같은 실제 실행 에이전트를 고정하거나 교체 가능한 기반 실행기로 두고, 그 에이전트의 스킬이 실제로 노출·호출·검증된 결과를 바탕으로 스킬 라이브러리와 하네스 정책을 안전하게 승격 또는 되돌리는 자기관리형 스킬 하네스 에이전트다.**

따라서 제품의 목표는 “새로운 기반 모델”이나 “Hermes의 기능 재구현”이 아니다. 기반 실행 에이전트가 경험에서 스킬을 만들고 태스크를 수행할 수 있게 한 뒤, Merlin이 그 스킬이 **언제 존재하고, 어느 태스크에 보이며, 실제로 무엇이 호출되었고, 언제 숨김·수리·병합·폐기되며, 어떤 하네스 변경이 안전한지**를 관리한다.

연구 비중은 코드 줄 수가 아니라 주장과 실험 우선순위에서
`하네스 거버넌스·진화 80% : 최소 계약 준수 스킬 공급 20%`다.
20%는 스킬 문구나 생성기를 반복 최적화하는 축이 아니다. 기본
Create-Skill 수준의 `SKILL.md` 중심 후보를 공급하고, 계약 수리·승격·은퇴·
롤백은 80% 하네스 lifecycle이 결정한다.

## 2. 사용자가 실제로 쓰는 제품 loop

현재 제품의 기본 진입점은 Hermes/Codex 스타일의 멀티턴 터미널 채팅이다.
`experiments.mvp.run_chat`이 첫 요청에서 실제 Codex provider thread를 만들고,
후속 요청에서 같은 thread를 resume한다. 매 turn마다 active library를 다시 읽어
top-k 후보만 prompt context로 노출하고, ID와 이유, raw trace hash, feedback을
불변 ledger에 남긴다. 이 prompt provisioning은 실제 skill-body 호출 증거가
아니며, `/feedback`은 자동 lifecycle 변경을 일으키지 않는다. 기존 Console은
아래 관리 loop의 고정 샘플을 검증하는 debugger다.

실제 chat 기본값은 provider-independent `SemanticSkillRouter` control
plane과 `governed-provisioning-v2` guard를 결합한다. v2는 active library에서
동일 declared name을 가진 variant를 결정론적·read-only projection으로 먼저
정리하며, suppressed variant를 semantic router에 보내지 않는다. 이 단계는
merge/retire나 behavioral equivalence를 승인하지 않는다. 현재 adapter는 turn당
독립적인 read-only/ephemeral Codex CLI router이며, 실행 plane의 main Codex
turn과 thread를 공유하지 않는다. 기본 router effort는 `low`이고 실행
effort는 별도 값이므로 최대 두 번의 model call이 발생한다. active skill이
없으면 router call을 생략한다. semantic 라우팅은 지연·비용을 추가하며,
실패하면 safe error enum과 `deterministic_fallback`을 남기고 사용자 turn은
계속한다.

결합 정책은
`active-only → exact artifact/input anchor pool → trigger/description positive
evidence 또는 semantic rank/negative exclusion/abstain → bounded lexical
do_not_use_when guard → exposure budget` 순서를 따른다. 명시적 파일명이 active skill의
`expected_artifacts` 또는 step input/output basename과 정확히 맞으면
언어와 무관한 positive contract evidence가 되며, negative guard에 걸리면
anchor 후보여도 제외된다. 결정은 정책 버전, active-library
snapshot ID/hash, 후보별 근거와 제외 사유, rank/provision/primary,
abstain을 분리해 남긴다. 영속 metadata에는 raw 요청·파일명·skill
body 대신 hash/length/count만 남긴다.

router에는 ID/name/description/trigger/`do_not_use_when`, 선언된 input/artifact,
SkillOps field 존재 여부만 제공한다. full body, step 지침, script는 제공하지
않는다. query는 provider stdin에만 넣고, 영속 routing record에는
hash/length/count/ID/enum, model evidence, raw trace pointer/hash만 남긴다. model
rationale는 요청하지도 저장하지도 않는다. semantic rank, prompt exposure,
provider-native load/invocation은 각각 다른 사실이다.

현재 provider adapter는 Codex CLI이지만 protocol은 CLI에 속하지 않는다.
애플리케이션이 routing·branching을 소유하는 미래/alternate 경로는
Responses API가 맞다. 현재 OPENAI_API_KEY 기반 API 성공 증거는 없고
Agents SDK dependency도 추가하지 않았다.

사용자는 “스킬을 하나 더 만들어 달라”는 도구가 아니라 다음의 반복 가능한 실행 환경을 사용한다.

```text
기반 실행 에이전트와 태스크/검증기 연결
→ 태스크별 제한된 스킬 노출
→ 에이전트 실행
→ 실제 스킬 본문 로드·호출, 결과, 비용, 검증기 기록
→ 스킬 자체 위험과 라우팅 위험 분리 진단
→ 좁은 변경을 복사본에만 적용
→ 같은 검증기(수명주기) 또는 보유/미보유 분할(정책)을 재실행
→ 승격 또는 원상 복구
→ 사람이 이유·근거 trace·변경 전후 결과를 확인
```

제품 화면이나 CLI가 나중에 추가되더라도 사용자가 받아야 하는 핵심 산출물은 다음과 같다.

1. 이번 실행의 조건: 기반 에이전트/모델, 하네스, 태스크, 스킬 스냅샷, 예산, 검증기.
2. 태스크별 증거: 노출한 스킬, 선택한 스킬, **실제 호출한 스킬**, 검증 결과, 비용·시간, 원시 trace 위치.
3. 진단: `wrong`, `mixed`, `empty`, `spurious` 같은 경로 사건과 스킬 자체 문제/라우팅 문제의 구분.
4. 제안: 숨김·수리·병합·폐기 또는 노출 예산·라우팅 규칙·처리기 변경.
5. 안전 판정: 동일 검증기 재실행 또는 보유/미보유 비회귀 평가, 그리고 promote/rollback 이유.

이 loop에서 인간은 정책 범위와 위험 한계를 정한다. Merlin은 증거가 없는 자동 영구 변경을 하지 않는다.

### 2.1 자율성 및 사용자 권한 계약

제품 목표는 사람이 `/learn` 같은 명령으로 매 단계를 지시하는 반자동 도구가
아니다. Merlin은 일반 질의에서 capability gap을 읽기 전용으로 감지하고,
중복·active coverage·예산·snapshot을 스스로 확인하며 변경과 검증 계획을
만든다. 기본 Managed 모드는 frozen 저위험·가역·세션 범위 정책 안의 등록
작업만 자동 승인한다. 다음 authority boundary로 승격할 때만 범위를 표시하고
자연어 허락을 받는다. Strict 모드는 모든 스킬 쓰기에 허락을 요구한다.

- 스킬·하네스 정책의 영구 또는 전역 변경
- open-ended 모델 후보 코드 생성과 실행
- provider/model/network 호출과 비용 발생
- 파일 overwrite/delete, 게시, 외부 시스템 변경

애매한 답변은 승인이 아니며, 거절·snapshot drift·중복·예산 초과·gate 실패는
모두 원본 불변으로 끝난다. Managed 정책 승인 또는 Strict 사용자 승인 뒤에도
G0–G6 전체를 통과한 COW overlay만 세션에 설치하고 원래 요청을 자동 재개한다. 등록 작업 변경 자체는 provider
authoring call 0회이며, 재개된 일반 chat의 router/executor 호출은 별도 계약이다. 현재 구현은
`backlog.todo → todo-items.json` 등록 작업 1종, 세션당 기본 1회에 한정된다.
세부 계약은 `docs/consent-gated-autonomy-v1.md`에 고정한다.

관리형 생성·수리는 별도 후속 gate다. 생성 메커니즘은 임의로 새로 만들지
않고, `docs/managed-skill-creation-contract.md`에서 동결한 공개 source와 논문
정의 매핑을 따른다. 후보는 `candidate → provenance → schema/baseline-failure/
target validator → regression gate → adopt 또는 reject` 순서를 통과해야 한다.
이 계약이 구현되기 전에는 생성 기능을 완료로 표시하지 않는다.

## 3. Hermes에서 재사용할 substrate와 Merlin 고유 delta

Hermes는 목표 제품의 기반 실행기 후보이지, Merlin이 처음부터 다시 구현할 대상이 아니다. 실제 Hermes 연동 전에는 공개된 기능을 제품 계약으로 가정하지 않고, 고정한 버전과 호출 가능한 실행·추적 표면을 별도로 기록해야 한다.

| 층 | Hermes 또는 다른 기반 에이전트에서 재사용할 substrate | Merlin이 추가로 책임질 delta |
| --- | --- | --- |
| 실행 | 대화, 계획, 도구 사용, 작업 공간, 실제 태스크 수행 | 에이전트 독립 실행 계약과 조건/예산/검증기 고정 |
| 학습·스킬 공급 | 경험에서 스킬 생성·수정, 기존 메모리와 스킬 라이브러리 | 후보를 구조·대상·회귀 게이트 뒤에만 채택하고, 생성 스냅샷을 모든 관리 arm에 고정 |
| 스킬 사용 | 스킬 발견·선택·로딩·호출 | `provisioned`, `selected`, `actually invoked`를 구분해 trace로 보존; 선택 기록만으로 호출을 추정하지 않음 |
| 일반 수명주기 | 사용량·최근성, 보관·병합·백업·복구 같은 Curator형 운영 | 태스크 결과·실제 호출·쉐도잉·회귀에서 스킬별/경로별 원인을 진단하고 좁은 행동을 copy-on-write로 검증 |
| 완료 검증 | 태스크 완료 계약 또는 일반 verification | 같은 태스크·같은 verifier 계약을 유지하는 수명주기 승격과, 보유/미보유 비회귀를 요구하는 하네스 정책 승격 |
| 자가 개선 | 프롬프트·스킬·도구 개선 가능성 | 변경 가능한 표면을 노출 예산·선택 규칙·수명주기 임계값·처리기 구성으로 제한하고, 재구성 가능한 variant와 rollback을 강제 |

Merlin의 차별점은 “스킬 생성/보관/검증 기능이 존재한다”가 아니다. 다음 연결을 실제 데이터로 닫는 것이다.

```text
paired outcome + actual invocation + shadowing + regression evidence
→ skill-local / route-local diagnosis
→ provisional lifecycle or harness-policy change
→ equivalent verifier re-run or held-in/held-out evaluation
→ auditable promotion or rollback
```

## 4. 현재 상태: 구현됨, 코드만 있음, 아직 없음

아래 표의 “구현됨”은 현재 저장소의 결정적 MVP와 테스트가 그 동작을 보장한다는 뜻이다. 실제 Hermes 또는 일반 LLM 에이전트에서 검증됐다는 뜻은 아니다. 2026-07-21 현재 workspace 전체 검증 수치는 `592/592 tests`다. Build Week allowlist는 별도로 `174 files`, `220/220 internal tests`이며 연구 전용 SkillsBench 실행기는 의도적으로 제외한다.

| 구분 | 현재 상태 | 증거와 한계 |
| --- | --- | --- |
| 태스크·검증·스킬·trace의 기본 자료구조 | 구현됨 | `TaskSpec`, `SkillArtifact`, `TraceRecord`, 파일 기반 library/trace store, exact/file/command verifier와 JSON task loader가 있다. |
| 결정적 스킬 실행과 task-conditioned routing | 구현됨 | `governed-provisioning-v2`가 active snapshot, same-name canonical projection, exact contract anchor, positive/negative evidence, 최소 점수와 abstain을 typed decision으로 남긴다. routing schema v2는 collision/suppression을 기록하고 v1 trace도 계속 재생한다. 고정 10-task 평가에서 oracle-bearing `9/9` clean-only, no-skill control abstain, mixed/distractor 0을 만족했다. 이는 모델 실행·task success·일반화 증거가 아니다. |
| route monitoring | 구현됨(선택 proxy) | hook/processor runtime은 active filter, exposure cap, do-not-use guard, `wrong/mixed/empty/spurious` 표기와 repeated-risk hide 제안을 제공한다. 현재 MVP runner의 `selected_skill_ids`는 paper-grade actual invocation이 아니다. |
| lifecycle copy-on-write와 rollback | 구현됨 | `stage_provisional_lifecycle_change`가 live library를 바꾸지 않고 복사본을 만들며, `evaluate_lifecycle_promotion`은 동일 task/verifier, pass-rate, `pi_o`, `pi_m` gate를 검사한다. Build Week demo는 accept와 reject/rollback 모두 테스트한다. |
| skill-local repair 폐루프 | 구현됨(bounded deterministic + two actual model-authored families) | `run_skill_repair`가 재현된 skill-local failure만 받아 target feedback으로 ordered version을 만들고 first-pass version을 선택한다. 분리된 held-out과 whole-library same-verifier non-regression, snapshot drift, unrelated artifact isolation을 통과할 때만 copy-on-write로 승격한다. route-local failure와 missing verifier는 repair에서 제외된다. 두 requested-GPT-5.6 family closure는 있으나 broad 일반화는 아직 아님. |
| hidden→retired 폐루프 | 구현됨(bounded deterministic core) | `run_skill_retirement`는 이미 hidden인 skill, 두 개 이상의 snapshot/trace-bound complete-evidence window, zero selection/invocation, promotion-grade verifier, baseline clean, same-verifier non-regression, COW isolation을 요구한다. 실패하면 hidden parent를 유지하며 물리 삭제는 하지 않는다. 실제 장기 trajectory와 model-backed retirement 품질은 아직 없음. |
| duplicate→alias merge 폐루프 | 구현됨(bounded deterministic core) | `run_skill_merge`는 두 active skill의 동일 routing scope, 두 개 이상의 trace hash, complete overlapping invocation evidence, promotion-grade verifier, exact output-hash equivalence, same-verifier library non-regression을 요구한다. canonical은 byte-identical로 유지하고 redundant skill만 retired hash-bound alias tombstone으로 바꾼다. 실패하면 둘 다 active인 원본으로 rollback한다. 실제 provider trajectory와 부분 중첩 skill의 semantic fusion은 아직 없음. |
| model-authored candidate quarantine | 구현됨(격리 intake v1) | strict provider JSON과 exact response SHA-256을 portable new-only bundle로 묶고, path/size/frontmatter/secret-like content/Python AST를 host 실행 없이 검사한다. process/network/dynamic-code surface를 차단하며 execution/promotion은 명시적으로 false다. 실제 GPT-5.6 생성 성공, isolated execution, G0-G6 채택은 아직 아님. |
| 재구성 가능한 harness variant | 구현됨(구조와 외부 입력 gate) | hook/processor manifest, configuration snapshot/restore, proposal preflight와 `evaluate_harness_evolution`이 있다. 후보 실행과 held-in/held-out delta 계산은 gate 내부가 아니라 caller가 제공한다. |
| metric substrate | 구현됨 | SkillsBench normalized gain, route risk, CTA-lite, actual-invocation용 `InvocationObservation`, More Skills 분해, paired bootstrap 함수가 있다. 일반 실행 trace에서 actual invocation으로 채우는 bridge는 없다. |
| 실제 모델 실행 seam | 제한적으로 구현됨 | `CodexCliAdapter`가 account-auth Codex CLI의 one-task `exec --json`을 raw JSONL SHA-256, thread ID, redacted command/CLI metadata와 연결하고 뒤에서 verifier를 한 번 실행한다. 2026-07-18 실제 answer-only smoke는 통과했지만, provider event에 resolved model 또는 Merlin skill-body load가 없어 benchmark/skill claim은 할 수 없다. |
| SkillsBench/B_cli 실행 기록 | 제한된 별도 증거 | 고정 3-task × 3 paired pilot은 `C0=1/9`, `C1=8/9`, 평균 paired delta `+7/9`이며 C1의 provider-native skill 호출을 별도 기록했다. full-87 v1/v2는 역사적 진단 기록이며 현재 실행 상태 또는 Merlin 효과 증거가 아니다. |
| Build Week Developer Tool | 구현됨, 범위 제한 | 10-task controlled demo에서 2개의 purpose-built distractor 때문에 `1/10, pi_o=11%, pi_m=89%`가 된 뒤, 두 hide를 copy-on-write로 검증하여 `9/10, 100%, 0%`로 회복한다. |
| 고정 생성기와 content-addressed generated snapshot | 아직 없음 | 생성 후보 공급은 설계와 artifact gate만 있고, C3 이후 모든 arm이 공유할 동결 스냅샷이 없다. |
| 실제 호출 공통화 | 구현됨(fixture + 제한된 실제 Codex smoke) | `BaseAgentAdapter` 계약이 selection과 actual skill-body load/provider invocation을 분리하고, raw trace pointer+SHA-256, immutable trace store, strict `TraceRecord -> InvocationObservation` 변환을 제공한다. 실제 Codex CLI smoke는 raw JSONL과 verifier를 연결했으나 actual skill event가 없으므로 `actual_invocation_evidence_complete=false`이고 paper metric 변환은 의도적으로 거부된다. Hermes/B_cli skill evidence는 아직 없다. |
| 공통 관리 정책과 `M0/M1/M2-H/M2-K/M3-K` 실행 | 부분 구현(fixture-only P0) | `ManagementRunContract`가 frozen snapshot/split/agent/model/tools/verifier/budget/repeat/equal capacity를 묶고, M0/M1/M2-H/M2-K evidence capability를 강제한다. `M2-H`는 telemetry-only, `M2-K`는 complete actual trace+outcome/regression만 받으며 incomplete/tampered evidence는 decision/metric에서 제외·공개된다. 3-task synthetic fixture report는 있으나 M3-K, live Hermes, full-87, provider-native skill-event benchmark는 아직 없다. |
| empirical oracle와 논문급 평가 | 아직 없음 | `S*_{restricted}` 추정, natural distractor 실험, full held-out/repeat 평가, task-cluster bootstrap 보고, lifecycle/variant의 실제 re-evaluation이 필요하다. |
| Hermes adapter | 아직 없음 | Hermes 버전 고정, 실행 요청 변환, 실제 skill event 수집, 결과/원시 trace 연결, 동일 verifier workspace contract가 구현되지 않았다. |

## 5. 실험 증거와 주장 경계

### 지금 말할 수 있는 것

- 결정적 10-task corpus에서, 일부러 혼동 가능하게 만든 두 distractor가 routing failure를 만들고, trace-backed hide를 **원본을 바꾸지 않은 상태에서** 동일 verifier로 재검증해 회복시킬 수 있다.
- 이 demo의 조치와 근거 trace ID, 원래/가변 library 상태, promotion checks, rollback 상태는 JSON/HTML report로 재현된다.
- 작은 synthetic scaling 결과는 controlled distractor가 있을 때 `pi_o` 하락과 `pi_m` 상승을 측정 pipeline이 포착함을 보인다.
- 별도 3-task B_cli pilot은 curated skill이 그 고정 model-harness cell에서 도움이 될 수 있다는 긍정 신호를 제공한다. 이는 Merlin 관리 효과가 아니라 C0/C1 calibration evidence다.

### 지금 말할 수 없는 것

- Merlin이 Hermes보다 성능이 좋다.
- Merlin이 일반 LLM agent의 실제 skill invocation을 이미 관리한다.
- controlled 10-task 회복이 SkillsBench 87-task, 자연 distractor, 또는 production agent에서도 재현된다.
- `M2-H` 정책 재구현 결과가 Hermes 전체 시스템의 결과다.
- 현재 또는 원격 full-87이 실행 중이거나 어떤 최종 성능을 냈다. `ONE`의 `47/261` v2 상태는 보존된 과거 monitor 기록일 뿐이다.

논문급 주장은 같은 기반 모델·태스크·verifier·budget·고정 generated snapshot을 유지하고, held-out task에서 `task count × repeats >= 100`, paired/cluster bootstrap CI, 실제 invocation evidence, regression non-increase를 함께 충족한 뒤에만 한다.

## 6. Build Week 제품 slice와 논문 전체의 분리

| 항목 | Build Week: Trace-Governed Skill Recovery | 논문/제품 전체: self-managing skill-harness agent |
| --- | --- | --- |
| 목적 | runtime loop가 trace → hide → same-verifier recovery를 안전하게 수행함을 보여 줌 | Hermes급 실행 에이전트 위에서 고정 skill snapshot의 관리 인과효과를 검증 |
| executor | deterministic `RecipeSkillExecutor`와 10 synthetic tasks | 교체 가능한 실제 agent adapter, 우선 B_cli/Hermes 후보, 이후 두 번째 모델 robustness |
| 위험 원인 | purpose-built 2 distractors | generated + curated + natural/controlled distractors, restricted empirical oracle |
| action | repeated route-risk의 `hide` | admission, task/global hide, repair, merge, retire, validator, provisioning, selector, narrow policy/processor change |
| acceptance | 같은 10 tasks와 정확히 같은 verifiers를 재실행 | lifecycle에는 same-verifier contract; policy variant에는 adaptation/held-in과 held-out non-regression을 모두 요구 |
| 검증된 수치 | 9/10 → 1/10 → 9/10, `pi_o` 100% → 11% → 100%, `pi_m` 0% → 89% → 0% | 아직 없음; full 87-task management arm을 실행해야 함 |
| 해석 | Developer Tool의 작동 증명 | 연구 주장을 위한 benchmark evidence |

Build Week의 same-verifier promotion은 **수명주기 변경이 몰래 task/verifier 표면을 바꿔 점수를 얻지 않는지**를 검사하는 action-level safety gate다. 이것을 `M3-K`의 held-out harness-evolution result라고 부르지 않는다.

## 7. 권장 첫 구현 수직 조각: 실제 호출 증거가 보존되는 base-agent bridge

이 수직 조각은 이제 network-free fake adapter로 구현·검증되었다. Hermes UI, memory, messaging, skill generator를 다시 만드는 일이 아니라, 어떤 기반 실행기에도 붙는 **한 task 실행 → 실제 호출 trace → deterministic verifier → read-only diagnosis** 경계를 완성한 것이다. 다음 단계는 이 경계를 실제 Hermes/B_cli evidence에 맞추는 일이다.

### 완료 정의

한 agent adapter가 한 deterministic task를 실행하면, Merlin이 다음을 하나의 immutable trace bundle로 기록한다.

```text
run contract + library snapshot id/hash
+ provisioned skill ids
+ selected skill ids (있다면)
+ actual skill-body load / invocation events
+ workspace/verifier result
+ model/backend/effort/budget metadata
+ raw-trace pointer and integrity hash
```

이 bundle에서 actual invocation event만 `InvocationObservation`으로 변환한다. `selected != invoked`인 경우에는 selection metric과 invocation metric을 분리해 보고하며, 누락된 actual invocation은 paper-grade `pi_o/pi_m` 계산에서 제외하거나 명시적으로 incomplete로 남긴다.

### 정확한 파일·API·test 계획

| 위치 | 추가/변경 | 계약 |
| --- | --- | --- |
| `src/merlin_harness/models.py` | 추가 | `AgentRunContract`, `SkillInvocationEvent`, `AgentRunResult`를 추가한다. event에는 `skill_id`, `event_kind` (`body_loaded` 또는 provider-native invocation), source/event id, timestamp/order, raw-trace reference를 둔다. `InvocationRecord`의 provisioned/selected 의미는 호환성을 위해 유지한다. |
| `src/merlin_harness/agent_adapter.py` | 새 파일 | `BaseAgentAdapter.run(request) -> AgentRunResult` protocol과 `AgentRunRequest`를 정의한다. adapter는 base-agent identity/version, model/effort, workspace, allowed skills, budget, raw trace locator를 채워야 한다. |
| `src/merlin_harness/runner.py` | 변경 | executor-only path를 유지하면서 adapter path를 추가한다. verifier는 adapter 반환 뒤 동일 workspace에서 한 번 실행하며, actual events를 `TraceRecord.metadata`의 versioned evidence block에 기록한다. |
| `src/merlin_harness/traces.py` | 변경 | trace schema version과 event bundle 직렬화/역직렬화를 지원한다. raw payload는 중복 저장하지 않고 파일 pointer와 hash를 기록한다. |
| `src/merlin_harness/metrics.py` | 변경 | `TraceRecord` evidence block을 `InvocationObservation`으로 바꾸는 strict helper를 추가한다. selection proxy API와 actual-invocation API를 섞어 쓸 수 없게 만든다. |
| `src/merlin_harness/__init__.py` | 변경 | 새 공개 adapter 계약만 export한다. |
| `tests/test_agent_adapter.py` | 새 파일 | fake adapter로 (a) selected지만 body 미로드, (b) oracle body load, (c) distractor body load, (d) no raw-trace/hash, (e) workspace escape/contract mismatch를 검증한다. |
| `tests/test_runner_agent_adapter.py` | 새 파일 | 같은 task/verifier가 adapter run 뒤 한 번 실행되고, task/skill/run contract가 trace에 남으며, selection과 invocation이 분리되는지 검증한다. |
| `tests/test_core.py` | 변경 | actual invocation이 `n/o/m` 분모와 More Skills decomposition으로 들어가고, incomplete evidence가 조용히 0으로 변환되지 않는지 추가한다. |
| `experiments/mvp/run_agent_trace_contract_smoke.py` | 새 파일 | network 없는 fake-adapter smoke를 제공한다. 실제 Hermes/B_cli smoke는 별도 명시 run ID와 raw artifact contract가 고정된 뒤에만 추가한다. |

이 조각의 acceptance는 “모델이 잘 풀었다”가 아니다. **trace에 실제 호출이 있는 한 task가 verifier 결과와 정확히 결합되고, 실제 호출이 없으면 Merlin이 그것을 호출로 가장하지 않는다**는 것이다. 구현은 selected-but-not-loaded, oracle/distractor load, incomplete evidence, raw hash mismatch, workspace/contract mismatch, immutable trace rewrite를 자동 테스트한다.

## 8. 이후 Hermes adapter와 `M2-H` 대 `M2-K` 계약

### Hermes adapter 순서

1. Hermes의 특정 release/commit과 실행 방식을 pin한다. CLI, daemon, API 중 실제로 non-interactive task 실행과 skill invocation evidence를 제공하는 표면을 선택한다.
2. `BaseAgentAdapter`로 task/workspace, task-conditioned exposed skills, completion/verification handoff, raw trace를 변환한다. Hermes 내부 memory·chat·automation을 복제하지 않는다.
3. provider-native skill load/invocation을 `SkillInvocationEvent`로 변환한다. 단순 검색, 화면 표시, candidate ranking은 actual invocation으로 기록하지 않는다.
4. 동일 base-agent/model/effort/tools/verifier/budget에서 one-task smoke를 먼저 실행한다. 그 다음 3-task contract smoke, 그 다음에만 87-task 새 frozen manifest를 검토한다.
5. 결과가 실제 Hermes runtime에서 나온 경우에만 “Hermes + Merlin harness”라고 표기한다. 그렇지 않은 `M2-H`는 policy reimplementation이다.

### 공통 management-policy contract

`M0`, `M1`, `M2-H`, `M2-K`, `M3-K`는 다음 입력·출력과 불변조건을 공유해야 한다.

```text
ManagementRoundInput
  - frozen library snapshot id/hash and equal active-library capacity
  - task/split/run contract (base agent, model, effort, tools, verifier, budget, repeats)
  - immutable adaptation traces and actual invocation evidence
  - pre-registered thresholds and allowed actions

ManagementRoundOutput
  - task-conditioned exposure decisions
  - lifecycle decisions with reason and evidence trace ids
  - optional narrow policy/processor proposal
  - resulting library/variant snapshot id and rollback parent
```

정책별 허용 증거는 의도적으로 다르다.

| Arm | 허용된 의사결정 증거 | 금지 또는 필수 조건 |
| --- | --- | --- |
| `M0` | 없음; predeclared expanded exposure | top-k를 몰래 적용하거나 lifecycle cleanup을 하지 않음 |
| `M1` | 사전 고정 retrieval/top-k | adaptation 결과로 held-out 전에 threshold를 다시 조정하지 않음 |
| `M2-H` | usage/view/patch/recency telemetry만 | task outcome, actual invocation quality, oracle/shadowing, regression을 lifecycle 판단에 사용하지 않음; active capacity는 M2-K와 동일 |
| `M2-K` | outcome, actual invocation, `pi_o/pi_m`, cost-no-gain, regression evidence | frozen snapshot과 predeclared action set을 유지; skill-local와 route-local action을 분리 |
| `M3-K` | M2-K evidence + variant proposal evidence | candidate runtime을 격리해 held-in과 held-out 모두 비회귀일 때만 promote; 실패하면 parent로 rollback |

핵심 비교는 held-out에서의 `M2-K - M2-H`다. 보고 단위는 pass/mean reward, actual-invocation `pi_o`, `pi_wrong`, `pi_mixed`, `pi_empty`, no-oracle spurious rate, regression rate, cost-no-gain, lifecycle action/rollback count다. 모든 arm은 같은 frozen generated snapshot, actual task denominator, verifier contract, model/harness/budget/repeat 값을 가져야 한다.

## 9. 하지 않을 재구현과 과장

- Hermes의 chat UX, memory, messaging, automation, subagent orchestration, generic skill learning, Curator backup UI를 복제해 “새 에이전트”처럼 부르지 않는다.
- Hermes에 lifecycle/verification이 없다고 주장하지 않는다. 공개 정책과 실제 실행을 분리해 서술한다.
- `M2-H` policy reimplementation을 Hermes 전체 runtime 비교라고 부르지 않는다.
- deterministic selector의 `selected_skill_ids`를 일반 agent의 actual invocation이라고 부르지 않는다.
- 10-task controlled recovery, 3-task pilot, 역사적 v1/v2 상태를 87-task Merlin result로 합치지 않는다.
- 모델 가중치 학습, 초기 RL, 임의 코드 전체 자동 수정, 완전한 HarnessX/GRPO를 첫 제품 조각에 넣지 않는다.
- LLM Wiki/Obsidian 운영을 제품 또는 논문 기여로 세지 않는다.

이 계약의 제품적 결론은 단순하다. Merlin은 Hermes를 대체하기 위해 시작하는 프로젝트가 아니라, Hermes 같은 실행 에이전트가 스킬을 축적할수록 더 안전하고 설명 가능하게 동작하도록 만드는 **검증 가능한 관리 계층**이다. 첫 성공 기준은 “더 많은 스킬을 만들었다”가 아니라 “실제 호출 증거에 근거한 좁은 변경이 동일 verifier와 이후 held-out gate를 통과했고, 실패하면 되돌릴 수 있다”이다.
