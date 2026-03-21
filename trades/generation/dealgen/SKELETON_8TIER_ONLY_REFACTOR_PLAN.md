# Skeleton 8-Tier Only 전환 리팩터링 계획

목표: 스켈레톤 생성/라우팅/템플릿 평가 경로에서 `contract_tag(OVERPAY/FAIR/VALUE)` 개념을 전역 삭제하고, 오직 8-tier(MVP~GARBAGE) 기준으로만 동작하게 만든다.

---

## 1) 핵심 설계 원칙

- **계약 가치 분기 로직은 폴백 없이 제거**한다.
- `classify_target_profile()`의 출력은 `tier` 단일 축으로 단순화한다.
- 스켈레톤 registry 라우팅은 `tier`만 입력으로 사용한다.
- template-first 스테이지도 `tier`만 기준으로 템플릿을 조회/점수 검증한다.
- 메타 태그/통계(`contract_tag`, `contract_tag_counts`)는 구조체부터 삭제한다.

---

## 2) 파일별 수정 계획

### A. 점수 SSOT에서 계약 보정 제거

### `trades/generation/dealgen/skeleton_score_ssot.py`

- 삭제
  - `ContractTag` 타입
  - `CONTRACT_TAG_BONUS`
  - `normalize_contract_tag()`
- 변경
  - `ScoreTarget`에서 `contract_tag` 필드 제거
  - `target_required_score(tier, contract_tag)` → `target_required_score(tier)`로 시그니처 축소
  - `build_score_target(tier, contract_tag, ...)` → `build_score_target(tier, ...)`
  - required score 계산식에서 계약 보정값(+/-1) 제거
- 정리
  - `__all__`에서 계약 태그 관련 export 삭제

영향: 점수 기준이 **순수 tier 점수**로 고정된다.

---

### B. 타겟 프로파일 분류에서 계약 태그 제거

### `trades/generation/dealgen/utils.py`

- 삭제
  - `_resolve_contract_gap_cap_share()`
  - `_classify_contract_value_tag()`
- 변경
  - `classify_target_profile()` 반환에서 `contract_tag` 제거
  - docstring/주석을 “OVR hardcut 8-tier 분류” 중심으로 수정

영향: 타겟 분류 결과는 `{ "tier": ... }`만 남는다.

---

### C. tier-score skeleton builder에서 계약 태그 제거

### `trades/generation/dealgen/skeleton_builders_tier_score_common.py`

- 변경
  - `_target_profile()` 반환 타입을 `(tier, contract_tag)` → `tier`로 단순화
  - `target_required_score(tier_u, contract_tag_u)` → `target_required_score(tier_u)`
  - `DealCandidate.contract_tag` 설정 제거
  - 태그 문자열 `contract_tag:*` 추가 로직 제거

영향: 동일 tier면 항상 동일 required score 기준으로 skeleton 패키지를 만든다.

---

### D. template-first builder에서 계약 태그 제거

### `trades/generation/dealgen/skeleton_builders_template.py`

- 변경
  - `_target_profile()` 반환 타입을 tier-only로 변경
  - `required = target_required_score(tier_u)`로 변경
  - `templates = get_templates_for_tier(tier_u)`로 변경
  - `DealCandidate.contract_tag` 설정/태깅 제거

영향: 템플릿 선택과 점수 게이트가 모두 tier-only가 된다.

---

### E. template spec API에서 계약 태그 필터 제거

### `trades/generation/dealgen/template_specs.py`

- 삭제
  - `ALL_CONTRACT_TAGS`
  - `PackageTemplate.contract_tags` 필드
  - `_normalize_tag()`
- 변경
  - `get_templates_for_tier(tier, contract_tag)` → `get_templates_for_tier(tier)`
  - 조회 결과 필터링에서 contract_tag 조건 제거
  - placeholder 정의 시 `contract_tags=...` 제거

영향: 템플릿은 tier scope만으로 조회된다.

---

### F. skeleton registry 라우팅에서 계약 오버레이 제거

### `trades/generation/dealgen/skeleton_registry.py`

