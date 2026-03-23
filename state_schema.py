from __future__ import annotations

from typing import Any, Dict

from state_modules.state_constants import DEFAULT_TRADE_RULES, _DEFAULT_TRADE_MARKET, _DEFAULT_TRADE_MEMORY

STATE_SCHEMA_VERSION = "4.4"
ALLOWED_PHASES = {"regular", "preseason", "play_in", "playoffs"}
NON_REGULAR_PHASES = {"preseason", "play_in", "playoffs"}
ALLOWED_TOP_LEVEL_KEYS = {
    "schema_version",
    "turn",
    "active_season_id",
    "season_history",
    "draft_pick_orders",  # draft_year(str/int) -> {pick_id: slot_int}
    "games",
    "player_stats",
    "team_stats",
    "game_results",
    "phase_results",
    "cached_views",
    "league",
    "ui_cache",
    "team_tactics",
    "standings_cache",
    "trade_agreements",
    "negotiations",
    "trade_market",
    "trade_memory",
    "postseason",
    "_migrations",
}

ALLOWED_STANDINGS_CACHE_KEYS = {
    "version",
    "built_from",
    "applied_game_ids",
    "records_by_team",
}
ALLOWED_STANDINGS_BUILT_FROM_KEYS = {"season_id", "regular_final_count"}
ALLOWED_STANDINGS_RECORD_KEYS = {
    "wins",
    "losses",
    "pf",
    "pa",
    "home_wins",
    "home_losses",
    "away_wins",
    "away_losses",
    "div_wins",
    "div_losses",
    "conf_wins",
    "conf_losses",
    "recent10",
    "streak_type",
    "streak_len",
}

# UI-only read model cache. Never treat this as authoritative SSOT.
ALLOWED_UI_CACHE_KEYS = {"players", "teams"}
ALLOWED_PHASE_RESULTS_KEYS = {"games", "player_stats", "team_stats", "game_results"}
ALLOWED_POSTSEASON_KEYS = {
    "field",
    "play_in",
    "playoffs",
    "champion",
    "my_team_id",
    "play_in_start_date",
    "play_in_end_date",
    "playoffs_start_date",
}
ALLOWED_LEAGUE_KEYS = {
    "season_year",
    "draft_year",
    "season_start",
    "current_date",
    "db_path",
    "master_schedule",
    "trade_rules",
    "last_gm_tick_date",
}
ALLOWED_MASTER_SCHEDULE_KEYS = {"games", "by_team", "by_date", "by_id"}
ALLOWED_CACHED_VIEWS_KEYS = {"scores", "schedule", "stats", "weekly_news", "playoff_news", "_meta"}
ALLOWED_CACHED_META_KEYS = {"scores", "schedule"}
ALLOWED_META_SCORES_KEYS = {"built_from_turn", "season_id"}
ALLOWED_META_SCHEDULE_KEYS = {"built_from_turn_by_team", "season_id"}
ALLOWED_SCORES_VIEW_KEYS = {"latest_date", "games"}
ALLOWED_SCHEDULE_VIEW_KEYS = {"teams"}
ALLOWED_STATS_VIEW_KEYS = {"leaders"}
ALLOWED_WEEKLY_NEWS_KEYS = {
    "last_generated_week_start",
    "last_generated_as_of_date",
    "built_from_turn",
    "season_id",
    "generator_version",
    "llm",
    "items",
}
ALLOWED_WEEKLY_NEWS_LLM_KEYS = {"used", "model", "error"}
ALLOWED_PLAYOFF_NEWS_KEYS = {
    "processed_game_ids",
    "built_from_turn",
    "season_id",
    "generator_version",
    "items",
}
ALLOWED_SEASON_HISTORY_RECORD_KEYS = {"regular", "phase_results", "postseason", "archived_at_turn", "archived_at_date"}
ALLOWED_MIGRATIONS_KEYS = {
    "db_initialized",
    "db_initialized_db_path",
    "contracts_bootstrapped_seasons",
    "repo_integrity_validated",
    "repo_integrity_validated_db_path",
    "ingest_turn_backfilled",
}


