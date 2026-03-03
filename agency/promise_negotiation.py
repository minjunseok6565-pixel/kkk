from __future__ import annotations

"""Promise offer negotiation (ACCEPT / COUNTER / REJECT) for player agency.

Design
------
This module implements the missing FM-style layer between:
- a player issue event ("I'm unhappy")
- the user's response ("I promise X")

In commercial-quality agency systems, a promise should *not* always work.
Players decide whether to accept a promise offer based on:
- their self expectations (self-perception of value)
- credibility (trustworthiness of the manager for this promise type)
- their mental traits and current stances

We intentionally keep this module:
- deterministic
- DB-free
- generic across promise types

It is designed to be called from:
- agency/responses.py (reactive events)
- agency/user_actions.py (proactive offers)

SSOT policy
-----------
Only *accepted* promises should be written to player_agency_promises.
COUNTER/REJECT outcomes are represented as follow-up agency_events
(PROMISE_NEGOTIATION) and stored in agency_events (append-only SSOT).

"""

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple

from .config import AgencyConfig
from .credibility import compute_credibility
from .utils import clamp, clamp01, make_event_id, mental_norm, safe_float, safe_float_opt
from .behavior_profile import compute_behavior_profile, suggest_negotiation_max_rounds


Verdict = Literal["ACCEPT", "COUNTER", "REJECT", "WALKOUT"]


@dataclass(frozen=True, slots=True)
class Offer:
    """A promise offer proposed by the user."""

    promise_type: str
    axis: str

    # Promise due month key (YYYY-MM). Optional but useful for time-based promises.
    due_month: Optional[str] = None

    # Some promises are scalar.
    target_value: Optional[float] = None

    # Extra structured target/context.
    target_json: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "promise_type": str(self.promise_type),
            "axis": str(self.axis),
            "due_month": self.due_month,
            "target_value": None if self.target_value is None else float(self.target_value),
            "target_json": dict(self.target_json or {}),
        }


@dataclass(frozen=True, slots=True)
class Decision:
    verdict: Verdict

    # Player standard for acceptance.
    ask: Any
    floor: Any

    # A concrete counter offer (when verdict=COUNTER)
    counter_offer: Optional[Offer] = None

    # Whether the offer was perceived as insulting.
    insulting: bool = False

    round_index: int = 0
    max_rounds: int = 1

    credibility: float = 0.5

    reasons: Tuple[str, ...] = ()
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": str(self.verdict),
            "ask": self.ask,
            "floor": self.floor,
            "counter_offer": None if self.counter_offer is None else self.counter_offer.to_dict(),
            "insulting": bool(self.insulting),
            "round_index": int(self.round_index),
            "max_rounds": int(self.max_rounds),
            "credibility": float(self.credibility),
            "reasons": list(self.reasons),
            "meta": dict(self.meta or {}),
        }


@dataclass(frozen=True, slots=True)
class _NegotiationDefaults:
    max_rounds_base: int = 2
    expire_months: int = 1

    minutes_tol_base: float = 2.0
    minutes_tol_max: float = 6.0
    minutes_insult_extra: float = 3.0
    minutes_cred_bump_max: float = 4.0

    role_tol_base: float = 0.10
    role_tol_max: float = 0.30
    role_insult_extra: float = 0.18
    role_cred_bump_max: float = 0.20

    help_accept_min_match: int = 2
    help_counter_min_match: int = 1
    help_reject_min_match: int = 0

    ext_due_by_years_left: Dict[int, int] = field(default_factory=lambda: {0: 1, 1: 2, 2: 3})
    ext_tol_months: int = 1


def _get_neg_cfg(cfg: AgencyConfig) -> Any:
    # Planned name: cfg.negotiation
    if hasattr(cfg, "negotiation"):
        return getattr(cfg, "negotiation")
    return _NegotiationDefaults()


def _get_state_value(state: Any, key: str, default: Any = None) -> Any:
    if isinstance(state, Mapping):
        return state.get(key, default)
    return getattr(state, key, default)


def _month_key_from_date(now_date_iso: Any) -> Optional[str]:
    s = str(now_date_iso or "").strip()
    if len(s) < 7:
        return None
    mk = s[:7]
    try:
        y, m = mk.split("-")
        _dt.date(int(y), int(m), 1)
    except Exception:
        return None
    return f"{int(y):04d}-{int(m):02d}"


def _add_months(month_key: str, months: int) -> Optional[str]:
    mk = str(month_key or "").strip()[:7]
    try:
        y, m = mk.split("-")
        y = int(y)
        m = int(m)
        base = (y * 12) + (m - 1)
        out = base + int(months)
        oy = out // 12
        om = (out % 12) + 1
        return f"{oy:04d}-{om:02d}"
    except Exception:
        return None


