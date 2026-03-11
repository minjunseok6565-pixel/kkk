from __future__ import annotations

"""trades/counter_offer/builder.py

Counter-offer generation for the trade negotiation flow.

High-level contract
-------------------
- Input: a *valid* 2-team Deal offered by the user in a negotiation session.
- Output: a CounterProposal containing a new Deal that the *other team* would ACCEPT,
  plus explainability metadata and UI-ready message.

Design principles
-----------------
1) SSOT for legality
   - Every candidate must pass tick_ctx.validate_deal (trade rules registry).

2) SSOT for valuation
   - Use trades.valuation.service.evaluate_deal_for_team with an injected tick_ctx
     (TradeGenerationTickContext) to reuse cached team_situations / decision_contexts.

3) NBA-like behavior
   - Prefer minimal edits: remove sweeteners, tighten pick protections, add 1 pick.
   - When FIT is the blocker, try a 1-player fit swap.
   - Avoid proposing extreme overpays to the user (user_margin_floor).

4) Determinism
   - Given the same offer + date + teams (+ relationship), the counter should be stable.

NOTE ABOUT JSON SERIALIZATION
----------------------------
Deal/Asset payloads must follow trades.models.serialize_deal rules so that
trades.models.parse_deal can round-trip safely (e.g. PickAsset.protection is
omitted when None rather than {"protection": null}).

valuation.types.to_jsonable already special-cases Deal/PickAsset to enforce this.
This builder still:
- keeps CounterProposal.deal as a proper Deal object (SSOT)
- includes a JSON-ready payload in CounterProposal.meta['deal_serialized']
  (serialize_deal output) for callers that need to persist/return a deal payload
  without relying on a particular serializer.
"""

from dataclasses import dataclass, replace
from datetime import date
import hashlib
import random
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from ..errors import TradeError
from ..models import (
    Deal,
    Asset,
    PlayerAsset,
    PickAsset,
    SwapAsset,
    FixedAsset,
    asset_key,
    canonicalize_deal,
    serialize_deal,
)

from ..valuation.types import (
    CounterProposal,
    DecisionReason,
    DealDecision,
    DealVerdict,
    TeamDealEvaluation,
)

from ..generation.generation_tick import (
    TradeGenerationTickContext,
    build_trade_generation_tick_context,
)

from ..generation.dealgen.scoring import evaluate_and_score
from ..generation.dealgen.types import DealProposal, DealGeneratorStats
from ..generation.dealgen.dedupe import dedupe_hash
from ..generation.dealgen.utils import _clone_deal, _shape_ok

from ..generation.dealgen.sweetener import maybe_apply_sweeteners
from ..generation.dealgen.fit_swap import maybe_apply_fit_swap

from .config import CounterOfferConfig
from .diff import DealDiff, compute_deal_diff
from .messaging import build_counter_message


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------


def _tid(x: Any) -> str:
    return str(x or "").strip().upper()


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


def _clamp(x: float, lo: float, hi: float) -> float:
    try:
        xf = float(x)
    except Exception:
        xf = float(lo)
    if xf < float(lo):
        return float(lo)
    if xf > float(hi):
        return float(hi)
    return float(xf)


def _lerp(a: float, b: float, t: float) -> float:
    return float(a) + (float(b) - float(a)) * _clamp(float(t), 0.0, 1.0)


def _deal_total_assets(deal: Deal) -> int:
    try:
        return int(sum(len(v) for v in deal.legs.values()))
    except Exception:
        return 0


def _margin(eval_: TeamDealEvaluation, dec: DealDecision) -> float:
    try:
        return float(eval_.net_surplus) - float(dec.required_surplus)
    except Exception:
        return 0.0


def _has_reason(dec: DealDecision, code: str) -> bool:
    reasons = getattr(dec, "reasons", None) or tuple()
    for r in reasons:
        try:
            if str(getattr(r, "code", "") or "") == code:
                return True
        except Exception:
            continue
    return False


