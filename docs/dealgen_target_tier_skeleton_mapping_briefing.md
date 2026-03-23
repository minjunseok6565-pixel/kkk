# DealGen 타겟 티어 ↔ 스켈레톤 완전 브리핑

대상 코드
- 티어 분류: `trades/generation/dealgen/utils.py::classify_target_tier`
- 라우팅/레지스트리: `trades/generation/dealgen/skeleton_registry.py`, `trades/generation/dealgen/types.py`
- 스켈레톤 구현: `skeleton_builders_*.py`

---

## 1) 티어 분류 규칙(현재 SSOT)

`classify_target_tier(...)`의 반환값은 아래 5개 고정입니다.

- `PICK_ONLY`
  - `need_tag` 또는 `match_tag`에 `"PICK"` 문자열이 포함되면 우선 반환
- `ROLE`
  - expiring 조건(`is_expiring=True` 또는 `remaining_years <= 1.1`) + `market_total <= 58` + `salary_m <= 30`
  - 또는 아래 임계치 미만
- `STARTER`: `market_total >= 52`
- `HIGH_STARTER`: `market_total >= 72`
- `STAR`: `market_total >= 86`

---

## 2) 티어 → 스켈레톤 라우팅 구조

`SkeletonRegistry.get_specs_for_mode_and_tier(mode, tier, config, ctx)`가 다음을 순서대로 적용합니다.

1. `mode_allow` 필터(BUY/SELL)
2. `target_tiers` 포함 여부
3. `DealGeneratorConfig.skeleton_route_*` 목록 필터
4. `gate_fn`(있을 때만)

라우트 키 매핑은 다음과 같습니다.
- `ROLE` → `skeleton_route_role`
- `STARTER` → `skeleton_route_starter`
- `HIGH_STARTER` → `skeleton_route_high_starter`
- `STAR` → **`skeleton_route_high_starter` 공유**
- `PICK_ONLY` → `skeleton_route_pick_only`

> 즉 STAR 전용 라우트 키는 없습니다.

---

## 3) 기본 설정에서 실제 가능한 티어별 조합(요약)

`build_default_registry()` + `DealGeneratorConfig()` 기준.

### BUY
- ROLE: 16개
- STARTER: 21개
- HIGH_STARTER: 20개
- STAR: 18개
- PICK_ONLY: 6개

### SELL
- ROLE: 13개
- STARTER: 17개
- HIGH_STARTER: 16개
- STAR: 14개
- PICK_ONLY: 5개

(각 목록은 본 문서의 4장 상세 항목에서 스켈레톤별로 전부 설명)

---

## 4) 스켈레톤별 완전 상세 (전부)

아래는 **레지스트리에 등록된 모든 skeleton_id(27개)** 기준입니다.

공통 전제(대부분의 스켈레톤)
- 기본 deal 뼈대는 동일: `seller` leg에 focal player(타겟/매물 선수)를 먼저 넣고 시작.
- 이후 `buyer` leg에 선수/픽/스왑을 추가하는 형태.
- 후보 선수 선택 시 공통적으로 다음 필터가 자주 적용됨:
  - `banned_players` 제외
  - `return_ban_teams`, `banned_receivers_by_player` 제외
  - `must_be_aggregation_friendly=True`인 경우 `aggregation_solo_only` 제외
- 픽 추가는 `_add_pick_package(...)` 공통 헬퍼를 사용:
  - `prefer` 우선순위대로 bucket 탐색
  - `max_picks` 상한 준수
  - `config.max_picks_per_side`, `config.max_seconds_per_side` 준수
  - 1라 픽은 `Stepien` 체크 후에만 추가
  - `banned_asset_keys`로 금지된 픽 제외

### A. compat 도메인

#### 1) `compat.picks_only` (BUY)
- 딜 형태
  - Seller → Buyer: focal player 1명
  - Buyer → Seller: **선수 없이 픽 패키지**
- 자산 구성
  - 픽 우선순위: `SECOND`, `FIRST_SAFE`
  - Seller가 REBUILD면 최대 2픽, 아니면 1픽
- 핵심 게이트
  - `ctx.target` 필수
  - `buyer_out` 존재 필수
  - Buyer가 `target.salary_m`를 **무출혈(cap absorb)**로 흡수 가능해야 함

#### 2) `compat.young_plus_pick` (BUY)
- 딜 형태
  - Seller → Buyer: focal player
  - Buyer → Seller: young player 1명 + (보통) second 1장
- 자산 구성
  - young 후보는 `_split_young_candidates`에서 prospect/throw-in 분리
  - Seller가 rebuild 성향이면 prospect 우선(상위 N 랜덤), 아니면 throw-in 풀에서 need-fit 샘플링
  - 픽 우선순위: `SECOND`, 최대 1픽
