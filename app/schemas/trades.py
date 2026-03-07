from __future__ import annotations

from typing import Any, Dict, List, Literal, Union, Annotated

from pydantic import BaseModel, Field


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


class TradeNegotiationOpenRequest(BaseModel):
    session_id: str
    team_id: str


# ---------------------------------------------------------------------------
# Canonical Trade Negotiation DTO (frontend-backend contract)
# ---------------------------------------------------------------------------


class TradeAssetPlayerSnapshot(BaseModel):
    kind: Literal["player"] = "player"
    player_id: str
    display_name: str
    pos: str
    to_team: str | None = None


class TradeAssetPickSnapshot(BaseModel):
    kind: Literal["pick"] = "pick"
    pick_id: str
    year: int
    round: int
    original_team: str
    owner_team: str
    protection: Dict[str, Any] | None = None
    to_team: str | None = None


class TradeAssetSwapSnapshot(BaseModel):
    kind: Literal["swap"] = "swap"
    swap_id: str
    pick_id_a: str
    pick_id_b: str
    year: int | None = None
    round: int | None = None
    to_team: str | None = None


class TradeAssetFixedSnapshot(BaseModel):
    kind: Literal["fixed_asset"] = "fixed_asset"
    asset_id: str
    label: str | None = None
    draft_year: int | None = None
    source_pick_id: str | None = None
    to_team: str | None = None


TradeAssetSnapshot = Annotated[
    Union[
        TradeAssetPlayerSnapshot,
        TradeAssetPickSnapshot,
        TradeAssetSwapSnapshot,
        TradeAssetFixedSnapshot,
    ],
    Field(discriminator="kind"),
]


class TradeNegotiationOfferPayload(BaseModel):
    teams: List[str]
    legs: Dict[str, List[TradeAssetSnapshot]]
    meta: Dict[str, Any] = Field(default_factory=dict)


class TradeContractViolation(BaseModel):
    path: str
    asset_kind: str
    asset_ref: str
    missing_fields: List[str]


class TradeNegotiationInboxSummary(BaseModel):
    headline: str
    offer_tone: str | None = None
    offer_privacy: str
    leak_status: str


class TradeNegotiationInboxOffer(BaseModel):
    deal: TradeNegotiationOfferPayload
    asset_counts: Dict[str, int]


class TradeNegotiationInboxActions(BaseModel):
    can_open: bool
    can_reject: bool
    can_commit: bool


class TradeNegotiationInboxRowResponse(BaseModel):
    session_id: str
    user_team_id: str
    other_team_id: str
    status: str
    phase: str
    created_at: str | None = None
    updated_at: str | None = None
    valid_until: str | None = None
    is_expired: bool
    summary: TradeNegotiationInboxSummary
    offer: TradeNegotiationInboxOffer
    actions: TradeNegotiationInboxActions
    contract_violations: List[TradeContractViolation] = Field(default_factory=list)


class TradeNegotiationInboxResponse(BaseModel):
    ok: bool
    team_id: str
    filters: Dict[str, Any]
    total: int
    rows: List[TradeNegotiationInboxRowResponse]
