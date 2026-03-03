from __future__ import annotations

"""Typed containers used by the stats analytics layer.

The workflow state's `player_stats` map is produced by the ingestion pipeline.
Its shape is expected to be:

    player_id -> {
        "player_id": str,
        "name": str | None,
        "team_id": str | None,
        "games": int,
        "totals": {"PTS": float, "AST": float, ...}
    }

This module defines:
- Normalized dataclasses used internally (`PlayerLine`)
- TypedDicts used for JSON-like outputs (`LeaderboardRow`, `LeaderboardsBundle`)

Design goal: keep the boundary between "raw state" and "derived view" explicit.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Mapping, Tuple, TypedDict


# ----------------------------
# Normalized internal records
# ----------------------------


@dataclass(frozen=True)
class PlayerLine:
    """Normalized player season line for analytics.

    Notes:
        - `gp` is taken from `entry["games"]` by default.
        - Totals are stored as floats; missing keys default to 0.0 at read time.
    """

    player_id: str
    name: str
    team_id: str
    gp: int
    totals: Mapping[str, float]

    def total(self, key: str, default: float = 0.0) -> float:
        try:
            v = self.totals.get(key, default)
            return float(v) if v is not None else float(default)
        except (TypeError, ValueError):
            return float(default)


# ----------------------------
# Configuration
# ----------------------------


QualifierProfile = Literal["auto", "regular", "playoffs", "relaxed"]
LeaderboardMode = Literal["per_game", "totals", "per_36", "advanced"]
MetricCategory = Literal["traditional", "shooting", "advanced", "rate"]


class LeaderboardConfig(TypedDict, total=False):
    """Configuration for leaderboard generation."""

    top_n: int
    include_ties: bool
    qualifier_profile: QualifierProfile
    modes: List[LeaderboardMode]
    categories: List[MetricCategory]
    metric_keys: List[str]


class QualifierRules(TypedDict):
    """Computed minimum requirements for leaderboard eligibility."""

    min_gp: int
    min_min_total: float
    min_fga: float
    min_3pa: float
    min_fta: float


# ----------------------------
# Metric definitions
# ----------------------------


QualifierKind = Literal["none", "gp_min", "fga_min", "3pa_min", "fta_min"]


@dataclass(frozen=True)
class Metric:
    """Definition of a single leaderboard metric.

    A Metric is responsible for:
    - computing its numeric value from a player's totals
    - declaring a qualifier kind (minimum GP, minimum attempts, etc.)
    - providing display/format hints
    - providing deterministic tie-breaker keys
    """

    key: str
    label: str
    category: MetricCategory
    mode: LeaderboardMode
    decimals: int
    sort_desc: bool
    requires: Tuple[str, ...]
    qualifier: QualifierKind = "gp_min"
    tiebreak_totals: Tuple[str, ...] = ()


# ----------------------------
# Leaderboard outputs
# ----------------------------


class LeaderboardRow(TypedDict, total=False):
    rank: int
    player_id: str
    name: str
    team_id: str
    games: int
    MIN: float
    value: float
    per_game: float
    qualifies: bool
    min_required: Dict[str, Any]


class LeaderboardsBundle(TypedDict):
    """Rich bundle intended for caching in state.cached_views.stats.leaders."""

    meta: Dict[str, Any]
    per_game: Dict[str, List[LeaderboardRow]]
    totals: Dict[str, List[LeaderboardRow]]
    per_36: Dict[str, List[LeaderboardRow]]
    advanced: Dict[str, List[LeaderboardRow]]


# ----------------------------
# Helpers
# ----------------------------


def coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_div(n: float, d: float, default: float = 0.0) -> float:
    try:
        d = float(d)
        if d == 0.0:
            return default
        return float(n) / d
    except (TypeError, ValueError, ZeroDivisionError):
        return default


def normalized_player_lines(player_stats: Mapping[str, Any]) -> List[PlayerLine]:
    """Normalize the workflow state's player_stats map into a list of PlayerLine."""

    lines: List[PlayerLine] = []
    if not isinstance(player_stats, Mapping):
        return lines

    for pid, entry_any in player_stats.items():
        if not isinstance(entry_any, Mapping):
            continue
        entry = entry_any
        player_id = str(entry.get("player_id") or pid)
        name = str(entry.get("name") or "")
        team_id = str(entry.get("team_id") or "")
        gp = coerce_int(entry.get("games") or 0, 0)
        totals_any = entry.get("totals") or {}
        totals: Dict[str, float] = {}
        if isinstance(totals_any, Mapping):
            for k, v in totals_any.items():
                if k is None:
                    continue
                try:
                    totals[str(k)] = coerce_float(v, 0.0)
                except Exception:
                    continue
        lines.append(PlayerLine(player_id=player_id, name=name, team_id=team_id, gp=gp, totals=totals))

    return lines
