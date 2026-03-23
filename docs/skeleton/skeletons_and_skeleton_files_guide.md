# dealgen 스켈레톤 파일 설명서 (테스트 파일 제외)

이 문서는 `trades/generation/dealgen/` 내부의 **`skeletons.py` + `skeleton_*` 계열 파일**(테스트 파일 제외)이 각각 어떤 역할을 하는지, 흐름 중심으로 쉽게 설명합니다.

---

## 1) 큰 그림: 스켈레톤이란?

트레이드 생성기는 “아무 조합이나” 만들지 않고, 먼저 **거래 구조 템플릿(=skeleton)** 을 고릅니다.  
예를 들어:
- “픽만 보내는 구조”
- “젊은 선수 + 픽 구조”
- “2-for-1 구조”
- “급여 정리용 구조”

즉, 스켈레톤은 **거래 모양(Shape)을 먼저 만드는 단계**입니다.

---

## 2) 파일별 역할

## `skeletons.py` (오케스트레이터)

핵심 포인트:
- BUY/SELL 모드에서 스켈레톤 생성 전체를 지휘합니다.
- 대상 자산의 `target_tier`를 분류하고, 레지스트리에서 해당 tier에 맞는 스켈레톤을 가져와 실행합니다.
- 생성된 후보에 공통 메타(`skeleton_id`, `domain`, `target_tier`, `arch_compat`)를 붙입니다.
- modifier를 적용해 보호픽/스왑 같은 파생 변형도 만듭니다.
- 마지막에 shape 제한(선수 수/픽 수/총 자산 수 등)을 재검증하고 beam width만큼 잘라냅니다.

쉽게 말하면:
- **“무엇을 만들지 결정 + 만들고 + 정리”** 하는 총괄 파일입니다.

---

## `skeleton_registry.py` (스켈레톤 라우팅 표)

핵심 포인트:
- `SkeletonSpec` 데이터 구조로 각 스켈레톤의 정체를 정의합니다.
  - `skeleton_id`, `domain`, `compat_archetype`, `mode_allow`, `target_tiers`, `priority`, `build_fn` 등
- `build_default_registry()`에서 실제 스켈레톤 목록을 등록합니다.
  - compat 계열
  - player_swap 계열
  - timeline 계열
  - salary_cleanup 계열
  - pick_engineering 계열
- `get_specs_for_mode_and_tier()`가 mode/tier/config를 받아 **실제로 실행할 스켈레톤 리스트를 필터링**합니다.

쉽게 말하면:
- **“어떤 상황에서 어떤 스켈레톤을 쓸지 적어둔 배차표”**입니다.

---

## `skeleton_modifiers.py` (후처리 변형기)

핵심 포인트:
- 기본 스켈레톤 후보를 입력받아 “추가 변형”을 만듭니다.
- 대표적으로:
  - 첫 번째 1라운드 픽에 보호 조건(top-N) 추가
  - 1라운드 픽을 스왑권으로 대체
- 적용 여부는 config 플래그/확률/팀 밸런스 정책을 보고 결정합니다.

쉽게 말하면:
- **“기본 거래안을 살짝 변형해 다양성을 늘리는 단계”**입니다.

---

## `skeleton_builders_compat.py` (기존 archetype 호환 빌더)

핵심 포인트:
- 기존 시스템 archetype과 1:1로 대응되는 안전한 기본 빌더 모음입니다.
- BUY 쪽:
  - `build_buy_picks_only`
  - `build_buy_young_plus_pick`
  - `build_buy_p4p_salary`
  - `build_buy_consolidate_2_for_1`
- SELL 쪽:
  - `build_sell_buyer_picks`
  - `build_sell_buyer_young_plus_pick`
  - `build_sell_buyer_p4p`
  - `build_sell_buyer_consolidate`

쉽게 말하면:
- **“레거시에서 검증된 정석 패키지 세트”**입니다.

---

## `skeleton_builders_player_swap.py` (선수 교환 중심 빌더)

핵심 포인트:
- 선수-선수 스왑류를 집중적으로 생성합니다.
- 예시:
  - `role_swap_small_delta`
  - `fit_swap_2_for_2`
  - `starter_for_two_rotation`
  - `one_for_two_depth`
  - `three_for_one_upgrade`
  - `bench_bundle_for_role`
  - `change_of_scenery_young`
  - `star_lateral_plus_delta`
