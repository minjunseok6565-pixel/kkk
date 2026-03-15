# 니즈 중요도 기반 임계값 + 소프트 게이트 + 하한 캡(항상 배제) 구현 계획

## 0) 목표/범위

본 계획은 **패키지 합산 포화(cap/saturation) 항목은 제외**하고, 아래 3가지만 구현한다.

1. **니즈 태그 중요도(need weight) 기반 임계값 조정**
2. **하드 컷오프 대신 소프트 게이트 적용**
3. **항상 배제되는 하한 캡(hard floor cap) 적용**

적용 범위는 다음 두 경로다.
- 개별 선수 fit 평가(`FitEngine`)의 supply 반영
- 패키지 need/supply 보정(`PackageEffects`)의 incoming supply 반영

`need_attr_profiles.py`의 태그별 attr 정의/공식은 이번 작업에서 변경하지 않는다.

---

## 1) 핵심 설계

### 1-1. 용어 정의

- `need`: 팀의 태그 중요도(`need_map[tag]`, 0~1)
- `raw_supply`: 선수/패키지의 태그 공급 원점수(기존 산출값, 0~1)
- `effective_supply`: 게이트 적용 후 실제 반영되는 공급값

---

### 1-2. 중요도 기반 임계값 함수

태그별 임계값을 고정값이 아니라 need에 따라 변화시키는 함수로 정의한다.

- 기본 파라미터
  - `threshold_max` : need가 매우 낮을 때 임계값(엄격)
  - `threshold_min` : need가 매우 높을 때 임계값(완화)
  - 제약: `0 <= threshold_min <= threshold_max <= 1`

- 함수(선형 기본안)

```text
threshold(need) = threshold_max - (threshold_max - threshold_min) * clamp(need, 0, 1)
```

특성:
- need ↑  => threshold ↓  (절박할수록 다소 낮은 능력 허용)
- need ↓  => threshold ↑  (중요하지 않으면 더 엄격)

---

### 1-3. 항상 배제 하한 캡(hard floor cap)

soft gate 이전 단계에서 **절대 배제 구간**을 둔다.

- 파라미터
  - `hard_floor_cap` (0~1): 이 값 미만은 무조건 0 처리

- 규칙

```text
if raw_supply < hard_floor_cap:
    effective_supply = 0
```

의미:
- 아무리 need가 높아도, `hard_floor_cap` 미만 능력은 “실질 기여 없음”으로 본다.

권장 제약:
- `hard_floor_cap <= threshold_min` 권장
  - 하한 캡이 소프트 임계값보다 높으면 사실상 hard cut 성격이 과도해질 수 있음

---

### 1-4. 소프트 게이트 함수

하드 컷 대신 연속 함수로 공급 반영률을 조절한다.

#### 후보 A: Smoothstep (권장, 튜닝 쉬움)

- 파라미터
  - `soft_width` (>0): 임계값 주변 완만 구간 폭
- 정의

```text
x = (raw_supply - threshold(need)) / soft_width
u = clamp(0.5 + 0.5 * x, 0, 1)
gate = u*u*(3 - 2*u)   # smoothstep
effective_supply = raw_supply * gate
```

특성:
- threshold 근처에서 부드럽게 증가
- 경계 불연속 완화

#### 후보 B: Sigmoid

```text
gate = 1 / (1 + exp(-k * (raw_supply - threshold(need))))
effective_supply = raw_supply * gate
```

특성:
- 수학적으로 매끈하나, `k` 직관성이 상대적으로 떨어짐

> 본 계획은 A(Smoothstep)를 기본 채택한다.

---

### 1-5. 패키지/개별 평가 적용 규칙

- **개별 fit(`FitEngine.score_fit`)**
  - 기존 `s = supply[tag]` 대신 `s_eff = gate(s, need_weight)` 사용
  - `matched_needs`, `contribution_by_tag`, `unmet/excess` 모두 `s_eff` 기준

- **패키지 보정(`PackageEffects._need_supply_balance_adjustment`)**
  - 기존 `supply = in_supply[tag]` 대신 `s_eff = gate(sum_supply, need_weight)` 사용
  - `fulfilled = min(need, s_eff)`, `excess = max(0, s_eff - need)`

주의:
- 이번 범위에서는 **패키지 합산 포화/포화함수 추가는 하지 않음**.
- 즉 집계 구조는 기존(sum) 유지, 반영단에서 gating만 적용.

---

## 2) 파일별 수정 계획

## 2-1. `trades/valuation/fit_engine.py`

### 변경 포인트

1. `FitEngineConfig`에 게이팅 파라미터 추가
   - `supply_gate_enabled: bool = True`
   - `supply_gate_threshold_min: float`
   - `supply_gate_threshold_max: float`
   - `supply_gate_hard_floor_cap: float`
   - `supply_gate_soft_width: float`

2. 내부 헬퍼 추가
   - `_need_threshold(need_w, cfg)`
   - `_soft_gate(raw_supply, need_w, cfg)`
   - 파라미터/제약 검증(역전값 방지)

3. `score_fit()`에서 태그별 공급 반영 변경
   - 기존 `s = supply.get(tag_s, 0)`
   - 변경 `s_raw -> s_eff`

