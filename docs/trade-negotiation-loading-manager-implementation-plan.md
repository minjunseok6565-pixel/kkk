# 전역 로딩 오버레이 신뢰성 개선 구현안 (문제 2)

기준 문서: `docs/trade-negotiation-frontend-audit.md`의 **B. 전역 로딩 오버레이 신뢰성 (문제 2)**.

## 목표
- `setLoading(true/false)` boolean 토글을 제거하고, **Task 기반 로딩 매니저**로 전환한다.
- 로딩 표시를 `global`/`market`/`tradeDealModal`/`tradeInbox` scope로 분리한다.
- 전역 오버레이는 **정말 화면 block이 필요한 작업**에만 사용하고, 인박스/모달은 로컬 로딩 표현으로 전환한다.

---

## 파일별 수정안

## 1) `static/js/app/state.js`

### 1-1. 로딩 Task 상태 저장소 추가
`state`에 아래 필드를 추가한다.

```js
loadingTasks: {
  nextTaskId: 1,
  byScope: {
    global: [],
    market: [],
    tradeDealModal: [],
    tradeInbox: [],
  },
},
```

- 각 task shape:
  - `id: string` (`${scope}:${seq}`)
  - `scope: string`
  - `message: string`
  - `priority: number` (기본 0, 높을수록 우선)
  - `startedAt: number` (Date.now)
  - `meta?: object`

### 1-2. 로컬 로딩 플래그 추가
기존 `marketTradeInboxLoading` 외에 아래 필드 추가:

```js
marketTradeInboxLoadingMessage: "",
marketTradeDealModalLoading: false,
marketTradeDealModalLoadingMessage: "",
marketLoadingMessage: "",
```

- `tradeInbox` scope는 인박스 subtitle/spinner 문구에 반영.
- `tradeDealModal` scope는 모달 내부 로딩 문구에 반영.
- `market` scope는 시장 탭 summary/subtitle에 반영(글로벌 오버레이 사용 금지).

### 1-3. 로딩 상태 초기화 함수 추가
아래 헬퍼를 추가 export한다.

```js
function createInitialLoadingTasks() { ... }
function resetLoadingTasks() { ... }
```

- 예외 흐름/화면 전환 후에도 stale task가 남지 않도록 강제 초기화할 때 사용.

---

## 2) `static/js/core/api.js`

### 2-1. 기존 API 대체: `setLoading` → Task API
기존:
```js
function setLoading(show, msg = "")
```

신규 API 추가:

```js
function beginTask(scope = "global", message = "", options = {})
function endTask(taskId)
function endTasksByScope(scope)
function getActiveTasks(scope)
function getTopTask(scope)
```

#### beginTask 동작 규칙
- `scope` normalize (`global|market|tradeDealModal|tradeInbox`, 그 외는 `global` 폴백)
- task 생성 후 `state.loadingTasks.byScope[scope]`에 push
- `priority` 기본값 0, `startedAt` 저장
- `applyLoadingPresentation(scope)` 호출
- 반환값: `taskId`

#### endTask 동작 규칙
- 모든 scope를 순회해 해당 `taskId` 제거
- 실제 제거가 발생한 경우에만 `applyLoadingPresentation(removedScope)` 호출
- 이미 종료된 taskId 재호출은 no-op (idempotent)

#### top message 결정 규칙
- **우선순위 높은 task 우선**, 동률이면 **가장 최근 startedAt** 우선
- 이를 `getTopTask(scope)`에서 계산

### 2-2. 프리젠테이션 적용 함수 추가

```js
function applyLoadingPresentation(scope)
```

- `scope === "global"`
  - active count > 0 이면 `els.loadingOverlay` 표시
  - 메시지는 `getTopTask("global")?.message` 반영
  - 0이면 숨김
- `scope === "tradeInbox"`
  - `state.marketTradeInboxLoading = activeCount > 0`
  - `state.marketTradeInboxLoadingMessage = topTask?.message || ""`
- `scope === "tradeDealModal"`
  - `state.marketTradeDealModalLoading = activeCount > 0`
  - `state.marketTradeDealModalLoadingMessage = topTask?.message || ""`
- `scope === "market"`
  - `state.marketLoadingMessage = topTask?.message || ""`

> 주의: `api.js`는 렌더 함수를 직접 import하지 않고 **상태 + 전역 overlay 최소 갱신**까지만 담당한다.

### 2-3. export 정리
- `setLoading` 제거
- 아래 export 추가:
  - `beginTask`, `endTask`, `endTasksByScope`, `getActiveTasks`, `getTopTask`

---

## 3) `static/js/features/market/marketScreen.js`

### 3-1. import 교체
기존:
```js
import { ..., setLoading } from "../../core/api.js";
```

변경:
```js
import { ..., beginTask, endTask } from "../../core/api.js";
```

### 3-2. setLoading 호출부 전면 교체
패턴 일괄 교체:

