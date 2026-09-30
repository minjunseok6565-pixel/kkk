# 웨이브/스트레치 + Dead Cap Ledger(정석안) 구현 계획

## 1) 목표(Goal)

이 문서는 다음 기능을 **정석적인 데이터 모델(별도 `team_dead_caps` 테이블)**로 구현하기 위한 상세 작업 계획이다.

### 제품 목표
1. 기존 `POST /api/contracts/release-to-fa`는 유지하되, 역할을 **"계약 만료자 비재계약 정리"**로 명확히 제한한다.
2. 신규 기능 2개를 추가한다.
   - **웨이브(waive)**: 선수를 FA로 방출하고, 남은 계약 연봉을 시즌별 그대로 dead cap으로 반영.
   - **스트레치(stretch)**: 선수를 FA로 방출하고, 남은 총연봉을 선택 분할 기간(최대 `(남은연수*2)+1`)으로 분할해 dead cap 반영.
3. dead cap은 Bird cap hold와 분리된 독립 개념으로 관리한다.
4. 오프시즌/시즌 중 구분 없이 항상 실행 가능해야 한다.

---

## 2) 완료 후 예상 동작(Expected Behavior)

### A. release-to-fa(기존) 동작
- 만료/비재계약 정리 전용으로 동작한다.
- 정책 위반(만료자가 아님) 시 400 에러를 반환한다.
- 이 API는 dead cap을 만들지 않는다.

### B. waive 동작
- 입력: `team_id`, `player_id`, `waived_date(optional)`.
- 검증:
  - 선수는 현재 해당 팀의 active roster여야 함.
  - 선수는 active contract를 보유해야 하며, 현재 시즌 기준 남은 계약연도가 1년 이상이어야 함.
- 처리:
  1) 선수 team_id를 `FA`로 이동(로스터/active contract team sync)
  2) 남은 시즌별 연봉을 dead cap ledger(`team_dead_caps`)에 시즌별 row로 생성
  3) 트랜잭션 로그 기록
- 결과:
  - 해당 선수는 FA가 된다.
  - 원 소속팀 salary cap에는 dead cap이 시즌별로 반영된다.

### C. stretch 동작
- 입력: `team_id`, `player_id`, `stretch_years`, `stretched_date(optional)`.
- 검증:
  - waive와 동일 + `1 <= stretch_years <= (remaining_years*2)+1`
- 처리:
  1) 선수 FA 이동
  2) 남은 총액 = 남은 계약연도 salary 합
  3) 분할 규칙으로 `stretch_years`개 시즌에 dead cap row 생성
     - 기본: 균등 분할(정수 처리 시 마지막 해 보정)
  4) 트랜잭션 로그 기록
- 결과:
  - 해당 선수는 FA가 된다.
  - 원 소속팀 salary cap에는 선택된 기간 동안 분할 dead cap이 반영된다.

### D. 캡 계산 동작
- 팀 cap salary 계산은 다음 합산으로 변경된다.
  - 팀 로스터 페이롤
  - Bird cap holds(기존)
  - Dead caps(신규)
- 어떤 선수가 이미 팀을 떠났더라도(dead cap ledger row가 있으면) 해당 시즌 cap에 반영된다.

---

## 3) 설계 원칙(Architecture Principles)

1. **도메인 분리**: Bird cap hold와 dead cap은 별도 저장소로 관리.
2. **원자성(Atomicity)**: 선수 FA 이동 + dead cap 생성 + tx 기록은 반드시 단일 트랜잭션.
3. **감사 가능성(Auditability)**: 모든 waive/stretch 수행은 transactions_log payload에 충분한 근거를 남긴다.
4. **재계산 안전성**: 시즌 전환/조회 시 dead cap 누락이 없도록 SSOT를 DB 테이블로 고정.
5. **멱등성 고려**: 동일 요청 재실행 시 중복 row가 폭발하지 않도록 키 설계와 conflict 정책을 명시.

---

## 4) 데이터 모델 변경(핵심)

## 4.1 신규 테이블: `team_dead_caps`

> 위치: `db_schema/core.py` (DDL)

### 권장 컬럼
- `dead_cap_id TEXT PRIMARY KEY`
- `team_id TEXT NOT NULL`
- `player_id TEXT NOT NULL`
- `origin_contract_id TEXT`  
- `source_type TEXT NOT NULL`  -- `WAIVE` | `STRETCH`
- `applied_season_year INTEGER NOT NULL`
- `amount INTEGER NOT NULL`     -- cap 반영 금액(해당 시즌)
- `is_voided INTEGER NOT NULL DEFAULT 0`
- `voided_reason TEXT`
- `meta_json TEXT`              -- 분할 근거/원래 잔여연봉 구성 등 추적 정보
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

