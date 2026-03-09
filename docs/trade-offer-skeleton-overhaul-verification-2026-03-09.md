# 트레이드 오퍼 스켈레톤 개편 검증 보고서 (2026-03-09)

검증 기준:
- 설계 철학: `docs/trade-offer-skeleton-overhaul-plan-2026-03-08.md`
- 구체 구현안: `docs/trade-offer-skeleton-overhaul-concrete-implementation-plan-2026-03-08.md`

재검증 커맨드:
- `rg -n "classify_target_tier|skeleton_route_|target_tier|skeleton_gate_strictness|skeleton_false_negative_bias|modifier_protection_default_ladder" trades/generation/dealgen`
- `python -m unittest trades.generation.dealgen.test_skeleton_registry_routing trades.generation.dealgen.test_skeleton_compat_aliases trades.generation.dealgen.test_skeleton_modifiers_protection_swap trades.generation.dealgen.test_skeleton_phase4_config trades.generation.dealgen.test_core_budget_guard`

---

## 1) 총평

현재 구현은 **스켈레톤 수 확장 자체는 상당 부분 달성**했지만, 구체 수정안의 핵심 요구사항(특히 **타깃 tier 라우팅**, **protection intent 연동**, **완료 조건 테스트 커버리지**)은 미완료다.

- 장점: registry + domain builder + modifiers 구조 도입, 20개+ skeleton ID 등록, shape 상한 완화.
- 한계: 타깃별 활성 세트 라우팅 부재, 계획에 명시된 일부 skeleton 미구현(`player_swap.one_for_two_depth`), protection intent 힌트 미연동, DoD/Acceptance 기준 검증 테스트 누락.

결론적으로, 목표였던 "실제로 더 다양한 딜이 발생"할 기반은 커졌으나, **문서가 약속한 방식대로 완결되었다고 보기에는 불충분**하다.

---

## 2) 철학/원칙 반영도 점검

### 원칙 A. 기존 4 archetype 호환 유지 + 내부 분해
- 상태: **충족(부분 이상)**
- 근거:
  - compat skeleton이 별도 등록되어 기존 archetype 경로를 유지한다.
  - 신규 builder 후보들도 `compat_archetype`/`arch_compat:*` 태그를 부여한다.

### 원칙 B. domain 재편(player_swap/timeline/salary_cleanup/pick_engineering)
- 상태: **충족**
- 근거:
  - 4개 domain builder 파일이 존재하고 registry에 연결되어 있다.

### 원칙 C. protection을 독립 skeleton이 아닌 modifier 중심으로 운용
- 상태: **부분 충족**
- 근거:
  - `skeleton_modifiers.py`에 protection/swap/second rebalance modifier가 있다.
  - 다만 구체안에서 요구한 `proposal_meta["protection_intent"]`를 decorator로 전달하는 연동은 확인되지 않는다.

### 원칙 D. 타깃 등급별 활성 skeleton 라우팅
- 상태: **미충족**
- 근거:
  - registry 조회는 `get_specs_for_mode()`만 사용하고, tier 인자를 받는 라우팅 인터페이스가 없다.
  - orchestrator(`build_offer_skeletons_buy/sell`)도 모드별 전체 spec를 순회한다.

### 원칙 E. false-negative 완화(현실성 관측 우선)
- 상태: **부분 충족**
- 근거:
  - shape 상한은 완화됨.
  - 그러나 구체안에서 제시된 strictness/bias 계열 config(`skeleton_gate_strictness`, `skeleton_false_negative_bias`)는 확인되지 않는다.

---

## 3) 구체 구현안(DoD/Acceptance) 항목별 검증

### 3-1. 신규 skeleton 수/구성
- 상태: **충족(수량 기준), 부분 미충족(목록 기준)**
- 근거:
  - registry에 26개 skeleton_id(compat 포함) 등록.
  - 신규 domain skeleton은 18개에 근접하나, 계획의 player_swap 8종 중 `one_for_two_depth`가 빠져 있다.

### 3-2. BUY/SELL 타깃 등급별 라우팅
- 상태: **미충족**
- 근거:
  - `target_tier` 분류/라우팅 경로가 실구현 상 존재하지 않거나 연결되어 있지 않다.

### 3-3. modifier(protection/swap) 작동
- 상태: **부분 충족**
- 근거:
  - protection_step_up_down / swap_substitute_for_first / second_round_rebalance 생성 로직은 있다.
  - 하지만 protection decorator와의 의도 강도 힌트 연동은 확인되지 않는다.

