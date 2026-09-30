# 목표: 기존 즉시 샐러리 제거형 방출을 폐기하고, `웨이브(잔여 연봉 그대로 캡 반영)` + `스트레치(잔여 총액을 선택 기간으로 분할 캡 반영)` 2가지 방출 모드를 즉시 적용한다.

이 문서는 **즉시 패치 가능한 공격적 구현 계획**이다.  
(범위 밖: 하위호환, 점진 마이그레이션, 레거시 보호)

---

## 0) 최종 동작 정의 (정책 확정)

### A. 공통
- 방출 시 선수는 즉시 `FA`로 전환한다.
- 기존 "방출하면 팀 캡에서 즉시 제거" 동작은 제거한다.
- 캡 반영은 선수 로스터 소속이 아니라 **방출 의무 레저(dead cap obligations)** 기준으로 처리한다.

### B. 웨이브 (`WAIVE`)
- 계약 잔여 시즌의 `salary_by_year`를 그대로 시즌별 데드캡으로 기록한다.
- 예: 11M, 12M, 13M 남아 있으면 데드캡도 11/12/13M.

### C. 스트레치 (`STRETCH`)
- 잔여 계약 총액 = 남은 시즌 연봉 합.
- `max_stretch_years = (remaining_years * 2) + 1`.
- 사용자 입력 `stretch_years`는 `1 <= stretch_years <= max_stretch_years`.
- 총액을 `stretch_years`로 분할한 연도별 데드캡을 생성.
  - 정수 달러 분배 규칙: `base = total // stretch_years`, `remainder = total % stretch_years`, 앞에서부터 `+1`씩 배분.
- 분할 시작연도는 방출이 발생한 리그 시즌(`release_season_year`)부터.

---

## 1) 파일별 작업 계획 (즉시 패치 단위)

## 1-1) `db_schema/core.py`

### 작업
1. 데드캡 전용 테이블 추가:
   - `dead_cap_obligations`
   - 컬럼:
     - `obligation_id TEXT PRIMARY KEY`
     - `team_id TEXT NOT NULL`
     - `player_id TEXT NOT NULL`
     - `mode TEXT NOT NULL`  (`WAIVE` / `STRETCH`)
     - `source_contract_id TEXT`
     - `release_season_year INTEGER NOT NULL`
     - `season_year INTEGER NOT NULL`
     - `amount INTEGER NOT NULL`
     - `stretch_years INTEGER` (STRETCH일 때만)
     - `created_at TEXT NOT NULL`
     - `updated_at TEXT NOT NULL`
2. 인덱스 추가:
   - `idx_dead_cap_team_season(team_id, season_year)`
   - `idx_dead_cap_player(player_id)`
3. 중복 방지 유니크 인덱스 추가:
   - `uq_dead_cap_team_player_season_mode(team_id, player_id, season_year, mode)`

### 의도
- 캡 계산을 선수 로스터 소속과 분리.
- 웨이브/스트레치 모두 동일 레저 모델에 수렴.

---

## 1-2) `app/schemas/contracts.py`

### 작업
`ReleaseToFARequest`를 폐기/대체하여 아래 필드를 받도록 변경:
- `player_id: str`
- `release_mode: str`  (`WAIVE` | `STRETCH`)
- `stretch_years: Optional[int] = None`
- `released_date: Optional[str] = None`

### 유효성
- `release_mode == 'STRETCH'`이면 `stretch_years` 필수.
- `release_mode == 'WAIVE'`이면 `stretch_years`는 `None` 또는 무시.

---

## 1-3) `app/api/routes/contracts.py`

### 작업
`/api/contracts/release-to-fa` 엔드포인트를 모드 기반 방출 엔드포인트로 승격:
- 기존 URL 유지 가능 (`/api/contracts/release-to-fa`)
- 내부 호출을 신규 서비스 함수로 변경:
  - `svc.release_player_with_obligation(...)`

### 요청/응답
- 요청: `player_id`, `release_mode`, `stretch_years`, `released_date`
- 응답 payload에 반드시 포함:
  - `release_mode`
  - `from_team`
  - `to_team='FA'`
  - `obligation_rows_created`
  - `obligation_total_amount`
  - `obligation_by_year` (예: `{2026: 5142857, ...}`)

