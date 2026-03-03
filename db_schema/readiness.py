# db_schema/readiness.py
"""SQLite SSOT schema: readiness (scheme familiarity + player sharpness).

This schema stores *between-game* readiness states.

Tables
------
- player_sharpness_state: player match sharpness (0..100) per season.
- team_scheme_familiarity_state: scheme familiarity (0..100) per team per season.

Notes
-----
* References `players` via foreign key, so apply after db_schema.core.
* Dates are stored as ISO strings (YYYY-MM-DD).
* season_year is part of the primary keys to avoid cross-season contamination.
"""

from __future__ import annotations


def ddl(*, now: str, schema_version: str) -> str:  # noqa: ARG001
    """Return DDL SQL for readiness tables (as a single executescript string)."""

    return f"""
                CREATE TABLE IF NOT EXISTS player_sharpness_state (
                    player_id TEXT NOT NULL,
                    season_year INTEGER NOT NULL,
                    sharpness REAL NOT NULL DEFAULT 50.0 CHECK (sharpness >= 0.0 AND sharpness <= 100.0),
                    last_date TEXT CHECK (last_date IS NULL OR length(last_date) = 10),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (player_id, season_year),
                    FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_player_sharpness_season_last_date
                    ON player_sharpness_state(season_year, last_date);

                CREATE TABLE IF NOT EXISTS team_scheme_familiarity_state (
                    team_id TEXT NOT NULL,
                    season_year INTEGER NOT NULL,
                    scheme_type TEXT NOT NULL CHECK (scheme_type IN ('offense', 'defense')),
                    scheme_key TEXT NOT NULL,
                    value REAL NOT NULL DEFAULT 50.0 CHECK (value >= 0.0 AND value <= 100.0),
                    last_date TEXT CHECK (last_date IS NULL OR length(last_date) = 10),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (team_id, season_year, scheme_type, scheme_key)
                );

                CREATE INDEX IF NOT EXISTS idx_team_scheme_familiarity_team_season
                    ON team_scheme_familiarity_state(team_id, season_year);

                CREATE INDEX IF NOT EXISTS idx_team_scheme_familiarity_team_type
                    ON team_scheme_familiarity_state(team_id, season_year, scheme_type);
"""
