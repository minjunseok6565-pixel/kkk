# Aggressive Need Tag 전면 교체 - 파일 단위 구체 작업 계획

본 문서는 아래 두 문서를 실행 가능한 코드 작업 단위로 분해한 파일별 계획서이다.
- `docs/aggressive_need_tag_replacement_plan.md`
- `docs/aggressive_need_tag_replacement_briefing.md`

핵심 전제:
- 점진 마이그레이션 없이 **전면 교체**
- 구 태그/구 로직 **완전 삭제**
- 테스트 파일은 **이번 작업에서 수정하지 않음**

---

## 0) 신규/삭제 파일

### 삭제
- `role_need_tags.py`
  - 파일 자체 삭제
  - import/참조 전수 제거

### 신규
- `need_attr_profiles.py`
  - 신규 태그 상수 집합 정의
  - 태그별 attrs 프로파일(가중치)은 **플레이스홀더**로 선언
  - `tag_supply(player_attrs, *, strict=True) -> dict[tag, score]` 제공
  - 입력 attrs 0~99 검증 및 내부 0~1 정규화

---

## 1) 전수 교체 대상 파일 재검증

아래 파일들을 신규 태그 체계 기준으로 전수 교체한다.

### A. 니즈 생성/컨텍스트
1. `data/team_situation.py`
2. `decision_context.py`

### B. valuation 핵심
3. `trades/valuation/fit_engine.py`
4. `trades/valuation/package_effects.py`
5. `trades/valuation/team_utility.py`
6. `trades/valuation/service.py`
7. `trades/valuation/types.py`

### C. generation
8. `trades/generation/generation_tick.py`
9. `trades/generation/asset_catalog.py`
10. `trades/generation/dealgen/*` (need_map 소비 경로)

### D. API/표시
11. `app/api/routes/trades.py`
12. `static/js/**` 중 need tag 문자열/라벨 소비 파일

### E. 문서/런타임 참조 정리
13. 구 태그를 런타임 참조하는 문서/주석(필요 범위) 정리

> 참고: 테스트 파일은 수정하지 않는다(이번 작업 범위 제외).

---

## 2) 파일별 구체 수정 계획

## 2.1 `need_attr_profiles.py` (신규)

### 추가할 블록
1. 신규 태그 정의 블록
   - 기본 태그: `OFF_*`, `DEF_*`
   - 조건부 접두 태그 생성기: `G_/W_/B_`
2. attrs 검증/정규화 유틸
   - `validate_attrs_0_99(attrs)`
   - `norm_99(v) -> 0..1`
3. 태그별 프로파일 딕셔너리
   - **플레이스홀더 가중치**로 선언
   - 예: `TAG_ATTR_WEIGHTS = {"OFF_ENGINE_PRIMARY": {"Ball Handle": 0.0, ...}}`
4. 공급 계산 API
   - `score_tag(tag, attrs, *, strict=True)`
   - `tag_supply(attrs, *, strict=True, active_tags=None)`

### 대체/삭제 없음
- 신규 파일 생성이므로 기존 블록 대체 없음

---

## 2.2 `role_need_tags.py` (삭제)

### 삭제할 내용
- 파일 전체 삭제
- 역할->구 태그 변환 함수 전부 제거

### 후속 반영
- `from role_need_tags import ...` import 전수 제거
- 호출부를 `need_attr_profiles` 또는 신규 태그 상수로 대체

---

## 2.3 `data/team_situation.py` (전면 개편)

### 삭제할 블록
1. 기존 role-fit 기반 need 생성 블록
2. style 기반 need 생성 블록
3. roster gap 기반 need 생성 블록
4. 구 태그 boost/merge에서 구 태그 상수 전제하는 블록
5. CAP_FLEX, OFFENSE_UPGRADE, DEFENSE_UPGRADE 등 구 태그 생성 블록

### 추가/대체 블록
1. 공격 role 점수 집계 블록
   - 선수별 상위3 공격 role 산출
   - role별 평균/가중 평균 점수
2. 수비 role 점수 집계 블록
   - matchengine_v3 수비 role 파이프라인 연동(스킴 role 점수)
3. 사용량 측정 블록
   - 공격: action share -> role 매핑
   - 수비: scheme share × role weights
   - season/recent10 혼합 + 초기 신뢰도 감쇠
4. 사용량-커버리지 보정 블록
   - `delta = usage - coverage` 기반 가감점
5. 포지션 부족 판정 블록
   - 상대 비율 부족 + 절대 인원 `<=3` 동시 충족
   - 충족 시에만 `G_/W_/B_` 접두 태그 활성화
6. 최종 need_map 생성 블록
   - 신규 태그만 사용
   - 0~1 클램프 + 상위 N개

### 주의
- 기존 구 태그 문자열이 남지 않도록 정적 점검

---

## 2.4 `decision_context.py` (계약 교체)

### 대체할 블록
1. need_map 파싱/정규화 블록
   - 구 태그 집합 가정 제거
   - 신규 태그 집합 기준으로 정규화
2. 니즈 관련 파생 지표 계산 블록
   - 구 태그 의존 계산식 제거
   - 태그 중립 방식(가중치 집계형)으로 변경

