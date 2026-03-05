from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)
_WARN_COUNTS: Dict[str, int] = {}


def _warn_limited(code: str, msg: str, *, limit: int = 5) -> None:
    """Log warning with traceback, but cap repeats per code."""
    n = _WARN_COUNTS.get(code, 0)
    if n < limit:
        logger.warning("%s %s", code, msg, exc_info=True)
    _WARN_COUNTS[code] = n + 1


from derived_formulas import compute_derived
from state import (
    get_db_path,
    get_league_context_snapshot,
    get_league_schedule_snapshot,
    get_player_stats_snapshot,
    ui_cache_set,
    ui_players_get,
    ui_players_set,
    ui_teams_get,
    ui_teams_set,
)

# Division/Conference mapping can stay in config (static).
# We intentionally do NOT import ROSTER_DF anymore.
from config import ALL_TEAM_IDS, TEAM_TO_CONF_DIV

_LEAGUE_REPO_IMPORT_ERROR: Optional[Exception] = None
try:
    from league_repo import LeagueRepo  # type: ignore
except ImportError as e:  # pragma: no cover
    LeagueRepo = None  # type: ignore
    _LEAGUE_REPO_IMPORT_ERROR = e


@contextmanager
def _repo_ctx() -> "LeagueRepo":
    """Open a SQLite LeagueRepo for the duration of the operation."""
    if LeagueRepo is None:
        raise ImportError(f"league_repo.py is required: {_LEAGUE_REPO_IMPORT_ERROR}")

    db_path = get_db_path()
    with LeagueRepo(db_path) as repo:
        # DB schema is guaranteed during server startup (state.startup_init_state()). repo
        yield repo

def _list_active_team_ids() -> List[str]:
    """Return active team ids from DB if possible.

    Notes:
    - If league.db_path is not configured, get_db_path() raises ValueError and this function will propagate.
    - If DB access fails for other reasons (e.g. sqlite error), this falls back to ALL_TEAM_IDS.
    """
    try:
        with _repo_ctx() as repo:
            teams = [str(t).upper() for t in repo.list_teams() if str(t).upper() != "FA"]
            if teams:
                return teams
    except (ImportError, sqlite3.Error, OSError, TypeError) as exc:
        _warn_limited(
            "LIST_TEAMS_FAILED_FALLBACK_ALL",
            f"exc_type={type(exc).__name__}",
            limit=3,
        )
        pass
    return list(ALL_TEAM_IDS)


def _has_free_agents_team() -> bool:
    try:
        with _repo_ctx() as repo:
            return "FA" in {str(t).upper() for t in repo.list_teams()}
    except (ImportError, sqlite3.Error, OSError, TypeError) as exc:
        _warn_limited(
            "HAS_FA_TEAM_CHECK_FAILED",
            f"exc_type={type(exc).__name__}",
            limit=3,
        )
        return False


def _parse_potential(pot_raw: Any) -> float:
    pot_map = {
        "A+": 1.0, "A": 0.95, "A-": 0.9,
        "B+": 0.85, "B": 0.8, "B-": 0.75,
        "C+": 0.7, "C": 0.65, "C-": 0.6,
        "D+": 0.55, "D": 0.5, "F": 0.4,
    }
    if isinstance(pot_raw, str):
        return float(pot_map.get(pot_raw.strip(), 0.6))
    try:
        return float(pot_raw)
    except (TypeError, ValueError):
        return 0.6


