"""SQLite SSOT schema: draft tables.

This module contains only DDL (and optional migrations) for the draft subsystem.

Tables:
- draft_order_plans: persisted draft order plan (incl. lottery outcome) for a given year
- draft_selections: pick-level selections (pre-apply)
- draft_results: pick-level applied results (SSOT for idempotent/resumable draft execution)
- draft_combine_results: combine outputs per prospect (pre-draft)
- draft_workout_results: workout outputs per (team, prospect) (pre-draft)
- draft_interview_results: interview outputs per (team, prospect) (pre-draft)
- draft_withdrawals: "test the waters" withdrawals (pre-draft)
- draft_undrafted_outcomes: undrafted routing outcomes after draft apply
- draft_watch_runs: monthly "watch" snapshot runs (pre-declaration; for early bigboards)
- draft_watch_probs: per-run player declare probabilities / projections (pre-declaration)

Design notes:
- draft_results records *applied* outcomes and must be written in the same transaction as the
  corresponding apply operations (players/roster/contracts/tx/...).
- draft_selections records choices during the draft session prior to apply.
- draft_order_plans provides a stable draft order plan so lottery/settlement can be run as
  explicit, idempotent steps.
"""

from __future__ import annotations

import sqlite3
from typing import Callable, Mapping


# Signature compatible with LeagueRepo._ensure_table_columns(cur, table, columns)
EnsureColumnsFn = Callable[[sqlite3.Cursor, str, Mapping[str, str]], None]


