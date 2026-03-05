# Incremental Standings Update 전환 구현 계획

## 목표
- 현재 `standings`/`team records` 계산이 **요청 시 전체 스케줄 재스캔(O(전체 경기 수))** 되는 구조를,
- 경기 결과 확정 시점에만 델타 반영하는 **증분 갱신 구조(O(1)~O(팀 수 log 팀 수))**로 전환한다.

---

## 현재 병목 요약 (현행 코드 기준)
- `team_utils.get_conference_standings_table()`는 매 요청마다 `master_schedule.games`를 순회하며 모든 집계를 재계산.
- `team_utils.get_conference_standings()`/`get_team_cards()`/`get_team_detail()`도 동일하게 `_compute_team_records()` 재사용으로 전체 스캔 비용 공유.
- 시즌 진행으로 `final` 경기 수가 증가할수록 탭 진입 지연이 선형 증가.

---

## 전환 전략 개요
1. **State에 standings 캐시(branch) 신설**
   - 팀별 누적 레코드/분할 스플릿(home/away/div/conf)/최근 10경기/L10/streak 등을 보관.
2. **경기 확정 이벤트에서만 캐시 증분 반영**
   - 시뮬레이션 흐름에서 한 경기 `final` 확정 시 `apply_game_result_to_standings_cache(game)` 호출.
3. **조회 API는 캐시 읽기 우선**
   - `team_utils`의 standings 관련 함수는 캐시를 읽어 포맷팅만 수행.
4. **무결성용 재빌드 경로 제공**
   - 저장 불일치/마이그레이션/디버그 시 전체 스캔으로 캐시 재생성 가능하도록 유지.

---

## 파일별 상세 수정 계획

## 1) `schema.py`
### 변경 내용
- `GameState`에 신규 캐시 스키마 추가:
  - 예: `standings_cache: Dict[str, Any]`
- 내부 필드(예시)
  - `version`: 캐시 스키마 버전
  - `built_from`: `{ season_id, regular_final_count }`
  - `records_by_team`: 팀별 누적 통계
  - `recent_results_by_team`: 최근 결과 ring-buffer(최대 10)
  - `last_applied_game_ids`: 중복 반영 방지 세트/맵

### 목적
- 캐시 데이터의 존재를 타입/검증 레벨에서 공식화.

### 유의점
- 저장/로드 직렬화 시 용량 과대화 방지를 위해, 불필요 파생값은 저장하지 않거나 재생성 가능하게 설계.

---

## 2) `state.py`
### 변경 내용
- 초기 상태 생성 경로에 `standings_cache` 기본값 주입.
- 읽기 accessor 추가:
  - `get_standings_cache_snapshot()`
- 쓰기 helper 추가:
  - `set_standings_cache(cache: dict)`
  - `mutate_standings_cache(fn)` 또는 전용 mutation 함수
- 무결성 유틸 추가:
  - `rebuild_standings_cache_from_master_schedule()`

### 목적
- API/서비스 레이어가 전체 state export 없이 standings 캐시만 읽고 쓸 수 있도록 분리.

### 유의점
- `_read_state`/`transaction` 패턴 유지.
- 대형 deepcopy 최소화(필요 필드만 `_to_plain`).

---

## 3) `state_modules/` (신규 모듈 권장: `state_modules/state_standings.py`)
### 변경 내용
- 순수 함수 중심으로 standings 증분 로직 캡슐화.
- 주요 함수(예시)
  - `create_empty_standings_cache(team_ids)`
  - `apply_final_game(cache, game, team_conf_div_map)`
  - `remove_final_game(cache, game, team_conf_div_map)` (정정/롤백 대비 옵션)
  - `compute_standings_rows(cache, conference)`
  - `ensure_cache_consistency(cache, regular_final_games)`

### 목적
- `team_utils`/`sim`/`state`에 분산될 복잡도를 단일 모듈로 집중.

### 유의점
- "같은 game_id 중복 반영 금지" 로직 필수.
- phase(`regular`)만 반영하도록 명시.

---

## 4) `team_utils.py`
### 변경 내용
- `_compute_team_records()`의 기본 데이터소스를
  - 기존: `master_schedule.games` 전체 순회
  - 변경: `standings_cache.records_by_team` 읽기
- `get_conference_standings_table()` 재작성:
  - 캐시 기반으로 row 조합 및 정렬/GB 계산만 수행
  - `L10`, `STRK`, home/away/div/conf 값을 캐시에서 직접 사용
- 폴백 로직 유지:
  - 캐시 미존재/불일치 시 `rebuild_standings_cache_from_master_schedule()` 후 재시도

### 목적
- 탭 진입 API의 CPU 비용을 상수/저비용으로 축소.

### 유의점
- 기존 응답 스키마(`pct`, `gb_display`, `home`, `away`, `div`, `conf`, `l10`, `strk`) 호환 유지.

---

## 5) `app/api/routes/sim.py`
### 변경 내용
- 경기 진행/리그 진행 완료 지점에 standings 캐시 증분 반영 훅 추가.
- 적용 후보
  - `/api/simulate-game`
  - `/api/advance-league`
  - `/api/game/progress-next-user-game-day`
  - `/api/game/auto-advance-to-next-user-game-day`
