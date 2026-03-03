from __future__ import annotations

"""Shared trade rules utilities.

This module is intended to be the SSOT (single source of truth) for parsing
league-level trade rule configuration values that multiple layers rely on
(generator, rules/validator, orchestration, etc.).

Currently it only exposes `parse_trade_deadline`, but other small, pure
utilities that need to be consistent across layers may live here.

Design goals:
- Robust against common input shapes (date, datetime, ISO date string,
  ISO datetime string).
- Deterministic output: returns a `datetime.date` or None.
- Explicit failure: invalid values raise `ValueError` so callers can decide
  their fail-open/fail-closed policy.
"""

from datetime import date, datetime
import re
from typing import Any, Optional


# Match an ISO date prefix at the beginning of a string (YYYY-MM-DD)
_ISO_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


# Trade window reopens in offseason (matches server.py enter-offseason date jump).
DEFAULT_OFFSEASON_TRADE_REOPEN_MONTH = 7
DEFAULT_OFFSEASON_TRADE_REOPEN_DAY = 1


def parse_trade_deadline(value: Any) -> Optional[date]:
    """Parse a league trade deadline value into a `date`.

    Accepted inputs:
    - None / "" / whitespace -> None
    - datetime.date -> same date
    - datetime.datetime -> `.date()`
    - str -> either an ISO date ("YYYY-MM-DD") or an ISO datetime string that
      *starts with* "YYYY-MM-DD" (e.g. "YYYY-MM-DDTHH:MM:SS" or
      "YYYY-MM-DD HH:MM:SS").

    Returns:
        A `date` if parsing succeeds, otherwise None when the value is empty.

    Raises:
        ValueError: if the value is present but cannot be parsed.
    """

    if value is None:
        return None

    # `datetime` is also an instance of `date`, so check it first.
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    s = str(value).strip()
    if not s:
        return None

    # Accept ISO datetime strings by taking the ISO date prefix.
    m = _ISO_DATE_PREFIX_RE.match(s)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError as exc:
            raise ValueError(
                f"Invalid trade_deadline date prefix: {m.group(1)!r} (raw={value!r})"
            ) from exc

    # Fallback: attempt to parse as a direct ISO date string.
    try:
        return date.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(
            f"Invalid trade_deadline: {value!r}. Expected ISO 'YYYY-MM-DD' or an ISO datetime starting with that date."
        ) from exc


def offseason_trade_reopen_date(
    trade_deadline: date,
    *,
    reopen_month: int = DEFAULT_OFFSEASON_TRADE_REOPEN_MONTH,
    reopen_day: int = DEFAULT_OFFSEASON_TRADE_REOPEN_DAY,
) -> date:
    """Return the date when trading re-opens after the deadline within the same league year.

    By default, this is July 1st of the deadline year (e.g. 2026-07-01 for a 2026-02-05 deadline).
    """

    return date(int(trade_deadline.year), int(reopen_month), int(reopen_day))


def is_trade_window_open(
    *,
    current_date: date,
    trade_deadline: Optional[date],
    reopen_month: int = DEFAULT_OFFSEASON_TRADE_REOPEN_MONTH,
    reopen_day: int = DEFAULT_OFFSEASON_TRADE_REOPEN_DAY,
) -> bool:
    """SSOT: decide whether the trade window is open on `current_date`.

    Policy:
    - If no deadline configured -> open.
    - If current_date is on/before deadline -> open.
    - If current_date is after deadline -> closed UNTIL offseason reopen date (default: Jul 1 of deadline year).
      From reopen date onward -> open again.

    This fixes the 'offseason date jump' (server.py sets current_date to next_year-07-01)
    without requiring season_year/trade_deadline to be updated immediately.
    """

    if trade_deadline is None:
        return True

    if current_date <= trade_deadline:
        return True

    reopen_date = offseason_trade_reopen_date(
        trade_deadline,
        reopen_month=int(reopen_month),
        reopen_day=int(reopen_day),
    )
    return current_date >= reopen_date
