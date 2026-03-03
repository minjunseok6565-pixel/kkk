# Fatigue 패키지 개발 가이드

이 문서는 `fatigue/` 패키지의 실제 코드만을 근거로, 개발/디버깅/튜닝 시 바로 활용할 수 있도록 정리한 운영 문서입니다.

## 1) 패키지 목적과 공개 API

`fatigue`는 **게임 간 피로 상태(ST/LT)를 영속화**하고, 경기 시작 전 `Player.energy`/`energy_cap`을 주입한 뒤 경기 종료 후 결과를 반영해 다시 저장합니다.

- 공개 진입점
  - `season_year_from_season_id(season_id)`
  - `prepare_game_fatigue(...)`
  - `finalize_game_fatigue(...)`
- 공개 타입
  - `PreparedPlayerFatigue`
  - `PreparedTeamFatigue`
  - `PreparedGameFatigue`

## 2) 데이터 모델과 핵심 개념

### 영속 상태 (`player_fatigue_state`)

`repo` 계층이 다루는 상태는 다음 3개입니다.

- `st`: 단기 피로 (0..1)
- `lt`: 장기 피로/누적 wear (0..1)
- `last_date`: 마지막 확정 날짜 (`YYYY-MM-DD`)

`fatigue.repo`는 읽기/쓰기 모두에서 방어적으로 clamp/date 정규화를 수행합니다.

### 경기 전/후 파이프라인

1. `prepare_game_fatigue`
   - 저장된 `st/lt/last_date` 로드
   - 휴식 회복 적용 (rest units + recovery multiplier)
   - 컨디션 기반 시작 에너지(`start_energy`) 계산
   - `Player.energy`, `Player.energy_cap` 주입
   - `PreparedGameFatigue` 반환

2. `finalize_game_fatigue`
   - 엔진 결과(`minutes_played_sec`, `fatigue`) 읽기
   - 분당/역할/템포/스태미나/종료 에너지 기반 `st_gain` 계산
   - `lt_gain` 계산 후 `st/lt` 업데이트
   - `last_date = prepared.game_date_iso`로 upsert

## 3) 수식/튜닝 지점 빠른 요약

모든 상수는 `fatigue/config.py`에서 관리됩니다.

### 회복

- 휴식 단위
  - 같은 날/역행: `0`
  - 백투백(next day): `OVERNIGHT_REST_UNITS`
  - 2일 이상: `OVERNIGHT_REST_UNITS + (delta_days - 1)`

- 회복식(지수감쇠)
  - `ST' = ST * exp(-ST_REC_RATE * rest_units * R)`
  - `LT' = LT * exp(-LT_REC_RATE * rest_units * R)`

- `R`(회복 배수) 구성
  - 지구력 기반 factor (`ENDURANCE`)
  - 나이 기반 factor (`AGE_REC_*` sigmoid)
  - 훈련 강도 factor (`intensity_mult ** (-TRAIN_REC_POW)`)
  - 최종 clamp: `[RECOVERY_MULT_MIN, RECOVERY_MULT_MAX]`

### 시작 에너지

- 컨디션: `1 - ST - LT_WEIGHT * LT`
- 시작 에너지: `[START_ENERGY_MIN, 1.0]`로 clamp
- 에너지 캡: `min(1.0, start_energy + CAP_BONUS)`

### 경기 후 누적

- 분량 계수: `(minutes / MIN_REF) ** MIN_EXP`
- 템포 계수: `((tempo / TEMPO_REF) ** TEMPO_EXP)`를 `[TEMPO_LO, TEMPO_HI]`로 clamp
- 역할 계수: `ROLE_MULT[handler|wing|big]`
- 스태미나 계수: `FAT_CAPACITY` 기반 lerp(`STAM_LO..STAM_HI`)
- 종료 패널티: 출장(`minutes > 0`) 시 `END_PEN_W * (1 - end_energy)` 가산

- `lt_gain`:
  - `st_gain * LT_GAIN_RATIO * (intensity_mult ** LT_TRAIN_POW) * lt_age_mult`
  - `lt_age_mult`는 `AGE_LT_*` sigmoid 기반

## 4) training/practice 연동 포인트

`prepare_game_fatigue`는 개인 플랜과 팀 practice를 혼합해 `intensity_mult`를 만듭니다.

- 개인 강도: `training_repo.get_player_training_plan` + `training.types.intensity_multiplier`
- 팀 강도: `resolve_practice_session` + `practice.types.intensity_for_pid`
- 혼합: `training.config.TEAM_INTENSITY_SHARE`, `PLAYER_INTENSITY_SHARE`

### 구현상 중요한 동작

- `last_date is None` 또는 경기 전날까지만 기록된 경우(`dd <= 1`) 팀 practice 평균은 `1.0`.
- 오래된 공백 기간은 `max_off_days = 21`로 계산 창을 제한.
- 팀 practice 강도는 로그 평균(기하평균)으로 합산.

## 5) DB 계층 사용 규칙

### `get_player_fatigue_states`

- 입력 pid를 문자열화 + 중복 제거 후 `IN` 조회
- 누락 row는 반환 dict에 없음
- `st/lt`는 0..1 clamp, `last_date`는 `YYYY-MM-DD` 정규화

### `upsert_player_fatigue_states`

- invalid row는 조용히 skip
- `created_at`은 insert 시만, `updated_at`은 항상 갱신
- `ON CONFLICT(player_id)` 기반 upsert

## 6) 안전성/장애 허용 동작

코드가 의도적으로 no-op 처리하는 구간:

- `finalize_game_fatigue` 입력 payload가 기대 형태가 아니면 warning 후 return
- per-team fatigue/minutes 맵 누락 시 해당 팀만 skip
- `Player.energy`/`energy_cap` 주입 실패 시 예외 삼키고 진행
- age 미존재 시 players 테이블 fallback

운영 관점에서는 **조용한 skip**이 데이터 누락을 숨길 수 있으므로, 로그 모니터링이 중요합니다.

## 7) 개발 체크리스트 (변경 시)

1. `fatigue/config.py` 상수 변경 시
   - 회복(`prepare`)과 누적(`finalize`) 양쪽 영향 범위를 함께 검토
   - `START_ENERGY_MIN`/`CAP_BONUS` 변경은 match engine 체감에 직접 영향

2. `prepare_game_fatigue` 변경 시
   - `Prepared*` 구조와 `finalize_game_fatigue` 입력 계약 유지 확인
   - lineup pid 수집/순서 결정성이 깨지지 않는지 확인

3. `finalize_game_fatigue` 변경 시
   - `raw_result.game_state.fatigue`, `minutes_played_sec` 키 계약 유지
   - `minutes` 단위(초→분) 변환 유지

4. `repo` 변경 시
   - clamp/date 정규화 제거 금지
   - upsert 시 `updated_at` 갱신 보장

## 8) 로컬 검증 커맨드

아래는 패키지 단위 최소 정합성 확인에 유용합니다.

```bash
python -m compileall fatigue
```

추가로 경기 시뮬레이션 경로에서 다음을 점검하면 회귀를 빠르게 찾을 수 있습니다.

- 경기 전 `Player.energy`가 `[START_ENERGY_MIN, 1.0]` 범위인지
- `energy_cap >= energy` 불변식이 유지되는지
- 경기 후 `player_fatigue_state.last_date`가 경기 날짜로 갱신되는지

---

문서 범위: `fatigue/config.py`, `fatigue/repo.py`, `fatigue/service.py`, `fatigue/__init__.py` 기준.
