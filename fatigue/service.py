from __future__ import annotations

import datetime as _dt
import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

import game_time
import schema
from league_repo import LeagueRepo
from matchengine_v3.models import Player, TeamState
from matchengine_v3.sim_fatigue import _fatigue_archetype_for_pid, _get_offense_role_by_pid
from practice.service import resolve_practice_session
from practice import types as p_types
from training import config as training_config
from training import repo as training_repo
from training.types import intensity_multiplier

from . import config as fat_cfg
from . import repo as fat_repo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types (returned by prepare_game_fatigue, consumed by finalize_game_fatigue)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreparedPlayerFatigue:
    """Prepared fatigue inputs for one player for one game.

    Fields:
        st_rest: ST after applying rest recovery up to game start.
        lt_rest: LT after applying rest recovery up to game start.
        start_energy: match-engine energy at tip-off (0..1).
        energy_cap: in-game recovery cap for this game (0..1).
        intensity_mult: blended training intensity multiplier that affects recovery + LT gain.
    """

    st_rest: float
    lt_rest: float
    start_energy: float
    energy_cap: float
    intensity_mult: float


@dataclass(frozen=True, slots=True)
class PreparedTeamFatigue:
    by_pid: Dict[str, PreparedPlayerFatigue]


@dataclass(frozen=True, slots=True)
class PreparedGameFatigue:
    game_date_iso: str
    season_year: int
    home_team_id: str
    away_team_id: str
    home: PreparedTeamFatigue
    away: PreparedTeamFatigue


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _clamp(x: float, lo: float, hi: float) -> float:
    try:
        v = float(x)
    except Exception:
        v = 0.0
    if v < lo:
        return float(lo)
    if v > hi:
        return float(hi)
    return float(v)


def _clamp01(x: float) -> float:
    return _clamp(x, 0.0, 1.0)


def _lerp(a: float, b: float, t: float) -> float:
    return float(a) + (float(b) - float(a)) * float(t)


def _sigmoid(x: float) -> float:
    """Numerically-stable logistic sigmoid."""
    # Avoid overflow for large |x|
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _parse_date_iso(value: Any) -> Optional[_dt.date]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s[:10]
    try:
        return _dt.date.fromisoformat(s)
    except Exception:
        return None


def _rest_units(*, last_date: Optional[_dt.date], game_date: _dt.date) -> float:
    """Compute rest units between last_date and game_date.

    - Same-day (delta<=0): 0
    - Next day: OVERNIGHT_REST_UNITS
    - Two+ days: OVERNIGHT + (delta_days-1)

    This makes back-to-backs feel meaningfully different from a full rest day.
    """
    if last_date is None:
        return 0.0
    delta = (game_date - last_date).days
    if delta <= 0:
        return 0.0
    return float(fat_cfg.OVERNIGHT_REST_UNITS + max(0, delta - 1))


def _endurance_factor(p: Player) -> float:
    """Endurance recovery multiplier from derived ENDURANCE (0..100)."""
    try:
        end = float(getattr(p, "derived", {}).get("ENDURANCE", 50.0))
    except Exception:
        end = 50.0
    end01 = _clamp01(end / 100.0)
    return _lerp(fat_cfg.ENDURANCE_FACTOR_MIN, fat_cfg.ENDURANCE_FACTOR_MAX, end01)


def _age_recovery_factor(age: int) -> float:
    """Recovery slows after AGE_REC_START with a smooth sigmoid curve."""
    try:
        a = float(age)
    except Exception:
        a = 0.0
    x = (a - float(fat_cfg.AGE_REC_START)) / float(fat_cfg.AGE_REC_K)
    return 1.0 - float(fat_cfg.AGE_REC_DROP_MAX) * _sigmoid(x)


def _training_recovery_factor(intensity_mult: float) -> float:
    """Higher training intensity reduces recovery speed."""
    try:
        im = float(intensity_mult)
    except Exception:
        im = 1.0
    if im <= 0.0:
        return 1.0
    return float(im ** (-float(fat_cfg.TRAIN_REC_POW)))


