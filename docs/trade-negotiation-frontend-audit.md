# 트레이드 협상 프론트 품질 감사 (발견 사항)

## 범위
- 화면/상태: `static/js/features/market/marketScreen.js`
- 공통 로딩/네트워크: `static/js/core/api.js`
- 전역 상태: `static/js/app/state.js`
- 포맷터/문구: `static/js/core/format.js`
- 마크업: `static/NBA.html`

## 핵심 결론
현재 트레이드 협상 프론트는 **비동기 경쟁 상태(race condition)**, **데이터 폴백 정책 미흡**, **전역 로딩 제어 취약성**이 동시에 존재합니다. 사용자가 보고한
- A→B 전환 시 A가 뜨는 현상,
- `Unknown Player`, `---- round pick` 표기,
- 맥락 없는 로딩 오버레이
는 모두 코드 레벨에서 재현 가능한 구조적 취약점입니다.

---

## 치명 (Critical)

### 1) 세션 전환 경쟁 상태: 늦게 끝난 이전 요청이 최신 UI를 덮어쓸 수 있음
- `openTradeInboxSession()`은 세션을 연 뒤 `openTradeDealEditorFromSession()`을 호출합니다.
- `openTradeDealEditorFromSession()` 내부에서 `await loadTradeDealPlayerPools(otherTeamId)` 이후 `renderTradeDealEditor()`를 수행하는데, **요청 토큰/세션 일치 검증이 전혀 없습니다.**
- 따라서 A 세션 요청이 늦게 완료되면, 이미 B/C로 이동한 뒤에도 A 기반 렌더가 마지막에 반영될 수 있습니다.
- 관련 위치:
  - `openTradeInboxSession` 호출 흐름 (`1331~1360`)
  - `openTradeDealEditorFromSession` 비동기 렌더 (`636~673`)
  - `loadTradeDealPlayerPools` 비동기 fetch 후 전역 상태 갱신 (`473~511`)

### 2) 전역 로딩 오버레이가 단순 boolean 토글이라 동시 작업에서 깨짐
- `setLoading(show)`는 단순 표시/숨김만 수행하며 요청 카운트/소유권 개념이 없습니다.
- 시장 화면에는 `setLoading(true/false)`를 여러 비동기 흐름이 병렬로 호출합니다. 한 요청이 먼저 끝나 `false`를 호출하면, 다른 요청이 진행 중이어도 오버레이가 꺼지거나 반대로 남아 있을 수 있습니다.
- 관련 위치:
  - `setLoading` 구현 (`448~451`, `static/js/core/api.js`)
  - 다중 호출 지점 (`1336/1359`, `1376/1392`, `1839/1848`, `2057/2086`, `2121~2141` 등)

---

## 높음 (High)

### 3) 세션 변경 시 상태 초기화가 부분적이라 이전 세션 흔적이 남기 쉬움
- `syncMarketTradeModalSessionState()`는 세션 변경 시 snapshot/tab/ui만 초기화하고, deal draft/asset pool/active session 관련 필드 정리는 하지 않습니다.
- 같은 틱에 다른 비동기 결과가 섞이면 이전 세션 흔적이 남을 여지가 큽니다.
- 관련 위치:
  - `syncMarketTradeModalSessionState` (`152~174`, `157~170`)

### 4) 협상 이동 버튼 연타 방지/잠금이 없어 중복 오픈 요청이 쉽게 발생
- 인박스 카드의 `협상` 버튼은 클릭 즉시 비활성화하지 않습니다.
- 빠른 연타 시 `openTradeInboxSession(row)`가 중첩 호출되어 경쟁 상태를 증폭합니다.
- 관련 위치:
  - 인박스 카드 버튼 바인딩 (`1216~1225`, `1241~1252`)

