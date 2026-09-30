from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, root_validator, validator


class ReleaseToFARequest(BaseModel):
    player_id: str
    released_date: Optional[str] = None  # YYYY-MM-DD (default: in-game date)

class WaivePlayerRequest(BaseModel):
    team_id: str
    player_id: str
    waived_date: Optional[str] = None  # YYYY-MM-DD (default: in-game date)


class StretchPlayerRequest(BaseModel):
    team_id: str
    player_id: str
    stretch_years: int
    stretched_date: Optional[str] = None  # YYYY-MM-DD (default: in-game date)


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


class ReSignRequest(BaseModel):
    session_id: str  # must reference an ACCEPTED contract negotiation session
    team_id: str
    player_id: str
    signed_date: Optional[str] = None  # YYYY-MM-DD (default: in-game date)
    years: int = 1
    salary_by_year: Optional[Dict[int, int]] = None  # {season_year: salary}
    team_option_years: Optional[List[int]] = None  # Absolute season_years; must be tail-consecutive and include final year
    # Deprecated shorthand; prefer team_option_years.
    team_option_last_year: bool = False  # If True, last year is a TEAM option (PENDING)


class ExtendRequest(BaseModel):
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
    mode: str = "SIGN_FA"  # SIGN_FA(일반 FA) | RE_SIGN(FA + 팀 Bird 권한 보유자 전용) | EXTEND(현재 팀 소속 연장)
    extension_type: Optional[str] = None  # EXTEND 계열에서만 사용: ROOKIE|VETERAN|DVE
    valid_days: Optional[int] = 7  # in-game days the offer window stays open (best-effort)
    preferred_channel: Optional[str] = None  # RE_SIGN: BIRD_FULL|BIRD_EARLY|BIRD_NON, SIGN_FA: STANDARD_FA|MINIMUM|NT_MLE|TP_MLE|ROOM_MLE (mode별 검증)

    @validator("mode", pre=True, always=True)
    def _normalize_mode(cls, value: Optional[str]) -> str:
        return str(value or "SIGN_FA").strip().upper() or "SIGN_FA"

    @validator("extension_type", pre=True, always=True)
    def _normalize_extension_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip().upper()
        return normalized or None

    @root_validator
    def _validate_extension_mode_and_type(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        mode = str(values.get("mode") or "").strip().upper()
        extension_type = values.get("extension_type")
        is_extend_mode = mode == "EXTEND" or mode.startswith("EXTEND_")

        if not is_extend_mode and extension_type is not None:
            raise ValueError("extension_type is only allowed when mode is EXTEND or EXTEND_*")
        if is_extend_mode and extension_type is None:
            raise ValueError("extension_type is required when mode is EXTEND or EXTEND_*")
        if extension_type is not None and extension_type not in {"ROOKIE", "VETERAN", "DVE"}:
            raise ValueError("extension_type must be one of: ROOKIE, VETERAN, DVE")
        return values


class ContractOfferPayload(BaseModel):
    start_season_year: Optional[int] = None
    years: Optional[int] = None
    salary_by_year: Optional[Dict[int, float]] = None
    aav: Optional[float] = None
    salary: Optional[float] = None
    contract_channel: Optional[str] = "STANDARD_FA"  # RE_SIGN는 Bird-only(BIRD_FULL|BIRD_EARLY|BIRD_NON), SIGN_FA는 STANDARD_FA|MINIMUM|MLE 계열
    options: Optional[List[Dict[str, Any]]] = None
    non_monetary: Optional[Dict[str, Any]] = None
    extension_profile: Optional[Dict[str, Any]] = None


class ContractNegotiationOfferRequest(BaseModel):
    session_id: str
    offer: ContractOfferPayload  # see contracts.negotiation.types.ContractOffer.from_payload


class ContractNegotiationAcceptCounterRequest(BaseModel):
    session_id: str


class ContractNegotiationCommitRequest(BaseModel):
    session_id: str
    signed_date: Optional[str] = None  # YYYY-MM-DD (default: in-game date)


class ContractNegotiationCancelRequest(BaseModel):
    session_id: str
    reason: Optional[str] = None


class BirdRightsRenounceRequest(BaseModel):
    team_id: str
    player_id: str
    season_year: int


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