def _month_diff(from_month: str, to_month: str) -> Optional[int]:
    try:
        y1, m1 = str(from_month).strip()[:7].split("-")
        y2, m2 = str(to_month).strip()[:7].split("-")
        a = (int(y1) * 12) + (int(m1) - 1)
        b = (int(y2) * 12) + (int(m2) - 1)
        return int(b - a)
    except Exception:
        return None


def _resolve_max_rounds(
    *,
    state: Any,
    mental: Mapping[str, Any],
    cfg: AgencyConfig,
    max_rounds: Optional[int],
) -> int:
    neg = _get_neg_cfg(cfg)
    base = int(getattr(neg, "max_rounds_base", 2) or 2)
    if max_rounds is not None and int(max_rounds) > 0:
        return int(max_rounds)

    trust = float(clamp01(_get_state_value(state, "trust", 0.5)))
    rs = float(clamp01(_get_state_value(state, "stance_resentment", 0.0)))
    return int(suggest_negotiation_max_rounds(base_rounds=base, mental=mental, trust=trust, stance_resentment=rs))


def evaluate_offer(
    *,
    state: Any,
    mental: Mapping[str, Any],
    offer: Offer,
    round_index: int,
    max_rounds: Optional[int],
    cfg: AgencyConfig,
) -> Decision:
    """Evaluate an offer and return a Decision.

    Args:
        state: player agency state mapping/dataclass.
        mental: player mental traits.
        offer: user's offer.
        round_index: 0-based negotiation round.
        max_rounds: max rounds allowed; if None, suggested from mental+stance.
        cfg: AgencyConfig.

    Returns:
        Decision.
    """
    # credibility is promise-type-specific.
    cred, cred_meta = compute_credibility(state=state, mental=mental, promise_type=offer.promise_type, cfg=cfg)

    mr = _resolve_max_rounds(state=state, mental=mental, cfg=cfg, max_rounds=max_rounds)

    # Behavioral profile can be useful for meta, but we only use stance values
    # directly for core math.
    trust = float(clamp01(_get_state_value(state, "trust", 0.5)))
    sk = float(clamp01(_get_state_value(state, "stance_skepticism", 0.0)))
    rs = float(clamp01(_get_state_value(state, "stance_resentment", 0.0)))
    hb = float(clamp01(_get_state_value(state, "stance_hardball", 0.0)))

    prof, prof_meta = compute_behavior_profile(
        mental=mental,
        trust=trust,
        stance_skepticism=sk,
        stance_resentment=rs,
        stance_hardball=hb,
    )

    neg = _get_neg_cfg(cfg)

    # Dispatch by promise type.
    pt = str(offer.promise_type or "").upper()
    if pt == "MINUTES":
        dec = _eval_minutes(state, mental, offer, round_index, mr, cred, cfg, neg)
    elif pt == "ROLE":
        dec = _eval_role(state, mental, offer, round_index, mr, cred, cfg, neg)
    elif pt == "LOAD":
        dec = _eval_load(state, mental, offer, round_index, mr, cred, cfg, neg)
    elif pt == "HELP":
        dec = _eval_help(state, mental, offer, round_index, mr, cred, cfg, neg)
    elif pt == "EXTENSION_TALKS":
        dec = _eval_extension_talks(state, mental, offer, round_index, mr, cred, cfg, neg)
    else:
        # Unknown => be conservative: accept only if offer looks reasonable.
        dec = Decision(
            verdict="COUNTER",
            ask=offer.target_value,
            floor=offer.target_value,
            counter_offer=None,
            insulting=False,
            round_index=int(round_index),
            max_rounds=int(mr),
            credibility=float(cred),
            reasons=("unknown_promise_type",),
            meta={"credibility": cred_meta, "profile": prof_meta},
        )

    # Add shared meta context.
    meta = dict(dec.meta or {})
    meta.setdefault("credibility", cred_meta)
    meta.setdefault("behavior_profile", prof_meta)

    # Ensure reasoned consistency on last round: avoid endless COUNTER loops.
    if dec.verdict == "COUNTER" and int(round_index) >= int(mr) - 1:
        # If we can't counter further, convert to REJECT.
        meta["counter_blocked"] = True
        return Decision(
            verdict="REJECT",
            ask=dec.ask,
            floor=dec.floor,
            counter_offer=None,
            insulting=bool(dec.insulting),
            round_index=int(round_index),
            max_rounds=int(mr),
            credibility=float(cred),
            reasons=tuple(list(dec.reasons) + ["max_rounds_reached"]),
            meta=meta,
        )

    return Decision(
        verdict=dec.verdict,
        ask=dec.ask,
        floor=dec.floor,
        counter_offer=dec.counter_offer,
        insulting=bool(dec.insulting),
        round_index=int(round_index),
        max_rounds=int(mr),
        credibility=float(cred),
        reasons=dec.reasons,
        meta=meta,
    )


# ---------------------------------------------------------------------------
# Type-specific evaluation
# ---------------------------------------------------------------------------