### 확인 항목
- 직렬화 payload에서 신규 태그만 출력되는지

---

## 2.5 `trades/valuation/fit_engine.py` (전면 개편)

### 삭제할 블록
1. `role_to_need_tag` 기반 공급 벡터 생성 전체
2. role_fit 우선 + 일부 attrs 보강 휴리스틱 경로
3. 구 태그 기반 `FIT_SUPPORTED_TAGS_BASE` 구성

### 추가/대체 블록
1. `need_attr_profiles` import 및 상수 사용
2. 공급 산출 단일 경로
   - `supply = tag_supply(player.attrs, strict=...)`
3. 지원 태그 집합
   - 기본: `ALL_NEW_NEED_TAGS`
   - 동적: 컨텍스트 활성 접두 태그 추가
4. fit scoring
   - `need_map` × `supply` 점곱 유지
   - explainability에 미충족/초과충족 필드 추가

### 호환성 처리
- role_fit 메타를 읽는 경로는 제거(완전 교체 전제)

---

## 2.6 `trades/valuation/package_effects.py` (전면 개편)

### 삭제할 블록
1. `_primary_archetype_tag()` 기반 단일 버킷 중복 감점 블록
2. 구 need 태그(CAP_FLEX/UPGRADE/DEPTH) 의존 보정 블록

### 추가/대체 블록
1. 패키지 공급벡터 계산 블록
   - incoming 선수별 `tag_supply` 합산
2. need 대비 충족/초과 계산 블록
   - `fulfilled[tag] = min(need, supply)`
   - `excess[tag] = max(0, supply-need)`
3. 초과공급 비선형 감점 블록
   - 태그별 excess를 합산해 penalty 산출
4. (선택) 슬롯 효율 보정 블록
   - 같은 충족량을 더 적은 선수로 달성 시 가점
   - 단, depth stress 시 가점 약화/상한 적용

### 결과 메타
- 태그별 need/supply/fulfilled/excess 상세 로그 남김

---

## 2.7 `trades/valuation/team_utility.py`

### 대체할 블록
- fit 결과 소비/스텝 라벨링에서 구 태그 전제 제거
- 신규 태그 이름/설명 표시로 교체

---

## 2.8 `trades/valuation/service.py`

### 대체할 블록
- valuation 서비스가 전달/집계하는 need_map 관련 블록
- 구 태그 해석 분기 제거, 신규 태그 그대로 전달

---

## 2.9 `trades/valuation/types.py`

### 대체할 블록
- 타입 주석/설명에서 구 태그 예시 제거
- need_map/fit breakdown 구조체 설명을 신규 태그 기준으로 교체

---

## 2.10 `trades/generation/generation_tick.py`

### 대체할 블록
- team_situation -> decision_context 전달 경로에서 구 태그 후처리 제거
- 신규 need_map 직통 전달

---

## 2.11 `trades/generation/asset_catalog.py`

### 대체할 블록
- need_map 기반 expendable/surplus 해석 블록에서 구 태그 상수 제거
- 신규 태그 일반화 집계로 교체

---

## 2.12 `trades/generation/dealgen/*`

### 대체할 블록
- need_map 참조/가중치 처리에서 구 태그 문자열 분기 제거
- 신규 태그 공통 처리기로 통일

---

## 2.13 `app/api/routes/trades.py`

### 대체할 블록
- 응답 JSON/설명 문자열에서 구 태그 의존 제거
- 신규 태그 노출 및 (필요 시) 라벨 변환 테이블 적용

---

## 2.14 `static/js/**` (필요 시)

### 대체할 블록
- 프론트 상수/라벨 매핑에서 구 태그 문자열 제거
- 신규 태그 렌더링 규칙 적용

---

## 3) 추가로 필요한 작업(별도 섹션)

아래는 요청사항 외에 실작업에서 누락되기 쉬운 필수 항목이다.

1. **태그 라벨/설명 사전**
   - 백엔드/프론트 공통으로 신규 태그 라벨 매핑 필요
2. **정적 점검 스크립트**
   - 구 태그 문자열 잔존 여부 점검(런타임 파일 대상)
3. **운영 튜닝 포인트 외부화**
   - 사용량-커버리지 보정 계수, 초과공급 패널티 계수, 슬롯효율 가점 상한을 설정화
4. **로그/디버그 표준화**
   - need/supply/fulfilled/excess를 동일 키 구조로 남겨 튜닝 가능성 확보

---

## 4) 구현 순서(권장)

1. `need_attr_profiles.py` 추가
2. `role_need_tags.py` 삭제 + import 정리
3. `fit_engine.py` 전면 교체
4. `package_effects.py` 전면 교체
5. `team_situation.py` 전면 교체
6. `decision_context.py` 계약 정리
7. generation/API 소비부 전수 교체
8. 정적 잔존 점검(구 태그 문자열)

---

## 5) 완료 기준
- `role_need_tags.py`가 저장소에서 제거됨
- 런타임 코드에서 구 태그 생성/소비가 사라짐
- need_map은 신규 태그만 생성/전달/소비됨
- `fit_engine.py`, `package_effects.py`는 attrs 기반 신규 체계로 동작
- 테스트 파일은 변경하지 않은 상태로 문서 계획 반영 완료