def build_ui_players(repo: "LeagueRepo") -> Dict[str, Dict[str, Any]]:
    """Pure builder: create UI players dict from DB SSOT via repo.

    - No reads/writes to global state.
    - Shape should remain stable to avoid UI regressions.
    """
    players: Dict[str, Dict[str, Any]] = {}

    # Determine which teams to build rosters for.
    # Prefer DB-driven teams; fall back to static config on failure.
    try:
        team_ids = [str(t).upper() for t in repo.list_teams() if str(t).upper() != "FA"]
        if not team_ids:
            team_ids = list(ALL_TEAM_IDS)
        has_fa = "FA" in {str(t).upper() for t in repo.list_teams()}
    except Exception:
        _warn_limited("UI_PLAYERS_LIST_TEAMS_FAILED", "falling back to ALL_TEAM_IDS", limit=3)
        team_ids = list(ALL_TEAM_IDS)
        has_fa = False

    roster_team_ids = list(team_ids)
    if has_fa:
        roster_team_ids.append("FA")

    for tid in roster_team_ids:
        try:
            roster_rows = repo.get_team_roster(tid)
        except (sqlite3.Error, TypeError, ValueError, KeyError):
            _warn_limited("DB_GET_TEAM_ROSTER_FAILED", f"team_id={tid!r}", limit=3)
            continue

        for row in roster_rows:
            pid = str(row.get("player_id"))
            attrs = row.get("attrs") or {}

            # Keep shape consistent with legacy UI cache entries.
            # (signed/acquired fields are UI-only now; trade rules must not depend on them.)
            entry: Dict[str, Any] = {
                "player_id": pid,
                "name": row.get("name") or attrs.get("Name") or "",
                "team_id": str(tid).upper(),
                "pos": row.get("pos") or attrs.get("POS") or attrs.get("Position") or "",
                "age": int(row.get("age") or 0),
                "overall": float(row.get("ovr") or 0.0),
                "salary": float(row.get("salary_amount") or 0.0),
                "potential": _parse_potential(attrs.get("Potential")),
                "signed_date": "1900-01-01",
                "signed_via_free_agency": False,
                "acquired_date": "1900-01-01",
                "acquired_via_trade": False,
            }

            try:
                entry["derived"] = compute_derived(attrs)
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                _warn_limited("DERIVED_COMPUTE_FAILED", f"player_id={pid!r}", limit=3)
                entry["derived"] = {}

            players[pid] = entry

    return players