def _eval_minutes(
    state: Any,
    mental: Mapping[str, Any],
    offer: Offer,
    round_index: int,
    max_rounds: int,
    cred: float,
    cfg: AgencyConfig,
    neg: Any,
) -> Decision:
    # Offer value
    offered = offer.target_value
    if offered is None:
        offered = safe_float_opt((offer.target_json or {}).get("target_mpg"))
    offered_f = float(max(0.0, safe_float(offered, 0.0)))

    # Ask uses self expectation when available.
    ask = safe_float_opt(_get_state_value(state, "self_expected_mpg", None))
    if ask is None:
        ask = safe_float_opt(_get_state_value(state, "minutes_expected_mpg", None))
    if ask is None:
        ask = safe_float_opt((offer.target_json or {}).get("ask_mpg"))
    ask_f = float(max(0.0, safe_float(ask, max(0.0, offered_f))))

    # Mental / stance
    work = mental_norm(mental, "work_ethic")
    coach = mental_norm(mental, "coachability")
    amb = mental_norm(mental, "ambition")
    loy = mental_norm(mental, "loyalty")
    ego = mental_norm(mental, "ego")
    adapt = mental_norm(mental, "adaptability")

    hb = float(clamp01(_get_state_value(state, "stance_hardball", 0.0)))

    tol_base = float(getattr(neg, "minutes_tol_base", 2.0))
    tol_max = float(getattr(neg, "minutes_tol_max", 6.0))
    tol = (
        tol_base
        + 2.0 * coach
        + 2.0 * adapt
        + 1.5 * loy
        + 1.5 * work
        - 2.0 * ego
        - 1.5 * amb
        - 2.0 * hb
    )
    tol = float(clamp(tol, 1.0, tol_max))

    bump_max = float(getattr(neg, "minutes_cred_bump_max", 4.0))
    ask_eff = ask_f + (1.0 - float(cred)) * bump_max * (0.50 + 0.50 * hb)

    # Credibility reduces tolerance ("I'll believe it when I see it").
    tol_eff = tol * (0.60 + 0.40 * float(cred))
    floor = ask_eff - tol_eff

    # If credibility is *very* low, require over-delivery for immediate acceptance.
    min_accept_cred = 0.20
    try:
        # Try to read from cfg.credibility or cfg.cred
        if hasattr(cfg, "credibility"):
            min_accept_cred = float(getattr(getattr(cfg, "credibility"), "min_accept_cred", 0.20))
        elif hasattr(cfg, "cred"):
            min_accept_cred = float(getattr(getattr(cfg, "cred"), "min_accept_cred", 0.20))
    except Exception:
        min_accept_cred = 0.20

    accept_target = float(ask_eff)
    if float(cred) < float(min_accept_cred):
        # Demand a stronger concession when you don't believe the manager.
        extra = (float(min_accept_cred) - float(cred)) * bump_max * (0.50 + 0.60 * hb)
        accept_target = float(ask_eff + extra)

    insult_extra = float(getattr(neg, "minutes_insult_extra", 3.0))
    insulting = bool(offered_f < (floor - (insult_extra * (0.70 + 0.60 * ego))))

    reasons: List[str] = []

    if offered_f >= accept_target:
        reasons.append("meets_or_exceeds_ask")
        return Decision(
            verdict="ACCEPT",
            ask=float(accept_target),
            floor=float(floor),
            counter_offer=None,
            insulting=bool(insulting),
            round_index=int(round_index),
            max_rounds=int(max_rounds),
            credibility=float(cred),
            reasons=tuple(reasons),
            meta={
                "type": "MINUTES",
                "offered_mpg": float(offered_f),
                "ask_mpg": float(ask_f),
                "ask_eff": float(ask_eff),
                "accept_target": float(accept_target),
                "tolerance": float(tol),
                "tolerance_eff": float(tol_eff),
                "floor": float(floor),
                "insulting": bool(insulting),
                "mental": {
                    "work_ethic": float(work),
                    "coachability": float(coach),
                    "ambition": float(amb),
                    "loyalty": float(loy),
                    "ego": float(ego),
                    "adaptability": float(adapt),
                },
                "stance_hardball": float(hb),
            },
        )

    if offered_f >= floor:
        # Negotiable zone.
        reasons.append("within_compromise_band")
        counter_target = float(accept_target)
        # Round to a clean number for UX.
        counter_target = round(counter_target * 2.0) / 2.0
        co = Offer(
            promise_type=offer.promise_type,
            axis=offer.axis,
            due_month=offer.due_month,
            target_value=float(counter_target),
            target_json={"target_mpg": float(counter_target)},
        )
        return Decision(
            verdict="COUNTER",
            ask=float(accept_target),
            floor=float(floor),
            counter_offer=co,
            insulting=bool(insulting),
            round_index=int(round_index),
            max_rounds=int(max_rounds),
            credibility=float(cred),
            reasons=tuple(reasons),
            meta={
                "type": "MINUTES",
                "offered_mpg": float(offered_f),
                "ask_mpg": float(ask_f),
                "ask_eff": float(ask_eff),
                "accept_target": float(accept_target),
                "tolerance": float(tol),
                "tolerance_eff": float(tol_eff),
                "floor": float(floor),
                "counter_target": float(counter_target),
                "insulting": bool(insulting),
            },
        )

    # Too low.
    reasons.append("below_floor")
    if insulting:
        reasons.append("insulting_offer")
        return Decision(
            verdict="WALKOUT",
            ask=float(accept_target),
            floor=float(floor),
            counter_offer=None,
            insulting=True,
            round_index=int(round_index),
            max_rounds=int(max_rounds),
            credibility=float(cred),
            reasons=tuple(reasons),
            meta={
                "type": "MINUTES",
                "offered_mpg": float(offered_f),
                "ask_eff": float(ask_eff),
                "accept_target": float(accept_target),
                "floor": float(floor),
                "insulting": True,
            },
        )

    return Decision(
        verdict="REJECT",
        ask=float(accept_target),
        floor=float(floor),
        counter_offer=None,
        insulting=False,
        round_index=int(round_index),
        max_rounds=int(max_rounds),
        credibility=float(cred),
        reasons=tuple(reasons),
        meta={
            "type": "MINUTES",
            "offered_mpg": float(offered_f),
            "ask_eff": float(ask_eff),
            "accept_target": float(accept_target),
            "floor": float(floor),
        },
    )


