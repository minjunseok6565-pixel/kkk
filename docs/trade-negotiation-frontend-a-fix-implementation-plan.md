# 트레이드 협상 A(세션/탭 레이스) 수정안 — 파일별 패치 설계서

## 목적
`docs/trade-negotiation-frontend-audit.md`의 A 범주(문제 1, 3, 4, 7, 8)를 한 번에 해결하기 위한 **즉시 구현 가능한 패치 단위 설계**입니다.

핵심 원칙:
1. 협상 UI는 상태머신으로만 전이한다. (`idle -> opening(session) -> ready(session) -> submitting(session) -> closed`)
2. 모든 비동기 작업은 scope/requestId/sessionId 검증을 통과해야만 반영한다.
3. fire-and-forget를 금지하고 scoped task scheduler를 통해서만 후속 갱신을 실행한다.
4. 사용자 중복 클릭을 UI+서버 양쪽(idempotency key)에서 모두 방어한다.

---

## 1) `static/js/app/state.js`

### 변경 목표
- 협상 UI 상태머신과 lifecycle guard를 전역 상태로 명시.
- 화면/모달 활성 상태, 요청 세대 토큰, 버튼 pending map을 표준 필드로 도입.

### 구체 수정안

#### A. 상태 필드 추가
`state` 루트에 아래 필드 추가:
- `marketScreenActive: false`
- `tradeDealModalOpen: false`
- `marketTradeSessionFsm: { status: "idle", sessionId: null, updatedAt: null, reason: "" }`
- `marketTradeRequestSeq: 0`
- `marketTradeRequestScopes: {}` // scope별 `{ requestId, sessionId, abortController }`
- `marketTradePendingActions: {}` // actionKey별 `{ pending, idempotencyKey }`
- `marketTaskQueueByScope: {}` // scope별 Promise 체인

#### B. 상태머신 헬퍼 추가
`state.js`에 순수 함수 추가:
- `getInitialMarketTradeSessionFsm()`
- `canTransitionMarketTradeSessionFsm(from, to, { fromSessionId, toSessionId })`
- `transitionMarketTradeSessionFsm(nextStatus, { sessionId, reason, strict = true })`

전이 규칙(강제):
- `idle -> opening`
- `opening -> ready | closed`
- `ready -> submitting | closed`
- `submitting -> ready | closed`
- `closed -> opening | idle`
- 그 외는 **무시 + console.warn 로그**

#### C. reset 함수 확장
- `resetMarketTradeDealState()`에서 아래도 초기화:
  - `marketTradeSessionFsm`
  - `marketTradeRequestScopes`
  - `marketTradePendingActions`
  - `tradeDealModalOpen`

#### D. sync 함수 확장
- `syncMarketTradeModalSessionState()`가 세션 변경 시 `marketTradeDealDraft`, `marketTradeAssetPool`, `marketTradeActiveSession`까지 명시 reset하도록 확장.
- 반환값에 `prevSessionId`, `nextSessionId` 포함해 caller에서 guard 로깅에 활용.

---

## 2) `static/js/core/api.js`

### 변경 목표
- open/load/submit/reject 공통으로 사용할 요청 세대 토큰 + AbortController 래퍼 제공.
- 동일 scope 새 요청 시작 시 이전 요청 자동 abort.

### 구체 수정안

#### A. 요청 래퍼 유틸 추가
신규 export 함수:
- `beginScopedRequest(scope, { sessionId = null, abortPrevious = true } = {})`
  - `state.marketTradeRequestSeq += 1`
  - `requestId` 발급
  - 이전 동일 scope 컨트롤러 abort
  - 새 `AbortController` 저장
  - `{ requestId, scope, sessionId, signal, startedAt }` 반환
- `isScopedRequestCurrent(scope, requestId, sessionId = null)`
  - 최신 requestId와 일치 + (sessionId 있으면 activeSessionId 일치) 확인
- `abortScopedRequest(scope)` / `abortAllMarketTradeRequests()`

#### B. fetchJson 개선
- `fetchJson(url, options)`에서 `options.signal` 전달 지원(이미 fetch는 지원하므로 래퍼 레벨에서 강제).
- abort 에러 표준화: `AbortError` 발생 시 message를 `REQUEST_ABORTED`로 normalize.

