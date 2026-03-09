# 트레이드 오퍼 스켈레톤 개편 보강 실행 계획 (2026-03-09)

기준 문서:
- 검증 보고서: `docs/trade-offer-skeleton-overhaul-verification-2026-03-09.md`
- 구체 구현안: `docs/trade-offer-skeleton-overhaul-concrete-implementation-plan-2026-03-08.md`
- 상위 철학: `docs/trade-offer-skeleton-overhaul-plan-2026-03-08.md`

---

## 0) 목적과 완료 기준

본 보강 계획의 목적은 검증 보고서에서 지적된 미흡 사항을 모두 해소하여,
"스켈레톤 수 증가"를 넘어 **타깃별로 현실적인 다양한 딜이 실제로 생성/관측되는 상태**를 만드는 것이다.

완료 판정(보강 DoD):
1. BUY/SELL 경로에 `target_tier` 기반 라우팅이 실제 연결되어 활성 스켈레톤 집합이 티어별로 달라진다.
2. `DealGeneratorConfig`/`SkeletonSpec`가 concrete plan 명세와 합치한다.
3. 누락된 `player_swap.one_for_two_depth`가 구현/등록된다.
4. protection intent(`light|mid|heavy`)가 modifier → protection decorator로 전달된다.
5. telemetry/stats에서 `skeleton_id/domain/target_tier/modifier_trace/arch_compat`를 집계한다.
6. tier 라우팅 + core shape 생성 보장 테스트가 추가되어 CI에서 검증된다.
7. 고정 seed 회귀 리포트로 다양성 증가를 수치로 확인한다.

---

## 1) 미흡사항-원안 매핑 요약

### A. 티어 라우팅 미구현
- 검증 지적: mode 기반 전체 순회만 수행하며 `target_tier` 라우팅 미작동.
- 원안 요구: `classify_target_tier` + `get_specs_for_mode_and_tier` + BUY/SELL 오케스트레이터 연결.
- 보강 핵심: **라우팅 인터페이스/호출 경로를 강제 일원화**.

### B. 설정 키/스펙 필드 누락
- 검증 지적: route/strictness/ladder 계열 config 키 부재, `SkeletonSpec` 필드 불일치.
- 원안 요구: route 4종 + strictness/bias + protection ladder + tier/gate/default_tags/allows_modifiers.
- 보강 핵심: **설계 문서 명세를 타입 계층에 반영**.

### C. 스켈레톤 카탈로그 불완전
- 검증 지적: `player_swap.one_for_two_depth` 누락.
- 원안 요구: player_swap 8종 완성.
- 보강 핵심: **카탈로그 완전성 회복**.

### D. protection intent 연동 단절
- 검증 지적: modifier는 있으나 `proposal_meta["protection_intent"]` 전달 부재.
- 원안 요구: decorator에서 intent 힌트 수용.
- 보강 핵심: **modifier → decorator 메타 파이프 연결**.

### E. 관측 지표 약함
- 검증 지적: DealCandidate 메타 일부만 추가되고 stats/telemetry 집계 누락.
- 원안 요구: skeleton/tier/modifier/arch_compat 집계.
- 보강 핵심: **생성 결과의 운영 가시성 확보**.

### F. 테스트가 요구사항을 직접 검증하지 못함
- 검증 지적: routing 테스트가 사실상 mode 포함성 검증 수준.
- 원안 요구: tier별 활성 세트 검증 + core shape 생성 보장.
- 보강 핵심: **요구사항 기반 테스트 재정렬**.

---

## 2) 파일별 보강 작업 상세

## 2-1. `trades/generation/dealgen/types.py`

수정 항목:
1. `DealGeneratorConfig`에 아래 키 추가
   - `skeleton_route_role`
   - `skeleton_route_starter`
   - `skeleton_route_high_starter`
   - `skeleton_route_pick_only`
   - `modifier_protection_default_ladder`
   - `skeleton_gate_strictness`
   - `skeleton_false_negative_bias`
2. `DealCandidate` 메타 필드 존재 여부 재검증
   - `skeleton_id`, `skeleton_domain`, `target_tier`, `compat_archetype`, `modifier_trace`
