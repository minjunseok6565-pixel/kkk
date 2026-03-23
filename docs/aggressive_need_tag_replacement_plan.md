# Need Tag 시스템 공격적 전면 교체 실행 계획

## 목표
- 기존 need tag 체계를 **점진 이행 없이 즉시 폐기**하고, 신규 태그/신규 산출 로직으로 한 번에 교체한다.
- 교체 직후에는 튜닝(가중치/임계값)으로 밸런스를 맞춘다.
- “니즈 생성”과 “니즈 충족 평가”의 기준을 분리한다.
  - 니즈 생성: 역할 점수 + 사용량/커버리지 + 포지션 부족 보정
  - 니즈 충족: role이 아니라 attrs 기반 수식

---

## 비목표(이번 교체 범위 밖)
- 구 태그와의 하위호환 유지
- 기존 role_to_need_tag, ROLE_GAP, 구 need_map 키와의 동시 운영
- 구 방식 fallback

---

## 대원칙
1. **구 태그/구 로직 완전 삭제**: 남겨두지 않는다.
2. **SSOT 재정의**: 태그 정의, 태그별 attrs 프로파일, 태그 지원 집합을 신규 기준으로 단일화한다.
3. **일관된 스케일**: need 강도와 player 공급량은 모두 0~1 정규화로 맞춘다.
4. **결정적(Deterministic) 계산**: 동일 입력이면 동일 결과가 나오게 구성한다.
5. **attrs 입력 계약 고정**: 입력 attrs는 **0~99만 허용**한다(0~1 입력은 비허용).

---

## 태그 체계 개정안 (G/W/B 접두어 조건부 부여)

핵심 정책:
- 태그는 기본적으로 역할축만 가진다: `OFF_<ROLE_FAMILY>`, `DEF_<ROLE_FAMILY>`.
- `G/W/B` 접두어는 **항상 붙지 않는다.**
- `G/W/B` 접두어는 포지션 부족 판정이 참일 때만 붙는다.

포지션 부족 판정(두 조건 동시 충족):
1. **상대 비율 부족**: 팀 내 G/W/B 상대 비율이 리그/기준치 대비 충분히 낮음
2. **절대 인원 부족**: 해당 포지션 인원 수가 `<= 3`

최종 태그 생성 규칙:
- 포지션 부족 판정이 False이면: `OFF_*`, `DEF_*`
- 포지션 부족 판정이 True이면: `G_OFF_*`, `W_DEF_*`, `B_OFF_*` 형태

---

## 최종 need_map 태그 전체 목록

### A. 기본(비접두) 공격 태그
- `OFF_ENGINE_PRIMARY`
- `OFF_ENGINE_SECONDARY`
- `OFF_TRANSITION_ENGINE`
- `OFF_SHOT_CREATOR`
- `OFF_RIM_PRESSURE`
- `OFF_SPOTUP_SPACER`
- `OFF_MOVEMENT_SHOOTER`
- `OFF_CUTTER_FINISHER`
- `OFF_CONNECTOR`
- `OFF_ROLL_MAN`
- `OFF_SHORTROLL_HUB`
- `OFF_POP_THREAT`
- `OFF_POST_ANCHOR`

### B. 기본(비접두) 수비 태그
- `DEF_ZONE_TOP_LEFT`
- `DEF_ZONE_TOP_RIGHT`
- `DEF_PNR_POA_DEFENDER`
- `DEF_PNR_POA_BLITZ`
- `DEF_PNR_POA_SWITCH`
- `DEF_PNR_POA_SWITCH_1_4`
- `DEF_PNR_POA_AT_THE_LEVEL`
- `DEF_LOWMAN_HELPER`
- `DEF_NAIL_HELPER`
- `DEF_WEAKSIDE_ROTATOR`
- `DEF_SWITCH_WING_STRONG`
- `DEF_SWITCH_WING_WEAK`
- `DEF_SWITCH_WING_STRONG_1_4`
- `DEF_SWITCH_WING_WEAK_1_4`
- `DEF_ZONE_BOTTOM_LEFT`
- `DEF_ZONE_BOTTOM_RIGHT`
- `DEF_ZONE_BOTTOM_CENTER`
- `DEF_PNR_COVER_BIG_DROP`
- `DEF_PNR_COVER_BIG_BLITZ`
- `DEF_BACKLINE_ANCHOR`
- `DEF_PNR_COVER_BIG_SWITCH`
- `DEF_PNR_COVER_BIG_SWITCH_1_4`
- `DEF_PNR_COVER_BIG_HEDGE_RECOVER`
- `DEF_PNR_COVER_BIG_AT_THE_LEVEL`

