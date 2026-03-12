from __future__ import annotations

from typing import List, Optional

from ...models import Deal, PlayerAsset
from .skeleton_registry import BuildContext
from .types import DealCandidate
from .utils import _add_pick_package, _clone_deal, _split_young_candidates


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
        skeleton_domain="timeline",
        compat_archetype=archetype,
        tags=[f"need:{tag}", f"skeleton:{skeleton_id}", f"arch_compat:{archetype}"],
    )


def build_veteran_for_young(ctx: BuildContext) -> List[DealCandidate]:
    focal, _, tag = _focal(ctx)
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
    deal = _clone_deal(_base_deal(ctx, focal))
    deal.legs[str(ctx.buyer_id).upper()].append(PlayerAsset(kind="player", player_id=pool[0]))
    return [_cand(ctx, deal, "timeline.veteran_for_young", "young_plus_pick", tag, focal)]


def build_veteran_for_young_plus_protected_first(ctx: BuildContext) -> List[DealCandidate]:
    base = build_veteran_for_young(ctx)
    focal, _, tag = _focal(ctx)
    if not base or not focal:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    deal = _clone_deal(base[0].deal)
    if buyer_out is not None:
        _add_pick_package(deal, from_team=ctx.buyer_id, out_cat=buyer_out, catalog=ctx.catalog, config=ctx.config, rng=ctx.rng, prefer=("FIRST_SAFE", "SECOND"), max_picks=1, banned_asset_keys=ctx.banned_asset_keys)
    return [_cand(ctx, deal, "timeline.veteran_for_young_plus_protected_first", "young_plus_pick", tag, focal)]


def build_bluechip_plus_first_plus_swap(ctx: BuildContext) -> List[DealCandidate]:
    base = build_veteran_for_young(ctx)
    focal, _, tag = _focal(ctx)
    if not base or not focal:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    deal = _clone_deal(base[0].deal)
    if buyer_out is not None:
        _add_pick_package(deal, from_team=ctx.buyer_id, out_cat=buyer_out, catalog=ctx.catalog, config=ctx.config, rng=ctx.rng, prefer=("FIRST_SAFE", "SWAP", "SECOND"), max_picks=2, banned_asset_keys=ctx.banned_asset_keys)
    return [_cand(ctx, deal, "timeline.bluechip_plus_first_plus_swap", "young_plus_pick", tag, focal)]
