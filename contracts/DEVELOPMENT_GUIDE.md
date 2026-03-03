# Contracts 개발 환경 가이드 (코드 기반)

이 문서는 `contracts/` 패키지의 현재 코드 구조를 기준으로, **개발/디버깅 생산성을 높이기 위한 실무 가이드**입니다.  
추정이나 기획 가정 없이, 실제 코드에서 확인 가능한 사실만 정리했습니다.

## 1) 패키지 구조와 책임 분리

- `contracts/models.py`
  - 계약 레코드 생성(`make_contract_record`)과 안전한 급여 coercion(`_safe_salary`) 담당.
  - `player_id`, `team_id`는 `schema.normalize_*`를 통해 정규화됨.
- `contracts/options.py`
  - 옵션 레코드 정규화, 옵션 행사/거절 처리, `salary_by_year` 기반 `years` 재계산 담당.
- `contracts/options_policy.py`
  - 오프시즌 옵션 의사결정 정책(기본 정책 + AI TEAM 옵션 확장 정책) 담당.
  - 기본 정책은 안정성 우선으로 예외 시 `EXERCISE` fallback이 많음.
- `contracts/terms.py`
  - 계약 스케줄 해석의 SSOT(remaining years/salary schedule) 역할.
  - 다양한 입력 포맷(dict/객체, alias 필드)을 방어적으로 수용.
- `contracts/free_agents.py`
  - DB 기준 FA 목록/상태 조회 유틸.
- `contracts/offseason.py`
  - 시즌 전환 시 계약 만료/옵션 처리 오케스트레이션.
  - `meta` 키를 이용한 idempotency 가드 존재.
- `contracts/two_way_service.py`
  - 2-way 협상 세션 시작/결정/커밋 플로우.

### 협상 서브패키지 (`contracts/negotiation/`)

- `types.py`: Offer/Decision/Position 데이터 모델 및 payload 정규화.
- `config.py`: 협상 튜닝 파라미터(dataclass).
- `engine.py`: 순수 로직(평가/카운터/accept/reject/walk).
- `store.py`: 메모리 세션 CRUD(state 기반) + 스키마 호환성 보정.
- `service.py`: DB I/O + 상태 전이 오케스트레이션.
- `errors.py`: 안정적인 에러 코드 집합.
- `utils.py`: coercion, 날짜, deterministic random 등 공용 유틸.

---

## 2) 개발 시 반드시 지켜야 할 불변 조건

1. **ID 정규화 선행**
   - `normalize_team_id`, `normalize_player_id`를 거쳐야 함.
   - 레거시 숫자형 player_id 허용 경로(`strict=False`, `allow_legacy_numeric=True`)가 일부 모듈에 존재.

2. **salary 스케줄 해석 SSOT는 `contracts/terms.py`**
   - 남은 연수/합계 계산을 다른 모듈에서 중복 구현하지 말고 `salary_schedule`, `remaining_years`, `remaining_salary_total` 재사용.

3. **오프시즌 처리 중복 실행 방지**
   - `contracts.offseason.process_offseason`는 `meta` 키(`contracts_offseason_done_{year}`)로 중복 실행 방지.
   - 개발 중 수동 재실행 시 이 키의 영향 확인 필요.

4. **협상 엔진은 순수/결정론적이어야 함**
   - `engine.py`는 DB I/O 없이 작동.
   - 경계값 난수는 `stable_u01` 기반 deterministic 처리.

5. **세션 payload는 JSON 직렬화 가능해야 함**
   - `store.py`에서 `last_offer`, `last_counter`, `last_decision`, `agreed_offer` 저장 시 검증.

---

## 3) 빠른 개발 루프를 위한 권장 작업 순서

### A. 순수 함수부터 검증

- 우선 `contracts/terms.py`, `contracts/negotiation/engine.py`, `contracts/options.py`처럼 I/O 없는 함수 단위부터 확인.
- 이유: 재현이 쉽고 디버깅 속도가 빠름.

### B. 그 다음 오케스트레이션 검증

- `contracts/negotiation/service.py`, `contracts/offseason.py`, `contracts/two_way_service.py`는
  DB/state 의존성이 있으므로 마지막에 통합 점검.

### C. 세션 스키마 호환성 확인

- `store._ensure_session_schema`가 구버전 세션도 보정하므로,
  새 필드 추가 시 이 함수에 기본값 반영이 핵심.

---

## 4) 변경 포인트별 체크리스트

### 옵션/계약 기간 로직 변경 시

- [ ] `options.apply_option_decision`의 거절 시점 이후 salary 제거 규칙 유지 여부
- [ ] `options.recompute_contract_years_from_salary`와 `terms.salary_schedule` 해석 일치 여부
- [ ] `options_policy.default_option_decision_policy` fallback 안정성(EXERCISE 우선) 훼손 여부

### 협상 로직 변경 시

- [ ] `types.ContractOffer.from_payload`의 입력 alias/보정 규칙과 충돌 없는지
- [ ] `engine.evaluate_offer`의 `Reason.code`/`meta` 키 호환성 유지
- [ ] `service.submit_contract_offer`의 phase/status 전이(`ACCEPTED`, `WALKED` 등) 유지
- [ ] `store`에 저장되는 payload가 JSON-serializable인지

### 오프시즌/커밋 로직 변경 시

- [ ] `offseason._get_db_path` fail-fast 성격 유지(기본 DB fallback 금지)
- [ ] idempotency key 처리 시점(성공 후 mark) 유지
- [ ] `service.commit_contract_negotiation`에서 `CapViolationError` 변환 구조 유지

---

## 5) 디버깅 우선순위(장애 유형별)

1. **입력 형식 문제**
   - 먼저 `ContractOffer.from_payload` 예외 메시지 확인.
2. **세션 상태 불일치**
   - `status`, `phase`, `valid_until`, `round`, `lowball_strikes` 확인.
3. **정책/의사결정 급변**
   - `options_policy`에서 필요한 컨텍스트(db_path, ui_cache, age/ovr) 누락 여부 점검.
4. **커밋 실패**
   - `NEGOTIATION_COMMIT_FAILED` details에서 cap 위반 코드/메시지 우선 확인.

---

## 6) 코드 읽기 순서(온보딩 최적화)

1. `contracts/terms.py` (SSOT 해석 규칙)
2. `contracts/options.py` / `contracts/options_policy.py` (옵션 처리)
3. `contracts/negotiation/types.py` (입출력 형태)
4. `contracts/negotiation/engine.py` (핵심 판단식)
5. `contracts/negotiation/store.py` (세션 저장 규약)
6. `contracts/negotiation/service.py` (실행 플로우)
7. `contracts/offseason.py`, `contracts/two_way_service.py` (시즌 전환/2-way 특수 플로우)

이 순서는 DB/상태 의존성을 뒤로 미뤄서, 신규 개발자가 빠르게 핵심 규칙을 이해하도록 설계했습니다.

---

## 7) 실무 팁 (현재 코드에서 바로 적용 가능)

- **해석 규칙 중복 금지**: 계약 잔여기간/연봉 합산은 `terms.py` API만 사용.
- **fallback는 보수적으로**: 이 패키지는 전반적으로 “실패 시 파괴적 동작을 피하는” 방향(예: EXERCISE, 0.0, empty list)을 택함.
- **확장 시 config 우선**: 협상 튜닝은 `ContractNegotiationConfig`에 파라미터화해서 실험 가능성을 유지.
- **세션 필드 추가 시 store 먼저 수정**: `_ensure_session_schema`를 갱신하지 않으면 핫리로드/구세션에서 깨질 수 있음.

