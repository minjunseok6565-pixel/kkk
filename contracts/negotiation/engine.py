from __future__ import annotations

"""Pure negotiation logic (no DB I/O).

The engine is deterministic and explainable:
- All decisions return structured reasons with numeric evidence.
- Borderline cases use deterministic 'randomness' (stable_u01) based on session/player context.
"""

from dataclasses import asdict, replace
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .config import ContractNegotiationConfig, DEFAULT_CONTRACT_NEGOTIATION_CONFIG
from .types import ContractOffer, NegotiationDecision, PlayerPosition, Reason
from .utils import clamp, clamp01, mental_norm, safe_float, safe_int, sigmoid, stable_u01


# -----------------------------------------------------------------------------
# Market reference
# -----------------------------------------------------------------------------


def expected_market_aav_from_ovr(ovr: float, *, cfg: ContractNegotiationConfig) -> float:
    """Estimate market AAV from OVR.

    For consistency across the project, we prefer the agency.options curve when available.
    """
    if bool(getattr(cfg, "use_agency_options_curve", True)):
        try:
            from agency.config import DEFAULT_CONFIG as _AGENCY_DEFAULT
            from agency.options import expected_market_aav_from_ovr as _agency_curve

            # Cap-normalized curve: propagate salary_cap when available.
            opt_cfg = _AGENCY_DEFAULT.options
            cap = safe_float(getattr(cfg, "salary_cap", None), 0.0)
            if cap > 1e-9:
                try:
                    opt_cfg = replace(opt_cfg, salary_cap=float(cap))
                except Exception:
                    opt_cfg = _AGENCY_DEFAULT.options

            return float(_agency_curve(float(ovr), cfg=opt_cfg))
        except Exception:
            # Fall back to local curve below.
            pass

    x = (float(ovr) - float(cfg.expected_salary_ovr_center)) / max(float(cfg.expected_salary_ovr_scale), 1e-9)
    s = sigmoid(float(x))

    # Cap-normalized local fallback curve.
    cap = safe_float(getattr(cfg, "salary_cap", None), 0.0)
    if cap > 1e-9:
        mid_pct = safe_float(getattr(cfg, "expected_salary_midpoint_cap_pct", None), 0.0)
        span_pct = safe_float(getattr(cfg, "expected_salary_span_cap_pct", None), 0.0)
        midpoint = float(cap) * float(mid_pct)
        span = float(cap) * float(span_pct)
    else:
        midpoint = float(cfg.expected_salary_midpoint)
        span = float(cfg.expected_salary_span)

    lo = float(midpoint) - float(span)
    hi = float(midpoint) + float(span)

    # Defensive: keep ordering and non-negative lower bound.
    if hi < lo:
        lo, hi = hi, lo
    if lo < 0.0:
        lo = 0.0
    return float(lo + (hi - lo) * s)


def round_salary(amount: float, *, cfg: ContractNegotiationConfig) -> float:
    """Round salary to a clean increment for UI consistency."""
    a = float(max(0.0, amount))
    inc = int(getattr(cfg, "salary_rounding", 10_000) or 10_000)
    if inc <= 0:
        return float(a)
    return float(int(round(a / inc)) * inc)


# -----------------------------------------------------------------------------
# Position construction
# -----------------------------------------------------------------------------


def _infer_ideal_years(age: int) -> int:
    a = int(age)
    if a <= 25:
        return 4
    if a <= 29:
        return 3
    if a <= 33:
        return 2
    return 1


