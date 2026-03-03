# db_schema/trade_assets.py
"""SQLite SSOT schema: trade-asset tables."""

from __future__ import annotations

import sqlite3
from typing import Callable, Mapping

EnsureColumnsFn = Callable[[sqlite3.Cursor, str, Mapping[str, str]], None]


def ddl(*, now: str, schema_version: str) -> str:
    """Return DDL SQL for trade-asset tables."""
    _ = (now, schema_version)
    return """

                -- Draft picks (SSOT)
                CREATE TABLE IF NOT EXISTS draft_picks (
                    pick_id TEXT PRIMARY KEY,
                    year INTEGER NOT NULL,
                    round INTEGER NOT NULL,
                    original_team TEXT NOT NULL,
                    owner_team TEXT NOT NULL,
                    protection_json TEXT,
                    trade_locked INTEGER NOT NULL DEFAULT 0,
                    trade_lock_reason TEXT,
                    trade_lock_start_season_year INTEGER,
                    trade_lock_eval_seasons INTEGER NOT NULL DEFAULT 0,
                    trade_lock_below_count INTEGER NOT NULL DEFAULT 0,
                    trade_lock_escalated INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_draft_picks_owner ON draft_picks(owner_team);
                CREATE INDEX IF NOT EXISTS idx_draft_picks_year_round ON draft_picks(year, round);

                -- Swap rights (SSOT)
                CREATE TABLE IF NOT EXISTS swap_rights (
                    swap_id TEXT PRIMARY KEY,
                    pick_id_a TEXT NOT NULL,
                    pick_id_b TEXT NOT NULL,
                    year INTEGER,
                    round INTEGER,
                    owner_team TEXT NOT NULL,
                    originator_team TEXT,
                    transfer_count INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_by_deal_id TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_swap_rights_owner ON swap_rights(owner_team);
                CREATE INDEX IF NOT EXISTS idx_swap_rights_year_round ON swap_rights(year, round);

                -- Fixed assets (SSOT)
                CREATE TABLE IF NOT EXISTS fixed_assets (
                    asset_id TEXT PRIMARY KEY,
                    label TEXT,
                    value REAL,
                    owner_team TEXT NOT NULL,
                    source_pick_id TEXT,
                    draft_year INTEGER,
                    attrs_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_fixed_assets_owner ON fixed_assets(owner_team);
"""


def migrate(cur: sqlite3.Cursor, *, ensure_columns: EnsureColumnsFn) -> None:
    ensure_columns(
        cur,
        "draft_picks",
        {
            "trade_locked": "INTEGER NOT NULL DEFAULT 0",
            "trade_lock_reason": "TEXT",
            "trade_lock_start_season_year": "INTEGER",
            "trade_lock_eval_seasons": "INTEGER NOT NULL DEFAULT 0",
            "trade_lock_below_count": "INTEGER NOT NULL DEFAULT 0",
            "trade_lock_escalated": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    ensure_columns(
        cur,
        "swap_rights",
        {
            "originator_team": "TEXT",
            "transfer_count": "INTEGER NOT NULL DEFAULT 0",
        },
    )