def _eval_role(
    state: Any,
    mental: Mapping[str, Any],
    offer: Offer,
    round_index: int,
    max_rounds: int,
    cred: float,
    cfg: AgencyConfig,
    neg: Any,
) -> Decision:
    # Focus: STARTS or CLOSES.
    tj = offer.target_json or {}
    focus = str(tj.get("role_focus") or tj.get("focus") or "STARTS").upper()
    if focus not in ("STARTS", "CLOSES"):
        focus = "STARTS"

    # Offered value
    if focus == "STARTS":
        offered = safe_float_opt(tj.get("min_starts_rate"))
    else:
        offered = safe_float_opt(tj.get("min_closes_rate"))

    # If role label was provided instead of explicit rates, derive a conservative mapping.
    if offered is None:
        role_label = str(tj.get("role") or tj.get("desired_role") or "").upper()
        if role_label == "STARTER":
            offered = 0.70 if focus == "STARTS" else 0.35
        elif role_label == "CLOSER":
            offered = 0.45 if focus == "STARTS" else 0.55
        elif role_label == "SIXTH_MAN":
            offered = 0.30 if focus == "STARTS" else 0.10
        else:
            offered = 0.40 if focus == "STARTS" else 0.20

    offered_f = float(clamp01(safe_float(offered, 0.0)))

    # Ask uses self expectations.
    if focus == "STARTS":
        ask = safe_float_opt(_get_state_value(state, "self_expected_starts_rate", None))
    else:
        ask = safe_float_opt(_get_state_value(state, "self_expected_closes_rate", None))

    if ask is None:
        # Fallback to a neutral ask.
        ask = 0.50 if focus == "STARTS" else 0.25

    ask_f = float(clamp01(safe_float(ask, offered_f)))

    work = mental_norm(mental, "work_ethic")
    coach = mental_norm(mental, "coachability")
    amb = mental_norm(mental, "ambition")
    loy = mental_norm(mental, "loyalty")
    ego = mental_norm(mental, "ego")
    adapt = mental_norm(mental, "adaptability")

    hb = float(clamp01(_get_state_value(state, "stance_hardball", 0.0)))

    tol_base = float(getattr(neg, "role_tol_base", 0.10))
    tol_max = float(getattr(neg, "role_tol_max", 0.30))
    tol = (
        tol_base
        + 0.20 * coach
        + 0.18 * adapt
        + 0.12 * loy
        + 0.10 * work
        - 0.20 * ego
        - 0.15 * amb
        - 0.20 * hb
    )
    tol = float(clamp(tol, 0.05, tol_max))

    bump_max = float(getattr(neg, "role_cred_bump_max", 0.20))
    ask_eff = ask_f + (1.0 - float(cred)) * bump_max * (0.50 + 0.50 * hb)

    tol_eff = tol * (0.60 + 0.40 * float(cred))
    floor = ask_eff - tol_eff

    # Accept gate when credibility is extremely low.
    min_accept_cred = 0.20
    try:
        if hasattr(cfg, "credibility"):
            min_accept_cred = float(getattr(getattr(cfg, "credibility"), "min_accept_cred", 0.20))
        elif hasattr(cfg, "cred"):
            min_accept_cred = float(getattr(getattr(cfg, "cred"), "min_accept_cred", 0.20))
    except Exception:
        min_accept_cred = 0.20

    accept_target = float(ask_eff)
    if float(cred) < float(min_accept_cred):
        extra = (float(min_accept_cred) - float(cred)) * bump_max * (0.50 + 0.60 * hb)
        accept_target = float(ask_eff + extra)

    insult_extra = float(getattr(neg, "role_insult_extra", 0.18))
    insulting = bool(offered_f < (floor - (insult_extra * (0.70 + 0.60 * ego))))

    reasons: List[str] = [f"focus_{focus.lower()}"]

    if offered_f >= accept_target:
        reasons.append("meets_or_exceeds_ask")
        return Decision(
            verdict="ACCEPT",
            ask=float(accept_target),
            floor=float(floor),
            counter_offer=None,
            insulting=bool(insulting),
            round_index=int(round_index),
            max_rounds=int(max_rounds),
            credibility=float(cred),
            reasons=tuple(reasons),
            meta={
                "type": "ROLE",
                "focus": focus,
                "offered_rate": float(offered_f),
                "ask_rate": float(ask_f),
                "ask_eff": float(ask_eff),
                "accept_target": float(accept_target),
                "tolerance": float(tol),
                "tolerance_eff": float(tol_eff),
                "floor": float(floor),
                "insulting": bool(insulting),
            },
        )

    if offered_f >= floor:
        reasons.append("within_compromise_band")
        counter_target = float(accept_target)
        # Round to 2 decimals for UI.
        counter_target = round(counter_target, 2)
        if focus == "STARTS":
            counter_json = {
                "role": str(tj.get("role") or tj.get("desired_role") or "STARTER").upper(),
                "min_starts_rate": float(counter_target),
                "role_focus": "STARTS",
            }
        else:
            counter_json = {
                "role": str(tj.get("role") or tj.get("desired_role") or "CLOSER").upper(),
                "min_closes_rate": float(counter_target),
                "role_focus": "CLOSES",
            }
        co = Offer(
            promise_type=offer.promise_type,
            axis=offer.axis,
            due_month=offer.due_month,
            target_value=None,
            target_json=counter_json,
        )
        return Decision(
            verdict="COUNTER",
            ask=float(accept_target),
            floor=float(floor),
            counter_offer=co,
            insulting=bool(insulting),
            round_index=int(round_index),
            max_rounds=int(max_rounds),
            credibility=float(cred),
            reasons=tuple(reasons),
            meta={
                "type": "ROLE",
                "focus": focus,
                "offered_rate": float(offered_f),
                "ask_eff": float(ask_eff),
                "accept_target": float(accept_target),
                "tolerance": float(tol),
                "tolerance_eff": float(tol_eff),
                "floor": float(floor),
                "counter_target": float(counter_target),
                "insulting": bool(insulting),
            },
        )

    reasons.append("below_floor")
    if insulting:
        reasons.append("insulting_offer")
        return Decision(
            verdict="WALKOUT",
            ask=float(accept_target),
            floor=float(floor),
            counter_offer=None,
            insulting=True,
            round_index=int(round_index),
            max_rounds=int(max_rounds),
            credibility=float(cred),
            reasons=tuple(reasons),
            meta={
                "type": "ROLE",
                "focus": focus,
                "offered_rate": float(offered_f),
                "accept_target": float(accept_target),
                "floor": float(floor),
            },
        )

    return Decision(
        verdict="REJECT",
        ask=float(accept_target),
        floor=float(floor),
        counter_offer=None,
        insulting=False,
        round_index=int(round_index),
        max_rounds=int(max_rounds),
        credibility=float(cred),
        reasons=tuple(reasons),
        meta={
            "type": "ROLE",
            "focus": focus,
            "offered_rate": float(offered_f),
            "accept_target": float(accept_target),
            "floor": float(floor),
        },
    )


