# Home/Medical 탭 성능 병목 완화 실행 계획 (검토안)

## 1) 문제 요약

최근 경기 진행이 누적된 뒤 Home/Medical 등 탭 진입이 느려지는 현상은, 기존 deepcopy 축소 이후에도 **서버 단 조회 경로에서 중복 계산/중복 조회가 남아있기 때문**으로 보입니다.

핵심 병목은 아래 두 축입니다.

1. **Home 대시보드 내부의 의료 3개 API 직렬 + 중복 계산**
   - `api_home_dashboard()`가 `overview → alerts → risk-calendar`를 직렬 호출
   - 내부에서 `_medical_team_overview_payload()`가 다회 재실행
   - schedule 조회도 재호출
2. **Home 대시보드에서 과도한 team_detail 계산**
   - Home에서 요약값만 필요하지만 `get_team_detail()`은 로스터/선수 컨디션/스탯까지 폭넓게 계산

---

## 2) 현재 의심 병목 상세

### A. 의료 3개 API 중복 계산

- `alerts`는 `overview_payload`를 now/prev 2회 호출
- `risk-calendar`도 `overview_payload`를 1회 호출
- `home/dashboard`도 별도 `overview`를 1회 호출

즉 Home 1회 진입에서 의료 관련 고비용 계산이 반복됩니다.

### B. schedule 중복 재조회

- Home에서 schedule 1회 조회 후,
- `alerts`, `risk-calendar` 내부에서도 schedule를 다시 조회합니다.

### C. Home에서 `get_team_detail()` 사용 범위 과다

- 실제 Home 사용 필드: 승/패/승률, payroll, cap_space
- 실제 계산 범위: 팀 detail + 로스터 + 선수별 condition/stats

---

## 3) 목표

1. Home 진입 p95 latency를 체감 가능한 수준으로 절감
2. 경기 누적 후에도 Home/Medical 탭 진입 시간의 증가 폭 완화
3. 응답 스키마 호환성 유지 (프론트 수정 최소화)

---

## 4) 수정 설계 (파일 단위)

## 4-1. `app/api/routes/core.py`

### (1) Home 전용 의료 집계 오케스트레이션 함수 추가

- 신규 내부 함수(예시)
  - `_build_home_medical_bundle(team_id, season_year, as_of_date, days)`
- 역할
  - `_medical_team_overview_payload()`를 **한 번만** 계산해 공통 기반 데이터 확보
  - `alerts`용 KPI delta(7d)를 위해 필요한 이전 시점 계산은 최소화
  - schedule는 `_get_team_schedule_view()` 결과를 전달받아 재사용

> 핵심: route-to-route 호출(`await api_medical_team_alerts(...)`) 대신
> **공통 helper를 직접 호출하는 구조**로 변경.

### (2) `api_home_dashboard()`의 직렬 호출 제거

- 기존: overview/alerts/calendar를 순차 await
- 변경 방향:
  - schedule 1회 계산
  - 의료 계산은 **중복 없이 구성**
  - 독립 가능한 연산은 `asyncio.gather` 병렬화

> 참고: SQLite 특성상 무분별 병렬화는 잠금/경합 리스크가 있으므로,
> “중복 제거 우선, 병렬화는 읽기 전용 범위 내 제한적 적용” 권장.

### (3) `api_medical_team_alerts()`, `api_medical_team_risk_calendar()` 시그니처 확장(내부용)

- 외부 API 호환성은 유지
- 내부 호출에서 아래를 선택 주입 가능하게 함
  - precomputed overview
  - precomputed schedule
- 없으면 기존처럼 자체 계산 (fallback)

### (4) Home activity feed 필드 정합성 보완

- `recent_injury_events`에서 `player_name` 대신 `name` 키를 쓰는 데이터 경로가 있어 표시 누락 가능성 점검
- 문구 생성 시 `e.get("player_name") or e.get("name")` fallback 적용

---

## 4-2. `team_utils.py`

### (1) Home 전용 lightweight summary accessor 추가

- 신규 함수(예시)
  - `get_team_summary_light(team_id: str) -> Dict[str, Any]`
- 반환 최소 필드
  - wins/losses/win_pct/payroll/cap_space/conference/division/rank(optional)
- 의도
  - `get_team_detail()`의 heavy path(로스터/선수별 condition/stat 집계) 회피

