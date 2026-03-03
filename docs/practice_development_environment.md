# Practice 패키지 개발환경 최적화 가이드

이 문서는 `practice/` 패키지의 **현재 코드 구현**만을 기준으로, 개발 중인 NBA 시뮬레이션 게임에서 `practice` 서브시스템을 안전하고 빠르게 개발하기 위한 운영 가이드다.

---

## 1) 패키지 역할 경계(의존성 기준)

`practice`는 경기 사이의 일일 훈련(전술/필름/스크리미지/회복/휴식)을 다룬다.

- `practice/repo.py`: SQLite I/O 전용 레이어(JSON 인코딩/디코딩 포함).
- `practice/service.py`: 비즈니스 로직 + 시뮬레이션 훅 + CRUD 래퍼.
- `practice/ai.py`: 규칙 기반 AUTO 세션 선택.
- `practice/types.py`: normalize 및 강건한 타입 보정, 인텐시티 계산.
- `practice/config.py`: 튜닝 상수(세션 타입, 강도, sharpness/familiarity/AI 임계치).
- `practice/defaults.py`: 기본 플랜/세션 값.

개발 시에는 이 경계를 유지하는 것이 중요하다.

- DB 쿼리 추가/수정: `repo.py`
- 정책/흐름/적용 타이밍 변경: `service.py`
- AUTO 판단 규칙 변경: `ai.py`
- 스키마 방어 및 입력 정규화: `types.py`
- 밸런싱 숫자 조정: `config.py`

---

## 2) 시간/결정론 규칙(필수)

코드에서 `practice`는 다음 원칙을 강하게 사용한다.

- 날짜는 모두 게임 내 ISO(`YYYY-MM-DD`) 기준.
- 호스트 OS 시계에 의존하지 않음.
- 자동 생성 세션은 DB에 저장해( `is_user_set=0`) 이후 동일 날짜에서 결정론 보장.

따라서 개발환경에서도 다음을 권장한다.

- 디버그/테스트 입력에 실시간 `now()` 대신 `date_iso` 기반 값 사용.
- 버그 리포트 템플릿에 `team_id`, `season_year`, `date_iso`, `is_user_set` 포함.

---

## 3) 데이터 계약(빠른 점검용)

### Practice plan

- `mode`: `AUTO` | `MANUAL`
- 유효하지 않은 값은 `AUTO`로 정규화됨.

### Practice session

- `type`: `OFF_TACTICS` | `DEF_TACTICS` | `FILM` | `SCRIMMAGE` | `RECOVERY` | `REST`
- `offense_scheme_key`, `defense_scheme_key`
- `participant_pids` (주로 SCRIMMAGE)
- `non_participant_type` (기본 `RECOVERY`)

정규화 로직이 강하게 방어적으로 작성되어 있으므로, API/CLI/UI에서 들어오는 데이터는 일단 `normalize_session`을 통과시키는 패턴을 유지하는 것이 안전하다.

---

## 4) AUTO 모드 이해(튜닝/디버깅 핵심)

AUTO 세션 선택 우선순위는 다음 순서다.

1. 다음 경기 임박(`days_to_next_game` 임계치 이하) → `RECOVERY`
2. 오펜스/디펜스 familiarity가 낮을 때 → 전술 설치(`OFF_TACTICS`/`DEF_TACTICS`)
3. 저 sharpness 선수가 충분히 많고 시간 여유가 있을 때 → `SCRIMMAGE`
4. 기본값 → `FILM`

힌트(`off_fam`, `def_fam`, `low_sharp_count`, `sharpness_by_pid`)가 일부 비어 있으면, 서비스 레이어가 readiness SSOT를 읽어 보완한다.

운영 팁:

- AUTO 동작이 예상과 다를 때는 AI 함수보다 **힌트 계산 경로**(SSOT에서 decay 적용 후 계산)를 먼저 확인.
- 임계치 변경은 `config.py`에서만 수행하고, 변경 전/후 결과를 같은 `date_iso`로 비교.

---

## 5) SCRIMMAGE 참가자 처리 규칙

`SCRIMMAGE`는 참가자 자동보정 로직이 있다.

- 기존 참가자가 최소 인원 이상이면 최대 인원만 clamp.
- 인원이 부족하면 로스터에서 자동 채움.
- sharpness 맵이 있으면 낮은 sharpness 우선 선발.
- 범위는 `SCRIMMAGE_MIN_PLAYERS`~`SCRIMMAGE_MAX_PLAYERS`.
- 저장된 세션을 읽을 때 `roster_pids`가 제공되면, 해당 로스터에 없는 참가자는 제거(clamp)한다.

즉, 개발환경에서 스크리미지 이슈를 재현할 때는 반드시 `roster_pids` 입력 여부를 고정해서 비교해야 한다.

---

## 6) readiness 연동 포인트(실수 방지)

`apply_practice_before_game`는 시뮬레이션 파이프라인 내에서 오프데이를 순회하며 readiness SSOT를 갱신한다.

- sharpness: 기본 decay + practice delta
- familiarity: decay 후 diminishing gain
- OUT 선수는 전술/스크리미지 이득을 받지 않도록 별도 처리(`RECOVERY`/`REST` 대응)
- 모든 실패는 로그 경고 후 진행(시뮬레이션 중단 방지)

개발 시 체크리스트:

- 변경이 readiness 수치 계산에 미치는 영향을 먼저 검토.
- 예외를 삼키는 지점(logger.warning)이 많으므로 로그 키워드 기반 확인을 루틴화.

---

## 7) 로컬 개발 루틴(권장)

### A. 최소 무결성 체크

```bash
python -m compileall practice
```

### B. 변경 범위 체크

- 상수 수정(`config.py`)만 했는지,
- 정책 수정(`ai.py`, `service.py`)이 포함됐는지,
- DB 스키마/저장 포맷 영향(`repo.py`, session/plan JSON 필드)이 있는지 분리해서 리뷰.

### C. 회귀 점검 시나리오(수동)

- 같은 `(team_id, season_year, date_iso)`에 대해 `resolve_practice_session` 2회 호출 시 동일 결과 유지되는지.
- `MANUAL` 모드일 때 자동 AI 분기가 타지 않는지.
- `SCRIMMAGE`에서 참가자 수/명단 clamp가 기대대로 동작하는지.

---

## 8) 추천 작업 순서(생산성)

1. `config.py` 임계치/상수 확정
2. `ai.py` 규칙 변경
3. `service.py`의 hint 보강/세션 finalize 영향 확인
4. `types.py` normalize 방어 로직 확인
5. `repo.py`로 저장/조회 포맷 확인

이 순서로 작업하면, 정책 변경이 데이터 계약을 깨뜨리지 않는지 빠르게 검증할 수 있다.

---

## 9) 문서 유지 원칙

- 이 문서는 `practice/` 코드와 불일치가 생기면 즉시 업데이트.
- 새 세션 타입/필드가 추가되면:
  - `config.py` 상수,
  - `types.py` normalize,
  - `service.py` finalize 및 readiness 반영,
  - `ai.py` 선택 규칙,
  - 본 문서
  를 한 묶음으로 업데이트.
