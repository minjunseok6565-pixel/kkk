# BUY 타깃 탐색 재설계 구현 명세 (파일별 확정안)

> 기준 문서: `docs/trade-buy-target-retrieval-redesign-plan-2026-03-08.md`
> 목적: 실제 코딩 착수 전, **파일별 변경점/구체 로직/작업 순서**를 확정한다.

---

## 1) 구현 범위 요약
- 2-1 이슈 해결 범위에 집중한다.
  - need-tag 인덱스 강결합 완화
  - `scan_limit = need_n * 3` 제거
  - listed always-on + non-listed deadline 확장
  - 계산량 가드레일(쿼터 분리, 상한, early stop)
- 기존 세이브 호환은 고려하지 않는다(신규 config/데이터 구조 허용).

---

## 2) 파일별 수정 확정안

## A. 데이터 모델/설정 계층

### A-1. `trades/generation/dealgen/types.py`

#### 변경 목적
- 신규 retrieval 경로에 필요한 quota/상한/정규화 가드 설정값을 SSOT로 정리한다.

#### 확정 수정
1. BUY retrieval 모드 플래그
   - `buy_target_retrieval_mode`는 제거한다.
   - 신규 tiered retrieval만 단일 경로로 유지한다.
2. Tier quota/스캔 상한 config 추가
   - `buy_target_listed_min_quota: int = 6`
   - `buy_target_listed_max_share: float = 0.75`
   - `buy_target_non_listed_base_quota: int = 8`
   - `buy_target_non_listed_deadline_bonus_max: int = 12`
   - `buy_target_max_teams_scanned_base: int = 8`
   - `buy_target_max_teams_scanned_deadline_bonus: int = 18`
   - `buy_target_max_players_scanned_base: int = 120`
   - `buy_target_max_players_scanned_deadline_bonus: int = 220`
3. Tier2/성능 가드 config 추가
   - `buy_target_expand_tier2_enabled: bool = True`
   - `buy_target_expand_tier2_budget_share: float = 0.35`
   - `buy_target_retrieval_iteration_cap: int = 400`
4. 점수 정규화/need 가중치 config 추가 (contract-aware)
   - `buy_target_need_mismatch_floor: float = -0.20`
   - `buy_target_player_core_weight_fit: float = 0.50`
   - `buy_target_player_core_weight_market: float = 0.35`
   - `buy_target_player_core_weight_need: float = 0.35`
   - `buy_target_contract_gap_softness_cap_share: float = 0.060`
   - `buy_target_contract_base_weight: float = 0.30`
   - `buy_target_contract_apron_mult_*`
   - `buy_target_contract_posture_mult_*`
   - `buy_target_contract_deadline_mult_min/max`
   - `buy_target_contract_team_sensitivity_min/max`
   - `buy_target_pre_score_contract_weight: float = 0.18`
5. 레거시 설정 정리
   - `incoming_pool_per_tag` 등 need-tag 탐색 전용 파라미터는 삭제한다.

---

### A-2. `trades/generation/asset_catalog.py`

#### 변경 목적
- need-tag 인덱스 외에 BUY 전역 탐색용 candidate 풀을 제공.

#### 확정 수정
1. `TradeAssetCatalog`에 전역 incoming 풀 추가
   - `incoming_all_players: Tuple[IncomingPlayerRef, ...]`
   - 정렬 기준(기본):
     - `(-market_total, salary_m, -remaining_years, player_id)`
2. catalog build 시 생성
   - 기존 lock/recent-signing-ban 통과 선수들을 tag 인덱싱과 함께
     `incoming_all_players`에도 1회 추가.
3. 중복 제거
   - 선수 ID 기준 1개만 유지(태그별 다중 ref와 분리).
4. 구조 정리
   - `incoming_by_need_tag`, `incoming_cheap_by_need_tag`는 BUY 탐색 경로에서 제거한다.
   - SELL 또는 다른 기능에서 필요 시 별도 검토 후 유지 여부를 결정한다.

---

## B. BUY 타깃 탐색 로직

