from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Collection, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence

DEFAULT_CRITICAL_BODY_PARTS: frozenset[str] = frozenset(
    {"KNEE", "BACK", "SPINE", "ACHILLES", "FOOT", "HIP", "NECK"}
)


@dataclass(frozen=True, slots=True)
class _InjuryEventRow:
    player_id: str
    date_iso: str
    body_part: Optional[str]
    severity: Optional[int]
    out_until_date: Optional[str]
    returning_until_date: Optional[str]


@dataclass(frozen=True, slots=True)
class _CurrentStateRow:
    player_id: str
    status: str
    out_until_date: Optional[str]
    returning_until_date: Optional[str]
    body_part: Optional[str]
    severity: Optional[int]


def _to_date_iso(d: Any) -> Optional[str]:
    if d is None:
        return None
    s = str(d).strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except Exception:
        return None


def _date_or_raise(d: str) -> date:
    try:
        return date.fromisoformat(str(d)[:10])
    except Exception as exc:  # pragma: no cover
        raise ValueError(f"invalid ISO date: {d!r}") from exc


def _safe_int(x: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if x is None:
            return default
        return int(x)
    except Exception:
        return default


def _normalize_status(status: Any) -> str:
    s = str(status or "").strip().upper()
    if s in {"HEALTHY", "OUT", "RETURNING"}:
        return s
    return "UNKNOWN"


def _normalize_body_part(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip().upper()
    return s if s else None


def _days_between(start_iso: str, end_iso: str) -> int:
    start = _date_or_raise(start_iso)
    end = _date_or_raise(end_iso)
    out = (end - start).days
    return out if out > 0 else 0


def _overlap_days(start_a_iso: str, end_a_iso: str, start_b_iso: str, end_b_iso: str) -> int:
    a0 = _date_or_raise(start_a_iso)
    a1 = _date_or_raise(end_a_iso)
    b0 = _date_or_raise(start_b_iso)
    b1 = _date_or_raise(end_b_iso)
    lo = a0 if a0 >= b0 else b0
    hi = a1 if a1 <= b1 else b1
    days = (hi - lo).days
    return days if days > 0 else 0


def _default_payload(*, as_of_date_iso: str) -> Dict[str, Any]:
    return {
        "version": 1,
        "as_of_date": str(as_of_date_iso)[:10],
        "source": {
            "current": "none",
            "history": "none",
        },
        "current": {
            "status": "UNKNOWN",
            "is_out": False,
            "is_returning": False,
            "days_to_return": 0,
            "body_part": None,
            "severity": None,
            "out_until_date": None,
            "returning_until_date": None,
        },
        "history": {
            "window_days": 365,
            "recent_count_30d": 0,
            "recent_count_180d": 0,
            "recent_count_365d": 0,
            "critical_count_365d": 0,
            "same_part_repeat_365d_max": 0,
            "same_part_counts_365d": {},
            "avg_severity_365d": 0.0,
            "weighted_severity_365d": 0.0,
            "last_injury_date": None,
            "days_since_last_injury": None,
        },
        "health_credit_inputs": {
            "availability_rate_365d": 1.0,
            "healthy_days_365d": 365,
            "out_days_365d": 0,
            "returning_days_365d": 0,
        },
        "flags": {
            "current_missing": True,
            "history_missing": True,
            "fallback_used": True,
        },
    }


def _iter_rows(rows: Iterable[Any], *, columns: Sequence[str]) -> Iterable[Mapping[str, Any]]:
    for row in rows:
        if isinstance(row, Mapping):
            yield row
            continue
        if hasattr(row, "keys"):
            keys = list(row.keys())  # sqlite3.Row
            yield {k: row[k] for k in keys}
            continue
        if isinstance(row, (tuple, list)):
            if len(row) != len(columns):
                continue
            yield {str(columns[i]): row[i] for i in range(len(columns))}
            continue


def _query_current_states(conn: Any, player_ids: Sequence[str]) -> Dict[str, _CurrentStateRow]:
    if not player_ids:
        return {}
    placeholders = ",".join(["?"] * len(player_ids))
    columns = (
        "player_id",
        "status",
        "out_until_date",
        "returning_until_date",
        "body_part",
        "severity",
    )
    rows = conn.execute(
        f"""
        SELECT
            player_id,
            status,
            out_until_date,
            returning_until_date,
            body_part,
            severity
        FROM player_injury_state
        WHERE player_id IN ({placeholders});
        """,
        list(player_ids),
    ).fetchall()

    out: Dict[str, _CurrentStateRow] = {}
    for r in _iter_rows(rows, columns=columns):
        pid = str(r.get("player_id") or "")
        if not pid:
            continue
        out[pid] = _CurrentStateRow(
            player_id=pid,
            status=_normalize_status(r.get("status")),
            out_until_date=_to_date_iso(r.get("out_until_date")),
            returning_until_date=_to_date_iso(r.get("returning_until_date")),
            body_part=_normalize_body_part(r.get("body_part")),
            severity=_safe_int(r.get("severity"), None),
        )
    return out


def _query_history_events(
    conn: Any,
    player_ids: Sequence[str],
    *,
    start_date_iso: str,
    end_date_iso: str,
) -> Dict[str, List[_InjuryEventRow]]:
    if not player_ids:
        return {}
    placeholders = ",".join(["?"] * len(player_ids))
    columns = (
        "player_id",
        "date",
        "body_part",
        "severity",
        "out_until_date",
        "returning_until_date",
    )
    rows = conn.execute(
        f"""
        SELECT
            player_id,
            date,
            body_part,
            severity,
            out_until_date,
            returning_until_date
        FROM injury_events
        WHERE player_id IN ({placeholders})
          AND date >= ?
          AND date <= ?;
        """,
        [*list(player_ids), str(start_date_iso)[:10], str(end_date_iso)[:10]],
    ).fetchall()

    out: Dict[str, List[_InjuryEventRow]] = {}
    for r in _iter_rows(rows, columns=columns):
        pid = str(r.get("player_id") or "")
        d = _to_date_iso(r.get("date"))
        if not pid or not d:
            continue
        out.setdefault(pid, []).append(
            _InjuryEventRow(
                player_id=pid,
                date_iso=d,
                body_part=_normalize_body_part(r.get("body_part")),
                severity=_safe_int(r.get("severity"), None),
                out_until_date=_to_date_iso(r.get("out_until_date")),
                returning_until_date=_to_date_iso(r.get("returning_until_date")),
            )
        )
    return out


def _apply_current_payload(payload: MutableMapping[str, Any], row: _CurrentStateRow, *, as_of_date_iso: str) -> None:
    status = row.status
    out_until = row.out_until_date
    returning_until = row.returning_until_date
    days_to_return = 0
    if status == "OUT" and out_until:
        days_to_return = _days_between(as_of_date_iso, out_until)

    payload["source"]["current"] = "player_injury_state"
    payload["current"] = {
        "status": status,
        "is_out": status == "OUT",
        "is_returning": status == "RETURNING",
        "days_to_return": int(days_to_return),
        "body_part": row.body_part,
        "severity": row.severity,
        "out_until_date": out_until,
        "returning_until_date": returning_until,
    }
    payload["flags"]["current_missing"] = False


def _severity_recency_weight(*, as_of_date_iso: str, event_date_iso: str) -> float:
    # deterministic simple exponential decay with half-life ~180 days.
    d = _days_between(event_date_iso, as_of_date_iso)
    if d <= 0:
        return 1.0
    return 0.5 ** (float(d) / 180.0)


def _apply_history_payload(
    payload: MutableMapping[str, Any],
    events: Sequence[_InjuryEventRow],
    *,
    as_of_date_iso: str,
    lookback_days: int,
    critical_body_parts: Collection[str],
) -> None:
    window_days = max(int(lookback_days), 1)
    p30 = _date_or_raise(as_of_date_iso) - timedelta(days=30)
    p180 = _date_or_raise(as_of_date_iso) - timedelta(days=180)
    p365 = _date_or_raise(as_of_date_iso) - timedelta(days=365)

    recent_30 = 0
    recent_180 = 0
    recent_365 = 0
    critical_365 = 0
    counts_part: Counter[str] = Counter()
    sev_sum = 0.0
    sev_cnt = 0
    weighted_num = 0.0
    weighted_den = 0.0
    last_date: Optional[date] = None

    window_start = (_date_or_raise(as_of_date_iso) - timedelta(days=window_days)).isoformat()
    window_end_excl = (_date_or_raise(as_of_date_iso) + timedelta(days=1)).isoformat()

    out_days = 0
    ret_days = 0

    for ev in events:
        d = _date_or_raise(ev.date_iso)
        if d >= p30:
            recent_30 += 1
        if d >= p180:
            recent_180 += 1
        if d >= p365:
            recent_365 += 1
            bp = ev.body_part
            if bp:
                counts_part[bp] += 1
                if bp in critical_body_parts:
                    critical_365 += 1

            sev = float(ev.severity) if ev.severity is not None else 0.0
            sev_sum += sev
            sev_cnt += 1
            w = _severity_recency_weight(as_of_date_iso=as_of_date_iso, event_date_iso=ev.date_iso)
            weighted_num += (sev * w)
            weighted_den += w

            if last_date is None or d > last_date:
                last_date = d

            if ev.out_until_date:
                out_days += _overlap_days(
                    window_start,
                    window_end_excl,
                    ev.date_iso,
                    ev.out_until_date,
                )
                if ev.returning_until_date:
                    ret_days += _overlap_days(
                        window_start,
                        window_end_excl,
                        ev.out_until_date,
                        ev.returning_until_date,
                    )

    same_part_repeat_max = max(counts_part.values()) if counts_part else 0
    avg_sev = (sev_sum / float(sev_cnt)) if sev_cnt > 0 else 0.0
    weighted_sev = (weighted_num / weighted_den) if weighted_den > 0 else 0.0

    days_since_last: Optional[int] = None
    last_date_iso: Optional[str] = None
    if last_date is not None:
        last_date_iso = last_date.isoformat()
        days_since_last = _days_between(last_date_iso, as_of_date_iso)

    out_days = max(0, int(out_days))
    ret_days = max(0, int(ret_days))
    unavailable = out_days + ret_days
    healthy_days = max(0, window_days - unavailable)
    availability_rate = max(0.0, min(1.0, healthy_days / float(window_days)))

    payload["source"]["history"] = "injury_events"
    payload["history"] = {
        "window_days": int(window_days),
        "recent_count_30d": int(recent_30),
        "recent_count_180d": int(recent_180),
        "recent_count_365d": int(recent_365),
        "critical_count_365d": int(critical_365),
        "same_part_repeat_365d_max": int(same_part_repeat_max),
        "same_part_counts_365d": {k: int(v) for k, v in sorted(counts_part.items())},
        "avg_severity_365d": float(avg_sev),
        "weighted_severity_365d": float(weighted_sev),
        "last_injury_date": last_date_iso,
        "days_since_last_injury": days_since_last,
    }
    payload["health_credit_inputs"] = {
        "availability_rate_365d": float(availability_rate),
        "healthy_days_365d": int(healthy_days),
        "out_days_365d": int(out_days),
        "returning_days_365d": int(ret_days),
    }
    payload["flags"]["history_missing"] = False


def build_injury_payload_for_player(
    *,
    as_of_date_iso: str,
    current_row: Optional[Mapping[str, Any]],
    history_rows: Sequence[Mapping[str, Any]],
    lookback_days: int = 365,
    critical_body_parts: Optional[Collection[str]] = None,
) -> Dict[str, Any]:
    """Build valuation injury payload for a single player from raw rows.

    This utility keeps schema generation deterministic and shared by bulk builders.
    """
    as_of = _to_date_iso(as_of_date_iso)
    if not as_of:
        raise ValueError(f"as_of_date_iso must be ISO date, got {as_of_date_iso!r}")

    crit = set(DEFAULT_CRITICAL_BODY_PARTS)
    if critical_body_parts is not None:
        crit = {str(x).strip().upper() for x in critical_body_parts if str(x).strip()}

    payload = _default_payload(as_of_date_iso=as_of)
    payload["history"]["window_days"] = max(int(lookback_days), 1)

    if current_row:
        row = _CurrentStateRow(
            player_id=str(current_row.get("player_id") or ""),
            status=_normalize_status(current_row.get("status")),
            out_until_date=_to_date_iso(current_row.get("out_until_date")),
            returning_until_date=_to_date_iso(current_row.get("returning_until_date")),
            body_part=_normalize_body_part(current_row.get("body_part")),
            severity=_safe_int(current_row.get("severity"), None),
        )
        _apply_current_payload(payload, row, as_of_date_iso=as_of)

    events: List[_InjuryEventRow] = []
    for r in history_rows:
        d = _to_date_iso(r.get("date"))
        if not d:
            continue
        events.append(
            _InjuryEventRow(
                player_id=str(r.get("player_id") or ""),
                date_iso=d,
                body_part=_normalize_body_part(r.get("body_part")),
                severity=_safe_int(r.get("severity"), None),
                out_until_date=_to_date_iso(r.get("out_until_date")),
                returning_until_date=_to_date_iso(r.get("returning_until_date")),
            )
        )
    if events:
        _apply_history_payload(
            payload,
            events,
            as_of_date_iso=as_of,
            lookback_days=max(int(lookback_days), 1),
            critical_body_parts=crit,
        )

    payload["flags"]["fallback_used"] = bool(payload["flags"]["current_missing"] or payload["flags"]["history_missing"])
    return payload


def build_injury_payloads_for_players(
    *,
    conn: Any,
    player_ids: Sequence[str],
    as_of_date_iso: str,
    lookback_days: int = 365,
    critical_body_parts: Optional[Collection[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Build injury payloads for valuation in bulk.

    Contract:
    - Bulk query current state once from player_injury_state.
    - Bulk query history once from injury_events for the lookback window.
    - Return deterministic map for all requested player_ids.
    """
    as_of = _to_date_iso(as_of_date_iso)
    if not as_of:
        raise ValueError(f"as_of_date_iso must be ISO date, got {as_of_date_iso!r}")

    pids: List[str] = []
    seen = set()
    for pid in player_ids:
        p = str(pid or "")
        if not p or p in seen:
            continue
        seen.add(p)
        pids.append(p)

    if not pids:
        return {}

    crit = set(DEFAULT_CRITICAL_BODY_PARTS)
    if critical_body_parts is not None:
        crit = {str(x).strip().upper() for x in critical_body_parts if str(x).strip()}

    payloads: Dict[str, Dict[str, Any]] = {pid: _default_payload(as_of_date_iso=as_of) for pid in pids}
    for pid in pids:
        payloads[pid]["history"]["window_days"] = max(int(lookback_days), 1)

    current_rows: Dict[str, _CurrentStateRow] = {}
    history_rows: Dict[str, List[_InjuryEventRow]] = {}

    current_query_ok = True
    history_query_ok = True

    try:
        current_rows = _query_current_states(conn, pids)
    except Exception:
        current_query_ok = False
        current_rows = {}

    start_date = (_date_or_raise(as_of) - timedelta(days=max(int(lookback_days), 1))).isoformat()
    try:
        history_rows = _query_history_events(conn, pids, start_date_iso=start_date, end_date_iso=as_of)
    except Exception:
        history_query_ok = False
        history_rows = {}

    for pid in pids:
        payload = payloads[pid]
        if current_query_ok:
            row = current_rows.get(pid)
            if row is not None:
                _apply_current_payload(payload, row, as_of_date_iso=as_of)
            else:
                payload["current"]["status"] = "HEALTHY"
                payload["flags"]["current_missing"] = True
                payload["source"]["current"] = "player_injury_state"
        else:
            payload["flags"]["current_missing"] = True
            payload["source"]["current"] = "none"

        if history_query_ok:
            events = history_rows.get(pid) or []
            if events:
                _apply_history_payload(
                    payload,
                    events,
                    as_of_date_iso=as_of,
                    lookback_days=max(int(lookback_days), 1),
                    critical_body_parts=crit,
                )
            else:
                payload["flags"]["history_missing"] = True
                payload["source"]["history"] = "injury_events"
        else:
            payload["flags"]["history_missing"] = True
            payload["source"]["history"] = "none"

        payload["flags"]["fallback_used"] = bool(
            payload["flags"]["current_missing"] or payload["flags"]["history_missing"]
        )

    return payloads
