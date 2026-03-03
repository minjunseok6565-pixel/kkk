from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ReleaseToFARequest(BaseModel):
    player_id: str
    released_date: Optional[str] = None  # YYYY-MM-DD (default: in-game date)


class SignFreeAgentRequest(BaseModel):
    session_id: str  # must reference an ACCEPTED contract negotiation session
    team_id: str
    player_id: str
    signed_date: Optional[str] = None  # YYYY-MM-DD (default: in-game date)
    years: int = 1
    salary_by_year: Optional[Dict[int, int]] = None  # {season_year: salary}
    team_option_years: Optional[List[int]] = None  # Absolute season_years; must be tail-consecutive and include final year
    # Deprecated shorthand; prefer team_option_years.
    team_option_last_year: bool = False  # If True, last year is a TEAM option (PENDING)


class ReSignOrExtendRequest(BaseModel):
    session_id: str  # must reference an ACCEPTED contract negotiation session
    team_id: str
    player_id: str
    signed_date: Optional[str] = None  # YYYY-MM-DD (default: in-game date)
    years: int = 1
    salary_by_year: Optional[Dict[int, int]] = None  # {season_year: salary}
    team_option_years: Optional[List[int]] = None  # Absolute season_years; must be tail-consecutive and include final year
    # Deprecated shorthand; prefer team_option_years.
    team_option_last_year: bool = False  # If True, last year is a TEAM option (PENDING)


class ContractNegotiationStartRequest(BaseModel):
    team_id: str
    player_id: str
    mode: str = "SIGN_FA"  # SIGN_FA | RE_SIGN | EXTEND
    valid_days: Optional[int] = 7  # in-game days the offer window stays open (best-effort)


class ContractNegotiationOfferRequest(BaseModel):
    session_id: str
    offer: Dict[str, Any]  # see contracts.negotiation.types.ContractOffer.from_payload


class ContractNegotiationAcceptCounterRequest(BaseModel):
    session_id: str


class ContractNegotiationCommitRequest(BaseModel):
    session_id: str
    signed_date: Optional[str] = None  # YYYY-MM-DD (default: in-game date)


class ContractNegotiationCancelRequest(BaseModel):
    session_id: str
    reason: Optional[str] = None


class TwoWayNegotiationStartRequest(BaseModel):
    team_id: str
    player_id: str
    valid_days: Optional[int] = 7


class TwoWayNegotiationDecisionRequest(BaseModel):
    session_id: str
    accept: bool


class TwoWayNegotiationCommitRequest(BaseModel):
    session_id: str
    signed_date: Optional[str] = None
