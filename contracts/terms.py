from __future__ import annotations

"""contracts/terms.py

Contract terms & schedule interpretation SSOT.

Why this exists
---------------
Across the codebase, multiple modules interpreted contract schedules differently:
- valuation market pricing
- package effects (commitment metric)
- team_situation
- options policy / offseason logic

That duplication makes it easy for subtle mismatches to appear (e.g. remaining years
computed one way in valuation and another way in team situation).

This module provides dependency-light helpers that interpret:
- salary_by_year (int/str keys)
- start_season_year + years fallback (if salary_by_year missing)
- salary_amount fallback (for snapshots that only have current season salary)

Design principles
-----------------
- Pure functions (no DB/state).
- Defensive coercion: never throws for malformed inputs.
- One canonical definition of "remaining years": number of seasons >= current year with salary>0.

This file intentionally does NOT import trades/valuation types to avoid layering issues.
Callers can pass dicts, dataclasses, or any objects with the expected attributes.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


# -----------------------------------------------------------------------------
# Coercion helpers
# -----------------------------------------------------------------------------
def _coerce_int(x: Any, default: int) -> int:
    try:
        if x is None:
            return int(default)
        return int(x)
    except Exception:
        return int(default)


def _coerce_float(x: Any, default: float) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Get key from dict-like or attribute from object."""
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    try:
        return getattr(obj, key)
    except Exception:
        return default


def coerce_salary_by_year_map(x: Any) -> Dict[int, float]:
    """Normalize a salary_by_year mapping to Dict[int, float].

    - Accepts int/str keys.
    - Skips keys that cannot be converted to int.
    - Coerces values to float (non-numeric -> 0.0).
    """
    if not isinstance(x, Mapping):
        return {}
    out: Dict[int, float] = {}
    for k, v in x.items():
        try:
            y = int(k)
        except Exception:
            continue
        sal = _coerce_float(v, 0.0)
        out[int(y)] = float(sal)
    return out


def _extract_salary_by_year(contract_like: Any) -> Dict[int, float]:
    if contract_like is None:
        return {}
    m = _get(contract_like, "salary_by_year", None)
    if m is None:
        # tolerated legacy aliases
        m = _get(contract_like, "salary_by_season", None)
    if m is None:
        m = _get(contract_like, "salary_by_season_map", None)
    return coerce_salary_by_year_map(m)


def _extract_start_season_year(contract_like: Any, default: int = 0) -> int:
    return _coerce_int(_get(contract_like, "start_season_year", default), default)


def _extract_years(contract_like: Any, default: int = 0) -> int:
    return _coerce_int(_get(contract_like, "years", default), default)


def _extract_salary_amount(obj_like: Any, default: float = 0.0) -> float:
    # Common snapshot fields: salary_amount (roster), salary (generic), salary_now.
    x = _get(obj_like, "salary_amount", None)
    if x is None:
        x = _get(obj_like, "salary", None)
    if x is None:
        x = _get(obj_like, "salary_now", None)
    return _coerce_float(x, float(default))


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------
def salary_for_season(contract_like: Any, season_year: int) -> float:
    """Return actual salary for `season_year` (0.0 if unknown)."""
    y = int(season_year)
    by_year = _extract_salary_by_year(contract_like)
    if by_year:
        return float(_coerce_float(by_year.get(int(y)), 0.0))

    # Fallback: constant salary across contract years if possible.
    start = _extract_start_season_year(contract_like, 0)
    years = _extract_years(contract_like, 0)
    sal = _extract_salary_amount(contract_like, 0.0)
    if sal <= 0.0:
        return 0.0
    if start > 0 and years > 0:
        end = int(start) + int(years) - 1
        if int(start) <= y <= int(end):
            return float(sal)
    # If we only know a salary amount but no start/years, treat as unknown for other years.
    return 0.0


