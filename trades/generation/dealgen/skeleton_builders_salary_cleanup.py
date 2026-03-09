from __future__ import annotations

from typing import List, Optional

from ...models import Deal, PlayerAsset
from .skeleton_registry import BuildContext
from .types import DealCandidate
from .utils import _add_pick_package, _can_absorb_without_outgoing, _clone_deal, _get_need_map, _pick_return_player_salaryish_with_need


def _focal(ctx: BuildContext) -> tuple[Optional[str], float, str]:
    if ctx.target is not None:
        return ctx.target.player_id, float(ctx.target.salary_m), str(ctx.target.need_tag)
    if ctx.sale_asset is not None:
        return ctx.sale_asset.player_id, float(ctx.sale_asset.salary_m), str(ctx.match_tag or "match")
    return None, 0.0, "unknown"


def _base_deal(ctx: BuildContext, focal_player_id: str) -> Deal:
    return Deal(
        teams=[str(ctx.buyer_id).upper(), str(ctx.seller_id).upper()],
        legs={
            str(ctx.buyer_id).upper(): [],
            str(ctx.seller_id).upper(): [PlayerAsset(kind="player", player_id=focal_player_id)],
        },
    )


def _cand(ctx: BuildContext, deal: Deal, skeleton_id: str, archetype: str, tag: str, focal_player_id: str) -> DealCandidate:
    return DealCandidate(
        deal=deal,
        buyer_id=ctx.buyer_id,
        seller_id=ctx.seller_id,
        focal_player_id=focal_player_id,
        archetype=archetype,
        skeleton_id=skeleton_id,
        skeleton_domain="salary_cleanup",
        compat_archetype=archetype,
        tags=[f"need:{tag}", f"skeleton:{skeleton_id}", f"arch_compat:{archetype}"],
    )


def build_rental_expiring_plus_second(ctx: BuildContext) -> List[DealCandidate]:
    focal, salary_m, tag = _focal(ctx)
    if not focal:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None:
        return []
    seller_need_map = _get_need_map(ctx.tick_ctx, ctx.seller_id)
    filler = _pick_return_player_salaryish_with_need(
        buyer_out,
        receiver_team_id=ctx.seller_id,
        target_salary_m=salary_m,
        need_map=seller_need_map,
        rng=ctx.rng,
        banned_players=ctx.banned_players,
        banned_receivers_by_player=ctx.banned_receivers_by_player,
        must_be_aggregation_friendly=True,
    )
    if not filler:
        return []
    deal = _clone_deal(_base_deal(ctx, focal))
    deal.legs[str(ctx.buyer_id).upper()].append(PlayerAsset(kind="player", player_id=filler))
    _add_pick_package(deal, from_team=ctx.buyer_id, out_cat=buyer_out, catalog=ctx.catalog, config=ctx.config, rng=ctx.rng, prefer=("SECOND",), max_picks=1, banned_asset_keys=ctx.banned_asset_keys)
    return [_cand(ctx, deal, "salary_cleanup.rental_expiring_plus_second", "p4p_salary", tag, focal)]


def build_pure_absorb_for_asset(ctx: BuildContext) -> List[DealCandidate]:
    focal, salary_m, tag = _focal(ctx)
    if not focal:
        return []
    ts_buyer = ctx.tick_ctx.get_team_situation(ctx.buyer_id)
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None or not _can_absorb_without_outgoing(ts_buyer, salary_m):
        return []
    deal = _clone_deal(_base_deal(ctx, focal))
    _add_pick_package(deal, from_team=ctx.buyer_id, out_cat=buyer_out, catalog=ctx.catalog, config=ctx.config, rng=ctx.rng, prefer=("SECOND", "FIRST_SAFE"), max_picks=2, banned_asset_keys=ctx.banned_asset_keys)
    return [_cand(ctx, deal, "salary_cleanup.pure_absorb_for_asset", "picks_only", tag, focal)]


def build_partial_dump_for_expiring(ctx: BuildContext) -> List[DealCandidate]:
    focal, salary_m, tag = _focal(ctx)
    if not focal:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None:
        return []
    seller_need_map = _get_need_map(ctx.tick_ctx, ctx.seller_id)
    filler = _pick_return_player_salaryish_with_need(
        buyer_out,
        receiver_team_id=ctx.seller_id,
        target_salary_m=max(1.0, salary_m * 0.7),
        need_map=seller_need_map,
        rng=ctx.rng,
        banned_players=ctx.banned_players,
        banned_receivers_by_player=ctx.banned_receivers_by_player,
        must_be_aggregation_friendly=True,
    )
    if not filler:
        return []
    deal = _clone_deal(_base_deal(ctx, focal))
    deal.legs[str(ctx.buyer_id).upper()].append(PlayerAsset(kind="player", player_id=filler))
    _add_pick_package(deal, from_team=ctx.buyer_id, out_cat=buyer_out, catalog=ctx.catalog, config=ctx.config, rng=ctx.rng, prefer=("SECOND",), max_picks=1, banned_asset_keys=ctx.banned_asset_keys)
    return [_cand(ctx, deal, "salary_cleanup.partial_dump_for_expiring", "p4p_salary", tag, focal)]


def build_bad_money_swap(ctx: BuildContext) -> List[DealCandidate]:
    focal, salary_m, tag = _focal(ctx)
    if not focal:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None:
        return []
    seller_need_map = _get_need_map(ctx.tick_ctx, ctx.seller_id)
    filler = _pick_return_player_salaryish_with_need(
        buyer_out,
        receiver_team_id=ctx.seller_id,
        target_salary_m=salary_m,
        need_map=seller_need_map,
        rng=ctx.rng,
        banned_players=ctx.banned_players,
        banned_receivers_by_player=ctx.banned_receivers_by_player,
        must_be_aggregation_friendly=False,
    )
    if not filler:
        return []
    deal = _clone_deal(_base_deal(ctx, focal))
    deal.legs[str(ctx.buyer_id).upper()].append(PlayerAsset(kind="player", player_id=filler))
    _add_pick_package(deal, from_team=ctx.buyer_id, out_cat=buyer_out, catalog=ctx.catalog, config=ctx.config, rng=ctx.rng, prefer=("SECOND",), max_picks=1, banned_asset_keys=ctx.banned_asset_keys)
    return [_cand(ctx, deal, "salary_cleanup.bad_money_swap", "p4p_salary", tag, focal)]
