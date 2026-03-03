# Training 패키지 개발환경 가이드

이 문서는 `training/` 패키지 코드를 기준으로, **개발/디버깅 환경을 빠르게 재현**하기 위한 실무용 노트입니다.

## 1) 시스템 구조 요약

- `training` 패키지는 팀/선수 훈련 계획, 오프시즌 성장, 월간 성장 틱을 담당합니다. (`training/__init__.py` 모듈 설명)  
- 영속 데이터는 `db_schema.training`에서 만드는 SQLite 테이블(`team_training_plans`, `player_training_plans`, `player_growth_profile`)에 저장됩니다.  
- 실제 선수 능력치 SSOT는 `players.attrs_json`입니다.  

## 2) 데이터/스키마 전제

### 2-1. 테이블
`db_schema/training.py` 기준으로 다음 테이블이 필요합니다.

- `team_training_plans(team_id, season_year, plan_json, created_at, updated_at)`
- `player_training_plans(player_id, season_year, plan_json, is_user_set, created_at, updated_at)`
- `player_growth_profile(player_id, ceiling_proxy, peak_age, decline_start_age, late_decline_age, created_at, updated_at)`

### 2-2. 스키마 적용 순서
- `db_schema/init.py`에서 `core` 이후 `training` 순서로 적용되도록 되어 있습니다.
- 이유: `player_training_plans`, `player_growth_profile`가 `players.player_id` FK를 참조합니다.

## 3) 실행 환경 최소 조건

`app/main.py` 기준:

- 서버 부팅 전 `LEAGUE_DB_PATH` 환경변수가 반드시 필요합니다. 없으면 startup에서 예외를 발생시킵니다.
- API 라우터에 `training.router`가 포함되어 있어 `/api/training/*` 엔드포인트가 활성화됩니다.
- `NBA_SIM_ADMIN_TOKEN`이 설정된 경우, `/api/*`에 대한 `POST`는 `X-Admin-Token` 헤더가 필요합니다.

## 4) API 기반 개발 루프 (권장)

`app/api/routes/training.py`에 이미 준비된 엔드포인트를 이용하면 수동 DB 조작 없이 검증이 가능합니다.

### 4-1. 팀 플랜
- 조회: `GET /api/training/team/{team_id}`
- 저장: `POST /api/training/team/set`

### 4-2. 선수 플랜
- 조회: `GET /api/training/player/{player_id}`
- 저장: `POST /api/training/player/set`

### 4-3. 요청 스키마
- `app/schemas/training.py` 기준
  - Team: `team_id`, `season_year?`, `focus?`, `intensity?`, `weights?`
  - Player: `player_id`, `season_year?`, `primary?`, `secondary?`, `intensity?`

## 5) 코드 이해 포인트 (변경 전 필수)

### 5-1. 정규화/입력 방어
- `training/types.py`에서 intensity/category alias를 정규화합니다.
- 잘못된 값은 기본값(`MED`, `BALANCED`)으로 귀결됩니다.

### 5-2. 기본 플랜 생성
- 팀 기본값: `BALANCED + MED`
- 선수 기본값: 카테고리 평균이 가장 낮은 영역을 primary/secondary로 선택, 편차가 작으면 `BALANCED`

### 5-3. 성장 적용 핵심
`training/growth_engine.py`의 `apply_growth_tick`:

- 시드: `stable_seed("growth_tick", tick_kind, tick_id, player_id)`로 결정적 RNG
- 입력 요소:
  - 팀/선수 강도 blend
  - 연령 성장/감퇴 곡선
  - 출장시간 factor
  - ceiling 근접 감쇠
  - 멘탈 기반 drive/stability
- 결과:
  - 포지티브/네거티브 포인트 분배 후 attrs 수정
  - body/관계 제약 함수 적용
  - ceiling 초과 시 양의 변화 일부를 되돌리는 guard loop 수행

### 5-4. 오케스트레이션
`training/service.py`:

- `_apply_leaguewide_growth_tick`가 active roster 전체를 순회하며
  - 계획 캐시 로드
  - 성장 프로필 upsert
  - `apply_growth_tick` 수행
  - `players.age/ovr/attrs_json` 갱신
- 월간/오프시즌 모두 meta key로 idempotent 처리
  - 월간: `nba_growth_tick_done_{YYYY-MM}`
  - 오프시즌: `nba_offseason_growth_done_{to_season_year}`

### 5-5. 부상 연동
- 월간: 해당 월 부상 OUT 일수 비율만큼 성장 배수(0..1) 감소
- 오프시즌: 현재 OUT 상태면 성장 배수 0 처리
- 부상 테이블 이상 시 전체 실패 대신 기본 배수 1.0으로 fallback

### 5-6. 자동 월간 틱 트리거
`training/checkpoints.py`:

- 현재 달 진입 시 이전 달(M-1) 정규시즌 완료 여부를 보고 월간 틱 실행
- 마지막 정규시즌 달 누락 방지를 위한 `ensure_last_regular_month_tick` 제공

## 6) 디버깅 체크리스트

### 6-1. DB 상태 확인 SQL

```sql
SELECT * FROM team_training_plans WHERE season_year = ?;
SELECT * FROM player_training_plans WHERE season_year = ?;
SELECT * FROM player_growth_profile LIMIT 20;
SELECT key, value FROM meta WHERE key LIKE 'nba_%growth%';
```

### 6-2. 성장 틱 결과 확인 포인트
- `apply_monthly_growth`/`apply_offseason_growth` 반환값의
  - `players_updated`
  - `avg_delta_proxy`, `max_gain_proxy`, `max_drop_proxy`
  - `top_gainers`, `top_decliners`

### 6-3. 재현성 점검
- 동일 `tick_kind/tick_id/player_id` 조합이면 RNG 시드가 동일하므로, 입력 상태가 같을 때 결과 재현성이 있어야 합니다.
- 비재현 이슈는 보통 입력(`attrs`, minutes, plan, injury state, now/tick_id`) 차이에서 발생합니다.

## 7) 튜닝 작업 권장 절차

1. `training/config.py`의 상수만 먼저 조정합니다. (엔진 로직 변경 전)
2. 같은 저장 상태에서 월간/오프시즌 결과 지표(`avg_delta_proxy` 등)를 비교합니다.
3. 필요 시 `training/mapping.py`의 카테고리-키 연결을 조정합니다.
4. 마지막에만 `growth_engine.py` 수식 로직을 수정합니다.

이 순서를 따르면 영향 범위를 작게 유지할 수 있습니다.

---

## 참고 소스 (문서 작성 기준)
- `training/__init__.py`
- `training/types.py`
- `training/defaults.py`
- `training/mapping.py`
- `training/config.py`
- `training/growth_profile.py`
- `training/growth_engine.py`
- `training/service.py`
- `training/checkpoints.py`
- `training/repo.py`
- `db_schema/training.py`
- `db_schema/init.py`
- `app/main.py`
- `app/api/router.py`
- `app/api/routes/training.py`
- `app/schemas/training.py`
