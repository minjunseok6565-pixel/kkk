from __future__ import annotations

"""pick_protection_decorator.py

Deal-local pick protection generation for AI trade proposals.

Why this module exists
----------------------
The SSOT already supports pick protections at the rule/execution/settlement level.
However, the AI deal generator historically only proposes picks with whatever
protection is already stored in the DB snapshot (often None).

This module adds an AI-side *decorator* step:
  - Start from a candidate deal that already chose a pick.
  - Create 1~N protection variants for that pick.
  - Re-validate and re-evaluate those variants.
  - Keep the best proposal.

Design constraints
------------------
- Do NOT mutate SSOT snapshots. A protection is deal-local until the trade is
  executed, at which point LeagueService persists the protection_json.
- Keep evaluation cost bounded and deterministic.
- Rely on trades.models.normalize_pick_protection() so schema stays centralized.
"""

from typing import Any, Dict, List, Optional, Tuple

from ...errors import TradeError
from ...models import Deal, PickAsset, normalize_pick_protection

from ..generation_tick import TradeGenerationTickContext
from ..asset_catalog import TradeAssetCatalog, TeamOutgoingCatalog, PickTradeCandidate

from .types import DealGeneratorBudget, DealGeneratorConfig, DealGeneratorStats, DealProposal
from .utils import _clone_deal
from .scoring import evaluate_and_score, _should_discard_prop


# -----------------------------------------------------------------------------
# Small pure helpers
# -----------------------------------------------------------------------------

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _canon_team_id(team_id: Any) -> str:
    return str(team_id or "").strip().upper()


def _margin_buyer(prop: DealProposal) -> float:
    return float(prop.buyer_eval.net_surplus) - float(prop.buyer_decision.required_surplus)


def _margin_seller(prop: DealProposal) -> float:
    return float(prop.seller_eval.net_surplus) - float(prop.seller_decision.required_surplus)


def _margin_for_team(prop: DealProposal, team_id: str) -> float:
    tid = _canon_team_id(team_id)
    if tid == _canon_team_id(prop.buyer_id):
        return _margin_buyer(prop)
    if tid == _canon_team_id(prop.seller_id):
        return _margin_seller(prop)
    return 0.0


def _proposal_key(prop: DealProposal) -> Tuple[float, float]:
    """Selection key for decorator stage.

    Primary: improve the *worst* side margin (min of buyer/seller margins).
    Secondary: score (includes smooth preference for stronger acceptance).

    This tends to prefer deals where one side had cushion and the other side was
    close to the line (a common place where protections appear in real NBA deals).
    """

    mb = _margin_buyer(prop)
    ms = _margin_seller(prop)
    return (min(mb, ms), float(prop.score))


# -----------------------------------------------------------------------------
# Protection construction
# -----------------------------------------------------------------------------

