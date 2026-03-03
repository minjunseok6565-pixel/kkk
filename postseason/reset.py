from __future__ import annotations

"""Postseason reset utilities.

This module centralizes the reset behavior so it's consistent across:
- /api/postseason/reset
- initialize_postseason (fresh start)
"""

from copy import deepcopy
import logging
from typing import Any, Dict

import state

logger = logging.getLogger(__name__)


def _default_phase_container() -> Dict[str, Any]:
    return {"games": [], "player_stats": {}, "team_stats": {}, "game_results": {}}


def _clear_phase_results_container(phase: str) -> None:
    """Best-effort clear of state.phase_results[phase].

    There is currently no public facade API for this in state.py, so we use
    `state_modules.state_store.transaction` as a controlled escape hatch.

    This keeps schema invariants intact and avoids postseason stat leakage across resets.
    """
    phase = str(phase)
    if phase not in {"preseason", "play_in", "playoffs"}:
        return

    # If a future facade exists, prefer it.
    if hasattr(state, "clear_phase_results") and callable(getattr(state, "clear_phase_results")):
        try:
            state.clear_phase_results(phase)  # type: ignore[attr-defined]
            return
        except Exception:
            logger.warning("clear_phase_results facade failed; falling back", exc_info=True)

    try:
        from state_modules.state_store import transaction
    except Exception:
        logger.warning("state_modules.state_store.transaction not available; skip phase reset", exc_info=True)
        return

    default_container = _default_phase_container()
    try:
        with transaction(f"postseason_reset:clear_phase_results[{phase}]") as gs:
            pr = gs.get("phase_results")
            if isinstance(pr, dict) and phase in pr and isinstance(pr[phase], dict):
                pr[phase] = deepcopy(default_container)
    except Exception:
        logger.warning("Failed to clear phase_results[%s]", phase, exc_info=True)


def reset_postseason_state() -> Dict[str, Any]:
    """Reset postseason + postseason-related caches.

    Effects:
    - state.postseason reset
    - phase_results.play_in & phase_results.playoffs cleared (best-effort)
    - cached_views.playoff_news cleared
    - cached_views.stats.leaders invalidated

    Returns:
        The new postseason snapshot (dict)
    """
    state.postseason_reset()

    # Clear non-regular result containers to prevent stat leakage.
    _clear_phase_results_container("play_in")
    _clear_phase_results_container("playoffs")

    # Clear playoff news cache (schema 4.2 exact keys).
    state.set_cached_playoff_news_snapshot(
        {
            "processed_game_ids": [],
            "built_from_turn": -1,
            "season_id": state.get_active_season_id(),
            "generator_version": "news.playoffs.v3",
            "items": [],
        }
    )

    # Invalidate stats leaders cache (UI-only).
    stats_cache = state.get_cached_stats_snapshot() or {}
    # Keep only the allowed key shape; state_schema enforces exact keys.
    stats_cache = {"leaders": None}
    state.set_cached_stats_snapshot(stats_cache)

    return state.get_postseason_snapshot()
