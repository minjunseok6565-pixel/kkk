# Sell Outgoing Bucket 쉽게 설명

이 문서는 현재 브랜치 코드 기준으로, **SELL(또는 SOFT_SELL) 팀이 내보낼 선수(outgoing)를 어떤 bucket으로 분류하는지**를 비개발자 관점에서 정리한 문서입니다.

## 한 줄 요약
SELL 쪽 outgoing bucket은 총 6개입니다.

1. `VETERAN_SALE`
2. `SURPLUS_LOW_FIT`
3. `SURPLUS_REDUNDANT`
4. `FILLER_BAD_CONTRACT`
5. `FILLER_CHEAP`
6. `CONSOLIDATE`

> 참고: SELL 우선순위에서는 `CONSOLIDATE`가 가장 뒤이고, 실제 cap도 0이라 SELL에서는 사실상 사용되지 않습니다.

---

## bucket별 쉬운 설명

### 1) VETERAN_SALE
- 의미: “지금 가치가 있는 베테랑을 리툴/리빌딩을 위해 파는 칸”
- 조건(요약):
  - 나이 29세 이상
  - 현재 가치(`market.now`)가 일정 수준 이상(6.0 이상)
  - 팀 자세가 SELL/SOFT_SELL 이거나 시간축이 REBUILD일 때 활성
- SELL에서의 특성:
  - 우선순위가 가장 높음
  - cap도 큼(5)

### 2) SURPLUS_LOW_FIT
- 의미: “우리 팀 스타일/로스터와 핏이 낮은 선수”
- 조건(요약):
  - `fit_vs_team <= 0.45`인 선수
- 직관:
  - 능력 자체와 별개로 “우리 팀에 안 맞는” 선수를 정리하는 용도

### 3) SURPLUS_REDUNDANT
- 의미: “같은 유형이 팀에 이미 많거나, 팀 니즈에 비해 과잉인 선수”
- 조건(요약):
  - 중복도 점수 `redundancy_score = 공급(supply) * (1-need)` 합산
  - 이 점수가 0.55 이상이면 후보
- 직관:
  - 팀이 필요로 하지 않는 포지션/역할에 자원이 몰려 있으면 여기로 감

### 4) FILLER_BAD_CONTRACT
- 의미: “가성비가 나쁜(비싸거나 부담되는) 계약 정리용 칸”
- 조건(요약):
  - `overpay = salary - market.total`
  - `market.total <= 6.0` 또는 `overpay >= 6.0`
- 직관:
  - 실력 대비 계약이 무겁다고 판단되는 선수를 딜 필러로 활용

#### FILLER_BAD_CONTRACT를 조금 더 자세히

이 버킷의 핵심은 질문 주신 것처럼 **“선수 가치를 연봉으로 얼마나 현실적으로 환산하느냐”**입니다.

코드상으로는 아래 순서로 판단합니다.

1. 먼저 `market.total`(선수 시장가치)를 계산
   - 농구 실력/잠재력: `OVR` 기반 현재가치 + 스타 보너스 + 나이 기반 미래가치 반영
   - 계약가치: “적정연봉(fair salary) - 실제연봉(actual salary)”의 차이를 추가 반영
2. 현재 연봉(`salary_m`)과 `market.total`을 비교
3. 아래 중 하나면 `FILLER_BAD_CONTRACT` 후보
   - `market.total <= 6.0` (시장가치 자체가 낮음)
   - `salary_m - market.total >= 6.0` (연봉이 가치보다 크게 비쌈)

즉, 단순히 “연봉이 높다”가 아니라 **“시장가치 대비 얼마나 비싼가(과지급)”**를 보려는 설계입니다.

---

## 선수 가치 ↔ 연봉 환산 로직, 현실성은 어떤가?

결론부터 말하면, 현재 로직은 **방향성은 현실적**이고 **실무적으로도 꽤 일관적**이지만,
NBA 프런트가 쓰는 수준의 정교한 모델과 비교하면 **의도적으로 단순화된 버전**입니다.

### 현실적인 부분(장점)

1. **농구 가치와 계약 가치를 분리해서 합산**
   - “잘하는 선수 + 나쁜 계약” 같은 케이스를 표현할 수 있음
   - 나쁜 계약은 실제로 음수 자산처럼 작동 가능

2. **적정연봉을 리그 캡 비율(cap share)로 계산**
   - 절대 달러보다 시즌별 캡 환경을 반영하기 쉬움
   - 시즌이 바뀌어도 스케일이 덜 흔들림

3. **멀티이어 계약은 연차별로 보고 할인(discount) 적용**
   - 먼 미래 연도 가치를 덜 쳐서, 현실적인 거래 감각에 맞춤

4. **결측 데이터는 중립값/방어 로직으로 fail-safe 처리**
   - 데이터 빈칸 때문에 가치가 폭주하는 걸 억제

### 단순화된 부분(한계)

