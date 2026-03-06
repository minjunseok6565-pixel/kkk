from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel


class TradeSubmitRequest(BaseModel):
    deal: Dict[str, Any]


class TradeSubmitCommittedRequest(BaseModel):
    deal_id: str


class TradeNegotiationStartRequest(BaseModel):
    user_team_id: str
    other_team_id: str
    default_offer_privacy: str = "PRIVATE"


class TradeNegotiationCommitRequest(BaseModel):
    session_id: str
    deal: Dict[str, Any]
    offer_privacy: str = "PRIVATE"
    expose_to_media: bool = False


class TradeBlockListRequest(BaseModel):
    team_id: str
    player_id: str
    priority: float = 0.5
    reason_code: str = "MANUAL"
    visibility: str = "PUBLIC"


class TradeBlockUnlistRequest(BaseModel):
    team_id: str
    player_id: str
    reason_code: str = "MANUAL_REMOVE"


class TradeEvaluateRequest(BaseModel):
    deal: Dict[str, Any]
    team_id: str
    include_breakdown: bool = True


class TradeBlockAggregateQuery(BaseModel):
    active_only: bool = True
    visibility: str = "PUBLIC"
    team_id: str | None = None
    limit: int = 300
    offset: int = 0
    sort: str = "priority_desc"