def _eval_load(
    state: Any,
    mental: Mapping[str, Any],
    offer: Offer,
    round_index: int,
    max_rounds: int,
    cred: float,
    cfg: AgencyConfig,
    neg: Any,
) -> Decision:
    # LOAD is inverted: the player wants a *maximum* MPG cap.
    tj = offer.target_json or {}

    offered = offer.target_value
    if offered is None:
        offered = safe_float_opt(tj.get("max_mpg"))
    offered_f = float(max(0.0, safe_float(offered, 0.0)))

    ask = safe_float_opt(tj.get("ask_max_mpg"))
    if ask is None:
        ask = safe_float_opt(_get_state_value(state, "self_expected_mpg", None))
    if ask is None:
        ask = offered_f

    ask_f = float(max(0.0, safe_float(ask, offered_f)))

    work = mental_norm(mental, "work_ethic")
    coach = mental_norm(mental, "coachability")
    amb = mental_norm(mental, "ambition")
    loy = mental_norm(mental, "loyalty")
    ego = mental_norm(mental, "ego")
    adapt = mental_norm(mental, "adaptability")

    hb = float(clamp01(_get_state_value(state, "stance_hardball", 0.0)))

    # Tolerance in minutes above the desired cap.
    tol = (
        1.0
        + 1.5 * coach
        + 1.0 * adapt
        + 1.0 * work
        + 0.5 * loy
        - 1.2 * ego
        - 1.0 * amb
        - 1.0 * hb
    )
    tol = float(clamp(tol, 0.5, 5.0))

    bump_max = float(getattr(neg, "minutes_cred_bump_max", 4.0))
    # Lower credibility => demand a *stricter* cap (lower ask).
    ask_eff = max(0.0, ask_f - (1.0 - float(cred)) * (0.50 * bump_max) * (0.35 + 0.65 * hb))

    tol_eff = tol * (0.60 + 0.40 * float(cred))
    ceiling = ask_eff + tol_eff

    insult_extra = float(getattr(neg, "minutes_insult_extra", 3.0))
    insulting = bool(offered_f > (ceiling + (insult_extra * (0.50 + 0.60 * ego))))

    reasons: List[str] = []

    if offered_f <= ask_eff:
        reasons.append("cap_meets_or_stricter")
        return Decision(
            verdict="ACCEPT",
            ask=float(ask_eff),
            floor=float(ceiling),
            counter_offer=None,
            insulting=bool(insulting),
            round_index=int(round_index),
            max_rounds=int(max_rounds),
            credibility=float(cred),
            reasons=tuple(reasons),
            meta={
                "type": "LOAD",
                "offered_max_mpg": float(offered_f),
                "ask_max_mpg": float(ask_f),
                "ask_eff": float(ask_eff),
                "tolerance": float(tol),
                "tolerance_eff": float(tol_eff),
                "ceiling": float(ceiling),
                "insulting": bool(insulting),
            },
        )

    if offered_f <= ceiling:
        reasons.append("within_compromise_band")
        counter_target = round(float(ask_eff) * 2.0) / 2.0
        co = Offer(
            promise_type=offer.promise_type,
            axis=offer.axis,
            due_month=offer.due_month,
            target_value=float(counter_target),
            target_json={"max_mpg": float(counter_target), "ask_max_mpg": float(ask_f)},
        )
        return Decision(
            verdict="COUNTER",
            ask=float(ask_eff),
            floor=float(ceiling),
            counter_offer=co,
            insulting=bool(insulting),
            round_index=int(round_index),
            max_rounds=int(max_rounds),
            credibility=float(cred),
            reasons=tuple(reasons),
            meta={
                "type": "LOAD",
                "offered_max_mpg": float(offered_f),
                "ask_eff": float(ask_eff),
                "ceiling": float(ceiling),
                "counter_target": float(counter_target),
                "insulting": bool(insulting),
            },
        )

    reasons.append("above_ceiling")
    if insulting:
        reasons.append("insulting_offer")
        return Decision(
            verdict="WALKOUT",
            ask=float(ask_eff),
            floor=float(ceiling),
            counter_offer=None,
            insulting=True,
            round_index=int(round_index),
            max_rounds=int(max_rounds),
            credibility=float(cred),
            reasons=tuple(reasons),
            meta={
                "type": "LOAD",
                "offered_max_mpg": float(offered_f),
                "ask_eff": float(ask_eff),
                "ceiling": float(ceiling),
            },
        )

    return Decision(
        verdict="REJECT",
        ask=float(ask_eff),
        floor=float(ceiling),
        counter_offer=None,
        insulting=False,
        round_index=int(round_index),
        max_rounds=int(max_rounds),
        credibility=float(cred),
        reasons=tuple(reasons),
        meta={
            "type": "LOAD",
            "offered_max_mpg": float(offered_f),
            "ask_eff": float(ask_eff),
            "ceiling": float(ceiling),
        },
    )