def salary_schedule(
    contract_like: Any,
    *,
    from_year: int,
    positive_only: bool = True,
) -> List[Tuple[int, float]]:
    """Build remaining salary schedule as a list of (season_year, salary).

    Rules
    -----
    - If salary_by_year exists: use it (filter year>=from_year, salary>0 if positive_only).
    - Else: attempt to synthesize schedule using start_season_year + years + salary_amount.
      If those are missing, fallback to a 1-year schedule if salary_amount is present.

    This is the SSOT for "remaining schedule" used by valuation/package effects.
    """
    cur = int(from_year)
    by_year = _extract_salary_by_year(contract_like)
    out: List[Tuple[int, float]] = []
    if by_year:
        for y, sal in by_year.items():
            yy = int(y)
            ss = float(_coerce_float(sal, 0.0))
            if yy < cur:
                continue
            if positive_only and ss <= 0.0:
                continue
            out.append((yy, ss))
        out.sort(key=lambda t: t[0])
        return out

    # Fallback synthesis.
    start = _extract_start_season_year(contract_like, 0)
    years = _extract_years(contract_like, 0)
    sal = _extract_salary_amount(contract_like, 0.0)

    if sal <= 0.0:
        return []

    if start <= 0:
        # If start year is unknown, assume it is the current year.
        start = cur
    if years <= 0:
        # If years is unknown, assume a 1-year deal (best-effort).
        years = 1

    end = int(start) + int(years) - 1
    for y in range(max(int(start), cur), int(end) + 1):
        if positive_only and sal <= 0.0:
            continue
        out.append((int(y), float(sal)))
    return out


def remaining_years(contract_like: Any, *, current_season_year: int) -> int:
    """SSOT: number of remaining seasons >= current season with salary>0."""
    sched = salary_schedule(contract_like, from_year=int(current_season_year), positive_only=True)
    return int(len(sched))


def remaining_salary_total(contract_like: Any, *, current_season_year: int) -> float:
    """Convenience: sum of remaining salaries (>= current season)."""
    return float(sum(s for _, s in salary_schedule(contract_like, from_year=int(current_season_year), positive_only=True)))


# -----------------------------------------------------------------------------
# Player-focused helper (valuation + package effects will likely use this)
# -----------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class PlayerContractTerms:
    """Interpreted contract terms for a player at a specific 'current season' context."""

    current_season_year: int
    salary_now: float
    remaining_years: int
    schedule: Tuple[Tuple[int, float], ...] = tuple()
    meta: Dict[str, Any] = field(default_factory=dict)


def player_contract_terms(player_like: Any, *, current_season_year: int) -> PlayerContractTerms:
    """Extract contract terms for a player-like object.

    Accepts:
    - PlayerSnapshot-like objects with `.contract` (preferred).
    - Dict-like player objects with `contract` key.
    - If no contract is attached, falls back to `salary_amount` as a 1-year schedule.

    This is intentionally conservative: if contract details are missing, we do NOT guess
    multi-year structure unless we have start+years.
    """
    cur = int(current_season_year)

    contract = _get(player_like, "contract", None)
    if contract is None and isinstance(player_like, Mapping):
        contract = player_like.get("contract")

    used_fallback = False

    if contract is not None:
        sched = salary_schedule(contract, from_year=cur, positive_only=True)
        sal_now = salary_for_season(contract, cur)
        rem = len(sched)
        meta = {"source": "contract", "used_fallback": False}
        return PlayerContractTerms(
            current_season_year=cur,
            salary_now=float(sal_now),
            remaining_years=int(rem),
            schedule=tuple((int(y), float(s)) for y, s in sched),
            meta=meta,
        )

    # Fallback: only current salary known.
    used_fallback = True
    sal_now = _extract_salary_amount(player_like, 0.0)
    sched: List[Tuple[int, float]] = []
    if sal_now > 0.0:
        sched = [(cur, float(sal_now))]
    meta = {"source": "salary_amount", "used_fallback": True}
    return PlayerContractTerms(
        current_season_year=cur,
        salary_now=float(sal_now),
        remaining_years=int(len(sched)),
        schedule=tuple(sched),
        meta=meta,
    )
