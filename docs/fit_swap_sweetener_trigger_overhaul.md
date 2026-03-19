# Fit-swap / Sweetener 트리거 로직 공격적 전환 설계

## 목표
- **즉시 적용(점진/마이그레이션 없음)**으로 fit-swap/sweetener 트리거를 다음 정책으로 전환한다.
  - 제안 제출자(proposer/initiator) 입장: `REJECT`, `COUNTER` 모두 트리거 유지
  - 제안 수락자(acceptor/recipient) 입장: `REJECT`에서만 트리거
- 목적:
  - fit-swap/sweetener 과도 트리거 완화
  - 불필요한 예산(검증/평가/repair) 소모 감소
  - 동일 예산 대비 제안 생성 건수 증가
  - 초안 단계에서 제출자 이익 우선

---

## 현재 동작 요약 (문제 정의)
- `maybe_apply_fit_swap(...)`:
  - seller 또는 buyer 어느 쪽이든 `REJECT/COUNTER + FIT_FAILS`면 receiver로 잡고 발동 가능.
- `maybe_apply_sweeteners(...)`:
  - seller 또는 buyer 어느 쪽이든 `REJECT/COUNTER` + close corridor 조건이면 발동 가능.
- 즉, 제안 주체(initiator)와 수락자(acceptor)를 구분하지 않고, 양측 `COUNTER`에 대해 모두 보정을 시도함.

---

## 파일별 수정 설계

## 1) `trades/generation/dealgen/fit_swap.py`

### 1-1. 함수 시그니처 확장
- `maybe_apply_fit_swap(...)`에 **트리거 컨텍스트 인자** 추가:
  - `initiator_team_id: str`
- 내부에서 아래를 계산:
  - `initiator_u = initiator_team_id.upper()`
  - `receiver_is_initiator = (receiver_id == initiator_u)`

### 1-2. 트리거 판정 로직 교체
- 기존: receiver 후보를 찾을 때 buyer/seller 양측 `REJECT/COUNTER`를 동일 취급
- 변경: receiver 팀의 역할(initiator vs acceptor)에 따라 verdict 허용 집합 분기
  - receiver가 initiator면: `{REJECT, COUNTER}` 허용
  - receiver가 acceptor면: `{REJECT}`만 허용
- `FIT_FAILS` reason 요구 조건은 유지

### 1-3. 가독성/재사용 보조 함수 추가
- 예: `_allowed_verdicts_for_receiver(receiver_id, initiator_team_id)`
- 예: `_is_triggerable_for_receiver(decision, receiver_id, initiator_team_id)`

### 1-4. 주석/Docstring 갱신
- 정책(initiator vs acceptor) 명시
- “acceptor의 COUNTER는 fit-swap 트리거 제외”를 분명히 문서화

---

## 2) `trades/generation/dealgen/sweetener.py`

### 2-1. 함수 시그니처 확장
- `maybe_apply_sweeteners(...)`에 다음 인자 추가:
  - `initiator_team_id: str`

### 2-2. 트리거 판정부 교체
- 현재 seller/buyer 둘 다 `REJECT/COUNTER`면 후보가 될 수 있는 분기(
  `if ... seller_decision ... elif ... buyer_decision ...`
  )를 역할 기반 허용 verdict로 재작성
- 역할 기반 허용 규칙(핵심):
  - initiator side decision: `REJECT` 또는 `COUNTER`면 트리거 가능
  - acceptor side decision: `REJECT`일 때만 트리거 가능
- deficit/corridor 조건(`abs(margin) <= close_corridor`)은 기존 유지

### 2-3. 보조 함수 도입
- 예: `_allowed_verdicts_for_team(team_id, initiator_team_id)`
- 예: `_can_trigger_for_team(team_id, decision, initiator_team_id)`

### 2-4. telemetry 태그(선택)
- 디버깅 편의를 위해 태그 추가 고려:
  - `sweetener_trigger_side:initiator|acceptor`
  - `sweetener_trigger_verdict:reject|counter`
