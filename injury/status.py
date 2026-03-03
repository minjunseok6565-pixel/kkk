from __future__ import annotations

"""Injury status helpers.

This module centralizes the project-wide interpretation of injury state fields
into a simple status for a given date.

Why this exists
---------------
Multiple subsystems (injury service, readiness, practice, AI hints) need to
interpret the same injury SSOT fields:

- out_until_date
- returning_until_date

Historically this logic lived as a private helper in ``injury/service.py``.
Moving it here removes duplication and prevents subtle drift between systems.

Status semantics (SSOT)
-----------------------
The stored dates define half-open ranges using ISO date strings (YYYY-MM-DD):

- OUT:
    ``on_date < out_until_date``
- RETURNING:
    ``out_until_date <= on_date < returning_until_date`` (when returning_until_date is present)
- HEALTHY:
    otherwise

Important: on the first day the player is available again (``on_date == out_until_date``),
the status is considered RETURNING (not OUT). This matches how returning debuffs are scaled
in the injury service.

All callers should prefer :func:`status_for_date` and the convenience predicates below,
rather than re-implementing comparisons.
"""

from typing import Any, Mapping

STATUS_OUT = "OUT"
STATUS_RETURNING = "RETURNING"
STATUS_HEALTHY = "HEALTHY"


def _date10(value: Any) -> str:
    """Best-effort conversion to YYYY-MM-DD (first 10 chars)."""
    if value is None:
        return ""
    try:
        return str(value)[:10]
    except Exception:
        return ""


def status_for_date(state: Mapping[str, Any] | None, *, on_date_iso: str) -> str:
    """Return normalized injury status for ``on_date_iso``.

    Parameters
    ----------
    state:
        Injury state mapping (typically a row from ``player_injury_state``).
        If ``None`` or missing required keys, the player is treated as HEALTHY.
    on_date_iso:
        Date (or datetime-like string) for which to evaluate status. Only the
        first 10 characters are used.

    Returns
    -------
    str
        One of: ``"OUT"``, ``"RETURNING"``, ``"HEALTHY"``.

    Notes
    -----
    This logic intentionally mirrors the legacy implementation previously found in
    ``injury/service.py::_normalize_status_for_date``.
    """
    if not state:
        return STATUS_HEALTHY

    date_iso = _date10(on_date_iso)
    out_until = state.get("out_until_date")
    returning_until = state.get("returning_until_date")

    if out_until and date_iso < _date10(out_until):
        return STATUS_OUT
    if returning_until and date_iso < _date10(returning_until):
        return STATUS_RETURNING
    return STATUS_HEALTHY


def is_out(state: Mapping[str, Any] | None, *, on_date_iso: str) -> bool:
    """True iff the player is OUT on the given date."""
    return status_for_date(state, on_date_iso=on_date_iso) == STATUS_OUT


def is_returning(state: Mapping[str, Any] | None, *, on_date_iso: str) -> bool:
    """True iff the player is in RETURNING status on the given date."""
    return status_for_date(state, on_date_iso=on_date_iso) == STATUS_RETURNING


def is_healthy(state: Mapping[str, Any] | None, *, on_date_iso: str) -> bool:
    """True iff the player is HEALTHY on the given date."""
    return status_for_date(state, on_date_iso=on_date_iso) == STATUS_HEALTHY
