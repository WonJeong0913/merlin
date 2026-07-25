# Plan vs Implementation Gap Matrix

Created: 2026-07-08. Audited: 2026-07-19. Direction updated: 2026-07-13. 기준: `docs/merlin-experiment-plan.md` (E0–E6, C0–C10 plus C8-H) vs 레포 현재 상태.

## 0. 2026-07-13 방향 전환 갭

연구 우선순위는 `하네스 거버넌스 80 : 제한된 스킬 생성/수리 20`으로
고정한다. 구현률과 무관하게 이후 작업 순서는 다음 의존성을 따른다.

| 새 요구 | 현재 구현 | 상태 |
|---|---|---|
| 모든 관리 arm이 공유하는 content-addressed generated-skill snapshot | 없음 | ❌ E3에서 한 번 생성 후 동결 필요 |
| 공통 `ManagementPolicy` 실행 계약 | 동일 snapshot/split/agent/model/tools/verifier/budget/repeat/capacity 계약과 공통 report schema 구현 | 🟡 network-free fixture 검증 완료, 실제 model trajectory 결선 필요 |
| `M0` naive expanded exposure | 공통 계약의 전체 동결 라이브러리 노출 arm 구현 | 🟡 논문 의미론 구현, 실제 model trajectory 필요 |
| `M1` fixed top-k provisioning | 공통 계약의 사전 고정 task별 top-k arm 구현 | 🟡 실제 model trajectory 결선 필요 |
| `M2-H` Hermes-Curator-inspired usage/recency baseline | outcome/invocation 비가시 telemetry-only hide plan 구현 | 🟡 matched 실제 trajectory 필요 |
| `M2-K` outcome/shadowing/regression lifecycle | complete actual-invocation evidence만 쓰는 route-local plan과 COW policy 적용→동일계약 재실행→승격/rollback 구현 | 🟡 synthetic actual-event fixture 8/8 gate 통과, 실제 model/full-87 재평가 필요 |
| `M3-K` gated policy/processor evolution | reconstructable variant + fresh executor별 내부 held-in/held-out/regression paired 실행 + proposal/capability/full-209 library binder + exact-522 raw/audit evidence replay | 🟡 522 trajectory 모두 동일 `(task, trial)`의 ordered full-209 snapshot에 결합. 실제 preflight + non-benchmark canary + inspected container를 합성하는 capability schema v3, ordinal-1 only gate, sealed first-cell replay 뒤 ordinal 2–6만 여는 단계적 admission 구현. Mac/현재 DESKTOP 문맥은 아직 적격 executor가 아니며 실제 model trajectory 필요 |

`M2-H`는 실제 Hermes 결과가 아니라 공개 규칙의 policy reimplementation이다.
실제 Hermes runtime을 같은 계약으로 실행하기 전에는 그렇게 표기한다.
짧은 벤치에서는 30/90일이 자연 경과하지 않으므로 adaptation split에서
active-library budget과 usage/recency threshold를 사전 고정하고 C9와 용량을
맞춘다. held-out 결과로 threshold를 조정하면 안 된다.

## 1. 지표 (Metrics) — 산식 골격, 실험 의미론 미결선

