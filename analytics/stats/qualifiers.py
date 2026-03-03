from __future__ import annotations

"""Eligibility rules (qualifiers) for leaderboards.

Leaderboards without qualifiers look unstable and untrustworthy in a season sim
(1-game samples can dominate). This module computes *season-relative*
minimum requirements so rules scale naturally for shortened seasons,
partial seasons, and playoffs.

Rules are intentionally:
- deterministic
- simple (easy to reason about)
- defensive to missing data

If you want to mimic NBA official thresholds exactly, you can implement a
separate profile. The default approach here is dynamic and game-friendly.
"""

from typing import Any, Mapping

from .types import Metric, QualifierProfile, QualifierRules, coerce_float, coerce_int


def infer_max_team_games(team_stats: Mapping[str, Any] | None, player_stats: Mapping[str, Any] | None) -> int:
    """Infer the maximum number of games played by any team.

    Prefer team_stats if available. Otherwise fall back to max player GP.
    """

    max_gp = 0
    if isinstance(team_stats, Mapping):
        for entry_any in team_stats.values():
            if not isinstance(entry_any, Mapping):
                continue
            max_gp = max(max_gp, coerce_int(entry_any.get("games"), 0))
    if max_gp > 0:
        return max_gp

    if isinstance(player_stats, Mapping):
        for entry_any in player_stats.values():
            if not isinstance(entry_any, Mapping):
                continue
            max_gp = max(max_gp, coerce_int(entry_any.get("games"), 0))

    return max_gp


def select_profile(profile: QualifierProfile, *, phase: str, max_team_games: int) -> QualifierProfile:
    """Pick an effective profile.

    - playoffs are more sample-starved by nature
    - early season needs relaxed rules to avoid empty boards
    """

    if profile != "auto":
        return profile

    if phase in {"playoffs", "play_in"}:
        return "playoffs"

    if max_team_games <= 20:
        return "relaxed"

    return "regular"


def build_qualifier_rules(
    team_stats: Mapping[str, Any] | None,
    player_stats: Mapping[str, Any] | None,
    *,
    profile: QualifierProfile = "auto",
    phase: str = "regular",
) -> QualifierRules:
    """Build qualifier rules for a given phase/profile."""

    max_team_games = infer_max_team_games(team_stats, player_stats)
    effective = select_profile(profile, phase=phase, max_team_games=max_team_games)

    if max_team_games <= 0:
        max_team_games = 82

    if effective == "regular":
        min_gp = max(10, int(max_team_games * 0.25))
        return {
            "min_gp": min_gp,
            "min_min_total": float(min_gp) * 10.0,
            "min_fga": float(min_gp) * 5.0,
            "min_3pa": float(min_gp) * 1.5,
            "min_fta": float(min_gp) * 2.0,
        }

    if effective == "playoffs":
        min_gp = max(2, int(max_team_games * 0.30))
        return {
            "min_gp": min_gp,
            "min_min_total": float(min_gp) * 8.0,
            "min_fga": float(min_gp) * 3.0,
            "min_3pa": float(min_gp) * 1.0,
            "min_fta": float(min_gp) * 1.0,
        }

    min_gp = max(3, int(max_team_games * 0.15))
    return {
        "min_gp": min_gp,
        "min_min_total": float(min_gp) * 6.0,
        "min_fga": float(min_gp) * 2.5,
        "min_3pa": float(min_gp) * 0.8,
        "min_fta": float(min_gp) * 0.8,
    }


def player_qualifies(metric: Metric, totals: Mapping[str, float], gp: int, rules: QualifierRules) -> bool:
    """Check whether a player qualifies for a metric leaderboard."""

    minutes = coerce_float(totals.get("MIN"), 0.0)

    if metric.qualifier == "none":
        return True

    if metric.qualifier == "gp_min":
        return gp >= rules["min_gp"] and minutes >= rules["min_min_total"]

    if metric.qualifier == "fga_min":
        return (
            gp >= rules["min_gp"]
            and minutes >= rules["min_min_total"]
            and coerce_float(totals.get("FGA"), 0.0) >= rules["min_fga"]
        )

    if metric.qualifier == "3pa_min":
        return (
            gp >= rules["min_gp"]
            and minutes >= rules["min_min_total"]
            and coerce_float(totals.get("3PA"), 0.0) >= rules["min_3pa"]
        )

    if metric.qualifier == "fta_min":
        return (
            gp >= rules["min_gp"]
            and minutes >= rules["min_min_total"]
            and coerce_float(totals.get("FTA"), 0.0) >= rules["min_fta"]
        )

    return gp >= rules["min_gp"] and minutes >= rules["min_min_total"]