### 5) `Unknown Player`가 빈번히 노출될 수밖에 없는 디렉터리 구축 방식
- 인박스 자산 텍스트는 `playerDirectory`에 없으면 즉시 `Unknown Player`로 표시합니다.
- 그런데 이 디렉터리는 **현재 각 팀 로스터 조회 결과**만으로 채우며, 트레이드/웨이브로 이미 로스터에서 빠진 선수, 과거 제안의 선수는 누락될 수 있습니다.
- 관련 위치:
  - 폴백 문구 (`747~756`)
  - 디렉터리 hydration (`686~714`)

### 6) `---- round pick` 표기의 직접 원인
- `formatPickLabel()`은 year가 비정상이면 `----`, round가 비정상이면 `round`로 강제 폴백합니다.
- 인박스에서 pick 자산을 표시할 때 year/round 유효성 보정 없이 바로 이 포맷터를 호출합니다.
- API가 `pick_id`만 주는 경우에도 현재 UI는 `pick_id`를 라벨로 사용하지 않아 `---- round pick`이 노출됩니다.
- 관련 위치:
  - `formatPickLabel` (`97~104`)
  - 인박스 픽 포맷 호출 (`757~763`)

### 7) 화면 이탈 이후에도 비동기 후속 렌더가 계속 실행됨
- `openTradeInboxSession()`/`rejectTradeInboxSession()`은 완료 후 `loadMarketTradeInbox({ force: true }).catch(() => {})`를 **백그라운드 fire-and-forget**로 호출합니다.
- 사용자가 이미 다른 화면으로 이동해도 렌더/상태 갱신이 이어지며, 로딩 문구와 화면 상태가 어긋나는 원인이 됩니다.
- 관련 위치:
  - 강제 새로고침 fire-and-forget (`1346`, `1355`, `1384`)

### 8) 시장 화면 재진입/탭 전환 시 동시 호출 직렬화가 없어 상태 경합 가능
- `showMarketScreen()`은 호출마다 `openMarketSubTab()`을 await하지만, 외부에서 연속 호출될 경우 이전 호출 취소/무시 장치가 없습니다.
- 탭 클릭도 동일하게 직렬화 토큰 없이 매번 새 요청을 띄웁니다.
- 관련 위치:
  - `showMarketScreen` (`2105~2116`)
  - `openMarketSubTab` (`1830~1850`)

---

## 치명/높음 이슈 체감 설명 (비개발자용)

아래는 **치명(Critical) + 높음(High)** 항목이 실제 게임 플레이에서 어떻게 느껴지는지, 기술 용어를 줄여서 설명한 요약입니다.

### 치명 1) 세션 전환 경쟁 상태
- **게임에서 보이는 현상:** 협상 A를 눌렀다가 바로 B를 눌렀는데, 갑자기 A 화면이 다시 나타납니다. 이어서 C를 누르면 B가 뜨는 식으로 "한 박자 늦게" 따라옵니다.
- **왜 불편한가:** 내가 지금 누구랑 협상 중인지 신뢰가 깨집니다. 잘못된 상대 기준으로 자산을 보고 제출할 위험이 생깁니다.
- **한 줄 비유:** 채팅방을 옮겼는데 이전 방 메시지가 늦게 도착해 현재 방을 덮어쓰는 상황입니다.

### 치명 2) 로딩 오버레이 on/off 충돌
- **게임에서 보이는 현상:** 이미 다른 화면으로 나왔는데도 "로딩 중"이 갑자기 뜨거나, 반대로 아직 작업 중인데 로딩이 먼저 사라집니다.
- **왜 불편한가:** 게임이 멈췄는지, 끝났는지 판단이 안 됩니다. 버튼을 또 눌러 중복 행동을 하게 됩니다.
- **한 줄 비유:** 엘리베이터 층수 표시가 실제 이동과 안 맞아서 도착 여부를 믿을 수 없는 상태입니다.

### 높음 3) 세션 변경 시 상태 초기화 불완전
- **게임에서 보이는 현상:** 새 협상을 열었는데 이전 협상에서 보던 탭/선택 상태가 섞여 보일 수 있습니다.
- **왜 불편한가:** "새 협상"인데 화면은 "이전 협상 잔상"이 남아 있어 판단을 헷갈리게 만듭니다.

