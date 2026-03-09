# SELL Outgoing Surplus Bucket 통합 적용 계획 (프로젝트 정합 버전)

## 1) 목적

`SURPLUS_LOW_FIT` + `SURPLUS_REDUNDANT`를 하나의 상위 개념(가칭 `SURPLUS_EXPENDABLE`)으로 통합하되,
현재 코드베이스의 데이터 구조/의존성을 깨지 않도록 **단계적으로 적용**한다.

핵심 목표는 아래 2가지다.

1. "팀 약점과 지금 안 맞는다"(misfit)와 "빼도 버틴다"(expendable)를 분리한다.
2. SELL 후보 선정에서 코어/정체성 선수가 잘못 올라오는 비율을 줄인다.

---

## 2) 현재 코드 기준 진단 (프로젝트 사실관계)

### 2.1 현재 버킷 구조

`asset_catalog`의 outgoing 버킷은 현재 아래와 같다.

- `FILLER_BAD_CONTRACT`
- `FILLER_CHEAP`
- `SURPLUS_LOW_FIT`
- `SURPLUS_REDUNDANT`
- `VETERAN_SALE`
- `CONSOLIDATE`

`SURPLUS_LOW_FIT`/`SURPLUS_REDUNDANT`는 별도 cap, 별도 우선순위를 가진다.
SELL 계열 posture에서는 `SURPLUS_LOW_FIT`이 `SURPLUS_REDUNDANT`보다 먼저 소비된다.

### 2.2 점수/신호 계산의 현재 방식

- `fit_vs_team`: `dc.need_map`(현재 팀 기준) vs 선수 공급 벡터로 계산
- `surplus_score`: `1 - fit_vs_team`
- `redundancy_score`: 선수 공급 × `(1 - dc.need_map[tag])`

즉 outgoing 판단이 사실상 **현재 팀 need_map** 의존이며,
leave-one-out(해당 선수를 뺀 동료 로스터 기준) 계산은 없다.

### 2.3 연쇄 영향 범위

두 버킷은 단순 분류용이 아니라 아래에 직접 연결되어 있다.

- proactive listing 허용 버킷/threshold
- SELL 후보 정렬 우선순위
- 딜 생성기/리페어/스켈레톤의 버킷 기반 선수 추출
- counter-offer sweetener 버킷

따라서 "버킷 이름만 변경"하면 하위 모듈이 넓게 깨질 수 있다.

---

## 3) 제안 방향의 타당성 검토

사용자가 제시한 큰 방향은 **현재 코드 문제와 정합**하다.

### 타당한 지점

1. LOW_FIT 단독으로 outgoing 사유를 강하게 쓰는 것은 부작용 가능성이 높다.
2. outgoing은 `fit_vs_team`보다 "제외 후 팀이 버티는가"(replaceability/dependence)가 더 직접적이다.
3. posture는 cap만 바꾸는 것보다 threshold/보호강도까지 같이 바꿔야 자연스럽다.
4. 결과 버킷은 단순화하고, 내부 reason flag를 분리하는 방식이 디버깅/UI 설명에 유리하다.

### 프로젝트 기준 보정이 필요한 지점

1. 현재 후보 정렬/listing threshold가 `surplus_score` 단일 필드에 연결되어 있어,
   즉시 다변량 점수로 바꾸면 하위 모듈 영향이 커진다.
2. 현재 `PlayerTradeCandidate`에는 core/identity/dependence 전용 필드가 없다.
3. 많은 모듈이 `SURPLUS_LOW_FIT`/`SURPLUS_REDUNDANT` 문자열에 직접 의존한다.

=> 결론: 방향은 유지하되, **"계산 확장 → 듀얼 라벨 운영 → 최종 통합"** 순서가 안전하다.

---

## 4) 적용 원칙 (이 레포에 맞춘 설계)

1. **호환성 우선**: 1차 단계에서 기존 버킷 문자열을 유지한다.
2. **의미 우선**: outgoing 전용 신호(`fit_vs_peers`, `peer_cover`, `dependence_risk`)를 먼저 도입한다.
3. **게이트 우선**: `LOW_FIT 단독 진입 금지`를 우선 반영한다.
4. **마이그레이션 강제**: 정렬/threshold 전환은 feature flag + dual-read(신규 키 부재 시 기존 `surplus_score` fallback)를 필수로 적용한다.
5. **설명 가능성 유지**: 최종 버킷은 1개로 가더라도 reason flag는 다중 보관한다.

---

## 5) 단계별 실행 계획

## Phase 0 — 계측/가시화 (선행)

목표: 현재 로직이 어디서 코어 오판을 내는지 수치로 확인.

- `PlayerTradeCandidate` 디버그 메타에 임시 진단값 추가
  - `fit_vs_team`
  - `redundancy_score_current`
  - (추가 예정 신호는 None 허용)
- 팀별 상위 `surplus_score` 선수 중 고시장가/고분전시간 선수 비율 리포트 유틸 추가(테스트용)

산출물:
- 회귀 비교용 baseline snapshot(테스트 fixture)

