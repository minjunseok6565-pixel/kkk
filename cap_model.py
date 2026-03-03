from __future__ import annotations

"""cap_model.py

Season-based salary cap (and apron) model SSOT.

Why this exists
---------------
The project historically had multiple copies of "cap for season" math spread across:
- state_modules/state_cap.py (original SSOT, mutates league.trade_rules)
- trades/valuation/* (pricing + package effects)
- contracts/options_policy.py
- draft/apply.py

That duplication makes SSOT splits inevitable. This module is the intended *single*
computation engine for season-based cap numbers.

Design goals
------------
- Deterministic, dependency-light.
- Can be constructed from `league.trade_rules` (preferred), but also from defaults.
- Supports two modes:
  1) Auto-updating cap (default): uses base season/year + annual growth + rounding.
  2) Frozen cap (`cap_auto_update=False`): treats current cap as a constant across seasons
     (recommended safe fallback for simulations that disable cap evolution).

- Also computes salary matching bracket parameters (below first apron) exactly like
  state_modules/state_cap.py, so that validator/rules and valuation can share one source.

Integration plan (next steps in your refactor)
----------------------------------------------
- Replace state_modules/state_cap._apply_cap_model_for_season()'s internal math with
  CapModel.apply_to_league(...).
- Replace valuation's local `_salary_cap_for_season()` copies with
  `env.cap_model.salary_cap_for_season(...)`.

This file intentionally does NOT import LeagueRepo / state modules to avoid circular imports.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, MutableMapping, Optional

from salary_matching_brackets import derive_salary_matching_brackets

# -----------------------------------------------------------------------------
# Defaults (fallbacks) - keep import flexible so this module survives refactors.
# -----------------------------------------------------------------------------
try:
    from config import (
        CAP_ANNUAL_GROWTH_RATE,
        CAP_BASE_FIRST_APRON,
        CAP_BASE_SALARY_CAP,
        CAP_BASE_SECOND_APRON,
        CAP_BASE_SEASON_YEAR,
        CAP_ROUND_UNIT,
    )
except Exception:  # pragma: no cover
    CAP_BASE_SEASON_YEAR = 2025
    CAP_BASE_SALARY_CAP = 154_647_000
    CAP_BASE_FIRST_APRON = 195_945_000
    CAP_BASE_SECOND_APRON = 207_824_000
    CAP_ANNUAL_GROWTH_RATE = 0.10
    CAP_ROUND_UNIT = 1000

DEFAULT_MATCH_BASE_MID_ADD = 8_527_000.0
DEFAULT_MATCH_BUFFER = 250_000


# -----------------------------------------------------------------------------
# Small coercion helpers (defensive; do not raise)
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


def _round_unit(unit: Any) -> int:
    u = _coerce_int(unit, int(CAP_ROUND_UNIT) or 1)
    if u <= 0:
        u = int(CAP_ROUND_UNIT) or 1
    if u <= 0:
        u = 1
    return int(u)


def _round_to_unit(value: float, unit: int) -> int:
    # Mirrors state_modules/state_cap.py behavior (banker's rounding via round()).
    u = _round_unit(unit)
    return int(round(float(value) / float(u)) * int(u))


# -----------------------------------------------------------------------------
# Public dataclasses
# -----------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CapNumbers:
    """Computed cap/apron numbers for a season (and derived matching parameters)."""

    season_year: int
    salary_cap: int
    first_apron: int
    second_apron: int

    # Salary matching parameters (below 1st apron). Optional if match auto-update is disabled.
    match_mid_add: Optional[int] = None
    match_small_out_max: Optional[int] = None
    match_mid_out_max: Optional[int] = None

    # Small debug/meta fields (non-SSOT, purely explainability).
    meta: Dict[str, Any] = field(default_factory=dict)

    def as_trade_rules_patch(self) -> Dict[str, int]:
        """Return the dict patch that would bring trade_rules in sync for this season."""
        patch: Dict[str, int] = {
            "salary_cap": int(self.salary_cap),
            "first_apron": int(self.first_apron),
            "second_apron": int(self.second_apron),
        }
        if self.match_mid_add is not None:
            patch["match_mid_add"] = int(self.match_mid_add)
        if self.match_small_out_max is not None:
            patch["match_small_out_max"] = int(self.match_small_out_max)
        if self.match_mid_out_max is not None:
            patch["match_mid_out_max"] = int(self.match_mid_out_max)
        return patch


@dataclass(frozen=True, slots=True)
class CapModelParams:
    """Parameters for CapModel.

    Keys map to league.trade_rules (when present). The values are kept as "raw"
    python primitives so they are JSON-friendly and easy to log.

    When `cap_auto_update` is False, CapModelParams is normalized to a *frozen*
    model: growth rate becomes 0 and base cap/aprons become the current season's
    (or the provided) cap values.
    """

    cap_auto_update: bool
    cap_base_season_year: int
    cap_base_salary_cap: float
    cap_base_first_apron: float
    cap_base_second_apron: float
    cap_annual_growth_rate: float
    cap_round_unit: int

    match_auto_update: bool
    match_base_mid_add: float
    match_buffer: int

    # If match_auto_update is disabled, these frozen values (if provided) will be returned.
    frozen_match_mid_add: Optional[int] = None
    frozen_match_small_out_max: Optional[int] = None
    frozen_match_mid_out_max: Optional[int] = None

    @classmethod
    def from_trade_rules(
        cls,
        trade_rules: Optional[Mapping[str, Any]],
        *,
        current_season_year: Optional[int] = None,
    ) -> "CapModelParams":
        tr: Mapping[str, Any] = trade_rules if isinstance(trade_rules, Mapping) else {}

        # state_cap semantics: auto-update unless explicitly False.
        cap_auto_update = tr.get("cap_auto_update") is not False

        base_year = _coerce_int(tr.get("cap_base_season_year"), int(CAP_BASE_SEASON_YEAR))
        base_cap = _coerce_float(tr.get("cap_base_salary_cap"), float(CAP_BASE_SALARY_CAP))
        base_first = _coerce_float(tr.get("cap_base_first_apron"), float(CAP_BASE_FIRST_APRON))
        base_second = _coerce_float(tr.get("cap_base_second_apron"), float(CAP_BASE_SECOND_APRON))
        growth = _coerce_float(tr.get("cap_annual_growth_rate"), float(CAP_ANNUAL_GROWTH_RATE))
        round_unit = _round_unit(tr.get("cap_round_unit", int(CAP_ROUND_UNIT) or 1))

        # Salary matching params (below 1st apron). Default: auto-update enabled.
        match_auto_update = tr.get("match_auto_update") is not False
        match_base_mid_add = _coerce_float(tr.get("match_base_mid_add"), float(DEFAULT_MATCH_BASE_MID_ADD))
        match_buffer = _coerce_int(tr.get("match_buffer"), int(DEFAULT_MATCH_BUFFER))
        if match_buffer < 0:
            match_buffer = 0

        # ---------------------------------------------------------------------
        # Frozen-cap mode (cap_auto_update=False)
        # ---------------------------------------------------------------------
        if not cap_auto_update:
            # Recommended behavior:
            # - Treat cap as constant across seasons.
            # - Freeze at the cap currently present in trade_rules (if any); else fallback to base values.
            salary_cap_now = _coerce_int(tr.get("salary_cap"), int(_round_to_unit(base_cap, round_unit)))
            if salary_cap_now <= 0:
                salary_cap_now = int(_round_to_unit(float(CAP_BASE_SALARY_CAP), round_unit))
                if salary_cap_now <= 0:
                    salary_cap_now = 1

            first_apron_now = _coerce_int(tr.get("first_apron"), int(_round_to_unit(base_first, round_unit)))
            if first_apron_now < salary_cap_now:
                first_apron_now = int(salary_cap_now)

            second_apron_now = _coerce_int(tr.get("second_apron"), int(_round_to_unit(base_second, round_unit)))
            if second_apron_now < first_apron_now:
                second_apron_now = int(first_apron_now)

            # Freeze base season at "now" if provided (only affects meta/debug; growth is 0 anyway).
            frozen_base_year = int(current_season_year) if current_season_year is not None else int(base_year)

            # Freeze matching parameters as well (state_cap returns early when cap_auto_update=False).
            frozen_mid_add: Optional[int] = None
            frozen_small_max: Optional[int] = None
            frozen_mid_max: Optional[int] = None

            if "match_mid_add" in tr or "match_small_out_max" in tr or "match_mid_out_max" in tr:
                # Preserve existing explicit values.
                if "match_mid_add" in tr:
                    frozen_mid_add = _coerce_int(tr.get("match_mid_add"), 0)
                if "match_small_out_max" in tr:
                    frozen_small_max = _coerce_int(tr.get("match_small_out_max"), 0)
                if "match_mid_out_max" in tr:
                    frozen_mid_max = _coerce_int(tr.get("match_mid_out_max"), 0)
            else:
                # If not present, derive once using the same scaling rule as state_cap:
                # scaled_mid_add = base_mid_add * (cap_now / base_salary_cap_for_match).
                base_salary_cap_for_match = base_cap
                if base_salary_cap_for_match <= 0:
                    base_salary_cap_for_match = float(CAP_BASE_SALARY_CAP) or 1.0
                if base_salary_cap_for_match <= 0:
                    base_salary_cap_for_match = 1.0

                scaled_mid_add = float(match_base_mid_add) * (float(salary_cap_now) / float(base_salary_cap_for_match))
                mm = _round_to_unit(scaled_mid_add, round_unit)
                if mm < match_buffer:
                    mm = int(match_buffer)
                frozen_mid_add = int(mm)
                frozen_small_max, frozen_mid_max = derive_salary_matching_brackets(
                    match_mid_add_d=int(frozen_mid_add),
                    match_buffer_d=int(match_buffer),
                )

            return cls(
                cap_auto_update=False,
                cap_base_season_year=int(frozen_base_year),
                cap_base_salary_cap=float(salary_cap_now),
                cap_base_first_apron=float(first_apron_now),
                cap_base_second_apron=float(second_apron_now),
                cap_annual_growth_rate=0.0,
                cap_round_unit=int(round_unit),
                match_auto_update=False,
                match_base_mid_add=float(match_base_mid_add),
                match_buffer=int(match_buffer),
                frozen_match_mid_add=frozen_mid_add,
                frozen_match_small_out_max=frozen_small_max,
                frozen_match_mid_out_max=frozen_mid_max,
            )

        # Normal auto-updating model.
        return cls(
            cap_auto_update=True,
            cap_base_season_year=int(base_year),
            cap_base_salary_cap=float(base_cap),
            cap_base_first_apron=float(base_first),
            cap_base_second_apron=float(base_second),
            cap_annual_growth_rate=float(growth),
            cap_round_unit=int(round_unit),
            match_auto_update=bool(match_auto_update),
            match_base_mid_add=float(match_base_mid_add),
            match_buffer=int(match_buffer),
            frozen_match_mid_add=None,
            frozen_match_small_out_max=None,
            frozen_match_mid_out_max=None,
        )

    @classmethod
    def defaults(cls) -> "CapModelParams":
        """Build params purely from config defaults (no trade_rules)."""
        return cls.from_trade_rules({}, current_season_year=None)


# -----------------------------------------------------------------------------
# CapModel (SSOT engine)
# -----------------------------------------------------------------------------
@dataclass(slots=True)
class CapModel:
    """Season-cap model SSOT engine (pure computations + optional caching)."""

    params: CapModelParams
    _cache: Dict[int, CapNumbers] = field(default_factory=dict, init=False, repr=False)

    # ---- constructors ----
    @classmethod
    def from_trade_rules(
        cls,
        trade_rules: Optional[Mapping[str, Any]],
        *,
        current_season_year: Optional[int] = None,
    ) -> "CapModel":
        return cls(params=CapModelParams.from_trade_rules(trade_rules, current_season_year=current_season_year))

    @classmethod
    def from_league(
        cls,
        league: Optional[Mapping[str, Any]],
        *,
        current_season_year: Optional[int] = None,
    ) -> "CapModel":
        lg: Mapping[str, Any] = league if isinstance(league, Mapping) else {}
        tr = lg.get("trade_rules") if isinstance(lg.get("trade_rules"), Mapping) else {}
        return cls.from_trade_rules(tr, current_season_year=current_season_year)

    @classmethod
    def defaults(cls) -> "CapModel":
        return cls(params=CapModelParams.defaults())

    # ---- accessors ----
    def numbers_for_season(self, season_year: int) -> CapNumbers:
        y = int(season_year)
        cached = self._cache.get(y)
        if cached is not None:
            return cached
        nums = self._compute_numbers_for_season(y)
        self._cache[y] = nums
        return nums

    def salary_cap_for_season(self, season_year: int) -> int:
        return int(self.numbers_for_season(int(season_year)).salary_cap)

    def first_apron_for_season(self, season_year: int) -> int:
        return int(self.numbers_for_season(int(season_year)).first_apron)

    def second_apron_for_season(self, season_year: int) -> int:
        return int(self.numbers_for_season(int(season_year)).second_apron)

    # ---- application (mutating helpers) ----
    def apply_to_trade_rules(
        self,
        trade_rules: MutableMapping[str, Any],
        season_year: int,
        *,
        respect_auto_update: bool = True,
    ) -> CapNumbers:
        """Apply season numbers to `trade_rules` (mutating) and return the numbers.

        If `respect_auto_update` is True (default) and params.cap_auto_update is False,
        this method will *not* mutate `trade_rules` (mirrors state_cap behavior).
        """
        nums = self.numbers_for_season(int(season_year))
        if respect_auto_update and (self.params.cap_auto_update is False):
            return nums

        patch = nums.as_trade_rules_patch()
        for k, v in patch.items():
            trade_rules[k] = int(v)
        return nums

    def apply_to_league(
        self,
        league: MutableMapping[str, Any],
        season_year: int,
        *,
        respect_auto_update: bool = True,
    ) -> CapNumbers:
        """Apply season numbers to `league['trade_rules']` (mutating) and return numbers."""
        tr = league.get("trade_rules")
        if not isinstance(tr, dict):
            tr = {}
            league["trade_rules"] = tr
        return self.apply_to_trade_rules(tr, int(season_year), respect_auto_update=respect_auto_update)

    # ---- internal compute ----
    def _compute_numbers_for_season(self, season_year: int) -> CapNumbers:
        p = self.params

        years_passed = int(season_year) - int(p.cap_base_season_year)
        multiplier = (1.0 + float(p.cap_annual_growth_rate)) ** int(years_passed)

        salary_cap = _round_to_unit(float(p.cap_base_salary_cap) * float(multiplier), int(p.cap_round_unit))
        first_apron = _round_to_unit(float(p.cap_base_first_apron) * float(multiplier), int(p.cap_round_unit))
        second_apron = _round_to_unit(float(p.cap_base_second_apron) * float(multiplier), int(p.cap_round_unit))

        # Enforce monotonic ordering (mirrors state_cap).
        if first_apron < salary_cap:
            first_apron = int(salary_cap)
        if second_apron < first_apron:
            second_apron = int(first_apron)

        match_mid_add: Optional[int] = None
        match_small_out_max: Optional[int] = None
        match_mid_out_max: Optional[int] = None

        # Salary matching auto update: mirrors state_cap, but uses params instead of reading trade_rules.
        if p.match_auto_update:
            base_salary_cap_for_match = float(p.cap_base_salary_cap)
            if base_salary_cap_for_match <= 0:
                base_salary_cap_for_match = float(CAP_BASE_SALARY_CAP)
            if base_salary_cap_for_match <= 0:
                base_salary_cap_for_match = 1.0

            match_buffer_d = int(round(float(p.match_buffer)))
            if match_buffer_d < 0:
                match_buffer_d = 0

            scaled_mid_add = float(p.match_base_mid_add) * (float(salary_cap) / float(base_salary_cap_for_match))
            match_mid_add_d = _round_to_unit(scaled_mid_add, int(p.cap_round_unit))
            if match_mid_add_d < match_buffer_d:
                match_mid_add_d = int(match_buffer_d)

            match_small_out_max_d, match_mid_out_max_d = derive_salary_matching_brackets(
                match_mid_add_d=int(match_mid_add_d),
                match_buffer_d=int(match_buffer_d),
            )

            match_mid_add = int(match_mid_add_d)
            match_small_out_max = int(match_small_out_max_d)
            match_mid_out_max = int(match_mid_out_max_d)
        else:
            # Frozen values (if provided).
            if p.frozen_match_mid_add is not None:
                match_mid_add = int(p.frozen_match_mid_add)
            if p.frozen_match_small_out_max is not None:
                match_small_out_max = int(p.frozen_match_small_out_max)
            if p.frozen_match_mid_out_max is not None:
                match_mid_out_max = int(p.frozen_match_mid_out_max)

        meta = {
            "years_passed": int(years_passed),
            "multiplier": float(multiplier),
            "cap_auto_update": bool(p.cap_auto_update),
            "match_auto_update": bool(p.match_auto_update),
        }

        return CapNumbers(
            season_year=int(season_year),
            salary_cap=int(salary_cap),
            first_apron=int(first_apron),
            second_apron=int(second_apron),
            match_mid_add=match_mid_add,
            match_small_out_max=match_small_out_max,
            match_mid_out_max=match_mid_out_max,
            meta=meta,
        )
