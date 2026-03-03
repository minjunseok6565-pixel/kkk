# 트레이드 블록/비공개·공개 제안/신규 불만 설계안 (구현 전 구상)

## 1) 현재 코드 구조에서 이미 있는 것

### 1-1. 불만/요구(Agency) 기반
- `player_agency_states` 성격의 상태를 `agency/types.py`의 `AgencyState`로 관리한다.
  - 축 기반 불만(`minutes_frustration`, `team_frustration`, `role_frustration`, `contract_frustration`, `health_frustration`, `chemistry_frustration`, `usage_frustration`)과 `trade_request_level`(0/1/2)이 이미 존재한다.
- 이벤트는 `AgencyEvent`(append-only) 개념으로 처리하며, 응답 처리 파이프라인(`agency/interaction_service.py`, `agency/responses.py`)이 있다.
- `SHOP_TRADE` 응답 타입 및 관련 약속(`agency/promises.py`)이 있어, “트레이드 관련 약속”이라는 게임적 내러티브 뼈대는 이미 존재한다.
- 선수 컨텍스트 메모(`context.mem`)에 `was_shopped` 같은 히스토리를 축적하는 훅이 있다.

### 1-2. 트레이드 시장 오케스트레이션 기반
- `trade_market` 파생 상태가 있고, 스키마 기본값에 `listings`, `threads`, `cooldowns`, `events`가 이미 있다 (`state_modules/state_constants.py`, `trades/orchestration/market_state.py`).
- `threads`는 “팀-팀 간 접촉 지속 상태”를 추적하고, `events`는 시장 사건 로그를 남긴다.
- 즉, 트레이드 블록/유출 루머/공개 전환 같은 기능을 넣을 “상태 저장 장소”는 이미 존재한다.

### 1-3. 협상/제안 흐름 기반
- 현재 API는 `submit`, `negotiation/start`, `negotiation/commit`, `evaluate` 중심이다 (`app/api/routes/trades.py`, `app/schemas/trades.py`).
- 하지만 제안 자체에 `visibility`(비공개/공개) 같은 명시 필드는 없다.
- 따라서 제안 공개 범위를 분기하려면 스키마 확장이 필요하다.

### 1-4. 가치평가(Valuation) 기반
- 트레이드 가치는 `trades/valuation/service.py`가 진입점이며, 순수 평가 엔진(시장가/팀 유틸/결정 정책)으로 이어진다.
- 현재는 데드라인 압박, 관계, 신뢰 등은 일부 반영되지만,
  - “상대 팀이 불만 선수 보유로 급하다”를 가격에 직접 반영하는 명시 규칙은 아직 없다.

---

## 2) 이번 요구사항 대비, 현재 비어있는 핵심

1. **불만 유형의 명시적 분리 부족**
   - 요청한 3종 불만(트레이드하려 함 / 동포지션 영입 시도 / 트레이드 안 해줌)은 현재 축 기반 지표+trade_request_level로만 간접 표현됨.

2. **트레이드 블록의 선수 단위 공개/비공개 모델 부재**
   - `trade_market.listings`는 존재하지만, “선수 단위 블록 등록 + 공개 범위” 계약이 없다.

3. **비공개 제안의 유출 확률 및 공개 전환 이벤트 부재**
   - 루머 이벤트는 있으나, “비공개 제안이 유출되어 대상 선수가 즉시 배신감 불만”이라는 규칙은 없음.

4. **동포지션 영입 시도 불만의 능력치 비교 로직 부재**
   - 대상 선수가 본인보다 약하면 불만 없음, 비슷하면 확률 상승, 강하면 더 상승이라는 함수가 필요.

5. **불만-가치 연동(매도 압박 할인) 부재**
   - 보유팀 급매 압박(트레이드 요청/미이행 불만)과 매수팀의 가치 하향 책정 로직을 연결해야 함.

6. **“트레이드하려 함” 불만의 자동 해소/임계점 폭발 상태 머신 부재**
   - 일정 기간 시도 없으면 해소되되, 반복 시도·유출 누적 시 영구적 결렬(사실상 트레이드 요청)로 전환하는 모델 필요.

---

## 3) 추천 구현 프레임 (코드 착수 전 설계)

## 3-1. 도메인 상태를 먼저 분리