### 권장 인덱스
- `(team_id, applied_season_year, is_voided)`
- `(player_id, applied_season_year)`
- `(origin_contract_id)`

### 유니크/중복 방지 전략
- `UNIQUE(team_id, player_id, origin_contract_id, source_type, applied_season_year)` 추천
- 동일 API 재호출로 같은 시즌 row 중복 생성 방지

---

## 4.2 적용 방식(개발 단계 정책)

> 개발 단계에서는 기존 DB/기존 세이브 호환을 고려하지 않고, 스키마를 직접 갱신하는 공격적 방식을 사용한다.

- `db_schema/core.py`에 `team_dead_caps`를 즉시 추가한다.
- 기존 DB/기존 세이브 호환 코드는 작성하지 않는다.
- 필요 시 개발 DB는 재생성한다.
- 인덱스/유니크 제약도 즉시 반영한다.

---

## 5) 백엔드 구현 작업(파일별 상세)

## 5.1 `league_repo.py`

### 신규 리포지토리 메서드
1. `upsert_team_dead_caps(rows)`
   - season/team/player/source/amount validation
   - 대량 upsert 지원
2. `list_team_dead_caps(team_id, season_year, active_only=True)`
   - `is_voided=0` 조건 선택 조회
3. `sum_active_dead_caps(team_id, season_year)`
   - cap 합산용
4. `void_dead_caps(player_id, team_id, reason, now_iso, season_year=None)`
   - 필요 시 후속 정책(취소/정정)에 대비

### 기존 메서드 영향
- `validate_integrity()`에 dead cap 기본 무결성 체크 추가(음수 금액 방지 등)

---

## 5.2 `league_service.py`

### 신규 서비스 API
1. `waive_player(...) -> ServiceEvent`
2. `stretch_player(...) -> ServiceEvent`

### 내부 헬퍼(권장)
- `_get_active_contract_for_player_in_cur(...)`
- `_remaining_salary_schedule(contract, current_season_year)`
- `_build_waive_dead_cap_rows(...)`
- `_build_stretch_dead_cap_rows(total, start_year, stretch_years)`
- `_sum_team_dead_caps_in_cur(team_id, season_year)`

### 캡 계산 반영
- `_compute_team_cap_salary_with_holds_in_cur(...)`를 확장해
  - `sum_active_dead_caps`(또는 동등 SQL) 추가
- 결과적으로 `payroll + bird_holds + dead_caps` 합산

### 기존 release-to-fa 정책 분리
- `release_player_to_free_agency`는 범용 release로 남기되,
  API 레벨에서 "만료자 비재계약" 정책 강제 또는
  서비스에 `release_reason`/`mode`를 받아 정책 체크 추가.

### 트랜잭션 로그
- `WAIVE_TO_FA`, `STRETCH_TO_FA` tx_type 추가
- payload 예시 필드:
  - `team_id`, `player_id`, `from_team`, `to_team`
  - `source_type`, `origin_contract_id`
  - `remaining_salary_by_year`
  - `dead_cap_schedule`
  - `stretch_years` (stretch만)

---

## 5.3 `app/schemas/contracts.py`

### 신규 요청 모델
- `WaivePlayerRequest`
  - `team_id: str`
  - `player_id: str`
  - `waived_date: Optional[str]`
- `StretchPlayerRequest`
  - `team_id: str`
  - `player_id: str`
  - `stretch_years: int`
  - `stretched_date: Optional[str]`

### 선택: 응답 모델(명시형)
- 이벤트 payload의 핵심 필드를 pydantic으로 정의하면 문서화/검증에 유리

---

## 5.4 `app/api/routes/contracts.py`

### 신규 엔드포인트
1. `POST /api/contracts/waive`
2. `POST /api/contracts/stretch`

### 동작
- `LeagueService` 신규 메서드 호출
- 성공 시 `{ok: true, event: ...}`
- 예외 매핑:
  - 정책 위반/입력오류: 400
  - 선수 없음/팀 불일치: 404/400 규칙 통일

### 기존 엔드포인트 조정
- `POST /api/contracts/release-to-fa`
  - "만료자 비재계약" 정책 체크 로직 추가
  - path는 유지하되 정책은 즉시 변경(만료자 비재계약 전용)한다.

### 조회 엔드포인트(권장)
- `GET /api/contracts/dead-caps?team_id=...&season_year=...&active_only=true`
  - 운영/디버깅 가시성 확보

---

## 5.5 `db_schema/core.py`

- `team_dead_caps` DDL 추가
- 인덱스/유니크 제약 추가
- migrate 루틴에 보강 로직 추가

---