| 계획 지표 | 구현 | 상태 |
|---|---|---|
| pass rate, G_skill/G_gen (포화 가드 포함) | `normalized_gain` | ✅ |
| PL, G_king | 산식 = 기존 함수 조합 | ✅ (실험 스크립트에서 조합만) |
| π_o | `clean_oracle_invocation_rate`, library-scale `oracle_invocation_event_summary` | 🟡 library-scale 경로는 raw-hash 검증 actual invocation만 사용. 기존 간이 wrapper는 selected 기반이라 paper claim에 사용 불가 |
| π_m = π_wrong + π_mixed | `shadowing_rate`, `oracle_invocation_event_rates`, `wrong/mixed_skill_invocation_rate` | 🟡 library-scale 1,566-cell 집계는 actual invocation 결선 완료. 실제 모델 trajectory 미실행 |
| π_empty | `no_skill_when_oracle_rate` | ✅ |
| spurious | `spurious_invocation_rate` | ✅ |
| cost_no_gain | `cost_no_gain_rate` | ✅ |
| SRR, OSR | 단순 산식 | ✅ (조합만) |
| R_route (상호배타 가중합) | `route_risk_components`, `route_risk_score` | ✅ |
| HGI (대시보드) | 없음 | 🟡 소형, 우선순위 낮음 |
| Δ_ctx / Δ_shd와 ρ_n/ρ_m/ρ_o | 분모-aware decomposition API + oracle-bound 6-arm aggregator | 🟡 1,566-cell synthetic complete trajectory와 불변식 결선 완료. 실제 모델 trajectory 필요 |
| paired bootstrap CI | `paired_bootstrap_ci` + `clustered_paired_bootstrap_cis` | 🟡 task cluster→paired trial trajectory 2-stage bootstrap를 1,566-cell decomposition에 결선. 87 cluster/261 trajectory/2,000회 synthetic 검증 완료, 실제 모델 trajectory 필요 |

## 2. 하네스 (Loop 3–4 / Phase 7) — scaffold, 논문 수준 실행계약 미완성

| 계획 요소 | 구현 | 상태 |
|---|---|---|
| hook/processor runtime | `harness.py` 8 hooks, 5 processors | 🟡 hook-indexed processor scaffold. hook별 event type/허용 mutation 검증 없음 |
| route-event 주석 (oracle_only/wrong/mixed/spurious/empty) | `ShadowingMonitorProcessor` | ✅ |
| exposure budget / do-not-use / skill-state 필터 | 각 processor | ✅ (do-not-use 매칭은 0.6 overlap 휴리스틱) |
| lifecycle 제안 (hide) | `ShadowingLifecycleProcessor` | 🟡 제안까지. route 오류를 task-scoped selector review보다 global hide로 귀속할 위험 |
| variant snapshot (config 포함, 재구성 가능) | `snapshot_harness_variant` + `build_runtime_from_variant` | ✅ |
| 승격 게이트 | core `evaluate_harness_evolution`은 외부-delta scaffold로 보존; research-only `harness_policy_evaluation.py`는 delta 입력 없이 parent/candidate를 내부 실행 | 🟡 deterministic 6-task×2-repeat×2-variant 24 trajectory에서 10/10. 실제 model/full-87 미실행 |

## 3. 실행 계층 (Executor) — 최대 갭

| 계획 요소 | 구현 | 상태 |
|---|---|---|
| 결정적 smoke executor | `runner.py` (recipe 기반) | ✅ E0 전용 |
| **model executor backends** | `CliModelExecutor`, Claude/Codex CLI factories, `backend-matrix.json` | 🟡 ONE의 Claude.ai/ChatGPT 계정 로그인은 확인. Claude 전용 `H_paper_cli_mcp_v1`을 Codex로 재라벨하지 않도록 model-free Codex MCP capability gate 추가. local Codex에서 20개 tool-bearing feature의 per-run disable 계약은 확인했지만 runtime inventory 증거는 아님. 새 schema-v3 composer는 실제 canary `tools/call(exec)`와 inspected Docker container까지 결합해야 ordinal 1만 허용한다. Mac의 기존 canary는 서버 호출 전 취소됐고 Docker도 없어 부적격 |
| **Docker task 실행** (SkillsBench verifier) | `run_oracle_readiness.py`로 ONE/WSL에서 87-task oracle+verifier 실행 완료 | ✅ B3 완료 |
| executor 인터페이스 (deterministic/API 교체 가능) | `src/merlin_harness/executors.py`, `runner.py executor=` 주입 | ✅ B1 완료 |
| harness mode tracking | 두 runner가 mode, wall time, timeout, B_cli auth contract를 기록 | 🟡 `H_scripted_solver`는 engineering-only; paper-aligned `H_paper_cli` 필요 |