#### C. 협상 API 함수에 `signal`, `idempotencyKey` 옵션 추가
기존 함수 시그니처 확장:
- `fetchTradeNegotiationInbox(..., { signal } )`
- `openTradeNegotiationSession(..., { signal, idempotencyKey })`
- `rejectTradeNegotiationSession(..., { signal, idempotencyKey })`
- `commitTradeNegotiationSession(..., { signal, idempotencyKey })`
- `fetchStateSummary({ signal })`

요청 헤더에 공통 주입:
- `X-Idempotency-Key: <key>` (mutation만)

---

## 3) `static/js/features/market/marketScreen.js`

### 변경 목표
- A 범주 이슈를 실제로 차단하는 실행 로직 적용.
- 상태머신 전이 강제, 응답 반영 가드, scoped scheduler, 버튼 중복 클릭 차단.

### 구체 수정안

#### A. 공통 가드/스케줄러 헬퍼 추가
파일 상단에 함수 추가:
- `isMarketUiActive()` => `state.marketScreenActive && state.tradeDealModalOpen`
- `getActiveSessionId()` => `state.marketTradeActiveSession?.session_id`
- `createTradeIdempotencyKey(action, sessionId)` => `${sessionId}:${action}:${timestamp}:${nonce}`
- `runScopedTask(scope, runner)`
  - `state.marketTaskQueueByScope[scope] = (prevPromise).finally(() => runner())`
  - fire-and-forget 금지, caller는 반드시 await

#### B. 화면 lifecycle 플래그 반영
- `showMarketScreen()` 진입 시 `state.marketScreenActive = true`
- 화면 이탈 지점(라우터 훅 또는 최소 `activateScreen` 전환 직전/후 처리)에서 `state.marketScreenActive = false` 및 `abortAllMarketTradeRequests()` 호출
- `openTradeDealEditorFromSession()` 모달 open 시 `state.tradeDealModalOpen = true`
- `closeTradeDealEditorModal()`에서 `state.tradeDealModalOpen = false`, 관련 scope abort, 상태머신 `closed` 전이

#### C. open/session 흐름 리팩터
`openTradeInboxSession(row)`:
1. 상태머신 `idle|closed -> opening(sessionId)` 시도
2. 버튼 pending + idempotencyKey 생성/저장
3. `beginScopedRequest("openSession", { sessionId })`
4. API 응답 후 아래 검증 실패 시 즉시 폐기:
   - `isScopedRequestCurrent("openSession", requestId, sessionId)`
   - `row.session_id === sessionId`
   - `state.marketScreenActive === true`
5. 통과 시 `openTradeDealEditorFromSession(...)` 호출
6. 인박스 갱신은 `await runScopedTask("tradeInboxRefresh", () => loadMarketTradeInbox({ force: true, reason: "post-open" }))`
7. finally에서 pending 해제

#### D. modal open 흐름 가드
`openTradeDealEditorFromSession(session, fallback)`:
1. `sessionId` 계산 후 상태머신 `opening -> ready` 준비
2. `syncMarketTradeModalSessionState()` 호출 + 세션 일치 여부 확인
3. `beginScopedRequest("dealPlayerPool", { sessionId })`
4. `loadTradeDealPlayerPools(otherTeamId, { requestCtx })` 호출
5. 응답 반영 직전 체크:
   - request current
   - `sessionId === getActiveSessionId()`
   - `state.tradeDealModalOpen === true`
6. 실패 시 render 생략 + late response drop 로그
7. 통과 시 `renderTradeDealEditor()` + 상태머신 `ready(sessionId)`

#### E. player pool/inbox 로더 공통화
- `loadTradeDealPlayerPools(otherTeamId, { requestCtx })`로 시그니처 변경
  - 내부 `fetchTeamDetail`, `fetchStateSummary({ signal })`
  - 반영 전 request/session/lifecycle 검증
- `loadMarketTradeInbox({ force, reason })`
  - `beginScopedRequest("tradeInbox", { sessionId: getActiveSessionId() })`
  - 응답 반영 전에 `marketScreenActive` 확인
  - stale 응답은 discard

