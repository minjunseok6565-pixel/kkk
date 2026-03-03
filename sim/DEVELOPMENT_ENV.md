# sim 패키지 개발 환경 가이드 (코드 기반)

이 문서는 **`sim/` 패키지 내부 코드만 근거로** 개발 루프를 빠르게 만들기 위한 가이드입니다.
목표는 “한 경기 시뮬레이션 파이프라인을 안전하게 반복 실행하고, 실패를 빠르게 관찰/분리”하는 것입니다.

---

## 1) 먼저 알아야 할 실행 진입점

`sim/`에서 실질적인 진입점은 아래 3개입니다.

- `advance_league_until(...)`: 마스터 스케줄 기반으로 날짜 범위를 자동 시뮬레이션.
- `simulate_single_game(...)`: 단일 경기 시뮬레이션(스케줄 엔트리 매칭 필수).
- `run_simulated_game(...)`: phase/game_id를 직접 받는 공통 러너(결과 ingest 옵션 포함).

핵심적으로 세 함수 모두 `matchengine_v3.sim_game.simulate_game(...)`를 호출하고,
DB 기반 로스터 어댑터(`build_team_state_from_db`)를 통해 `TeamState`를 만들며,
최종적으로 v2 결과로 어댑트 후 상태에 ingest합니다.

---

## 2) 개발 환경 최적화 포인트 (코드에서 직접 확인되는 사실)

### A. “실패해도 경기 자체는 진행”되는 구조를 활용

`league_sim.py`와 `match_runner.py`는 다음 서브시스템(피로도/부상/레디니스/월간 tick)을
대부분 `try/except + warning 로그`로 감싸 두었습니다.

- 준비 단계 실패: `*_PREPARE_FAILED`
- 적용/마무리 단계 실패: `*_APPLY_FAILED`, `*_FINALIZE_FAILED`
- 월간 체크포인트 실패: `MONTHLY_GROWTH_TICK_FAILED`, `MONTHLY_AGENCY_TICK_FAILED`

즉, 로컬 개발에서는 “전체 파이프라인을 한 번에 돌리되 warning 로그로 깨진 지점을 추적”하는 방식이 가장 생산적입니다.

### B. 스케줄이 SSOT(Source of Truth)

`simulate_single_game(...)`는 `(date, home, away)`에 맞는 마스터 스케줄 엔트리를 반드시 찾습니다.
따라서 단일 경기 디버깅 시에도,
- 날짜
- 홈/어웨이 방향
- 이미 final 상태 여부
를 먼저 확인해야 빠릅니다.

### C. `repo.init_db()`가 idempotent로 반복 호출됨

게임 실행마다 DB 스키마 보정이 들어가도록 설계되어 있어,
초기 개발 단계에서 “마이그레이션 누락 때문에 즉시 중단”되는 상황을 줄여줍니다.

### D. 로스터 어댑터는 안전장치가 많음

- `exclude_pids` 적용 후 5인 미만이면 exclusion 무시(경기 가능성 보장)
- 임시 attribute modifier는 0~99 clamp
- tactics preset 로딩 실패 시 safe no-op

따라서 디버깅 시 “왜 특정 exclusion이 반영 안 됐는지”를 체크할 때는
**5인 미만 안전장치**를 우선 의심하면 됩니다.

---

## 3) 추천 개발 루프

### 루프 1: 단일 경기 파이프라인 확인

1. 대상 경기의 `date/home/away`를 스케줄과 동일하게 맞춤
2. `simulate_single_game(...)` 실행
3. warning 로그 키워드(`*_FAILED`) 중심으로 병목 파악
4. 필요 시 동일 경기 재실행 전 `status == final` 여부 확인

### 루프 2: 날짜 범위 자동 시뮬레이션 확인

1. `advance_league_until(target_date_str, user_team_id=...)` 실행
2. 월간 tick이 있는 달 경계(YYYY-MM)에서 warning 로그 확인
3. 사용자 팀 경기 skip 의도 동작(`user_team_id`) 확인

### 루프 3: phase별 공통 러너 검증

1. `run_simulated_game(...)`에 `phase`, `game_id`, `persist` 조합을 달리하여 실행
2. `persist=False`로 엔진 결과 확인 후, `persist=True` ingest 비교
3. regular phase에서는 two-way 출전 기록 로직이 추가 실행되므로 로그 분리 확인

---

## 4) 디버깅 시 반드시 볼 관찰 포인트

- `season_year` 파싱 실패 여부 (`SEASON_YEAR_PARSE_FAILED`)
- readiness/fatigue 준비 결과가 실제 TeamState에 반영됐는지
  - attrs modifier
  - tactics multiplier
- 스케줄 매칭 오류 메시지
  - 날짜에 경기 없음
  - 홈/어웨이 뒤집힘
  - 이미 final
- 결과 ingest 경로
  - `ingest_game_result(...)` 호출 여부
  - `run_simulated_game(..., persist=False)`일 때 `game_obj`가 `None`인지

---

## 5) `sim/` 작업 시 권장 체크리스트

- [ ] 변경 함수가 스케줄 SSOT 규칙을 깨지 않는가?
- [ ] 예외가 상위로 전파되어 경기 전체를 중단시키지 않는가?
- [ ] 로스터 5인 미만 방지 안전장치와 충돌하지 않는가?
- [ ] tactics preset / context 병합 규칙(명시 입력 우선)을 유지하는가?
- [ ] 월간 tick 호출 위치가 중복/누락되지 않는가?

---

## 6) 코드 위치 빠른 참조

- 리그 자동/단일 시뮬레이션: `sim/league_sim.py`
- 공통 매치 러너: `sim/match_runner.py`
- DB→TeamState 변환/전술 preset/역할 배정: `sim/roster_adapter.py`

이 세 파일을 함께 열고 보는 것이 `sim/` 개발 생산성을 가장 높입니다.
