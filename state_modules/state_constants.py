from __future__ import annotations

from typing import Any, Dict

from config import (
    CAP_ANNUAL_GROWTH_RATE,
    CAP_BASE_FIRST_APRON,
    CAP_BASE_SALARY_CAP,
    CAP_BASE_SECOND_APRON,
    CAP_BASE_SEASON_YEAR,
    CAP_ROUND_UNIT,
    MLE_ANNUAL_GROWTH_RATE,
    MLE_BASE_NT,
    MLE_BASE_ROOM,
    MLE_BASE_SEASON_YEAR,
    MLE_BASE_TP,
    MLE_ENABLED,
)

DEFAULT_TRADE_RULES: Dict[str, Any] = {
    "trade_deadline": None,
    "salary_cap": 0.0,
    "first_apron": 0.0,
    "second_apron": 0.0,
    "cap_auto_update": True,
    "cap_base_season_year": CAP_BASE_SEASON_YEAR,
    "cap_base_salary_cap": CAP_BASE_SALARY_CAP,
    "cap_base_first_apron": CAP_BASE_FIRST_APRON,
    "cap_base_second_apron": CAP_BASE_SECOND_APRON,
    "cap_annual_growth_rate": CAP_ANNUAL_GROWTH_RATE,
    "cap_round_unit": CAP_ROUND_UNIT,
    "match_auto_update": True,
    "match_base_mid_add": 8_527_000,
    "match_mid_add": 8_527_000,
    "match_buffer": 250_000,
    "match_small_out_max": 8_277_000,
    "match_mid_out_max": 33_108_000,
    "first_apron_mult": 1.00,
    "second_apron_mult": 1.00,
    "new_fa_sign_ban_days": 90,
    "two_way_sign_ban_days": 30,
    "aggregation_ban_days": 60,
    # League-wide player contract AAV hard cap by experience bucket (share of salary cap).
    # - exp <= 6  : 25%
    # - exp 7~9   : 30%
    # - exp >= 10 : 35%
    "contract_aav_max_pct_by_exp": {
        "le_6": 0.25,
        "7_9": 0.30,
        "ge_10": 0.35,
    },
    "roster_limit_rule_enabled": False,
    "max_pick_years_ahead": 7,
    "stepien_lookahead": 7,
    "mle_enabled": MLE_ENABLED,
    "mle_base_season_year": MLE_BASE_SEASON_YEAR,
    "mle_annual_growth_rate": MLE_ANNUAL_GROWTH_RATE,
    "mle_channels": {
        "NT_MLE": {
            "first_year_base": MLE_BASE_NT,
            "max_years": 4,
        },
        "TP_MLE": {
            "first_year_base": MLE_BASE_TP,
            "max_years": 2,
        },
        "ROOM_MLE": {
            "first_year_base": MLE_BASE_ROOM,
            "max_years": 3,
        },
    },
}

_ALLOWED_SCHEDULE_STATUSES = {"scheduled", "final", "in_progress", "canceled"}

_DEFAULT_TRADE_MARKET: Dict[str, Any] = {
    "last_tick_date": None,
    "listings": {},
    "threads": {},
    "cooldowns": {},
    "events": [],
    "applied_exec_deal_ids": {},
    # Derived helper cursor for idempotent side-effects (no authority; DB remains SSOT)
    "grievance_cursor": {},
}

_DEFAULT_TRADE_MEMORY: Dict[str, Any] = {
    "relationships": {},
}

_ALLOWED_PHASES = {"regular", "play_in", "playoffs", "preseason"}

_META_PLAYER_KEYS = {"PlayerID", "TeamID", "Name", "Pos", "Position"}