def _combined_recovery_mult(p: Player, *, age: int, intensity_mult: float) -> float:
    r = _endurance_factor(p) * _age_recovery_factor(int(age)) * _training_recovery_factor(float(intensity_mult))
    return _clamp(float(r), float(fat_cfg.RECOVERY_MULT_MIN), float(fat_cfg.RECOVERY_MULT_MAX))


def _apply_rest_recovery(
    *,
    st: float,
    lt: float,
    rest_units: float,
    recovery_mult: float,
) -> Tuple[float, float]:
    """Apply exponential recovery for ST and LT."""
    st0 = _clamp01(st)
    lt0 = _clamp01(lt)
    ru = max(0.0, float(rest_units))
    r = float(recovery_mult)

    # Exponential decay: fast for ST, slow for LT.
    st_rest = st0 * math.exp(-float(fat_cfg.ST_REC_RATE) * ru * r)
    lt_rest = lt0 * math.exp(-float(fat_cfg.LT_REC_RATE) * ru * r)
    return (_clamp01(st_rest), _clamp01(lt_rest))


def _compute_start_energy_and_cap(*, st_rest: float, lt_rest: float) -> Tuple[float, float]:
    cond_raw = 1.0 - _clamp01(st_rest) - float(fat_cfg.LT_WEIGHT) * _clamp01(lt_rest)
    cond_raw = _clamp01(cond_raw)
    start_energy = _clamp(cond_raw, float(fat_cfg.START_ENERGY_MIN), 1.0)

    # Energy cap ensures condition matters throughout the game (bench recovery won't fully reset).
    cap = min(1.0, float(start_energy) + float(fat_cfg.CAP_BONUS))
    cap = max(float(start_energy), float(cap))  # invariant
    return (float(start_energy), float(cap))


def _plan_intensity_mult(plan: Any) -> float:
    if isinstance(plan, Mapping):
        return float(intensity_multiplier(plan.get("intensity")))
    return 1.0


def _blended_intensity_mult(*, team_mult: float, player_mult: float) -> float:
    """Blend team vs player training intensity.

    Uses training.config TEAM/PLAYER_INTENSITY_SHARE and normalizes if needed.
    """
    try:
        ts = float(training_config.TEAM_INTENSITY_SHARE)
    except Exception:
        ts = 0.6
    try:
        ps = float(training_config.PLAYER_INTENSITY_SHARE)
    except Exception:
        ps = 0.4
    total = ts + ps
    if total <= 0:
        return 1.0
    return float((ts * float(team_mult) + ps * float(player_mult)) / total)


def _bulk_load_ages(cur, player_ids: list[str]) -> Dict[str, int]:
    """Bulk-load ages from players table as a safety fallback.

    We *prefer* using Player.age once the adapter populates it, but this fallback
    keeps the fatigue system robust even if Player.age isn't wired yet.
    """
    ids = [str(pid) for pid in player_ids if str(pid)]
    seen: set[str] = set()
    uniq: list[str] = []
    for pid in ids:
        if pid in seen:
            continue
        seen.add(pid)
        uniq.append(pid)
    if not uniq:
        return {}

    placeholders = ",".join(["?"] * len(uniq))
    try:
        rows = cur.execute(
            f"SELECT player_id, age FROM players WHERE player_id IN ({placeholders});",
            uniq,
        ).fetchall()
    except Exception:
        return {}

    out: Dict[str, int] = {}
    for r in rows:
        pid = str(r[0])
        try:
            out[pid] = int(r[1] or 0)
        except Exception:
            out[pid] = 0
    return out