### C. 포지션 부족 시에만 활성화되는 접두 태그
- `G_` + (A/B 태그 중 가드 매핑 role)
- `W_` + (A/B 태그 중 윙 매핑 role)
- `B_` + (A/B 태그 중 빅 매핑 role)

즉, 접두 태그는 동적 집합이며 “해당 포지션 부족 판정 시에만” need_map 후보에 들어간다.

---

## 작업 항목 (Aggressive Replace)

### 1) team_situation 전면 교체
대상: `data/team_situation.py`

#### 1-1. 삭제할 것
- 기존 role-fit 기반 need 생성 함수/분기
- 기존 style 기반 need 생성 함수/분기
- 기존 roster gap 기반 need 생성 함수/분기
- CAP_FLEX 등 구 need 트리거
- 구 need merge 로직에서 구 태그를 가정하는 부분

#### 1-2. 신규로 넣을 것
- 공격 role 점수 산출:
  - 선수별 상위 3개 공격 role + 점수
  - role별 가중 평균 점수 산출
- 수비 role 점수 산출:
  - matchengine_v3 수비 role 파이프라인(스킴 role 프로파일 + role 점수)을 활용
  - 선수별 상위 3개 수비 role + 점수(혹은 팀 수비 role 점수 집계)
- 후보 need 선정 규칙:
  1. role 점수가 낮은 항목
  2. role 점수가 높아도 커버 선수 수가 적은 항목
- 사용량-커버리지 보정(세부는 하단 “사용량 측정 규격” 참조)
- 포지션 밸런스 보정:
  - 상대 비율 + 절대 인원(`<=3`)을 동시에 만족하는 포지션만 접두 태그 활성화
- 최종 need_map 산출:
  - 신규 태그만 생성
  - weight 0~1 클램프
  - 상위 N개만 유지(운영 파라미터)

---

## 사용량 측정 규격 (구체화 #1)

역할별 사용량은 아래 순서로 산정한다.

1) **기본 원천: 팀 시즌 breakdown action share**
- 공격: `offense_breakdowns.off_actions`의 액션 비중을 정규화해서 role-family로 매핑
- 수비: 팀의 수비 스킴 사용 비중 + outcome/group weights를 role로 분해

2) **단기-장기 혼합**
- 최근 10경기 사용량(단기)과 시즌 누적 사용량(장기) 혼합
- `usage = 0.6 * season + 0.4 * recent10`

3) **초반 시즌 신뢰도 감쇠**
- 시즌 진행률이 낮으면 usage를 중립치로 수축
- `usage_adj = neutral + (usage - neutral) * stat_trust`

4) **role별 최종 사용량 스케일**
- 모든 role 사용량은 팀 내 합 1.0으로 재정규화
- role별 `usage_role in [0,1]`

사용량-커버리지 보정식(권장):
- `coverage_role`: 해당 role top3 후보 선수 수/품질을 반영한 0~1
- `delta = usage_role - coverage_role`
- `bonus = +k * delta` if `delta > th`
- `penalty = -k2 * (-delta)` if `delta < -th2`

---

## role_need_tags 폐기 및 need_attr_profiles 신설
대상:
- 삭제: `role_need_tags.py`
- 신설: `need_attr_profiles.py`

### 신규 모듈 책임
- 태그 정의 상수 (신규 태그 전체 목록)
- 태그별 attrs 계산식(가중치 기반) 정의
- 태그별 설명/라벨(선택)
- `tag_supply(player_attrs) -> {tag: score}` API 제공

### attrs 입력 계약
- 입력 attrs 허용 범위: **0~99만 허용**
- 범위 외 값 또는 0~1 추정값 입력 시:
  - strict 모드: 예외 처리
  - non-strict 모드: 로그 후 클램프/중립 대체
- 내부 점수는 0~1로 정규화하여 출력

---

## fit_engine 전면 개편
대상: `trades/valuation/fit_engine.py`

