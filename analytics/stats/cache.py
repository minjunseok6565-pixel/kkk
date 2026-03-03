from __future__ import annotations

"""Caching helpers for leaderboards.

The canonical cache slot is:
    state.cached_views.stats.leaders

The underlying state schema allows this key to be `None` or any JSON-like object.
The game already invalidates this cache on ingest. This module provides
convenience helpers to:
- read cache
- rebuild when missing
- store a rich bundle with meta info

These helpers are defensive so the analytics package can be imported in
contexts where the global `state` module isn't available (e.g., offline tests).
"""

from typing import Any, Mapping

from .leaders import compute_leaderboards
from .types import LeaderboardConfig, LeaderboardsBundle


def _try_import_state():
    try:
        import state  # type: ignore
        return state
    except Exception:
        return None


def get_cached_leaderboards(*, state_module=None) -> Any:
    """Return the raw cached leaderboards object (may be None)."""

    st = state_module or _try_import_state()
    if st is None:
        return None
    try:
        stats_cache = st.get_cached_stats_snapshot() or {}
        if isinstance(stats_cache, Mapping):
            return stats_cache.get("leaders")
    except Exception:
        return None
    return None


def set_cached_leaderboards(bundle: Any, *, state_module=None) -> None:
    """Persist `bundle` under state.cached_views.stats.leaders."""

    st = state_module or _try_import_state()
    if st is None:
        return
    try:
        stats_cache = st.get_cached_stats_snapshot() or {}
        if not isinstance(stats_cache, dict):
            stats_cache = {"leaders": None}
        stats_cache["leaders"] = bundle
        st.set_cached_stats_snapshot(stats_cache)
    except Exception:
        return


def get_or_build_cached_leaderboards(
    *,
    phase: str = "regular",
    config: LeaderboardConfig | None = None,
    state_module=None,
) -> LeaderboardsBundle:
    """Return cached leaderboards for `phase`, building them when necessary.

    Cache shape stored by this function:

        {
          "meta": {...},
          "by_phase": {
             "regular": <LeaderboardsBundle>,
             "playoffs": <LeaderboardsBundle>,
             "play_in": <LeaderboardsBundle>
          }
        }
    """

    st = state_module or _try_import_state()
    if st is None:
        return {"meta": {"phase": phase, "note": "state module not available"}, "per_game": {}, "totals": {}, "per_36": {}, "advanced": {}}

    raw = get_cached_leaderboards(state_module=st)
    if isinstance(raw, Mapping):
        by_phase = raw.get("by_phase")
        if isinstance(by_phase, Mapping) and phase in by_phase and isinstance(by_phase.get(phase), Mapping):
            return by_phase[phase]  # type: ignore[return-value]

    workflow = st.export_workflow_state() or {}
    if not isinstance(workflow, Mapping):
        workflow = {}

    if phase == "regular":
        player_stats = workflow.get("player_stats") or {}
        team_stats = workflow.get("team_stats") or {}
    else:
        pr = (workflow.get("phase_results") or {}).get(phase, {}) if isinstance(workflow.get("phase_results"), Mapping) else {}
        player_stats = pr.get("player_stats") or {}
        team_stats = pr.get("team_stats") or {}

    bundle = compute_leaderboards(player_stats, team_stats, phase=phase, config=config)

    outer = {
        "meta": {
            "built_from_turn": int(workflow.get("turn") or -1),
            "active_season_id": workflow.get("active_season_id"),
            "phases": ["regular", "play_in", "playoffs"],
        },
        "by_phase": {phase: bundle},
    }
    set_cached_leaderboards(outer, state_module=st)
    return bundle
