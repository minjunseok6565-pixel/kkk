from __future__ import annotations

"""trades/valuation/env.py

Valuation runtime environment (SSOT container).

What it is
----------
Valuation needs a small set of "global" context that should NOT be stored inside
snapshots (PlayerSnapshot/ContractSnapshot), because it changes per tick / per league:

- current season year
- salary cap model (derived from league.trade_rules)

This module defines a lightweight container (ValuationEnv) that should be passed
through valuation layers (market pricing, team utility, package effects) so they
all reference the same SSOT.

Next steps (in your refactor)
-----------------------------
- service.py should build ValuationEnv once per evaluation:
    env = ValuationEnv.from_trade_rules(trade_rules, current_season_year=season_year)

- market_pricing/package_effects/team_utility should accept env and use:
    env.current_season_year
    env.cap_model.salary_cap_for_season(...)
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from cap_model import CapModel, CapNumbers


@dataclass(frozen=True, slots=True)
class ValuationEnv:
    """Runtime context for valuation functions."""

    current_season_year: int
    cap_model: CapModel
    meta: Dict[str, Any] = field(default_factory=dict)

    # ---- convenience constructors ----
    @classmethod
    def from_trade_rules(
        cls,
        trade_rules: Mapping[str, Any],
        *,
        current_season_year: int,
    ) -> "ValuationEnv":
        y = int(current_season_year)
        cap_model = CapModel.from_trade_rules(trade_rules, current_season_year=y)
        return cls(current_season_year=y, cap_model=cap_model)

    @classmethod
    def from_league(
        cls,
        league: Mapping[str, Any],
        *,
        current_season_year: Optional[int] = None,
    ) -> "ValuationEnv":
        # Fall back to league['season_year'] if caller doesn't provide it.
        y = current_season_year
        if y is None:
            try:
                y = int(league.get("season_year"))  # type: ignore[arg-type]
            except Exception:
                y = 0
        cap_model = CapModel.from_league(league, current_season_year=int(y) if y else None)
        return cls(current_season_year=int(y) if y else 0, cap_model=cap_model)

    # ---- cap accessors ----
    def cap_numbers(self, season_year: Optional[int] = None) -> CapNumbers:
        y = int(self.current_season_year if season_year is None else season_year)
        return self.cap_model.numbers_for_season(y)

    @property
    def cap_now(self) -> CapNumbers:
        return self.cap_numbers(self.current_season_year)

    def salary_cap(self, season_year: Optional[int] = None) -> int:
        return int(self.cap_numbers(season_year).salary_cap)

    def first_apron(self, season_year: Optional[int] = None) -> int:
        return int(self.cap_numbers(season_year).first_apron)

    def second_apron(self, season_year: Optional[int] = None) -> int:
        return int(self.cap_numbers(season_year).second_apron)

    # ---- env transforms ----
    def with_season_year(self, season_year: int) -> "ValuationEnv":
        """Return a new env with a different 'current season year' (same cap model)."""
        return ValuationEnv(current_season_year=int(season_year), cap_model=self.cap_model, meta=dict(self.meta))
