from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, validator


def _normalize_team_id_upper(value: str) -> str:
    return str(value or "").strip().upper()


class TradeLabAssetsQuery(BaseModel):
    team_id: str

    @validator("team_id")
    def _validate_team_id(cls, value: str) -> str:
        normalized = _normalize_team_id_upper(value)
        if not normalized:
            raise ValueError("team_id is required")
        return normalized


class TradeLabAssetPlayer(BaseModel):
    kind: Literal["player"] = "player"
    player_id: str
    name: str
    pos: str
    age: int
    ovr: int
    salary: float
    team_id: str
    injury: Optional[Dict[str, Any]] = None

    @validator("team_id")
    def _normalize_team_id(cls, value: str) -> str:
        normalized = _normalize_team_id_upper(value)
        if not normalized:
            raise ValueError("team_id is required")
        return normalized


class TradeLabAssetPick(BaseModel):
    kind: Literal["pick"] = "pick"
    pick_id: str
    year: int
    round: int
    original_team: str
    owner_team: str
    protection: Optional[Dict[str, Any]] = None

    @validator("original_team", "owner_team")
    def _normalize_team_ids(cls, value: str) -> str:
        normalized = _normalize_team_id_upper(value)
        if not normalized:
            raise ValueError("team id is required")
        return normalized


class TradeLabTeamAssetsResponse(BaseModel):
    ok: bool
    team_id: str
    current_date: str
    players: List[TradeLabAssetPlayer]
    first_round_picks: List[TradeLabAssetPick]

    @validator("team_id")
    def _normalize_team_id(cls, value: str) -> str:
        normalized = _normalize_team_id_upper(value)
        if not normalized:
            raise ValueError("team_id is required")
        return normalized