### 3-4. 핵심 딜 형태 출현 보장 테스트
- 상태: **부분 충족**
- 근거:
  - routing/alias/modifier 관련 테스트는 존재.
  - 계획서에 명시된 core shape 생성 보장 테스트(`test_skeleton_builders_core_shapes.py`)는 없다.

### 3-5. 계산량 하드캡 유지
- 상태: **충족(코드 구조상)**
- 근거:
  - config의 `max_validations_hard`, `max_evaluations_hard` 유지.
  - core에 budget guard 테스트 파일도 존재.

---

## 4) 추가로 확인된 실제 갭 (중복 제외 보강)

### 4-1) config 키 누락(라우팅/완화 정책 일부)
아래 키들은 concrete plan에 명시되어 있으나 현재 `DealGeneratorConfig`에 없다.
- `skeleton_route_role`
- `skeleton_route_starter`
- `skeleton_route_high_starter`
- `skeleton_route_pick_only`
- `modifier_protection_default_ladder`
- `skeleton_gate_strictness`
- `skeleton_false_negative_bias`

### 4-2) `SkeletonSpec` 인터페이스 불일치
concrete plan의 `SkeletonSpec`에 제시된 `target_tiers`, `gate_fn`, `default_tags`, `allows_modifiers` 필드가 현재 구현에는 없다.
즉 registry 단계에서 “tier별 활성 + gate 함수 기반 스펙 제어”가 구조적으로 빠져 있다.

### 4-3) telemetry/관측 메타 기록 미흡
`DealCandidate`에 메타 필드는 추가됐지만, `DealGeneratorStats`와 core의 telemetry 집계 지점에는
문서가 요구한 `skeleton_id`, `target_tier`, `modifier_trace`, `arch_compat` 집계가 반영되어 있지 않다.
현재는 fit-swap/sweetener 중심 텔레메트리와 domain 균등 beam 배분 위주다.

### 4-4) 테스트는 존재하지만 “tier 라우팅 검증”이 아님
`test_skeleton_registry_routing.py`는 이름과 달리 실제로는 BUY/SELL mode 포함 여부만 검증한다.
문서 요구사항인 “tier별 활성 세트 라우팅 검증”과는 차이가 있다.

---

## 5) 목표 달성도 판단 ("스켈레톤 확대로 다양한 딜 발생")

### 판단: **부분 달성 (완벽 달성 아님)**

- 왜 개선됐는가:
  - 도메인 다변화와 skeleton 개수 증가로 후보 공간은 분명히 확장됨.
- 왜 완벽하지 않은가:
  1. 타깃 tier 라우팅이 없어 "타깃별 현실적 패키지 집합"이라는 핵심 컨트롤이 구현안 수준으로는 미완성.
  2. 계획된 일부 skeleton 미구현(`one_for_two_depth`)으로 카탈로그 완전성이 떨어짐.
  3. 시뮬레이션 회귀 지표(고유 skeleton 수 증가, 2-for-2/3-for-1 출현률, 도메인 비율 등)를 검증한 결과물이 저장되어 있지 않아 "실제로 다양해졌는지"를 데이터로 단정하기 어렵다.
  4. telemetry에 skeleton 메타 집계가 약해 운영 중 “다양성 증가의 실측 관측”도 어렵다.

---

## 6) 우선 보완 권고 (필수)

1. `classify_target_tier` + tier별 route table + `get_specs_for_mode_and_tier` 도입/연결.
2. `DealGeneratorConfig`에 route/strictness/ladder 키를 계획안대로 보강.
3. 누락 skeleton `player_swap.one_for_two_depth` 구현 및 registry 등록.
4. protection intent 메타를 `pick_protection_decorator`까지 전달하는 연결선 추가.
5. `core.py`/stats(및 연계 telemetry)에서 `skeleton_id/domain/target_tier/modifier_trace/arch_compat` 집계 경로 추가.
6. `test_skeleton_builders_core_shapes` + tier 라우팅 전용 테스트를 추가해 검증 강도 상향.
7. 고정 seed 회귀 스크립트/리포트로 "다양성 지표 증가"를 수치화해 문서화.

이 항목들이 완료되어야, 본 개편의 문서상 목표를 "완벽 반영"으로 판정할 수 있다.
