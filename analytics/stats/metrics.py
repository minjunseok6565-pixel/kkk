from __future__ import annotations

"""Metric registry and computations.

This module defines the set of supported leaderboard metrics and how each
metric value is computed from a player's season totals.

The goal is to:
- centralize metric definitions (single source of truth)
- keep computations deterministic and safe for missing data
- provide UI-friendly formatting hints
"""

from typing import Dict, Iterable, List, Mapping, Optional

from .types import LeaderboardMode, Metric, MetricCategory, coerce_float, safe_div


def build_metric_registry() -> Dict[str, Metric]:
    """Return the full metric registry.

    Keys are stable identifiers used in API responses and caches.
    """

    metrics: List[Metric] = []

    # Traditional (per game)
    metrics += [
        Metric("PTS", "Points", "traditional", "per_game", 1, True, ("PTS", "MIN"), "gp_min", ("PTS", "MIN")),
        Metric("REB", "Rebounds", "traditional", "per_game", 1, True, ("REB", "MIN"), "gp_min", ("REB", "MIN")),
        Metric("AST", "Assists", "traditional", "per_game", 1, True, ("AST", "MIN"), "gp_min", ("AST", "MIN")),
        Metric("STL", "Steals", "traditional", "per_game", 1, True, ("STL", "MIN"), "gp_min", ("STL", "MIN")),
        Metric("BLK", "Blocks", "traditional", "per_game", 1, True, ("BLK", "MIN"), "gp_min", ("BLK", "MIN")),
        Metric("3PM", "3PT Made", "traditional", "per_game", 1, True, ("3PM", "3PA", "MIN"), "gp_min", ("3PM", "MIN")),
        Metric("TOV", "Turnovers", "traditional", "per_game", 1, False, ("TOV", "MIN"), "gp_min", ("TOV", "MIN")),
        Metric("MIN", "Minutes", "traditional", "per_game", 1, True, ("MIN",), "gp_min", ("MIN",)),
    ]

    # Shooting percentages (ratio)
    metrics += [
        Metric("FG_PCT", "FG%", "shooting", "advanced", 3, True, ("FGM", "FGA"), "fga_min", ("FGA", "PTS")),
        Metric("3P_PCT", "3P%", "shooting", "advanced", 3, True, ("3PM", "3PA"), "3pa_min", ("3PA", "3PM")),
        Metric("FT_PCT", "FT%", "shooting", "advanced", 3, True, ("FTM", "FTA"), "fta_min", ("FTA", "FTM")),
        Metric("EFG_PCT", "eFG%", "shooting", "advanced", 3, True, ("FGM", "FGA", "3PM"), "fga_min", ("FGA", "PTS")),
        Metric("TS_PCT", "TS%", "shooting", "advanced", 3, True, ("PTS", "FGA", "FTA"), "fga_min", ("FGA", "PTS")),
    ]

    # Advanced ratios
    metrics += [
        Metric("AST_TOV", "AST/TO", "advanced", "advanced", 2, True, ("AST", "TOV"), "gp_min", ("AST", "TOV")),
    ]

    # Per-36 rates (useful for bench players)
    metrics += [
        Metric("PTS_PER36", "PTS/36", "rate", "per_36", 1, True, ("PTS", "MIN"), "gp_min", ("PTS", "MIN")),
        Metric("REB_PER36", "REB/36", "rate", "per_36", 1, True, ("REB", "MIN"), "gp_min", ("REB", "MIN")),
        Metric("AST_PER36", "AST/36", "rate", "per_36", 1, True, ("AST", "MIN"), "gp_min", ("AST", "MIN")),
        Metric("STL_PER36", "STL/36", "rate", "per_36", 1, True, ("STL", "MIN"), "gp_min", ("STL", "MIN")),
        Metric("BLK_PER36", "BLK/36", "rate", "per_36", 1, True, ("BLK", "MIN"), "gp_min", ("BLK", "MIN")),
        Metric("3PM_PER36", "3PM/36", "rate", "per_36", 1, True, ("3PM", "MIN"), "gp_min", ("3PM", "MIN")),
    ]

    return {m.key: m for m in metrics}


def list_metrics(
    *,
    registry: Optional[Mapping[str, Metric]] = None,
    modes: Optional[Iterable[LeaderboardMode]] = None,
    categories: Optional[Iterable[MetricCategory]] = None,
    keys: Optional[Iterable[str]] = None,
) -> List[Metric]:
    """Filter metrics from the registry."""

    reg = dict(registry or build_metric_registry())
    if keys is not None:
        return [reg[k] for k in keys if k in reg]

    out: List[Metric] = []
    mode_set = set(modes) if modes is not None else None
    cat_set = set(categories) if categories is not None else None
    for m in reg.values():
        if mode_set is not None and m.mode not in mode_set:
            continue
        if cat_set is not None and m.category not in cat_set:
            continue
        out.append(m)

    # Stable order: category -> mode -> key
    out.sort(key=lambda x: (x.category, x.mode, x.key))
    return out


def compute_metric_value(metric: Metric, totals: Mapping[str, float], gp: int) -> float:
    """Compute metric value from totals.

    For per_game/per_36, `gp` and `MIN` are used. For ratio metrics, totals are used.
    """

    def t(key: str) -> float:
        return coerce_float(totals.get(key), 0.0)

    if metric.mode == "per_game":
        base_key = metric.key
        if base_key == "MIN":
            return safe_div(t("MIN"), float(gp), 0.0)
        return safe_div(t(base_key), float(gp), 0.0)

    if metric.mode == "totals":
        return t(metric.key)

    if metric.mode == "per_36":
        minutes = t("MIN")
        if minutes <= 0.0:
            return 0.0
        base = metric.key.replace("_PER36", "")
        return safe_div(t(base) * 36.0, minutes, 0.0)

    # advanced (ratios)
    k = metric.key
    if k == "FG_PCT":
        return safe_div(t("FGM"), t("FGA"), 0.0)
    if k == "3P_PCT":
        return safe_div(t("3PM"), t("3PA"), 0.0)
    if k == "FT_PCT":
        return safe_div(t("FTM"), t("FTA"), 0.0)
    if k == "EFG_PCT":
        return safe_div(t("FGM") + 0.5 * t("3PM"), t("FGA"), 0.0)
    if k == "TS_PCT":
        denom = 2.0 * (t("FGA") + 0.44 * t("FTA"))
        return safe_div(t("PTS"), denom, 0.0)
    if k == "AST_TOV":
        tov = t("TOV")
        return safe_div(t("AST"), max(1.0, tov), 0.0)

    return 0.0
