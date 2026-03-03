from __future__ import annotations

"""Common match simulation + ingestion pipeline.

This module is meant to be shared by:
- regular season simulation (future refactor of sim.league_sim)
- play-in simulation (postseason)
- playoffs simulation (postseason)

Key responsibilities
--------------------
- Build GameContext with correct SSOT (active_season_id + phase)
- Load TeamState from DB (roster_adapter)
- Apply persisted fatigue model (best-effort)
- Run matchengine_v3 and adapt to v2 payload
- Ingest result into GameState (state.ingest_game_result)

This module intentionally returns both:
- `game_obj` (the compact record stored in state.games or phase_results.games)
- `game_result_v2` (full v2 payload, useful for boxscore UI and debugging)
"""

import logging
import random
from contextlib import contextmanager
from datetime import date
from typing import Any, Dict, Optional

import fatigue
import readiness
from league_repo import LeagueRepo
from matchengine_v2_adapter import adapt_matchengine_result_to_v2, build_context_from_team_ids
from matchengine_v3.sim_game import simulate_game
from sim.roster_adapter import build_team_state_from_db
import state
from two_way_eligibility import get_two_way_exclusions_for_game
from two_way_repo import is_player_on_active_two_way, record_two_way_appearance

logger = logging.getLogger(__name__)


@contextmanager
def _repo_ctx() -> LeagueRepo:
    db_path = state.get_db_path()
    with LeagueRepo(db_path) as repo:
        yield repo


def summarize_v2_result(v2_result: Dict[str, Any], *, game_date_override: Optional[str] = None) -> Dict[str, Any]:
    """Extract a compact, UI-friendly summary from a GameResultV2 payload."""
    game = (v2_result or {}).get("game") or {}
    final = (v2_result or {}).get("final") or {}

    game_id = str(game.get("game_id") or "")
    phase = str(game.get("phase") or "")
    home_team_id = str(game.get("home_team_id") or "")
    away_team_id = str(game.get("away_team_id") or "")

    # Date is controlled by external schedule/context; allow override for safety.
    game_date = str(game_date_override or game.get("date") or "")

    try:
        home_score = int(final.get(home_team_id, 0) or 0)
    except Exception:
        home_score = 0
    try:
        away_score = int(final.get(away_team_id, 0) or 0)
    except Exception:
        away_score = 0

    winner = home_team_id if home_score >= away_score else away_team_id

    try:
        is_ot = int(game.get("overtime_periods", 0) or 0) > 0
    except Exception:
        is_ot = False

    return {
        "game_id": game_id,
        "date": game_date,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "home_score": home_score,
        "away_score": away_score,
        "winner": winner,
        "status": "final",
        "is_overtime": is_ot,
        "phase": phase,
        # Legacy fields used by playoff UI/news
        "final_score": {home_team_id: home_score, away_team_id: away_score},
        "boxscore": (v2_result or {}).get("teams"),
    }


