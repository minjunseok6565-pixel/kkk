# 트레이드 v2 단일 SSOT 승격 사전점검 보고서

작성일: 2026-03-10  
기준 브랜치: `work`

---

## 0) 이번 작업 범위

요청한 2개 작업 수행 결과:

1. **P0 완료**: `MarketPricer._price_pick()`의 distribution 입력 타입 정합성 보완
   - dataclass(`PickDistributionBundle`) / dict 입력 모두 동작하도록 접근 경로 통일
2. **P1 완료**: `package_effects`에서 context_v2 산출 재사용 경로 추가
   - role/contract texture를 context_v2에서 주입받아 우선 사용
   - 누락분만 fallback 재계산

---

## 1) 변경 사항 요약

### 1-1. P0 (pick distribution 타입 정합성)

- `market_pricing.py`에서 distribution 필드 접근을 direct attribute 대신 공용 accessor(`_pick_distribution_get`) 기반으로 통일.
- swap 경로에서도 `ev_pick` 접근을 동일 방식으로 통일.
- `meta.uses_distribution` 플래그를 명시해 테스트 기대값과 설명 메타 일관성 확보.

### 1-2. P1 (context_v2 산출 재사용)

- `PackageEffects.apply(...)`에 `valuation_context_v2` 입력 추가.
- `role_textures`, `contract_textures`를 context_v2에서 player 기준으로 prefetch하는 경로 추가.
- 다음 연산에서 prefetch 결과를 우선 사용:
  - diminishing returns(역할 질감 중복)
  - roster waste / depth / hole penalty의 texture 축 계산
  - CAP_FLEX의 contract texture 집계
- `deal_evaluator`가 provider에 `valuation_context_v2`가 있으면 package_effects로 전달하도록 연결.
- service의 `_ContextV2ProviderAdapter`에 `valuation_context_v2` property 노출 추가.

### 1-3. 회귀 방지 테스트 추가

- `trades/valuation/test_package_effects_texture_components.py`에 아래 2개 테스트 추가:
  - context_v2 role texture가 주입되면 내부 `build_role_textures` 재호출 없이 동작
  - context_v2 contract texture가 주입되면 내부 `build_contract_textures` 재호출 없이 동작

---

## 2) 검증 결과

실행 명령:

- `PYTHONPATH=. pytest -q tests/test_issue1_dual_read_integration.py tests/test_pick_distribution_semantics.py tests/test_market_pricing_pick_distribution_path.py trades/valuation/test_package_effects_texture_components.py`

결과:

- **14 passed**
- 이전 실패 원인이던 dict distribution 입력 경로 실패(3건) 해소 확인
- context_v2 재사용 경로 테스트 통과 확인

---

## 3) 이제 v1 완전 삭제하고 v2 단일 SSOT로 승격 가능한가?

## 판정: **아직 즉시 전면 삭제는 비권장 (NO-GO)**

현재 코드 기준으로 아래 v1/레거시 잔존이 명확함:

1. `data_context.py`의 standings 기반 `build_pick_expectations_from_standings()`가 여전히 존재/사용됨
2. service 레벨에 `use_valuation_context_v2` / stage / dual-read 제어가 남아 있어 v1 경로와 공존 전제
3. `_ContextV2ProviderAdapter.get_pick_expectation()`는 distribution 미존재 시 base(v1) expectation fallback 허용
4. `package_effects`의 `cap_flex_enable_legacy_commitment_fallback=True` 기본값으로 CAP_FLEX legacy fallback이 여전히 활성 가능
5. `context_v2.collect_v1_v2_diff()` 자체가 dual-read 공존 운영을 전제

즉, **이번 작업으로 품질은 크게 개선되었지만**, 코드베이스 정책 자체는 여전히 “v1 공존 과도기” 상태다.

---

## 4) v2 단일 SSOT 승격을 위한 잔여 필수 작업 (Go-Live Checklist)

### 필수 (Blocking)

1. v1 expectation 빌더 제거
   - `build_pick_expectations_from_standings()` 제거
   - expectation 소비처를 distribution 기반 입력으로 완전 치환

2. service 플래그/스테이지/dual-read 경로 제거
   - `use_valuation_context_v2`, `valuation_context_v2_stage`, `valuation_context_v2_dual_read` 정리
   - context_v2 강제 경로 단일화

3. provider fallback 제거
   - `_ContextV2ProviderAdapter.get_pick_expectation()`의 base fallback 제거
   - 분포 미존재 시 에러/coverage 플래그 처리로 정책 명확화

4. CAP_FLEX legacy fallback 제거
   - `cap_flex_enable_legacy_commitment_fallback=False` 고정
   - cap ledger 기반만 사용

5. dual-read diff 제거
   - `collect_v1_v2_diff` 및 관련 telemetry 구조 제거/대체

### 권장 (Non-blocking but strongly recommended)

6. 전체 회귀 테스트 스위트 + 시뮬레이션 리플레이 검증
7. v2 전용 운영 메트릭(`source_coverage`, missing_input rate) 경보 임계치 재정의

---

## 5) 최종 결론

- **이번 요청(P0/P1)은 완료되었고, 관련 실패 테스트는 모두 해소되었다.**
- 그러나 현재 브랜치는 여전히 v1 공존 설계가 남아 있으므로, **지금 즉시 v1 완전 삭제/dual-read 종료/단일 SSOT 승격은 시기상조**다.
- 위 Blocking 체크리스트 완료 후에 GO 판정을 내리는 것이 안전하다.

