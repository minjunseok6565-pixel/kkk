from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Dict, List, Optional, Sequence, Set, Tuple

from ..asset_catalog import PickTradeCandidate, PlayerTradeCandidate, SwapTradeCandidate, TeamOutgoingCatalog
from ...models import Deal, PickAsset, PlayerAsset, SwapAsset, asset_key
from .skeleton_registry import BuildContext
from .skeleton_score_ssot import SCORE_TOLERANCE, TIER_POINTS, asset_points_for_pick, is_score_satisfied, target_required_score
from .template_specs import PackageTemplate, TemplateSlot, get_templates_for_tier
from .types import DealCandidate
from .utils import _clone_deal, _shape_ok, classify_target_profile


TIER_ORDER: Tuple[str, ...] = (
    "MVP",
    "ALL_NBA",
    "ALL_STAR",
    "HIGH_STARTER",
    "STARTER",
    "HIGH_ROTATION",
    "ROTATION",
    "GARBAGE",
)
TIER_RANK: Dict[str, int] = {name: idx for idx, name in enumerate(TIER_ORDER)}


@dataclass(frozen=True, slots=True)
class _TemplateBuildResult:
    assets: Tuple[object, ...]
    score: float
    fail_reason: str = ""


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


def _player_tier(cand: PlayerTradeCandidate, ctx: BuildContext) -> str:
    probe = SimpleNamespace(
        ovr=getattr(getattr(cand, "snap", None), "ovr", None),
        contract_gap_cap_share=0.0,
    )
    tier = str(classify_target_profile(target=probe, config=ctx.config).get("tier") or "GARBAGE").upper()
    return tier if tier in TIER_RANK else "GARBAGE"


def _tier_meets_bounds(candidate_tier: str, *, min_tier: str = "", max_tier: str = "") -> bool:
    ct = str(candidate_tier or "GARBAGE").upper()
    if ct not in TIER_RANK:
        ct = "GARBAGE"
    c_rank = TIER_RANK[ct]

    min_t = str(min_tier or "").upper().strip()
    max_t = str(max_tier or "").upper().strip()

    if min_t in TIER_RANK and c_rank > TIER_RANK[min_t]:
        return False
    if max_t in TIER_RANK and c_rank < TIER_RANK[max_t]:
        return False
    return True


def _player_slot_ok(cand: PlayerTradeCandidate, slot: TemplateSlot, ctx: BuildContext) -> bool:
    if not _is_sendable_player(ctx, cand):
        return False

    constraints = dict(slot.constraints or {})
    tier = _player_tier(cand, ctx)
    if not _tier_meets_bounds(tier, min_tier=str(constraints.get("min_tier") or ""), max_tier=str(constraints.get("max_tier") or "")):
        return False

    try:
        salary_m = float(getattr(cand, "salary_m", 0.0) or 0.0)
    except Exception:
        salary_m = 0.0
    max_salary_m = constraints.get("max_salary_m")
    if max_salary_m is not None:
        try:
            if salary_m > float(max_salary_m):
                return False
        except Exception:
            pass

    min_control_years = constraints.get("min_control_years")
    if min_control_years is not None:
        try:
            if float(getattr(cand, "remaining_years", 0.0) or 0.0) < float(min_control_years):
                return False
        except Exception:
            return False

    return True


def _pick_slot_ok(cand: PickTradeCandidate, slot: TemplateSlot, used_asset_keys: Set[str], ctx: BuildContext) -> bool:
    constraints = dict(slot.constraints or {})
    ak = f"pick:{cand.pick_id}"
    if ak in used_asset_keys or ak in ctx.banned_asset_keys:
        return False

    wanted_round = constraints.get("round")
    if wanted_round is not None:
        try:
            if int(getattr(cand.snap, "round", 0) or 0) != int(wanted_round):
                return False
        except Exception:
            return False

    protection_allowed = constraints.get("protection_allowed")
    if protection_allowed is False:
        if getattr(cand.snap, "protection", None):
            return False

    return True


def _swap_slot_ok(cand: SwapTradeCandidate, slot: TemplateSlot, used_asset_keys: Set[str], ctx: BuildContext) -> bool:
    constraints = dict(slot.constraints or {})
    ak = f"swap:{cand.swap_id}"
    if ak in used_asset_keys or ak in ctx.banned_asset_keys:
        return False

    y = getattr(cand.snap, "year", None)
    y_min = constraints.get("year_window_min")
    y_max = constraints.get("year_window_max")

    if y_min is not None and y is not None:
        try:
            if int(y) < int(y_min):
                return False
        except Exception:
            return False
    if y_max is not None and y is not None:
        try:
            if int(y) > int(y_max):
                return False
        except Exception:
            return False

    return True