```js
const taskId = beginTask("<scope>", "<message>", { priority: <n> });
try {
  ...
} finally {
  endTask(taskId);
}
```

#### scope 매핑 (현재 코드 기준)
- `openTradeInboxSession`:
  - `beginTask("tradeInbox", "협상 세션을 여는 중...")`
- `rejectTradeInboxSession`:
  - `beginTask("tradeInbox", "제안을 거절하는 중...")`
- `loadMarketTradeInbox`:
  - `beginTask("tradeInbox", "협상 inbox를 불러오는 중...")`
  - 기존 `setMarketTradeInboxLoading(true/false)`는 제거하고 task presentation으로 통합
- `openMarketSubTab`에서 FA/Trade Block 로드:
  - `beginTask("market", "FA 명단을 불러오는 중...")`
  - `beginTask("market", "트레이드 블록 명단을 불러오는 중...")`
- `openTradeBlockScope("mine")`:
  - `beginTask("market", "내 팀 트레이드 블록 명단을 불러오는 중...")`
- `performMarketNegotiationAction` (FA 협상 흐름):
  - `beginTask("global", "협상 진행 중...", { priority: 50 })`
  - 이유: 실제 mutation + 다단계 액션으로 전체 block 허용
- 모달 이벤트 핸들러:
  - 협상 시작 버튼: `tradeDealModal`
  - 협상 제출 버튼: `tradeDealModal` (전역 오버레이 금지)
  - 트레이드 블록 로스터 오픈/등록: `market` 로컬 처리

### 3-3. finally 안전성 보강
아래 원칙을 적용:

```js
const taskId = beginTask(...);
try {
  ...
} finally {
  endTask(taskId);
}
```

- 모든 async 경로에서 `beginTask`가 있으면 반드시 같은 함수 스코프에 `finally/endTask`를 둔다.
- fire-and-forget (`loadMarketTradeInbox({force:true}).catch`)에는 task를 연결하지 않는다.

### 3-4. 로컬 로딩 렌더 연결
- `renderMarketTradeInbox()`에서 `state.marketTradeInboxLoading` + `state.marketTradeInboxLoadingMessage` 반영.
- `renderTradeDealEditor()`에서 `state.marketTradeDealModalLoading`이 true면:
  - `market-trade-deal-messages` 상단에 spinner/message 표시
  - submit/reject 버튼 disabled 처리
- `openMarketSubTab()`에서 `state.marketLoadingMessage`를 summary 텍스트에 반영.

---

## 4) `static/js/app/dom.js`

### 4-1. 로컬 로딩 엘리먼트 참조 추가
모달/인박스 로딩 메시지 영역을 명시적으로 참조한다.

추가/확인 대상:
- `marketTradeInboxLoading`
- `marketTradeDealLoading` (신규 마크업 id)
- `marketTradeDealMessages` (기존 있으면 재사용)
- `marketTradeDealSubmit`, `marketTradeDealReject` (disabled 처리 용도)

> 이미 선언된 항목은 중복 추가하지 않고, 누락만 보완.

---

## 5) `static/NBA.html`

### 5-1. 로컬 로딩 표현 마크업 보완
전역 오버레이를 줄이기 위해 아래 최소 마크업을 추가/정비한다.

- 인박스 섹션:
  - `#market-trade-inbox-loading`에 `aria-live="polite"` 유지
  - 필요 시 inline spinner 래퍼 추가
- 딜 에디터 모달:
  - 메시지 영역 근처에 로딩 전용 슬롯 추가

예시:
```html
<div id="market-trade-deal-loading" class="subtitle hidden" aria-live="polite"></div>
```

---

## 6) `static/css/screens/market.css` (+ 필요 시 `static/css/base/utilities.css`)

### 6-1. 로컬 로딩 스타일 추가
- 인박스/모달 전용 inline spinner 스타일 추가.
- 전역 overlay용 `.loading-overlay` 스타일은 유지.

예시 클래스:
- `.market-inline-loading`
- `.market-inline-loading.is-active`
- `.market-trade-deal-actions .btn[disabled]`

---

## 권장 작업 순서 (오류 없이 진행하기 위한 최적 묶음)

아래 순서는 **서로 강결합된 변경을 한 묶음으로 처리**해, 중간 단계에서 import/DOM 불일치 오류가 나지 않도록 설계했다.

### 작업 묶음 1) 상태/코어 API 기반 구축
**수정 파일**
- `static/js/app/state.js`
- `static/js/core/api.js`

**작업 내용**
1. `state.js`에 `loadingTasks`, 로컬 로딩 메시지 필드, `createInitialLoadingTasks/resetLoadingTasks` 추가
2. `api.js`에 `beginTask/endTask/endTasksByScope/getActiveTasks/getTopTask/applyLoadingPresentation` 구현
3. `api.js` export에서 `setLoading` 제거, 신규 API export 반영

