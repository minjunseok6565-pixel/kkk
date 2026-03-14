# DealGen 타겟 티어 ↔ 스켈레톤 연결 브리핑

대상:
- 티어 분류 로직: `trades/generation/dealgen/utils.py`의 `classify_target_tier`
- 스켈레톤 라우팅/필터: `trades/generation/dealgen/skeleton_registry.py`, `trades/generation/dealgen/types.py`

---

## 1) 현재 티어 분류 규칙(SSOT)

`classify_target_tier(...)`는 최종적으로 아래 5개 문자열만 반환합니다.

- `PICK_ONLY`
  - `need_tag` 또는 `match_tag`에 `"PICK"`가 포함되면 최우선으로 반환
- `ROLE`
  - `is_expiring`(또는 `remaining_years <= 1.1`) 이면서 `market_total <= 58` & `salary_m <= 30`
  - 또는 아래 STARTER/HIGH_STARTER/STAR 임계치 미만
- `STARTER`
  - `market_total >= 52`
- `HIGH_STARTER`
  - `market_total >= 72`
- `STAR`
  - `market_total >= 86`

즉, 시장가 임계치는 `52/72/86`이고, pick 태그가 있으면 무조건 `PICK_ONLY`로 덮어씌웁니다.

---

## 2) 티어가 스켈레톤에 연결되는 방식

스켈레톤 선택은 `SkeletonRegistry.get_specs_for_mode_and_tier(mode, tier, config)`에서 다음 순서로 결정됩니다.

1. `mode_allow`(BUY/SELL) 일치
2. `target_tiers`에 해당 티어 포함
3. 해당 티어용 route 목록(`DealGeneratorConfig.skeleton_route_*`)에 포함
4. (선택) gate_fn 통과

### 라우트 키 매핑(중요)
- `ROLE` → `skeleton_route_role`
- `STARTER` → `skeleton_route_starter`
- `HIGH_STARTER` → `skeleton_route_high_starter`
- `STAR` → **`skeleton_route_high_starter`를 공유**
- `PICK_ONLY` → `skeleton_route_pick_only`

따라서 `STAR` 전용 route는 없고, `HIGH_STARTER` route를 함께 사용합니다.

---

## 3) 현재 기본 설정 기준 “실제로 나올 수 있는” 모든 조합

아래는 `build_default_registry()` + `DealGeneratorConfig()` 기본값에서 티어별로 라우팅된 스켈레톤 전체입니다.

## 3-1) BUY 모드

### ROLE (16)
- compat.picks_only
- compat.young_plus_pick
- compat.p4p_salary
- player_swap.role_swap_small_delta
- player_swap.one_for_two_depth
- player_swap.bench_bundle_for_role
- player_swap.change_of_scenery_young
- timeline.veteran_for_young
- salary_cleanup.rental_expiring_plus_second
- salary_cleanup.pure_absorb_for_asset
- salary_cleanup.partial_dump_for_expiring
- salary_cleanup.bad_money_swap
- pick_engineering.first_split
- pick_engineering.second_ladder_to_protected_first
- pick_engineering.swap_purchase
- pick_engineering.swap_substitute_for_first

### STARTER (21)
- compat.picks_only
- compat.young_plus_pick
- compat.p4p_salary
- compat.consolidate_2_for_1
- player_swap.role_swap_small_delta
- player_swap.fit_swap_2_for_2
- player_swap.starter_for_two_rotation
- player_swap.one_for_two_depth
- player_swap.three_for_one_upgrade
- player_swap.bench_bundle_for_role
- player_swap.change_of_scenery_young
- timeline.veteran_for_young
- timeline.veteran_for_young_plus_protected_first
- salary_cleanup.rental_expiring_plus_second
- salary_cleanup.pure_absorb_for_asset
- salary_cleanup.partial_dump_for_expiring
- salary_cleanup.bad_money_swap
- pick_engineering.first_split
- pick_engineering.second_ladder_to_protected_first
- pick_engineering.swap_purchase
- pick_engineering.swap_substitute_for_first

### HIGH_STARTER (20)
- compat.picks_only
- compat.young_plus_pick
- compat.p4p_salary
- compat.consolidate_2_for_1
- player_swap.role_swap_small_delta
- player_swap.fit_swap_2_for_2
- player_swap.starter_for_two_rotation
- player_swap.one_for_two_depth
- player_swap.three_for_one_upgrade
- player_swap.star_lateral_plus_delta
- timeline.veteran_for_young
- timeline.veteran_for_young_plus_protected_first
- timeline.bluechip_plus_first_plus_swap
- salary_cleanup.pure_absorb_for_asset
- salary_cleanup.partial_dump_for_expiring
- salary_cleanup.bad_money_swap
- pick_engineering.first_split
- pick_engineering.second_ladder_to_protected_first
- pick_engineering.swap_purchase
- pick_engineering.swap_substitute_for_first