과거 `paper-cli-full87-v2-20260712` manifest는 당시
`container_exec_mcp.py`의 hash를 동결했다. 이후 MCP 경계 강화로 현재 파일
hash가 달라졌으므로 그 역사적 manifest는 validator에서 의도대로
fail-closed 된다. 과거 manifest hash를 소급 수정하지 않으며, 다음 실제
실행은 새 executor/version/manifest 계약으로 생성해야 한다.

## 4. 스킬 생성·수리 (Loop 2 / C2, C3, v0→v1) — bounded vertical slice 완료

| 계획 요소 | 구현 | 상태 |
|---|---|---|
| 실패/need → 후보 스킬 생성 (C2) | registered deterministic compiler + 실제 requested-GPT-5.6 structured authoring path | 🟡 TODO extraction 한 family의 bounded evidence, 일반 생성 아님 |
| validation gate 통과 채용 (C3) | quarantine, AST safety, target/hidden/negative, verifier trust, COW G0–G6 | ✅ 한 model-authored candidate 12/12 기록 및 2026-07-19 raw-chain fresh 15/15 재검증; confinement-runtime 적용 실패는 candidate verifier 실패와 분리. 일반화 실험은 미완성 |
| SkillRevise-lite 수리 (v0→v1) | skill-local diagnosis, target-only reviser visibility, first target-pass, hidden/library regression, COW promotion/rollback | 🟡 결정론적 fixture와 두 distinct requested-GPT-5.6 v1→v2 closure 완료(각 6/6 gates, 13/13·14/14 chain audits); broad multi-family·반복 실험 미완성 |

이 절은 지원 축 20%다. B6 완료 후 생성기를 반복 최적화하지 않고
accepted/rejected snapshot을 동결한다. B9는 skill-local failure에만 적용하고,
wrong/mixed/empty route failure를 skill text 수리로 덮지 않는다.

## 5. 실험 인프라 (E-phases)

| Phase | 필요물 | 상태 |
|---|---|---|
| E0 smoke | run_library_scaling.py, 10 task, seed skills | ✅ 완료 (결과 보유) |
| E1 readiness | static + ONE/WSL executable audit | 🟡 raw 74는 float parser 오류. 원본 보존 재분류: strict 79/87, reward-authoritative 80/87. Targeted corrected rerun은 fix-build 두 건을 reward=0, debug-trl을 partial=0.6으로 확정. civ6는 reward=1인데 pytest 2 fail이라 verifier exception. 수정 runner로 full rerun 필요 |
| E2 no-skill vs curated (87) | B_cli + Docker + scheduling manifest | ⛔ 확대 중단. 기존 C0 2/9, C1 5/9는 `seed=1`이 아니라 single-trial scripted engineering pilot이며 YAML/skill-image 누수 가능성과 non-native truncated C1 때문에 paper C0/C1 claim 불가. body-only, skill-free image, r0, resource limit 패치는 완료; `H_paper_cli` 반복 실행은 미완성 |
| E3 generated (87) | + 스킬 생성기 | ❌ |
| E4 shadowing + S*_restricted 추정 | canonical 1,305-cell manifest 위에 87 no-skill + 232 curated single-skill × 3 = 957-cell empirical-oracle 추정 manifest, 0/1-skill cell materializer, external-result recorder, 6-cell task-local expansion gate, portable evidence assembler 구현. Codex MCP capability가 handshake/canary/native-feature suppression/container 조건을 분리하고 현재 local 실행을 fail-closed 차단. 실제 ordinal-1·6-cell GPT-5.6 pilot·957-cell oracle 추정·1,566-cell 모델 실행은 미완성 | 🟡 실행 계약 완료, 적격 executor host 필요 |
| E5 개입 (C7–C10) | M2-K route guard COW 재평가 + M3-K 내부 variant 평가 + proposal/capability/full-209 binding + ordinal-1 → six-cell immutable handoff + 522 external evidence replay | 🟡 controlled end-to-end, exact-denominator synthetic replay, oracle/verifier 비노출 materializer와 실행 직전 209-skill/task/verifier/variant byte revalidator, first-cell sealed-evidence admission 완료; local executor 부적격으로 실제 model trajectory 미완성 |
| E6 최종 claim | + bootstrap CI 분석 | ❌ |
| adaptation/held-out/regression split manifest | `experiments/skillsbench/split-manifest.json` (35/30/22) | ✅ B2 완료 |

