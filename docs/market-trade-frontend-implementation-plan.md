# Market 트레이드 프론트 구현 계획 (제안/협상 + 트레이드 블록 제안)

## 0) 코드 기준 사전 정합성 체크 (요청 설명 vs 실제 코드)

아래 3가지는 구현 전에 반드시 맞춰야 하는 부분이다.

1. **거래 딜 payload 포맷**
   - 현재 거래 딜은 `{"teams": [...], "legs": {...}}` 형태이며 `legs`는 **배열이 아니라 팀 ID 키를 갖는 객체(dict)** 여야 한다.
   - 프론트 딜 에디터는 내부 상태를 이 포맷에 맞춰 직렬화해야 한다.

2. **자산 종류 명칭**
   - 백엔드가 인식하는 자산 kind는 `player`, `pick`, `swap`, `fixed_asset` 이다.
   - 요청에서 말한 “보호픽 에셋”은 독립 kind가 아니라 `pick` 자산의 `protection` 필드로 표현된다.

3. **고정자산(fixed asset) 결합 규칙**
   - 현재 검증 로직상 `fixed_asset`은 독립 자산으로 거래 가능하며, “보호픽에 반드시 붙어야 함” 규칙은 코드에 없다.
   - 즉, 프론트에서 강제로 결합 규칙을 넣으면 실제 백엔드/게임 규칙과 어긋날 수 있다.

> 결론: 프론트 규칙은 백엔드 SSOT에 맞춰 `player/pick/swap/fixed_asset` 독립 선택 + `pick.protection` 편집 방식으로 설계한다.

---

## 1) 목표 UX (시장 탭)

시장 하위 탭을 다음 3개로 구성한다.

- `FA`
- `트레이드 블록`
- `제안/협상` (신규)

신규 플로우:

1. **제안/협상 탭**
   - 우리 팀(`selectedTeamId`) 기준 Inbox 목록 로딩
   - 제안은 `other_team_id`(제안 팀) 기준으로 그룹 표시
   - 각 row: 제안 요약 + `협상` + `거절`
   - `거절`: 즉시 `/api/trade/negotiation/reject` 호출 후 목록에서 제거(optimistic UI 가능)
   - `협상`: `/api/trade/negotiation/open` 호출 후 공통 딜 화면 진입

2. **트레이드 블록 탭**
   - 각 선수 row에 `제안` 버튼(기존 문구 `트레이드 제안`을 통일해도 됨)
   - 버튼 클릭 시 상대팀과 신규 협상 세션 시작(`/api/trade/negotiation/start`)
   - 시작된 세션으로 공통 딜 화면 진입

3. **공통 딜 화면(협상/제안 공용)**
   - 좌측: 우리팀/상대팀 자산 풀(선수, 픽, 스왑, 고정자산)
   - 중앙: 현재 제안 구성(팀별 legs)
   - 우측: 협상 상태/최근 카운터/메시지/제출 버튼
   - `제출` 클릭 시 `/api/trade/negotiation/commit`
   - `accepted=true`이면 커밋딜 ID가 반환되므로 후속 실행 `/api/trade/submit-committed` 흐름까지 이어서 실제 트레이드 반영

---

## 2) 프론트 구조 변경 계획

## 2.1 DOM/마크업 (`static/NBA.html`)

1. 시장 subtab 버튼에 `제안/협상` 추가
2. 신규 panel `market-panel-trade-inbox` 추가
   - 팀별 그룹 컨테이너
   - 빈 상태 문구
3. 공통 딜 에디터 모달/패널 추가 (`market-trade-deal-modal`)
   - 헤더: 세션ID, 상대팀, 만료일
   - 자산 선택 패널(우리팀/상대팀)
   - 제안 legs 미리보기
   - 액션 버튼: `제출`, `취소`, (선택) `거절`

## 2.2 DOM 참조 확장 (`static/js/app/dom.js`)

신규 element id 매핑 추가:

- subtab: `marketSubtabTradeInbox`
- panel: `marketPanelTradeInbox`
- inbox summary/body/group container
- deal editor modal 관련 refs

## 2.3 앱 상태 확장 (`static/js/app/state.js`)

추가 상태:

- `marketTradeInboxRows`
- `marketTradeInboxGrouped`
- `marketTradeInboxLoading`
- `marketTradeActiveSession`
- `marketTradeDealDraft` (`teams`, `legs`, `meta`)
- `marketTradeAssetPool` (team별 player/pick/swap/fixed_assets)
- `marketTradeUi` (selected assets, validation errors, submit pending)

---

## 3) API 연동 계획

## 3.1 Inbox 로드

- API: `GET /api/trade/negotiation/inbox?team_id={selectedTeamId}&status=ACTIVE&phase=OPEN`
- 결과를 `other_team_id` 기준 그룹화
- 그룹 정렬: 최신 `updated_at desc`
- row 액션:
  - 협상: open 호출
  - 거절: reject 호출

## 3.2 협상 열기

- API: `POST /api/trade/negotiation/open`
- body: `{ session_id, team_id }`
- 성공 시 세션 payload로 딜 에디터 초기화
  - base draft 우선순위:
    1) `session.draft_deal`
    2) `session.last_offer`
    3) 빈 deal (`teams:[user,other], legs:{user:[],other:[]}`)

## 3.3 트레이드 블록에서 새 제안 시작

- API: `POST /api/trade/negotiation/start`
- body: `{ user_team_id, other_team_id, default_offer_privacy:"PRIVATE" }`
- 성공 시 딜 에디터 오픈
- `session.last_offer`가 없으므로 빈 draft로 시작하되,
  - UX 편의로 선택한 블록 선수(상대 선수)를 상대팀 leg에 자동 prefill 옵션 제공

## 3.4 제안 제출(협상형만)