### 에러
- STRETCH에서 범위 초과 시 400.
- 잔여 급여가 0인 선수 방출 시에도 모드 허용하되 obligation 0건 허용.

---

## 1-4) `league_service.py` (핵심)

### 신규 private 유틸 추가
1. `_get_active_contract_for_player_in_cur(cur, player_id) -> (contract_id, contract_dict|None)`
2. `_current_or_release_season_year(released_date_iso) -> int`
3. `_remaining_salary_schedule(contract, from_season_year) -> Dict[int, int]`
   - `salary_by_year`에서 `>= from_season_year`만 추출
4. `_build_waive_obligation_schedule(remaining_schedule) -> Dict[int, int]`
5. `_build_stretch_obligation_schedule(total_amount, release_season_year, stretch_years) -> Dict[int, int]`
6. `_insert_dead_cap_obligations_in_cur(...)`
7. `_sum_dead_cap_for_team_season_in_cur(cur, team_id, season_year) -> int`

### 기존 함수 대체
- `release_player_to_free_agency(...)`를 제거하거나 deprecated 처리하고,
- 신규 함수 도입:

```python
def release_player_with_obligation(
    self,
    player_id: str,
    *,
    release_mode: str,              # WAIVE | STRETCH
    stretch_years: int | None = None,
    released_date: date | str | None = None,
) -> ServiceEvent:
```

### 처리 순서 (원자적 트랜잭션)
1. active roster 조회, `from_team` 확보 (`FA`면 에러)
2. active contract 조회 + 잔여 스케줄 계산
3. release_mode 분기
   - WAIVE: 시즌별 동일 금액 obligation schedule
   - STRETCH: 총액/기간 분할 obligation schedule
4. `dead_cap_obligations` insert
5. `_move_player_team_in_cur(cur, pid, 'FA')` 호출 (선수 즉시 FA)
6. transactions log 기록 (`RELEASE_WAIVE` / `RELEASE_STRETCH`)
7. ServiceEvent 반환

### 페이롤 계산 함수 확장
`_compute_team_payroll_for_season_in_cur`를 아래로 변경:
- 기존 팀 로스터 salary 합
- **+ dead cap obligations 합** (`team_id`, `season_year`)
- 반환은 합산값

즉, FA 계약 cap 체크/트레이드 cap 관련 모든 기존 경로가 자동으로 데드캡을 반영하게 만든다.

---

## 1-5) `contracts/offseason.py`

### 작업
- 만료 계약 자동 처리에서 "단순 FA 방출" 경로를 유지하되,
- **이 경로는 의무 생성 없이** 계약만료 이동으로 남긴다.
- 단, 사람이 수동 방출하는 기능(웨이브/스트레치)과 혼동되지 않도록 이벤트 타입 분리.

### 주의
- "계약 만료로 FA"와 "유효 계약 중 웨이브/스트레치"는 다른 이벤트로 남겨야 통계/UI 혼선이 없다.

---

## 1-6) `static/js/features/offseason/offseasonDevFlow.js`

### 작업
1. 기존 `RELEASE` 단일 버튼을 2개로 분리:
   - `웨이브`
   - `스트레치`
2. 스트레치 클릭 시 기간 입력 UI 제공:
   - 숫자 입력 or select
   - 백엔드에서 `max_stretch_years`를 받아 제약하거나 프런트에서 계산 가능
3. API 호출 body 변경:

```json
{
  "player_id": "P000123",
  "release_mode": "STRETCH",
  "stretch_years": 7
}
```

또는

```json
{
  "player_id": "P000123",
  "release_mode": "WAIVE"
}
```

4. 결과 상태 문구에 연도별 의무 요약 표시.

---

## 1-7) `static/js/features/myteam/myTeamScreen.js` (+ 필요시 상세 탭)

### 작업
- 팀 캡 요약 영역에 `dead cap` 별도 라인 노출:
  - `TEAM PAYROLL`
  - `DEAD CAP`
  - `CAP SPACE`
