# Injury 패키지 개발 환경 가이드

이 문서는 `injury/` 패키지의 **실제 코드 동작**을 기준으로, 개발/디버깅/튜닝 효율을 높이기 위한 작업 기준을 정리한다.

## 1) 공개 API와 호출 타이밍

`injury` 패키지의 공개 엔트리포인트는 다음 3개다.

- `prepare_game_injuries(...)`
- `make_in_game_injury_hook(...)`
- `finalize_game_injuries(...)`

권장 호출 순서:

1. 경기 전: `prepare_game_injuries`
2. 경기 시뮬레이션 중 세그먼트 단위: `make_in_game_injury_hook`가 반환한 hook 호출
3. 경기 후: `finalize_game_injuries`

핵심 목적:

- 경기 전: OUT/RETURNING 상태 반영 + 오프데이(훈련) 부상 처리 + 게임용 준비 데이터 생성
- 경기 중: 확률 기반 게임 내 부상 이벤트 생성(엔진 RNG와 분리된 결정적 시드 사용)
- 경기 후: 이벤트 영속화 + 상태 갱신 + 영구 능력치 하락 반영

## 2) 데이터 저장소(SSOT)와 상태 의미

코드는 부상 관련 사이드이펙트를 SQLite SSOT 테이블에 집중시킨다.

- `player_injury_state` (현재 상태)
- `injury_events` (append-only 이벤트 로그)

상태 판정은 `injury/status.py`의 `status_for_date`를 기준으로 통일된다.

- `OUT`: `on_date < out_until_date`
- `RETURNING`: `out_until_date <= on_date < returning_until_date`
- `HEALTHY`: 그 외

즉, `on_date == out_until_date`인 첫 복귀일은 `OUT`이 아니라 `RETURNING`이다.

## 3) 위험도(확률) 모델 빠른 이해

부상 확률은 공통적으로 hazard 식을 사용한다.

- `p = 1 - exp(-lambda * dt)`

`lambda`는 아래 요인을 곱해서 구성된다.

- 기본 hazard (`BASE_GAME_HAZARD_PER_SEC`, `BASE_TRAINING_HAZARD_PER_DAY`)
- `I_InjuryFreq` 기반 지수 배율
- 에너지/내구도 기반 피로 배율
- 나이 배율
- LT wear 배율
- 재부상 이력 보너스
- (훈련 컨텍스트만) 훈련 강도 배율
- 전역 배율 `GLOBAL_INJURY_MULT`

튜닝 시작점 추천:

- **전체 빈도 조절**: `GLOBAL_INJURY_MULT`
- **경기/훈련 분리 조절**: `BASE_GAME_HAZARD_PER_SEC`, `BASE_TRAINING_HAZARD_PER_DAY`
- **피로 영향 조절**: `FATIGUE_K`, `FATIGUE_POW`, `FATIGUE_MULT_CAP`
- **재부상 체감 조절**: `REINJURY_*` 계열

## 4) 결정성(Determinism)과 재현성

게임 내 부상은 엔진 RNG를 직접 소비하지 않고, `stable_seed(...)` 기반 시드로 `random.Random`을 별도 생성한다.

게임 중 시드 입력 요소:

- `game_id`, possession, quarter, clock, team_id, player_id 등

의미:

- 같은 입력이면 같은 시점/선수에 대해 같은 부상 롤 결과를 재현하기 쉽다.
- 부상 로직 추가/수정이 match engine의 기존 RNG 스트림을 오염시키는 문제를 줄인다.

## 5) prepare 단계에서 반드시 확인할 것

`prepare_game_injuries`는 아래를 수행한다.

- 양 팀 로스터 로딩 및 메타(나이/포지션/파생치) 준비
- 기존 `player_injury_state` 로딩 + 누락 state 기본값 보정
- `last_processed_date`부터 경기 전일까지 오프데이 훈련 부상 처리
- 경기일 상태 정규화(`OUT/RETURNING/HEALTHY`) 및 결과 맵 구성
  - `unavailable_pids_by_team`
  - `attrs_mods_by_pid` (복귀 디버프 스케일 적용)
  - `reinjury_counts_by_pid`
  - `lt_wear_by_pid`
