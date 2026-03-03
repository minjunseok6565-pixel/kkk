"""SQLite SSOT schema: scouting tables (in-season, user-driven).

Tables:
- scouting_scouts:
    Per-team scout staff (typically 6~7). Holds specialty + profile JSON
    (accuracy multipliers, bias rules, writing style tags, etc).

- scouting_assignments:
    User-driven assignment of a scout to a target player.
    Holds Kalman/Bayesian cumulative state in progress_json (mu/sigma per axis).

- scouting_reports:
    Monthly (end-of-month) scouting reports.
    Stores structured payload_json + optional LLM-generated report_text.
    Unique per (assignment_id, period_key) to guarantee idempotency.

Design notes:
- This subsystem must do nothing if there are no ACTIVE assignments.
- target_player_id namespace is shared across college/NBA players (player_id).
- Store player_snapshot_json on the report to remain readable even if college row is deleted after draft.
"""

from __future__ import annotations

import sqlite3
from typing import Callable, Mapping

# Signature compatible with LeagueRepo._ensure_table_columns(cur, table, columns)
EnsureColumnsFn = Callable[[sqlite3.Cursor, str, Mapping[str, str]], None]


def ddl(*, now: str, schema_version: str) -> str:
    """Return DDL SQL for scouting tables."""
    _ = (now, schema_version)
    return """

                -- ---------------------------------------------------------------------
                -- Scout staff (team-owned)
                -- ---------------------------------------------------------------------
                CREATE TABLE IF NOT EXISTS scouting_scouts (
                    scout_id TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    specialty_key TEXT NOT NULL,          -- e.g., ATHLETICS / SHOOTING / DEFENSE / PLAYMAKING / MEDICAL / CHARACTER / ANALYTICS
                    profile_json TEXT NOT NULL DEFAULT '{}', -- acc_mult, learn_rate, bias_rules, writing_style, rng_seed, etc
                    traits_json  TEXT NOT NULL DEFAULT '{}', -- optional: experience, reputation, etc
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_scouting_scouts_team
                    ON scouting_scouts(team_id);

                CREATE INDEX IF NOT EXISTS idx_scouting_scouts_team_specialty
                    ON scouting_scouts(team_id, specialty_key);


                -- ---------------------------------------------------------------------
                -- Assignments (user-driven)
                -- ---------------------------------------------------------------------
                CREATE TABLE IF NOT EXISTS scouting_assignments (
                    assignment_id TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    scout_id TEXT NOT NULL,
                    target_player_id TEXT NOT NULL,
                    target_kind TEXT NOT NULL DEFAULT 'COLLEGE', -- for future: NBA
                    assigned_date TEXT NOT NULL,                 -- YYYY-MM-DD (in-game date)
                    status TEXT NOT NULL DEFAULT 'ACTIVE',       -- ACTIVE / ENDED / PAUSED
                    ended_date TEXT,                             -- YYYY-MM-DD
                    progress_json TEXT NOT NULL DEFAULT '{}',     -- Kalman/Bayes cumulative state (mu/sigma/last_processed_period...)
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(scout_id) REFERENCES scouting_scouts(scout_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_scouting_assignments_team
                    ON scouting_assignments(team_id);

                CREATE INDEX IF NOT EXISTS idx_scouting_assignments_scout
                    ON scouting_assignments(team_id, scout_id);

                CREATE INDEX IF NOT EXISTS idx_scouting_assignments_target
                    ON scouting_assignments(team_id, target_player_id);

                CREATE INDEX IF NOT EXISTS idx_scouting_assignments_status
                    ON scouting_assignments(team_id, status);

                -- Optional but very useful: prevent a scout from having multiple ACTIVE assignments
                -- (SQLite supports partial indexes in modern versions)
                CREATE UNIQUE INDEX IF NOT EXISTS uq_scouting_active_assignment_per_scout
                    ON scouting_assignments(team_id, scout_id)
                    WHERE status='ACTIVE';


                -- ---------------------------------------------------------------------
                -- Reports (monthly, end-of-month)
                -- ---------------------------------------------------------------------
                CREATE TABLE IF NOT EXISTS scouting_reports (
                    report_id TEXT PRIMARY KEY,
                    assignment_id TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    scout_id TEXT NOT NULL,
                    target_player_id TEXT NOT NULL,
                    target_kind TEXT NOT NULL DEFAULT 'COLLEGE',
                    season_year INTEGER NOT NULL,                -- inferred from as_of_date (college season year)
                    period_key TEXT NOT NULL,                    -- YYYY-MM (month being reported)
                    as_of_date TEXT NOT NULL,                    -- YYYY-MM-DD (month end date)
                    days_covered INTEGER NOT NULL DEFAULT 0,      -- days observed in this month window
                    player_snapshot_json TEXT NOT NULL DEFAULT '{}', -- name/pos/ht/wt/school/class, etc
                    payload_json TEXT NOT NULL DEFAULT '{}',      -- structured report inputs (grades/confidence, mu/sigma summaries, risk tags)
                    report_text TEXT,                            -- LLM output (optional / cached)
                    status TEXT NOT NULL DEFAULT 'READY_STRUCT',  -- READY_STRUCT / READY_TEXT / FAILED_TEXT
                    llm_meta_json TEXT NOT NULL DEFAULT '{}',     -- model, prompt_version, tokens, error, etc
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(assignment_id) REFERENCES scouting_assignments(assignment_id) ON DELETE CASCADE
                );

                -- Idempotency: one report per assignment per month
                CREATE UNIQUE INDEX IF NOT EXISTS uq_scouting_reports_assignment_period
                    ON scouting_reports(assignment_id, period_key);

                CREATE INDEX IF NOT EXISTS idx_scouting_reports_team_date
                    ON scouting_reports(team_id, as_of_date);

                CREATE INDEX IF NOT EXISTS idx_scouting_reports_team_target
                    ON scouting_reports(team_id, target_player_id);

                CREATE INDEX IF NOT EXISTS idx_scouting_reports_team_period
                    ON scouting_reports(team_id, period_key);

                CREATE INDEX IF NOT EXISTS idx_scouting_reports_team_scout
                    ON scouting_reports(team_id, scout_id);
"""


def migrate(cur: sqlite3.Cursor, *, ensure_columns: EnsureColumnsFn) -> None:
    """Optional post-DDL migrations for scouting tables (currently no-op)."""
    _ = (cur, ensure_columns)
    return
