from __future__ import annotations

import logging
import random
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional, Set

import schema

from league_repo import LeagueRepo
from matchengine_v2_adapter import (
    adapt_matchengine_result_to_v2,
    build_context_from_master_schedule_entry,
)
from matchengine_v3.sim_game import simulate_game
import fatigue
import injury
import readiness
import practice
from state import (
    export_full_state_snapshot,
    get_db_path,
    get_league_context_snapshot,
    get_current_date_as_date,
    ingest_game_result,
    set_current_date,
)
from sim.roster_adapter import build_team_state_from_db

logger = logging.getLogger(__name__)


@contextmanager
def _repo_ctx() -> LeagueRepo:
    db_path = get_db_path()
    with LeagueRepo(db_path) as repo:
        yield repo


def _run_match(
    *,
    home_team_id: str,
    away_team_id: str,
    game_date: str,
    home_tactics: Optional[Dict[str, Any]] = None,
    away_tactics: Optional[Dict[str, Any]] = None,
    context: schema.GameContext,
) -> Dict[str, Any]:
    rng = random.Random()
    with _repo_ctx() as repo:
        # Ensure schema is applied (idempotent). This guarantees fatigue/injury/readiness tables exist
        # even if the DB was created before the modules were added.
        repo.init_db()

        season_year = fatigue.season_year_from_season_id(str(getattr(context, "season_id", "") or ""))

        # ------------------------------------------------------------
        # Injury: prepare between-game state (OUT players + returning debuffs)
        # - must run BEFORE building TeamState so roster_adapter can exclude/apply debuffs
        # ------------------------------------------------------------
        prepared_inj = None
        unavailable_by_team: Dict[str, Set[str]] = {}
        attrs_mods_by_pid = None
        try:
            prepared_inj = injury.prepare_game_injuries(
                repo,
                game_id=str(getattr(context, "game_id", "") or ""),
                game_date_iso=str(game_date),
                season_year=int(season_year),
                home_team_id=str(home_team_id),
                away_team_id=str(away_team_id),
                home_tactics=home_tactics,
                away_tactics=away_tactics,
            )
            unavailable_by_team = dict(prepared_inj.unavailable_pids_by_team or {})
            attrs_mods_by_pid = prepared_inj.attrs_mods_by_pid
        except Exception:
            logger.warning(
                "INJURY_PREPARE_FAILED game_date=%s home=%s away=%s",
                game_date,
                str(home_team_id),
                str(away_team_id),
                exc_info=True,
            )
            prepared_inj = None

        # ------------------------------------------------------------
        # Practice: between-game training sessions (team conditioning)
        # - placeholder hook in v1; later will update readiness SSOT
        # ------------------------------------------------------------
        try:
            practice.apply_practice_before_game(
                repo,
                game_date_iso=str(game_date),
                season_year=int(season_year),
                home_team_id=str(home_team_id),
                away_team_id=str(away_team_id),
                home_tactics=home_tactics,
                away_tactics=away_tactics,
                unavailable_pids_by_team=unavailable_by_team,
            )
        except Exception:
            logger.warning(
                "PRACTICE_APPLY_FAILED game_date=%s home=%s away=%s",
                game_date,
                str(home_team_id),
                str(away_team_id),
                exc_info=True,
            )

        # ------------------------------------------------------------
        # Readiness: prepare between-game readiness (player sharpness + scheme familiarity)
        # - must run BEFORE building TeamState so roster_adapter can apply readiness mods
        # ------------------------------------------------------------
        prepared_ready = None
        try:
            prepared_ready = readiness.prepare_game_readiness(
                repo,
                game_date_iso=str(game_date),
                season_year=int(season_year),
                home_team_id=str(home_team_id),
                away_team_id=str(away_team_id),
                home_tactics=home_tactics,
                away_tactics=away_tactics,
                unavailable_pids_by_team=unavailable_by_team,
            )
        except Exception:
            logger.warning(
                "READINESS_PREPARE_FAILED game_date=%s home=%s away=%s",
                game_date,
                str(home_team_id),
                str(away_team_id),
                exc_info=True,
            )
            prepared_ready = None

        # Merge readiness attribute mods with injury RETURNING debuffs (both are temporary deltas).
        # SSOT: persistence is handled by each subsystem; this merge is only for roster_adapter input.
        if prepared_ready is not None and getattr(prepared_ready, "attrs_mods_by_pid", None):
            if attrs_mods_by_pid is None:
                attrs_mods_by_pid = prepared_ready.attrs_mods_by_pid
            else:
                merged_mods: Dict[str, Dict[str, float]] = {}

                def _merge_src(src: Optional[Mapping[str, Mapping[str, float]]]) -> None:
                    if not src:
                        return
                    for pid, mods in src.items():
                        if not isinstance(mods, Mapping) or not mods:
                            continue
                        bucket = merged_mods.setdefault(str(pid), {})
                        for k, delta in mods.items():
                            try:
                                d = float(delta)
                            except Exception:
                                continue
                            if d == 0.0:
                                continue
                            key = str(k)
                            bucket[key] = float(bucket.get(key, 0.0) or 0.0) + d

                _merge_src(attrs_mods_by_pid)
                _merge_src(prepared_ready.attrs_mods_by_pid)
                attrs_mods_by_pid = merged_mods

        hid = schema.normalize_team_id(str(home_team_id)).upper()
        aid = schema.normalize_team_id(str(away_team_id)).upper()

        home = build_team_state_from_db(
            repo=repo,
            team_id=home_team_id,
            tactics=home_tactics,
            exclude_pids=set(unavailable_by_team.get(hid, set()) or set()),
            attrs_mods_by_pid=attrs_mods_by_pid,
        )
        away = build_team_state_from_db(
            repo=repo,
            team_id=away_team_id,
            tactics=away_tactics,
            exclude_pids=set(unavailable_by_team.get(aid, set()) or set()),
            attrs_mods_by_pid=attrs_mods_by_pid,
        )

        # ------------------------------------------------------------
        # Readiness: apply familiarity-derived multipliers to TeamState.tactics (in-memory only)
        # ------------------------------------------------------------
        if prepared_ready is not None:
            try:
                mult_h = prepared_ready.tactics_mult_by_team.get(hid)
                if mult_h is not None:
                    readiness.apply_readiness_to_team_state(home, mult_h)

                mult_a = prepared_ready.tactics_mult_by_team.get(aid)
                if mult_a is not None:
                    readiness.apply_readiness_to_team_state(away, mult_a)
            except Exception:
                logger.warning(
                    "READINESS_APPLY_FAILED game_date=%s home=%s away=%s",
                    game_date,
                    str(home_team_id),
                    str(away_team_id),
                    exc_info=True,
                )

        # ------------------------------------------------------------
        # Fatigue: prepare between-game condition (start_energy + energy_cap)
        # ------------------------------------------------------------
        prepared_fat = None
        try:
            prepared_fat = fatigue.prepare_game_fatigue(
                repo,
                game_date_iso=game_date,
                season_year=int(season_year),
                home=home,
                away=away,
            )
        except Exception:
            logger.warning(
                "FATIGUE_PREPARE_FAILED game_date=%s home=%s away=%s",
                game_date,
                str(home_team_id),
                str(away_team_id),
                exc_info=True,
            )

        # ------------------------------------------------------------
        # Injury: in-game hook (segment-level) + simulate
        # ------------------------------------------------------------
        injury_hook = None
        if prepared_inj is not None:
            try:
                injury_hook = injury.make_in_game_injury_hook(prepared_inj, context=context, home=home, away=away)
            except Exception:
                logger.warning(
                    "INJURY_HOOK_BUILD_FAILED game_date=%s home=%s away=%s",
                    game_date,
                    str(home_team_id),
                    str(away_team_id),
                    exc_info=True,
                )
                injury_hook = None

        raw_result = simulate_game(rng, home, away, context=context, injury_hook=injury_hook)

        # ------------------------------------------------------------
        # Post-game finalize: fatigue + injuries
        # ------------------------------------------------------------
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
                    "FATIGUE_FINALIZE_FAILED game_date=%s home=%s away=%s",
                    game_date,
                    str(home_team_id),
                    str(away_team_id),
                    exc_info=True,
                )

        if prepared_inj is not None:
            try:
                injury.finalize_game_injuries(
                    repo,
                    prepared=prepared_inj,
                    home=home,
                    away=away,
                    raw_result=raw_result,
                )
            except Exception:
                logger.warning(
                    "INJURY_FINALIZE_FAILED game_date=%s home=%s away=%s",
                    game_date,
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
                    "READINESS_FINALIZE_FAILED game_date=%s home=%s away=%s",
                    game_date,
                    str(home_team_id),
                    str(away_team_id),
                    exc_info=True,
                )

    v2_result = adapt_matchengine_result_to_v2(
        raw_result,
        context,
        engine_name="matchengine_v3",
    )
    return ingest_game_result(game_result=v2_result, game_date=game_date)


