from __future__ import annotations

from typing import Any, Dict

from cap_model import CapModel


def _apply_cap_model_for_season(league: Dict[str, Any], season_year: int) -> None:
    """Apply the season-specific cap/apron values to trade rules.

    This is the SSOT for season-based cap numbers. We also piggy-back salary
    matching (below-1st-apron) parameters here because, in the real NBA, those
    thresholds scale with the cap over time.
    """
    trade_rules = league.setdefault("trade_rules", {})
    
    # Pass season_year so frozen-cap mode can freeze the "base year" meaningfully
    # for meta/debug (growth is 0 in that mode anyway).
    cap_model = CapModel.from_trade_rules(trade_rules, current_season_year=int(season_year))

    # Mirrors legacy semantics:
    # - if cap_auto_update is explicitly False, do not mutate trade_rules.
    cap_model.apply_to_trade_rules(trade_rules, int(season_year), respect_auto_update=True)
