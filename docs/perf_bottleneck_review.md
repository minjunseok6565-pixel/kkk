# 경기 진행 후 UI 버벅임 잔존 병목 점검 리포트

## 범위
- 이번 리포트는 **코드 정독 기반 병목 탐색**에 집중한다.
- 실제 수정은 하지 않았고, 남아있는 병목 후보와 해결 방향만 정리한다.

## 핵심 결론 (요약)
1. `Home` 대시보드 API가 여전히 무거움.
   - 한 번의 `/api/home/dashboard/{team_id}` 호출에서 스케줄/순위/메디컬/요약을 모두 계산한다.
2. 순위 경량 API가 캐시를 우회해 매번 전체 경기 스캔을 수행한다.
3. 팀 요약 계산에서 payroll 계산이 중복 DB I/O를 유발한다.
4. 탭 전환 시 각 화면이 독립 API를 매번 새로 호출하고 있어 누적 데이터가 커질수록 체감 지연이 남을 가능성이 높다.

---

## 상세 병목 후보

### 1) Home 대시보드 엔드포인트의 과도한 집계 결합
- `/api/home/dashboard/{team_id}`는 한 요청 안에서 아래를 모두 수행한다.
  - 전체 시즌 스케줄 기반 가공
  - 팀 요약
  - 홈용 순위
  - 메디컬 overview(현재)
  - 메디컬 overview(7일 전)
  - 메디컬 alerts
  - 메디컬 risk-calendar
- 즉, Home 한 번 열 때 복수의 비용 큰 경로를 직렬 실행한다.

**근거 코드**
- `api_home_dashboard`에서 위 계산들을 한 번에 호출.【F:app/api/routes/core.py†L2076-L2123】

**개선 방향 (설계)**
- Home payload를 `core`/`medical`/`activity`로 분리하고, 초기 렌더에는 `core`만 먼저 응답.
- 메디컬 블록은 지연 로드(또는 background refresh)로 이동.
- 동일 `as_of/team_id` 조합에 대해 짧은 TTL(예: 5~15초) 캐시 적용.

### 2) Home용 standings 계산이 standings cache를 활용하지 않음
- `get_conference_standings_home_light()`는 현재 `master_schedule` 전체를 순회해 W/L, L10, streak를 재계산한다.
- 경기 수가 늘수록 Home 진입 시간이 선형으로 증가.
- 이미 프로젝트에는 증분 갱신되는 standings cache 경로가 존재하는데, 이 함수는 그 경로를 사용하지 않는다.

**근거 코드**
- 전체 게임 순회 기반 계산 로직.【F:team_utils.py†L479-L579】
- 반면, 테이블용 standings는 cache 기반 계산을 사용.【F:team_utils.py†L463-L476】

**개선 방향 (설계)**
- Home용 standings도 cache(`_get_or_rebuild_standings_cache`) 기반으로 전환.
- L10/streak는 cache record(`recent10`, `streak_type`, `streak_len`)를 직접 사용해 O(팀수)로 계산.
- 최소한 요청 단위 memoization으로 동일 요청 내 중복 계산 제거.

### 3) Team summary light에서 payroll DB 계산이 중복 호출됨
- `get_team_summary_light()`는 `payroll`과 `cap_space`를 함께 반환.
- 그런데 `cap_space` 계산 내부가 다시 payroll을 계산하여 roster DB를 한 번 더 읽는다.

**근거 코드**
- `get_team_summary_light`에서 payroll + cap_space 둘 다 요청.【F:team_utils.py†L737-L770】
- `_compute_cap_space` 내부가 `_compute_team_payroll` 재호출.【F:team_utils.py†L309-L312】

**개선 방향 (설계)**
- `get_team_summary_light`에서 payroll 1회 계산 후, cap_space는 해당 값을 인자로 받아 계산.
- 함수 시그니처 예시: `_compute_cap_space_from_payroll(payroll)`.

### 4) 탭 전환 시 네트워크 호출 재실행 + 화면별 전체 렌더
- 스케줄/훈련/순위/메디컬 탭 진입 시 각 화면 함수가 즉시 API를 호출한다.
- 캐시/중복 요청 억제/백그라운드 prefetch가 약하면, 시즌 진행 후 데이터가 커질수록 탭 반응이 느려질 수 있다.

**근거 코드**
- 이벤트 바인딩에서 탭 클릭마다 화면 로더 호출.【F:static/js/app/events.js†L37-L55】
- 예: 스케줄 화면 진입 시 매번 `/api/team-schedule/{team_id}` 호출.【F:static/js/features/schedule/scheduleScreen.js†L64-L68】

**개선 방향 (설계)**
- 프론트에 화면 단위 SWR 캐시 도입(짧은 stale 허용).
- 같은 탭 연속 진입 시 즉시 이전 데이터 표시 + 백그라운드 revalidate.
- in-flight request dedupe (동일 key 요청은 하나만).

### 5) Home 대시보드에서 메디컬 계산이 2회 + 파생 계산 다수
- overview(now), overview(prev) 두 번 계산 후 alerts/risk_calendar까지 이어서 생성.
- Home 화면을 자주 왕복할 때 누적 비용이 큼.

**근거 코드**
- 메디컬 overview now/prev 및 alerts/risk_calendar 생성.【F:app/api/routes/core.py†L2092-L2123】

**개선 방향 (설계)**
- Home에서 필요한 최소 지표만 별도 라이트 엔드포인트로 분리.
- 상세 메디컬 데이터는 메디컬 탭 진입 시에만 계산.
- 기준일 `as_of`가 변하지 않는 동안 결과 캐시.

---

## 우선순위 제안 (수정 전 계획)
1. **P0**: `get_conference_standings_home_light()`를 cache 기반으로 교체.
2. **P0**: `get_team_summary_light()` payroll 중복 I/O 제거.
3. **P1**: `/api/home/dashboard`를 경량/지연 블록으로 분리.
4. **P1**: 프론트 탭별 SWR 캐시 + in-flight dedupe 적용.
5. **P2**: 메디컬/활동피드 계산 결과 TTL 캐시.

## 검증 방법 제안 (수정 이후 측정)
- 서버: 엔드포인트별 p50/p95 로그 추가 (`/api/home/dashboard`, `/api/team-schedule`, `/api/standings/table`, 메디컬 4개).
- 클라이언트: 탭 클릭~첫 paint까지 `performance.mark/measure` 계측.
- 회귀 기준: 시즌 0경기 / 200경기 / 800경기 상태에서 탭 전환 latency 비교.

