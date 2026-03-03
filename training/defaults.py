from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

from derived_formulas import compute_derived

from .mapping import ALL_CATEGORIES, CATEGORY_KEYS
from .types import normalize_player_plan, normalize_team_plan


def default_team_plan(*, team_id: str, season_year: int) -> Dict[str, Any]:
    """Default team plan when user did not choose.

    MVP: balanced, medium intensity.
    (Team-specific meta like coach style can be wired here later.)
    """
    _ = (team_id, season_year)
    return normalize_team_plan({"focus": "BALANCED", "intensity": "MED", "weights": {}})


def _category_score(attrs: Mapping[str, Any], category: str) -> float:
    keys = CATEGORY_KEYS.get(category) or []
    vals = []
    for k in keys:
        try:
            v = float(attrs.get(k, 50.0))
        except Exception:
            v = 50.0
        vals.append(v)
    if not vals:
        return 50.0
    return float(sum(vals) / len(vals))


def choose_default_player_focus(attrs: Mapping[str, Any]) -> Tuple[str, Optional[str]]:
    """Pick (primary, secondary) focus by weakest categories.

    We compute a simple mean of 2K base keys per category.
    """
    scores = [(c, _category_score(attrs, c)) for c in ALL_CATEGORIES]
    scores_sorted = sorted(scores, key=lambda x: x[1])
    primary = scores_sorted[0][0] if scores_sorted else "BALANCED"
    secondary = scores_sorted[1][0] if len(scores_sorted) > 1 else None

    # If the player is already "balanced" across categories, keep balanced.
    # (stddev threshold chosen to avoid random noise making weird defaults.)
    if scores:
        vals = [s for _c, s in scores]
        mean = sum(vals) / len(vals)
        var = sum((x - mean) ** 2 for x in vals) / max(1, len(vals))
        stdev = math.sqrt(var)
        if stdev < 5.0:
            return ("BALANCED", None)
    return (primary, secondary)


def default_player_plan(
    *,
    player_id: str,
    season_year: int,
    attrs: Mapping[str, Any],
) -> Dict[str, Any]:
    """Default player plan when user did not choose.

    MVP:
      - Primary/secondary = weakest categories
      - Intensity = MED
    """
    _ = (player_id, season_year)
    primary, secondary = choose_default_player_focus(attrs)
    return normalize_player_plan({"primary": primary, "secondary": secondary, "intensity": "MED"})


def derived_summary(attrs: Mapping[str, Any]) -> Dict[str, float]:
    """Convenience helper for UI/debug: return derived groups."""
    try:
        d = compute_derived(attrs)
    except Exception:
        return {}
    # Keep a small subset that maps to our categories.
    return {
        "FIN_RIM": float(d.get("FIN_RIM", 0.0)),
        "SHOT_3": float(d.get("SHOT_3_CS", 0.0)),
        "PASS": float(d.get("PASS_SAFE", 0.0)),
        "DEF": float(d.get("DEF_POA", 0.0)),
        "REB": float(d.get("REB_DR", 0.0)),
        "PHY": float(d.get("PHYSICAL", 0.0)),
    }