- 구현 방식
  - 경기 결과가 `final`로 state 반영되는 동일 트랜잭션 내부(또는 직후 단일 트랜잭션)에서 캐시 update 호출.

### 목적
- 조회 시점 계산 제거, 쓰기 시점 계산으로 전환.

### 유의점
- 한 요청에서 여러 경기 확정 시 반복 호출 비용 최소화를 위해 batch apply 함수 고려.

---

## 6) `app/api/routes/core.py`
### 변경 내용
- `/api/standings/table` 및 standings 의존 API가 캐시 기반 `team_utils`를 사용하도록 경로 점검.
- 필요 시 diagnostics endpoint(개발용):
  - 캐시 상태(`built_from`, 팀별 집계 일부, consistency flag) 확인.

### 목적
- API 계층은 비즈니스 로직 변경 영향 최소화 + 관측성 강화.

---

## 7) 테스트 파일
### 수정/추가 대상
- `tests/test_team_schedule_api.py` (영향도 점검)
- `tests/test_home_dashboard_api.py` (standings 제공 경로 변화 회귀 확인)
- **신규 권장**: `tests/test_standings_incremental_cache.py`

### 테스트 케이스 설계
1. **증분 반영 기본**
   - 빈 캐시에 final game 1개 반영 -> 양 팀 wins/losses/ppg/opp_ppg 반영 검증.
2. **중복 반영 방지**
   - 동일 game_id 재적용 시 수치 불변.
3. **정렬/GB/L10/STRK**
   - 여러 경기 반영 후 rank/gb/l10/strk가 기존 스펙과 동일.
4. **폴백 재빌드**
   - 캐시 파손 상태에서 standings 조회 시 재빌드 후 정상 반환.
5. **대량 진행 성능 회귀 방지(단위 수준)**
   - N경기 적용 뒤 standings 조회가 전체 스캔 없이 동작(모킹으로 스캔 호출 금지 검증).

---

## 데이터 구조 초안 (standings_cache)
```json
{
  "version": 1,
  "built_from": {
    "season_id": "2025-26",
    "regular_final_count": 123
  },
  "applied_game_ids": {
    "G0001": true,
    "G0002": true
  },
  "records_by_team": {
    "BOS": {
      "wins": 10,
      "losses": 4,
      "pf": 1580,
      "pa": 1499,
      "home_wins": 6,
      "home_losses": 2,
      "away_wins": 4,
      "away_losses": 2,
      "div_wins": 3,
      "div_losses": 1,
      "conf_wins": 8,
      "conf_losses": 3,
      "recent10": [1,1,0,1,1,1,0,1,1,0],
      "streak_type": "W",
      "streak_len": 2
    }
  }
}
```

---

## 마이그레이션/호환 전략
- 런타임 첫 standings 조회 시:
  1) 캐시 없음 -> 전체 재빌드 1회
  2) 이후 경기 확정은 증분 반영
- 기존 save 파일 로드:
  - `standings_cache` 누락 허용
  - 누락 시 lazy rebuild

---

## 관측성/운영 체크
- 로그/메트릭(간단 카운터)
  - `standings_cache_hit`
  - `standings_cache_rebuild`
  - `standings_cache_apply_game`
  - `standings_cache_duplicate_skip`
- 성능 비교
  - 기준: `/api/standings/table` p50/p95
  - 시나리오: 10경기/200경기/1000경기 진행 후 응답시간

---

## 예상 리스크 및 부작용
1. **정합성 리스크**
   - 이벤트 누락/중복으로 순위 오염 가능.
   - 대응: `applied_game_ids`, 재빌드 폴백, 주기적 consistency check.
2. **복잡도 증가**
   - write-path가 복잡해져 유지보수 난이도 상승.
   - 대응: standings 로직을 `state_standings.py`로 분리.
3. **저장 데이터 증가**
   - 캐시 저장 시 파일 크기 증가.
   - 대응: 최소 필드만 저장 + 파생값은 조회 시 계산.
4. **정정/롤백 케이스**
   - 경기 결과 수정 시 역적용 필요.
   - 대응: 초기 단계에서는 "재빌드" 우선, 이후 remove/apply 지원.

---

## 단계별 실행 순서 (구현 착수 시)
1. `schema.py` + `state.py`에 캐시 스키마/접근자 추가
2. `state_modules/state_standings.py` 순수 로직 구현
3. `team_utils.py` standings read-path를 캐시 기반으로 전환
4. `sim.py` write-path에 증분 반영 훅 삽입
5. 테스트 추가/수정
6. 성능 비교 후 폴백/로그 튜닝

---

## 완료 정의 (DoD)
- `standings` 조회 경로에서 `master_schedule.games` 전체 순회가 발생하지 않는다(캐시 정상 시).
- 경기 진행 후 standings 관련 수치가 즉시 일관되게 반영된다.
- 캐시 삭제/파손 상태에서도 standings API는 자동 복구된다.
- 기존 UI 응답 스키마 호환을 유지한다.