- 공격적 전환이므로 필수는 아니지만, 빠른 검증을 위해 권장

---

## 3) `trades/generation/dealgen/core.py`

### 3-1. BUY 모드 호출부 인자 전달
- `maybe_apply_fit_swap(...)` 호출 시 `initiator_team_id=buyer_id` 전달
- `maybe_apply_sweeteners(...)` 호출 시 `initiator_team_id=buyer_id` 전달

### 3-2. SELL 모드 호출부 인자 전달
- `maybe_apply_fit_swap(...)` 호출 시 `initiator_team_id=seller_id` 전달
- `maybe_apply_sweeteners(...)` 호출 시 `initiator_team_id=seller_id` 전달

### 3-3. 타입/린트 정리
- 신규 인자 추가에 따른 호출부/정렬/포맷 정리

---

## 4) `trades/counter_offer/builder.py`

> 즉시 정책 일관성 확보를 위해 **동시에 반영**한다.

### 4-1. fit-swap 호출부 전달
- `maybe_apply_fit_swap(...)` 호출 시 `initiator_team_id=user` 전달
  - counter-offer 문맥에서 user가 제안 제출자

### 4-2. sweetener 호출부 전달(존재 시)
- `maybe_apply_sweeteners(...)` 경로가 있으면 동일하게 `initiator_team_id=user` 전달
- 현재 builder 전략 순서에서 sweetener 적용 경로를 확인해 누락 없이 반영

### 4-3. 주석 정리
- “counter-offer에서도 동일 트리거 정책 사용” 명시

---

## 5) 테스트 파일

## 5-1. `trades/generation/dealgen/test_*` 신규/수정
- fit-swap 트리거 정책 테스트 추가:
  1. receiver=initiator, verdict=COUNTER, FIT_FAILS -> 트리거됨
  2. receiver=acceptor, verdict=COUNTER, FIT_FAILS -> **트리거 안 됨**
  3. receiver=acceptor, verdict=REJECT, FIT_FAILS -> 트리거됨
- sweetener 트리거 정책 테스트 추가:
  1. initiator side COUNTER + close deficit -> 트리거됨
  2. acceptor side COUNTER + close deficit -> **트리거 안 됨**
  3. acceptor side REJECT + close deficit -> 트리거됨

## 5-2. `trades/counter_offer/test_*` 신규/수정
- counter-offer 빌더 경로에서 initiator 전달 및 정책 일관성 검증

---

## 구현 순서 (공격적 일괄 적용)
1. `fit_swap.py`, `sweetener.py` 시그니처 + 트리거 로직 수정
2. `core.py`, `counter_offer/builder.py` 호출부 일괄 반영
3. 관련 테스트 일괄 갱신
4. 전체 테스트 실행 후 실패 케이스 기준으로 미세 조정

---

## 주의사항
- 마이그레이션/세이브 호환 고려 없음 (개발 단계 가정)
- 기존 config flag는 유지하되, 트리거 정책은 새 규칙으로 즉시 강제
- 런타임 플래그/토글 분기(legacy fallback)는 두지 않음

---

## 패치 완료 시 달성되는 상태 (정확한 정의)

아래 조건이 **항상** 성립한다.

1. **initiator(제안 제출자) 측 불만일 때**
   - verdict가 `REJECT` 또는 `COUNTER`면 fit-swap/sweetener 트리거 가능

2. **acceptor(제안 수락자) 측 불만일 때**
   - verdict가 `REJECT`일 때만 fit-swap/sweetener 트리거 가능
   - verdict가 `COUNTER`면 fit-swap/sweetener는 트리거되지 않음

3. 위 규칙은
   - 일반 deal generation(BUY/SELL 모드)
   - counter-offer builder 경로
   에서 동일하게 적용된다.

4. 결과적으로
   - acceptor의 `COUNTER` 때문에 발생하던 보정 시도(검증/평가 소모)가 제거되고,
   - 동일 예산에서 더 많은 제안 탐색/생성이 가능해진다.
