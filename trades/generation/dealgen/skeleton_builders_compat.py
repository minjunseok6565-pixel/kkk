from __future__ import annotations

from typing import List, Optional, Set

from ...models import Deal, PlayerAsset
from .skeleton_registry import BuildContext
from .types import DealCandidate
from .utils import (
    _add_pick_package,
    _best_need_tag,
    _can_absorb_without_outgoing,
    _clone_deal,
    _get_need_map,
    _pick_bucket_player_for_need,
    _pick_from_id_pool_for_need,
    _pick_return_player_salaryish_with_need,
    _split_young_candidates,
)


def _with_core_tags(tags: List[str], *, mode: str, focal_player_id: str, archetype: str, skeleton_id: str) -> List[str]:
    out = list(tags)
    for t in (
        f"mode:{str(mode).upper()}",
        f"focal:{focal_player_id}",
        f"arch:{archetype}",
        f"skeleton:{skeleton_id}",
        f"arch_compat:{archetype}",
    ):
        if t not in out:
            out.append(t)
    return out


def _base_buy_deal(buyer_id: str, seller_id: str, target_player_id: str) -> Deal:
    return Deal(
        teams=[str(buyer_id).upper(), str(seller_id).upper()],
        legs={
            str(buyer_id).upper(): [],
            str(seller_id).upper(): [PlayerAsset(kind="player", player_id=target_player_id)],
        },
    )


def build_buy_picks_only(ctx: BuildContext) -> List[DealCandidate]:
    if ctx.target is None:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None:
        return []
    ts_buyer = ctx.tick_ctx.get_team_situation(ctx.buyer_id)
    ts_seller = ctx.tick_ctx.get_team_situation(ctx.seller_id)
    if not _can_absorb_without_outgoing(ts_buyer, float(ctx.target.salary_m)):
        return []
    deal = _clone_deal(_base_buy_deal(ctx.buyer_id, ctx.seller_id, ctx.target.player_id))
    max_picks = 2 if str(getattr(ts_seller, "time_horizon", "RE_TOOL") or "RE_TOOL") == "REBUILD" else 1
    _add_pick_package(
        deal,
        from_team=ctx.buyer_id,
        out_cat=buyer_out,
        catalog=ctx.catalog,
        config=ctx.config,
        rng=ctx.rng,
        prefer=("SECOND", "FIRST_SAFE"),
        max_picks=max_picks,
        banned_asset_keys=ctx.banned_asset_keys,
    )
    return [DealCandidate(
        deal=deal,
        buyer_id=ctx.buyer_id,
        seller_id=ctx.seller_id,
        focal_player_id=ctx.target.player_id,
        archetype="picks_only",
        skeleton_id="compat.picks_only",
        skeleton_domain="compat",
        compat_archetype="picks_only",
        tags=_with_core_tags([f"need:{ctx.target.need_tag}", "pkg:picks"], mode="BUY", focal_player_id=ctx.target.player_id, archetype="picks_only", skeleton_id="compat.picks_only"),
    )]


