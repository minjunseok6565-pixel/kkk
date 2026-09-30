from __future__ import annotations

"""MINIMUM contract policy SSOT.

This module is intentionally pure:
- no DB access
- deterministic calculations only

Policy summary:
- Base season: 2025 (25-26)
- Growth: +10% per season (compound)
- Salary by exp bucket (0..10, clamp outside range)
- Contract years: only 1 or 2
- 2-year MINIMUM uses identical salary for year 2
"""

from dataclasses import dataclass, field
from typing import Any, Mapping

from contracts.negotiation.utils import safe_float, safe_int


MINIMUM_BASE_SEASON_YEAR: int = 2025
MINIMUM_GROWTH_RATE: float = 0.10
MINIMUM_BY_EXP_2025: dict[int, int] = {
    0: 1_272_870,
    1: 2_048_494,
    2: 2_296_274,
    3: 2_378_870,
    4: 2_461_463,
    5: 2_667_947,
    6: 2_874_436,
    7: 3_080_921,
    8: 3_287_409,
    9: 3_303_774,
    10: 3_634_153,
}


@dataclass(frozen=True, slots=True)
class MinimumOfferValidation:
    ok: bool
    expected_first_year_salary: int
    expected_years: list[int] = field(default_factory=lambda: [1, 2])
    reasons: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "expected_first_year_salary": int(self.expected_first_year_salary),
            "expected_years": [int(y) for y in (self.expected_years or [1, 2])],
            "reasons": [dict(r) for r in (self.reasons or [])],
        }


def _clamp_exp(exp: Any) -> int:
    exp_i = int(safe_int(exp, 0))
    if exp_i < 0:
        return 0
    if exp_i > 10:
        return 10
    return int(exp_i)


def _safe_int_round(value: float) -> int:
    f = float(safe_float(value, 0.0))
    if f <= 0.0:
        return 0
    return int(round(f))


def _coerce_season_year(value: Any) -> int:
    out = int(safe_int(value, 0))
    if out <= 0:
        raise ValueError(f"season_year must be > 0, got={value!r}")
    return int(out)


def _normalize_salary_by_year_map(value: Any) -> dict[int, int]:
    if not isinstance(value, Mapping):
        return {}

    out: dict[int, int] = {}
    for k, v in value.items():
        try:
            y = int(k)
        except Exception:
            continue
        sal = _safe_int_round(float(safe_float(v, 0.0)))
        if y <= 0 or sal <= 0:
            continue
        out[int(y)] = int(sal)
    return out


def _extract_offer_start_year(offer: Mapping[str, Any], salary_by_year: Mapping[int, int]) -> int:
    sy = offer.get("start_season_year")
    if sy is None:
        sy = offer.get("start_year")

    sy_i = int(safe_int(sy, 0))
    if sy_i > 0:
        return int(sy_i)

    if salary_by_year:
        return int(min(salary_by_year.keys()))

    return 0


def minimum_first_year_salary(exp: Any, season_year: Any) -> int:
    """Return MINIMUM first-year salary for exp/season based on SSOT policy."""
    sy = _coerce_season_year(season_year)
    exp_c = _clamp_exp(exp)

    base = int(MINIMUM_BY_EXP_2025[exp_c])
    years_passed = int(sy - int(MINIMUM_BASE_SEASON_YEAR))
    growth = (1.0 + float(MINIMUM_GROWTH_RATE)) ** int(years_passed)
    scaled = float(base) * float(growth)
    return int(_safe_int_round(scaled))


def build_minimum_salary_by_year(exp: Any, start_season_year: Any, years: Any) -> dict[str, float]:
    """Build MINIMUM salary schedule.

    Rules:
    - years must be 1 or 2
    - year-2 salary equals year-1 salary
    """
    start = _coerce_season_year(start_season_year)
    years_i = int(safe_int(years, 0))
    if years_i not in {1, 2}:
        raise ValueError(f"MINIMUM years must be 1 or 2, got={years!r}")

    first = int(minimum_first_year_salary(exp=exp, season_year=start))
    out: dict[str, float] = {str(int(start)): float(first)}
    if years_i == 2:
        out[str(int(start) + 1)] = float(first)
    return out