## Phase 1 — leave-one-out 신호 도입 (버킷명 유지)

목표: 현재 버킷 체계를 유지한 채 outgoing 의미를 교정.

### 1) 신규 신호 계산

선수 `p`별로 아래를 계산한다.

- `peer_need_map(p)`: p 제외 팀 로스터 기준 need
  - R1 기본 구현: `dc.need_map` + `team_supply_total - supply_p` 기반 역보정 근사
  - `fit_engine.compute_team_need_from_supply(...)`는 현재 코드에 없으므로 신규 API 과제로 분리
- `fit_vs_peers(p)`: p 공급 vs `peer_need_map`
- `misfit_peer = 1 - fit_vs_peers`
- `redundancy_peer`: p 공급 × `(1 - peer_need_map)`
- `peer_cover`: p 공급을 동료 공급이 커버 가능한 비율
- `dependence_risk`: `max(0, fit_vs_peers - fit_vs_team)`

### 2) 보호/보정 신호 (현재 데이터에서 가능한 범위)

신규 데이터 소스 없이 현 필드로 1차 근사치 구성하되, `PlayerTradeCandidate`에 없는 value_breakdown 계열 값은 `asset_catalog` 내부에서 명시적으로 주입 경로를 정의한다.

- `core_proxy`
  - 팀 내 market rank
  - 예상 로테이션 기여(가능하면 분전시간, 없으면 market 기반)
- `identity_risk_proxy`
  - 팀 상위 강점 태그 기여 비중(분전시간 없으면 공급 합 기반)
- `contract_pressure`
  - 1순위: `contract_gap_cap_share`
  - 2순위: `actual_cap_share_avg - expected_cap_share_avg`
  - 둘 다 없을 때만 `salary_m - market.total` 기반 근사
- `minutes_squeeze_proxy`
  - 포지션/태그 중복도 기반 근사

### 3) 통합 점수 (내부)

- `expendable_base` (긍정 신호)
- `protection_score` (보호 신호)
- `trade_block_score = expendable_base - protection_weight * protection_score`

주의:
- 기존 `surplus_score`는 호환을 위해 유지한다.
- 정렬/threshold 전환은 **필수 feature flag**로 제어한다.
- 최소 1개 릴리즈 동안 dual-read를 강제한다.
  - 신규 점수 존재 시: `raw_trade_block_score`/`trade_block_score` 사용
  - 신규 점수 부재 시: `surplus_score` fallback

## Phase 2 — 게이트 적용 + 우선순위 수정

목표: LOW_FIT 과잉 진입을 억제.

### 게이트 규칙

`surplus 계열 버킷 진입`은 아래를 만족해야 한다.

- hard-protect 아님
- score 임계치 통과
- 그리고 아래 중 하나 이상
  - `redundancy_peer_norm >= gate`
  - `peer_cover >= gate`
  - `minutes_squeeze_proxy >= gate`
  - `contract_pressure >= gate`

즉 **misfit 단독 진입 금지**.

### 정렬 수정

SELL 후보 정렬에서 우선 순위를 다음으로 교체.

1. bucket priority
2. public request signal desc
3. `trade_block_score` desc
4. expiring desc
5. contract_pressure desc
6. market_total asc
7. player_id

## Phase 3 — 버킷 통합(외부 노출)

목표: 외부에는 `SURPLUS_EXPENDABLE`로 단일화, 내부 reason은 유지.

### 데이터 구조

- 신규 버킷 ID: `SURPLUS_EXPENDABLE`
- reason flags (복수 가능)
  - `LOW_PEER_FIT`
  - `REDUNDANT_DEPTH`
  - `ROLE_BLOCKED`
  - `EXPENSIVE_FOR_ROLE`
- protection flags
  - `CORE_PLAYER`
  - `IDENTITY_ANCHOR`
  - `WEAKNESS_EXPOSURE_RISK`

### 호환 전략

- 일정 기간 `SURPLUS_LOW_FIT`/`SURPLUS_REDUNDANT`를 alias로 동시 제공
- dealgen/counter-offer/listing 정책에서 새 버킷 우선, 구버킷 fallback

## Phase 4 — posture별 철학 분기 강화

현재는 posture별 cap 비중이 큰데, 아래로 확장.

- `SELL/SOFT_SELL`
  - threshold 완화
  - expiring/비코어 베테랑/계약압박 가중 강화
  - core 보호는 유지하되 비코어 보호 약화
- `STAND_PAT`
  - 중립
- `SOFT_BUY/AGGRESSIVE_BUY`
  - threshold 강화
  - core/identity 보호 강화
  - 진짜 중복만 유지

---

## 6) 코드 반영 우선순위 (파일 단위)

1. `trades/generation/asset_catalog.py`
   - 신규 신호 계산/저장
   - surplus 게이트 반영
   - 통합 버킷 및 alias 처리
2. `trades/generation/dealgen/targets.py`
   - SELL 정렬 키 교체 (`raw_trade_block_score` 우선)
   - feature flag + dual-read fallback (`surplus_score`) 적용