- API가 팀 시즌 dead cap 요약을 내려주면 그대로 표시.

### 목적
- 유저가 웨이브/스트레치 후 캡이 왜 줄었는지 즉시 이해 가능.

---

## 1-8) `league_repo.py` (선택적, 하지만 권장)

### 작업
- dead cap 조회 helper 추가:
  - `get_dead_cap_by_team_season(team_id, season_year)`
  - `get_dead_cap_rows_by_team(team_id)`
- 서비스 단 SQL 직접호출을 줄이고 repo 경유로 통일.

---

## 1-9) 테스트 파일 추가

### 신규 테스트 제안
1. `tests/contracts/test_release_modes.py`
   - 웨이브 시 obligation 시즌/금액이 계약 잔여와 동일한지
   - 스트레치 시 총액 보존 + 기간 제한 검증
2. `tests/contracts/test_payroll_includes_dead_cap.py`
   - 선수는 FA인데 팀 payroll에 dead cap이 포함되는지
3. `tests/api/test_release_to_fa_modes.py`
   - API 200/400 케이스
   - 응답 payload 필드 검증

### 최소 필수 케이스
- 3년 11/12/13M 예시를 fixture로 고정하여 WAIVE/STRETCH 결과를 스냅샷 검증.

---

## 2) 즉시 적용을 위한 코드 변경 원칙

1. **레거시 동작 제거 우선**
   - 기존 "release == cap 즉시 삭제" 로직 경로는 호출 불가 상태로 전환.
2. **중앙 집약**
   - 방출의무 생성은 `LeagueService` 단일 엔트리에서만 수행.
3. **계산 단일화**
   - 캡/페이롤은 항상 `_compute_team_payroll_for_season_in_cur`만 신뢰.
4. **명시적 이벤트 타입**
   - `RELEASE_WAIVE`, `RELEASE_STRETCH`를 트랜잭션 로그에 구분 기록.

---

## 3) 구현 체크리스트 (패치 순서)

1. [ ] `db_schema/core.py`에 `dead_cap_obligations` DDL + index 추가
2. [ ] `app/schemas/contracts.py` 요청 스키마 확장
3. [ ] `league_service.py`에 신규 방출 함수 + obligation 계산/저장 유틸 추가
4. [ ] `league_service.py` 페이롤 계산에 dead cap 합산 반영
5. [ ] `app/api/routes/contracts.py` 엔드포인트 입력/호출/응답 갱신
6. [ ] `offseasonDevFlow.js` 버튼/입력/API body/결과문구 갱신
7. [ ] `myTeamScreen.js` dead cap 표시 추가
8. [ ] 단위/API 테스트 추가
9. [ ] 기존 release 경로 제거/호출부 전환 완료 확인

---

## 4) 수용 기준 (Definition of Done)

- 웨이브:
  - 선수는 즉시 FA.
  - 잔여 계약 시즌별 금액이 팀 데드캡으로 1:1 반영.
- 스트레치:
  - 선수는 즉시 FA.
  - 잔여 총액 보존.
  - 선택한 기간으로 정확히 분할되어 시즌별 데드캡 반영.
- 캡 체크:
  - FA 계약 시 cap-space 계산에 dead cap이 반드시 포함.
- UI:
  - 유저가 웨이브/스트레치 선택 가능.
  - 결과로 생성된 dead cap 스케줄 확인 가능.
- 기존 즉시 샐러리 제거형 방출 동작은 제거됨.

---

## 5) 예시 검증 시나리오 (고정)

- 대상 선수 잔여 계약: `Y1=11M, Y2=12M, Y3=13M`

### WAIVE
- dead cap: `Y1=11M, Y2=12M, Y3=13M`
- 선수: 즉시 FA

### STRETCH (7년 선택)
- total=36M
- 예시 분배(정수 달러):
  - `base = 36,000,000 // 7 = 5,142,857`
  - `remainder = 1`
  - 연도별: `5,142,858`, 이후 6개년 `5,142,857`
- 선수: 즉시 FA

이 결과가 API 응답/DB/캡계산/UI에 일관되게 반영되면 완료.
