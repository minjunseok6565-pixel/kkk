"""Top-level package for derived analytics.

This package is intentionally *read-only* with respect to the game's sources of truth:
- The SQLite repository (LeagueRepo)
- The in-memory workflow state (state.py)

Analytics modules compute *derived views* such as leaderboards, advanced metrics,
and award candidate lists suitable for UI, news, and season reports.
"""

from __future__ import annotations

from . import stats

__all__ = ["stats"]
