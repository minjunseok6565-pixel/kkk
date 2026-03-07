# 트레이드 협상 C안(플레이어/픽 데이터 정합성) 상세 수정안

> 기준 문서: `docs/trade-negotiation-frontend-audit.md`의 C. 플레이어/픽 데이터 정합성(문제 5, 6)
>
> 목적: 이 문서를 그대로 작업 티켓으로 쪼개면, 즉시 패치 가능한 수준의 백엔드/프론트 변경 범위를 확정한다.

---

## 0) 최종 목표(계약)

### Canonical Asset DTO(응답 계약)
- `player` asset 필수 필드
  - `kind: "player"`
  - `player_id: string`
  - `display_name: string` (트레이드 시점 스냅샷)
  - `pos: string` (트레이드 시점 스냅샷)
- `pick` asset 필수 필드
  - `kind: "pick"`
  - `pick_id: string`
  - `year: int`
  - `round: int`
  - `original_team: string`
  - `owner_team: string`
- 서버는 **인박스(`/api/trade/negotiation/inbox`)와 세션 열기(`/api/trade/negotiation/open`) 응답 모두**에 위 필드를 누락 없이 보장.
- 프론트는 **DTO 우선 렌더**를 기본으로 하고, 캐시/디렉터리는 보조(부가 정보)만 담당.
- `Unknown Player`, `---- round pick`은 표시 대체가 아닌 **계약 위반(Contract Violation)** 으로 처리하여 telemetry 전송.

---

## 1) 파일별 수정안 — 백엔드

## 1-1. `app/schemas/trades.py`
### 수정 목적
- 협상 인박스/세션 응답에 대한 응답 스키마를 명문화해서 필수 필드 누락을 코드 레벨에서 막는다.

### 구체 수정
1. 아래 Pydantic 응답 모델 추가
   - `TradeAssetPlayerSnapshot`
   - `TradeAssetPickSnapshot`
   - `TradeAssetSwapSnapshot`(옵션, 현 구조 유지용)
   - `TradeAssetFixedSnapshot`(옵션, 현 구조 유지용)
   - `TradeNegotiationOfferPayload` (`teams`, `legs` 구조 유지하되 asset union 포함)
   - `TradeNegotiationInboxRowResponse`
   - `TradeNegotiationSessionResponse`
2. `player`/`pick` 모델은 필수 필드 누락 시 validation error가 나도록 optional 제거.
3. `legs` 자산 타입은 `kind` discriminator 기반으로 선언(최소 `player`, `pick`에 대해 엄격 검증).

### 패치 포인트
- 기존 request model 하단에 response model 블록 추가.
- 코멘트로 “frontend canonical DTO contract” 명시.

---

## 1-2. `app/api/routes/trades.py`
### 수정 목적
- 응답 직전에 canonical DTO를 채워 넣는 단일 정규화 계층을 추가.

### 구체 수정
1. 헬퍼 추가
   - `_collect_player_ids_from_deal(offer_payload)`
   - `_collect_pick_ids_from_deal(offer_payload)`
   - `_hydrate_player_asset_snapshots(player_ids, db_path)`
     - 소스: `players` + `roster`
     - 반환: `{player_id: {display_name, pos}}`
   - `_hydrate_pick_asset_snapshots(pick_ids, db_path)`
     - 소스: draft pick 소유/원소속 조회 가능한 저장소(현재 코드베이스 기준 `LeagueRepo` 또는 pick 관련 repo)
     - 반환: `{pick_id: {year, round, original_team, owner_team}}`
   - `_canonicalize_offer_assets(offer_payload, *, player_snaps, pick_snaps, session_id, endpoint)`
     - `player` asset에 `display_name`, `pos` 주입
     - `pick` asset에 `year`, `round`, `original_team`, `owner_team` 주입
     - 필수 필드 누락 자산 발견 시
       - 응답에는 `contract_violations[]`에 누락 상세 추가
       - 서버 로그(구조화 로그) 즉시 기록
2. `_build_trade_negotiation_inbox_row()` 변경
   - 기존 `offer_payload`를 위 canonicalizer 통과 후 `offer.deal`에 사용.
   - row level에 `contract_violations`(배열) 추가.
3. `api_trade_negotiation_open()` 반환 변경
   - `session` 객체 안의 `last_offer`/`draft_deal`도 동일 canonicalization 적용.
   - open endpoint 응답에도 `contract_violations`(있다면) 포함.