3. 기본값 정책
   - route tuple은 기존 4 archetype + 신규 skeleton ID를 안전 기본값으로 포함.
   - strictness/bias는 concrete plan 기본값(0.35 / 0.75) 채택.

수용 기준:
- config 인스턴스 생성 시 신규 키 누락 예외가 없다.
- 런타임에서 라우팅/게이트/modifier가 config 기반으로 분기한다.

## 2-2. `trades/generation/dealgen/skeleton_registry.py`

수정 항목:
1. `SkeletonSpec` 필드 정합화
   - 필수 반영: `target_tiers`, `gate_fn`, `default_tags`, `allows_modifiers`.
2. 조회 API 확장
   - `get_specs_for_mode_and_tier(mode, tier, config)` 추가.
   - tier + mode + gate_fn + route config를 모두 적용한 활성 리스트 반환.
3. 정렬 규칙
   - `priority` 오름차순 + 동일 priority에서 domain 균형 유지 정책 문서화.

수용 기준:
- 동일 mode라도 tier가 다르면 반환 spec 집합이 달라진다.
- gate 함수 false인 spec은 최종 활성에서 제외된다.

## 2-3. `trades/generation/dealgen/utils.py`

수정 항목:
1. `classify_target_tier(...)` 구현/보강
   - 반환: `ROLE|STARTER|HIGH_STARTER|STAR|PICK_ONLY`.
2. 게이트 보조 함수 추가
   - rental 판단, bad-money 판단, strictness/bias 반영형 threshold helper.
3. shape gate 연계
   - false-negative 완화를 위해 strictness/bias를 gate 판단 입력으로 통합.

수용 기준:
- 대표 타깃 fixture에서 tier 분류가 일관적으로 재현된다.
- strictness 조정 시 gate 통과율이 단조적으로 변한다.

## 2-4. `trades/generation/dealgen/skeletons.py`

수정 항목:
1. BUY/SELL 오케스트레이터 공통 플로우 정렬
   - `tier = classify_target_tier(...)`
   - `specs = get_specs_for_mode_and_tier(mode, tier, config)`
   - spec 순회 생성 → shape/filter → modifier 적용 → beam cap.
2. 호환성 메타 강제
   - 모든 candidate에 `archetype=compat_archetype`
   - tags에 `arch_compat:<compat_archetype>` 부여.
3. modifier 적용 제약
   - `allows_modifiers=False` spec은 modifier 스킵.

수용 기준:
- BUY/SELL 모두 tier 라우팅 결과가 로그/메타에서 확인된다.
- 호환 alias가 누락된 candidate가 없다.

## 2-5. `trades/generation/dealgen/skeleton_builders_player_swap.py`

수정 항목:
1. `player_swap.one_for_two_depth` 신규 구현
   - 기본 shape: 1 ↔ 2 depth
   - `starter_for_two_rotation` 대비 낮은 강도(합산 가치/샐러리 gap 완화).
2. registry 등록
   - target tier 허용 범위, gate_fn, default tags 명시.
3. core shape 안정성
   - `_shape_ok` 통과 가능한 최소 템플릿 2종 확보.

수용 기준:
- unit test에서 해당 skeleton_id가 실제 candidate를 최소 1개 생성한다.

## 2-6. `trades/generation/dealgen/skeleton_modifiers.py`

수정 항목:
1. protection modifier가 `proposal_meta["protection_intent"]`를 세팅
   - ladder 단계와 intent 매핑: `prot_light→light`, `prot_mid→mid`, `prot_heavy→heavy`.
2. modifier trace 표준화
   - `modifier_trace`에 `protection_step_up_down`, `swap_substitute_for_first`, `second_round_rebalance` 기록.
3. variant cap 보장
   - 후보당 `modifier_max_variants_per_candidate` 초과 금지.

수용 기준:
- protection 변형 생성 케이스에서 intent 메타가 decorator 입력으로 전달된다.

## 2-7. `trades/generation/dealgen/pick_protection_decorator.py`

