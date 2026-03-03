from __future__ import annotations

"""DB access layer for the readiness subsystem.

This module is intentionally *pure DB I/O*:
- no imports from matchengine or simulation code (avoid circular dependencies)
- no business logic besides defensive normalization

All dates are stored as ISO YYYY-MM-DD strings.
"""

import datetime as _dt
import sqlite3
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple


def _clamp100(x: float) -> float:
    try:
        v = float(x)
    except Exception:
        return 50.0
    if v < 0.0:
        return 0.0
    if v > 100.0:
        return 100.0
    return float(v)


def _norm_date_iso(value: Any) -> Optional[str]:
    """Normalize a date-like value to YYYY-MM-DD, or return None.

    We intentionally do not import `game_time` here to keep repo as a pure DB layer.
    """
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


def _uniq_strs(values: Iterable[Any]) -> list[str]:
    """De-duplicate while preserving deterministic order (small N; O(n^2) ok)."""
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        s = str(v)
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# Player sharpness state
# ---------------------------------------------------------------------------


def get_player_sharpness_states(
    cur: sqlite3.Cursor,
    player_ids: list[str],
    *,
    season_year: int,
) -> Dict[str, Dict[str, Any]]:
    """Bulk-load player sharpness states for a season.

    Returns:
        dict[player_id] = {"sharpness": float, "last_date": Optional[str]}

    Notes:
    - Missing rows are omitted.
    - Values are clamped to [0, 100] defensively.
    """
    uniq = _uniq_strs(player_ids)
    if not uniq:
        return {}

    placeholders = ",".join(["?"] * len(uniq))
    rows = cur.execute(
        f"""
        SELECT player_id, sharpness, last_date
        FROM player_sharpness_state
        WHERE season_year=? AND player_id IN ({placeholders});
        """,
        [int(season_year), *uniq],
    ).fetchall()

    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        pid = str(r[0])
        sharp = _clamp100(r[1] if r[1] is not None else 50.0)
        last_date = _norm_date_iso(r[2])
        out[pid] = {"sharpness": float(sharp), "last_date": last_date}
    return out


def upsert_player_sharpness_states(
    cur: sqlite3.Cursor,
    states_by_pid: Mapping[str, Mapping[str, Any]],
    *,
    season_year: int,
    now: str,
) -> None:
    """Upsert sharpness states (bulk) for a season.

    Args:
        states_by_pid: mapping[player_id] -> {"sharpness": float, "last_date": YYYY-MM-DD|None}
        season_year: season key to write
        now: in-game UTC-like timestamp string

    Commercial safety:
    - Invalid rows are skipped silently.
    - created_at is written only on INSERT; updated_at always updated.
    """
    if not states_by_pid:
        return

    rows: list[tuple[Any, ...]] = []
    sy = int(season_year)
    for pid, st in states_by_pid.items():
        pid_s = str(pid)
        if not pid_s:
            continue
        try:
            sharp = _clamp100(st.get("sharpness", 50.0))  # type: ignore[arg-type]
        except Exception:
            continue
        last_date = _norm_date_iso(st.get("last_date"))  # type: ignore[arg-type]
        rows.append((pid_s, sy, float(sharp), last_date, str(now), str(now)))

    if not rows:
        return

    cur.executemany(
        """
        INSERT INTO player_sharpness_state(player_id, season_year, sharpness, last_date, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_id, season_year) DO UPDATE SET
            sharpness=excluded.sharpness,
            last_date=excluded.last_date,
            updated_at=excluded.updated_at;
        """,
        rows,
    )


# ---------------------------------------------------------------------------
# Team scheme familiarity state
# ---------------------------------------------------------------------------


SchemeKey = Tuple[str, str]  # (scheme_type, scheme_key)