def create_default_game_state() -> Dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "turn": 0,
        "active_season_id": None,
        "draft_pick_orders": {},  # draft_year -> {pick_id: slot_int}
        "season_history": {},
        "games": [],  # 각 경기의 메타 데이터
        "player_stats": {},  # player_id -> 시즌 누적 스탯
        "team_stats": {},  # team_id -> 시즌 누적 팀 스탯(가공 팀 박스)
        "game_results": {},  # game_id -> 매치엔진 원본 결과(신규 엔진 기준)
        "phase_results": {
            "preseason": {"games": [], "player_stats": {}, "team_stats": {}, "game_results": {}},
            "play_in": {"games": [], "player_stats": {}, "team_stats": {}, "game_results": {}},
            "playoffs": {"games": [], "player_stats": {}, "team_stats": {}, "game_results": {}},
        },
        "cached_views": {
            "scores": {
                "latest_date": None,
                "games": [],  # 최근 경기일자 기준 경기 리스트
            },
            "schedule": {
                "teams": {},  # team_id -> {past_games: [], upcoming_games: []}
            },
            "_meta": {
                "scores": {"built_from_turn": -1, "season_id": None},
                "schedule": {"built_from_turn_by_team": {}, "season_id": None},
            },
            "stats": {
                "leaders": None,
            },
            "weekly_news": {
                "last_generated_week_start": None,
                "last_generated_as_of_date": None,
                "built_from_turn": -1,
                "season_id": None,
                "generator_version": "news.weekly.v2",
                "llm": {"used": False, "model": None, "error": None},
                "items": [],
            },
            "playoff_news": {
                "processed_game_ids": [],
                "built_from_turn": -1,
                "season_id": None,
                "generator_version": "news.playoffs.v3",
                "items": [],
            },
        },
        "postseason": {
            "field": None,
            "play_in": None,
            "playoffs": None,
            "champion": None,
            "my_team_id": None,
            "play_in_start_date": None,
            "play_in_end_date": None,
            "playoffs_start_date": None,
        },  # 플레이-인/플레이오프 시뮬레이션 결과 캐시
        "league": {
            "season_year": None,
            "draft_year": None,  # 드래프트 연도(예: 2025-26 시즌이면 2026)
            "season_start": None,  # YYYY-MM-DD
            "current_date": None,  # 마지막으로 리그를 진행한 인게임 날짜
            "db_path": None,
            "master_schedule": {
                "games": [],  # 전체 리그 경기 리스트
                "by_team": {},  # team_id -> [game_id, ...]
                "by_date": {},  # date_str -> [game_id, ...]
                "by_id": {},
            },
            "trade_rules": {**DEFAULT_TRADE_RULES},
            "last_gm_tick_date": None,  # 마지막 AI GM 트레이드 시도 날짜
        },
        "ui_cache": {
            "teams": {},  # UI용 팀 성향 / 메타(권위 없음)
            "players": {},  # UI용 선수 메타(권위 없음)
        },
        "team_tactics": {},  # team_id -> {tactics: {...}, updated_at_turn: int}
        "standings_cache": {
            "version": 1,
            "built_from": {
                "season_id": None,
                "regular_final_count": 0,
            },
            "applied_game_ids": {},
            "records_by_team": {},
        },
        "trade_agreements": {},  # deal_id -> committed deal data
        "negotiations": {},  # session_id -> negotiation sessions
        "trade_market": dict(_DEFAULT_TRADE_MARKET),
        "trade_memory": dict(_DEFAULT_TRADE_MEMORY),
        "_migrations": {
            "db_initialized": False,
            "db_initialized_db_path": None,
            "contracts_bootstrapped_seasons": {},
            "repo_integrity_validated": False,
            "repo_integrity_validated_db_path": None,
            "ingest_turn_backfilled": False,
        },
    }


def _require_container(state: dict, key: str, expected_type: type, type_label: str) -> Any:
    value = state.get(key)
    if not isinstance(value, expected_type):
        raise ValueError(f"GameState invalid: {key} must be {type_label}")
    return value


def _require_nested_container(container: dict, path: str, expected_type: type, type_label: str) -> Any:
    value = container.get(path)
    if not isinstance(value, expected_type):
        raise ValueError(f"GameState invalid: {path} must be {type_label}")
    return value


def _require_exact_keys(container: dict, allowed_keys: set[str], label: str) -> None:
    if set(container.keys()) != allowed_keys:
        raise ValueError(f"GameState invalid: {label} keys must be {sorted(allowed_keys)}")


