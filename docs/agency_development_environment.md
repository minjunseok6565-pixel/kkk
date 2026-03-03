# Agency 개발환경 최적화 가이드

이 문서는 `agency/` 패키지의 현재 코드 구조를 기반으로, **개발자가 빠르게 수정/검증/디버깅**할 수 있도록 정리한 실무용 가이드입니다.

## 1) 패키지 구조를 기준으로 한 작업 분리

`agency` 패키지는 역할이 분리된 레이어 구조를 전제로 작성되어 있습니다.

- `repo.py`: DB I/O 전담 (비즈니스 로직 배제)
- `expectations.py`: 역할/레버리지/기대 출장시간 계산 (순수 계산)
- `tick.py`: 월간 상태 업데이트 + 이벤트 생성
- `service.py`: 월간 tick 오케스트레이션 (read/compute/write)
- `options.py`: 선수 옵션(PO/ETO) 의사결정 계산

**권장 개발 방식**

- DB 스키마/쿼리 수정은 `repo.py` 또는 `db_schema/agency.py`에만 반영
- 수식/행동 모델 조정은 `tick.py`, `behavior_profile.py`, `metrics.py` 같은 순수 계산 계층에서 우선 반영
- 시뮬레이션 진입/호출 흐름 수정은 `service.py` 또는 `checkpoints.py`에서만 처리

이 분리를 지키면 회귀 범위를 좁히고 디버깅 시간을 줄일 수 있습니다.

---

## 2) 월간 처리 파이프라인(핵심 실행 경로)

월간 agency 업데이트는 아래 순서를 따릅니다.

1. `checkpoints.maybe_run_monthly_agency_tick(...)`
   - 처리 대상 월 결정
   - 스냅샷에서 월 완료 여부/게임 존재 여부 확인
   - 월별 split, 팀 승률, 팀별 일자 게임 수집
2. `service.apply_monthly_agency_tick(...)`
   - 메타키(`nba_agency_tick_done_YYYY-MM`)로 **idempotent** 처리
   - 로스터/선수/부상/계약/기대치/기존 agency state 로드
   - 선수별 `tick.apply_monthly_player_tick(...)` 실행
   - 결과 state upsert + agency events append
3. 필요 시 interaction 계층
   - `interaction_service.respond_to_agency_event(...)`
   - `interaction_service.apply_user_agency_action(...)`

**개발 팁**

- 월간 로직 문제는 `checkpoints.py -> service.py -> tick.py` 순으로 로그를 좁혀가면 원인 분리가 빠릅니다.
- 사용자 응답/약속/후속 이벤트는 `interaction_service.py`, `responses.py`, `promises.py`를 함께 추적해야 합니다.

---

## 3) 데이터/스키마 기준 개발 체크리스트

Agency SSOT 관련 테이블:

- `player_agency_state`
- `agency_events`
- `agency_event_responses`
- `player_agency_promises`

`db_schema/agency.py`는 다음 특성을 전제로 합니다.

- 날짜는 ISO 문자열 저장 (`YYYY-MM-DD`, 월 키는 `YYYY-MM`)
- 이벤트는 `event_id` PK + append-only 패턴
- 마이그레이션은 additive(파괴적 변경 지양)

**실무 체크리스트**

- 새 상태 필드 추가 시
  1) `db_schema/agency.py` DDL + migrate 반영
  2) `repo.get_player_agency_states` decode 반영
  3) `repo.upsert_player_agency_states` encode 반영
  4) `interaction_service._default_state_for_event` 기본값 반영
- 새 이벤트 타입 추가 시
  1) `config.AgencyConfig.event_types` 등록
  2) `tick.py` candidate 생성/게이팅 확인
  3) `responses.py` 허용 응답/후속 로직 점검

---

## 4) 결정론/재현성 유지 규칙

현재 agency 코드는 재현성을 위해 아래 규칙을 이미 갖고 있습니다.

- `utils.stable_u01(...)`: 입력 기반 결정론적 난수
- `utils.make_event_id(...)`: idempotent 이벤트 ID 생성
- 월간 체크포인트 메타키 사용으로 중복 월처리 방지
- 날짜/월 정규화 유틸(`norm_date_iso`, `norm_month_key`) 사용

**개발 시 권장**

- 새 난수 판단 로직은 `random` 직접 호출 대신 `stable_u01` 기반으로 작성
- append-only 이벤트에는 `make_event_id` 규칙을 그대로 유지
- 날짜 문자열은 비교 전에 정규화 헬퍼를 반드시 통과

---

## 5) 로컬 검증 루틴(빠른 피드백 루프)

### A. 최소 정적 검증

```bash
python -m compileall agency
```

### B. 월간 tick 스모크 실행 포인트

실행 진입점은 아래 2개 중 하나를 사용하면 됩니다.

- 게임 진행 중 자동 트리거 경로: `agency.checkpoints.maybe_run_monthly_agency_tick(...)`
- 강제 보정 경로: `agency.checkpoints.ensure_last_regular_month_agency_tick(...)`

### C. DB 직접 점검 SQL(예시)

```sql
-- 최근 agency 이벤트 타입 분포
SELECT event_type, COUNT(*)
FROM agency_events
GROUP BY event_type
ORDER BY COUNT(*) DESC;

-- 상태 이상치 확인 (0~1 범위 이탈 여부)
SELECT player_id, minutes_frustration, team_frustration, trust
FROM player_agency_state
WHERE minutes_frustration NOT BETWEEN 0 AND 1
   OR team_frustration NOT BETWEEN 0 AND 1
   OR trust NOT BETWEEN 0 AND 1;

-- 활성 약속 due 현황
SELECT status, due_month, COUNT(*)
FROM player_agency_promises
GROUP BY status, due_month
ORDER BY due_month;
```

---

## 6) 코드 읽기 우선순위(온보딩 권장 순서)

신규 기여자가 agency를 빠르게 파악하려면 다음 순서를 권장합니다.

1. `agency/__init__.py` (설계 의도/레이어)
2. `agency/config.py` (튜닝 가능한 파라미터 전반)
3. `agency/types.py` (핵심 입출력 데이터 구조)
4. `agency/checkpoints.py` (언제 돌리는지)
5. `agency/service.py` (무엇을 묶어 실행하는지)
6. `agency/tick.py` (실제 상태 업데이트/이벤트 생성 수식)
7. `agency/interaction_service.py`, `agency/responses.py`, `agency/promises.py` (유저 상호작용/약속 사이클)

---

## 7) 튜닝 작업 시 안전 가이드

튜닝은 `AgencyConfig` 하위 파라미터(예: frustration/event/stance/negotiation/options)로 흡수되도록 유지하는 것이 좋습니다.

- 수치를 코드 상수로 박기보다 config 필드로 승격
- 새로운 조건 분기는 meta/payload에 근거값을 남겨 사후 분석 가능하게 구성
- 이벤트 임계값 변경 시 cooldown/샘플 게이팅(`min_games_*`, month split 가중)과 함께 조정

이 원칙을 지키면 시뮬레이션 밸런스를 흔들지 않고 반복 튜닝하기 쉽습니다.
