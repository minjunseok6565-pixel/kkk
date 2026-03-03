from __future__ import annotations

from typing import Any, List, Literal, TypedDict


EventType = Literal[
    # Regular-season weekly
    "UPSET",
    "CLUTCH_OT",
    "BLOWOUT",
    "STREAK_TEAM",
    "STANDINGS_SHAKEUP",
    "PLAYER_40PTS",
    "PLAYER_TRIPLE_DOUBLE",
    "PLAYER_10AST",
    "PLAYER_20REB",
    "PLAYER_5STL",
    "PLAYER_5BLK",
    "PLAYER_7_3PM",
    "PLAYER_MASTERCLASS",
    "TRANSACTION",
    # Postseason
    "PLAYOFF_GAME_RECAP",
    "PLAYOFF_SERIES_SWING",
    "PLAYOFF_MATCH_POINT",
    "PLAYOFF_ELIMINATION",
    "CHAMPION",
]


class NewsEvent(TypedDict):
    """A structured, fact-grounded news event."""

    event_id: str
    date: str  # YYYY-MM-DD
    type: EventType
    importance: float
    facts: dict[str, Any]
    related_team_ids: List[str]
    related_player_ids: List[str]
    related_player_names: List[str]
    tags: List[str]


class NewsArticle(TypedDict):
    """A UI-ready news article."""

    article_id: str
    event_id: str
    date: str  # YYYY-MM-DD
    title: str
    summary: str
    tags: List[str]
    related_team_ids: List[str]
    related_player_names: List[str]
