"""Injury subsystem package.

Public API (v1)
---------------
- prepare_game_injuries(...)
- make_in_game_injury_hook(...)
- finalize_game_injuries(...)

The injury system is designed to be layered on top of:
- between-game fatigue (fatigue/)
- training plans (training/)
- match engine v3 (matchengine_v3/)

All side effects are contained to SQLite SSOT tables:
- player_injury_state
- injury_events
"""

from .service import finalize_game_injuries, make_in_game_injury_hook, prepare_game_injuries

__all__ = [
    "prepare_game_injuries",
    "make_in_game_injury_hook",
    "finalize_game_injuries",
]
