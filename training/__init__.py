"""Training + player growth subsystem.

This package implements:
  - Team / player training plans (DB-backed)
  - Offseason growth application
  - In-season monthly growth ticks (hooked into simulation)

SSOT notes
---------
* Persisted plans/profiles live in SQLite tables created by db_schema.training.
* Player ratings remain SSOT in `players.attrs_json`.
"""

from .service import apply_offseason_growth, get_or_default_team_plan, get_or_default_player_plan
from .checkpoints import maybe_run_monthly_growth_tick

__all__ = [
    "apply_offseason_growth",
    "maybe_run_monthly_growth_tick",
    "get_or_default_team_plan",
    "get_or_default_player_plan",
]
