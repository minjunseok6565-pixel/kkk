# db_schema/core.py
"""SQLite SSOT schema: core league tables.

This module contains *only* DDL and schema migrations.
It must not import LeagueRepo (to avoid circular imports).

NOTE: This is a pure refactor split from league_repo.py.
The SQL and migration behavior must remain functionally identical.
"""

from __future__ import annotations

import sqlite3
from typing import Callable, Mapping


# Signature compatible with LeagueRepo._ensure_table_columns(cur, table, columns)
EnsureColumnsFn = Callable[[sqlite3.Cursor, str, Mapping[str, str]], None]


def ddl(*, now: str, schema_version: str) -> str:
    """Return DDL SQL for core tables (as a single executescript string)."""
    return f"""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                INSERT INTO meta(key, value) VALUES ('schema_version', '{schema_version}')
                ON CONFLICT(key) DO UPDATE SET value=excluded.value;
                INSERT OR IGNORE INTO meta(key, value) VALUES ('created_at', '{now}');

                CREATE TABLE IF NOT EXISTS players (
                    player_id TEXT PRIMARY KEY,
                    name TEXT,
                    pos TEXT,
                    age INTEGER,
                    exp INTEGER,
                    height_in INTEGER,
                    weight_lb INTEGER,
                    ovr INTEGER,
                    attrs_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS roster (
                    player_id TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    salary_amount INTEGER,
                    status TEXT NOT NULL DEFAULT 'active',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_roster_team_id ON roster(team_id);

                CREATE TABLE IF NOT EXISTS contracts (
                    contract_id TEXT PRIMARY KEY,
                    player_id TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    start_season_id TEXT,
                    end_season_id TEXT,
                    salary_by_season_json TEXT,
                    contract_type TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_contracts_player_id ON contracts(player_id);
                CREATE INDEX IF NOT EXISTS idx_contracts_team_id ON contracts(team_id);

                -- Transactions log (SSOT)
                CREATE TABLE IF NOT EXISTS transactions_log (
                    tx_hash TEXT PRIMARY KEY,
                    tx_type TEXT NOT NULL,
                    tx_date TEXT,
                    season_year INTEGER,
                    deal_id TEXT,
                    source TEXT,
                    teams_json TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions_log(tx_date);

                -- Contract indices (legacy-compatible SSOT)
                CREATE TABLE IF NOT EXISTS player_contracts (
                    player_id TEXT NOT NULL,
                    contract_id TEXT NOT NULL,
                    PRIMARY KEY(player_id, contract_id),
                    FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE,
                    FOREIGN KEY(contract_id) REFERENCES contracts(contract_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS active_contracts (
                    player_id TEXT PRIMARY KEY,
                    contract_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE,
                    FOREIGN KEY(contract_id) REFERENCES contracts(contract_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS free_agents (
                    player_id TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE
                );

                -- Team-level contract exception (MLE etc.) first-year budget usage per season/channel
                CREATE TABLE IF NOT EXISTS team_contract_exception_budget_usage (
                    season_year INTEGER NOT NULL,
                    team_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    first_year_spent_total INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT '{now}',
                    PRIMARY KEY (season_year, team_id, channel)
                );
                CREATE INDEX IF NOT EXISTS idx_exception_budget_usage_team_season
                    ON team_contract_exception_budget_usage(team_id, season_year);

                -- Team-level room MLE eligibility flags per season
                CREATE TABLE IF NOT EXISTS team_room_mle_flags (
                    season_year INTEGER NOT NULL,
                    team_id TEXT NOT NULL,
                    became_below_cap_once INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT '{now}',
                    PRIMARY KEY (season_year, team_id)
                );
                CREATE INDEX IF NOT EXISTS idx_room_mle_flags_team_season
                    ON team_room_mle_flags(team_id, season_year);

                -- Team-level Bird rights per season
                CREATE TABLE IF NOT EXISTS team_bird_rights (
                    season_year INTEGER NOT NULL,
                    team_id TEXT NOT NULL,
                    player_id TEXT NOT NULL,
                    bird_type TEXT NOT NULL,
                    tenure_years_same_team INTEGER NOT NULL,
                    is_renounced INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT '{now}',
                    updated_at TEXT NOT NULL DEFAULT '{now}',
                    PRIMARY KEY (season_year, team_id, player_id)
                );
                CREATE INDEX IF NOT EXISTS idx_bird_rights_team_season
                    ON team_bird_rights(team_id, season_year);
                CREATE INDEX IF NOT EXISTS idx_bird_rights_player_season
                    ON team_bird_rights(player_id, season_year);

                -- Team cap holds (Bird source)
                CREATE TABLE IF NOT EXISTS team_cap_holds (
                    season_year INTEGER NOT NULL,
                    team_id TEXT NOT NULL,
                    player_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    bird_type TEXT NOT NULL,
                    hold_amount INTEGER NOT NULL,
                    is_released INTEGER NOT NULL DEFAULT 0,
                    released_reason TEXT,
                    created_at TEXT NOT NULL DEFAULT '{now}',
                    updated_at TEXT NOT NULL DEFAULT '{now}',
                    PRIMARY KEY (season_year, team_id, player_id, source_type)
                );
                CREATE INDEX IF NOT EXISTS idx_cap_holds_team_season_active
                    ON team_cap_holds(team_id, season_year, is_released);

                -- Team dead caps (Waive/Stretch source)
                CREATE TABLE IF NOT EXISTS team_dead_caps (
                    dead_cap_id TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    player_id TEXT NOT NULL,
                    origin_contract_id TEXT,
                    source_type TEXT NOT NULL,
                    applied_season_year INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    is_voided INTEGER NOT NULL DEFAULT 0,
                    voided_reason TEXT,
                    meta_json TEXT,
                    created_at TEXT NOT NULL DEFAULT '{now}',
                    updated_at TEXT NOT NULL DEFAULT '{now}',
                    UNIQUE(team_id, player_id, origin_contract_id, source_type, applied_season_year)
                );
                CREATE INDEX IF NOT EXISTS idx_dead_caps_team_season_voided
                    ON team_dead_caps(team_id, applied_season_year, is_voided);
                CREATE INDEX IF NOT EXISTS idx_dead_caps_player_season
                    ON team_dead_caps(player_id, applied_season_year);
                CREATE INDEX IF NOT EXISTS idx_dead_caps_origin_contract
                    ON team_dead_caps(origin_contract_id);
		"""