def _validate_phase_results(container: dict, label: str) -> None:
    _require_exact_keys(container, ALLOWED_PHASE_RESULTS_KEYS, label)
    if not isinstance(container.get("games"), list):
        raise ValueError(f"GameState invalid: {label}.games must be list")
    if not isinstance(container.get("player_stats"), dict):
        raise ValueError(f"GameState invalid: {label}.player_stats must be dict")
    if not isinstance(container.get("team_stats"), dict):
        raise ValueError(f"GameState invalid: {label}.team_stats must be dict")
    if not isinstance(container.get("game_results"), dict):
        raise ValueError(f"GameState invalid: {label}.game_results must be dict")


def _validate_postseason_container(container: dict, label: str) -> None:
    _require_exact_keys(container, ALLOWED_POSTSEASON_KEYS, label)
    forbidden_keys = {"games", "player_stats", "team_stats", "game_results"}
    for value in container.values():
        if isinstance(value, dict) and any(key in value for key in forbidden_keys):
            raise ValueError(f"GameState invalid: {label} must not contain results containers")


def validate_game_state(state: dict) -> None:
    if not isinstance(state, dict):
        raise ValueError("GameState invalid: state must be a dict")
        

    _require_exact_keys(state, ALLOWED_TOP_LEVEL_KEYS, "top-level")

    schema_version = state.get("schema_version")
    if schema_version != STATE_SCHEMA_VERSION:
        raise ValueError(f"GameState invalid: schema_version must be '{STATE_SCHEMA_VERSION}'")

    turn = state.get("turn")
    if not isinstance(turn, int) or turn < 0:
        raise ValueError("GameState invalid: turn must be int >= 0")

    _require_container(state, "games", list, "list")
    _require_container(state, "player_stats", dict, "dict")
    _require_container(state, "team_stats", dict, "dict")
    _require_container(state, "game_results", dict, "dict")
    draft_pick_orders = _require_container(state, "draft_pick_orders", dict, "dict")

    # Validate draft_pick_orders content shape (best-effort; tolerant on year key types).
    for y, mapping in draft_pick_orders.items():
        if not isinstance(mapping, dict):
            raise ValueError(f"GameState invalid: draft_pick_orders[{y!r}] must be dict")
        for pick_id, slot in mapping.items():
            if not isinstance(pick_id, str):
                raise ValueError("GameState invalid: draft_pick_orders values must have str pick_id keys")
            if not isinstance(slot, int):
                raise ValueError("GameState invalid: draft_pick_orders values must have int slot values")

    active_season_id = state.get("active_season_id")
    if active_season_id is not None and not isinstance(active_season_id, str):
        raise ValueError("GameState invalid: active_season_id must be str or None")

    season_history = _require_container(state, "season_history", dict, "dict")
    phase_results = _require_container(state, "phase_results", dict, "dict")
    cached_views = _require_container(state, "cached_views", dict, "dict")
    postseason = _require_container(state, "postseason", dict, "dict")
    league = _require_container(state, "league", dict, "dict")
    ui_cache = _require_container(state, "ui_cache", dict, "dict")
    _require_container(state, "team_tactics", dict, "dict")
    standings_cache = _require_container(state, "standings_cache", dict, "dict")
    _require_container(state, "trade_agreements", dict, "dict")
    _require_container(state, "negotiations", dict, "dict")
    trade_market = _require_container(state, "trade_market", dict, "dict")
    _require_container(state, "trade_memory", dict, "dict")
    migrations = _require_container(state, "_migrations", dict, "dict")


    # trade_market is derived state. grievance_cursor is optional but must be dict when present.
    if "grievance_cursor" in trade_market and not isinstance(trade_market.get("grievance_cursor"), dict):
        raise ValueError("GameState invalid: trade_market.grievance_cursor must be dict")

    _require_exact_keys(ui_cache, ALLOWED_UI_CACHE_KEYS, "ui_cache")
    if not isinstance(ui_cache.get("teams"), dict):
        raise ValueError("GameState invalid: ui_cache.teams must be dict")
    if not isinstance(ui_cache.get("players"), dict):
        raise ValueError("GameState invalid: ui_cache.players must be dict")

    _require_exact_keys(standings_cache, ALLOWED_STANDINGS_CACHE_KEYS, "standings_cache")
    if not isinstance(standings_cache.get("version"), int):
        raise ValueError("GameState invalid: standings_cache.version must be int")
    built_from = _require_nested_container(standings_cache, "built_from", dict, "dict")
    _require_exact_keys(built_from, ALLOWED_STANDINGS_BUILT_FROM_KEYS, "standings_cache.built_from")
    built_season_id = built_from.get("season_id")
    if built_season_id is not None and not isinstance(built_season_id, str):
        raise ValueError("GameState invalid: standings_cache.built_from.season_id must be str or None")
    if not isinstance(built_from.get("regular_final_count"), int):
        raise ValueError("GameState invalid: standings_cache.built_from.regular_final_count must be int")
    applied_game_ids = standings_cache.get("applied_game_ids")
    if not isinstance(applied_game_ids, dict):
        raise ValueError("GameState invalid: standings_cache.applied_game_ids must be dict")
    for gid, applied in applied_game_ids.items():
        if not isinstance(gid, str):
            raise ValueError("GameState invalid: standings_cache.applied_game_ids keys must be str")
        if not isinstance(applied, bool):
            raise ValueError("GameState invalid: standings_cache.applied_game_ids values must be bool")

    records_by_team = standings_cache.get("records_by_team")
    if not isinstance(records_by_team, dict):
        raise ValueError("GameState invalid: standings_cache.records_by_team must be dict")
    for tid, rec in records_by_team.items():
        if not isinstance(tid, str):
            raise ValueError("GameState invalid: standings_cache.records_by_team keys must be str")
        if not isinstance(rec, dict):
            raise ValueError("GameState invalid: standings_cache.records_by_team values must be dict")
        _require_exact_keys(rec, ALLOWED_STANDINGS_RECORD_KEYS, f"standings_cache.records_by_team.{tid}")
        for key in ALLOWED_STANDINGS_RECORD_KEYS - {"recent10", "streak_type"}:
            if not isinstance(rec.get(key), int):
                raise ValueError(f"GameState invalid: standings_cache.records_by_team.{tid}.{key} must be int")
        if not isinstance(rec.get("streak_type"), str):
            raise ValueError(f"GameState invalid: standings_cache.records_by_team.{tid}.streak_type must be str")
        recent10 = rec.get("recent10")
        if not isinstance(recent10, list):
            raise ValueError(f"GameState invalid: standings_cache.records_by_team.{tid}.recent10 must be list")
        for v in recent10:
            if not isinstance(v, int):
                raise ValueError(f"GameState invalid: standings_cache.records_by_team.{tid}.recent10 values must be int")

    _require_exact_keys(phase_results, NON_REGULAR_PHASES, "phase_results")
    for phase_key in NON_REGULAR_PHASES:
        phase_container = _require_nested_container(phase_results, phase_key, dict, "dict")
        _validate_phase_results(phase_container, f"phase_results.{phase_key}")

    _validate_postseason_container(postseason, "postseason")

    _require_exact_keys(cached_views, ALLOWED_CACHED_VIEWS_KEYS, "cached_views")
    scores = _require_nested_container(cached_views, "scores", dict, "dict")
    _require_exact_keys(scores, ALLOWED_SCORES_VIEW_KEYS, "cached_views.scores")
    if not isinstance(scores.get("games"), list):
        raise ValueError("GameState invalid: cached_views.scores.games must be list")

    schedule = _require_nested_container(cached_views, "schedule", dict, "dict")
    _require_exact_keys(schedule, ALLOWED_SCHEDULE_VIEW_KEYS, "cached_views.schedule")
    if not isinstance(schedule.get("teams"), dict):
        raise ValueError("GameState invalid: cached_views.schedule.teams must be dict")

    stats_view = _require_nested_container(cached_views, "stats", dict, "dict")
    _require_exact_keys(stats_view, ALLOWED_STATS_VIEW_KEYS, "cached_views.stats")

    weekly_news = _require_nested_container(cached_views, "weekly_news", dict, "dict")
    _require_exact_keys(weekly_news, ALLOWED_WEEKLY_NEWS_KEYS, "cached_views.weekly_news")
    w_week_start = weekly_news.get("last_generated_week_start")
    if w_week_start is not None and not isinstance(w_week_start, str):
        raise ValueError("GameState invalid: cached_views.weekly_news.last_generated_week_start must be str or None")
    w_as_of = weekly_news.get("last_generated_as_of_date")
    if w_as_of is not None and not isinstance(w_as_of, str):
        raise ValueError("GameState invalid: cached_views.weekly_news.last_generated_as_of_date must be str or None")
    if not isinstance(weekly_news.get("built_from_turn"), int):
        raise ValueError("GameState invalid: cached_views.weekly_news.built_from_turn must be int")
    w_season_id = weekly_news.get("season_id")
    if w_season_id is not None and not isinstance(w_season_id, str):
        raise ValueError("GameState invalid: cached_views.weekly_news.season_id must be str or None")
    if not isinstance(weekly_news.get("generator_version"), str):
        raise ValueError("GameState invalid: cached_views.weekly_news.generator_version must be str")
    w_llm = weekly_news.get("llm")
    if not isinstance(w_llm, dict):
        raise ValueError("GameState invalid: cached_views.weekly_news.llm must be dict")
    _require_exact_keys(w_llm, ALLOWED_WEEKLY_NEWS_LLM_KEYS, "cached_views.weekly_news.llm")
    if not isinstance(w_llm.get("used"), bool):
        raise ValueError("GameState invalid: cached_views.weekly_news.llm.used must be bool")
    w_model = w_llm.get("model")
    if w_model is not None and not isinstance(w_model, str):
        raise ValueError("GameState invalid: cached_views.weekly_news.llm.model must be str or None")
    w_err = w_llm.get("error")
    if w_err is not None and not isinstance(w_err, str):
        raise ValueError("GameState invalid: cached_views.weekly_news.llm.error must be str or None")
    if not isinstance(weekly_news.get("items"), list):
        raise ValueError("GameState invalid: cached_views.weekly_news.items must be list")

    playoff_news = _require_nested_container(cached_views, "playoff_news", dict, "dict")
    _require_exact_keys(playoff_news, ALLOWED_PLAYOFF_NEWS_KEYS, "cached_views.playoff_news")
    if not isinstance(playoff_news.get("processed_game_ids"), list):
        raise ValueError("GameState invalid: cached_views.playoff_news.processed_game_ids must be list")
    if not isinstance(playoff_news.get("built_from_turn"), int):
        raise ValueError("GameState invalid: cached_views.playoff_news.built_from_turn must be int")
    p_season_id = playoff_news.get("season_id")
    if p_season_id is not None and not isinstance(p_season_id, str):
        raise ValueError("GameState invalid: cached_views.playoff_news.season_id must be str or None")
    if not isinstance(playoff_news.get("generator_version"), str):
        raise ValueError("GameState invalid: cached_views.playoff_news.generator_version must be str")
    if not isinstance(playoff_news.get("items"), list):
        raise ValueError("GameState invalid: cached_views.playoff_news.items must be list")

    meta = _require_nested_container(cached_views, "_meta", dict, "dict")
    _require_exact_keys(meta, ALLOWED_CACHED_META_KEYS, "cached_views._meta")
    meta_scores = _require_nested_container(meta, "scores", dict, "dict")
    _require_exact_keys(meta_scores, ALLOWED_META_SCORES_KEYS, "cached_views._meta.scores")
    if not isinstance(meta_scores.get("built_from_turn"), int):
        raise ValueError("GameState invalid: cached_views._meta.scores.built_from_turn must be int")
    season_id = meta_scores.get("season_id")
    if season_id is not None and not isinstance(season_id, str):
        raise ValueError("GameState invalid: cached_views._meta.scores.season_id must be str or None")
    meta_schedule = _require_nested_container(meta, "schedule", dict, "dict")
    _require_exact_keys(meta_schedule, ALLOWED_META_SCHEDULE_KEYS, "cached_views._meta.schedule")
    built_from_turn_by_team = meta_schedule.get("built_from_turn_by_team")
    if not isinstance(built_from_turn_by_team, dict):
        raise ValueError("GameState invalid: cached_views._meta.schedule.built_from_turn_by_team must be dict")
    schedule_season_id = meta_schedule.get("season_id")
    if schedule_season_id is not None and not isinstance(schedule_season_id, str):
        raise ValueError("GameState invalid: cached_views._meta.schedule.season_id must be str or None")

    _require_exact_keys(league, ALLOWED_LEAGUE_KEYS, "league")
    master_schedule = _require_nested_container(league, "master_schedule", dict, "dict")
    _require_exact_keys(master_schedule, ALLOWED_MASTER_SCHEDULE_KEYS, "league.master_schedule")
    if not isinstance(master_schedule.get("games"), list):
        raise ValueError("GameState invalid: league.master_schedule.games must be list")
    if not isinstance(master_schedule.get("by_team"), dict):
        raise ValueError("GameState invalid: league.master_schedule.by_team must be dict")
    if not isinstance(master_schedule.get("by_date"), dict):
        raise ValueError("GameState invalid: league.master_schedule.by_date must be dict")
    if not isinstance(master_schedule.get("by_id"), dict):
        raise ValueError("GameState invalid: league.master_schedule.by_id must be dict")

    # -----------------------------
    # SSOT: active_season_id <-> league.season_year/draft_year 일치 강제
    # -----------------------------
    league_year = league.get("season_year")
    draft_year = league.get("draft_year")

    if league_year is not None and not isinstance(league_year, int):
        raise ValueError("GameState invalid: league.season_year must be int or None")
    if draft_year is not None and not isinstance(draft_year, int):
        raise ValueError("GameState invalid: league.draft_year must be int or None")

    # active_season_id와 league.season_year는 반드시 함께 존재하거나 둘 다 None이어야 한다.
    if (active_season_id is None) != (league_year is None):
        raise ValueError(
            "GameState invalid: active_season_id and league.season_year must be both set or both None"
        )

    # season_year가 None이면 draft_year도 None이어야 한다(부분 초기화 금지).
    if league_year is None:
        if draft_year is not None:
            raise ValueError("GameState invalid: league.draft_year must be None when league.season_year is None")
    else:
        # season_year가 설정되면 draft_year는 필수이며 season_year+1 이어야 한다.
        if draft_year is None:
            raise ValueError("GameState invalid: league.draft_year must be set when league.season_year is set")
        if int(draft_year) != int(league_year) + 1:
            raise ValueError("GameState invalid: league.draft_year must equal league.season_year + 1")

    # active_season_id가 설정된 경우: active의 연도(YYYY)가 league.season_year와 동일해야 한다.
    if active_season_id is not None:
        # 포맷: 'YYYY-YY' (예: '2026-27')
        if "-" not in active_season_id:
            raise ValueError("GameState invalid: active_season_id must be like 'YYYY-YY'")
        try:
            active_year = int(str(active_season_id).split("-", 1)[0])
        except Exception:
            raise ValueError("GameState invalid: active_season_id must be like 'YYYY-YY'")
        if int(league_year) != int(active_year):
            raise ValueError("GameState invalid: league.season_year must match active_season_id year")

        # -----------------------------
        # master_schedule 시즌 일치 강제
        # - games가 비어있으면(아직 스케줄 생성 전) 검사는 스킵한다.
        # - games가 존재하면, season_id가 있는 모든 엔트리는 active_season_id와 일치해야 한다.
        # -----------------------------
        ms_games = master_schedule.get("games") or []
        if isinstance(ms_games, list) and ms_games:
            for i, g in enumerate(ms_games):
                if not isinstance(g, dict):
                    continue
                sid = g.get("season_id")
                if sid is None:
                    continue
                if str(sid) != str(active_season_id):
                    raise ValueError(
                        f"GameState invalid: league.master_schedule.games[{i}].season_id must match active_season_id"
                    )

    for season_id, record in season_history.items():
        if not isinstance(season_id, str):
            raise ValueError("GameState invalid: season_history keys must be str")
        if not isinstance(record, dict):
            raise ValueError("GameState invalid: season_history records must be dict")
        _require_exact_keys(record, ALLOWED_SEASON_HISTORY_RECORD_KEYS, f"season_history.{season_id}")
        regular = _require_nested_container(record, "regular", dict, "dict")
        _validate_phase_results(regular, f"season_history.{season_id}.regular")
        record_phase_results = _require_nested_container(record, "phase_results", dict, "dict")
        _require_exact_keys(record_phase_results, NON_REGULAR_PHASES, f"season_history.{season_id}.phase_results")
        for phase_key in NON_REGULAR_PHASES:
            phase_container = _require_nested_container(record_phase_results, phase_key, dict, "dict")
            _validate_phase_results(phase_container, f"season_history.{season_id}.phase_results.{phase_key}")
        record_postseason = _require_nested_container(record, "postseason", dict, "dict")
        _validate_postseason_container(record_postseason, f"season_history.{season_id}.postseason")
        archived_at_turn = record.get("archived_at_turn")
        if not isinstance(archived_at_turn, int):
            raise ValueError("GameState invalid: season_history.archived_at_turn must be int")
        archived_at_date = record.get("archived_at_date")
        if archived_at_date is not None and not isinstance(archived_at_date, str):
            raise ValueError("GameState invalid: season_history.archived_at_date must be str or None")

    _require_exact_keys(migrations, ALLOWED_MIGRATIONS_KEYS, "_migrations")


if __name__ == "__main__":
    s = create_default_game_state()
    validate_game_state(s)
    print("OK")
