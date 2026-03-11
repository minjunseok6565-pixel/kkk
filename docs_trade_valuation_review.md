# 트레이드 밸류에이션 로직 점검 브리핑

## 결론 요약

**현재 상태는 "로직 자체가 완전히 잘못됐다"기보다는, 여러 보수적 레버가 동시에 누적되며 거래 성사 확률을 과도하게 낮추는 구조**에 가깝습니다.

- 밸류에이션 파이프라인(시장가 → 팀 효용 → 패키지 효과 → 의사결정)은 책임 분리가 잘 되어 있고 구조적으로는 합리적입니다.
- 다만, `fit/risk/finance/package`의 **복수 페널티가 곱/합으로 중첩**되고,
- 최종 의사결정에서 **최소 잉여 요구치(min_surplus_required) + 낮은 overpay 허용 + 높은 counter 성향**이 결합되며,
- 딜 생성기에서 **극단적 마진 discard 필터**까지 추가로 걸려, 실제 제안 흐름이 크게 줄어들 수 있습니다.

즉, 이번 이슈는 **주로 수치 튜닝/운영 파라미터 문제**로 보는 게 타당하되,
아래에 별도로 정리한 **의도-구현 불일치 1건(min_surplus_required 음수 허용 소실)**은
버그성 항목으로 우선 점검이 필요합니다.

---

## 1) 현재 로직 구조 검토 (설계 타당성)

### 1-1. 밸류에이션 파이프라인은 정상적

서비스 레이어는 다음을 순서대로 수행합니다.

1. 법규/규칙 검증(validator)
2. 컨텍스트 구성(팀 상황/의사결정 컨텍스트)
3. 자산 평가(시장가 + 팀효용 + 패키지 효과)
4. 최종 수락/거절/카운터 판정

이 책임 분리는 모듈화 관점에서 바람직합니다. 서비스 코드에서도 각 레이어를 재해석/중복하지 않도록 명시되어 있습니다.

### 1-2. 의사결정 레이어의 해석도 일관적

`DecisionPolicy`는 이미 계산된 `net_surplus`를 기반으로
- `required_surplus`(수락 요구치),
- `overpay_allowed`(음수 허용치),
- `counter corridor`
를 적용해 ACCEPT/COUNTER/REJECT를 판정합니다.

로직 자체(경계 기반 판정)는 일반적인 협상 모델로 납득 가능합니다.

---

## 2) 왜 보수적으로 체감되는가 (핵심 원인)

아래는 "개별 로직은 합리적이지만 합성 시 보수화"되는 지점입니다.

### 2-1. 팀 효용에서 곱연산 페널티가 누적

플레이어 가치 조정 과정에서,

- fit 배율,
- risk 할인,
- finance 페널티

가 순차 곱 형태로 반영됩니다. 각 단계가 0.85~0.95 수준만 되어도 누적 시 의미 있게 작아집니다.

예: 0.90 × 0.90 × 0.88 ≈ 0.712

즉, 개별 페널티는 moderate해 보여도 총합이 강한 discount로 작동합니다.

### 2-2. fit gate 임계치가 높은 구간으로 이동 가능

`min_fit_threshold`는 유효 fit strictness에 따라 0.45~0.70 범위에서 올라갑니다. strict 팀/상황에서는 fit fail이 잦아지고, fit 엔진의 threshold penalty가 추가되어 incoming 가치가 더 깎일 수 있습니다.

### 2-3. finance penalty scale 상단이 큼

`finance_penalty_scale`는 유효 금융보수성에 따라 최대 1.60까지 커질 수 있습니다. 동시에 팀 효용의 finance factor는 score 기반으로 다시 감산되므로, 고연봉/장기 계약 자산이 많이 저평가될 가능성이 있습니다.

### 2-4. min_surplus_required + counter_rate 조합

`min_surplus_required`는 협상 강도와 posture에 따라 증가하고(SELL 계열 가산),
`counter_rate`는 toughness가 높으면 크게 올라가며,
결정 정책에서 deterministic 모드일 때 `counter_rate > 0.5`이면 회색지대에서 COUNTER로 기울어집니다.

결과적으로 ACCEPT가 줄고 COUNTER/REJECT가 늘 수 있습니다.

### 2-5. 딜 생성기의 early discard가 추가로 제안 풀 축소

생성기에는 마진이 크게 나쁜 후보를 폐기하는 필터가 있습니다.
기본값(`discard_if_overpay_below=-18`, `discard_if_reject_margin_below=-14` 등)은 합리적일 수 있으나,
상위 레이어가 이미 보수적일 때는 "수리 가능한 후보"도 초기에 사라질 수 있습니다.

### 2-6. `w_now`/`w_future` 상보 구조가 극단값에서 한 축을 과도하게 약화 가능