### 높음 4) 협상 버튼 연타 시 중복 요청
- **게임에서 보이는 현상:** 협상 버튼을 빠르게 두세 번 누르면 화면이 흔들리거나, 어떤 요청이 최종 적용됐는지 모호해집니다.
- **왜 불편한가:** 같은 행동을 한 번 했는데 내부적으로 여러 번 처리되어 예측이 어려워집니다.

### 높음 5) `Unknown Player` 표기 빈발
- **게임에서 보이는 현상:** 분명 선수 자산인데 이름 대신 `Unknown Player`가 자주 뜹니다.
- **왜 불편한가:** 어떤 선수가 오가고 있는지 핵심 정보를 못 보니 협상 판단이 사실상 불가능해집니다.

### 높음 6) `---- round pick` 표기
- **게임에서 보이는 현상:** 픽 자산이 "`---- round pick`" 같은 깨진 문구로 표시됩니다.
- **왜 불편한가:** 몇 년도 몇 라운드 픽인지 핵심 정보가 사라져, 자산 가치를 평가하기 어렵습니다.

### 높음 7) 화면 이탈 후에도 뒤늦은 갱신 반영
- **게임에서 보이는 현상:** 협상 화면을 닫았는데, 잠시 후 목록/메시지가 다시 바뀌는 등 "유령 업데이트"가 보입니다.
- **왜 불편한가:** 내가 현재 보고 있는 화면 기준으로 시스템이 동작하는지 확신하기 어렵습니다.

### 높음 8) 탭 전환/재진입 동시 호출 경합
- **게임에서 보이는 현상:** 탭을 빠르게 오가면 방금 본 탭이 아니라 이전 탭 데이터가 늦게 끼어드는 느낌이 납니다.
- **왜 불편한가:** 사용자가 한 "마지막 행동"이 항상 최종 화면에 반영된다는 기본 기대가 깨집니다.

---

## 치명/높음 이슈 근본 해결 설계 (상업 출시 품질 기준)

> 원칙: 증상 가리기(문구 치환/임시 가드) 금지. **데이터 계약(API) + 상태머신 + 비동기 제어 + 관측성**을 함께 수정해 재발을 차단한다.

### A. 세션/탭 레이스 계열 (문제 1, 3, 4, 7, 8)

#### 근본 원인
- 협상 세션을 여는/갱신하는 비동기 호출에 "요청 세대(request generation)" 개념이 없습니다.
- 화면 이탈/모달 종료 이후에 도착한 응답을 폐기하는 수명(lifecycle) 체크가 없습니다.
- 동일 액션 중복 클릭을 구조적으로 막지 못합니다.

#### 해결 설계
1. **협상 UI 상태머신 도입**
   - 상태: `idle -> opening(session) -> ready(session) -> submitting(session) -> closed`.
   - 전환 규칙을 중앙 함수 하나로 강제하고, 상태 불일치 전환은 무시/로그 처리.
2. **요청 세대 토큰 + AbortController 표준화**
   - `open session`, `load pool`, `load inbox`, `submit/reject`에 공통 래퍼 적용.
   - 새 요청 시작 시 이전 동일 스코프 요청 abort.
   - 응답 반영 전 `requestId === currentRequestId && sessionId === activeSessionId` 검증.
3. **화면/모달 lifecycle guard**
   - `marketScreenActive`, `tradeDealModalOpen` 플래그를 두고 false면 late response 폐기.
   - fire-and-forget 갱신 금지, 반드시 스코프된 task scheduler 경유.
4. **중복 클릭 방지**
   - 협상/거절/제출 버튼은 해당 task pending 동안 disable + idempotency key 부여.
   - 백엔드도 동일 idempotency key로 중복 처리 방어.

