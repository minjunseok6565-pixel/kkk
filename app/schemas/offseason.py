from __future__ import annotations

from typing import Any, Dict, Literal, Optional, List

from pydantic import BaseModel, Field


class OffseasonContractsProcessRequest(BaseModel):
    user_team_id: str


class TeamOptionPendingRequest(BaseModel):
    user_team_id: str


class TeamOptionDecisionItem(BaseModel):
    contract_id: str
    decision: Literal["EXERCISE", "DECLINE"]


class TeamOptionDecideRequest(BaseModel):
    user_team_id: str
    decisions: List[TeamOptionDecisionItem] = Field(default_factory=list)


class AgencyEventRespondRequest(BaseModel):
    user_team_id: str
    event_id: str
    response_type: str
    response_payload: Optional[Dict[str, Any]] = None
    now_date: Optional[str] = None  # YYYY-MM-DD (default: in-game date)


class AgencyUserActionRequest(BaseModel):
    user_team_id: str
    player_id: str
    action_type: str
    action_payload: Optional[Dict[str, Any]] = None
    now_date: Optional[str] = None  # YYYY-MM-DD (default: in-game date)