#### F. 탭/화면 전환 직렬화
- `openMarketSubTab(tab)`를 `runScopedTask("marketSubTab", async () => { ... })`로 감싼 public wrapper 제공
- `showMarketScreen()`도 `runScopedTask("showMarketScreen", async () => { ... })`로 직렬화
- 이전 실행 중 새 요청이 오면 이전 scope request abort + 최신 호출만 반영

#### G. fire-and-forget 제거
아래 호출을 전부 금지/대체:
- `loadMarketTradeInbox({ force: true }).catch(() => {})`
-> `await runScopedTask("tradeInboxRefresh", () => loadMarketTradeInbox({ force: true, reason: "..." }))`

#### H. 중복 클릭 방지 + idempotency key 연동
- 인박스 카드의 협상/거절 버튼 렌더 시 `data-session-id`, `data-action` 부여
- 클릭 즉시 버튼 disable
- pending map 키: `${action}:${sessionId}`
- pending 중 재클릭은 무시
- API 호출 시 `idempotencyKey` 전달

#### I. 제출/거절 버튼 disable 정책 통일
- `setTradeDealSubmitPending()` 확장:
  - `marketTradeDealSubmit`, `marketTradeDealReject`, 인박스 row 버튼들 disable 토글
- `submitTradeDealDraft()` 실행 시 상태머신 `ready -> submitting -> ready`
- reject(모달/인박스)도 동일 pending 정책 사용

---

## 4) `static/NBA.html`

### 변경 목표
- 버튼 id/selector가 pending 제어를 안정적으로 받도록 최소 마크업 속성 보강.

### 구체 수정안
- 인박스 카드 템플릿 버튼에 다음 data attribute 명시:
  - 협상 버튼: `data-trade-action="open-session"`
  - 거절 버튼: `data-trade-action="reject-session"`
- 딜 에디터 모달 거절 버튼(기존 id 있다면 재사용)에 `data-trade-action="reject-from-modal"` 부여
- 접근성 대응: disabled 시 `aria-disabled="true"` 동기화

---

## 5) `app/schemas/trades.py`

### 변경 목표
- 백엔드가 프론트 idempotency key를 받을 수 있도록 스키마 확장.

### 구체 수정안
아래 요청 모델에 optional 필드 추가:
- `TradeNegotiationOpenRequest.idempotency_key: str | None = None`
- `TradeNegotiationRejectRequest.idempotency_key: str | None = None`
- `TradeNegotiationCommitRequest.idempotency_key: str | None = None`
- `TradeSubmitCommittedRequest.idempotency_key: str | None = None`

검증 규칙:
- 길이 8~128, 영문/숫자/`-`/`_` 허용(유효성 실패 시 422)

---

## 6) `app/api/routes/trades.py`

### 변경 목표
- mutation 요청의 중복 처리를 idempotency key 기준으로 방어.

### 구체 수정안

#### A. 공통 헬퍼 추가
파일 내부 private helper:
- `_normalize_idempotency_key(raw) -> str`
- `_idempotency_replay_guard(scope, session_id, team_id, key, payload_hash)`
  - 동일 key+scope에 대해 이미 성공 응답이 있으면 저장된 결과 재반환
  - 진행 중이면 `{"ok": true, "idempotent": true, "in_flight": true}` 또는 마지막 성공 반환

#### B. 대상 엔드포인트 적용
- `/api/trade/negotiation/open`
- `/api/trade/negotiation/reject`
- `/api/trade/negotiation/commit`
- `/api/trade/submit-committed`

각 핸들러 초반에 guard 호출:
1. key 없으면 기존 동작 유지
2. key 있으면 replay 검사
3. 처리 성공 시 결과 저장(최소 TTL 2분, session 단위 메모리 캐시)

#### C. 응답 필드 표준화
위 엔드포인트 성공 응답에 공통 추가:
- `idempotent: bool`
- `idempotency_key: <echo>` (키 있을 때)

---

## 7) `trades/negotiation_store.py`

### 변경 목표
- 라우터 레벨 외에 도메인 저장소에서도 중복 mutation 방어.

