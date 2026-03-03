# College 패키지 개발 환경 가이드

이 문서는 `college/` 패키지를 **코드 기준으로만** 빠르게 이해하고, 재현 가능한 개발/검증 루프를 만들기 위한 실무 가이드다.

## 1) 패키지 구조(역할 분리)

- `college/service.py`
  - 오케스트레이션 레이어.
  - 월별 스냅샷, 드래프트 워치, 시즌 종료 선언, 오프시즌 진급/충원 등 상태 변경 로직이 여기 모여 있다.
- `college/generation.py`
  - 팀/선수 생성 로직.
  - 포지션 분포, 신체/나이/재능 샘플링, 초기 월드 생성 함수가 있다.
- `college/sim.py`
  - 시즌 스탯 시뮬레이터.
  - 팀 단위/선수 단위 시즌 누적 지표를 생성한다.
- `college/declarations.py`
  - 드래프트 선언 확률 모델.
  - OVR, 나이, 학년, 잠재력, 시즌 스탯, 클래스 강도, 예상 픽을 입력으로 선언 확률 계산.
- `college/ui.py`
  - 읽기 중심 조회 레이어.
  - API 라우트에서 호출하는 팀/선수/드래프트풀 조회 함수가 위치.
- `college/config.py`
  - 리그 크기, 로스터 정책, 시뮬레이션 계수, 월별 체크포인트 등 튜닝 파라미터 집합.
- `college/names.py`
  - 이름 뱅크 로드 + 중복 회피 이름 생성.
- `college/types.py`, `college/serialization.py`
  - 데이터 구조/직렬화 유틸리티.

## 2) 개발 환경에서 꼭 알아야 하는 런타임 제약

### FastAPI 실행 시 필수 환경변수

- `LEAGUE_DB_PATH`가 없으면 서버 startup에서 즉시 `RuntimeError`를 발생시킨다.
- `app/main.py`에서 startup 시 `state.set_db_path(db_path)` → `state.startup_init_state()`를 호출한다.

즉, College API 개발을 시작하기 전에 **DB 경로가 고정된 환경**을 먼저 만들고 서버를 띄워야 한다.

## 3) College 개발 시 핵심 데이터 흐름

### A. 월드 부트스트랩

- `ensure_world_bootstrapped(db_path, season_year)`
  - 팀이 없으면 `build_college_teams()`로 팀 생성.
  - `generate_initial_world_players(...)`로 초기 선수 풀 생성.
  - player_id는 `allocate_player_ids(...)`로 할당(메타 `seq_player_id` + 양쪽 테이블 스캔 기반).

### B. 시즌 종료 + 선언 생성

- `finalize_season_and_generate_entries(db_path, season_year, draft_year)`
  - `simulate_college_season(...)` 결과를 저장.
  - 클래스 강도(`draft_class_strength`)를 보장/생성.
  - 선언 확률 모델(`declare_probability`)로 `college_draft_entries`를 구성.

### C. 월별 체크포인트(진행 중 스냅샷)

- `run_monthly_watch_and_stats_checkpoints(...)`
  - 월별 누적 스냅샷 재계산 + 드래프트 워치 런 생성 흐름.
- `recompute_college_monthly_stats_snapshot(...)`
  - 동일 `(season_year, period_key, player_id)`에 대해 재현 가능하도록 안정적 seed 기반 계산.
- `recompute_draft_watch_run(...)`
  - `run_id = DY{draft_year}@{period_key}` 형태로 스냅샷 저장.
  - `force=False`면 기존 run 재사용(idempotent).

### D. 오프시즌

- `advance_offseason(db_path, from_season_year, to_season_year)`
  - 학년 진급/졸업, 신입생 충원, 최소 로스터 보장, 하드캡 트리밍 정책을 적용.

## 4) DB 기준으로 보는 College SSOT 테이블

`db_schema/college.py` 기준 주요 테이블:

- `college_teams`
- `college_players`
- `college_player_season_stats`
- `college_team_season_stats`
- `college_draft_entries`
- `draft_class_strength`

추가로 `college/service.py`의 월별 워치 기능은 `draft_watch_runs`, `draft_watch_probs`를 사용한다.

## 5) 튜닝 포인트(개발 중 자주 조정)

`college/config.py`에서 실험할 때 우선순위가 높은 항목:

- 리그/로스터 스케일
  - `COLLEGE_TEAM_COUNT`, `COLLEGE_ROSTER_SIZE`, `COLLEGE_SEASON_GAMES_PER_TEAM`
- 오프시즌 인원 정책
  - `OFFSEASON_FRESHMEN_PER_TEAM`, `OFFSEASON_MIN_ROSTER`, `OFFSEASON_HARD_CAP`
- 선언/클래스 강도 관련
  - `MIN_DRAFT_ELIGIBLE_AGE`, `CLASS_STRENGTH_STD`, `CLASS_STRENGTH_CLAMP`
- 월별 체크포인트 안정성
  - `COLLEGE_MONTHLY_CHECKPOINTS`, `COLLEGE_GAMES_BY_CHECKPOINT_MONTH`, `COLLEGE_SNAPSHOT_NOISE_STD`, `COLLEGE_SNAPSHOT_CLAMPS`

권장 방식:

1. 작은 범위(한두 개 상수)만 변경
2. 월별 스냅샷 + draft watch를 같은 seed 입력으로 재실행
3. 결과 차이를 비교해 영향 범위를 확인

## 6) API 개발 기준점(College 조회 경로)

`app/api/routes/college.py` 기준 공개 라우트:

- `GET /api/college/meta`
- `GET /api/college/teams`
- `GET /api/college/team-detail/{college_team_id}`
- `GET /api/college/players`
- `GET /api/college/player/{player_id}`
- `GET /api/college/draft-pool/{draft_year}`
- `POST /api/college/draft-watch/recompute`

`college/ui.py`는 조회/가공 레이어이므로, 프론트 이슈를 디버깅할 때 service보다 먼저 확인하면 효율적이다.

## 7) 빠른 로컬 검증 루프(코드 변경 직후)

아래 순서만 유지해도 대부분의 회귀를 빠르게 잡을 수 있다.

1. 정적 점검
   - `python -m compileall college`
2. 최소 import/smoke
   - `python - <<'PY'`
     - `import college`
     - `from college import service, ui, generation, sim, declarations, config`
     - `print('ok')`
     - `PY`
3. DB를 사용하는 통합 흐름은 별도 개발 DB 파일에서만 실행
   - (운영/실사용 세이브 DB와 분리 권장)

## 8) 재현성/디버깅 실무 팁

- `service._stable_seed(...)` 패턴을 사용해 월별 워치/확률 계산이 입력 기준으로 고정되도록 되어 있다.
- 이름 중복 이슈는 `names.build_used_name_keys(...)` + `generate_unique_full_name(...)` 경로를 먼저 본다.
- player_id 충돌은 `allocate_player_ids(...)`가 `players` + `college_players` 양쪽을 기준으로 보정하므로, 문제 발생 시 meta(`seq_player_id`)와 실제 max id를 함께 점검한다.

---

이 문서는 현재 코드(`college/*`, `db_schema/college.py`, `app/api/routes/college.py`, `app/main.py`)를 기준으로 작성되었다.
