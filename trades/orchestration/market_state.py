from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set

import game_time
import state

from .types import CleanupReport, OrchestrationConfig


def _ensure_trade_market_schema(m: Dict[str, Any]) -> Dict[str, Any]:
    # IMPORTANT: keep in-place mutation when m is already a dict (state persistence depends on this)
    if not isinstance(m, dict):
        m = dict(m or {})
    m.setdefault("last_tick_date", None)
    m.setdefault("last_fail_closed_date", None)
    m.setdefault("last_fail_closed_reason", None)
    m.setdefault("listings", {})
    m.setdefault("threads", {})
    m.setdefault("cooldowns", {})
    m.setdefault("events", [])
    m.setdefault("human_controlled_team_ids", [])
    m.setdefault("tick_nonce", 0)
    m.setdefault("applied_exec_deal_ids", {})
    m.setdefault("grievance_cursor", {})
    m.setdefault("proactive_listing_meta", {})
    if not isinstance(m.get("listings"), dict):
        m["listings"] = {}
    if not isinstance(m.get("threads"), dict):
        m["threads"] = {}
    if not isinstance(m.get("cooldowns"), dict):
        m["cooldowns"] = {}
    if not isinstance(m.get("events"), list):
        m["events"] = []
    if not isinstance(m.get("applied_exec_deal_ids"), dict):
        m["applied_exec_deal_ids"] = {}
    if not isinstance(m.get("grievance_cursor"), dict):
        m["grievance_cursor"] = {}
    if not isinstance(m.get("proactive_listing_meta"), dict):
        m["proactive_listing_meta"] = {}
    if not isinstance(m.get("human_controlled_team_ids"), (list, tuple, set, str, type(None))):
        m["human_controlled_team_ids"] = []
    try:
        m["tick_nonce"] = int(m.get("tick_nonce") or 0)
    except Exception:
        m["tick_nonce"] = 0
    return m


def _ensure_trade_memory_schema(mem: Dict[str, Any]) -> Dict[str, Any]:
    # IMPORTANT: keep in-place mutation when mem is already a dict (state persistence depends on this)
    if not isinstance(mem, dict):
        mem = dict(mem or {})
    mem.setdefault("relationships", {})
    if not isinstance(mem.get("relationships"), dict):
        mem["relationships"] = {}
    return mem


def load_trade_market() -> Dict[str, Any]:
    return _ensure_trade_market_schema(state.trade_market_get() or {})


def save_trade_market(market: Dict[str, Any]) -> None:
    state.trade_market_set(_ensure_trade_market_schema(market))


def load_trade_memory() -> Dict[str, Any]:
    return _ensure_trade_memory_schema(state.trade_memory_get() or {})


def save_trade_memory(mem: Dict[str, Any]) -> None:
    state.trade_memory_set(_ensure_trade_memory_schema(mem))


def _clamp01(x: Any, default: float = 0.5) -> float:
    try:
        xf = float(x)
    except Exception:
        return float(default)
    if xf <= 0.0:
        return 0.0
    if xf >= 1.0:
        return 1.0
    return xf


def _coerce_iso_date(raw: Any) -> Optional[str]:
    if not isinstance(raw, str) or not raw:
        return None
    s = raw[:10]
    try:
        date.fromisoformat(s)
        return s
    except Exception:
        return None


def _normalize_listing_entry(
    player_id: str,
    payload: Dict[str, Any],
    *,
    today_iso: Optional[str] = None,
) -> Dict[str, Any]:
    now_iso = game_time.require_date_iso(today_iso, field="today_iso")
    p = dict(payload or {}) if isinstance(payload, dict) else {}
    team_id = str(p.get("team_id") or "").upper()
    listed_by = str(p.get("listed_by") or "USER").upper()
    visibility = str(p.get("visibility") or "PUBLIC").upper()
    status = str(p.get("status") or "ACTIVE").upper()
    if visibility not in {"PUBLIC", "PRIVATE"}:
        visibility = "PUBLIC"
    if status not in {"ACTIVE", "INACTIVE"}:
        status = "ACTIVE"

    source = p.get("source") if isinstance(p.get("source"), dict) else {}
    meta = p.get("meta") if isinstance(p.get("meta"), dict) else {}

    out = {
        "player_id": str(player_id),
        "team_id": team_id,
        "status": status,
        "visibility": visibility,
        "listed_by": listed_by,
        "priority": _clamp01(p.get("priority"), default=0.5),
        "reason_code": str(p.get("reason_code") or "MANUAL").upper(),
        "created_at": _coerce_iso_date(p.get("created_at")) or now_iso,
        "updated_at": _coerce_iso_date(p.get("updated_at")) or now_iso,
        "expires_on": _coerce_iso_date(p.get("expires_on")),
        "source": dict(source),
        "meta": dict(meta),
    }
    return out