1. **핵심 지표가 OVR 중심**
   - 실제 시장은 포지션별 수급, 플레이오프 적합성, 부상 리스크, 팀 컨텍스트를 더 세밀히 봄

2. **계약 리스크 모델이 비교적 얕음**
   - 보장/비보장, 옵션 구조, 에이징 커브 불확실성, 장기 부상 확률 등은 제한적으로만 반영

3. **FILLER_BAD_CONTRACT의 컷오프가 고정값**
   - `6.0`/`6.0` 같은 문턱은 이해는 쉽지만,
     시즌/리그 환경/팀 전략별로 동적 최적화는 아직 약함

4. **실거래 데이터 기반 보정(calibration) 정보가 문서상 드러나지 않음**
   - “최근 N시즌 트레이드 결과와 얼마나 맞는지”를 자동으로 맞추는 루프가 있으면 더 현실적

### 비개발자 관점의 한 줄 평가

- 지금 모델은 **“실무에 쓸 수 있는 규칙 기반 + 경제성 반영”** 단계로는 충분히 합리적입니다.
- 다만 **초정밀 현실 재현**이 목표라면, OVR 이외 변수와 실제 거래 데이터 기반 보정을 더 넣는 것이 다음 단계입니다.

### 5) FILLER_CHEAP
- 의미: “작은 연봉의 보조 자산(딜 금액 맞추기/로스터 조정용)”
- 조건(요약):
  - 연봉 `<= 2.5`
  - 시장가치 `<= 4.5`
- 직관:
  - 대형 코어 자산이 아니라, 거래 구조를 맞추는 보조 조각

### 6) CONSOLIDATE
- 의미: “자산 묶기(2~3명 ↔ 1명 업그레이드)용 중간급 자산 칸”
- 조건(요약):
  - BUY 성향 팀(`AGGRESSIVE_BUY`/`SOFT_BUY`)에서만 활성
  - 팀 내 가치 순위 30~70% 중간 구간 위주
- SELL에서의 특성:
  - cap=0이라 SELL에는 사실상 안 쓰임

---

## SELL에서 실제로는 어떻게 동작하나?

### A. 먼저 bucket별 최대 인원(cap)을 posture별로 정함
SELL 기준 cap:
- `VETERAN_SALE`: 5
- `SURPLUS_LOW_FIT`: 7
- `SURPLUS_REDUNDANT`: 6
- `FILLER_BAD_CONTRACT`: 4
- `FILLER_CHEAP`: 4
- `CONSOLIDATE`: 0

### B. 같은 선수가 여러 bucket에 걸치면 “우선순위”로 1회만 배정
SELL 우선순위:
1. `VETERAN_SALE`
2. `SURPLUS_LOW_FIT`
3. `SURPLUS_REDUNDANT`
4. `FILLER_BAD_CONTRACT`
5. `FILLER_CHEAP`
6. `CONSOLIDATE`

즉, 한 선수가 동시에 `VETERAN_SALE`과 `SURPLUS_LOW_FIT` 조건을 만족하면, SELL에서는 앞선 `VETERAN_SALE`로 먼저 잡히고 뒤 bucket에서는 중복 제외됩니다.

### C. Proactive Listing(자동 공개 매물 등록)에서는 일부 bucket만 사용
현재 로직에서 자동 공개 매물(트레이드 블록) 대상으로 허용되는 bucket은 아래 5개입니다.
- `VETERAN_SALE`
- `SURPLUS_LOW_FIT`
- `SURPLUS_REDUNDANT`
- `FILLER_BAD_CONTRACT`
- `CONSOLIDATE`

즉, `FILLER_CHEAP`는 **딜 구조(샐러리 매칭/보조 조각) 용도**로는 쓰이지만, 자동 트레이드 블록 공개 대상에서는 제외됩니다.

SELL posture에서 기본 threshold(낮을수록 등록 쉬움):
- `SURPLUS_LOW_FIT`: 0.32
- `SURPLUS_REDUNDANT`: 0.38
- `FILLER_BAD_CONTRACT`: 0.62
- `VETERAN_SALE`: 0.35
- `CONSOLIDATE`: 0.90

허용 bucket에 한해 `surplus_score >= bucket별 threshold`를 만족해야 자동 등록 후보가 됩니다.

---

## 비개발자용 비유

- `VETERAN_SALE`: “지금 값 나올 때 파는 중고차”
- `SURPLUS_LOW_FIT`: “성능은 괜찮은데 우리 용도와 안 맞는 장비”
- `SURPLUS_REDUNDANT`: “이미 창고에 같은 물건이 많은 재고”
- `FILLER_BAD_CONTRACT`: “유지비 대비 효율이 떨어지는 자산”
- `FILLER_CHEAP`: “금액 맞추기용 소액 부품”
- `CONSOLIDATE`: “여러 중급 자산 묶어서 상급 1개로 바꾸기용 재료”
