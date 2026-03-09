from __future__ import annotations

from typing import List, Optional

from ...models import Deal, PlayerAsset
from .skeleton_registry import BuildContext
from .types import DealCandidate
from .utils import (
    _add_pick_package,
    _best_need_tag,
    _clone_deal,
    _get_need_map,
    _pick_bucket_player_for_need,
    _pick_return_player_salaryish_with_need,
    _split_young_candidates,
)


def _focal(ctx: BuildContext) -> tuple[Optional[str], float, str, str]:
    if ctx.target is not None:
        return ctx.target.player_id, float(ctx.target.salary_m), str(ctx.target.need_tag), "BUY"
    if ctx.sale_asset is not None:
        return ctx.sale_asset.player_id, float(ctx.sale_asset.salary_m), str(ctx.match_tag or "match"), "SELL"
    return None, 0.0, "unknown", "BUY"


def _base_deal(ctx: BuildContext, focal_player_id: str) -> Deal:
    return Deal(
        teams=[str(ctx.buyer_id).upper(), str(ctx.seller_id).upper()],
        legs={
            str(ctx.buyer_id).upper(): [],
            str(ctx.seller_id).upper(): [PlayerAsset(kind="player", player_id=focal_player_id)],
        },
    )


def _cand(ctx: BuildContext, *, deal: Deal, archetype: str, skeleton_id: str, tags: List[str], focal_player_id: str) -> DealCandidate:
    return DealCandidate(
        deal=deal,
        buyer_id=ctx.buyer_id,
        seller_id=ctx.seller_id,
        focal_player_id=focal_player_id,
        archetype=archetype,
        skeleton_id=skeleton_id,
        skeleton_domain="player_swap",
        compat_archetype=archetype,
        tags=[*tags, f"skeleton:{skeleton_id}", f"arch_compat:{archetype}"],
    )


def build_role_swap_small_delta(ctx: BuildContext) -> List[DealCandidate]:
    focal, salary_m, tag, _ = _focal(ctx)
    if not focal:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None:
        return []
    seller_need_map = _get_need_map(ctx.tick_ctx, ctx.seller_id)
    pid = _pick_return_player_salaryish_with_need(
        buyer_out,
        receiver_team_id=ctx.seller_id,
        target_salary_m=salary_m,
        need_map=seller_need_map,
        rng=ctx.rng,
        banned_players=ctx.banned_players,
        banned_receivers_by_player=ctx.banned_receivers_by_player,
        must_be_aggregation_friendly=False,
    )
    if not pid:
        return []
    deal = _clone_deal(_base_deal(ctx, focal))
    deal.legs[str(ctx.buyer_id).upper()].append(PlayerAsset(kind="player", player_id=pid))
    _add_pick_package(deal, from_team=ctx.buyer_id, out_cat=buyer_out, catalog=ctx.catalog, config=ctx.config, rng=ctx.rng, prefer=("SECOND", "SWAP"), max_picks=1, banned_asset_keys=ctx.banned_asset_keys)
    return [_cand(ctx, deal=deal, archetype="p4p_salary", skeleton_id="player_swap.role_swap_small_delta", tags=[f"need:{tag}", "pkg:role_swap_small_delta"], focal_player_id=focal)]


def build_fit_swap_2_for_2(ctx: BuildContext) -> List[DealCandidate]:
    focal, _, tag, _ = _focal(ctx)
    if not focal:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    seller_need_map = _get_need_map(ctx.tick_ctx, ctx.seller_id)
    if buyer_out is None:
        return []
    p1 = _pick_bucket_player_for_need(buyer_out, bucket="CONSOLIDATE", receiver_team_id=ctx.seller_id, banned_players=ctx.banned_players, banned_receivers_by_player=ctx.banned_receivers_by_player, need_map=seller_need_map, must_be_aggregation_friendly=True)
    p2 = _pick_bucket_player_for_need(buyer_out, bucket="FILLER_CHEAP", receiver_team_id=ctx.seller_id, banned_players=ctx.banned_players, banned_receivers_by_player=ctx.banned_receivers_by_player, need_map=seller_need_map, must_be_aggregation_friendly=True)
    if not p1 or not p2 or p1 == p2:
        return []
    deal = _clone_deal(_base_deal(ctx, focal))
    deal.legs[str(ctx.buyer_id).upper()].extend([PlayerAsset(kind="player", player_id=p1), PlayerAsset(kind="player", player_id=p2)])
    return [_cand(ctx, deal=deal, archetype="consolidate_2_for_1", skeleton_id="player_swap.fit_swap_2_for_2", tags=[f"need:{tag}", "pkg:fit_swap_2_for_2"], focal_player_id=focal)]


def build_starter_for_two_rotation(ctx: BuildContext) -> List[DealCandidate]:
    focal, _, tag, _ = _focal(ctx)
    if not focal:
        return []
    base = build_fit_swap_2_for_2(ctx)
    if not base:
        return []
    return [
        _cand(
            ctx,
            deal=base[0].deal,
            archetype="consolidate_2_for_1",
            skeleton_id="player_swap.starter_for_two_rotation",
            tags=[f"need:{tag}", "pkg:starter_for_two_rotation"],
            focal_player_id=focal,
        )
    ]


