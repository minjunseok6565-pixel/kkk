# db_schema/injury.py
"""SQLite SSOT schema: injuries.

This module introduces:
  - player_injury_state (current availability + returning debuffs)
  - injury_events (append-only log of injuries)

Notes
-----
* These tables reference `players` via player_id foreign key, so this module must
  be applied after db_schema.core.
* Dates are stored as ISO strings (YYYY-MM-DD).
* injury_events is append-only and keyed by injury_id for idempotency.
"""

from __future__ import annotations


def ddl(*, now: str, schema_version: str) -> str:  # noqa: ARG001
    """Return DDL SQL for injury tables (as a single executescript string)."""

    return f"""

                CREATE TABLE IF NOT EXISTS player_injury_state (
                    player_id TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'HEALTHY'
                        CHECK(status IN ('HEALTHY', 'OUT', 'RETURNING')),

                    injury_id TEXT,
                    start_date TEXT,
                    out_until_date TEXT,
                    returning_until_date TEXT,

                    body_part TEXT,
                    injury_type TEXT,
                    severity INTEGER NOT NULL DEFAULT 0,

                    temp_debuff_json TEXT NOT NULL DEFAULT '{{}}',
                    perm_drop_json TEXT NOT NULL DEFAULT '{{}}',
                    reinjury_count_json TEXT NOT NULL DEFAULT '{{}}',

                    last_processed_date TEXT,

                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,

                    FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_player_injury_state_team_status
                    ON player_injury_state(team_id, status);

                CREATE INDEX IF NOT EXISTS idx_player_injury_state_out_until
                    ON player_injury_state(out_until_date);

                CREATE INDEX IF NOT EXISTS idx_player_injury_state_returning_until
                    ON player_injury_state(returning_until_date);


                CREATE TABLE IF NOT EXISTS injury_events (
                    injury_id TEXT PRIMARY KEY,
                    player_id TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    season_year INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    context TEXT NOT NULL CHECK(context IN ('game', 'training')),

                    game_id TEXT,
                    quarter INTEGER,
                    clock_sec INTEGER,

                    body_part TEXT NOT NULL,
                    injury_type TEXT NOT NULL,
                    severity INTEGER NOT NULL,

                    duration_days INTEGER NOT NULL,
                    out_until_date TEXT NOT NULL,

                    returning_days INTEGER NOT NULL,
                    returning_until_date TEXT NOT NULL,

                    temp_debuff_json TEXT NOT NULL DEFAULT '{{}}',
                    perm_drop_json TEXT NOT NULL DEFAULT '{{}}',

                    created_at TEXT NOT NULL,

                    FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_injury_events_player_date
                    ON injury_events(player_id, date);

                CREATE INDEX IF NOT EXISTS idx_injury_events_team_date
                    ON injury_events(team_id, date);

                CREATE INDEX IF NOT EXISTS idx_injury_events_game
                    ON injury_events(game_id);

                CREATE INDEX IF NOT EXISTS idx_injury_events_player_part_date
                    ON injury_events(player_id, body_part, date);

"""