def validate_minimum_offer(
    offer: Mapping[str, Any],
    player_exp: Any,
    season_year: Any,
) -> MinimumOfferValidation:
    """Validate MINIMUM channel offer shape/rules.

    Validation targets:
    - years: only 1 or 2
    - start season must match negotiation season
    - salary_by_year must exactly match MINIMUM policy schedule
    - options must be empty
    """
    src = offer if isinstance(offer, Mapping) else {}

    reasons: list[dict[str, Any]] = []
    target_season = int(_coerce_season_year(season_year))
    expected_first = int(minimum_first_year_salary(exp=player_exp, season_year=target_season))

    years_i = int(safe_int(src.get("years"), 0))
    if years_i not in {1, 2}:
        reasons.append(
            {
                "code": "MINIMUM_INVALID_YEARS",
                "message": "MINIMUM years must be 1 or 2.",
                "details": {"years": int(years_i), "allowed": [1, 2]},
            }
        )

    curve = _normalize_salary_by_year_map(src.get("salary_by_year"))
    start = _extract_offer_start_year(src, curve)
    if int(start) <= 0:
        reasons.append(
            {
                "code": "MINIMUM_MISSING_START_SEASON",
                "message": "Offer must include start_season_year (or inferable salary_by_year).",
            }
        )
    elif int(start) != int(target_season):
        reasons.append(
            {
                "code": "MINIMUM_START_SEASON_MISMATCH",
                "message": "MINIMUM offer start_season_year must match negotiation season.",
                "details": {
                    "offer_start_season_year": int(start),
                    "negotiation_season_year": int(target_season),
                },
            }
        )

    options = src.get("options")
    if isinstance(options, list) and options:
        reasons.append(
            {
                "code": "MINIMUM_OPTIONS_NOT_ALLOWED",
                "message": "MINIMUM offers do not allow options.",
                "details": {"options_count": int(len(options))},
            }
        )

    if years_i in {1, 2} and int(start) > 0:
        expected_curve = {
            int(start): int(expected_first),
            **({int(start) + 1: int(expected_first)} if years_i == 2 else {}),
        }

        if not curve:
            reasons.append(
                {
                    "code": "MINIMUM_MISSING_SALARY_CURVE",
                    "message": "MINIMUM offer must include salary_by_year.",
                    "details": {"expected_salary_by_year": {str(k): int(v) for k, v in expected_curve.items()}},
                }
            )
        else:
            if set(curve.keys()) != set(expected_curve.keys()):
                reasons.append(
                    {
                        "code": "MINIMUM_INVALID_SALARY_YEARS",
                        "message": "salary_by_year years do not match MINIMUM contract years.",
                        "details": {
                            "offer_years": sorted(int(y) for y in curve.keys()),
                            "expected_years": sorted(int(y) for y in expected_curve.keys()),
                        },
                    }
                )

            for y, expected_salary in expected_curve.items():
                got = int(curve.get(int(y), 0))
                if got != int(expected_salary):
                    reasons.append(
                        {
                            "code": "MINIMUM_SALARY_MISMATCH",
                            "message": "salary_by_year does not match MINIMUM policy salary.",
                            "details": {
                                "season_year": int(y),
                                "offer_salary": int(got),
                                "expected_salary": int(expected_salary),
                            },
                        }
                    )

    return MinimumOfferValidation(
        ok=(len(reasons) == 0),
        expected_first_year_salary=int(expected_first),
        expected_years=[1, 2],
        reasons=reasons,
    )


__all__ = [
    "MINIMUM_BASE_SEASON_YEAR",
    "MINIMUM_GROWTH_RATE",
    "MINIMUM_BY_EXP_2025",
    "MinimumOfferValidation",
    "minimum_first_year_salary",
    "build_minimum_salary_by_year",
    "validate_minimum_offer",
]