def build_player_position(
    player_snapshot: Mapping[str, Any],
    team_snapshot: Mapping[str, Any],
    agency_snapshot: Mapping[str, Any],
    *,
    mode: str,
    cfg: ContractNegotiationConfig = DEFAULT_CONTRACT_NEGOTIATION_CONFIG,
) -> PlayerPosition:
    """Compute market/ask/floor + years preferences + style parameters."""
    ovr = safe_int(player_snapshot.get("ovr"), 0)
    age = safe_int(player_snapshot.get("age"), 27)
    mental = player_snapshot.get("mental") if isinstance(player_snapshot.get("mental"), Mapping) else {}
    leverage = clamp01(player_snapshot.get("leverage", 0.5))

    minutes_fr = clamp01(agency_snapshot.get("minutes_frustration", 0.0))
    team_fr = clamp01(agency_snapshot.get("team_frustration", 0.0))
    trust = clamp01(agency_snapshot.get("trust", 0.5))

    win_pct_raw = team_snapshot.get("win_pct", None)
    win_pct = None
    try:
        if win_pct_raw is not None:
            win_pct = clamp01(win_pct_raw)
    except Exception:
        win_pct = None

    amb = mental_norm(mental, "ambition")
    ego = mental_norm(mental, "ego")
    loy = mental_norm(mental, "loyalty")
    coach = mental_norm(mental, "coachability")
    adapt = mental_norm(mental, "adaptability")

    frustration = clamp01(0.5 * minutes_fr + 0.5 * team_fr)

    market_aav = expected_market_aav_from_ovr(float(ovr), cfg=cfg)
    # Defensive: if market is tiny, treat as replacement-level.
    if market_aav <= 1.0:
        market_aav = 1_000_000.0

    # --- Team misery signal (only matters if win_pct known)
    badness = 0.0
    if win_pct is not None:
        good = float(cfg.good_team_win_pct)
        if good <= 0:
            badness = 0.0
        else:
            badness = clamp01((good - float(win_pct)) / good)

    # --- Ask multiplier (soft)
    # Players with high leverage + ambition/ego push ask up.
    # Loyalty+trust can create a team-friendly discount on re-signs.
    team_misery = badness * amb * (1.0 - loy)
    ask_premium = (
        float(cfg.w_ambition_ask) * (amb - 0.5)
        + float(cfg.w_ego_ask) * (ego - 0.5)
        + float(cfg.w_leverage_ask) * (leverage - 0.5)
        + float(cfg.w_frustration_ask) * (frustration - 0.5)
        + float(cfg.w_bad_team_ask) * (team_misery - 0.15)
    )

    loyalty_trust = loy * trust
    team_discount = float(cfg.w_loyalty_trust_discount_ask) * (loyalty_trust - 0.25)

    # Extra discount for re-sign/extend when relationship is good.
    if str(mode).upper() in {"RE_SIGN", "EXTEND"}:
        team_discount *= 1.25

    ask_mult = 1.0 + float(ask_premium) - float(team_discount)
    ask_mult = clamp(ask_mult, float(cfg.ask_mult_min), float(cfg.ask_mult_max))

    # --- Floor gap: tighter gap for stubborn profiles; wider for flexible.
    gap = float(cfg.base_floor_gap)
    gap += float(cfg.w_floor_gap_ego) * (ego - 0.5)
    gap += float(cfg.w_floor_gap_ambition) * (amb - 0.5)
    gap += float(cfg.w_floor_gap_coachability) * (coach - 0.5)
    gap += float(cfg.w_floor_gap_loyalty) * (loy - 0.5)
    gap += float(cfg.w_floor_gap_trust) * (trust - 0.5)
    gap = clamp(gap, 0.03, 0.18)

    floor_mult = ask_mult - gap
    floor_mult = clamp(floor_mult, float(cfg.floor_mult_min), min(float(cfg.floor_mult_max), float(ask_mult)))

    ask_aav = round_salary(float(market_aav) * float(ask_mult), cfg=cfg)
    floor_aav = round_salary(float(market_aav) * float(floor_mult), cfg=cfg)

    # --- Years preferences
    ideal_years = _infer_ideal_years(age)

    # Ambitious prime players often prefer shorter (bet on self).
    if age <= 30 and amb >= 0.66:
        ideal_years -= 1

    # Loyal + trusting players are willing to commit longer.
    if loy >= 0.70 and trust >= 0.55:
        ideal_years += 1

    ideal_years = int(clamp(ideal_years, float(cfg.min_years_allowed), float(cfg.max_years_allowed)))

    min_years = int(cfg.min_years_allowed)
    max_years = int(cfg.max_years_allowed)

    # Slightly narrow the range for very high ambition (shorter window)
    if amb >= 0.75 and age <= 30:
        max_years = max(min_years, min(max_years, ideal_years + 1))

    # Slightly widen for high loyalty/trust (stability)
    if loy >= 0.75 and trust >= 0.60:
        max_years = min(int(cfg.max_years_allowed), max(max_years, ideal_years + 2))

    # Ensure ordering
    min_years = max(int(cfg.min_years_allowed), min_years)
    max_years = min(int(cfg.max_years_allowed), max_years)
    ideal_years = int(clamp(ideal_years, float(min_years), float(max_years)))

    # --- Concession / insult / patience
    concession = float(cfg.concession_base)
    concession += float(cfg.concession_w_coachability) * (coach - 0.5)
    concession += float(cfg.concession_w_adaptability) * (adapt - 0.5)
    concession += float(cfg.concession_w_trust) * (trust - 0.5)
    concession += float(cfg.concession_w_ego) * (ego - 0.5)
    concession += float(cfg.concession_w_ambition) * (amb - 0.5)
    concession = clamp(concession, float(cfg.concession_min), float(cfg.concession_max))

    insult_ratio = float(cfg.insult_ratio_base)
    insult_ratio += float(cfg.insult_w_ego) * (ego - 0.5)
    insult_ratio += float(cfg.insult_w_leverage) * (leverage - 0.5)
    insult_ratio = clamp(insult_ratio, float(cfg.insult_ratio_min), float(cfg.insult_ratio_max))

    patience = float(cfg.patience_base)
    patience += float(cfg.patience_w_coachability) * (coach - 0.5)
    patience += float(cfg.patience_w_loyalty) * (loy - 0.5)
    patience += float(cfg.patience_w_adaptability) * (adapt - 0.5)
    patience += float(cfg.patience_w_trust) * (trust - 0.5)
    patience += float(cfg.patience_w_ego) * (ego - 0.5)
    patience += float(cfg.patience_w_ambition) * (amb - 0.5)
    patience = clamp01(patience)

    rounds_min = int(cfg.rounds_min)
    rounds_max = int(cfg.rounds_max)
    if rounds_max < rounds_min:
        rounds_max = rounds_min
    max_rounds_i = int(round(rounds_min + (rounds_max - rounds_min) * float(patience)))
    max_rounds_i = int(clamp(max_rounds_i, float(rounds_min), float(rounds_max)))

    required_demands: List[Dict[str, Any]] = []
    if bool(getattr(cfg, "enable_non_monetary_gate", False)):
        if minutes_fr >= float(cfg.require_minutes_promise_threshold) and ego >= 0.60 and leverage >= 0.60:
            required_demands.append({"type": "MINUTES_PROMISE"})
        if team_fr >= float(cfg.require_help_promise_threshold) and amb >= 0.65 and leverage >= 0.75:
            required_demands.append({"type": "HELP_PROMISE"})

    return PlayerPosition(
        market_aav=float(market_aav),
        ask_aav=float(ask_aav),
        floor_aav=float(floor_aav),
        min_years=int(min_years),
        ideal_years=int(ideal_years),
        max_years=int(max_years),
        concession_rate=float(concession),
        insult_ratio=float(insult_ratio),
        patience=float(patience),
        max_rounds=int(max_rounds_i),
        required_demands=required_demands,
    )


