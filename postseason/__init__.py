"""Postseason (Play-In + Playoffs) subsystem.

This package implements the play-in / playoffs director for the NBA simulation game.

Design goals
------------
- Deterministic IDs for postseason games/series (stable for replay, debugging, news de-dup)
- Correct phase ingestion: play-in => phase='play_in', playoffs => phase='playoffs'
- Keeps stable bracket/series/game dict shapes for UI/news consumers
- Safe, tolerant behavior: fail-loud on SSOT mismatches, but avoid partial state corruption

Public entry points
-------------------
Functions are implemented in `postseason.director` and re-exported at package level
for convenience (e.g., `from postseason import initialize_postseason`).
"""

from .director import (
    build_postseason_field,
    reset_postseason_state,
    initialize_postseason,
    play_my_team_play_in_game,
    advance_my_team_one_game,
    auto_advance_current_round,
)

__all__ = [
    "build_postseason_field",
    "reset_postseason_state",
    "initialize_postseason",
    "play_my_team_play_in_game",
    "advance_my_team_one_game",
    "auto_advance_current_round",
]
