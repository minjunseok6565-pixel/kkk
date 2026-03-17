from __future__ import annotations

import random
from typing import Dict, List, Optional, Set, Tuple

from ...models import Deal, PlayerAsset, asset_key

from ..generation_tick import TradeGenerationTickContext
from ..asset_catalog import TradeAssetCatalog, TeamOutgoingCatalog, BucketId

from .types import DealGeneratorConfig, DealGeneratorBudget, TargetCandidate, DealCandidate, SellAssetCandidate
from .skeleton_registry import BuildContext, build_default_registry
from .skeleton_modifiers import apply_modifiers
from .utils import (
    _add_pick_package,
    _shape_ok,
    classify_target_profile,
)


# =============================================================================
# Offer skeletons
# =============================================================================


def _with_core_tags(tags: List[str], *, mode: str, focal_player_id: str, archetype: str) -> List[str]:
    """(D) 후속 디버깅/분석을 위해 일관된 핵심 태그를 보장한다."""

    out = list(tags)
    for t in (f"mode:{str(mode).upper()}", f"focal:{focal_player_id}", f"arch:{archetype}"):
        if t not in out:
            out.append(t)
    return out


def _attach_v3_meta(candidate: DealCandidate, *, spec: object, target_tier: str, contract_tag: str) -> None:
    skeleton_id = str(getattr(spec, "skeleton_id", "") or "")
    domain = str(getattr(spec, "domain", "") or "")
    compat = str(getattr(spec, "compat_archetype", "") or candidate.archetype)

    if skeleton_id:
        candidate.skeleton_id = skeleton_id
    if domain:
        candidate.skeleton_domain = domain
    candidate.compat_archetype = compat
    candidate.target_tier = str(target_tier).upper()
    candidate.contract_tag = str(contract_tag).upper()
    candidate.archetype = compat

    if not isinstance(candidate.tags, list):
        candidate.tags = list(candidate.tags or [])

    for t in (
        f"skeleton:{candidate.skeleton_id}",
        f"domain:{candidate.skeleton_domain}",
        f"target_tier:{candidate.target_tier}",
        f"contract_tag:{candidate.contract_tag}",
        f"arch_compat:{candidate.compat_archetype}",
        f"arch:{candidate.archetype}",
    ):
        if t not in candidate.tags:
            candidate.tags.append(t)


def build_offer_skeletons_buy(
    buyer_id: str,
    seller_id: str,
    target: TargetCandidate,
    tick_ctx: TradeGenerationTickContext,
    catalog: TradeAssetCatalog,
    *,
    config: DealGeneratorConfig,
    budget: DealGeneratorBudget,
    rng: random.Random,
    banned_asset_keys: Set[str],
    banned_players: Set[str],
    banned_receivers_by_player: Optional[Dict[str, Set[str]]] = None,
) -> List[DealCandidate]:
    """BUY 모드: target 1명 기준 2~4개 archetype 스켈레톤."""
    registry = build_default_registry()
    target_profile = classify_target_profile(target=target, config=config)
    target_tier = str(target_profile.get("tier") or "STARTER")
    contract_tag = str(target_profile.get("contract_tag") or "FAIR")
    ctx = BuildContext(
        mode="BUY",
        buyer_id=buyer_id,
        seller_id=seller_id,
        target=target,
        tick_ctx=tick_ctx,
        catalog=catalog,
        config=config,
        budget=budget,
        rng=rng,
        banned_asset_keys=banned_asset_keys,
        banned_players=banned_players,
        banned_receivers_by_player=banned_receivers_by_player,
    )
    out_v3: List[DealCandidate] = []
    for spec in registry.get_specs_for_mode_and_tier("BUY", target_tier, config, ctx=ctx, contract_tag=contract_tag):
        built = spec.build_fn(ctx)
        for cand in built:
            _attach_v3_meta(cand, spec=spec, target_tier=target_tier, contract_tag=contract_tag)
        out_v3.extend(built)
    out_v3 = apply_modifiers(
        out_v3,
        catalog=catalog,
        config=config,
        banned_asset_keys=banned_asset_keys,
        max_variants_per_candidate=int(getattr(config, "modifier_max_variants_per_candidate", 3) or 3),
    )

    trimmed_v3: List[DealCandidate] = []
    for c in out_v3:
        if not c.compat_archetype:
            c.compat_archetype = c.archetype
        if not c.target_tier:
            c.target_tier = str(target_tier).upper()
        if not c.contract_tag:
            c.contract_tag = str(contract_tag).upper()
        if _shape_ok(c.deal, config=config, catalog=catalog):
            trimmed_v3.append(c)
    return trimmed_v3[: max(2, int(budget.beam_width))]