def build_buy_young_plus_pick(ctx: BuildContext) -> List[DealCandidate]:
    if ctx.target is None:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None:
        return []
    seller_need_map = _get_need_map(ctx.tick_ctx, ctx.seller_id)
    ts_seller = ctx.tick_ctx.get_team_situation(ctx.seller_id)
    rebuildish = str(getattr(ts_seller, "time_horizon", "RE_TOOL") or "RE_TOOL") in {"REBUILD", "RE_TOOL"}

    prospect_ids, throwin_ids = _split_young_candidates(
        buyer_out,
        config=ctx.config,
        receiver_team_id=ctx.seller_id,
        banned_players=ctx.banned_players,
        banned_receivers_by_player=ctx.banned_receivers_by_player,
        must_be_aggregation_friendly=True,
    )

    young_id: Optional[str] = None
    source_tag: Optional[str] = None
    if rebuildish:
        pool = prospect_ids if prospect_ids else throwin_ids
        if pool:
            max_prospect = int(getattr(ctx.config, "young_prospect_max_candidates", 6) or 6)
            top_n = max(2, min(max_prospect, len(pool)))
            bucket = list(pool[:top_n])
            ctx.rng.shuffle(bucket)
            young_id = bucket[0]
            source_tag = "young_source:prospect" if prospect_ids else "young_source:throwin"
    else:
        pool = throwin_ids
        if pool:
            young_id = _pick_from_id_pool_for_need(
                buyer_out,
                pool_ids=pool,
                receiver_team_id=ctx.seller_id,
                target_salary_m=float(ctx.target.salary_m),
                need_map=seller_need_map,
                rng=ctx.rng,
                banned_players=ctx.banned_players,
                banned_receivers_by_player=ctx.banned_receivers_by_player,
                must_be_aggregation_friendly=True,
                top_scan=6,
            )
            source_tag = "young_source:throwin"

    if not young_id:
        return []

    deal = _clone_deal(_base_buy_deal(ctx.buyer_id, ctx.seller_id, ctx.target.player_id))
    deal.legs[str(ctx.buyer_id).upper()].append(PlayerAsset(kind="player", player_id=young_id))
    _add_pick_package(
        deal,
        from_team=ctx.buyer_id,
        out_cat=buyer_out,
        catalog=ctx.catalog,
        config=ctx.config,
        rng=ctx.rng,
        prefer=("SECOND",),
        max_picks=1,
        banned_asset_keys=ctx.banned_asset_keys,
    )
    tags = [f"need:{ctx.target.need_tag}", "pkg:young+pick"]
    if source_tag:
        tags.append(source_tag)
    c_ret = buyer_out.players.get(str(young_id))
    if c_ret is not None:
        rt = _best_need_tag(seller_need_map, c_ret)
        if rt:
            tags.append(f"return_need:{rt}")
    return [DealCandidate(
        deal=deal,
        buyer_id=ctx.buyer_id,
        seller_id=ctx.seller_id,
        focal_player_id=ctx.target.player_id,
        archetype="young_plus_pick",
        skeleton_id="compat.young_plus_pick",
        skeleton_domain="compat",
        compat_archetype="young_plus_pick",
        tags=_with_core_tags(tags, mode="BUY", focal_player_id=ctx.target.player_id, archetype="young_plus_pick", skeleton_id="compat.young_plus_pick"),
    )]


def build_buy_p4p_salary(ctx: BuildContext) -> List[DealCandidate]:
    if ctx.target is None:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None:
        return []
    seller_need_map = _get_need_map(ctx.tick_ctx, ctx.seller_id)
    filler_id = _pick_return_player_salaryish_with_need(
        buyer_out,
        receiver_team_id=ctx.seller_id,
        target_salary_m=float(ctx.target.salary_m),
        need_map=seller_need_map,
        rng=ctx.rng,
        banned_players=ctx.banned_players,
        banned_receivers_by_player=ctx.banned_receivers_by_player,
        must_be_aggregation_friendly=False,
    )
    if not filler_id:
        return []
    deal = _clone_deal(_base_buy_deal(ctx.buyer_id, ctx.seller_id, ctx.target.player_id))
    deal.legs[str(ctx.buyer_id).upper()].append(PlayerAsset(kind="player", player_id=filler_id))
    tags = [f"need:{ctx.target.need_tag}", "pkg:player_for_player"]
    c_ret = buyer_out.players.get(str(filler_id))
    if c_ret is not None:
        rt = _best_need_tag(seller_need_map, c_ret)
        if rt:
            tags.append(f"return_need:{rt}")
    return [DealCandidate(
        deal=deal,
        buyer_id=ctx.buyer_id,
        seller_id=ctx.seller_id,
        focal_player_id=ctx.target.player_id,
        archetype="p4p_salary",
        skeleton_id="compat.p4p_salary",
        skeleton_domain="compat",
        compat_archetype="p4p_salary",
        tags=_with_core_tags(tags, mode="BUY", focal_player_id=ctx.target.player_id, archetype="p4p_salary", skeleton_id="compat.p4p_salary"),
    )]