# -----------------------------------------------------------------------------
# Common simulation pipeline helpers
# -----------------------------------------------------------------------------


class _MonthlyTickCache:
    """Tiny cache to avoid running monthly tick checks repeatedly within the same month."""

    def __init__(self) -> None:
        self.last_month_key: Optional[str] = None


def _maybe_run_monthly_ticks(
    *,
    db_path: str,
    game_date_iso: str,
    tick_cache: Optional[_MonthlyTickCache] = None,
) -> None:
    """Run monthly growth + agency ticks (idempotent) with optional month-level caching."""

    gd = str(game_date_iso)[:10]
    month_key = gd[:7]

    if tick_cache is not None:
        if tick_cache.last_month_key == month_key:
            return
        tick_cache.last_month_key = month_key

    # One shared snapshot for both ticks (perf + consistent view).
    state_snapshot = None
    try:
        state_snapshot = export_full_state_snapshot()
    except Exception:
        # Snapshot failures must never prevent games.
        logger.warning("STATE_SNAPSHOT_FAILED_FOR_MONTHLY_TICKS date=%s", gd, exc_info=True)
        state_snapshot = None

    # Growth tick
    try:
        from training.checkpoints import maybe_run_monthly_growth_tick

        maybe_run_monthly_growth_tick(
            db_path=str(db_path),
            game_date_iso=gd,
            state_snapshot=state_snapshot,
        )
    except Exception:
        logger.warning("MONTHLY_GROWTH_TICK_FAILED date=%s", gd, exc_info=True)

    # Agency tick
    try:
        from agency.checkpoints import maybe_run_monthly_agency_tick

        maybe_run_monthly_agency_tick(
            db_path=str(db_path),
            game_date_iso=gd,
            state_snapshot=state_snapshot,
        )
    except Exception:
        logger.warning("MONTHLY_AGENCY_TICK_FAILED date=%s", gd, exc_info=True)