### 구체 수정안
- 세션 메타에 `last_request_keys` 저장 공간 추가
- `open_inbox_session`, `close_as_rejected`, `mark_committed_and_close`에 optional `idempotency_key` 파라미터 추가
- key가 이미 처리되었으면 상태변경 없이 idempotent 응답 반환

> 라우터/스토어 이중 방어로 API 재시도, 더블클릭, 네트워크 재전송 모두 흡수.

---

## 8) 테스트/검증 수정 파일

### 8-1) `tests/` 프론트/통합 테스트 추가
- `tests/frontend/test_market_trade_race_guards.(js|ts)`
  - A→B→C 빠른 전환에서 마지막 세션만 렌더되는지
  - 모달 닫기 후 late response가 UI를 안 바꾸는지
  - 동일 scope 새 요청 시 이전 abort 되는지

### 8-2) `tests/test_trade_negotiation_idempotency.py`
- open/reject/commit/submit-committed 동일 key 2회 호출 시 mutation 1회만 수행되는지
- 서로 다른 payload+동일 key에 대한 방어(에러 또는 최초 응답 재사용) 정책 검증

### 8-3) 수동 QA 체크리스트 문서
- `docs/qa-trade-negotiation-race-checklist.md` 신규
  - 연타, 탭 왕복, 화면 이탈, 네트워크 지연(인위적) 시나리오

---

## 구현 전제 (개발 단계 기준)
- 이번 작업은 **세이브 데이터 호환성/마이그레이션을 고려하지 않는다.**
- 기존 저장 포맷을 유지하기 위한 우회 로직(legacy fallback)은 넣지 않는다.
- 필요한 경우 상태 필드/응답 스키마를 즉시 목표 구조로 고정한다.

---

## 권장 작업 순서 (오류 최소화 작업 묶음)
아래 순서는 "한 묶음 내 파일들은 서로 강결합"이고, "묶음 간에는 의존성이 단방향"이 되도록 최적화한 순서입니다.

### 묶음 1) 프론트 상태/요청 인프라 선구축
**목표:** 나머지 화면 로직이 바로 사용할 공통 기반을 먼저 완성.

**수정 파일**
- `static/js/app/state.js`
- `static/js/core/api.js`

**작업 내용**
1. `state.js`
   - FSM 상태, lifecycle 플래그, request scope 저장소, pending action map, task queue map 추가.
   - FSM 전이 함수(`canTransition...`, `transition...`) 추가 및 reset/sync 함수 확장.
2. `api.js`
   - `beginScopedRequest`, `isScopedRequestCurrent`, `abortScopedRequest`, `abortAllMarketTradeRequests` 추가.
   - `fetchJson`의 abort 표준화(`REQUEST_ABORTED`) 적용.
   - 협상 관련 API 함수에 `signal`, `idempotencyKey` 옵션 및 헤더 주입 추가.

**이 묶음 완료 확인**
- 정적 점검: import/export 에러 없이 빌드 가능해야 함.
- 호출부 변경 전이라도 유틸 단위 실행(또는 간단 스모크) 시 abort/세대 토큰 발급이 정상 동작해야 함.

### 묶음 2) 프론트 화면 실행 흐름 일괄 치환
**목표:** 실제 레이스 발생 지점(open/load/submit/reject/tab/screen)을 한 번에 안전 경로로 교체.

**수정 파일**
- `static/js/features/market/marketScreen.js`
- `static/NBA.html`

**작업 내용**
1. `marketScreen.js`
   - 공통 헬퍼(`runScopedTask`, `isMarketUiActive`, `createTradeIdempotencyKey`) 추가.
   - `showMarketScreen`, `openMarketSubTab`, `openTradeInboxSession`, `openTradeDealEditorFromSession`, `loadTradeDealPlayerPools`, `loadMarketTradeInbox`, `submitTradeDealDraft`, `rejectTradeInboxSession`, `closeTradeDealEditorModal`을 scoped request/fsm/lifecycle 가드 기반으로 치환.
   - fire-and-forget refresh 제거 후 `await runScopedTask(...)`로 통일.
   - pending 동안 버튼 disable + 중복 클릭 무시 + idempotency key 전달.