- 훈련 부상 이벤트와 state를 DB에 upsert/insert

운영 팁:

- `last_processed_date`가 누락되면 백필 없이 현재 경기일로 맞춰진다.
- 팀 가용 인원이 `MIN_AVAILABLE_PLAYERS_SOFT` 이하로 내려갈 상황은 훈련 부상을 억제한다.

## 6) in-game hook 단계에서 반드시 확인할 것

hook 내부 안전장치:

- `MAX_INJURIES_PER_GAME_TOTAL`
- `MAX_INJURIES_PER_TEAM_PER_GAME`
- `MAX_SEVERE_INJURY_PER_GAME` + `SEVERE_THRESHOLD`

동작 요약:

- 현재 코트 위 선수만 대상
- 선수별 hazard 확률 계산 후 이벤트 롤
- 부상 발생 시 `game_state.injured_out`, `game_state.injury_events` 갱신
- 한 팀당 한 세그먼트에 1건 발생하면 해당 팀 루프 종료 (PBP 안정성)

## 7) finalize 단계에서 반드시 확인할 것

`finalize_game_injuries`는 raw result에서 이벤트를 추출해 반영한다.

- 우선 경로: `raw_result["game_state"]["injury_events"]`
- 폴백 경로: `raw_result["injury_events"]`

그 다음:

1. 기존 `injury_id` 조회로 idempotency 보장(중복 처리 방지)
2. 신규 이벤트만 `injury_events`에 기록
3. `player_injury_state` 갱신 (`OUT`, 기간/종류/심각도/디버프)
4. 신규 이벤트의 `perm_drop`이 있으면 `players.attrs_json`에 영구 하락 적용 후 OVR 재계산

## 8) 카탈로그/템플릿 변경 작업 규칙

`injury/catalog.py`는 데이터 중심 구조다.

- 먼저 body part 선택(포지션/재부상 바이어스 반영)
- 다음 injury template 선택
- severity/duration/effect 롤

템플릿 변경 시 체크리스트:

- `body_part`는 canonical 값 사용 (`BODY_PARTS`)
- `contexts`는 `game`/`training` 의미와 일치
- `temp_attr_weights` / `perm_attr_weights` 키는 `players.attrs_json`의 실제 능력치 키와 정합
- severity별 duration/returning 범위가 상업적 안정성 정책과 충돌하지 않는지 확인

## 9) 디버깅용 최소 점검 시나리오

### 시나리오 A: 복귀 디버프 스케일

- 조건: 특정 선수 state가 RETURNING 기간에 걸쳐 있음
- 기대: `attrs_mods_by_pid[pid]` 값이 복귀 초반에 크고, `returning_until_date`에 가까워질수록 0으로 감쇠

### 시나리오 B: 중복 finalize 방지

- 동일 `raw_result`로 `finalize_game_injuries`를 2회 호출
- 기대: 두 번째 호출에서 신규 이벤트가 없어 `perm_drop` 이중 적용이 없어야 함

### 시나리오 C: severe cap

- 한 경기에서 severe 1건 발생 후 추가 severe가 발생 가능한 상황 구성
- 기대: 후속 롤에서 `max_severity = SEVERE_THRESHOLD - 1` 캡이 걸려 severe 추가 발생이 억제

## 10) 개발 환경 최적화 제안(현 코드 기준)

- 로깅 강화 포인트
  - `prepare_game_injuries`: 선수별 hazard 입력(energy, age, injury_freq, durability, intensity)
  - `hook`: `p` 값, 시드 구성 요소, 최종 이벤트
  - `finalize`: 신규/기존 injury_id 개수, perm_drop 적용 대상
- 빠른 리그레션 체크
  - 동일 입력에서 hook 결과 재현성(이벤트 수/ID) 비교
  - `status_for_date` 경계일(`out_until_date`) 판정 테스트
- DB 검증 쿼리 습관화
  - 경기 전후 `player_injury_state` 스냅샷 비교
  - `injury_events`에서 기간 겹침/중복 id 확인

---

이 문서는 `injury/` 코드의 현재 구현을 압축한 운영 가이드다. 향후 API/스키마/튜닝 상수가 바뀌면 반드시 함께 갱신해야 한다.
