from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class SimGameRequest(BaseModel):
    home_team_id: str
    away_team_id: str
    home_tactics: Optional[Dict[str, Any]] = None
    away_tactics: Optional[Dict[str, Any]] = None
    game_date: Optional[str] = None  # 인게임 날짜 (YYYY-MM-DD)


class ChatMainRequest(BaseModel):
    apiKey: str
    userInput: str = Field(..., alias="userMessage")
    mainPrompt: Optional[str] = ""
    context: Any = ""

    class Config:
        allow_population_by_field_name = True
        allow_population_by_alias = True
        fields = {"userInput": "userMessage"}


class AdvanceLeagueRequest(BaseModel):
    target_date: str  # YYYY-MM-DD, 이 날짜까지 리그를 자동 진행
    user_team_id: Optional[str] = None
    apiKey: Optional[str] = None  # Optional: used for month-end scouting LLM generation


class ProgressNextUserGameDayRequest(BaseModel):
    user_team_id: str
    mode: str = "auto_if_needed"  # auto_if_needed | strict_today_only
    apiKey: Optional[str] = None
    idempotency_key: Optional[str] = None


class AutoAdvanceToNextUserGameDayRequest(BaseModel):
    user_team_id: str
    apiKey: Optional[str] = None
    idempotency_key: Optional[str] = None


class PostseasonSetupRequest(BaseModel):
    my_team_id: str
    use_random_field: bool = False


class EmptyRequest(BaseModel):
    pass


class WeeklyNewsRequest(BaseModel):
    apiKey: str


class ApiKeyRequest(BaseModel):
    apiKey: str


class SeasonReportRequest(BaseModel):
    apiKey: str
    user_team_id: str
