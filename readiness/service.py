from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Dict, Mapping, Optional, Tuple

import game_time
import schema
from league_repo import LeagueRepo
from matchengine_v3.models import TeamState
from sim.roster_adapter import resolve_effective_schemes

from . import config as r_cfg
from . import formulas as r_f
from . import repo as r_repo
from .types import PreparedGameReadiness, PreparedTeamSchemes, TacticsMultipliers

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _merge_mods(dst: Dict[str, Dict[str, float]], pid: str, add: Mapping[str, float]) -> None:
    """Accumulate attribute deltas into ``dst[pid]``.

    This is a small local helper because it deals with the exact representation
    used by roster_adapter.build_team_state_from_db (attrs_mods_by_pid).

    Math SSOT lives in readiness.formulas; this function is only about merging.
    """

    if not add:
        return
    bucket = dst.setdefault(pid, {})
    for k, v in add.items():
        try:
            dv = float(v)
        except Exception:
            continue
        if dv == 0.0:
            continue
        bucket[str(k)] = float(bucket.get(str(k), 0.0) or 0.0) + dv


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def prepare_game_readiness(
    repo: LeagueRepo,
    *,
    game_date_iso: str,
    season_year: int,
    home_team_id: str,
    away_team_id: str,
    home_tactics: Optional[Mapping[str, Any]] = None,
    away_tactics: Optional[Mapping[str, Any]] = None,
    unavailable_pids_by_team: Optional[Mapping[str, set[str]]] = None,
) -> PreparedGameReadiness:
    """Prepare readiness state for a game.

    Returns a PreparedGameReadiness object that contains:
    - per-player sharpness values after decay up to game start
    - per-team scheme familiarity values after decay up to game start
    - attrs_mods_by_pid (sharpness + optional familiarity)
    - tactics_mult_by_team (from familiarity)

    This function does not write to SQLite; persistence happens in
    ``finalize_game_readiness``.

    SSOT / drift prevention
    -----------------------
    - Tactics (effective scheme selection) is delegated to
      ``sim.roster_adapter.resolve_effective_schemes``.
    - All readiness math (clamp/decay/gain) is delegated to
      ``readiness.formulas``.
    """

    gdi = game_time.require_date_iso(game_date_iso, field="game_date_iso")
    gdate = _dt.date.fromisoformat(gdi)

    hid = str(schema.normalize_team_id(home_team_id, strict=True)).upper()
    aid = str(schema.normalize_team_id(away_team_id, strict=True)).upper()
    if hid == aid:
        raise ValueError(f"prepare_game_readiness: home/away team_id must differ (both {hid!r})")

    # Effective schemes for this game (SSOT: roster_adapter applies coach presets).
    home_off, home_def = resolve_effective_schemes(hid, home_tactics)
    away_off, away_def = resolve_effective_schemes(aid, away_tactics)

    # Involved players: active rosters for both teams.
    home_roster = repo.get_team_roster(hid)
    away_roster = repo.get_team_roster(aid)
    home_pids = [
        str(schema.normalize_player_id(r.get("player_id"), strict=False))
        for r in (home_roster or [])
        if r.get("player_id")
    ]
    away_pids = [
        str(schema.normalize_player_id(r.get("player_id"), strict=False))
        for r in (away_roster or [])
        if r.get("player_id")
    ]

    # De-duplicate while preserving order.
    all_pids = list(dict.fromkeys(home_pids + away_pids))

    # Optional: injury-derived OUT/unavailable PID hint (game-date availability).
    #
    # SSOT: injury status lives in the injury subsystem. This is only a read-only
    # hint passed down by the caller (typically league_sim after injury.prepare).
    out_pids: set[str] = set()
    if unavailable_pids_by_team:
        try:
            for raw_tid, raw_pids in (unavailable_pids_by_team or {}).items():
                try:
                    tid = str(schema.normalize_team_id(raw_tid, strict=False)).upper()
                except Exception:
                    tid = str(raw_tid or "").upper()
                if tid not in (hid, aid):
                    continue
                for raw_pid in (raw_pids or []):
                    try:
                        pid = str(schema.normalize_player_id(raw_pid, strict=False))
                    except Exception:
                        pid = str(raw_pid).strip()
                    if pid:
                        out_pids.add(pid)
        except Exception:
            out_pids = set()

    sharpness_pre_by_pid: Dict[str, float] = {}
    attrs_mods_by_pid: Dict[str, Dict[str, float]] = {}

    schemes_by_team: Dict[str, PreparedTeamSchemes] = {}
    tactics_mult_by_team: Dict[str, TacticsMultipliers] = {}

    with repo.transaction() as cur:
        # --- Player sharpness ---
        st_by_pid = r_repo.get_player_sharpness_states(cur, all_pids, season_year=int(season_year))

        for pid in all_pids:
            row = st_by_pid.get(pid) or {}
            sharp0 = float(row.get("sharpness", r_cfg.SHARPNESS_DEFAULT) or r_cfg.SHARPNESS_DEFAULT)
            days = r_f.days_since(last_date_iso=row.get("last_date"), on_date=gdate)
            # Faster sharpness decay for players who are OUT on game_date (injury hint).
            if pid in out_pids:
                try:
                    decay_out = float(getattr(r_cfg, "SHARPNESS_DECAY_PER_DAY_OUT"))
                except Exception:
                    decay_out = None
                if decay_out is not None:
                    sharp_pre = r_f.decay_sharpness_linear(sharp0, days=days, decay_per_day=decay_out)
                else:
                    sharp_pre = r_f.decay_sharpness_linear(sharp0, days=days)
            else:
                sharp_pre = r_f.decay_sharpness_linear(sharp0, days=days)
            sharpness_pre_by_pid[pid] = float(sharp_pre)

            mods = r_f.sharpness_attr_mods(sharp_pre)
            if mods:
                attrs_mods_by_pid[pid] = dict(mods)

        # --- Team scheme familiarity (only schemes selected for this game) ---
        def _load_team_fam(team_id: str, *, off_key: str, def_key: str) -> Tuple[float, float]:
            rows = r_repo.get_team_scheme_familiarity_states(
                cur,
                team_id=str(team_id),
                season_year=int(season_year),
                schemes=[("offense", str(off_key)), ("defense", str(def_key))],
            )

            off_row = rows.get(("offense", str(off_key))) or {}
            def_row = rows.get(("defense", str(def_key))) or {}

            off0 = float(off_row.get("value", r_cfg.FAMILIARITY_DEFAULT) or r_cfg.FAMILIARITY_DEFAULT)
            def0 = float(def_row.get("value", r_cfg.FAMILIARITY_DEFAULT) or r_cfg.FAMILIARITY_DEFAULT)

            off_days = r_f.days_since(last_date_iso=off_row.get("last_date"), on_date=gdate)
            def_days = r_f.days_since(last_date_iso=def_row.get("last_date"), on_date=gdate)

            off_pre = r_f.decay_familiarity_exp(off0, days=off_days)
            def_pre = r_f.decay_familiarity_exp(def0, days=def_days)
            return (float(off_pre), float(def_pre))

        home_off_fam, home_def_fam = _load_team_fam(hid, off_key=home_off, def_key=home_def)
        away_off_fam, away_def_fam = _load_team_fam(aid, off_key=away_off, def_key=away_def)

        schemes_by_team[hid] = PreparedTeamSchemes(
            team_id=hid,
            offense_scheme_key=str(home_off),
            defense_scheme_key=str(home_def),
            offense_familiarity_pre=float(home_off_fam),
            defense_familiarity_pre=float(home_def_fam),
        )
        schemes_by_team[aid] = PreparedTeamSchemes(
            team_id=aid,
            offense_scheme_key=str(away_off),
            defense_scheme_key=str(away_def),
            offense_familiarity_pre=float(away_off_fam),
            defense_familiarity_pre=float(away_def_fam),
        )

        tactics_mult_by_team[hid] = r_f.tactics_mult_from_familiarity(off_fam=home_off_fam, def_fam=home_def_fam)
        tactics_mult_by_team[aid] = r_f.tactics_mult_from_familiarity(off_fam=away_off_fam, def_fam=away_def_fam)

        # Optional: also apply subtle team-wide IQ/vision/rotation mods derived from familiarity.
        if bool(getattr(r_cfg, "ENABLE_FAMILIARITY_ATTR_MODS", False)):
            home_off_mods, home_def_mods = r_f.familiarity_attr_mods(off_fam=home_off_fam, def_fam=home_def_fam)
            away_off_mods, away_def_mods = r_f.familiarity_attr_mods(off_fam=away_off_fam, def_fam=away_def_fam)

            for pid in home_pids:
                _merge_mods(attrs_mods_by_pid, pid, home_off_mods)
                _merge_mods(attrs_mods_by_pid, pid, home_def_mods)
            for pid in away_pids:
                _merge_mods(attrs_mods_by_pid, pid, away_off_mods)
                _merge_mods(attrs_mods_by_pid, pid, away_def_mods)

    return PreparedGameReadiness(
        game_date_iso=gdi,
        season_year=int(season_year),
        home_team_id=hid,
        away_team_id=aid,
        sharpness_pre_by_pid=sharpness_pre_by_pid,
        schemes_by_team=schemes_by_team,
        attrs_mods_by_pid=attrs_mods_by_pid,
        tactics_mult_by_team=tactics_mult_by_team,
    )