def run_simulated_game(
    *,
    game_id: str,
    game_date: Optional[str],
    phase: str,
    home_team_id: str,
    away_team_id: str,
    home_tactics: Optional[Dict[str, Any]] = None,
    away_tactics: Optional[Dict[str, Any]] = None,
    rng_seed: Optional[int] = None,
    update_in_game_date: bool = False,
    persist: bool = True,
) -> Dict[str, Any]:
    """Simulate a single game and optionally ingest it into the game state.

    Args:
        game_id: unique identifier. For postseason we use deterministic IDs.
        game_date: ISO date (YYYY-MM-DD). If None, uses current in-game date.
        phase: one of {'regular','preseason','play_in','playoffs'}.
        update_in_game_date: if True, state.set_current_date(game_date) before simulation.
        persist: if True, ingest result into state and return game_obj.
    """
    league_context = state.get_league_context_snapshot()
    game_date_str = str(game_date or state.get_current_date_as_date().isoformat())

    # Ensure the date is a valid ISO date (truncate if needed).
    if len(game_date_str) >= 10:
        game_date_str = game_date_str[:10]
    try:
        date.fromisoformat(game_date_str)
    except Exception as exc:
        raise ValueError(f"Invalid game_date ISO: {game_date_str!r}") from exc

    if update_in_game_date:
        state.set_current_date(game_date_str)

    # Ensure monthly growth tick is applied (idempotent).
    try:
        from training.checkpoints import maybe_run_monthly_growth_tick

        maybe_run_monthly_growth_tick(db_path=state.get_db_path(), game_date_iso=game_date_str)
    except Exception:
        # Growth tick must never crash games.
        logger.warning("MONTHLY_GROWTH_TICK_FAILED date=%s", game_date_str, exc_info=True)

    # Ensure monthly agency tick is applied (idempotent).
    try:
        from agency.checkpoints import maybe_run_monthly_agency_tick

        maybe_run_monthly_agency_tick(db_path=state.get_db_path(), game_date_iso=game_date_str)
    except Exception:
        # Agency tick must never crash games.
        logger.warning("MONTHLY_AGENCY_TICK_FAILED date=%s", game_date_str, exc_info=True)

    context = build_context_from_team_ids(
        str(game_id),
        game_date_str,
        str(home_team_id).upper(),
        str(away_team_id).upper(),
        league_context,
        phase=str(phase),
    )

    rng = random.Random(rng_seed) if rng_seed is not None else random.Random()

    with _repo_ctx() as repo:
        # Ensure schema is applied (idempotent). Guarantees fatigue/readiness tables exist.
        repo.init_db()

        season_year = None
        try:
            season_year = fatigue.season_year_from_season_id(str(getattr(context, "season_id", "") or ""))
        except Exception:
            # Season parsing failures must never crash games.
            logger.warning(
                "SEASON_YEAR_PARSE_FAILED phase=%s date=%s season_id=%s",
                str(phase),
                game_date_str,
                str(getattr(context, "season_id", "") or ""),
                exc_info=True,
            )
            season_year = None

        # ------------------------------------------------------------
        # Readiness: prepare between-game readiness (player sharpness + scheme familiarity)
        # - must run BEFORE building TeamState so roster_adapter can apply readiness mods
        # ------------------------------------------------------------
        prepared_ready = None
        attrs_mods_by_pid = None
        if season_year is not None:
            try:
                prepared_ready = readiness.prepare_game_readiness(
                    repo,
                    game_date_iso=game_date_str,
                    season_year=int(season_year),
                    home_team_id=str(home_team_id).upper(),
                    away_team_id=str(away_team_id).upper(),
                    home_tactics=home_tactics,
                    away_tactics=away_tactics,
                )
                attrs_mods_by_pid = prepared_ready.attrs_mods_by_pid
            except Exception:
                logger.warning(
                    "READINESS_PREPARE_FAILED phase=%s date=%s home=%s away=%s",
                    str(phase),
                    game_date_str,
                    str(home_team_id),
                    str(away_team_id),
                    exc_info=True,
                )
                prepared_ready = None
                attrs_mods_by_pid = None

        tw_ex = get_two_way_exclusions_for_game(
            repo=repo,
            home_team_id=str(home_team_id).upper(),
            away_team_id=str(away_team_id).upper(),
            phase=str(phase),
            season_year=int(season_year) if season_year is not None else int(league_context.get("season_year") or 0),
        )

        home = build_team_state_from_db(
            repo=repo,
            team_id=str(home_team_id).upper(),
            tactics=home_tactics,
            exclude_pids=set(tw_ex.home_exclude),
            attrs_mods_by_pid=attrs_mods_by_pid,
        )
        away = build_team_state_from_db(
            repo=repo,
            team_id=str(away_team_id).upper(),
            tactics=away_tactics,
            exclude_pids=set(tw_ex.away_exclude),
            attrs_mods_by_pid=attrs_mods_by_pid,
        )

        # ------------------------------------------------------------
        # Readiness: apply familiarity-derived multipliers to TeamState.tactics (in-memory only)
        # ------------------------------------------------------------
        if prepared_ready is not None:
            try:
                mult_h = prepared_ready.tactics_mult_by_team.get(prepared_ready.home_team_id)
                if mult_h is not None:
                    readiness.apply_readiness_to_team_state(home, mult_h)

                mult_a = prepared_ready.tactics_mult_by_team.get(prepared_ready.away_team_id)
                if mult_a is not None:
                    readiness.apply_readiness_to_team_state(away, mult_a)
            except Exception:
                logger.warning(
                    "READINESS_APPLY_FAILED phase=%s date=%s home=%s away=%s",
                    str(phase),
                    game_date_str,
                    str(home_team_id),
                    str(away_team_id),
                    exc_info=True,
                )

        # ------------------------------------------------------------
        # Fatigue: prepare between-game condition (start_energy + energy_cap)
        # ------------------------------------------------------------
        prepared_fat = None
        if season_year is not None:
            try:
                prepared_fat = fatigue.prepare_game_fatigue(
                    repo,
                    game_date_iso=game_date_str,
                    season_year=int(season_year),
                    home=home,
                    away=away,
                )
            except Exception:
                logger.warning(
                    "FATIGUE_PREPARE_FAILED phase=%s date=%s home=%s away=%s",
                    str(phase),
                    game_date_str,
                    str(home_team_id),
                    str(away_team_id),
                    exc_info=True,
                )

        raw_result = simulate_game(rng, home, away, context=context)

        if prepared_fat is not None:
            try:
                fatigue.finalize_game_fatigue(
                    repo,
                    prepared=prepared_fat,
                    home=home,
                    away=away,
                    raw_result=raw_result,
                )
            except Exception:
                logger.warning(
                    "FATIGUE_FINALIZE_FAILED phase=%s date=%s home=%s away=%s",
                    str(phase),
                    game_date_str,
                    str(home_team_id),
                    str(away_team_id),
                    exc_info=True,
                )

        if prepared_ready is not None:
            try:
                readiness.finalize_game_readiness(
                    repo,
                    prepared=prepared_ready,
                    raw_result=raw_result,
                )
            except Exception:
                logger.warning(
                    "READINESS_FINALIZE_FAILED phase=%s date=%s home=%s away=%s",
                    str(phase),
                    game_date_str,
                    str(home_team_id),
                    str(away_team_id),
                    exc_info=True,
                )

    v2_result = adapt_matchengine_result_to_v2(
        raw_result,
        context,
        engine_name="matchengine_v3",
    )

    game_obj = None
    if persist:
        game_obj = state.ingest_game_result(game_result=v2_result, game_date=game_date_str)

    # Two-way usage tracking (regular season only): count actual participants once per game.
    try:
        phase_l = str(phase or "").lower()
        if phase_l == "regular":
            season_year_used = int(season_year) if season_year is not None else int(league_context.get("season_year") or 0)
            teams_payload = (v2_result or {}).get("teams") or {}
            with _repo_ctx() as repo2:
                with repo2.transaction() as cur:
                    for tid in (str(home_team_id).upper(), str(away_team_id).upper()):
                        team_rows = (teams_payload.get(tid) or {}).get("players") or []
                        for row in team_rows:
                            pid = str(row.get("PlayerID") or "").strip()
                            if not pid:
                                continue
                            try:
                                mins = int(row.get("MIN", 0) or 0)
                            except Exception:
                                mins = 0
                            if mins <= 0:
                                continue
                            if not is_player_on_active_two_way(cur, pid):
                                continue
                            record_two_way_appearance(
                                cur,
                                player_id=pid,
                                season_year=season_year_used,
                                game_id=str(game_id),
                                phase=phase_l,
                                now_iso=game_date_str,
                            )
    except Exception:
        logger.warning("TWO_WAY_USAGE_RECORD_FAILED game_id=%s", str(game_id), exc_info=True)

    return {
        "context": context,
        "game_obj": game_obj,
        "game_result_v2": v2_result,
    }
