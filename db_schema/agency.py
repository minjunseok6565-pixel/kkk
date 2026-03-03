# db_schema/agency.py
"""SQLite SSOT schema: player agency.

This module introduces:
  - player_agency_state (current player "agency" state: frustrations, expectations, cooldowns)
  - agency_events (append-only log of agency-related events)

Notes
-----
* These tables reference `players` via player_id foreign key, so this module must
  be applied after db_schema.core.
* Dates are stored as ISO strings (YYYY-MM-DD). Month keys are stored as YYYY-MM.
* agency_events is append-only and keyed by event_id for idempotency.

Design goals
------------
- Keep the schema small but extensible.
- Make reads cheap for UI (team feed, player feed).
- Make writes safe and idempotent (event_id primary key + INSERT OR IGNORE).
"""

from __future__ import annotations


def ddl(*, now: str, schema_version: str) -> str:  # noqa: ARG001
    """Return DDL SQL for agency tables (as a single executescript string)."""

    return """

                CREATE TABLE IF NOT EXISTS player_agency_state (
                    player_id TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    season_year INTEGER NOT NULL,

                    role_bucket TEXT NOT NULL DEFAULT 'UNKNOWN'
                        CHECK(role_bucket IN ('UNKNOWN','FRANCHISE','STAR','STARTER','ROTATION','BENCH','GARBAGE')),
                    leverage REAL NOT NULL DEFAULT 0.0
                        CHECK(leverage >= 0.0 AND leverage <= 1.0),

                    minutes_expected_mpg REAL NOT NULL DEFAULT 0.0,
                    minutes_actual_mpg REAL NOT NULL DEFAULT 0.0,

                    -- v1 axes
                    minutes_frustration REAL NOT NULL DEFAULT 0.0
                        CHECK(minutes_frustration >= 0.0 AND minutes_frustration <= 1.0),
                    team_frustration REAL NOT NULL DEFAULT 0.0
                        CHECK(team_frustration >= 0.0 AND team_frustration <= 1.0),
                    trust REAL NOT NULL DEFAULT 0.5
                        CHECK(trust >= 0.0 AND trust <= 1.0),

                    -- v2 axes (additional; unused by v1 tick but persisted for v2+)
                    role_frustration REAL NOT NULL DEFAULT 0.0
                        CHECK(role_frustration >= 0.0 AND role_frustration <= 1.0),
                    contract_frustration REAL NOT NULL DEFAULT 0.0
                        CHECK(contract_frustration >= 0.0 AND contract_frustration <= 1.0),
                    health_frustration REAL NOT NULL DEFAULT 0.0
                        CHECK(health_frustration >= 0.0 AND health_frustration <= 1.0),
                    chemistry_frustration REAL NOT NULL DEFAULT 0.0
                        CHECK(chemistry_frustration >= 0.0 AND chemistry_frustration <= 1.0),
                    usage_frustration REAL NOT NULL DEFAULT 0.0
                        CHECK(usage_frustration >= 0.0 AND usage_frustration <= 1.0),

                    -- v2 monthly role evidence cache (derived; used for UI/explainability)
                    starts_rate REAL NOT NULL DEFAULT 0.0
                        CHECK(starts_rate >= 0.0 AND starts_rate <= 1.0),
                    closes_rate REAL NOT NULL DEFAULT 0.0
                        CHECK(closes_rate >= 0.0 AND closes_rate <= 1.0),
                    usage_share REAL NOT NULL DEFAULT 0.0
                        CHECK(usage_share >= 0.0 AND usage_share <= 1.0),

                    -- v3: self expectations (player self-perception; optional)
                    self_expected_mpg REAL,
                    self_expected_starts_rate REAL
                        CHECK(self_expected_starts_rate IS NULL OR (self_expected_starts_rate >= 0.0 AND self_expected_starts_rate <= 1.0)),
                    self_expected_closes_rate REAL
                        CHECK(self_expected_closes_rate IS NULL OR (self_expected_closes_rate >= 0.0 AND self_expected_closes_rate <= 1.0)),

                    -- v3: dynamic stances (short-to-mid-term attitude; 0..1)
                    stance_skepticism REAL NOT NULL DEFAULT 0.0
                        CHECK(stance_skepticism >= 0.0 AND stance_skepticism <= 1.0),
                    stance_resentment REAL NOT NULL DEFAULT 0.0
                        CHECK(stance_resentment >= 0.0 AND stance_resentment <= 1.0),
                    stance_hardball REAL NOT NULL DEFAULT 0.0
                        CHECK(stance_hardball >= 0.0 AND stance_hardball <= 1.0),

                    trade_request_level INTEGER NOT NULL DEFAULT 0
                        CHECK(trade_request_level IN (0,1,2)),

                    cooldown_minutes_until TEXT,
                    cooldown_trade_until TEXT,
                    cooldown_help_until TEXT,
                    cooldown_contract_until TEXT,

                    -- v2 cooldowns
                    cooldown_role_until TEXT,
                    cooldown_health_until TEXT,
                    cooldown_chemistry_until TEXT,

                    -- v2 escalation stages (0..4)
                    escalation_role INTEGER NOT NULL DEFAULT 0
                        CHECK(escalation_role >= 0 AND escalation_role <= 4),
                    escalation_contract INTEGER NOT NULL DEFAULT 0
                        CHECK(escalation_contract >= 0 AND escalation_contract <= 4),
                    escalation_team INTEGER NOT NULL DEFAULT 0
                        CHECK(escalation_team >= 0 AND escalation_team <= 4),
                    escalation_health INTEGER NOT NULL DEFAULT 0
                        CHECK(escalation_health >= 0 AND escalation_health <= 4),
                    escalation_chemistry INTEGER NOT NULL DEFAULT 0
                        CHECK(escalation_chemistry >= 0 AND escalation_chemistry <= 4),

                    last_processed_month TEXT,

                    context_json TEXT NOT NULL DEFAULT '{}',

                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,

                    FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_player_agency_state_team
                    ON player_agency_state(team_id);

                CREATE INDEX IF NOT EXISTS idx_player_agency_state_team_tradelevel
                    ON player_agency_state(team_id, trade_request_level);

                CREATE INDEX IF NOT EXISTS idx_player_agency_state_team_frustration
                    ON player_agency_state(team_id, minutes_frustration, team_frustration);


                CREATE TABLE IF NOT EXISTS agency_events (
                    event_id TEXT PRIMARY KEY,
                    player_id TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    season_year INTEGER NOT NULL,
                    date TEXT NOT NULL,

                    event_type TEXT NOT NULL,
                    severity REAL NOT NULL DEFAULT 0.0,

                    payload_json TEXT NOT NULL DEFAULT '{}',

                    created_at TEXT NOT NULL,

                    FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_agency_events_player_date
                    ON agency_events(player_id, date);

                CREATE INDEX IF NOT EXISTS idx_agency_events_team_date
                    ON agency_events(team_id, date);

                CREATE INDEX IF NOT EXISTS idx_agency_events_type_date
                    ON agency_events(event_type, date);


                -- User responses to agency events (idempotency + UI state)
                CREATE TABLE IF NOT EXISTS agency_event_responses (
                    response_id TEXT PRIMARY KEY,
                    source_event_id TEXT NOT NULL UNIQUE,
                    player_id TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    season_year INTEGER NOT NULL,
                    response_type TEXT NOT NULL,
                    response_payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,

                    FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_agency_event_responses_player
                    ON agency_event_responses(player_id);

                CREATE INDEX IF NOT EXISTS idx_agency_event_responses_team
                    ON agency_event_responses(team_id);

                CREATE INDEX IF NOT EXISTS idx_agency_event_responses_source
                    ON agency_event_responses(source_event_id);


                -- Promises created by user responses, resolved later (monthly tick)
                CREATE TABLE IF NOT EXISTS player_agency_promises (
                    promise_id TEXT PRIMARY KEY,
                    player_id TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    season_year INTEGER NOT NULL,

                    source_event_id TEXT,
                    response_id TEXT,

                    -- NOTE: promise_type CHECK constraint intentionally removed for v2 development.
                    promise_type TEXT NOT NULL,

                    status TEXT NOT NULL DEFAULT 'ACTIVE'
                        CHECK(status IN ('ACTIVE','FULFILLED','BROKEN','EXPIRED','CANCELLED')),

                    created_date TEXT NOT NULL,
                    due_month TEXT NOT NULL,

                    target_value REAL,
                    target_json TEXT NOT NULL DEFAULT '{}',
                    evidence_json TEXT NOT NULL DEFAULT '{}',

                    resolved_at TEXT,

                    FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_player_agency_promises_player_status_due
                    ON player_agency_promises(player_id, status, due_month);

                CREATE INDEX IF NOT EXISTS idx_player_agency_promises_team_status_due
                    ON player_agency_promises(team_id, status, due_month);

                CREATE INDEX IF NOT EXISTS idx_player_agency_promises_due
                    ON player_agency_promises(status, due_month);

"""


def migrate(cur, *, ensure_columns) -> None:
    """Post-DDL migrations (additive columns).

    NOTE: This project intentionally keeps agency schema migrations additive
    (no destructive changes) to preserve commercial robustness.

    The `ensure_columns` helper matches LeagueRepo._ensure_table_columns.
    """
    ensure_columns(
        cur,
        "player_agency_state",
        {
            # v3: self expectations
            "self_expected_mpg": "REAL",
            "self_expected_starts_rate": "REAL",
            "self_expected_closes_rate": "REAL",

            # v3: dynamic stances
            "stance_skepticism": "REAL NOT NULL DEFAULT 0.0",
            "stance_resentment": "REAL NOT NULL DEFAULT 0.0",
            "stance_hardball": "REAL NOT NULL DEFAULT 0.0",
        },
    )
