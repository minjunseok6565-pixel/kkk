# classify_target_tier 개편 영향 브리핑

## 목적
`classify_target_tier()`가 기존 레거시 tier(ROLE/STAR/PICK_ONLY)에서
신규 8-tier(+contract tag 분리) 구조로 바뀌면서 영향을 받는 파일과
직접 개편이 필요한 파일을 정리한다.

---

## 1) 개편 영향 파일(현재 호출/의존)

### 직접 호출
- `trades/generation/dealgen/skeletons.py`
  - BUY/SELL 경로에서 `classify_target_tier(...)` 결과를 스켈레톤 라우팅 키로 사용.

### 라우팅 규칙 의존
- `trades/generation/dealgen/skeleton_registry.py`
  - `route_attr_map`, `ALL_TARGET_TIERS`, `spec.target_tiers`가 구 tier 중심.

### 설정/타입 의존
- `trades/generation/dealgen/types.py`
  - `skeleton_route_role/starter/high_starter/pick_only` 중심 설정 구조.
  - 신규 8-tier + 계약태그 기반 라우팅 키 미정의.

### 관측/통계
- `trades/generation/dealgen/core.py`
  - `target_tier_counts` 집계는 문자열 기반이라 동작은 가능하나,
    신규 체계 기준 대시보드/해석 업데이트 필요.

### 테스트
- `trades/generation/dealgen/test_skeleton_phase4_config.py`
  - 아직 `ROLE`, `PICK_ONLY` 기대값을 검증.
- `trades/generation/dealgen/test_skeleton_registry_routing.py`
  - 레거시 tier 라우팅 가정 검증.
- 그 외 `target_tier` 문자열을 단정하는 테스트들.

---

## 2) 직접 개편 필요 파일(우선순위)

1. `trades/generation/dealgen/skeletons.py`
   - `classify_target_tier` 대신 `classify_target_profile`을 사용해
     `tier + contract_tag`를 함께 전달하도록 수정.
2. `trades/generation/dealgen/skeleton_registry.py`
   - `ALL_TARGET_TIERS` 및 `route_attr_map`를 신규 8-tier 체계로 재정의.
   - `PICK_ONLY` 제거 및 asset-kind 기반 분기 이관.
3. `trades/generation/dealgen/types.py`
   - route 설정 필드를 8-tier(+필요 시 contract_tag 분기) 기준으로 재설계.
4. 관련 테스트 파일
   - 레거시 기대값(`ROLE`, `PICK_ONLY`) 제거.
   - 신규 tier와 contract tag 전달/라우팅 동작을 검증하도록 수정.

---

## 3) "호출부가 태그를 못 받는 문제"의 원인

현재 구조는 아래와 같음.
- `classify_target_profile(...)`는 `tier`, `contract_tag`를 모두 반환.
- `classify_target_tier(...)`는 호환용 래퍼로 `tier` 문자열만 반환.

따라서 문제는 두 층위의 조합이다.
1. **인터페이스 설계 측면**: `classify_target_tier` 시그니처가 tier-only 반환.
2. **호출부 전환 미완료**: 주요 호출부(`skeletons.py`)가 아직 profile API를 사용하지 않음.

즉, 함수 자체와 호출부 모두 영향이 있으며,
실제 해결은 호출부를 `classify_target_profile`로 전환하는 것이 핵심이다.

---

## 4) 분포 기반 percentile + EMA 반영 현황

- 분포 기반 percentile:
  - 우선순위로 explicit percentile 필드(`league_percentile` 등)를 사용.
  - 필드가 없으면 `basketball_total -> sigmoid` 프록시로 폴백.
- EMA:
  - `q_b_ema/skill_percentile_ema/tier_percentile_ema`를 읽어
    `alpha`(기본 0.35)로 EMA를 적용.
  - 이전 EMA가 없으면 현재 score 사용.

> 주의: EMA "저장"은 이 유틸 파일 범위를 벗어난다.
> 본 파일은 읽기/계산만 수행한다.

---

## 5) 후속 작업 체크리스트

- [ ] skeletons.py에서 `classify_target_profile` 사용
- [ ] candidate/meta에 `contract_tag` 전파
- [ ] registry/types 라우팅 구조 8-tier 기준 재편
- [ ] PICK_ONLY 완전 제거 (pick/swap은 asset-kind 라우팅으로 이관)
- [ ] 테스트 전면 갱신
