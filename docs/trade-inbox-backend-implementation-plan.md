# Trade Inbox Backend Implementation Plan

## 목표
시장 화면의 신규 `제안/협상` 탭에서 **우리 팀으로 들어온 트레이드 제안**을 조회하고, 다음 액션을 안전하게 수행할 수 있도록 백엔드 계약(Contract)과 상태 전이를 정의한다.

- 제안 목록 조회(Inbox)
- 제안 거절(Reject)
- 기존 수신 제안 협상 열람/재개(Open)
- 협상 완료(Commit) 시 상태 정합성 강화
- 중복/경합/멱등(idempotent) 처리

---

## 현재 상태 요약

- 오케스트레이션에서 유저 팀 대상 수신 오퍼를 생성하고 `negotiation` 세션(`last_offer`)에 저장하는 로직은 존재.
- 트레이드 협상 API는 `start`, `commit` 중심으로 구성되어 있으며, Inbox/Reject/Open 전용 API는 없음.
- 따라서 프론트의 “수신 제안 목록 → 협상/거절” 사용자 흐름이 끊겨 있음.

---

## 설계 원칙

1. **세션 SSOT 유지**: 수신 제안의 출처/상태는 `state.negotiations` 세션을 SSOT로 사용.
2. **명시적 상태 전이**: `phase`/`status`를 규칙적으로 갱신.
3. **읽기 API는 정렬/필터/페이지네이션 지원**: 프론트 초기 탭 로딩 성능 고려.
4. **액션 API는 멱등 보장**: 중복 클릭/재시도 시 일관 응답.
5. **권한 검증**: `user_team_id`가 세션의 `user_team_id`와 다르면 차단.

---

## 1) Inbox 조회 API

### Endpoint
`GET /api/trade/negotiation/inbox`

### Query Params
- `team_id: str` (required)
- `status: str = "ACTIVE"` (optional; `ACTIVE|CLOSED|ALL`)
- `phase: str = "OPEN"` (optional; `OPEN|COUNTER_PENDING|REJECTED|ACCEPTED|ALL`)
- `include_expired: bool = false`
- `limit: int = 50` (1~200)
- `offset: int = 0`
- `sort: str = "updated_desc"` (`updated_desc|created_desc|expires_asc`)

### Response
```json
{
  "ok": true,
  "team_id": "LAL",
  "filters": {
    "status": "ACTIVE",
    "phase": "OPEN",
    "include_expired": false,
    "limit": 50,
    "offset": 0,
    "sort": "updated_desc"
  },
  "total": 2,
  "rows": [
    {
      "session_id": "uuid",
      "user_team_id": "LAL",
      "other_team_id": "BKN",
      "status": "ACTIVE",
      "phase": "INBOX_PENDING",
      "created_at": "2026-01-10T10:00:00Z",
      "updated_at": "2026-01-10T10:00:00Z",
      "valid_until": "2026-01-12",
      "is_expired": false,
      "summary": {
        "headline": "BKN → LAL 트레이드 제안",
        "offer_tone": "SERIOUS",
        "offer_privacy": "PRIVATE",
        "leak_status": "NONE"
      },
      "offer": {
        "deal": {"teams": [], "legs": []},
        "asset_counts": {
          "user_outgoing_players": 1,
          "user_incoming_players": 1,
          "user_outgoing_picks": 1,
          "user_incoming_picks": 0
        }
      },
      "actions": {
        "can_open": true,
        "can_reject": true,
        "can_commit": false
      }
    }
  ]
}
```

### 상태 해석 규칙
- `INBOX_PENDING`: 유저가 아직 열람/협상 시작 전
- `COUNTER_PENDING`: 상대 카운터가 떠 있는 협상 중
- `NEGOTIATING`: 열린 협상 진행 중
- `REJECTED`: 유저가 거절 완료
- `ACCEPTED`: 합의되어 커밋 가능(또는 커밋 완료 전)

### 구현 대상 파일
- `app/schemas/trades.py`
  - `TradeNegotiationInboxQuery`(pydantic model) 추가
- `app/api/routes/trades.py`
  - `@router.get("/api/trade/negotiation/inbox")` 추가
  - `state.negotiations_get()` 스냅샷 기준 필터/정렬/페이지네이션 로직 추가
  - `last_offer`, `market_context.offer_meta`, `valid_until` 기반 요약 생성 헬퍼 추가

---

## 2) Reject API

### Endpoint
`POST /api/trade/negotiation/reject`

### Request
```json
{
  "session_id": "uuid",
  "team_id": "LAL",
  "reason": "NOT_INTERESTED"
}
```

