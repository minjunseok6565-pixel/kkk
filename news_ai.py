from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from league_repo import LeagueRepo

from state import (
    export_workflow_state,
    get_active_season_id,
    get_current_date_as_date,
    get_db_path,
    get_postseason_snapshot,
)

from news.cache import (
    PLAYOFF_GENERATOR_VERSION,
    WEEKLY_GENERATOR_VERSION,
    get_playoff_cache,
    get_weekly_cache,
    set_playoff_cache,
    set_weekly_cache,
    weekly_cache_is_fresh,
)
from news.editorial import select_top_events
from news.extractors.playoffs import extract_playoff_events, iter_series
from news.extractors.weekly import build_week_window, extract_weekly_events
from news.ids import make_event_id
from news.models import NewsArticle, NewsEvent
from news.scoring import apply_importance
from news.render.template_ko import render_article
from news.render.gemini_rewrite import rewrite_article_with_gemini

logger = logging.getLogger(__name__)

# Avoid unbounded growth in long-running saves
_MAX_PLAYOFF_NEWS_ITEMS = 250


def _iso(d: date) -> str:
    return d.isoformat()


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if len(s) >= 10:
        s = s[:10]
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def _extract_transaction_events(*, start: date, end: date) -> List[NewsEvent]:
    """Fetch SSOT transactions from SQLite and convert to NewsEvent."""
    db_path = get_db_path()
    events: List[NewsEvent] = []
    try:
        with LeagueRepo(db_path) as repo:
            repo.init_db()
            rows = repo.list_transactions(limit=500, since_date=_iso(start))
    except Exception as exc:
        logger.warning("transactions fetch failed: %s", exc, exc_info=True)
        return []

    for t in rows:
        if not isinstance(t, dict):
            continue
        d = _as_date(t.get("date"))
        if not d:
            continue
        if not (start <= d <= end):
            continue

        tx_type = str(t.get("type") or "transaction")
        title = str(t.get("title") or "").strip()
        summary = str(t.get("summary") or "").strip()
        if not title:
            if tx_type.lower() == "trade":
                title = "트레이드 소식"
            else:
                title = "로스터 변동"
        if not summary:
            summary = str(t)

        teams = t.get("teams") or []
        if not isinstance(teams, list):
            teams = []
        team_ids = [str(x) for x in teams if x]

        events.append(
            {
                "event_id": make_event_id("TX", d.isoformat(), tx_type, t.get("deal_id") or ""),
                "date": d.isoformat(),
                "type": "TRANSACTION",
                "importance": 0.0,
                "facts": {
                    "tx_type": tx_type,
                    "title": title,
                    "summary": summary,
                    "teams": team_ids,
                    "payload": t,
                },
                "related_team_ids": team_ids,
                "related_player_ids": [],
                "related_player_names": [],
                "tags": ["transaction", tx_type],
            }
        )

    return events


def _can_use_gemini_rewrite(api_key: str) -> Tuple[bool, str | None, str | None]:
    """Return (usable, model_name, error).

    Gemini rewrite is optional; weekly news must still work without it.
    """
    if not api_key:
        return False, None, None

    model_name = "gemini-3-pro-preview"

    try:
        import google.generativeai  # noqa: F401

        return True, model_name, None
    except Exception:
        return False, None, "google.generativeai unavailable"


def refresh_weekly_news(api_key: str) -> Dict[str, Any]:
    """Generate (or return cached) weekly news.

    Output is compatible with the existing frontend:
      {"current_date": "YYYY-MM-DD", "items": [ {title, summary, ...}, ... ]}

    Cache policy (state_schema 4.2):
      - Cached per week_start (Monday)
      - Regenerated when as_of_date advances, season changes, or generator_version changes
      - Stores LLM metadata in cache['llm']
    """
    if not api_key:
        # Weekly endpoint historically required apiKey; keep fail-loud to match server expectation.
        raise ValueError("apiKey is required")

    current = get_current_date_as_date()  # fail-loud if in-game date missing
    current_iso = _iso(current)
    ws, we, week_key = build_week_window(current)

    season_id = get_active_season_id()

    cache = get_weekly_cache()
    if weekly_cache_is_fresh(cache, week_start=week_key, as_of_date=current_iso, season_id=season_id):
        return {"current_date": current_iso, "items": cache["items"]}

    snapshot = export_workflow_state()

    events = extract_weekly_events(snapshot, start_date=_iso(ws), end_date=_iso(we))
    events += _extract_transaction_events(start=ws, end=we)

    apply_importance(events)
    selected = select_top_events(events, min_count=3, max_count=6)

    articles: List[NewsArticle] = [render_article(e) for e in selected]

    # Optional style rewrite; safe fallback to templates
    llm_used, llm_model, llm_error = _can_use_gemini_rewrite(api_key)
    if llm_used:
        rewritten: List[NewsArticle] = []
        for a in articles:
            rewritten.append(rewrite_article_with_gemini(api_key, a, model_name=llm_model or "gemini-3-pro-preview"))
        articles = rewritten

    # Write cache (4.1)
    cache["last_generated_week_start"] = week_key
    cache["last_generated_as_of_date"] = current_iso
    cache["built_from_turn"] = int(snapshot.get("turn") or -1)
    cache["season_id"] = season_id
    cache["generator_version"] = WEEKLY_GENERATOR_VERSION
    cache["llm"] = {"used": bool(llm_used), "model": llm_model, "error": llm_error}
    cache["items"] = articles
    set_weekly_cache(cache)

    return {"current_date": current_iso, "items": articles}