def apply_readiness_to_team_state(team: TeamState, mult: TacticsMultipliers) -> None:
    """Apply familiarity-derived multipliers to a TeamState.tactics in memory.

    This mutates ``team.tactics`` (in-memory only). Persisted SSOT is stored in
    SQLite.

    Commercial safety:
    - If tactics is missing or fields are not numeric, this no-ops rather than
      crashing.
    """

    try:
        tactics = getattr(team, "tactics", None)
    except Exception:
        tactics = None
    if tactics is None:
        return

    def _safe_mul(get_name: str, m: float) -> None:
        try:
            base = float(getattr(tactics, get_name))
        except Exception:
            return
        val = float(base) * float(m)
        val = r_f.clamp(val, float(r_cfg.TACTICS_MULT_MIN), float(r_cfg.TACTICS_MULT_MAX))
        try:
            setattr(tactics, get_name, float(val))
        except Exception:
            return

    _safe_mul("scheme_weight_sharpness", float(mult.scheme_weight_sharpness))
    _safe_mul("scheme_outcome_strength", float(mult.scheme_outcome_strength))
    _safe_mul("def_scheme_weight_sharpness", float(mult.def_scheme_weight_sharpness))
    _safe_mul("def_scheme_outcome_strength", float(mult.def_scheme_outcome_strength))


