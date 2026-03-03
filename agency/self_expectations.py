from __future__ import annotations

"""Self-expectations (player self-perception) for the agency system.

Motivation
----------
The existing agency system has a strong "team expectation" notion:
- role bucket => expected MPG
- role bucket => expected starts/closes rates

For Football Manager–style agency, this is not enough.
Players need *self expectations* ("how I see my own value") that can differ
from the team's plan.

This module provides deterministic, DB-free helpers to:
- bootstrap self expectations when missing
- update self expectations monthly (as an EMA drift toward a target)

SSOT policy
-----------
Self expectations are intended to be stored in player_agency_state (SSOT) because
they are:
- persistent across months
- influenced by prior months and interactions
- required for consistent dialogue/negotiation logic

Call sites (tick/service/interaction) should:
- call update_self_expectations_monthly() early in a monthly tick
- store returned updates into state
- optionally store returned meta into state.context for debugging/telemetry

This file is pure and contains no DB I/O.
"""

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from .config import AgencyConfig
from .metrics import role_expected_rates
from .utils import clamp, clamp01, mental_norm, safe_float


@dataclass(frozen=True, slots=True)
class _SelfExpDefaults:
    """Fallback config values used until AgencyConfig is expanded."""

    mpg_delta_scale: float = 0.30
    rate_delta_scale: float = 0.22

    drift_up: float = 0.18
    drift_down: float = 0.10

    down_sticky_ego: float = 0.55
    down_sticky_ambition: float = 0.35

    min_self_mpg_floor: float = 6.0
    max_self_mpg_ceiling: float = 40.0


def _get_self_exp_cfg(cfg: AgencyConfig) -> Any:
    """Return self-expectations config object (supports gradual integration)."""
    # Planned name: cfg.self_exp
    if hasattr(cfg, "self_exp"):
        return getattr(cfg, "self_exp")
    # Alternative name
    if hasattr(cfg, "self_expectations"):
        return getattr(cfg, "self_expectations")
    return _SelfExpDefaults()


def _team_expected_mpg(expected_mpg: Any, role_bucket: str, cfg: AgencyConfig) -> float:
    """Robustly resolve team expected MPG."""
    x = safe_float(expected_mpg, 0.0)
    if x > 0.0:
        return float(x)
    rb = str(role_bucket or "UNKNOWN").upper()
    try:
        v = cfg.expectations.expected_mpg_by_role.get(rb)
    except Exception:
        v = None
    if v is None:
        try:
            v = cfg.expectations.expected_mpg_by_role.get("UNKNOWN", 12.0)
        except Exception:
            v = 12.0
    return float(max(0.0, safe_float(v, 12.0)))