def build_one_for_two_depth(ctx: BuildContext) -> List[DealCandidate]:
    """1 ↔ 2 depth: starter_for_two_rotation의 저강도(ROLE/STARTER 중심) 버전."""

    focal, salary_m, tag, _ = _focal(ctx)
    if not focal:
        return []

    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None:
        return []

    seller_need_map = _get_need_map(ctx.tick_ctx, ctx.seller_id)
    p1 = _pick_bucket_player_for_need(
        buyer_out,
        bucket="FILLER_CHEAP",
        receiver_team_id=ctx.seller_id,
        banned_players=ctx.banned_players,
        banned_receivers_by_player=ctx.banned_receivers_by_player,
        need_map=seller_need_map,
        must_be_aggregation_friendly=True,
    )
    p2 = _pick_return_player_salaryish_with_need(
        buyer_out,
        receiver_team_id=ctx.seller_id,
        target_salary_m=max(1.0, salary_m * 0.45),
        need_map=seller_need_map,
        rng=ctx.rng,
        banned_players=ctx.banned_players | {str(p1 or "")},
        banned_receivers_by_player=ctx.banned_receivers_by_player,
        must_be_aggregation_friendly=True,
    )
    if not p1 or not p2 or p1 == p2:
        return []

    deal = _clone_deal(_base_deal(ctx, focal))
    deal.legs[str(ctx.buyer_id).upper()].extend(
        [
            PlayerAsset(kind="player", player_id=p1),
            PlayerAsset(kind="player", player_id=p2),
        ]
    )
    return [
        _cand(
            ctx,
            deal=deal,
            archetype="consolidate_2_for_1",
            skeleton_id="player_swap.one_for_two_depth",
            tags=[f"need:{tag}", "pkg:one_for_two_depth"],
            focal_player_id=focal,
        )
    ]


def build_three_for_one_upgrade(ctx: BuildContext) -> List[DealCandidate]:
    focal, salary_m, tag, _ = _focal(ctx)
    if not focal:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    seller_need_map = _get_need_map(ctx.tick_ctx, ctx.seller_id)
    if buyer_out is None:
        return []
    p1 = _pick_bucket_player_for_need(buyer_out, bucket="CONSOLIDATE", receiver_team_id=ctx.seller_id, banned_players=ctx.banned_players, banned_receivers_by_player=ctx.banned_receivers_by_player, need_map=seller_need_map, must_be_aggregation_friendly=True)
    p2 = _pick_bucket_player_for_need(buyer_out, bucket="FILLER_CHEAP", receiver_team_id=ctx.seller_id, banned_players=ctx.banned_players, banned_receivers_by_player=ctx.banned_receivers_by_player, need_map=seller_need_map, must_be_aggregation_friendly=True)
    p3 = _pick_return_player_salaryish_with_need(buyer_out, receiver_team_id=ctx.seller_id, target_salary_m=max(1.0, salary_m * 0.5), need_map=seller_need_map, rng=ctx.rng, banned_players=ctx.banned_players | {str(p1 or ""), str(p2 or "")}, banned_receivers_by_player=ctx.banned_receivers_by_player, must_be_aggregation_friendly=True)
    if not p1 or not p2 or not p3 or len({p1, p2, p3}) < 3:
        return []
    deal = _clone_deal(_base_deal(ctx, focal))
    deal.legs[str(ctx.buyer_id).upper()].extend([PlayerAsset(kind="player", player_id=p1), PlayerAsset(kind="player", player_id=p2), PlayerAsset(kind="player", player_id=p3)])
    return [_cand(ctx, deal=deal, archetype="consolidate_2_for_1", skeleton_id="player_swap.three_for_one_upgrade", tags=[f"need:{tag}", "pkg:three_for_one_upgrade"], focal_player_id=focal)]


def build_bench_bundle_for_role(ctx: BuildContext) -> List[DealCandidate]:
    focal, _, tag, _ = _focal(ctx)
    if not focal:
        return []
    base = build_fit_swap_2_for_2(ctx)
    if not base:
        return []
    deal = _clone_deal(base[0].deal)
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is not None:
        _add_pick_package(deal, from_team=ctx.buyer_id, out_cat=buyer_out, catalog=ctx.catalog, config=ctx.config, rng=ctx.rng, prefer=("SECOND",), max_picks=2, banned_asset_keys=ctx.banned_asset_keys)
    return [_cand(ctx, deal=deal, archetype="consolidate_2_for_1", skeleton_id="player_swap.bench_bundle_for_role", tags=[f"need:{tag}", "pkg:bench_bundle_for_role"], focal_player_id=focal)]


def build_change_of_scenery_young(ctx: BuildContext) -> List[DealCandidate]:
    focal, _, tag, _ = _focal(ctx)
    if not focal:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None:
        return []
    prospect_ids, throwin_ids = _split_young_candidates(
        buyer_out,
        config=ctx.config,
        receiver_team_id=ctx.seller_id,
        banned_players=ctx.banned_players,
        banned_receivers_by_player=ctx.banned_receivers_by_player,
        must_be_aggregation_friendly=True,
    )
    pool = prospect_ids if prospect_ids else throwin_ids
    if not pool:
        return []
    yid = pool[0]
    deal = _clone_deal(_base_deal(ctx, focal))
    deal.legs[str(ctx.buyer_id).upper()].append(PlayerAsset(kind="player", player_id=yid))
    return [_cand(ctx, deal=deal, archetype="young_plus_pick", skeleton_id="player_swap.change_of_scenery_young", tags=[f"need:{tag}", "pkg:change_of_scenery_young"], focal_player_id=focal)]


def build_star_lateral_plus_delta(ctx: BuildContext) -> List[DealCandidate]:
    focal, _, tag, _ = _focal(ctx)
    base = build_role_swap_small_delta(ctx)
    if not focal or not base:
        return []
    deal = _clone_deal(base[0].deal)
    return [_cand(ctx, deal=deal, archetype="p4p_salary", skeleton_id="player_swap.star_lateral_plus_delta", tags=[f"need:{tag}", "pkg:star_lateral_plus_delta"], focal_player_id=focal)]
