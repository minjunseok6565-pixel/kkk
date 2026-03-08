# Trade Inbox '협상' 버튼 실패 원인 분석

## 증상 정리
- 인박스에서 **협상** 버튼 클릭 시 로딩 문구("협상 세션을 여는 중...")가 잠깐 보였다가 사라지고, 세션 편집기가 열리지 않는 경우가 있음.
- 또는 서버 로그에 `POST /api/trade/negotiation/open ... 400 Bad Request`가 찍히고, 프런트 UI에는 오류 문구가 `[object Object]` 형태로 노출되는 경우가 있음.

## 원인 1: open 요청 응답이 프런트에서 "정상 응답인데도" 버려지는 로직

### 핵심 메커니즘
`openTradeInboxSession()`는 요청 직후 `beginScopedRequest("openSession", { sessionId })`를 호출하고,
응답 이후 `shouldApplyResponseForScope("openSession", requestId, sessionId, ...)` 검사에 통과해야 다음 단계(딜 에디터 오픈)로 진행한다.

그런데 `isScopedRequestCurrent()` 및 `shouldApplyResponseForScope()`의 세션 검증은
"현재 활성 협상 세션(`state.marketTradeActiveSession.session_id`)"과 요청의 `sessionId`가 같아야 true를 반환하도록 되어 있다.

문제는 **openSession 흐름에서는 아직 활성 세션을 세팅하기 전**에 위 검증을 수행한다는 점이다.
즉, 최초 오픈 시점에는 활성 세션이 보통 `null`이므로 검증이 false가 되어 응답이 drop되고,
결과적으로 로딩만 잠깐 보이고 아무 것도 열리지 않는 현상이 발생한다.

### 코드 근거
- open 요청 후 gate 검사 후 바로 return: `static/js/features/market/marketScreen.js`
- 세션 일치 강제 검증: `static/js/core/api.js`, `static/js/features/market/marketScreen.js`

## 원인 2: 400 에러 메시지가 `[object Object]`로 표시되는 에러 직렬화/파싱 불일치

### 백엔드 응답 형태
트레이드 도메인 에러는 `_trade_error_response()`에서 다음 구조로 내려간다.

```json
{
  "ok": false,
  "error": {
    "code": "...",
    "message": "...",
    "details": {...}
  }
}
```

### 프런트 파싱 로직 문제
`fetchJson()`의 `resolveApiErrorDetail()`는 `data.detail`, `data.message`를 우선 확인하고,
그다음 `String(data.error)`를 수행한다.

여기서 `data.error`는 객체이므로 문자열 변환 시 `[object Object]`가 되어,
최종적으로 사용자 alert에 그대로 노출된다.

즉, 백엔드는 구조화된 `error.message`를 주고 있는데,
프런트가 그 필드를 직접 읽지 않아 사람이 읽을 수 없는 메시지로 깨진다.

## 원인 3: 실제 400 자체는 비정상이라기보다 도메인 검증 실패일 가능성이 큼

`/api/trade/negotiation/open`는 다음 조건에서 `TradeError`(HTTP 400)를 반환한다.
- `team_id` 누락 (`NEGOTIATION_BAD_QUERY`)
- 선택 팀이 수신 팀(세션 owner)과 불일치 (`NEGOTIATION_NOT_AUTHORIZED`)
- 세션 상태가 ACTIVE가 아니거나 phase가 REJECTED (`NEGOTIATION_NOT_ACTIVE`)
- AI 자동 종료로 끝난 세션 (`NEGOTIATION_ENDED_BY_AI`)

따라서 로그의 400은 "엔드포인트 고장"이라기보다,
요청 시점 상태/권한 검증에 걸렸을 때의 정상적인 도메인 오류 경로일 수 있다.
다만 현재 프런트 메시지 파싱 문제 때문에 이 의미가 `[object Object]`로 손실된다.

## 결론
이번 현상은 단일 원인보다 다음 2가지가 결합되어 관측된다.
1. **정상 응답 드롭(프런트 세션 gate 타이밍 불일치)** 때문에 "잠깐 로딩 후 아무 일도 안 일어남".
2. **에러 메시지 파싱 누락** 때문에 실제 400 사유가 `[object Object]`로 보임.

즉, 사용자가 본 두 증상은 서로 다른 계층의 문제(응답 적용 gate / 에러 표시 계층)에서 동시에 발생한 결과다.