def _iter_pick_candidates(out_cat: TeamOutgoingCatalog, slot: TemplateSlot) -> List[PickTradeCandidate]:
    constraints = dict(slot.constraints or {})
    raw_pref = constraints.get("bucket_prefer")
    prefer: List[str] = []
    if isinstance(raw_pref, str) and raw_pref.strip():
        prefer = [raw_pref.strip().upper()]
    elif isinstance(raw_pref, Sequence):
        prefer = [str(x).upper() for x in raw_pref if str(x).strip()]

    ordered_ids: List[str] = []
    if prefer:
        for bucket in prefer:
            ordered_ids.extend(list(out_cat.pick_ids_by_bucket.get(bucket, tuple()) or tuple()))

    if not ordered_ids:
        for bucket in ("FIRST_SAFE", "FIRST_SENSITIVE", "SECOND"):
            ordered_ids.extend(list(out_cat.pick_ids_by_bucket.get(bucket, tuple()) or tuple()))

    seen: Set[str] = set()
    out: List[PickTradeCandidate] = []
    for pid in ordered_ids:
        p = out_cat.picks.get(str(pid))
        if p is None:
            continue
        key = str(p.pick_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _select_asset_for_slot(
    *,
    slot: TemplateSlot,
    ctx: BuildContext,
    out_cat: TeamOutgoingCatalog,
    used_players: Set[str],
    used_asset_keys: Set[str],
) -> Optional[object]:
    if slot.asset_type == "PLAYER":
        player_rows: List[Tuple[PlayerTradeCandidate, float, str]] = []
        for pid, cand in (out_cat.players or {}).items():
            if str(pid) in used_players:
                continue
            if not _player_slot_ok(cand, slot, ctx):
                continue
            tier = _player_tier(cand, ctx)
            pts = float(TIER_POINTS.get(tier, 0.0))
            player_rows.append((cand, pts, tier))

        player_rows.sort(key=lambda r: (r[1], str(r[0].player_id)), reverse=True)
        if not player_rows:
            return None
        chosen = player_rows[0][0]
        used_players.add(str(chosen.player_id))
        used_asset_keys.add(f"player:{chosen.player_id}")
        return PlayerAsset(kind="player", player_id=str(chosen.player_id))

    if slot.asset_type == "PICK":
        for pick_cand in _iter_pick_candidates(out_cat, slot):
            if not _pick_slot_ok(pick_cand, slot, used_asset_keys, ctx):
                continue
            used_asset_keys.add(f"pick:{pick_cand.pick_id}")
            return PickAsset(
                kind="pick",
                pick_id=str(pick_cand.pick_id),
                protection=getattr(pick_cand.snap, "protection", None),
            )
        return None

    if slot.asset_type == "SWAP":
        ordered_swaps = sorted(
            [s for s in (out_cat.swaps or {}).values()],
            key=lambda s: (int(getattr(s.snap, "year", 0) or 0), str(s.swap_id)),
        )
        for swap_cand in ordered_swaps:
            if not _swap_slot_ok(swap_cand, slot, used_asset_keys, ctx):
                continue
            used_asset_keys.add(f"swap:{swap_cand.swap_id}")
            return SwapAsset(
                kind="swap",
                swap_id=str(swap_cand.swap_id),
                pick_id_a=str(getattr(swap_cand.snap, "pick_id_a")),
                pick_id_b=str(getattr(swap_cand.snap, "pick_id_b")),
            )
        return None

    return None


def _asset_points(asset: object, *, out_cat: TeamOutgoingCatalog, ctx: BuildContext) -> float:
    if isinstance(asset, PlayerAsset):
        c = out_cat.players.get(str(asset.player_id))
        if c is None:
            return 0.0
        tier = _player_tier(c, ctx)
        return float(TIER_POINTS.get(tier, 0.0))
    if isinstance(asset, PickAsset):
        p = out_cat.picks.get(str(asset.pick_id))
        if p is None:
            return 0.0
        return float(asset_points_for_pick(int(getattr(p.snap, "round", 0) or 0)))
    # swaps currently score-neutral in tier_score SSOT
    return 0.0


def _build_from_template(
    *,
    template: PackageTemplate,
    ctx: BuildContext,
    out_cat: TeamOutgoingCatalog,
    required_score: float,
) -> _TemplateBuildResult:
    used_players: Set[str] = set()
    used_asset_keys: Set[str] = set()
    assets: List[object] = []

    max_players = int(getattr(ctx.config, "max_players_per_side", 4) or 4)
    max_assets = min(
        int(getattr(ctx.config, "max_assets_per_side", 9) or 9),
        max(1, int(getattr(template, "max_assets_from_buyer", 6) or 6)),
    )

    for slot in list(template.slots or tuple()):
        if len(assets) >= max_assets:
            if bool(getattr(slot, "required", True)):
                return _TemplateBuildResult(assets=tuple(assets), score=0.0, fail_reason="shape_gate")
            continue

        if slot.asset_type == "PLAYER" and len(used_players) >= max_players:
            if bool(getattr(slot, "required", True)):
                return _TemplateBuildResult(assets=tuple(assets), score=0.0, fail_reason="shape_gate")
            continue

        selected = _select_asset_for_slot(
            slot=slot,
            ctx=ctx,
            out_cat=out_cat,
            used_players=used_players,
            used_asset_keys=used_asset_keys,
        )
        if selected is None:
            if bool(getattr(slot, "required", True)):
                fail = "no_match"
                if slot.asset_type == "PLAYER":
                    fail = "no_player_match"
                elif slot.asset_type == "PICK":
                    fail = "no_pick_match"
                elif slot.asset_type == "SWAP":
                    fail = "no_swap_match"
                return _TemplateBuildResult(assets=tuple(assets), score=0.0, fail_reason=fail)
            continue

        assets.append(selected)

    score = sum(_asset_points(a, out_cat=out_cat, ctx=ctx) for a in assets)
    ratio = float(getattr(template, "min_score_ratio", 1.0) or 1.0)
    ratio = max(0.0, ratio)
    required = max(0.0, float(required_score) * ratio)
    if not is_score_satisfied(score, required, SCORE_TOLERANCE):
        return _TemplateBuildResult(assets=tuple(assets), score=float(score), fail_reason="score_gate")

    return _TemplateBuildResult(assets=tuple(assets), score=float(score), fail_reason="")


def build_template_first_skeletons(
    ctx: BuildContext,
    *,
    tier: str,
    skeleton_id_prefix: str,
    max_candidates: int,
) -> List[DealCandidate]:
    """Build template-first skeletons for the current target/sale focal.

    This builder is intentionally standalone and does not require registry wiring.
    Upstream integration can call this directly, or register wrappers per tier.
    """

    focal = _focal(ctx)
    if not focal:
        return []

    out_cat = ctx.catalog.outgoing_by_team.get(str(ctx.buyer_id).upper())
    if out_cat is None:
        return []

    tier_u = _target_profile(ctx, tier)
    required = target_required_score(tier_u)

    templates = get_templates_for_tier(tier_u)
    if not templates:
        return []

    base = _base_deal(ctx, focal)
    out: List[DealCandidate] = []
    seen: Set[Tuple[str, ...]] = set()

    for template in templates:
        res = _build_from_template(template=template, ctx=ctx, out_cat=out_cat, required_score=required)
        if res.fail_reason:
            continue

        deal = _clone_deal(base)
        buyer_leg = deal.legs.get(str(ctx.buyer_id).upper(), [])
        buyer_leg.extend(list(res.assets))
        deal.legs[str(ctx.buyer_id).upper()] = buyer_leg

        if not _shape_ok(deal, config=ctx.config, catalog=ctx.catalog):
            continue

        sig = tuple(sorted(asset_key(a) for a in deal.legs.get(str(ctx.buyer_id).upper(), []) or []))
        if sig in seen:
            continue
        seen.add(sig)

        template_id = str(getattr(template, "template_id", "") or "")
        skeleton_id = f"{skeleton_id_prefix}.{template_id}" if skeleton_id_prefix else template_id

        out.append(
            DealCandidate(
                deal=deal,
                buyer_id=ctx.buyer_id,
                seller_id=ctx.seller_id,
                focal_player_id=focal,
                archetype="template_first",
                skeleton_id=skeleton_id,
                skeleton_domain="template",
                compat_archetype="template_first",
                target_tier=tier_u,
                tags=[
                    f"skeleton:{skeleton_id}",
                    f"template_id:{template_id}",
                    f"target_tier:{tier_u}",
                    "template:first",
                    "template_stage:primary",
                    "template_result:built",
                ],
            )
        )

        if len(out) >= max(1, int(max_candidates)):
            break

    return out


__all__ = ["build_template_first_skeletons"]
