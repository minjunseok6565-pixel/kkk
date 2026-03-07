# 날짜 배치 기반 병렬 계산 + 직렬 Write 구현 계획

## 1) 목표와 비목표

### 목표
- **성능 개선 목표**: 날짜별 다경기 구간에서 "계산" 단계만 병렬화해 요청 지연 시간을 줄인다.
- **안전성 목표**: 경기 전/후 write는 직렬 유지하여 데이터 정합성 리스크를 최소화한다.
- **행동 동일성 목표**: 기존 경기 처리 순서 규칙(유저 경기 우선, 같은 날짜 타 경기 후처리)을 유지한다.

### 비목표
- 게임 로직/밸런스/확률 모델 변경
- DB 스키마 변경
- 세이브 호환/마이그레이션 전략
- API 응답 스키마 확장(필수 상황 제외)

---

## 2) 현재 파이프라인 기준 정리

현재 `sim/league_sim.py`는 다음 단계가 분리되어 있다.
- `_prepare_match_runtime(...)` : 경기 전 준비(일부 write 포함)
- `_compute_match_raw(...)` : 엔진 계산(병렬 후보)
- `_finalize_match_side_effects(...)` : 경기 후 write
- `_adapt_and_ingest_result(...)` : 결과 반영/스케줄 final 처리

핵심 원칙:
- **prepare/finalize/ingest는 직렬 유지**
- **compute만 병렬 실행**

---

## 3) 구현 전략 (날짜 배치 단위)

날짜별 처리 단위를 만들고, 같은 날짜의 비유저 경기 묶음을 다음 순서로 처리한다.

1. `pre` 직렬 단계
   - 날짜의 경기 목록을 기존 deterministic 순서(기존 `by_date[day]` 순서)로 순회
   - 각 경기마다 `_prepare_match_runtime(...)` 실행
   - 결과를 `PreparedJob` 리스트로 메모리에 보관

2. `compute` 병렬 단계
   - `PreparedJob` 리스트를 worker pool로 넘겨 `_compute_match_raw(...)`만 병렬 실행
   - 결과를 `(game_id, raw_result)`로 수집
   - 실패한 작업은 예외를 캡처하여 별도 기록

3. `post` 직렬 단계
   - **원래 순서대로** 작업을 다시 순회
   - 각 경기마다 `_finalize_match_side_effects(...)` 실행
   - 이어서 `_adapt_and_ingest_result(...)` 실행

유저 경기일 규칙:
- 유저 경기는 기존처럼 먼저 단독 처리
- 동일 날짜의 타 경기는 위 날짜 배치(직렬 pre → 병렬 compute → 직렬 post)

---

## 4) 파일별 수정 계획

## A. `sim/league_sim.py` (핵심)

### A-1. 내부 데이터 구조 추가
- `PreparedJob` (dataclass)
  - 필드 예시:
    - `game_id`, `game_date`, `home_team_id`, `away_team_id`
    - `context`
    - `runtime` (`_PreparedMatchRuntime`)

### A-2. 날짜 배치 실행 헬퍼 추가
- `_prepare_day_jobs_serial(...) -> list[PreparedJob]`
  - 입력: 날짜, 해당 날짜 경기 엔트리 목록, tactics/league_context
  - 처리: `_prepare_match_runtime`를 순차 호출하여 job 리스트 생성

- `_compute_day_jobs_parallel(...) -> dict[game_id, raw_result]`
  - 입력: `PreparedJob` 리스트
  - 처리: worker pool(`concurrent.futures`)로 `_compute_match_raw` 병렬 실행
  - 출력: game_id 기준 raw 결과 맵
  - 예외 정책: 실패한 게임은 즉시 상위로 올리거나(엄격 모드), 로그 + fallback 직렬 재계산(안전 모드) 중 하나를 고정 채택

- `_finalize_day_jobs_serial(...) -> list[dict]`
  - 입력: jobs + raw 결과 맵
  - 처리: 원래 순서대로 `_finalize_match_side_effects` 후 `_adapt_and_ingest_result`
  - 출력: ingest된 game_obj 리스트

### A-3. 기존 진입점 최소 변경
- `advance_league_until(...)`
  - 날짜 루프를 유지하되, "해당 날짜 비유저 경기"를 하루 단위로 모아 위 3단계 헬퍼로 실행
  - 기존과 동일하게 `set_current_date(target_date_str)` 수행

