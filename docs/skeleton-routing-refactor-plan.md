# Skeleton Routing Refactor Plan

## Goal

Fix SELL under-generation caused by tier route IDs being treated as a hard allowlist, while also making routing policy explicit per mode (BUY/SELL).

---

## Problem Summary

Today, `SkeletonRegistry.get_specs_for_mode_and_tier(...)`:

1. Resolves a tier-specific route tuple from config (`skeleton_route_role`, `skeleton_route_starter`, etc.).
2. Builds `route_id_set`.
3. Filters out any skeleton whose ID is not in that set.

Because default route tuples are BUY-compat-heavy (e.g. `compat.picks_only`, `compat.p4p_salary`) and SELL compat IDs are different (`compat.buyer_picks`, `compat.buyer_p4p`, ...), SELL compat skeletons get excluded by the hard allowlist check.

---

## Refactor Strategy

Implement both:

1. **Hard allowlist → soft preference** in registry selection.
2. **Mode-specific route configuration** (BUY/SELL route sets).

This keeps eligibility logic robust while preserving operator control over route ordering.

---

## Concrete File-Level Change Plan

## 1) `trades/generation/dealgen/types.py`

### A. Add SELL route fields (and BUY-explicit fields if desired for clarity)

Add new config tuples for SELL mode:

- `skeleton_route_role_sell`
- `skeleton_route_starter_sell`
- `skeleton_route_high_starter_sell`
- `skeleton_route_pick_only_sell`

Populate defaults with SELL-capable compat IDs first:

- `compat.buyer_picks`
- `compat.buyer_young_plus_pick`
- `compat.buyer_p4p`
- `compat.buyer_consolidate`

and include cross-mode families (`player_swap.*`, `timeline.*`, `salary_cleanup.*`, `pick_engineering.*`) as appropriate by tier.

> Optional clarity improvement:
> Rename or alias current fields as BUY route fields (e.g. `skeleton_route_role_buy`) while retaining backward compatibility.

### B. Backward compatibility policy

To avoid breaking existing config users:

- Keep existing `skeleton_route_role/starter/high_starter/pick_only` fields.
- Treat them as BUY defaults if new BUY-specific fields are not introduced.
- New SELL fields default independently and are optional overrides.

---

## 2) `trades/generation/dealgen/skeleton_registry.py`

### A. Route resolver should be mode-aware

Introduce an internal resolver method (or inline helper) such as:

- `_resolve_route_ids(mode_upper: str, tier_upper: str, config: DealGeneratorConfig) -> Tuple[str, ...]`

Behavior:

- `mode=BUY`: use BUY route tuple for tier.
- `mode=SELL`: use SELL route tuple for tier.
- fallback for unknown mode: existing legacy route tuple.

### B. Remove hard allowlist filtering

In `get_specs_for_mode_and_tier(...)`, remove this exclusion behavior:

- `if route_id_set and spec.skeleton_id not in route_id_set: continue`

Eligibility should only be determined by:

- `mode_allow`
- `target_tiers`
- `gate_fn` (if provided)

### C. Keep route as soft preference in sort

Preserve or improve sorting to prioritize route-listed IDs without excluding others.

Suggested deterministic sort key:

1. base `priority`
2. `in_route` rank (0 if in route else 1)
3. explicit route position (index for in-route IDs, large sentinel otherwise)
4. `skeleton_id` tie-breaker

This gives meaningful ordering while maintaining full candidate coverage.

### D. Optional strict mode switch (safety valve)

If desired, add a config toggle like `skeleton_route_strict_allowlist: bool = False`.

- Default `False` (soft preference behavior).
- When `True`, restore old hard filtering semantics for debugging/experiments.

---

## 3) Tests

## A. Update / add tests in `trades/generation/dealgen/test_skeleton_registry_routing.py`

Add test cases:

1. **SELL mode includes SELL compat skeletons even if not in BUY route list**
   - Assert `compat.buyer_picks` etc. are present in SELL outputs.

2. **Route IDs affect order, not inclusion**
   - Provide small route list and assert in-route specs come first.
   - Assert out-of-route but mode/tier-valid specs still appear.

3. **Mode-specific route selection**
   - Configure BUY and SELL routes differently for same tier.
   - Assert ordering follows the selected mode’s route tuple.

4. **gate_fn still respected**
   - Ensure route softening does not bypass gate constraints.

## B. Optional regression test in SELL generation flow

In SELL pipeline tests (where `build_offer_skeletons_sell` behavior is covered), add a regression assertion that baseline SELL compat families are discoverable under default config.

---

## 4) Docs / Comments

Update inline comments in:

- `types.py` route section: clarify route tuples are ordering preferences, not strict allowlists (unless strict mode is enabled).
- `skeleton_registry.py` selection logic: document eligibility vs ranking split.

---

## Migration / Rollout Notes

1. No external behavior break expected for BUY ordering.
2. SELL should gain candidate coverage (fix under-generation).
3. If strict-mode toggle is added, leave default off and do not enable in production configs unless intentionally required.

---

## Acceptance Criteria

- SELL mode returns SELL compat skeleton families under default config.
- Route config can still prioritize preferred skeletons by tier.
- No skeleton is excluded solely because it is absent from route tuple (in default soft mode).
- Existing tests pass and new regression tests pass.
