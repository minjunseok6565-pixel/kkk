from __future__ import annotations

"""Rule-based CPU practice planner.

This module is intentionally simple and deterministic.
It does *not* query the database or inspect rosters directly; that is handled
by practice.service.

If inputs are missing or invalid, the AI must fall back to safe defaults.
"""

from typing import Any, Dict, Optional

from . import config as p_cfg


def choose_session_for_date(
    *,
    date_iso: str,  # noqa: ARG001
    days_to_next_game: Optional[int] = None,
    off_fam: Optional[float] = None,
    def_fam: Optional[float] = None,
    low_sharp_count: Optional[int] = None,
    fallback_off_scheme: Optional[str] = None,
    fallback_def_scheme: Optional[str] = None,
) -> Dict[str, Any]:
    """Choose an auto practice session for one date.

    Args:
        days_to_next_game: integer >=0 if known.
        off_fam/def_fam: current familiarity for main schemes (0..100) if known.
        low_sharp_count: number of roster players below a threshold (e.g. <45) if known.
        fallback_off_scheme/fallback_def_scheme: scheme keys to target.

    Returns:
        A *raw* session dict (practice.types.normalize_session will sanitize).
    """

    # Conservative defaults.
    try:
        d2g = int(days_to_next_game) if days_to_next_game is not None else None
    except Exception:
        d2g = None

    try:
        off = float(off_fam) if off_fam is not None else None
    except Exception:
        off = None

    try:
        de = float(def_fam) if def_fam is not None else None
    except Exception:
        de = None

    try:
        lows = int(low_sharp_count) if low_sharp_count is not None else None
    except Exception:
        lows = None

    # 1) Close to a game => recovery to avoid fatigue spikes.
    if d2g is not None and d2g <= p_cfg.AI_RECOVERY_D2G_THRESHOLD:
        return {"type": "RECOVERY"}

    # 2) If we know familiarity and one side is low, install tactics.
    if off is not None and de is not None:
        if off < p_cfg.AI_LOW_FAMILIARITY_THRESHOLD and off <= de:
            return {"type": "OFF_TACTICS", "offense_scheme_key": fallback_off_scheme}
        if de < p_cfg.AI_LOW_FAMILIARITY_THRESHOLD and de < off:
            return {"type": "DEF_TACTICS", "defense_scheme_key": fallback_def_scheme}

    # 3) If many players are out of rhythm and we have time, run a scrimmage.
    if lows is not None and lows >= p_cfg.AI_LOW_SHARPNESS_COUNT_TRIGGER and (d2g is None or d2g >= p_cfg.AI_SCRIMMAGE_MIN_D2G):
        return {"type": "SCRIMMAGE"}

    # 4) Default: film/workthrough.
    return {
        "type": "FILM",
        "offense_scheme_key": fallback_off_scheme,
        "defense_scheme_key": fallback_def_scheme,
    }
