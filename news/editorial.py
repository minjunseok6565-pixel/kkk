from __future__ import annotations

from collections import Counter
from typing import List, Set

from .models import NewsEvent


def _teams(e: NewsEvent) -> Set[str]:
    return {str(t) for t in (e.get("related_team_ids") or []) if t}


def _etype(e: NewsEvent) -> str:
    return str(e.get("type") or "")


def select_top_events(
    events: List[NewsEvent],
    *,
    min_count: int = 3,
    max_count: int = 6,
    max_per_team: int = 2,
    max_per_player: int = 1,
) -> List[NewsEvent]:
    """Select a curated set of events for publication.

    Editorial goals:
    - importance first (score)
    - but avoid monotony: limit per team/player and prevent long runs of same type
    """
    if not events:
        return []

    # Sort primarily by importance, then by date desc for freshness.
    ordered = sorted(
        events,
        key=lambda e: (float(e.get("importance") or 0.0), str(e.get("date") or "")),
        reverse=True,
    )

    selected: List[NewsEvent] = []
    team_counts: Counter[str] = Counter()
    player_counts: Counter[str] = Counter()
    last_type: str | None = None
    run_len = 0

    def can_take(ev: NewsEvent) -> bool:
        ts = _teams(ev)
        if any(team_counts[t] >= max_per_team for t in ts):
            return False
        for pid in (ev.get("related_player_ids") or []):
            if pid and player_counts[str(pid)] >= max_per_player:
                return False
        nonlocal last_type, run_len
        et = _etype(ev)
        if last_type == et and run_len >= 1:
            # don't allow 3 in a row of the same type
            return False
        return True

    for ev in ordered:
        if len(selected) >= max_count:
            break
        if not can_take(ev):
            continue

        selected.append(ev)
        for t in _teams(ev):
            team_counts[t] += 1
        for pid in (ev.get("related_player_ids") or []):
            if pid:
                player_counts[str(pid)] += 1

        et = _etype(ev)
        if et == last_type:
            run_len += 1
        else:
            last_type = et
            run_len = 1

    # Ensure at least min_count if possible (relax constraints)
    if len(selected) < min_count and len(selected) < len(ordered):
        for ev in ordered:
            if len(selected) >= min_count:
                break
            if ev in selected:
                continue
            selected.append(ev)

    # Final chronological order (older -> newer) reads better for weekly recaps.
    selected = sorted(selected, key=lambda e: str(e.get("date") or ""))
    return selected
