from __future__ import annotations

"""Monthly agency tick logic.

This module contains the *behavioral core*:
- Update frustrations + trust (state variables)
- Emit events (complaints, demands, trade requests)

Design notes
------------
1) Mental traits are modulators, not absolute rules.
2) Strong actions are gated by leverage (role/importance).
3) Deterministic randomness (stable hashing) provides variety without breaking
   reproducibility.

This file is intentionally free of DB I/O.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .config import AgencyConfig
from .escalation import advance_stage, decay_stage, desired_stage, stage_fields
from .metrics import contract_seasons_left, fatigue_level, role_status_pressure
from .types import MonthlyPlayerInputs
from .behavior_profile import compute_behavior_profile
from .self_expectations import update_self_expectations_monthly
from .stance import apply_monthly_stance_decay
from .utils import (
    clamp,
    clamp01,
    date_add_days,
    make_event_id,
    mental_norm,
    norm_date_iso,
    stable_u01,
)


# ---------------------------------------------------------------------------
# Candidate model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventCandidate:
    axis: str
    priority: float
    event: Dict[str, Any]
    state_updates: Dict[str, Any] = field(default_factory=dict)
    mem_updates: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------

   
def _cooldown_active(until_iso: Optional[str], *, now_date_iso: str) -> bool:
    u = norm_date_iso(until_iso)
    if not u:
        return False
    # ISO dates compare lexicographically.
    return str(u) > str(now_date_iso)[:10]



def _behavior_profile_for(state: Mapping[str, Any], inputs: MonthlyPlayerInputs) -> Tuple[Any, Dict[str, Any]]:
    """Compute BehaviorProfile for this player from mental + dynamic stances."""
    prof, meta = compute_behavior_profile(
        mental=inputs.mental or {},
        trust=state.get("trust", 0.5),
        stance_skepticism=state.get("stance_skepticism", 0.0),
        stance_resentment=state.get("stance_resentment", 0.0),
        stance_hardball=state.get("stance_hardball", 0.0),
    )
    return prof, meta


def _adjust_escalation_params(
    *,
    threshold: float,
    delta_2: float,
    delta_3: float,
    profile: Any,
) -> Tuple[float, float, float]:
    """Adjust escalation thresholds/deltas per-player.

    - Higher patience -> raises speak-up threshold + slows escalation.
    - Higher publicness -> stage-3 (PUBLIC) is reachable sooner.
    """
    try:
        pat = float(getattr(profile, "patience", 0.5))
    except Exception:
        pat = 0.5
    try:
        pub = float(getattr(profile, "publicness", 0.5))
    except Exception:
        pub = 0.5

    th = float(clamp01(float(threshold) + 0.10 * (pat - 0.5) - 0.05 * (pub - 0.5)))

    d2 = float(delta_2) * (1.0 + 0.20 * (pat - 0.5)) * (1.0 - 0.15 * pub)
    d3 = float(delta_3) * (1.0 + 0.25 * (pat - 0.5)) * (1.0 - 0.35 * pub)

    d2 = float(clamp(d2, 0.02, 0.90))
    d3 = float(clamp(d3, 0.02, 0.90))

    return th, d2, d3

def _injury_multiplier(status: Optional[str], cfg: AgencyConfig) -> float:
    fcfg = cfg.frustration
    s = str(status or "").upper()
    if s == "OUT":
        return float(clamp01(getattr(fcfg, "injury_out_multiplier", 0.05)))
    if s == "RETURNING":
        return float(clamp01(getattr(fcfg, "injury_returning_multiplier", 0.40)))
    return 1.0


def _compute_minutes_tolerance_mpg(mental: Mapping[str, Any], cfg: AgencyConfig) -> float:
    fcfg = cfg.frustration
    coach = mental_norm(mental, "coachability")
    loy = mental_norm(mental, "loyalty")
    adapt = mental_norm(mental, "adaptability")
    ego = mental_norm(mental, "ego")
    amb = mental_norm(mental, "ambition")

    tol = (
        float(fcfg.tolerance_base_mpg)
        + float(fcfg.tolerance_coachability_bonus) * coach
        + float(fcfg.tolerance_loyalty_bonus) * loy
        + float(fcfg.tolerance_adaptability_bonus) * adapt
        - float(fcfg.tolerance_ego_penalty) * ego
        - float(fcfg.tolerance_ambition_penalty) * amb
    )
    return float(clamp(tol, float(fcfg.tolerance_min_mpg), float(fcfg.tolerance_max_mpg)))

# ---------------------------------------------------------------------------
# Frustration updates (EMA)
# ---------------------------------------------------------------------------

def _update_minutes_frustration(
    *,
    prev: float,
    expected_mpg: float,
    actual_mpg: float,
    games_played: int,
    games_possible: int,
    mental: Mapping[str, Any],
    leverage: float,
    injury_status: Optional[str],
    injury_multiplier: Optional[float],
    cfg: AgencyConfig,
) -> Tuple[float, Dict[str, Any]]:
    """Update minutes frustration using a smooth EMA."""
    fcfg = cfg.frustration

    expected = max(0.0, float(expected_mpg))
    actual = max(0.0, float(actual_mpg))

    gap = max(0.0, expected - actual)

    tol = _compute_minutes_tolerance_mpg(mental, cfg)
    gap_pressure = clamp01(gap / max(tol, 1e-9))

    # DNP frequency pressure (separate from MPG gap)
    gp = max(0, int(games_played or 0))
    gpos = max(0, int(games_possible or 0))

    dnp_rate = 0.0
    if gpos > 0:
        dnp_rate = clamp01(1.0 - (gp / float(gpos)))

    dnp_grace = float(getattr(fcfg, "dnp_grace_rate", 0.20))
    dnp_soft = max(1e-6, float(getattr(fcfg, "dnp_softness_rate", 0.40)))
    dnp_pressure_raw = clamp01((dnp_rate - dnp_grace) / dnp_soft)

    # Scale by schedule sample size so we don't overreact to 1-2 games.
    try:
        full_g = int(getattr(cfg.month_context, "full_weight_games", 10))
    except Exception:
        full_g = 10
    full_g = max(1, int(full_g))
    schedule_weight = clamp01(gpos / float(full_g))
    dnp_pressure = float(dnp_pressure_raw) * float(schedule_weight)

    dnp_w = float(getattr(fcfg, "dnp_pressure_weight", 0.65))
    total_pressure = clamp01(float(gap_pressure) + float(dnp_w) * float(dnp_pressure))

    coach = mental_norm(mental, "coachability")
    loy = mental_norm(mental, "loyalty")
    ego = mental_norm(mental, "ego")
    amb = mental_norm(mental, "ambition")

    gain_mult = clamp(
        0.60 + 0.70 * ego + 0.40 * amb - 0.50 * coach - 0.30 * loy,
        0.25,
        1.75,
    )

    inj_mult: float
    if injury_multiplier is not None:
        try:
            inj_mult = float(injury_multiplier)
        except Exception:
            inj_mult = _injury_multiplier(injury_status, cfg)
        else:
            inj_mult = float(clamp01(inj_mult))
    else:
        inj_mult = _injury_multiplier(injury_status, cfg)

    # If the player is "close enough" to expectation *and* not getting DNP'd often,
    # let frustration cool down faster.
    within_gap = gap <= (0.50 * tol)
    within_dnp = float(dnp_pressure) <= 0.10
    within = bool(within_gap and within_dnp)
    decay = float(fcfg.minutes_decay) * (1.4 if within else 1.0)

    updated = (
        float(prev) * max(0.0, 1.0 - decay)
        + float(total_pressure) * float(fcfg.minutes_base_gain) * gain_mult * inj_mult
    )
    updated = float(clamp01(updated))

    meta = {
        "expected_mpg": expected,
        "actual_mpg": actual,
        "gap": gap,
        "tolerance_mpg": tol,
        "gap_pressure": float(gap_pressure),
        "dnp_rate": float(dnp_rate),
        "games_played": int(gp),
        "games_possible": int(gpos),
        "dnp_grace_rate": float(dnp_grace),
        "dnp_softness_rate": float(dnp_soft),
        "dnp_pressure_raw": float(dnp_pressure_raw),
        "dnp_schedule_weight": float(schedule_weight),
        "dnp_pressure": float(dnp_pressure),
        "dnp_pressure_weight": float(dnp_w),
        "total_pressure": float(total_pressure),
        "gain_mult": float(gain_mult),
        "injury_mult": float(inj_mult),
        "injury_status": str(injury_status or "").upper(),
        "within": bool(within),
        "within_gap": bool(within_gap),
        "within_dnp": bool(within_dnp),
    }
    return updated, meta


def _update_team_frustration(
    *,
    prev: float,
    team_win_pct: float,
    team_strategy: Optional[str],
    age: Optional[int],
    mental: Mapping[str, Any],
    leverage: float,
    cfg: AgencyConfig,
) -> Tuple[float, Dict[str, Any]]:
    fcfg = cfg.frustration

    win_pct = clamp(team_win_pct, 0.0, 1.0)

    # "Badness" starts accumulating below team_good_win_pct.
    target = clamp(fcfg.team_good_win_pct, 0.35, 0.75)
    badness = clamp01((target - win_pct) / max(target, 1e-9))

    amb = mental_norm(mental, "ambition")
    ego = mental_norm(mental, "ego")
    loy = mental_norm(mental, "loyalty")
    adapt = mental_norm(mental, "adaptability")
    lev = clamp01(leverage)

    # Base pressure: losing when the player wants to win.
    base_pressure = badness * (0.35 + 0.65 * amb) * (0.40 + 0.60 * lev) * (1.10 - 0.80 * loy)

    # Direction mismatch pressure: player wants to compete, team is rebuilding/developing.
    strat = str(team_strategy or "BALANCED").upper()

    strat_map = getattr(fcfg, "team_strategy_values", None)
    if isinstance(strat_map, dict):
        try:
            team_compete = float(strat_map.get(strat, strat_map.get("BALANCED", 0.65)))
        except Exception:
            team_compete = 0.65
    else:
        team_compete = {
            "WIN_NOW": 1.00,
            "BALANCED": 0.65,
            "DEVELOP": 0.45,
            "REBUILD": 0.25,
        }.get(strat, 0.65)

    # Age factor: older players are less tolerant of long timelines.
    age_i: Optional[int]
    try:
        age_i = int(age) if age is not None else None
    except Exception:
        age_i = None

    age_factor = 0.0
    if age_i is not None:
        age_factor = clamp01((float(age_i) - 24.0) / 10.0)

    age_w = float(getattr(fcfg, "team_strategy_age_weight", 0.20))
    player_compete = clamp01(
        0.30
        + 0.65 * amb
        + 0.15 * ego
        + age_w * age_factor
        + 0.10 * lev
        - 0.10 * loy
        - 0.10 * adapt
    )

    mismatch = clamp01(player_compete - team_compete)
    strategy_pressure = mismatch * (0.45 + 0.55 * lev) * (1.10 - 0.80 * loy)

    strategy_w = float(getattr(fcfg, "team_strategy_weight", 0.45))
    pressure = float(base_pressure) + float(strategy_w) * float(strategy_pressure)

    # If team is doing well, decay slightly faster.
    decay = float(fcfg.team_decay) * (1.35 if win_pct >= target else 1.0)

    updated = float(prev) * max(0.0, 1.0 - decay) + float(pressure) * float(fcfg.team_base_gain)
    updated = float(clamp01(updated))

    meta = {
        "team_win_pct": float(win_pct),
        "target_win_pct": float(target),
        "badness": float(badness),
        "base_pressure": float(base_pressure),
        "team_strategy": str(strat),
        "team_compete": float(team_compete),
        "player_compete": float(player_compete),
        "age": age_i,
        "age_factor": float(age_factor),
        "strategy_weight": float(strategy_w),
        "strategy_pressure": float(strategy_pressure),
        "mismatch": float(mismatch),
        "pressure": float(pressure),
    }
    return updated, meta



def _update_role_frustration(
    *,
    prev: float,
    minutes_frustration: float,
    role_bucket: str,
    starts_rate: float,
    closes_rate: float,
    expected_starts_rate: Optional[float] = None,
    expected_closes_rate: Optional[float] = None,
    mental: Mapping[str, Any],
    leverage: float,
    injury_status: Optional[str],
    injury_multiplier: Optional[float],
    cfg: AgencyConfig,
) -> Tuple[float, Dict[str, Any]]:
    """Role/Status frustration.

    Uses a weighted blend of:
    - minutes frustration ("I'm not playing")
    - status pressure from starts/closes ("I'm not treated like my role")

    This provides NBA flavor without requiring position-level data.
    """
    fcfg = cfg.frustration

    mfr = float(clamp01(minutes_frustration))
    status_p, status_meta = role_status_pressure(
        role_bucket=str(role_bucket or "UNKNOWN"),
        starts_rate=float(clamp01(starts_rate)),
        closes_rate=float(clamp01(closes_rate)),
        expected_starts_rate=expected_starts_rate,
        expected_closes_rate=expected_closes_rate,
        cfg=cfg,
    )

    w_m = float(getattr(fcfg, "role_minutes_weight", 0.65))
    w_s = float(getattr(fcfg, "role_status_weight", 0.35))
    pressure = clamp01((w_m * mfr) + (w_s * float(status_p)))

    coach = mental_norm(mental, "coachability")
    loy = mental_norm(mental, "loyalty")
    ego = mental_norm(mental, "ego")
    amb = mental_norm(mental, "ambition")

    gain_mult = clamp(0.60 + 0.60 * ego + 0.30 * amb - 0.45 * coach - 0.25 * loy, 0.25, 1.70)

    inj_mult: float
    if injury_multiplier is not None:
        try:
            inj_mult = float(injury_multiplier)
        except Exception:
            inj_mult = _injury_multiplier(injury_status, cfg)
        else:
            inj_mult = float(clamp01(inj_mult))
    else:
        inj_mult = _injury_multiplier(injury_status, cfg)

    # If pressure is low, decay faster.
    decay = float(getattr(fcfg, "role_decay", 0.12)) * (1.35 if pressure <= 0.20 else 1.0)
    base_gain = float(getattr(fcfg, "role_base_gain", 0.40))

    updated = float(prev) * max(0.0, 1.0 - decay) + float(pressure) * base_gain * gain_mult * inj_mult
    updated = float(clamp01(updated))

    meta = {
        "minutes_frustration": float(mfr),
        "status": dict(status_meta),
        "weights": {"minutes": float(w_m), "status": float(w_s)},
        "pressure": float(pressure),
        "gain_mult": float(gain_mult),
        "injury_mult": float(inj_mult),
        "injury_status": str(injury_status or "").upper(),
    }
    return updated, meta


def _update_contract_frustration(
    *,
    prev: float,
    season_year: int,
    contract_end_season_id: Optional[str],
    mental: Mapping[str, Any],
    leverage: float,
    trust: float,
    cfg: AgencyConfig,
) -> Tuple[float, Dict[str, Any]]:
    """Contract/Security frustration.

    A simple pressure curve based on seasons left.
    - expiring: high pressure
    - 1 year left: medium
    - 2 years: low
    """
    fcfg = cfg.frustration

    left = contract_seasons_left(contract_end_season_id, season_year=int(season_year))

    base = 0.0
    if left is None:
        base = 0.0
    elif left <= 0:
        base = 1.0
    elif left == 1:
        base = 0.65
    elif left == 2:
        base = 0.30
    else:
        base = 0.0

    ego = mental_norm(mental, "ego")
    amb = mental_norm(mental, "ambition")
    loy = mental_norm(mental, "loyalty")

    lev = float(clamp01(leverage))
    trust01 = float(clamp01(trust))

    # Low trust increases contract insecurity pressure.
    trust_factor = clamp(1.05 + (0.50 * max(0.0, 0.55 - trust01)), 0.85, 1.55)

    pressure = float(base)
    pressure *= (0.55 + 0.45 * lev)
    pressure *= (0.85 + 0.55 * ego + 0.25 * amb)
    pressure *= (1.10 - 0.70 * loy)
    pressure *= float(trust_factor)
    pressure = float(clamp01(pressure))

    decay = float(getattr(fcfg, "contract_decay", 0.10)) * (1.30 if base <= 0.05 else 1.0)
    base_gain = float(getattr(fcfg, "contract_base_gain", 0.25))

    updated = float(prev) * max(0.0, 1.0 - decay) + float(pressure) * base_gain
    updated = float(clamp01(updated))

    meta = {
        "contract_end_season_id": contract_end_season_id,
        "seasons_left": left,
        "base": float(base),
        "pressure": float(pressure),
        "trust": float(trust01),
        "trust_factor": float(trust_factor),
        "leverage": float(lev),
        "ego": float(ego),
        "ambition": float(amb),
        "loyalty": float(loy),
    }
    return updated, meta


def _update_health_frustration(
    *,
    prev: float,
    fatigue_st: Optional[float],
    fatigue_lt: Optional[float],
    injury_status: Optional[str],
    mental: Mapping[str, Any],
    leverage: float,
    cfg: AgencyConfig,
) -> Tuple[float, Dict[str, Any]]:
    """Health/Load frustration ("I need rest / manage my load")."""
    fcfg = cfg.frustration

    fat, fat_meta = fatigue_level(fatigue_st=fatigue_st, fatigue_lt=fatigue_lt, cfg=cfg)

    grace = float(getattr(fcfg, "health_fatigue_grace", 0.35))
    soft = max(1e-6, float(getattr(fcfg, "health_fatigue_softness", 0.40)))
    pressure = clamp01((float(fat) - grace) / soft)

    s = str(injury_status or "").upper()
    # If OUT, the player is already not playing; we reduce the need to complain about load.
    if s == "OUT":
        pressure *= 0.25
    elif s == "RETURNING":
        pressure *= 1.10

    coach = mental_norm(mental, "coachability")
    ego = mental_norm(mental, "ego")
    lev = float(clamp01(leverage))

    # High coachability reduces complaining; high ego increases.
    gain_mult = clamp(0.85 + 0.30 * ego - 0.35 * coach + 0.10 * lev, 0.50, 1.60)

    decay = float(getattr(fcfg, "health_decay", 0.18)) * (1.35 if pressure <= 0.15 else 1.0)
    base_gain = float(getattr(fcfg, "health_base_gain", 0.35))

    updated = float(prev) * max(0.0, 1.0 - decay) + float(pressure) * base_gain * float(gain_mult)
    updated = float(clamp01(updated))

    meta = {
        "fatigue": dict(fat_meta),
        "grace": float(grace),
        "softness": float(soft),
        "pressure": float(pressure),
        "injury_status": s,
        "gain_mult": float(gain_mult),
    }
    return updated, meta


def _update_chemistry_frustration(
    *,
    prev: float,
    team_frustration: float,
    trust: float,
    mental: Mapping[str, Any],
    leverage: float,
    cfg: AgencyConfig,
) -> Tuple[float, Dict[str, Any]]:
    """Chemistry/Locker-room frustration.

    We don't model individual relationships in v2 core.
    Instead we use:
    - team frustration (losing and uncertainty)
    - low trust (player feels ignored)
    - ego/adaptability traits
    """
    fcfg = cfg.frustration

    tfr = float(clamp01(team_frustration))
    trust01 = float(clamp01(trust))

    grace = float(getattr(fcfg, "chemistry_team_grace", 0.45))
    soft = max(1e-6, float(getattr(fcfg, "chemistry_team_softness", 0.35)))

    team_pressure = clamp01((tfr - grace) / soft)
    trust_pressure = clamp01((0.55 - trust01) / 0.55)

    ego = mental_norm(mental, "ego")
    adapt = mental_norm(mental, "adaptability")
    lev = float(clamp01(leverage))

    pressure = 0.70 * team_pressure + 0.30 * trust_pressure
    pressure *= clamp(0.90 + 0.45 * ego - 0.30 * adapt + 0.10 * lev, 0.55, 1.75)
    pressure = float(clamp01(pressure))

    decay = float(getattr(fcfg, "chemistry_decay", 0.10)) * (1.25 if pressure <= 0.15 else 1.0)
    base_gain = float(getattr(fcfg, "chemistry_base_gain", 0.20))

    updated = float(prev) * max(0.0, 1.0 - decay) + float(pressure) * base_gain
    updated = float(clamp01(updated))

    meta = {
        "team_frustration": float(tfr),
        "trust": float(trust01),
        "team_pressure": float(team_pressure),
        "trust_pressure": float(trust_pressure),
        "pressure": float(pressure),
        "ego": float(ego),
        "adaptability": float(adapt),
    }
    return updated, meta


def _update_usage_frustration(
    *,
    prev: float,
    role_bucket: str,
    usage_share: float,
    mental: Mapping[str, Any],
    cfg: AgencyConfig,
) -> Tuple[float, Dict[str, Any]]:
    """Optional usage frustration.

    v2 core keeps this intentionally mild because users may not have direct
    levers to change usage without deeper tactical controls.

    We still update it as a background variable for future narrative.
    """
    fcfg = cfg.frustration

    rb = str(role_bucket or "UNKNOWN").upper()
    usg = float(clamp01(usage_share))

    # A conservative "expected" usage share by coarse role.
    # (Not a true NBA USG%. It's a possession involvement proxy.)
    expected = {
        "FRANCHISE": 0.26,
        "STAR": 0.23,
        "STARTER": 0.18,
        "ROTATION": 0.14,
        "BENCH": 0.10,
        "GARBAGE": 0.06,
        "UNKNOWN": 0.12,
    }.get(rb, 0.12)

    gap = max(0.0, float(expected) - float(usg))
    pressure = clamp01(gap / 0.10)  # soft scale

    ego = mental_norm(mental, "ego")
    amb = mental_norm(mental, "ambition")
    gain_mult = clamp(0.70 + 0.25 * ego + 0.15 * amb, 0.50, 1.35)

    decay = float(getattr(fcfg, "usage_decay", 0.12)) * (1.35 if pressure <= 0.15 else 1.0)
    base_gain = float(getattr(fcfg, "usage_base_gain", 0.15))

    updated = float(prev) * max(0.0, 1.0 - decay) + float(pressure) * base_gain * gain_mult
    updated = float(clamp01(updated))

    meta = {
        "role_bucket": rb,
        "usage_share": float(usg),
        "expected_usage_share": float(expected),
        "gap": float(gap),
        "pressure": float(pressure),
        "gain_mult": float(gain_mult),
    }
    return updated, meta


def _update_trust_v2(
    *,
    prev: float,
    frustrations: Mapping[str, float],
    mental: Mapping[str, Any],
    cfg: AgencyConfig,
) -> Tuple[float, Dict[str, Any]]:
    """Trust update using multiple axes.

    We take the average of the *top 3* frustrations to avoid double-counting
    overlapping signals (e.g., minutes vs role).
    """
    fcfg = cfg.frustration

    trust = float(clamp01(prev))

    fr_vals: List[float] = []
    for k, v in frustrations.items():
        try:
            fr_vals.append(float(clamp01(v)))
        except Exception:
            continue

    fr_vals.sort(reverse=True)
    top = fr_vals[:3] if fr_vals else [0.0]
    fr_avg = float(sum(top) / float(len(top) or 1))

    coach = mental_norm(mental, "coachability")
    loy = mental_norm(mental, "loyalty")
    ego = mental_norm(mental, "ego")
    amb = mental_norm(mental, "ambition")
    adapt = mental_norm(mental, "adaptability")

    bad_th = clamp(fcfg.trust_bad_frustration_threshold, 0.3, 0.9)

    # Degrade trust when frustration is high.
    if fr_avg > bad_th:
        severity = (fr_avg - bad_th) / max(1.0 - bad_th, 1e-9)
        weight = clamp(0.70 + 0.60 * ego + 0.30 * amb - 0.20 * coach, 0.25, 1.75)
        trust = clamp01(trust - float(fcfg.trust_decay) * float(severity) * float(weight))
        return trust, {
            "fr_avg": fr_avg,
            "mode": "decay",
            "severity": float(severity),
            "weight": float(weight),
            "top_frustrations": top,
        }

    # Recover trust slowly when things are calm.
    calm_th = bad_th * 0.55
    if fr_avg < calm_th:
        calmness = (calm_th - fr_avg) / max(calm_th, 1e-9)
        weight = clamp(0.55 + 0.60 * loy + 0.35 * coach + 0.25 * adapt - 0.25 * ego, 0.15, 1.75)
        trust = clamp01(trust + float(fcfg.trust_recovery) * float(calmness) * float(weight))
        return trust, {
            "fr_avg": fr_avg,
            "mode": "recover",
            "calmness": float(calmness),
            "weight": float(weight),
            "top_frustrations": top,
        }

    return trust, {"fr_avg": fr_avg, "mode": "stable", "top_frustrations": top}

# ---------------------------------------------------------------------------
# Candidate builders (NO state mutation)
# ---------------------------------------------------------------------------


def _stage_weight(stage: int) -> float:
    if stage <= 1:
        return 1.00
    if stage == 2:
        return 1.15
    return 1.30


def _candidate_role_issue(
    *,
    state: Mapping[str, Any],
    inputs: MonthlyPlayerInputs,
    cfg: AgencyConfig,
    sample_weight: float,
) -> Optional[EventCandidate]:
    ecfg = cfg.events

    fr = float(clamp01(state.get("role_frustration")))

    prof, prof_meta = _behavior_profile_for(state, inputs)
    th0 = float(ecfg.role_issue_threshold)
    th, d2, d3 = _adjust_escalation_params(
        threshold=th0,
        delta_2=float(ecfg.axis_escalate_delta_2),
        delta_3=float(ecfg.axis_escalate_delta_3),
        profile=prof,
    )
    if fr < th:
        return None

    now_date = str(inputs.now_date_iso)[:10]
    if _cooldown_active(state.get("cooldown_role_until"), now_date_iso=now_date):
        return None

    lev = float(clamp01(inputs.leverage))
    ego = mental_norm(inputs.mental, "ego")

    if lev < float(ecfg.role_issue_min_leverage) and ego < 0.78:
        return None

    gp = int(inputs.games_played or 0)
    if gp > 0 and gp < int(ecfg.min_games_for_events or 2):
        return None

    softness = max(1e-6, float(ecfg.role_issue_softness))
    base_p = clamp01((fr - th) / softness)

    p = base_p * (0.45 + 0.55 * lev) * (0.80 + 0.40 * ego)
    p *= clamp(0.35 + 0.65 * float(clamp01(sample_weight)), 0.20, 1.00)

    roll = stable_u01(inputs.player_id, inputs.month_key, "role_issue", int(state.get("escalation_role") or 0))
    if roll >= p:
        return None

    desired = desired_stage(
        frustration=fr,
        threshold=th,
        delta_2=float(d2),
        delta_3=float(d3),
    )
    stage = advance_stage(state.get("escalation_role"), desired=desired)

    if stage == 1:
        et = cfg.event_types.get("role_private", "ROLE_PRIVATE")
    elif stage == 2:
        et = cfg.event_types.get("role_agent", "ROLE_AGENT")
    else:
        et = cfg.event_types.get("role_public", "ROLE_PUBLIC")

    event_id = make_event_id("agency", inputs.player_id, inputs.month_key, et)

    severity = clamp01((0.60 * fr + 0.25 * lev + 0.15 * ego) * _stage_weight(stage))

    # Include evidence for explainability.
    status_meta = ((state.get("context") or {}).get("role") or {}).get("status")
    if not isinstance(status_meta, Mapping):
        status_meta = {}

    payload = {
        "axis": "ROLE",
        **stage_fields(stage),
        "role_bucket": str(inputs.role_bucket or "UNKNOWN"),
        "leverage": float(lev),
        "ego": float(ego),
        "role_frustration": float(fr),
        "expected_mpg": float(inputs.expected_mpg),
        "self_expected_mpg": float(state.get("self_expected_mpg") or inputs.expected_mpg),
        "self_expected_starts_rate": state.get("self_expected_starts_rate"),
        "self_expected_closes_rate": state.get("self_expected_closes_rate"),
        "escalation": {
            "threshold": float(th),
            "delta_2": float(d2),
            "delta_3": float(d3),
            "behavior_profile": dict(prof_meta or {}),
        },
        "self_expected_mpg": float(state.get("self_expected_mpg") or inputs.expected_mpg),
        "starts_rate": float(clamp01(inputs.starts_rate)),
        "closes_rate": float(clamp01(inputs.closes_rate)),
        "usage_share": float(clamp01(inputs.usage_share)),
        "status_evidence": dict(status_meta),
        "sample_games_played": int(gp),
        "sample_weight": float(clamp01(sample_weight)),
    }

    # Negotiation focus hint: which role-status axis is currently the bigger gap.
    try:
        exp_s = state.get("self_expected_starts_rate")
        exp_c = state.get("self_expected_closes_rate")
        if exp_s is None and isinstance(status_meta, Mapping):
            exp_s = status_meta.get("expected_starts_rate")
        if exp_c is None and isinstance(status_meta, Mapping):
            exp_c = status_meta.get("expected_closes_rate")

        if exp_s is not None and exp_c is not None:
            gap_s = float(exp_s) - float(clamp01(inputs.starts_rate))
            gap_c = float(exp_c) - float(clamp01(inputs.closes_rate))
            payload["gap_starts"] = float(gap_s)
            payload["gap_closes"] = float(gap_c)
            payload["role_focus"] = "STARTS" if gap_s >= gap_c else "CLOSES"
    except Exception:
        pass

    state_updates = {
        "cooldown_role_until": date_add_days(now_date, int(ecfg.cooldown_role_days)),
        "escalation_role": int(stage),
    }

    priority = float(severity) * (0.85 + 0.15 * float(stage))

    return EventCandidate(
        axis="ROLE",
        priority=priority,
        event={
            "event_id": event_id,
            "player_id": inputs.player_id,
            "team_id": inputs.team_id,
            "season_year": int(inputs.season_year),
            "date": now_date,
            "event_type": et,
            "severity": float(severity),
            "payload": payload,
        },
        state_updates=state_updates,
        mem_updates={"last_major_issue_axis": "ROLE", "last_major_issue_month": str(inputs.month_key), "public_blowups_inc": 1 if int(stage) >= 3 else 0},
    )


def _candidate_contract_issue(
    *,
    state: Mapping[str, Any],
    inputs: MonthlyPlayerInputs,
    cfg: AgencyConfig,
    sample_weight: float,
) -> Optional[EventCandidate]:
    ecfg = cfg.events

    fr = float(clamp01(state.get("contract_frustration")))

    prof, prof_meta = _behavior_profile_for(state, inputs)
    th0 = float(ecfg.contract_issue_threshold)
    th, d2, d3 = _adjust_escalation_params(
        threshold=th0,
        delta_2=float(ecfg.axis_escalate_delta_2),
        delta_3=float(ecfg.axis_escalate_delta_3),
        profile=prof,
    )
    if fr < th:
        return None

    now_date = str(inputs.now_date_iso)[:10]
    if _cooldown_active(state.get("cooldown_contract_until"), now_date_iso=now_date):
        return None

    lev = float(clamp01(inputs.leverage))
    ego = mental_norm(inputs.mental, "ego")

    if lev < float(ecfg.contract_issue_min_leverage) and ego < 0.70:
        return None

    gp = int(inputs.games_played or 0)
    if gp > 0 and gp < int(ecfg.min_games_for_events or 2):
        return None

    softness = max(1e-6, float(ecfg.contract_issue_softness))
    base_p = clamp01((fr - th) / softness)

    p = base_p * (0.55 + 0.45 * lev) * (0.85 + 0.30 * ego)
    p *= clamp(0.35 + 0.65 * float(clamp01(sample_weight)), 0.20, 1.00)

    roll = stable_u01(inputs.player_id, inputs.month_key, "contract_issue", int(state.get("escalation_contract") or 0))
    if roll >= p:
        return None

    desired = desired_stage(
        frustration=fr,
        threshold=th,
        delta_2=float(d2),
        delta_3=float(d3),
    )
    stage = advance_stage(state.get("escalation_contract"), desired=desired)

    if stage == 1:
        et = cfg.event_types.get("contract_private", "CONTRACT_PRIVATE")
    elif stage == 2:
        et = cfg.event_types.get("contract_agent", "CONTRACT_AGENT")
    else:
        et = cfg.event_types.get("contract_public", "CONTRACT_PUBLIC")

    event_id = make_event_id("agency", inputs.player_id, inputs.month_key, et)

    severity = clamp01((0.62 * fr + 0.28 * lev + 0.10 * ego) * _stage_weight(stage))

    left = contract_seasons_left(inputs.contract_end_season_id, season_year=int(inputs.season_year))

    payload = {
        "axis": "CONTRACT",
        **stage_fields(stage),
        "role_bucket": str(inputs.role_bucket or "UNKNOWN"),
        "leverage": float(lev),
        "ego": float(ego),
        "contract_frustration": float(fr),
        "escalation": {
            "threshold": float(th),
            "delta_2": float(d2),
            "delta_3": float(d3),
            "behavior_profile": dict(prof_meta or {}),
        },
        "active_contract_id": inputs.active_contract_id,
        "contract_end_season_id": inputs.contract_end_season_id,
        "seasons_left": left,
        "sample_games_played": int(gp),
        "sample_weight": float(clamp01(sample_weight)),
    }

    state_updates = {
        "cooldown_contract_until": date_add_days(now_date, int(ecfg.cooldown_contract_days)),
        "escalation_contract": int(stage),
    }

    priority = float(severity) * (0.90 + 0.10 * float(stage))

    return EventCandidate(
        axis="CONTRACT",
        priority=priority,
        event={
            "event_id": event_id,
            "player_id": inputs.player_id,
            "team_id": inputs.team_id,
            "season_year": int(inputs.season_year),
            "date": now_date,
            "event_type": et,
            "severity": float(severity),
            "payload": payload,
        },
        state_updates=state_updates,
        mem_updates={"last_major_issue_axis": "CONTRACT", "last_major_issue_month": str(inputs.month_key), "public_blowups_inc": 1 if int(stage) >= 3 else 0},
    )


def _candidate_health_issue(
    *,
    state: Mapping[str, Any],
    inputs: MonthlyPlayerInputs,
    cfg: AgencyConfig,
    sample_weight: float,
) -> Optional[EventCandidate]:
    ecfg = cfg.events

    fr = float(clamp01(state.get("health_frustration")))

    prof, prof_meta = _behavior_profile_for(state, inputs)
    th0 = float(ecfg.health_issue_threshold)
    th, d2, d3 = _adjust_escalation_params(
        threshold=th0,
        delta_2=float(ecfg.axis_escalate_delta_2),
        delta_3=float(ecfg.axis_escalate_delta_3),
        profile=prof,
    )
    if fr < th:
        return None

    now_date = str(inputs.now_date_iso)[:10]
    if _cooldown_active(state.get("cooldown_health_until"), now_date_iso=now_date):
        return None

    # Health issues should be able to appear even for lower leverage players.
    lev = float(clamp01(inputs.leverage))
    ego = mental_norm(inputs.mental, "ego")

    gp = int(inputs.games_played or 0)
    if gp > 0 and gp < int(ecfg.min_games_for_events or 2):
        return None

    softness = max(1e-6, float(ecfg.health_issue_softness))
    base_p = clamp01((fr - th) / softness)

    p = base_p * (0.75 + 0.25 * lev) * (0.85 + 0.25 * ego)
    p *= clamp(0.35 + 0.65 * float(clamp01(sample_weight)), 0.20, 1.00)

    roll = stable_u01(inputs.player_id, inputs.month_key, "health_issue", int(state.get("escalation_health") or 0))
    if roll >= p:
        return None

    desired = desired_stage(
        frustration=fr,
        threshold=th,
        delta_2=float(d2),
        delta_3=float(d3),
    )
    stage = advance_stage(state.get("escalation_health"), desired=desired)

    if stage == 1:
        et = cfg.event_types.get("health_private", "HEALTH_PRIVATE")
    elif stage == 2:
        et = cfg.event_types.get("health_agent", "HEALTH_AGENT")
    else:
        et = cfg.event_types.get("health_public", "HEALTH_PUBLIC")

    event_id = make_event_id("agency", inputs.player_id, inputs.month_key, et)

    severity = clamp01((0.65 * fr + 0.20 * lev + 0.15 * ego) * _stage_weight(stage))

    fat, fat_meta = fatigue_level(fatigue_st=inputs.fatigue_st, fatigue_lt=inputs.fatigue_lt, cfg=cfg)

    payload = {
        "axis": "HEALTH",
        **stage_fields(stage),
        "role_bucket": str(inputs.role_bucket or "UNKNOWN"),
        "leverage": float(lev),
        "ego": float(ego),
        "health_frustration": float(fr),
        "escalation": {
            "threshold": float(th),
            "delta_2": float(d2),
            "delta_3": float(d3),
            "behavior_profile": dict(prof_meta or {}),
        },
        "injury_status": str(inputs.injury_status or "").upper(),
        "fatigue": dict(fat_meta),
        "sample_games_played": int(gp),
        "sample_weight": float(clamp01(sample_weight)),
    }

    state_updates = {
        "cooldown_health_until": date_add_days(now_date, int(ecfg.cooldown_health_days)),
        "escalation_health": int(stage),
    }


    priority = float(severity) * (0.92 + 0.08 * float(stage))

    return EventCandidate(
        axis="HEALTH",
        priority=priority,
        event={
            "event_id": event_id,
            "player_id": inputs.player_id,
            "team_id": inputs.team_id,
            "season_year": int(inputs.season_year),
            "date": now_date,
            "event_type": et,
            "severity": float(severity),
            "payload": payload,
        },
        state_updates=state_updates,
        mem_updates={"last_major_issue_axis": "HEALTH", "last_major_issue_month": str(inputs.month_key), "public_blowups_inc": 1 if int(stage) >= 3 else 0},
    )


def _candidate_chemistry_issue(
    *,
    state: Mapping[str, Any],
    inputs: MonthlyPlayerInputs,
    cfg: AgencyConfig,
    sample_weight: float,
) -> Optional[EventCandidate]:
    ecfg = cfg.events

    fr = float(clamp01(state.get("chemistry_frustration")))

    prof, prof_meta = _behavior_profile_for(state, inputs)
    th0 = float(ecfg.chemistry_issue_threshold)
    th, d2, d3 = _adjust_escalation_params(
        threshold=th0,
        delta_2=float(ecfg.axis_escalate_delta_2),
        delta_3=float(ecfg.axis_escalate_delta_3),
        profile=prof,
    )
    if fr < th:
        return None

    now_date = str(inputs.now_date_iso)[:10]
    if _cooldown_active(state.get("cooldown_chemistry_until"), now_date_iso=now_date):
        return None

    lev = float(clamp01(inputs.leverage))
    ego = mental_norm(inputs.mental, "ego")

    if lev < float(ecfg.chemistry_issue_min_leverage) and ego < 0.78:
        return None

    gp = int(inputs.games_played or 0)
    if gp > 0 and gp < int(ecfg.min_games_for_events or 2):
        return None

    softness = max(1e-6, float(ecfg.chemistry_issue_softness))
    base_p = clamp01((fr - th) / softness)

    p = base_p * (0.45 + 0.55 * lev) * (0.80 + 0.40 * ego)
    p *= clamp(0.35 + 0.65 * float(clamp01(sample_weight)), 0.20, 1.00)

    roll = stable_u01(inputs.player_id, inputs.month_key, "chemistry_issue", int(state.get("escalation_chemistry") or 0))
    if roll >= p:
        return None

    desired = desired_stage(
        frustration=fr,
        threshold=th,
        delta_2=float(d2),
        delta_3=float(d3),
    )
    stage = advance_stage(state.get("escalation_chemistry"), desired=desired)

    if stage == 1:
        et = cfg.event_types.get("chemistry_private", "CHEMISTRY_PRIVATE")
    elif stage == 2:
        et = cfg.event_types.get("chemistry_agent", "CHEMISTRY_AGENT")
    else:
        et = cfg.event_types.get("chemistry_public", "CHEMISTRY_PUBLIC")

    event_id = make_event_id("agency", inputs.player_id, inputs.month_key, et)

    severity = clamp01((0.62 * fr + 0.25 * lev + 0.13 * ego) * _stage_weight(stage))

    payload = {
        "axis": "CHEMISTRY",
        **stage_fields(stage),
        "role_bucket": str(inputs.role_bucket or "UNKNOWN"),
        "leverage": float(lev),
        "ego": float(ego),
        "chemistry_frustration": float(fr),
        "escalation": {
            "threshold": float(th),
            "delta_2": float(d2),
            "delta_3": float(d3),
            "behavior_profile": dict(prof_meta or {}),
        },
        "team_win_pct": float(inputs.team_win_pct),
        "team_frustration": float(clamp01(state.get("team_frustration"))),
        "trust": float(clamp01(state.get("trust"))),
        "sample_games_played": int(gp),
        "sample_weight": float(clamp01(sample_weight)),
    }

    state_updates = {
        "cooldown_chemistry_until": date_add_days(now_date, int(ecfg.cooldown_chemistry_days)),
        "escalation_chemistry": int(stage),
    }

    priority = float(severity) * (0.90 + 0.10 * float(stage))

    return EventCandidate(
        axis="CHEMISTRY",
        priority=priority,
        event={
            "event_id": event_id,
            "player_id": inputs.player_id,
            "team_id": inputs.team_id,
            "season_year": int(inputs.season_year),
            "date": now_date,
            "event_type": et,
            "severity": float(severity),
            "payload": payload,
        },
        state_updates=state_updates,
        mem_updates={"last_major_issue_axis": "CHEMISTRY", "last_major_issue_month": str(inputs.month_key), "public_blowups_inc": 1 if int(stage) >= 3 else 0},
    )


def _candidate_team_issue(
    *,
    state: Mapping[str, Any],
    inputs: MonthlyPlayerInputs,
    cfg: AgencyConfig,
    sample_weight: float,
) -> Optional[EventCandidate]:
    ecfg = cfg.events

    fr = float(clamp01(state.get("team_frustration")))

    prof, prof_meta = _behavior_profile_for(state, inputs)
    th0 = float(ecfg.team_issue_threshold)
    th, d2, d3 = _adjust_escalation_params(
        threshold=th0,
        delta_2=float(ecfg.axis_escalate_delta_2),
        delta_3=float(ecfg.axis_escalate_delta_3),
        profile=prof,
    )
    if fr < th:
        return None

    now_date = str(inputs.now_date_iso)[:10]
    # Reuse cooldown_help_until for direction axis.
    if _cooldown_active(state.get("cooldown_help_until"), now_date_iso=now_date):
        return None

    lev = float(clamp01(inputs.leverage))
    amb = mental_norm(inputs.mental, "ambition")

    if lev < float(ecfg.team_issue_min_leverage) and amb < 0.65:
        return None
    gp = int(inputs.games_played or 0)
    if gp > 0 and gp < int(ecfg.min_games_for_events or 2):
        return None

    softness = max(1e-6, float(ecfg.team_issue_softness))
    base_p = clamp01((fr - th) / softness)
    p = base_p * (0.55 + 0.45 * amb) * (0.50 + 0.50 * lev)
    p *= clamp(0.35 + 0.65 * float(clamp01(sample_weight)), 0.20, 1.00)

    roll = stable_u01(inputs.player_id, inputs.month_key, "team_issue", int(state.get("escalation_team") or 0))
    if roll >= p:
        return None

    desired = desired_stage(
        frustration=fr,
        threshold=th,
        delta_2=float(d2),
        delta_3=float(d3),
    )
    stage = advance_stage(state.get("escalation_team"), desired=desired)

    # Stage 2 uses the existing HELP_DEMAND event family (keeps prior UI/responses viable).
    if stage == 1:
        et = cfg.event_types.get("team_private", "TEAM_PRIVATE")
    elif stage == 2:
        et = cfg.event_types.get("help_demand", "HELP_DEMAND")
    else:
        et = cfg.event_types.get("team_public", "TEAM_PUBLIC")

    event_id = make_event_id("agency", inputs.player_id, inputs.month_key, et)

    severity = clamp01((0.60 * fr + 0.25 * amb + 0.15 * lev) * _stage_weight(stage))

    payload = {
        "axis": "TEAM",
        **stage_fields(stage),
        "role_bucket": str(inputs.role_bucket or "UNKNOWN"),
        "team_win_pct": float(inputs.team_win_pct),
        "team_strategy": str(inputs.team_strategy or "BALANCED"),
        "leverage": float(lev),
        "ambition": float(amb),
        "team_frustration": float(fr),
        "escalation": {
            "threshold": float(th),
            "delta_2": float(d2),
            "delta_3": float(d3),
            "behavior_profile": dict(prof_meta or {}),
        },
        "sample_games_played": int(gp),
        "sample_games_possible": int(getattr(inputs, "games_possible", 0) or 0),
        "sample_weight": float(clamp01(sample_weight)),
    }

    # Cooldown for direction axis.
    cd_days = int(getattr(ecfg, "cooldown_team_days", 45))
    if stage >= 2:
        # Keep existing help cooldown as the default for stronger demands.
        cd_days = int(getattr(ecfg, "cooldown_help_days", cd_days))

    state_updates = {
        "cooldown_help_until": date_add_days(now_date, cd_days),
        "escalation_team": int(stage),
    }

    priority = float(severity) * (0.90 + 0.10 * float(stage))

    return EventCandidate(
        axis="TEAM",
        priority=priority,
        event={
            "event_id": event_id,
            "player_id": inputs.player_id,
            "team_id": inputs.team_id,
            "season_year": int(inputs.season_year),
            "date": now_date,
            "event_type": et,
            "severity": float(severity),
            "payload": payload,
        },
        state_updates=state_updates,
        mem_updates={"last_major_issue_axis": "TEAM", "last_major_issue_month": str(inputs.month_key), "public_blowups_inc": 1 if int(stage) >= 3 else 0},
    )


def _candidate_minutes_complaint(
    *,
    state: Mapping[str, Any],
    inputs: MonthlyPlayerInputs,
    cfg: AgencyConfig,
    sample_weight: float,
) -> Optional[EventCandidate]:
    ecfg = cfg.events

    fr = float(clamp01(state.get("minutes_frustration")))
    if fr < float(ecfg.minutes_complaint_threshold):
        return None

    now_date = str(inputs.now_date_iso)[:10]
    if _cooldown_active(state.get("cooldown_minutes_until"), now_date_iso=now_date):
        return None

    ego = mental_norm(inputs.mental, "ego")
    lev = float(clamp01(inputs.leverage))

    if lev < float(ecfg.minutes_complaint_min_leverage) and ego < float(ecfg.minutes_complaint_ego_override):
        return None

    role = str(inputs.role_bucket or "UNKNOWN")
    low_role = role in {"GARBAGE", "BENCH"}
    if low_role and lev < 0.20 and ego < 0.90:
        return None

    gp = int(inputs.games_played or 0)
    try:
        min_g = int(ecfg.min_games_for_events)
    except Exception:
        min_g = 2
    if gp > 0 and gp < max(1, min_g):
        if float(inputs.actual_minutes or 0.0) > 0.0:
            return None

    softness = max(1e-6, float(ecfg.minutes_complaint_softness))
    base_p = clamp01((fr - float(ecfg.minutes_complaint_threshold)) / softness)

    p = base_p * (0.40 + 0.60 * lev) * (0.80 + 0.40 * ego)
    p *= clamp(0.35 + 0.65 * float(clamp01(sample_weight)), 0.20, 1.00)

    roll = stable_u01(inputs.player_id, inputs.month_key, "minutes_complaint")
    if roll >= p:
        return None

    et = cfg.event_types.get("minutes_complaint", "MINUTES_COMPLAINT")
    event_id = make_event_id("agency", inputs.player_id, inputs.month_key, et)

    severity = clamp01(0.50 * fr + 0.30 * ego + 0.20 * lev)
    severity *= clamp(0.60 + 0.40 * float(clamp01(sample_weight)), 0.60, 1.00)

    ctx_m = ((state.get("context") or {}).get("minutes") or {})
    if not isinstance(ctx_m, Mapping):
        ctx_m = {}

    payload = {
        "axis": "MINUTES",
        "role_bucket": role,
        "expected_mpg": float(inputs.expected_mpg),
        "self_expected_mpg": float(state.get("self_expected_mpg") or inputs.expected_mpg),
        "actual_mpg": float(ctx_m.get("actual_mpg") or state.get("minutes_actual_mpg") or 0.0),
        "gap": float(ctx_m.get("gap") or 0.0),
        "leverage": float(lev),
        "ego": float(ego),
        "frustration": float(fr),
        "sample_games_played": int(inputs.games_played or 0),
        "sample_weight": float(clamp01(sample_weight)),
    }

    state_updates = {
        "cooldown_minutes_until": date_add_days(now_date, int(ecfg.cooldown_minutes_days)),
    }

    priority = float(severity)

    return EventCandidate(
        axis="MINUTES",
        priority=priority,
        event={
            "event_id": event_id,
            "player_id": inputs.player_id,
            "team_id": inputs.team_id,
            "season_year": int(inputs.season_year),
            "date": now_date,
            "event_type": et,
            "severity": float(severity),
            "payload": payload,
        },
        state_updates=state_updates,
        mem_updates={"last_major_issue_axis": "MINUTES", "last_major_issue_month": str(inputs.month_key)},
    )


def _candidate_trade_request(
    *,
    state: Mapping[str, Any],
    inputs: MonthlyPlayerInputs,
    cfg: AgencyConfig,
    sample_weight: float,
) -> Optional[EventCandidate]:
    """Trade request candidate (existing v1 behavior, expanded to v2 axes).

    We treat this as a special action that can override other complaints.
    """
    ecfg = cfg.events

    now_date = str(inputs.now_date_iso)[:10]
    if _cooldown_active(state.get("cooldown_trade_until"), now_date_iso=now_date):
        return None

    lev = float(clamp01(inputs.leverage))
    # Very low leverage: never request trade (can still complain).
    if lev < 0.30:
        return None

    gp = int(inputs.games_played or 0)
    if gp > 0 and gp < int(ecfg.min_games_for_events or 2):
        return None

    ego = mental_norm(inputs.mental, "ego")
    amb = mental_norm(inputs.mental, "ambition")
    loy = mental_norm(inputs.mental, "loyalty")

    # v2: include more sources of discontent
    fr_role = float(clamp01(state.get("role_frustration")))
    fr_team = float(clamp01(state.get("team_frustration")))
    fr_contract = float(clamp01(state.get("contract_frustration")))
    fr_health = float(clamp01(state.get("health_frustration")))
    fr_chem = float(clamp01(state.get("chemistry_frustration")))

    request_score = (
        + 0.30 * fr_team
        + 0.25 * fr_role
        + 0.15 * fr_contract
        + 0.15 * fr_health
        + 0.10 * fr_chem
        + 0.05 * ego
    )
    request_score *= (0.40 + 0.60 * lev)

    base = float(ecfg.trade_request_threshold_base)
    threshold = (
        base
        + float(ecfg.trade_request_threshold_loyalty_bonus) * loy
        + float(ecfg.trade_request_threshold_ambition_bonus) * amb
    )

    # Trust can delay a request slightly.
    trust = float(clamp01(state.get("trust")))
    threshold += 0.05 * (trust - 0.5)

    softness = max(1e-6, float(ecfg.trade_request_softness))
    p = clamp01((request_score - threshold) / softness)

    # High ego/ambition increases chance of pulling the trigger.
    p *= clamp(0.75 + 0.55 * ego + 0.35 * amb - 0.10 * loy, 0.10, 2.00)
    p *= clamp(0.35 + 0.65 * float(clamp01(sample_weight)), 0.20, 1.00)
    p = clamp01(p)

    prev_level = int(state.get("trade_request_level") or 0)

    roll = stable_u01(inputs.player_id, inputs.month_key, "trade_request", int(prev_level))
    if roll >= p:
        return None

    new_level = 1 if prev_level <= 0 else prev_level

    # Escalate to public if already private and pressure is significantly above threshold.
    et = cfg.event_types.get("trade_request", "TRADE_REQUEST")
    if prev_level == 1 and (request_score - threshold) >= float(ecfg.trade_request_public_escalate_delta):
        et = cfg.event_types.get("trade_request_public", "TRADE_REQUEST_PUBLIC")
        new_level = 2

    event_id = make_event_id("agency", inputs.player_id, inputs.month_key, et)

    severity = clamp01((0.60 * request_score + 0.20 * ego + 0.20 * lev))
    severity *= clamp(0.60 + 0.40 * float(clamp01(sample_weight)), 0.60, 1.00)

    payload = {
        "axis": "TRADE",
        "role_bucket": str(inputs.role_bucket or "UNKNOWN"),
        "leverage": float(lev),
        "trust": float(trust),
        "role_frustration": float(fr_role),
        "team_frustration": float(fr_team),
        "contract_frustration": float(fr_contract),
        "health_frustration": float(fr_health),
        "chemistry_frustration": float(fr_chem),
        "request_score": float(request_score),
        "threshold": float(threshold),
        "ego": float(ego),
        "ambition": float(amb),
        "loyalty": float(loy),
        "public": bool(new_level >= 2),
        "level": int(new_level),
        "sample_games_played": int(gp),
        "sample_weight": float(clamp01(sample_weight)),
    }

    state_updates = {
        "trade_request_level": int(new_level),
        "cooldown_trade_until": date_add_days(now_date, int(ecfg.cooldown_trade_days)),
    }

    # Trade requests should almost always win selection if triggered.
    priority = float(severity) + 0.75

    return EventCandidate(
        axis="TRADE",
        priority=priority,
        event={
            "event_id": event_id,
            "player_id": inputs.player_id,
            "team_id": inputs.team_id,
            "season_year": int(inputs.season_year),
            "date": now_date,
            "event_type": et,
            "severity": float(severity),
            "payload": payload,
        },
        state_updates=state_updates,
        mem_updates={
            "last_major_issue_axis": "TRADE",
            "last_major_issue_month": str(inputs.month_key),
            "public_blowups_inc": 1 if int(new_level) >= 2 else 0,
        },
    )


def _pick_best_candidate(*, candidates: List[EventCandidate], inputs: MonthlyPlayerInputs) -> Optional[EventCandidate]:
    if not candidates:
        return None

    pid = str(inputs.player_id)
    mk = str(inputs.month_key)

    best: Optional[EventCandidate] = None
    best_key: Tuple[float, float] = (-1.0, -1.0)

    for c in candidates:
        pr = float(c.priority)
        # deterministic tiebreaker
        tie = float(stable_u01(pid, mk, "pick_issue", str(c.axis)))
        key = (pr, tie)
        if best is None or key > best_key:
            best = c
            best_key = key

    return best


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_monthly_player_tick(
    prev_state: Optional[Mapping[str, Any]],
    *,
    inputs: MonthlyPlayerInputs,
    cfg: AgencyConfig,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Apply one player's monthly tick.

    Args:
        prev_state: existing SSOT state dict (or None for new).
        inputs: MonthlyPlayerInputs
        cfg: AgencyConfig

    Returns:
        (new_state_dict, events)

    The returned state dict matches columns in player_agency_state, but uses
    Python-native types:
      - context: dict
    """
    # ------------------------------------------------------------------
    # Initialize state with safe defaults.
    # ------------------------------------------------------------------
    st: Dict[str, Any] = {
        "player_id": str(inputs.player_id),
        "team_id": str(inputs.team_id).upper(),
        "season_year": int(inputs.season_year),
        "role_bucket": str(inputs.role_bucket or "UNKNOWN"),
        "leverage": float(clamp01(inputs.leverage)),
        "minutes_expected_mpg": float(max(0.0, inputs.expected_mpg)),
        "minutes_actual_mpg": 0.0,
        "minutes_frustration": 0.0,
        "team_frustration": 0.0,
        "trust": 0.5,
        "role_frustration": 0.0,
        "contract_frustration": 0.0,
        "health_frustration": 0.0,
        "chemistry_frustration": 0.0,
        "usage_frustration": 0.0,
        "starts_rate": float(clamp01(inputs.starts_rate)),
        "closes_rate": float(clamp01(inputs.closes_rate)),
        "usage_share": float(clamp01(inputs.usage_share)),

        # v3: self expectations (player self-perception; initialized/updated monthly)
        "self_expected_mpg": None,
        "self_expected_starts_rate": None,
        "self_expected_closes_rate": None,

        # v3: dynamic stances (0..1)
        "stance_skepticism": 0.0,
        "stance_resentment": 0.0,
        "stance_hardball": 0.0,

        "trade_request_level": 0,
        "cooldown_minutes_until": None,
        "cooldown_trade_until": None,
        "cooldown_help_until": None,
        "cooldown_contract_until": None,
        "cooldown_role_until": None,
        "cooldown_health_until": None,
        "cooldown_chemistry_until": None,
        "escalation_role": 0,
        "escalation_contract": 0,
        "escalation_team": 0,
        "escalation_health": 0,
        "escalation_chemistry": 0,
        "last_processed_month": str(inputs.month_key),
        "context": {},
    }

    # Preserve prior state fields.
    prev_ctx: Dict[str, Any] = {}
    if prev_state:
        # copy known fields (defensive)
        for k in list(st.keys()):
            if k in prev_state and prev_state.get(k) is not None:
                st[k] = prev_state.get(k)

        # Ensure identity fields reflect current roster.
        st["player_id"] = str(inputs.player_id)
        st["team_id"] = str(inputs.team_id).upper()
        st["season_year"] = int(inputs.season_year)

        pc = prev_state.get("context")
        if isinstance(pc, Mapping):
            prev_ctx = dict(pc)

    # Expectations (current month)
    st["role_bucket"] = str(inputs.role_bucket or st.get("role_bucket") or "UNKNOWN")
    st["leverage"] = float(clamp01(inputs.leverage))
    st["minutes_expected_mpg"] = float(max(0.0, inputs.expected_mpg))
    st["starts_rate"] = float(clamp01(inputs.starts_rate))
    st["closes_rate"] = float(clamp01(inputs.closes_rate))
    st["usage_share"] = float(clamp01(inputs.usage_share))

    # Actuals
    gp = int(inputs.games_played or 0)
    mins = float(max(0.0, inputs.actual_minutes))
    actual_mpg = mins / float(gp) if gp > 0 else 0.0
    st["minutes_actual_mpg"] = float(actual_mpg)

    try:
        full = int(cfg.month_context.full_weight_games)
    except Exception:
        full = 10
    full = max(1, full)
    if gp > 0:
        sample_weight = float(clamp01(gp / float(full)))
    else:
        exp_mpg = float(st.get("minutes_expected_mpg") or 0.0)
        sample_weight = 1.0 if mins <= 0.0 and exp_mpg > 0.0 else 0.0
       
    # ------------------------------------------------------------------
    # v3: self expectations + stance decay (FM-style)
    # ------------------------------------------------------------------

    self_exp_updates, self_exp_meta = update_self_expectations_monthly(
        state=st,
        expected_mpg=float(st.get("minutes_expected_mpg") or 0.0),
        role_bucket=str(st.get("role_bucket") or inputs.role_bucket or "UNKNOWN"),
        mental=inputs.mental or {},
        cfg=cfg,
    )
    if self_exp_updates:
        st.update(self_exp_updates)

    stance_updates, stance_meta = apply_monthly_stance_decay(
        state=st,
        mental=inputs.mental or {},
        cfg=cfg,
    )
    if stance_updates:
        st.update(stance_updates)

    beh_profile, beh_meta = compute_behavior_profile(
        mental=inputs.mental or {},
        trust=st.get("trust", 0.5),
        stance_skepticism=st.get("stance_skepticism", 0.0),
        stance_resentment=st.get("stance_resentment", 0.0),
        stance_hardball=st.get("stance_hardball", 0.0),
    )

    # ------------------------------------------------------------------
    # Update frustrations and trust.
    # ------------------------------------------------------------------
    context: Dict[str, Any] = {}

    new_m_fr, meta_m = _update_minutes_frustration(
        prev=float(st.get("minutes_frustration") or 0.0),
        expected_mpg=float(st.get("self_expected_mpg") or st.get("minutes_expected_mpg") or 0.0),
        actual_mpg=float(actual_mpg),
        games_played=int(gp),
        games_possible=int(getattr(inputs, "games_possible", 0) or 0),
        mental=inputs.mental,
        leverage=float(st.get("leverage") or 0.0),
        injury_status=inputs.injury_status,
        injury_multiplier=getattr(inputs, "injury_multiplier", None),
        cfg=cfg,
    )
    st["minutes_frustration"] = float(new_m_fr)
    # Add team/self expectation context for explainability.
    try:
        meta_m = dict(meta_m or {})
        meta_m.setdefault("team_expected_mpg", float(st.get("minutes_expected_mpg") or 0.0))
        meta_m.setdefault("self_expected_mpg", float(st.get("self_expected_mpg") or st.get("minutes_expected_mpg") or 0.0))
    except Exception:
        pass

    new_t_fr, meta_t = _update_team_frustration(
        prev=float(st.get("team_frustration") or 0.0),
        team_win_pct=float(inputs.team_win_pct),
        team_strategy=getattr(inputs, "team_strategy", None),
        age=getattr(inputs, "age", None),
        mental=inputs.mental,
        leverage=float(st.get("leverage") or 0.0),
        cfg=cfg,
    )
    st["team_frustration"] = float(new_t_fr)

    new_r_fr, meta_r = _update_role_frustration(
        prev=float(st.get("role_frustration") or 0.0),
        minutes_frustration=float(st.get("minutes_frustration") or 0.0),
        role_bucket=str(st.get("role_bucket") or "UNKNOWN"),
        starts_rate=float(st.get("starts_rate") or 0.0),
        closes_rate=float(st.get("closes_rate") or 0.0),
        expected_starts_rate=(
            float(st.get("self_expected_starts_rate")) if st.get("self_expected_starts_rate") is not None else None
        ),
        expected_closes_rate=(
            float(st.get("self_expected_closes_rate")) if st.get("self_expected_closes_rate") is not None else None
        ),
        mental=inputs.mental,
        leverage=float(st.get("leverage") or 0.0),
        injury_status=inputs.injury_status,
        injury_multiplier=getattr(inputs, "injury_multiplier", None),
        cfg=cfg,
    )
    st["role_frustration"] = float(new_r_fr)

    # Trust update uses multi-axis view.
    trust0 = float(st.get("trust") or 0.5)

    # We compute contract/health/chemistry with trust0 first, then re-run trust.
    new_c_fr, meta_c = _update_contract_frustration(
        prev=float(st.get("contract_frustration") or 0.0),
        season_year=int(inputs.season_year),
        contract_end_season_id=inputs.contract_end_season_id,
        mental=inputs.mental,
        leverage=float(st.get("leverage") or 0.0),
        trust=float(trust0),
        cfg=cfg,
    )
    st["contract_frustration"] = float(new_c_fr)

    new_h_fr, meta_h = _update_health_frustration(
        prev=float(st.get("health_frustration") or 0.0),
        fatigue_st=inputs.fatigue_st,
        fatigue_lt=inputs.fatigue_lt,
        injury_status=inputs.injury_status,
        mental=inputs.mental,
        leverage=float(st.get("leverage") or 0.0),
        cfg=cfg,
    )
    st["health_frustration"] = float(new_h_fr)

    new_ch_fr, meta_ch = _update_chemistry_frustration(
        prev=float(st.get("chemistry_frustration") or 0.0),
        team_frustration=float(st.get("team_frustration") or 0.0),
        trust=float(trust0),
        mental=inputs.mental,
        leverage=float(st.get("leverage") or 0.0),
        cfg=cfg,
    )
    st["chemistry_frustration"] = float(new_ch_fr)

    new_u_fr, meta_u = _update_usage_frustration(
        prev=float(st.get("usage_frustration") or 0.0),
        role_bucket=str(st.get("role_bucket") or "UNKNOWN"),
        usage_share=float(st.get("usage_share") or 0.0),
        mental=inputs.mental,
        cfg=cfg,
    )
    st["usage_frustration"] = float(new_u_fr)

    new_trust, meta_trust = _update_trust_v2(
        prev=float(trust0),
        frustrations={
            "minutes": float(st.get("minutes_frustration") or 0.0),
            "team": float(st.get("team_frustration") or 0.0),
            "role": float(st.get("role_frustration") or 0.0),
            "contract": float(st.get("contract_frustration") or 0.0),
            "health": float(st.get("health_frustration") or 0.0),
            "chemistry": float(st.get("chemistry_frustration") or 0.0),
        },
        mental=inputs.mental,
        cfg=cfg,
    )
    st["trust"] = float(new_trust)

    # Context
    context["minutes"] = meta_m
    context["team"] = meta_t
    context["role"] = meta_r
    context["contract"] = meta_c
    context["health"] = meta_h
    context["chemistry"] = meta_ch
    context["usage"] = meta_u
    context["trust"] = meta_trust

    # v3: self expectations + stances
    context["self_expectations"] = dict(self_exp_meta or {})
    context["stance_decay"] = dict(stance_meta or {})
    context["behavior_profile"] = dict(beh_meta or {})

    context["player"] = {
        "ovr": inputs.ovr,
        "age": inputs.age,
        "role_bucket": str(inputs.role_bucket or "UNKNOWN"),
        "leverage": float(clamp01(inputs.leverage)),
        "games_played": int(gp),
        "month_key": str(inputs.month_key),
        "injury_status": str(inputs.injury_status or ""),
        "injury_multiplier": getattr(inputs, "injury_multiplier", None),
        "team_strategy": str(inputs.team_strategy or ""),
    }

    context["sample"] = {
        "games_played": int(gp),
        "games_possible": int(getattr(inputs, "games_possible", 0) or 0),
        "dnp_rate": float((meta_m or {}).get("dnp_rate") or 0.0),
        "full_weight_games": int(full),
        "sample_weight": float(sample_weight),
    }

    # Preserve / carry memory frame.
    mem0 = prev_ctx.get("mem") if isinstance(prev_ctx.get("mem"), Mapping) else {}
    mem: Dict[str, Any] = dict(mem0) if isinstance(mem0, Mapping) else {}
    context["mem"] = mem

    # Persist context

    st["context"] = context
    st["last_processed_month"] = str(inputs.month_key)

    # ------------------------------------------------------------------
    # Stage decay (spam control / realism)
    # ------------------------------------------------------------------
    ecfg = cfg.events

    st["escalation_role"] = decay_stage(
        st.get("escalation_role"),
        frustration=float(st.get("role_frustration") or 0.0),
        threshold=float(ecfg.role_issue_threshold),
    )
    st["escalation_contract"] = decay_stage(
        st.get("escalation_contract"),
        frustration=float(st.get("contract_frustration") or 0.0),
        threshold=float(ecfg.contract_issue_threshold),
    )
    st["escalation_health"] = decay_stage(
        st.get("escalation_health"),
        frustration=float(st.get("health_frustration") or 0.0),
        threshold=float(ecfg.health_issue_threshold),
    )
    st["escalation_chemistry"] = decay_stage(
        st.get("escalation_chemistry"),
        frustration=float(st.get("chemistry_frustration") or 0.0),
        threshold=float(ecfg.chemistry_issue_threshold),
    )
    st["escalation_team"] = decay_stage(
        st.get("escalation_team"),
        frustration=float(st.get("team_frustration") or 0.0),
        threshold=float(ecfg.team_issue_threshold),
    )

    # ------------------------------------------------------------------
    # Build candidates and pick one issue (spam guard).
    # ------------------------------------------------------------------
    candidates: List[EventCandidate] = []

    for cand in (
        _candidate_trade_request(state=st, inputs=inputs, cfg=cfg, sample_weight=sample_weight),
        _candidate_minutes_complaint(state=st, inputs=inputs, cfg=cfg, sample_weight=sample_weight),
        _candidate_role_issue(state=st, inputs=inputs, cfg=cfg, sample_weight=sample_weight),
        _candidate_contract_issue(state=st, inputs=inputs, cfg=cfg, sample_weight=sample_weight),
        _candidate_health_issue(state=st, inputs=inputs, cfg=cfg, sample_weight=sample_weight),
        _candidate_team_issue(state=st, inputs=inputs, cfg=cfg, sample_weight=sample_weight),
        _candidate_chemistry_issue(state=st, inputs=inputs, cfg=cfg, sample_weight=sample_weight),
    ):
        if cand is not None:
            candidates.append(cand)

    chosen = _pick_best_candidate(candidates=candidates, inputs=inputs)

    events: List[Dict[str, Any]] = []

    if chosen is not None:
        # Apply state updates for the chosen issue (cooldowns + stages, etc.)
        for k, v in (chosen.state_updates or {}).items():
            st[k] = v

        # Memory updates (small frame; keep stable keys)
        if isinstance(context.get("mem"), dict):
            mem = context["mem"]  # type: ignore[assignment]
        else:
            mem = {}
            context["mem"] = mem

        for mk, mv in (chosen.mem_updates or {}).items():
            if mk.endswith("_inc"):
                # increment helper
                k = mk[:-4]
                try:
                    mem[k] = int(mem.get(k) or 0) + int(mv or 0)
                except Exception:
                    mem[k] = int(mv or 0)
            else:
                mem[mk] = mv

        # Explainability
        context["issue_choice"] = {
            "axis": str(chosen.axis),
            "priority": float(chosen.priority),
            "event_type": str((chosen.event or {}).get("event_type") or ""),
        }
 
        events.append(dict(chosen.event))
 
    return st, events
