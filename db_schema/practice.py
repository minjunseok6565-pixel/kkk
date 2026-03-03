# db_schema/practice.py
"""SQLite SSOT schema: team practice (tactical + conditioning sessions).

This schema is intentionally separate from `db_schema.training`.

Rationale
---------
The existing training subsystem (team_training_plans / player_training_plans)
primarily models *growth* (monthly ticks + offseason). Historically, team
training intensity was also used as a proxy for between-game practice load.

The practice subsystem introduced here is meant to model *between-game* team
sessions (tactical installs, film, scrimmage, recovery, rest) which will:
  - influence readiness (scheme familiarity + player sharpness)
  - influence fatigue recovery and training-day injury risk

Tables
------
- team_practice_plans: per (team, season) policy (AUTO/MANUAL) and defaults.
- team_practice_sessions: per (team, season, date) session JSON (user-set or AI).

Notes
-----
* team_id is a string key used throughout the project (no teams table).
* Dates are stored as ISO strings (YYYY-MM-DD).
* season_year is part of the primary keys to avoid cross-season contamination.
"""

from __future__ import annotations


def ddl(*, now: str, schema_version: str) -> str:  # noqa: ARG001
    """Return DDL SQL for practice tables (as a single executescript string)."""

    return f"""
                CREATE TABLE IF NOT EXISTS team_practice_plans (
                    team_id TEXT NOT NULL,
                    season_year INTEGER NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (team_id, season_year)
                );

                CREATE TABLE IF NOT EXISTS team_practice_sessions (
                    team_id TEXT NOT NULL,
                    season_year INTEGER NOT NULL,
                    date_iso TEXT NOT NULL CHECK (length(date_iso) = 10),
                    session_json TEXT NOT NULL,
                    is_user_set INTEGER NOT NULL DEFAULT 0 CHECK (is_user_set IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (team_id, season_year, date_iso)
                );

                CREATE INDEX IF NOT EXISTS idx_practice_sessions_team_season_date
                    ON team_practice_sessions(team_id, season_year, date_iso);
"""