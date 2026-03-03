from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class ScoutingAssignRequest(BaseModel):
    team_id: str
    scout_id: str
    player_id: str
    assigned_date: Optional[str] = None  # YYYY-MM-DD (default: in-game date)
    target_kind: Literal["COLLEGE"] = "COLLEGE"


class ScoutingUnassignRequest(BaseModel):
    team_id: str
    assignment_id: Optional[str] = None
    scout_id: Optional[str] = None
    ended_date: Optional[str] = None  # YYYY-MM-DD (default: in-game date)
