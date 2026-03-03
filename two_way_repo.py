from __future__ import annotations

import sqlite3
from typing import Any, Dict, Iterable, Optional, Set

from schema import normalize_player_id, normalize_team_id


def _norm_pid(player_id: Any) -> str:
    return str(normalize_player_id(player_id, strict=False, allow_legacy_numeric=True))


def _norm_tid(team_id: Any) -> str:
    return str(normalize_team_id(team_id, strict=True)).upper()


def count_active_two_way_by_team(cur: sqlite3.Cursor, team_id: str) -> int:
    tid = _norm_tid(team_id)
    row = cur.execute(
        """
        SELECT COUNT(1) AS n
        FROM contracts
        WHERE team_id=?
          AND UPPER(COALESCE(contract_type,''))='TWO_WAY'
          AND UPPER(COALESCE(status,''))='ACTIVE'
          AND COALESCE(is_active, 0)=1;
        """,
        (tid,),
    ).fetchone()
    try:
        return int(row["n"] if row is not None else 0)
    except Exception:
        return int(row[0]) if row else 0


def is_player_on_active_two_way(cur: sqlite3.Cursor, player_id: str) -> bool:
    pid = _norm_pid(player_id)
    row = cur.execute(
        """
        SELECT 1
        FROM contracts
        WHERE player_id=?
          AND UPPER(COALESCE(contract_type,''))='TWO_WAY'
          AND UPPER(COALESCE(status,''))='ACTIVE'
          AND COALESCE(is_active, 0)=1
        LIMIT 1;
        """,
        (pid,),
    ).fetchone()
    return bool(row)


def get_active_two_way_players_by_team(cur: sqlite3.Cursor, team_id: str) -> Set[str]:
    tid = _norm_tid(team_id)
    rows = cur.execute(
        """
        SELECT player_id
        FROM contracts
        WHERE team_id=?
          AND UPPER(COALESCE(contract_type,''))='TWO_WAY'
          AND UPPER(COALESCE(status,''))='ACTIVE'
          AND COALESCE(is_active, 0)=1;
        """,
        (tid,),
    ).fetchall()
    out: Set[str] = set()
    for r in rows:
        try:
            out.add(_norm_pid(r["player_id"]))
        except Exception:
            try:
                out.add(_norm_pid(r[0]))
            except Exception:
                continue
    return out


def get_two_way_games_used(cur: sqlite3.Cursor, player_id: str, season_year: int) -> int:
    pid = _norm_pid(player_id)
    y = int(season_year)
    row = cur.execute(
        "SELECT COUNT(1) AS n FROM two_way_appearances WHERE player_id=? AND season_year=?;",
        (pid, y),
    ).fetchone()
    try:
        return int(row["n"] if row is not None else 0)
    except Exception:
        return int(row[0]) if row else 0


def record_two_way_appearance(
    cur: sqlite3.Cursor,
    *,
    player_id: str,
    season_year: int,
    game_id: str,
    phase: str,
    now_iso: str,
) -> bool:
    pid = _norm_pid(player_id)
    y = int(season_year)
    gid = str(game_id)
    ph = str(phase)
    before = cur.execute(
        "SELECT 1 FROM two_way_appearances WHERE player_id=? AND game_id=? LIMIT 1;",
        (pid, gid),
    ).fetchone()
    if before:
        return False
    cur.execute(
        """
        INSERT INTO two_way_appearances(player_id, season_year, game_id, phase, created_at)
        VALUES (?, ?, ?, ?, ?);
        """,
        (pid, y, gid, ph, str(now_iso)),
    )
    return True
