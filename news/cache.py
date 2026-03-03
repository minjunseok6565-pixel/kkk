from __future__ import annotations

from typing import Any, Dict

from state import (
    get_cached_playoff_news_snapshot,
    get_cached_weekly_news_snapshot,
    set_cached_playoff_news_snapshot,
    set_cached_weekly_news_snapshot,
)

# Cache generator versions (used for invalidation when logic changes)
WEEKLY_GENERATOR_VERSION = "news.weekly.v2"
PLAYOFF_GENERATOR_VERSION = "news.playoffs.v3"


_WEEKLY_KEYS = {
    "last_generated_week_start",
    "last_generated_as_of_date",
    "built_from_turn",
    "season_id",
    "generator_version",
    "llm",
    "items",
}
_WEEKLY_LLM_KEYS = {"used", "model", "error"}

_PLAYOFF_KEYS = {
    "processed_game_ids",
    "built_from_turn",
    "season_id",
    "generator_version",
    "items",
}


def _require_exact_keys(obj: Dict[str, Any], keys: set[str], *, path: str) -> None:
    extra = set(obj.keys()) - keys
    missing = keys - set(obj.keys())
    if missing or extra:
        raise ValueError(f"Invalid cache shape at {path}: missing={sorted(missing)} extra={sorted(extra)}")


def _assert_weekly_cache(cache: Any) -> Dict[str, Any]:
    if not isinstance(cache, dict):
        raise ValueError("weekly_news cache must be a dict")

    _require_exact_keys(cache, _WEEKLY_KEYS, path="cached_views.weekly_news")

    llm = cache["llm"]
    if not isinstance(llm, dict):
        raise ValueError("cached_views.weekly_news.llm must be dict")
    _require_exact_keys(llm, _WEEKLY_LLM_KEYS, path="cached_views.weekly_news.llm")

    if not isinstance(llm["used"], bool):
        raise ValueError("cached_views.weekly_news.llm.used must be bool")
    if llm["model"] is not None and not isinstance(llm["model"], str):
        raise ValueError("cached_views.weekly_news.llm.model must be str|None")
    if llm["error"] is not None and not isinstance(llm["error"], str):
        raise ValueError("cached_views.weekly_news.llm.error must be str|None")

    if cache["last_generated_week_start"] is not None and not isinstance(cache["last_generated_week_start"], str):
        raise ValueError("cached_views.weekly_news.last_generated_week_start must be str|None")
    if cache["last_generated_as_of_date"] is not None and not isinstance(cache["last_generated_as_of_date"], str):
        raise ValueError("cached_views.weekly_news.last_generated_as_of_date must be str|None")
    if not isinstance(cache["built_from_turn"], int):
        raise ValueError("cached_views.weekly_news.built_from_turn must be int")
    if cache["season_id"] is not None and not isinstance(cache["season_id"], str):
        raise ValueError("cached_views.weekly_news.season_id must be str|None")
    if not isinstance(cache["generator_version"], str):
        raise ValueError("cached_views.weekly_news.generator_version must be str")
    if not isinstance(cache["items"], list):
        raise ValueError("cached_views.weekly_news.items must be list")

    return cache


def _assert_playoff_cache(cache: Any) -> Dict[str, Any]:
    if not isinstance(cache, dict):
        raise ValueError("playoff_news cache must be a dict")

    _require_exact_keys(cache, _PLAYOFF_KEYS, path="cached_views.playoff_news")

    if not isinstance(cache["processed_game_ids"], list):
        raise ValueError("cached_views.playoff_news.processed_game_ids must be list")
    if not isinstance(cache["built_from_turn"], int):
        raise ValueError("cached_views.playoff_news.built_from_turn must be int")
    if cache["season_id"] is not None and not isinstance(cache["season_id"], str):
        raise ValueError("cached_views.playoff_news.season_id must be str|None")
    if not isinstance(cache["generator_version"], str):
        raise ValueError("cached_views.playoff_news.generator_version must be str")
    if not isinstance(cache["items"], list):
        raise ValueError("cached_views.playoff_news.items must be list")

    return cache


def get_weekly_cache() -> Dict[str, Any]:
    """Read cached weekly news.

    This is a strict reader for schema 4.1+.
    If cache shape is wrong, we raise immediately (no legacy normalization).
    """
    return _assert_weekly_cache(get_cached_weekly_news_snapshot())


def set_weekly_cache(cache: Dict[str, Any]) -> None:
    """Persist weekly cache.

    This is a strict writer for schema 4.1+.
    """
    _assert_weekly_cache(cache)
    set_cached_weekly_news_snapshot(cache)


def get_playoff_cache() -> Dict[str, Any]:
    """Read cached playoff news.

    Strict reader for schema 4.1+.
    """
    return _assert_playoff_cache(get_cached_playoff_news_snapshot())


def set_playoff_cache(cache: Dict[str, Any]) -> None:
    """Persist playoff cache.

    Strict writer for schema 4.1+.
    """
    _assert_playoff_cache(cache)
    set_cached_playoff_news_snapshot(cache)


def weekly_cache_is_fresh(
    cache: Dict[str, Any],
    *,
    week_start: str,
    as_of_date: str,
    season_id: str | None,
    generator_version: str = WEEKLY_GENERATOR_VERSION,
) -> bool:
    """Return True if the weekly cache is safe to serve without regenerating."""
    # Strict assumptions: cache already validated by _assert_weekly_cache().
    if not cache["items"]:
        return False
    if cache["generator_version"] != generator_version:
        return False
    if cache["season_id"] != season_id:
        return False
    if cache["last_generated_week_start"] != week_start:
        return False
    if cache["last_generated_as_of_date"] != as_of_date:
        return False
    return True