def _get_listings_container(trade_market: Dict[str, Any]) -> Dict[str, Any]:
    m = _ensure_trade_market_schema(trade_market)
    raw = m.get("listings")
    if not isinstance(raw, dict):
        raw = {}
        m["listings"] = raw
    return raw


def leak_publicize_cursor_key(*, session_id: Optional[str] = None, deal_id: Optional[str] = None) -> str:
    sid = str(session_id or "").strip()
    did = str(deal_id or "").strip()
    if sid:
        return f"PRIVATE_LEAK_PUBLICIZED::SESSION::{sid}"
    if did:
        return f"PRIVATE_LEAK_PUBLICIZED::DEAL::{did}"
    return "PRIVATE_LEAK_PUBLICIZED::UNKNOWN"


def is_private_leak_publicized(
    trade_market: Dict[str, Any],
    *,
    session_id: Optional[str] = None,
    deal_id: Optional[str] = None,
) -> bool:
    m = _ensure_trade_market_schema(trade_market)
    cur = m.get("grievance_cursor") if isinstance(m.get("grievance_cursor"), dict) else {}
    if not isinstance(cur, dict):
        return False
    keys: List[str] = []
    if session_id:
        keys.append(leak_publicize_cursor_key(session_id=session_id))
    if deal_id:
        keys.append(leak_publicize_cursor_key(deal_id=deal_id))
    for k in keys:
        raw = cur.get(k)
        if isinstance(raw, dict) and raw.get("publicized") is True:
            return True
    return False


def mark_private_leak_publicized(
    trade_market: Dict[str, Any],
    *,
    today: date,
    player_ids: List[str],
    user_team_id: str,
    other_team_id: str,
    session_id: Optional[str] = None,
    deal_id: Optional[str] = None,
    leaked_by: Optional[str] = None,
) -> None:
    m = _ensure_trade_market_schema(trade_market)
    cur = m.get("grievance_cursor") if isinstance(m.get("grievance_cursor"), dict) else {}
    m["grievance_cursor"] = cur

    payload = {
        "publicized": True,
        "date": str(today.isoformat()),
        "session_id": str(session_id) if session_id else None,
        "deal_id": str(deal_id) if deal_id else None,
        "user_team_id": str(user_team_id).upper(),
        "other_team_id": str(other_team_id).upper(),
        "player_ids": [str(x) for x in (player_ids or []) if str(x)],
        "leaked_by": str(leaked_by).upper() if leaked_by else None,
    }

    if session_id:
        cur[leak_publicize_cursor_key(session_id=session_id)] = dict(payload)
    if deal_id:
        cur[leak_publicize_cursor_key(deal_id=deal_id)] = dict(payload)


