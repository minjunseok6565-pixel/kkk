"""Team practice subsystem (between-game tactical + conditioning sessions).

This package is *not* the same as `training`:
- `training` focuses on long-term growth (monthly/offseason) and persists
  team/player development plans.
- `practice` focuses on between-game preparation and conditioning, and will be
  used to drive:
    * readiness (scheme familiarity + player sharpness)
    * fatigue recovery modifiers
    * training-day injury risk modifiers

Public API (v1)
---------------
- resolve_practice_session: load or auto-generate a per-day practice session.
- get_or_default_team_practice_plan / set_team_practice_plan
- get_team_practice_session / set_team_practice_session / list_team_practice_sessions

Design goals
------------
- Commercially safe: resilient to malformed JSON and missing data.
- Deterministic: no hidden OS-clock usage; dates must be in-game ISO.
- Separation of concerns: repo = DB I/O, service = business logic.
"""

from .service import (
    apply_practice_before_game,
    get_or_default_team_practice_plan,
    get_team_practice_session,
    list_team_practice_sessions,
    resolve_practice_session,
    set_team_practice_plan,
    set_team_practice_session,
)

__all__ = [
    "apply_practice_before_game",
    "get_or_default_team_practice_plan",
    "get_team_practice_session",
    "list_team_practice_sessions",
    "resolve_practice_session",
    "set_team_practice_plan",
    "set_team_practice_session",
]