- 삭제
  - `SkeletonSpec.contract_tags` 필드
  - `_route_ids_for_phase()`의 `contract_attr_map`
  - combined phase에서 `skeleton_route_contract_*`를 union하던 로직
  - `get_specs_for_mode_and_tier(..., contract_tag=...)` 파라미터 및 관련 필터
- 변경
  - 라우팅 입력은 `mode + tier + route_phase`만 사용

영향: registry 라우팅은 완전한 8-tier route 테이블로만 동작한다.

---

### G. 호출부/메타에서 contract_tag 제거

### `trades/generation/dealgen/skeletons.py`

- 변경
  - `_attach_v3_meta(..., contract_tag)` → contract_tag 인자 제거
  - `candidate.contract_tag` 설정 삭제
  - `contract_tag:*` 태그 append 삭제
  - `classify_target_profile()` 호출 후 tier만 읽도록 변경
  - `_build_candidates_for_phase()`에서 registry 호출 시 contract_tag 전달 제거

### `trades/generation/dealgen/core.py`

- 변경
  - 집계에서 `cand.contract_tag` 참조 제거
  - `stats.contract_tag_counts` 누적 제거

### `trades/generation/dealgen/types.py`

- 삭제
  - `DealGeneratorConfig.skeleton_route_contract_overpay/fair/value`
  - `DealGeneratorConfig.target_tier_contract_fair_band`
  - `DealGeneratorConfig.target_tier_contract_value_boundary`
  - `DealGenerationStats.contract_tag_counts`
  - `DealCandidate.contract_tag`
- 주석 정리
  - “contract tag overlay” 관련 섹션 삭제

영향: 타입 레벨에서 contract_tag 개념 자체가 소거된다.

---

## 3) 테스트/검증 수정 계획

### 삭제 또는 대체가 필요한 테스트

- 삭제 대상(계약 태그 기능 전용)
  - `trades/generation/dealgen/test_skeleton_contract_route_config.py`
- 시그니처/기대값 수정
  - `trades/generation/dealgen/test_skeleton_score_ssot.py`
  - `trades/generation/dealgen/test_template_builder_placeholders.py`
  - `trades/generation/dealgen/test_template_first_fallback_routing.py`
  - `trades/generation/dealgen/test_skeleton_registry_routing.py`
  - `trades/generation/dealgen/test_skeleton_phase4_config.py`
  - `trades/generation/dealgen/test_template_eval_fallback_in_core.py`

### 검증 포인트

- skeleton 생성 경로가 `tier`만으로 정상 동작하는지.
- template-only / fallback-only / combined phase에서 route 선택이 tier 기준으로 안정적인지.
- required score가 과거 대비 `OVERPAY/FAIR/VALUE`에 따라 달라지지 않고 고정되는지.
- 통계/telemetry에서 contract_tag 필드 누락으로 인한 참조 에러가 없는지.

---

## 4) 실행 순서(권장)

1. **SSOT/분류 함수부터 축소** (`skeleton_score_ssot.py`, `utils.py`)
2. **builder 계층 정리** (`skeleton_builders_tier_score_common.py`, `skeleton_builders_template.py`, `template_specs.py`)
3. **registry/호출부 연결 정리** (`skeleton_registry.py`, `skeletons.py`)
4. **types/core 정리** (`types.py`, `core.py`)
5. **테스트 정리 및 실패 케이스 제거**
6. 전체 테스트(최소 dealgen 타겟) 실행

이 순서를 따르면 시그니처 변경에 따른 연쇄 에러를 빠르게 수렴할 수 있다.

---

## 5) 완료 기준(Definition of Done)

- 코드베이스에서 `OVERPAY|FAIR|VALUE` 기반 skeleton 분기 코드가 제거됨.
- `skeleton_route_contract_*` / `contract_tag` / `contract_tag_counts` 참조가 제거됨.
- template 조회 API가 tier-only 시그니처로 통일됨.
- 주요 dealgen 테스트가 통과하고, 스켈레톤 생성이 런타임 에러 없이 동작함.

