from __future__ import annotations

"""DB access layer for the injury subsystem.

This module is intentionally *pure DB I/O*:
- no imports from matchengine/training (avoid circular dependencies)
- no business logic besides defensive normalization

All dates are stored as ISO YYYY-MM-DD strings.
"""

import datetime as _dt
import json
import sqlite3
from typing import Any, Dict, Mapping, Optional


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def _json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _norm_date_iso(value: Any) -> Optional[str]:
    """Normalize date-like value to YYYY-MM-DD, else None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s[:10]
    try:
        _dt.date.fromisoformat(s)
    except Exception:
        return None
    return s


def get_player_injury_states(
    cur: sqlite3.Cursor,
    player_ids: list[str],
) -> Dict[str, Dict[str, Any]]:
    """Bulk-load player injury states.

    Returns:
        dict[player_id] -> state dict.

    Notes:
    - Missing rows are omitted.
    - JSON fields are decoded into dicts.
    """
    ids = [str(pid) for pid in player_ids if str(pid)]
    seen: set[str] = set()
    uniq: list[str] = []
    for pid in ids:
        if pid in seen:
            continue
        seen.add(pid)
        uniq.append(pid)
    if not uniq:
        return {}

    placeholders = ",".join(["?"] * len(uniq))
    rows = cur.execute(
        f"""
        SELECT
            player_id,
            team_id,
            status,
            injury_id,
            start_date,
            out_until_date,
            returning_until_date,
            body_part,
            injury_type,
            severity,
            temp_debuff_json,
            perm_drop_json,
            reinjury_count_json,
            last_processed_date
        FROM player_injury_state
        WHERE player_id IN ({placeholders});
        """,
        uniq,
    ).fetchall()

    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        pid = str(r[0])
        out[pid] = {
            "player_id": pid,
            "team_id": str(r[1]) if r[1] is not None else None,
            "status": str(r[2]) if r[2] is not None else "HEALTHY",
            "injury_id": str(r[3]) if r[3] is not None else None,
            "start_date": _norm_date_iso(r[4]),
            "out_until_date": _norm_date_iso(r[5]),
            "returning_until_date": _norm_date_iso(r[6]),
            "body_part": str(r[7]) if r[7] is not None else None,
            "injury_type": str(r[8]) if r[8] is not None else None,
            "severity": int(r[9] or 0) if r[9] is not None else 0,
            "temp_debuff": _json_loads(r[10], default={}) or {},
            "perm_drop": _json_loads(r[11], default={}) or {},
            "reinjury_count": _json_loads(r[12], default={}) or {},
            "last_processed_date": _norm_date_iso(r[13]),
        }
    return out


def upsert_player_injury_states(
    cur: sqlite3.Cursor,
    states_by_pid: Mapping[str, Mapping[str, Any]],
    *,
    now: str,
) -> None:
    """Upsert injury states.

    Args:
        states_by_pid: mapping[player_id] -> state dict.
        now: UTC-like timestamp string (in-game time)

    Commercial safety:
    - invalid entries are skipped silently.
    """
    if not states_by_pid:
        return

    rows: list[tuple[Any, ...]] = []
    for pid, st in states_by_pid.items():
        pid_s = str(pid)
        if not pid_s:
            continue
        try:
            team_id = st.get("team_id")
            status = str(st.get("status") or "HEALTHY")
            injury_id = st.get("injury_id")
            start_date = _norm_date_iso(st.get("start_date"))
            out_until = _norm_date_iso(st.get("out_until_date"))
            returning_until = _norm_date_iso(st.get("returning_until_date"))
            body_part = st.get("body_part")
            injury_type = st.get("injury_type")
            severity = int(st.get("severity") or 0)
            temp_debuff = st.get("temp_debuff") or {}
            perm_drop = st.get("perm_drop") or {}
            reinjury_count = st.get("reinjury_count") or {}
            last_processed = _norm_date_iso(st.get("last_processed_date"))
        except Exception:
            continue

        rows.append(
            (
                pid_s,
                str(team_id).upper() if team_id is not None else None,
                status,
                str(injury_id) if injury_id is not None else None,
                start_date,
                out_until,
                returning_until,
                str(body_part).upper() if body_part is not None else None,
                str(injury_type).upper() if injury_type is not None else None,
                int(severity),
                _json_dumps(dict(temp_debuff)),
                _json_dumps(dict(perm_drop)),
                _json_dumps(dict(reinjury_count)),
                last_processed,
                str(now),
                str(now),
            )
        )

    if not rows:
        return

    cur.executemany(
        """
        INSERT INTO player_injury_state(
            player_id,
            team_id,
            status,
            injury_id,
            start_date,
            out_until_date,
            returning_until_date,
            body_part,
            injury_type,
            severity,
            temp_debuff_json,
            perm_drop_json,
            reinjury_count_json,
            last_processed_date,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_id) DO UPDATE SET
            team_id=excluded.team_id,
            status=excluded.status,
            injury_id=excluded.injury_id,
            start_date=excluded.start_date,
            out_until_date=excluded.out_until_date,
            returning_until_date=excluded.returning_until_date,
            body_part=excluded.body_part,
            injury_type=excluded.injury_type,
            severity=excluded.severity,
            temp_debuff_json=excluded.temp_debuff_json,
            perm_drop_json=excluded.perm_drop_json,
            reinjury_count_json=excluded.reinjury_count_json,
            last_processed_date=excluded.last_processed_date,
            updated_at=excluded.updated_at;
        """,
        rows,
    )


def insert_injury_events(
    cur: sqlite3.Cursor,
    events: list[Mapping[str, Any]],
    *,
    now: str,
) -> None:
    """Insert injury events (append-only log).

    Args:
        events: list of dict rows matching injury_events columns.
        now: created_at timestamp string

    Invalid entries are skipped silently.
    """
    if not events:
        return

    rows: list[tuple[Any, ...]] = []
    for e in events:
        try:
            injury_id = str(e.get("injury_id") or "")
            if not injury_id:
                continue
            player_id = str(e.get("player_id") or "")
            team_id = str(e.get("team_id") or "")
            if not player_id or not team_id:
                continue
            season_year = int(e.get("season_year") or 0)
            date_iso = _norm_date_iso(e.get("date"))
            if not date_iso:
                continue
            context = str(e.get("context") or "")
            game_id = e.get("game_id")
            quarter = e.get("quarter")
            clock_sec = e.get("clock_sec")
            body_part = str(e.get("body_part") or "")
            injury_type = str(e.get("injury_type") or "")
            severity = int(e.get("severity") or 1)
            duration_days = int(e.get("duration_days") or 0)
            out_until = _norm_date_iso(e.get("out_until_date"))
            returning_days = int(e.get("returning_days") or 0)
            returning_until = _norm_date_iso(e.get("returning_until_date"))
            temp_debuff = e.get("temp_debuff") or {}
            perm_drop = e.get("perm_drop") or {}
        except Exception:
            continue

        if not out_until or not returning_until:
            continue

        rows.append(
            (
                injury_id,
                player_id,
                team_id.upper(),
                int(season_year),
                date_iso,
                context,
                str(game_id) if game_id is not None else None,
                int(quarter) if quarter is not None else None,
                int(clock_sec) if clock_sec is not None else None,
                body_part,
                injury_type,
                int(severity),
                int(duration_days),
                out_until,
                int(returning_days),
                returning_until,
                _json_dumps(dict(temp_debuff)),
                _json_dumps(dict(perm_drop)),
                str(now),
            )
        )

    if not rows:
        return

    cur.executemany(
        """
        INSERT OR IGNORE INTO injury_events(
            injury_id,
            player_id,
            team_id,
            season_year,
            date,
            context,
            game_id,
            quarter,
            clock_sec,
            body_part,
            injury_type,
            severity,
            duration_days,
            out_until_date,
            returning_days,
            returning_until_date,
            temp_debuff_json,
            perm_drop_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        rows,
    )