def build_buy_consolidate_2_for_1(ctx: BuildContext) -> List[DealCandidate]:
    if ctx.target is None:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None:
        return []
    seller_need_map = _get_need_map(ctx.tick_ctx, ctx.seller_id)
    cons_id = _pick_bucket_player_for_need(
        buyer_out,
        bucket="CONSOLIDATE",
        receiver_team_id=ctx.seller_id,
        banned_players=ctx.banned_players,
        banned_receivers_by_player=ctx.banned_receivers_by_player,
        must_be_aggregation_friendly=True,
        need_map=seller_need_map,
    )
    cheap_id = _pick_bucket_player_for_need(
        buyer_out,
        bucket="FILLER_CHEAP",
        receiver_team_id=ctx.seller_id,
        banned_players=ctx.banned_players,
        banned_receivers_by_player=ctx.banned_receivers_by_player,
        must_be_aggregation_friendly=True,
        need_map=seller_need_map,
    )
    if not cons_id or not cheap_id or cons_id == cheap_id:
        return []

    deal = _clone_deal(_base_buy_deal(ctx.buyer_id, ctx.seller_id, ctx.target.player_id))
    deal.legs[str(ctx.buyer_id).upper()].extend([PlayerAsset(kind="player", player_id=cons_id), PlayerAsset(kind="player", player_id=cheap_id)])
    _add_pick_package(
        deal,
        from_team=ctx.buyer_id,
        out_cat=buyer_out,
        catalog=ctx.catalog,
        config=ctx.config,
        rng=ctx.rng,
        prefer=("SECOND",),
        max_picks=1,
        banned_asset_keys=ctx.banned_asset_keys,
    )
    tags = [f"need:{ctx.target.need_tag}", "pkg:consolidate"]
    rt_seen: Set[str] = set()
    for _pid in (cons_id, cheap_id):
        c_ret = buyer_out.players.get(str(_pid))
        if c_ret is None:
            continue
        rt = _best_need_tag(seller_need_map, c_ret)
        if rt and rt not in rt_seen:
            rt_seen.add(rt)
            tags.append(f"return_need:{rt}")
    return [DealCandidate(
        deal=deal,
        buyer_id=ctx.buyer_id,
        seller_id=ctx.seller_id,
        focal_player_id=ctx.target.player_id,
        archetype="consolidate_2_for_1",
        skeleton_id="compat.consolidate_2_for_1",
        skeleton_domain="compat",
        compat_archetype="consolidate_2_for_1",
        tags=_with_core_tags(tags, mode="BUY", focal_player_id=ctx.target.player_id, archetype="consolidate_2_for_1", skeleton_id="compat.consolidate_2_for_1"),
    )]


def _base_sell_deal(buyer_id: str, seller_id: str, sale_player_id: str) -> Deal:
    return Deal(
        teams=[str(buyer_id).upper(), str(seller_id).upper()],
        legs={
            str(buyer_id).upper(): [],
            str(seller_id).upper(): [PlayerAsset(kind="player", player_id=sale_player_id)],
        },
    )


def build_sell_buyer_picks(ctx: BuildContext) -> List[DealCandidate]:
    if ctx.sale_asset is None:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None:
        return []
    ts_seller = ctx.tick_ctx.get_team_situation(ctx.seller_id)
    ts_buyer = ctx.tick_ctx.get_team_situation(ctx.buyer_id)
    if not _can_absorb_without_outgoing(ts_buyer, float(ctx.sale_asset.salary_m)):
        return []
    deal = _clone_deal(_base_sell_deal(ctx.buyer_id, ctx.seller_id, ctx.sale_asset.player_id))
    max_picks = 2 if str(getattr(ts_seller, "time_horizon", "RE_TOOL") or "RE_TOOL") == "REBUILD" else 1
    _add_pick_package(
        deal,
        from_team=ctx.buyer_id,
        out_cat=buyer_out,
        catalog=ctx.catalog,
        config=ctx.config,
        rng=ctx.rng,
        prefer=("SECOND", "FIRST_SAFE"),
        max_picks=max_picks,
        banned_asset_keys=ctx.banned_asset_keys,
    )
    return [DealCandidate(
        deal=deal,
        buyer_id=ctx.buyer_id,
        seller_id=ctx.seller_id,
        focal_player_id=ctx.sale_asset.player_id,
        archetype="buyer_picks",
        skeleton_id="compat.buyer_picks",
        skeleton_domain="compat",
        compat_archetype="buyer_picks",
        tags=_with_core_tags([f"match:{ctx.match_tag}", "pkg:picks"], mode="SELL", focal_player_id=ctx.sale_asset.player_id, archetype="buyer_picks", skeleton_id="compat.buyer_picks"),
    )]


