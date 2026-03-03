from __future__ import annotations

"""Small, deterministic metric helpers for agency tick v2.

All functions here are pure and cheap.
They intentionally do not read DB/SSOT.
"""

from typing import Any, Dict, Optional, Tuple

from .config import AgencyConfig
from .utils import clamp01, safe_float


def role_expected_rates(role_bucket: str, *, cfg: AgencyConfig) -> Tuple[float, float]:
    """Return (expected_starts_rate, expected_closes_rate) for a role bucket."""
    rb = str(role_bucket or "UNKNOWN").upper()
    exp_s = cfg.expectations.expected_starts_rate_by_role.get(rb, cfg.expectations.expected_starts_rate_by_role.get("UNKNOWN", 0.30))
    exp_c = cfg.expectations.expected_closes_rate_by_role.get(rb, cfg.expectations.expected_closes_rate_by_role.get("UNKNOWN", 0.10))
    return float(clamp01(exp_s)), float(clamp01(exp_c))


def role_status_pressure(
    *,
    role_bucket: str,
    starts_rate: Any,
    closes_rate: Any,
    expected_starts_rate: Optional[Any] = None,
    expected_closes_rate: Optional[Any] = None,
    cfg: AgencyConfig,
) -> Tuple[float, Dict[str, Any]]:
    """Compute pressure from missing starts/closes relative to expectation.

    Returns:
        (pressure in 0..1, meta dict)
    """
    s = float(clamp01(starts_rate))
    c = float(clamp01(closes_rate))

    base_exp_s, base_exp_c = role_expected_rates(role_bucket, cfg=cfg)

    exp_s_src = "role_bucket"
    exp_c_src = "role_bucket"

    if expected_starts_rate is not None:
        exp_s = float(clamp01(expected_starts_rate))
        exp_s_src = "override"
    else:
        exp_s = float(base_exp_s)

    if expected_closes_rate is not None:
        exp_c = float(clamp01(expected_closes_rate))
        exp_c_src = "override"
    else:
        exp_c = float(base_exp_c)

    # Absolute gaps.
    gap_s = max(0.0, exp_s - s)
    gap_c = max(0.0, exp_c - c)

    # Weighted sum scaled by softness.
    w_s = float(getattr(cfg.expectations, "role_status_start_weight", 0.65))
    w_c = float(getattr(cfg.expectations, "role_status_close_weight", 0.35))
    softness = max(1e-6, float(getattr(cfg.expectations, "role_status_softness", 0.35)))

    raw = (w_s * gap_s) + (w_c * gap_c)
    pressure = float(clamp01(raw / softness))

    meta = {
        "starts_rate": float(s),
        "closes_rate": float(c),
        "expected_starts_rate": float(exp_s),
        "expected_closes_rate": float(exp_c),
        "expected_sources": {"starts": exp_s_src, "closes": exp_c_src},
        "gap_starts": float(gap_s),
        "gap_closes": float(gap_c),
        "weights": {"starts": float(w_s), "closes": float(w_c)},
        "softness": float(softness),
        "raw": float(raw),
        "pressure": float(pressure),
    }
    return pressure, meta


def fatigue_level(*, fatigue_st: Any, fatigue_lt: Any, cfg: AgencyConfig) -> Tuple[float, Dict[str, Any]]:
    """Compute a single fatigue level in 0..1.

    We mirror the fatigue subsystem's condition formula:
      condition = 1 - ST - LT_WEIGHT*LT
    so fatigue ~= 1 - condition.
    """
    st = float(clamp01(safe_float(fatigue_st, 0.0)))
    lt = float(clamp01(safe_float(fatigue_lt, 0.0)))

    lt_w = float(getattr(cfg.frustration, "health_lt_weight", 0.65))
    lvl = float(clamp01(st + lt_w * lt))

    return lvl, {"st": float(st), "lt": float(lt), "lt_weight": float(lt_w), "fatigue": float(lvl)}


def contract_seasons_left(contract_end_season_id: Optional[str], *, season_year: int) -> Optional[int]:
    """Parse end_season_id (e.g., '2025-26') and return seasons left from season_year.

    Returns:
        - None if unknown/unparseable
        - 0 if expiring this season
        - 1 if one more season after this, etc.
    """
    if not contract_end_season_id:
        return None
    s = str(contract_end_season_id).strip()
    if not s:
        return None
    try:
        end_start_year = int(s.split("-")[0])
    except Exception:
        return None
    try:
        cur = int(season_year)
    except Exception:
        return None
    return int(end_start_year - cur)
