from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import sqlite3

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
    mark_private_leak_publicized,
)
from app.schemas.trades import (
    TradeBlockListRequest,
    TradeBlockAggregateQuery,
    TradeBlockUnlistRequest,
    TradeEvaluateRequest,
    TradeNegotiationCommitRequest,
    TradeNegotiationStartRequest,
    TradeSubmitCommittedRequest,
    TradeSubmitRequest,
)
from app.services.cache_facade import _try_ui_cache_refresh_players
from app.services.contract_facade import _validate_repo_integrity
from app.services.trade_facade import _trade_error_response
from agency.service import apply_trade_offer_grievances

router = APIRouter()


def _normalize_offer_privacy(raw: Any, *, default: str = "PRIVATE") -> str:
    v = str(raw or default).upper()
    return "PUBLIC" if v == "PUBLIC" else "PRIVATE"


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


def _first_number(*values: Any, default: float = 0.0) -> float:
    for v in values:
        try:
            n = float(v)
        except Exception:
            continue
        if n == n:  # NaN guard
            return n
    return float(default)


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


def _extract_trade_block_box_stats(
    *,
    player_id: str,
    workflow_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
    ws = workflow_state if isinstance(workflow_state, dict) else (state.export_workflow_state() or {})
    player_stats = ws.get("player_stats") if isinstance(ws.get("player_stats"), dict) else {}
    raw = player_stats.get(str(player_id)) if isinstance(player_stats, dict) else {}
    totals = raw.get("totals") if isinstance(raw, dict) and isinstance(raw.get("totals"), dict) else {}
    stats = raw.get("stats") if isinstance(raw, dict) and isinstance(raw.get("stats"), dict) else {}

    return {
        "pts": _first_number(raw.get("pts"), totals.get("PTS"), stats.get("pts"), default=0),
        "ast": _first_number(raw.get("ast"), totals.get("AST"), stats.get("ast"), default=0),
        "reb": _first_number(raw.get("reb"), totals.get("REB"), stats.get("reb"), default=0),
        "three_pm": _first_number(
            raw.get("three_pm"),
            totals.get("3PM"),
            stats.get("three_pm"),
            stats.get("fg3m"),
            default=0,
        ),
    }


def _build_trade_block_row(
    listing: Dict[str, Any],
    *,
    player_snapshots: Dict[str, Dict[str, Any]],
    workflow_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pid = str(listing.get("player_id") or "")
    snap = player_snapshots.get(pid) or {}
    box = _extract_trade_block_box_stats(player_id=pid, workflow_state=workflow_state)
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
        "pts": _first_number(box.get("pts"), default=0),
        "ast": _first_number(box.get("ast"), default=0),
        "reb": _first_number(box.get("reb"), default=0),
        "three_pm": _first_number(box.get("three_pm"), default=0),
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
        workflow_state = state.export_workflow_state() or {}

        rows = [
            _build_trade_block_row(row, player_snapshots=snapshots, workflow_state=workflow_state)
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
        in_game_date = state.get_current_date_as_date()
        db_path = state.get_db_path()
        agreements.gc_expired_agreements(current_date=in_game_date)
        deal = agreements.verify_committed_deal(req.deal_id, current_date=in_game_date)
        validate_deal(
            deal,
            current_date=in_game_date,
            allow_locked_by_deal_id=req.deal_id,
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
        return {"ok": True, "deal_id": req.deal_id, "transaction": transaction}
    except TradeError as exc:
        return _trade_error_response(exc)


@router.post("/api/trade/negotiation/start")
async def api_trade_negotiation_start(req: TradeNegotiationStartRequest):
    try:
        in_game_date = state.get_current_date_as_date()
        session = negotiation_store.create_session(
            user_team_id=req.user_team_id, other_team_id=req.other_team_id
        )
        # Ensure sessions naturally expire even if the user ignores them,
        # so they don't permanently consume the active-session cap.
        valid_until = (in_game_date + timedelta(days=2)).isoformat()
        negotiation_store.set_valid_until(session["session_id"], valid_until)
        # default privacy metadata (backward-compatible)
        default_privacy = _normalize_offer_privacy(getattr(req, "default_offer_privacy", None), default="PRIVATE")
        negotiation_store.set_market_context_offer_meta(
            session["session_id"],
            {"offer_privacy": default_privacy, "leak_status": "NONE"},
        )
        # Keep response consistent with stored session
        session["valid_until"] = valid_until
        session.setdefault("market_context", {})
        if isinstance(session["market_context"], dict):
            session["market_context"]["offer_meta"] = {"offer_privacy": default_privacy, "leak_status": "NONE"}
        return {"ok": True, "session": session}
    except TradeError as exc:
        return _trade_error_response(exc)


@router.post("/api/trade/negotiation/commit")
async def api_trade_negotiation_commit(req: TradeNegotiationCommitRequest):
    try:
        in_game_date = state.get_current_date_as_date()
        db_path = state.get_db_path()
        session = negotiation_store.get_session(req.session_id)
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
                        valid_days=2,
                        current_date=in_game_date,
                        validate=False,   # already validated above
                        db_path=db_path,
                    )
                    negotiation_store.set_committed(req.session_id, committed["deal_id"])
                    negotiation_store.set_status(req.session_id, "CLOSED")
                    negotiation_store.set_phase(req.session_id, "ACCEPTED")
                    negotiation_store.set_valid_until(req.session_id, committed["expires_at"])

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

                    return {
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
                    }
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
        try:
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
        except Exception:
            pass

        # Offer privacy effects (market-level, never mutate DB SSOT)
        leaked_player_ids: List[str] = []
        try:
            market = load_trade_market()
            today = in_game_date

            # PUBLIC: proposer(=user team) outgoing players auto-list
            if offer_privacy == "PUBLIC":
                outgoing = _extract_outgoing_player_ids_for_team(deal_serialized, from_team_id=session["user_team_id"])
                ttl = int(getattr(cfg, "trade_block_auto_list_days_public_offer", 10) or 10)
                if ttl <= 0:
                    ttl = 1
                for pid in outgoing:
                    upsert_trade_listing(
                        market,
                        today=today,
                        player_id=pid,
                        team_id=session["user_team_id"],
                        listed_by="AUTO_PUBLIC_OFFER",
                        visibility="PUBLIC",
                        priority=0.6,
                        reason_code="PUBLIC_OFFER",
                        expires_on=(today + timedelta(days=ttl+1)).isoformat(),
                        source={"session_id": req.session_id, "offer_privacy": "PUBLIC"},
                    )
                    leaked_player_ids.append(pid)
                if outgoing:
                    record_market_event(
                        market,
                        today=today,
                        event_type="TRADE_BLOCK_AUTO_LISTED_FROM_PUBLIC_OFFER",
                        payload={"session_id": req.session_id, "team_id": str(session["user_team_id"]).upper(), "player_ids": outgoing},
                    )

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

                    # Leak -> public conversion (idempotent per session).
                    if leaked_player_ids and not is_private_leak_publicized(market, session_id=str(req.session_id)):
                        ttl = int(getattr(cfg, "trade_block_auto_list_days_public_offer", 10) or 10)
                        if ttl <= 0:
                            ttl = 1
                        for pid in leaked_player_ids:
                            upsert_trade_listing(
                                market,
                                today=today,
                                player_id=pid,
                                team_id=session["user_team_id"],
                                listed_by="AUTO_PRIVATE_LEAK",
                                visibility="PUBLIC",
                                priority=0.62,
                                reason_code="PRIVATE_OFFER_LEAK_PUBLICIZED",
                                expires_on=(today + timedelta(days=ttl + 1)).isoformat(),
                                source={
                                    "session_id": req.session_id,
                                    "offer_privacy": "PRIVATE",
                                    "publicized_from": "LEAK",
                                },
                            )
                        record_market_event(
                            market,
                            today=today,
                            event_type="TRADE_OFFER_PUBLICIZED_FROM_LEAK",
                            payload={
                                "session_id": req.session_id,
                                "user_team_id": str(session["user_team_id"]).upper(),
                                "other_team_id": str(session["other_team_id"]).upper(),
                                "player_ids": leaked_player_ids,
                            },
                        )
                        mark_private_leak_publicized(
                            market,
                            session_id=str(req.session_id),
                            today=today,
                            player_ids=leaked_player_ids,
                            user_team_id=str(session["user_team_id"]).upper(),
                            other_team_id=str(session["other_team_id"]).upper(),
                            leaked_by="USER",
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
                valid_days=2,
                current_date=in_game_date,
                validate=False,   # already validated above
                db_path=db_path,  # keep hash/locking based on the same db snapshot
            )
            negotiation_store.set_committed(req.session_id, committed["deal_id"])
            # Once committed, this negotiation should no longer consume "ACTIVE" capacity.
            negotiation_store.set_status(req.session_id, "CLOSED")
            # Optional but useful for UI/debugging.
            negotiation_store.set_phase(req.session_id, "ACCEPTED")
            # Keep session expiry aligned with the committed deal expiry.
            negotiation_store.set_valid_until(req.session_id, committed["expires_at"])
            return {
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
            }

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

                return {
                    "ok": True,
                    "accepted": False,
                    "counter_unimplemented": False,
                    "deal": serialize_deal(deal),
                    "ai_verdict": to_jsonable(decision.verdict),
                    "ai_decision": to_jsonable(decision),
                    "ai_evaluation": eval_summary,
                    "offer_privacy": offer_privacy,
                    "leak_status": leak_status,
                }

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

        return {
            "ok": True,
            "accepted": False,
            "counter_unimplemented": False,
            "deal": serialize_deal(deal),
            "ai_verdict": to_jsonable(decision.verdict),
            "ai_decision": to_jsonable(decision),
            "ai_evaluation": eval_summary,
                        "offer_privacy": offer_privacy,
                        "leak_status": leak_status,
        }
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

        return {
            "ok": True,
            "team_id": str(req.team_id).upper(),
            "deal": serialize_deal(deal),
            "decision": to_jsonable(decision),
            "evaluation": to_jsonable(evaluation),
        }
    except TradeError as exc:
        return _trade_error_response(exc)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Trade evaluation failed: {exc}")


# -------------------------------------------------------------------------
# 로스터 요약 API (LLM 컨텍스트용)
