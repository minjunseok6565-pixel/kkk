from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional
from dataclasses import asdict, is_dataclass

import hashlib
import json
import sqlite3
import time

from fastapi import APIRouter, HTTPException

import state
from config import ALL_TEAM_IDS
from league_repo import LeagueRepo
from trades import agreements, negotiation_store
from trades.apply import apply_deal_to_db
from trades.errors import TradeError
from trades.models import canonicalize_deal, parse_deal, serialize_deal
from trades.validator import validate_deal
from trades.orchestration.types import OrchestrationConfig
from trades.orchestration.ai_end_policy import evaluate_and_maybe_end
from trades.orchestration.market_state import (
    load_trade_market,
    save_trade_market,
    load_trade_memory,
    save_trade_memory,
    upsert_trade_listing,
    remove_trade_listing,
    list_team_trade_listings,
    record_market_event,
    bump_relationship,
    get_rel_meta_date_iso,
    is_private_leak_publicized,
)
from app.schemas.trades import (
    TradeBlockListRequest,
    TradeBlockAggregateQuery,
    TradeBlockUnlistRequest,
    TradeEvaluateRequest,
    TradeNegotiationInboxQuery,
    TradeNegotiationCommitRequest,
    TradeNegotiationOpenRequest,
    TradeNegotiationRejectRequest,
    TradeNegotiationInboxResponse,
    TradeNegotiationStartRequest,
    TradeSubmitCommittedRequest,
    TradeSubmitRequest,
)
from app.schemas.trade_lab import (
    TradeLabAssetPick,
    TradeLabAssetPlayer,
    TradeLabTeamAssetsResponse,
)
from app.services.cache_facade import _try_ui_cache_refresh_players
from app.services.contract_facade import _validate_repo_integrity
from app.services.trade_facade import _trade_error_response
from app.services.trade_contract_telemetry import emit_trade_contract_violation
from agency.service import apply_trade_offer_grievances
from schema import normalize_team_id

router = APIRouter()

_IDEMPOTENCY_CACHE_TTL_S = 120
_idempotency_result_cache: Dict[str, Dict[str, Any]] = {}


def _normalize_offer_privacy(raw: Any, *, default: str = "PRIVATE") -> str:
    v = str(raw or default).upper()
    return "PUBLIC" if v == "PUBLIC" else "PRIVATE"


def _normalize_idempotency_key(raw: Any) -> str:
    return str(raw or "").strip()


def _hash_payload(payload: Any) -> str:
    try:
        serialized = str(payload) if isinstance(payload, str) else json.dumps(payload, sort_keys=True, ensure_ascii=False)
    except Exception:
        serialized = str(payload)
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()


def _make_idempotency_cache_key(*, scope: str, session_id: str, team_id: str, key: str) -> str:
    return "::".join([
        str(scope or "").strip() or "unknown",
        str(session_id or "").strip() or "none",
        str(team_id or "").strip().upper() or "none",
        _normalize_idempotency_key(key),
    ])


def _gc_idempotency_cache(now_ts: Optional[float] = None) -> None:
    now = float(now_ts or time.time())
    stale_keys = [
        cache_key
        for cache_key, entry in _idempotency_result_cache.items()
        if now - float(entry.get("created_at") or 0.0) > _IDEMPOTENCY_CACHE_TTL_S
    ]
    for cache_key in stale_keys:
        _idempotency_result_cache.pop(cache_key, None)


def _idempotency_replay_guard(
    *,
    scope: str,
    session_id: str,
    team_id: str,
    key: str,
    payload_hash: str,
) -> Optional[Dict[str, Any]]:
    idem_key = _normalize_idempotency_key(key)
    if not idem_key:
        return None

    _gc_idempotency_cache()
    cache_key = _make_idempotency_cache_key(
        scope=scope,
        session_id=session_id,
        team_id=team_id,
        key=idem_key,
    )
    cached = _idempotency_result_cache.get(cache_key)
    if not cached:
        return None

    if str(cached.get("payload_hash") or "") != str(payload_hash or ""):
        raise TradeError(
            "NEGOTIATION_IDEMPOTENCY_CONFLICT",
            "idempotency_key already used with a different payload",
            {"scope": scope, "session_id": session_id, "team_id": team_id},
        )

    out = dict(cached.get("response") or {})
    out["idempotent"] = True
    out["idempotency_key"] = idem_key
    return out


def _store_idempotency_response(
    *,
    scope: str,
    session_id: str,
    team_id: str,
    key: str,
    payload_hash: str,
    response_payload: Dict[str, Any],
) -> None:
    idem_key = _normalize_idempotency_key(key)
    if not idem_key:
        return
    _gc_idempotency_cache()
    cache_key = _make_idempotency_cache_key(
        scope=scope,
        session_id=session_id,
        team_id=team_id,
        key=idem_key,
    )
    _idempotency_result_cache[cache_key] = {
        "created_at": time.time(),
        "payload_hash": str(payload_hash or ""),
        "response": dict(response_payload or {}),
    }


def _extract_outgoing_player_ids_for_team(deal_payload: Dict[str, Any], *, from_team_id: str) -> List[str]:
    out: List[str] = []
    legs = deal_payload.get("legs") if isinstance(deal_payload, dict) else None
    if not isinstance(legs, list):
        return out
    src = str(from_team_id).upper()
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        if str(leg.get("from_team") or "").upper() != src:
            continue
        assets = leg.get("assets")
        if not isinstance(assets, list):
            continue
        for a in assets:
            if not isinstance(a, dict):
                continue
            if str(a.get("kind") or "").upper() != "PLAYER":
                continue
            pid = a.get("player_id")
            if pid:
                out.append(str(pid))
    # stable dedupe
    uniq: List[str] = []
    seen = set()
    for pid in out:
        if pid in seen:
            continue
        seen.add(pid)
        uniq.append(pid)
    return uniq




def _extract_incoming_player_ids_for_team(deal_payload: Dict[str, Any], *, to_team_id: str) -> List[str]:
    incoming: List[str] = []
    legs = deal_payload.get("legs") if isinstance(deal_payload, dict) else None
    if not isinstance(legs, list):
        return incoming
    dst = str(to_team_id).upper()
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        if str(leg.get("to_team") or "").upper() != dst:
            continue
        assets = leg.get("assets")
        if not isinstance(assets, list):
            continue
        for a in assets:
            if not isinstance(a, dict):
                continue
            if str(a.get("kind") or "").upper() != "PLAYER":
                continue
            pid = a.get("player_id")
            if pid:
                incoming.append(str(pid))
    uniq: List[str] = []
    seen = set()
    for pid in incoming:
        if pid in seen:
            continue
        seen.add(pid)
        uniq.append(pid)
    return uniq


def _get_season_year_from_state(*, fallback_year: int) -> int:
    active = state.get_active_season_id()
    if isinstance(active, str) and active.strip():
        s0 = active.strip()
        try:
            return int(s0[:4])
        except Exception:
            pass
    # defensive fallback for malformed state; keep deterministic positive int
    return int(fallback_year)

def _player_current_team_id(player_id: str, *, db_path: str) -> str:
    pid = str(player_id)
    with LeagueRepo(db_path) as repo:
        team_map = repo.get_team_ids_by_players([pid]) or {}
    return str((team_map or {}).get(pid) or "").upper()


def _require_player_on_team(*, player_id: str, team_id: str, db_path: str) -> None:
    current = _player_current_team_id(player_id, db_path=db_path)
    expect = str(team_id).upper()
    if not current or current != expect:
        raise TradeError(
            "TRADE_BLOCK_PLAYER_TEAM_MISMATCH",
            "Player is not on the specified team",
            {"player_id": str(player_id), "team_id": expect, "current_team_id": current or None},
        )


def _clamp_int(v: Any, *, lo: int, hi: int, default: int) -> int:
    try:
        x = int(v)
    except Exception:
        x = int(default)
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def _normalize_trade_block_visibility(raw: Any, *, default: str = "PUBLIC") -> str:
    v = str(raw or default).upper().strip()
    if v in {"PUBLIC", "PRIVATE", "ALL"}:
        return v
    return str(default).upper()


def _normalize_trade_block_sort(raw: Any, *, default: str = "priority_desc") -> str:
    v = str(raw or default).lower().strip()
    allowed = {"priority_desc", "ovr_desc", "updated_desc", "age_asc", "age_desc"}
    if v in allowed:
        return v
    return str(default).lower()


def _normalize_trade_lab_team_id(raw: Any) -> str:
    tid_raw = str(raw or "").strip()
    if not tid_raw:
        raise TradeError(
            "TRADE_LAB_INVALID_TEAM_ID",
            "team_id is required",
            {"team_id": raw},
        )
    try:
        tid = str(normalize_team_id(tid_raw, strict=True)).upper()
    except Exception:
        raise TradeError(
            "TRADE_LAB_INVALID_TEAM_ID",
            "Invalid team_id",
            {"team_id": tid_raw},
        )
    if tid not in ALL_TEAM_IDS:
        raise TradeError(
            "TRADE_LAB_INVALID_TEAM_ID",
            "Invalid team_id",
            {"team_id": tid_raw, "normalized_team_id": tid},
        )
    return tid