def finalize_game_readiness(
    repo: LeagueRepo,
    *,
    prepared: PreparedGameReadiness,
    raw_result: Mapping[str, Any],
) -> None:
    """Finalize and persist readiness after the game.

    - Updates player sharpness using minutes played.
    - Updates scheme familiarity for the schemes used in the game.

    Commercial safety:
    - If result payload is missing required fields, we log and no-op.
    """

    if not isinstance(raw_result, Mapping):
        logger.warning("finalize_game_readiness: raw_result is not a mapping; skipping")
        return

    gs = raw_result.get("game_state")
    if not isinstance(gs, Mapping):
        logger.warning("finalize_game_readiness: raw_result.game_state missing; skipping")
        return

    minutes_by_team = gs.get("minutes_played_sec")
    if not isinstance(minutes_by_team, Mapping):
        logger.warning("finalize_game_readiness: raw_result.game_state.minutes_played_sec missing; skipping")
        return

    hid = str(prepared.home_team_id).upper()
    aid = str(prepared.away_team_id).upper()

    def _mins_map_for_team(tid: str) -> Mapping[str, Any]:
        m = minutes_by_team.get(tid) or {}
        return m if isinstance(m, Mapping) else {}

    mins_home = _mins_map_for_team(hid)
    mins_away = _mins_map_for_team(aid)

    def _minutes_for_pid(pid: str) -> float:
        try:
            sec = mins_home.get(pid)
            if sec is None:
                sec = mins_away.get(pid)
            return float(sec or 0.0) / 60.0
        except Exception:
            return 0.0

    # Use the game date as the UTC-like timestamp (SSOT: never use host OS clock).
    now_iso = game_time.utc_like_from_date_iso(prepared.game_date_iso, field="game_date_iso")

    # Build upsert payloads.
    sharp_rows: Dict[str, Dict[str, Any]] = {}
    fam_rows_by_team: Dict[str, Dict[r_repo.SchemeKey, Dict[str, Any]]] = {hid: {}, aid: {}}

    for pid, sharp_pre in (prepared.sharpness_pre_by_pid or {}).items():
        minutes = _minutes_for_pid(str(pid))
        sharp_post = r_f.apply_sharpness_gain(float(sharp_pre), minutes=minutes)
        sharp_rows[str(pid)] = {"sharpness": float(sharp_post), "last_date": prepared.game_date_iso}

    for tid in (hid, aid):
        tprep = (prepared.schemes_by_team or {}).get(tid)
        if tprep is None:
            continue
        off_post = r_f.apply_familiarity_gain(float(tprep.offense_familiarity_pre))
        def_post = r_f.apply_familiarity_gain(float(tprep.defense_familiarity_pre))

        fam_rows_by_team[tid][("offense", str(tprep.offense_scheme_key))] = {
            "value": float(off_post),
            "last_date": prepared.game_date_iso,
        }
        fam_rows_by_team[tid][("defense", str(tprep.defense_scheme_key))] = {
            "value": float(def_post),
            "last_date": prepared.game_date_iso,
        }

    with repo.transaction() as cur:
        try:
            r_repo.upsert_player_sharpness_states(
                cur,
                sharp_rows,
                season_year=int(prepared.season_year),
                now=str(now_iso),
            )
        except Exception:
            logger.warning("finalize_game_readiness: sharpness upsert failed; continuing", exc_info=True)

        for tid, rows in fam_rows_by_team.items():
            if not rows:
                continue
            try:
                r_repo.upsert_team_scheme_familiarity_states(
                    cur,
                    rows,
                    team_id=str(tid),
                    season_year=int(prepared.season_year),
                    now=str(now_iso),
                )
            except Exception:
                logger.warning("finalize_game_readiness: familiarity upsert failed team=%s", tid, exc_info=True)
