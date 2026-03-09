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
    "SURPLUS_EXPENDABLE",
    "FILLER_BAD_CONTRACT",
)


def _priority_signal(player: Any, *, config: Any, normalized: bool) -> float:
    if not normalized:
        raw = getattr(player, "raw_trade_block_score", None)
        if raw is not None:
            return float(raw)
    norm = getattr(player, "trade_block_score", None)
    if norm is not None:
        return _clamp01(norm) if normalized else float(norm)
    return 0.0


def _bucket_priority_key(player: Any, *, config: Any) -> Tuple[int, float]:
    buckets = {str(b).upper() for b in (getattr(player, "buckets", None) or tuple())}
    pri_score = _priority_signal(player, config=config, normalized=False)
    for idx, b in enumerate(_PROACTIVE_ALLOWED_BUCKETS):
        if b in buckets:
            return idx, -pri_score
    return len(_PROACTIVE_ALLOWED_BUCKETS), -pri_score


def _should_run_proactive_listing_today(
    *,
    trade_market: Dict[str, Any],
    team_id: str,
    today: date,
    config: Any,
) -> bool:
    cadence = str(getattr(config, "ai_proactive_listing_cadence", "DAILY") or "DAILY").upper()
    if cadence != "WEEKLY":
        return True

    anchor = int(getattr(config, "ai_proactive_listing_anchor_weekday", 0) or 0)
    anchor = max(0, min(6, anchor))
    if int(today.weekday()) != anchor:
        return False

    meta = trade_market.get("proactive_listing_meta") if isinstance(trade_market.get("proactive_listing_meta"), dict) else {}
    team_meta = meta.get(str(team_id).upper()) if isinstance(meta, dict) else None
    last_iso = team_meta.get("last_eval_at") if isinstance(team_meta, dict) else None
    last_d = _parse_iso_ymd(last_iso)
    if last_d is not None and (today - last_d).days < 7:
        return False
    return True


def _stamp_proactive_listing_eval(*, trade_market: Dict[str, Any], team_id: str, today: date) -> None:
    meta = trade_market.get("proactive_listing_meta")
    if not isinstance(meta, dict):
        meta = {}
        trade_market["proactive_listing_meta"] = meta

    tid = str(team_id).upper()
    cur = meta.get(tid)
    if not isinstance(cur, dict):
        cur = {}
    cur["last_eval_at"] = today.isoformat()
    meta[tid] = cur


def _resolve_bucket_threshold(*, bucket: str, team_situation: Any, config: Any) -> float:
    posture = str(getattr(team_situation, "trade_posture", "STAND_PAT") or "STAND_PAT").upper()
    horizon = str(getattr(team_situation, "time_horizon", "RE_TOOL") or "RE_TOOL").upper()
    urgency = _clamp01(getattr(team_situation, "urgency", 0.0))
    constraints = getattr(team_situation, "constraints", None)
    cooldown_active = bool(getattr(constraints, "cooldown_active", False))

    table = getattr(config, "ai_proactive_listing_bucket_thresholds", {}) or {}
    row = table.get(posture, {}) if isinstance(table, dict) else {}
    default_threshold = _safe_float(getattr(config, "ai_proactive_listing_threshold_default", 0.55), 0.55)
    base = _safe_float(
        (row.get(bucket) if isinstance(row, dict) else None),
        default_threshold,
    )

    if horizon == "WIN_NOW" and bucket == "SURPLUS_EXPENDABLE":
        base += _safe_float(getattr(config, "ai_proactive_listing_threshold_horizon_win_now_delta", -0.03), -0.03)
    elif horizon == "REBUILD" and bucket == "VETERAN_SALE":
        base += _safe_float(getattr(config, "ai_proactive_listing_threshold_horizon_rebuild_delta", -0.05), -0.05)

    u_cut = _safe_float(getattr(config, "ai_proactive_listing_threshold_urgency_cut", 0.75), 0.75)
    if urgency >= u_cut:
        base += _safe_float(getattr(config, "ai_proactive_listing_threshold_urgency_delta", -0.03), -0.03)

    if cooldown_active:
        base += _safe_float(getattr(config, "ai_proactive_listing_threshold_cooldown_active_delta", 0.05), 0.05)

    lo = _safe_float(getattr(config, "ai_proactive_listing_threshold_min", 0.10), 0.10)
    hi = _safe_float(getattr(config, "ai_proactive_listing_threshold_max", 0.95), 0.95)
    if lo > hi:
        lo, hi = hi, lo
    return max(lo, min(hi, base))


def _passes_listing_threshold(*, player: Any, bucket: str, team_situation: Any, config: Any) -> bool:
    if not bool(getattr(config, "ai_proactive_listing_threshold_enabled", True)):
        return True
    score = _priority_signal(player, config=config, normalized=True)
    threshold = _resolve_bucket_threshold(bucket=bucket, team_situation=team_situation, config=config)
    return float(score) >= float(threshold)


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

    if not _should_run_proactive_listing_today(
        trade_market=trade_market,
        team_id=tid,
        today=today,
        config=config,
    ):
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

    team_situation = tick_ctx.get_team_situation(tid)
    posture = str(getattr(team_situation, "trade_posture", "STAND_PAT") or "STAND_PAT").upper()

    ttl_days = int(getattr(config, "ai_proactive_listing_ttl_days_default", 5) or 5)
    if posture == "SELL":
        ttl_days = int(getattr(config, "ai_proactive_listing_ttl_days_sell", 12) or ttl_days)
    elif posture == "SOFT_SELL":
        ttl_days = int(getattr(config, "ai_proactive_listing_ttl_days_soft_sell", 7) or ttl_days)

    pri_base = float(getattr(config, "ai_proactive_listing_priority_base", 0.45) or 0.0)
    pri_span = float(getattr(config, "ai_proactive_listing_priority_span", 0.35) or 0.0)

    candidate_ids: List[str] = []
    candidate_bucket_by_pid: Dict[str, str] = {}
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
                candidate_bucket_by_pid[p] = str(b)

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

        bucket = str(candidate_bucket_by_pid.get(p) or "").upper()
        if not bucket:
            continue
        if not _passes_listing_threshold(
            player=player,
            bucket=bucket,
            team_situation=team_situation,
            config=config,
        ):
            continue

        pri_idx, pri_surplus = _bucket_priority_key(player, config=config)
        rows.append(((pri_idx, pri_surplus, p), p))

    if not rows:
        _stamp_proactive_listing_eval(trade_market=trade_market, team_id=tid, today=today)
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

    _stamp_proactive_listing_eval(trade_market=trade_market, team_id=tid, today=today)
    return listed
