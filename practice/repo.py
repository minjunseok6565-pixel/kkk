from __future__ import annotations

"""DB access layer for the practice subsystem.

This module is intentionally *pure DB I/O*:
- no imports from matchengine/simulation (avoid circular dependencies)
- no business logic besides defensive JSON encoding/decoding

All dates are stored as ISO YYYY-MM-DD strings.
"""

import json
import sqlite3
from typing import Any, Dict, Mapping, Optional, Tuple


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


# ---------------------------------------------------------------------------
# Team practice plans
# ---------------------------------------------------------------------------


def get_team_practice_plan(
    cur: sqlite3.Cursor,
    *,
    team_id: str,
    season_year: int,
) -> Optional[Dict[str, Any]]:
    row = cur.execute(
        "SELECT plan_json FROM team_practice_plans WHERE team_id=? AND season_year=?;",
        (str(team_id).upper(), int(season_year)),
    ).fetchone()
    if not row:
        return None
    return _json_loads(row[0], default=None)


def upsert_team_practice_plan(
    cur: sqlite3.Cursor,
    *,
    team_id: str,
    season_year: int,
    plan: Mapping[str, Any],
    now: str,
) -> None:
    cur.execute(
        """
        INSERT INTO team_practice_plans(team_id, season_year, plan_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(team_id, season_year) DO UPDATE SET
          plan_json=excluded.plan_json,
          updated_at=excluded.updated_at;
        """,
        (str(team_id).upper(), int(season_year), _json_dumps(dict(plan)), str(now), str(now)),
    )


# ---------------------------------------------------------------------------
# Team practice sessions (daily)
# ---------------------------------------------------------------------------


def get_team_practice_session(
    cur: sqlite3.Cursor,
    *,
    team_id: str,
    season_year: int,
    date_iso: str,
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Return (session_json, is_user_set)."""
    row = cur.execute(
        """
        SELECT session_json, is_user_set
        FROM team_practice_sessions
        WHERE team_id=? AND season_year=? AND date_iso=?;
        """,
        (str(team_id).upper(), int(season_year), str(date_iso)[:10]),
    ).fetchone()
    if not row:
        return (None, False)
    return (_json_loads(row[0], default=None), bool(int(row[1] or 0)))


def upsert_team_practice_session(
    cur: sqlite3.Cursor,
    *,
    team_id: str,
    season_year: int,
    date_iso: str,
    session: Mapping[str, Any],
    now: str,
    is_user_set: bool,
) -> None:
    cur.execute(
        """
        INSERT INTO team_practice_sessions(team_id, season_year, date_iso, session_json, is_user_set, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(team_id, season_year, date_iso) DO UPDATE SET
          session_json=excluded.session_json,
          is_user_set=excluded.is_user_set,
          updated_at=excluded.updated_at;
        """,
        (
            str(team_id).upper(),
            int(season_year),
            str(date_iso)[:10],
            _json_dumps(dict(session)),
            1 if bool(is_user_set) else 0,
            str(now),
            str(now),
        ),
    )


def list_team_practice_sessions(
    cur: sqlite3.Cursor,
    *,
    team_id: str,
    season_year: int,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """List sessions for a team and season.

    Returns:
      dict[date_iso] = {"session": dict, "is_user_set": bool}
    """

    q = """
      SELECT date_iso, session_json, is_user_set
      FROM team_practice_sessions
      WHERE team_id=? AND season_year=?
    """
    params: list[Any] = [str(team_id).upper(), int(season_year)]
    if date_from:
        q += " AND date_iso>=?"
        params.append(str(date_from)[:10])
    if date_to:
        q += " AND date_iso<=?"
        params.append(str(date_to)[:10])
    q += " ORDER BY date_iso ASC;"

    rows = cur.execute(q, params).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        d = str(r[0])[:10]
        out[d] = {
            "session": _json_loads(r[1], default={}) or {},
            "is_user_set": bool(int(r[2] or 0)),
        }
    return out
