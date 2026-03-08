from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from .market_state import list_team_trade_listings, record_market_event, upsert_trade_listing


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _parse_iso_ymd(value: Any) -> Optional[date]:
    if value is None:
        return None
    s = str(value).strip()
    if len(s) < 10:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def _clamp01(x: Any) -> float:
    v = _safe_float(x, 0.0)
    if v <= 0.0:
        return 0.0
    if v >= 1.0:
        return 1.0
    return v


def _listing_expires_on(today: date, ttl_days: int) -> str:
    d = int(ttl_days or 0)
    if d <= 0:
        d = 1
    return date.fromordinal(today.toordinal() + d + 1).isoformat()


def _team_daily_proactive_count(trade_market: Dict[str, Any], *, today: date, team_id: str) -> int:
    events = trade_market.get("events") if isinstance(trade_market.get("events"), list) else []
    day_iso = today.isoformat()
    tid = str(team_id).upper()
    n = 0
    for e in events:
        if not isinstance(e, dict):
            continue
        if str(e.get("at") or "") != day_iso:
            continue
        if str(e.get("type") or "") != "TRADE_BLOCK_LISTED":
            continue
        p = e.get("payload") if isinstance(e.get("payload"), dict) else {}
        if str(p.get("origin") or "") != "PROACTIVE":
            continue
        if str(p.get("team_id") or "").upper() != tid:
            continue
        n += 1
    return n


def _is_player_in_proactive_cooldown(
    trade_market: Dict[str, Any],
    *,
    today: date,
    player_id: str,
    cooldown_days: int,
) -> bool:
    if int(cooldown_days or 0) <= 0:
        return False

    latest: Optional[date] = None

    listings = trade_market.get("listings") if isinstance(trade_market.get("listings"), dict) else {}
    raw = listings.get(str(player_id)) if isinstance(listings, dict) else None
    if isinstance(raw, dict):
        d = _parse_iso_ymd(raw.get("updated_at") or raw.get("created_at"))
        if d is not None:
            latest = d

    events = trade_market.get("events") if isinstance(trade_market.get("events"), list) else []
    for e in events:
        if not isinstance(e, dict):
            continue
        if str(e.get("type") or "") != "TRADE_BLOCK_LISTED":
            continue
        p = e.get("payload") if isinstance(e.get("payload"), dict) else {}
        if str(p.get("player_id") or "") != str(player_id):
            continue
        d = _parse_iso_ymd(e.get("at"))
        if d is None:
            continue
        if latest is None or d > latest:
            latest = d

    if latest is None:
        return False
    return (today - latest).days < int(cooldown_days)


_PROACTIVE_ALLOWED_BUCKETS: Tuple[str, ...] = (
    "VETERAN_SALE",
    "EXPIRING",
    "SURPLUS_LOW_FIT",
    "SURPLUS_REDUNDANT",
    "FILLER_BAD_CONTRACT",
    "FILLER_CHEAP",
    "CONSOLIDATE",
)


def _bucket_priority_key(player: Any) -> Tuple[int, float]:
    buckets = {str(b).upper() for b in (getattr(player, "buckets", None) or tuple())}
    for idx, b in enumerate(_PROACTIVE_ALLOWED_BUCKETS):
        if b in buckets:
            return idx, -max(0.0, _safe_float(getattr(player, "surplus_score", 0.0), 0.0))
    return len(_PROACTIVE_ALLOWED_BUCKETS), -max(0.0, _safe_float(getattr(player, "surplus_score", 0.0), 0.0))