- `_focal`, `_base_deal`, `_cand` 같은 공통 유틸로 중복 코드를 줄입니다.

쉽게 말하면:
- **“선수 패키지 교환 시나리오 전문 제작소”**입니다.

---

## `skeleton_builders_timeline.py` (팀 방향성/타임라인 빌더)

핵심 포인트:
- 팀 타임라인(리빌드/경쟁 창)에 맞춘 구조를 만듭니다.
- 예시:
  - `veteran_for_young`
  - `veteran_for_young_plus_protected_first`
  - `bluechip_plus_first_plus_swap`

쉽게 말하면:
- **“지금 성적보다 2~3년 후를 보는 거래 구조”**를 만드는 파일입니다.

---

## `skeleton_builders_salary_cleanup.py` (급여 정리 빌더)

핵심 포인트:
- 샐러리캡 정리 목적 거래안을 만듭니다.
- 예시:
  - `rental_expiring_plus_second`
  - `pure_absorb_for_asset`
  - `partial_dump_for_expiring`
  - `bad_money_swap`
- 만기 계약(expiring), 흡수(absorb), 악성 계약 교환 등을 다룹니다.

쉽게 말하면:
- **“경기력보다 급여 구조 개선을 위한 트레이드 템플릿”**입니다.

---

## `skeleton_builders_pick_engineering.py` (픽 공학 빌더)

핵심 포인트:
- 픽 조합 자체를 재구성하는 빌더입니다.
- 예시:
  - `first_split`
  - `second_ladder_to_protected_first`
  - `swap_purchase`
  - `swap_substitute_for_first`

쉽게 말하면:
- **“선수보다 픽 구조를 정교하게 만지는 템플릿”**입니다.

---

## 3) 실제 실행 흐름 (아주 간단 버전)

1. `skeletons.py`에서 대상 자산 tier를 계산한다.  
2. `skeleton_registry.py`에서 mode+tier에 맞는 스켈레톤 목록을 받는다.  
3. 각 스켈레톤의 builder 함수를 실행한다.  
4. `skeleton_modifiers.py`로 보호픽/스왑 변형을 추가한다.  
5. shape 제한을 통과한 후보만 남긴다.

---

## 4) 현재 발생 중인 target tier 불일치 원인 (정확한 위치)

아래 두 지점에서 불일치가 핵심적으로 발생합니다.

### (A) tier 체계 불일치 (8-tier vs 5-tier)

- 실제 분류기(`classify_target_tier`)는 `classify_target_profile()` 결과에서 8단계 tier를 반환합니다.  
  (예: `ALL_NBA`, `ALL_STAR`, `ROTATION`, `GARBEGE` 등)
- 그런데 스켈레톤 레지스트리의 라우팅/허용 tier는 사실상 5단계(`ROLE`, `STARTER`, `HIGH_STARTER`, `STAR`, `PICK_ONLY`) 기준입니다.

즉, 분류 결과가 `ALL_STAR` 같은 값이면, 레지스트리에서 매칭되는 스켈레톤이 없어질 수 있습니다.

### (B) SELL의 `pick_bridge`가 `PICK_ONLY`로 연결되지 않음

- `classify_target_profile()` 내부에서 `match_tag`를 받아도 바로 버리고(`_ = match_tag`) 분기 로직에 쓰지 않습니다.
- 그래서 SELL에서 `match_tag="pick_bridge"`여도 `PICK_ONLY`가 아니라 일반 skill tier(예: `STARTER`)가 나옵니다.

결과적으로 “pick-only용 라우트”를 강제하고 싶은 의도와 실제 라우팅 tier가 어긋납니다.

---

## 5) 재현 요약

로컬에서 확인한 결과:
- `sale_asset + match_tag='pick_bridge'` 분류 결과: `STARTER` (기대: `PICK_ONLY`)
- BUY 타깃 `market_total` 샘플에서 `GARBEGE`, `ROTATION`, `ALL_STAR`, `ALL_NBA` 등이 나오는 경우 레지스트리 매칭 개수 0건 발생

즉 현재는 **분류기는 더 세분화(8-tier)** 되어 있고, **스켈레톤 선택기는 축약 체계(5-tier)** 에 기대고 있어 경계 구간에서 누락이 생길 수 있습니다.