# -----------------------------------------------------------------------------
# Offer evaluation
# -----------------------------------------------------------------------------


def _pos_from_session(session: Mapping[str, Any], *, cfg: ContractNegotiationConfig) -> PlayerPosition:
    raw = session.get("player_position")
    if isinstance(raw, PlayerPosition):
        return raw
    if isinstance(raw, Mapping):
        try:
            return PlayerPosition(
                market_aav=float(safe_float(raw.get("market_aav"), 0.0)),
                ask_aav=float(safe_float(raw.get("ask_aav"), 0.0)),
                floor_aav=float(safe_float(raw.get("floor_aav"), 0.0)),
                min_years=safe_int(raw.get("min_years"), int(cfg.min_years_allowed)),
                ideal_years=safe_int(raw.get("ideal_years"), 2),
                max_years=safe_int(raw.get("max_years"), int(cfg.max_years_allowed)),
                concession_rate=float(clamp01(raw.get("concession_rate", 0.22))),
                insult_ratio=float(clamp(raw.get("insult_ratio", 0.92), 0.0, 1.0)),
                patience=float(clamp01(raw.get("patience", 0.5))),
                max_rounds=safe_int(raw.get("max_rounds"), 4),
                required_demands=list(raw.get("required_demands") or []),
            )
        except Exception:
            pass

    # Rebuild from snapshots if missing/invalid.
    player_snapshot = session.get("player_snapshot") if isinstance(session.get("player_snapshot"), Mapping) else {}
    team_snapshot = session.get("team_snapshot") if isinstance(session.get("team_snapshot"), Mapping) else {}
    agency_snapshot = session.get("agency_snapshot") if isinstance(session.get("agency_snapshot"), Mapping) else {}
    mode = str(session.get("mode") or "SIGN_FA")
    return build_player_position(player_snapshot, team_snapshot, agency_snapshot, mode=mode, cfg=cfg)