### 삭제할 것
- role_to_need_tag 기반 공급 벡터 구성
- 일부 태그만 attrs로 보강하는 임시 휴리스틱
- ROLE_TO_NEED_TAG 기반 FIT_SUPPORTED_TAGS_BASE 구성

### 신규로 넣을 것
- `need_attr_profiles.py` 기반 단일 공급 산출 경로
- `FIT_SUPPORTED_TAGS_BASE = ALL_NEW_NEED_TAGS` (접두 태그는 runtime 활성 집합)
- need_map(신규 태그)과 공급벡터(신규 태그)의 점곱 기반 fit 계산
- explainability에 태그별 기여도/미충족/초과충족 지표 추가

---

## package_effects 전면 개편
대상: `trades/valuation/package_effects.py`

### 삭제할 것
- `_primary_archetype_tag()` 단일 대표태그 중심 버킷팅 감점
- 구 need 태그(CAP_FLEX/OFFENSE_UPGRADE/DEFENSE_UPGRADE/DEPTH 등) 전제 로직

### 신규로 넣을 것
- 패키지 단위 태그 공급량 계산:
  - 각 선수 `tag_supply`를 합산해 패키지 총 공급벡터 생성
- need 강도 대비 초과공급 기반 감점:
  - `excess = max(0, package_supply[tag] - team_need[tag])`
  - excess가 클수록 비선형 감점 증가
  - need 이내 공급은 감점 없음
- 중복 감점은 “태그 중복 여부”가 아니라 “초과 공급량”으로만 판단

---

## 전수 교체 대상(구체화 #3)

아래는 신규 태그 체계로 **반드시 전수 교체**해야 하는 런타임 코드 기준 목록이다.

### A. 니즈 생성/계약 계층
- `data/team_situation.py`
- `decision_context.py`

### B. valuation 핵심
- `trades/valuation/fit_engine.py`
- `trades/valuation/team_utility.py`
- `trades/valuation/package_effects.py`
- `trades/valuation/service.py`
- `trades/valuation/types.py` (FitAssessment/need_map 직렬화 영향 필드)

### C. generation 연계
- `trades/generation/generation_tick.py` (team_situation -> decision_context 전달 경로)
- `trades/generation/asset_catalog.py` (fit/misfit/need 해석 경로)
- `trades/generation/dealgen/*` (need_map 참조/가중치 반영 경로 전수)

### D. API/표시 계층
- `app/api/routes/trades.py` (need_map/설명 노출)
- need 태그를 직접 표기/번역하는 라우트/프론트 상수 파일 전수

### E. 테스트 전수
- `trades/valuation/test_*.py`
- `trades/generation/test_*.py`
- `data/team_situation` 관련 테스트
- need 태그 스냅샷/고정 문자열 의존 테스트 전부

### F. 제거/대체 파일
- 제거: `role_need_tags.py`
- 추가: `need_attr_profiles.py`
- 구 태그 하드코딩 문자열/주석/문서 중 런타임 참조 문구 전부 제거

---

## 테스트/검증 계획 (필수)

### 단위 테스트
- `need_attr_profiles`
  - 태그별 점수 범위(0~1) 보장
  - 결측 attrs 처리
  - 0~99 범위 입력 검증(범위 외 예외/처리)
- `fit_engine`
  - 신규 태그만 사용되는지
  - supported 태그 필터 정확성
- `package_effects`
  - need 이내 공급 시 감점 0
  - 초과공급량 증가에 따른 감점 단조 증가

### 통합 테스트
- team_situation -> decision_context -> fit_engine -> package_effects 파이프라인 E2E
- 대표 팀 시나리오(가드 부족/윙 부족/빅 부족/수비 취약/밸런스 팀)

### 회귀 테스트
- 구 태그 문자열이 코드 경로에 남지 않았는지 정적 검사
- 주요 API 응답 스키마 스냅샷 갱신

---

## 완료 정의 (Definition of Done)
- 구 태그/구 로직 코드가 저장소에 남아있지 않다.
- team_situation이 신규 태그 need_map만 생산한다.
- fit_engine/package_effects가 신규 attrs 기반 태그 체계만 소비한다.
- 단위/통합/회귀 테스트가 모두 통과한다.
- 튜닝 파라미터 문서화가 완료되어 운영자가 조정 가능하다.
