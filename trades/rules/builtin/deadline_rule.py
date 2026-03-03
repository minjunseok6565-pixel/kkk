from __future__ import annotations

from dataclasses import dataclass

from ...errors import TRADE_DEADLINE_INVALID, TRADE_DEADLINE_PASSED, TradeError
from ...trade_rules import parse_trade_deadline, is_trade_window_open, offseason_trade_reopen_date
from ..base import TradeContext


@dataclass
class DeadlineRule:
    rule_id: str = "deadline"
    priority: int = 10
    enabled: bool = True

    def validate(self, deal, ctx: TradeContext) -> None:
        trade_deadline = (
            ctx.game_state.get("league", {}).get("trade_rules", {}).get("trade_deadline")
        )
        if not trade_deadline:
            return

        try:
            deadline_date = parse_trade_deadline(trade_deadline)
        except ValueError:
            # Fail-closed: invalid league config should block trades.
            raise TradeError(
                TRADE_DEADLINE_INVALID,
                "Invalid trade deadline config",
                {"trade_deadline": str(trade_deadline)},
            )

        if deadline_date is None:
            return

        if not is_trade_window_open(current_date=ctx.current_date, trade_deadline=deadline_date):
            reopen = offseason_trade_reopen_date(deadline_date)
            raise TradeError(
                TRADE_DEADLINE_PASSED,
                "Trade deadline has passed",
                {
                    "current_date": ctx.current_date.isoformat(),
                    "deadline": deadline_date.isoformat(),
                    "trade_reopens": reopen.isoformat(),
                    "trade_deadline_raw": str(trade_deadline),
                },
            )