def _demands_satisfied(offer: ContractOffer, demands: List[Dict[str, Any]]) -> bool:
    if not demands:
        return True
    nm = offer.non_monetary or {}
    # Convention: offer.non_monetary.promises can be a list of {type: ...}
    promises = nm.get("promises") if isinstance(nm.get("promises"), list) else []
    promise_types = {str(p.get("type")) for p in promises if isinstance(p, Mapping) and p.get("type")}
    for d in demands:
        if not isinstance(d, Mapping):
            continue
        t = d.get("type")
        if not t:
            continue
        if str(t) not in promise_types:
            return False
    return True


def _years_preference_strength(mental: Mapping[str, Any]) -> float:
    amb = mental_norm(mental, "ambition")
    loy = mental_norm(mental, "loyalty")
    # positive => prefer shorter, negative => prefer longer
    return clamp( (amb - 0.5) - (loy - 0.5), -1.0, 1.0 )


def _adjust_floor_for_years(
    floor_aav: float,
    *,
    offer_years: int,
    ideal_years: int,
    mental: Mapping[str, Any],
    cfg: ContractNegotiationConfig,
) -> Tuple[float, float]:
    """Return (adjusted_floor, penalty_mult)."""
    offer_y = int(offer_years)
    ideal_y = int(ideal_years)
    if offer_y <= 0 or ideal_y <= 0:
        return (float(floor_aav), 1.0)

    pref = float(_years_preference_strength(mental))
    delta = offer_y - ideal_y

    # Only penalize when mismatch fights the player's preference.
    penalty = 0.0
    if delta > 0 and pref > 0:
        penalty = float(cfg.years_mismatch_penalty) * float(delta) * float(pref)
    elif delta < 0 and pref < 0:
        penalty = float(cfg.years_mismatch_penalty) * float(abs(delta)) * float(abs(pref))

    penalty = clamp(penalty, 0.0, 0.25)
    mult = 1.0 + penalty
    return (float(floor_aav) * float(mult), float(mult))


