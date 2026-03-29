from __future__ import annotations

"""League-wide salary limit helpers (experience-bucket based).

This module is intentionally pure and reusable:
- It only computes limit percentages / absolute AAV caps.
- It does not read DB/state directly.
"""

from dataclasses import dataclass
from typing import Any, Mapping

from contracts.negotiation.utils import safe_float, safe_int


DEFAULT_CONTRACT_AAV_MAX_PCT_BY_EXP: dict[str, float] = {
    "le_6": 0.25,
    "7_9": 0.30,
    "ge_10": 0.35,
}


@dataclass(frozen=True, slots=True)
class ExpAavLimit:
    exp: int
    bucket: str
    max_pct_of_salary_cap: float
    salary_cap: float
    max_aav_abs: float

    def to_payload(self) -> dict[str, Any]:
        return {
            "exp": int(self.exp),
            "bucket": str(self.bucket),
            "max_pct_of_salary_cap": float(self.max_pct_of_salary_cap),
            "salary_cap": float(self.salary_cap),
            "max_aav_abs": float(self.max_aav_abs),
        }


def _coerce_pct_by_exp(raw: Mapping[str, Any] | None) -> dict[str, float]:
    src = raw or {}
    le_6 = float(safe_float(src.get("le_6"), DEFAULT_CONTRACT_AAV_MAX_PCT_BY_EXP["le_6"]))
    b_7_9 = float(safe_float(src.get("7_9"), DEFAULT_CONTRACT_AAV_MAX_PCT_BY_EXP["7_9"]))
    ge_10 = float(safe_float(src.get("ge_10"), DEFAULT_CONTRACT_AAV_MAX_PCT_BY_EXP["ge_10"]))
    return {
        "le_6": 0.0 if le_6 < 0.0 else le_6,
        "7_9": 0.0 if b_7_9 < 0.0 else b_7_9,
        "ge_10": 0.0 if ge_10 < 0.0 else ge_10,
    }


def exp_bucket_for_limit(exp: Any) -> str:
    exp_i = int(safe_int(exp, 0))
    if exp_i <= 6:
        return "le_6"
    if exp_i <= 9:
        return "7_9"
    return "ge_10"


def contract_aav_max_pct_for_exp(exp: Any, pct_by_exp: Mapping[str, Any] | None = None) -> float:
    by_exp = _coerce_pct_by_exp(pct_by_exp)
    bucket = exp_bucket_for_limit(exp)
    return float(safe_float(by_exp.get(bucket), 0.0))


def contract_aav_max_abs_for_exp(
    *,
    exp: Any,
    salary_cap: Any,
    pct_by_exp: Mapping[str, Any] | None = None,
) -> float:
    cap = float(safe_float(salary_cap, 0.0))
    if cap <= 0.0:
        return 0.0
    pct = float(contract_aav_max_pct_for_exp(exp, pct_by_exp))
    if pct <= 0.0:
        return 0.0
    return float(cap) * float(pct)


def build_exp_aav_limit(
    *,
    exp: Any,
    salary_cap: Any,
    pct_by_exp: Mapping[str, Any] | None = None,
) -> ExpAavLimit:
    exp_i = int(safe_int(exp, 0))
    cap = float(safe_float(salary_cap, 0.0))
    bucket = exp_bucket_for_limit(exp_i)
    pct = float(contract_aav_max_pct_for_exp(exp_i, pct_by_exp))
    max_abs = float(contract_aav_max_abs_for_exp(exp=exp_i, salary_cap=cap, pct_by_exp=pct_by_exp))
    return ExpAavLimit(
        exp=int(exp_i),
        bucket=str(bucket),
        max_pct_of_salary_cap=float(pct),
        salary_cap=float(cap),
        max_aav_abs=float(max_abs),
    )


def max_tier_upper_bound(exp: Any, salary_cap: Any, prev_salary: Any) -> float:
    """Return extension max-tier upper bound using exp bucket and 105% prev-salary floor."""
    cap = float(safe_float(salary_cap, 0.0))
    prev = float(safe_float(prev_salary, 0.0))
    tier_cap_abs = float(contract_aav_max_abs_for_exp(exp=exp, salary_cap=cap))
    prev_floor = float(prev) * 1.05 if prev > 0.0 else 0.0
    return float(max(tier_cap_abs, prev_floor))


def veteran_extension_first_year_ceiling(
    prev_salary: Any,
    eaps: Any,
    exp: Any,
    salary_cap: Any,
) -> float:
    """VE first-year ceiling constrained by 140% base and max-tier upper bound."""
    prev = float(safe_float(prev_salary, 0.0))
    eaps_f = float(safe_float(eaps, 0.0))
    base_140 = max(prev * 1.40, eaps_f * 1.40)
    tier_upper = float(max_tier_upper_bound(exp=exp, salary_cap=salary_cap, prev_salary=prev))
    return float(min(base_140, tier_upper))


def rookie_extension_first_year_ceiling(prev_salary: Any, salary_cap: Any) -> float:
    """RSE first-year ceiling: max(25% cap, 105% of previous salary)."""
    prev = float(safe_float(prev_salary, 0.0))
    cap = float(safe_float(salary_cap, 0.0))
    return float(max(cap * 0.25, prev * 1.05))


def dve_first_year_band(
    salary_cap: Any,
    min_pct: Any = 0.30,
    max_pct: Any = 0.35,
) -> dict[str, float]:
    """DVE first-year min/max band based on cap percentages."""
    cap = float(safe_float(salary_cap, 0.0))
    lo = float(safe_float(min_pct, 0.30))
    hi = float(safe_float(max_pct, 0.35))
    if lo < 0.0:
        lo = 0.0
    if hi < lo:
        hi = lo
    return {
        "min_first_year": float(cap * lo) if cap > 0.0 else 0.0,
        "max_first_year": float(cap * hi) if cap > 0.0 else 0.0,
    }