def _parse_trade_block_aggregate_query(
    *,
    active_only: Any = True,
    visibility: Any = "PUBLIC",
    team_id: Any = None,
    limit: Any = 300,
    offset: Any = 0,
    sort: Any = "priority_desc",
) -> TradeBlockAggregateQuery:
    tid = str(team_id or "").upper().strip() or None
    return TradeBlockAggregateQuery(
        active_only=bool(active_only),
        visibility=_normalize_trade_block_visibility(visibility, default="PUBLIC"),
        team_id=tid,
        limit=_clamp_int(limit, lo=1, hi=500, default=300),
        offset=_clamp_int(offset, lo=0, hi=1_000_000, default=0),
        sort=_normalize_trade_block_sort(sort, default="priority_desc"),
    )


def _normalize_pick_protection_payload(raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return dict(raw)
    return None


@router.get("/api/trade/lab/team-assets")
async def api_trade_lab_team_assets(team_id: str):
    """Return tradable team assets for Trade Lab (players + first-round picks)."""
    try:
        tid = _normalize_trade_lab_team_id(team_id)
        current_date = state.get_current_date_as_date()
        db_path = state.get_db_path()

        with LeagueRepo(db_path) as repo:
            roster_rows = repo.get_team_roster(tid) or []
            draft_picks_map = repo.get_draft_picks_map() or {}

        players: List[TradeLabAssetPlayer] = []
        for row in roster_rows:
            attrs = row.get("attrs") if isinstance(row, dict) else {}
            if not isinstance(attrs, dict):
                attrs = {}
            injury = attrs.get("injury")
            injury_payload = dict(injury) if isinstance(injury, dict) else None

            players.append(
                TradeLabAssetPlayer(
                    kind="player",
                    player_id=str(row.get("player_id") or ""),
                    name=str(row.get("name") or ""),
                    pos=str(row.get("pos") or ""),
                    age=int(_first_number(row.get("age"), default=0)),
                    ovr=int(_first_number(row.get("ovr"), default=0)),
                    salary=float(_first_number(row.get("salary_amount"), default=0.0)),
                    team_id=tid,
                    injury=injury_payload,
                )
            )

        players.sort(
            key=lambda x: (
                -int(_first_number(x.ovr, default=0)),
                int(_first_number(x.age, default=999)),
                str(x.player_id),
            )
        )

        first_round_picks: List[TradeLabAssetPick] = []
        for pick_id, row in draft_picks_map.items():
            if not isinstance(row, dict):
                continue

            owner_team = str(row.get("owner_team") or "").upper()
            if owner_team != tid:
                continue

            round_no = int(_first_number(row.get("round"), default=0))
            if round_no != 1:
                continue

            first_round_picks.append(
                TradeLabAssetPick(
                    kind="pick",
                    pick_id=str(pick_id),
                    year=int(_first_number(row.get("year"), default=0)),
                    round=round_no,
                    original_team=str(row.get("original_team") or "").upper(),
                    owner_team=owner_team,
                    protection=_normalize_pick_protection_payload(row.get("protection")),
                )
            )

        first_round_picks.sort(
            key=lambda x: (
                int(_first_number(x.year, default=9999)),
                str(x.pick_id),
            )
        )

        response = TradeLabTeamAssetsResponse(
            ok=True,
            team_id=tid,
            current_date=current_date.isoformat(),
            players=players,
            first_round_picks=first_round_picks,
        )
        return response.dict()
    except TradeError as exc:
        return _trade_error_response(exc)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Trade lab team assets failed: {exc}")


def _first_number(*values: Any, default: float = 0.0) -> float:
    for v in values:
        try:
            n = float(v)
        except Exception:
            continue
        if n == n:  # NaN guard
            return n
    return float(default)


def _collect_player_ids_from_deal_obj(deal: Any) -> List[str]:
    out: List[str] = []
    legs = getattr(deal, "legs", None)
    if not isinstance(legs, dict):
        return out
    for assets in legs.values():
        if not isinstance(assets, list):
            continue
        for asset in assets:
            kind = str(getattr(asset, "kind", "")).lower()
            if kind != "player":
                continue
            pid = str(getattr(asset, "player_id", "")).strip()
            if pid:
                out.append(pid)
    return list(dict.fromkeys(out))


def _to_jsonable_obj(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(k): _to_jsonable_obj(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable_obj(v) for v in value]
    return value


def _build_trade_evaluate_debug_context(*, team_id: str, deal: Any, db_path: str, in_game_date: date) -> Dict[str, Any]:
    """Best-effort debug context payload for Trade Lab explainability cards."""
    tid = str(team_id).upper()
    out: Dict[str, Any] = {
        "team": {},
        "players": [],
        "meta": {"team_id": tid, "current_date": in_game_date.isoformat()},
    }
    try:
        from data.team_situation import build_team_situation_context, TeamSituationEvaluator  # type: ignore
        from decision_context import build_decision_context, gm_traits_from_profile_json, GMTradeTraits  # type: ignore
        from trades.valuation.data_context import build_repo_valuation_data_context  # type: ignore
    except Exception:
        return out

    # Team context + decision context
    try:
        ts_ctx = build_team_situation_context(db_path=db_path, current_date=in_game_date)
        ts_eval = TeamSituationEvaluator(ctx=ts_ctx, db_path=db_path).evaluate_team(tid)

        gm_profile: Dict[str, Any] = {}
        try:
            with LeagueRepo(db_path) as repo:
                gp = repo.get_gm_profile(tid) or {}
                gm_profile = dict(gp) if isinstance(gp, dict) else {"value": gp}
        except Exception:
            gm_profile = {}

        gm_traits = gm_traits_from_profile_json(gm_profile, default=GMTradeTraits())
        dctx = build_decision_context(team_situation=ts_eval, gm_traits=gm_traits, team_id=tid)
        out["team"] = {
            "team_situation": _to_jsonable_obj(ts_eval),
            "decision_context": _to_jsonable_obj(dctx),
            "gm_profile": gm_profile,
        }
    except Exception as exc:
        out["team"] = {"error": f"TEAM_CONTEXT_UNAVAILABLE:{type(exc).__name__}"}

    # Player context (only players participating in current deal)
    try:
        player_ids = _collect_player_ids_from_deal_obj(deal)
        if player_ids:
            season_year = _get_season_year_from_state(fallback_year=in_game_date.year)
            provider = build_repo_valuation_data_context(
                db_path=db_path,
                current_season_year=int(season_year),
                current_date_iso=in_game_date.isoformat(),
            )
            players_payload: List[Dict[str, Any]] = []
            for pid in player_ids:
                try:
                    snap = provider.get_player_snapshot(pid)
                    players_payload.append(_to_jsonable_obj(snap))
                except Exception:
                    continue
            out["players"] = players_payload
    except Exception as exc:
        out["players"] = []
        out["meta"]["players_error"] = f"PLAYER_CONTEXT_UNAVAILABLE:{type(exc).__name__}"

    return out


def _hydrate_trade_block_player_snapshots(player_ids: List[str], *, db_path: str) -> Dict[str, Dict[str, Any]]:
    pids = [str(pid) for pid in (player_ids or []) if str(pid)]
    uniq = list(dict.fromkeys(pids))
    if not uniq:
        return {}

    placeholders = ",".join("?" for _ in uniq)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT
                p.player_id,
                p.name,
                p.pos,
                p.age,
                p.ovr,
                p.height_in,
                p.weight_lb,
                p.attrs_json,
                r.team_id,
                r.salary_amount
            FROM players p
            LEFT JOIN roster r
              ON r.player_id = p.player_id
             AND r.status = 'active'
            WHERE p.player_id IN ({placeholders})
            """,
            tuple(uniq),
        ).fetchall()

    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        pid = str(r["player_id"])
        out[pid] = {
            "player_id": pid,
            "team_id": str(r["team_id"] or "").upper() or None,
            "name": str(r["name"] or ""),
            "pos": str(r["pos"] or ""),
            "age": int(_first_number(r["age"], default=0)),
            "overall": int(_first_number(r["ovr"], default=0)),
            "height_in": _first_number(r["height_in"], default=0),
            "weight_lb": _first_number(r["weight_lb"], default=0),
            "salary": _first_number(r["salary_amount"], default=0),
        }
    return out


def _build_trade_block_row(
    listing: Dict[str, Any],
    *,
    player_snapshots: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    pid = str(listing.get("player_id") or "")
    snap = player_snapshots.get(pid) or {}
    return {
        "player_id": pid,
        "team_id": str(listing.get("team_id") or "").upper(),
        "name": snap.get("name") or "-",
        "pos": snap.get("pos") or "-",
        "overall": int(_first_number(snap.get("overall"), default=0)),
        "age": int(_first_number(snap.get("age"), default=0)),
        "height_in": _first_number(snap.get("height_in"), default=0),
        "weight_lb": _first_number(snap.get("weight_lb"), default=0),
        "salary": _first_number(snap.get("salary"), default=0),
        "listing": {
            "status": str(listing.get("status") or "").upper() or "ACTIVE",
            "visibility": str(listing.get("visibility") or "").upper() or "PUBLIC",
            "priority": _first_number(listing.get("priority"), default=0.5),
            "reason_code": str(listing.get("reason_code") or "MANUAL").upper(),
            "listed_by": str(listing.get("listed_by") or "USER").upper(),
            "created_at": listing.get("created_at"),
            "updated_at": listing.get("updated_at"),
            "expires_on": listing.get("expires_on"),
        },
    }


def _iter_trade_block_listings(
    *,
    market: Dict[str, Any],
    today: date,
    team_id: Optional[str],
    active_only: bool,
) -> List[Dict[str, Any]]:
    if team_id:
        return list_team_trade_listings(
            market,
            team_id=team_id,
            active_only=bool(active_only),
            today=today,
        )

    rows: List[Dict[str, Any]] = []
    for tid in ALL_TEAM_IDS:
        rows.extend(
            list_team_trade_listings(
                market,
                team_id=tid,
                active_only=bool(active_only),
                today=today,
            )
        )
    return rows


def _apply_trade_block_visibility_filter(rows: List[Dict[str, Any]], *, visibility: str) -> List[Dict[str, Any]]:
    vis = _normalize_trade_block_visibility(visibility, default="PUBLIC")
    if vis == "ALL":
        return list(rows or [])
    return [
        row
        for row in (rows or [])
        if str((row or {}).get("visibility") or "PUBLIC").upper() == vis
    ]


def _sort_trade_block_rows(rows: List[Dict[str, Any]], *, sort_key: str) -> List[Dict[str, Any]]:
    key = _normalize_trade_block_sort(sort_key)
    data = list(rows or [])

    if key == "ovr_desc":
        data.sort(
            key=lambda r: (
                -int(_first_number(r.get("overall"), default=0)),
                -float(_first_number(((r.get("listing") or {}).get("priority")), default=0.0)),
                str(((r.get("listing") or {}).get("updated_at") or "")),
                str(r.get("player_id") or ""),
            )
        )
        return data

    if key == "updated_desc":
        data.sort(
            key=lambda r: (
                str(((r.get("listing") or {}).get("updated_at") or "")),
                -int(_first_number(r.get("overall"), default=0)),
                -float(_first_number(((r.get("listing") or {}).get("priority")), default=0.0)),
                str(r.get("player_id") or ""),
            ),
            reverse=True,
        )
        return data

    if key == "age_asc":
        data.sort(
            key=lambda r: (
                int(_first_number(r.get("age"), default=0)),
                -int(_first_number(r.get("overall"), default=0)),
                str(r.get("player_id") or ""),
            )
        )
        return data

    if key == "age_desc":
        data.sort(
            key=lambda r: (
                -int(_first_number(r.get("age"), default=0)),
                -int(_first_number(r.get("overall"), default=0)),
                str(r.get("player_id") or ""),
            )
        )
        return data

    # default: priority_desc
    data.sort(
        key=lambda r: (
            -float(_first_number(((r.get("listing") or {}).get("priority")), default=0.0)),
            -int(_first_number(r.get("overall"), default=0)),
            str(((r.get("listing") or {}).get("updated_at") or "")),
            str(r.get("player_id") or ""),
        )
    )
    return data


def _normalize_inbox_status(raw: Any, *, default: str = "ACTIVE") -> str:
    v = str(raw or default).upper().strip()
    if v in {"ACTIVE", "CLOSED", "ALL"}:
        return v
    return str(default).upper()


def _normalize_inbox_phase(raw: Any, *, default: str = "OPEN") -> str:
    v = str(raw or default).upper().strip()
    if v in {"OPEN", "COUNTER_PENDING", "REJECTED", "ACCEPTED", "ALL"}:
        return v
    return str(default).upper()


def _normalize_inbox_sort(raw: Any, *, default: str = "updated_desc") -> str:
    v = str(raw or default).lower().strip()
    if v in {"updated_desc", "created_desc", "expires_asc"}:
        return v
    return str(default).lower()


def _parse_trade_negotiation_inbox_query(
    *,
    team_id: Any,
    status: Any = "ACTIVE",
    phase: Any = "OPEN",
    include_expired: Any = False,
    limit: Any = 50,
    offset: Any = 0,
    sort: Any = "updated_desc",
) -> TradeNegotiationInboxQuery:
    tid = str(team_id or "").upper().strip()
    if not tid:
        raise TradeError("NEGOTIATION_BAD_QUERY", "team_id is required")
    return TradeNegotiationInboxQuery(
        team_id=tid,
        status=_normalize_inbox_status(status, default="ACTIVE"),
        phase=_normalize_inbox_phase(phase, default="OPEN"),
        include_expired=bool(include_expired),
        limit=_clamp_int(limit, lo=1, hi=200, default=50),
        offset=_clamp_int(offset, lo=0, hi=1_000_000, default=0),
        sort=_normalize_inbox_sort(sort, default="updated_desc"),
    )


def _iso_date(s: Any) -> Optional[date]:
    if not s:
        return None
    raw = str(s).strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except Exception:
        return None


def _ensure_session_ready_for_commit(
    session: Dict[str, Any],
    *,
    session_id: str,
    today: date,
) -> Optional[Dict[str, Any]]:
    status = str(session.get("status") or "ACTIVE").upper()
    phase = str(session.get("phase") or "INIT").upper()

    # Idempotent success: already accepted & closed with committed deal.
    committed_deal_id = str(session.get("committed_deal_id") or "").strip()
    if status == "CLOSED" and phase == "ACCEPTED" and committed_deal_id:
        return {
            "ok": True,
            "accepted": True,
            "idempotent": True,
            "deal_id": committed_deal_id,
            "expires_at": session.get("valid_until"),
            "session_id": str(session_id),
        }

    if status != "ACTIVE":
        raise TradeError(
            "NEGOTIATION_NOT_ACTIVE",
            "Negotiation session is closed",
            {"session_id": str(session_id), "status": status, "phase": phase},
        )

    if phase not in {"NEGOTIATING", "COUNTER_PENDING"}:
        raise TradeError(
            "NEGOTIATION_INVALID_PHASE",
            "Negotiation session cannot be committed from current phase",
            {"session_id": str(session_id), "phase": phase},
        )

    return None


def _phase_matches_inbox_filter(*, phase: str, phase_filter: str) -> bool:
    pf = _normalize_inbox_phase(phase_filter, default="OPEN")
    p = str(phase or "").upper()
    if pf == "ALL":
        return True
    if pf == "OPEN":
        return p in {"INIT", "INBOX_PENDING", "NEGOTIATING"}
    return p == pf


def _status_matches_filter(*, status: str, status_filter: str) -> bool:
    sf = _normalize_inbox_status(status_filter, default="ACTIVE")
    st = str(status or "").upper()
    if sf == "ALL":
        return True
    return st == sf


def _count_assets_for_user(offer_payload: Dict[str, Any], *, user_team_id: str) -> Dict[str, int]:
    counts = {
        "user_outgoing_players": 0,
        "user_incoming_players": 0,
        "user_outgoing_picks": 0,
        "user_incoming_picks": 0,
    }
    legs = offer_payload.get("legs") if isinstance(offer_payload, dict) else None
    tid = str(user_team_id).upper()

    if isinstance(legs, list):
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            from_team = str(leg.get("from_team") or "").upper()
            to_team = str(leg.get("to_team") or "").upper()
            assets = leg.get("assets")
            if not isinstance(assets, list):
                continue

            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                kind = str(asset.get("kind") or "").upper()
                if kind not in {"PLAYER", "PICK"}:
                    continue
                if from_team == tid:
                    key = "user_outgoing_players" if kind == "PLAYER" else "user_outgoing_picks"
                    counts[key] += 1
                if to_team == tid:
                    key = "user_incoming_players" if kind == "PLAYER" else "user_incoming_picks"
                    counts[key] += 1
        return counts

    if isinstance(legs, dict):
        teams = [str(t).upper() for t in (offer_payload.get("teams") or []) if str(t)]
        counterpart = next((team for team in teams if team != tid), "")
        for from_team_raw, assets in legs.items():
            from_team = str(from_team_raw or "").upper()
            if not isinstance(assets, list):
                continue
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                kind = str(asset.get("kind") or "").upper()
                if kind not in {"PLAYER", "PICK"}:
                    continue
                to_team = str(asset.get("to_team") or "").upper() or (counterpart if counterpart else "")
                if from_team == tid:
                    key = "user_outgoing_players" if kind == "PLAYER" else "user_outgoing_picks"
                    counts[key] += 1
                if to_team == tid:
                    key = "user_incoming_players" if kind == "PLAYER" else "user_incoming_picks"
                    counts[key] += 1
    return counts


def _first_nonempty_str(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _is_deal_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return isinstance(payload.get("teams"), list) and isinstance(payload.get("legs"), dict)


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


def _collect_player_ids_from_deal(offer_payload: Dict[str, Any]) -> List[str]:
    deal = _extract_deal_payload(offer_payload)
    out: List[str] = []
    legs = deal.get("legs") if isinstance(deal, dict) else {}
    for assets in (legs.values() if isinstance(legs, dict) else []):
        if not isinstance(assets, list):
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            if str(asset.get("kind") or "").lower() != "player":
                continue
            pid = str(asset.get("player_id") or "").strip()
            if pid:
                out.append(pid)
    return list(dict.fromkeys(out))


def _collect_pick_ids_from_deal(offer_payload: Dict[str, Any]) -> List[str]:
    deal = _extract_deal_payload(offer_payload)
    out: List[str] = []
    legs = deal.get("legs") if isinstance(deal, dict) else {}
    for assets in (legs.values() if isinstance(legs, dict) else []):
        if not isinstance(assets, list):
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            if str(asset.get("kind") or "").lower() != "pick":
                continue
            pick_id = str(asset.get("pick_id") or "").strip()
            if pick_id:
                out.append(pick_id)
    return list(dict.fromkeys(out))


def _hydrate_player_asset_snapshots(player_ids: List[str], *, db_path: str) -> Dict[str, Dict[str, Any]]:
    pids = [str(pid).strip() for pid in (player_ids or []) if str(pid).strip()]
    if not pids:
        return {}
    placeholders = ",".join("?" for _ in pids)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT player_id, name, pos
            FROM players
            WHERE player_id IN ({placeholders})
            """,
            tuple(pids),
        ).fetchall()
    snaps: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        pid = str(row["player_id"])
        snaps[pid] = {
            "display_name": str(row["name"] or "").strip(),
            "pos": str(row["pos"] or "").strip(),
        }
    return snaps


def _hydrate_pick_asset_snapshots(pick_ids: List[str], *, db_path: str) -> Dict[str, Dict[str, Any]]:
    ids = [str(pid).strip() for pid in (pick_ids or []) if str(pid).strip()]
    if not ids:
        return {}
    wanted = set(ids)
    with LeagueRepo(db_path) as repo:
        picks = repo.get_draft_picks_map() or {}
    out: Dict[str, Dict[str, Any]] = {}
    for pick_id in wanted:
        row = picks.get(pick_id)
        if not isinstance(row, dict):
            continue
        out[pick_id] = {
            "year": row.get("year"),
            "round": row.get("round"),
            "original_team": _first_nonempty_str(row.get("original_team")),
            "owner_team": _first_nonempty_str(row.get("owner_team")),
        }
    return out


def _canonicalize_offer_assets(
    offer_payload: Dict[str, Any],
    *,
    player_snaps: Dict[str, Dict[str, Any]],
    pick_snaps: Dict[str, Dict[str, Any]],
    session_id: str,
    endpoint: str,
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    deal = _extract_deal_payload(offer_payload)
    if not deal:
        return {"teams": [], "legs": {}, "meta": {}}, []

    teams = list(deal.get("teams") or [])
    legs = deal.get("legs") if isinstance(deal.get("legs"), dict) else {}
    out_legs: Dict[str, List[Dict[str, Any]]] = {}
    violations: List[Dict[str, Any]] = []

    for from_team, assets in legs.items():
        from_key = str(from_team or "").upper()
        rows: List[Dict[str, Any]] = []
        if not isinstance(assets, list):
            out_legs[from_key] = rows
            continue
        for idx, asset in enumerate(assets):
            if not isinstance(asset, dict):
                continue
            normalized = dict(asset)
            kind = str(asset.get("kind") or "").lower().strip()
            if kind == "player":
                pid = str(asset.get("player_id") or "").strip()
                snap = player_snaps.get(pid) or {}
                normalized["player_id"] = pid
                normalized["display_name"] = _first_nonempty_str(asset.get("display_name"), snap.get("display_name"))
                normalized["pos"] = _first_nonempty_str(asset.get("pos"), snap.get("pos"))
                missing = [
                    field
                    for field in ("player_id", "display_name", "pos")
                    if not _first_nonempty_str(normalized.get(field))
                ]
                if missing:
                    violation = {
                        "path": f"legs.{from_key}[{idx}]",
                        "asset_kind": "player",
                        "asset_ref": pid or f"idx:{idx}",
                        "missing_fields": missing,
                        "session_id": session_id,
                        "endpoint": endpoint,
                    }
                    violations.append(violation)
                    emit_trade_contract_violation(violation)
            elif kind == "pick":
                pick_id = str(asset.get("pick_id") or "").strip()
                snap = pick_snaps.get(pick_id) or {}
                normalized["pick_id"] = pick_id
                normalized["year"] = asset.get("year", snap.get("year"))
                normalized["round"] = asset.get("round", snap.get("round"))
                normalized["original_team"] = _first_nonempty_str(asset.get("original_team"), snap.get("original_team"))
                normalized["owner_team"] = _first_nonempty_str(asset.get("owner_team"), snap.get("owner_team"), from_key)
                missing: List[str] = []
                if not _first_nonempty_str(normalized.get("pick_id")):
                    missing.append("pick_id")
                try:
                    normalized["year"] = int(normalized.get("year"))
                except Exception:
                    missing.append("year")
                try:
                    normalized["round"] = int(normalized.get("round"))
                except Exception:
                    missing.append("round")
                if not _first_nonempty_str(normalized.get("original_team")):
                    missing.append("original_team")
                if not _first_nonempty_str(normalized.get("owner_team")):
                    missing.append("owner_team")
                if missing:
                    violation = {
                        "path": f"legs.{from_key}[{idx}]",
                        "asset_kind": "pick",
                        "asset_ref": pick_id or f"idx:{idx}",
                        "missing_fields": missing,
                        "session_id": session_id,
                        "endpoint": endpoint,
                    }
                    violations.append(violation)
                    emit_trade_contract_violation(violation)
            rows.append(normalized)
        out_legs[from_key] = rows

    canonical_deal = {
        "teams": teams,
        "legs": out_legs,
        "meta": deal.get("meta") if isinstance(deal.get("meta"), dict) else {},
    }
    return canonical_deal, violations


def _canonicalize_offer_payload_for_response(
    offer_payload: Dict[str, Any], *, session_id: str, endpoint: str, db_path: str
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    player_ids = _collect_player_ids_from_deal(offer_payload)
    pick_ids = _collect_pick_ids_from_deal(offer_payload)
    player_snaps = _hydrate_player_asset_snapshots(player_ids, db_path=db_path)
    pick_snaps = _hydrate_pick_asset_snapshots(pick_ids, db_path=db_path)
    return _canonicalize_offer_assets(
        offer_payload,
        player_snaps=player_snaps,
        pick_snaps=pick_snaps,
        session_id=session_id,
        endpoint=endpoint,
    )


def _build_trade_negotiation_inbox_row(session: Dict[str, Any], *, today: date, db_path: str) -> Dict[str, Any]:
    phase = str(session.get("phase") or "INIT").upper()
    status = str(session.get("status") or "ACTIVE").upper()
    team_id = str(session.get("user_team_id") or "").upper()
    offer_payload = session.get("last_offer") if isinstance(session.get("last_offer"), dict) else {}
    canonical_offer, contract_violations = _canonicalize_offer_payload_for_response(
        offer_payload,
        session_id=str(session.get("session_id") or ""),
        endpoint="/api/trade/negotiation/inbox",
        db_path=db_path,
    )
    market_context = session.get("market_context") if isinstance(session.get("market_context"), dict) else {}
    offer_meta = market_context.get("offer_meta") if isinstance(market_context.get("offer_meta"), dict) else {}

    auto_end = session.get("auto_end") if isinstance(session.get("auto_end"), dict) else {}
    is_expired = str(auto_end.get("status") or "").upper() == "ENDED"

    return {
        "session_id": str(session.get("session_id") or ""),
        "user_team_id": team_id,
        "other_team_id": str(session.get("other_team_id") or "").upper(),
        "status": status,
        "phase": phase,
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
        "valid_until": session.get("valid_until"),
        "is_expired": is_expired,
        "summary": {
            "headline": f"{str(session.get('other_team_id') or '').upper()} → {team_id} 트레이드 제안",
            "offer_tone": str(offer_meta.get("offer_tone") or "").upper() or None,
            "offer_privacy": str(offer_meta.get("offer_privacy") or "PRIVATE").upper(),
            "leak_status": str(offer_meta.get("leak_status") or "NONE").upper(),
        },
        "offer": {
            "deal": canonical_offer,
            "asset_counts": _count_assets_for_user(canonical_offer, user_team_id=team_id),
        },
        "actions": {
            "can_open": status == "ACTIVE" and phase in {"INIT", "INBOX_PENDING", "NEGOTIATING", "COUNTER_PENDING"} and not is_expired,
            "can_reject": status == "ACTIVE" and phase not in {"REJECTED", "ACCEPTED"},
            "can_commit": status == "ACTIVE" and phase in {"NEGOTIATING", "COUNTER_PENDING"} and not is_expired,
        },
        "contract_violations": contract_violations,
    }


def _sort_trade_negotiation_inbox_rows(rows: List[Dict[str, Any]], *, sort_key: str) -> List[Dict[str, Any]]:
    key = _normalize_inbox_sort(sort_key, default="updated_desc")
    data = list(rows or [])

    def _ts(v: Any) -> float:
        raw = str(v or "").strip()
        if not raw:
            return float("-inf")
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except Exception:
            return float("-inf")

    if key == "created_desc":
        data.sort(
            key=lambda r: (
                _ts(r.get("created_at")),
                _ts(r.get("updated_at")),
                str(r.get("session_id") or ""),
            ),
            reverse=True,
        )
        return data

    if key == "expires_asc":
        data.sort(
            key=lambda r: (
                _iso_date(r.get("valid_until")) or date.max,
                -_ts(r.get("updated_at")),
                str(r.get("session_id") or ""),
            )
        )
        return data

    # default updated_desc
    data.sort(
        key=lambda r: (
            _ts(r.get("updated_at")),
            _ts(r.get("created_at")),
            str(r.get("session_id") or ""),
        ),
        reverse=True,
    )
    return data


@router.get("/api/trade/negotiation/inbox", response_model=TradeNegotiationInboxResponse)
async def api_trade_negotiation_inbox(
    team_id: str,
    status: str = "ACTIVE",
    phase: str = "OPEN",
    include_expired: bool = False,
    limit: int = 50,
    offset: int = 0,
    sort: str = "updated_desc",
):
    try:
        q = _parse_trade_negotiation_inbox_query(
            team_id=team_id,
            status=status,
            phase=phase,
            include_expired=include_expired,
            limit=limit,
            offset=offset,
            sort=sort,
        )

        today = state.get_current_date_as_date()
        db_path = state.get_db_path()
        sessions = state.negotiations_get() or {}

        rows: List[Dict[str, Any]] = []
        for sid, raw in (sessions.items() if isinstance(sessions, dict) else []):
            session = raw if isinstance(raw, dict) else {}
            if str(session.get("user_team_id") or "").upper() != q.team_id:
                continue

            offer_payload = session.get("last_offer")
            if not isinstance(offer_payload, dict) or not offer_payload:
                # Inbox is only for already-proposed incoming offers.
                continue

            s = dict(session)
            s.setdefault("session_id", str(sid))
            row = _build_trade_negotiation_inbox_row(s, today=today, db_path=db_path)

            if not q.include_expired and row.get("is_expired"):
                continue
            if not _status_matches_filter(status=row.get("status"), status_filter=q.status):
                continue
            if not _phase_matches_inbox_filter(phase=row.get("phase"), phase_filter=q.phase):
                continue

            rows.append(row)

        rows = _sort_trade_negotiation_inbox_rows(rows, sort_key=q.sort)
        total = len(rows)
        paged = rows[q.offset:q.offset + q.limit]
        return {
            "ok": True,
            "team_id": q.team_id,
            "filters": {
                "status": q.status,
                "phase": q.phase,
                "include_expired": q.include_expired,
                "limit": q.limit,
                "offset": q.offset,
                "sort": q.sort,
            },
            "total": total,
            "rows": paged,
        }
    except TradeError as exc:
        return _trade_error_response(exc)


@router.get("/api/trade/block")
async def api_trade_block_aggregate(
    active_only: bool = True,
    visibility: str = "PUBLIC",
    team_id: Optional[str] = None,
    limit: int = 300,
    offset: int = 0,
    sort: str = "priority_desc",
):
    try:
        q = _parse_trade_block_aggregate_query(
            active_only=active_only,
            visibility=visibility,
            team_id=team_id,
            limit=limit,
            offset=offset,
            sort=sort,
        )

        today = state.get_current_date_as_date()
        db_path = state.get_db_path()
        market = load_trade_market()

        listings = _iter_trade_block_listings(
            market=market,
            today=today,
            team_id=q.team_id,
            active_only=q.active_only,
        )
        listings = _apply_trade_block_visibility_filter(listings, visibility=q.visibility)

        pids = [str(r.get("player_id") or "") for r in listings if str(r.get("player_id") or "")]
        snapshots = _hydrate_trade_block_player_snapshots(pids, db_path=db_path)
        rows = [
            _build_trade_block_row(row, player_snapshots=snapshots)
            for row in listings
        ]
        rows = _sort_trade_block_rows(rows, sort_key=q.sort)

        total = len(rows)
        paged = rows[q.offset:q.offset + q.limit]
        return {
            "ok": True,
            "filters": {
                "active_only": q.active_only,
                "visibility": q.visibility,
                "team_id": q.team_id,
                "limit": q.limit,
                "offset": q.offset,
                "sort": q.sort,
            },
            "total": total,
            "rows": paged,
        }
    except TradeError as exc:
        return _trade_error_response(exc)


@router.post("/api/trade/block/list")
async def api_trade_block_list(req: TradeBlockListRequest):
    try:
        today = state.get_current_date_as_date()
        db_path = state.get_db_path()
        _require_player_on_team(player_id=req.player_id, team_id=req.team_id, db_path=db_path)
        market = load_trade_market()
        entry = upsert_trade_listing(
            market,
            today=today,
            player_id=req.player_id,
            team_id=req.team_id,
            listed_by="USER",
            visibility=req.visibility,
            priority=req.priority,
            reason_code=req.reason_code,
        )
        record_market_event(
            market,
            today=today,
            event_type="TRADE_BLOCK_LISTED",
            payload={"team_id": str(req.team_id).upper(), "player_id": str(req.player_id), "reason_code": str(req.reason_code or "MANUAL").upper()},
        )
        save_trade_market(market)
        return {"ok": True, "listing": entry}
    except TradeError as exc:
        return _trade_error_response(exc)


@router.post("/api/trade/block/unlist")
async def api_trade_block_unlist(req: TradeBlockUnlistRequest):
    try:
        today = state.get_current_date_as_date()
        db_path = state.get_db_path()
        _require_player_on_team(player_id=req.player_id, team_id=req.team_id, db_path=db_path)
        market = load_trade_market()
        removed = remove_trade_listing(market, player_id=req.player_id)
        if removed:
            record_market_event(
                market,
                today=today,
                event_type="TRADE_BLOCK_UNLISTED",
                payload={"team_id": str(req.team_id).upper(), "player_id": str(req.player_id), "reason_code": str(req.reason_code or "MANUAL_REMOVE").upper()},
            )
            save_trade_market(market)
        return {"ok": True, "removed": bool(removed)}
    except TradeError as exc:
        return _trade_error_response(exc)


@router.get("/api/trade/block/{team_id}")
async def api_trade_block_get(team_id: str, active_only: bool = True):
    try:
        today = state.get_current_date_as_date()
        market = load_trade_market()
        listings = list_team_trade_listings(market, team_id=team_id, active_only=bool(active_only), today=today)
        return {"ok": True, "team_id": str(team_id).upper(), "active_only": bool(active_only), "listings": listings}
    except TradeError as exc:
        return _trade_error_response(exc)


@router.post("/api/trade/submit")
async def api_trade_submit(req: TradeSubmitRequest):
    try:
        in_game_date = state.get_current_date_as_date()
        db_path = state.get_db_path()
        agreements.gc_expired_agreements(current_date=in_game_date)
        deal = canonicalize_deal(parse_deal(req.deal))
        validate_deal(deal, current_date=in_game_date)
        transaction = apply_deal_to_db(
            db_path=db_path,
            deal=deal,
            source="menu",
            deal_id=None,
            trade_date=in_game_date,
            dry_run=False,
        )
        _validate_repo_integrity(db_path)
        moved_ids: List[str] = []
        for mv in (transaction.get("player_moves") or []):
            if isinstance(mv, dict):
                pid = mv.get("player_id")
                if pid:
                    moved_ids.append(str(pid))
        _try_ui_cache_refresh_players(moved_ids, context="trade.submit")
        return {
            "ok": True,
            "deal": serialize_deal(deal),
            "transaction": transaction,
        }
    except TradeError as exc:
        return _trade_error_response(exc)


@router.post("/api/trade/submit-committed")
async def api_trade_submit_committed(req: TradeSubmitCommittedRequest):
    try:
        idem_key = _normalize_idempotency_key(getattr(req, "idempotency_key", None))
        scope = "submit-committed"
        payload_hash = _hash_payload({"deal_id": req.deal_id})
        replay = _idempotency_replay_guard(
            scope=scope,
            session_id=str(req.deal_id),
            team_id="",
            key=idem_key,
            payload_hash=payload_hash,
        )
        if isinstance(replay, dict):
            return replay

        in_game_date = state.get_current_date_as_date()
        db_path = state.get_db_path()
        agreements.gc_expired_agreements(current_date=in_game_date)
        deal = agreements.verify_committed_deal(req.deal_id, current_date=in_game_date)
        validate_deal(
            deal,
            current_date=in_game_date,
        )
        transaction = apply_deal_to_db(
            db_path=db_path,
            deal=deal,
            source="negotiation",
            deal_id=req.deal_id,
            trade_date=in_game_date,
            dry_run=False,
        )
        _validate_repo_integrity(db_path)
        agreements.mark_executed(req.deal_id)
        moved_ids: List[str] = []
        for mv in (transaction.get("player_moves") or []):
            if isinstance(mv, dict):
                pid = mv.get("player_id")
                if pid:
                    moved_ids.append(str(pid))
        _try_ui_cache_refresh_players(moved_ids, context="trade.submit_committed")
        response = {
            "ok": True,
            "deal_id": req.deal_id,
            "transaction": transaction,
            "idempotent": False,
        }
        if idem_key:
            response["idempotency_key"] = idem_key
            _store_idempotency_response(
                scope=scope,
                session_id=str(req.deal_id),
                team_id="",
                key=idem_key,
                payload_hash=payload_hash,
                response_payload=response,
            )
        return response
    except TradeError as exc:
        return _trade_error_response(exc)


@router.post("/api/trade/negotiation/start")
async def api_trade_negotiation_start(req: TradeNegotiationStartRequest):
    try:
        idem_key = _normalize_idempotency_key(getattr(req, "idempotency_key", None))
        user_team_id = str(req.user_team_id or "").upper().strip()
        other_team_id = str(req.other_team_id or "").upper().strip()
        if not user_team_id or not other_team_id:
            raise TradeError("NEGOTIATION_BAD_QUERY", "user_team_id and other_team_id are required")

        scope = "negotiation-start"
        synthetic_session_id = f"{user_team_id}:{other_team_id}"
        payload_hash = _hash_payload({
            "user_team_id": user_team_id,
            "other_team_id": other_team_id,
            "default_offer_privacy": getattr(req, "default_offer_privacy", "PRIVATE"),
        })
        replay = _idempotency_replay_guard(
            scope=scope,
            session_id=synthetic_session_id,
            team_id=user_team_id,
            key=idem_key,
            payload_hash=payload_hash,
        )
        if isinstance(replay, dict):
            return replay

        session = negotiation_store.create_session(
            user_team_id=user_team_id, other_team_id=other_team_id
        )
        # AI-driven auto-end policy is now the primary mechanism for stale sessions.
        negotiation_store.touch_ai_action(session["session_id"])
        # default privacy metadata (backward-compatible)
        default_privacy = _normalize_offer_privacy(getattr(req, "default_offer_privacy", None), default="PRIVATE")
        negotiation_store.set_market_context_offer_meta(
            session["session_id"],
            {"offer_privacy": default_privacy, "leak_status": "NONE"},
        )
        # Keep response consistent with stored session
        session = negotiation_store.get_session(session["session_id"])
        session.setdefault("market_context", {})
        if isinstance(session["market_context"], dict):
            session["market_context"]["offer_meta"] = {"offer_privacy": default_privacy, "leak_status": "NONE"}

        response = {
            "ok": True,
            "session": session,
            "idempotent": False,
        }
        if idem_key:
            response["idempotency_key"] = idem_key
            _store_idempotency_response(
                scope=scope,
                session_id=synthetic_session_id,
                team_id=user_team_id,
                key=idem_key,
                payload_hash=payload_hash,
                response_payload=response,
            )
        return response
    except TradeError as exc:
        return _trade_error_response(exc)


@router.post("/api/trade/negotiation/open")
async def api_trade_negotiation_open(req: TradeNegotiationOpenRequest):
    try:
        idem_key = _normalize_idempotency_key(getattr(req, "idempotency_key", None))
        team_id = str(req.team_id or "").upper().strip()
        if not team_id:
            raise TradeError("NEGOTIATION_BAD_QUERY", "team_id is required")

        scope = "negotiation-open"
        payload_hash = _hash_payload({"session_id": req.session_id, "team_id": team_id})
        replay = _idempotency_replay_guard(
            scope=scope,
            session_id=str(req.session_id),
            team_id=team_id,
            key=idem_key,
            payload_hash=payload_hash,
        )
        if isinstance(replay, dict):
            return replay

        session = negotiation_store.get_session(req.session_id)
        owner_team_id = str(session.get("user_team_id") or "").upper().strip()
        if not owner_team_id or owner_team_id != team_id:
            raise TradeError(
                "NEGOTIATION_NOT_AUTHORIZED",
                "Only the recipient team can open this negotiation",
                {"session_id": str(req.session_id), "team_id": team_id, "owner_team_id": owner_team_id or None},
            )

        status = str(session.get("status") or "ACTIVE").upper()
        phase = str(session.get("phase") or "INIT").upper()
        if status != "ACTIVE" or phase == "REJECTED":
            raise TradeError(
                "NEGOTIATION_NOT_ACTIVE",
                "Negotiation session is not active",
                {"session_id": str(req.session_id), "status": status, "phase": phase},
            )

        today = state.get_current_date_as_date()
        auto_eval = evaluate_and_maybe_end(
            req.session_id,
            today=today,
            seed_context={
                "seed_salt": str(getattr(OrchestrationConfig(), "seed_salt", "trade_orchestration_v2")),
                "deadline_pressure": 0.0,
            },
        )
        if bool(auto_eval.get("ended")):
            raise TradeError(
                "NEGOTIATION_ENDED_BY_AI",
                "Negotiation session has been ended by AI",
                {
                    "session_id": str(req.session_id),
                    "today": today.isoformat(),
                    "reason_code": auto_eval.get("reason_code"),
                    "probability": auto_eval.get("probability"),
                    "roll": auto_eval.get("roll"),
                },
            )

        result = negotiation_store.open_inbox_session(req.session_id, idempotency_key=idem_key)
        updated = result.get("session") if isinstance(result, dict) else None
        if not isinstance(updated, dict):
            updated = negotiation_store.get_session(req.session_id)

        db_path = state.get_db_path()
        contract_violations: List[Dict[str, Any]] = []
        if isinstance(updated.get("last_offer"), dict):
            canonical_offer, violations = _canonicalize_offer_payload_for_response(
                updated["last_offer"],
                session_id=str(updated.get("session_id") or req.session_id),
                endpoint="/api/trade/negotiation/open:last_offer",
                db_path=db_path,
            )
            updated["last_offer"] = canonical_offer
            contract_violations.extend(violations)
        if isinstance(updated.get("draft_deal"), dict):
            canonical_draft, violations = _canonicalize_offer_payload_for_response(
                updated["draft_deal"],
                session_id=str(updated.get("session_id") or req.session_id),
                endpoint="/api/trade/negotiation/open:draft_deal",
                db_path=db_path,
            )
            updated["draft_deal"] = canonical_draft
            contract_violations.extend(violations)

        response = {
            "ok": True,
            "session": updated,
            "opened": True,
            "idempotent": bool((result or {}).get("idempotent")) if isinstance(result, dict) else False,
            "contract_violations": contract_violations,
        }
        if idem_key:
            response["idempotency_key"] = idem_key
            _store_idempotency_response(
                scope=scope,
                session_id=str(req.session_id),
                team_id=team_id,
                key=idem_key,
                payload_hash=payload_hash,
                response_payload=response,
            )
        return response
    except TradeError as exc:
        return _trade_error_response(exc)


@router.post("/api/trade/negotiation/reject")
async def api_trade_negotiation_reject(req: TradeNegotiationRejectRequest):
    try:
        idem_key = _normalize_idempotency_key(getattr(req, "idempotency_key", None))
        team_id = str(req.team_id or "").upper().strip()
        if not team_id:
            raise TradeError("NEGOTIATION_BAD_QUERY", "team_id is required")

        scope = "negotiation-reject"
        payload_hash = _hash_payload({"session_id": req.session_id, "team_id": team_id, "reason": req.reason})
        replay = _idempotency_replay_guard(
            scope=scope,
            session_id=str(req.session_id),
            team_id=team_id,
            key=idem_key,
            payload_hash=payload_hash,
        )
        if isinstance(replay, dict):
            return replay

        session = negotiation_store.get_session(req.session_id)
        owner_team_id = str(session.get("user_team_id") or "").upper().strip()
        if not owner_team_id or owner_team_id != team_id:
            raise TradeError(
                "NEGOTIATION_NOT_AUTHORIZED",
                "Only the recipient team can reject this negotiation",
                {"session_id": str(req.session_id), "team_id": team_id, "owner_team_id": owner_team_id or None},
            )

        result = negotiation_store.close_as_rejected(req.session_id, reason=req.reason, idempotency_key=idem_key)
        updated = result.get("session") if isinstance(result, dict) else None
        if not isinstance(updated, dict):
            updated = negotiation_store.get_session(req.session_id)

        response = {
            "ok": True,
            "session_id": str(updated.get("session_id") or req.session_id),
            "status": str(updated.get("status") or "CLOSED").upper(),
            "phase": str(updated.get("phase") or "REJECTED").upper(),
            "rejected": True,
            "idempotent": bool((result or {}).get("idempotent")) if isinstance(result, dict) else False,
        }
        if idem_key:
            response["idempotency_key"] = idem_key
            _store_idempotency_response(
                scope=scope,
                session_id=str(req.session_id),
                team_id=team_id,
                key=idem_key,
                payload_hash=payload_hash,
                response_payload=response,
            )
        return response
    except TradeError as exc:
        return _trade_error_response(exc)


@router.post("/api/trade/negotiation/commit")
async def api_trade_negotiation_commit(req: TradeNegotiationCommitRequest):
    try:
        idem_key = _normalize_idempotency_key(getattr(req, "idempotency_key", None))
        scope = "negotiation-commit"
        payload_hash = _hash_payload({
            "session_id": req.session_id,
            "deal": req.deal,
            "offer_privacy": getattr(req, "offer_privacy", None),
            "expose_to_media": bool(getattr(req, "expose_to_media", False)),
        })
        in_game_date = state.get_current_date_as_date()
        db_path = state.get_db_path()
        session = negotiation_store.get_session(req.session_id)
        owner_team_id = str(session.get("user_team_id") or "").upper().strip()

        replay = _idempotency_replay_guard(
            scope=scope,
            session_id=str(req.session_id),
            team_id=owner_team_id,
            key=idem_key,
            payload_hash=payload_hash,
        )
        if isinstance(replay, dict):
            return replay

        def _finalize_commit_response(payload: Dict[str, Any]) -> Dict[str, Any]:
            out = dict(payload or {})
            out.setdefault("idempotent", False)
            if idem_key:
                out["idempotency_key"] = idem_key
                _store_idempotency_response(
                    scope=scope,
                    session_id=str(req.session_id),
                    team_id=owner_team_id,
                    key=idem_key,
                    payload_hash=payload_hash,
                    response_payload=out,
                )
            return out

        idempotent_payload = _ensure_session_ready_for_commit(
            session,
            session_id=str(req.session_id),
            today=in_game_date,
        )
        if isinstance(idempotent_payload, dict):
            return _finalize_commit_response(idempotent_payload)

        deal = canonicalize_deal(parse_deal(req.deal))
        team_ids = {session["user_team_id"].upper(), session["other_team_id"].upper()}
        if set(deal.teams) != team_ids or len(deal.teams) != 2:
            raise TradeError(
                "DEAL_INVALIDATED",
                "Deal teams must match negotiation session",
                {"session_id": req.session_id, "teams": deal.teams},
            )
        # Hot path: negotiation UI calls this endpoint repeatedly.
        # DB integrity is already guaranteed at startup and after any write APIs.
        # Avoid running full repo integrity check on every offer update.
        validate_deal(deal, current_date=in_game_date, db_path=db_path, integrity_check=False)
        
        # Always persist the latest valid offer payload
        deal_serialized = serialize_deal(deal)
        negotiation_store.set_draft_deal(req.session_id, deal_serialized)

        offer_privacy = _normalize_offer_privacy(getattr(req, "offer_privacy", None), default="PRIVATE")
        expose_to_media = bool(getattr(req, "expose_to_media", False))
        leak_status = "NONE"
        cfg = OrchestrationConfig()

        # Local imports to keep integration flexible.
        from trades.valuation.service import evaluate_deal_for_team as eval_service  # type: ignore
        from trades.valuation.types import (
            to_jsonable,
            DealVerdict,
            DealDecision,
            DecisionReason,
        )  # type: ignore
        from trades.generation.dealgen.dedupe import dedupe_hash  # type: ignore

        # ------------------------------------------------------------------
        # Fast path: if the user submits the exact last AI counter-offer, accept
        # immediately.
        # - Prevents the frustrating UX where the AI "rejects its own counter".
        # - Only active while the session is in COUNTER_PENDING phase.
        # ------------------------------------------------------------------
        try:
            phase = str(session.get("phase") or "").upper()
            last_counter = session.get("last_counter")
            expected_hash = last_counter.get("counter_hash") if isinstance(last_counter, dict) else None
            if phase == "COUNTER_PENDING" and isinstance(expected_hash, str) and expected_hash.strip():
                if dedupe_hash(deal) == expected_hash.strip():
                    committed = agreements.create_committed_deal(
                        deal,
                        valid_days=22,
                        current_date=in_game_date,
                        validate=False,   # already validated above
                        db_path=db_path,
                    )
                    negotiation_store.mark_committed_and_close(
                        req.session_id,
                        deal_id=str(committed["deal_id"]),
                        expires_at=committed.get("expires_at"),
                        idempotency_key=idem_key,
                    )

                    fast_decision = DealDecision(
                        verdict=DealVerdict.ACCEPT,
                        required_surplus=0.0,
                        overpay_allowed=0.0,
                        confidence=1.0,
                        reasons=(
                            DecisionReason(
                                code="COUNTER_ACCEPTED",
                                message="Accepted last counter offer",
                            ),
                        ),
                        counter=None,
                        meta={"fast_accept": True},
                    )

                    # Preserve any cached evaluation summary if present
                    fast_eval: Dict[str, Any] = {}
                    if isinstance(last_counter, dict):
                        ev = last_counter.get("ai_evaluation")
                        if isinstance(ev, dict):
                            fast_eval = dict(ev)

                    # Fast-accept path: still apply grievance side effects once (PUBLIC only).
                    try:
                        if str(offer_privacy).upper() == "PUBLIC":
                            proposer_tid = str(session["user_team_id"]).upper()
                            apply_trade_offer_grievances(
                                db_path=db_path,
                                season_year=_get_season_year_from_state(fallback_year=in_game_date.year),
                                now_date_iso=in_game_date.isoformat(),
                                proposer_team_id=proposer_tid,
                                outgoing_player_ids=_extract_outgoing_player_ids_for_team(deal_serialized, from_team_id=proposer_tid),
                                incoming_player_ids=_extract_incoming_player_ids_for_team(deal_serialized, to_team_id=proposer_tid),
                                trigger_source="PUBLIC_OFFER",
                                session_id=str(req.session_id),
                                source_path="API_NEGOTIATION_FAST_ACCEPT",
                            )
                    except Exception:
                        pass

                    return _finalize_commit_response({
                        "ok": True,
                        "accepted": True,
                        "fast_accept": True,
                        "deal_id": committed["deal_id"],
                        "expires_at": committed["expires_at"],
                        "deal": serialize_deal(deal),
                        "ai_verdict": to_jsonable(fast_decision.verdict),
                        "ai_decision": to_jsonable(fast_decision),
                        "ai_evaluation": fast_eval,
                        "offer_privacy": offer_privacy,
                        "leak_status": leak_status,
                    })
        except Exception:
            # Fast-accept should never crash the commit flow.
            pass


        # ------------------------------------------------------------------
        # AI evaluation (other team perspective)
        # NOTE:
        # - legality is already checked by validate_deal above
        # - valuation service will build DecisionContext internally (team_situation + gm profile)
        # ------------------------------------------------------------------
        other_team_id = session["other_team_id"].upper()

        decision, evaluation = eval_service(
            deal=deal,
            team_id=other_team_id,
            current_date=in_game_date,
            db_path=db_path,
            include_breakdown=False,   # keep negotiation response light
            include_package_effects=True,
            allow_counter=True,
            validate=False,            # already validated above
        )

        eval_summary = {
            "team_id": other_team_id,
            "incoming_total": float(evaluation.incoming_total),
            "outgoing_total": float(evaluation.outgoing_total),
            "net_surplus": float(evaluation.net_surplus),
            "surplus_ratio": float(evaluation.surplus_ratio),
        }

        # Record the latest offer evaluation in-session (do NOT overwrite last_counter).
        # - last_counter is reserved for the actual counter deal payload (for fast-accept).
        negotiation_store.set_last_offer(
            req.session_id,
            {
                "offer": deal_serialized,
                "ai_verdict": to_jsonable(decision.verdict),
                "ai_decision": to_jsonable(decision),
                "ai_evaluation": eval_summary,
                "offer_privacy": offer_privacy,
                "leak_status": leak_status,
            },
        )

        # Offer privacy effects (market-level, never mutate DB SSOT)
        leaked_player_ids: List[str] = []
        try:
            market = load_trade_market()
            today = in_game_date

            # PRIVATE: never auto-list. If user explicitly exposes, emit leak + relationship hit.
            if offer_privacy == "PRIVATE" and expose_to_media:
                mem = load_trade_memory()
                pair_cd = int(getattr(cfg, "private_leak_pair_cooldown_days", 7) or 0)
                allow_leak = True
                if pair_cd > 0:
                    last_iso = (
                        get_rel_meta_date_iso(
                            mem,
                            team_a=str(session["user_team_id"]).upper(),
                            team_b=str(session["other_team_id"]).upper(),
                            key="last_private_offer_leak_at",
                        )
                        or get_rel_meta_date_iso(
                            mem,
                            team_a=str(session["other_team_id"]).upper(),
                            team_b=str(session["user_team_id"]).upper(),
                            key="last_private_offer_leak_at",
                        )
                    )
                    if isinstance(last_iso, str):
                        try:
                            d0 = date.fromisoformat(last_iso[:10])
                            if (today - d0).days < max(0, pair_cd):
                                allow_leak = False
                        except Exception:
                            pass

                if allow_leak:
                    leak_status = "LEAKED_BY_USER"
                    leaked_player_ids = _extract_outgoing_player_ids_for_team(deal_serialized, from_team_id=session["user_team_id"])
                    record_market_event(
                        market,
                        today=today,
                        event_type="PRIVATE_OFFER_LEAKED",
                        payload={
                            "session_id": req.session_id,
                            "leaked_by": "USER",
                            "user_team_id": str(session["user_team_id"]).upper(),
                            "other_team_id": str(session["other_team_id"]).upper(),
                            "player_ids": leaked_player_ids,
                        },
                    )

                    trust_penalty = _clamp_int(
                        getattr(cfg, "user_leak_trust_penalty", 35),
                        lo=0,
                        hi=100,
                        default=35,
                    )
                    broken_inc = _clamp_int(
                        getattr(cfg, "user_leak_promises_broken_inc", 1),
                        lo=0,
                        hi=10,
                        default=1,
                    )
                    rel = session.get("relationship") if isinstance(session.get("relationship"), dict) else {}
                    trust_now = _clamp_int(rel.get("trust", 0), lo=-100, hi=100, default=0)
                    broken_now = _clamp_int(rel.get("promises_broken", 0), lo=0, hi=100, default=0)
                    negotiation_store.set_relationship(
                        req.session_id,
                        {
                            "trust": _clamp_int(trust_now - trust_penalty, lo=-100, hi=100, default=-trust_penalty),
                            "promises_broken": _clamp_int(broken_now + broken_inc, lo=0, hi=100, default=broken_inc),
                        },
                    )
                    bump_relationship(
                        mem,
                        team_a=str(session["user_team_id"]).upper(),
                        team_b=str(session["other_team_id"]).upper(),
                        today=today,
                        patch={
                            "counts": {
                                "private_offer_leaked_by_user": 1,
                                "trust_break_events": 1,
                                "promises_broken_events": broken_inc,
                            },
                            "meta": {
                                "last_private_offer_leak_at": today.isoformat(),
                                "last_private_offer_leak_by": "USER",
                                "last_private_offer_leak_session_id": str(req.session_id),
                            },
                        },
                    )
                    save_trade_memory(mem)
                else:
                    record_market_event(
                        market,
                        today=today,
                        event_type="PRIVATE_OFFER_LEAK_SUPPRESSED",
                        payload={
                            "session_id": req.session_id,
                            "user_team_id": str(session["user_team_id"]).upper(),
                            "other_team_id": str(session["other_team_id"]).upper(),
                            "reason": "PAIR_COOLDOWN",
                        },
                    )
            save_trade_market(market)

            # persist offer_meta snapshot
            negotiation_store.set_market_context_offer_meta(
                req.session_id,
                {
                    "offer_privacy": offer_privacy,
                    "leak_status": leak_status,
                    "leak_at": today.isoformat() if leak_status != "NONE" else None,
                    "private_offer_exposed_player_ids": leaked_player_ids,
                    "publicized_from_leak": bool(
                        str(leak_status).upper() != "NONE" and is_private_leak_publicized(market, session_id=str(req.session_id))
                    ),
                },
            )
        except Exception:
            pass

        # Apply agency grievance effects exactly once per commit call (best-effort).
        try:
            proposer_tid = str(session["user_team_id"]).upper()
            outgoing_ids = _extract_outgoing_player_ids_for_team(deal_serialized, from_team_id=proposer_tid)
            incoming_ids = _extract_incoming_player_ids_for_team(deal_serialized, to_team_id=proposer_tid)
            grievance_source = "PUBLIC_OFFER"
            if str(leak_status).upper() != "NONE":
                grievance_source = "PRIVATE_OFFER_LEAKED"
            elif str(offer_privacy).upper() != "PUBLIC":
                grievance_source = "NONE"

            if grievance_source != "NONE":
                apply_trade_offer_grievances(
                    db_path=db_path,
                    season_year=_get_season_year_from_state(fallback_year=in_game_date.year),
                    now_date_iso=in_game_date.isoformat(),
                    proposer_team_id=proposer_tid,
                    outgoing_player_ids=outgoing_ids,
                    incoming_player_ids=incoming_ids,
                    trigger_source=grievance_source,
                    session_id=str(req.session_id),
                    source_path="API_NEGOTIATION_COMMIT",
                )
        except Exception:
            # Never block trade negotiation flow by agency side effects.
            pass

        # Decide action
        verdict = decision.verdict

        if verdict == DealVerdict.ACCEPT:
            committed = agreements.create_committed_deal(
                deal,
                valid_days=22,
                current_date=in_game_date,
                validate=False,   # already validated above
                db_path=db_path,  # keep hash/locking based on the same db snapshot
            )
            negotiation_store.mark_committed_and_close(
                req.session_id,
                deal_id=str(committed["deal_id"]),
                expires_at=committed.get("expires_at"),
                idempotency_key=idem_key,
            )
            return _finalize_commit_response({
                "ok": True,
                "accepted": True,
                "deal_id": committed["deal_id"],
                "expires_at": committed["expires_at"],
                "deal": serialize_deal(deal),
                "ai_verdict": to_jsonable(decision.verdict),
                "ai_decision": to_jsonable(decision),
                "ai_evaluation": eval_summary,
                "offer_privacy": offer_privacy,
                "leak_status": leak_status,
            })

        # ------------------------------------------------------------------
        # COUNTER: build an actual counter proposal (NBA-like minimal edits)
        # ------------------------------------------------------------------
        if verdict == DealVerdict.COUNTER:
            counter_prop = None
            try:
                from trades.counter_offer.init import build_counter_offer  # type: ignore

                counter_prop = build_counter_offer(
                    offer=deal,
                    user_team_id=session["user_team_id"],
                    other_team_id=session["other_team_id"],
                    current_date=in_game_date,
                    db_path=db_path,
                    session=session,
                )
            except Exception:
                counter_prop = None

            if counter_prop is not None and getattr(counter_prop, "deal", None) is not None:
                # Attach the generated counter proposal to the decision (SSOT).
                decision = DealDecision(
                    verdict=decision.verdict,
                    required_surplus=float(decision.required_surplus),
                    overpay_allowed=float(decision.overpay_allowed),
                    confidence=float(decision.confidence),
                    reasons=decision.reasons,
                    counter=counter_prop,
                    meta=dict(decision.meta or {}),
                )

                # Persist counter offer in-session (for UI + fast-accept).
                try:
                    counter_hash = counter_prop.meta.get("counter_hash") if isinstance(counter_prop.meta, dict) else None
                    deal_payload = None
                    if isinstance(counter_prop.meta, dict):
                        deal_payload = counter_prop.meta.get("deal_serialized")
                    if not isinstance(deal_payload, dict):
                        # Defensive fallback
                        deal_payload = serialize_deal(counter_prop.deal)

                    negotiation_store.set_last_counter(
                        req.session_id,
                        {
                            "counter_hash": counter_hash,
                            "counter_deal": deal_payload,
                            "strategy": counter_prop.meta.get("strategy") if isinstance(counter_prop.meta, dict) else None,
                            "diff": counter_prop.meta.get("diff") if isinstance(counter_prop.meta, dict) else None,
                            "message": counter_prop.meta.get("message") if isinstance(counter_prop.meta, dict) else None,
                            "generated_at": in_game_date.isoformat(),
                            "base_hash": counter_prop.meta.get("base_hash") if isinstance(counter_prop.meta, dict) else None,
                            "ai_evaluation": eval_summary,
                    "offer_privacy": offer_privacy,
                    "leak_status": leak_status,
                        },
                    )
                except Exception:
                    pass

                # Push a GM-style message for the counter.
                try:
                    msg = ""
                    if isinstance(counter_prop.meta, dict):
                        msg = str(counter_prop.meta.get("message") or "")
                    msg = msg.strip() if msg else ""
                    if not msg:
                        msg = f"[{other_team_id}] COUNTER"
                    negotiation_store.append_message(req.session_id, speaker="OTHER_GM", text=msg)
                    negotiation_store.set_phase(req.session_id, "COUNTER_PENDING")
                except Exception:
                    pass

                # Response: counter details are embedded in ai_decision.counter (SSOT).

                return _finalize_commit_response({
                    "ok": True,
                    "accepted": False,
                    "counter_unimplemented": False,
                    "deal": serialize_deal(deal),
                    "ai_verdict": to_jsonable(decision.verdict),
                    "ai_decision": to_jsonable(decision),
                    "ai_evaluation": eval_summary,
                    "offer_privacy": offer_privacy,
                    "leak_status": leak_status,
                })

            # If we couldn't build a legal/acceptable counter, fall back conservatively to REJECT.
            decision = DealDecision(
                verdict=DealVerdict.REJECT,
                required_surplus=float(decision.required_surplus),
                overpay_allowed=float(decision.overpay_allowed),
                confidence=float(decision.confidence),
                reasons=tuple(decision.reasons)
                + (
                    DecisionReason(
                        code="COUNTER_BUILD_FAILED",
                        message="Could not generate a legal counter offer",
                    ),
                ),
                counter=None,
                meta=dict(decision.meta or {}),
            )
            verdict = DealVerdict.REJECT


        # Build a short reason string for UI
        try:
            reason_lines = []
            for r in (decision.reasons or [])[:4]:
                if isinstance(r, dict):
                    msg = r.get("message") or r.get("code") or ""
                else:
                    msg = getattr(r, "message", None) or getattr(r, "code", None) or ""
                if msg:
                    reason_lines.append(str(msg))
            reason_text = " | ".join(reason_lines) if reason_lines else "AI rejected the offer."
        except Exception:
            reason_text = "AI rejected the offer."

        # Record rejection in session (message + phase)
        try:
            negotiation_store.append_message(
                req.session_id,
                speaker="OTHER_GM",
                text=f"[{other_team_id}] {verdict}: {reason_text}",
            )
            negotiation_store.set_phase(req.session_id, "REJECTED")
        except Exception:
            pass

        return _finalize_commit_response({
            "ok": True,
            "accepted": False,
            "counter_unimplemented": False,
            "deal": serialize_deal(deal),
            "ai_verdict": to_jsonable(decision.verdict),
            "ai_decision": to_jsonable(decision),
            "ai_evaluation": eval_summary,
            "offer_privacy": offer_privacy,
            "leak_status": leak_status,
        })
    except TradeError as exc:
        return _trade_error_response(exc)


@router.post("/api/trade/evaluate")
async def api_trade_evaluate(req: TradeEvaluateRequest):
    """
    Debug endpoint: evaluate a proposed deal from a single team's perspective.
    Flow:
      deal = canonicalize_deal(parse_deal(req.deal))
      validate_deal(deal, current_date=in_game_date)
      trades.valuation.service.evaluate_deal_for_team(...)
      return decision + breakdown
    """
    try:
        in_game_date = state.get_current_date_as_date()
        db_path = state.get_db_path()

        deal = canonicalize_deal(parse_deal(req.deal))
        # Hot path: debug / UI-driven repeated calls.
        # Integrity is checked at startup and after any DB writes.
        validate_deal(deal, current_date=in_game_date, db_path=db_path, integrity_check=False)

        # Local import to avoid hard dependency during incremental integration.
        from trades.valuation.service import evaluate_deal_for_team as eval_service  # type: ignore
        from trades.valuation.types import to_jsonable  # type: ignore

        decision, evaluation = eval_service(
            deal=deal,
            team_id=req.team_id,
            current_date=in_game_date,
            db_path=db_path,
            include_breakdown=bool(req.include_breakdown),
            # We already validated above; avoid duplicate validate_deal in service.
            validate=False,
        )

        debug_context = _build_trade_evaluate_debug_context(
            team_id=str(req.team_id),
            deal=deal,
            db_path=db_path,
            in_game_date=in_game_date,
        )

        return {
            "ok": True,
            "team_id": str(req.team_id).upper(),
            "deal": serialize_deal(deal),
            "decision": to_jsonable(decision),
            "evaluation": to_jsonable(evaluation),
            "debug_context": debug_context,
        }
    except TradeError as exc:
        return _trade_error_response(exc)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Trade evaluation failed: {exc}")


# -------------------------------------------------------------------------
# 로스터 요약 API (LLM 컨텍스트용)
