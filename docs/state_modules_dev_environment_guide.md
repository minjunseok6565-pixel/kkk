# state_modules 개발환경 최적화 가이드

이 문서는 `state_modules/`의 실제 코드만 근거로, 상태 서브시스템을 안전하고 빠르게 개발하기 위한 작업 기준을 정리한다.

## 1) 모듈 책임 분리(핵심)

`state_modules`는 역할이 명확하게 나뉜다.

- `state_store.py`: 프로세스-로컬 전역 상태 저장소 + 트랜잭션/읽기 전용 뷰 제공.
- `state_schedule.py`: `master_schedule` 생성/검증/인덱싱/요약/조회 유틸.
- `state_results.py`: `GameResultV2` 검증 + 시즌 누적 집계용 순수 계산 유틸.
- `state_bootstrap.py`: DB 초기화/시드/캡 적용/컨트랙트 부트스트랩/무결성 검증(시작 시 1회성 계열).
- `state_migrations.py`: `ingest_turn` 백필 마이그레이션(1회성).
- `state_trade.py`: 트레이드 관련 상태 키 존재/타입 보장.
- `state_cap.py`: 시즌 기준 샐러리캡/apron/매칭 파라미터 적용 진입점.
- `state_constants.py`: 트레이드 규칙 기본값, 허용 스테이터스/페이즈 등 상수.
- `state_utils.py`: 타입 보조, 숫자 판별, 중첩 dict 합산 유틸.

또한 `state_results.py`와 `state_schedule.py` 모두 "상태 변경은 facade(`state.py`)에서만"이라는 원칙을 명시한다. 즉, 신규 기능도 같은 분리 원칙을 유지해야 디버깅 비용이 낮다.

## 2) 안전한 상태 접근 규칙

개발 중 상태 관련 버그를 줄이려면 아래 규칙을 기본값으로 사용한다.

1. **쓰기**는 `state_store.transaction()` 안에서만 수행.
   - 중첩 트랜잭션 허용.
   - 최외곽 커밋 시 `validate_game_state()` 실행.
   - 롤백 없음(예외 시 부분 변경이 남을 수 있음).
2. **읽기(라이브 뷰)**는 `state_store.read_state()` 사용.
   - 내부적으로 깊은 read-only wrapper(`_RODict`, `_ROList`)를 제공해 실수로 mutate 못 하게 막음.
3. **내보내기/직렬화용 복사본**은 `state_store.snapshot_state()` 사용.
4. 개발용 초기화는 `reset_state_for_dev()` 사용하되, 트랜잭션 내부에서는 금지됨.

## 3) 스타트업/시즌 전환 체크포인트

`state_bootstrap.py` 기준으로 startup 시점에 한 번만 도는 체크들이 존재한다.

- DB 경로는 `league.db_path`가 **필수** (`_require_db_path`).
- DB 초기화 + GM 프로필 시드 + 스카우트 시드(`ensure_db_initialized_and_seeded`).
- cap auto update가 켜져 있으면 시즌 기준으로 cap 값 재적용(`ensure_cap_model_populated_if_needed`).
- 스케줄 생성 직후 시즌별 1회 컨트랙트 부트스트랩(`ensure_contracts_bootstrapped_after_schedule_creation_once`).
- DB 무결성 검증 1회(`validate_repo_integrity_once_startup`).

실제 facade(`state.py`)에서도 스케줄 재생성 시 cap 적용, 드래프트 픽 시드 보장, 계약 부트스트랩을 연동하므로, 관련 기능 추가 시 해당 체인을 깨지 않게 유지하는 것이 중요하다.

## 4) 스케줄 생성 로직 튜닝 포인트

`state_schedule.build_master_schedule()`는 정규시즌 스케줄을 **순수하게 생성**해 반환한다.

개발자가 자주 확인할 항목:

- 생성 목표: 팀당 82경기, 정확히 41H/41A.
- 하드 제약 예시: 하루 1경기/팀, 3연전 금지, 4-in-5 금지, B2B 상한(`_B2B_MAX`), 블랙아웃(올스타/12월 24일).
- 소프트 최적화: B2B/3-in-4, 리매치 간격, 홈/원정 스트릭, 휴식 불리.
- 탐색 파라미터: `_DAY_BUILD_ATTEMPTS`, `_FULL_SCHEDULE_ATTEMPTS`, `_REPAIR_ITERS`.

스케줄 관련 변경은 아래 공개 함수 중심으로 영향 범위를 통제한다.

- `build_master_schedule(...)`
- `ensure_master_schedule_indices(...)`
- `mark_master_schedule_game_final(...)`
- `get_schedule_summary(...)`
- `days_to_next_game(...)`

## 5) 결과 ingest 계약(GameResultV2)

`state_results.validate_v2_game_result()`가 계약 위반을 즉시 예외로 막는다. 핵심 계약:

- `schema_version == "2.0"`
- `game.phase`는 허용 페이즈 집합 내 값
- `home_team_id != away_team_id`
- `final`과 `teams` 키는 side(`home/away`)가 아니라 팀 ID 2개와 정확히 일치
- 선수 row에는 `PlayerID`, `TeamID` 필수, 그리고 `TeamID`는 해당 팀과 일치

누적 집계는 `_accumulate_player_rows`, `_accumulate_team_game_result`가 담당하며 숫자형 필드만 안전하게 누적한다.

## 6) 개발환경에서 바로 쓰는 점검 루틴

아래 순서로 빠르게 회귀 점검하면 상태 계열 문제를 조기에 찾을 수 있다.

```bash
python -m compileall state_modules
```

```bash
python - <<'PY'
from datetime import date
from state_modules.state_schedule import build_master_schedule, get_schedule_summary

ms = build_master_schedule(season_year=2025, season_start=date(2025, 10, 1), rng_seed=7)
s = get_schedule_summary(ms)
print('total_games=', s['total_games'])
print('status_keys=', sorted(s['status_counts'].keys()))
print('BOS=', s['teams']['BOS'])
PY
```

```bash
python - <<'PY'
from state_modules.state_results import validate_v2_game_result

sample = {
  'schema_version': '2.0',
  'game': {
    'game_id': 'g1', 'date': '2025-10-01', 'season_id': '2025-26', 'phase': 'regular',
    'home_team_id': 'BOS', 'away_team_id': 'LAL', 'overtime_periods': 0, 'possessions_per_team': 95
  },
  'final': {'BOS': 100, 'LAL': 90},
  'teams': {
    'BOS': {'totals': {'PTS': 100}, 'players': [{'PlayerID': 'p1', 'TeamID': 'BOS'}]},
    'LAL': {'totals': {'PTS': 90}, 'players': [{'PlayerID': 'p2', 'TeamID': 'LAL'}]}
  }
}
validate_v2_game_result(sample)
print('ok')
PY
```

## 7) 실무 권장사항(이 패키지 한정)

- 상태를 직접 건드리는 새 로직은 먼저 facade(`state.py`)에 배치하고, `state_modules`는 순수 유틸/검증/보장 함수로 분리.
- 스케줄 생성 파라미터를 조정할 때는 결과 품질(요약 통계) + 생성 실패율(RuntimeError)을 함께 기록.
- startup 1회성 플래그는 `_migrations` 아래에 추가하고, DB path/시즌 단위 키를 명시해 idempotent하게 관리.
- 트레이드 상태 키 확장 시 `state_trade._ensure_trade_state()`에 존재성/타입 보장을 같이 추가.

---

이 문서는 현재 `state_modules/` 코드 구조를 기준으로 작성했으며, 이후 함수 계약이 변경되면 문서도 함께 갱신하는 것을 권장한다.