- API: `POST /api/trade/negotiation/commit`
- body: `{ session_id, deal, offer_privacy, expose_to_media }`

응답 처리:

- `accepted=true` + `deal_id` 반환:
  - 사용자 확인 후 `POST /api/trade/submit-committed` 자동 호출(또는 별도 “거래 실행” 버튼)
  - 성공 시 시장/로스터 캐시 새로고침
- `accepted=false`:
  - `ai_decision.counter` 존재 시 카운터를 화면에 표시하고 draft 갱신 가능
  - 거절 시 이유(`ai_decision.reasons`) 표시

## 3.5 거절

- API: `POST /api/trade/negotiation/reject`
- body: `{ session_id, team_id, reason }`
- 성공 시 inbox row 제거(즉시 삭제 UX)

---

## 4) 공통 딜 에디터 상세 설계

## 4.1 딜 내부 상태 모델

```js
{
  sessionId,
  teams: [userTeamId, otherTeamId],
  legs: {
    [userTeamId]: [Asset, ...],
    [otherTeamId]: [Asset, ...],
  },
  meta: {}
}
```

`Asset` 직렬화 규칙:

- 선수: `{ kind:"player", player_id }`
- 픽: `{ kind:"pick", pick_id, protection? }`
- 스왑: `{ kind:"swap", swap_id, pick_id_a, pick_id_b }`
- 고정자산: `{ kind:"fixed_asset", asset_id }`

## 4.2 자산 풀 구성

단기 구현은 `/api/state/summary` + 기존 목록 API 조합으로 구성:

- 우리팀/상대팀 선수 목록: 로스터 API(기존 팀 상세 데이터 소스 재사용)
- 픽/스왑/고정자산: `/api/state/summary`의 `db_snapshot.trade_assets`
  - `draft_picks`, `swap_rights`, `fixed_assets`를 owner_team 기준 필터

> 중기 개선: `GET /api/trade/assets?team_id=...` 전용 API를 백엔드에 추가해 프론트 단순화.

## 4.3 보호픽/고정자산 UX 규칙

백엔드 SSOT 준수:

- 보호픽: pick row 내 편집기로 `protection`을 설정/수정
- 고정자산: 별도 자산으로 선택 가능(필수 결합 없음)

단, 사용성 강화를 위해 아래 가이드 적용 가능:

- protection 없는 픽 선택 시 “보호 조건 추가” 보조 버튼
- source_pick_id가 연결된 fixed asset은 해당 pick과 같은 카드 그룹으로 시각 묶음 표시(논리 결합 아님)

## 4.4 유효성 체크

프론트 사전 검증(빠른 피드백) + 서버 검증(최종 SSOT) 이중화:

- 같은 자산 중복 추가 방지
- 최소 1개 자산 포함
- 팀 legs 키 정합성 체크
- 보호조건 JSON 포맷 검증(기본 필드 유무)
- 서버 오류코드별 메시지 매핑 (`PICK_NOT_OWNED`, `PROTECTION_CONFLICT`, `SWAP_INVALID` 등)

---

## 5) 화면/상태 전이

1. 시장 진입 → 기본 `FA`
2. `제안/협상` 탭 클릭 → inbox load
3. `협상` 클릭 → `open` 성공 → 딜 에디터
4. `트레이드 블록` 탭 `제안` 클릭 → `start` 성공 → 딜 에디터
5. 딜 수정 → `제출`(commit)
6. 결과:
   - 수락: `submit-committed` 실행 → 완료 토스트 + 목록 갱신
   - 카운터/거절: 세션 유지, 재제안 가능
7. `거절` 클릭: 즉시 row 제거

---

## 6) 구현 단계 (권장 순서)

1. **UI 골격**: HTML 탭/패널/모달 + dom.js/state.js 필드 추가
2. **Inbox 탭 동작**: load, group render, open/reject 액션
3. **딜 에디터 최소 기능**: 선수 자산만으로 commit round-trip
4. **자산 확장**: pick/swap/fixed_asset + pick protection 편집기
5. **협상 고도화**: counter 표시/반영, idempotent 응답 처리
6. **실거래 반영**: accepted 시 submit-committed 연동
7. **에러/토스트/로딩 UX 정리**

---

## 7) 테스트 체크리스트

1. 제안/협상 탭 진입 시 inbox 렌더
2. `협상` 버튼 → open 성공/만료/권한 에러 핸들링
3. `거절` 버튼 → 즉시 삭제 + 재조회 시 미노출
4. 트레이드 블록 `제안` 버튼 → start 성공 후 딜 화면 진입
5. 선수 자산만 구성한 commit 성공/실패
6. pick protection 포함 commit 성공/`PROTECTION_CONFLICT` 처리
7. swap/fixed_asset 포함 commit 성공/오류 처리
8. accepted 결과에서 submit-committed까지 완료되어 실제 로스터 반영

---

## 8) 리스크 및 대응

- 리스크: 자산 풀 데이터를 여러 소스에서 조합 시 로딩 지연
  - 대응: 탭 전환 시 lazy load + 캐싱
- 리스크: 보호조건 입력 UX 복잡도
  - 대응: preset(Top-3/Top-5/Lottery) 제공 후 고급 JSON 편집은 접기
- 리스크: 카운터 딜 자동 반영 시 사용자 혼동
  - 대응: “AI 카운터 적용” 버튼을 별도로 두고 diff 요약 표시

---

## 9) 구현 시 준수 원칙

- 거래 실행은 **협상형 파이프라인만 사용**
  - start/open → commit → (accepted 시) submit-committed
- 즉시 실행형 `POST /api/trade/submit`는 본 기능에서 사용하지 않음
- 딜 데이터/검증의 SSOT는 백엔드이며, 프론트 검증은 보조 수단