4. 응답 스키마 적용
   - FastAPI route decorator에 `response_model` 적용(인박스/open 우선).

### 패치 포인트
- `_build_trade_negotiation_inbox_row` 주변(현재 인박스 row 조립부).
- `api_trade_negotiation_inbox`, `api_trade_negotiation_open` return payload 직전.

---

## 1-3. `trades/negotiation_store.py`
### 수정 목적
- 세션 저장 시점에서 snapshot 필드를 같이 저장해, 이후 로스터 변동과 무관하게 재현 가능한 응답을 보장.

### 구체 수정
1. `set_last_offer()` 또는 offer 저장 경로에 canonical snapshot 보강 로직 추가.
   - 저장 포맷 예시:
     - player asset: `player_id`, `display_name`, `pos`
     - pick asset: `pick_id`, `year`, `round`, `original_team`, `owner_team`
2. 저장 시점 강제 보강
   - `last_offer` 저장 시 player/pick canonical snapshot 필드를 즉시 채운 뒤 저장.
   - 필수 필드를 만들 수 없으면 저장 단계에서 `TradeError`를 발생시켜 잘못된 payload 유입을 차단.

### 패치 포인트
- `set_last_offer`, `open_inbox_session` 내부 직렬화/반환 경로.

---

## 1-4. (신규) `app/services/trade_contract_telemetry.py`
### 수정 목적
- 계약 위반을 누락 없이 수집하는 공통 유틸 추가.

### 구체 수정
1. 함수 추가
   - `emit_trade_contract_violation(event: dict) -> None`
2. 동작
   - 1차: python logger로 구조화 경고(`warning`) 기록
   - 2차: Sentry SDK 존재 시 `capture_message` 또는 `capture_exception` + context 태깅
   - 태그: `endpoint`, `session_id`, `asset_kind`, `missing_fields`
3. routes에서 import해 canonicalize 중 위반 발생 시 호출.

### 패치 포인트
- 신규 파일 생성 + `app/api/routes/trades.py`에서 사용.

---

## 2) 파일별 수정안 — 프론트

## 2-1. `static/js/features/market/marketScreen.js`
### 수정 목적
- 렌더링 우선순위를 `playerDirectory`가 아니라 DTO 필드로 전환.

### 구체 수정
1. `formatInboxAssetText(asset, ...)` 수정
   - `player`:
     - `display_name`, `pos`를 1순위 사용
     - `display_name` 누락 시 즉시 contract violation 이벤트 전송 + 화면에 경고 배지(예: `[계약오류] 선수 정보 누락`) 렌더
     - `playerDirectory` fallback은 **개발 편의 fallback**으로만 남기되, 사용 시 telemetry 같이 전송.
   - `pick`:
     - `year`, `round`, `original_team`, `owner_team`를 1순위 사용
     - 필수 필드 누락 시 `formatPickLabel` 호출 금지(즉 `---- round pick` 차단)
     - 누락 시 경고 텍스트 + telemetry 전송
2. `hydrateMarketTradeInboxPlayerDirectory(rows)` 역할 축소
   - 인박스 라벨용 데이터 소스에서 제외.
   - 모달 선수 상세(연봉 등 부가정보)에서만 사용하도록 call-site 정리.
3. 계약 위반 렌더 규칙 추가
   - inbox card / deal top package 둘 다 동일 처리.
   - 문자열 하드코딩: `Unknown Player`, `---- round pick` 제거.
4. `splitDealAssetsForInbox()`에서 asset 사본에 `direction`(`incoming|outgoing`) 부여
   - telemetry payload에서 자산 방향까지 함께 전송 가능하게 준비.

### 패치 포인트
- `formatInboxAssetText`, `renderTradeDealTopPackage`, `renderMarketTradeInbox`, `hydrateMarketTradeInboxPlayerDirectory`.

---

## 2-2. (신규) `static/js/core/telemetry.js`
### 수정 목적
- 프론트 계약 위반 이벤트를 표준화된 방식으로 송신.

### 구체 수정
1. 함수 추가
   - `reportTradeContractViolation(payload)`
2. 동작
   - 콘솔 warning(개발) + `fetch('/api/telemetry/client', ...)` 비동기 fire-and-forget
   - 네트워크 실패 시 앱 동작 영향 없음
3. payload 스펙
   - `event_name: 'trade_contract_violation_detected'`
   - `endpoint`, `session_id`, `asset_kind`, `asset_ref`, `missing_fields`, `screen: 'market_trade_inbox'`

