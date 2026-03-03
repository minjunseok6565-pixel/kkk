from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Any, Dict, Mapping, Optional


def _clamp01(x: float) -> float:
    try:
        v = float(x)
    except Exception:
        return 0.0
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


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


def get_player_fatigue_states(
    cur: sqlite3.Cursor,
    player_ids: list[str],
) -> Dict[str, Dict[str, Any]]:
    """Bulk-load player fatigue states.

    Returns:
        dict[player_id] = {"st": float, "lt": float, "last_date": Optional[str]}

    Notes:
    - Missing rows are simply omitted from the returned dict.
    - Values are clamped to [0, 1] defensively.
    """
    ids = [str(pid) for pid in player_ids if str(pid)]
    # De-duplicate but keep deterministic order (small lists; O(n^2) is fine).
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
        SELECT player_id, st, lt, last_date
        FROM player_fatigue_state
        WHERE player_id IN ({placeholders});
        """,
        uniq,
    ).fetchall()

    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        pid = str(r[0])
        st = _clamp01(r[1] if r[1] is not None else 0.0)
        lt = _clamp01(r[2] if r[2] is not None else 0.0)
        last_date = _norm_date_iso(r[3])
        out[pid] = {"st": float(st), "lt": float(lt), "last_date": last_date}
    return out


def upsert_player_fatigue_states(
    cur: sqlite3.Cursor,
    states_by_pid: Mapping[str, Mapping[str, Any]],
    *,
    now: str,
) -> None:
    """Upsert fatigue states (bulk).

    Args:
        states_by_pid: mapping[player_id] -> {"st": float, "lt": float, "last_date": YYYY-MM-DD|None}
        now: UTC-like timestamp string (SSOT: in-game time, not OS time)

    Notes:
    - Invalid rows are skipped silently (commercial safety). Callers should validate upstream.
    - `created_at` is written only on INSERT; `updated_at` is always written.
    """
    if not states_by_pid:
        return

    rows: list[tuple[Any, ...]] = []
    for pid, st in states_by_pid.items():
        pid_s = str(pid)
        if not pid_s:
            continue
        try:
            st_v = _clamp01(st.get("st", 0.0))  # type: ignore[arg-type]
            lt_v = _clamp01(st.get("lt", 0.0))  # type: ignore[arg-type]
        except Exception:
            continue
        last_date = _norm_date_iso(st.get("last_date"))  # type: ignore[arg-type]
        rows.append((pid_s, float(st_v), float(lt_v), last_date, str(now), str(now)))

    if not rows:
        return

    cur.executemany(
        """
        INSERT INTO player_fatigue_state(player_id, st, lt, last_date, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_id) DO UPDATE SET
            st=excluded.st,
            lt=excluded.lt,
            last_date=excluded.last_date,
            updated_at=excluded.updated_at;
        """,
        rows,
    )
