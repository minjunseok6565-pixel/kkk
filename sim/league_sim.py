from __future__ import annotations

import concurrent.futures
import logging
import os
import random
from contextlib import contextmanager
from dataclasses import dataclass
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
    get_team_tactics_snapshot,
    ingest_game_result,
    set_current_date,
)
from sim.roster_adapter import build_team_state_from_db

logger = logging.getLogger(__name__)


def _normalize_team_id(team_id: str) -> str:
    return schema.normalize_team_id(str(team_id)).upper()


def _get_master_schedule_snapshot() -> tuple[Dict[str, List[str]], Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    league_full = export_full_state_snapshot().get("league", {})
    master_schedule = league_full.get("master_schedule", {})
    by_date: Dict[str, List[str]] = master_schedule.get("by_date") or {}
    games: List[Dict[str, Any]] = master_schedule.get("games") or []
    by_id_raw = master_schedule.get("by_id")

    if not by_date or not games:
        raise RuntimeError(
            "Master schedule is not initialized. Expected state.startup_init_state() to run before simulation calls."
        )

    if isinstance(by_id_raw, dict) and len(by_id_raw) == len(games):
        by_id: Dict[str, Dict[str, Any]] = by_id_raw
    else:
        by_id = {g.get("game_id"): g for g in games if isinstance(g, dict) and g.get("game_id")}

    return by_date, by_id, games


def _find_next_user_game_entry(*, user_team_id: str, from_date: str) -> Optional[Dict[str, Any]]:
    tid = _normalize_team_id(user_team_id)
    by_date, by_id, _ = _get_master_schedule_snapshot()
    try:
        base_day = date.fromisoformat(str(from_date)[:10])
    except Exception as exc:
        raise ValueError(f"invalid from_date: {from_date}") from exc

    candidate_dates = sorted(d for d in by_date.keys() if isinstance(d, str) and len(d) >= 10)
    for day_str in candidate_dates:
        try:
            d = date.fromisoformat(day_str[:10])
        except Exception:
            continue
        if d < base_day:
            continue
        for gid in by_date.get(day_str, []):
            entry = by_id.get(gid)
            if not isinstance(entry, dict):
                continue
            if str(entry.get("status") or "").lower() == "final":
                continue
            home_id = str(entry.get("home_team_id") or "").upper()
            away_id = str(entry.get("away_team_id") or "").upper()
            if home_id == tid or away_id == tid:
                e = dict(entry)
                e["date"] = day_str
                return e
    return None


@contextmanager
def _repo_ctx() -> LeagueRepo:
    db_path = get_db_path()
    with LeagueRepo(db_path) as repo:
        yield repo


@dataclass(frozen=True, slots=True)
class _PreparedMatchRuntime:
    home: Any
    away: Any
    prepared_inj: Any
    prepared_ready: Any
    prepared_fat: Any


DAY_SIM_ENABLE_PARALLEL = True
DAY_SIM_RETRY_SERIAL_ON_COMPUTE_FAILURE = True
DAY_SIM_MAX_WORKERS = min(4, int(os.cpu_count() or 1))


@dataclass(frozen=True, slots=True)
class _PreparedJob:
    game_id: str
    game_date: str
    home_team_id: str
    away_team_id: str
    context: schema.GameContext
    runtime: _PreparedMatchRuntime


def _prepare_match_runtime(
    *,
    repo: LeagueRepo,
    home_team_id: str,
    away_team_id: str,
    game_date: str,
    season_year: int,
    home_tactics: Optional[Dict[str, Any]],
    away_tactics: Optional[Dict[str, Any]],
    context: schema.GameContext,
) -> _PreparedMatchRuntime:
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

    return _PreparedMatchRuntime(
        home=home,
        away=away,
        prepared_inj=prepared_inj,
        prepared_ready=prepared_ready,
        prepared_fat=prepared_fat,
    )


def _compute_match_raw(
    *,
    runtime: _PreparedMatchRuntime,
    context: schema.GameContext,
    game_date: str,
    home_team_id: str,
    away_team_id: str,
) -> Mapping[str, Any]:
    # ------------------------------------------------------------
    # Injury: in-game hook (segment-level) + simulate
    # ------------------------------------------------------------
    injury_hook = None
    if runtime.prepared_inj is not None:
        try:
            injury_hook = injury.make_in_game_injury_hook(
                runtime.prepared_inj,
                context=context,
                home=runtime.home,
                away=runtime.away,
            )
        except Exception:
            logger.warning(
                "INJURY_HOOK_BUILD_FAILED game_date=%s home=%s away=%s",
                game_date,
                str(home_team_id),
                str(away_team_id),
                exc_info=True,
            )
            injury_hook = None

    rng = random.Random()
    return simulate_game(rng, runtime.home, runtime.away, context=context, injury_hook=injury_hook)


def _finalize_match_side_effects(
    *,
    repo: LeagueRepo,
    runtime: _PreparedMatchRuntime,
    raw_result: Mapping[str, Any],
    game_date: str,
    home_team_id: str,
    away_team_id: str,
) -> None:
    # ------------------------------------------------------------
    # Post-game finalize: fatigue + injuries
    # ------------------------------------------------------------
    if runtime.prepared_fat is not None:
        try:
            fatigue.finalize_game_fatigue(
                repo,
                prepared=runtime.prepared_fat,
                home=runtime.home,
                away=runtime.away,
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

    if runtime.prepared_inj is not None:
        try:
            injury.finalize_game_injuries(
                repo,
                prepared=runtime.prepared_inj,
                home=runtime.home,
                away=runtime.away,
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

    if runtime.prepared_ready is not None:
        try:
            readiness.finalize_game_readiness(
                repo,
                prepared=runtime.prepared_ready,
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


def _adapt_and_ingest_result(*, raw_result: Mapping[str, Any], context: schema.GameContext, game_date: str) -> Dict[str, Any]:
    v2_result = adapt_matchengine_result_to_v2(
        raw_result,
        context,
        engine_name="matchengine_v3",
    )
    return ingest_game_result(game_result=v2_result, game_date=game_date)


def _prepare_day_jobs_serial(
    *,
    repo: LeagueRepo,
    game_date: str,
    entries: List[Dict[str, Any]],
    league_context: Dict[str, Any],
    tactics_by_team: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> List[_PreparedJob]:
    """Prepare same-day game jobs in deterministic serial order."""

    jobs: List[_PreparedJob] = []
    tactics_map = tactics_by_team or {}

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        game_id = str(entry.get("game_id") or "").strip()
        date_str = str(entry.get("date") or game_date).strip()[:10]
        home_id = str(entry.get("home_team_id") or "").strip().upper()
        away_id = str(entry.get("away_team_id") or "").strip().upper()
        phase = str(entry.get("phase") or "regular")

        if not game_id or not date_str or not home_id or not away_id:
            raise ValueError("master_schedule entry missing required fields (game_id/date/home_team_id/away_team_id)")

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
        season_year = fatigue.season_year_from_season_id(str(getattr(context, "season_id", "") or ""))

        home_tactics = tactics_map.get(home_id)
        away_tactics = tactics_map.get(away_id)

        runtime = _prepare_match_runtime(
            repo=repo,
            home_team_id=home_id,
            away_team_id=away_id,
            game_date=date_str,
            season_year=int(season_year),
            home_tactics=dict(home_tactics) if isinstance(home_tactics, Mapping) else None,
            away_tactics=dict(away_tactics) if isinstance(away_tactics, Mapping) else None,
            context=context,
        )

        jobs.append(
            _PreparedJob(
                game_id=game_id,
                game_date=date_str,
                home_team_id=home_id,
                away_team_id=away_id,
                context=context,
                runtime=runtime,
            )
        )

    return jobs


def _compute_day_jobs_parallel(*, jobs: List[_PreparedJob]) -> Dict[str, Mapping[str, Any]]:
    """Compute same-day jobs in parallel with deterministic fallback policy."""

    if not jobs:
        return {}

    if len(jobs) == 1 or not DAY_SIM_ENABLE_PARALLEL or DAY_SIM_MAX_WORKERS <= 1:
        only = jobs[0]
        return {
            only.game_id: _compute_match_raw(
                runtime=only.runtime,
                context=only.context,
                game_date=only.game_date,
                home_team_id=only.home_team_id,
                away_team_id=only.away_team_id,
            )
        }

    worker_count = max(1, min(DAY_SIM_MAX_WORKERS, len(jobs)))
    results: Dict[str, Mapping[str, Any]] = {}
    failed: Dict[str, Exception] = {}

    def _task(job: _PreparedJob) -> tuple[str, Mapping[str, Any]]:
        raw = _compute_match_raw(
            runtime=job.runtime,
            context=job.context,
            game_date=job.game_date,
            home_team_id=job.home_team_id,
            away_team_id=job.away_team_id,
        )
        return (job.game_id, raw)

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as ex:
        future_to_job = {ex.submit(_task, job): job for job in jobs}
        for future in concurrent.futures.as_completed(future_to_job):
            job = future_to_job[future]
            try:
                gid, raw = future.result()
                results[gid] = raw
            except Exception as exc:
                failed[job.game_id] = exc

    if failed and not DAY_SIM_RETRY_SERIAL_ON_COMPUTE_FAILURE:
        failed_gid = next(iter(failed.keys()))
        raise RuntimeError(f"DAY_COMPUTE_FAILED game_id={failed_gid}") from failed[failed_gid]

    if failed:
        for job in jobs:
            if job.game_id not in failed:
                continue
            logger.warning("DAY_COMPUTE_PARALLEL_FAILED_FALLBACK_SERIAL game_id=%s", job.game_id, exc_info=failed[job.game_id])
            results[job.game_id] = _compute_match_raw(
                runtime=job.runtime,
                context=job.context,
                game_date=job.game_date,
                home_team_id=job.home_team_id,
                away_team_id=job.away_team_id,
            )

    return results


def _finalize_day_jobs_serial(
    *,
    repo: LeagueRepo,
    jobs: List[_PreparedJob],
    raw_results_by_game_id: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Finalize same-day jobs serially in original order and ingest results."""

    out: List[Dict[str, Any]] = []
    for job in jobs:
        raw_result = raw_results_by_game_id.get(job.game_id)
        if raw_result is None:
            raise ValueError(f"Missing computed raw result for game_id={job.game_id}")

        _finalize_match_side_effects(
            repo=repo,
            runtime=job.runtime,
            raw_result=raw_result,
            game_date=job.game_date,
            home_team_id=job.home_team_id,
            away_team_id=job.away_team_id,
        )
        out.append(_adapt_and_ingest_result(raw_result=raw_result, context=job.context, game_date=job.game_date))

    return out


def _run_match(
    *,
    home_team_id: str,
    away_team_id: str,
    game_date: str,
    home_tactics: Optional[Dict[str, Any]] = None,
    away_tactics: Optional[Dict[str, Any]] = None,
    context: schema.GameContext,
) -> Dict[str, Any]:
    with _repo_ctx() as repo:
        # Ensure schema is applied (idempotent). This guarantees fatigue/injury/readiness tables exist
        # even if the DB was created before the modules were added.
        repo.init_db()

        season_year = fatigue.season_year_from_season_id(str(getattr(context, "season_id", "") or ""))
        runtime = _prepare_match_runtime(
            repo=repo,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            game_date=game_date,
            season_year=int(season_year),
            home_tactics=home_tactics,
            away_tactics=away_tactics,
            context=context,
        )

        raw_result = _compute_match_raw(
            runtime=runtime,
            context=context,
            game_date=game_date,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
        )

        _finalize_match_side_effects(
            repo=repo,
            runtime=runtime,
            raw_result=raw_result,
            game_date=game_date,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
        )

    return _adapt_and_ingest_result(raw_result=raw_result, context=context, game_date=game_date)


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


def _simulate_day_non_user_games(
    *,
    day_str: str,
    game_ids: List[str],
    by_id: Mapping[str, Dict[str, Any]],
    user_team_upper: Optional[str],
    league_context: Dict[str, Any],
    tick_cache: Optional[_MonthlyTickCache],
) -> List[Dict[str, Any]]:
    """Simulate same-day non-user games with serial prepare/finalize and parallel compute."""

    day_entries: List[Dict[str, Any]] = []
    for gid in game_ids:
        g = by_id.get(gid) if isinstance(by_id, Mapping) else None
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

        entry = dict(g)
        entry["date"] = day_str
        day_entries.append(entry)

    if not day_entries:
        return []

    try:
        _maybe_run_monthly_ticks(db_path=get_db_path(), game_date_iso=day_str, tick_cache=tick_cache)
    except Exception:
        logger.warning("MONTHLY_TICKS_FAILED date=%s", day_str, exc_info=True)

    with _repo_ctx() as repo:
        repo.init_db()
        jobs = _prepare_day_jobs_serial(
            repo=repo,
            game_date=day_str,
            entries=day_entries,
            league_context=league_context,
            tactics_by_team=None,
        )
        raw_results = _compute_day_jobs_parallel(jobs=jobs)
        return _finalize_day_jobs_serial(
            repo=repo,
            jobs=jobs,
            raw_results_by_game_id=raw_results,
        )


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

        simulated_game_objs.extend(
            _simulate_day_non_user_games(
                day_str=day_str,
                game_ids=list(game_ids),
                by_id=by_id if isinstance(by_id, Mapping) else {},
                user_team_upper=user_team_upper,
                league_context=league_context,
                tick_cache=tick_cache,
            )
        )

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


def auto_advance_to_next_user_game_day(user_team_id: str) -> Dict[str, Any]:
    user_tid = _normalize_team_id(user_team_id)
    current_date = get_current_date_as_date()
    current_date_str = current_date.isoformat()

    next_entry = _find_next_user_game_entry(user_team_id=user_tid, from_date=current_date_str)
    if next_entry is None:
        raise ValueError(f"NO_NEXT_USER_GAME: user_team_id={user_tid}")

    target_date = str(next_entry.get("date") or "")[:10]
    if not target_date:
        raise ValueError("NO_NEXT_USER_GAME_DATE")

    simulated: List[Dict[str, Any]] = []
    if current_date_str < target_date:
        day_before = (date.fromisoformat(target_date) - timedelta(days=1)).isoformat()
        if day_before >= current_date_str:
            simulated = advance_league_until(day_before, user_team_id=user_tid)

    set_current_date(target_date)

    by_date, by_id, _games = _get_master_schedule_snapshot()
    user_pending = False
    other_pending_count = 0
    for gid in by_date.get(target_date, []):
        entry = by_id.get(gid)
        if not isinstance(entry, dict):
            continue
        if str(entry.get("status") or "").lower() == "final":
            continue
        home_id = str(entry.get("home_team_id") or "").upper()
        away_id = str(entry.get("away_team_id") or "").upper()
        if home_id == user_tid or away_id == user_tid:
            user_pending = True
        else:
            other_pending_count += 1

    return {
        "ok": True,
        "user_team_id": user_tid,
        "current_date_before": current_date_str,
        "next_user_game_date": target_date,
        "current_date_after": target_date,
        "auto_advance": {
            "from_exclusive": current_date_str,
            "to_inclusive": (date.fromisoformat(target_date) - timedelta(days=1)).isoformat() if current_date_str < target_date else current_date_str,
            "simulated_count": len(simulated),
            "simulated_game_ids": [str(g.get("game_id")) for g in simulated if isinstance(g, dict) and g.get("game_id")],
        },
        "game_day_status": {
            "date": target_date,
            "user_game_pending": bool(user_pending),
            "other_games_pending_count": int(other_pending_count),
        },
    }




def _load_saved_team_tactics(team_id: str) -> Optional[Dict[str, Any]]:
    """Load persisted team tactics payload from state storage.

    Returns None when no valid tactics payload is stored for the team.
    """
    tid = _normalize_team_id(team_id)
    record = get_team_tactics_snapshot(tid)
    if not isinstance(record, dict):
        return None
    tactics = record.get("tactics")
    if not isinstance(tactics, dict) or not tactics:
        return None
    return dict(tactics)

def progress_next_user_game_day(user_team_id: str, *, mode: str = "auto_if_needed") -> Dict[str, Any]:
    user_tid = _normalize_team_id(user_team_id)
    mode_norm = str(mode or "auto_if_needed")
    if mode_norm not in {"auto_if_needed", "strict_today_only"}:
        raise ValueError(f"INVALID_MODE: {mode_norm}")

    current_date = get_current_date_as_date()
    current_date_str = current_date.isoformat()

    next_entry = _find_next_user_game_entry(user_team_id=user_tid, from_date=current_date_str)
    if next_entry is None:
        raise ValueError(f"NO_NEXT_USER_GAME: user_team_id={user_tid}")

    target_date = str(next_entry.get("date") or "")[:10]
    if not target_date:
        raise ValueError("NO_NEXT_USER_GAME_DATE")

    if current_date_str < target_date and mode_norm == "strict_today_only":
        raise ValueError(f"USER_GAME_NOT_TODAY: current_date={current_date_str}, next_user_game_date={target_date}")

    pre_simulated: List[Dict[str, Any]] = []
    response_mode = "played_today"
    if current_date_str < target_date:
        day_before = (date.fromisoformat(target_date) - timedelta(days=1)).isoformat()
        if day_before >= current_date_str:
            pre_simulated = advance_league_until(day_before, user_team_id=user_tid)
        response_mode = "auto_advanced_then_played"

    # User game on target day
    next_entry = _find_next_user_game_entry(user_team_id=user_tid, from_date=target_date)
    if next_entry is None or str(next_entry.get("date") or "")[:10] != target_date:
        raise ValueError(f"USER_GAME_ALREADY_FINAL: date={target_date}, user_team_id={user_tid}")

    home_id = str(next_entry.get("home_team_id") or "")
    away_id = str(next_entry.get("away_team_id") or "")
    saved_user_tactics = _load_saved_team_tactics(user_tid)
    user_game_result = simulate_single_game(
        home_team_id=home_id,
        away_team_id=away_id,
        game_date=target_date,
        home_tactics=saved_user_tactics if str(home_id).upper() == user_tid else None,
        away_tactics=saved_user_tactics if str(away_id).upper() == user_tid else None,
    )

    # Same-day other games: run day batch (serial pre -> parallel compute -> serial post).
    by_date, by_id, _games = _get_master_schedule_snapshot()
    tick_cache = _MonthlyTickCache()
    day_game_ids = list(by_date.get(target_date, []) or [])
    other_simulated = _simulate_day_non_user_games(
        day_str=target_date,
        game_ids=day_game_ids,
        by_id=by_id,
        user_team_upper=user_tid,
        league_context=get_league_context_snapshot(),
        tick_cache=tick_cache,
    )
    set_current_date(target_date)

    total_simulated = list(pre_simulated)
    total_simulated.append(user_game_result)
    total_simulated.extend(other_simulated)

    return {
        "ok": True,
        "mode": response_mode,
        "user_team_id": user_tid,
        "current_date_before": current_date_str,
        "target_user_game_date": target_date,
        "current_date_after": get_current_date_as_date().isoformat(),
        "auto_advance": {
            "from_exclusive": current_date_str,
            "to_inclusive": (date.fromisoformat(target_date) - timedelta(days=1)).isoformat() if current_date_str < target_date else current_date_str,
            "simulated_count": len(pre_simulated),
            "simulated_game_ids": [str(g.get("game_id")) for g in pre_simulated if isinstance(g, dict) and g.get("game_id")],
        },
        "game_day": {
            "date": target_date,
            "user_game": {
                "game_id": user_game_result.get("game_id"),
                "home_team_id": user_game_result.get("home_team_id"),
                "away_team_id": user_game_result.get("away_team_id"),
                "status": "final",
            },
            "other_games_simulated_count": len(other_simulated),
            "other_game_ids": [str(g.get("game_id")) for g in other_simulated if isinstance(g, dict) and g.get("game_id")],
        },
        "totals": {
            "simulated_count": len(total_simulated),
        },
    }
