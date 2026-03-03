# db_schema/fatigue.py
"""SQLite SSOT schema: fatigue state tables.

This module introduces `player_fatigue_state`, which stores *between-game* fatigue
state for each player as a pair of components:

- ST (short-term fatigue): fast-changing fatigue that recovers quickly with rest
- LT (long-term fatigue / wear): slow-changing fatigue that recovers slowly

The match engine models *in-game* fatigue separately (see matchengine_v3/sim_fatigue.py).
This table exists so the simulation can persist fatigue across games.

Notes
-----
* This table references `players` via foreign key, so this module must be applied
  after db_schema.core (which creates `players`).
* Values are stored in normalized 0..1 range.
* `last_date` is stored as a YYYY-MM-DD ISO date string (or NULL if unknown).
"""

from __future__ import annotations


def ddl(*, now: str, schema_version: str) -> str:  # noqa: ARG001
    """Return DDL SQL for fatigue tables (as a single executescript string)."""

    # We include basic CHECK constraints to catch corrupted writes early.
    # SQLite enforces CHECK constraints.
    return f"""
                CREATE TABLE IF NOT EXISTS player_fatigue_state (
                    player_id TEXT PRIMARY KEY,
                    st REAL NOT NULL DEFAULT 0.0 CHECK (st >= 0.0 AND st <= 1.0),
                    lt REAL NOT NULL DEFAULT 0.0 CHECK (lt >= 0.0 AND lt <= 1.0),
                    last_date TEXT CHECK (last_date IS NULL OR length(last_date) = 10),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_player_fatigue_last_date
                    ON player_fatigue_state(last_date);
"""