수정 항목:
1. `proposal_meta.protection_intent` 입력 수용.
2. intent 기반 기본 protection ladder 우선순위 적용.
3. intent 미존재 시 기존 fallback 유지(역호환).

수용 기준:
- 동일 base candidate라도 intent 값에 따라 보호 강도 후보가 달라진다.

## 2-8. `trades/generation/dealgen/core.py` (+ 연계 telemetry 모듈)

수정 항목:
1. stats/telemetry 수집 필드 추가
   - `skeleton_id`, `skeleton_domain`, `target_tier`, `modifier_trace`, `arch_compat`.
2. 집계 지표 추가
   - tick당 고유 skeleton 수
   - domain별 비율
   - modifier 적용 성공률
3. budget guard와 동시 기록
   - hard cap 조기 중단 시점에도 skeleton 메타를 함께 남김.

수용 기준:
- 운영 로그에서 “무엇이 얼마나 생성됐는지”를 skeleton 단위로 추적 가능.

---

## 3) 테스트/검증 보강 계획

## 3-1. 테스트 파일 단위 작업

1. `trades/generation/dealgen/test_skeleton_registry_routing.py`
   - mode 포함성 검증에서 확장하여 **tier별 활성 집합**을 직접 검증.
   - route config override 시 활성 집합이 즉시 바뀌는지 확인.

2. `trades/generation/dealgen/test_skeleton_builders_core_shapes.py` (신규)
   - 핵심 skeleton 최소 14개를 fixture로 고정.
   - 각 skeleton에서 candidate 1개 이상 + `_shape_ok` 통과 보장.

3. `trades/generation/dealgen/test_skeleton_modifiers_protection_swap.py`
   - protection intent 메타 전파 검증 케이스 추가.
   - `allows_modifiers=False`에서 modifier 미적용 검증 추가.

4. `trades/generation/dealgen/test_skeleton_phase4_config.py`
   - strictness/bias 값 변화에 따른 gate 통과율 변화 검증 추가.

5. `trades/generation/dealgen/test_core_budget_guard.py`
   - hard cap 유지 + telemetry 필드 누락 없음 동시 검증.

## 3-2. 회귀 리포트 산출

신규 문서 생성:
- `docs/trade-offer-skeleton-overhaul-diversity-regression-2026-03-09.md`

포함 지표:
- 고유 `skeleton_id` 수(기존 대비 증감)
- 2-for-2 / 3-for-1 / one-for-two-depth 출현률
- domain 비율(player_swap/timeline/salary_cleanup/pick_engineering)
- target tier별 오퍼 도달률
- validations/evaluations 및 hard cap 근접도

---

## 4) 권장 구현 순서 (리스크 최소화, 파일 단위 명시)

### Step 1. 타입/설정 정합화 (컴파일/초기화 안정화)
수정 파일:
- `trades/generation/dealgen/types.py`
- `trades/generation/dealgen/skeleton_registry.py`

작업 포인트:
- `DealGeneratorConfig` 신규 키 추가(route/strictness/bias/ladder).
- `SkeletonSpec` 필드 정합화(`target_tiers`, `gate_fn`, `default_tags`, `allows_modifiers`).
- registry 조회 함수 `get_specs_for_mode_and_tier(mode, tier, config)` 추가.

선행 이유:
- 이후 단계(`utils.py`, `skeletons.py`, 테스트)가 모두 이 타입/인터페이스를 참조하므로 가장 먼저 고정해야 함.

### Step 2. 티어 분류 + 오케스트레이터 연결 (실행 경로 전환)
수정 파일:
- `trades/generation/dealgen/utils.py`
- `trades/generation/dealgen/skeletons.py`

작업 포인트:
- `classify_target_tier(...)` 구현.
- BUY/SELL에서 `get_specs_for_mode_and_tier(...)` 호출로 전환.
- candidate 메타(`arch_compat`, tier, skeleton_id) 일관 부착.

선행 이유:
- 스켈레톤 다양성의 핵심 제어점이 라우팅이므로, 실제 생성 경로를 먼저 계획안 구조로 바꿔야 함.