4. explainability 확장
   - `FitScoreBreakdown`에 (필요 시) 아래 필드 추가
     - `raw_supply_by_tag`
     - `effective_supply_by_tag`
     - `gate_by_tag`
     - `threshold_by_tag`
     - `hard_floor_blocked_tags`

### 비고
- `compute_player_supply_vector()`(raw 산출)는 유지하고, `score_fit()` 반영 단계에서 gating 적용.

---

## 2-2. `trades/valuation/package_effects.py`

### 변경 포인트

1. `PackageEffectsConfig`에 게이팅 파라미터 추가(이름/기본값은 FitEngine과 통일)
   - `need_supply_gate_enabled`
   - `need_supply_gate_threshold_min/max`
   - `need_supply_gate_hard_floor_cap`
   - `need_supply_gate_soft_width`

2. 내부 헬퍼 추가
   - `_need_threshold(...)`
   - `_soft_gate(...)`

3. `_need_supply_balance_adjustment()`의 태그 루프 수정
   - `s_raw = in_supply[tag]`
   - `s_eff = gate(s_raw, need)`
   - `fulfilled/excess` 계산에 `s_eff` 사용

4. 메타 확장
   - 기존 `incoming_supply` 유지 + 아래 추가
     - `incoming_supply_raw`
     - `incoming_supply_effective`
     - `gate_by_tag`
     - `threshold_by_tag`
     - `hard_floor_blocked_tags`

### 비고
- `slot_efficiency_bonus` 로직은 유지 (요청사항상 saturation 항목은 제외)

---

## 2-3. `trades/valuation/types.py`

### 변경 포인트

- `FitAssessment` 혹은 관련 explainability 타입이 `FitScoreBreakdown` 직렬화와 연결돼 있다면 신규 필드 반영
- 타입 변경에 따른 직렬화 안정성 확인

---

## 2-4. `trades/valuation/team_utility.py` / `trades/valuation/service.py`

### 변경 포인트

- `FitEngineConfig`/`PackageEffectsConfig`를 생성·전달하는 경로에서 신규 파라미터 공급
- 미설정 시 기본값으로 안전 동작하도록 fallback 보장

---

## 2-5. 테스트 파일

### 신규/수정 권장

1. `trades/valuation/test_*` 중 fit 관련 테스트
   - hard floor 미만 공급은 항상 0인지
   - need 증가 시 threshold 하락으로 `effective_supply`가 증가하는지
   - gate 연속성(임계점 전후 단절 완화) 검증

2. package effects 테스트
   - 동일 raw_supply라도 need 높은 태그가 더 반영되는지
   - hard floor 캡이 need와 무관하게 배제하는지
   - 기존 slot_efficiency 보너스와 충돌 없는지

3. 회귀 테스트
   - 게이트 off 시 기존 수치와 동일(혹은 허용 오차 내 동일)

---

## 3) 권장 기본값(초기 튜닝 스타트)

> 실제 값은 테스트/로그 기반으로 2~3회 튜닝 전제

- `threshold_max = 0.45`
- `threshold_min = 0.25`
- `hard_floor_cap = 0.12`
- `soft_width = 0.20`

해석:
- 매우 낮은 공급(0.12 미만)은 완전 배제
- 중요도 낮은 태그는 0.45 부근부터 유의미 반영
- 중요도 높은 태그는 0.25 부근부터 반영 확대

---

## 4) 검증/관측 계획

### 4-1. 로그/메타 관측

- 딜 평가 로그에 태그별 `raw -> effective` 변환 기록
- 어떤 태그가 hard floor에 의해 배제됐는지 카운트
- need 분포별 gate 반영률 히스토그램

### 4-2. 시나리오 테스트

1. **절박 니즈 태그**: 중간 raw_supply 선수도 일부 반영되는지
2. **비절박 니즈 태그**: 동일 선수에서 반영률이 낮아지는지
3. **극저능력 태그**: hard floor로 항상 0 처리되는지
4. **다수 저품질 선수 패키지**: 과도한 충족 오인 감소 여부

---

## 5) 단계별 실행 순서

1. `fit_engine.py`에 게이트 함수/파라미터 추가
2. `package_effects.py`에 동일 정책 반영
3. 타입/서비스 wiring(`types.py`, `team_utility.py`, `service.py`) 정리
4. 테스트 추가/수정 및 회귀 검증
5. 기본값 1차 튜닝 + 메타 로그 확인

---

## 6) 비범위(이번 계획에서 제외)

- 태그별 attr 공식/가중치 변경(`need_attr_profiles.py` 수정 없음)
- 패키지 합산 포화(saturation) 신규 도입
- need_map 생성 로직(`data/team_situation.py`) 구조 변경

---

## 7) 기대 효과

- “절박한 니즈는 조금 낮은 능력도 수용”과
- “너무 낮은 능력은 무조건 배제”를 동시에 표현
- hard cutoff 단절 문제를 soft gate로 완화
- 기존 파이프라인(sum 집계/slot 효율 보너스)과 충돌 없이 단계적 도입 가능