- `progress_next_user_game_day(...)`
  - 유저 경기 단독 처리 로직 유지
  - 같은 날짜 타 경기 처리 호출 경로를 날짜 배치 헬퍼 사용으로 교체

### A-4. 동시성 안전 파라미터
- 모듈 상수 추가(초기 보수적 기본값)
  - `DAY_SIM_MAX_WORKERS = min(4, os.cpu_count() or 1)`
  - `DAY_SIM_ENABLE_PARALLEL = True`
- 장애 대응을 위한 fallback 플래그
  - 병렬 compute 실패 시 직렬 compute 재시도 여부

---

## B. `app/api/routes/sim.py` (선택적, 최소 변경 원칙)

기본적으로 수정 불필요.
- API 스키마/응답 형식 유지
- 내부 `advance_league_until`, `progress_next_user_game_day` 호출 결과만 그대로 사용

필요 시(운영 가시성 목적) 최소 로그만 추가:
- "day_parallel_batch_count", "day_parallel_workers" 등 디버그 로그

---

## C. 테스트 파일(신규)

### C-1. `tests/sim/test_day_batch_parallel_pipeline.py` (신규)
목표: "계산 병렬 + write 직렬" 계약 검증

검증 항목:
1. 호출 순서 보장
   - prepare는 경기 순서대로 호출
   - finalize/ingest도 경기 순서대로 호출
2. compute 병렬화 검증
   - compute 함수가 복수 워커로 실행됨(모킹으로 동시 실행 흔적 검증)
3. 유저 경기일 규칙
   - 유저 경기 선처리 후 같은 날짜 타 경기 배치 처리
4. 실패 fallback
   - 특정 경기 compute 실패 시 정책대로 처리(직렬 재시도 또는 전체 중단)

테스트 방식:
- 실제 엔진 계산 대신 `_prepare_match_runtime`, `_compute_match_raw`, `_finalize_match_side_effects`, `_adapt_and_ingest_result`를 monkeypatch해 호출 순서/횟수/입력만 검증

---

## 5) 리스크 억제 방안 (중요)

### 리스크 1: write 경쟁/락 경합
- 억제: prepare/finalize/ingest 절대 직렬 유지
- compute 단계에서 DB write 금지(계약 수준으로 문서화)

### 리스크 2: 비결정적 반영 순서
- 억제: post 단계는 원래 게임 순서(`by_date` 순서) 강제
- 결과 맵은 조회용으로만 쓰고 반영 순서는 별도 리스트로 고정

### 리스크 3: 월간 tick 중복/순서 이슈
- 억제: tick은 날짜당 1회만 실행되도록 day 루프 상단에서 수행(현행 cache 의미 유지)
- compute 병렬 워커에서는 tick 절대 호출 금지

### 리스크 4: 병렬 실패 시 전체 중단/부분 반영 혼란
- 억제: "pre 완료 + compute 일부 실패" 시 정책 명확화
  - 권장: 실패 경기 직렬 재시도 후 계속 진행(안전 우선)
  - 재시도 실패 시 해당 날짜 처리 중단 + 명확한 예외 반환

### 리스크 5: 리소스 과다 사용
- 억제: worker 수 상한 보수적 설정(기본 2~4)
- 날짜당 경기 수가 적으면 병렬 우회(예: 1경기면 직렬)

---

## 6) 단계별 구현 순서 (안전 우선)

1. 날짜 배치 헬퍼 3종 추가(prepare serial / compute parallel / post serial)
2. `advance_league_until`에 하루 단위 배치 적용
3. `progress_next_user_game_day`의 same-day other games 경로 배치 적용
4. 병렬 실패 fallback 정책 구현
5. 단위 테스트 추가 및 순서 계약 검증
6. 로깅/메트릭 최소 추가

---

## 7) 검증/테스트 계획

- 정적/기본 검증
  - `python -m py_compile sim/league_sim.py`
- 단위 테스트
  - 신규 테스트 파일로 순서 보장/병렬 수행/fallback 확인
- 회귀 시나리오
  - 유저 경기 없는 날짜 다수 진행
  - 유저 경기일(유저 경기 + 타 경기) 진행
  - 동일 날짜 소수 경기/다수 경기
  - compute 강제 예외 주입

---

## 8) 변경 통제 원칙

- 수정 범위를 `sim/league_sim.py` + 신규 테스트 파일로 제한
- API/DB 스키마/도메인 수식 변경 금지
- 기존 로그 키 변경 최소화(필요 시 추가만)
- 성능 개선 외 의도되지 않은 리팩터링 금지

