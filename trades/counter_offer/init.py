from __future__ import annotations

"""trades/counter_offer/init.py

Public entrypoint for the counter-offer subsystem.

Why init.py (not __init__.py)?
------------------------------
This project sometimes keeps a lightweight `init.py` as an explicit import target
(e.g., db_schema/init.py). The `counter_offer` folder can still be imported as a
namespace package if __init__.py is absent.

Typical usage
-------------
    from trades.counter_offer.init import build_counter_offer

    counter = build_counter_offer(
        offer=deal,
        user_team_id=session["user_team_id"],
        other_team_id=session["other_team_id"],
        current_date=in_game_date,
        db_path=db_path,
        session=session,
    )

The returned object is trades.valuation.types.CounterProposal.
"""

from datetime import date
from typing import Any, Mapping, Optional

from ..models import Deal
from ..generation.generation_tick import TradeGenerationTickContext
from ..valuation.types import CounterProposal

from .config import CounterOfferConfig
from .builder import CounterOfferBuilder


def build_counter_offer(
    *,
    offer: Deal,
    user_team_id: str,
    other_team_id: str,
    current_date: date,
    db_path: str,
    session: Optional[Mapping[str, Any]] = None,
    allow_locked_by_deal_id: Optional[str] = None,
    tick_ctx: Optional[TradeGenerationTickContext] = None,
    config: Optional[CounterOfferConfig] = None,
) -> Optional[CounterProposal]:
    """One-shot helper to build a counter offer."""

    builder = CounterOfferBuilder(config=config)
    return builder.build(
        offer=offer,
        user_team_id=user_team_id,
        other_team_id=other_team_id,
        current_date=current_date,
        db_path=db_path,
        session=session,
        allow_locked_by_deal_id=allow_locked_by_deal_id,
        tick_ctx=tick_ctx,
    )


__all__ = [
    "CounterOfferConfig",
    "CounterOfferBuilder",
    "build_counter_offer",
]
