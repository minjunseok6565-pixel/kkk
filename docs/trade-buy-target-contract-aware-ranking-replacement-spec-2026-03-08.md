# BUY 타깃 랭킹 연봉 로직 전면 교체 확정안 (2026-03-08)

## 0) 목표/원칙 (확정)
- 레거시의 `salary_m` 직접 감점(`-salary_penalty`)을 **완전 삭제**한다.
- BUY 타깃 점수는 아래 3축으로 분리한다.
  1. **선수 순수 가치(Player Core Value)**: 연봉 정보 미사용
  2. **계약 가치(Contract Value)**: 기대 cap share 대비 실제 cap share의 차이로만 계산
  3. **팀 상황 보정(Team Salary Sensitivity)**: 동일 계약이라도 팀 상황별 영향 강도 차등 적용
- 절대 연봉이 높다는 이유만으로 음수 처리하지 않는다.
- 기존 세이브/DB 호환은 고려하지 않고, 신규 로직으로 완전 대체한다.

---

## 1) 수정 파일 확정 (코드 변경 대상)

### A. `trades/generation/dealgen/types.py`
**삭제(레거시):**
- `buy_target_salary_penalty_weight`
- `buy_target_salary_penalty_cap`

**신규(확정 default):**
- `buy_target_player_core_weight_fit: float = 0.50`
- `buy_target_player_core_weight_market: float = 0.35`
- `buy_target_player_core_weight_need: float = 0.35`
- `buy_target_contract_gap_softness_cap_share: float = 0.060`  
  (gap / 6% cap-share에서 tanh 기울기 기준)
- `buy_target_contract_base_weight: float = 0.30`  
  (team 보정 전 계약 가치 영향도)
- `buy_target_contract_apron_mult_below_cap: float = 0.55`
- `buy_target_contract_apron_mult_over_cap: float = 0.90`
- `buy_target_contract_apron_mult_above_1st: float = 1.25`
- `buy_target_contract_apron_mult_above_2nd: float = 1.70`
- `buy_target_contract_posture_mult_aggressive_buy: float = 1.10`
- `buy_target_contract_posture_mult_soft_buy: float = 1.00`
- `buy_target_contract_posture_mult_stand_pat: float = 0.90`
- `buy_target_contract_posture_mult_soft_sell: float = 0.70`
- `buy_target_contract_posture_mult_sell: float = 0.60`
- `buy_target_contract_deadline_mult_min: float = 0.90`
- `buy_target_contract_deadline_mult_max: float = 1.15`
- `buy_target_contract_team_sensitivity_min: float = 0.35`
- `buy_target_contract_team_sensitivity_max: float = 2.20`
- `buy_target_pre_score_contract_weight: float = 0.18`

---

### B. `trades/generation/asset_catalog.py`
**핵심 변경:** `IncomingPlayerRef`에 계약 가치/순수 가치 분해값을 직렬화해 BUY 랭킹에서 재활용 가능하게 만든다.

**`IncomingPlayerRef` 필드 추가:**
- `basketball_total: float`  (연봉 비사용 순수 가치)
- `contract_total: float`    (기대 대비 계약 가치)
- `contract_gap_cap_share: float`  (ExpectedCapShare - ActualCapShare, 평균)
- `expected_cap_share_avg: float`
- `actual_cap_share_avg: float`

**구현 규칙(확정):**
1. 선수 market price 계산 시 `MarketPricer.price_snapshot(...).meta["value_breakdown"]`에서
   - `basketball.total`
   - `contract.total`
   을 추출한다.
2. `contract_gap_cap_share`는 valuation meta rows 기반으로 계산한다.
   - 각 잔여 시즌 `gap_y = fair_pct_y - actual_salary_y / cap_y`
   - 할인 가중 평균: `w_y = max(0.35, (1 - contract_year_discount_rate) ^ years_ahead)`
   - `gap_avg = sum(gap_y * w_y) / sum(w_y)`
3. `expected_cap_share_avg`, `actual_cap_share_avg`도 같은 가중 평균으로 기록한다.
4. 값이 비어 있으면 0.0 fallback.

---

### C. `trades/generation/dealgen/targets.py`
레거시 salary penalty를 완전 제거하고, 아래 확정 수식으로 교체한다.

#### C-1) 제거 대상
- `_cheap_pre_score()`의 `salary_soft_penalty` 로직 삭제
- `_final_rank()`의 `salary_penalty` 로직 삭제
- rank/pre-score에서 `salary_m` 직접 사용 로직 전부 삭제

#### C-2) 신규 helper 함수 (추가)
1. `_contract_gap_score(gap_cap_share, cfg)`
```python
# 입력: gap_cap_share = ExpectedCapShare - ActualCapShare
# 출력: [-1, +1]
soft = max(0.005, cfg.buy_target_contract_gap_softness_cap_share)
return math.tanh(gap_cap_share / soft)
```

