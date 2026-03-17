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
            "GARBAGE": "skeleton_route_garbage",
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


ALL_TARGET_TIERS: Tuple[str, ...] = ("MVP", "ALL_NBA", "ALL_STAR", "HIGH_STARTER", "STARTER", "HIGH_ROTATION", "ROTATION", "GARBAGE")


def build_default_registry() -> SkeletonRegistry:
    from .skeleton_builders_mvp import build_mvp_mixed, build_mvp_pick_heavy, build_mvp_player_heavy
    from .skeleton_builders_all_nba import build_all_nba_mixed, build_all_nba_pick_heavy, build_all_nba_player_heavy
    from .skeleton_builders_all_star import build_all_star_mixed, build_all_star_pick_heavy, build_all_star_player_heavy
    from .skeleton_builders_high_starter import (
        build_high_starter_mixed,
        build_high_starter_pick_heavy,
        build_high_starter_player_heavy,
    )
    from .skeleton_builders_starter import build_starter_mixed, build_starter_pick_heavy, build_starter_player_heavy
    from .skeleton_builders_high_rotation import (
        build_high_rotation_mixed,
        build_high_rotation_pick_heavy,
        build_high_rotation_player_heavy,
    )
    from .skeleton_builders_rotation import build_rotation_mixed, build_rotation_pick_heavy, build_rotation_player_heavy
    from .skeleton_builders_garbage import build_garbage_garbage
    from .skeleton_builders_timeline import (
        build_bluechip_plus_first_plus_swap,
        build_veteran_for_young,
        build_veteran_for_young_plus_protected_first,
    )

    specs: Tuple[SkeletonSpec, ...] = (
        SkeletonSpec("mvp.player_heavy", "tier_score", "player_heavy", ("BUY", "SELL"), ("MVP",), 20, build_mvp_player_heavy),
        SkeletonSpec("mvp.pick_heavy", "tier_score", "pick_heavy", ("BUY", "SELL"), ("MVP",), 21, build_mvp_pick_heavy),
        SkeletonSpec("mvp.mixed", "tier_score", "mixed", ("BUY", "SELL"), ("MVP",), 22, build_mvp_mixed),
        SkeletonSpec("all_nba.player_heavy", "tier_score", "player_heavy", ("BUY", "SELL"), ("ALL_NBA",), 23, build_all_nba_player_heavy),
        SkeletonSpec("all_nba.pick_heavy", "tier_score", "pick_heavy", ("BUY", "SELL"), ("ALL_NBA",), 24, build_all_nba_pick_heavy),
        SkeletonSpec("all_nba.mixed", "tier_score", "mixed", ("BUY", "SELL"), ("ALL_NBA",), 25, build_all_nba_mixed),
        SkeletonSpec("all_star.player_heavy", "tier_score", "player_heavy", ("BUY", "SELL"), ("ALL_STAR",), 26, build_all_star_player_heavy),
        SkeletonSpec("all_star.pick_heavy", "tier_score", "pick_heavy", ("BUY", "SELL"), ("ALL_STAR",), 27, build_all_star_pick_heavy),
        SkeletonSpec("all_star.mixed", "tier_score", "mixed", ("BUY", "SELL"), ("ALL_STAR",), 28, build_all_star_mixed),
        SkeletonSpec("high_starter.player_heavy", "tier_score", "player_heavy", ("BUY", "SELL"), ("HIGH_STARTER",), 29, build_high_starter_player_heavy),
        SkeletonSpec("high_starter.pick_heavy", "tier_score", "pick_heavy", ("BUY", "SELL"), ("HIGH_STARTER",), 30, build_high_starter_pick_heavy),
        SkeletonSpec("high_starter.mixed", "tier_score", "mixed", ("BUY", "SELL"), ("HIGH_STARTER",), 31, build_high_starter_mixed),
        SkeletonSpec("starter.player_heavy", "tier_score", "player_heavy", ("BUY", "SELL"), ("STARTER",), 32, build_starter_player_heavy),
        SkeletonSpec("starter.pick_heavy", "tier_score", "pick_heavy", ("BUY", "SELL"), ("STARTER",), 33, build_starter_pick_heavy),
        SkeletonSpec("starter.mixed", "tier_score", "mixed", ("BUY", "SELL"), ("STARTER",), 34, build_starter_mixed),
        SkeletonSpec("high_rotation.player_heavy", "tier_score", "player_heavy", ("BUY", "SELL"), ("HIGH_ROTATION",), 35, build_high_rotation_player_heavy),
        SkeletonSpec("high_rotation.pick_heavy", "tier_score", "pick_heavy", ("BUY", "SELL"), ("HIGH_ROTATION",), 36, build_high_rotation_pick_heavy),
        SkeletonSpec("high_rotation.mixed", "tier_score", "mixed", ("BUY", "SELL"), ("HIGH_ROTATION",), 37, build_high_rotation_mixed),
        SkeletonSpec("rotation.player_heavy", "tier_score", "player_heavy", ("BUY", "SELL"), ("ROTATION",), 38, build_rotation_player_heavy),
        SkeletonSpec("rotation.pick_heavy", "tier_score", "pick_heavy", ("BUY", "SELL"), ("ROTATION",), 39, build_rotation_pick_heavy),
        SkeletonSpec("rotation.mixed", "tier_score", "mixed", ("BUY", "SELL"), ("ROTATION",), 40, build_rotation_mixed),
        SkeletonSpec("garbage.garbage", "tier_score", "mixed", ("BUY", "SELL"), ("GARBAGE",), 41, build_garbage_garbage),
        SkeletonSpec(
            skeleton_id="timeline.veteran_for_young",
            domain="timeline",
            compat_archetype="young_plus_pick",
            mode_allow=("BUY", "SELL"),
            target_tiers=("HIGH_STARTER", "STARTER", "HIGH_ROTATION", "ROTATION", "GARBAGE"),
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
    )
    return SkeletonRegistry(specs=specs)