### B-1. `trades/generation/dealgen/targets.py` (핵심)

#### 변경 목적
- `select_targets_buy()`를 tiered retrieval + weighted ranking 구조로 재작성.

#### 확정 수정

##### 1) 함수 구조 단순화
- `select_targets_buy(...)`를 tiered retrieval 단일 구현으로 교체한다.
- `_select_targets_buy_legacy(...)`는 만들지 않고, 기존 need-tag 스캔 구현은 삭제한다.

##### 2) Tiered retrieval 상세
- 입력 파생값
  - `deadline_pressure = clamp(ts.constraints.deadline_pressure, 0, 1)`
  - `urgency = clamp(ts.urgency, 0, 1)`
  - `intensity = 0.65 * deadline_pressure + 0.35 * urgency`
- 동적 쿼터
  - `listed_quota_min = cfg.buy_target_listed_min_quota`
  - `non_listed_quota = base + round(bonus_max * intensity)`
- 동적 스캔 상한 (S-curve)
  - `s(x) = x*x*(3 - 2*x)` (smoothstep)
  - `teams_cap = base + round(team_bonus * s(intensity))`
  - `players_cap = base + round(player_bonus * s(intensity))`

##### 3) Tier 구성
- Tier 0 (listed)
  - active public listing && from_team == listing.team_id인 후보 전량 수집.
  - 단, hard eligibility(자기팀 제외, ban, seller cooldown, seller catalog 존재)는 동일 적용.
- Tier 1 (cheap non-listed)
  - `incoming_all_players`를 훑으며 listed 제외 후보를 cheap pre-score로 상위 추림.
  - cheap pre-score:
    - `0.62*basketball_norm + 0.38*need_similarity + pre_score_contract_weight*contract_gap_score`
- Tier 2 (expanded non-listed)
  - `buy_target_expand_tier2_enabled`가 true이고 deadline/urgency가 높을 때만 가동.
  - Tier1에서 안 뽑힌 잔여 후보 일부를 teams_cap / players_cap / iteration_cap 내에서 확장.

##### 4) scan_limit 제거 방식
- `need_n * 3` 및 need-tag 기반 선별 루프를 완전 제거.
- 대신 아래 보장 규칙 적용:
  - listed 후보는 최소 쿼터 보장
  - non-listed 후보도 최소 base 쿼터 보장
  - 어느 한쪽이 budget을 잠식하지 못하도록 `listed_max_share` 적용

##### 5) Ranking 상세 (need는 가중치)
- 최종 점수 `rank`:
  - `rank = player_core + contract_term + listed_bonus + noise`
  - `player_core = w_fit*fit + w_market*basketball_norm + need_term`
  - `contract_term = contract_base_weight * tanh(contract_gap/softness) * team_sensitivity`
- need bonus
  - `need_similarity = Σ_tag (team_need[tag] * player_supply[tag]) / Σ_tag team_need[tag]`
  - `need_bonus = need_weight_scale * (need_similarity - 0.5)`
  - 하한: `need_mismatch_floor`
- contract/team sensitivity
  - `contract_gap = expected_cap_share_avg - actual_cap_share_avg`
  - `team_sensitivity`는 apron/posture/deadline multiplier를 곱하고 min/max clamp 적용
- listed bonus
  - `buy_target_listing_interest_*` 계산은 유지하되 retrieval 우선권이 이미 있으므로 cap 과도시 clamp.

##### 6) diversity/cooldown guard
- 동일 `from_team` 과밀 방지:
  - 팀별 상위 후보 수를 soft cap(예: `max(2, round(max_targets*0.35))`) 후 초과분 감점.
- 동일 player 반복 노출:
  - 기존 `target_repeat_penalty`와 합성 적용.

---

### B-2. `trades/generation/dealgen/utils.py`

#### 변경 목적
- deadline/urgency 기반 탐색 상한 계산 유틸, 정규화 유틸을 공용화.

#### 확정 수정
1. 수치 유틸 추가
   - `_smoothstep01(x: float) -> float`
   - `_safe_norm(x, lo, hi) -> float`
