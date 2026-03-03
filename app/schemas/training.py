from __future__ import annotations

from typing import Dict, Optional

from pydantic import BaseModel


class TeamTrainingPlanRequest(BaseModel):
    team_id: str
    season_year: Optional[int] = None
    focus: Optional[str] = None
    intensity: Optional[str] = None
    weights: Optional[Dict[str, float]] = None


class PlayerTrainingPlanRequest(BaseModel):
    player_id: str
    season_year: Optional[int] = None
    primary: Optional[str] = None
    secondary: Optional[str] = None
    intensity: Optional[str] = None
