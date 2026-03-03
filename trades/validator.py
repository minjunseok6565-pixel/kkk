from __future__ import annotations

from datetime import date
from typing import Optional, TYPE_CHECKING

from .errors import TradeError, DEAL_INVALIDATED
from .models import Deal, canonicalize_deal
from .rules import build_trade_context, validate_all

if TYPE_CHECKING:
    from .rules.tick_context import TradeRuleTickContext


def validate_deal(
    deal: Deal,
    current_date: Optional[date] = None,
    allow_locked_by_deal_id: Optional[str] = None,
    db_path: Optional[str] = None,
    tick_ctx: Optional["TradeRuleTickContext"] = None,
    integrity_check: Optional[bool] = None,
) -> None:
    # Avoid frame inspection inside build_trade_context; pass the lock exception explicitly.
    extra = None
    if allow_locked_by_deal_id is not None:
        extra = {"allow_locked_by_deal_id": str(allow_locked_by_deal_id)}

    ctx = None
    try:
        deal = canonicalize_deal(deal)

        ctx = build_trade_context(
            deal,
            current_date=current_date,
            db_path=db_path,
            tick_ctx=tick_ctx,
            extra=extra,
        )
        if integrity_check is None:
            # Default: validate integrity once for standalone calls,
            # or once per tick_ctx if tick_ctx was built with validate_integrity=False.
            if tick_ctx is None:
                integrity_check = True
            else:
                integrity_check = not getattr(tick_ctx, "integrity_validated", False)

        if integrity_check:
            ctx.repo.validate_integrity()
            if tick_ctx is not None:
                try:
                    tick_ctx.integrity_validated = True
                except Exception:
                    pass

        prepared_rules = getattr(tick_ctx, "prepared_rules", None) if tick_ctx is not None else None
        validate_all(deal, ctx, prepared_rules=prepared_rules)
    except TradeError:
        raise
    except Exception as exc:
        # Convert any unexpected failures (legacy payloads, corrupted state, etc.)
        # into TradeError so API layers never return a 500 for bad trade inputs.
        raise TradeError(
            DEAL_INVALIDATED,
            "Trade validation failed due to invalid/legacy state",
            {"exc_type": type(exc).__name__, "error": str(exc)},
        ) from exc
    finally:
        # Validator closes ctx.repo only if it owns the repo.
        if ctx is not None and getattr(ctx, "owns_repo", True):
            repo = getattr(ctx, "repo", None)
            if repo is not None:
                repo.close()