def build_ui_teams(team_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """Pure builder: create UI teams dict from static config.

    - No reads/writes to global state.
    """
    teams_meta: Dict[str, Dict[str, Any]] = {}
    for tid in team_ids:
        tid_u = str(tid).upper()
        if tid_u == "FA":
            continue
        info = TEAM_TO_CONF_DIV.get(tid_u, {})
        teams_meta[tid_u] = {
            "team_id": tid_u,
            "conference": info.get("conference"),
            "division": info.get("division"),
            "tendency": "neutral",
            "window": "now",
            "market": "mid",
            "patience": 0.5,
        }
    return teams_meta


def ui_cache_rebuild_all() -> None:
    """State writer: rebuild the entire UI cache from DB/config."""
    with _repo_ctx() as repo:
        # Team ids for teams meta should exclude FA.
        try:
            team_ids = [str(t).upper() for t in repo.list_teams() if str(t).upper() != "FA"]
            if not team_ids:
                team_ids = list(ALL_TEAM_IDS)
        except Exception:
            _warn_limited("UI_TEAMS_LIST_TEAMS_FAILED", "falling back to ALL_TEAM_IDS", limit=3)
            team_ids = list(ALL_TEAM_IDS)

        players = build_ui_players(repo)
        teams = build_ui_teams(team_ids)

    # Commit as one unit to avoid transient mismatch between players/teams.
    ui_cache_set({"players": players, "teams": teams})


def ui_cache_refresh_players(player_ids: Iterable[str]) -> None:
    """State writer: refresh UI cache entries for the given player_ids only.

    - Reads latest data from DB SSOT (players + roster salary/team).
    - Deletes entries for players not found on an active roster.
    """
    # Normalize & de-dup deterministically.
    normalized: List[str] = []
    seen: set[str] = set()
    for pid in player_ids:
        spid = str(pid)
        if not spid or spid in seen:
            continue
        seen.add(spid)
        normalized.append(spid)

    if not normalized:
        return

    current = ui_players_get()
    if not isinstance(current, dict):
        current = {}
    updated_players: Dict[str, Dict[str, Any]] = dict(current)

    with _repo_ctx() as repo:
        try:
            team_by_pid = repo.get_team_ids_by_players(normalized)
        except Exception:
            _warn_limited("UI_REFRESH_TEAM_LOOKUP_FAILED", "skipping refresh", limit=3)
            return

        for pid in normalized:
            tid = team_by_pid.get(pid)
            if not tid:
                # No active roster entry -> remove from UI cache.
                updated_players.pop(pid, None)
                continue

            try:
                row = repo.get_player(pid)
            except (KeyError, sqlite3.Error, TypeError, ValueError):
                _warn_limited("DB_GET_PLAYER_FAILED", f"player_id={pid!r}", limit=3)
                continue

            attrs = row.get("attrs") or {}
            try:
                salary_amt = repo.get_salary_amount(pid)
            except Exception:
                salary_amt = None

            entry: Dict[str, Any] = {
                "player_id": str(row.get("player_id") or pid),
                "name": row.get("name") or attrs.get("Name") or "",
                "team_id": str(tid).upper(),
                "pos": row.get("pos") or attrs.get("POS") or attrs.get("Position") or "",
                "age": int(row.get("age") or 0),
                "overall": float(row.get("ovr") or 0.0),
                "salary": float(salary_amt or 0.0),
                "potential": _parse_potential(attrs.get("Potential")),
                "signed_date": "1900-01-01",
                "signed_via_free_agency": False,
                "acquired_date": "1900-01-01",
                "acquired_via_trade": False,
            }

            try:
                entry["derived"] = compute_derived(attrs)
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                _warn_limited("DERIVED_COMPUTE_FAILED", f"player_id={pid!r}", limit=3)
                entry["derived"] = {}

            updated_players[pid] = entry

    ui_players_set(updated_players)


def _compute_team_payroll(team_id: str) -> float:
    """Compute payroll from DB roster (NOT from Excel)."""
    total = 0.0
    with _repo_ctx() as repo:
        roster = repo.get_team_roster(team_id)
        for r in roster:
            try:
                total += float(r.get("salary_amount") or 0.0)
            except (TypeError, ValueError):
                _warn_limited(
                    "PAYROLL_SALARY_COERCE_FAILED",
                    f"team_id={team_id!r} raw={r.get('salary_amount')!r}",
                    limit=3,
                )
                continue
    return float(total)


def _compute_cap_space(team_id: str) -> float:
    payroll = _compute_team_payroll(team_id)
    # Assumes cap model (salary_cap/aprons) is already populated during server startup/hydration.
    league_context = get_league_context_snapshot()
    trade_rules = league_context.get("trade_rules", {})
    try:
        salary_cap = float(trade_rules.get("salary_cap") or 0.0)
    except (TypeError, ValueError):
        _warn_limited("SALARY_CAP_COERCE_FAILED", f"raw={trade_rules.get('salary_cap')!r}", limit=3)
        salary_cap = 0.0
    return salary_cap - payroll


def _get_master_schedule_games() -> List[Dict[str, Any]]:
    """Read master schedule games via lightweight state accessor."""
    snap = get_league_schedule_snapshot() or {}
    ms = snap.get("master_schedule") if isinstance(snap, dict) else {}
    ms = ms if isinstance(ms, dict) else {}
    games = ms.get("games") or []
    return games if isinstance(games, list) else []


def _compute_team_records() -> Dict[str, Dict[str, Any]]:
    """Compute W/L and points from master_schedule."""
    games = _get_master_schedule_games()

    if not games:
        raise RuntimeError(
            "Master schedule is not initialized. Expected state.startup_init_state() to run before calling team_utils._compute_team_records()."
        )
    
    team_ids = _list_active_team_ids()
    records: Dict[str, Dict[str, Any]] = {
        tid: {"wins": 0, "losses": 0, "pf": 0, "pa": 0}
        for tid in team_ids
    }

    for g in games:
        if g.get("status") != "final":
            continue
        home_id = str(g.get("home_team_id") or "")
        away_id = str(g.get("away_team_id") or "")
        home_score = g.get("home_score")
        away_score = g.get("away_score")
        if home_id not in records or away_id not in records:
            continue
        if home_score is None or away_score is None:
            continue

        records[home_id]["pf"] += int(home_score)
        records[home_id]["pa"] += int(away_score)
        records[away_id]["pf"] += int(away_score)
        records[away_id]["pa"] += int(home_score)

        if home_score > away_score:
            records[home_id]["wins"] += 1
            records[away_id]["losses"] += 1
        elif away_score > home_score:
            records[away_id]["wins"] += 1
            records[home_id]["losses"] += 1

    return records


def get_conference_standings() -> Dict[str, List[Dict[str, Any]]]:
    """Return standings grouped by conference."""
    records = _compute_team_records()

    standings = {"east": [], "west": []}

    for tid, rec in records.items():
        info = TEAM_TO_CONF_DIV.get(tid, {})
        conf = info.get("conference")
        if not conf:
            continue

        wins = rec.get("wins", 0)
        losses = rec.get("losses", 0)
        games_played = wins + losses
        win_pct = wins / games_played if games_played else 0.0
        pf = rec.get("pf", 0)
        pa = rec.get("pa", 0)
        point_diff = pf - pa

        entry = {
            "team_id": tid,
            "conference": conf,
            "division": info.get("division"),
            "wins": wins,
            "losses": losses,
            "win_pct": win_pct,
            "games_played": games_played,
            "point_diff": point_diff,
        }

        if str(conf).lower() == "east":
            standings["east"].append(entry)
        else:
            standings["west"].append(entry)

    def sort_and_gb(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows_sorted = sorted(
            rows,
            key=lambda r: (r.get("win_pct", 0), r.get("point_diff", 0)),
            reverse=True,
        )
        if not rows_sorted:
            return rows_sorted

        leader = rows_sorted[0]
        leader_w, leader_l = leader.get("wins", 0), leader.get("losses", 0)
        for r in rows_sorted:
            gb = ((leader_w - r.get("wins", 0)) + (r.get("losses", 0) - leader_l)) / 2
            r["gb"] = gb
        for idx, r in enumerate(rows_sorted, start=1):
            r["rank"] = idx
        return rows_sorted

    standings["east"] = sort_and_gb(standings["east"])
    standings["west"] = sort_and_gb(standings["west"])

    return standings


def get_conference_standings_table() -> Dict[str, List[Dict[str, Any]]]:
    """Return standings rows tailored for the standings table UI.

    Notes:
    - Uses only regular-season final games from master_schedule.
    - Keeps numeric fields for machine use while also providing display-ready strings
      for the requested table format (PCT '.763', leader GB '-', L10 'W-L').
    """
    games = _get_master_schedule_games()

    if not games:
        raise RuntimeError(
            "Master schedule is not initialized. Expected state.startup_init_state() to run before calling team_utils.get_conference_standings_table()."
        )

    team_ids = _list_active_team_ids()
    records: Dict[str, Dict[str, Any]] = {
        tid: {
            "wins": 0,
            "losses": 0,
            "pf": 0,
            "pa": 0,
            "home_wins": 0,
            "home_losses": 0,
            "away_wins": 0,
            "away_losses": 0,
            "div_wins": 0,
            "div_losses": 0,
            "conf_wins": 0,
            "conf_losses": 0,
        }
        for tid in team_ids
    }
    team_results: Dict[str, List[tuple[str, int]]] = defaultdict(list)

    def _is_regular_final(g: Dict[str, Any]) -> bool:
        if g.get("status") != "final":
            return False
        return str(g.get("phase") or "regular") == "regular"

    for g in games:
        if not _is_regular_final(g):
            continue

        home_id = str(g.get("home_team_id") or "")
        away_id = str(g.get("away_team_id") or "")
        home_score = g.get("home_score")
        away_score = g.get("away_score")
        if home_id not in records or away_id not in records:
            continue
        if home_score is None or away_score is None:
            continue

        hs = int(home_score)
        a_s = int(away_score)
        game_date = str(g.get("date") or "")[:10]

        records[home_id]["pf"] += hs
        records[home_id]["pa"] += a_s
        records[away_id]["pf"] += a_s
        records[away_id]["pa"] += hs

        home_info = TEAM_TO_CONF_DIV.get(home_id, {}) or {}
        away_info = TEAM_TO_CONF_DIV.get(away_id, {}) or {}
        same_conf = home_info.get("conference") and (home_info.get("conference") == away_info.get("conference"))
        same_div = same_conf and home_info.get("division") and (home_info.get("division") == away_info.get("division"))

        if hs > a_s:
            records[home_id]["wins"] += 1
            records[away_id]["losses"] += 1
            records[home_id]["home_wins"] += 1
            records[away_id]["away_losses"] += 1
            team_results[home_id].append((game_date, 1))
            team_results[away_id].append((game_date, 0))
            if same_conf:
                records[home_id]["conf_wins"] += 1
                records[away_id]["conf_losses"] += 1
            if same_div:
                records[home_id]["div_wins"] += 1
                records[away_id]["div_losses"] += 1
        elif a_s > hs:
            records[away_id]["wins"] += 1
            records[home_id]["losses"] += 1
            records[away_id]["away_wins"] += 1
            records[home_id]["home_losses"] += 1
            team_results[away_id].append((game_date, 1))
            team_results[home_id].append((game_date, 0))
            if same_conf:
                records[away_id]["conf_wins"] += 1
                records[home_id]["conf_losses"] += 1
            if same_div:
                records[away_id]["div_wins"] += 1
                records[home_id]["div_losses"] += 1

    standings = {"east": [], "west": []}

    for tid, rec in records.items():
        info = TEAM_TO_CONF_DIV.get(tid, {})
        conf = info.get("conference")
        if not conf:
            continue

        wins = int(rec.get("wins", 0) or 0)
        losses = int(rec.get("losses", 0) or 0)
        games_played = wins + losses
        win_pct = wins / games_played if games_played else 0.0

        pf = int(rec.get("pf", 0) or 0)
        pa = int(rec.get("pa", 0) or 0)
        ppg = (pf / games_played) if games_played else 0.0
        opp_ppg = (pa / games_played) if games_played else 0.0
        diff = ppg - opp_ppg

        results_sorted = sorted(team_results.get(tid, []), key=lambda x: x[0])
        recent = results_sorted[-10:]
        last10_w = sum(r for _, r in recent)
        last10_l = len(recent) - last10_w
        l10 = f"{last10_w}-{last10_l}"

        streak = "-"
        if results_sorted:
            last_result = results_sorted[-1][1]
            streak_len = 0
            for _, r in reversed(results_sorted):
                if r != last_result:
                    break
                streak_len += 1
            streak = f"{'W' if last_result == 1 else 'L'}{streak_len}"

        entry = {
            "team_id": tid,
            "conference": conf,
            "division": info.get("division"),
            "wins": wins,
            "losses": losses,
            "win_pct": win_pct,
            "pct": f"{win_pct:.3f}"[1:],
            "games_played": games_played,
            "gb": 0.0,
            "gb_display": "-",
            "home": f"{int(rec.get('home_wins', 0) or 0)}-{int(rec.get('home_losses', 0) or 0)}",
            "away": f"{int(rec.get('away_wins', 0) or 0)}-{int(rec.get('away_losses', 0) or 0)}",
            "div": f"{int(rec.get('div_wins', 0) or 0)}-{int(rec.get('div_losses', 0) or 0)}",
            "conf": f"{int(rec.get('conf_wins', 0) or 0)}-{int(rec.get('conf_losses', 0) or 0)}",
            "ppg": round(ppg, 1),
            "opp_ppg": round(opp_ppg, 1),
            "diff": round(diff, 1),
            "strk": streak,
            "l10": l10,
            "point_diff": pf - pa,
        }

        if str(conf).lower() == "east":
            standings["east"].append(entry)
        else:
            standings["west"].append(entry)

    def _format_gb(gb: float) -> str:
        if abs(float(gb)) < 1e-9:
            return "-"
        rounded = round(float(gb), 1)
        if abs(rounded - int(rounded)) < 1e-9:
            return str(int(rounded))
        return f"{rounded:.1f}"

    def sort_and_finalize(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows_sorted = sorted(
            rows,
            key=lambda r: (r.get("win_pct", 0), r.get("point_diff", 0)),
            reverse=True,
        )
        if not rows_sorted:
            return rows_sorted

        leader = rows_sorted[0]
        leader_w, leader_l = int(leader.get("wins", 0) or 0), int(leader.get("losses", 0) or 0)
        for idx, r in enumerate(rows_sorted, start=1):
            gb = ((leader_w - int(r.get("wins", 0) or 0)) + (int(r.get("losses", 0) or 0) - leader_l)) / 2
            r["gb"] = gb
            r["gb_display"] = _format_gb(gb)
            r["rank"] = idx
        return rows_sorted

    standings["east"] = sort_and_finalize(standings["east"])
    standings["west"] = sort_and_finalize(standings["west"])

    return standings


def get_team_cards() -> List[Dict[str, Any]]:
    """Return team summary cards."""
    records = _compute_team_records()
    team_ids = _list_active_team_ids()

    team_cards: List[Dict[str, Any]] = []
    for tid in team_ids:
        meta = ui_teams_get().get(tid, {})
        # Default meta fallback (cache may be empty): use static conf/div mapping.
        static_info = TEAM_TO_CONF_DIV.get(tid, {}) or {}
        conf = meta.get("conference") or static_info.get("conference")
        div = meta.get("division") or static_info.get("division")
        rec = records.get(tid, {})
        wins = rec.get("wins", 0)
        losses = rec.get("losses", 0)
        gp = wins + losses
        win_pct = wins / gp if gp else 0.0
        card = {
            "team_id": tid,
            "conference": conf,
            "division": div,
            "wins": wins,
            "losses": losses,
            "win_pct": win_pct,
            "tendency": meta.get("tendency"),
            "payroll": _compute_team_payroll(tid),
            "cap_space": _compute_cap_space(tid),
        }
        team_cards.append(card)

    return team_cards


def get_team_detail(team_id: str) -> Dict[str, Any]:
    """Return team detail (summary + roster) using DB roster."""
    tid = str(team_id).upper()

    team_ids = set(_list_active_team_ids())
    if tid not in team_ids:
        raise ValueError(f"Team '{tid}' not found")

    records = _compute_team_records()
    standings = get_conference_standings()
    rank_map = {r["team_id"]: r for r in standings.get("east", []) + standings.get("west", [])}

    meta = ui_teams_get().get(tid, {})
    # Default meta fallback (cache may be empty): use static conf/div mapping.
    static_info = TEAM_TO_CONF_DIV.get(tid, {}) or {}
    conf = meta.get("conference") or static_info.get("conference")
    div = meta.get("division") or static_info.get("division")
    rec = records.get(tid, {})
    rank_entry = rank_map.get(tid, {})
    wins = rec.get("wins", 0)
    losses = rec.get("losses", 0)
    gp = wins + losses
    win_pct = wins / gp if gp else 0.0
    pf = rec.get("pf", 0)
    pa = rec.get("pa", 0)
    point_diff = pf - pa

    summary = {
        "team_id": tid,
        "conference": conf,
        "division": div,
        "wins": wins,
        "losses": losses,
        "win_pct": win_pct,
        "point_diff": point_diff,
        "rank": rank_entry.get("rank"),
        "gb": rank_entry.get("gb"),
        "tendency": meta.get("tendency"),
        "payroll": _compute_team_payroll(tid),
        "cap_space": _compute_cap_space(tid),
    }

    season_stats = get_player_stats_snapshot(phase="regular") or {}
    league_ctx = get_league_context_snapshot() or {}
    season_year = int(league_ctx.get("season_year") or 0)

    roster: List[Dict[str, Any]] = []
    with _repo_ctx() as repo:
        roster_rows = repo.get_team_roster(tid)
        roster_pids = [str(r.get("player_id")) for r in (roster_rows or []) if r.get("player_id")]

        fatigue_by_pid: Dict[str, Dict[str, Any]] = {}
        sharpness_by_pid: Dict[str, Dict[str, Any]] = {}
        if roster_pids:
            try:
                from fatigue import repo as f_repo
                from readiness import repo as r_repo

                with repo.transaction() as cur:
                    fatigue_by_pid = f_repo.get_player_fatigue_states(cur, roster_pids)
                    if season_year > 0:
                        sharpness_by_pid = r_repo.get_player_sharpness_states(
                            cur,
                            roster_pids,
                            season_year=int(season_year),
                        )
            except Exception as exc:
                _warn_limited(
                    "TEAM_DETAIL_CONDITION_LOAD_FAILED",
                    f"team_id={tid!r} exc_type={type(exc).__name__}",
                    limit=3,
                )

        for row in roster_rows:
            pid = str(row.get("player_id"))
            p_stats = season_stats.get(pid, {}) or {}
            games = int(p_stats.get("games", 0) or 0)
            totals = p_stats.get("totals", {}) or {}

            fatigue_row = fatigue_by_pid.get(pid) or {}
            st_fatigue = float(fatigue_row.get("st", 0.0) or 0.0)
            lt_fatigue = float(fatigue_row.get("lt", 0.0) or 0.0)

            sharp_row = sharpness_by_pid.get(pid) or {}
            sharpness = float(sharp_row.get("sharpness", 50.0) or 50.0)

            def per_game_val(key: str) -> float:
                try:
                    return float(totals.get(key, 0.0)) / games if games else 0.0
                except (TypeError, ValueError, ZeroDivisionError):
                    return 0.0

            roster.append(
                {
                    "player_id": pid,
                    "name": row.get("name"),
                    "pos": row.get("pos"),
                    "ovr": float(row.get("ovr") or 0.0),
                    "age": int(row.get("age") or 0),
                    "height_in": int(row.get("height_in") or 0),
                    "weight_lb": int(row.get("weight_lb") or 0),
                    "salary": float(row.get("salary_amount") or 0.0),
                    "short_term_fatigue": st_fatigue,
                    "long_term_fatigue": lt_fatigue,
                    "short_term_stamina": max(0.0, 1.0 - st_fatigue),
                    "long_term_stamina": max(0.0, 1.0 - lt_fatigue),
                    "sharpness": sharpness,
                    "pts": per_game_val("PTS"),
                    "ast": per_game_val("AST"),
                    "reb": per_game_val("REB"),
                    "three_pm": per_game_val("3PM"),
                }
            )

    roster_sorted = sorted(roster, key=lambda r: r.get("ovr", 0), reverse=True)

    return {
        "summary": summary,
        "roster": roster_sorted,
    }
















