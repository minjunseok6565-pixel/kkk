from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, Optional
from uuid import uuid4

import game_time
import state
from league_repo import LeagueRepo

from .errors import TradeError, NEGOTIATION_NOT_FOUND
from .models import Deal, canonicalize_deal, parse_deal, serialize_deal

NEGOTIATION_BAD_PAYLOAD = "NEGOTIATION_BAD_PAYLOAD"


def _now_iso() -> str:
    return game_time.now_utc_like_iso()


def _is_deal_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("teams"), list) and isinstance(payload.get("legs"), dict)


def _extract_deal_payload(payload: Any) -> Dict[str, Any]:
    if _is_deal_payload(payload):
        return payload
    if not isinstance(payload, dict):
        return {}
    for key in ("offer", "deal"):
        candidate = payload.get(key)
        if _is_deal_payload(candidate):
            return candidate
    return {}


def _collect_asset_ids(deal_payload: Dict[str, Any]) -> tuple[list[str], list[str]]:
    player_ids: list[str] = []
    pick_ids: list[str] = []
    legs = deal_payload.get("legs") if isinstance(deal_payload, dict) else {}
    for assets in (legs.values() if isinstance(legs, dict) else []):
        if not isinstance(assets, list):
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            kind = str(asset.get("kind") or "").lower().strip()
            if kind == "player":
                pid = str(asset.get("player_id") or "").strip()
                if pid:
                    player_ids.append(pid)
            elif kind == "pick":
                pick_id = str(asset.get("pick_id") or "").strip()
                if pick_id:
                    pick_ids.append(pick_id)
    return list(dict.fromkeys(player_ids)), list(dict.fromkeys(pick_ids))