def _eval_help(
    state: Any,
    mental: Mapping[str, Any],
    offer: Offer,
    round_index: int,
    max_rounds: int,
    cred: float,
    cfg: AgencyConfig,
    neg: Any,
) -> Decision:
    tj = offer.target_json or {}

    # Ask tags: what the player wants.
    ask_tags_raw = tj.get("ask_need_tags") or tj.get("player_need_tags") or tj.get("need_tags_ask")
    if ask_tags_raw is None:
        # If not provided, fall back to whatever is in event payload.
        ask_tags_raw = tj.get("need_tags") or []

    offer_tags_raw = tj.get("offer_need_tags") or tj.get("need_tags_offer")
    if offer_tags_raw is None:
        # If not provided, use provided need_tags as the offer.
        offer_tags_raw = tj.get("need_tags") or []

    def _norm_tags(x: Any) -> List[str]:
        if not isinstance(x, list):
            return []
        out: List[str] = []
        for t in x:
            s = str(t or "").strip().upper()
            if not s:
                continue
            if s not in out:
                out.append(s)
        return out

    ask_tags = _norm_tags(ask_tags_raw)
    offer_tags = _norm_tags(offer_tags_raw)

    # Count matches.
    ask_set = set(ask_tags)
    match = sum(1 for t in offer_tags if t in ask_set)

    accept_min = int(getattr(neg, "help_accept_min_match", 2) or 2)
    counter_min = int(getattr(neg, "help_counter_min_match", 1) or 1)

    # Low credibility => require more concrete alignment.
    if float(cred) < 0.25:
        accept_min = min(len(ask_tags), max(accept_min, 2))

    ego = mental_norm(mental, "ego")

    insulting = bool(match <= 0 and float(cred) < 0.25 and ego > 0.70)

    reasons: List[str] = []
    reasons.append(f"match_{match}")

    if match >= accept_min:
        reasons.append("sufficient_tag_match")
        return Decision(
            verdict="ACCEPT",
            ask=list(ask_tags),
            floor=list(ask_tags),
            counter_offer=None,
            insulting=bool(insulting),
            round_index=int(round_index),
            max_rounds=int(max_rounds),
            credibility=float(cred),
            reasons=tuple(reasons),
            meta={
                "type": "HELP",
                "ask_tags": list(ask_tags),
                "offer_tags": list(offer_tags),
                "match": int(match),
                "accept_min": int(accept_min),
                "cred": float(cred),
                "insulting": bool(insulting),
            },
        )

    if match >= counter_min:
        reasons.append("partial_match_counter")
        # Counter with the player's top ask tags.
        counter_tags = ask_tags[: max(1, min(3, len(ask_tags)))]
        co = Offer(
            promise_type=offer.promise_type,
            axis=offer.axis,
            due_month=offer.due_month,
            target_value=None,
            target_json={
                "offer_need_tags": list(counter_tags),
                "ask_need_tags": list(ask_tags),
                "need_tags": list(counter_tags),
            },
        )
        return Decision(
            verdict="COUNTER",
            ask=list(ask_tags),
            floor=list(ask_tags),
            counter_offer=co,
            insulting=bool(insulting),
            round_index=int(round_index),
            max_rounds=int(max_rounds),
            credibility=float(cred),
            reasons=tuple(reasons),
            meta={
                "type": "HELP",
                "ask_tags": list(ask_tags),
                "offer_tags": list(offer_tags),
                "match": int(match),
                "accept_min": int(accept_min),
                "counter_tags": list(counter_tags),
                "cred": float(cred),
                "insulting": bool(insulting),
            },
        )

    reasons.append("no_match_reject")
    if insulting:
        reasons.append("insulting_offer")
        return Decision(
            verdict="WALKOUT",
            ask=list(ask_tags),
            floor=list(ask_tags),
            counter_offer=None,
            insulting=True,
            round_index=int(round_index),
            max_rounds=int(max_rounds),
            credibility=float(cred),
            reasons=tuple(reasons),
            meta={
                "type": "HELP",
                "ask_tags": list(ask_tags),
                "offer_tags": list(offer_tags),
                "match": int(match),
                "cred": float(cred),
                "insulting": True,
            },
        )

    return Decision(
        verdict="REJECT",
        ask=list(ask_tags),
        floor=list(ask_tags),
        counter_offer=None,
        insulting=False,
        round_index=int(round_index),
        max_rounds=int(max_rounds),
        credibility=float(cred),
        reasons=tuple(reasons),
        meta={
            "type": "HELP",
            "ask_tags": list(ask_tags),
            "offer_tags": list(offer_tags),
            "match": int(match),
            "cred": float(cred),
        },
    )


