# db_schema/retirement.py
"""SQLite SSOT schema: offseason retirement decisions/events."""

from __future__ import annotations


def ddl(*, now: str, schema_version: str) -> str:  # noqa: ARG001
    """Return DDL SQL for retirement tables (as a single executescript string)."""

    return """

                CREATE TABLE IF NOT EXISTS player_retirement_decisions (
                    season_year INTEGER NOT NULL,
                    player_id TEXT NOT NULL,

                    decision TEXT NOT NULL
                        CHECK(decision IN ('RETIRED', 'STAY')),
                    considered INTEGER NOT NULL DEFAULT 0
                        CHECK(considered IN (0,1)),

                    consideration_prob REAL NOT NULL DEFAULT 0.0,
                    retirement_prob REAL NOT NULL DEFAULT 0.0,
                    random_roll REAL NOT NULL DEFAULT 0.0,

                    age INTEGER,
                    team_id TEXT,
                    injury_status TEXT,

                    inputs_json TEXT NOT NULL DEFAULT '{}',
                    explanation_json TEXT NOT NULL DEFAULT '{}',

                    decided_at TEXT NOT NULL,
                    processed_at TEXT,
                    source TEXT NOT NULL DEFAULT 'offseason',

                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,

                    PRIMARY KEY (season_year, player_id),
                    FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_retirement_decisions_year_decision
                    ON player_retirement_decisions(season_year, decision);

                CREATE INDEX IF NOT EXISTS idx_retirement_decisions_player_year
                    ON player_retirement_decisions(player_id, season_year);


                CREATE TABLE IF NOT EXISTS retirement_events (
                    event_id TEXT PRIMARY KEY,
                    season_year INTEGER NOT NULL,
                    player_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_retirement_events_player_date
                    ON retirement_events(player_id, date);

                CREATE INDEX IF NOT EXISTS idx_retirement_events_year_date
                    ON retirement_events(season_year, date);

"""


def migrate(cur, *, ensure_columns) -> None:  # noqa: ARG001
    """No additive migrations yet."""
    return None

