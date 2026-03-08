# 트레이드 협상 충돌 후속 검증 리포트

## 목적
- 재검증 결과를 바탕으로 **실제 타당한 문제만** 중복 없이 최종 이슈 목록으로 정리.
- 각 이슈별로 **구체적인 해결 방안(수정 파일 + 변경 내용)** 제시.

## 검증 방법
- 정적 확인: `app/api/routes/trades.py`, `static/js/features/market/marketScreen.js`, `static/js/core/api.js`, `static/js/app/state.js`.
- 문법 확인: `python -m py_compile app/api/routes/trades.py`.
- 검색 확인: `rg -n`으로 상태 전이/핸들러 연결/telemetry 태깅 경로 검토.

---

## 최종 확정 이슈 목록 및 해결 방안 (중복 제거)

### 1) `api_trade_negotiation_open` 병합 손상으로 인한 문법 오류(SyntaxError) 및 서버 import 실패

**해결 방안**
- 수정 파일: `app/api/routes/trades.py`
- 수정 내용:
  1. `api_trade_negotiation_open()`의 `response = {` 블록을 정상 완결 형태로 재구성한다.
  2. canonicalization(`_canonicalize_offer_payload_for_response`) 결과를 `response["session"]`에 반영한 뒤, 단일 `return response` 경로로 정리한다.
  3. 수정 후 `python -m py_compile app/api/routes/trades.py`로 문법 검증을 필수 수행한다.

---

### 2) 같은 핸들러 내부 idempotency 제어흐름 도달 불가(unreachable)

**해결 방안**
- 수정 파일: `app/api/routes/trades.py`
- 수정 내용:
  1. `return {...}` 조기 반환을 제거하고, `response` 객체 생성 → `if idem_key:` 캐시 저장 → `return response` 순서로 제어흐름을 일원화한다.
  2. open/reject/commit API가 동일한 idempotency 패턴을 쓰도록 `api_trade_negotiation_open()`을 다른 핸들러와 동일 템플릿으로 정렬한다.
  3. `_store_idempotency_response(...)` 호출 전에 `payload_hash`/`scope`/`session_id`/`team_id`를 명시적으로 확정해 재사용한다.

---

### 3) 인박스 계약위반 telemetry의 `session_id` 오태깅 가능성(row 세션 미사용)

**해결 방안**
- 수정 파일: `static/js/features/market/marketScreen.js`
- 수정 내용:
  1. `formatInboxAssetText(asset, options)` 시그니처에 `sessionId`를 추가한다.
  2. 인박스 렌더(`renderMarketTradeInbox`)에서 `row.session_id`를 각 asset 렌더 호출로 전달한다.
  3. `buildContractViolationBadge()`에 전달하는 violation payload의 `session_id`를 `state.marketTradeActiveSession` 참조 대신 `options.sessionId`로 설정한다.
  4. `session_id`가 비어 있을 때는 빈 문자열 대신 `row-session-missing` 같은 명시 값으로 태깅해 분석 가능성을 유지한다.

---

### 4) 계약위반 수집 로직의 렌더 기반 중복 누적/과다 집계

**해결 방안**
- 수정 파일: `static/js/features/market/marketScreen.js`, `static/js/app/state.js`
- 수정 내용:
  1. `state`에 `marketTradeContractViolationSeen`(예: `Set` 대체용 객체 맵) 필드를 추가한다.
  2. `pushTradeContractViolation()`에서 `session_id + endpoint + asset_kind + asset_ref + missing_fields`를 키로 중복 제거 후 push 하도록 변경한다.
  3. `loadMarketTradeInbox()` 시작 시 사이클 기준으로 seen 맵을 초기화하고, 렌더 중복 호출로 같은 violation이 누적되지 않게 한다.
  4. 필요 시 `render-origin`(loading/loaded) 메타를 별도로 붙여 중복 집계 없이 원인 분석만 가능하게 한다.

---

### 5) `marketScreenActive` 비활성 전환 누락으로 stale guard 약화

**해결 방안**
- 수정 파일: `static/js/features/market/marketScreen.js`, `static/js/app/router.js`(또는 화면 전환을 제어하는 라우팅 파일)
- 수정 내용:
  1. 마켓 화면 이탈 시점(다른 screen 활성화 직전/직후)에서 `state.marketScreenActive = false`를 명시한다.
  2. 같은 시점에 `abortAllMarketTradeRequests()`를 호출해 미완료 요청을 중단한다.
  3. 마켓 재진입 시 `state.marketScreenActive = true`와 함께 초기 탭 로딩을 재시작해 lifecycle 경계를 명확히 한다.

---

### 6) 협상 Start 경로의 중복 클릭/재시도 방어 공백

**해결 방안**
- 수정 파일: `static/js/features/market/marketScreen.js`, `static/js/core/api.js`, `app/schemas/trades.py`, `app/api/routes/trades.py`
- 수정 내용:
  1. `startTradeNegotiationFromModal()` 실행 중에는 Start 버튼을 disable하고 pending 상태를 전역 pending map으로 관리한다.
  2. `startTradeNegotiationSession()`에 `signal`/`idempotencyKey` 인자를 추가하고 헤더(`X-Idempotency-Key`)를 전달한다.
  3. 백엔드 start 엔드포인트 스키마/핸들러에 `idempotency_key`를 수용하고 replay guard를 적용한다.
  4. finally에서 버튼 상태를 반드시 복구하고, 중복 클릭 시 early return 하도록 통일한다.

---

### 7) 모달 close 시 active session 상태 잔존

**해결 방안**
- 수정 파일: `static/js/features/market/marketScreen.js`, `static/js/app/state.js`
- 수정 내용:
  1. `closeTradeDealEditorModal()`에서 `state.marketTradeActiveSession = null`을 명시한다.
  2. 동시에 `marketTradeDealDraft`, `marketTradeAssetPool`, `marketTradeUi`를 세션 종료 목적에 맞게 초기화한다.
  3. `syncMarketTradeModalSessionState()`와 close 로직이 충돌하지 않도록 공통 `resetTradeDealModalContext()` 헬퍼를 `state.js`에 추가해 재사용한다.

---

### 8) 탭 전환 경로의 취소/무효화 정책 불균일(부분 타당)

**해결 방안**
- 수정 파일: `static/js/features/market/marketScreen.js`, `static/js/core/api.js`
- 수정 내용:
  1. FA/트레이드블록 로더에도 `beginScopedRequest()` 기반 request context를 도입한다.
  2. 각 fetch 호출에 `signal`을 연결하고, 반영 직전에 `isScopedRequestCurrent()`를 검사해 stale 응답을 폐기한다.
  3. `openMarketSubTab()`에서 탭별 scope(`faList`, `tradeBlockList`, `tradeInbox`)를 분리해 상호 간섭을 줄인다.
  4. 화면 전환 시 공통 abort 루틴에서 탭 관련 scope도 함께 정리한다.

---

> 참고: 1번과 2번은 동일 코드 블록에서 발생하지만, 성격(문법 파손 vs 제어흐름 파손)이 달라 최종 목록에서 분리 유지.
