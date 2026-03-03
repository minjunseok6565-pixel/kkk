# db_schema/team_strategy.py
"""SQLite SSOT schema: team strategy.

This table is intentionally small and SSOT-friendly.
It allows the simulation to persist an explicit "front office direction" per team
and season, which other systems (agency, trades, scouting) can reference.

Notes
-----
* There is no teams table in this project; team_id is stored as TEXT.
* strategy is constrained to a small fixed set to keep downstream logic predictable.
"""

from __future__ import annotations


def ddl(*, now: str, schema_version: str) -> str:  # noqa: ARG001
    """Return DDL SQL for team_strategy table (as a single executescript string)."""

    return f"""
                CREATE TABLE IF NOT EXISTS team_strategy (
                    team_id TEXT NOT NULL,
                    season_year INTEGER NOT NULL,
                    strategy TEXT NOT NULL
                        CHECK(strategy IN ('WIN_NOW','BALANCED','DEVELOP','REBUILD')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(team_id, season_year)
                );

                CREATE INDEX IF NOT EXISTS idx_team_strategy_season
                    ON team_strategy(season_year);

                CREATE INDEX IF NOT EXISTS idx_team_strategy_team
                    ON team_strategy(team_id);
"""
