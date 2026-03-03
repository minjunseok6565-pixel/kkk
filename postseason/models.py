from __future__ import annotations

"""Typed structures for the postseason subsystem.

Note:
- These are *typing helpers* only. Runtime code uses plain dicts to keep JSON friendliness.
- We intentionally keep the legacy shapes used by the existing UI (series/game dictionaries).
"""

from typing import Any, Dict, List, Literal, Optional, TypedDict


ConferenceKey = Literal["east", "west"]
RoundName = Literal[
    "Conference Quarterfinals",
    "Conference Semifinals",
    "Conference Finals",
    "NBA Finals",
]
PhaseName = Literal["play_in", "playoffs"]


class SeedEntry(TypedDict, total=False):
    team_id: str
    seed: int
    conference: str
    division: Optional[str]
    wins: int
    losses: int
    games_played: int
    win_pct: float
    point_diff: float


class GameSummary(TypedDict, total=False):
    # Deterministic unique ID for this postseason game.
    game_id: str
    date: str  # YYYY-MM-DD

    home_team_id: str
    away_team_id: str

    home_score: int
    away_score: int
    winner: str

    status: str  # "final"
    is_overtime: bool
    phase: PhaseName

    # Legacy compatibility fields (UI / news / debugging)
    final_score: Dict[str, int]          # {team_id: score}
    boxscore: Dict[str, Any]             # v2 teams payload


class PlayInMatchup(TypedDict, total=False):
    game_id: str
    date: Optional[str]
    home: Optional[SeedEntry]
    away: Optional[SeedEntry]
    result: Optional[GameSummary]


class PlayInConferenceState(TypedDict, total=False):
    conference: ConferenceKey
    participants: Dict[int, SeedEntry]   # seeds 7..10
    matchups: Dict[str, PlayInMatchup]   # keys: seven_vs_eight, nine_vs_ten, final
    seed7: Optional[SeedEntry]
    seed8: Optional[SeedEntry]
    eliminated: List[str]


class SeriesState(TypedDict, total=False):
    # Deterministic unique series ID.
    series_id: str

    round: RoundName
    matchup: str              # "1 vs 8" / "SF1" / "CF" / "FINALS"

    home_court: str
    road: str

    home_entry: SeedEntry
    road_entry: SeedEntry

    games: List[GameSummary]
    wins: Dict[str, int]
    best_of: int
    winner: Optional[SeedEntry]
    start_date: str  # YYYY-MM-DD


class BracketState(TypedDict, total=False):
    east: Dict[str, Any]
    west: Dict[str, Any]
    finals: Optional[SeriesState]


class PlayoffsState(TypedDict, total=False):
    engine_version: str

    seeds: Dict[str, Dict[int, SeedEntry]]  # conf -> seed -> entry
    bracket: BracketState
    current_round: RoundName
    start_date: str

    # Optional UX helpers (not required by existing UI)
    calendar: Dict[str, List[str]]          # date -> [game_id]
    game_specs: Dict[str, Dict[str, Any]]   # game_id -> metadata
