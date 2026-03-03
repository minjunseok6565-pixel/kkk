from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class GameNewRequest(BaseModel):
    slot_name: str
    slot_id: Optional[str] = None
    user_team_id: Optional[str] = None
    season_year: Optional[int] = None
    overwrite_if_exists: bool = False


class GameSaveRequest(BaseModel):
    slot_id: str
    save_name: Optional[str] = None
    note: Optional[str] = None


class GameLoadRequest(BaseModel):
    slot_id: str
    strict: bool = True
    expected_save_version: Optional[int] = None


class GameSetUserTeamRequest(BaseModel):
    slot_id: str
    user_team_id: str
