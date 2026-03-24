from __future__ import annotations

"""Bird rights policy helpers (pure functions).

Scope:
- bird type classification from same-team tenure
- cap-hold multipliers
- years/raise limits by bird type
- first-year max salary by bird type
"""

from dataclasses import dataclass
from typing import Any, Mapping

from contracts.negotiation.utils import safe_float, safe_int
from contracts.policy.salary_limits import contract_aav_max_abs_for_exp


BIRD_NONE = "NONE"
BIRD_NON = "NON_BIRD"
BIRD_EARLY = "EARLY_BIRD"
BIRD_FULL = "FULL_BIRD"

_DEFAULT_CAP_HOLD_MULTIPLIER_BY_TYPE: dict[str, float] = {
    BIRD_FULL: 1.5,
    BIRD_EARLY: 1.3,
    BIRD_NON: 1.2,
}

_DEFAULT_MAX_YEARS_BY_TYPE: dict[str, int] = {
    BIRD_FULL: 5,
    BIRD_EARLY: 4,
    BIRD_NON: 4,
}

_DEFAULT_MAX_RAISE_PCT_BY_TYPE: dict[str, float] = {
    BIRD_FULL: 0.08,
    BIRD_EARLY: 0.08,
    BIRD_NON: 0.05,
}

_DEFAULT_EARLY_MULT_PREV = 1.75
_DEFAULT_EARLY_MULT_LEAGUE_AVG = 1.05
_DEFAULT_NON_MULT_PREV = 1.20


@dataclass(frozen=True, slots=True)
class BirdFirstYearLimit:
    bird_type: str
    max_first_year_salary: int
    reason: str


def _normalize_bird_type(value: Any) -> str:
    v = str(value or "").strip().upper()
    if v in {BIRD_FULL, BIRD_EARLY, BIRD_NON}:
        return v
    return BIRD_NONE


def _trade_rules_dict(trade_rules: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(trade_rules) if isinstance(trade_rules, Mapping) else {}


def classify_bird_type(tenure_years_same_team: Any) -> str:
    y = int(safe_int(tenure_years_same_team, 0))
    if y >= 3:
        return BIRD_FULL
    if y >= 2:
        return BIRD_EARLY
    if y >= 1:
        return BIRD_NON
    return BIRD_NONE


def cap_hold_multiplier(bird_type: Any, *, trade_rules: Mapping[str, Any] | None = None) -> float:
    bt = _normalize_bird_type(bird_type)
    if bt == BIRD_NONE:
        return 0.0
    tr = _trade_rules_dict(trade_rules)
    raw = tr.get("bird_cap_hold_multiplier_by_type")
    if isinstance(raw, Mapping):
        v = float(safe_float(raw.get(bt), _DEFAULT_CAP_HOLD_MULTIPLIER_BY_TYPE[bt]))
        if v > 0.0:
            return float(v)
    return float(_DEFAULT_CAP_HOLD_MULTIPLIER_BY_TYPE[bt])


def max_years_for_bird_type(bird_type: Any) -> int:
    bt = _normalize_bird_type(bird_type)
    if bt == BIRD_NONE:
        return 0
    return int(_DEFAULT_MAX_YEARS_BY_TYPE[bt])


def max_raise_pct_for_bird_type(bird_type: Any, *, trade_rules: Mapping[str, Any] | None = None) -> float:
    bt = _normalize_bird_type(bird_type)
    if bt == BIRD_NONE:
        return 0.0
    tr = _trade_rules_dict(trade_rules)
    raw = tr.get("bird_raise_max_pct_by_type")
    if isinstance(raw, Mapping):
        v = float(safe_float(raw.get(bt), _DEFAULT_MAX_RAISE_PCT_BY_TYPE[bt]))
        if v >= 0.0:
            return float(v)
    return float(_DEFAULT_MAX_RAISE_PCT_BY_TYPE[bt])


def _salary_cap_for_season(season_year: Any, trade_rules: Mapping[str, Any] | None) -> float:
    tr = _trade_rules_dict(trade_rules)
    cap = float(safe_float(tr.get("salary_cap"), 0.0))
    if cap > 0.0:
        return cap
    return 0.0


def first_year_limit_for_bird_type(
    *,
    bird_type: Any,
    prev_salary: Any,
    exp: Any,
    season_year: Any,
    trade_rules: Mapping[str, Any] | None = None,
    league_average_salary: Any = None,
) -> BirdFirstYearLimit:
    bt = _normalize_bird_type(bird_type)
    prev = float(safe_float(prev_salary, 0.0))
    if prev < 0.0:
        prev = 0.0

    if bt == BIRD_NONE:
        return BirdFirstYearLimit(bird_type=BIRD_NONE, max_first_year_salary=0, reason="not_eligible")

    if bt == BIRD_NON:
        tr = _trade_rules_dict(trade_rules)
        mult_prev = float(safe_float(tr.get("bird_non_multiplier_prev_salary"), _DEFAULT_NON_MULT_PREV))
        limit = max(0.0, prev * max(mult_prev, 0.0))
        return BirdFirstYearLimit(
            bird_type=bt,
            max_first_year_salary=int(round(limit)),
            reason="non_bird_prev_salary_multiplier",
        )

    if bt == BIRD_EARLY:
        tr = _trade_rules_dict(trade_rules)
        mult_prev = float(safe_float(tr.get("bird_early_multiplier_prev_salary"), _DEFAULT_EARLY_MULT_PREV))
        mult_avg = float(safe_float(tr.get("bird_early_multiplier_league_avg"), _DEFAULT_EARLY_MULT_LEAGUE_AVG))
        avg_salary = float(safe_float(league_average_salary, 0.0))
        via_prev = prev * max(mult_prev, 0.0)
        via_avg = avg_salary * max(mult_avg, 0.0)
        limit = max(via_prev, via_avg, 0.0)
        return BirdFirstYearLimit(
            bird_type=bt,
            max_first_year_salary=int(round(limit)),
            reason="early_bird_max_of_prev_or_league_avg",
        )

    # FULL_BIRD: reuse existing exp-based max policy (cap-percentage by exp).
    cap = _salary_cap_for_season(season_year, trade_rules)
    if cap <= 0.0:
        return BirdFirstYearLimit(
            bird_type=bt,
            max_first_year_salary=0,
            reason="full_bird_missing_salary_cap",
        )
    tr = _trade_rules_dict(trade_rules)
    pct_by_exp = tr.get("contract_aav_max_pct_by_exp")
    abs_limit = float(
        contract_aav_max_abs_for_exp(
            exp=int(safe_int(exp, 0)),
            salary_cap=float(cap),
            pct_by_exp=pct_by_exp if isinstance(pct_by_exp, Mapping) else None,
        )
    )
    if abs_limit < 0.0:
        abs_limit = 0.0
    return BirdFirstYearLimit(
        bird_type=bt,
        max_first_year_salary=int(round(abs_limit)),
        reason="full_bird_exp_based_max",
    )
