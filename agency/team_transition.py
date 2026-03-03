from __future__ import annotations

"""Team transition handling for player agency.

When a monthly tick is evaluated for a player against the team they actually
played for in the processed month, it is possible that the player is on a
different team *now* (roster at tick time). This occurs when the player is
traded (or signed) around the month boundary.

This module provides a *pure*, explainable adjustment that:
- Transfers the player's agency state onto the new team
- Partially resets relationship variables (trust/frustration) in a realistic way
- Emits an optional TEAM_CHANGED event payload for UI/analytics

SSOT safety
-----------
- This module does not write to the DB.
- It never invents roster transactions. It only reconciles state fields.

The service layer is responsible for:
- Choosing when to apply transition (typically after the monthly tick)
- Recomputing expected role/leverage for the new team (next-month context)
- Writing the updated state/event to SSOT tables (player_agency_state/agency_events)
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from .utils import clamp, clamp01, date_add_days, make_event_id, mental_norm, norm_date_iso, safe_float, safe_int


@dataclass(frozen=True, slots=True)
class TransitionConfig:
    """Tunable parameters for team transition behavior."""

    # Trust baseline and reset strength
    baseline_trust: float = 0.50
    baseline_trust_requested_bonus: float = 0.06  # if the player wanted out
    baseline_trust_unwanted_loyalty_penalty: float = 0.10  # scaled by loyalty when trade not requested
    baseline_trust_unwanted_ego_penalty: float = 0.05  # scaled by ego when trade not requested

    trust_reset_base: float = 0.55
    trust_reset_adapt_bonus: float = 0.25

    # Frustration clearing (fraction of existing frustration cleared on team change)
    clear_minutes_base: float = 0.35
    clear_minutes_adapt_bonus: float = 0.20
    clear_minutes_requested_bonus: float = 0.25
    clear_minutes_unwanted_loyalty_penalty: float = 0.20  # reduces clearing when loyalty high and not requested

    clear_team_base: float = 0.45
    clear_team_adapt_bonus: float = 0.20
    clear_team_requested_bonus: float = 0.25
    clear_team_unwanted_loyalty_penalty: float = 0.25

    # Prevent "instant" trade request loops after a move (settling period)
    trade_cooldown_days_after_move: int = 30

    # Event tuning
    event_type: str = "TEAM_CHANGED"
    event_prefix: str = "agency"
    event_severity_base: float = 0.15


DEFAULT_TRANSITION_CONFIG = TransitionConfig()


@dataclass(frozen=True, slots=True)
class TeamTransitionOutcome:
    """Result of applying a team transition."""

    state_after: Dict[str, Any]
    event: Optional[Dict[str, Any]] = None
    meta: Dict[str, Any] = field(default_factory=dict)


def _normalize_team_id(value: Any) -> str:
    s = str(value or "").strip().upper()
    return s


def _infer_requested_trade(*, requested_trade: Optional[bool], trade_request_level_before: Optional[int]) -> bool:
    if requested_trade is not None:
        return bool(requested_trade)
    try:
        lvl = int(trade_request_level_before or 0)
    except Exception:
        lvl = 0
    return lvl > 0


def apply_team_transition(
    state: Mapping[str, Any],
    *,
    player_id: str,
    season_year: int,
    from_team_id: str,
    to_team_id: str,
    month_key: str,
    now_date_iso: str,
    mental: Mapping[str, Any],
    trade_request_level_before: Optional[int] = None,
    requested_trade: Optional[bool] = None,
    split_summary: Optional[Mapping[str, Any]] = None,
    reason: Optional[str] = None,
    cfg: TransitionConfig = DEFAULT_TRANSITION_CONFIG,
) -> TeamTransitionOutcome:
    """Return (state_after, optional event) for a team change.

    Args:
        state: current agency state dict (typically the result of a monthly tick)
        player_id: player identifier
        season_year: league season year
        from_team_id: team id used for month evaluation (where the player played)
        to_team_id: current roster team id (where the player is now)
        month_key: YYYY-MM processed month
        now_date_iso: YYYY-MM-DD (tick timestamp date)
        mental: mapping of mental traits (0..100 or 0..1)
        trade_request_level_before: previous trade request level (0/1/2) if known
        requested_trade: explicit boolean override
        split_summary: optional month split summary dict (for explainability)
        reason: optional reason string from caller (e.g., "POST_MONTH_TRADE")
        cfg: transition config

    Returns:
        TeamTransitionOutcome containing a *new* state dict and an optional event dict.

    Notes:
        This function is deterministic and side-effect free.
    """
    # Normalize identifiers
    pid = str(player_id or "")
    if not pid:
        return TeamTransitionOutcome(state_after=dict(state), event=None, meta={"skipped": True, "reason": "no_player_id"})

    from_tid = _normalize_team_id(from_team_id)
    to_tid = _normalize_team_id(to_team_id)

    nd = norm_date_iso(now_date_iso) or str(now_date_iso)[:10]
    mk = str(month_key or "")[:7]

    # If no actual team change, no-op.
    if from_tid and to_tid and from_tid == to_tid:
        return TeamTransitionOutcome(state_after=dict(state), event=None, meta={"skipped": True, "reason": "same_team"})

    # Copy state (do not mutate input)
    st: Dict[str, Any] = dict(state or {})

    old_team = str(st.get("team_id") or from_tid or "").upper()
    st["team_id"] = to_tid or old_team

    old_trust = float(clamp01(safe_float(st.get("trust"), 0.5)))
    old_m_fr = float(clamp01(safe_float(st.get("minutes_frustration"), 0.0)))
    old_t_fr = float(clamp01(safe_float(st.get("team_frustration"), 0.0)))

    # v3: dynamic stances + self expectations
    old_sk = float(clamp01(safe_float(st.get("stance_skepticism"), 0.0)))
    old_rs = float(clamp01(safe_float(st.get("stance_resentment"), 0.0)))
    old_hb = float(clamp01(safe_float(st.get("stance_hardball"), 0.0)))
    old_self_mpg = st.get("self_expected_mpg")
    old_self_sr = st.get("self_expected_starts_rate")
    old_self_cr = st.get("self_expected_closes_rate")

    lev = float(clamp01(safe_float(st.get("leverage"), 0.0)))

    req = _infer_requested_trade(requested_trade=requested_trade, trade_request_level_before=trade_request_level_before)

    loy = mental_norm(mental, "loyalty")
    ego = mental_norm(mental, "ego")
    adapt = mental_norm(mental, "adaptability")

    # ------------------------------------------------------------------
    # Trust: move toward a baseline suited for this transition
    # ------------------------------------------------------------------
    baseline = float(cfg.baseline_trust)
    if req:
        baseline += float(cfg.baseline_trust_requested_bonus)
    else:
        baseline -= float(cfg.baseline_trust_unwanted_loyalty_penalty) * loy
        baseline -= float(cfg.baseline_trust_unwanted_ego_penalty) * ego
    baseline = float(clamp01(baseline))

    reset_strength = float(clamp01(float(cfg.trust_reset_base) + float(cfg.trust_reset_adapt_bonus) * adapt))
    new_trust = float(clamp01(old_trust * (1.0 - reset_strength) + baseline * reset_strength))
    st["trust"] = float(new_trust)

    # ------------------------------------------------------------------
    # Frustration: partially clears on a new team (but not fully)
    # ------------------------------------------------------------------
    clear_m = float(cfg.clear_minutes_base) + float(cfg.clear_minutes_adapt_bonus) * adapt + (float(cfg.clear_minutes_requested_bonus) if req else 0.0)
    if not req:
        clear_m -= float(cfg.clear_minutes_unwanted_loyalty_penalty) * loy
    clear_m = float(clamp(clear_m, 0.0, 0.85))

    clear_t = float(cfg.clear_team_base) + float(cfg.clear_team_adapt_bonus) * adapt + (float(cfg.clear_team_requested_bonus) if req else 0.0)
    if not req:
        clear_t -= float(cfg.clear_team_unwanted_loyalty_penalty) * loy
    clear_t = float(clamp(clear_t, 0.0, 0.90))

    new_m_fr = float(clamp01(old_m_fr * (1.0 - clear_m)))
    new_t_fr = float(clamp01(old_t_fr * (1.0 - clear_t)))

    st["minutes_frustration"] = float(new_m_fr)
    st["team_frustration"] = float(new_t_fr)

    # Reset trade request level when moving teams (requests are team-specific).
    st["trade_request_level"] = 0

    # v3: stances + self expectations partially reset on a new team.
    # The player gets a "fresh start" effect; requested moves reset more.
    clear_s = 0.20 + 0.40 * adapt + (0.15 if req else 0.0)
    if not req:
        clear_s -= 0.15 * loy
    clear_s = float(clamp(clear_s, 0.0, 0.85))

    new_sk = float(clamp01(old_sk * (1.0 - clear_s)))
    new_rs = float(clamp01(old_rs * (1.0 - clear_s)))
    new_hb = float(clamp01(old_hb * (1.0 - clear_s)))
    st["stance_skepticism"] = float(new_sk)
    st["stance_resentment"] = float(new_rs)
    st["stance_hardball"] = float(new_hb)

    # Recalibrate self expectations on the new team.
    st["self_expected_mpg"] = None
    st["self_expected_starts_rate"] = None
    st["self_expected_closes_rate"] = None

    # Add a settling cooldown to prevent immediate re-request loops.
    if int(cfg.trade_cooldown_days_after_move) > 0:
        until = date_add_days(nd, int(cfg.trade_cooldown_days_after_move))
        prev_until = norm_date_iso(st.get("cooldown_trade_until"))
        # Keep the later date.
        if prev_until is None or str(until) > str(prev_until):
            st["cooldown_trade_until"] = until

    # ------------------------------------------------------------------
    # Optional event for explainability/UI
    # ------------------------------------------------------------------
    trust_delta = float(new_trust - old_trust)
    m_delta = float(new_m_fr - old_m_fr)
    t_delta = float(new_t_fr - old_t_fr)
    sk_delta = float(new_sk - old_sk)
    rs_delta = float(new_rs - old_rs)
    hb_delta = float(new_hb - old_hb)

    classification = "REQUESTED_TRADE" if req else "UNEXPECTED_TRADE"

    sev = float(cfg.event_severity_base)
    sev += abs(trust_delta) * 2.0
    sev += abs(m_delta) * 1.5
    sev += abs(t_delta) * 1.2
    sev = float(clamp01(sev * (0.60 + 0.40 * lev)))

    payload: Dict[str, Any] = {
        "from_team_id": from_tid or old_team,
        "to_team_id": to_tid,
        "month_key": mk,
        "classification": classification,
        "reason": str(reason) if reason else None,
        "state_deltas": {
            "trust": trust_delta,
            "minutes_frustration": m_delta,
            "team_frustration": t_delta,
            "stance_skepticism": sk_delta,
            "stance_resentment": rs_delta,
            "stance_hardball": hb_delta,
        },
        "state_before": {
            "team_id": from_tid or old_team,
            "trust": old_trust,
            "minutes_frustration": old_m_fr,
            "team_frustration": old_t_fr,
            "stance_skepticism": old_sk,
            "stance_resentment": old_rs,
            "stance_hardball": old_hb,
            "self_expected_mpg": old_self_mpg,
            "self_expected_starts_rate": old_self_sr,
            "self_expected_closes_rate": old_self_cr,
            "trade_request_level": safe_int(trade_request_level_before, safe_int(state.get("trade_request_level"), 0)),
        },
        "state_after": {
            "team_id": to_tid,
            "trust": new_trust,
            "minutes_frustration": new_m_fr,
            "team_frustration": new_t_fr,
            "stance_skepticism": new_sk,
            "stance_resentment": new_rs,
            "stance_hardball": new_hb,
            "self_expected_mpg": None,
            "self_expected_starts_rate": None,
            "self_expected_closes_rate": None,
            "trade_request_level": 0,
        },
    }

    # Attach split summary if provided (keep small)
    if isinstance(split_summary, Mapping):
        # Avoid unbounded payload sizes
        payload["month_split"] = dict(split_summary)

    event = {
        "event_id": make_event_id(str(cfg.event_prefix), "team_changed", pid, mk, from_tid or old_team, to_tid),
        "player_id": pid,
        "team_id": to_tid,
        "season_year": int(season_year),
        "date": nd,
        "event_type": str(cfg.event_type),
        "severity": sev,
        "payload": payload,
    }

    meta = {
        "requested_trade": bool(req),
        "classification": classification,
        "trust_reset_strength": reset_strength,
        "baseline_trust": baseline,
        "clear_minutes_fraction": clear_m,
        "clear_team_fraction": clear_t,
        "clear_stance_fraction": clear_s,
    }

    return TeamTransitionOutcome(state_after=st, event=event, meta=meta)
