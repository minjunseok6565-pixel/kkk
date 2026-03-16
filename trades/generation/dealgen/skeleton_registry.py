from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set, Tuple
import random

from ..generation_tick import TradeGenerationTickContext
from ..asset_catalog import TradeAssetCatalog
from .types import DealCandidate, DealGeneratorBudget, DealGeneratorConfig, SellAssetCandidate, TargetCandidate


@dataclass(frozen=True, slots=True)
class BuildContext:
    mode: str
    buyer_id: str
    seller_id: str
    tick_ctx: TradeGenerationTickContext
    catalog: TradeAssetCatalog
    config: DealGeneratorConfig
    budget: DealGeneratorBudget
    rng: random.Random
    banned_asset_keys: Set[str]
    banned_players: Set[str]
    banned_receivers_by_player: Optional[Dict[str, Set[str]]]
    target: Optional[TargetCandidate] = None
    sale_asset: Optional[SellAssetCandidate] = None
    match_tag: str = ""


@dataclass(frozen=True, slots=True)
class SkeletonSpec:
    skeleton_id: str
    domain: str
    compat_archetype: str
    mode_allow: Tuple[str, ...]
    target_tiers: Tuple[str, ...]
    priority: int
    build_fn: Callable[[BuildContext], List[DealCandidate]]
    gate_fn: Optional[Callable[[BuildContext], bool]] = None
    default_tags: Tuple[str, ...] = tuple()
    allows_modifiers: bool = True
    contract_tags: Tuple[str, ...] = ("OVERPAY", "FAIR", "VALUE")


@dataclass(frozen=True, slots=True)
class SkeletonRegistry:
    specs: Tuple[SkeletonSpec, ...]

    def get_specs_for_mode(self, mode: str) -> List[SkeletonSpec]:
        mu = str(mode).upper()
        out = [s for s in self.specs if mu in s.mode_allow]
        out.sort(key=lambda s: (int(s.priority), s.skeleton_id))
        return out

    def get_specs_for_mode_and_tier(
        self,
        mode: str,
        tier: str,
        config: DealGeneratorConfig,
        ctx: Optional[BuildContext] = None,
        contract_tag: str = "",
    ) -> List[SkeletonSpec]:
        mode_upper = str(mode).upper()
        tier_upper = str(tier).upper()
        contract_upper = str(contract_tag).upper().strip()

        route_attr_map = {
            "MVP": "skeleton_route_mvp",
            "ALL_NBA": "skeleton_route_all_nba",
            "ALL_STAR": "skeleton_route_all_star",
            "HIGH_STARTER": "skeleton_route_high_starter",
            "STARTER": "skeleton_route_starter",
            "HIGH_ROTATION": "skeleton_route_high_rotation",
            "ROTATION": "skeleton_route_rotation",
            "GARBEGE": "skeleton_route_garbege",
        }
        route_attr = route_attr_map.get(tier_upper)
        route_ids = tuple(getattr(config, route_attr, tuple()) or tuple()) if route_attr else tuple()

        contract_route_map = {
            "OVERPAY": "skeleton_route_contract_overpay",
            "FAIR": "skeleton_route_contract_fair",
            "VALUE": "skeleton_route_contract_value",
        }
        contract_route_attr = contract_route_map.get(contract_upper)
        contract_route_ids = tuple(getattr(config, contract_route_attr, tuple()) or tuple()) if contract_route_attr else tuple()

        route_id_set = set(route_ids) | set(contract_route_ids)

        out: List[SkeletonSpec] = []
        for spec in self.specs:
            if mode_upper not in spec.mode_allow:
                continue
            if tier_upper not in spec.target_tiers:
                continue
            if contract_upper and contract_upper not in spec.contract_tags:
                continue
            if route_id_set and spec.skeleton_id not in route_id_set:
                continue
            if ctx is not None and spec.gate_fn is not None and not bool(spec.gate_fn(ctx)):
                continue
            out.append(spec)

        out.sort(
            key=lambda s: (
                int(s.priority),
                0 if s.skeleton_id in route_id_set else 1,
                s.skeleton_id,
            )
        )
        return out