`decision_context`에서 `w_future = 1.0 - w_now` 형태를 사용하므로,
urgency/성향이 한쪽으로 강하게 쏠리면 다른 축이 자동으로 내려갑니다.
픽/유망주(미래 축) 혹은 즉시전력(현재 축) 중 한쪽이 과소평가되면,
이미 존재하는 fit/risk/finance 페널티와 합성되어 체감 보수성이 더 커질 수 있습니다.

### 2-7. 의도-구현 불일치: `min_surplus_required` 음수 허용이 decision 단계에서 소실

`decision_context`는 공격적 BUY 상황에서 `min_surplus_required`가 음수가 될 수 있게 설계되어
"약간 손해를 감수하는 수용"을 표현합니다.

하지만 `decision_policy`에서 이를 `max(0.0, ...)`으로 재절단하여
실제 판정에서는 음수 허용이 사라집니다.

이 항목은 단순 튜닝이라기보다 **정책 의도가 구현에서 누락된 불일치(버그성)**로 보는 것이 맞습니다.

---

## 3) 판단: 로직 오류 vs 튜닝 문제

### 판단

- **구조/알고리즘 자체는 대체로 합리적**
- **다만 `min_surplus_required` 음수 허용 소실은 의도-구현 불일치로 우선 점검 필요**
- **그 외 제안 급감의 주원인은 튜닝 이슈 가능성이 높음**

특히 "팀 효용 할인 누적" + "협상 임계 강화" + "생성기 discard"가 동시 작동하면,
리그 전체 체감은 급격히 보수적으로 변합니다.

---

## 4) 권장 튜닝 순서 (저위험 → 고효과)

### Phase 0 (즉시 확인: 의도-구현 불일치)

1. **`min_surplus_required` 음수 허용 의도 복구 여부 결정**
   - 현재 `decision_policy`에서 `max(0.0, ...)`로 절단되어 공격적 BUY 의도가 소실될 수 있음
   - 정책 의도 유지가 맞다면 clamp 하한을 음수까지 열거나, 별도 안전장치와 함께 허용 범위 복구

### Phase 1 (정책/수치 조정)

2. **min_surplus_required 범위 축소**
   - 현재: 대략 `[-0.03, 0.10]` + posture 보정
   - 제안: 상단 완화(예: `0.07~0.08` 수준) 또는 SELL 가산치 축소

3. **counter_rate 완화 또는 stochastic 모드 검토**
   - deterministic에서 `>0.5`면 gray zone 카운터 고정 성향
   - `stochastic_counter=True`로 전환하거나, base counter_rate를 하향

   - 추가로 `counter_corridor_ratio`(기본 6%) 완화도 병행 검토

4. **finance_penalty_scale 상단 완화**
   - 현재 상단 1.60은 고보수 팀에서 체감이 큼
   - 상단/기울기 소폭 하향

5. **fit threshold 상단 완화**
   - `min_fit_threshold` 상단 0.70 → 0.62~0.65 수준 검토

6. **`w_now`/`w_future` 극단값 완충**
   - 한 축이 치솟을 때 반대축이 과도하게 하락하지 않도록 하한/완충 검토

### Phase 2 (거래량 복원용)

7. **generation discard 기준 완화**
   - `discard_if_reject_margin_below`, `discard_if_overpay_below`를 1~3포인트 완화
   - sweetener가 복구 가능한 near-miss가 살아남도록 조정

8. **패키지 효과 중 hole/roster waste/cap_room_cost 축 캡 점검**
   - 이미 cap이 있으나, 로스터/포지션 편차가 큰 리그에서는 누적 충격이 클 수 있음

---

## 5) 최소 계측(telemetry) 제안

수정 전/후 1시즌 샘플에서 아래를 팀 posture별로 비교하면 원인 분리가 명확해집니다.

- `net_surplus - required_surplus` 분포 (buyer/seller)
- verdict 비율 (ACCEPT/COUNTER/REJECT)
- REJECT 사유 비중 (`FIT_FAILS`, `INSUFFICIENT_SURPLUS`, `PACKAGE_EFFECTS`)
- discard 사유별 카운트
- sweetener 시도 대비 성공률

이 지표로 "평가가 낮아서"인지, "판정 임계치가 높아서"인지, "생성 단계에서 죽는지"를 구분할 수 있습니다.

---

## 6) 최종 제언

- 현재 문제를 **로직 폐기/재작성 이슈로 보지 말고**,
  **튜닝 파이프라인(DecisionContext knobs + generation discard + 일부 package cap) 재보정 이슈**로 접근하는 것이 적절합니다.
- 우선은 Phase 1만 적용해도 "거래 제안량/성사율"이 유의미하게 회복될 가능성이 높습니다.
- 이후 telemetry 기반으로 Phase 2를 미세 조정하는 것이 안전합니다.