def build_counter_offer(
    *,
    session: Mapping[str, Any],
    offer: ContractOffer,
    pos: PlayerPosition,
    cfg: ContractNegotiationConfig,
) -> ContractOffer:
    player_snap = session.get("player_snapshot") if isinstance(session.get("player_snapshot"), Mapping) else {}
    mental = player_snap.get("mental") if isinstance(player_snap.get("mental"), Mapping) else {}
    market_aav = float(pos.market_aav)

    round_i = safe_int(session.get("round"), 0)
    max_rounds = safe_int(session.get("max_rounds"), int(pos.max_rounds))
    if max_rounds <= 0:
        max_rounds = int(pos.max_rounds) if int(pos.max_rounds) > 0 else 4

    progress = clamp01((float(round_i) + 1.0) / float(max_rounds))

    offer_aav = float(offer.aav())
    ask_aav = float(pos.ask_aav)
    floor_aav = float(pos.floor_aav)

    # Ask target decays towards floor as rounds progress; faster for flexible players.
    decay_k = float(cfg.ask_decay_base) + float(cfg.ask_decay_by_concession) * float(pos.concession_rate)
    target_weight = max(0.0, 1.0 - float(progress) * float(decay_k))
    target = float(floor_aav) + (float(ask_aav) - float(floor_aav)) * float(target_weight)

    step = max(float(cfg.min_counter_step_abs), float(cfg.min_counter_step_pct_of_market) * float(market_aav))
    counter_aav = max(float(target), float(offer_aav) + float(step))
    counter_aav = min(float(counter_aav), float(ask_aav))
    counter_aav = max(float(counter_aav), float(floor_aav))

    # Years counter:
    offer_years = int(offer.years)
    ideal = int(pos.ideal_years)
    if offer_years < int(pos.min_years) or offer_years > int(pos.max_years):
        counter_years = int(ideal)
    else:
        # If years mismatch is large, snap to ideal.
        if abs(int(offer_years) - int(ideal)) >= 2:
            counter_years = int(ideal)
        else:
            counter_years = int(offer_years)

    start_year = int(offer.start_season_year)
    salary_by_year = {int(start_year + i): round_salary(float(counter_aav), cfg=cfg) for i in range(int(counter_years))}

    # Include required demands as hints to UI (if enabled)
    non_monetary = dict(offer.non_monetary or {})
    if bool(getattr(cfg, "enable_non_monetary_gate", False)) and pos.required_demands:
        non_monetary.setdefault("required_demands", list(pos.required_demands))

    counter_options: list[dict] = []
    for opt in (offer.options or []):
        try:
            oy = int(opt.get("season_year") or 0)
        except Exception:
            continue
        if oy < int(start_year) or oy >= int(start_year) + int(counter_years):
            continue
        counter_options.append(dict(opt))

    return ContractOffer(
        start_season_year=int(start_year),
        years=int(counter_years),
        salary_by_year=salary_by_year,
        options=counter_options,
        non_monetary=non_monetary,
    )


