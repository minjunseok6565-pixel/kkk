# Draft 개발환경 최적화 가이드 (코드 기반)

이 문서는 `draft/` 패키지와 연결된 API/DB 코드를 기준으로, **재현 가능하고 디버깅이 쉬운 개발환경**을 만들기 위한 체크리스트를 정리한다.

## 1) 현재 Draft 시스템 구조(모듈 경계)

`draft/__init__.py` 기준으로 드래프트 시스템은 아래 모듈 단위로 분리되어 있다.

- 순수 계산 계층
  - `standings.py`: 정규시즌 결과 기반 팀 레코드 계산/정렬
  - `lottery.py`: 하위 14팀 대상 top-4 로터리
  - `order.py`: 1R/2R 슬롯 맵핑
- 상태/저장 계층
  - `finalize.py`: order plan 저장/로드, 정산(settlement), turn 생성
  - `pipeline.py`: `lottery -> settlement -> apply` 스텝 오케스트레이션
- 세션/AI/적용 계층
  - `pool.py`: 드래프트 풀 로딩/표현
  - `session.py`: 드래프트 세션 상태머신
  - `ai.py`, `ai_needs.py`: AI 선택 정책 인터페이스/구현
  - `apply.py`: 실제 선수/로스터/계약 반영
- 이벤트 계층(사전 정보)
  - `events.py`: combine/workout
  - `interviews.py`: 인터뷰
  - `withdrawals.py`: 테스트 더 워터스(복귀)
  - `undrafted.py`: 미지명 선수 후속 처리

→ 즉, 개발 시에는 **순수 계산(재현성)**과 **DB 쓰기(부작용)**를 분리해서 테스트하는 구성이 이미 코드에 반영되어 있다.

## 2) 환경 재현성 핵심 포인트 (Seed / Time / Idempotency)

### Seed 고정 규칙

- `pipeline.py`는 로터리/타이브레이크 seed를 `draft_year-1` 기준 오프셋으로 계산한다.
  - `_RNG_SEED_OFFSET = 100_003`
  - `_TIE_BREAK_SEED_OFFSET = 200_017`
- `events.py`, `withdrawals.py`, `undrafted.py`, `ai_needs.py`는 `random.Random(...)` + 안정 해시(`_stable_u32`)를 사용한다.

**권장 개발환경 규칙**
- 동일 DB + 동일 `draft_year` + 동일 seed 입력이면 결과가 동일해야 하므로, 로컬 디버그 시 seed를 명시적으로 고정한다.
- API 테스트 시 요청 바디의 `rng_seed`를 항상 기록(로그/노트)한다.

### 시간 소스 규칙 (OS 시계 fallback 금지)

- `draft/engine.py`는 selection 시각을 in-game 날짜로 강제한다.
- `draft/pipeline.py`도 `tx_date_iso`를 in-game timeline 기준으로 사용하도록 되어 있다.

**권장 개발환경 규칙**
- 테스트 스크립트/수동 호출에서 서버 OS 날짜를 쓰지 말고, state의 in-game 날짜를 기준으로 요청을 보낸다.

### Idempotency(재실행 안전성)

코드에 재실행 안전 장치가 명확히 들어가 있다.

- `run_lottery`: `draft_order_plans` 존재 시 재사용
- `apply_selections`: `draft_results`를 SSOT로 읽고 부분 완료 상태에서도 재개 가능
- `withdrawals.py`: meta key `draft_withdrawals_done_{draft_year}`
- `undrafted.py`: meta key `draft_undrafted_resolved_{draft_year}`

**권장 개발환경 규칙**
- 개발 중 강제 재실험이 필요하면, 관련 테이블/메타키를 초기화한 뒤 다시 실행한다.
- 반대로 재개 시나리오 테스트는 일부 pick만 적용 후 재호출해서 검증한다.

## 3) DB 스키마를 먼저 맞추는 이유

`draft` 로직은 여러 지점에서 테이블 누락 시 `RuntimeError`를 명시적으로 던진다(예: `draft_selections`, `draft_combine_results`, `draft_workout_results` 등).