### 패치 포인트
- 신규 파일 생성, `marketScreen.js`에서 import.

---

## 2-3. `static/js/core/format.js`
### 수정 목적
- `formatPickLabel()`이 계약 누락 데이터를 가리는 fallback 도구로 쓰이지 않도록 경계를 명확화.

### 구체 수정
1. 함수 주석/네이밍 정리
   - `formatPickLabel`은 “유효 데이터가 전제된 formatter”임을 명시.
2. 선택안
   - (권장) `strictFormatPickLabel({year, round, ...})` 추가 후, year/round 비정상 시 throw.
   - `marketScreen.js`는 strict formatter 사용.

### 패치 포인트
- 포맷 함수 정의부와 export.

---

## 2-4. `static/js/app/state.js`
### 수정 목적
- 계약 위반 누적 상태를 디버깅 가능하게 유지.

### 구체 수정
1. 상태 필드 추가
   - `marketTradeContractViolations: []`
2. reset 함수 반영
   - `resetMarketTradeInboxState`, `resetMarketTradeDealState`에서 초기화.

### 패치 포인트
- state object + reset helpers.

---

## 3) 파일별 수정안 — 클라이언트/서버 텔레메트리 라우트

## 3-1. `app/api/routes/core.py` (또는 telemetry 전담 신규 route 파일)
### 수정 목적
- 프론트에서 송신한 계약 위반 이벤트를 서버에서 수집.

### 구체 수정
1. endpoint 추가
   - `POST /api/telemetry/client`
   - body: event payload(dict)
2. 검증
   - `event_name`, `screen`, `asset_kind`, `missing_fields` 최소 검증.
3. 처리
   - logger warning으로 구조화 로그 기록
   - sentry가 있으면 capture
   - 응답은 항상 `{ok: true}` (UX 비차단)

### 패치 포인트
- route 함수 추가 및 router 등록 확인.

---

## 4) 테스트/검증 파일별 수정안

## 4-1. `tests/api/test_trade_negotiation_contract_dto.py` (신규)
### 추가 테스트
1. inbox 응답 player asset 필수필드 존재 테스트
2. inbox 응답 pick asset 필수필드 존재 테스트
3. open 응답 필수필드 존재 테스트
4. 필수 필드 누락 payload 주입 시 `contract_violations` 노출 + telemetry 함수 호출(mock) 테스트

---

## 4-2. `static/js/features/market/__tests__/marketScreen.contract.spec.js` (신규)
### 추가 테스트
1. player DTO 존재 시 `display_name/pos` 우선 렌더
2. DTO 누락 + directory 존재 시 fallback 렌더 + violation report 호출
3. pick DTO 누락 시 `---- round pick` 미노출 보장
4. 계약 위반 UI 문구/배지 노출 검증

---

## 5) 작업 순서(권장, 오류 최소화 기준 작업 묶음)

아래 순서는 **의존성 순서 + 충돌 최소화** 기준으로 묶은 것이다. 각 묶음은 한 번에 리뷰/테스트 가능한 최소 단위로 구성한다.

### 묶음 A — 서버 계약 타입 먼저 고정 (스키마/타입 고정)
- 수정 파일
  - `app/schemas/trades.py`
- 작업 내용
  1. canonical asset response model(`player`, `pick`) 추가
  2. inbox/open response model 추가
  3. 필수 필드 optional 제거 + discriminator 선언
- 이 묶음을 먼저 하는 이유
  - 뒤 단계(routes/store/frontend)의 기준 타입이 먼저 확정되어 구현 흔들림을 방지함.

### 묶음 B — 서버 canonicalization 파이프라인 구현 (응답/저장 일관성)
- 수정 파일
  - `app/api/routes/trades.py`
  - `trades/negotiation_store.py`
- 작업 내용
  1. routes에 `_collect_*`, `_hydrate_*`, `_canonicalize_offer_assets` 헬퍼 추가
  2. inbox/open 응답 직전 canonicalization 적용
  3. store의 `set_last_offer` 저장 단계에서 snapshot 강제 보강/검증 적용
- 이 묶음을 B로 묶는 이유
  - 저장/응답 로직을 동시에 맞춰야 DTO 누락이 재발하지 않음.

### 묶음 C — 서버 관측성(telemetry) 연결
- 수정 파일
  - `app/services/trade_contract_telemetry.py` (신규)
  - `app/api/routes/trades.py`
  - `app/api/routes/core.py` (또는 telemetry route 신규 파일)