- 핵심 게이트
  - `ctx.target`, `buyer_out` 필수
  - young 후보를 찾지 못하면 실패

#### 3) `compat.p4p_salary` (BUY)
- 딜 형태
  - Seller → Buyer: focal player
  - Buyer → Seller: salary-ish return player 1명
- 자산 구성
  - `_pick_return_player_salaryish_with_need`로 need+급여 유사 후보 선택
- 핵심 게이트
  - `ctx.target`, `buyer_out` 필수
  - filler 후보 없으면 실패

#### 4) `compat.consolidate_2_for_1` (BUY)
- 딜 형태
  - Seller → Buyer: focal player
  - Buyer → Seller: 선수 2명(`CONSOLIDATE` + `FILLER_CHEAP`) + (옵션) second 1장
- 자산 구성
  - 픽 우선순위: `SECOND`, 최대 1픽
- 핵심 게이트
  - `ctx.target`, `buyer_out` 필수
  - 두 선수 모두 선택되어야 하고 서로 달라야 함

#### 5) `compat.buyer_picks` (SELL)
- 딜 형태
  - Seller → Buyer: sale focal player
  - Buyer → Seller: **선수 없이 픽 패키지**
- 자산 구성
  - 픽 우선순위: `SECOND`, `FIRST_SAFE`
  - Seller가 REBUILD면 최대 2픽, 아니면 1픽
- 핵심 게이트
  - `ctx.sale_asset`, `buyer_out` 필수
  - Buyer가 sale_asset 급여를 무출혈 흡수 가능해야 함

#### 6) `compat.buyer_young_plus_pick` (SELL)
- 딜 형태
  - Seller → Buyer: sale focal player
  - Buyer → Seller: young 1명 + second 1장
- 자산 구성
  - young 추출 규칙은 BUY 버전과 거의 동일
  - 픽 우선순위: `SECOND`, 최대 1픽
- 핵심 게이트
  - `ctx.sale_asset`, `buyer_out` 필수
  - young 후보 미존재 시 실패

#### 7) `compat.buyer_p4p` (SELL)
- 딜 형태
  - Seller → Buyer: sale focal player
  - Buyer → Seller: salary-ish return player 1명
- 자산 구성
  - need-fit+salaryish 1명 선택
- 핵심 게이트
  - `ctx.sale_asset`, `buyer_out` 필수
  - Seller time_horizon이 `WIN_NOW` 또는 `RE_TOOL`이어야 함
  - filler 미선정 시 실패

#### 8) `compat.buyer_consolidate` (SELL)
- 딜 형태
  - Seller → Buyer: sale focal player
  - Buyer → Seller: `CONSOLIDATE` + `FILLER_CHEAP` + (옵션) second 1장
- 자산 구성
  - 픽 우선순위: `SECOND`, 최대 1픽
- 핵심 게이트
  - `ctx.sale_asset`, `buyer_out` 필수
  - 2명 모두 필요, 중복 불가

---

### B. player_swap 도메인

#### 9) `player_swap.role_swap_small_delta` (BUY/SELL)
- 딜 형태
  - Seller → Buyer: focal player
  - Buyer → Seller: salary-ish player 1명 + (옵션) sweetener 픽/스왑 1개
- 자산 구성
  - 픽 우선순위: `SECOND`, `SWAP`; 최대 1개
- 핵심 게이트
  - focal 존재(`target` 또는 `sale_asset`)
  - `buyer_out` 필수
  - filler 미선정 시 실패

#### 10) `player_swap.fit_swap_2_for_2` (BUY/SELL)
- 딜 형태
  - Seller → Buyer: focal player
  - Buyer → Seller: `CONSOLIDATE` + `FILLER_CHEAP` (2명)
- 핵심 게이트
  - focal, `buyer_out` 필수
  - 2명 모두 필요 + 중복 불가

#### 11) `player_swap.starter_for_two_rotation` (BUY/SELL)
- 딜 형태
  - 실질적으로 `fit_swap_2_for_2`의 래퍼
  - Seller → Buyer: focal 1
  - Buyer → Seller: 2명
- 핵심 게이트
  - `fit_swap_2_for_2`가 성공해야 함(= 동일 게이트)

#### 12) `player_swap.one_for_two_depth` (BUY/SELL)
- 딜 형태
  - Seller → Buyer: focal 1
  - Buyer → Seller: depth 2명(`FILLER_CHEAP` + salaryish 약 45%)
- 의도
  - starter_for_two_rotation의 저강도 버전(ROLE/STARTER 중심)
- 핵심 게이트
  - focal, `buyer_out` 필수
  - `p1`, `p2` 모두 필요 + 중복 불가
- 레지스트리 gate_fn
  - `ctx.target is not None or ctx.sale_asset is not None` (사실상 focal 존재 체크를 이중으로 수행)