def _simulate_from_schedule_entry(
    entry: Dict[str, Any],
    *,
    league_context: Dict[str, Any],
    home_tactics: Optional[Dict[str, Any]] = None,
    away_tactics: Optional[Dict[str, Any]] = None,
    tick_cache: Optional[_MonthlyTickCache] = None,
) -> Dict[str, Any]:
    """Simulate a game using the master_schedule entry as SSOT.

    This is the shared internal pipeline used by BOTH:
      - advance_league_until (auto league sim)
      - simulate_single_game (user-driven)

    It also runs monthly growth + agency checkpoints in one common place.
    """

    if not isinstance(entry, dict):
        raise ValueError("schedule entry must be a dict")

    game_id = str(entry.get("game_id") or "").strip()
    date_str = str(entry.get("date") or "").strip()[:10]
    home_id = str(entry.get("home_team_id") or "").strip().upper()
    away_id = str(entry.get("away_team_id") or "").strip().upper()
    phase = str(entry.get("phase") or "regular")

    if not game_id or not date_str or not home_id or not away_id:
        raise ValueError("master_schedule entry missing required fields (game_id/date/home_team_id/away_team_id)")

    # Run monthly checkpoints (must never crash games).
    try:
        _maybe_run_monthly_ticks(db_path=get_db_path(), game_date_iso=date_str, tick_cache=tick_cache)
    except Exception:
        logger.warning("MONTHLY_TICKS_FAILED date=%s game_id=%s", date_str, game_id, exc_info=True)

    # SSOT: build context from the schedule entry only.
    entry_for_ctx = dict(entry)
    entry_for_ctx["game_id"] = game_id
    entry_for_ctx["date"] = date_str
    entry_for_ctx["home_team_id"] = home_id
    entry_for_ctx["away_team_id"] = away_id

    context = build_context_from_master_schedule_entry(
        entry=entry_for_ctx,
        league_state=league_context,
        phase=phase,
    )

    return _run_match(
        home_team_id=home_id,
        away_team_id=away_id,
        game_date=date_str,
        home_tactics=home_tactics,
        away_tactics=away_tactics,
        context=context,
    )


