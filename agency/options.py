from __future__ import annotations

"""Player option / ETO decisions.

This module provides deterministic, market-aware option decisions.

Core idea
---------
- Compare option salary vs a market AAV estimate derived from OVR.
- If strongly favorable, decision is deterministic.
- Otherwise, use a *deterministic probabilistic* model modulated by mental traits.

Why deterministic randomness?
----------------------------
- Keeps outcomes stable across reloads given the same inputs.
- Still provides variety (not every borderline case behaves identically).
"""

from typing import Any, Dict, Literal, Mapping, Tuple

from .config import OptionsConfig
from .types import PlayerOptionDecision, PlayerOptionInputs
from .utils import clamp, clamp01, mental_norm, safe_float, sigmoid, stable_u01


Decision = Literal["EXERCISE", "DECLINE"]


def _resolve_expected_salary_scale(cfg: OptionsConfig) -> Tuple[float, float, Dict[str, Any]]:
    """Resolve expected-salary curve scale.

    Returns (midpoint_dollars, span_dollars, meta).

    - If cfg.salary_cap is provided (> eps), we use cap-share ratios.
    - Otherwise we fall back to legacy absolute-dollar defaults.
    """
    cap = float(safe_float(getattr(cfg, "salary_cap", None), 0.0))
    if cap > 1e-9:
        mid_pct = float(safe_float(getattr(cfg, "expected_salary_midpoint_cap_pct", None), 0.0))
        span_pct = float(safe_float(getattr(cfg, "expected_salary_span_cap_pct", None), 0.0))
        midpoint = cap * mid_pct
        span = cap * span_pct
        return float(midpoint), float(span), {
            "source": "cap_pct",
            "salary_cap": float(cap),
            "mid_pct": float(mid_pct),
            "span_pct": float(span_pct),
        }

    return float(cfg.expected_salary_midpoint), float(cfg.expected_salary_span), {
        "source": "legacy_abs",
        "salary_cap": None,
    }


def expected_market_aav_from_ovr(ovr: float, *, cfg: OptionsConfig) -> float:
    """Estimate market AAV from OVR using a sigmoid mapping.

    This intentionally mirrors the general shape used elsewhere (trade market pricing),
    but lives here so agency decisions can be tuned independently.
    """
    x = (float(ovr) - float(cfg.expected_salary_ovr_center)) / max(float(cfg.expected_salary_ovr_scale), 1e-9)
    s = sigmoid(x)

    midpoint, span, _meta = _resolve_expected_salary_scale(cfg)
    lo = float(midpoint) - float(span)
    hi = float(midpoint) + float(span)

    # Defensive: keep ordering and non-negative lower bound.
    if hi < lo:
        lo, hi = hi, lo
    if lo < 0.0:
        lo = 0.0
    
    return float(lo + (hi - lo) * s)


def decide_player_option(
    inputs: PlayerOptionInputs,
    *,
    cfg: OptionsConfig,
    seed_salt: str = "",
) -> PlayerOptionDecision:
    """Return option decision + meta.

    Args:
        inputs: PlayerOptionInputs
        cfg: OptionsConfig
        seed_salt: optional extra salt (e.g., season_year) to avoid cross-season collisions

    Returns:
        PlayerOptionDecision(decision, meta)
    """
    ovr = int(inputs.ovr)
    age = int(inputs.age)
    option_salary = float(max(0.0, inputs.option_salary))

    market_aav = expected_market_aav_from_ovr(float(ovr), cfg=cfg)

    # Useful debug metadata about salary scale.
    cap = float(safe_float(getattr(cfg, "salary_cap", None), 0.0))
    midpoint, span, scale_meta = _resolve_expected_salary_scale(cfg)
    market_cap_pct = (float(market_aav) / float(cap)) if cap > 1e-9 else None

    # If market estimate is near zero, be conservative.
    if market_aav <= 1.0:
        return PlayerOptionDecision("EXERCISE", {"reason": "market_aav_too_low", "market_aav": market_aav})

    value_ratio = option_salary / float(market_aav)

    meta: Dict[str, Any] = {
        "ovr": int(ovr),
        "age": int(age),
        "option_salary": float(option_salary),
        "market_aav": float(market_aav),
        "market_aav_cap_pct": market_cap_pct,
        "value_ratio": float(value_ratio),
        "salary_cap": (cap if cap > 1e-9 else None),
        "expected_salary_scale": {
            **(scale_meta or {}),
            "midpoint": float(midpoint),
            "span": float(span),
        },
        "team_id": inputs.team_id,
        "team_win_pct": inputs.team_win_pct,
        "injury_risk": float(clamp01(inputs.injury_risk)),
    }

    # Hard decisions
    if value_ratio >= float(cfg.hard_exercise_ratio):
        meta["reason"] = "hard_exercise"
        return PlayerOptionDecision("EXERCISE", meta)

    if value_ratio <= float(cfg.hard_decline_ratio):
        meta["reason"] = "hard_decline"
        return PlayerOptionDecision("DECLINE", meta)

    # Ambiguous: compute probability of DECLINE.
    # - better market vs option => higher p_decline
    # - ambition/ego pushes towards decline (seek better deal)
    # - loyalty pushes towards exercise (stay / take safe money)
    # - age/injury pushes towards exercise (risk aversion)

    amb = mental_norm(inputs.mental, "ambition")
    loy = mental_norm(inputs.mental, "loyalty")
    ego = mental_norm(inputs.mental, "ego")

    # Normalize age into 0..1 (roughly 22..37 range)
    age_norm = clamp01((float(age) - 22.0) / 15.0)
    injury = clamp01(inputs.injury_risk)

    # Logit: positive => more likely DECLINE
    logit = (
        float(cfg.w_value) * (float(cfg.ambiguous_value_center) - float(value_ratio))
        + float(cfg.w_ambition) * (amb - 0.5)
        + float(cfg.w_ego) * (ego - 0.5)
        + float(cfg.w_loyalty) * (loy - 0.5)
        + float(cfg.w_age) * (age_norm - 0.5)
        + float(cfg.w_injury_risk) * (injury - 0.5)
    )

    p_decline = clamp01(sigmoid(float(logit)))

    roll = stable_u01("option", inputs.player_id, ovr, age, int(option_salary), seed_salt)

    decision: Decision = "DECLINE" if roll < p_decline else "EXERCISE"

    meta.update(
        {
            "reason": "ambiguous_probabilistic",
            "logit": float(logit),
            "p_decline": float(p_decline),
            "roll": float(roll),
            "ambition": float(amb),
            "loyalty": float(loy),
            "ego": float(ego),
            "age_norm": float(age_norm),
        }
    )

    return PlayerOptionDecision(decision, meta)


def decide_player_option_simple(
    *,
    player_id: str,
    ovr: int,
    age: int,
    option_salary: float,
    mental: Mapping[str, Any] | None = None,
    injury_risk: float = 0.0,
    cfg: OptionsConfig,
    seed_salt: str = "",
) -> Decision:
    """Compatibility wrapper: return just the Decision."""
    res = decide_player_option(
        PlayerOptionInputs(
            player_id=str(player_id),
            ovr=int(ovr),
            age=int(age),
            option_salary=float(option_salary),
            injury_risk=float(injury_risk),
            mental=mental or {},
        ),
        cfg=cfg,
        seed_salt=seed_salt,
    )
    return res.decision