- 작업 내용
  1. `emit_trade_contract_violation` 구현
  2. canonicalization 중 위반 탐지 시 telemetry 호출 연결
  3. `/api/telemetry/client` 수집 endpoint 추가
- 이 묶음을 C로 분리한 이유
  - B 완료 후 observability만 독립 검증 가능(기능 영향 최소).

### 묶음 D — 프론트 렌더 소스 전환 (DTO-first)
- 수정 파일
  - `static/js/features/market/marketScreen.js`
  - `static/js/core/format.js`
  - `static/js/app/state.js`
- 작업 내용
  1. `formatInboxAssetText`를 DTO 필드 우선 사용으로 전환
  2. 누락 시 fallback 문자열 대신 계약오류 UI + report 트리거
  3. pick 표시에서 strict formatter 경계 적용(`---- round pick` 차단)
  4. 계약 위반 상태 배열(`marketTradeContractViolations`) 추가/초기화
- 이 묶음을 D로 묶는 이유
  - 사용자 체감 변화가 한 묶음에서 완결되고 회귀 확인이 쉬움.

### 묶음 E — 프론트 telemetry 모듈 추가 및 연동
- 수정 파일
  - `static/js/core/telemetry.js` (신규)
  - `static/js/features/market/marketScreen.js`
- 작업 내용
  1. `reportTradeContractViolation` 구현
  2. 계약 위반 분기마다 공통 payload로 전송
- 이 묶음을 E로 분리한 이유
  - UI 렌더 변경(D)과 네트워크 부수효과(E)를 분리하면 디버깅이 단순해짐.

### 묶음 F — 자동화 테스트 일괄 추가
- 수정 파일
  - `tests/api/test_trade_negotiation_contract_dto.py` (신규)
  - `static/js/features/market/__tests__/marketScreen.contract.spec.js` (신규)
- 작업 내용
  1. 백엔드: inbox/open 필수 필드 보장, 누락 시 violation/telemetry 검증
  2. 프론트: DTO 우선 렌더, 누락 시 오류 표시/telemetry 호출 검증
- 이 묶음을 마지막으로 두는 이유
  - A~E 변경을 한 번에 고정한 뒤 테스트를 작성해야 fixture churn이 줄고 안정적으로 통과함.

### 묶음 G — 최종 통합 QA
- 수정 파일
  - (코드 수정 없음, 검증 단계)
- 검증 시나리오
  1. 정상 payload에서 인박스/모달 선수·픽 라벨 정상 표시
  2. 필수 필드 누락 payload 강제 주입 시 계약오류 UI 노출
  3. 누락 케이스에서 서버/클라 telemetry 동시 수집 확인
  4. 화면 전환/재진입 후에도 오류 상태/표시 일관성 확인

---

## 6) 완료 기준(DoD)

- 인박스/오픈 응답의 player/pick 자산 필수필드 누락 0건(신규 생성 데이터 기준)
- `Unknown Player`, `---- round pick` 문자열이 시장 협상 UI에서 0건
- 필드 누락(오염/비정상 데이터) 발생 시
  - UI에 계약 오류가 명시 노출되고
  - telemetry 이벤트가 서버 로그/Sentry로 수집됨
- 회귀 테스트(백엔드/프론트) 모두 통과

---

## 7) 수정 전/후 유저 체감 비교

### 수정 전
- 인박스에서 간헐적으로 `Unknown Player`, `---- round pick`이 노출되어 제안 신뢰도가 떨어짐.
- 실제 자산 정보가 아닌 "현재 로스터 캐시 상태"에 따라 표시가 바뀌어, 같은 제안도 시점에 따라 다르게 보임.
- 데이터 누락이 발생해도 조용히 폴백되어 운영자가 문제를 늦게 인지.

### 수정 후
- 인박스/협상 모달의 선수·픽 라벨이 제안 시점 스냅샷 기준으로 안정적으로 표시됨.
- 데이터 누락은 숨기지 않고 즉시 "계약 오류"로 표면화되어, 사용자와 운영자가 원인을 빠르게 인지.
- 계약 위반 이벤트가 자동 수집되어, 재현 어려운 간헐 이슈도 운영 대시보드/Sentry에서 추적 가능.
- 결과적으로 협상 화면의 정보 신뢰도와 "내가 보는 제안이 맞다"는 체감이 크게 개선됨.