def get_team_scheme_familiarity_states(
    cur: sqlite3.Cursor,
    *,
    team_id: str,
    season_year: int,
    schemes: list[SchemeKey],
) -> Dict[SchemeKey, Dict[str, Any]]:
    """Load familiarity rows for specific schemes.

    Returns:
        dict[(scheme_type, scheme_key)] = {"value": float, "last_date": Optional[str]}

    Missing rows are omitted.
    """
    tid = str(team_id)
    if not tid:
        return {}
    uniq: list[SchemeKey] = []
    seen: set[SchemeKey] = set()
    for st, sk in schemes or []:
        key = (str(st), str(sk))
        if not key[0] or not key[1]:
            continue
        if key in seen:
            continue
        seen.add(key)
        uniq.append(key)
    if not uniq:
        return {}

    # SQLite doesn't support tuple IN for arbitrary length reliably, so we OR.
    where = " OR ".join(["(scheme_type=? AND scheme_key=?)"] * len(uniq))
    params: list[Any] = [str(tid), int(season_year)]
    for st, sk in uniq:
        params.extend([st, sk])

    rows = cur.execute(
        f"""
        SELECT scheme_type, scheme_key, value, last_date
        FROM team_scheme_familiarity_state
        WHERE team_id=? AND season_year=? AND ({where});
        """,
        params,
    ).fetchall()

    out: Dict[SchemeKey, Dict[str, Any]] = {}
    for r in rows:
        st = str(r[0])
        sk = str(r[1])
        val = _clamp100(r[2] if r[2] is not None else 50.0)
        last_date = _norm_date_iso(r[3])
        out[(st, sk)] = {"value": float(val), "last_date": last_date}
    return out


def list_team_scheme_familiarity_states(
    cur: sqlite3.Cursor,
    *,
    team_id: str,
    season_year: int,
    scheme_type: Optional[str] = None,
) -> Dict[SchemeKey, Dict[str, Any]]:
    """List all familiarity rows for a team in a season (optionally filtered by type)."""
    tid = str(team_id)
    if not tid:
        return {}

    params: list[Any] = [str(tid), int(season_year)]
    type_clause = ""
    if scheme_type is not None:
        type_clause = " AND scheme_type=?"
        params.append(str(scheme_type))

    rows = cur.execute(
        f"""
        SELECT scheme_type, scheme_key, value, last_date
        FROM team_scheme_familiarity_state
        WHERE team_id=? AND season_year=?{type_clause}
        ORDER BY scheme_type ASC, scheme_key ASC;
        """,
        params,
    ).fetchall()

    out: Dict[SchemeKey, Dict[str, Any]] = {}
    for r in rows:
        st = str(r[0])
        sk = str(r[1])
        val = _clamp100(r[2] if r[2] is not None else 50.0)
        last_date = _norm_date_iso(r[3])
        out[(st, sk)] = {"value": float(val), "last_date": last_date}
    return out


def upsert_team_scheme_familiarity_states(
    cur: sqlite3.Cursor,
    states: Mapping[SchemeKey, Mapping[str, Any]],
    *,
    team_id: str,
    season_year: int,
    now: str,
) -> None:
    """Upsert scheme familiarity rows (bulk).

    Args:
        states: mapping[(scheme_type, scheme_key)] -> {"value": float, "last_date": YYYY-MM-DD|None}
    """
    if not states:
        return
    tid = str(team_id)
    if not tid:
        return

    rows: list[tuple[Any, ...]] = []
    sy = int(season_year)

    for (st, sk), row in states.items():
        st_s = str(st)
        sk_s = str(sk)
        if not st_s or not sk_s:
            continue
        try:
            val = _clamp100(row.get("value", 50.0))  # type: ignore[arg-type]
        except Exception:
            continue
        last_date = _norm_date_iso(row.get("last_date"))  # type: ignore[arg-type]
        rows.append((tid, sy, st_s, sk_s, float(val), last_date, str(now), str(now)))

    if not rows:
        return

    cur.executemany(
        """
        INSERT INTO team_scheme_familiarity_state(
            team_id, season_year, scheme_type, scheme_key, value, last_date, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(team_id, season_year, scheme_type, scheme_key) DO UPDATE SET
            value=excluded.value,
            last_date=excluded.last_date,
            updated_at=excluded.updated_at;
        """,
        rows,
    )
