# Trade Negotiation Loading Manager - Bundle 4 검증/정리 리포트

## 검증 목표
`docs/trade-negotiation-loading-manager-implementation-plan.md`의 작업 묶음 4 기준으로 아래를 확인한다.

1. Task 기반 로딩 매니저의 핵심 규칙이 동작하는가?
   - ref-count
   - top message 우선순위/최신성
   - scope별 로딩 상태 반영
2. 마켓 화면 전환 로직에서 `setLoading` 의존이 제거되었는가?
3. DOM/마크업/스타일 변경 이후 문법/런타임 리스크가 없는가?
4. 유저 체감 품질이 실제로 개선되는가?

---

## 실행한 검증

### 1) Task API 동작 스모크 검증 (headless Node)
- 방식:
  - mock `document/window` 환경에서 `state.js` + `api.js`를 import
  - `beginTask/endTask/endTasksByScope/getTopTask/getActiveTasks`를 직접 호출해 규칙 검증
- 확인 항목:
  - `global` scope에서 3개 task 병렬 시작 후 1개씩 종료 시 active count가 정확히 감소
  - 우선순위 높은 task가 top message로 선택되는지 확인
  - `tradeInbox` 시작/종료 시 `marketTradeInboxLoading` true/false 반영
  - `market` 시작/종료 시 `marketLoadingMessage` set/clear
  - `tradeDealModal` 시작/종료 시 loading/message set/clear
- 결과: 통과 (`task api smoke ok`)

### 2) 정적 문법 검증
- `marketScreen.js`, `dom.js`의 syntax check 수행
- 결과: 오류 없음

### 3) bundle 2 완료 조건 점검
- `marketScreen.js` 내 `setLoading(` 호출이 남아있지 않은지 점검
- 결과: 없음 (`setLoading`은 `core/api.js` shim에만 존재)

### 4) bundle 3 동기화 점검
- 아래 key가 JS/HTML/CSS 전 구간에 일관되게 존재하는지 확인
  - `marketTradeDealLoading`
  - `market-trade-deal-loading`
  - `market-inline-loading`
  - `market-inline-loading-spinner`
- 결과: 일관성 확인

---

## 목표 달성 판단

## A. 기능 목표 달성
- **달성**
  - Task API의 ref-count/우선순위 규칙이 스모크 테스트에서 동작함.
  - 마켓 화면 주요 흐름이 scope 기반 task(`global|market|tradeDealModal|tradeInbox`)로 전환됨.
  - 인박스/모달 로딩이 로컬 UI로 노출되도록 DOM/마크업/스타일이 동기화됨.

## B. 부작용/리스크 평가

### 잔여 리스크 1) 레거시 `setLoading` shim 잔존
- 현재 `core/api.js`에 호환용 shim이 남아있음.
- 마켓 외 화면에서 기존 `setLoading` 사용 시 global scope task로 동작하므로 즉시 장애는 없지만,
  장기적으로는 사용처를 점진 제거해야 완전 전환이 끝난다.
- 권장 후속 작업:
  - 전 코드베이스 `setLoading(` 호출 inventory 작성
  - 화면별로 scope 기반 begin/end로 치환
  - 최종적으로 shim 제거

### 잔여 리스크 2) 렌더 트리거 의존성
- `market` scope 메시지는 state 필드 기반이므로 화면 갱신 타이밍에 의존한다.
- 현재는 주요 경로에서 begin/end 직후 summary re-render를 호출해 커버했지만,
  신규 경로 추가 시 동일 패턴을 지키지 않으면 메시지 갱신이 늦어질 수 있다.
- 권장 후속 작업:
  - 마켓 summary re-render를 공통 유틸로 묶어 누락 방지

### 잔여 리스크 3) 실서비스 상호작용 E2E 부재
- 현재는 스모크/정적 검증 중심이며, 실제 API 응답 지연/실패를 포함한 브라우저 E2E는 미구현.
- 권장 후속 작업:
  - A→B→C 연속 클릭, 탭 전환, 모달 닫기 직후 응답 도착 시나리오 자동화

---

## 사용자 체감 브리핑

## 적용 전
- 병렬 요청 중 하나가 먼저 끝나면 로딩이 조기에 꺼지는 등 체감 불안정.
- 인박스/모달 작업 중에도 전역 오버레이가 떠서 맥락이 끊김.
- "지금 무엇을 로딩하는지" 메시지가 흔들려 신뢰도가 낮음.

## 적용 후
- 같은 scope에서 마지막 task가 끝날 때만 로딩 해제되어 체감이 안정적.
- 인박스/모달은 해당 영역에서만 로딩이 보여져 흐름이 덜 끊김.
- 우선순위/최신성 규칙으로 로딩 메시지가 더 일관되고 예측 가능해짐.

요약하면, 유저는 **"로딩이 덜 튀고, 덜 막히고, 지금 뭘 처리 중인지 더 잘 보인다"**는 체감을 갖게 된다.