### (2) `api_home_dashboard()`에서 `get_team_detail()` 대체

- Home 필요한 최소 summary만 사용
- 기존 응답 스키마 유지 (필드 위치 동일)

---

## 4-3. (선택) `docs/` 하위 문서 업데이트

- `docs/main_home_data_plan_ko.md`에
  - “Home은 team_detail full 대신 summary_light 사용”
  - “medical bundle 중복 제거” 설계 원칙 반영

---

## 5) 예상 오류/부작용 및 대응책

## 5-1. 데이터 신선도 불일치

### 리스크
- overview/schedule를 재사용하면, 서로 다른 함수가 기존처럼 "각자 지금 시점" 조회하던 방식과 미세하게 달라질 수 있음

### 대응
- Home 요청 단위에서 `as_of_date`/`current_date`를 단일 기준으로 고정
- 번들 내 공통 기준값을 명시적으로 전달

---

## 5-2. 스키마 호환성 깨짐

### 리스크
- helper 분리 과정에서 응답 필드 누락/키명 변경

### 대응
- 기존 API 응답 shape를 golden 샘플로 고정 비교
- key set diff 테스트 추가 (home, medical 3개)
- optional 필드는 기본값(`[]`, `{}`, `None`) 강제

---

## 5-3. SQLite 읽기 경합/잠금 이슈

### 리스크
- 병렬화 과도 시 DB connection 다중 오픈으로 지연 또는 lock 대기 악화

### 대응
- 1차는 병렬화보다 **중복 제거** 중심
- 병렬화는 CPU 후처리(정렬/매핑) 위주 적용
- DB read는 단일 transaction 묶음 우선

---

## 5-4. 계산 캐시 도입 시 오염/stale 데이터

### 리스크
- 캐시 키가 부정확하면 경기 진행 후 이전 값 반환 가능

### 대응
- 캐시 도입 시 키에 최소 포함
  - `team_id`, `current_date`, `season_year`, `state revision`(있다면)
- TTL 짧게(예: 1~3초) + write path에서 invalidate 훅

---

## 5-5. feed 표기 결함

### 리스크
- injury event에서 player name 키 불일치로 “Unknown” 증가

### 대응
- fallback 키 지원 (`player_name`/`name`)
- 누락률 메트릭 로깅

---

## 6) 단계별 실행안

### Phase 1 (저위험, 고효율)
1. `api_home_dashboard`에서 의료 3개 route 호출 제거, 내부 helper 조합으로 전환
2. schedule 1회 조회 재사용
3. team_detail -> summary_light 대체

**기대효과:** Home 탭 응답시간 가장 크게 개선

### Phase 2 (안정화)
1. 의료 API 내부에 precomputed 인자(fallback 포함) 도입
2. key-set 호환 테스트/부하 로그 추가

**기대효과:** 회귀 위험 감소, 구조 재사용성 향상

### Phase 3 (선택)
1. 짧은 TTL 캐시/요청 단위 memoization 검토
2. p95 기반 튜닝

**기대효과:** 경기 누적 시 성능 저하 완화 폭 추가 확보

---

## 7) 검증 계획

1. 기능 회귀
   - Home 응답의 필수 필드 존재/타입 검증
   - Medical 화면 렌더링 정상 확인
2. 성능 비교
   - 변경 전/후 Home API 30회 연속 호출 p50/p95 측정
   - 경기 N회(예: 30, 60, 120) 진행 후 동일 측정
3. 장애 관측
   - SQLite lock/wait 로그
   - 응답 timeout 비율

---

## 8) 수정 대상 파일 요약

- **주요 수정**
  - `app/api/routes/core.py`
  - `team_utils.py`
- **문서(선택)**
  - `docs/main_home_data_plan_ko.md`
  - 본 문서: `docs/home_medical_performance_mitigation_plan_ko.md`

---

## 9) 최종 권고

- 현재 의심한 지점(의료 3개 중복 계산 + 직렬 호출)은 실제 병목일 가능성이 매우 높습니다.
- 단, Home의 `get_team_detail()` 과사용 병목까지 함께 처리해야 체감 개선이 확실해집니다.
- 따라서 구현 우선순위는 **(1) 중복 제거 → (2) lightweight summary 전환 → (3) 제한적 병렬화** 순서를 권장합니다.