#### 게임 코드 정합성 포인트
- 트레이드 세션은 서버가 authoritative source이며, 프론트는 "마지막 유효 응답만 반영" 원칙 유지.
- 세션 phase/status 전이는 서버 enum과 1:1 매핑한 프론트 enum으로 검증.

#### 완료 기준(DoD)
- A→B→C 500ms 간격 스트레스 시 최종 C만 렌더.
- 모달 닫은 뒤 어떤 비동기 응답도 UI/로딩 상태를 바꾸지 않음.
- 중복 클릭 20회 테스트에서 서버 mutation 1회만 기록.

---

### B. 전역 로딩 오버레이 신뢰성 (문제 2)

#### 근본 원인
- 로딩을 boolean으로 표현해 동시 작업의 소유권/잔여 작업 수를 표현하지 못합니다.

#### 해결 설계
1. **Task 기반 로딩 매니저 도입**
   - `beginTask(scope, message)` / `endTask(taskId)` API 제공.
   - scope 예: `global`, `market`, `tradeDealModal`, `tradeInbox`.
2. **ref-count + 최상위 메시지 규칙**
   - 같은 scope에서 active task 수가 0일 때만 로딩 해제.
   - 메시지는 최신 시작 task 또는 우선순위 높은 task 기준으로 결정.
3. **로딩 표현 분리**
   - 전체 화면 block이 필요한 작업만 global overlay 사용.
   - 인박스/모달은 로컬 skeleton/spinner 사용(글로벌 오버레이 남용 금지).

#### 완료 기준(DoD)
- 병렬 작업 3개 중 1개 종료 시 오버레이 유지, 3개 모두 종료 시 해제.
- 화면 이탈 후 stale finally가 로딩 상태를 덮어쓰지 않음.

---

### C. 플레이어/픽 데이터 정합성 (문제 5, 6)

#### 근본 원인
- 인박스 렌더 데이터가 "현재 로스터"에 과도 의존합니다.
- 거래 자산 스키마가 표시 필수 필드(year/round, player name snapshot)를 항상 보장하지 않습니다.

#### 해결 설계
1. **거래 자산 canonical DTO 재정의 (백엔드-프론트 계약)**
   - player asset: `player_id`, `display_name`, `pos`(snapshot) 필수.
   - pick asset: `pick_id`, `year`, `round`, `original_team`, `owner_team` 필수.
   - 서버는 인박스/세션 응답에서 위 필드 누락 없이 제공.
2. **프론트는 DTO 우선 렌더, 캐시는 보조로만 사용**
   - `Unknown Player`, `---- round pick`은 "표시 대체"가 아니라 "계약 위반 알림"으로 취급.
   - 계약 위반 응답은 Sentry/telemetry로 즉시 수집.

#### 게임 코드 정합성 포인트
- 드래프트/트레이드 자산의 단일 진실 원천은 `trade_assets` 도메인.
- UI 표시는 로스터 조회 결과가 아니라 트레이드 세션 snapshot을 기준으로 해야 replay 가능.

#### 완료 기준(DoD)
- QA 시나리오에서 `Unknown Player`, `---- round pick` 문자열 0건.
- 계약 필드 누락 응답률 < 0.1%, 누락 시 자동 알림.

---

### D. 상업용 품질을 위한 운영 가드레일

1. **관측성(Observability)**
   - 이벤트 로깅: `trade_session_open_requested`, `trade_session_open_applied`, `late_response_dropped`, `contract_violation_detected`.
   - 메트릭: 레이스 드랍율, 로딩 체류 시간 p95, 협상 액션 성공률.
2. **회귀 방지 테스트**
   - 단위: 상태머신 전이/토큰 검증/로딩 매니저 ref-count.
   - 통합: A→B→C 빠른 전환, 모달 닫기 직후 응답 도착, 인박스/딜 에디터 왕복 재진입.
   - E2E: 실제 클릭 스트레스(더블클릭/탭 왕복) 자동화.
3. **점진 배포 전략**
   - feature flag로 새 협상 파이프라인을 단계적 활성화.
   - 구버전 대비 에러율/이탈률 비교 후 전면 전환.