def _eval_extension_talks(
    state: Any,
    mental: Mapping[str, Any],
    offer: Offer,
    round_index: int,
    max_rounds: int,
    cred: float,
    cfg: AgencyConfig,
    neg: Any,
) -> Decision:
    tj = offer.target_json or {}

    # Determine years_left (seasons left) if present.
    years_left = tj.get("years_left")
    if years_left is None:
        years_left = tj.get("seasons_left")
    if years_left is None:
        years_left = tj.get("contract_seasons_left")

    try:
        yl = int(years_left)
    except Exception:
        yl = 1

    due_map = getattr(neg, "ext_due_by_years_left", {0: 1, 1: 2, 2: 3})
    try:
        ask_due = int(due_map.get(int(yl), 2))
    except Exception:
        ask_due = 2

    tol_m = int(getattr(neg, "ext_tol_months", 1) or 1)

    # Low credibility => demand earlier talks.
    hb = float(clamp01(_get_state_value(state, "stance_hardball", 0.0)))
    tighten = int(round((1.0 - float(cred)) * (0.50 + 0.60 * hb)))
    ask_eff = max(0, ask_due - tighten)

    now_mk = tj.get("now_month_key") or tj.get("month_key") or _month_key_from_date(tj.get("now_date_iso"))
    if not now_mk:
        now_mk = _month_key_from_date(_get_state_value(state, "last_processed_month", ""))

    offered_month = offer.due_month or tj.get("due_month")
    offered_in = None
    if now_mk and offered_month:
        offered_in = _month_diff(str(now_mk), str(offered_month))

    if offered_in is None:
        # If we cannot compute the offset, assume it's acceptable but push a counter
        # to collect a concrete timeline.
        offered_in = ask_eff + tol_m

    offered_in = int(offered_in)

    reasons: List[str] = [f"years_left_{yl}"]

    if offered_in <= ask_eff:
        reasons.append("timeline_ok")
        return Decision(
            verdict="ACCEPT",
            ask=int(ask_eff),
            floor=int(ask_eff + tol_m),
            counter_offer=None,
            insulting=False,
            round_index=int(round_index),
            max_rounds=int(max_rounds),
            credibility=float(cred),
            reasons=tuple(reasons),
            meta={
                "type": "EXTENSION_TALKS",
                "years_left": int(yl),
                "ask_due_months": int(ask_due),
                "ask_eff_months": int(ask_eff),
                "offered_in_months": int(offered_in),
                "tol_months": int(tol_m),
                "now_month_key": now_mk,
                "offered_due_month": offered_month,
            },
        )

    if offered_in <= ask_eff + tol_m:
        reasons.append("slightly_late_counter")
        # Counter: ask for earlier due month.
        counter_due = offered_month
        if now_mk:
            counter_due = _add_months(str(now_mk), int(ask_eff))
        co = Offer(
            promise_type=offer.promise_type,
            axis=offer.axis,
            due_month=counter_due,
            target_value=None,
            target_json={"years_left": int(yl), "now_month_key": now_mk},
        )
        return Decision(
            verdict="COUNTER",
            ask=int(ask_eff),
            floor=int(ask_eff + tol_m),
            counter_offer=co,
            insulting=False,
            round_index=int(round_index),
            max_rounds=int(max_rounds),
            credibility=float(cred),
            reasons=tuple(reasons),
            meta={
                "type": "EXTENSION_TALKS",
                "years_left": int(yl),
                "ask_eff_months": int(ask_eff),
                "offered_in_months": int(offered_in),
                "counter_due_month": counter_due,
                "now_month_key": now_mk,
            },
        )

    reasons.append("too_late_reject")
    return Decision(
        verdict="REJECT",
        ask=int(ask_eff),
        floor=int(ask_eff + tol_m),
        counter_offer=None,
        insulting=False,
        round_index=int(round_index),
        max_rounds=int(max_rounds),
        credibility=float(cred),
        reasons=tuple(reasons),
        meta={
            "type": "EXTENSION_TALKS",
            "years_left": int(yl),
            "ask_eff_months": int(ask_eff),
            "offered_in_months": int(offered_in),
            "tol_months": int(tol_m),
            "now_month_key": now_mk,
            "offered_due_month": offered_month,
        },
    )