### Response (정상)
```json
{
  "ok": true,
  "session_id": "uuid",
  "status": "CLOSED",
  "phase": "REJECTED",
  "rejected": true,
  "idempotent": false
}
```

### 멱등 응답 (이미 REJECTED/CLOSED)
```json
{
  "ok": true,
  "session_id": "uuid",
  "status": "CLOSED",
  "phase": "REJECTED",
  "rejected": true,
  "idempotent": true
}
```

### 검증/처리 규칙
1. `session_id` 존재 검증
2. `team_id.upper() == session.user_team_id` 검증
3. 만료 여부와 무관하게 거절은 허용(UX 단순화)
4. 상태 전이:
   - `phase = "REJECTED"`
   - `status = "CLOSED"`
   - `updated_at` 갱신
   - message append: `USER_GM` / 거절 사유 텍스트
5. 이미 `REJECTED/CLOSED`면 멱등 성공 반환

### 구현 대상 파일
- `app/schemas/trades.py`
  - `TradeNegotiationRejectRequest` 추가
- `trades/negotiation_store.py`
  - `close_as_rejected(session_id: str, reason: Optional[str])` 유틸 추가
- `app/api/routes/trades.py`
  - `@router.post("/api/trade/negotiation/reject")` 추가

---

## 3) Open API (수신 제안 열람/협상 진입)

> 프론트 ‘협상’ 버튼은 `start`를 호출하지 않고, **기존 수신 세션을 열람/재개**해야 함.

### Endpoint
`POST /api/trade/negotiation/open`

### Request
```json
{
  "session_id": "uuid",
  "team_id": "LAL"
}
```

### Response
```json
{
  "ok": true,
  "session": {
    "session_id": "uuid",
    "user_team_id": "LAL",
    "other_team_id": "BKN",
    "status": "ACTIVE",
    "phase": "NEGOTIATING",
    "draft_deal": {"teams": [], "legs": []},
    "last_offer": {"teams": [], "legs": []},
    "last_counter": null,
    "valid_until": "2026-01-12",
    "market_context": {}
  },
  "opened": true,
  "idempotent": false
}
```

### 처리 규칙
- 권한 검증 (`team_id == session.user_team_id`)
- `status=CLOSED` 또는 `phase=REJECTED`면 `NEGOTIATION_NOT_ACTIVE` 에러
- 만료 세션이면 `NEGOTIATION_EXPIRED` 에러
- `phase=INBOX_PENDING`이면 `NEGOTIATING`로 전이
- 이미 `NEGOTIATING/COUNTER_PENDING`이면 멱등 성공

### 구현 대상 파일
- `app/schemas/trades.py`
  - `TradeNegotiationOpenRequest` 추가
- `app/api/routes/trades.py`
  - `@router.post("/api/trade/negotiation/open")` 추가
- `trades/negotiation_store.py`
  - `open_inbox_session(session_id: str)` 유틸(phase 전이)

---

## 4) Commit API 정합성 강화

현재 `POST /api/trade/negotiation/commit`은 유효한 딜 평가/수락/카운터/거절 로직이 이미 있음.
여기에 아래 guard를 추가한다.

### 추가 Guard
1. 세션 권한/활성 상태 검증
   - `status == ACTIVE` 아니면 실패
2. 세션 phase 제한
   - 허용: `NEGOTIATING`, `COUNTER_PENDING`
   - 거부: `INBOX_PENDING`, `REJECTED`, `ACCEPTED`(이미 완료) 등
3. 만료 검증
   - `valid_until < today`면 `NEGOTIATION_EXPIRED`
4. 처리 성공 시 상태 전이 명확화
   - ACCEPT 경로: `phase=ACCEPTED`, `status=CLOSED`
   - COUNTER 경로: `phase=COUNTER_PENDING`, `status=ACTIVE`
   - REJECT 경로: `phase=REJECTED`, `status=ACTIVE`(협상 지속 여부 정책 선택 필요)
     - 권장: 유저가 낸 제안이 거절된 경우 `ACTIVE` 유지 (재제안 가능)

### 구현 대상 파일
- `app/api/routes/trades.py`
  - commit 진입부 guard block 추가
  - 에러 코드 표준화 (`NEGOTIATION_NOT_ACTIVE`, `NEGOTIATION_INVALID_PHASE`, `NEGOTIATION_EXPIRED`)

---

## 5) 중복/경합/멱등 처리

### 서버 처리 원칙
1. **Reject 멱등**
   - 동일 session reject 재호출은 성공 + `idempotent=true`