def _build_playoffs_boxscore_lookup(workflow: Dict[str, Any]) -> Dict[Tuple[str, str, str, int, int], Dict[str, Any]]:
    """Index playoff phase game_results for quick matching."""
    out: Dict[Tuple[str, str, str, int, int], Dict[str, Any]] = {}
    pr = (workflow.get("phase_results") or {}).get("playoffs") or {}
    games = pr.get("games") or []
    game_results = pr.get("game_results") or {}

    if not isinstance(games, list) or not isinstance(game_results, dict):
        return out

    for g in games:
        if not isinstance(g, dict):
            continue
        try:
            d = str(g.get("date") or "")[:10]
            home = str(g.get("home_team_id") or "")
            away = str(g.get("away_team_id") or "")
            hs = int(g.get("home_score"))
            as_ = int(g.get("away_score"))
            gid = str(g.get("game_id") or "")
        except Exception:
            continue
        gr = game_results.get(gid)
        if isinstance(gr, dict) and d and home and away:
            out[(d, home, away, hs, as_)] = gr

    return out


def _collect_final_playoff_game_ids(playoffs: Dict[str, Any]) -> List[str]:
    """Collect game_ids for completed (winner known) playoff games."""
    ids: List[str] = []
    for s in iter_series(playoffs):
        games = s.get("games") or []
        if not isinstance(games, list):
            continue
        for g in games:
            if not isinstance(g, dict):
                continue
            gid = g.get("game_id")
            winner = g.get("winner")
            if gid and winner:
                ids.append(str(gid))
    return ids


def refresh_playoff_news() -> Dict[str, Any]:
    """Append newly completed playoff games as news articles.

    Output is compatible with the existing frontend:
      {"items": [...], "new_items": [...]} 

    Cache policy (state_schema 4.2):
      - Dedup using processed_game_ids (deterministic)
      - Store generator_version/season_id/built_from_turn for debugging

    NOTE:
      - No legacy/backfill logic: cache is assumed to always be schema 4.1.
    """
    postseason = get_postseason_snapshot()
    playoffs = postseason.get("playoffs")
    if not playoffs:
        raise ValueError("플레이오프 진행 중이 아닙니다.")

    season_id = get_active_season_id()

    cache = get_playoff_cache()
    items = cache["items"]

    processed_list = cache["processed_game_ids"]
    processed_set = {str(x) for x in processed_list if x}

    # Detect whether there are any new completed games
    final_ids = _collect_final_playoff_game_ids(playoffs)
    new_game_ids = [gid for gid in final_ids if gid not in processed_set]

    if not new_game_ids:
        return {"items": items, "new_items": []}

    workflow = export_workflow_state()
    box_lookup = _build_playoffs_boxscore_lookup(workflow)

    events = extract_playoff_events(
        playoffs,
        processed_game_ids=processed_set,
        boxscore_lookup=box_lookup,
    )

    apply_importance(events)

    # A single refresh can produce multiple events per game (recap+swing+matchpoint+elimination).
    # Limit to avoid spamming: keep top 12 by importance per refresh.
    events_sorted = sorted(events, key=lambda e: float(e.get("importance") or 0.0), reverse=True)[:12]
    events_sorted = sorted(events_sorted, key=lambda e: str(e.get("date") or ""))

    new_articles: List[NewsArticle] = [render_article(e) for e in events_sorted]

    items_out = list(items) + new_articles

    if len(items_out) > _MAX_PLAYOFF_NEWS_ITEMS:
        items_out = items_out[-_MAX_PLAYOFF_NEWS_ITEMS :]

    # Update processed ids (append in order)
    for gid in new_game_ids:
        sgid = str(gid)
        if sgid in processed_set:
            continue
        processed_set.add(sgid)
        processed_list.append(sgid)

    cache["processed_game_ids"] = processed_list
    cache["built_from_turn"] = int(workflow.get("turn") or -1)
    cache["season_id"] = season_id
    cache["generator_version"] = PLAYOFF_GENERATOR_VERSION
    cache["items"] = items_out
    set_playoff_cache(cache)

    return {"items": items_out, "new_items": new_articles}