## 5.6 시즌 전환/오프시즌 코드 (`contracts/offseason.py`, 필요 시 `league_service.py`)

- dead cap은 시즌별 ledger이므로 기본적으로 자동 소멸(해당 시즌 row만 합산)
- 별도 만료 처리 없이도 시즌 쿼리 기준으로 자연 종료됨
- 단, 관리 목적으로 `is_voided`/정정 기능이 있으면 오프시즌 정리 루틴에서 통계 제공 가능

---

## 5.7 문서/계약(Contract) 명세

- API 문서(README 또는 라우트 docstring)에
  - release-to-fa 용도 제한
  - waive/stretch 차이
  - stretch 분할 규칙(반올림 규칙 포함)
  - dead cap 조회 방법 명시

---

## 6) 분할 알고리즘 명세(스트레치)

## 6.1 입력
- `remaining_salary_by_year`: `{year: amount}` (year 오름차순)
- `stretch_years`

## 6.2 계산
- `total = sum(remaining_salary_by_year.values())`
- `base = total // stretch_years`
- `rem = total % stretch_years`
- 첫 `rem`개 시즌에 `base+1`, 나머지에 `base` 배정 (혹은 마지막 시즌 보정 규칙 택1)

> 정수 달러 기준 일관성 유지가 중요. 규칙은 고정하고 테스트로 잠가야 함.

## 6.3 시즌 시작점
- release가 발생한 현재 시즌을 `start_year`로 사용
- 분할은 `start_year ~ start_year + stretch_years - 1`

---

## 7) 정책 검증 규칙(Validation Rules)

1. 선수 소속 검증: 요청 `team_id`와 현재 active roster team 일치
2. 계약 검증: active_contract 존재 + 남은 연차 > 0
3. 웨이브: 남은 시즌별 금액 그대로 dead cap 생성
4. 스트레치: 분할 년수 범위 검증
5. 중복 방지: 동일 source_type에 대해 동일 시즌 row 중복 금지
6. 금액 검증: 음수 금액 금지
7. 날짜 검증: 입력 날짜는 ISO(YYYY-MM-DD), 없으면 인게임 날짜 사용

---

## 8) 테스트 계획(반드시 구현)

## 8.1 Repo 단위 테스트
- dead cap upsert/list/sum/void 정상 동작
- 중복 upsert 시 멱등성
- 음수/불량 입력 방어

## 8.2 Service 단위 테스트
- waive: FA 이동 + dead cap schedule 생성 + tx 로그
- stretch: 분할 합계 보존(total invariant) + 분할 시즌 수 검증
- 팀 cap 계산에 dead cap 합산 반영
- release-to-fa 정책 제한 검증(만료자 전용)

## 8.3 API 테스트
- `/waive`, `/stretch` 성공/실패 케이스
- `/dead-caps` 조회 결과 검증
- 기존 `/release-to-fa` 회귀 테스트

## 8.4 회귀 테스트(기존 기능 영향)
- Bird rights/cap holds 관련 테스트가 깨지지 않는지 확인
- FA 사인/재계약/연장 로직 정상 동작 확인

---

## 9) 단계별 구현 순서(실행 플랜)

1. **Schema/Repo 먼저**: `team_dead_caps` + repo CRUD/sum 추가
2. **Service 캡 계산 확장**: dead cap 합산 반영
3. **waive/stretch 서비스 로직 추가**
4. **API 스키마/라우트 추가**
5. **release-to-fa 정책 제한 적용**
6. **조회 API(dead-caps) 추가**
7. **테스트 추가 및 기존 테스트 회귀 확인**
8. **문서 정리**

---

## 10) 위험요소와 대응

1. **중복 기록 위험**
   - 대응: 유니크 키 + upsert 정책 + 멱등 테스트
2. **분할 반올림 오차**
   - 대응: 정수 분할 규칙 고정 + 합계 보존 테스트
3. **기존 release-to-fa 사용자 영향**
   - 대응: 에러 메시지 명확화 + 프론트 호출 정책 동기화
4. **캡 계산 회귀 위험**
   - 대응: 팀 cap 계산 단위 테스트를 강화하고, 기존 bird hold 시나리오 회귀 보장

---

## 11) 완료 기준(Definition of Done)

- [ ] 신규 DB 테이블 `team_dead_caps` 생성(직접 스키마 갱신)
- [ ] waive/stretch API 및 서비스 구현
- [ ] release-to-fa 정책 제한 적용(만료자 비재계약 전용)
- [ ] 팀 cap 계산에 dead cap 합산 반영
- [ ] dead cap 조회 API 제공
- [ ] 단위/통합 테스트 통과
- [ ] 기능 문서화 완료