def build_offer_skeletons_sell(
    *,
    seller_id: str,
    buyer_id: str,
    sale_asset: SellAssetCandidate,
    match_tag: str,
    tick_ctx: TradeGenerationTickContext,
    catalog: TradeAssetCatalog,
    config: DealGeneratorConfig,
    budget: DealGeneratorBudget,
    rng: random.Random,
    banned_asset_keys: Set[str],
    banned_players: Set[str],
    banned_receivers_by_player: Optional[Dict[str, Set[str]]] = None,
) -> List[DealCandidate]:
    """SELL 모드: (seller sends sale_asset.player_id) 기준 BUYER 패키지를 생성."""
    registry = build_default_registry()
    target_profile = classify_target_profile(sale_asset=sale_asset, match_tag=match_tag, config=config)
    target_tier = str(target_profile.get("tier") or "STARTER")
    contract_tag = str(target_profile.get("contract_tag") or "FAIR")
    ctx = BuildContext(
        mode="SELL",
        buyer_id=buyer_id,
        seller_id=seller_id,
        sale_asset=sale_asset,
        match_tag=match_tag,
        tick_ctx=tick_ctx,
        catalog=catalog,
        config=config,
        budget=budget,
        rng=rng,
        banned_asset_keys=banned_asset_keys,
        banned_players=banned_players,
        banned_receivers_by_player=banned_receivers_by_player,
    )
    out_v3: List[DealCandidate] = []
    for spec in registry.get_specs_for_mode_and_tier("SELL", target_tier, config, ctx=ctx, contract_tag=contract_tag):
        built = spec.build_fn(ctx)
        for cand in built:
            _attach_v3_meta(cand, spec=spec, target_tier=target_tier, contract_tag=contract_tag)
        out_v3.extend(built)
    out_v3 = apply_modifiers(
        out_v3,
        catalog=catalog,
        config=config,
        banned_asset_keys=banned_asset_keys,
        max_variants_per_candidate=int(getattr(config, "modifier_max_variants_per_candidate", 3) or 3),
    )

    trimmed_v3: List[DealCandidate] = []
    for c in out_v3:
        if not c.compat_archetype:
            c.compat_archetype = c.archetype
        if not c.target_tier:
            c.target_tier = str(target_tier).upper()
        if not c.contract_tag:
            c.contract_tag = str(contract_tag).upper()
        if _shape_ok(c.deal, config=config, catalog=catalog):
            trimmed_v3.append(c)
    return trimmed_v3[: max(2, int(budget.beam_width))]




# =============================================================================
# Candidate variant expansion (light beam)
# =============================================================================


