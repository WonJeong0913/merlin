# Merlin Management-Policy Contract

## 범위

이 문서는 논문용 `M0`, `M1`, `M2-H`, `M2-K` 비교가 같은 실행 조건을
공유하도록 하는 P0 계약이다. 구현은 `src/merlin_harness/management.py`에 있고,
정책 결과는 **read-only decision plan**이다. live library를 바꾸거나,
repair·RL·Hermes runtime·M3-K harness edit를 실행하지 않는다.

따라서 이 조각은 다음을 주장하지 않는다.

- 실제 Hermes 또는 일반 agent에서 M2-H/M2-K가 우월하다.
- controlled fixture의 actual-event가 실제 provider skill invocation이다.
- 현재 10-task Build Week lifecycle 결과가 management-arm benchmark다.
- Codex answer-only smoke가 actual skill invocation 또는 M2-K metric evidence다.

## 공통 불변 계약

`ManagementRunContract`는 모든 arm이 정확히 같아야 하는 다음 값을
content-addressed object로 고정한다.

- frozen library snapshot ID/SHA-256, active skill IDs, equal active-library capacity
- split ID, task IDs, repeats
- base agent/version/backend, model/effort/tools
- task별 verifier ID와 budget ID

`compare_management_reports()`는 snapshot, split, agent, model, effort,
tools, verifier, budget, repeat 또는 output capacity가 하나라도 다르면
비교를 거부한다. 결과 snapshot은 parent snapshot과 input hash에서 계산한
`read_only_decision_plan`이며 `library_mutated=false`다.

## 정책별 capability fence

| Arm | policy-time 입력 | 출력 결정 | 명시적 금지 |
| --- | --- | --- | --- |
| M0 | task별 사전 선언 expanded exposure | exposure만 | adaptation evidence, retrieval tuning, cleanup |
| M1 | task별 사전 고정 top-k | fixed top-k exposure만 | outcome 기반 threshold tuning, cleanup |
| M2-H | `usage/view/patch/recency`만 든 `TelemetryEvidence` | skill-local `hide_skill` proposal | outcome, actual invocation, oracle/shadowing, regression trace 입력 |
| M2-K | raw SHA를 매번 재검증하는 `TraceRecord` + parent verifier outcome/regression group | route-local `guard_route` proposal | incomplete/tampered evidence, skill-content defect 추론, live mutation |

정책은 report trace를 공통 입력으로 받지 않는다. 모든 arm의 동일 report는
policy 실행 뒤 `build_management_round_report()`가 별도로 산출한다. 이
분리가 M2-H가 outcome/invocation evidence를 단지 "사용하지 않았다"고
말하는 대신, 애초에 받지 못하게 한다.

## 공통 report schema와 denominator

각 report row는 verifier pass/score, provisioned/selected/actual-invoked skill
IDs, oracle IDs, actual-evidence complete flag, `n/m/o`, route class, cost,
latency를 가진다. summary는 다음 값을 모두 보존한다.

- verifier total/pass/mean score와 score missing
- complete actual-oracle denominator, incomplete actual-evidence count,
  no-oracle count
- `n/m/o`, wrong/mixed/empty/spurious count와 `pi_o`, `pi_wrong`,
  `pi_mixed`, `pi_empty`, `pi_m`, spurious rate
- cost/latency의 present/missing/mean

`actual_invocation_evidence_complete=false`는 `n` 또는 empty로 강제되지
않는다. metric eligible population에서 제외되고, incomplete denominator에
남는다. raw hash가 바뀐 trace는 report/decision 모두 error로 거부한다.

## Controlled fixture report

다음은 네 arm에 같은 3-task/1-repeat/frozen 3-skill snapshot을 적용하는
network-free fixture다. fixture는 explicit synthetic `skill_body_loaded`
events를 사용하며 Build Week trace를 reinterpret하지 않는다.

```bash
PYTHONPATH=. python3 -m experiments.mvp.run_management_policy_comparison \
  --output /private/tmp/merlin-management-policy-comparison
```

fixture에서 M2-H는 stale telemetry만으로 `distractor`의 skill-local hide를
제안하고, M2-K는 verifier regression을 동반한 complete actual wrong
invocation으로 해당 task route의 route-local guard를 제안한다. 두 report의
metrics/task-row schema는 같지만, decision evidence와 decision scope는
의도적으로 다르다.

기본 명령은 이미 저장된 Codex smoke trace도 read-only로 rehash한다. 해당
trace는 answer-only이고 skill event가 없으므로 M2-K decision denominator의
`excluded_incomplete=1`, paper metric `eligible=0`, `n/m/o=0`으로만
기록된다. 이 과정은 Codex 또는 provider를 재호출하지 않는다.

## 남은 empirical gate

논문급 `M2-K - M2-H` 비교에는 동일 frozen generated snapshot과 held-out
tasks/repeats, provider-native complete skill events, empirical oracle,
regression non-increase, cost/latency collection, paired cluster bootstrap이
필요하다. 이 계약은 그 실행을 공정하게 거부하거나 기록할 수 있게 하는
P0 substrate이지 그 결과가 아니다.