`db_schema/draft.py` 기준 핵심 테이블:

- 오더/결과
  - `draft_order_plans`
  - `draft_selections` (pre-apply SSOT)
  - `draft_results` (applied SSOT)
- 사전 이벤트
  - `draft_combine_results`
  - `draft_workout_results`
  - `draft_interview_results`
  - `draft_withdrawals`
- 사후 처리
  - `draft_undrafted_outcomes`
- 모니터링/워치
  - `draft_watch_runs`
  - `draft_watch_probs`

**권장 개발환경 규칙**
- 개발 시작 시점에 드래프트 스키마가 반영된 DB인지 먼저 확인한다.
- “코드가 아니라 DB 상태” 때문에 실패하는 케이스를 분리하기 위해, 스키마 검증을 테스트 첫 단계로 둔다.

## 4) API 기반 표준 실행 순서(오프시즌)

`app/api/routes/offseason.py`에는 드래프트 스텝이 명시적으로 분리되어 있다.

권장 순서:

1. `/api/offseason/draft/lottery`
2. `/api/offseason/draft/settle`
3. `/api/offseason/draft/combine`
4. `/api/offseason/draft/workouts`
5. `/api/offseason/draft/interviews`
6. `/api/offseason/draft/withdrawals`
7. `/api/offseason/draft/selections/auto` 또는 `/api/offseason/draft/selections/pick`
8. `/api/offseason/draft/apply`

보조 조회:
- `/api/offseason/draft/interviews/questions`
- `/api/offseason/draft/experts`
- `/api/offseason/draft/bigboard/expert`
- `/api/offseason/draft/bundle`

**권장 개발환경 규칙**
- 단계 건너뛰기 대신 API 순서를 유지해 문제를 분리한다.
- `bundle` 조회를 활용해 세션/풀 상태를 눈으로 확인한다.

## 5) Draft AI 정책 디버깅 포인트

- 기본 정책 키: `needs_potential_gm_v1` (`draft/engine.py`)
- `auto_run_selections`는 `stop_on_user_controlled_team_ids` 없이 `allow_autopick_user_team=False`이면 fail-closed로 예외를 던진다.
- `DraftEngineBundle.to_public_dict()`/`pool` 직렬화는 민감 키(예: OVR/잠재치)를 노출하지 않도록 설계되어 있다.

**권장 개발환경 규칙**
- 유저팀 자동픽 방지 플래그를 테스트 케이스에 반드시 포함한다.
- 디버그 메타를 볼 때도 공개 직렬화/내부 직렬화를 구분해서 확인한다.

## 6) 최소 운영 체크리스트 (실무형)

- [ ] DB에 draft 스키마 존재 확인 (`draft_order_plans`, `draft_selections`, `draft_results` 포함)
- [ ] in-game 날짜가 설정된 state 사용
- [ ] 테스트 실행마다 `draft_year`, `rng_seed` 기록
- [ ] stepwise API 순서 준수 (`lottery -> settle -> ... -> apply`)
- [ ] apply 전후 `draft_selections` / `draft_results` row 수 확인
- [ ] 재실행 테스트(부분 적용 후 재개) 1회 수행

---

## 7) 개발환경 최적화 결론

현재 `draft/` 구현은 이미 다음 특성을 갖고 있어 개발환경 최적화에 유리하다.

1. **단계 분리**: 계산/세션/적용/이벤트가 분리되어 원인 추적이 쉽다.
2. **재현성**: seed 규칙과 안정 해시를 광범위하게 사용한다.
3. **안전한 재시도**: SSOT 테이블(`draft_results`) + idempotent 메타키를 사용한다.
4. **실패 명확성**: 선행 조건 미충족 시 fail-loud 예외를 던진다.

따라서 개발환경의 핵심은 “새 기능 추가”보다, **스키마 확인 + seed/날짜 고정 + 단계별 실행 규율**을 CI/로컬 루틴으로 표준화하는 것이다.