def _hydrate_player_snapshots(player_ids: list[str], *, db_path: str) -> Dict[str, Dict[str, str]]:
    if not player_ids:
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT player_id, name, pos
            FROM players
            WHERE player_id IN ({placeholders})
            """,
            tuple(player_ids),
        ).fetchall()
    out: Dict[str, Dict[str, str]] = {}
    for row in rows:
        pid = str(row["player_id"])
        out[pid] = {
            "display_name": str(row["name"] or "").strip(),
            "pos": str(row["pos"] or "").strip(),
        }
    return out


def _hydrate_pick_snapshots(pick_ids: list[str], *, db_path: str) -> Dict[str, Dict[str, Any]]:
    if not pick_ids:
        return {}
    with LeagueRepo(db_path) as repo:
        picks = repo.get_draft_picks_map() or {}
    out: Dict[str, Dict[str, Any]] = {}
    for pick_id in pick_ids:
        row = picks.get(pick_id)
        if not isinstance(row, dict):
            continue
        out[pick_id] = {
            "year": row.get("year"),
            "round": row.get("round"),
            "original_team": str(row.get("original_team") or "").upper(),
            "owner_team": str(row.get("owner_team") or "").upper(),
        }
    return out


def _require_nonempty_string(v: Any, *, field: str, context: Dict[str, Any]) -> str:
    out = str(v or "").strip()
    if not out:
        raise TradeError(NEGOTIATION_BAD_PAYLOAD, f"Missing required field: {field}", context)
    return out


def _canonicalize_last_offer_payload(payload: Any, *, session_id: str, db_path: str) -> Dict[str, Any]:
    deal = _extract_deal_payload(payload)
    if not deal:
        raise TradeError(
            NEGOTIATION_BAD_PAYLOAD,
            "last_offer payload must include deal.teams and deal.legs",
            {"session_id": session_id},
        )

    player_ids, pick_ids = _collect_asset_ids(deal)
    player_snaps = _hydrate_player_snapshots(player_ids, db_path=db_path)
    pick_snaps = _hydrate_pick_snapshots(pick_ids, db_path=db_path)

    teams = list(deal.get("teams") or [])
    legs = deal.get("legs") if isinstance(deal.get("legs"), dict) else {}
    out_legs: Dict[str, list[Dict[str, Any]]] = {}
    for from_team, assets in legs.items():
        from_key = str(from_team or "").upper()
        out_assets: list[Dict[str, Any]] = []
        for idx, asset in enumerate(assets if isinstance(assets, list) else []):
            if not isinstance(asset, dict):
                continue
            row = dict(asset)
            kind = str(asset.get("kind") or "").lower().strip()
            if kind == "player":
                pid = _require_nonempty_string(asset.get("player_id"), field="player_id", context={"session_id": session_id, "path": f"legs.{from_key}[{idx}]"})
                snap = player_snaps.get(pid) or {}
                row["player_id"] = pid
                row["display_name"] = _require_nonempty_string(
                    snap.get("display_name") or asset.get("display_name"),
                    field="display_name",
                    context={"session_id": session_id, "player_id": pid, "path": f"legs.{from_key}[{idx}]"},
                )
                row["pos"] = _require_nonempty_string(
                    snap.get("pos") or asset.get("pos"),
                    field="pos",
                    context={"session_id": session_id, "player_id": pid, "path": f"legs.{from_key}[{idx}]"},
                )
            elif kind == "pick":
                pick_id = _require_nonempty_string(asset.get("pick_id"), field="pick_id", context={"session_id": session_id, "path": f"legs.{from_key}[{idx}]"})
                snap = pick_snaps.get(pick_id) or {}
                row["pick_id"] = pick_id
                try:
                    row["year"] = int(snap.get("year") if snap.get("year") is not None else asset.get("year"))
                except Exception as exc:
                    raise TradeError(
                        NEGOTIATION_BAD_PAYLOAD,
                        "Missing required field: year",
                        {"session_id": session_id, "pick_id": pick_id, "path": f"legs.{from_key}[{idx}]"},
                    ) from exc
                try:
                    row["round"] = int(snap.get("round") if snap.get("round") is not None else asset.get("round"))
                except Exception as exc:
                    raise TradeError(
                        NEGOTIATION_BAD_PAYLOAD,
                        "Missing required field: round",
                        {"session_id": session_id, "pick_id": pick_id, "path": f"legs.{from_key}[{idx}]"},
                    ) from exc
                row["original_team"] = _require_nonempty_string(
                    snap.get("original_team") or asset.get("original_team"),
                    field="original_team",
                    context={"session_id": session_id, "pick_id": pick_id, "path": f"legs.{from_key}[{idx}]"},
                )
                row["owner_team"] = _require_nonempty_string(
                    snap.get("owner_team") or asset.get("owner_team") or from_key,
                    field="owner_team",
                    context={"session_id": session_id, "pick_id": pick_id, "path": f"legs.{from_key}[{idx}]"},
                )
            out_assets.append(row)
        out_legs[from_key] = out_assets
    return {
        "teams": teams,
        "legs": out_legs,
        "meta": deal.get("meta") if isinstance(deal.get("meta"), dict) else {},
    }


def _ensure_session_schema(session: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure negotiation sessions include new schema defaults."""
    default_relationship = {"trust": 0, "fatigue": 0, "promises_broken": 0}
    default_summary = {"text": "", "updated_at": None}

    messages = session.get("messages")
    if not isinstance(messages, list):
        session["messages"] = []

    session.setdefault("phase", "INIT")
    if not isinstance(session.get("phase"), str):
        session["phase"] = "INIT"

    session.setdefault("status", "ACTIVE")
    status = session.get("status")
    if not isinstance(status, str):
        session["status"] = "ACTIVE"
    else:
        session["status"] = status.upper()

    session.setdefault("last_offer", None)
    session.setdefault("last_counter", None)

    session.setdefault("constraints", {})
    if not isinstance(session.get("constraints"), dict):
        session["constraints"] = {}

    session.setdefault("valid_until", None)
    valid_until = session.get("valid_until")
    if valid_until is not None and not isinstance(valid_until, str):
        session["valid_until"] = None

    session.setdefault("auto_end", {"status": "PENDING", "ended_at": None, "reason": None, "score": None, "detail": None})
    auto_end = session.get("auto_end")
    if not isinstance(auto_end, dict):
        auto_end = {"status": "PENDING", "ended_at": None, "reason": None, "score": None, "detail": None}
        session["auto_end"] = auto_end
    status = auto_end.get("status")
    auto_end["status"] = str(status).upper() if isinstance(status, str) and status else "PENDING"
    auto_end.setdefault("ended_at", None)
    if auto_end.get("ended_at") is not None and not isinstance(auto_end.get("ended_at"), str):
        auto_end["ended_at"] = None
    auto_end.setdefault("reason", None)
    if auto_end.get("reason") is not None and not isinstance(auto_end.get("reason"), str):
        auto_end["reason"] = None
    auto_end.setdefault("score", None)
    try:
        if auto_end.get("score") is not None:
            auto_end["score"] = float(auto_end.get("score"))
    except Exception:
        auto_end["score"] = None
    auto_end.setdefault("detail", None)
    if auto_end.get("detail") is not None and not isinstance(auto_end.get("detail"), dict):
        auto_end["detail"] = None

    session.setdefault("last_user_action_at", None)
    if session.get("last_user_action_at") is not None and not isinstance(session.get("last_user_action_at"), str):
        session["last_user_action_at"] = None

    session.setdefault("last_ai_action_at", None)
    if session.get("last_ai_action_at") is not None and not isinstance(session.get("last_ai_action_at"), str):
        session["last_ai_action_at"] = None

    session.setdefault("summary", dict(default_summary))
    summary = session.get("summary")
    if not isinstance(summary, dict):
        summary = dict(default_summary)
        session["summary"] = summary
    summary.setdefault("text", "")
    summary.setdefault("updated_at", None)

    session.setdefault("relationship", dict(default_relationship))
    relationship = session.get("relationship")
    if not isinstance(relationship, dict):
        relationship = dict(default_relationship)
        session["relationship"] = relationship
    relationship.setdefault("trust", 0)
    relationship.setdefault("fatigue", 0)
    relationship.setdefault("promises_broken", 0)

    session.setdefault("market_context", {})
    if not isinstance(session.get("market_context"), dict):
        session["market_context"] = {}

    return session