#### 13) `player_swap.three_for_one_upgrade` (BUY/SELL)
- 딜 형태
  - Seller → Buyer: focal 1
  - Buyer → Seller: 3명(`CONSOLIDATE` + `FILLER_CHEAP` + salaryish 약 50%)
- 핵심 게이트
  - focal, `buyer_out` 필수
  - 3명 모두 필요, 전원 distinct

#### 14) `player_swap.bench_bundle_for_role` (BUY/SELL)
- 딜 형태
  - 기본은 `fit_swap_2_for_2`와 동일한 2명 패키지
  - 거기에 second 중심 픽을 최대 2장 추가 시도
- 자산 구성
  - 픽 우선순위: `SECOND`, 최대 2픽
- 핵심 게이트
  - `fit_swap_2_for_2` 성공 필요

#### 15) `player_swap.change_of_scenery_young` (BUY/SELL)
- 딜 형태
  - Seller → Buyer: focal 1
  - Buyer → Seller: young 1명(픽 없음)
- 자산 구성
  - prospect 우선, 없으면 throw-in
- 핵심 게이트
  - focal, `buyer_out` 필수
  - young pool 비면 실패

#### 16) `player_swap.star_lateral_plus_delta` (BUY/SELL)
- 딜 형태
  - `role_swap_small_delta`를 재사용한 lateral+delta 형태
  - Seller → Buyer: focal 1
  - Buyer → Seller: salary-ish 1명 + (옵션) second/swap 1개
- 핵심 게이트
  - `role_swap_small_delta` 성공이 전제

---

### C. timeline 도메인

#### 17) `timeline.veteran_for_young` (BUY/SELL)
- 딜 형태
  - Seller → Buyer: focal 1
  - Buyer → Seller: young 1명
- 자산 구성
  - `_split_young_candidates`에서 prospect 우선, 없으면 throw-in
- 핵심 게이트
  - focal, `buyer_out` 필수
  - young pool 비면 실패

#### 18) `timeline.veteran_for_young_plus_protected_first` (BUY/SELL)
- 딜 형태
  - `veteran_for_young` + 픽 1장
  - Seller → Buyer: focal 1
  - Buyer → Seller: young 1명 + (FIRST_SAFE 우선) 픽 1
- 자산 구성
  - 픽 우선순위: `FIRST_SAFE`, `SECOND`; 최대 1픽
- 핵심 게이트
  - `veteran_for_young` 성공 필요

#### 19) `timeline.bluechip_plus_first_plus_swap` (BUY/SELL)
- 딜 형태
  - `veteran_for_young` + 고강도 픽 패키지
  - Seller → Buyer: focal 1
  - Buyer → Seller: young 1명 + (FIRST_SAFE/SWAP/SECOND 중) 최대 2개
- 자산 구성
  - 픽 우선순위: `FIRST_SAFE`, `SWAP`, `SECOND`; 최대 2
- 핵심 게이트
  - `veteran_for_young` 성공 필요

---

### D. salary_cleanup 도메인

#### 20) `salary_cleanup.rental_expiring_plus_second` (BUY/SELL)
- 딜 형태
  - Seller → Buyer: focal 1
  - Buyer → Seller: salaryish player 1명 + second 1장
- 자산 구성
  - filler는 aggregation-friendly 모드로 선택
  - 픽 우선순위: `SECOND`; 최대 1
- 핵심 게이트
  - focal, `buyer_out` 필수
  - filler 미선정 시 실패

#### 21) `salary_cleanup.pure_absorb_for_asset` (BUY/SELL)
- 딜 형태
  - Seller → Buyer: focal 1
  - Buyer → Seller: **선수 없이 픽 패키지(자산 보상)**
- 자산 구성
  - 픽 우선순위: `SECOND`, `FIRST_SAFE`; 최대 2
- 핵심 게이트
  - focal, `buyer_out` 필수
  - Buyer가 focal salary를 무출혈 흡수 가능해야 함

#### 22) `salary_cleanup.partial_dump_for_expiring` (BUY/SELL)
- 딜 형태
  - Seller → Buyer: focal 1
  - Buyer → Seller: salaryish player(목표급여의 약 70%) + second 1장
- 자산 구성
  - 픽 우선순위: `SECOND`; 최대 1
- 핵심 게이트
  - focal, `buyer_out` 필수
  - filler 미선정 시 실패

#### 23) `salary_cleanup.bad_money_swap` (BUY/SELL)
- 딜 형태
  - Seller → Buyer: focal 1
  - Buyer → Seller: salaryish player(타깃 급여 근사) + second 1장
- 차이점
  - `partial_dump`와 달리 filler 선택 시 `must_be_aggregation_friendly=False`
- 핵심 게이트
  - focal, `buyer_out` 필수
  - filler 미선정 시 실패

---

### E. pick_engineering 도메인