### A) 선수별 트레이드 민감도 상태(신규)
`agency_state.context.trade_grievance` 아래에 JSON으로 시작(초기엔 DB 스키마 변경 최소화):

- `trade_targeting_score` : “자신을 트레이드하려 한다” 누적 노출 점수 (0~1+)
- `trade_targeting_stage` : `NONE | RUMOR | ACTIVE_GRIEVANCE | BROKEN_RELATION`
- `trade_targeting_last_seen_at`
- `same_pos_recruit_score` : 동포지션 영입 시도 노출 누적
- `not_traded_out_score` : “트레이드 시켜주지 않는다” 누적
- `private_leak_count`
- `auto_resolve_eligible` : 자동 해소 가능 여부

> 이유: 처음부터 테이블 컬럼 대량 추가보다, `context`로 실험/튜닝 후 고정 컬럼 승격이 안전.

### B) 트레이드 블록 엔트리 스키마(신규)
`trade_market.listings[player_id]` 표준 형태를 정한다:

- `player_id`, `team_id`
- `visibility`: `PUBLIC | PRIVATE`
- `source`: `MANUAL | AUTO_FROM_PUBLIC_OFFER | AUTO_FROM_REQUEST`
- `status`: `ACTIVE | REMOVED | TRADED`
- `created_at`, `updated_at`, `expires_on`(옵션)
- `meta`: 마지막 제안 ID, 노출 사유 등

> 현재 `listings` 컨테이너가 있으므로 호환성 높게 확장 가능.

### C) 제안 단위 가시성(신규)
협상/제안 DTO에 아래 필드 추가 설계:
- `offer_visibility`: `PRIVATE | PUBLIC`
- (선택) `allow_media_leak`: bool (기본 true)

비공개 제안은 거래 상대팀만 알고, 공개 제안은 즉시 시장 이벤트+블록 반영.

---

## 3-2. 불만 발생 규칙(요구사항 반영)

### 1) “자신을 트레이드 하려고 한다”
발생 트리거:
- 공개 제안에서 본인이 outgoing asset
- 비공개 제안 유출로 본인이 outgoing 노출

예외(모순 방지):
- 이미 본인이 `trade_request_level > 0` 이거나
- 이미 “트레이드 시켜주지 않는다” 단계가 임계 이상이면
- 해당 불만 신규 발생을 스킵

추가:
- 단발 노출은 `auto_resolve_eligible = true`
- 반복 노출/유출 누적 시 `BROKEN_RELATION` 진입 + `trade_request_level` 상향

### 2) “자신의 동 포지션에 선수를 영입하려고 한다”
트리거:
- 내가 영입하려는 대상 선수 포지션 == 기존 로스터 선수 포지션

확률 함수(권장):
- `delta = target_ovr - incumbent_ovr`
- `delta <= -2`: 0%
- `-1 ~ +1`: 낮은 확률 시작
- `+2 ~ +5`: 선형 증가
- `+6 이상`: 상한 근접

보정 인자:
- 자존심/에고/레버리지(mental, leverage)
- 입지(스타터/프랜차이즈면 더 민감)

### 3) “자신을 트레이드 시켜주지 않는다”
트리거:
- 아래 중 하나 만족 후 시간 경과:
  - 선수가 trade request 상태
  - 팀이 SHOP_TRADE 약속을 했는데 미이행
  - 공개적으로 블록에 올려두고 장기 미처리

증가 방식:
- 일/주 단위로 `not_traded_out_score` 상승
- 데드라인 임박 시 가중치 상승

---

## 3-3. 비공개 ↔ 공개 전환(유출)

### 이벤트 모델
- 비공개 제안 접수 시 `TRADE_OFFER_PRIVATE_SENT`
- 확률 롤 성공 시 `TRADE_OFFER_LEAKED`
- 유출되면 자동으로 `PUBLIC` 전환 이벤트 `TRADE_OFFER_PUBLICIZED`

### 유출 확률 구성(권장)
`p_leak = base + rival_media_tendency + big_name_bonus + repeated_contact_bonus`
- 상한 clamp (예: 0.55)

### 유출 시 즉시 효과
- 대상 outgoing 선수: 즉시 높은 강도의 배신감 이벤트
- 트레이드 블록 공개 등록
- 시장 events/rumor에 기록