def _compute_seed(
    *,
    salt: str,
    current_date: date,
    user_team_id: str,
    other_team_id: str,
    base_hash: str,
    session_id: Optional[str],
) -> int:
    raw = f"{salt}|{current_date.isoformat()}|{_tid(user_team_id)}|{_tid(other_team_id)}|{base_hash}|{session_id or ''}"
    h = hashlib.sha256(raw.encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big", signed=False)


def _extract_relationship(session: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    if not session:
        return {}
    r = session.get("relationship")
    return r if isinstance(r, Mapping) else {}


def _decision_context(tick_ctx: TradeGenerationTickContext, team_id: str) -> Any:
    try:
        return tick_ctx.get_decision_context(_tid(team_id))
    except Exception:
        return None


def _extra_ask_value(
    *,
    cfg: CounterOfferConfig,
    tick_ctx: TradeGenerationTickContext,
    other_team_id: str,
    base_corridor: float,
    relationship: Mapping[str, Any],
) -> float:
    """How much above the acceptance threshold the AI asks for.

    Uses decision_context traits (negotiation toughness / relationship sensitivity)
    and session relationship (trust/fatigue/promises_broken).

    Output unit: same as valuation (TVU).
    """

    corridor = max(0.0, float(base_corridor))
    if corridor <= 1e-6:
        return 0.0

    base = corridor * float(cfg.extra_ask_base_frac_of_corridor)
    cap = corridor * float(cfg.extra_ask_max_frac_of_corridor)

    dc = _decision_context(tick_ctx, other_team_id)

    eff_tough = 0.5
    rel_scale = 0.0
    deadline_pressure = 0.0
    try:
        if dc is not None:
            eff = getattr(dc, "effective_traits", None)
            if eff is not None and getattr(eff, "eff_neg_tough", None) is not None:
                eff_tough = float(getattr(eff, "eff_neg_tough"))
            knobs = getattr(dc, "knobs", None)
            if knobs is not None and getattr(knobs, "relationship_scale", None) is not None:
                rel_scale = float(getattr(knobs, "relationship_scale"))
            if getattr(dc, "deadline_pressure", None) is not None:
                deadline_pressure = float(getattr(dc, "deadline_pressure"))
    except Exception:
        eff_tough = 0.5
        rel_scale = 0.0
        deadline_pressure = 0.0

    # Base toughness multiplier
    tough_mult = _lerp(float(cfg.tough_mult_lo), float(cfg.tough_mult_hi), _clamp(eff_tough, 0.0, 1.0))

    # Relationship effects (scaled by decision_context relationship_scale)
    trust = _safe_int(relationship.get("trust"), 0)
    fatigue = _safe_int(relationship.get("fatigue"), 0)
    broken = _safe_int(relationship.get("promises_broken"), 0)

    trust_norm = _clamp(trust / 10.0, -1.0, 1.0)
    fatigue_norm = _clamp(fatigue / 10.0, 0.0, 1.0)
    broken_norm = _clamp(broken / 5.0, 0.0, 1.0)

    rel_strength = _clamp(rel_scale / 1.5, 0.0, 1.0)

    mult = tough_mult

    if trust_norm > 0:
        mult *= 1.0 - float(cfg.trust_relief) * rel_strength * trust_norm
    elif trust_norm < 0:
        mult *= 1.0 + float(cfg.distrust_pressure) * rel_strength * abs(trust_norm)

    mult *= 1.0 + float(cfg.fatigue_pressure) * rel_strength * fatigue_norm
    mult *= 1.0 + float(cfg.promises_broken_pressure) * rel_strength * broken_norm

    # Deadline tends to reduce the ask a bit (teams want certainty)
    mult *= 1.0 - float(cfg.deadline_relief) * _clamp(deadline_pressure, 0.0, 1.0)

    extra = base * mult
    extra = _clamp(extra, 0.0, max(0.0, cap))
    return float(extra)


def _protected_asset_keys(
    base_prop: DealProposal,
    *,
    seller_id: str,
    top_n: int,
) -> Set[str]:
    """Protect the seller's top outgoing assets (so we don't counter by removing the 'headline' piece)."""

    n = max(0, int(top_n))
    if n <= 0:
        return set()

    out_vals = []
    try:
        for tv in (base_prop.seller_eval.side.outgoing or tuple()):
            # market_value.total is SSOT for 'headline'
            mv = getattr(getattr(tv, "market_value", None), "total", None)
            out_vals.append((float(mv or 0.0), str(getattr(tv, "asset_key", ""))))
    except Exception:
        out_vals = []

    out_vals.sort(key=lambda x: (-x[0], x[1]))
    keys = [k for _, k in out_vals if k]
    return set(keys[:n])


@dataclass(frozen=True, slots=True)
class CounterCandidate:
    strategy: str
    deal: Deal
    prop: DealProposal
    diff: DealDiff
    meta: Dict[str, Any]


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


class CounterOfferBuilder:
    """Build a high-quality counter offer using existing trade subsystems."""

    def __init__(self, config: Optional[CounterOfferConfig] = None):
        self.config = config or CounterOfferConfig()

    def build(
        self,
        *,
        offer: Deal,
        user_team_id: str,
        other_team_id: str,
        current_date: date,
        db_path: str,
        session: Optional[Mapping[str, Any]] = None,
        allow_locked_by_deal_id: Optional[str] = None,
        tick_ctx: Optional[TradeGenerationTickContext] = None,
        skip_repo_integrity_check: bool = True,
    ) -> Optional[CounterProposal]:
        """Build a counter offer.

        Returns
        -------
        CounterProposal | None
            - None if no acceptable counter could be generated.
        """

        cfg = self.config
        user = _tid(user_team_id)
        other = _tid(other_team_id)

        base_deal = canonicalize_deal(offer)

        # We only support 2-team counter offers in this module.
        if len(base_deal.teams) != 2 or set(base_deal.teams) != {user, other}:
            return None

        # Tick context (cache reuse)
        if tick_ctx is None:
            # Build lightweight tick ctx; we can opt out of repo.validate_integrity for hot paths.
            with build_trade_generation_tick_context(
                current_date=current_date,
                db_path=db_path,
                team_ids=(user, other),
                validate_integrity=not bool(skip_repo_integrity_check),
            ) as tc:
                # If we skipped validation, mark as validated to prevent validate_deal() from
                # invoking repo.validate_integrity() implicitly.
                if bool(skip_repo_integrity_check):
                    try:
                        tc.rule_tick_ctx.integrity_validated = True
                    except Exception:
                        pass

                # Re-enter with an injected tick_ctx so we reuse caches while guaranteeing
                # the underlying repo is closed when this build() finishes.
                return self.build(
                    offer=offer,
                    user_team_id=user,
                    other_team_id=other,
                    current_date=current_date,
                    db_path=db_path,
                    session=session,
                    allow_locked_by_deal_id=allow_locked_by_deal_id,
                    tick_ctx=tc,
                    skip_repo_integrity_check=skip_repo_integrity_check,
                )

        # Validate base deal legality (SSOT)
        try:
            tick_ctx.validate_deal(base_deal, allow_locked_by_deal_id=allow_locked_by_deal_id)
        except TradeError:
            return None

        dealgen_cfg = cfg.to_dealgen_config()

        # Base evaluation + score (cached path)
        base_prop, used_evals = evaluate_and_score(
            base_deal,
            buyer_id=user,
            seller_id=other,
            tick_ctx=tick_ctx,
            config=dealgen_cfg,
            tags=("counter:base",),
            opponent_repeat_count=0,
            stats=None,
        )
        if base_prop is None:
            return None

        # If seller already accepts, no need to counter.
        if base_prop.seller_decision.verdict == DealVerdict.ACCEPT:
            return None

        # Only attempt counters when seller is in COUNTER; if it's REJECT, we can still try
        # but keep budgets tight.
        seller_verdict = base_prop.seller_decision.verdict
        if seller_verdict not in (DealVerdict.COUNTER, DealVerdict.REJECT):
            return None

        relationship = _extract_relationship(session)
        session_id = None
        if session and isinstance(session.get("session_id"), str):
            session_id = str(session.get("session_id"))

        base_hash = dedupe_hash(base_deal)
        rng = random.Random(
            _compute_seed(
                salt=str(cfg.seed_salt),
                current_date=current_date,
                user_team_id=user,
                other_team_id=other,
                base_hash=base_hash,
                session_id=session_id,
            )
        )

        # Determine how much surplus the other team wants in the counter.
        base_corridor = 0.0
        try:
            base_corridor = float((base_prop.seller_decision.meta or {}).get("corridor") or 0.0)
        except Exception:
            base_corridor = 0.0
        desired_extra = _extra_ask_value(
            cfg=cfg,
            tick_ctx=tick_ctx,
            other_team_id=other,
            base_corridor=base_corridor,
            relationship=relationship,
        )

        # Protect the seller's headline outgoing asset(s) so we don't counter by removing them.
        protected_keys = _protected_asset_keys(base_prop, seller_id=other, top_n=cfg.anchor_keep_top_n)

        # Candidate collection
        candidates: List[CounterCandidate] = []

        # Budget tracking
        validations_used = 1
        evaluations_used = used_evals

        def _can_spend(v: int = 0, e: int = 0) -> bool:
            return (validations_used + int(v) <= int(cfg.max_validations)) and (evaluations_used + int(e) <= int(cfg.max_evaluations))

        def _validate_and_score(deal2: Deal, *, tag: str) -> Optional[DealProposal]:
            nonlocal validations_used, evaluations_used

            if not _can_spend(v=1, e=2):
                return None

            deal2c = canonicalize_deal(deal2)
            if not _shape_ok(deal2c, config=dealgen_cfg, catalog=getattr(tick_ctx, "asset_catalog", None)):
                return None

            try:
                tick_ctx.validate_deal(deal2c, allow_locked_by_deal_id=allow_locked_by_deal_id)
                validations_used += 1
            except TradeError:
                validations_used += 1
                return None

            prop2, evals2 = evaluate_and_score(
                deal2c,
                buyer_id=user,
                seller_id=other,
                tick_ctx=tick_ctx,
                config=dealgen_cfg,
                tags=(tag,),
                opponent_repeat_count=0,
                stats=None,
            )
            evaluations_used += int(evals2)
            return prop2

        def _add_candidate(prop2: DealProposal, *, strategy: str, meta: Optional[Dict[str, Any]] = None) -> None:
            if prop2 is None:
                return
            if len(candidates) >= int(cfg.max_candidates):
                return

            # Must keep protected keys in seller leg
            try:
                seller_leg = prop2.deal.legs.get(other, []) or []
                seller_leg_keys = {asset_key(a) for a in seller_leg}
                if protected_keys and not protected_keys.issubset(seller_leg_keys):
                    return
            except Exception:
                pass

            diff = compute_deal_diff(base_deal, prop2.deal)
            candidates.append(
                CounterCandidate(
                    strategy=str(strategy),
                    deal=prop2.deal,
                    prop=prop2,
                    diff=diff,
                    meta=dict(meta or {}),
                )
            )

        # ------------------------------------------------------------------
        # Strategy 1: FIT swap (if fit is a major blocker)
        # ------------------------------------------------------------------
        if cfg.enable_fit_swap and _has_reason(base_prop.seller_decision, "FIT_FAILS") and _can_spend(v=2, e=2):
            try:
                stats = DealGeneratorStats(mode="COUNTER_FIT_SWAP")
                budget = cfg.to_dealgen_budget(
                    max_validations=min(int(cfg.max_validations) - validations_used, 70),
                    max_evaluations=min(int(cfg.max_evaluations) - evaluations_used, 40),
                    max_repairs=int(cfg.max_repairs),
                )
                res = maybe_apply_fit_swap(
                    base_prop,
                    tick_ctx=tick_ctx,
                    catalog=getattr(tick_ctx, "asset_catalog", None),
                    config=dealgen_cfg,
                    budget=budget,
                    allow_locked_by_deal_id=allow_locked_by_deal_id,
                    banned_asset_keys=set(),
                    banned_players=set(),
                    banned_receivers_by_player={},
                    protected_player_id=None,
                    opponent_repeat_count=0,
                    rng=rng,
                    validations_remaining=int(budget.max_validations),
                    evaluations_remaining=int(budget.max_evaluations),
                    stats=stats,
                )
                if res and res.proposal is not None:
                    # budget accounting (best-effort)
                    validations_used += int(res.validations_used)
                    evaluations_used += int(res.evaluations_used)

                    _add_candidate(
                        res.proposal,
                        strategy="FIT_SWAP",
                        meta={"candidates_tried": int(getattr(res, "candidates_tried", 0) or 0), "swapped": bool(getattr(res, "swapped", False))},
                    )

                    # If still not accepted by seller, we can try sweeteners on top of fit-swap.
                    if cfg.enable_pick_sweeteners and res.proposal.seller_decision.verdict in (DealVerdict.COUNTER, DealVerdict.REJECT):
                        pass
            except Exception:
                # Fit swap is optional; ignore failures.
                pass

        # ------------------------------------------------------------------
        # Strategy 2: Remove outgoing sweeteners from OTHER leg
        # ------------------------------------------------------------------
        if cfg.enable_remove_outgoing:
            try:
                removable = _collect_removable_outgoing_assets(
                    base_prop,
                    seller_id=other,
                    protected_keys=protected_keys,
                    player_market_cap=float(cfg.remove_outgoing_player_market_cap),
                )

                # Single removals
                for a in removable[: max(0, int(cfg.remove_outgoing_max_assets))]:
                    if len(candidates) >= int(cfg.max_candidates):
                        break
                    deal2 = _clone_deal(base_deal)
                    leg = list(deal2.legs.get(other, []) or [])
                    leg2 = [x for x in leg if asset_key(x) != asset_key(a)]
                    deal2.legs[other] = leg2

                    prop2 = _validate_and_score(deal2, tag="counter:remove_outgoing")
                    if prop2 is None:
                        continue
                    _add_candidate(prop2, strategy="REMOVE_OUTGOING", meta={"removed": asset_key(a)})

                # Two-asset removals (limited combinations)
                if cfg.remove_outgoing_try_combinations and len(removable) >= 2 and int(cfg.remove_outgoing_max_assets) >= 2:
                    head = removable[: min(4, len(removable))]
                    for i in range(len(head)):
                        for j in range(i + 1, len(head)):
                            if len(candidates) >= int(cfg.max_candidates):
                                break
                            a1 = head[i]
                            a2 = head[j]
                            deal2 = _clone_deal(base_deal)
                            leg = list(deal2.legs.get(other, []) or [])
                            leg2 = [x for x in leg if asset_key(x) not in {asset_key(a1), asset_key(a2)}]
                            deal2.legs[other] = leg2

                            prop2 = _validate_and_score(deal2, tag="counter:remove_outgoing_2")
                            if prop2 is None:
                                continue
                            _add_candidate(
                                prop2,
                                strategy="REMOVE_OUTGOING",
                                meta={"removed": [asset_key(a1), asset_key(a2)]},
                            )
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Strategy 3: Reduce pick protection on USER picks (more valuable pick)
        # ------------------------------------------------------------------
        if cfg.enable_reduce_pick_protection and len(candidates) < int(cfg.max_candidates):
            try:
                for deal2, meta in _propose_reduce_pick_protection(
                    base_deal,
                    user_id=user,
                    cfg=cfg,
                    rng=rng,
                ):
                    if len(candidates) >= int(cfg.max_candidates):
                        break
                    prop2 = _validate_and_score(deal2, tag="counter:reduce_protection")
                    if prop2 is None:
                        continue
                    _add_candidate(prop2, strategy="REDUCE_PICK_PROTECTION", meta=meta)
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Strategy 4: Pick/swap sweeteners (add value from USER to OTHER)
        # ------------------------------------------------------------------
        if cfg.enable_pick_sweeteners and len(candidates) < int(cfg.max_candidates) and _can_spend(v=2, e=2):
            try:
                stats = DealGeneratorStats(mode="COUNTER_SWEETENER")
                budget = cfg.to_dealgen_budget(
                    max_validations=min(int(cfg.max_validations) - validations_used, 90),
                    max_evaluations=min(int(cfg.max_evaluations) - evaluations_used, 45),
                    max_repairs=int(cfg.max_repairs),
                )

                best_prop, extra_v, extra_e = maybe_apply_sweeteners(
                    base_prop,
                    tick_ctx=tick_ctx,
                    catalog=getattr(tick_ctx, "asset_catalog", None),
                    config=dealgen_cfg,
                    budget=budget,
                    allow_locked_by_deal_id=allow_locked_by_deal_id,
                    banned_asset_keys=set(),
                    rng=rng,
                    stats=stats,
                )

                validations_used += int(extra_v)
                evaluations_used += int(extra_e)

                if best_prop is not None and best_prop.deal is not None:
                    # Ensure it actually changed
                    if dedupe_hash(best_prop.deal) != base_hash:
                        _add_candidate(
                            best_prop,
                            strategy="SWEETENER_PICK_SWAP",
                            meta={"sweeteners_added": int(getattr(stats, "sweeteners_added", 0) or 0)},
                        )
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Strategy 5: Player sweetener fallback (add 1 cheap player from USER)
        # ------------------------------------------------------------------
        if cfg.enable_player_sweeteners and len(candidates) < int(cfg.max_candidates) and _can_spend(v=2, e=2):
            try:
                deficit = _estimate_needed_value(base_prop, desired_extra=desired_extra)
                deal2s = _propose_player_sweeteners(
                    base_deal,
                    tick_ctx=tick_ctx,
                    user_id=user,
                    other_id=other,
                    cfg=cfg,
                    rng=rng,
                    need_value=float(deficit),
                )
                for deal2, meta in deal2s:
                    if len(candidates) >= int(cfg.max_candidates):
                        break
                    prop2 = _validate_and_score(deal2, tag="counter:player_sweetener")
                    if prop2 is None:
                        continue
                    _add_candidate(prop2, strategy="SWEETENER_PLAYER", meta=meta)
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Select best candidate
        # ------------------------------------------------------------------
        best = _pick_best_candidate(
            candidates,
            desired_extra=float(desired_extra),
            user_margin_floor=float(cfg.user_margin_floor),
            prefer_min_edits=bool(cfg.prefer_min_edits),
        )
        if best is None:
            return None

        # Build message + reasons
        message = build_counter_message(
            user_team_id=user,
            other_team_id=other,
            base_decision=base_prop.seller_decision,
            base_evaluation=base_prop.seller_eval,
            diff=best.diff,
            provider=getattr(tick_ctx, "provider", None),
            relationship=relationship,
            strategy=best.strategy,
        )

        # Explainability reasons (keep small)
        reasons: List[DecisionReason] = []
        reasons.append(
            DecisionReason(
                code="COUNTER_OFFER",
                message="Counter offer generated",
                meta={
                    "strategy": best.strategy,
                    "edit_distance": int(best.diff.edit_distance),
                },
            )
        )
        if _has_reason(base_prop.seller_decision, "FIT_FAILS"):
            reasons.append(DecisionReason(code="FIT_FAILS", message="Fit concerns triggered a counter"))

        # Target surplus summary
        seller_margin = _margin(best.prop.seller_eval, best.prop.seller_decision)
        buyer_margin = _margin(best.prop.buyer_eval, best.prop.buyer_decision)

        meta: Dict[str, Any] = {
            "strategy": best.strategy,
            "base_hash": base_hash,
            "counter_hash": dedupe_hash(best.deal),
            "desired_extra": float(desired_extra),
            "seller_margin": float(seller_margin),
            "buyer_margin": float(buyer_margin),
            "diff": best.diff.to_payload(),
            "message": message,
            # Safe, parseable representation for API responses
            "deal_serialized": serialize_deal(best.deal),
            # Internal budgets (for tuning/telemetry)
            "budget_used": {"validations": int(validations_used), "evaluations": int(evaluations_used)},
        }
        # merge candidate meta
        try:
            meta.update(best.meta or {})
        except Exception:
            pass

        return CounterProposal(
            deal=best.deal,
            reasons=tuple(reasons),
            meta=meta,
        )


# -----------------------------------------------------------------------------
# Strategy helpers
# -----------------------------------------------------------------------------


def _collect_removable_outgoing_assets(
    base_prop: DealProposal,
    *,
    seller_id: str,
    protected_keys: Set[str],
    player_market_cap: float,
) -> List[Asset]:
    """Collect assets in seller leg that we can remove for a minimal counter."""

    sid = _tid(seller_id)
    leg = list(base_prop.deal.legs.get(sid, []) or [])
    if not leg:
        return []

    # market totals from seller evaluation
    mkt_map: Dict[str, float] = {}
    try:
        for tv in (base_prop.seller_eval.side.outgoing or tuple()):
            k = str(getattr(tv, "asset_key", ""))
            if not k:
                continue
            mv = getattr(getattr(tv, "market_value", None), "total", None)
            mkt_map[k] = float(mv or 0.0)
    except Exception:
        mkt_map = {}

    def kind_pri(a: Asset) -> int:
        if isinstance(a, PickAsset):
            return 0
        if isinstance(a, SwapAsset):
            return 1
        if isinstance(a, FixedAsset):
            return 2
        return 3

    removable: List[Asset] = []
    for a in leg:
        k = asset_key(a)
        if k in protected_keys:
            continue

        # Avoid removing major players (looks unrealistic)
        if isinstance(a, PlayerAsset):
            if float(mkt_map.get(k, 0.0)) > float(player_market_cap):
                continue

        removable.append(a)

    removable.sort(key=lambda a: (kind_pri(a), float(mkt_map.get(asset_key(a), 0.0)), asset_key(a)))
    return removable


def _propose_reduce_pick_protection(
    base_deal: Deal,
    *,
    user_id: str,
    cfg: CounterOfferConfig,
    rng: random.Random,
) -> Sequence[Tuple[Deal, Dict[str, Any]]]:
    """Generate small variants that make a protected user pick more valuable.

    - Only supports TOP_N protection (project SSOT)
    - Reduces N (Top-10 -> Top-8 -> ...)
    """

    uid = _tid(user_id)
    leg = list(base_deal.legs.get(uid, []) or [])

    picks: List[PickAsset] = []
    for a in leg:
        if not isinstance(a, PickAsset):
            continue
        p = getattr(a, "protection", None)
        if not isinstance(p, Mapping):
            continue
        t = str(p.get("type") or p.get("rule") or "").strip().upper()
        if t != "TOP_N":
            continue
        try:
            n = int(p.get("n"))
        except Exception:
            continue
        if n <= int(cfg.reduce_pick_protection_min_n):
            continue
        picks.append(a)

    if not picks:
        return tuple()

    # deterministic: shuffle but stable
    picks_sorted = sorted(picks, key=lambda a: asset_key(a))
    rng.shuffle(picks_sorted)

    out: List[Tuple[Deal, Dict[str, Any]]] = []

    max_cands = max(0, int(cfg.reduce_pick_protection_max_candidates))

    step = max(1, int(cfg.reduce_pick_protection_step))
    steps = max(1, int(cfg.reduce_pick_protection_max_steps))
    min_n = max(1, int(cfg.reduce_pick_protection_min_n))

    for a in picks_sorted:
        if max_cands and len(out) >= max_cands:
            break

        prot = dict(getattr(a, "protection", None) or {})
        try:
            cur_n = int(prot.get("n"))
        except Exception:
            continue

        for s in range(1, steps + 1):
            if max_cands and len(out) >= max_cands:
                break

            new_n = max(min_n, cur_n - s * step)
            if new_n >= cur_n:
                continue

            prot2 = dict(prot)
            prot2["type"] = "TOP_N"
            prot2["n"] = int(new_n)

            # Rebuild leg with modified pick asset
            deal2 = _clone_deal(base_deal)
            leg2: List[Asset] = []
            for x in (deal2.legs.get(uid, []) or []):
                if asset_key(x) == asset_key(a) and isinstance(x, PickAsset):
                    # PickAsset is a frozen dataclass; use replace() to preserve kind/pick_id/to_team.
                    leg2.append(replace(x, protection=prot2))
                else:
                    leg2.append(x)
            deal2.legs[uid] = leg2

            out.append(
                (
                    deal2,
                    {
                        "pick_id": str(a.pick_id),
                        "before": prot,
                        "after": prot2,
                    },
                )
            )

    return tuple(out)


def _estimate_needed_value(base_prop: DealProposal, *, desired_extra: float) -> float:
    """Estimate how much additional value is needed on top of the base offer.

    We use seller margin + desired extra ask.
    """

    try:
        cur = _margin(base_prop.seller_eval, base_prop.seller_decision)
        # If cur is -2.0 and desired_extra is 1.0, need about 3.0.
        return max(0.0, float(desired_extra) - float(cur))
    except Exception:
        return max(0.0, float(desired_extra))


def _propose_player_sweeteners(
    base_deal: Deal,
    *,
    tick_ctx: TradeGenerationTickContext,
    user_id: str,
    other_id: str,
    cfg: CounterOfferConfig,
    rng: random.Random,
    need_value: float,
) -> Sequence[Tuple[Deal, Dict[str, Any]]]:
    """Add one low-value player from user to other team as a fallback sweetener."""

    uid = _tid(user_id)
    oid = _tid(other_id)

    cat = getattr(tick_ctx, "asset_catalog", None)
    if cat is None:
        return tuple()

    out_cat = getattr(cat, "outgoing_by_team", {}).get(uid)
    if out_cat is None:
        return tuple()

    # current outgoing player count (user leg)
    current_player_ids: Set[str] = set()
    for a in (base_deal.legs.get(uid, []) or []):
        if isinstance(a, PlayerAsset):
            current_player_ids.add(str(a.player_id))

    # Also exclude players already moving in the deal (either side)
    all_moving_players: Set[str] = set()
    for leg in base_deal.legs.values():
        for a in leg or []:
            if isinstance(a, PlayerAsset):
                all_moving_players.add(str(a.player_id))

    buckets = tuple(str(b) for b in (cfg.player_sweetener_buckets or tuple()))

    pool: List[Any] = []
    for b in buckets:
        for pid in out_cat.player_ids_by_bucket.get(b, tuple()):
            c = out_cat.players.get(pid)
            if c is None:
                continue
            if str(pid) in all_moving_players:
                continue
            # lock
            try:
                if bool(getattr(getattr(c, "lock", None), "is_locked", False)):
                    continue
            except Exception:
                pass
            # return ban
            try:
                bans = set(str(x).upper() for x in (getattr(c, "return_ban_teams", None) or tuple()))
                if oid in bans:
                    continue
            except Exception:
                pass

            # aggregation solo-only cannot be bundled
            try:
                if bool(getattr(c, "aggregation_solo_only", False)) and len(current_player_ids) >= 1:
                    continue
            except Exception:
                pass

            # market cap
            mkt = 0.0
            try:
                mkt = float(getattr(getattr(c, "market", None), "total", 0.0) or 0.0)
            except Exception:
                mkt = 0.0
            if mkt > float(cfg.player_sweetener_market_cap):
                continue

            pool.append(c)

    if not pool:
        return tuple()

    # Rank by closeness to need_value, then by cheaper market, then stable id.
    def _score(c: Any) -> Tuple[float, float, str]:
        mkt = 0.0
        try:
            mkt = float(getattr(getattr(c, "market", None), "total", 0.0) or 0.0)
        except Exception:
            mkt = 0.0
        return (abs(mkt - float(need_value)), mkt, str(getattr(c, "player_id", "")))

    pool.sort(key=_score)

    # Limit candidate pool and shuffle within same score bucket (deterministic)
    max_pool = max(1, int(cfg.player_sweetener_candidate_pool))
    pool = pool[: max_pool * 2]
    rng.shuffle(pool)
    pool.sort(key=_score)
    pool = pool[:max_pool]

    out: List[Tuple[Deal, Dict[str, Any]]] = []

    max_add = max(1, int(cfg.player_sweetener_max_additions))
    for c in pool[:max_add]:
        pid = str(getattr(c, "player_id", ""))
        if not pid:
            continue

        deal2 = _clone_deal(base_deal)
        deal2.legs.setdefault(uid, [])
        # For 2-team deals, to_team can be omitted; keep None for SSOT.
        deal2.legs[uid].append(PlayerAsset(kind="player", player_id=pid, to_team=None))

        out.append((deal2, {"player_id": pid, "market_total": float(getattr(getattr(c, "market", None), "total", 0.0) or 0.0)}))

    return tuple(out)


# -----------------------------------------------------------------------------
# Candidate selection
# -----------------------------------------------------------------------------


def _pick_best_candidate(
    candidates: Sequence[CounterCandidate],
    *,
    desired_extra: float,
    user_margin_floor: float,
    prefer_min_edits: bool,
) -> Optional[CounterCandidate]:
    """Pick the best candidate with NBA-like heuristics."""

    if not candidates:
        return None

    # Filter: seller must ACCEPT
    acceptable: List[CounterCandidate] = []
    for c in candidates:
        try:
            if c.prop.seller_decision.verdict != DealVerdict.ACCEPT:
                continue
        except Exception:
            continue

        # User guard
        try:
            bm = _margin(c.prop.buyer_eval, c.prop.buyer_decision)
            if bm < float(user_margin_floor):
                continue
        except Exception:
            pass

        acceptable.append(c)

    if not acceptable:
        return None

    # Rank
    def rank(c: CounterCandidate) -> Tuple[int, int, float, float, float, int]:
        # smaller is better
        edit = int(getattr(c.diff, "edit_distance", 0) or 0)

        # Prefer user not REJECT
        buyer_reject = 0
        try:
            if c.prop.buyer_decision.verdict == DealVerdict.REJECT:
                buyer_reject = 1
        except Exception:
            buyer_reject = 0

        bm = 0.0
        sm = 0.0
        try:
            bm = _margin(c.prop.buyer_eval, c.prop.buyer_decision)
            sm = _margin(c.prop.seller_eval, c.prop.seller_decision)
        except Exception:
            bm = 0.0
            sm = 0.0

        # Desired seller margin is desired_extra (>=0). We want sm close to it,
        # but not too much larger (avoid absurd fleece).
        want = max(0.0, float(desired_extra))
        # over-ask penalty (prefer not to be too greedy)
        over = max(0.0, sm - want)
        under = max(0.0, want - sm)
        seller_pen = under * 2.5 + over * 0.6

        # User penalty: negative margin is bad
        user_pen = max(0.0, -bm)

        # Use prop.score as a tie-breaker (higher better)
        score = float(getattr(c.prop, "score", 0.0) or 0.0)

        # Total assets as a last tie-breaker (prefer simple)
        assets = _deal_total_assets(c.deal)

        if prefer_min_edits:
            return (edit, buyer_reject, float(seller_pen), float(user_pen), -score, assets)
        # If not, allow slightly larger edit distance if it improves user_pen a lot.
        return (buyer_reject, float(seller_pen), float(user_pen), edit, -score, assets)

    acceptable.sort(key=rank)
    return acceptable[0]