def build_top_n_protection(
    top_n: int,
    *,
    comp_value: float,
    label: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a canonical TOP_N protection dict.

    Uses trades.models.normalize_pick_protection() to enforce SSOT schema.
    """

    n_i = int(top_n)
    payload: Dict[str, Any] = {
        "type": "TOP_N",
        "n": n_i,
        "compensation": {
            "label": str(label or f"Top-{n_i} protected pick compensation"),
            "value": float(comp_value),
        },
    }
    return normalize_pick_protection(payload)


def _comp_value_for_top_n(
    top_n: int,
    *,
    pick_market_total: float,
    config: Any,
) -> float:
    """Heuristic compensation value for a protected pick.

    SSOT only needs a numeric value; market_pricing blends expected value as:
      p_convey * unprotected + (1-p_convey) * compensation

    Defaults are conservative and avoid compensation exceeding the pick value.
    """

    base = float(getattr(config, "pick_protection_comp_base", 3.5) or 3.5)
    n = int(top_n)
    # Roughly: stronger protections tend to require better compensation.
    scale = {
        4: 2.0,   # ~two 2nds
        6: 1.7,
        8: 1.4,
        10: 1.0,  # ~one 2nd
        12: 0.9,
        14: 0.8,
    }.get(n, 1.0)

    comp = base * float(scale)

    # Defensive clamp: keep compensation meaningfully below the (unprotected) pick value.
    pm = float(pick_market_total or 0.0)
    if pm > 0.0:
        comp = min(comp, max(0.5, 0.65 * pm))

    return float(_clamp(comp, 0.25, 25.0))


def _strength_from_decision_context(ctx: Any, *, pick_bucket: Optional[str] = None) -> float:
    """Compute a [0,1] strength where higher => stronger protection (smaller N)."""

    # Prefer effective traits when available.
    eff = getattr(ctx, "effective_traits", None)
    gm = getattr(ctx, "gm_traits", None)

    def _get(name: str, default: float) -> float:
        try:
            if eff is not None and hasattr(eff, name):
                return float(getattr(eff, name) or 0.0)
        except Exception:
            pass
        try:
            if gm is not None:
                # gm_traits uses different field names.
                if name == "eff_risk_tol":
                    return float(getattr(gm, "risk_tolerance", default) or default)
                if name == "eff_pick_pref":
                    return float(getattr(gm, "pick_preference", default) or default)
        except Exception:
            pass
        return float(default)

    risk_tol = _clamp(_get("eff_risk_tol", 0.5), 0.0, 1.0)
    pick_pref = _clamp(_get("eff_pick_pref", 0.5), 0.0, 1.0)
    urgency = _clamp(float(getattr(ctx, "urgency", 0.5) or 0.5), 0.0, 1.0)

    posture = str(getattr(ctx, "posture", "") or "").upper()

    # Stronger protection when:
    # - GM is risk-averse
    # - GM values picks highly
    # Weaken when urgency is high (win-now desperation).
    strength = 0.45 + (1.0 - risk_tol) * 0.40 + pick_pref * 0.25 - urgency * 0.20

    # Sensitive 1sts tend to be the ones teams protect in reality.
    if str(pick_bucket or "").upper() in ("FIRST_SENSITIVE", "SENSITIVE"):
        strength += 0.08

    # Selling posture rarely sends premium picks; if they do, they tend to protect.
    if posture in ("SELL", "SOFT_SELL"):
        strength += 0.04

    return float(_clamp(strength, 0.0, 1.0))


def choose_top_n_options(
    ctx: Any,
    *,
    pick_bucket: Optional[str],
    config: Any,
    max_variants: int = 2,
) -> List[int]:
    """Choose 1~N TOP_N values to try.

    Keeps the set small to control eval cost.
    """

    # Allow config override with an explicit list like (4, 8, 10).
    raw = getattr(config, "pick_protection_topn_options", None)
    if isinstance(raw, (list, tuple)) and raw:
        out: List[int] = []
        for x in raw:
            try:
                n = int(x)
            except Exception:
                continue
            if 1 <= n <= 30 and n not in out:
                out.append(n)
        if out:
            return out[: max(1, int(max_variants))]

    strength = _strength_from_decision_context(ctx, pick_bucket=pick_bucket)

    # Map strength -> primary TOP_N.
    if strength >= 0.78:
        primary = 4
    elif strength >= 0.64:
        primary = 8
    elif strength >= 0.50:
        primary = 10
    else:
        primary = 14

    # Add an adjacent option (slightly weaker) to allow acceptance tuning.
    ladder = [4, 8, 10, 14]
    try:
        i = ladder.index(primary)
    except ValueError:
        i = 2
    secondary = ladder[min(i + 1, len(ladder) - 1)]

    out = [primary]
    if secondary not in out:
        out.append(secondary)

    return out[: max(1, int(max_variants))]


def default_sweetener_protection(
    *,
    pick_bucket: Optional[str],
    pick_market_total: float,
    config: Any,
) -> Dict[str, Any]:
    """A lightweight protection choice for sweetener candidate pools.

    Sweeteners should not explode evaluation space; keep it deterministic and
    fairly standard (Top-10, or Top-8 for sensitive 1sts).
    """

    b = str(pick_bucket or "").upper()
    top_n = 8 if b in ("FIRST_SENSITIVE", "SENSITIVE") else 10
    comp = _comp_value_for_top_n(top_n, pick_market_total=float(pick_market_total), config=config)
    return build_top_n_protection(top_n, comp_value=comp)


# -----------------------------------------------------------------------------
# Deal mutation (pure)
# -----------------------------------------------------------------------------

def apply_pick_protection(
    deal: Deal,
    *,
    team_id: str,
    pick_id: str,
    protection: Dict[str, Any],
) -> Optional[Deal]:
    """Return a cloned Deal where (team_id -> pick_id) is updated with protection.

    Returns None if the pick is not found in that team's outgoing leg, or if the
    pick already has protection.
    """

    tid = _canon_team_id(team_id)
    pid = str(pick_id)

    if tid not in deal.legs:
        return None

    replaced_any = False
    d2 = _clone_deal(deal)
    leg = list(d2.legs.get(tid, []) or [])
    new_leg = []
    for a in leg:
        if isinstance(a, PickAsset) and str(a.pick_id) == pid:
            # Only attach protection when it was originally unprotected.
            if a.protection is not None:
                new_leg.append(a)
            else:
                new_leg.append(
                    PickAsset(
                        kind=a.kind,
                        pick_id=a.pick_id,
                        to_team=getattr(a, "to_team", None),
                        protection=dict(protection),
                    )
                )
                replaced_any = True
        else:
            new_leg.append(a)

    if not replaced_any:
        return None

    d2.legs[tid] = new_leg
    return d2


def _select_pick_to_protect(
    deal: Deal,
    *,
    team_id: str,
    out_cat: Optional[TeamOutgoingCatalog],
    tick_ctx: TradeGenerationTickContext,
) -> Optional[Tuple[str, Optional[PickTradeCandidate]]]:
    """Pick a single unprotected 1st-round pick in this team's outgoing leg."""

    tid = _canon_team_id(team_id)
    leg = list(deal.legs.get(tid, []) or [])

    pick_ids: List[str] = []
    for a in leg:
        if not isinstance(a, PickAsset):
            continue
        if a.protection is not None:
            continue
        pick_ids.append(str(a.pick_id))

    if not pick_ids:
        return None

    rows: List[Tuple[float, int, str, Optional[PickTradeCandidate]]] = []
    for pid in pick_ids:
        cand: Optional[PickTradeCandidate] = None
        if out_cat is not None:
            cand = out_cat.picks.get(str(pid))

        # Determine round (prefer catalog, fallback to provider)
        rnd: Optional[int] = None
        if cand is not None:
            try:
                rnd = int(cand.snap.round)
            except Exception:
                rnd = None
        if rnd is None:
            try:
                snap = tick_ctx.provider.get_pick_snapshot(str(pid))
                rnd = int(getattr(snap, "round", 0) or 0)
            except Exception:
                rnd = None

        if rnd != 1:
            continue

        mv = 0.0
        if cand is not None:
            try:
                mv = float(getattr(cand.market, "total", 0.0) or 0.0)
            except Exception:
                mv = 0.0

        # Sort key: market desc, then stable id
        rows.append((mv, rnd, str(pid), cand))

    if not rows:
        return None

    rows.sort(key=lambda t: (-t[0], t[2]))
    _, __, pid, cand = rows[0]
    return pid, cand


# -----------------------------------------------------------------------------
# Public API for core.py
# -----------------------------------------------------------------------------

def maybe_apply_pick_protection_variants(
    base_prop: DealProposal,
    *,
    tick_ctx: TradeGenerationTickContext,
    catalog: TradeAssetCatalog,
    config: DealGeneratorConfig,
    budget: DealGeneratorBudget,
    allow_locked_by_deal_id: Optional[str],
    opponent_repeat_count: int,
    stats: Optional[DealGeneratorStats] = None,
) -> Tuple[DealProposal, int, int]:
    """Try pick-protection variants and keep the best proposal.

    Returns
    -------
    (best_prop, validations_used, evaluations_used)
    """

    if not bool(getattr(config, "pick_protection_decorator_enabled", True)):
        return base_prop, 0, 0

    # Fast budget guard.
    if stats is not None:
        if stats.validations >= budget.max_validations or stats.evaluations >= budget.max_evaluations:
            return base_prop, 0, 0

    deal = base_prop.deal

    # Candidate teams: those who are sending an unprotected 1st in this deal.
    candidates: List[Tuple[float, float, str, str, Optional[PickTradeCandidate]]] = []
    for tid in (_canon_team_id(base_prop.buyer_id), _canon_team_id(base_prop.seller_id)):
        out_cat = catalog.outgoing_by_team.get(tid)
        sel = _select_pick_to_protect(deal, team_id=tid, out_cat=out_cat, tick_ctx=tick_ctx)
        if sel is None:
            continue
        pid, cand = sel

        m = _margin_for_team(base_prop, tid)

        # "Need" heuristic: prioritize the side that's closer to failing.
        # - Negative margin is urgent.
        # - If both are positive, still allow protection on the tighter side.
        need = (-m) if m < 0.0 else (0.15 - m)  # small positive when m < 0.15

        candidates.append((float(need), float(m), tid, pid, cand))

    if not candidates:
        return base_prop, 0, 0

    # Highest need first; tie-break smaller margin first.
    candidates.sort(key=lambda t: (-t[0], t[1], t[2], t[3]))
    _, __, protect_team, pick_id, pick_cand = candidates[0]

    # Determine bucket + market
    pick_bucket: Optional[str] = None
    pick_market_total = 0.0
    if pick_cand is not None:
        pick_bucket = str(getattr(pick_cand, "bucket", "") or "")
        try:
            pick_market_total = float(getattr(pick_cand.market, "total", 0.0) or 0.0)
        except Exception:
            pick_market_total = 0.0

    # Decision context for the team sending the pick.
    try:
        dc = tick_ctx.get_decision_context(protect_team)
    except Exception:
        dc = None

    max_variants = int(getattr(config, "pick_protection_max_variants", 2) or 2)
    top_ns = choose_top_n_options(dc, pick_bucket=pick_bucket, config=config, max_variants=max_variants)

    # Build deal variants.
    variants: List[Tuple[int, Deal]] = []
    for n in top_ns:
        try:
            comp = _comp_value_for_top_n(n, pick_market_total=pick_market_total, config=config)
            prot = build_top_n_protection(n, comp_value=comp)
        except TradeError as err:
            if stats is not None:
                stats.bump_failure(str(getattr(err, "code", "pick_protection_invalid")))
            continue
        except Exception:
            if stats is not None:
                stats.bump_failure("pick_protection_build_exception")
            continue

        d2 = apply_pick_protection(deal, team_id=protect_team, pick_id=pick_id, protection=prot)
        if d2 is None:
            continue
        variants.append((int(n), d2))

    if not variants:
        return base_prop, 0, 0

    best = base_prop
    best_key = _proposal_key(base_prop)
    used_v = 0
    used_e = 0

    # Evaluate each variant.
    for n, d2 in variants:
        # Budget guard (conservative): 1 validation + 2 evals.
        if stats is not None:
            if stats.validations + used_v >= budget.max_validations:
                break
            if stats.evaluations + used_e >= budget.max_evaluations:
                break

        # validate (no repair)
        try:
            tick_ctx.validate_deal(d2, allow_locked_by_deal_id=allow_locked_by_deal_id)
            used_v += 1
        except TradeError as err:
            used_v += 1
            if stats is not None:
                stats.bump_failure(str(getattr(err, "code", "pick_protection_validate_error")))
            continue
        except Exception:
            used_v += 1
            if stats is not None:
                stats.bump_failure("pick_protection_validate_exception")
            continue

        # Evaluate + score.
        tags = tuple(list(best.tags) + [f"pick_protection:TOP_{int(n)}", f"pick_protection_team:{protect_team}"])
        prop2, e_used = evaluate_and_score(
            d2,
            buyer_id=base_prop.buyer_id,
            seller_id=base_prop.seller_id,
            tick_ctx=tick_ctx,
            config=config,
            tags=tags,
            opponent_repeat_count=int(opponent_repeat_count),
            stats=stats,
        )
        used_e += int(e_used)
        if prop2 is None:
            continue

        # Discard protection variants that are still "game-experience bad".
        if _should_discard_prop(prop2, config):
            continue

        k2 = _proposal_key(prop2)
        if k2 > best_key:
            best = prop2
            best_key = k2

    return best, used_v, used_e