def compute_personality_score(mental: Mapping[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """Compute a signed personality score in [-1, 1].

    Positive => self expectations drift above team plan.
    Negative => self expectations drift below team plan.

    This score is intentionally linear and easy to tune.
    """
    work = mental_norm(mental, "work_ethic")
    coach = mental_norm(mental, "coachability")
    amb = mental_norm(mental, "ambition")
    loy = mental_norm(mental, "loyalty")
    ego = mental_norm(mental, "ego")
    adapt = mental_norm(mental, "adaptability")

    raw = (
        +0.55 * ego
        +0.45 * amb
        +0.15 * work
        -0.35 * coach
        -0.25 * loy
        -0.25 * adapt
    )

    # Clamp to [-1, 1] for interpretability.
    p = float(clamp(raw, -1.0, 1.0))

    meta = {
        "mental": {
            "work_ethic": float(work),
            "coachability": float(coach),
            "ambition": float(amb),
            "loyalty": float(loy),
            "ego": float(ego),
            "adaptability": float(adapt),
        },
        "raw": float(raw),
        "score": float(p),
    }
    return p, meta


def _drift_rate(*, target: float, prev: float, mental: Mapping[str, Any], cfg: AgencyConfig) -> Tuple[float, Dict[str, Any]]:
    """Return drift rate for EMA update (up/down asymmetry)."""
    scfg = _get_self_exp_cfg(cfg)

    ego = mental_norm(mental, "ego")
    amb = mental_norm(mental, "ambition")

    rate_up = float(getattr(scfg, "drift_up", 0.18))

    base_down = float(getattr(scfg, "drift_down", 0.10))
    sticky_ego = float(getattr(scfg, "down_sticky_ego", 0.55))
    sticky_amb = float(getattr(scfg, "down_sticky_ambition", 0.35))

    # Ego/ambition make it harder to lower expectations.
    down_mult = 1.0 - (sticky_ego * ego) - (sticky_amb * amb)
    # Ensure a minimum ability to adapt downward.
    rate_down = max(0.02, base_down * down_mult)

    if target >= prev:
        rate = float(rate_up)
        direction = "up"
    else:
        rate = float(rate_down)
        direction = "down"

    meta = {
        "direction": direction,
        "rate": float(rate),
        "rate_up": float(rate_up),
        "rate_down": float(rate_down),
        "down_mult": float(down_mult),
        "ego": float(ego),
        "ambition": float(amb),
    }
    return float(rate), meta


def bootstrap_self_expectations(
    *,
    state: Mapping[str, Any],
    expected_mpg: Any,
    role_bucket: str,
    mental: Mapping[str, Any],
    cfg: AgencyConfig,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Bootstrap self expectations when missing.

    This sets self expectations directly to the computed target (no drift).

    Args:
        state: current agency state mapping (only used for context; not mutated).
        expected_mpg: team expected MPG for this month.
        role_bucket: role bucket label.
        mental: player mental traits.
        cfg: AgencyConfig

    Returns:
        (updates dict, meta dict)
    """
    scfg = _get_self_exp_cfg(cfg)

    team_mpg = _team_expected_mpg(expected_mpg, role_bucket, cfg)
    exp_starts, exp_closes = role_expected_rates(role_bucket, cfg=cfg)

    p, pmeta = compute_personality_score(mental)

    mpg_scale = float(getattr(scfg, "mpg_delta_scale", 0.30))
    rate_scale = float(getattr(scfg, "rate_delta_scale", 0.22))

    min_floor = float(getattr(scfg, "min_self_mpg_floor", 6.0))
    max_ceil = float(getattr(scfg, "max_self_mpg_ceiling", 40.0))

    target_mpg = clamp(team_mpg + (mpg_scale * p * team_mpg), min_floor, max_ceil)
    target_s = clamp01(exp_starts + (rate_scale * p))
    target_c = clamp01(exp_closes + (rate_scale * p))

    updates = {
        "self_expected_mpg": float(target_mpg),
        "self_expected_starts_rate": float(target_s),
        "self_expected_closes_rate": float(target_c),
    }

    meta: Dict[str, Any] = {
        "team_expected_mpg": float(team_mpg),
        "team_expected_starts_rate": float(exp_starts),
        "team_expected_closes_rate": float(exp_closes),
        "target_self_mpg": float(target_mpg),
        "target_self_starts_rate": float(target_s),
        "target_self_closes_rate": float(target_c),
        "scales": {"mpg_delta_scale": float(mpg_scale), "rate_delta_scale": float(rate_scale)},
        "personality": dict(pmeta),
        "mode": "bootstrap",
    }

    # Include current state values for debugging.
    meta["prev"] = {
        "self_expected_mpg": state.get("self_expected_mpg") if isinstance(state, Mapping) else None,
        "self_expected_starts_rate": state.get("self_expected_starts_rate") if isinstance(state, Mapping) else None,
        "self_expected_closes_rate": state.get("self_expected_closes_rate") if isinstance(state, Mapping) else None,
    }
    return updates, meta


def update_self_expectations_monthly(
    *,
    state: Mapping[str, Any],
    expected_mpg: Any,
    role_bucket: str,
    mental: Mapping[str, Any],
    cfg: AgencyConfig,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Update self expectations for a month.

    This drifts existing self expectations toward a computed target.

    If self expectations are missing, falls back to bootstrap.

    Returns:
        (updates dict, meta dict)
    """
    # Read prev; accept missing/None.
    prev_mpg = state.get("self_expected_mpg") if isinstance(state, Mapping) else None
    prev_s = state.get("self_expected_starts_rate") if isinstance(state, Mapping) else None
    prev_c = state.get("self_expected_closes_rate") if isinstance(state, Mapping) else None

    if prev_mpg is None or prev_s is None or prev_c is None:
        return bootstrap_self_expectations(
            state=state,
            expected_mpg=expected_mpg,
            role_bucket=role_bucket,
            mental=mental,
            cfg=cfg,
        )

    scfg = _get_self_exp_cfg(cfg)

    team_mpg = _team_expected_mpg(expected_mpg, role_bucket, cfg)
    exp_starts, exp_closes = role_expected_rates(role_bucket, cfg=cfg)

    p, pmeta = compute_personality_score(mental)

    mpg_scale = float(getattr(scfg, "mpg_delta_scale", 0.30))
    rate_scale = float(getattr(scfg, "rate_delta_scale", 0.22))

    min_floor = float(getattr(scfg, "min_self_mpg_floor", 6.0))
    max_ceil = float(getattr(scfg, "max_self_mpg_ceiling", 40.0))

    target_mpg = clamp(team_mpg + (mpg_scale * p * team_mpg), min_floor, max_ceil)
    target_s = clamp01(exp_starts + (rate_scale * p))
    target_c = clamp01(exp_closes + (rate_scale * p))

    prev_mpg_f = float(safe_float(prev_mpg, team_mpg))
    prev_s_f = float(clamp01(safe_float(prev_s, exp_starts)))
    prev_c_f = float(clamp01(safe_float(prev_c, exp_closes)))

    rate_mpg, rate_meta_mpg = _drift_rate(target=float(target_mpg), prev=float(prev_mpg_f), mental=mental, cfg=cfg)
    rate_s, rate_meta_s = _drift_rate(target=float(target_s), prev=float(prev_s_f), mental=mental, cfg=cfg)
    rate_c, rate_meta_c = _drift_rate(target=float(target_c), prev=float(prev_c_f), mental=mental, cfg=cfg)

    new_mpg = prev_mpg_f + rate_mpg * (float(target_mpg) - prev_mpg_f)
    new_s = prev_s_f + rate_s * (float(target_s) - prev_s_f)
    new_c = prev_c_f + rate_c * (float(target_c) - prev_c_f)

    updates = {
        "self_expected_mpg": float(clamp(new_mpg, min_floor, max_ceil)),
        "self_expected_starts_rate": float(clamp01(new_s)),
        "self_expected_closes_rate": float(clamp01(new_c)),
    }

    meta: Dict[str, Any] = {
        "team_expected_mpg": float(team_mpg),
        "team_expected_starts_rate": float(exp_starts),
        "team_expected_closes_rate": float(exp_closes),
        "target_self_mpg": float(target_mpg),
        "target_self_starts_rate": float(target_s),
        "target_self_closes_rate": float(target_c),
        "prev": {
            "self_expected_mpg": float(prev_mpg_f),
            "self_expected_starts_rate": float(prev_s_f),
            "self_expected_closes_rate": float(prev_c_f),
        },
        "new": dict(updates),
        "rates": {"mpg": rate_meta_mpg, "starts": rate_meta_s, "closes": rate_meta_c},
        "scales": {"mpg_delta_scale": float(mpg_scale), "rate_delta_scale": float(rate_scale)},
        "personality": dict(pmeta),
        "mode": "monthly_update",
    }

    return updates, meta
