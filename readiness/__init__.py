"""Between-game readiness subsystem.

Readiness models:
- Player match sharpness ("game feel")
- Team scheme familiarity ("tactical adaptation")

Public API
----------
- prepare_game_readiness
- apply_readiness_to_team_state
- finalize_game_readiness

Implementation details live in readiness.service and readiness.repo.
"""

from .service import apply_readiness_to_team_state, finalize_game_readiness, prepare_game_readiness
from .types import PreparedGameReadiness, PreparedTeamSchemes, TacticsMultipliers

__all__ = [
    "PreparedGameReadiness",
    "PreparedTeamSchemes",
    "TacticsMultipliers",
    "prepare_game_readiness",
    "apply_readiness_to_team_state",
    "finalize_game_readiness",
]
