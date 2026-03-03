from __future__ import annotations

import json
from typing import Any, Dict, Optional
from uuid import uuid4

import game_time
import state

from .errors import (
    ContractNegotiationError,
    NEGOTIATION_BAD_PAYLOAD,
    NEGOTIATION_NOT_FOUND,
)

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _now_iso() -> str:
    return game_time.now_utc_like_iso()


def _ensure_session_schema(session: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure contract negotiation sessions include schema defaults.

    Sessions are stored in-memory (state.negotiations), so we must be tolerant
    of older versions or partial payloads (e.g., dev hot reload).
    """
    if not isinstance(session, dict):
        return {}

    session.setdefault("kind", "CONTRACT")
    session.setdefault("mode", "SIGN_FA")
    session.setdefault("status", "ACTIVE")
    session.setdefault("phase", "INIT")

    session.setdefault("created_at", _now_iso())
    session.setdefault("updated_at", _now_iso())

    messages = session.get("messages")
    if not isinstance(messages, list):
        session["messages"] = []

    session.setdefault("valid_until", None)
    if session.get("valid_until") is not None and not isinstance(session.get("valid_until"), str):
        session["valid_until"] = None

    session.setdefault("team_id", None)
    session.setdefault("player_id", None)

    session.setdefault("round", 0)
    if not isinstance(session.get("round"), int):
        try:
            session["round"] = int(session.get("round") or 0)
        except Exception:
            session["round"] = 0

    session.setdefault("max_rounds", 4)
    if not isinstance(session.get("max_rounds"), int):
        try:
            session["max_rounds"] = int(session.get("max_rounds") or 4)
        except Exception:
            session["max_rounds"] = 4

    session.setdefault("constraints", {})
    if not isinstance(session.get("constraints"), dict):
        session["constraints"] = {}

    # Snapshots
    session.setdefault("player_snapshot", {})
    if not isinstance(session.get("player_snapshot"), dict):
        session["player_snapshot"] = {}

    session.setdefault("team_snapshot", {})
    if not isinstance(session.get("team_snapshot"), dict):
        session["team_snapshot"] = {}

    session.setdefault("agency_snapshot", {})
    if not isinstance(session.get("agency_snapshot"), dict):
        session["agency_snapshot"] = {}

    session.setdefault("player_position", {})
    if not isinstance(session.get("player_position"), dict):
        session["player_position"] = {}

    # Offer / decision tracking
    session.setdefault("last_offer", None)
    session.setdefault("last_counter", None)
    session.setdefault("last_decision", None)
    session.setdefault("agreed_offer", None)

    # Walkout sensitivity tracking
    session.setdefault("lowball_strikes", 0)
    try:
        session["lowball_strikes"] = int(session.get("lowball_strikes") or 0)
    except Exception:
        session["lowball_strikes"] = 0

    return session


def _load_session_or_404(session_id: str) -> Dict[str, Any]:
    session = state.negotiation_session_get(session_id)
    if not session:
        raise ContractNegotiationError(
            NEGOTIATION_NOT_FOUND,
            "Negotiation session not found",
            {"session_id": session_id},
        )
    return _ensure_session_schema(session)


def _atomic_update(session_id: str, patch_fn) -> Dict[str, Any]:
    """Apply a mutation to a single session atomically and persist it."""

    def _mut(session: Dict[str, Any]) -> None:
        _ensure_session_schema(session)
        patch_fn(session)
        session["updated_at"] = _now_iso()

    try:
        return state.negotiation_session_update(session_id, _mut)
    except KeyError:
        raise ContractNegotiationError(
            NEGOTIATION_NOT_FOUND,
            "Negotiation session not found",
            {"session_id": session_id},
        )


# -----------------------------------------------------------------------------
# Public API (store)
# -----------------------------------------------------------------------------


def create_session(
    team_id: str,
    player_id: str,
    *,
    mode: str = "SIGN_FA",
    valid_until_iso: Optional[str] = None,
    max_rounds: Optional[int] = None,
    player_snapshot: Optional[dict] = None,
    team_snapshot: Optional[dict] = None,
    agency_snapshot: Optional[dict] = None,
    player_position: Optional[dict] = None,
    constraints: Optional[dict] = None,
) -> Dict[str, Any]:
    session_id = str(uuid4())
    session = {
        "session_id": session_id,
        "kind": "CONTRACT",
        "mode": str(mode),
        "status": "ACTIVE",
        "phase": "INIT",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "messages": [],
        "valid_until": valid_until_iso,
        "team_id": str(team_id).upper(),
        "player_id": str(player_id),
        "round": 0,
        "max_rounds": int(max_rounds) if isinstance(max_rounds, int) else 4,
        "constraints": dict(constraints or {}),
        "player_snapshot": dict(player_snapshot or {}),
        "team_snapshot": dict(team_snapshot or {}),
        "agency_snapshot": dict(agency_snapshot or {}),
        "player_position": dict(player_position or {}),
        "last_offer": None,
        "last_counter": None,
        "last_decision": None,
        "agreed_offer": None,
        "lowball_strikes": 0,
    }
    _ensure_session_schema(session)
    state.negotiation_session_put(session_id, session)
    return session


def get_session(session_id: str) -> Dict[str, Any]:
    return _load_session_or_404(session_id)


def append_message(session_id: str, speaker: str, text: str, *, meta: Optional[dict] = None) -> None:
    at = _now_iso()
    msg = {"speaker": str(speaker), "text": str(text), "at": at}
    if isinstance(meta, dict) and meta:
        msg["meta"] = dict(meta)

    def _patch(session: Dict[str, Any]) -> None:
        session["messages"].append(msg)

    _atomic_update(session_id, _patch)


def set_phase(session_id: str, phase: str) -> None:
    phase_value = phase if isinstance(phase, str) else "INIT"

    def _patch(session: Dict[str, Any]) -> None:
        session["phase"] = phase_value

    _atomic_update(session_id, _patch)


def set_status(session_id: str, status: str) -> None:
    status_value = (status if isinstance(status, str) else "ACTIVE").upper()

    def _patch(session: Dict[str, Any]) -> None:
        session["status"] = status_value

    _atomic_update(session_id, _patch)


def set_valid_until(session_id: str, valid_until_iso: Optional[str]) -> None:
    if valid_until_iso is not None and not isinstance(valid_until_iso, str):
        valid_until_iso = None

    def _patch(session: Dict[str, Any]) -> None:
        session["valid_until"] = valid_until_iso

    _atomic_update(session_id, _patch)


def set_constraints(session_id: str, constraints: dict) -> None:
    constraints_value = constraints if isinstance(constraints, dict) else {}

    def _patch(session: Dict[str, Any]) -> None:
        session["constraints"] = dict(constraints_value)

    _atomic_update(session_id, _patch)


def set_player_snapshot(session_id: str, snap: dict) -> None:
    snap_value = snap if isinstance(snap, dict) else {}

    def _patch(session: Dict[str, Any]) -> None:
        session["player_snapshot"] = dict(snap_value)

    _atomic_update(session_id, _patch)


def set_team_snapshot(session_id: str, snap: dict) -> None:
    snap_value = snap if isinstance(snap, dict) else {}

    def _patch(session: Dict[str, Any]) -> None:
        session["team_snapshot"] = dict(snap_value)

    _atomic_update(session_id, _patch)


def set_agency_snapshot(session_id: str, snap: dict) -> None:
    snap_value = snap if isinstance(snap, dict) else {}

    def _patch(session: Dict[str, Any]) -> None:
        session["agency_snapshot"] = dict(snap_value)

    _atomic_update(session_id, _patch)


def set_player_position(session_id: str, pos: dict) -> None:
    pos_value = pos if isinstance(pos, dict) else {}

    def _patch(session: Dict[str, Any]) -> None:
        session["player_position"] = dict(pos_value)

    _atomic_update(session_id, _patch)


def set_last_offer(session_id: str, payload: Any) -> None:
    try:
        json.dumps(payload)
    except (TypeError, OverflowError):
        raise ContractNegotiationError(
            NEGOTIATION_BAD_PAYLOAD,
            "last_offer payload must be JSON-serializable",
            {"session_id": session_id},
        )

    def _patch(session: Dict[str, Any]) -> None:
        session["last_offer"] = payload

    _atomic_update(session_id, _patch)


def set_last_counter(session_id: str, payload: Any) -> None:
    try:
        json.dumps(payload)
    except (TypeError, OverflowError):
        raise ContractNegotiationError(
            NEGOTIATION_BAD_PAYLOAD,
            "last_counter payload must be JSON-serializable",
            {"session_id": session_id},
        )

    def _patch(session: Dict[str, Any]) -> None:
        session["last_counter"] = payload

    _atomic_update(session_id, _patch)


def set_last_decision(session_id: str, payload: Any) -> None:
    try:
        json.dumps(payload)
    except (TypeError, OverflowError):
        raise ContractNegotiationError(
            NEGOTIATION_BAD_PAYLOAD,
            "last_decision payload must be JSON-serializable",
            {"session_id": session_id},
        )

    def _patch(session: Dict[str, Any]) -> None:
        session["last_decision"] = payload

    _atomic_update(session_id, _patch)


def set_agreed_offer(session_id: str, payload: Any) -> None:
    try:
        json.dumps(payload)
    except (TypeError, OverflowError):
        raise ContractNegotiationError(
            NEGOTIATION_BAD_PAYLOAD,
            "agreed_offer payload must be JSON-serializable",
            {"session_id": session_id},
        )

    def _patch(session: Dict[str, Any]) -> None:
        session["agreed_offer"] = payload

    _atomic_update(session_id, _patch)


def bump_round(session_id: str, delta: int = 1) -> int:
    try:
        inc = int(delta)
    except Exception:
        inc = 1

    def _patch(session: Dict[str, Any]) -> None:
        session["round"] = int(session.get("round", 0)) + inc

    out = _atomic_update(session_id, _patch)
    try:
        return int(out.get("round", 0))
    except Exception:
        return 0


def bump_lowball_strikes(session_id: str, delta: int = 1) -> int:
    try:
        inc = int(delta)
    except Exception:
        inc = 1

    def _patch(session: Dict[str, Any]) -> None:
        session["lowball_strikes"] = int(session.get("lowball_strikes", 0)) + inc

    out = _atomic_update(session_id, _patch)
    try:
        return int(out.get("lowball_strikes", 0))
    except Exception:
        return 0


def close_session(session_id: str, *, phase: Optional[str] = None, status: str = "CLOSED") -> None:
    def _patch(session: Dict[str, Any]) -> None:
        if phase is not None:
            session["phase"] = str(phase)
        session["status"] = str(status).upper()

    _atomic_update(session_id, _patch)
