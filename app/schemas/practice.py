from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class TeamPracticePlanRequest(BaseModel):
    team_id: str
    season_year: Optional[int] = None
    mode: Optional[str] = None  # AUTO | MANUAL


class TeamPracticeSessionRequest(BaseModel):
    team_id: str
    season_year: Optional[int] = None
    date_iso: str  # YYYY-MM-DD
    type: Optional[str] = None
    offense_scheme_key: Optional[str] = None
    defense_scheme_key: Optional[str] = None
    participant_pids: Optional[List[str]] = None
    non_participant_type: Optional[str] = None


class TeamPracticePreviewRequest(BaseModel):
    season_year: Optional[int] = None
    date_iso: str  # YYYY-MM-DD
    type: Optional[str] = None
    offense_scheme_key: Optional[str] = None
    defense_scheme_key: Optional[str] = None
    participant_pids: Optional[List[str]] = None
    non_participant_type: Optional[str] = None

