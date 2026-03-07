from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, constr

IdempotencyKey = constr(pattern=r"^[A-Za-z0-9_-]{8,128}$")


class TradeSubmitRequest(BaseModel):
    deal: Dict[str, Any]


class TradeSubmitCommittedRequest(BaseModel):
    deal_id: str
    idempotency_key: IdempotencyKey | None = None


class TradeNegotiationStartRequest(BaseModel):
    user_team_id: str
    other_team_id: str
    default_offer_privacy: str = "PRIVATE"


class TradeNegotiationCommitRequest(BaseModel):
    session_id: str
    deal: Dict[str, Any]
    offer_privacy: str = "PRIVATE"
    expose_to_media: bool = False
    idempotency_key: IdempotencyKey | None = None


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


class TradeNegotiationInboxQuery(BaseModel):
    """Query params for trade negotiation inbox listing.

    NOTE:
    - This request schema remains unchanged for backward compatibility.
    - Inbox API response rows now include AI auto-end context fields:
      - `ai_end_risk` (dict): snapshot of probability/risk signals for the row.
      - `auto_end` (dict): session-level AI auto-end metadata (status/reason/score/detail).
    """

    team_id: str
    status: str = "ACTIVE"
    phase: str = "OPEN"
    include_expired: bool = False
    limit: int = 50
    offset: int = 0
    sort: str = "updated_desc"


class TradeNegotiationRejectRequest(BaseModel):
    session_id: str
    team_id: str
    reason: str = ""
    idempotency_key: IdempotencyKey | None = None


class TradeNegotiationOpenRequest(BaseModel):
    session_id: str
    team_id: str
    idempotency_key: IdempotencyKey | None = None