2. 탐색 상한 계산기
   - `compute_buy_retrieval_caps(ts, cfg) -> (teams_cap, players_cap, intensity)`
3. 테스트 가능한 순수 함수로 분리(단위테스트 대상).

---

## C. 파이프라인 연동/예산 가드

### C-1. `trades/generation/dealgen/core.py`

#### 변경 목적
- retrieval 단계에서 budget 소진/점유율 가드를 적용.

#### 확정 수정
1. BUY 경로에서 target 생성 직후 가드
   - `budget.max_targets` 하드 컷 유지.
2. Tier2 early-stop 연동
   - `stats.validations/evaluations` 소진 임계치 접근 시 Tier2 비활성 힌트 전달.
3. 디버그 태그(선택)
   - 후보 생성 통계( listed/non-listed/tier2 사용량 )를 stats debug 필드에 남김.

---

### C-2. `trades/generation/dealgen/config.py`

#### 변경 목적
- 기존 budget scaling과 tiered retrieval 상한 값의 결합 규칙 정리.

#### 확정 수정
1. `_scale_budget()`는 유지.
2. 별도 helper 추가:
   - `_scale_buy_retrieval_limits(cfg, team_situation) -> Dict[str, int|float]`
   - 이 값은 targets tiered 모드가 사용.

---

## D. 테스트 파일

### D-1. 신규: `trades/generation/dealgen/test_targets_buy_tiered_retrieval.py`

#### 확정 테스트 케이스
1. listed always-on
   - deadline=0이어도 listed 후보 포함.
2. non-listed monotonic expansion
   - deadline 증가 시 non-listed 채택 수 단조 증가.
3. quota 분리
   - listed 과밀이어도 non-listed base quota 유지.
4. scan_limit 회귀 방지
   - 선두 구간 소모 상황에서도 후속 고가치 후보 진입 확인.
5. need=weight only
   - need 불일치 후보 존재 + score 하향 확인.
6. teams_cap / players_cap
   - 강한 deadline에서만 cap이 유의미하게 확대되는지 확인.

### D-2. 수정: `trades/generation/dealgen/test_targets_buy_listing_interest.py`
- retrieval 우선권 도입 이후에도 listing bonus 계산이 유효한지 회귀 검증.
- cap/recency 동작 기존 기대치 조정.

### D-3. 선택(가능하면 추가): `trades/generation/dealgen/test_utils_retrieval_caps.py`
- `_smoothstep01`, `_safe_norm`, `compute_buy_retrieval_caps` 단위 검증.

---

## E. 문서/운영

### E-1. `docs/trade-buy-target-retrieval-redesign-plan-2026-03-08.md`
- 이번 명세 문서 링크 추가(실행 상세 문서로 참조).

### E-2. 신규(선택): `docs/trade-buy-target-retrieval-tuning-guide.md`
- 운영 튜닝용 파라미터 설명(쿼터, 상한, need/salary 가중치).

---

## 3) 구체 로직 확정 (의사코드)

```python
# targets.py (tiered only)
need_map = resolve_need_map(...)
listed_meta = _active_public_listing_meta_by_player(tick_ctx)
teams_cap, players_cap, intensity = compute_buy_retrieval_caps(ts, cfg)

listed_pool = []
non_listed_pool = []
for ref in catalog.incoming_all_players:
    if not eligible(ref):
        continue
    if is_listed(ref, listed_meta):
        listed_pool.append(ref)
    else:
        non_listed_pool.append(ref)

# Tier0
tier0 = rank_listed_first(listed_pool)

# Tier1
tier1_seed = top_k_by_cheap_score(non_listed_pool, k=non_listed_quota + reserve)

# Tier2 (optional expand)
if cfg.buy_target_expand_tier2_enabled and intensity > threshold:
    tier2 = expand_from_residual(non_listed_pool - tier1_seed,
                                 teams_cap=teams_cap,
                                 players_cap=players_cap,
                                 iter_cap=cfg.buy_target_retrieval_iteration_cap)
else:
    tier2 = []

merged = quota_merge_with_guarantee(
    tier0, tier1_seed, tier2,
    listed_min_quota=cfg.buy_target_listed_min_quota,
    non_listed_base_quota=cfg.buy_target_non_listed_base_quota,
    listed_max_share=cfg.buy_target_listed_max_share,
    max_targets=budget.max_targets,
)

ranked = final_rank(merged, need_map, cfg, listed_meta)
return ranked[:budget.max_targets]
```

