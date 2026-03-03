# Retirement 패키지 개발 가이드

이 문서는 `retirement/` 패키지 코드를 기준으로, 오프시즌 은퇴 처리 로직을 빠르게 이해하고 안정적으로 개발/디버깅하기 위한 실무 가이드입니다.

## 1) 구성 요소 한눈에 보기

- `retirement/config.py`
  - 은퇴 판단 모델 가중치와 가드레일(상/하한, 젊은 선수 cap, 엘리트 OVR 패널티)을 `RetirementConfig` dataclass로 관리합니다.
- `retirement/types.py`
  - 입력(`RetirementInputs`)과 출력(`RetirementDecision`) 스키마를 dataclass로 명시합니다.
- `retirement/repo.py`
  - DB 읽기/쓰기 계층입니다.
  - 선수 입력 집계(`list_player_inputs`), 결정 upsert(`upsert_decisions`), 결정 조회(`list_decisions`), 이벤트 적재(`append_retirement_events`)를 담당합니다.
- `retirement/engine.py`
  - 순수 계산 계층입니다.
  - 고려 단계(`consider_prob`)와 최종 결정 단계(`retirement_prob`)의 2단계 확률 모델을 계산합니다.
- `retirement/service.py`
  - 오케스트레이션 계층입니다.
  - 미리보기(`preview_offseason_retirement`)와 실제 반영(`process_offseason_retirement`)을 제공합니다.

## 2) 실제 데이터 플로우

### Preview 경로

1. `_load_or_build_decisions()`가 시즌의 기존 `player_retirement_decisions`를 먼저 조회합니다.
2. 기존 결과가 없으면 `list_player_inputs()`로 입력 데이터를 수집합니다.
3. `attrs_json`에서 `extract_mental_from_attrs(..., keys=AGENCY_CONFIG.mental_attr_keys)`로 멘탈 능력치를 추출합니다.
4. `evaluate_retirement_candidate()`로 선수별 의사결정을 계산합니다.
5. 계산 결과를 `upsert_decisions()`로 저장한 뒤 반환합니다.
6. `preview_offseason_retirement()`는 총 대상 수/고려 수/은퇴 수와 상세 결정을 응답으로 구성합니다.

### Process 경로

1. `meta` 테이블의 `retirement_processed_{season_year}` 키로 idempotency를 확인합니다.
2. 이미 처리된 시즌이면 `skipped=True, reason='already_done'`를 반환합니다.
3. 처리 전이면 decision을 로드한 뒤 `decision='RETIRED'` 선수만 추립니다.
4. 트랜잭션에서 아래 순서로 반영합니다.
   - `contracts` 활성 계약 비활성화 + 상태 `RETIRED` 변경
   - `active_contracts` 제거
   - `roster` 상태를 `retired`로 변경
   - `player_retirement_decisions.processed_at` 업데이트
   - `retirement_events`에 이벤트 append (`INSERT OR IGNORE`)
   - `meta` idempotency 플래그 저장
5. 트랜잭션 이후 `append_transactions()`로 거래 로그를 남기고 `validate_integrity()`를 호출합니다.

## 3) 엔진 계산 로직 핵심 요약

- 나이 팩터: `(age - 29) / 11`을 `[0,1]`로 clamp.
- 팀 소속 여부 팩터: `team_id == 'FA'`면 `1.0`, 아니면 `0.0`.
- 부상 부담(`injury_burden`): 아래 항목의 가중합을 `[0,1]`로 clamp.
  - 현재 부상 상태/심각도
  - 최근 1년 결장일
  - 최근 3년 결장일
  - 최근 3년 중증 부상 수
  - 재부상 누적
  - 영구 능력 하락 누적
- 1단계(고려): `consider_z` → `sigmoid` → `consider_prob`.
- 2단계(최종): `decision_z` → `sigmoid` → `retirement_prob`.
  - 상호작용 항(age×injury, teamless×loyalty, ambition×adaptability)을 포함.
  - `ovr >= elite_ovr_guard`면 `elite_ovr_z_penalty`만큼 감산.
  - 확률은 hard floor/ceiling으로 clamp.
  - `age <= youth_age_guard`면 `youth_prob_cap` 상한 적용.
- 난수는 `stable_u01("retire.consider", ...)`, `stable_u01("retire.final", ...)`을 써서 시즌/선수 기준 결정적(deterministic)입니다.

## 4) 입력 데이터 해석 시 주의점

- 부상 상태는 단순 컬럼이 아니라 `status_for_date()`로 `decision_date_iso` 기준 정규화됩니다.
- `reinjury_count_json`, `perm_drop_json`은 dict 합산(변환 실패 값은 무시)으로 집계됩니다.
- 최근 부상 집계는 `injury_events`에서 `season_year-3`부터 `season_year-1`까지만 조회합니다.
- 최근 1년 결장일(`missed_days_1y`)은 `season_year-1` 데이터만 카운트합니다.

## 5) 개발 시 추천 체크리스트

### A. 모델 튜닝/가중치 수정

- `RetirementConfig` 값 수정 후에는 아래를 함께 확인합니다.
  - 젊은 선수 은퇴율이 `youth_prob_cap`에 과도하게 걸리지 않는지
  - 엘리트 선수(`elite_ovr_guard` 이상) 은퇴 억제가 의도와 맞는지
  - hard floor/ceiling이 통계 분포를 과도하게 자르지 않는지

### B. DB 반영 로직 수정

- `process_offseason_retirement()` 수정 시 반드시 idempotency 보장 여부를 먼저 점검합니다.
- `retirement_events`는 `INSERT OR IGNORE` 기반이므로 event_id 충돌 정책을 변경할 때 `make_event_id` 규칙과 함께 검토합니다.
- 계약/로스터/결정 테이블 업데이트 순서를 바꿀 경우, 중간 실패 시 일관성을 트랜잭션 단위로 재검증합니다.

### C. 디버깅 포인트

- 미리보기 결과 검증: `considered_count`, `retired_count`, `retired_player_ids`.
- 결정 근거 검증: 각 decision의 `inputs` / `explanation` JSON.
- 최종 반영 검증:
  - `contracts.is_active=0`, `status='RETIRED'`
  - `active_contracts` 삭제
  - `roster.status='retired'`
  - `player_retirement_decisions.processed_at` 설정
  - `retirement_events` 및 transaction 로그 적재

## 6) 개발 환경 최적화 팁 (이 패키지 기준)

- **Preview 우선 개발**: 실제 반영 전 `preview_offseason_retirement()` 결과를 먼저 확인하는 흐름으로 작업하면 데이터 훼손 위험을 줄일 수 있습니다.
- **결정적 난수 활용**: `stable_u01` 기반이라 동일 입력/시즌이면 결과가 재현되므로, 회귀 비교에 유리합니다.
- **입력/설명 JSON 활용**: 모델 변경 시 결과값만 보지 말고 `inputs`, `explanation`을 diff해서 어떤 항이 변화를 만들었는지 추적하세요.
- **Idempotency 키 확인 습관**: 반복 실행 테스트 시 `meta.retirement_processed_{season_year}` 상태를 먼저 확인하면 혼동을 줄일 수 있습니다.
