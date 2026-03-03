"""Between-game fatigue subsystem.

This package implements the persisted fatigue model described in the design doc:

- *Condition* (between games): computed from persisted ST/LT fatigue components
- *Energy* (in game): the match engine's per-player fatigue/energy (0..1)

Public API
----------
- season_year_from_season_id
- prepare_game_fatigue
- finalize_game_fatigue

Implementation details live in fatigue.service and fatigue.repo.
"""

from .service import (
    PreparedGameFatigue,
    PreparedPlayerFatigue,
    PreparedTeamFatigue,
    finalize_game_fatigue,
    prepare_game_fatigue,
    season_year_from_season_id,
)

__all__ = [
    "PreparedGameFatigue",
    "PreparedPlayerFatigue",
    "PreparedTeamFatigue",
    "season_year_from_season_id",
    "prepare_game_fatigue",
    "finalize_game_fatigue",
]