def apply_ai_proactive_listings(
    *,
    team_id: str,
    tick_ctx: Any,
    trade_market: Dict[str, Any],
    today: date,
    config: Any,
) -> List[str]:
    tid = str(team_id).upper()

    if not bool(getattr(config, "ai_proactive_listing_enabled", True)):
        return []

    team_active_cap = int(getattr(config, "ai_proactive_listing_team_active_cap", 4) or 4)
    team_daily_cap = int(getattr(config, "ai_proactive_listing_team_daily_cap", 2) or 2)
    cooldown_days = int(getattr(config, "ai_proactive_listing_player_cooldown_days", 7) or 7)

    active_rows = list_team_trade_listings(trade_market, team_id=tid, active_only=True, today=today)
    active_pids = {str(r.get("player_id") or "") for r in active_rows if isinstance(r, dict)}
    active_pids.discard("")

    already_today = _team_daily_proactive_count(trade_market, today=today, team_id=tid)
    remaining = min(max(0, team_active_cap - len(active_pids)), max(0, team_daily_cap - already_today))
    if remaining <= 0:
        return []

    catalog = getattr(tick_ctx, "asset_catalog", None)
    out_team = None
    try:
        out_team = getattr(catalog, "outgoing_by_team", {}).get(tid) if catalog is not None else None
    except Exception:
        out_team = None
    if out_team is None:
        return []

    players = getattr(out_team, "players", {}) or {}
    if not isinstance(players, dict) or not players:
        return []

    posture = str(getattr(tick_ctx.get_team_situation(tid), "trade_posture", "STAND_PAT") or "STAND_PAT").upper()
    ttl_days = int(getattr(config, "ai_proactive_listing_ttl_days_default", 5) or 5)
    if posture == "SELL":
        ttl_days = int(getattr(config, "ai_proactive_listing_ttl_days_sell", 12) or ttl_days)
    elif posture == "SOFT_SELL":
        ttl_days = int(getattr(config, "ai_proactive_listing_ttl_days_soft_sell", 7) or ttl_days)

    pri_base = float(getattr(config, "ai_proactive_listing_priority_base", 0.45) or 0.0)
    pri_span = float(getattr(config, "ai_proactive_listing_priority_span", 0.35) or 0.0)

    candidate_ids: List[str] = []
    player_ids_by_bucket = getattr(out_team, "player_ids_by_bucket", {}) or {}
    if isinstance(player_ids_by_bucket, dict):
        seen: set[str] = set()
        for b in _PROACTIVE_ALLOWED_BUCKETS:
            for pid in player_ids_by_bucket.get(b, tuple()) or tuple():
                p = str(pid)
                if not p or p in seen:
                    continue
                if p not in players:
                    continue
                seen.add(p)
                candidate_ids.append(p)

    rows: List[Tuple[Tuple[int, float, str], str]] = []
    for p in candidate_ids:
        player = players.get(p)
        if player is None:
            continue
        if not p:
            continue
        if p in active_pids:
            continue

        lock = getattr(player, "lock", None)
        if bool(getattr(lock, "is_locked", False)):
            continue

        banned_until = _parse_iso_ymd(getattr(player, "recent_signing_banned_until", None))
        if banned_until is not None and today < banned_until:
            continue

        if _is_player_in_proactive_cooldown(
            trade_market,
            today=today,
            player_id=p,
            cooldown_days=cooldown_days,
        ):
            continue

        pri_idx, pri_surplus = _bucket_priority_key(player)
        rows.append(((pri_idx, pri_surplus, p), p))

    if not rows:
        return []

    rows.sort(key=lambda x: x[0])
    listed: List[str] = []

    for rank, pid in rows[:remaining]:
        rank_idx, _, _ = rank
        bucket_weight = max(0.0, 1.0 - (float(rank_idx) * 0.1))
        priority = _clamp01(pri_base + pri_span * min(max(bucket_weight, 0.0), 1.0))
        upsert_trade_listing(
            trade_market,
            today=today,
            player_id=pid,
            team_id=tid,
            listed_by="AI_GM",
            visibility="PUBLIC",
            priority=priority,
            reason_code="AI_PROACTIVE_SHOP",
            expires_on=_listing_expires_on(today, ttl_days),
            meta={"origin": "PROACTIVE"},
        )
        record_market_event(
            trade_market,
            today=today,
            event_type="TRADE_BLOCK_LISTED",
            payload={
                "team_id": tid,
                "player_id": pid,
                "listed_by": "AI_GM",
                "reason_code": "AI_PROACTIVE_SHOP",
                "origin": "PROACTIVE",
                "priority": float(priority),
            },
        )
        listed.append(pid)

    return listed
