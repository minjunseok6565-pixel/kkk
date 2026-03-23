from __future__ import annotations

from types import SimpleNamespace
from typing import Dict, List, Optional, Sequence, Set, Tuple

from ..asset_catalog import PlayerTradeCandidate, TeamOutgoingCatalog
from ...models import Deal, PickAsset, PlayerAsset, asset_key
from .skeleton_registry import BuildContext
from .skeleton_score_ssot import (
    SCORE_TOLERANCE,
    TIER_POINTS,
    asset_points_for_pick,
    is_score_satisfied,
    target_required_score,
)
from .types import DealCandidate
from .utils import _add_pick_package, _clone_deal, _shape_ok, classify_target_profile

MAX_SEARCH_ATTEMPTS = 12
MAX_CANDIDATES_PER_BUILDER = 6


STYLE_PLAYER_HEAVY = "player_heavy"
STYLE_PICK_HEAVY = "pick_heavy"
STYLE_MIXED = "mixed"


def _focal(ctx: BuildContext) -> Optional[str]:
    if ctx.target is not None:
        return str(ctx.target.player_id)
    if ctx.sale_asset is not None:
        return str(ctx.sale_asset.player_id)
    return None


def _target_profile(ctx: BuildContext, fallback_tier: str) -> str:
    profile = classify_target_profile(
        target=ctx.target,
        sale_asset=ctx.sale_asset,
        match_tag=ctx.match_tag,
        config=ctx.config,
    )
    tier = str(profile.get("tier") or fallback_tier).upper()
    if tier not in TIER_POINTS:
        tier = str(fallback_tier).upper()
    return tier


def _base_deal(ctx: BuildContext, focal_player_id: str) -> Deal:
    buyer = str(ctx.buyer_id).upper()
    seller = str(ctx.seller_id).upper()
    return Deal(
        teams=[buyer, seller],
        legs={
            buyer: [],
            seller: [PlayerAsset(kind="player", player_id=focal_player_id)],
        },
    )


def _receiver_banned(ctx: BuildContext, player_id: str) -> bool:
    if ctx.banned_receivers_by_player is None:
        return False
    seller = str(ctx.seller_id).upper()
    return seller in ctx.banned_receivers_by_player.get(str(player_id), set())


def _is_sendable_player(ctx: BuildContext, cand: PlayerTradeCandidate) -> bool:
    pid = str(cand.player_id)
    if pid in ctx.banned_players:
        return False
    if _receiver_banned(ctx, pid):
        return False
    seller = str(ctx.seller_id).upper()
    if seller in set(cand.return_ban_teams or ()):  # return-to-trading-team ban
        return False
    return True


def _player_points(cand: PlayerTradeCandidate, ctx: BuildContext) -> float:
    probe = SimpleNamespace(
        ovr=getattr(getattr(cand, "snap", None), "ovr", None),
        contract_gap_cap_share=0.0,
    )
    tier = str(classify_target_profile(target=probe, config=ctx.config).get("tier") or "GARBAGE").upper()
    return float(TIER_POINTS.get(tier, 0.0))


def _player_pool(ctx: BuildContext, out_cat: TeamOutgoingCatalog) -> List[Tuple[str, float]]:
    scored: List[Tuple[str, float]] = []
    for pid, cand in (out_cat.players or {}).items():
        if not _is_sendable_player(ctx, cand):
            continue
        pts = _player_points(cand, ctx)
        if pts <= 0.0:
            continue
        scored.append((str(pid), float(pts)))
    scored.sort(key=lambda x: (x[1], x[0]), reverse=True)
    return scored


def _deal_score(ctx: BuildContext, out_cat: TeamOutgoingCatalog, deal: Deal, player_pts_map: Dict[str, float]) -> float:
    buyer = str(ctx.buyer_id).upper()
    total = 0.0
    for a in deal.legs.get(buyer, []) or []:
        if isinstance(a, PlayerAsset):
            total += float(player_pts_map.get(str(a.player_id), 0.0))
        elif isinstance(a, PickAsset):
            pick = out_cat.picks.get(str(a.pick_id)) if out_cat is not None else None
            if pick is not None:
                total += asset_points_for_pick(int(getattr(getattr(pick, "snap", None), "round", 0) or 0))
    return float(total)