2. `_team_contract_sensitivity(team_situation, cfg)`
```python
apron_mult = {
  "BELOW_CAP": cfg.buy_target_contract_apron_mult_below_cap,
  "OVER_CAP": cfg.buy_target_contract_apron_mult_over_cap,
  "ABOVE_1ST_APRON": cfg.buy_target_contract_apron_mult_above_1st,
  "ABOVE_2ND_APRON": cfg.buy_target_contract_apron_mult_above_2nd,
}.get(apron_status, cfg.buy_target_contract_apron_mult_over_cap)

posture_mult = {
  "AGGRESSIVE_BUY": cfg.buy_target_contract_posture_mult_aggressive_buy,
  "SOFT_BUY": cfg.buy_target_contract_posture_mult_soft_buy,
  "STAND_PAT": cfg.buy_target_contract_posture_mult_stand_pat,
  "SOFT_SELL": cfg.buy_target_contract_posture_mult_soft_sell,
  "SELL": cfg.buy_target_contract_posture_mult_sell,
}.get(posture, cfg.buy_target_contract_posture_mult_stand_pat)

# deadline_pressure in [0,1]
deadline_mult = cfg.buy_target_contract_deadline_mult_min + \
    (cfg.buy_target_contract_deadline_mult_max - cfg.buy_target_contract_deadline_mult_min) * clamp01(deadline_pressure)

sens = apron_mult * posture_mult * deadline_mult
return clamp(sens, cfg.buy_target_contract_team_sensitivity_min, cfg.buy_target_contract_team_sensitivity_max)
```

3. `_player_core_score(ref, need_similarity, cfg)`
```python
fit = clamp01(ref.tag_strength)
# basketball_total은 음수 가능성 대비 양끝 클리핑 후 정규화
basketball_norm = clamp01((ref.basketball_total + 15.0) / 45.0)
need_term = max(cfg.buy_target_need_mismatch_floor,
                cfg.buy_target_player_core_weight_need * (clamp01(need_similarity) - 0.5))

core = (
  cfg.buy_target_player_core_weight_fit * fit
  + cfg.buy_target_player_core_weight_market * basketball_norm
  + need_term
)
return core
```

#### C-3) 최종 rank 확정식
```python
contract_score = _contract_gap_score(ref.contract_gap_cap_share, cfg)
team_sens = _team_contract_sensitivity(buyer_ts, cfg)
contract_term = cfg.buy_target_contract_base_weight * contract_score * team_sens

rank = player_core + contract_term + listing_boost + jitter
```
- `jitter`는 기존 랜덤 타이브레이커(0~0.01) 유지.

#### C-4) pre-score 확정식
```python
core_pre = 0.62 * clamp01((ref.basketball_total + 15.0) / 45.0) + 0.38 * clamp01(need_similarity)
contract_pre = cfg.buy_target_pre_score_contract_weight * _contract_gap_score(ref.contract_gap_cap_share, cfg)
pre_score = core_pre + contract_pre
```
- pre-score에서도 연봉 직접 패널티 금지.

---

### D. 테스트 파일

#### 1) `trades/generation/dealgen/test_targets_buy_tiered_retrieval.py`
- 레거시 salary penalty 전제 assertion 제거
- 신규 assertion 추가:
  - 동일 선수 순수가치에서 `contract_gap_cap_share`가 높을수록 rank 상승
  - 동일 계약에서 `ABOVE_2ND_APRON` 팀이 `BELOW_CAP` 팀보다 계약 점수 영향 절대값이 큼
  - contract gap이 음수인 고연봉 스타도 `basketball_total`이 매우 높으면 상위 유지 가능

#### 2) `trades/generation/dealgen/test_targets_buy_listing_interest.py`
- listing boost가 contract term과 합산되더라도 cap 적용/정렬 안정성 유지 확인

#### 3) 신규: `trades/generation/dealgen/test_targets_buy_contract_value.py`
필수 케이스(확정):
1. **Underpaid breakout**: gap +8%p -> rank 유의미한 플러스
2. **Fair max star**: gap ~0 -> 계약 항 중립
3. **Overpaid long-term role**: gap -10%p -> rank 마이너스
4. **Team context**: 동일 gap에서 apron/posture/deadline에 따라 계약 영향 가중 차등

---

## 2) 삭제/대체 정책 (확정)
- BUY 타깃 선택 경로에서 salary amount(`salary_m`)를 점수 계산에 쓰는 코드를 전부 제거한다.
- `salary_m`는 정렬 tie-breaker/출력 정보용으로만 유지 가능(점수 영향 0).
- 문서/튜닝 가이드의 salary penalty 파라미터 항목은 contract-value 계열로 전면 교체한다.

---

## 3) 밸런스 안전장치 (상업 운영 기준)
- `contract_gap_score = tanh(gap/softness)`로 극단값 포화 처리해 폭주 방지
- `team_sensitivity` min/max 클램프로 상태 이상치 방어
- pre-score contract 영향도를 final rank보다 작게(`0.18`) 유지해 탐색 다양성 확보
- 최종 정렬 tie-break는 기존 market/ID 순서를 유지해 결정론 안정성 유지

---

## 4) 구현 순서 (확정)
1. `asset_catalog.py`: incoming ref 확장 + valuation breakdown/contract gap 기록
2. `types.py`: 레거시 salary penalty config 삭제, 신규 contract-aware config 추가
3. `targets.py`: pre-score/final rank를 신규 수식으로 전면 교체
4. 테스트 3종 반영(기존 2개 수정 + 신규 1개)
5. 문서/튜닝 가이드 파라미터명 동기화

---

## 5) 완료 판정 기준 (Done)
- BUY 타깃 코드 경로에 `salary_penalty`, `buy_target_salary_penalty_*` 참조가 0건
- 테스트에서 contract gap의 부호/크기와 team sensitivity에 따라 rank 변화 방향이 일관
- 상위 고가치 선수가 "고연봉 자체"가 아니라 "과/저평가 계약"으로 분리 평가됨