def season_year_from_season_id(season_id: str) -> int:
    """Extract season_year from a season_id like '2025-26'.

    We treat the first component as the season year integer.
    """
    s = str(season_id or "").strip()
    if not s:
        raise ValueError("season_id is empty")
    head = s.split("-", 1)[0]
    try:
        return int(head)
    except Exception as exc:
        raise ValueError(f"Invalid season_id (cannot parse year): {season_id!r}") from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def prepare_game_fatigue(
    repo: LeagueRepo,
    *,
    game_date_iso: str,
    season_year: int,
    home: TeamState,
    away: TeamState,
) -> PreparedGameFatigue:
    """Prepare and inject pre-game fatigue into TeamState lineups.

    Effects:
    - Reads persisted ST/LT/last_date for involved players from SQLite.
    - Applies rest + training + endurance + age recovery up to game_date.
    - Computes start_energy and energy_cap for each player.
    - Writes player.energy and player.energy_cap on Player objects (in memory).

    Returns:
        PreparedGameFatigue with per-player prepared values for later finalization.
    """
    gdi = game_time.require_date_iso(game_date_iso, field="game_date_iso")
    gdate = _dt.date.fromisoformat(gdi)

    home_tid = str(getattr(home, "team_id", "") or "").upper()
    away_tid = str(getattr(away, "team_id", "") or "").upper()
    if not home_tid or not away_tid:
        raise ValueError("prepare_game_fatigue: TeamState.team_id must be set for both teams")

    # Collect player IDs (lineup includes starters+bench selected by adapter).
    home_pids = [str(p.pid) for p in getattr(home, "lineup", []) if getattr(p, "pid", None)]
    away_pids = [str(p.pid) for p in getattr(away, "lineup", []) if getattr(p, "pid", None)]
    all_pids = list(dict.fromkeys(home_pids + away_pids))

    # Team roster PID lists (stable order) for practice-session participant autofill and AUTO hints.
    try:
        home_roster = repo.get_team_roster(home_tid) or []
    except Exception:
        home_roster = []
    try:
        away_roster = repo.get_team_roster(away_tid) or []
    except Exception:
        away_roster = []

    home_roster_pids: list[str] = []
    for r in home_roster:
        if not isinstance(r, Mapping):
            continue
        pid_raw = r.get("player_id")
        if not pid_raw:
            continue
        home_roster_pids.append(str(schema.normalize_player_id(pid_raw, strict=False)))
    home_roster_pids = list(dict.fromkeys(home_roster_pids))

    away_roster_pids: list[str] = []
    for r in away_roster:
        if not isinstance(r, Mapping):
            continue
        pid_raw = r.get("player_id")
        if not pid_raw:
            continue
        away_roster_pids.append(str(schema.normalize_player_id(pid_raw, strict=False)))
    away_roster_pids = list(dict.fromkeys(away_roster_pids))

    # Practice fallback schemes for AUTO sessions (best-effort).
    home_off_scheme = str(getattr(getattr(home, "tactics", None), "offense_scheme", "") or "")
    home_def_scheme = str(getattr(getattr(home, "tactics", None), "defense_scheme", "") or "")
    away_off_scheme = str(getattr(getattr(away, "tactics", None), "offense_scheme", "") or "")
    away_def_scheme = str(getattr(getattr(away, "tactics", None), "defense_scheme", "") or "")

    now_iso = game_time.utc_like_from_date_iso(gdi, field="game_date_iso")

    # Precompute effective team practice intensity per player (between last_date and game_date).
    team_practice_mult_by_pid: Dict[str, float] = {}

    with repo.transaction() as cur:
        state_by_pid = fat_repo.get_player_fatigue_states(cur, all_pids)
        age_by_pid = _bulk_load_ages(cur, all_pids)

        # Player plan cache for involved players (query-per-player, small N).
        player_int_cache: Dict[str, float] = {}
        for pid in all_pids:
            plan, _is_user = training_repo.get_player_training_plan(cur, player_id=pid, season_year=int(season_year))
            player_int_cache[pid] = _plan_intensity_mult(plan)

        # Practice session cache within this transaction.
        practice_session_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}

        def _get_practice_session(team_id: str, *, date_iso: str) -> Dict[str, Any]:
            day_iso = str(date_iso)[:10]
            key = (str(team_id).upper(), day_iso)
            cached = practice_session_cache.get(key)
            if cached is not None:
                return cached

            tid = str(team_id).upper()
            if tid == home_tid:
                roster_pids = home_roster_pids or home_pids
                fb_off, fb_def = home_off_scheme, home_def_scheme
            else:
                roster_pids = away_roster_pids or away_pids
                fb_off, fb_def = away_off_scheme, away_def_scheme

            d2g = None
            day_dt = _parse_date_iso(day_iso)
            if day_dt is not None:
                try:
                    d2g_val = int((gdate - day_dt).days)
                    if d2g_val < 0:
                        d2g_val = 0
                    d2g = int(d2g_val)
                except Exception:
                    d2g = None

            sess = resolve_practice_session(
                cur,
                team_id=tid,
                season_year=int(season_year),
                date_iso=day_iso,
                fallback_off_scheme=fb_off or None,
                fallback_def_scheme=fb_def or None,
                roster_pids=list(roster_pids),
                days_to_next_game=d2g,
                now_iso=now_iso,
            )
            practice_session_cache[key] = sess
            return sess

        def _effective_team_practice_mult(*, team_id: str, player_id: str, last_date: Optional[_dt.date]) -> float:
            if last_date is None:
                return 1.0
            dd = (gdate - last_date).days
            if dd <= 1:
                return 1.0

            # Safety/perf: ignore extremely old last_date to avoid generating
            # hundreds of practice rows on long breaks (offseason, etc.).
            # Only the most recent window matters for recovery feel.
            max_off_days = 21
            if dd > (max_off_days + 1):
                last_date = gdate - _dt.timedelta(days=int(max_off_days + 1))
                dd = int(max_off_days + 1)
            logs: list[float] = []
            for step in range(1, dd):
                day = last_date + _dt.timedelta(days=int(step))
                if day >= gdate:
                    break
                sess = _get_practice_session(str(team_id).upper(), date_iso=day.isoformat())
                mult = float(p_types.intensity_for_pid(sess, str(player_id)))
                logs.append(math.log(max(1e-6, mult)))
            if not logs:
                return 1.0
            return float(math.exp(sum(logs) / float(len(logs))))

        # Compute per-player team practice multiplier for this game.
        for pid in all_pids:
            row = state_by_pid.get(str(pid)) or {}
            last_date = _parse_date_iso(row.get("last_date"))
            if str(pid) in home_pids:
                team_id = home_tid
            else:
                team_id = away_tid
            team_practice_mult_by_pid[str(pid)] = _effective_team_practice_mult(team_id=team_id, player_id=str(pid), last_date=last_date)

    def _prepare_player(p: Player) -> PreparedPlayerFatigue:
        pid = str(getattr(p, "pid", "") or "")
        row = state_by_pid.get(pid) or {}
        st0 = float(row.get("st", 0.0) or 0.0)
        lt0 = float(row.get("lt", 0.0) or 0.0)
        last_date = _parse_date_iso(row.get("last_date"))

        # Determine age (prefer Player.age if present and >0).
        age = 0
        try:
            age_attr = getattr(p, "age", None)
            if age_attr is not None:
                age = int(age_attr or 0)
        except Exception:
            age = 0
        if age <= 0:
            age = int(age_by_pid.get(pid, 0) or 0)

        # Training intensity multiplier (blend team practice and player plan).
        team_mult = float(team_practice_mult_by_pid.get(pid, 1.0) or 1.0)
        player_mult = float(player_int_cache.get(pid, 1.0))
        intensity_mult = _blended_intensity_mult(team_mult=float(team_mult), player_mult=float(player_mult))

        ru = _rest_units(last_date=last_date, game_date=gdate)
        rmult = _combined_recovery_mult(p, age=age, intensity_mult=intensity_mult)
        st_rest, lt_rest = _apply_rest_recovery(st=st0, lt=lt0, rest_units=ru, recovery_mult=rmult)
        start_energy, energy_cap = _compute_start_energy_and_cap(st_rest=st_rest, lt_rest=lt_rest)

        # Inject into Player for match engine consumption.
        try:
            p.energy = float(start_energy)
        except Exception:
            # Player is a dataclass; this should not fail unless frozen.
            pass
        # energy_cap is a newly introduced field in later diffs; set via setattr for forward-compat.
        try:
            setattr(p, "energy_cap", float(energy_cap))
        except Exception:
            pass

        return PreparedPlayerFatigue(
            st_rest=float(st_rest),
            lt_rest=float(lt_rest),
            start_energy=float(start_energy),
            energy_cap=float(energy_cap),
            intensity_mult=float(intensity_mult),
        )

    home_by_pid: Dict[str, PreparedPlayerFatigue] = {}
    away_by_pid: Dict[str, PreparedPlayerFatigue] = {}

    # Apply per player (order deterministic).
    for p in getattr(home, "lineup", []) or []:
        pid = str(getattr(p, "pid", "") or "")
        if not pid:
            continue
        home_by_pid[pid] = _prepare_player(p)

    for p in getattr(away, "lineup", []) or []:
        pid = str(getattr(p, "pid", "") or "")
        if not pid:
            continue
        away_by_pid[pid] = _prepare_player(p)

    return PreparedGameFatigue(
        game_date_iso=gdi,
        season_year=int(season_year),
        home_team_id=home_tid,
        away_team_id=away_tid,
        home=PreparedTeamFatigue(by_pid=home_by_pid),
        away=PreparedTeamFatigue(by_pid=away_by_pid),
    )


