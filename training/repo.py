from __future__ import annotations

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


# -----------------------------------------------------------------------------
# Team plans
# -----------------------------------------------------------------------------


def get_team_training_plan(
    cur: sqlite3.Cursor,
    *,
    team_id: str,
    season_year: int,
) -> Optional[Dict[str, Any]]:
    row = cur.execute(
        "SELECT plan_json FROM team_training_plans WHERE team_id=? AND season_year=?;",
        (str(team_id).upper(), int(season_year)),
    ).fetchone()
    if not row:
        return None
    return _json_loads(row[0], default=None)


def upsert_team_training_plan(
    cur: sqlite3.Cursor,
    *,
    team_id: str,
    season_year: int,
    plan: Mapping[str, Any],
    now: str,
) -> None:
    cur.execute(
        """
        INSERT INTO team_training_plans(team_id, season_year, plan_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(team_id, season_year) DO UPDATE SET
            plan_json=excluded.plan_json,
            updated_at=excluded.updated_at;
        """,
        (str(team_id).upper(), int(season_year), _json_dumps(dict(plan)), str(now), str(now)),
    )


def list_team_training_plans_for_season(cur: sqlite3.Cursor, *, season_year: int) -> Dict[str, Dict[str, Any]]:
    rows = cur.execute(
        "SELECT team_id, plan_json FROM team_training_plans WHERE season_year=?;",
        (int(season_year),),
    ).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        tid = str(r[0]).upper()
        out[tid] = _json_loads(r[1], default={}) or {}
    return out


# -----------------------------------------------------------------------------
# Player plans
# -----------------------------------------------------------------------------


def get_player_training_plan(
    cur: sqlite3.Cursor,
    *,
    player_id: str,
    season_year: int,
) -> Tuple[Optional[Dict[str, Any]], bool]:
    row = cur.execute(
        "SELECT plan_json, is_user_set FROM player_training_plans WHERE player_id=? AND season_year=?;",
        (str(player_id), int(season_year)),
    ).fetchone()
    if not row:
        return (None, False)
    plan = _json_loads(row[0], default=None)
    is_user_set = bool(int(row[1] or 0))
    return (plan, is_user_set)


def upsert_player_training_plan(
    cur: sqlite3.Cursor,
    *,
    player_id: str,
    season_year: int,
    plan: Mapping[str, Any],
    now: str,
    is_user_set: bool = True,
) -> None:
    cur.execute(
        """
        INSERT INTO player_training_plans(player_id, season_year, plan_json, is_user_set, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_id, season_year) DO UPDATE SET
            plan_json=excluded.plan_json,
            is_user_set=excluded.is_user_set,
            updated_at=excluded.updated_at;
        """,
        (
            str(player_id),
            int(season_year),
            _json_dumps(dict(plan)),
            1 if is_user_set else 0,
            str(now),
            str(now),
        ),
    )


def list_player_training_plans_for_season(cur: sqlite3.Cursor, *, season_year: int) -> Dict[str, Dict[str, Any]]:
    rows = cur.execute(
        "SELECT player_id, plan_json FROM player_training_plans WHERE season_year=?;",
        (int(season_year),),
    ).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        pid = str(r[0])
        out[pid] = _json_loads(r[1], default={}) or {}
    return out


# -----------------------------------------------------------------------------
# Growth profile
# -----------------------------------------------------------------------------


def get_growth_profile(cur: sqlite3.Cursor, *, player_id: str) -> Optional[Dict[str, Any]]:
    row = cur.execute(
        """
        SELECT player_id, ceiling_proxy, peak_age, decline_start_age, late_decline_age
        FROM player_growth_profile
        WHERE player_id=?;
        """,
        (str(player_id),),
    ).fetchone()
    if not row:
        return None
    return {
        "player_id": str(row[0]),
        "ceiling_proxy": float(row[1]),
        "peak_age": float(row[2]),
        "decline_start_age": float(row[3]),
        "late_decline_age": float(row[4]),
    }


def upsert_growth_profile(cur: sqlite3.Cursor, *, profile: Mapping[str, Any], now: str) -> None:
    pid = str(profile.get("player_id") or "")
    if not pid:
        raise ValueError("profile.player_id required")
    cur.execute(
        """
        INSERT INTO player_growth_profile(
            player_id, ceiling_proxy, peak_age, decline_start_age, late_decline_age, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_id) DO UPDATE SET
            ceiling_proxy=excluded.ceiling_proxy,
            peak_age=excluded.peak_age,
            decline_start_age=excluded.decline_start_age,
            late_decline_age=excluded.late_decline_age,
            updated_at=excluded.updated_at;
        """,
        (
            pid,
            float(profile.get("ceiling_proxy") or 0.0),
            float(profile.get("peak_age") or 0.0),
            float(profile.get("decline_start_age") or 0.0),
            float(profile.get("late_decline_age") or 0.0),
            str(now),
            str(now),
        ),
    )


def list_growth_profiles(cur: sqlite3.Cursor) -> Dict[str, Dict[str, Any]]:
    rows = cur.execute(
        "SELECT player_id, ceiling_proxy, peak_age, decline_start_age, late_decline_age FROM player_growth_profile;"
    ).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        out[str(r[0])] = {
            "player_id": str(r[0]),
            "ceiling_proxy": float(r[1]),
            "peak_age": float(r[2]),
            "decline_start_age": float(r[3]),
            "late_decline_age": float(r[4]),
        }
    return out