def advance_league_until(
    target_date_str: str,
    user_team_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    league_full = export_full_state_snapshot().get("league", {})
    master_schedule = league_full.get("master_schedule", {})
    by_date: Dict[str, List[str]] = master_schedule.get("by_date") or {}
    games: List[Dict[str, Any]] = master_schedule.get("games") or []
    by_id = master_schedule.get("by_id")

    if not by_date or not games:
        raise RuntimeError(
            "Master schedule is not initialized. Expected state.startup_init_state() to run before calling advance_league_until()."
        )

    # Defensive: ensure we have a fast game_id -> entry index.
    if not isinstance(by_id, dict) or len(by_id) != len(games):
        by_id = {g.get("game_id"): g for g in games if isinstance(g, dict) and g.get("game_id")}
    
    try:
        target_date = date.fromisoformat(target_date_str)
    except ValueError as exc:
        raise ValueError(f"invalid target_date: {target_date_str}") from exc

    league_context = get_league_context_snapshot()
    current_date_str = league_context.get("current_date")
    if current_date_str:
        try:
            current_date = date.fromisoformat(current_date_str)
        except ValueError:
            current_date = target_date
    else:
        if league_context.get("season_start"):
            try:
                season_start = date.fromisoformat(league_context["season_start"])
            except ValueError:
                season_start = target_date
        else:
            season_start = target_date
        current_date = season_start - timedelta(days=1)

    simulated_game_objs: List[Dict[str, Any]] = []
    user_team_upper = user_team_id.upper() if user_team_id else None

    tick_cache = _MonthlyTickCache()

    day = current_date + timedelta(days=1)
    while day <= target_date:
        day_str = day.isoformat()

        game_ids = by_date.get(day_str, [])
        if not game_ids:
            day += timedelta(days=1)
            continue

        for gid in game_ids:
            g = by_id.get(gid) if isinstance(by_id, dict) else None
            if not isinstance(g, dict):
                continue
            if g.get("status") == "final":
                continue

            home_id = str(g.get("home_team_id") or "").upper()
            away_id = str(g.get("away_team_id") or "").upper()
            if not home_id or not away_id:
                continue

            if user_team_upper and (home_id == user_team_upper or away_id == user_team_upper):
                continue

            # Ensure the SSOT date on the entry matches the loop date.
            entry = dict(g)
            entry["date"] = day_str

            game_obj = _simulate_from_schedule_entry(
                entry,
                league_context=league_context,
                tick_cache=tick_cache,
            )
            simulated_game_objs.append(game_obj)

        day += timedelta(days=1)

    set_current_date(target_date_str)
    return simulated_game_objs


def simulate_single_game(
    home_team_id: str,
    away_team_id: str,
    game_date: Optional[str] = None,
    home_tactics: Optional[Dict[str, Any]] = None,
    away_tactics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """User-driven single game simulation.

    IMPORTANT (New Normal)
    ----------------------
    This function is now schedule-backed. It MUST find a matching master_schedule entry
    for (game_date, home_team_id, away_team_id), and will simulate using that entry's
    game_id/date/home/away as SSOT.
    """

    hid = schema.normalize_team_id(str(home_team_id)).upper()
    aid = schema.normalize_team_id(str(away_team_id)).upper()

    game_date_str = (game_date or get_current_date_as_date().isoformat())[:10]

    league_full = export_full_state_snapshot().get("league", {})
    master_schedule = league_full.get("master_schedule", {})
    by_date: Dict[str, List[str]] = master_schedule.get("by_date") or {}
    by_id = master_schedule.get("by_id")
    games: List[Dict[str, Any]] = master_schedule.get("games") or []

    if not by_date or not games:
        raise RuntimeError(
            "Master schedule is not initialized. Expected state.startup_init_state() to run before calling simulate_single_game()."
        )

    if not isinstance(by_id, dict) or len(by_id) != len(games):
        by_id = {g.get("game_id"): g for g in games if isinstance(g, dict) and g.get("game_id")}


    candidate_ids = by_date.get(game_date_str) or []
    if not candidate_ids:
        raise ValueError(f"No scheduled games on date={game_date_str}")

    entry: Optional[Dict[str, Any]] = None
    for gid in candidate_ids:
        g = by_id.get(gid) if isinstance(by_id, dict) else None
        if not isinstance(g, dict):
            continue
        if str(g.get("home_team_id") or "").upper() == hid and str(g.get("away_team_id") or "").upper() == aid:
            entry = g
            break

    if entry is None:
        # Helpful diagnostics: check if the matchup exists but home/away is swapped.
        for gid in candidate_ids:
            g = by_id.get(gid) if isinstance(by_id, dict) else None
            if not isinstance(g, dict):
                continue
            if str(g.get("home_team_id") or "").upper() == aid and str(g.get("away_team_id") or "").upper() == hid:
                raise ValueError(
                    f"Scheduled game exists on {game_date_str} but home/away mismatch. "
                    f"Schedule expects home={aid}, away={hid} (you sent home={hid}, away={aid})."
                )

        raise ValueError(f"Scheduled game not found for date={game_date_str} home={hid} away={aid}")

    if str(entry.get("status") or "") == "final":
        raise ValueError(f"Game already simulated: game_id={entry.get('game_id')}")

    league_context = get_league_context_snapshot()

    entry_for_sim = dict(entry)
    entry_for_sim["date"] = game_date_str

    return _simulate_from_schedule_entry(
        entry_for_sim,
        league_context=league_context,
        home_tactics=home_tactics,
        away_tactics=away_tactics,
        tick_cache=None,
    )
