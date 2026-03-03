from __future__ import annotations

from typing import Any

from .models import NewsEvent


_BASE: dict[str, float] = {
    # Postseason
    "CHAMPION": 100.0,
    "PLAYOFF_ELIMINATION": 85.0,
    "PLAYOFF_MATCH_POINT": 70.0,
    "PLAYOFF_SERIES_SWING": 60.0,
    "PLAYOFF_GAME_RECAP": 50.0,
    # Weekly
    "UPSET": 55.0,
    "CLUTCH_OT": 40.0,
    "BLOWOUT": 35.0,
    "STANDINGS_SHAKEUP": 50.0,
    "STREAK_TEAM": 38.0,
    "PLAYER_40PTS": 52.0,
    "PLAYER_TRIPLE_DOUBLE": 48.0,
    "PLAYER_20REB": 45.0,
    "PLAYER_10AST": 42.0,
    "PLAYER_5STL": 42.0,
    "PLAYER_5BLK": 42.0,
    "PLAYER_7_3PM": 40.0,
    "PLAYER_MASTERCLASS": 44.0,
    "TRANSACTION": 36.0,
}


def score_event(e: NewsEvent) -> float:
    """Assign an importance score to a NewsEvent.

    Scoring is intentionally simple and deterministic. It can be tuned later
    without breaking the event/article data model.
    """
    etype = str(e.get("type"))
    base = float(_BASE.get(etype, 25.0))
    facts: dict[str, Any] = e.get("facts") or {}

    # Upset: add weight by pregame win% gap.
    if etype == "UPSET":
        gap = facts.get("pregame_win_pct_gap")
        try:
            gap_f = float(gap)
        except Exception:
            gap_f = 0.0
        base += max(0.0, min(30.0, gap_f * 100.0))

    # Streak: longer streak -> higher
    if etype == "STREAK_TEAM":
        st = facts.get("streak_len")
        try:
            st_i = int(st)
        except Exception:
            st_i = 0
        base += max(0.0, min(20.0, (st_i - 4) * 4.0))

    # Player events: more points -> higher
    if etype in {"PLAYER_40PTS", "PLAYER_MASTERCLASS"}:
        pts = facts.get("pts")
        try:
            pts_f = float(pts)
        except Exception:
            pts_f = 0.0
        if pts_f > 30:
            base += min(12.0, (pts_f - 30) * 0.6)

    # Postseason narrative boosts
    if etype == "PLAYOFF_GAME_RECAP":
        diff = facts.get("margin")
        try:
            d = abs(int(diff))
        except Exception:
            d = 0
        if d <= 3:
            base += 8.0
        elif d >= 20:
            base += 4.0
        if facts.get("is_overtime"):
            base += 10.0

    if etype in {"PLAYOFF_MATCH_POINT", "PLAYOFF_ELIMINATION"}:
        # game 6/7 adds intensity
        gn = facts.get("game_number")
        try:
            gni = int(gn)
        except Exception:
            gni = 0
        if gni >= 6:
            base += 6.0

    return float(base)


def apply_importance(events: list[NewsEvent]) -> list[NewsEvent]:
    for e in events:
        e["importance"] = score_event(e)
    return events