2. **Open 멱등**
   - 이미 열린 세션 재호출은 성공 + `idempotent=true`
3. **Commit 경쟁 처리**
   - `state.negotiation_session_update` 원자 갱신을 이용해 최종 상태 확인 후 처리
   - 이미 CLOSED면 즉시 실패(또는 멱등 정책 적용)
4. **중복 Inbox 노출 억제**
   - `session_id` unique 기준
   - 만료 + CLOSED 세션은 기본 숨김

### 보완 항목
- 필요 시 `updated_at` 기준 optimistic guard 필드(`if_match_updated_at`) 도입 가능

---

## 상태 전이 표 (권장)

| 이벤트 | 기존 상태 | 다음 상태 |
|---|---|---|
| AI 오퍼 생성 | INIT | INBOX_PENDING (ACTIVE) |
| 유저 협상 열람(open) | INBOX_PENDING | NEGOTIATING (ACTIVE) |
| 유저 제안 commit → 카운터 발생 | NEGOTIATING | COUNTER_PENDING (ACTIVE) |
| 유저 제안 commit → 수락 | NEGOTIATING/COUNTER_PENDING | ACCEPTED (CLOSED) |
| 유저 거절(reject) | INBOX_PENDING/NEGOTIATING/COUNTER_PENDING | REJECTED (CLOSED) |
| 만료 처리 | ACTIVE + expired | EXPIRED (CLOSED) 또는 ACTIVE+blocked |

> `EXPIRED` phase를 도입하지 않으면 `status=CLOSED` + reason meta로 대체 가능.

---

## 수정 파일 상세 (체크리스트)

### A. `app/schemas/trades.py`
- [ ] `TradeNegotiationInboxQuery`
- [ ] `TradeNegotiationRejectRequest`
- [ ] `TradeNegotiationOpenRequest`

### B. `trades/negotiation_store.py`
- [ ] `close_as_rejected(session_id, reason)`
- [ ] `open_inbox_session(session_id)`
- [ ] 만료 판정 헬퍼(`is_expired(session, today)`) 추가 고려

### C. `app/api/routes/trades.py`
- [ ] `GET /api/trade/negotiation/inbox`
- [ ] `POST /api/trade/negotiation/open`
- [ ] `POST /api/trade/negotiation/reject`
- [ ] `POST /api/trade/negotiation/commit` guard 강화
- [ ] 공통 헬퍼: team 권한 검증, phase/status 검증, 만료 판정

### D. (선택) `trades/errors.py`
- [ ] 에러코드 상수 추가
  - `NEGOTIATION_INVALID_PHASE`
  - `NEGOTIATION_EXPIRED`
  - `NEGOTIATION_NOT_AUTHORIZED`

---

## API 에러 코드 초안

- `NEGOTIATION_NOT_FOUND`
- `NEGOTIATION_NOT_AUTHORIZED`
- `NEGOTIATION_NOT_ACTIVE`
- `NEGOTIATION_INVALID_PHASE`
- `NEGOTIATION_EXPIRED`
- `DEAL_INVALIDATED`

---

## 프론트 연결 시나리오 (요약)

1. 시장 > 제안/협상 탭 진입
   - `GET /api/trade/negotiation/inbox?team_id={selectedTeamId}`
2. 리스트에서 `협상` 클릭
   - `POST /api/trade/negotiation/open`
   - 성공 시 상세 협상 화면 진입, session payload 바인딩
3. 리스트/상세에서 `거절` 클릭
   - `POST /api/trade/negotiation/reject`
   - 성공 시 리스트에서 제거(또는 REJECTED 뱃지)
4. 상세에서 `제안 제출/완료`
   - 기존 `POST /api/trade/negotiation/commit` 사용

---

## 테스트 계획 (백엔드)

1. Inbox 조회
   - team_id 필터, phase/status 필터, limit/offset, 정렬 검증
2. Reject
   - 정상 거절, 권한 오류, 없는 세션, 멱등 재호출
3. Open
   - INBOX_PENDING → NEGOTIATING 전이
   - 이미 열린 세션 멱등
   - REJECTED/CLOSED/만료 세션 차단
4. Commit Guard
   - INBOX_PENDING에서 commit 차단
   - REJECTED에서 commit 차단
   - 만료 세션 commit 차단

---

## 단계별 구현 순서 (권장)

1. 스키마/에러코드 추가
2. negotiation_store 유틸 추가
3. Inbox API 구현
4. Open/Reject API 구현
5. Commit guard 강화
6. 단위 테스트 작성 및 리그레션 검증

