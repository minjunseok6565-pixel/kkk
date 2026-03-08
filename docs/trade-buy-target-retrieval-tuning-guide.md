# BUY 타깃 Tiered Retrieval 튜닝 가이드

이 문서는 현재 구현된 BUY 타깃 탐색 파이프라인을 운영/밸런싱 관점에서 조정하기 위한 실무 가이드입니다.

---

## 1) 현재 구현 요약

현재 BUY 타깃 탐색은 `trades/generation/dealgen/targets.py`의 단일 경로에서 동작합니다.

1. **Tier 0 (listed)**: 활성 PUBLIC listing 후보 우선 수집
2. **Tier 1 (non-listed seed)**: cheap pre-score 기반 non-listed 핵심 후보 선정
3. **Tier 2 (optional expand)**: budget/pressure 허용 시 non-listed 확장
4. **Quota merge**: listed 최소 보장 + listed 최대 비중 + non-listed 쿼터 보장
5. **Final rank**: fit/market/salary/need/listing boost 조합으로 최종 정렬

핵심: 기존 need-tag 고정 스캔(`scan_limit = need_n * 3`)은 사용하지 않습니다.

---

## 2) 설정 키별 역할 (SSOT: `DealGeneratorConfig`)

### 2-1. Quota 관련
- `buy_target_listed_min_quota`
  - listed 후보 최소 보장 슬롯.
  - 올리면 시즌 초 블록 중심 시장이 강해짐.
- `buy_target_listed_max_share`
  - listed 비중 상한(0~1).
  - 낮추면 non-listed 발견이 늘고, 높이면 블록 중심 수렴.
- `buy_target_non_listed_base_quota`
  - 시즌 초 non-listed 기본 슬롯.
- `buy_target_non_listed_deadline_bonus_max`
  - deadline 압력으로 추가되는 non-listed 확장 상한.

### 2-2. 스캔 상한 관련
- `buy_target_max_teams_scanned_base`
- `buy_target_max_teams_scanned_deadline_bonus`
- `buy_target_max_players_scanned_base`
- `buy_target_max_players_scanned_deadline_bonus`
- `buy_target_retrieval_iteration_cap`
  - 위 상한이 높을수록 다양성/현실성은 오르지만 계산량도 증가.

### 2-3. Tier2 관련
- `buy_target_expand_tier2_enabled`
  - Tier2 on/off.
- `buy_target_expand_tier2_budget_share`
  - 최대 타깃 수 대비 Tier2 확장 비중.

### 2-4. 랭킹 가중치 관련
- `buy_target_fit_weight`
- `buy_target_market_weight`
- `buy_target_need_weight_scale`
- `buy_target_need_mismatch_floor`
- `buy_target_salary_penalty_weight`
- `buy_target_salary_penalty_cap`

### 2-5. listing boost 관련
- `buy_target_listing_interest_boost_base`
- `buy_target_listing_interest_priority_scale`
- `buy_target_listing_interest_need_weight_scale`
- `buy_target_listing_interest_recency_half_life_days`
- `buy_target_listing_interest_cap`

---

## 3) 튜닝 절차 (권장)

### Step A. 목표 시나리오 먼저 정의
예시:
- 시즌 초: listed 위주, non-listed는 제한적으로
- 마감 임박: non-listed가 확실히 증가
- 전체: 스타/고가치 자산 완전 누락 방지

### Step B. Quota 계열 먼저 조정
1. `listed_min_quota` / `listed_max_share`
2. `non_listed_base_quota` / `non_listed_deadline_bonus_max`

> 먼저 quota를 안정화한 뒤 가중치(스코어) 튜닝으로 넘어가야 디버깅이 쉬움.

### Step C. 스캔 상한/성능 가드 조정
- 팀/선수 cap과 iteration cap을 조절해 성능 예산 맞춤
- 과부하 시 `iteration_cap`과 `tier2_budget_share`를 먼저 낮춤

### Step D. 점수식 조정
- `fit/market/need/salary` 가중치 미세조정
- 특정 자산군(고연봉 스타/유망주)이 과대/과소 평가되는지 점검

---

## 4) 관측 지표 (최소 세트)

튜닝할 때 최소 아래 지표를 함께 봐야 합니다.

1. **BUY target pool 크기** (평균/95p)
2. **listed vs non-listed 타깃 비중**
3. **팀 다양성** (타깃 from_team 고유 수)
4. **고가치 타깃 도달률** (market_total 상위 구간)
5. **평가 예산 소모량** (`validations`, `evaluations`)
6. **reject/counter 비율**

---

## 5) 빠른 처방표 (운영 중 이슈 대응)

### 증상 A: 시즌 초부터 너무 과열됨
- `buy_target_non_listed_base_quota` ↓
- `buy_target_max_teams_scanned_base` ↓
- `buy_target_max_players_scanned_base` ↓

### 증상 B: 마감 임박인데도 비공개 매물 탐색이 약함
- `buy_target_non_listed_deadline_bonus_max` ↑
- `buy_target_max_teams_scanned_deadline_bonus` ↑
- `buy_target_max_players_scanned_deadline_bonus` ↑

### 증상 C: listed만 과도하게 반복됨
- `buy_target_listed_max_share` ↓
- `buy_target_listed_min_quota` 과도값이면 ↓

### 증상 D: 고연봉 스타가 계속 후순위
- `buy_target_salary_penalty_weight` ↓
- `buy_target_salary_penalty_cap` ↓
- `buy_target_market_weight` ↑

### 증상 E: 팀 니즈 색깔이 약함
- `buy_target_need_weight_scale` ↑
- `buy_target_need_mismatch_floor`를 더 보수적으로(더 낮게) 조정

---

## 6) 추천 시작값 (현 상태 기준)

운영 시작 시 크게 무리 없는 기준점:
- `buy_target_listed_min_quota = 6`
- `buy_target_listed_max_share = 0.75`
- `buy_target_non_listed_base_quota = 8`
- `buy_target_non_listed_deadline_bonus_max = 12`
- `buy_target_expand_tier2_enabled = true`
- `buy_target_expand_tier2_budget_share = 0.35`
- `buy_target_retrieval_iteration_cap = 400`

튜닝 단위는 한 번에 10~20% 이내로 조정 권장.

---

## 7) 변경 후 검증 체크리스트

- [ ] listed always-on 테스트 통과
- [ ] quota 분리 테스트 통과
- [ ] deadline 저/중/고 단계 단조 증가 테스트 통과
- [ ] late high-value 후보 미누락 테스트 통과
- [ ] validation/evaluation 소모량이 예산 범위 내인지 확인

---

## 8) 관련 파일 맵

- 구현: `trades/generation/dealgen/targets.py`
- cap 계산: `trades/generation/dealgen/utils.py`, `trades/generation/dealgen/config.py`
- 설정: `trades/generation/dealgen/types.py`
- 테스트:
  - `trades/generation/dealgen/test_targets_buy_tiered_retrieval.py`
  - `trades/generation/dealgen/test_targets_buy_listing_interest.py`
  - `trades/generation/dealgen/test_utils_retrieval_caps.py`
  - `trades/generation/dealgen/test_core_budget_guard.py`