---

## 4) 최적 구현 순서 (작업 묶음 기준)

아래 순서는 **리스크가 낮고, 중간 검증이 쉬운 순서**로 구성했다.

### 1단계: 설정/유틸 기반 깔기 (저위험, 병렬 가능)
- 작업 파일
  - `trades/generation/dealgen/types.py`
  - `trades/generation/dealgen/utils.py`
  - `trades/generation/dealgen/config.py`
- 작업 내용
  - 신규 config 키 추가
  - smoothstep/normalization/cap 계산 유틸 추가
  - retrieval limit scale helper 추가
- 완료 기준
  - 신규 config 로드/기본값 문제 없음
  - 유틸 단위 테스트 통과

### 2단계: catalog 전역 incoming 풀 추가 (중위험)
- 작업 파일
  - `trades/generation/asset_catalog.py`
- 작업 내용
  - `incoming_all_players` 필드 추가 및 build 경로 구현
  - 중복 제거/정렬 기준 확정
- 완료 기준
  - catalog 빌드 성공
  - BUY 타깃 생성이 신규 전역 풀 경로에서만 수행됨

### 3단계: BUY tiered retrieval 본체 구현 (고위험 핵심)
- 작업 파일
  - `trades/generation/dealgen/targets.py`
- 작업 내용
  - `select_targets_buy` 단일 경로 교체
  - Tier0/1/2 retrieval + quota merge + 최종 ranking 구현
  - `scan_limit = need_n * 3` 및 need-tag 탐색 루프 완전 삭제
- 완료 기준
  - 기존 테스트 + 신규 핵심 테스트 1차 통과
  - 성능 폭증 없이 타깃 수 안정 생성

### 4단계: 파이프라인 budget 연동/가드 마무리 (중위험)
- 작업 파일
  - `trades/generation/dealgen/core.py`
- 작업 내용
  - Tier2 early-stop, budget 임계치 연동
  - 디버그/통계 포인트 추가
- 완료 기준
  - evaluation/validation 예산 초과 없음

### 5단계: 테스트 보강 및 회귀 안정화 (고중요)
- 작업 파일
  - `trades/generation/dealgen/test_targets_buy_tiered_retrieval.py` (신규)
  - `trades/generation/dealgen/test_targets_buy_listing_interest.py`
  - `trades/generation/dealgen/test_utils_retrieval_caps.py` (선택)
- 작업 내용
  - listed always-on, quota 분리, non-listed 확장, scan_limit 회귀 방지 검증
- 완료 기준
  - 신규/기존 테스트 통과
  - deadline 단계별 기대 동작 확인

### 6단계: 문서 정리/튜닝 가이드 추가 (저위험)
- 작업 파일
  - `docs/trade-buy-target-retrieval-redesign-plan-2026-03-08.md`
  - `docs/trade-buy-target-retrieval-tuning-guide.md` (선택)
- 작업 내용
  - 구현 반영 상태와 튜닝 지침 문서화
- 완료 기준
  - 다음 작업자/튜너가 문서만으로 파라미터 조정 가능

---

## 5) 착수 전 체크리스트
- [ ] `types.py` config 추가 후 기본값/주석 정리 완료
- [ ] `asset_catalog.py` 전역 풀 필드 추가 완료
- [ ] `targets.py` 단일 tiered 경로에서 fixed scan limit 제거 확인
- [ ] listed/non-listed quota 분리 가드 동작 확인
- [ ] non-listed 탐색 폭이 deadline pressure에 따라 증가하는지 확인
- [ ] 테스트 케이스(최소 5개) 추가 완료
- [ ] 성능 회귀(틱당 시간) 기준선 수집