#### 24) `pick_engineering.first_split` (BUY/SELL)
- 딜 형태
  - Seller → Buyer: focal 1
  - Buyer → Seller: 픽 최대 2장
- 자산 구성
  - 우선순위: `FIRST_SAFE`, `SECOND`
- 핵심 게이트
  - focal, `buyer_out` 필수

#### 25) `pick_engineering.second_ladder_to_protected_first` (BUY/SELL)
- 딜 형태
  - Seller → Buyer: focal 1
  - Buyer → Seller: 픽 최대 3장
- 자산 구성
  - 우선순위: `SECOND`, `SECOND`, `FIRST_SAFE`

#### 26) `pick_engineering.swap_purchase` (BUY/SELL)
- 딜 형태
  - Seller → Buyer: focal 1
  - Buyer → Seller: swap/second 중심 최대 2자산
- 자산 구성
  - 우선순위: `SWAP`, `SECOND`
  - 참고: `_add_pick_package`는 실제로 SWAP 버킷을 직접 추가하지 않고 skip하므로, 실질 추가는 뒤의 `SECOND`가 주가 됨

#### 27) `pick_engineering.swap_substitute_for_first` (BUY/SELL)
- 딜 형태
  - Seller → Buyer: focal 1
  - Buyer → Seller: swap 대체 컨셉 최대 3자산
- 자산 구성
  - 우선순위: `SWAP`, `SWAP`, `SECOND`
  - 위와 동일하게 현재 `_add_pick_package` 구현상 SWAP은 skip되고 SECOND 위주로 형성됨

> 참고: 레지스트리 등록 스켈레톤은 총 27개인데, 위 상세는 실무에서 이름이 혼동되는 pick_engineering 4개를 포함해 전부 설명했으며 numbering은 도메인별 가독성 때문에 연속으로 유지했습니다.

---

## 5) 레지스트리 게이트/티어 허용치(빠짐없이)

### 레지스트리 레벨 gate_fn
- 명시 gate_fn이 있는 스켈레톤은 `player_swap.one_for_two_depth` 1개뿐:
  - `ctx.target is not None or ctx.sale_asset is not None`

### tier 허용(정의값)
- ALL_TARGET_TIERS = `ROLE, STARTER, HIGH_STARTER, STAR, PICK_ONLY`
- `compat.picks_only`: ALL (BUY)
- `compat.young_plus_pick`: ROLE~STAR (BUY)
- `compat.p4p_salary`: ROLE~STAR (BUY)
- `compat.consolidate_2_for_1`: STARTER~STAR (BUY)
- `compat.buyer_picks`: ALL (SELL)
- `compat.buyer_young_plus_pick`: ROLE~STAR (SELL)
- `compat.buyer_p4p`: ROLE~STAR (SELL)
- `compat.buyer_consolidate`: STARTER~STAR (SELL)
- `player_swap.role_swap_small_delta`: ROLE~STAR
- `player_swap.fit_swap_2_for_2`: STARTER~STAR
- `player_swap.starter_for_two_rotation`: STARTER~STAR
- `player_swap.one_for_two_depth`: ROLE~HIGH_STARTER
- `player_swap.three_for_one_upgrade`: STARTER~STAR
- `player_swap.bench_bundle_for_role`: ROLE, STARTER
- `player_swap.change_of_scenery_young`: ROLE, STARTER
- `player_swap.star_lateral_plus_delta`: HIGH_STARTER, STAR
- `timeline.veteran_for_young`: ROLE~HIGH_STARTER
- `timeline.veteran_for_young_plus_protected_first`: STARTER~STAR
- `timeline.bluechip_plus_first_plus_swap`: HIGH_STARTER, STAR
- `salary_cleanup.rental_expiring_plus_second`: ROLE, STARTER
- `salary_cleanup.pure_absorb_for_asset`: ALL
- `salary_cleanup.partial_dump_for_expiring`: ROLE~STAR
- `salary_cleanup.bad_money_swap`: ROLE~STAR
- `pick_engineering.first_split`: ALL
- `pick_engineering.second_ladder_to_protected_first`: ALL
- `pick_engineering.swap_purchase`: ALL
- `pick_engineering.swap_substitute_for_first`: ALL

---

## 6) 리팩토링 시 반드시 같이 손볼 축

티어/스켈레톤을 개편할 때는 최소 아래 4축을 동시에 맞춰야 안전합니다.

1. `classify_target_tier` 반환 체계
2. `route_attr_map`(특히 STAR ↔ HIGH_STARTER 공유 여부)
3. `DealGeneratorConfig.skeleton_route_*` 기본 목록
4. `SkeletonSpec.target_tiers`, `mode_allow`, `gate_fn`

이 중 하나라도 누락되면 특정 티어에서 스켈레톤이 0개가 되거나, 의도하지 않은 도메인(예: pick_engineering 과다)으로 편향될 수 있습니다.