## 6. 빌드 순서 (의존성 기준)

```text
B1. executor 인터페이스 분리 (deterministic | api) ......... ✅ 완료
B2. split manifest 생성기 (87 → adapt/held-out/regression) .. ✅ 완료
B3. Docker/BenchFlow 실행 어댑터 + oracle solve.sh 검증 ..... ✅ 완료 on ONE/WSL
B4. model executor backend + C0/C1 실행 (E2) ................. 🟡 B_cli 인증과 scripted contract는 동작. 다음은 full 확대가 아니라 `H_paper_cli` 1-task×3 trial contract 검증 → 3-task paired pilot → strict-ready set 순서
B5. R_route 합성 + bootstrap CI 분석 모듈 ................... ✅ 완료
B6. 고정 스킬 생성기 + gate + snapshot 동결 (E3) ............ 중형, 20% 지원 축
B7. S*_restricted 격리 추정기 (E4) .......................... 🟡 schedule/materializer/recorder/ordinal-1→6-cell gate/assembler와 Codex MCP fail-closed preflight 완료. strict tool control + inspected Docker host에서만 실제 실행 허용
B8a. 공통 ManagementPolicy + M0/M1/M2-H ..................... ✅ 공통 계약/fixture 완료, 실제 trajectory 필요
B8b. lifecycle 실제 적용 + M2-K 재평가 루프 ................. 🟡 COW route-policy + 8-gate synthetic 재실행 완료, 실제 trajectory 필요
B8c. 내부 variant 평가 + M3-K 승격/rollback ................. 🟡 24-trajectory runtime + full-209 binder + ordinal-1→6-cell handoff + exact-522 external replay 완료, strict eligible host model run 필요
B9. bounded Reviser (v0→v1, skill-local only) ................ 🟡 두 distinct actual requested-GPT-5.6 script-only repair closure 완료, broad multi-family/repeated model evaluation 미완성
B10. bounded Retirement (hidden→retired tombstone) ........... 🟡 7-gate COW promotion/rollback core와 fail-closed tests 완료, 장기 actual trajectory 미완성
B11. bounded Merge (duplicate→alias tombstone) ............... 🟡 9-gate exact-equivalence/COW promotion·rollback core와 controlled evidence 완료, actual provider trajectory·semantic fusion 미완성
```

소형 = 반나절 이하, 중형 = 1–3일. B4부터 계정 사용량이 발생한다. 현재 스케줄은 strict derived readiness `passed=79`를 사용하지만, 수정 runner의 87-task 재실행 전에는 확정 readiness로 간주하지 않는다.

## 7. 요약

- **검증된 실행 부품**: B_cli 계정 인증, Docker oracle/verifier, split/scheduling artifact, M0/M1/M2-H/M2-K 공통 계약, clustered bootstrap, bounded creation/repair, M2-K COW 재평가/rollback, M3-K 내부 paired variant promotion/rollback.
- **fixture 수준 결선**: complete synthetic actual-event로 route-local guard 계획→정확한 staged exposure→동일계약 재실행→8-gate 승격 및 regression rollback을 검증했다.
- **부재**: 실제 957-cell empirical oracle, 1,566-cell library-scale model 결과, matched 실제 M0/M1/M2-H/M2-K trajectory, 522-cell M3-K model trajectory.
- **결론**: 단일 구현률 퍼센트는 폐기한다. 현재 B8b의 코드 경로는 닫혔지만 논문 결과는 아니다. 다음 병목은 strict Codex tool-control + inspected Docker host에서 실제 full-209 ordinal 1을 봉인하고, 그 evidence replay가 통과한 뒤 나머지 six-cell pilot을 실행하는 것이다.