# ---------------------------------------------------------------------------
# Event building
# ---------------------------------------------------------------------------


def make_thread_id(*parts: Any) -> str:
    """Deterministic thread id for negotiation chains."""
    return make_event_id("agency", "neg_thread", *parts)


def make_negotiation_event(
    *,
    thread_id: str,
    source_event_id: str,
    player_id: str,
    team_id: str,
    season_year: int,
    now_date_iso: str,
    decision: Decision,
    offer: Offer,
    cfg: AgencyConfig,
) -> Dict[str, Any]:
    """Build an AgencyEvent dict for a negotiation outcome.

    The event_id is deterministic for idempotency.

    Payload is designed to be fully self-contained for UI rendering.
    """
    neg = _get_neg_cfg(cfg)

    # Event id stable per thread + round.
    event_id = make_event_id("agency", "negotiation", str(thread_id), str(decision.round_index))

    # Severity for UI/analytics.
    sev = 0.35
    if decision.verdict == "REJECT":
        sev = 0.55
    elif decision.verdict == "WALKOUT":
        sev = 0.75

    # Expiry month key for the negotiation event.
    expire_m = int(getattr(neg, "expire_months", 1) or 1)
    now_mk = _month_key_from_date(now_date_iso)
    expires_mk = _add_months(now_mk, expire_m) if now_mk else None

    payload: Dict[str, Any] = {
        "thread_id": str(thread_id),
        "source_event_id": str(source_event_id),
        "axis": str(offer.axis),
        "promise_type": str(offer.promise_type),
        "verdict": str(decision.verdict),
        "round_index": int(decision.round_index),
        "max_rounds": int(decision.max_rounds),
        "expires_month": expires_mk,
        "credibility": float(decision.credibility),
        "insulting": bool(decision.insulting),
        "offer": offer.to_dict(),
        "decision": decision.to_dict(),
        "reasons": list(decision.reasons),
        "ui": {
            "ask": decision.ask,
            "floor": decision.floor,
            "gm_offer": offer.target_value if offer.target_value is not None else offer.target_json,
            "counter": None if decision.counter_offer is None else decision.counter_offer.to_dict(),
        },
    }

    return {
        "event_id": event_id,
        "player_id": str(player_id),
        "team_id": str(team_id),
        "season_year": int(season_year),
        "date": str(now_date_iso)[:10],
        "event_type": "PROMISE_NEGOTIATION",
        "severity": float(sev),
        "payload": payload,
    }
