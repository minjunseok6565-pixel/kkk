# db_schema/training.py
"""SQLite SSOT schema: training + growth tables.

This module introduces:
  - team_training_plans
  - player_training_plans
  - player_growth_profile

Notes
-----
* These tables are intentionally separated from core tables.
* They reference `players` (via player_id foreign key), so this module must be
  applied *after* db_schema.core.
"""

from __future__ import annotations


def ddl(*, now: str, schema_version: str) -> str:  # noqa: ARG001
    """Return DDL SQL for training tables (as a single executescript string)."""

    # IMPORTANT:
    # - We keep plan_json as TEXT SSOT so we can evolve the plan schema without
    #   requiring migrations.
    # - For player_training_plans, `is_user_set` is used to distinguish defaults
    #   vs explicit user choice.
    return f"""

                CREATE TABLE IF NOT EXISTS team_training_plans (
                    team_id TEXT NOT NULL,
                    season_year INTEGER NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (team_id, season_year)
                );

                CREATE INDEX IF NOT EXISTS idx_team_training_plans_season
                    ON team_training_plans(season_year);

                CREATE TABLE IF NOT EXISTS player_training_plans (
                    player_id TEXT NOT NULL,
                    season_year INTEGER NOT NULL,
                    plan_json TEXT NOT NULL,
                    is_user_set INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (player_id, season_year),
                    FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_player_training_plans_season
                    ON player_training_plans(season_year);

                CREATE TABLE IF NOT EXISTS player_growth_profile (
                    player_id TEXT PRIMARY KEY,
                    ceiling_proxy REAL NOT NULL,
                    peak_age REAL NOT NULL,
                    decline_start_age REAL NOT NULL,
                    late_decline_age REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE
                );
"""