### STAR (18)
- compat.picks_only
- compat.young_plus_pick
- compat.p4p_salary
- compat.consolidate_2_for_1
- player_swap.role_swap_small_delta
- player_swap.fit_swap_2_for_2
- player_swap.starter_for_two_rotation
- player_swap.three_for_one_upgrade
- player_swap.star_lateral_plus_delta
- timeline.veteran_for_young_plus_protected_first
- timeline.bluechip_plus_first_plus_swap
- salary_cleanup.pure_absorb_for_asset
- salary_cleanup.partial_dump_for_expiring
- salary_cleanup.bad_money_swap
- pick_engineering.first_split
- pick_engineering.second_ladder_to_protected_first
- pick_engineering.swap_purchase
- pick_engineering.swap_substitute_for_first

### PICK_ONLY (6)
- compat.picks_only
- salary_cleanup.pure_absorb_for_asset
- pick_engineering.first_split
- pick_engineering.second_ladder_to_protected_first
- pick_engineering.swap_purchase
- pick_engineering.swap_substitute_for_first

---

## 3-2) SELL 모드

### ROLE (13)
- player_swap.role_swap_small_delta
- player_swap.one_for_two_depth
- player_swap.bench_bundle_for_role
- player_swap.change_of_scenery_young
- timeline.veteran_for_young
- salary_cleanup.rental_expiring_plus_second
- salary_cleanup.pure_absorb_for_asset
- salary_cleanup.partial_dump_for_expiring
- salary_cleanup.bad_money_swap
- pick_engineering.first_split
- pick_engineering.second_ladder_to_protected_first
- pick_engineering.swap_purchase
- pick_engineering.swap_substitute_for_first

### STARTER (17)
- player_swap.role_swap_small_delta
- player_swap.fit_swap_2_for_2
- player_swap.starter_for_two_rotation
- player_swap.one_for_two_depth
- player_swap.three_for_one_upgrade
- player_swap.bench_bundle_for_role
- player_swap.change_of_scenery_young
- timeline.veteran_for_young
- timeline.veteran_for_young_plus_protected_first
- salary_cleanup.rental_expiring_plus_second
- salary_cleanup.pure_absorb_for_asset
- salary_cleanup.partial_dump_for_expiring
- salary_cleanup.bad_money_swap
- pick_engineering.first_split
- pick_engineering.second_ladder_to_protected_first
- pick_engineering.swap_purchase
- pick_engineering.swap_substitute_for_first

### HIGH_STARTER (16)
- player_swap.role_swap_small_delta
- player_swap.fit_swap_2_for_2
- player_swap.starter_for_two_rotation
- player_swap.one_for_two_depth
- player_swap.three_for_one_upgrade
- player_swap.star_lateral_plus_delta
- timeline.veteran_for_young
- timeline.veteran_for_young_plus_protected_first
- timeline.bluechip_plus_first_plus_swap
- salary_cleanup.pure_absorb_for_asset
- salary_cleanup.partial_dump_for_expiring
- salary_cleanup.bad_money_swap
- pick_engineering.first_split
- pick_engineering.second_ladder_to_protected_first
- pick_engineering.swap_purchase
- pick_engineering.swap_substitute_for_first

### STAR (14)
- player_swap.role_swap_small_delta
- player_swap.fit_swap_2_for_2
- player_swap.starter_for_two_rotation
- player_swap.three_for_one_upgrade
- player_swap.star_lateral_plus_delta
- timeline.veteran_for_young_plus_protected_first
- timeline.bluechip_plus_first_plus_swap
- salary_cleanup.pure_absorb_for_asset
- salary_cleanup.partial_dump_for_expiring
- salary_cleanup.bad_money_swap
- pick_engineering.first_split
- pick_engineering.second_ladder_to_protected_first
- pick_engineering.swap_purchase
- pick_engineering.swap_substitute_for_first

### PICK_ONLY (5)
- salary_cleanup.pure_absorb_for_asset
- pick_engineering.first_split
- pick_engineering.second_ladder_to_protected_first
- pick_engineering.swap_purchase
- pick_engineering.swap_substitute_for_first

---

## 4) 참고: “레지스트리에는 있지만 기본 라우트에서 빠진” 조합

SELL 전용 compat 스켈레톤(`compat.buyer_*`)은 레지스트리에는 존재하지만, 현재 `skeleton_route_*` 기본 목록에 포함되지 않아 기본 설정에서는 선택되지 않습니다.

- compat.buyer_picks
- compat.buyer_young_plus_pick
- compat.buyer_p4p
- compat.buyer_consolidate

즉 “코드상 가능(등록됨)”과 “기본 설정에서 실제 라우팅됨”을 구분해서 봐야 합니다.

---

## 5) 리팩토링 시 체크포인트

- 티어 체계를 바꾸면 최소 아래 3축을 같이 수정해야 합니다.
  1) `classify_target_tier` 반환값 집합
  2) `route_attr_map`(특히 STAR가 HIGH_STARTER route 공유하는 구조)
  3) `DealGeneratorConfig.skeleton_route_*` 기본 튜플 + 각 `SkeletonSpec.target_tiers`
- 누락 시, 특정 티어가 빈 스켈레톤 목록으로 떨어질 수 있습니다.
