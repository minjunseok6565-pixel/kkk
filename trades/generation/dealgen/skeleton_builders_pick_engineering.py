from __future__ import annotations

from typing import List, Optional

from ...models import Deal, PlayerAsset
from .skeleton_registry import BuildContext
from .types import DealCandidate
from .utils import _add_pick_package, _clone_deal


def _focal(ctx: BuildContext) -> tuple[Optional[str], str]:
    if ctx.target is not None:
        return ctx.target.player_id, str(ctx.target.need_tag)
    if ctx.sale_asset is not None:
        return ctx.sale_asset.player_id, str(ctx.match_tag or "match")
    return None, "unknown"


def _base_deal(ctx: BuildContext, focal_player_id: str) -> Deal:
    return Deal(
        teams=[str(ctx.buyer_id).upper(), str(ctx.seller_id).upper()],
        legs={
            str(ctx.buyer_id).upper(): [],
            str(ctx.seller_id).upper(): [PlayerAsset(kind="player", player_id=focal_player_id)],
        },
    )


def _cand(ctx: BuildContext, deal: Deal, skeleton_id: str, tag: str, focal_player_id: str) -> DealCandidate:
    archetype = "picks_only" if ctx.target is not None else "buyer_picks"
    return DealCandidate(
        deal=deal,
        buyer_id=ctx.buyer_id,
        seller_id=ctx.seller_id,
        focal_player_id=focal_player_id,
        archetype=archetype,
        skeleton_id=skeleton_id,
        skeleton_domain="pick_engineering",
        compat_archetype=archetype,
        tags=[f"need:{tag}", f"skeleton:{skeleton_id}", f"arch_compat:{archetype}"],
    )


def build_first_split(ctx: BuildContext) -> List[DealCandidate]:
    focal, tag = _focal(ctx)
    if not focal:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None:
        return []
    deal = _clone_deal(_base_deal(ctx, focal))
    _add_pick_package(deal, from_team=ctx.buyer_id, out_cat=buyer_out, catalog=ctx.catalog, config=ctx.config, rng=ctx.rng, prefer=("FIRST_SAFE", "SECOND"), max_picks=2, banned_asset_keys=ctx.banned_asset_keys)
    return [_cand(ctx, deal, "pick_engineering.first_split", tag, focal)]


def build_second_ladder_to_protected_first(ctx: BuildContext) -> List[DealCandidate]:
    focal, tag = _focal(ctx)
    if not focal:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None:
        return []
    deal = _clone_deal(_base_deal(ctx, focal))
    _add_pick_package(deal, from_team=ctx.buyer_id, out_cat=buyer_out, catalog=ctx.catalog, config=ctx.config, rng=ctx.rng, prefer=("SECOND", "SECOND", "FIRST_SAFE"), max_picks=3, banned_asset_keys=ctx.banned_asset_keys)
    return [_cand(ctx, deal, "pick_engineering.second_ladder_to_protected_first", tag, focal)]


def build_swap_purchase(ctx: BuildContext) -> List[DealCandidate]:
    focal, tag = _focal(ctx)
    if not focal:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None:
        return []
    deal = _clone_deal(_base_deal(ctx, focal))
    _add_pick_package(deal, from_team=ctx.buyer_id, out_cat=buyer_out, catalog=ctx.catalog, config=ctx.config, rng=ctx.rng, prefer=("SWAP", "SECOND"), max_picks=2, banned_asset_keys=ctx.banned_asset_keys)
    return [_cand(ctx, deal, "pick_engineering.swap_purchase", tag, focal)]


def build_swap_substitute_for_first(ctx: BuildContext) -> List[DealCandidate]:
    focal, tag = _focal(ctx)
    if not focal:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None:
        return []
    deal = _clone_deal(_base_deal(ctx, focal))
    _add_pick_package(deal, from_team=ctx.buyer_id, out_cat=buyer_out, catalog=ctx.catalog, config=ctx.config, rng=ctx.rng, prefer=("SWAP", "SWAP", "SECOND"), max_picks=3, banned_asset_keys=ctx.banned_asset_keys)
    return [_cand(ctx, deal, "pick_engineering.swap_substitute_for_first", tag, focal)]