def _pick_pref(style: str) -> Tuple[str, ...]:
    if style == STYLE_PICK_HEAVY:
        return ("FIRST_SAFE", "FIRST_SENSITIVE", "SECOND")
    if style == STYLE_PLAYER_HEAVY:
        return ("SECOND", "FIRST_SAFE")
    return ("FIRST_SAFE", "SECOND")


def _player_share(style: str) -> float:
    if style == STYLE_PLAYER_HEAVY:
        return 0.8
    if style == STYLE_PICK_HEAVY:
        return 0.2
    return 0.5


def build_tier_style_skeleton(
    ctx: BuildContext,
    *,
    tier: str,
    style: str,
    skeleton_id: str,
    max_candidates: int = MAX_CANDIDATES_PER_BUILDER,
    max_attempts: int = MAX_SEARCH_ATTEMPTS,
) -> List[DealCandidate]:
    focal = _focal(ctx)
    if not focal:
        return []

    out_cat = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if out_cat is None:
        return []

    tier_u = _target_profile(ctx, tier)
    required = target_required_score(tier_u)
    target_player_score = float(required) * _player_share(style)

    base = _base_deal(ctx, focal)
    pool = _player_pool(ctx, out_cat)
    player_pts_map: Dict[str, float] = {pid: pts for pid, pts in pool}
    if required > 0.0 and not pool and style != STYLE_PICK_HEAVY:
        return []

    max_candidates_i = max(1, int(max_candidates))
    max_attempts_i = max(1, int(max_attempts))

    out: List[DealCandidate] = []
    seen: Set[Tuple[str, ...]] = set()

    for _ in range(max_attempts_i):
        deal = _clone_deal(base)
        used_players: Set[str] = set()

        # Phase 1: player accumulation (share target)
        for pid, pts in pool:
            if len(used_players) >= int(getattr(ctx.config, "max_players_per_side", 4) or 4):
                break
            if pid in used_players:
                continue
            cur_score = _deal_score(ctx, out_cat, deal, player_pts_map)
            if cur_score >= target_player_score:
                break
            deal.legs[str(ctx.buyer_id).upper()].append(PlayerAsset(kind="player", player_id=pid))
            used_players.add(pid)

        # Phase 2: pick accumulation to reach required
        progress = True
        pick_steps = 0
        while pick_steps < max_attempts_i:
            cur_score = _deal_score(ctx, out_cat, deal, player_pts_map)
            if is_score_satisfied(cur_score, required, SCORE_TOLERANCE):
                break
            before = len(deal.legs[str(ctx.buyer_id).upper()])
            _add_pick_package(
                deal,
                from_team=ctx.buyer_id,
                out_cat=out_cat,
                catalog=ctx.catalog,
                config=ctx.config,
                rng=ctx.rng,
                prefer=_pick_pref(style),
                max_picks=1,
                banned_asset_keys=ctx.banned_asset_keys,
            )
            after = len(deal.legs[str(ctx.buyer_id).upper()])
            if after <= before:
                progress = False
                break
            pick_steps += 1

        if not progress:
            break

        offered = _deal_score(ctx, out_cat, deal, player_pts_map)
        if not is_score_satisfied(offered, required, SCORE_TOLERANCE):
            break

        if not _shape_ok(deal, config=ctx.config, catalog=ctx.catalog):
            continue

        sig = tuple(sorted(asset_key(a) for a in deal.legs.get(str(ctx.buyer_id).upper(), []) or []))
        if sig in seen:
            break
        seen.add(sig)

        out.append(
            DealCandidate(
                deal=deal,
                buyer_id=ctx.buyer_id,
                seller_id=ctx.seller_id,
                focal_player_id=focal,
                archetype=style,
                skeleton_id=skeleton_id,
                skeleton_domain="tier_score",
                compat_archetype=style,
                target_tier=tier_u,
                tags=[
                    f"skeleton:{skeleton_id}",
                    f"target_tier:{tier_u}",
                    f"arch:{style}",
                    "score_gate:on",
                ],
            )
        )

        if len(out) >= max_candidates_i:
            break

    return out


__all__ = [
    "MAX_SEARCH_ATTEMPTS",
    "MAX_CANDIDATES_PER_BUILDER",
    "STYLE_PLAYER_HEAVY",
    "STYLE_PICK_HEAVY",
    "STYLE_MIXED",
    "build_tier_style_skeleton",
]