def upsert_trade_listing(
    trade_market: Dict[str, Any],
    *,
    today: date,
    player_id: str,
    team_id: str,
    listed_by: str,
    visibility: str = "PUBLIC",
    priority: float = 0.5,
    reason_code: str = "MANUAL",
    expires_on: Optional[str] = None,
    source: Optional[Dict[str, Any]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    listings = _get_listings_container(trade_market)
    pid = str(player_id)
    now_iso = today.isoformat()
    prev = listings.get(pid) if isinstance(listings.get(pid), dict) else {}
    entry = _normalize_listing_entry(
        pid,
        {
            **dict(prev or {}),
            "player_id": pid,
            "team_id": str(team_id).upper(),
            "status": "ACTIVE",
            "visibility": str(visibility or "PUBLIC").upper(),
            "listed_by": str(listed_by or "USER").upper(),
            "priority": _clamp01(priority),
            "reason_code": str(reason_code or "MANUAL").upper(),
            "created_at": prev.get("created_at") if isinstance(prev, dict) else now_iso,
            "updated_at": now_iso,
            "expires_on": _coerce_iso_date(expires_on),
            "source": dict(source or (prev.get("source") if isinstance(prev, dict) else {}) or {}),
            "meta": dict(meta or (prev.get("meta") if isinstance(prev, dict) else {}) or {}),
        },
        today_iso=now_iso,
    )
    listings[pid] = entry
    return dict(entry)


def remove_trade_listing(trade_market: Dict[str, Any], *, player_id: str) -> bool:
    listings = _get_listings_container(trade_market)
    pid = str(player_id)
    if pid in listings:
        listings.pop(pid, None)
        return True
    return False


def list_team_trade_listings(
    trade_market: Dict[str, Any],
    *,
    team_id: str,
    active_only: bool = True,
    today: Optional[date] = None,
) -> List[Dict[str, Any]]:
    listings = _get_listings_container(trade_market)
    tid = str(team_id).upper()
    out: List[Dict[str, Any]] = []
    d = today
    for pid, raw in listings.items():
        if not isinstance(raw, dict):
            continue
        fallback_iso = _coerce_iso_date(raw.get("updated_at")) or _coerce_iso_date(raw.get("created_at")) or game_time.game_date_iso()
        e = _normalize_listing_entry(str(pid), raw, today_iso=fallback_iso)
        if e.get("team_id") != tid:
            continue
        if active_only:
            if str(e.get("status") or "").upper() != "ACTIVE":
                continue
            exp = e.get("expires_on")
            if d is not None and isinstance(exp, str):
                try:
                    if d >= date.fromisoformat(exp[:10]):
                        continue
                except Exception:
                    pass
        out.append(e)
    out.sort(key=lambda x: (str(x.get("updated_at") or ""), str(x.get("player_id") or "")), reverse=True)
    return out


def get_active_listing_team_ids(trade_market: Dict[str, Any], *, today: date) -> Set[str]:
    listings = _get_listings_container(trade_market)
    out: Set[str] = set()
    for pid, raw in listings.items():
        if not isinstance(raw, dict):
            continue
        fallback_iso = _coerce_iso_date(raw.get("updated_at")) or _coerce_iso_date(raw.get("created_at")) or game_time.game_date_iso()
        e = _normalize_listing_entry(str(pid), raw, today_iso=fallback_iso)
        if str(e.get("status") or "").upper() != "ACTIVE":
            continue
        exp = e.get("expires_on")
        if isinstance(exp, str):
            try:
                if today >= date.fromisoformat(exp[:10]):
                    continue
            except Exception:
                continue
        tid = str(e.get("team_id") or "").upper()
        if tid:
            out.add(tid)
    return out


def get_human_controlled_team_ids(
    trade_market: Dict[str, Any],
    *,
    state_key: str = "human_controlled_team_ids",
) -> Set[str]:
    m = _ensure_trade_market_schema(trade_market)
    raw = m.get(state_key)
    if raw is None:
        return set()
    if isinstance(raw, (list, tuple, set)):
        return {str(x).upper() for x in raw if x}
    if isinstance(raw, str):
        return {s.strip().upper() for s in raw.split(",") if s.strip()}
    return set()


def set_human_controlled_team_ids(
    trade_market: Dict[str, Any],
    team_ids: Iterable[str],
    *,
    state_key: str = "human_controlled_team_ids",
) -> None:
    m = _ensure_trade_market_schema(trade_market)
    m[state_key] = sorted({str(x).upper() for x in team_ids if x})


def prune_market_events(trade_market: Dict[str, Any], *, max_kept: int) -> int:
    m = _ensure_trade_market_schema(trade_market)
    ev = m.get("events") if isinstance(m.get("events"), list) else []
    if not isinstance(max_kept, int) or max_kept <= 0:
        max_kept = 200
    if len(ev) > max_kept:
        prune_n = len(ev) - max_kept
        m["events"] = ev[prune_n:]
        return prune_n
    return 0


def prune_applied_exec_deal_ids(trade_market: Dict[str, Any], *, max_kept: int) -> int:
    """Keep only the most recent N applied exec_deal_id markers.

    Stored schema:
      trade_market["applied_exec_deal_ids"]: { exec_deal_id(str): "YYYY-MM-DD" }

    This is used as an idempotency guard so a trade commit (DB SSOT) can be safely
    projected into market/memory multiple times without duplicating events/counts.
    """
    m = _ensure_trade_market_schema(trade_market)
    raw = m.get("applied_exec_deal_ids")
    if not isinstance(raw, dict):
        raw = {}
        m["applied_exec_deal_ids"] = raw

    try:
        max_i = int(max_kept)
    except Exception:
        max_i = 500

    if max_i <= 0:
        max_i = 500

    if len(raw) <= max_i:
        return 0

    items: List[tuple[date, str]] = []
    for k, v in raw.items():
        key = str(k)
        d = date.min
        if isinstance(v, str) and v:
            s = v[:10]
            try:
                d = date.fromisoformat(s)
            except Exception:
                d = date.min
        items.append((d, key))

    items.sort(key=lambda t: (t[0], t[1]), reverse=True)
    keep_keys = {key for _, key in items[:max_i]}

    before_n = len(raw)
    new_map: Dict[str, Any] = {}
    for k, v in raw.items():
        ks = str(k)
        if ks in keep_keys:
            new_map[ks] = v

    m["applied_exec_deal_ids"] = new_map
    return before_n - len(new_map)


def pre_tick_cleanup(
    *,
    today: date,
    trade_market: Dict[str, Any],
    trade_memory: Dict[str, Any],
    config: OrchestrationConfig,
) -> CleanupReport:
    """
    tick_ctx build 전에 호출 권장.

    cooldown 기간 정의(명확화):
    - cooldown_days = "오늘 액션 이후, 다음날부터 N일 동안 막는다"
    - 저장 필드 expires_on(YYYY-MM-DD): tick 시작 시점에 today >= expires_on 이면 cooldown 키 제거(배타적 종료)
      예) today=3/1, days=1  -> 3/2 하루 막힘 -> expires_on=3/3, 3/3 tick 시작에 키 제거
    """
    report = CleanupReport()
    m = _ensure_trade_market_schema(trade_market)
    _ensure_trade_memory_schema(trade_memory)

    cd = m.get("cooldowns") if isinstance(m.get("cooldowns"), dict) else {}
    removed = 0
    kept: Dict[str, Any] = {}

    for k, v in (cd or {}).items():
        tid = str(k).upper()

        expires_on = None
        # 신규 스키마
        if isinstance(v, dict):
            expires_on = v.get("expires_on")

        # 레거시 지원: until(inclusive last active date) 또는 until(ambiguous) 필드를 발견하면 변환
        if isinstance(expires_on, str):
            try:
                exp = date.fromisoformat(expires_on[:10])
                if today >= exp:
                    removed += 1
                    continue
            except Exception:
                # 파싱 실패: 보수적으로 유지(키 존재=active)
                kept[tid] = v
                continue

        else:
            # expires_on이 없을 때: 레거시 'until'/'expires_at'/'until_inclusive' 등을 최대한 해석
            legacy_until = None
            if isinstance(v, dict):
                legacy_until = v.get("until_inclusive") or v.get("until") or v.get("expires_at")

            if isinstance(legacy_until, str):
                try:
                    d_until = date.fromisoformat(legacy_until[:10])
                    # 레거시 until을 inclusive last active로 간주하고 expires_on = until + 1 day
                    exp = d_until + timedelta(days=1)
                    if today >= exp:
                        removed += 1
                        continue
                    # 보존하면서 새 필드도 추가(점진적 마이그레이션)
                    nv = dict(v)
                    nv["expires_on"] = exp.isoformat()
                    kept[tid] = nv
                    continue
                except Exception:
                    kept[tid] = v
                    continue

            # 어떤 날짜 정보도 없으면: 안전하게 유지(개발자/툴로 정리 필요)
            kept[tid] = v

    m["cooldowns"] = kept
    report.removed_cooldowns = removed

    # --- listings cleanup (expiry + shape)
    listings = m.get("listings") if isinstance(m.get("listings"), dict) else {}
    kept_listings: Dict[str, Any] = {}
    removed_listings = 0
    for k, v in (listings or {}).items():
        pid = str(k)
        if not isinstance(v, dict):
            removed_listings += 1
            continue
        e = _normalize_listing_entry(pid, v, today_iso=today.isoformat())
        exp = e.get("expires_on")
        if isinstance(exp, str):
            try:
                d_exp = date.fromisoformat(exp[:10])
                if today >= d_exp:
                    removed_listings += 1
                    continue
            except Exception:
                pass
        kept_listings[pid] = e
    m["listings"] = kept_listings

    # --- threads cleanup (expiry + size cap)
    th = m.get("threads") if isinstance(m.get("threads"), dict) else {}
    removed_th = 0
    kept_th: Dict[str, Any] = {}

    for k, v in (th or {}).items():
        key = str(k)
        if not isinstance(v, dict):
            # 오염된 엔트리는 제거(상업용 상태 안정성)
            removed_th += 1
            continue

        expires_on = v.get("expires_on")
        if isinstance(expires_on, str):
            try:
                exp = date.fromisoformat(expires_on[:10])
                if today >= exp:
                    removed_th += 1
                    continue
            except Exception:
                # 파싱 실패: 보수적으로 유지(키 존재=active)
                pass

        kept_th[key] = v

    m["threads"] = kept_th
    report.removed_threads_expired = removed_th

    # Keep only the most recent N threads to avoid state bloat
    try:
        max_threads = int(getattr(config, "max_threads_kept", 50) or 50)
    except Exception:
        max_threads = 50

    if max_threads > 0 and isinstance(m.get("threads"), dict) and len(m["threads"]) > max_threads:

        def _thread_last_at(entry: Dict[str, Any]) -> date:
            for k2 in ("last_at", "started_at"):
                s = entry.get(k2)
                if isinstance(s, str):
                    try:
                        return date.fromisoformat(s[:10])
                    except Exception:
                        continue
            return date.min

        items = [(k2, v2) for k2, v2 in m["threads"].items() if isinstance(v2, dict)]
        items.sort(key=lambda kv: _thread_last_at(kv[1]), reverse=True)
        keep_keys = {k2 for k2, _ in items[:max_threads]}

        if len(keep_keys) < len(m["threads"]):
            before_n = len(m["threads"])
            m["threads"] = {k2: v2 for k2, v2 in m["threads"].items() if k2 in keep_keys}
            report.pruned_threads_limit = before_n - len(m["threads"])

    # Keep idempotency markers bounded to avoid state bloat.
    try:
        max_ids = int(getattr(config, "max_applied_exec_deal_ids_kept", 500) or 500)
    except Exception:
        max_ids = 500
    report.pruned_applied_exec_deal_ids = prune_applied_exec_deal_ids(m, max_kept=max_ids)

    report.pruned_events = prune_market_events(m, max_kept=int(config.max_market_events_kept))
    return report


def add_team_cooldown(
    trade_market: Dict[str, Any],
    *,
    team_id: str,
    today: date,
    days: int,
    reason: str,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """
    cooldown 추가.

    days 정의(명확):
    - days <= 0: cooldown을 만들지 않는다.
    - days = N: 오늘 액션 이후 "다음날부터 N일" 막는다.
      expires_on = today + (N + 1)
    """
    m = _ensure_trade_market_schema(trade_market)
    try:
        days_i = int(days)
    except Exception:
        days_i = 0

    if days_i <= 0:
        return

    tid = str(team_id).upper()
    expires_on = date.fromordinal(today.toordinal() + days_i + 1).isoformat()

    m["cooldowns"][tid] = {
        "expires_on": expires_on,
        "reason": str(reason),
        "meta": dict(meta or {}),
        "created_at": today.isoformat(),
    }


def record_market_event(
    trade_market: Dict[str, Any],
    *,
    today: date,
    event_type: str,
    payload: Dict[str, Any],
) -> None:
    m = _ensure_trade_market_schema(trade_market)
    m["events"].append(
        {"at": today.isoformat(), "type": str(event_type), "payload": dict(payload or {})}
    )


# -----------------------------------------------------------------------------
# Trade execution projector (SSOT)
# -----------------------------------------------------------------------------


def _normalized_team_ids_from_transaction(transaction: Dict[str, Any]) -> List[str]:
    raw = transaction.get("teams")
    teams: List[str] = []

    if isinstance(raw, (list, tuple, set)):
        for t in raw:
            if t:
                teams.append(str(t).upper())
    elif isinstance(raw, str) and raw:
        # Accept "A,B" or "A|B"-style strings just in case.
        parts = [p.strip() for p in raw.replace("|", ",").split(",")]
        teams.extend([p.upper() for p in parts if p])

    if not teams:
        assets = transaction.get("assets")
        if isinstance(assets, dict):
            for k in assets.keys():
                if k:
                    teams.append(str(k).upper())

    # stable dedupe (preserve order)
    out: List[str] = []
    seen: Set[str] = set()
    for t in teams:
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _has_trade_executed_event(trade_market: Dict[str, Any], *, exec_deal_id: str) -> bool:
    m = _ensure_trade_market_schema(trade_market)
    ev = m.get("events") if isinstance(m.get("events"), list) else []
    x = str(exec_deal_id)
    for e in reversed(ev or []):
        if not isinstance(e, dict):
            continue
        if str(e.get("type", "")) != "TRADE_EXECUTED":
            continue
        payload = e.get("payload")
        if isinstance(payload, dict) and str(payload.get("exec_deal_id", "")) == x:
            return True
    return False


def apply_trade_executed_effects(
    *,
    transaction: Dict[str, Any],
    trade_market: Dict[str, Any],
    trade_memory: Dict[str, Any],
    today: date,
    config: OrchestrationConfig,
    score: Optional[float] = None,
    effective_pressure_by_team: Optional[Dict[str, float]] = None,
    rush_scalar: Optional[float] = None,
    buyer_id: Optional[str] = None,
    seller_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Project a committed trade (DB SSOT) into trade_market/trade_memory.

    SSOT principle:
      - transaction (LeagueService.execute_trade payload) is the source of truth.
      - trade_market/trade_memory are derived "presentation + constraints" state.

    Idempotency:
      - Uses trade_market["applied_exec_deal_ids"][exec_deal_id] to avoid duplicates.
      - Also applies lightweight dedupe for events/relationships as a safety net.
    """
    m = _ensure_trade_market_schema(trade_market)
    mem = _ensure_trade_memory_schema(trade_memory)

    exec_deal_id = str(transaction.get("deal_id") or "").strip()
    if not exec_deal_id:
        raise ValueError("transaction.deal_id is required to apply market effects")

    deal_key = str(transaction.get("deal_identity") or exec_deal_id).strip()
    teams = _normalized_team_ids_from_transaction(transaction)

    applied_map = m.get("applied_exec_deal_ids")
    if not isinstance(applied_map, dict):
        applied_map = {}
        m["applied_exec_deal_ids"] = applied_map

    if exec_deal_id in applied_map:
        return {
            "applied": False,
            "already_applied": True,
            "exec_deal_id": exec_deal_id,
            "deal_id": deal_key,
            "teams": teams,
            "event_recorded": False,
            "relationships_bumped": 0,
        }

    # Normalize optional meta inputs
    try:
        rush = float(rush_scalar or 0.0)
    except Exception:
        rush = 0.0

    # Cooldowns
    for tid in teams:
        others = [x for x in teams if x != tid]
        pressure = 0.0
        if isinstance(effective_pressure_by_team, dict):
            try:
                pressure = float(effective_pressure_by_team.get(tid, 0.0) or 0.0)
            except Exception:
                pressure = 0.0

        days = int(getattr(config, "cooldown_days_after_executed_trade", 5) or 5)
        if effective_pressure_by_team is not None or rush_scalar is not None:
            try:
                from . import policy  # local import to avoid cycles

                days = int(policy.cooldown_days_after_executed_trade(pressure, rush, config=config))
            except Exception:
                days = int(getattr(config, "cooldown_days_after_executed_trade", 5) or 5)

        meta: Dict[str, Any] = {
            "deal_id": deal_key,
            "exec_deal_id": exec_deal_id,
            "source": transaction.get("source"),
        }
        if len(others) == 1:
            meta["other"] = others[0]
        elif others:
            meta["others"] = others
        if effective_pressure_by_team is not None:
            meta["effective_pressure"] = float(pressure or 0.0)
        if rush_scalar is not None:
            meta["rush_scalar"] = float(rush or 0.0)

        add_team_cooldown(
            trade_market,
            team_id=tid,
            today=today,
            days=int(days),
            reason="TRADE_EXECUTED",
            meta=meta,
        )

    # Market event (dedupe by exec_deal_id)
    event_recorded = False
    if not _has_trade_executed_event(trade_market, exec_deal_id=exec_deal_id):
        payload: Dict[str, Any] = {
            "deal_id": deal_key,
            "exec_deal_id": exec_deal_id,
            "teams": teams,
            "source": transaction.get("source"),
        }

        b = str(buyer_id).upper() if buyer_id else (teams[0] if len(teams) == 2 else "")
        s = str(seller_id).upper() if seller_id else (teams[1] if len(teams) == 2 else "")
        if b:
            payload["buyer_id"] = b
        if s:
            payload["seller_id"] = s
        if score is not None:
            try:
                payload["score"] = float(score or 0.0)
            except Exception:
                payload["score"] = 0.0

        record_market_event(trade_market, today=today, event_type="TRADE_EXECUTED", payload=payload)
        event_recorded = True

    # Relationship bump: bump each pair once (dedupe by last_exec_deal_id)
    relationships_bumped = 0
    for i in range(len(teams)):
        for j in range(i + 1, len(teams)):
            a = teams[i]
            b = teams[j]
            entry = get_relationship_entry(trade_memory, team_a=a, team_b=b)
            if isinstance(entry, dict):
                meta = entry.get("meta")
                if isinstance(meta, dict) and str(meta.get("last_exec_deal_id", "")) == exec_deal_id:
                    continue

            bump_relationship(
                trade_memory,
                team_a=a,
                team_b=b,
                today=today,
                patch={
                    "counts": {"trade_executed": 1},
                    "meta": {"last_deal_id": deal_key, "last_exec_deal_id": exec_deal_id},
                },
            )
            relationships_bumped += 1

    # Mark applied (idempotency)
    try:
        m["applied_exec_deal_ids"][exec_deal_id] = today.isoformat()
    except Exception:
        # Best-effort: keep running even if state is corrupted.
        m["applied_exec_deal_ids"] = {str(exec_deal_id): today.isoformat()}

    return {
        "applied": True,
        "already_applied": False,
        "exec_deal_id": exec_deal_id,
        "deal_id": deal_key,
        "teams": teams,
        "event_recorded": bool(event_recorded),
        "relationships_bumped": int(relationships_bumped),
    }


def apply_trade_executed_effects_to_state(
    *,
    transaction: Dict[str, Any],
    today: Optional[date] = None,
    config: Optional[OrchestrationConfig] = None,
    score: Optional[float] = None,
    effective_pressure_by_team: Optional[Dict[str, float]] = None,
    rush_scalar: Optional[float] = None,
    buyer_id: Optional[str] = None,
    seller_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply executed-trade effects to trade_market + trade_memory atomically.

    IMPORTANT:
    - We must avoid read-modify-write races between separate market/memory saves.
    - This function therefore mutates both containers inside a single
      state.transaction() critical section.
    """

    cfg = config or OrchestrationConfig()

    if today is None:
        iso = None
        for k in ("trade_date", "date"):
            v = transaction.get(k)
            if isinstance(v, str) and v:
                iso = v
                break
        if iso:
            try:
                today = date.fromisoformat(iso[:10])
            except Exception:
                today = None

    if today is None:
        try:
            today = state.get_current_date_as_date()
        except Exception:
            raise

    # Atomic mutation: update both trade_market and trade_memory in one state transaction.
    with state.transaction("apply_trade_executed_effects_to_state") as st:
        raw_market = st.get("trade_market")
        if not isinstance(raw_market, dict):
            raw_market = {}
            st["trade_market"] = raw_market

        raw_mem = st.get("trade_memory")
        if not isinstance(raw_mem, dict):
            raw_mem = {}
            st["trade_memory"] = raw_mem

        market = _ensure_trade_market_schema(raw_market)
        mem = _ensure_trade_memory_schema(raw_mem)

        # Defensive: ensure the state points at the same dict objects we mutate.
        st["trade_market"] = market
        st["trade_memory"] = mem

        report = apply_trade_executed_effects(
            transaction=transaction,
            trade_market=market,
            trade_memory=mem,
            today=today,
            config=cfg,
            score=score,
            effective_pressure_by_team=effective_pressure_by_team,
            rush_scalar=rush_scalar,
            buyer_id=buyer_id,
            seller_id=seller_id,
        )

        # best-effort pruning to avoid state bloat on API-driven trades
        try:
            prune_applied_exec_deal_ids(
                market,
                max_kept=int(getattr(cfg, "max_applied_exec_deal_ids_kept", 500) or 500),
            )
        except Exception:
            pass
        try:
            prune_market_events(
                market,
                max_kept=int(getattr(cfg, "max_market_events_kept", 200) or 200),
            )
        except Exception:
            pass

        return report




# -----------------------------------------------------------------------------
# Threads (minimal "talks ongoing" market memory)
# -----------------------------------------------------------------------------


def make_pair_key(team_a: str, team_b: str) -> str:
    a = str(team_a).upper()
    b = str(team_b).upper()
    if a <= b:
        return f"{a}|{b}"
    return f"{b}|{a}"


def _compute_expires_on(today: date, days: int) -> str:
    """
    threads TTL 정의:
    - days = N: 오늘 이후 "다음날부터 N일" 동안 접촉 상태를 유지한다.
      expires_on = today + (N + 1)
    - days <= 0: 최소 1일은 유지(당일만 존재하면 체감이 약함)
    """
    try:
        d = int(days)
    except Exception:
        d = 0
    if d <= 0:
        d = 1
    return date.fromordinal(today.toordinal() + d + 1).isoformat()


def touch_thread(
    trade_market: Dict[str, Any],
    *,
    today: date,
    team_a: str,
    team_b: str,
    deal_id: str,
    score: float,
    reason_code: str,
    ttl_days: int,
) -> Dict[str, Any]:
    """
    Upsert/touch a thread for (team_a, team_b). Returns the entry dict with an
    additional transient key "_created" (bool) indicating whether it was newly created.
    """
    m = _ensure_trade_market_schema(trade_market)
    threads = m.get("threads") if isinstance(m.get("threads"), dict) else {}
    m["threads"] = threads

    a = str(team_a).upper()
    b = str(team_b).upper()
    if a == b:
        return {}

    key = make_pair_key(a, b)
    now_iso = today.isoformat()
    expires_on = _compute_expires_on(today, ttl_days)

    entry = threads.get(key)
    created = False
    if not isinstance(entry, dict):
        created = True
        entry = {
            "pair_key": key,
            "team_a": key.split("|")[0],
            "team_b": key.split("|")[1],
            "started_at": now_iso,
            "rumor_count": 0,
        }

    entry["last_at"] = now_iso
    entry["expires_on"] = expires_on
    entry["last_deal_id"] = str(deal_id)
    try:
        entry["last_score"] = float(score or 0.0)
    except Exception:
        entry["last_score"] = 0.0
    entry["last_reason_code"] = str(reason_code)
    try:
        entry["rumor_count"] = int(entry.get("rumor_count") or 0) + 1
    except Exception:
        entry["rumor_count"] = 1

    threads[key] = entry

    # Return a view that contains creation info without persisting it into state.
    out = dict(entry)
    out["_created"] = created
    return out


def get_active_thread_team_ids(
    trade_market: Dict[str, Any],
    *,
    today: date,
    excluded_team_ids: Optional[Set[str]] = None,
) -> Set[str]:
    m = _ensure_trade_market_schema(trade_market)
    threads = m.get("threads")
    if not isinstance(threads, dict):
        return set()

    excluded = {str(x).upper() for x in (excluded_team_ids or set()) if x}
    out: Set[str] = set()

    for v in threads.values():
        if not isinstance(v, dict):
            continue
        exp = v.get("expires_on")
        if not isinstance(exp, str):
            continue
        try:
            d_exp = date.fromisoformat(exp[:10])
        except Exception:
            continue
        if today >= d_exp:
            continue

        a = str(v.get("team_a") or "").upper()
        b = str(v.get("team_b") or "").upper()
        if a and a not in excluded:
            out.add(a)
        if b and b not in excluded:
            out.add(b)

    return out


def bump_relationship(
    trade_memory: Dict[str, Any],
    *,
    team_a: str,
    team_b: str,
    today: date,
    patch: Dict[str, Any],
) -> None:
    mem = _ensure_trade_memory_schema(trade_memory)
    rel = mem["relationships"]

    a = str(team_a).upper()
    b = str(team_b).upper()
    if a == b:
        return

    rel.setdefault(a, {})
    rel.setdefault(b, {})
    rel[a].setdefault(b, {"last_at": None, "counts": {}, "meta": {}})
    rel[b].setdefault(a, {"last_at": None, "counts": {}, "meta": {}})

    def _apply(entry: Dict[str, Any]) -> None:
        entry["last_at"] = today.isoformat()
        counts = entry.setdefault("counts", {})
        if isinstance(patch.get("counts"), dict):
            for ck, cv in patch["counts"].items():
                try:
                    counts[str(ck)] = int(counts.get(str(ck), 0)) + int(cv)
                except Exception:
                    continue
        meta = entry.setdefault("meta", {})
        if isinstance(patch.get("meta"), dict):
            meta.update(patch["meta"])

    _apply(rel[a][b])
    _apply(rel[b][a])


# -----------------------------------------------------------------------------
# Relationship read helpers (NO implicit creation; safe for dry_run / analysis)
# -----------------------------------------------------------------------------

def get_relationship_entry(
    trade_memory: Dict[str, Any],
    *,
    team_a: str,
    team_b: str,
) -> Optional[Dict[str, Any]]:
    """
    Read-only accessor for trade_memory.relationships[team_a][team_b].

    IMPORTANT:
    - Do NOT create missing keys (no setdefault) to avoid state pollution in dry_run / analysis.
    - Returns the entry dict if it exists and is a dict, otherwise None.
    """
    if not isinstance(trade_memory, dict):
        return None
    rel = trade_memory.get("relationships")
    if not isinstance(rel, dict):
        return None

    a = str(team_a).upper()
    b = str(team_b).upper()
    if a == b:
        return None

    a_map = rel.get(a)
    if not isinstance(a_map, dict):
        return None
    entry = a_map.get(b)
    if isinstance(entry, dict):
        return entry
    return None


def get_rel_meta_date_iso(
    trade_memory: Dict[str, Any],
    *,
    team_a: str,
    team_b: str,
    key: str,
) -> Optional[str]:
    """
    Convenience: return entry.meta[key] if it looks like an ISO date (YYYY-MM-DD).
    Returns None if missing or invalid.
    """
    entry = get_relationship_entry(trade_memory, team_a=team_a, team_b=team_b)
    if not entry:
        return None
    meta = entry.get("meta")
    if not isinstance(meta, dict):
        return None
    v = meta.get(key)
    if not isinstance(v, str):
        return None
    s = v[:10]
    try:
        # Validate format
        date.fromisoformat(s)
        return s
    except Exception:
        return None
