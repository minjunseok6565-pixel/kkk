from __future__ import annotations

import random
from typing import List, Optional, Set

from ...models import Deal, PickAsset, normalize_pick_protection
from ..asset_catalog import TradeAssetCatalog
from .types import DealCandidate, DealGeneratorConfig
from .utils import _add_pick_package, _clone_deal


def _find_first_pick_asset_sender(deal: Deal, catalog: TradeAssetCatalog) -> tuple[Optional[str], Optional[PickAsset]]:
    for team_id, assets in (deal.legs or {}).items():
        out_cat = catalog.outgoing_by_team.get(str(team_id).upper())
        if out_cat is None:
            continue
        for a in assets:
            if isinstance(a, PickAsset):
                pc = out_cat.picks.get(str(a.pick_id))
                if pc is not None and str(getattr(pc, "bucket", "")).upper().startswith("FIRST"):
                    return str(team_id).upper(), a
    return None, None


def _replace_pick_with_protection(deal: Deal, *, sender_team: str, pick_id: str, protection: dict) -> Deal:
    d2 = _clone_deal(deal)
    sender = str(sender_team).upper()
    new_assets = []
    for a in list(d2.legs.get(sender, [])):
        if isinstance(a, PickAsset) and str(a.pick_id) == str(pick_id) and a.protection is None:
            new_assets.append(PickAsset(kind="pick", pick_id=a.pick_id, to_team=a.to_team, protection=dict(protection)))
        else:
            new_assets.append(a)
    d2.legs[sender] = new_assets
    return d2


def _top_n_protection(n: int) -> dict:
    return normalize_pick_protection(
        {
            "type": "TOP_N",
            "n": int(n),
            "compensation": {"label": f"Top-{int(n)} protection compensation", "value": 3.0},
        }
    )


def apply_modifiers(
    candidates: List[DealCandidate],
    *,
    catalog: TradeAssetCatalog,
    config: DealGeneratorConfig,
    banned_asset_keys: Set[str],
    max_variants_per_candidate: Optional[int] = None,
) -> List[DealCandidate]:
    if not bool(getattr(config, "skeleton_modifiers_enabled", True)):
        return list(candidates)

    limit = int(max_variants_per_candidate or getattr(config, "modifier_max_variants_per_candidate", 3) or 3)
    out: List[DealCandidate] = list(candidates)

    for cand in list(candidates):
        created = 0

        # 1) protection_step_up_down
        if created < limit and bool(getattr(config, "modifier_protection_enabled", True)):
            sender, first_pick = _find_first_pick_asset_sender(cand.deal, catalog)
            if sender and first_pick is not None and first_pick.protection is None:
                for label, n in (("prot_light", 10), ("prot_mid", 14), ("prot_heavy", 20)):
                    if created >= limit:
                        break
                    intent = str(label).replace("prot_", "")
                    d2 = _replace_pick_with_protection(cand.deal, sender_team=sender, pick_id=first_pick.pick_id, protection=_top_n_protection(n))
                    c2 = DealCandidate(
                        deal=d2,
                        buyer_id=cand.buyer_id,
                        seller_id=cand.seller_id,
                        focal_player_id=cand.focal_player_id,
                        archetype=cand.archetype,
                        skeleton_id=cand.skeleton_id,
                        skeleton_domain=cand.skeleton_domain,
                        target_tier=cand.target_tier,
                        compat_archetype=cand.compat_archetype,
                        modifier_trace=[*list(cand.modifier_trace or []), "protection_step_up_down", label],
                        tags=[
                            *list(cand.tags or []),
                            f"modifier:{label}",
                            f"protection_intent:{intent}",
                            f"proposal_meta:protection_intent={intent}",
                        ],
                        repairs_used=cand.repairs_used,
                    )
                    out.append(c2)
                    created += 1

        # 2) swap_substitute_for_first
        if created < limit and bool(getattr(config, "modifier_swap_substitute_enabled", True)):
            buyer_out = catalog.outgoing_by_team.get(str(cand.buyer_id).upper())
            sender, first_pick = _find_first_pick_asset_sender(cand.deal, catalog)
            if buyer_out is not None and sender == str(cand.buyer_id).upper() and first_pick is not None:
                d3 = _clone_deal(cand.deal)
                # remove one first pick asset from buyer leg
                removed = False
                kept = []
                for a in list(d3.legs.get(str(cand.buyer_id).upper(), [])):
                    if isinstance(a, PickAsset) and not removed and str(a.pick_id) == str(first_pick.pick_id):
                        removed = True
                        continue
                    kept.append(a)
                d3.legs[str(cand.buyer_id).upper()] = kept
                _add_pick_package(
                    d3,
                    from_team=cand.buyer_id,
                    out_cat=buyer_out,
                    catalog=catalog,
                    config=config,
                    rng=random.Random(0),
                    prefer=("SWAP", "SECOND"),
                    max_picks=2,
                    banned_asset_keys=banned_asset_keys,
                )
                c3 = DealCandidate(
                    deal=d3,
                    buyer_id=cand.buyer_id,
                    seller_id=cand.seller_id,
                    focal_player_id=cand.focal_player_id,
                    archetype=cand.archetype,
                    skeleton_id=cand.skeleton_id,
                    skeleton_domain=cand.skeleton_domain,
                    target_tier=cand.target_tier,
                    compat_archetype=cand.compat_archetype,
                    modifier_trace=[*list(cand.modifier_trace or []), "swap_substitute_for_first"],
                    tags=[*list(cand.tags or []), "modifier:swap_substitute_for_first"],
                    repairs_used=cand.repairs_used,
                )
                out.append(c3)
                created += 1

        # 3) second_round_rebalance
        if created < limit:
            buyer_out = catalog.outgoing_by_team.get(str(cand.buyer_id).upper())
            if buyer_out is not None:
                d4 = _clone_deal(cand.deal)
                _add_pick_package(
                    d4,
                    from_team=cand.buyer_id,
                    out_cat=buyer_out,
                    catalog=catalog,
                    config=config,
                    rng=random.Random(1),
                    prefer=("SECOND",),
                    max_picks=1,
                    banned_asset_keys=banned_asset_keys,
                )
                c4 = DealCandidate(
                    deal=d4,
                    buyer_id=cand.buyer_id,
                    seller_id=cand.seller_id,
                    focal_player_id=cand.focal_player_id,
                    archetype=cand.archetype,
                    skeleton_id=cand.skeleton_id,
                    skeleton_domain=cand.skeleton_domain,
                    target_tier=cand.target_tier,
                    compat_archetype=cand.compat_archetype,
                    modifier_trace=[*list(cand.modifier_trace or []), "second_round_rebalance"],
                    tags=[*list(cand.tags or []), "modifier:second_round_rebalance"],
                    repairs_used=cand.repairs_used,
                )
                out.append(c4)
                created += 1

    return out
