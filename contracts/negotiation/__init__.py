from __future__ import annotations

"""Contract negotiation subsystem.

This package provides:
- In-memory negotiation sessions stored in state.negotiations (parallel to trade negotiations)
- A deterministic, explainable negotiation engine (ask/floor, counter, reject, accept)
- Orchestration helpers for server endpoints (start/offer/accept/commit)

The contract negotiation system is designed to be:
- Deterministic across reloads given the same inputs
- Explainable (decisions include reason codes + evidence)
- Safe (defensive validation; never assume trusted payloads)
"""

from .config import ContractNegotiationConfig, DEFAULT_CONTRACT_NEGOTIATION_CONFIG
from .errors import (
    ContractNegotiationError,
    NEGOTIATION_NOT_FOUND,
    NEGOTIATION_BAD_PAYLOAD,
    NEGOTIATION_INVALID_OFFER,
    NEGOTIATION_INVALID_MODE,
    NEGOTIATION_EXPIRED,
    NEGOTIATION_CLOSED,
    NEGOTIATION_COMMIT_NOT_ACCEPTED,
    NEGOTIATION_COMMIT_FAILED,
)
from .types import (
    ContractNegotiationMode,
    ContractNegotiationPhase,
    ContractNegotiationStatus,
    NegotiationSpeaker,
    NegotiationVerdict,
    Reason,
    ContractOffer,
    PlayerPosition,
    NegotiationDecision,
)
from .store import (
    create_session,
    get_session,
    append_message,
    set_phase,
    set_status,
    set_valid_until,
    set_constraints,
    set_player_snapshot,
    set_team_snapshot,
    set_agency_snapshot,
    set_player_position,
    set_last_offer,
    set_last_counter,
    set_last_decision,
    set_agreed_offer,
    bump_round,
    bump_lowball_strikes,
    close_session,
)
from .engine import (
    build_player_position,
    evaluate_offer,
    build_counter_offer,
)
from .service import (
    start_contract_negotiation,
    submit_contract_offer,
    accept_last_counter,
    commit_contract_negotiation,
)