def expand_variants(
    buyer_id: str,
    seller_id: str,
    target: TargetCandidate,
    base_candidates: List[DealCandidate],
    tick_ctx: TradeGenerationTickContext,
    catalog: TradeAssetCatalog,
    *,
    config: DealGeneratorConfig,
    budget: DealGeneratorBudget,
    rng: random.Random,
    banned_asset_keys: Set[str],
    banned_players: Set[str],
    banned_receivers_by_player: Optional[Dict[str, Set[str]]] = None,
) -> List[DealCandidate]:
    """Registry 기반 후보를 archetype-정합 변형으로 얕게 확장한다."""

    goal = min(12, max(6, int(budget.beam_width)))
    if not base_candidates:
        return []

    buyer = str(buyer_id).upper()
    seller = str(seller_id).upper()
    buyer_out = catalog.outgoing_by_team.get(buyer)
    if buyer_out is None:
        return list(base_candidates)

    out: List[DealCandidate] = list(base_candidates)
    hard_cap = max(goal, min(24, goal * 2))
    seen_sigs: Set[Tuple[str, ...]] = set()

    def _deal_sig(deal: Deal) -> Tuple[str, ...]:
        return tuple(sorted(asset_key(a) for a in (deal.legs.get(buyer, []) or [])))

    for c in base_candidates:
        seen_sigs.add(_deal_sig(c.deal))

    def _is_sendable_player(pid: str) -> bool:
        if pid in banned_players:
            return False
        cand = buyer_out.players.get(str(pid))
        if cand is None:
            return False
        if seller in set(getattr(cand, "return_ban_teams", ()) or ()):  # return-to-trading-team ban
            return False
        if banned_receivers_by_player is not None:
            if seller in banned_receivers_by_player.get(str(pid), set()):
                return False
        return True

    def _copy_candidate(base: DealCandidate, deal: Deal, extra_tags: List[str]) -> None:
        if len(out) >= hard_cap:
            return
        if not _shape_ok(deal, config=config, catalog=catalog):
            return
        sig = _deal_sig(deal)
        if sig in seen_sigs:
            return
        seen_sigs.add(sig)
        tags = list(getattr(base, "tags", []) or [])
        for t in extra_tags:
            if t not in tags:
                tags.append(t)
        out.append(
            DealCandidate(
                deal=deal,
                buyer_id=base.buyer_id,
                seller_id=base.seller_id,
                focal_player_id=base.focal_player_id,
                archetype=base.archetype,
                skeleton_id=base.skeleton_id,
                skeleton_domain=base.skeleton_domain,
                target_tier=base.target_tier,
                contract_tag=base.contract_tag,
                compat_archetype=base.compat_archetype,
                modifier_trace=list(getattr(base, "modifier_trace", []) or []),
                tags=tags,
            )
        )

    def _clone_deal(deal: Deal) -> Deal:
        return Deal(teams=list(deal.teams), legs={k: list(v) for k, v in (deal.legs or {}).items()}, meta=dict(deal.meta or {}))

    def _player_ids_in_deal(deal: Deal) -> Set[str]:
        ids: Set[str] = set()
        for a in deal.legs.get(buyer, []) or []:
            if isinstance(a, PlayerAsset):
                ids.add(str(a.player_id))
        return ids

    def _pick_player_candidates(*, descending_market: bool) -> List[str]:
        scored: List[Tuple[float, str]] = []
        for pid, cand in (buyer_out.players or {}).items():
            pid_u = str(pid)
            if not _is_sendable_player(pid_u):
                continue
            m = float(getattr(getattr(cand, "market", None), "total", 0.0) or 0.0)
            scored.append((m, pid_u))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=bool(descending_market))
        return [pid for _, pid in scored]

    top_market_players = _pick_player_candidates(descending_market=True)
    salaryish_players = _pick_player_candidates(descending_market=False)

    for base in base_candidates:
        if len(out) >= hard_cap:
            break

        style = str(getattr(base, "compat_archetype", "") or getattr(base, "archetype", "") or "mixed").lower()
        deal_base = base.deal

        # pick-heavy / mixed: add one extra pick token (prefer tier-score style)
        if style in {"pick_heavy", "mixed"}:
            d = _clone_deal(deal_base)
            before = len(d.legs.get(buyer, []) or [])
            _add_pick_package(
                d,
                from_team=buyer,
                out_cat=buyer_out,
                catalog=catalog,
                config=config,
                rng=rng,
                prefer=("FIRST_SAFE", "FIRST_SENSITIVE", "SECOND") if style == "pick_heavy" else ("FIRST_SAFE", "SECOND"),
                max_picks=1,
                banned_asset_keys=banned_asset_keys,
            )
            after = len(d.legs.get(buyer, []) or [])
            if after > before:
                _copy_candidate(base, d, [f"variant:{style}:pick_plus"])

        # player-heavy / mixed: add one player or upgrade a player slot
        if style in {"player_heavy", "mixed"}:
            deal_players = _player_ids_in_deal(deal_base)
            pool = top_market_players if style == "player_heavy" else salaryish_players

            add_pid: Optional[str] = None
            for pid in pool:
                if pid in deal_players:
                    continue
                add_pid = pid
                break

            if add_pid is not None:
                d = _clone_deal(deal_base)
                d.legs.setdefault(buyer, []).append(PlayerAsset(kind="player", player_id=add_pid))
                _copy_candidate(base, d, [f"variant:{style}:player_plus"])

            existing_players = [a for a in (deal_base.legs.get(buyer, []) or []) if isinstance(a, PlayerAsset)]
            if existing_players:
                replace_from = str(existing_players[-1].player_id)
                replace_to: Optional[str] = None
                for pid in pool:
                    if pid == replace_from or pid in deal_players:
                        continue
                    replace_to = pid
                    break
                if replace_to is not None:
                    d = _clone_deal(deal_base)
                    new_leg = []
                    replaced = False
                    for a in d.legs.get(buyer, []) or []:
                        if (not replaced) and isinstance(a, PlayerAsset) and str(a.player_id) == replace_from:
                            new_leg.append(PlayerAsset(kind="player", player_id=replace_to))
                            replaced = True
                        else:
                            new_leg.append(a)
                    d.legs[buyer] = new_leg
                    _copy_candidate(base, d, [f"variant:{style}:player_swap"])

        # timeline/other domains: conservative sweetener-only diversification
        if style not in {"pick_heavy", "player_heavy", "mixed"}:
            d = _clone_deal(deal_base)
            before = len(d.legs.get(buyer, []) or [])
            _add_pick_package(
                d,
                from_team=buyer,
                out_cat=buyer_out,
                catalog=catalog,
                config=config,
                rng=rng,
                prefer=("SECOND",),
                max_picks=1,
                banned_asset_keys=banned_asset_keys,
            )
            after = len(d.legs.get(buyer, []) or [])
            if after > before:
                _copy_candidate(base, d, ["variant:timeline:pick_plus"])

    if len(out) > hard_cap:
        out = out[:hard_cap]
    return out


# =============================================================================
# Validate + Repair
# =============================================================================