def finalize_game_fatigue(
    repo: LeagueRepo,
    *,
    prepared: PreparedGameFatigue,
    home: TeamState,
    away: TeamState,
    raw_result: Mapping[str, Any],
) -> None:
    """Finalize and persist fatigue after the game.

    Reads match result (minutes played + end-of-game fatigue), applies game load,
    and upserts the resulting ST/LT state with last_date = game_date.

    Commercial safety:
    - If the result payload is missing required fields, we log a warning and no-op.
      (Better to keep sim running than to crash; missing updates can be diagnosed via logs.)
    """
    if not isinstance(raw_result, Mapping):
        logger.warning("finalize_game_fatigue: raw_result is not a mapping; skipping")
        return

    gs = raw_result.get("game_state")
    if not isinstance(gs, Mapping):
        logger.warning("finalize_game_fatigue: raw_result.game_state missing; skipping")
        return

    fatigue_by_team = gs.get("fatigue")
    minutes_by_team = gs.get("minutes_played_sec")
    if not isinstance(fatigue_by_team, Mapping) or not isinstance(minutes_by_team, Mapping):
        logger.warning("finalize_game_fatigue: fatigue/minutes missing; skipping")
        return

    # Pace / tempo proxy from engine output (possessions per team).
    try:
        tempo = float(raw_result.get("possessions_per_team") or fat_cfg.TEMPO_REF)
    except Exception:
        tempo = float(fat_cfg.TEMPO_REF)
    tempo_factor = _clamp((tempo / float(fat_cfg.TEMPO_REF)) ** float(fat_cfg.TEMPO_EXP), float(fat_cfg.TEMPO_LO), float(fat_cfg.TEMPO_HI))

    # Bulk ages fallback (same as in prepare; ensures LT age gain works even if Player.age isn't wired).
    all_pids = [str(p.pid) for p in (list(getattr(home, "lineup", []) or []) + list(getattr(away, "lineup", []) or [])) if getattr(p, "pid", None)]
    with repo.transaction() as cur:
        age_by_pid = _bulk_load_ages(cur, all_pids)


    def _age_for_player(p: Player) -> int:
        pid = str(getattr(p, "pid", "") or "")
        try:
            a = int(getattr(p, "age", 0) or 0)
        except Exception:
            a = 0
        if a <= 0:
            a = int(age_by_pid.get(pid, 0) or 0)
        return int(max(0, a))

    def _stamina_mult(p: Player) -> float:
        try:
            cap = float(getattr(p, "derived", {}).get("FAT_CAPACITY", 50.0))
        except Exception:
            cap = 50.0
        c01 = _clamp01(cap / 100.0)
        return _lerp(float(fat_cfg.STAM_LO), float(fat_cfg.STAM_HI), c01)

    def _m_factor(minutes: float) -> float:
        m = max(0.0, float(minutes))
        if m <= 0.0:
            return 0.0
        return float((m / float(fat_cfg.MIN_REF)) ** float(fat_cfg.MIN_EXP))

    def _lt_age_mult(age: int) -> float:
        x = (float(age) - float(fat_cfg.AGE_LT_START)) / float(fat_cfg.AGE_LT_K)
        return 1.0 + float(fat_cfg.AGE_LT_BOOST) * _sigmoid(x)

    def _finalize_team(team: TeamState, team_prepared: PreparedTeamFatigue, team_id: str) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        role_by_pid = _get_offense_role_by_pid(team)

        fat_map = fatigue_by_team.get(team_id) or {}
        mins_map = minutes_by_team.get(team_id) or {}
        if not isinstance(fat_map, Mapping) or not isinstance(mins_map, Mapping):
            # Engine should always provide these; if missing, skip gracefully.
            logger.warning("finalize_game_fatigue: missing per-team maps for %s", team_id)
            return out

        for p in getattr(team, "lineup", []) or []:
            pid = str(getattr(p, "pid", "") or "")
            if not pid:
                continue
            prep = team_prepared.by_pid.get(pid)
            if prep is None:
                # Should not happen if prepare() ran, but keep safe.
                continue

            # Minutes and end-of-game energy.
            try:
                minutes = float(mins_map.get(pid, 0.0) or 0.0) / 60.0
            except Exception:
                minutes = 0.0
            try:
                end_energy = _clamp01(float(fat_map.get(pid, prep.start_energy) or prep.start_energy))
            except Exception:
                end_energy = _clamp01(prep.start_energy)

            # Archetype-based role multiplier (consistent with matchengine fatigue/rotation mapping).
            try:
                archetype = str(_fatigue_archetype_for_pid(team, pid, role_by_pid) or "wing")
            except Exception:
                archetype = "wing"
            role_mult = float(fat_cfg.ROLE_MULT.get(archetype, 1.0))

            mfac = _m_factor(minutes)
            st_gain = float(fat_cfg.BASE_ST_GAIN) * mfac * role_mult * tempo_factor * _stamina_mult(p)

            # End-of-game exhaustion penalty should only apply if the player actually played.
            if minutes > 0.0:
                st_gain += float(fat_cfg.END_PEN_W) * (1.0 - float(end_energy))

            st_new = _clamp01(float(prep.st_rest) + float(st_gain))

            age = _age_for_player(p)
            lt_train_mult = float(prep.intensity_mult) ** float(fat_cfg.LT_TRAIN_POW) if prep.intensity_mult > 0 else 1.0
            lt_gain = float(st_gain) * float(fat_cfg.LT_GAIN_RATIO) * float(lt_train_mult) * float(_lt_age_mult(age))
            lt_new = _clamp01(float(prep.lt_rest) + float(lt_gain))

            out[pid] = {"st": float(st_new), "lt": float(lt_new), "last_date": prepared.game_date_iso}
        return out

    home_updates = _finalize_team(home, prepared.home, prepared.home_team_id)
    away_updates = _finalize_team(away, prepared.away, prepared.away_team_id)

    # Persist
    updates: Dict[str, Dict[str, Any]] = {}
    updates.update(home_updates)
    updates.update(away_updates)
    if not updates:
        return

    now_iso = game_time.utc_like_from_date_iso(prepared.game_date_iso, field="game_date_iso")
    with repo.transaction() as cur:
        fat_repo.upsert_player_fatigue_states(cur, updates, now=str(now_iso))
