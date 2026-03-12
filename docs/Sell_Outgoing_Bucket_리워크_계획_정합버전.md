# Sell Outgoing Bucket 리워크 계획 (프로젝트 정합 버전)

## 문서 목적
- `sell outgoing bucket` 중 `FILLER_BAD_CONTRACT`, `VETERAN_SALE`의 판정 품질을 개선한다.
- 다만 **현재 코드/데이터에 실제로 존재하는 신호만 사용**해서 단계적으로 리워크한다.
- 구현 시 기존 버킷명(`FILLER_BAD_CONTRACT`, `VETERAN_SALE`)과 우선순위/캡 구조를 유지해 downstream 영향(`listing_policy`, `dealgen`, `repair`)을 최소화한다.

---

## 1) 현재 코드 기준 확인된 상태
기준 파일: `trades/generation/asset_catalog.py`

### 1-1. `FILLER_BAD_CONTRACT` 현재 판정
- 현재 로직은 아래 둘 중 하나면 후보 진입:
  - `c.market.total <= 6.0`
  - `overpay = c.salary_m - c.market.total >= 6.0`
- 이 구조는 저가/저가치 말단 자산을 `bad contract`로 과포착할 위험이 있다.

### 1-2. `VETERAN_SALE` 현재 판정
- 팀이 `SELL`/`SOFT_SELL` posture이거나 `time_horizon == REBUILD`인 경우,
- `age >= 29` 그리고 `market.now >= 6.0`이면 후보 진입.
- 나이 하드컷 중심이라 팀 타임라인과 선수 계약창의 불일치를 충분히 반영하지 못한다.

### 1-3. 현재 시스템에서 이미 활용 가능한 신호
- 선수 단위: `salary_m`, `remaining_years`, `is_expiring`, `market.now`, `market.total`, `snap.age`
- 계약 관련 value breakdown: `contract_gap_cap_share`, `expected_cap_share_avg`, `actual_cap_share_avg`
- 팀 단위: `trade_posture`, `time_horizon`, `signals.flexibility`, `signals.re_sign_pressure`, `signals.core_age`
- 의사결정 컨텍스트: `decision_context.need_map` (예: `CAP_FLEX` 가중치가 있는 경우 활용 가능)

> 참고: 현재 `asset_catalog`에는 apron/tax 전용 신호가 직접 노출되어 있지 않으므로, 해당 개념은 1차 리워크에서 직접 도입하지 않고 **기존 시그널 기반 proxy**로 처리한다.

---

## 2) 리워크 원칙
1. **의미 보존 + 정합성 우선**  
   - 버킷 ID는 유지하고 내부 스코어링/게이트만 교체.
2. **하드컷 축소, 다요인 점수화 강화**  
   - 단일 조건 진입을 줄이고 계약부담·기간·팀상황 결합으로 판정.
3. **단계적 적용**  
   - 1차: 기존 데이터만으로 구현 가능해야 함.
   - 2차: 필요 시 신규 신호(예: apron pressure) 확장.
4. **하위 모듈 호환성 유지**  
   - `player_ids_by_bucket` 출력 형식, 캡(`_bucket_caps_for_posture`), 우선순위(`_outgoing_priority_for_posture`)는 유지.

---

## 3) `FILLER_BAD_CONTRACT` 리워크 방향

## 목표 해석
`FILLER_BAD_CONTRACT`를 “싼 저가 자산”이 아니라 **팀이 정리하고 싶은 계약 부담 자산**으로 재정의한다.

### 3-1. 제거/축소할 요소
- `market.total <= 6.0` 단독 진입 제거.
  - 저가 저효용 자산은 `FILLER_CHEAP` 맥락으로 남기고,
  - `BAD_CONTRACT`는 과지급/기간/팀 유연성 압박이 결합될 때만 진입.

### 3-2. 1차(현행 데이터 기반) 계약부담 점수 설계
- 후보 계산용 핵심값(이미 존재):
  - `negative_money = max(0, actual_cap_share_avg - expected_cap_share_avg)`
    (시장가치 `market.total`과 분리된 cap share 기반 순수 과지급 신호)
  - `years_factor = clamp01(remaining_years / Y_MAX)` (예: `Y_MAX=4`)
  - `team_flex_pressure = 1 - signals.flexibility`
  - `expendability_proxy = surplus_score` (기존 fit 기반 보조 신호)

