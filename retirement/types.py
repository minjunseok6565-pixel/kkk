from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True, slots=True)
class RetirementInputs:
    player_id: str
    season_year: int
    age: int
    ovr: int
    team_id: str
    injury_status: str
    injury_severity: int
    injury_context: Dict[str, Any]
    mental: Dict[str, Any]


@dataclass(frozen=True, slots=True)
class RetirementDecision:
    player_id: str
    season_year: int
    considered: bool
    decision: str  # RETIRED | STAY
    consider_prob: float
    retirement_prob: float
    random_roll: float
    age: int
    team_id: str
    injury_status: str
    inputs: Dict[str, Any]
    explanation: Dict[str, Any]