def evaluate_offer(
    session: Mapping[str, Any],
    offer_payload: Mapping[str, Any] | ContractOffer,
    *,
    cfg: ContractNegotiationConfig = DEFAULT_CONTRACT_NEGOTIATION_CONFIG,
) -> NegotiationDecision:
    """Evaluate a team offer and return a structured decision."""
    # Normalize offer
    try:
        offer = offer_payload if isinstance(offer_payload, ContractOffer) else ContractOffer.from_payload(offer_payload)
    except Exception as exc:
        return NegotiationDecision(
            verdict="REJECT",
            reasons=[Reason("OFFER_INVALID", f"Invalid offer payload: {type(exc).__name__}", {})],
            effects={"trust_delta": -0.02},
            tone="FIRM",
            meta={"error": str(exc)},
        )

    # Hard bounds (guardrails)
    if offer.years < int(cfg.min_years_allowed) or offer.years > int(cfg.max_years_allowed):
        return NegotiationDecision(
            verdict="REJECT",
            reasons=[
                Reason(
                    "OFFER_YEARS_OUT_OF_RANGE",
                    "Contract length is outside allowed bounds.",
                    {"years": int(offer.years), "min": int(cfg.min_years_allowed), "max": int(cfg.max_years_allowed)},
                )
            ],
            effects={"trust_delta": -0.01},
            tone="FIRM",
            meta={"offer": offer.to_payload()},
        )

    # Load position
    pos = _pos_from_session(session, cfg=cfg)

    player_snap = session.get("player_snapshot") if isinstance(session.get("player_snapshot"), Mapping) else {}
    mental = player_snap.get("mental") if isinstance(player_snap.get("mental"), Mapping) else {}

    amb = mental_norm(mental, "ambition")
    ego = mental_norm(mental, "ego")
    loy = mental_norm(mental, "loyalty")
    leverage = clamp01(player_snap.get("leverage", 0.5))

    round_i = safe_int(session.get("round"), 0)
    max_rounds = safe_int(session.get("max_rounds"), int(pos.max_rounds))
    if max_rounds <= 0:
        max_rounds = int(pos.max_rounds) if int(pos.max_rounds) > 0 else 4

    offer_aav = float(offer.aav())
    ask_aav = float(pos.ask_aav)
    base_floor = float(pos.floor_aav)

    floor_adj, floor_year_mult = _adjust_floor_for_years(
        base_floor,
        offer_years=int(offer.years),
        ideal_years=int(pos.ideal_years),
        mental=mental,
        cfg=cfg,
    )

    # Option value adjustment (player perspective):
    # - TEAM options are player-unfriendly => increase required floor
    # - PLAYER/ETO options are player-friendly => decrease required floor
    team_opt_count = 0
    player_opt_count = 0
    for opt in (offer.options or []):
        t = str(opt.get("type") or "").strip().upper()
        if t == "TEAM":
            team_opt_count += 1
        elif t in {"PLAYER", "ETO"}:
            player_opt_count += 1
    opt_delta = (
        float(team_opt_count) * float(getattr(cfg, "team_option_penalty_per_year", 0.0))
        - float(player_opt_count) * float(getattr(cfg, "player_option_bonus_per_year", 0.0))
    )
    opt_cap = abs(float(getattr(cfg, "option_value_cap", 0.10)))
    if opt_delta > opt_cap:
        opt_delta = opt_cap
    if opt_delta < -opt_cap:
        opt_delta = -opt_cap
    floor_adj = float(floor_adj) * (1.0 + float(opt_delta))

    years_ok = int(pos.min_years) <= int(offer.years) <= int(pos.max_years)

    demands_ok = _demands_satisfied(offer, list(pos.required_demands or []))

    meta: Dict[str, Any] = {
        "offer_aav": float(offer_aav),
        "ask_aav": float(ask_aav),
        "floor_aav": float(base_floor),
        "floor_adj_aav": float(floor_adj),
        "floor_year_mult": float(floor_year_mult),
        "team_option_count": int(team_opt_count),
        "player_option_count": int(player_opt_count),
        "option_premium_delta": float(opt_delta),
        "offer_years": int(offer.years),
        "ideal_years": int(pos.ideal_years),
        "min_years": int(pos.min_years),
        "max_years": int(pos.max_years),
        "years_ok": bool(years_ok),
        "demands_ok": bool(demands_ok),
        "round": int(round_i),
        "max_rounds": int(max_rounds),
        "leverage": float(leverage),
        "ambition": float(amb),
        "ego": float(ego),
        "loyalty": float(loy),
    }

    # If non-monetary gate is on and demands are missing, counter even if money is ok.
    if bool(getattr(cfg, "enable_non_monetary_gate", False)) and pos.required_demands and not demands_ok:
        reasons = [
            Reason(
                "MISSING_REQUIRED_PROMISES",
                "Player requires additional assurances to sign.",
                {"required_demands": list(pos.required_demands or [])},
            )
        ]
        # If money is good, keep money and counter with demand hints.
        counter = build_counter_offer(session=session, offer=offer, pos=pos, cfg=cfg)
        return NegotiationDecision(
            verdict="COUNTER",
            reasons=reasons,
            counter_offer=counter,
            effects={"trust_delta": -0.01},
            tone="FIRM",
            meta=meta,
        )

    # If years are outside the player's acceptable range, counter with ideal years.
    if not years_ok:
        counter = build_counter_offer(session=session, offer=offer, pos=pos, cfg=cfg)
        reasons = [
            Reason(
                "YEARS_NOT_ACCEPTABLE",
                "Contract length does not match the player's preference.",
                {"offer_years": int(offer.years), "min": int(pos.min_years), "ideal": int(pos.ideal_years), "max": int(pos.max_years)},
            )
        ]
        return NegotiationDecision(
            verdict="COUNTER",
            reasons=reasons,
            counter_offer=counter,
            effects={"trust_delta": -0.005},
            tone="FIRM",
            meta=meta,
        )

    # ACCEPT if offer meets or beats ask
    if offer_aav >= ask_aav:
        return NegotiationDecision(
            verdict="ACCEPT",
            reasons=[
                Reason(
                    "OFFER_AT_OR_ABOVE_ASK",
                    "Offer meets or exceeds the player's ask.",
                    {"offer_aav": float(offer_aav), "ask_aav": float(ask_aav)},
                )
            ],
            effects={"trust_delta": 0.02 + 0.04 * float(leverage)},
            tone="CALM",
            meta=meta,
        )

    # ACCEPT if offer meets adjusted floor
    if offer_aav >= floor_adj:
        return NegotiationDecision(
            verdict="ACCEPT",
            reasons=[
                Reason(
                    "OFFER_MEETS_FLOOR",
                    "Offer meets the player's minimum threshold.",
                    {"offer_aav": float(offer_aav), "floor_adj_aav": float(floor_adj)},
                )
            ],
            effects={"trust_delta": 0.015 + 0.03 * float(leverage)},
            tone="CALM",
            meta=meta,
        )

    # Below floor -> determine severity
    lowball_strikes = safe_int(session.get("lowball_strikes"), 0)
    insult_threshold = float(floor_adj) * float(pos.insult_ratio)
    is_insulting = offer_aav < insult_threshold

    meta.update(
        {
            "lowball_strikes": int(lowball_strikes),
            "insult_threshold": float(insult_threshold),
            "is_insulting": bool(is_insulting),
        }
    )

    # If offer is insulting, potentially walk out.
    if is_insulting:
        # Escalation depends on ego/leverage and repeated insults.
        new_strikes = int(lowball_strikes) + 1
        meta["new_lowball_strikes"] = int(new_strikes)

        # Deterministic 'temper' roll for borderline walkouts.
        # Higher ego/leverage reduces tolerance.
        temper = stable_u01("contract", session.get("session_id"), player_snap.get("player_id"), round_i, int(offer_aav))
        walk_bias = 0.15 + 0.55 * float(ego) + 0.30 * float(leverage)
        walk_bias = clamp01(walk_bias)

        should_walk = bool(new_strikes >= int(cfg.lowball_strikes_to_walk) or temper < walk_bias * 0.25)

        if should_walk:
            return NegotiationDecision(
                verdict="WALK",
                reasons=[
                    Reason(
                        "INSULTING_OFFER",
                        "Player felt disrespected by the offer and walked away.",
                        {"offer_aav": float(offer_aav), "insult_threshold": float(insult_threshold), "strikes": int(new_strikes)},
                    )
                ],
                effects={"trust_delta": -0.06 - 0.08 * float(leverage)},
                tone="ANGRY" if ego >= 0.60 else "FIRM",
                meta=meta,
            )

        # Otherwise, reject but keep negotiating.
        return NegotiationDecision(
            verdict="REJECT",
            reasons=[
                Reason(
                    "OFFER_TOO_LOW",
                    "Offer is significantly below the player's minimum.",
                    {"offer_aav": float(offer_aav), "floor_adj_aav": float(floor_adj), "insult_threshold": float(insult_threshold)},
                )
            ],
            effects={"trust_delta": -0.02 - 0.03 * float(ego)},
            tone="FIRM",
            meta=meta,
        )

    # Not insulting: counter if close enough, else reject.
    if offer_aav >= float(floor_adj) * float(1.0 - float(cfg.counter_near_margin)):
        counter = build_counter_offer(session=session, offer=offer, pos=pos, cfg=cfg)
        return NegotiationDecision(
            verdict="COUNTER",
            reasons=[
                Reason(
                    "NEAR_FLOOR_COUNTER",
                    "Offer is close, but the player wants a bit more.",
                    {"offer_aav": float(offer_aav), "floor_adj_aav": float(floor_adj)},
                )
            ],
            counter_offer=counter,
            effects={"trust_delta": -0.005},
            tone="FIRM",
            meta=meta,
        )

    # If out of rounds, walk (optional).
    if bool(getattr(cfg, "walk_if_round_exceeded", True)) and (int(round_i) + 1) >= int(max_rounds):
        return NegotiationDecision(
            verdict="WALK",
            reasons=[
                Reason(
                    "NEGOTIATION_DEADLOCK",
                    "Negotiation ran out of rounds without reaching a deal.",
                    {"round": int(round_i), "max_rounds": int(max_rounds)},
                )
            ],
            effects={"trust_delta": -0.04 - 0.05 * float(leverage)},
            tone="FIRM",
            meta=meta,
        )

    return NegotiationDecision(
        verdict="REJECT",
        reasons=[
            Reason(
                "OFFER_BELOW_FLOOR",
                "Offer is below the player's minimum threshold.",
                {"offer_aav": float(offer_aav), "floor_adj_aav": float(floor_adj)},
            )
        ],
        effects={"trust_delta": -0.012},
        tone="FIRM",
        meta=meta,
    )