def migrate(cur: sqlite3.Cursor, *, ensure_columns: EnsureColumnsFn) -> None:
    """Apply post-DDL schema migrations.

    This must remain functionally identical to the post-executescript section
    in LeagueRepo.init_db (ensure_table_columns + indices).
    """
    # Extend contracts table with full JSON storage (keeps contract shape stable across versions)
    ensure_columns(
        cur,
        "contracts",
        {
            "signed_date": "TEXT",
            "start_season_year": "INTEGER",
            "years": "INTEGER",
            "options_json": "TEXT",
            "status": "TEXT",
            "contract_json": "TEXT",
        },
    )

    # Ensure transactions_log has season_year as a first-class column (SSOT)
    # This keeps rule queries fast and avoids parsing payload_json for season filtering.
    ensure_columns(
        cur,
        "transactions_log",
        {
            "season_year": "INTEGER",
        },
    )

    # Indices for fast rule filtering (safe after ensuring columns exist).
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_tx_season_year ON transactions_log(season_year);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_tx_season_type_date ON transactions_log(season_year, tx_type, tx_date);"
    )

    # Dead-cap ledger table/indexes (waive/stretch SSOT).
    # Keep migrate path idempotent so existing DBs are brought up to date.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS team_dead_caps (
            dead_cap_id TEXT PRIMARY KEY,
            team_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            origin_contract_id TEXT,
            source_type TEXT NOT NULL,
            applied_season_year INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            is_voided INTEGER NOT NULL DEFAULT 0,
            voided_reason TEXT,
            meta_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(team_id, player_id, origin_contract_id, source_type, applied_season_year)
        );
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_dead_caps_team_season_voided ON team_dead_caps(team_id, applied_season_year, is_voided);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_dead_caps_player_season ON team_dead_caps(player_id, applied_season_year);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_dead_caps_origin_contract ON team_dead_caps(origin_contract_id);"
    )