def _load_session_or_404(session_id: str) -> Dict[str, Any]:
    session = state.negotiation_session_get(session_id)
    if not session:
        raise TradeError(
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
        raise TradeError(
            NEGOTIATION_NOT_FOUND,
            "Negotiation session not found",
            {"session_id": session_id},
        )


def create_session(user_team_id: str, other_team_id: str) -> Dict[str, Any]:
    session_id = str(uuid4())
    session = {
        "session_id": session_id,
        "user_team_id": user_team_id.upper(),
        "other_team_id": other_team_id.upper(),
        "messages": [],
        "status": "ACTIVE",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "draft_deal": None,
        "committed_deal_id": None,
        "phase": "INIT",  # negotiation FSM phase
        "last_offer": None,  # last deal payload offered
        "last_counter": None,  # last counter-offer payload
        "constraints": {},  # negotiation constraints metadata
        "valid_until": None,  # ISO expiry or None
        "auto_end": {"status": "PENDING", "ended_at": None, "reason": None, "score": None, "detail": None},
        "last_user_action_at": None,
        "last_ai_action_at": None,
        "summary": {"text": "", "updated_at": None},  # session summary metadata
        "relationship": {"trust": 0, "fatigue": 0, "promises_broken": 0},
        "market_context": {},  # trade market context snapshot
    }
    state.negotiation_session_put(session_id, session)
    return session


def get_session(session_id: str) -> Dict[str, Any]:
    return _load_session_or_404(session_id)


def append_message(session_id: str, speaker: str, text: str) -> None:
    at = _now_iso()
    msg = {"speaker": speaker, "text": text, "at": at}

    def _patch(session: Dict[str, Any]) -> None:
        session["messages"].append(msg)

    _atomic_update(session_id, _patch)


def touch_user_action(session_id: str, at_iso: Optional[str] = None) -> None:
    at = str(at_iso).strip() if isinstance(at_iso, str) and at_iso.strip() else _now_iso()

    def _patch(session: Dict[str, Any]) -> None:
        session["last_user_action_at"] = at

    _atomic_update(session_id, _patch)


def touch_ai_action(session_id: str, at_iso: Optional[str] = None) -> None:
    at = str(at_iso).strip() if isinstance(at_iso, str) and at_iso.strip() else _now_iso()

    def _patch(session: Dict[str, Any]) -> None:
        session["last_ai_action_at"] = at

    _atomic_update(session_id, _patch)


def set_draft_deal(session_id: str, deal_serialized: dict) -> None:
    deal: Deal = canonicalize_deal(parse_deal(deal_serialized))
    deal_payload = serialize_deal(deal)

    def _patch(session: Dict[str, Any]) -> None:
        session["draft_deal"] = deal_payload

    _atomic_update(session_id, _patch)


def set_committed(session_id: str, deal_id: str) -> None:
    def _patch(session: Dict[str, Any]) -> None:
        session["committed_deal_id"] = deal_id

    _atomic_update(session_id, _patch)


def set_phase(session_id: str, phase: str) -> None:
    phase_value = phase if isinstance(phase, str) else "INIT"

    def _patch(session: Dict[str, Any]) -> None:
        session["phase"] = phase_value

    _atomic_update(session_id, _patch)


def set_status(session_id: str, status: str) -> None:
    status_value = status if isinstance(status, str) else "ACTIVE"
    status_value = status_value.upper()

    def _patch(session: Dict[str, Any]) -> None:
        session["status"] = status_value

    _atomic_update(session_id, _patch)


def set_constraints(session_id: str, constraints: dict) -> None:
    constraints_value = constraints if isinstance(constraints, dict) else {}

    def _patch(session: Dict[str, Any]) -> None:
        session["constraints"] = constraints_value

    _atomic_update(session_id, _patch)


def set_valid_until(session_id: str, valid_until_iso: Optional[str]) -> None:
    if valid_until_iso is not None and not isinstance(valid_until_iso, str):
        valid_until_iso = None

    def _patch(session: Dict[str, Any]) -> None:
        session["valid_until"] = valid_until_iso

    _atomic_update(session_id, _patch)


def set_market_context_offer_meta(session_id: str, offer_meta: dict) -> None:
    if not isinstance(offer_meta, dict):
        offer_meta = {}

    def _patch(session: Dict[str, Any]) -> None:
        mc = session.get("market_context")
        if not isinstance(mc, dict):
            mc = {}
            session["market_context"] = mc
        mc["offer_meta"] = dict(offer_meta)

    _atomic_update(session_id, _patch)


def set_summary(session_id: str, summary: dict) -> None:
    if not isinstance(summary, dict):
        summary = {"text": "", "updated_at": None}
    summary.setdefault("text", "")
    summary.setdefault("updated_at", None)

    def _patch(session: Dict[str, Any]) -> None:
        session["summary"] = summary

    _atomic_update(session_id, _patch)


def bump_fatigue(session_id: str, delta: int = 1) -> None:
    try:
        increment = int(delta)
    except (TypeError, ValueError):
        increment = 1

    def _patch(session: Dict[str, Any]) -> None:
        relationship = session.get("relationship")
        if not isinstance(relationship, dict):
            relationship = {"trust": 0, "fatigue": 0, "promises_broken": 0}
            session["relationship"] = relationship
        relationship["fatigue"] = int(relationship.get("fatigue", 0)) + increment

    _atomic_update(session_id, _patch)


def set_relationship(session_id: str, patch: dict) -> None:
    def _patch(session: Dict[str, Any]) -> None:
        relationship = session.get("relationship")
        if not isinstance(relationship, dict):
            relationship = {"trust": 0, "fatigue": 0, "promises_broken": 0}
            session["relationship"] = relationship
        if isinstance(patch, dict):
            for key in ("trust", "fatigue", "promises_broken"):
                if key in patch:
                    relationship[key] = patch[key]

    _atomic_update(session_id, _patch)


def set_last_offer(session_id: str, payload: Any) -> None:
    try:
        json.dumps(payload)
    except (TypeError, OverflowError):
        raise TradeError(
            NEGOTIATION_BAD_PAYLOAD,
            "last_offer payload must be JSON-serializable",
            {"session_id": session_id},
        )

    db_path = state.get_db_path()
    canonical_payload = _canonicalize_last_offer_payload(payload, session_id=session_id, db_path=db_path)

    def _patch(session: Dict[str, Any]) -> None:
        session["last_offer"] = canonical_payload

    _atomic_update(session_id, _patch)


def set_last_counter(session_id: str, payload: Any) -> None:
    try:
        json.dumps(payload)
    except (TypeError, OverflowError):
        raise TradeError(
            NEGOTIATION_BAD_PAYLOAD,
            "last_counter payload must be JSON-serializable",
            {"session_id": session_id},
        )

    def _patch(session: Dict[str, Any]) -> None:
        session["last_counter"] = payload

    _atomic_update(session_id, _patch)


def close_as_rejected(session_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
    reason_text = str(reason or "").strip()
    idempotent = False

    def _patch(session: Dict[str, Any]) -> None:
        nonlocal idempotent
        current_phase = str(session.get("phase") or "").upper()
        current_status = str(session.get("status") or "").upper()
        if current_phase == "REJECTED" and current_status == "CLOSED":
            idempotent = True
            return

        session["phase"] = "REJECTED"
        session["status"] = "CLOSED"

        if reason_text:
            messages = session.get("messages")
            if not isinstance(messages, list):
                messages = []
                session["messages"] = messages
            messages.append({
                "speaker": "USER_GM",
                "text": f"REJECTED: {reason_text}",
                "at": _now_iso(),
            })

    updated = _atomic_update(session_id, _patch)
    return {"session": updated, "idempotent": bool(idempotent)}


def mark_auto_ended(
    session_id: str,
    reason: str,
    score: Optional[float] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    reason_text = str(reason or "").strip().upper() or "AI_DECISION"
    detail_payload = dict(detail) if isinstance(detail, dict) else None
    score_value: Optional[float]
    try:
        score_value = None if score is None else float(score)
    except Exception:
        score_value = None

    idempotent = False

    def _patch(session: Dict[str, Any]) -> None:
        nonlocal idempotent
        current_status = str(session.get("status") or "").upper()
        current_phase = str(session.get("phase") or "").upper()
        auto_end = session.get("auto_end") if isinstance(session.get("auto_end"), dict) else {}
        if str(auto_end.get("status") or "").upper() == "ENDED" and current_status == "CLOSED":
            idempotent = True
            return

        session["status"] = "CLOSED"
        session["phase"] = "EXPIRED_BY_AI"
        session["auto_end"] = {
            "status": "ENDED",
            "ended_at": _now_iso(),
            "reason": reason_text,
            "score": score_value,
            "detail": detail_payload,
        }

        if current_phase != "EXPIRED_BY_AI" or current_status != "CLOSED":
            messages = session.get("messages")
            if not isinstance(messages, list):
                messages = []
                session["messages"] = messages
            messages.append({
                "speaker": "OTHER_GM",
                "text": f"AUTO_ENDED_BY_AI: {reason_text}",
                "at": _now_iso(),
            })

    updated = _atomic_update(session_id, _patch)
    return {"session": updated, "idempotent": bool(idempotent)}


def open_inbox_session(session_id: str) -> Dict[str, Any]:
    idempotent = False

    def _patch(session: Dict[str, Any]) -> None:
        nonlocal idempotent
        phase = str(session.get("phase") or "INIT").upper()
        status = str(session.get("status") or "ACTIVE").upper()

        if status != "ACTIVE":
            raise TradeError(
                "NEGOTIATION_NOT_ACTIVE",
                "Negotiation session is closed",
                {"session_id": session_id, "status": status},
            )

        if phase in {"NEGOTIATING", "COUNTER_PENDING"}:
            idempotent = True
            return

        if phase in {"INIT", "INBOX_PENDING"}:
            session["phase"] = "NEGOTIATING"
            return

        raise TradeError(
            "NEGOTIATION_INVALID_PHASE",
            "Negotiation session cannot be opened from current phase",
            {"session_id": session_id, "phase": phase},
        )

    updated = _atomic_update(session_id, _patch)
    return {"session": updated, "idempotent": bool(idempotent)}


def mark_committed_and_close(
    session_id: str,
    *,
    deal_id: str,
    expires_at: Optional[str] = None,
) -> Dict[str, Any]:
    deal_id_s = str(deal_id or "").strip()
    if not deal_id_s:
        raise TradeError("DEAL_INVALIDATED", "deal_id is required", {"session_id": session_id})

    expires_value = expires_at if isinstance(expires_at, str) else None
    idempotent = False

    def _patch(session: Dict[str, Any]) -> None:
        nonlocal idempotent
        status = str(session.get("status") or "").upper()
        phase = str(session.get("phase") or "").upper()
        cur_deal = str(session.get("committed_deal_id") or "").strip()

        if status == "CLOSED" and phase == "ACCEPTED" and cur_deal == deal_id_s:
            idempotent = True
            return

        if status != "ACTIVE":
            raise TradeError(
                "NEGOTIATION_NOT_ACTIVE",
                "Negotiation session is closed",
                {"session_id": session_id, "status": status, "phase": phase},
            )

        if phase not in {"NEGOTIATING", "COUNTER_PENDING"}:
            raise TradeError(
                "NEGOTIATION_INVALID_PHASE",
                "Negotiation session cannot be committed from current phase",
                {"session_id": session_id, "phase": phase},
            )

        session["committed_deal_id"] = deal_id_s
        session["status"] = "CLOSED"
        session["phase"] = "ACCEPTED"
        session["valid_until"] = expires_value

    updated = _atomic_update(session_id, _patch)
    return {"session": updated, "idempotent": bool(idempotent)}