def ddl(*, now: str, schema_version: str) -> str:
    """Return DDL SQL for draft tables."""
    _ = (now, schema_version)
    return """
                -- Draft pick results (applied outcomes; SSOT for idempotency)
                CREATE TABLE IF NOT EXISTS draft_results (
                    pick_id TEXT PRIMARY KEY,
                    draft_year INTEGER NOT NULL,
                    overall_no INTEGER NOT NULL,
                    round INTEGER NOT NULL,
                    slot INTEGER NOT NULL,
                    original_team TEXT NOT NULL,
                    drafting_team TEXT NOT NULL,
                    prospect_temp_id TEXT NOT NULL,
                    player_id TEXT NOT NULL,
                    contract_id TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'draft',
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                -- Enforce one applied result per (draft_year, overall_no)
                CREATE UNIQUE INDEX IF NOT EXISTS uq_draft_results_year_overall
                    ON draft_results(draft_year, overall_no);

                CREATE INDEX IF NOT EXISTS idx_draft_results_year
                    ON draft_results(draft_year);

                -- Persisted draft order plan for a year (incl. lottery outcome)
                CREATE TABLE IF NOT EXISTS draft_order_plans (
                    draft_year INTEGER PRIMARY KEY,
                    plan_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                -- Draft selections (pre-apply; SSOT for the interactive draft session)
                CREATE TABLE IF NOT EXISTS draft_selections (
                    pick_id TEXT PRIMARY KEY,
                    draft_year INTEGER NOT NULL,
                    overall_no INTEGER NOT NULL,
                    round INTEGER NOT NULL,
                    slot INTEGER NOT NULL,
                    original_team TEXT NOT NULL,
                    drafting_team TEXT NOT NULL,
                    prospect_temp_id TEXT NOT NULL,
                    selected_at TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'draft',
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                -- Enforce one selection per (draft_year, overall_no)
                CREATE UNIQUE INDEX IF NOT EXISTS uq_draft_selections_year_overall
                    ON draft_selections(draft_year, overall_no);

                CREATE INDEX IF NOT EXISTS idx_draft_selections_year
                    ON draft_selections(draft_year);

                -- Combine results per prospect (pre-draft informational event)
                CREATE TABLE IF NOT EXISTS draft_combine_results (
                    draft_year INTEGER NOT NULL,
                    prospect_temp_id TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (draft_year, prospect_temp_id)
                );

                CREATE INDEX IF NOT EXISTS idx_draft_combine_results_year
                    ON draft_combine_results(draft_year);

                -- Workout results per (team, prospect) (pre-draft informational event)
                CREATE TABLE IF NOT EXISTS draft_workout_results (
                    draft_year INTEGER NOT NULL,
                    team_id TEXT NOT NULL,
                    prospect_temp_id TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (draft_year, team_id, prospect_temp_id)
                );

                CREATE INDEX IF NOT EXISTS idx_draft_workout_results_year_team
                    ON draft_workout_results(draft_year, team_id);

                -- Interview results per (team, prospect) (pre-draft informational event)
                CREATE TABLE IF NOT EXISTS draft_interview_results (
                    draft_year INTEGER NOT NULL,
                    team_id TEXT NOT NULL,
                    prospect_temp_id TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (draft_year, team_id, prospect_temp_id)
                );

                CREATE INDEX IF NOT EXISTS idx_draft_interview_results_year_team
                    ON draft_interview_results(draft_year, team_id);

                -- -----------------------------------------------------------------------------
                -- Withdrawals (test the waters) + Undrafted resolution
                -- -----------------------------------------------------------------------------

                -- Underclass withdrawal decisions (pre-draft)
                CREATE TABLE IF NOT EXISTS draft_withdrawals (
                    draft_year INTEGER NOT NULL,
                    player_id TEXT NOT NULL,
                    withdrawn_at TEXT NOT NULL,
                    decision_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (draft_year, player_id)
                );

                CREATE INDEX IF NOT EXISTS idx_draft_withdrawals_year
                    ON draft_withdrawals(draft_year);

                -- Undrafted outcomes after draft apply
                CREATE TABLE IF NOT EXISTS draft_undrafted_outcomes (
                    draft_year INTEGER NOT NULL,
                    player_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,              -- FA / RETIRED (future: GLEAGUE / OVERSEAS)
                    decided_at TEXT NOT NULL,
                    decision_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (draft_year, player_id)
                );

                CREATE INDEX IF NOT EXISTS idx_draft_undrafted_year
                    ON draft_undrafted_outcomes(draft_year);

                CREATE INDEX IF NOT EXISTS idx_draft_undrafted_year_outcome
                    ON draft_undrafted_outcomes(draft_year, outcome);

                -- -----------------------------------------------------------------------------
                -- Draft watch snapshots (pre-declaration)
                -- -----------------------------------------------------------------------------

                -- Watch run metadata (one per (draft_year, period_key))
                CREATE TABLE IF NOT EXISTS draft_watch_runs (
                    run_id TEXT PRIMARY KEY,
                    draft_year INTEGER NOT NULL,
                    period_key TEXT NOT NULL,          -- YYYY-MM
                    as_of_date TEXT NOT NULL,           -- YYYY-MM-DD
                    season_year INTEGER NOT NULL,       -- stats season used for this run
                    min_inclusion_prob REAL NOT NULL DEFAULT 0.35,
                    created_at TEXT NOT NULL
                );

                -- Enforce one run per (draft_year, period_key)
                CREATE UNIQUE INDEX IF NOT EXISTS uq_draft_watch_runs_year_period
                    ON draft_watch_runs(draft_year, period_key);

                CREATE INDEX IF NOT EXISTS idx_draft_watch_runs_year
                    ON draft_watch_runs(draft_year);

                -- Per-run player declare probabilities / projections
                CREATE TABLE IF NOT EXISTS draft_watch_probs (
                    run_id TEXT NOT NULL,
                    player_id TEXT NOT NULL,
                    declare_prob REAL NOT NULL,
                    projected_pick INTEGER,
                    decision_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, player_id),
                    FOREIGN KEY(run_id) REFERENCES draft_watch_runs(run_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_draft_watch_probs_run_prob
                    ON draft_watch_probs(run_id, declare_prob);

                CREATE INDEX IF NOT EXISTS idx_draft_watch_probs_run_pick
                    ON draft_watch_probs(run_id, projected_pick);
"""


def migrate(cur: sqlite3.Cursor, *, ensure_columns: EnsureColumnsFn) -> None:
    """Optional post-DDL migrations for draft tables.

    Currently no-op (initial version).
    Kept for forward compatibility.
    """
    _ = (cur, ensure_columns)
    return