### Step 3. 누락 스켈레톤 보강 (카탈로그 완결)
수정 파일:
- `trades/generation/dealgen/skeleton_builders_player_swap.py`
- `trades/generation/dealgen/skeleton_registry.py` (등록/라우트 포함)

작업 포인트:
- `player_swap.one_for_two_depth` 구현.
- tier 허용 범위/priority/gate/default_tags 포함 registry 등록.

선행 이유:
- Step 2에서 라우팅이 붙은 뒤 실제 후보군이 충분히 생성되도록 즉시 카탈로그 완전성을 맞춤.

### Step 4. modifier → decorator intent 파이프 연결
수정 파일:
- `trades/generation/dealgen/skeleton_modifiers.py`
- `trades/generation/dealgen/pick_protection_decorator.py`

작업 포인트:
- modifier에서 `proposal_meta["protection_intent"]` 기록.
- decorator에서 intent 기반 ladder 우선순위 적용(미지정 시 fallback).

선행 이유:
- 라우팅/카탈로그가 만든 후보의 quality variation을 보호강도 레버로 실질 확장하기 위함.

### Step 5. telemetry/stats 확장 (운영 관측 가능화)
수정 파일:
- `trades/generation/dealgen/core.py`
- `trades/generation/dealgen/trade_contract_telemetry.py` (또는 실제 telemetry 집계 파일)
- 필요 시 `trades/generation/dealgen/types.py` (stats 구조체 필드 추가)

작업 포인트:
- `skeleton_id/domain/target_tier/modifier_trace/arch_compat` 수집.
- 고유 skeleton 수, domain 비율, modifier 성공률 집계 추가.

선행 이유:
- 테스트 외 실제 실행에서 “다양성이 늘었는지”를 관측 가능한 형태로 남기기 위함.

### Step 6. 테스트 보강 (요구사항 고정)
수정 파일:
- `trades/generation/dealgen/test_skeleton_registry_routing.py`
- `trades/generation/dealgen/test_skeleton_builders_core_shapes.py` (신규)
- `trades/generation/dealgen/test_skeleton_modifiers_protection_swap.py`
- `trades/generation/dealgen/test_skeleton_phase4_config.py`
- `trades/generation/dealgen/test_core_budget_guard.py`

작업 포인트:
- tier 라우팅 검증, core shape 생성 보장, intent 전파, strictness/bias 동작, hard cap+telemetry 동시 검증.

선행 이유:
- Step 1~5 결과를 회귀 안전장치로 고정해야 이후 튜닝 시 역행을 방지 가능.

### Step 7. 고정 seed 회귀 및 결과 문서화
수정 파일:
- `docs/trade-offer-skeleton-overhaul-diversity-regression-2026-03-09.md` (신규)
- 필요 시 실행 스크립트/명령 정리 문서(`docs/...` 하위)

작업 포인트:
- 고유 skeleton 수, 2-for-2/3-for-1/one-for-two-depth 출현률, domain 비율, tier 도달률, hard cap 사용량 보고.

선행 이유:
- "다양한 딜이 실제로 발생"했는지 최종 산출물로 증명하는 단계.

---

## 5) 최종 인수 체크리스트

- [ ] `get_specs_for_mode_and_tier`가 BUY/SELL 실경로에서 호출된다.
- [ ] tier별 활성 skeleton 집합 차이가 테스트로 고정된다.
- [ ] `player_swap.one_for_two_depth`가 registry에 등록되고 생성 검증이 통과한다.
- [ ] protection intent가 decorator까지 전달되어 ladder 선택에 반영된다.
- [ ] telemetry에 skeleton 메타 5종이 집계된다.
- [ ] core shapes 테스트(14개 이상)와 tier 라우팅 테스트가 모두 통과한다.
- [ ] 회귀 리포트에서 다양성 지표 개선이 수치로 확인된다.
- [ ] validations/evaluations hard cap이 유지된다.

이 체크리스트를 모두 충족하면, 상위 철학(호환 유지 + 현실적 다양성 확대 + 관측 가능성 확보)이 구현/검증/운영 관측까지 일관되게 완결된다.