def build_sell_buyer_young_plus_pick(ctx: BuildContext) -> List[DealCandidate]:
    if ctx.sale_asset is None:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None:
        return []
    time_horizon = str(getattr(ctx.tick_ctx.get_team_situation(ctx.seller_id), "time_horizon", "RE_TOOL") or "RE_TOOL")
    seller_need_map = _get_need_map(ctx.tick_ctx, ctx.seller_id)
    rebuildish = time_horizon in {"REBUILD", "RE_TOOL"}

    prospect_ids, throwin_ids = _split_young_candidates(
        buyer_out,
        config=ctx.config,
        receiver_team_id=ctx.seller_id,
        banned_players=ctx.banned_players,
        banned_receivers_by_player=ctx.banned_receivers_by_player,
        must_be_aggregation_friendly=True,
    )

    young_id: Optional[str] = None
    source_tag: Optional[str] = None
    if rebuildish:
        pool = prospect_ids if prospect_ids else throwin_ids
        if pool:
            max_prospect = int(getattr(ctx.config, "young_prospect_max_candidates", 6) or 6)
            top_n = max(2, min(max_prospect, len(pool)))
            bucket = list(pool[:top_n])
            ctx.rng.shuffle(bucket)
            young_id = bucket[0]
            source_tag = "young_source:prospect" if prospect_ids else "young_source:throwin"
    else:
        pool = throwin_ids
        if pool:
            young_id = _pick_from_id_pool_for_need(
                buyer_out,
                pool_ids=pool,
                receiver_team_id=ctx.seller_id,
                target_salary_m=float(ctx.sale_asset.salary_m),
                need_map=seller_need_map,
                rng=ctx.rng,
                banned_players=ctx.banned_players,
                banned_receivers_by_player=ctx.banned_receivers_by_player,
                must_be_aggregation_friendly=True,
                top_scan=6,
            )
            source_tag = "young_source:throwin"
    if not young_id:
        return []

    deal = _clone_deal(_base_sell_deal(ctx.buyer_id, ctx.seller_id, ctx.sale_asset.player_id))
    deal.legs[str(ctx.buyer_id).upper()].append(PlayerAsset(kind="player", player_id=young_id))
    _add_pick_package(
        deal,
        from_team=ctx.buyer_id,
        out_cat=buyer_out,
        catalog=ctx.catalog,
        config=ctx.config,
        rng=ctx.rng,
        prefer=("SECOND",),
        max_picks=1,
        banned_asset_keys=ctx.banned_asset_keys,
    )
    tags = [f"match:{ctx.match_tag}", "pkg:young+pick"]
    if source_tag:
        tags.append(source_tag)
    c_ret = buyer_out.players.get(str(young_id))
    if c_ret is not None:
        rt = _best_need_tag(seller_need_map, c_ret)
        if rt:
            tags.append(f"return_need:{rt}")
    return [DealCandidate(
        deal=deal,
        buyer_id=ctx.buyer_id,
        seller_id=ctx.seller_id,
        focal_player_id=ctx.sale_asset.player_id,
        archetype="buyer_young_plus_pick",
        skeleton_id="compat.buyer_young_plus_pick",
        skeleton_domain="compat",
        compat_archetype="buyer_young_plus_pick",
        tags=_with_core_tags(tags, mode="SELL", focal_player_id=ctx.sale_asset.player_id, archetype="buyer_young_plus_pick", skeleton_id="compat.buyer_young_plus_pick"),
    )]