2. `NBA.html`
   - 인박스/모달 버튼 `data-trade-action` 추가.
   - disabled 시 `aria-disabled` 동기화 가능한 selector 보강.

**이 묶음 완료 확인**
- A→B→C 전환 스모크에서 마지막 클릭만 반영되는지 확인.
- 모달 닫기 후 late response가 렌더를 건드리지 않는지 확인.

### 묶음 3) 백엔드 idempotency 계약 반영
**목표:** 프론트 중복 방지와 서버 중복 방어를 동일 키로 연결.

**수정 파일**
- `app/schemas/trades.py`
- `app/api/routes/trades.py`
- `trades/negotiation_store.py`

**작업 내용**
1. `schemas/trades.py`
   - open/reject/commit/submit-committed 요청 모델에 `idempotency_key` 추가 + 포맷 검증.
2. `routes/trades.py`
   - `_normalize_idempotency_key`, `_idempotency_replay_guard` 추가.
   - 대상 mutation 엔드포인트 4개에 replay guard 적용.
   - 성공 응답에 `idempotent`, `idempotency_key` 필드 표준화.
3. `negotiation_store.py`
   - 세션 단위 `last_request_keys` 저장 및 key 재요청 시 무변경 idempotent 반환 처리.

**이 묶음 완료 확인**
- 동일 key 재호출 시 mutation 1회만 반영되는지 API 레벨 검증.

### 묶음 4) 테스트/QA 문서 마무리
**목표:** 회귀 방지와 수동 검증 경로를 고정.

**수정 파일**
- `tests/frontend/test_market_trade_race_guards.(js|ts)`
- `tests/test_trade_negotiation_idempotency.py`
- `docs/qa-trade-negotiation-race-checklist.md`

**작업 내용**
1. 레이스/late-response/abort/직렬화 시나리오 테스트 추가.
2. idempotency key 중복 호출 테스트 추가.
3. 수동 QA 절차 문서화.

**이 묶음 완료 확인**
- 자동 테스트 통과 + 수동 시나리오 체크리스트 전 항목 통과.

### 순서 고정 이유 (요약)
- 묶음 1이 먼저 없으면 묶음 2 구현 중 임시 코드가 다수 발생해 오류율이 올라감.
- 묶음 2를 묶음 3보다 먼저 끝내야 프론트에서 필요한 API 계약(`idempotency_key`)이 명확해짐.
- 묶음 3 이후 묶음 4를 수행해야 테스트가 최종 계약 기준으로 안정화됨.

---

## 수용 기준(DoD)
- A→B→C 10회 연속 클릭해도 최종 C만 렌더
- 모달 닫힌 뒤 도착한 응답이 UI/로딩 상태를 변경하지 않음
- 동일 세션 버튼 20연타 시 open/reject/commit API mutation은 1회만 유효 처리
- 탭 전환/화면 재진입 중 stale 응답이 state를 덮어쓰지 않음

---

## 유저 체감 비교 (수정 전 vs 수정 후)

### 수정 전
- 빠르게 협상 카드를 넘기면 이전 팀 협상 화면이 갑자기 덮어써서 “내가 지금 누구랑 협상 중인지” 헷갈림.
- 협상 모달을 닫았는데도 잠시 후 로딩/리스트가 다시 바뀌어 화면이 불안정하게 보임.
- 버튼 연타 시 같은 요청이 여러 번 들어가며 거절/제출 결과가 들쭉날쭉하게 느껴짐.
- 탭 전환 시 가끔 오래된 데이터가 재등장해 UI 신뢰도가 떨어짐.

### 수정 후
- 가장 마지막으로 클릭한 협상만 열리고, 이전 요청 결과는 자동 폐기되어 화면이 안정적으로 유지됨.
- 모달/화면을 벗어난 뒤 도착한 응답은 무시되어, 사용자가 보고 있는 컨텍스트가 절대 오염되지 않음.
- 버튼은 처리 중 자동 비활성화되고 서버도 idempotency key로 중복을 차단해, 연타해도 결과가 한 번만 반영됨.
- 탭/화면 전환이 직렬화되어 “늦게 온 응답이 최신 화면을 덮는” 체감 문제가 사실상 사라짐.