- 제안 점수(초안):
  - `bad_contract_score =`
    - `w1 * norm(negative_money)`
    - `+ w2 * years_factor`
    - `+ w3 * team_flex_pressure`
    - `+ w4 * expendability_proxy`

### 3-3. 진입 게이트(1차)
- 필수 게이트:
  - `negative_money` 최소치 충족 (예: 소액 오차 제외)
- 보조 게이트(아래 중 1개 이상):
  - `remaining_years >= 2` (또는 비-expiring)
  - `team_flex_pressure` 높음
- 이후 기존처럼 점수 정렬 + posture cap 적용.

### 3-4. 2차 확장 후보
- 팀 재무 압박 신호가 SSOT로 제공되면(`apron/tax pressure`) `team_flex_pressure`를 대체/보강.
- 단, 2차 도입 전까지는 신규 필드 가정 금지.

---

## 4) `VETERAN_SALE` 리워크 방향

## 목표 해석
외부 노출 버킷명은 유지하되, 내부 의미를 **타임라인 불일치 기반 매각 신호**로 재정의한다.

### 4-1. 유지/변경 포인트
- 유지:
  - SELL 계열 posture, REBUILD horizon에서 적극 탐색
  - `market.now`가 일정 수준 이상인 “현재가치 자산” 우선
- 변경:
  - `age >= 29` 하드 게이트를 약화(또는 보조조건화)
  - 팀 윈도우(`time_horizon`)와 선수 계약창(`remaining_years`, `is_expiring`)의 불일치를 중심화

### 4-2. 1차(현행 데이터 기반) 타임라인 불일치 점수 설계
- 핵심 구성요소:
  - `timeline_mismatch`:
    - 팀 `time_horizon`가 `REBUILD/RE_TOOL`일수록,
    - 선수 `age` 높고 계약이 중장기일수록 가중
  - `market_now_norm = norm(market.now)`
  - `age_decline_proxy`:
    - 나이 자체보다 고연령 구간에서의 완만한 리스크 가중
  - `contract_window_risk`:
    - `is_expiring` + `signals.re_sign_pressure` 조합,
    - 혹은 `remaining_years`가 팀 horizon과 어긋나는 경우 가중

- 제안 점수(초안):
  - `veteran_sale_score =`
    - `a1 * timeline_mismatch`
    - `+ a2 * market_now_norm`
    - `+ a3 * age_decline_proxy`
    - `+ a4 * contract_window_risk`

### 4-3. 진입 게이트(1차)
- 팀 조건:
  - 기본: `trade_posture in {SELL, SOFT_SELL}` 또는 `time_horizon == REBUILD`
  - 선택: `RE_TOOL` 포함 여부는 실험 플래그로 점검
- 선수 조건:
  - `market.now` 하한은 유지(품질 필터)
  - `timeline_mismatch` 최소치 또는 (`age_decline_proxy` + `contract_window_risk`) 조합 충족
- 결과:
  - “29세라서 자동 매각”이 아니라,
  - “팀 방향과 시계열이 어긋나는 가치 자산” 중심 선별.

---

## 5) 구현 순서 (안전한 롤아웃)
1. `asset_catalog.py` 내 두 버킷의 **스코어 함수/게이트 함수 분리**
   - 현재 인라인 조건식을 함수화해 테스트 가능성 확보.
2. `FILLER_BAD_CONTRACT` 단독 저가 조건 제거 및 계약부담 점수 반영
3. `VETERAN_SALE`에 타임라인 불일치 점수 반영
4. 기존 버킷 캡/우선순위 로직은 유지
5. 로깅/디버그 메타에 “왜 분류되었는지” 핵심 사유(상위 2~3개)를 남길 수 있도록 확장 검토

---

## 6) 검증 계획

### 6-1. 단위 테스트(필수)
- `FILLER_BAD_CONTRACT`
  - 저가 저효용이지만 과지급이 낮고 단기 계약인 케이스는 제외되는지
  - 과지급+장기+낮은 유연성 케이스가 상위로 오는지
- `VETERAN_SALE`
  - 동일 연령이라도 팀 horizon별로 결과가 달라지는지
  - `market.now` 높고 timeline mismatch 높은 케이스 우선 정렬되는지