def build_sell_buyer_p4p(ctx: BuildContext) -> List[DealCandidate]:
    if ctx.sale_asset is None:
        return []
    if str(getattr(ctx.tick_ctx.get_team_situation(ctx.seller_id), "time_horizon", "RE_TOOL") or "RE_TOOL") not in {"WIN_NOW", "RE_TOOL"}:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None:
        return []
    seller_need_map = _get_need_map(ctx.tick_ctx, ctx.seller_id)
    filler_id = _pick_return_player_salaryish_with_need(
        buyer_out,
        receiver_team_id=ctx.seller_id,
        target_salary_m=float(ctx.sale_asset.salary_m),
        need_map=seller_need_map,
        rng=ctx.rng,
        banned_players=ctx.banned_players,
        banned_receivers_by_player=ctx.banned_receivers_by_player,
        must_be_aggregation_friendly=False,
    )
    if not filler_id:
        return []
    deal = _clone_deal(_base_sell_deal(ctx.buyer_id, ctx.seller_id, ctx.sale_asset.player_id))
    deal.legs[str(ctx.buyer_id).upper()].append(PlayerAsset(kind="player", player_id=filler_id))
    tags = [f"match:{ctx.match_tag}", "pkg:player_for_player"]
    c_ret = buyer_out.players.get(str(filler_id))
    if c_ret is not None:
        rt = _best_need_tag(seller_need_map, c_ret)
        if rt:
            tags.append(f"return_need:{rt}")
    return [DealCandidate(
        deal=deal,
        buyer_id=ctx.buyer_id,
        seller_id=ctx.seller_id,
        focal_player_id=ctx.sale_asset.player_id,
        archetype="buyer_p4p",
        skeleton_id="compat.buyer_p4p",
        skeleton_domain="compat",
        compat_archetype="buyer_p4p",
        tags=_with_core_tags(tags, mode="SELL", focal_player_id=ctx.sale_asset.player_id, archetype="buyer_p4p", skeleton_id="compat.buyer_p4p"),
    )]


def build_sell_buyer_consolidate(ctx: BuildContext) -> List[DealCandidate]:
    if ctx.sale_asset is None:
        return []
    buyer_out = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if buyer_out is None:
        return []
    seller_need_map = _get_need_map(ctx.tick_ctx, ctx.seller_id)
    cons_id = _pick_bucket_player_for_need(
        buyer_out,
        bucket="CONSOLIDATE",
        receiver_team_id=ctx.seller_id,
        banned_players=ctx.banned_players,
        banned_receivers_by_player=ctx.banned_receivers_by_player,
        must_be_aggregation_friendly=True,
        need_map=seller_need_map,
    )
    cheap_id = _pick_bucket_player_for_need(
        buyer_out,
        bucket="FILLER_CHEAP",
        receiver_team_id=ctx.seller_id,
        banned_players=ctx.banned_players,
        banned_receivers_by_player=ctx.banned_receivers_by_player,
        must_be_aggregation_friendly=True,
        need_map=seller_need_map,
    )
    if not cons_id or not cheap_id or cons_id == cheap_id:
        return []

    deal = _clone_deal(_base_sell_deal(ctx.buyer_id, ctx.seller_id, ctx.sale_asset.player_id))
    deal.legs[str(ctx.buyer_id).upper()].extend([PlayerAsset(kind="player", player_id=cons_id), PlayerAsset(kind="player", player_id=cheap_id)])
    _add_pick_package(
        deal,
        from_team=ctx.buyer_id,
        out_cat=buyer_out,
        catalog=ctx.catalog,
        config=ctx.config,
        rng=ctx.rng,
        prefer=("SECOND",),
        max_picks=1,
        banned_asset_keys=ctx.banned_asset_keys,
    )
    tags = [f"match:{ctx.match_tag}", "pkg:consolidate"]
    rt_seen: Set[str] = set()
    for _pid in (cons_id, cheap_id):
        c_ret = buyer_out.players.get(str(_pid))
        if c_ret is None:
            continue
        rt = _best_need_tag(seller_need_map, c_ret)
        if rt and rt not in rt_seen:
            rt_seen.add(rt)
            tags.append(f"return_need:{rt}")
    return [DealCandidate(
        deal=deal,
        buyer_id=ctx.buyer_id,
        seller_id=ctx.seller_id,
        focal_player_id=ctx.sale_asset.player_id,
        archetype="buyer_consolidate",
        skeleton_id="compat.buyer_consolidate",
        skeleton_domain="compat",
        compat_archetype="buyer_consolidate",
        tags=_with_core_tags(tags, mode="SELL", focal_player_id=ctx.sale_asset.player_id, archetype="buyer_consolidate", skeleton_id="compat.buyer_consolidate"),
    )]