**이 묶음으로 처리하는 이유**
- `marketScreen.js`를 바꾸기 전에 task API가 먼저 있어야 import 에러가 나지 않는다.
- 상태 shape와 API가 동시에 맞춰져야 런타임 undefined 접근을 방지할 수 있다.

### 작업 묶음 2) 마켓 비즈니스 로직 전환 (setLoading 제거)
**수정 파일**
- `static/js/features/market/marketScreen.js`

**작업 내용**
1. import를 `setLoading`에서 `beginTask/endTask`로 변경
2. `setLoading` 사용 지점을 scope 규칙에 따라 일괄 치환
3. `loadMarketTradeInbox/openTradeInboxSession/rejectTradeInboxSession/openMarketSubTab/openTradeBlockScope/performMarketNegotiationAction`에 `try/finally` 정합성 확인
4. 로컬 메시지 상태(`marketTradeInboxLoadingMessage`, `marketLoadingMessage`, `marketTradeDealModalLoadingMessage`)를 렌더 함수에서 소비하도록 연결

**이 묶음으로 처리하는 이유**
- 로딩 제어 핵심 로직이 한 파일에 집중되어 있어, 한 번에 치환해야 혼합 상태(`setLoading` + task API 공존) 버그를 피할 수 있다.

### 작업 묶음 3) DOM 참조 + 마크업 + 스타일 동기화
**수정 파일**
- `static/js/app/dom.js`
- `static/NBA.html`
- `static/css/screens/market.css`
- (선택) `static/css/base/utilities.css`

**작업 내용**
1. `dom.js`에 신규/누락 엘리먼트 참조 추가
2. `NBA.html`에 `market-trade-deal-loading` 등 로컬 로딩 슬롯 반영
3. `market.css`에 인박스/모달 로컬 로딩 스타일 및 버튼 disabled 스타일 추가

**이 묶음으로 처리하는 이유**
- DOM id와 JS 참조가 분리되면 즉시 null 참조 오류가 날 수 있으므로, 마크업/참조/스타일을 하나의 원자적 묶음으로 맞춘다.

### 작업 묶음 4) 검증/정리
**수정 파일**
- (코드 수정 없음, 필요 시 `marketScreen.js` 미세조정)

**작업 내용**
1. 병렬 task 3개 동시 수행 시 scope별 ref-count 해제 타이밍 확인
2. 인박스/모달은 로컬 로딩만 표시되고 global overlay 남용이 없는지 확인
3. 빠른 탭 전환/연타 후 task leak(활성 task 잔존) 확인
4. 메시지 우선순위(우선순위 > 최신 시작) 동작 확인 후 문구 미세조정

**이 묶음으로 처리하는 이유**
- UI 체감 이슈는 기능 구현 직후 통합 시나리오에서만 드러나므로, 마지막에 한 번에 검증해야 재작업을 줄일 수 있다.

---

## 테스트/검증 포인트 (구현 직후)

### 1) 수동 시나리오
1. Trade Inbox에서 `협상` 3개를 빠르게 연속 클릭
   - 첫 번째/두 번째 완료 시 로딩 유지
   - 마지막 task 종료 시에만 해당 scope 로딩 해제
2. 딜 모달 열고 `제출` 클릭 후 즉시 탭 이동
   - global overlay가 남지 않고, 로컬 모달 로딩만 종료됨
3. FA 로딩 + Trade Block 로딩 겹치기
   - `market` scope 메시지가 최신 또는 고우선 task 기준으로 갱신

### 2) 콘솔 점검
디버그 헬퍼(개발 전용)로 active task 확인:

```js
window.__loadingDebug = {
  get state() { return state.loadingTasks; }
};
```

- leak task가 없는지(화면 이탈 후 active 0) 확인.

---

## 수정 전/후 유저 체감 비교

### 수정 전
- 여러 비동기 작업이 겹치면 로딩 오버레이가 **너무 일찍 꺼지거나**, 반대로 **끝났는데 남아있는** 경우가 있음.
- 인박스/모달 작업도 전역 오버레이가 떠서 화면 전체가 잠기는 느낌을 주고, 현재 어떤 작업이 진행 중인지 맥락이 약함.
- 빠른 탭 전환/연타에서 로딩 문구가 뒤섞여 신뢰도가 떨어짐.

### 수정 후
- 로딩이 task 단위로 추적되어, **같은 scope의 마지막 작업이 끝날 때만** 정확히 해제됨.
- 인박스/모달은 해당 영역에서만 로딩이 보여지고, 전역 오버레이는 정말 필요한 작업에만 떠서 UX가 가벼워짐.
- 메시지 규칙(우선순위 + 최신성)으로 로딩 문구가 일관되어 "지금 무슨 작업 중인지" 체감이 명확해짐.
- 결과적으로 사용자는 연타/빠른 전환에서도 "로딩 동작이 믿을 만하다"고 느끼게 됨.
