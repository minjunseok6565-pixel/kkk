# DB Schema 개발 환경 가이드 (NBA 시뮬레이션)

이 문서는 현재 `db_schema/` 패키지의 **실제 코드**를 기준으로, 스키마 개발/검증 작업을 빠르게 반복하기 위한 개발자 가이드다.

## 1) 스키마 적용 구조 이해

- 외부 공개 엔트리포인트는 `db_schema.apply_schema` 하나다.
- `apply_schema`는 `DEFAULT_MODULES` 순서대로 각 모듈의 `ddl()`을 모아 한 번에 `executescript`로 실행하고, 이후 각 모듈의 `migrate()`를 순회 실행한다.
- 따라서 개발 시 핵심 원칙은 아래 2가지다.
  1. **테이블 생성/인덱스 기본 정의는 `ddl()`**
  2. **기존 테이블 호환성 보강은 `migrate()` + `ensure_columns`**

### 현재 기본 모듈 순서

1. `core`
2. `training`
3. `fatigue`
4. `injury`
5. `readiness`
6. `practice`
7. `team_strategy`
8. `agency`
9. `trade_assets`
10. `draft`
11. `gm`
12. `college`
13. `scouting`
14. `retirement`
15. `two_way`

> 주의: 다수 모듈이 `players` FK를 참조하므로 `core` 선행은 필수다.

---

## 2) 모듈별 테이블 인벤토리 (현재 코드 기준)

### Core

- `meta`
- `players`
- `roster`
- `contracts`
- `transactions_log`
- `player_contracts`
- `active_contracts`
- `free_agents`

`migrate()`에서 `contracts`, `transactions_log`의 additive column 보강과 트랜잭션 인덱스를 추가한다.

### Training / Player Development

- `team_training_plans`
- `player_training_plans`
- `player_growth_profile`

### Fatigue

- `player_fatigue_state`

`st`, `lt`는 `0..1` 범위 체크가 포함된다.

### Injury

- `player_injury_state`
- `injury_events`

### Readiness

- `player_sharpness_state`
- `team_scheme_familiarity_state`

### Practice

- `team_practice_plans`
- `team_practice_sessions`

### Team Strategy

- `team_strategy`

전략 값은 `WIN_NOW | BALANCED | DEVELOP | REBUILD`로 제한된다.

### Agency

- `player_agency_state`
- `agency_events`
- `agency_event_responses`
- `player_agency_promises`

`migrate()`에서 `player_agency_state`의 v3 관련 additive column을 보강한다.

### Trade Assets

- `draft_picks`
- `swap_rights`
- `fixed_assets`

`migrate()`에서 `draft_picks`, `swap_rights` 컬럼 확장을 additive 방식으로 보강한다.

### Draft

- `draft_results`
- `draft_order_plans`
- `draft_selections`
- `draft_combine_results`
- `draft_workout_results`
- `draft_interview_results`
- `draft_withdrawals`
- `draft_undrafted_outcomes`
- `draft_watch_runs`
- `draft_watch_probs`

### GM

- `gm_profiles`

### College

- `college_teams`
- `college_players`
- `college_player_season_stats`
- `college_team_season_stats`
- `college_draft_entries`
- `draft_class_strength`

### Scouting

- `scouting_scouts`
- `scouting_assignments`
- `scouting_reports`

### Retirement

- `player_retirement_decisions`
- `retirement_events`

### Two-way

- `two_way_appearances`

---

## 3) 개발 환경 최적화를 위한 실무 규칙

### A. 스키마 변경은 기본적으로 Additive

현재 코드 스타일은 과거 데이터/세이브 호환성을 위해 `ensure_columns` 기반의 additive migration 패턴을 사용한다.

- 새 컬럼 추가: `migrate()`에서 `ensure_columns(...)`
- 파괴적 변경(삭제/타입변경): 현재 패턴상 직접 지원하지 않으므로 신중하게 별도 설계

### B. FK/모듈 의존성 먼저 확인

- `players`를 참조하는 테이블은 `core` 이후 모듈에 배치
- 모듈 추가 시 `db_schema/init.py`의 `DEFAULT_MODULES` 위치를 의존 순서로 배치

### C. JSON 칼럼은 SSOT 유연성 목적

여러 시스템이 `*_json TEXT`를 광범위하게 사용한다. 이는 스키마 진화 비용을 낮추려는 현재 설계 의도다.

- 구조화 쿼리 빈도가 높은 값만 first-class column으로 승격
- 승격 시 인덱스까지 같이 고려

### D. 인덱스는 DDL에 명시적으로 포함

대부분 테이블이 읽기 경로 기준 인덱스를 즉시 생성한다. 새 조회 경로를 추가했다면 인덱스 필요성을 함께 검토한다.

---

## 4) 로컬 개발 루프 (권장)

### 1. 정적/문법 검증

```bash
python -m compileall db_schema
```

### 2. 스키마 엔트리포인트 import 점검

```bash
python - <<'PY'
from db_schema import apply_schema
print("ok", callable(apply_schema))
PY
```

### 3. 모듈 추가/수정 체크리스트

- [ ] `ddl()`에 `CREATE TABLE IF NOT EXISTS` 사용
- [ ] 필요한 `CREATE INDEX IF NOT EXISTS` 포함
- [ ] 기존 DB 호환이 필요하면 `migrate()`에 `ensure_columns` 추가
- [ ] `DEFAULT_MODULES` 순서 의존성 점검
- [ ] FK 대상 테이블 선행 생성 여부 확인

---

## 5) 빠른 문제 진단 포인트

- 초기화 시 실패:
  - 모듈 순서 문제(`players` 참조 테이블이 `core`보다 먼저 실행)
  - DDL 문자열 내 SQL 문법 문제
- 운영 중 컬럼 누락:
  - `migrate()`에 `ensure_columns` 누락 가능성
- 조회 성능 저하:
  - 신규 where/sort 패턴에 맞는 인덱스 부재 가능성

---

## 6) 결론

현재 `db_schema/`는 “모듈 분리 + 일괄 DDL + additive migration” 패턴이 일관되게 적용되어 있다.

개발 효율을 높이려면 다음 3가지만 고정 습관으로 가져가면 된다.

1. 의존 순서(`DEFAULT_MODULES`)를 먼저 본다.
2. 스키마 진화는 `migrate()+ensure_columns`로 처리한다.
3. 변경 직후 `compileall` + 최소 import 체크를 자동화한다.