ALL_TARGET_TIERS: Tuple[str, ...] = ("MVP", "ALL_NBA", "ALL_STAR", "HIGH_STARTER", "STARTER", "HIGH_ROTATION", "ROTATION", "GARBEGE")


def build_default_registry() -> SkeletonRegistry:
    from .skeleton_builders_compat import (
        build_buy_consolidate_2_for_1,
        build_buy_p4p_salary,
        build_buy_picks_only,
        build_buy_young_plus_pick,
        build_sell_buyer_consolidate,
        build_sell_buyer_p4p,
        build_sell_buyer_picks,
        build_sell_buyer_young_plus_pick,
    )
    from .skeleton_builders_player_swap import (
        build_bench_bundle_for_role,
        build_change_of_scenery_young,
        build_fit_swap_2_for_2,
        build_one_for_two_depth,
        build_role_swap_small_delta,
        build_star_lateral_plus_delta,
        build_starter_for_two_rotation,
        build_three_for_one_upgrade,
    )
    from .skeleton_builders_timeline import (
        build_bluechip_plus_first_plus_swap,
        build_veteran_for_young,
        build_veteran_for_young_plus_protected_first,
    )
    from .skeleton_builders_salary_cleanup import (
        build_bad_money_swap,
        build_partial_dump_for_expiring,
        build_pure_absorb_for_asset,
        build_rental_expiring_plus_second,
    )
    from .skeleton_builders_pick_engineering import (
        build_first_split,
        build_second_ladder_to_protected_first,
        build_swap_purchase,
        build_swap_substitute_for_first,
    )

    specs: Tuple[SkeletonSpec, ...] = (
        SkeletonSpec(
            skeleton_id="compat.picks_only",
            domain="compat",
            compat_archetype="picks_only",
            mode_allow=("BUY",),
            target_tiers=ALL_TARGET_TIERS,
            priority=10,
            build_fn=build_buy_picks_only,
        ),
        SkeletonSpec(
            skeleton_id="compat.young_plus_pick",
            domain="compat",
            compat_archetype="young_plus_pick",
            mode_allow=("BUY",),
            target_tiers=("ALL_NBA", "ALL_STAR", "HIGH_STARTER", "STARTER", "HIGH_ROTATION", "ROTATION"),
            priority=20,
            build_fn=build_buy_young_plus_pick,
        ),
        SkeletonSpec(
            skeleton_id="compat.p4p_salary",
            domain="compat",
            compat_archetype="p4p_salary",
            mode_allow=("BUY",),
            target_tiers=("ALL_NBA", "ALL_STAR", "HIGH_STARTER", "STARTER", "HIGH_ROTATION", "ROTATION"),
            priority=30,
            build_fn=build_buy_p4p_salary,
        ),
        SkeletonSpec(
            skeleton_id="compat.consolidate_2_for_1",
            domain="compat",
            compat_archetype="consolidate_2_for_1",
            mode_allow=("BUY",),
            target_tiers=("MVP", "ALL_NBA", "ALL_STAR", "HIGH_STARTER", "STARTER"),
            priority=40,
            build_fn=build_buy_consolidate_2_for_1,
        ),
        SkeletonSpec(
            skeleton_id="compat.buyer_picks",
            domain="compat",
            compat_archetype="buyer_picks",
            mode_allow=("SELL",),
            target_tiers=ALL_TARGET_TIERS,
            priority=10,
            build_fn=build_sell_buyer_picks,
        ),
        SkeletonSpec(
            skeleton_id="compat.buyer_young_plus_pick",
            domain="compat",
            compat_archetype="buyer_young_plus_pick",
            mode_allow=("SELL",),
            target_tiers=("ALL_NBA", "ALL_STAR", "HIGH_STARTER", "STARTER", "HIGH_ROTATION", "ROTATION"),
            priority=20,
            build_fn=build_sell_buyer_young_plus_pick,
        ),
        SkeletonSpec(
            skeleton_id="compat.buyer_p4p",
            domain="compat",
            compat_archetype="buyer_p4p",
            mode_allow=("SELL",),
            target_tiers=("ALL_NBA", "ALL_STAR", "HIGH_STARTER", "STARTER", "HIGH_ROTATION", "ROTATION"),
            priority=30,
            build_fn=build_sell_buyer_p4p,
        ),
        SkeletonSpec(
            skeleton_id="compat.buyer_consolidate",
            domain="compat",
            compat_archetype="buyer_consolidate",
            mode_allow=("SELL",),
            target_tiers=("MVP", "ALL_NBA", "ALL_STAR", "HIGH_STARTER", "STARTER"),
            priority=40,
            build_fn=build_sell_buyer_consolidate,
        ),
        SkeletonSpec(
            skeleton_id="player_swap.role_swap_small_delta",
            domain="player_swap",
            compat_archetype="p4p_salary",
            mode_allow=("BUY", "SELL"),
            target_tiers=("ALL_NBA", "ALL_STAR", "HIGH_STARTER", "STARTER", "HIGH_ROTATION", "ROTATION"),
            priority=50,
            build_fn=build_role_swap_small_delta,
        ),
        SkeletonSpec(
            skeleton_id="player_swap.fit_swap_2_for_2",
            domain="player_swap",
            compat_archetype="consolidate_2_for_1",
            mode_allow=("BUY", "SELL"),
            target_tiers=("MVP", "ALL_NBA", "ALL_STAR", "HIGH_STARTER", "STARTER"),
            priority=51,
            build_fn=build_fit_swap_2_for_2,
        ),
        SkeletonSpec(
            skeleton_id="player_swap.starter_for_two_rotation",
            domain="player_swap",
            compat_archetype="consolidate_2_for_1",
            mode_allow=("BUY", "SELL"),
            target_tiers=("MVP", "ALL_NBA", "ALL_STAR", "HIGH_STARTER", "STARTER"),
            priority=52,
            build_fn=build_starter_for_two_rotation,
            default_tags=("shape:1_for_2", "depth:return_two"),
        ),
        SkeletonSpec(
            skeleton_id="player_swap.one_for_two_depth",
            domain="player_swap",
            compat_archetype="consolidate_2_for_1",
            mode_allow=("BUY", "SELL"),
            target_tiers=("HIGH_STARTER", "STARTER", "HIGH_ROTATION", "ROTATION", "GARBEGE"),
            priority=53,
            build_fn=build_one_for_two_depth,
            gate_fn=lambda ctx: bool(ctx.target is not None or ctx.sale_asset is not None),
            default_tags=("shape:1_for_2", "intensity:low"),
            allows_modifiers=True,
        ),
        SkeletonSpec(
            skeleton_id="player_swap.three_for_one_upgrade",
            domain="player_swap",
            compat_archetype="consolidate_2_for_1",
            mode_allow=("BUY", "SELL"),
            target_tiers=("MVP", "ALL_NBA", "ALL_STAR", "HIGH_STARTER", "STARTER"),
            priority=54,
            build_fn=build_three_for_one_upgrade,
        ),
        SkeletonSpec(
            skeleton_id="player_swap.bench_bundle_for_role",
            domain="player_swap",
            compat_archetype="consolidate_2_for_1",
            mode_allow=("BUY", "SELL"),
            target_tiers=("STARTER", "HIGH_ROTATION", "ROTATION", "GARBEGE"),
            priority=55,
            build_fn=build_bench_bundle_for_role,
        ),
        SkeletonSpec(
            skeleton_id="player_swap.change_of_scenery_young",
            domain="player_swap",
            compat_archetype="young_plus_pick",
            mode_allow=("BUY", "SELL"),
            target_tiers=("STARTER", "HIGH_ROTATION", "ROTATION", "GARBEGE"),
            priority=56,
            build_fn=build_change_of_scenery_young,
        ),
        SkeletonSpec(
            skeleton_id="player_swap.star_lateral_plus_delta",
            domain="player_swap",
            compat_archetype="p4p_salary",
            mode_allow=("BUY", "SELL"),
            target_tiers=("MVP", "ALL_NBA", "ALL_STAR", "HIGH_STARTER"),
            priority=57,
            build_fn=build_star_lateral_plus_delta,
        ),
        SkeletonSpec(
            skeleton_id="timeline.veteran_for_young",
            domain="timeline",
            compat_archetype="young_plus_pick",
            mode_allow=("BUY", "SELL"),
            target_tiers=("HIGH_STARTER", "STARTER", "HIGH_ROTATION", "ROTATION", "GARBEGE"),
            priority=60,
            build_fn=build_veteran_for_young,
        ),
        SkeletonSpec(
            skeleton_id="timeline.veteran_for_young_plus_protected_first",
            domain="timeline",
            compat_archetype="young_plus_pick",
            mode_allow=("BUY", "SELL"),
            target_tiers=("MVP", "ALL_NBA", "ALL_STAR", "HIGH_STARTER", "STARTER"),
            priority=61,
            build_fn=build_veteran_for_young_plus_protected_first,
        ),
        SkeletonSpec(
            skeleton_id="timeline.bluechip_plus_first_plus_swap",
            domain="timeline",
            compat_archetype="young_plus_pick",
            mode_allow=("BUY", "SELL"),
            target_tiers=("MVP", "ALL_NBA", "ALL_STAR", "HIGH_STARTER"),
            priority=62,
            build_fn=build_bluechip_plus_first_plus_swap,
        ),
        SkeletonSpec(
            skeleton_id="salary_cleanup.rental_expiring_plus_second",
            domain="salary_cleanup",
            compat_archetype="p4p_salary",
            mode_allow=("BUY", "SELL"),
            target_tiers=("STARTER", "HIGH_ROTATION", "ROTATION", "GARBEGE"),
            priority=70,
            build_fn=build_rental_expiring_plus_second,
        ),
        SkeletonSpec(
            skeleton_id="salary_cleanup.pure_absorb_for_asset",
            domain="salary_cleanup",
            compat_archetype="picks_only",
            mode_allow=("BUY", "SELL"),
            target_tiers=ALL_TARGET_TIERS,
            priority=71,
            build_fn=build_pure_absorb_for_asset,
        ),
        SkeletonSpec(
            skeleton_id="salary_cleanup.partial_dump_for_expiring",
            domain="salary_cleanup",
            compat_archetype="p4p_salary",
            mode_allow=("BUY", "SELL"),
            target_tiers=("ALL_NBA", "ALL_STAR", "HIGH_STARTER", "STARTER", "HIGH_ROTATION", "ROTATION"),
            priority=72,
            build_fn=build_partial_dump_for_expiring,
        ),
        SkeletonSpec(
            skeleton_id="salary_cleanup.bad_money_swap",
            domain="salary_cleanup",
            compat_archetype="p4p_salary",
            mode_allow=("BUY", "SELL"),
            target_tiers=("ALL_NBA", "ALL_STAR", "HIGH_STARTER", "STARTER", "HIGH_ROTATION", "ROTATION"),
            priority=73,
            build_fn=build_bad_money_swap,
        ),
        SkeletonSpec(
            skeleton_id="pick_engineering.first_split",
            domain="pick_engineering",
            compat_archetype="picks_only",
            mode_allow=("BUY", "SELL"),
            target_tiers=ALL_TARGET_TIERS,
            priority=80,
            build_fn=build_first_split,
        ),
        SkeletonSpec(
            skeleton_id="pick_engineering.second_ladder_to_protected_first",
            domain="pick_engineering",
            compat_archetype="picks_only",
            mode_allow=("BUY", "SELL"),
            target_tiers=ALL_TARGET_TIERS,
            priority=81,
            build_fn=build_second_ladder_to_protected_first,
        ),
        SkeletonSpec(
            skeleton_id="pick_engineering.swap_purchase",
            domain="pick_engineering",
            compat_archetype="picks_only",
            mode_allow=("BUY", "SELL"),
            target_tiers=ALL_TARGET_TIERS,
            priority=82,
            build_fn=build_swap_purchase,
        ),
        SkeletonSpec(
            skeleton_id="pick_engineering.swap_substitute_for_first",
            domain="pick_engineering",
            compat_archetype="picks_only",
            mode_allow=("BUY", "SELL"),
            target_tiers=ALL_TARGET_TIERS,
            priority=83,
            build_fn=build_swap_substitute_for_first,
        ),
    )
    return SkeletonRegistry(specs=specs)
