"""Two-way contract support tables.

SSOT notes
----------
- Contract identity/type still lives in `contracts` (contract_type='TWO_WAY').
- This module stores runtime usage constraints only (50-game limit tracking).
"""

from __future__ import annotations

import sqlite3
from typing import Callable, Mapping

EnsureColumnsFn = Callable[[sqlite3.Cursor, str, Mapping[str, str]], None]


def ddl(*, now: str, schema_version: str) -> str:
    return """
        CREATE TABLE IF NOT EXISTS two_way_appearances (
            player_id TEXT NOT NULL,
            season_year INTEGER NOT NULL,
            game_id TEXT NOT NULL,
            phase TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(player_id, game_id)
        );

        CREATE INDEX IF NOT EXISTS idx_two_way_appearances_season
            ON two_way_appearances(season_year);
        CREATE INDEX IF NOT EXISTS idx_two_way_appearances_player_season
            ON two_way_appearances(player_id, season_year);
    """


def migrate(cur: sqlite3.Cursor, *, ensure_columns: EnsureColumnsFn) -> None:
    # No post-DDL migrations required yet.
    return