3. `trades/orchestration/listing_policy.py`
   - proactive listing 허용 버킷/threshold를 통합 버킷 기준으로 확장
   - feature flag + dual-read fallback (`surplus_score`) 적용
4. `trades/generation/dealgen/types.py`
   - posture별 threshold 테이블 키 추가/마이그레이션
5. `trades/counter_offer/config.py`, `trades/generation/dealgen/utils.py`, `repair.py` 등
   - 구버킷 참조를 alias-safe 형태로 교정
6. 테스트/문서
   - 기존 SURPLUS 2버킷 테스트를 통합 버킷 + alias 공존 케이스로 업데이트

---

## 7) 리스크 및 완화

- **리스크 A: 딜 생성량 급감/급증**
  - 완화: feature flag(필수) + dual-read 기간 운영 + posture별 threshold 스윕 테스트
- **리스크 B: 기존 테스트 대량 깨짐(버킷 문자열 고정)**
  - 완화: alias 기간 운영 + 테스트 fixture 이중 허용
- **리스크 C: 계산 비용 증가(leave-one-out)**
  - 완화: 팀 단위 공급 벡터 사전합 후 선수별 차감 방식으로 O(N·T) 유지

---

## 8) 수용 기준 (Definition of Done)

1. 코어 오판 감소
   - SELL posture에서 상위 outgoing 후보의 core_proxy 평균이 기존 대비 하락
2. 중복 자원 포착 증가
   - `peer_cover`/`redundancy_peer` 상위 선수가 실제 surplus 후보 상위에 더 많이 위치
3. 행동 자연성
   - BUY posture에서 core/identity 보호 지표 상승
4. 호환성
   - 기존 버킷 참조 모듈이 alias 모드에서 회귀 없이 통과

---

## 9) 즉시 실행 권장안 (이번 사이클)

이번 사이클에서는 아래까지만 먼저 수행하는 것을 권장.

1. Phase 1 일부: `fit_vs_peers`, `redundancy_peer`, `peer_cover`, `dependence_risk` 계산 및 로그 노출
2. Phase 2 일부: `LOW_FIT 단독 진입 금지` 게이트 도입
3. 정렬/threshold 경로에 feature flag + dual-read를 반영해 `surplus_score` 단독 의존 완화

이 3개만 반영해도,
- 코어/정체성 자원의 과잉 매물화 감소
- SELL/BUY posture 체감 자연성 개선
- 향후 `SURPLUS_EXPENDABLE` 완전 통합을 위한 데이터 기반 확보


---

## 10) 적용 시 게임 체감 변화 (수정 전/후 쉬운 설명)

아래는 `SURPLUS_EXPENDABLE_구체_수정안.md`의 변경이 실제 게임에서 어떻게 느껴지는지 정리한 것이다.

### 10.1 수정 전

- SELL 팀은 "팀 약점과 잘 안 맞는 선수"를 과하게 매물로 올리는 경향이 있다.
- 그래서 팀 강점을 구성하는 핵심 선수도 `LOW_FIT` 신호 때문에 트레이드 블록에 뜰 수 있다.
- 유저 입장에서는 "왜 저 선수를 팔지?"라는 비현실적인 장면이 나온다.

### 10.2 수정 후

- AI는 먼저 "이 선수를 빼도 팀이 버티는가"를 본다.
  - 동료가 역할을 대체 가능한지(`peer_cover`)
  - 진짜 중복인지(`redundancy_peer_norm`)
  - 코어/팀 정체성인지(`core_proxy`, `identity_risk_proxy`)
- `LOW_FIT`만으로는 매물 진입이 불가능해서, 코어 오판이 크게 줄어든다.
- 결과적으로 트레이드 블록은 "안 맞는 선수"보다 "빼도 되는 선수" 중심으로 바뀐다.

### 10.3 포지션(팀 방향)별 체감

- `SELL/SOFT_SELL`
  - 비코어 베테랑, 계약 부담 선수, 역할 중복 선수가 더 잘 올라온다.
- `STAND_PAT`
  - 과도한 매물 노출이 줄고, 중립적인 운영이 된다.
- `SOFT_BUY/AGGRESSIVE_BUY`
  - 코어/정체성 보호가 강해져 "이겨야 하는 팀이 코어를 쉽게 파는" 상황이 줄어든다.

### 10.4 유저가 보게 되는 대표 변화

1. 강팀의 3&D 핵심 윙이 단순 LOW_FIT로 매물에 뜨는 일이 감소
2. 벤치 역할 중복 자원(비슷한 태그/포지션)이 더 자주 trade block에 등장
3. bad contract 처리와 surplus 처리가 분리되어 딜 맥락이 이해하기 쉬워짐
4. 같은 SELL이라도 팀 상황(posture)에 따라 "누굴 파는지"가 더 자연스럽게 달라짐

한 줄 요약:

> 변경 전은 "팀 약점에 안 맞는가" 중심,
> 변경 후는 "빼도 팀이 유지되는가" 중심으로 바뀌어 트레이드 AI의 설득력이 높아진다.