---

## 3-4. 가치평가 반영(급매 압박/매수 할인)

## 원칙
- 선수의 절대 시장가를 바꾸기보다,
- **협상 맥락에서 seller reservation price를 낮추는 방식**이 안정적.

### 구현 위치(추천)
- `trades/valuation/service.py`에서 DecisionContext 구성 후,
- 팀별 `distress_discount`를 계산해 decision 단계(accept/counter threshold)에 반영.

### distress_discount 입력
- 선수가 `trade_request_level > 0`
- 또는 `not_traded_out_score` 임계 이상
- 혹은 `BROKEN_RELATION`

예시:
- 경미: 2~4%
- 중간: 5~8%
- 심각: 9~12%

### 양면 효과
- **보유팀**: “빨리 팔아야 함” → 요구치 하향
- **매수팀**: “상대 급함 인지” → 제안가 하향 정당화

---

## 3-5. 상태 머신(자동 해소 vs 폭발)

`TRADE_TARGETING` 상태 머신:

- `NONE`
- `RUMOR` (노출 1회)
- `ACTIVE_GRIEVANCE` (반복 노출)
- `BROKEN_RELATION` (임계점 초과, 반영구)

전이:
- 노출 없음 N일 지속: `RUMOR -> NONE`
- 추가 노출/유출: 단계 상승
- `BROKEN_RELATION` 진입 시 자동 해소 금지 + trade request 유도

임계점 예시:
- 30일 내 노출 3회 또는
- 유출 1회 + 추가 노출 1회

---

## 4) 실제 코드 반영 시 추천 순서 (리스크 낮은 순)

1. **관측 이벤트 먼저 추가**
   - private/public/leak 이벤트만 심고 UI 로그 확인
2. **트레이드 블록 스키마 도입**
   - `listings` read/write helper부터 만들기
3. **불만 계산기 모듈 추가**
   - `agency/trade_grievance.py`(신규)로 분리
4. **협상 API에 visibility 확장**
   - schema/route/negotiation_store 반영
5. **valuation distress_discount 연결**
   - 결정 임계치에 한정 반영(시장가 직접 변형 금지)
6. **튜닝/밸런싱 + 텔레메트리 대시보드**
   - 유출률, 불만 생성률, 자동해소율, 공개전환율 모니터

---

## 5) 테스트 전략(코드 착수 시)

- 단위 테스트
  - 동포지션 불만 확률 함수(OVR delta별)
  - 모순 방지 규칙(이미 트레이드 요청 중이면 “트레이드하려 함” 미발생)
  - 상태 머신 전이(자동해소/폭발)
- 통합 테스트
  - private offer -> leak -> public conversion -> grievance 즉시 반영
  - public offer 시 outgoing 선수 블록 자동 등록
  - 장기 미이행 시 “트레이드 안 해줌” 증가
- 회귀 테스트
  - 기존 trade submit/negotiation 정상 동작
  - 기존 agency 이벤트 처리 파이프라인 무결성

---

## 6) 설계상 결정 포인트(구현 전에 확정 필요)

1. 비공개 제안 유출 확률을 상대팀 성향 기반으로 할지, 리그 전역 상수로 할지
2. 트레이드 블록 공개/비공개를 UI에서 어떻게 보여줄지(인간 유저 팀만 상세 노출 여부)
3. “반영구 결렬”의 해소 수단을 완전 제거할지(감독 교체/우승/맥스 재계약 등 특수 해소 루트 허용 여부)
4. distress_discount를 valuation 총액에 적용할지, decision threshold에만 적용할지

---

## 7) 결론

현재 코드베이스는 **agency(불만/요청), trade_market(시장 상태), valuation(거래 의사결정)** 이 이미 분리되어 있어,
요구사항을 “신규 모듈 1~2개 + DTO 확장 + 정책 함수 추가”로 비교적 안전하게 확장할 수 있다.

핵심은 다음 3가지다.
1) 선수 감정 상태 머신을 명시적으로 추가,
2) 제안 가시성(private/public)과 유출 이벤트를 거래 흐름에 삽입,
3) 불만이 협상 가격(요구치)에 반영되도록 distress_discount를 도입.

