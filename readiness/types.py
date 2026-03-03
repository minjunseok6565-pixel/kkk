from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True, slots=True)
class TacticsMultipliers:
    """Multipliers applied to matchengine_v3.tactics.TacticsConfig knobs.

    These values are intended to be multiplied on top of user/coach-configured knobs.
    Service code must clamp the resulting knobs to a safe range.
    """

    scheme_weight_sharpness: float = 1.0
    scheme_outcome_strength: float = 1.0
    def_scheme_weight_sharpness: float = 1.0
    def_scheme_outcome_strength: float = 1.0


@dataclass(frozen=True, slots=True)
class PreparedTeamSchemes:
    """Prepared scheme familiarity snapshot for one team for one game."""

    team_id: str
    offense_scheme_key: str
    defense_scheme_key: str
    offense_familiarity_pre: float
    defense_familiarity_pre: float


@dataclass(frozen=True, slots=True)
class PreparedGameReadiness:
    """Prepared readiness inputs for one game.

    This mirrors the pattern used by fatigue/injury:
    - Returned by prepare_game_readiness
    - Consumed by finalize_game_readiness
    """

    game_date_iso: str
    season_year: int
    home_team_id: str
    away_team_id: str

    # Player sharpness after decay up to game start (before applying this game's gain).
    sharpness_pre_by_pid: Dict[str, float]

    # Team schemes/familiarities after decay up to game start (before applying this game's gain).
    schemes_by_team: Dict[str, PreparedTeamSchemes]

    # Pre-game attribute modifiers to be applied by roster_adapter.
    attrs_mods_by_pid: Dict[str, Dict[str, float]]

    # Pre-game tactics multipliers to be applied to TeamState.tactics.
    tactics_mult_by_team: Dict[str, TacticsMultipliers]