### 6-2. 회귀 체크(필수)
- 기존 테스트 스위트에서 downstream(`listing_policy`, `dealgen/repair/targets`) 깨짐 없는지 확인
- posture별 버킷 cap 준수 여부 확인

### 6-3. 품질 지표(권장)
- SELL/REBUILD 팀에서 `VETERAN_SALE` 후보의 평균 `market.now` 유지 여부
- `FILLER_BAD_CONTRACT`에서 `negative_money` 분포가 기존 대비 상향되는지
- `FILLER_CHEAP`와의 경계가 분리되는지(중복/혼선 감소)

---

## 7) 리스크 및 완화
- 리스크: 후보 수 급감/급증으로 생성 딜 다양성 왜곡
  - 완화: threshold를 feature flag 또는 config override로 단계 조정
- 리스크: 팀별 데이터 편차로 특정 posture만 과민반응
  - 완화: posture/horizon별 threshold 보정치 분리
- 리스크: 설명 가능성 저하
  - 완화: bucket 판정 시 reason code 저장(예: `NEGATIVE_MONEY`, `TIMELINE_MISMATCH`)

---

## 8) 최종 정리
- `FILLER_BAD_CONTRACT`는 “저가 자산”이 아니라 “계약 정리 대상”으로 재정의한다.
- `VETERAN_SALE`는 나이 하드컷 중심에서 “팀 타임라인 불일치 매각” 중심으로 전환한다.
- 1차는 **현재 프로젝트에 이미 존재하는 필드만** 사용해 구현 가능하도록 설계하고,
  추후 재무 압박(apron/tax) 신호가 SSOT로 들어오면 2차 확장한다.

---

## 9) (추가) 구체 수정안 적용 시 게임 체감 변화

아래는 `Sell_Outgoing_Bucket_리워크_구체_수정안.md` 기준으로 실제 로직이 반영됐을 때, 기존 대비 플레이 감각이 어떻게 달라지는지 쉽게 풀어쓴 설명이다.

### 9-1. FILLER_BAD_CONTRACT 체감 변화
- 이전:
  - "시장가치가 낮다"는 이유만으로 `FILLER_BAD_CONTRACT`에 들어가는 케이스가 있었음.
  - 그래서 싼 말단 벤치 자원이 "나쁜 계약"처럼 보이는 어색함이 발생.
- 이후:
  - "적정 cap share 대비 실제 cap share 과지급"이 먼저 확인되고,
  - 여기에 "계약기간 부담" 또는 "팀 유연성 압박" 같은 맥락이 붙어야 진입.
- 유저 체감:
  - 저가 자산은 주로 `FILLER_CHEAP` 쪽으로 남고,
  - `FILLER_BAD_CONTRACT`는 진짜로 "돈이 아픈 계약" 위주가 됨.

### 9-2. VETERAN_SALE 체감 변화
- 이전:
  - SELL/REBUILD 팀에서 29세 이상 + 현재가치가 높으면 비교적 쉽게 `VETERAN_SALE` 진입.
  - 팀 방향과의 정합성보다 나이 컷이 앞서는 인상이 있었음.
- 이후:
  - 팀 `time_horizon`과 선수의 나이/계약창이 얼마나 어긋나는지(`timeline_mismatch`)가 핵심.
  - 나이는 여전히 참고하지만, 자동 컷이 아니라 리스크 구성요소로 처리.
- 유저 체감:
  - 리빌딩 팀이 31세 가치 자산을 정리하는 판단은 더 설득력 있게 보이고,
  - 29세 핵심을 무조건 매물로 내놓는 부자연스러움은 줄어듦.

### 9-3. 거래 시장 전체 흐름 변화
- 이전 대비 "분류 이유"가 직관적으로 바뀜:
  - `FILLER_BAD_CONTRACT` = 저가선수 묶음이 아니라 계약부담 정리
  - `VETERAN_SALE` = 고령 하드컷이 아니라 타임라인 불일치 매각
- 결과적으로,
  - SELL 팀 outgoing 풀이 더 NBA 팬 관점의 현실감에 가까워지고,
  - 딜 생성 시 왜 이 선수가 나왔는지 설명하기 쉬워짐.