def get_overlapping_injury_events(
    cur: sqlite3.Cursor,
    player_ids: list[str],
    *,
    start_date: str,
    end_date: str,
) -> list[Dict[str, Any]]:
    """Return injury events that overlap [start_date, end_date) for given players.

    Overlap condition:
        date < end_date AND out_until_date > start_date
    """
    start_iso = _norm_date_iso(start_date) or str(start_date)[:10]
    end_iso = _norm_date_iso(end_date) or str(end_date)[:10]

    ids = [str(pid) for pid in player_ids if str(pid)]
    seen: set[str] = set()
    uniq: list[str] = []
    for pid in ids:
        if pid in seen:
            continue
        seen.add(pid)
        uniq.append(pid)
    if not uniq:
        return []

    placeholders = ",".join(["?"] * len(uniq))
    rows = cur.execute(
        f"""
        SELECT
            injury_id, player_id, team_id, season_year, date, context, game_id,
            quarter, clock_sec, body_part, injury_type, severity, duration_days,
            out_until_date, returning_days, returning_until_date,
            temp_debuff_json, perm_drop_json, created_at
        FROM injury_events
        WHERE player_id IN ({placeholders})
          AND date < ?
          AND out_until_date > ?
        ORDER BY date ASC;
        """,
        uniq + [end_iso, start_iso],
    ).fetchall()

    out: list[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "injury_id": str(r[0]),
                "player_id": str(r[1]),
                "team_id": str(r[2]),
                "season_year": int(r[3] or 0),
                "date": _norm_date_iso(r[4]),
                "context": str(r[5]),
                "game_id": r[6],
                "quarter": r[7],
                "clock_sec": r[8],
                "body_part": str(r[9]),
                "injury_type": str(r[10]),
                "severity": int(r[11] or 0),
                "duration_days": int(r[12] or 0),
                "out_until_date": _norm_date_iso(r[13]),
                "returning_days": int(r[14] or 0),
                "returning_until_date": _norm_date_iso(r[15]),
                "temp_debuff": _json_loads(r[16], default={}) or {},
                "perm_drop": _json_loads(r[17], default={}) or {},
                "created_at": r[18],
            }
        )
    return out