## 중간 (Medium)

### 9) 자산 방향 판정 로직이 모호해 incoming/outgoing이 동시에 찍힐 수 있음
- `splitDealAssetsForInbox()`는 `fromTeamId === userTeamId || receiver === otherTeamId`면 outgoing,
  `fromTeamId === otherTeamId || receiver === userTeamId`면 incoming으로 분류합니다.
- receiver 추론이 빈 값이거나 데이터가 비정형일 때 동일 자산이 양쪽에 들어갈 가능성이 있습니다.
- 관련 위치:
  - receiver 추론 (`716~724`)
  - 분류 로직 (`726~745`)

### 10) 인박스 로딩 중에도 이전 목록을 그대로 그려 UX가 혼동됨
- 로딩 시작 시 `setMarketTradeInboxLoading(true)` 후 즉시 `renderMarketTradeInbox()`를 호출해 기존 데이터를 계속 노출합니다.
- 사용자는 "로딩 중인데 왜 이전 협상 카드가 보이지?" 같은 혼란을 겪습니다.
- 관련 위치:
  - `loadMarketTradeInbox` 시작부 (`1297~1302`)

### 11) 모달 닫기 시 세션/드래프트/자산 상태를 명시적으로 정리하지 않음
- `closeTradeDealEditorModal()`은 DOM만 닫고 pending 플래그만 변경합니다.
- 명시적인 상태 정리가 없어 다음 오픈 시 비동기 타이밍에 따라 이전 상태 흔적 노출 가능성이 큽니다.
- 관련 위치:
  - `closeTradeDealEditorModal` (`1545~1550`)

### 12) 협상 모달의 `거절` 버튼이 존재하지만 기능 바인딩이 없음
- 마크업에 `market-trade-deal-reject` 버튼이 있으나 이벤트 핸들러 등록 코드가 없습니다.
- UI 완성도가 떨어지고 사용자 기대와 동작이 불일치합니다.
- 관련 위치:
  - 버튼 마크업 (`1196~1198`, `static/NBA.html`)
  - 바인딩 코드에는 submit/cancel만 존재 (`2125~2142`)

### 13) 인박스 카드 템플릿에 반복 ID를 사용해 HTML 유효성/유지보수 리스크 존재 (신규)
- 카드 템플릿 내부 노드가 `id`로 구성되어 있고, 이를 복제해 목록에 반복 삽입합니다.
- 현재는 fragment 내부 query로 동작하지만, 문서 전체 기준 ID 중복이 누적되어 접근성/테스트/스타일 확장 시 취약점이 됩니다.
- 관련 위치:
  - 템플릿 내부 반복 id (`271~290`, `static/NBA.html`)
  - 복제 후 id selector로 바인딩 (`1192~1197`)

### 14) 에러 피드백이 `alert()` 중심으로 파편화되어 협상 UX 신뢰도 저하 (신규)
- 트레이드 협상 열기/거절/제출, 블록 등록/해제 등 다수 경로가 `alert()` 의존형 에러 노출을 사용합니다.
- 연속 액션 흐름에서 모달/로딩과 결합되면 체감 품질 저하가 큽니다.
- 관련 위치:
  - 인박스 액션/블록 액션/협상 액션의 alert 호출 (`1219`, `1224`, `1245`, `1251`, `1650`, `2099~2101`, `2122`, `2136`, `2150`, `2170`)

---

## 우선순위 제안 (수정은 별도)
1. **세션/요청 토큰 도입 + 마지막 요청만 반영** (문제 1, 8)
2. **로딩 오버레이 ref-count 또는 task-key 기반으로 교체** (문제 2)
3. **player/pick 데이터 계약 정합성 개선** (문제 5, 6)
4. **모달 닫기/화면 이탈 시 비동기 업데이트 무시 가드 추가** (문제 7, 11)
5. **템플릿 id 구조 개선 + alert 중심 UX 정리** (문제 13, 14)
