from __future__ import annotations

import json
import os
import sqlite3
import hashlib
import math
from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from config import BASE_DIR, ALL_TEAM_IDS
from league_repo import LeagueRepo
from schema import normalize_player_id, normalize_team_id
import state
from analytics.stats.leaders import compute_leaderboards
from team_utils import (
    get_conference_standings,
    get_conference_standings_home_light,
    get_conference_standings_table,
    get_team_cards,
    get_team_detail,
    get_team_summary_light,
)

router = APIRouter()

static_dir = os.path.join(BASE_DIR, "static")

TEAM_FULL_NAMES: Dict[str, str] = {
    "ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets", "CHA": "Charlotte Hornets",
    "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers", "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets",
    "DET": "Detroit Pistons", "GSW": "Golden State Warriors", "HOU": "Houston Rockets", "IND": "Indiana Pacers",
    "LAC": "LA Clippers", "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies", "MIA": "Miami Heat",
    "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves", "NOP": "New Orleans Pelicans", "NYK": "New York Knicks",
    "OKC": "Oklahoma City Thunder", "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers", "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings", "SAS": "San Antonio Spurs", "TOR": "Toronto Raptors",
    "UTA": "Utah Jazz", "WAS": "Washington Wizards",
}


def _format_mmdd(date_value: Any) -> str:
    raw = str(date_value or "")[:10]
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        return f"{raw[5:7]}/{raw[8:10]}"
    return "--/--"


def _deterministic_tipoff_time(game_id: Any) -> str:
    slots = ("07:00 PM", "07:30 PM", "08:00 PM", "08:30 PM", "09:00 PM", "09:30 PM")
    digest = hashlib.md5(str(game_id or "").encode("utf-8")).hexdigest()
    idx = int(digest[:8], 16) % len(slots)
    return slots[idx]


def _num_or_none(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick_leader(rows: List[Dict[str, Any]], stat_keys: List[str]) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None
    best_value = float("-inf")
    for row in rows:
        value = None
        for k in stat_keys:
            value = _num_or_none(row.get(k))
            if value is not None:
                break
        if value is None:
            continue
        if value > best_value:
            best_value = value
            best = {
                "player_id": str(row.get("PlayerID") or ""),
                "name": row.get("Name") or "",
                "value": int(value),
            }
    return best


def _to_int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clock_mmss_to_sec(value: Any) -> Optional[int]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if ":" not in raw:
        return _to_int_or_none(raw)
    mmss = raw.split(":", 1)
    if len(mmss) != 2:
        return None
    mm = _to_int_or_none(mmss[0])
    ss = _to_int_or_none(mmss[1])
    if mm is None or ss is None:
        return None
    if mm < 0 or ss < 0:
        return None
    return int(mm * 60 + ss)


def _build_game_flow_series(
    replay_events: List[Dict[str, Any]],
    *,
    home_final: int,
    away_final: int,
    overtime_periods: int,
) -> List[Dict[str, int]]:
    if not replay_events:
        return []

    rows: List[tuple[int, int, int, int]] = []
    for ev in replay_events:
        if not isinstance(ev, dict):
            continue
        seq = _to_int_or_none(ev.get("seq")) or 0
        t = _to_int_or_none(ev.get("game_elapsed_sec"))
        if t is None:
            q = _to_int_or_none(ev.get("quarter"))
            clock_left = _clock_mmss_to_sec(ev.get("clock_sec"))
            if q is not None and clock_left is not None:
                if q <= 4:
                    t = max(0, (q - 1) * 720 + (720 - clock_left))
                else:
                    t = max(0, 2880 + (q - 5) * 300 + (300 - clock_left))
        hs = _to_int_or_none(ev.get("score_home"))
        as_ = _to_int_or_none(ev.get("score_away"))
        if t is None or hs is None or as_ is None:
            continue
        rows.append((t, seq, hs, as_))

    if not rows:
        return []

    rows.sort(key=lambda x: (x[0], x[1]))

    collapsed: Dict[int, tuple[int, int]] = {}
    for t, _seq, hs, as_ in rows:
        collapsed[t] = (hs, as_)

    out: List[Dict[str, int]] = []
    if 0 not in collapsed:
        out.append({"t": 0, "home_score": 0, "away_score": 0})

    for t in sorted(collapsed.keys()):
        hs, as_ = collapsed[t]
        out.append({"t": int(t), "home_score": int(hs), "away_score": int(as_)})

    total_sec = int(2880 + max(0, int(overtime_periods or 0)) * 300)
    if not out or out[-1]["t"] < total_sec or out[-1]["home_score"] != home_final or out[-1]["away_score"] != away_final:
        out.append(
            {
                "t": total_sec,
                "home_score": int(home_final),
                "away_score": int(away_final),
            }
        )
    return out


def _build_win_probability_series(
    game_flow_series: List[Dict[str, int]],
    *,
    overtime_periods: int,
    pre_game_strength_gap: float,
) -> List[Dict[str, float]]:
    if not game_flow_series:
        return []

    total_sec = float(2880 + max(0, int(overtime_periods or 0)) * 300)
    if total_sec <= 0:
        total_sec = 2880.0

    out: List[Dict[str, float]] = []
    for p in game_flow_series:
        t = float(p.get("t") or 0)
        home_score = int(p.get("home_score") or 0)
        away_score = int(p.get("away_score") or 0)
        score_diff = float(home_score - away_score)
        progress = max(0.0, min(1.0, t / total_sec))
        z = (0.055 * score_diff) + (1.6 * (1.0 - progress) * float(pre_game_strength_gap))
        home_prob = 1.0 / (1.0 + math.exp(-z))
        home_prob = max(0.0, min(1.0, home_prob))
        away_prob = 1.0 - home_prob
        out.append(
            {
                "t": int(t),
                "home": round(home_prob, 4),
                "away": round(away_prob, 4),
            }
        )
    return out


def _record_before_game(games: List[Dict[str, Any]], *, game_id: str) -> tuple[int, int]:
    wins = 0
    losses = 0
    for g in games:
        gid = str(g.get("game_id") or "")
        if gid == game_id:
            break
        if not bool(g.get("is_completed")):
            continue
        result = g.get("result") if isinstance(g.get("result"), dict) else {}
        wl = str(result.get("wl") or "")
        if wl == "W":
            wins += 1
        elif wl == "L":
            losses += 1
    return wins, losses


def _attr_float(attrs: Any, *keys: str) -> Optional[float]:
    if not isinstance(attrs, dict):
        return None
    lower_map = {str(k).lower(): v for k, v in attrs.items()}
    for key in keys:
        raw = lower_map.get(str(key).lower())
        try:
            if raw is None:
                continue
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _risk_tier_from_inputs(
    *,
    st_fatigue: float,
    lt_fatigue: float,
    age: int,
    injury_freq: Optional[float],
    durability: Optional[float],
    reinjury_total: int,
) -> Dict[str, Any]:
    score = 0.0
    score += _clamp(st_fatigue, 0.0, 1.0) * 30.0
    score += _clamp(lt_fatigue, 0.0, 1.0) * 30.0
    score += _clamp((int(age) - 28) / 10.0, 0.0, 1.0) * 15.0
    if injury_freq is not None:
        score += _clamp((float(injury_freq) - 1.0) / 9.0, 0.0, 1.0) * 15.0
    if durability is not None:
        score += (1.0 - _clamp(float(durability) / 100.0, 0.0, 1.0)) * 10.0
    score += _clamp(float(reinjury_total) / 5.0, 0.0, 1.0) * 10.0
    final = int(round(_clamp(score, 0.0, 100.0)))
    if final >= 67:
        tier = "HIGH"
    elif final >= 34:
        tier = "MEDIUM"
    else:
        tier = "LOW"
    return {"risk_score": final, "risk_tier": tier}



def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _parse_iso_date(value: Any, *, field: str) -> date:
    raw = str(value or "")[:10]
    try:
        return date.fromisoformat(raw)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid {field}: expected YYYY-MM-DD")


def _build_risk_profile(
    *,
    row: Mapping[str, Any],
    injury_state: Mapping[str, Any],
    fatigue_state: Mapping[str, Any],
    sharpness_state: Mapping[str, Any],
    as_of_date: str,
) -> Dict[str, Any]:
    from injury.status import status_for_date

    attrs = row.get("attrs") if isinstance(row.get("attrs"), dict) else {}
    st = float((fatigue_state or {}).get("st", 0.0) or 0.0)
    lt = float((fatigue_state or {}).get("lt", 0.0) or 0.0)
    sharpness = float((sharpness_state or {}).get("sharpness", 50.0) or 50.0)
    age = int(row.get("age") or 0)
    injury_freq = _attr_float(attrs, "injury_freq", "injuryfrequency", "injury_frequency")
    durability = _attr_float(attrs, "durability", "dur")
    reinjury = injury_state.get("reinjury_count") if isinstance(injury_state, dict) else {}
    reinjury_total = sum(_safe_int(v, 0) for v in (reinjury or {}).values()) if isinstance(reinjury, dict) else 0
    risk = _risk_tier_from_inputs(
        st_fatigue=st,
        lt_fatigue=lt,
        age=age,
        injury_freq=injury_freq,
        durability=durability,
        reinjury_total=reinjury_total,
    )
    status = status_for_date(injury_state, on_date_iso=as_of_date)
    return {
        "player_id": str(row.get("player_id") or ""),
        "name": row.get("name"),
        "pos": row.get("pos"),
        "age": age,
        "injury_status": status,
        "injury_state": dict(injury_state or {}),
        "condition": {
            "short_term_fatigue": st,
            "long_term_fatigue": lt,
            "short_term_stamina": max(0.0, 1.0 - st),
            "long_term_stamina": max(0.0, 1.0 - lt),
            "sharpness": sharpness,
        },
        "risk_inputs": {
            "injury_freq": injury_freq,
            "durability": durability,
            "age": age,
            "lt_wear_proxy": lt,
            "energy_proxy": max(0.0, 1.0 - st),
            "reinjury_count": reinjury if isinstance(reinjury, dict) else {},
        },
        "risk_score": int(risk["risk_score"]),
        "risk_tier": risk["risk_tier"],
    }


def _medical_team_overview_payload(
    *,
    team_id: str,
    season_year: int,
    as_of: date,
    history_days: int,
    top_n: int,
) -> Dict[str, Any]:
    db_path = state.get_db_path()
    tid = str(normalize_team_id(team_id, strict=True))
    as_of_iso = as_of.isoformat()
    history_days = max(1, min(int(history_days or 180), 730))
    top_n = max(1, min(int(top_n or 5), 20))
    health_high_threshold = 0.5

    from agency import repo as agency_repo
    from fatigue import repo as fatigue_repo
    from injury import repo as injury_repo
    from readiness import repo as readiness_repo

    with LeagueRepo(db_path) as repo:
        repo.init_db()
        roster_rows = repo.get_team_roster(tid)
        pids = [str(r.get("player_id")) for r in (roster_rows or []) if r.get("player_id")]

        with repo.transaction() as cur:
            fatigue_by_pid = fatigue_repo.get_player_fatigue_states(cur, pids) if pids else {}
            sharp_by_pid = readiness_repo.get_player_sharpness_states(cur, pids, season_year=season_year) if (pids and season_year > 0) else {}
            injury_by_pid = injury_repo.get_player_injury_states(cur, pids) if pids else {}
            agency_by_pid = agency_repo.get_player_agency_states(cur, pids) if pids else {}
            start_iso = (as_of - timedelta(days=history_days)).isoformat()
            end_iso = (as_of + timedelta(days=1)).isoformat()
            recent_events = injury_repo.get_overlapping_injury_events(cur, pids, start_date=start_iso, end_date=end_iso) if pids else []

    risk_items: List[Dict[str, Any]] = []
    unavailable: List[Dict[str, Any]] = []
    health_rows: List[Dict[str, Any]] = []

    status_counts = {"OUT": 0, "RETURNING": 0, "HEALTHY": 0}
    risk_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}

    for row in roster_rows:
        pid = str(row.get("player_id"))
        prof = _build_risk_profile(
            row=row,
            injury_state=injury_by_pid.get(pid) or {},
            fatigue_state=fatigue_by_pid.get(pid) or {},
            sharpness_state=sharp_by_pid.get(pid) or {},
            as_of_date=as_of_iso,
        )
        risk_items.append(prof)
        status = str(prof.get("injury_status") or "HEALTHY")
        status_counts[status] = status_counts.get(status, 0) + 1
        tier = str(prof.get("risk_tier") or "LOW")
        risk_counts[tier] = risk_counts.get(tier, 0) + 1

        if status in {"OUT", "RETURNING"}:
            st = prof.get("injury_state") or {}
            unavailable.append({
                "player_id": pid,
                "name": row.get("name"),
                "pos": row.get("pos"),
                "recovery_status": status,
                "injury_current": {
                    "body_part": st.get("body_part"),
                    "injury_type": st.get("injury_type"),
                    "severity": st.get("severity"),
                    "out_until_date": st.get("out_until_date"),
                    "returning_until_date": st.get("returning_until_date"),
                },
            })

        agency_state = agency_by_pid.get(pid) or {}
        hf = _safe_float(agency_state.get("health_frustration"), 0.0)
        if agency_state:
            health_rows.append({
                "player_id": pid,
                "name": row.get("name"),
                "pos": row.get("pos"),
                "health_frustration": hf,
                "trade_request_level": _safe_int(agency_state.get("trade_request_level"), 0),
                "cooldown_health_until": agency_state.get("cooldown_health_until"),
                "escalation_health": _safe_int(agency_state.get("escalation_health"), 0),
            })

    risk_items_sorted = sorted(risk_items, key=lambda x: (int(x.get("risk_score") or 0), str(x.get("name") or "")), reverse=True)
    unavailable_sorted = sorted(unavailable, key=lambda x: (str((x.get("injury_current") or {}).get("out_until_date") or ""), str(x.get("name") or "")))
    health_sorted = sorted(health_rows, key=lambda x: (float(x.get("health_frustration") or 0.0), str(x.get("name") or "")), reverse=True)

    name_by_pid = {str(r.get("player_id")): r.get("name") for r in roster_rows}
    recent_events_sorted = sorted(recent_events, key=lambda x: str(x.get("date") or ""), reverse=True)
    recent_event_items: List[Dict[str, Any]] = []
    for e in recent_events_sorted[:top_n]:
        pid = str(e.get("player_id") or "")
        recent_event_items.append({
            "injury_id": e.get("injury_id"),
            "player_id": pid,
            "name": name_by_pid.get(pid),
            "date": e.get("date"),
            "context": e.get("context"),
            "body_part": e.get("body_part"),
            "injury_type": e.get("injury_type"),
            "severity": e.get("severity"),
            "out_until_date": e.get("out_until_date"),
            "returning_until_date": e.get("returning_until_date"),
        })

    hf_values = [float(r.get("health_frustration") or 0.0) for r in health_rows]
    hf_count = len(hf_values)

    return {
        "team_id": tid,
        "season_year": season_year,
        "as_of_date": as_of_iso,
        "history_days": history_days,
        "top_n": top_n,
        "summary": {
            "roster_count": len(roster_rows),
            "injury_status_counts": status_counts,
            "risk_tier_counts": risk_counts,
            "health_frustration": {
                "count_with_state": hf_count,
                "high_count": sum(1 for v in hf_values if v >= health_high_threshold),
                "max": max(hf_values) if hf_values else 0.0,
                "avg": (sum(hf_values) / hf_count) if hf_values else 0.0,
            },
        },
        "watchlists": {
            "highest_risk": risk_items_sorted[:top_n],
            "currently_unavailable": unavailable_sorted[:top_n],
            "health_frustration_high": [r for r in health_sorted if float(r.get("health_frustration") or 0.0) >= health_high_threshold][:top_n],
            "recent_injury_events": recent_event_items,
        },
    }
@router.get("/")
async def root():
    """간단한 헬스체크 및 NBA.html 링크 안내."""
    index_path = os.path.join(static_dir, "NBA.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "느바 시뮬 GM 서버입니다. /static/NBA.html 을 확인하세요."}

@router.get("/api/stats/leaders")
async def api_stats_leaders():
    """Regular-season per-game leaders.

    Note:
        - We intentionally keep the payload small (top 5, no ties) because this endpoint
          is commonly used as a quick "at-a-glance" widget.
        - This endpoint no longer depends on legacy `stats_util.py` facades.
    """
    workflow_state = state.export_workflow_state() or {}
    if not isinstance(workflow_state, dict):
        workflow_state = {}

    player_stats = workflow_state.get("player_stats") or {}
    team_stats = workflow_state.get("team_stats") or {}

    cfg = {
        "top_n": 5,
        "include_ties": False,
        "modes": ["per_game"],
        "metric_keys": ["PTS", "AST", "REB", "3PM"],
    }
    bundle = compute_leaderboards(player_stats, team_stats, phase="regular", config=cfg)
    leaders = bundle.get("per_game") or {}

    current_date = state.get_current_date()
    return {"leaders": leaders, "updated_at": current_date}


@router.get("/api/stats/playoffs/leaders")
async def api_playoff_stats_leaders():
    """Playoff per-game leaders (same small payload as regular season)."""
    workflow_state = state.export_workflow_state() or {}
    if not isinstance(workflow_state, dict):
        workflow_state = {}

    phase_results = workflow_state.get("phase_results") or {}
    if not isinstance(phase_results, dict):
        phase_results = {}

    playoffs = phase_results.get("playoffs") or {}
    if not isinstance(playoffs, dict):
        playoffs = {}

    player_stats = playoffs.get("player_stats") or {}
    team_stats = playoffs.get("team_stats") or {}

    cfg = {
        "top_n": 5,
        "include_ties": False,
        "modes": ["per_game"],
        "metric_keys": ["PTS", "AST", "REB", "3PM"],
    }
    bundle = compute_leaderboards(player_stats, team_stats, phase="playoffs", config=cfg)
    leaders = bundle.get("per_game") or {}
    current_date = state.get_current_date()
    return {"leaders": leaders, "updated_at": current_date}


@router.get("/api/standings")
async def api_standings():
    return get_conference_standings()


@router.get("/api/standings/table")
async def api_standings_table():
    return get_conference_standings_table()


@router.get("/api/teams")
async def api_teams():
    return get_team_cards()


@router.get("/api/team-detail/{team_id}")
async def api_team_detail(team_id: str):
    try:
        return get_team_detail(team_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# -------------------------------------------------------------------------
# College (Read-only / UI) API
# -------------------------------------------------------------------------




@router.get("/api/player-detail/{player_id}")
async def api_player_detail(player_id: str, season_year: Optional[int] = None):
    """Return rich player detail for My Team UI."""
    pid = str(normalize_player_id(player_id, strict=False, allow_legacy_numeric=True))
    if not pid:
        raise HTTPException(status_code=400, detail="Invalid player_id")

    db_path = state.get_db_path()
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(season_year or (league_ctx.get("season_year") or 0))

    workflow_state = state.export_workflow_state()
    season_player_stats = (workflow_state.get("player_stats") or {}) if isinstance(workflow_state, dict) else {}
    season_stats_entry = season_player_stats.get(pid) or {}

    with LeagueRepo(db_path) as repo:
        repo.init_db()
        try:
            player = repo.get_player(pid)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

        with repo.transaction() as cur:
            roster_row = cur.execute(
                """
                SELECT team_id, salary_amount, status
                FROM roster
                WHERE player_id=?
                LIMIT 1;
                """,
                (pid,),
            ).fetchone()

            active_contract_row = cur.execute(
                """
                SELECT *
                FROM contracts
                WHERE player_id=? AND COALESCE(is_active,0)=1
                ORDER BY updated_at DESC
                LIMIT 1;
                """,
                (pid,),
            ).fetchone()

            contract_rows = cur.execute(
                """
                SELECT *
                FROM contracts
                WHERE player_id=?
                ORDER BY start_season_year DESC, updated_at DESC;
                """,
                (pid,),
            ).fetchall()

            two_way_row = cur.execute(
                """
                SELECT COALESCE(contract_json,'') AS contract_json
                FROM contracts
                WHERE player_id=?
                  AND UPPER(COALESCE(contract_type,''))='TWO_WAY'
                  AND UPPER(COALESCE(status,''))='ACTIVE'
                  AND COALESCE(is_active, 0)=1
                ORDER BY updated_at DESC
                LIMIT 1;
                """,
                (pid,),
            ).fetchone()

            two_way = {"is_two_way": False, "game_limit": None, "games_used": 0, "games_remaining": None}
            if two_way_row:
                contract_data: Dict[str, Any] = {}
                raw_cd = two_way_row["contract_json"]
                if raw_cd:
                    try:
                        contract_data = json.loads(str(raw_cd))
                    except Exception:
                        contract_data = {}
                game_limit = int(contract_data.get("two_way_game_limit") or 50)
                used = cur.execute(
                    "SELECT COUNT(1) AS n FROM two_way_appearances WHERE player_id=? AND season_year=?;",
                    (pid, sy),
                ).fetchone()
                games_used = int((used["n"] if used is not None else 0) or 0)
                two_way = {
                    "is_two_way": True,
                    "game_limit": game_limit,
                    "games_used": games_used,
                    "games_remaining": max(0, int(game_limit) - int(games_used)),
                }

            from agency import repo as agency_repo
            agency_state = agency_repo.get_player_agency_states(cur, [pid]).get(pid)

            from injury import repo as injury_repo
            injury_state = injury_repo.get_player_injury_states(cur, [pid]).get(pid)

            from fatigue import repo as fatigue_repo
            fatigue_state = fatigue_repo.get_player_fatigue_states(cur, [pid]).get(pid)

            from readiness import repo as readiness_repo
            sharpness_state = None
            if sy > 0:
                sharpness_state = readiness_repo.get_player_sharpness_states(
                    cur,
                    [pid],
                    season_year=int(sy),
                ).get(pid)

    from contract_codec import contract_from_row

    active_contract = contract_from_row(active_contract_row) if active_contract_row else None
    contracts = [contract_from_row(r) for r in contract_rows]

    dissatisfaction = {
        "is_dissatisfied": False,
        "state": agency_state,
    }
    if isinstance(agency_state, dict):
        axes = [
            float(agency_state.get("team_frustration") or 0.0),
            float(agency_state.get("role_frustration") or 0.0),
            float(agency_state.get("contract_frustration") or 0.0),
            float(agency_state.get("health_frustration") or 0.0),
            float(agency_state.get("chemistry_frustration") or 0.0),
            float(agency_state.get("usage_frustration") or 0.0),
        ]
        trade_request_level = int(agency_state.get("trade_request_level") or 0)
        dissatisfaction["is_dissatisfied"] = trade_request_level > 0 or any(v >= 0.5 for v in axes)

    injury = {
        "is_injured": False,
        "status": "HEALTHY",
        "state": injury_state,
    }
    fatigue = fatigue_state or {}
    st_fatigue = float(fatigue.get("st", 0.0) or 0.0)
    lt_fatigue = float(fatigue.get("lt", 0.0) or 0.0)
    sharpness = float((sharpness_state or {}).get("sharpness", 50.0) or 50.0)
    if isinstance(injury_state, dict):
        status = str(injury_state.get("status") or "HEALTHY").upper()
        injury["status"] = status
        injury["is_injured"] = status in {"OUT", "RETURNING"}

    return {
        "ok": True,
        "player": {
            "player_id": pid,
            "name": player.get("name"),
            "pos": player.get("pos"),
            "age": player.get("age"),
            "height_in": player.get("height_in"),
            "weight_lb": player.get("weight_lb"),
            "ovr": player.get("ovr"),
            "attrs": player.get("attrs") or {},
        },
        "roster": {
            "team_id": (str(roster_row["team_id"]).upper() if roster_row and roster_row["team_id"] else None),
            "status": (str(roster_row["status"]) if roster_row and roster_row["status"] else None),
            "salary_amount": int(roster_row["salary_amount"] or 0) if roster_row else None,
        },
        "contract": {
            "active": active_contract,
            "all": contracts,
        },
        "dissatisfaction": dissatisfaction,
        "season_stats": season_stats_entry,
        "two_way": two_way,
        "condition": {
            "short_term_fatigue": st_fatigue,
            "long_term_fatigue": lt_fatigue,
            "short_term_stamina": max(0.0, 1.0 - st_fatigue),
            "long_term_stamina": max(0.0, 1.0 - lt_fatigue),
            "sharpness": sharpness,
            "fatigue_state": fatigue_state,
            "sharpness_state": sharpness_state,
        },
        "injury": injury,
    }


@router.get("/api/medical/team/{team_id}/injury-risk")
async def api_medical_injury_risk(
    team_id: str,
    season_year: Optional[int] = None,
    min_risk_tier: Optional[str] = None,
    include_healthy_only: bool = True,
):
    db_path = state.get_db_path()
    tid = str(normalize_team_id(team_id, strict=True))
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(season_year or (league_ctx.get("season_year") or 0))
    as_of_date = state.get_current_date_as_date().isoformat()

    tier_order = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
    min_tier = str(min_risk_tier or "LOW").upper()
    if min_tier not in tier_order:
        raise HTTPException(status_code=400, detail="min_risk_tier must be one of: LOW, MEDIUM, HIGH")

    with LeagueRepo(db_path) as repo:
        repo.init_db()
        roster_rows = repo.get_team_roster(tid)
        pids = [str(r.get("player_id")) for r in (roster_rows or []) if r.get("player_id")]
        fatigue_by_pid: Dict[str, Dict[str, Any]] = {}
        sharpness_by_pid: Dict[str, Dict[str, Any]] = {}
        injury_by_pid: Dict[str, Dict[str, Any]] = {}

        if pids:
            from fatigue import repo as fatigue_repo
            from readiness import repo as readiness_repo
            from injury import repo as injury_repo

            with repo.transaction() as cur:
                fatigue_by_pid = fatigue_repo.get_player_fatigue_states(cur, pids)
                if sy > 0:
                    sharpness_by_pid = readiness_repo.get_player_sharpness_states(cur, pids, season_year=sy)
                injury_by_pid = injury_repo.get_player_injury_states(cur, pids)

    from injury.status import status_for_date

    items: List[Dict[str, Any]] = []
    for row in roster_rows:
        pid = str(row.get("player_id"))
        attrs = row.get("attrs") if isinstance(row.get("attrs"), dict) else {}
        injury_state = injury_by_pid.get(pid) or {}
        normalized_status = status_for_date(injury_state, on_date_iso=as_of_date)
        if include_healthy_only and normalized_status != "HEALTHY":
            continue

        fatigue_row = fatigue_by_pid.get(pid) or {}
        st = float(fatigue_row.get("st", 0.0) or 0.0)
        lt = float(fatigue_row.get("lt", 0.0) or 0.0)
        sharpness = float((sharpness_by_pid.get(pid) or {}).get("sharpness", 50.0) or 50.0)
        age = int(row.get("age") or 0)
        injury_freq = _attr_float(attrs, "injury_freq", "injuryfrequency", "injury_frequency")
        durability = _attr_float(attrs, "durability", "dur")
        reinjury = injury_state.get("reinjury_count") if isinstance(injury_state, dict) else {}
        reinjury_total = sum(int(v or 0) for v in (reinjury or {}).values()) if isinstance(reinjury, dict) else 0
        risk = _risk_tier_from_inputs(
            st_fatigue=st,
            lt_fatigue=lt,
            age=age,
            injury_freq=injury_freq,
            durability=durability,
            reinjury_total=reinjury_total,
        )
        if tier_order[risk["risk_tier"]] < tier_order[min_tier]:
            continue

        items.append(
            {
                "player_id": pid,
                "name": row.get("name"),
                "pos": row.get("pos"),
                "age": age,
                "injury_status": normalized_status,
                "injury_state": injury_state,
                "condition": {
                    "short_term_fatigue": st,
                    "long_term_fatigue": lt,
                    "short_term_stamina": max(0.0, 1.0 - st),
                    "long_term_stamina": max(0.0, 1.0 - lt),
                    "sharpness": sharpness,
                },
                "risk_inputs": {
                    "injury_freq": injury_freq,
                    "durability": durability,
                    "age": age,
                    "lt_wear_proxy": lt,
                    "energy_proxy": max(0.0, 1.0 - st),
                    "reinjury_count": reinjury if isinstance(reinjury, dict) else {},
                },
                "risk_score": int(risk["risk_score"]),
                "risk_tier": risk["risk_tier"],
            }
        )

    items.sort(key=lambda x: (int(x.get("risk_score") or 0), str(x.get("name") or "")), reverse=True)
    return {
        "team_id": tid,
        "season_year": sy,
        "as_of_date": as_of_date,
        "min_risk_tier": min_tier,
        "include_healthy_only": bool(include_healthy_only),
        "items": items,
    }


@router.get("/api/medical/team/{team_id}/injured")
async def api_medical_injured_players(
    team_id: str,
    season_year: Optional[int] = None,
    include_returning: bool = True,
    include_event_history: bool = True,
    history_days: int = 180,
):
    db_path = state.get_db_path()
    tid = str(normalize_team_id(team_id, strict=True))
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(season_year or (league_ctx.get("season_year") or 0))
    as_of = state.get_current_date_as_date()
    as_of_iso = as_of.isoformat()
    history_days = max(1, min(int(history_days or 180), 730))
    start_iso = (as_of - timedelta(days=history_days)).isoformat()
    end_iso = (as_of + timedelta(days=1)).isoformat()

    with LeagueRepo(db_path) as repo:
        repo.init_db()
        roster_rows = repo.get_team_roster(tid)
        pids = [str(r.get("player_id")) for r in (roster_rows or []) if r.get("player_id")]

        from injury import repo as injury_repo
        from injury.status import status_for_date

        with repo.transaction() as cur:
            injury_by_pid = injury_repo.get_player_injury_states(cur, pids) if pids else {}
            events = (
                injury_repo.get_overlapping_injury_events(
                    cur,
                    pids,
                    start_date=start_iso,
                    end_date=end_iso,
                )
                if (include_event_history and pids)
                else []
            )

    events_by_pid: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        pid = str(e.get("player_id") or "")
        if not pid:
            continue
        events_by_pid.setdefault(pid, []).append(e)

    items: List[Dict[str, Any]] = []
    for row in roster_rows:
        pid = str(row.get("player_id"))
        state_row = injury_by_pid.get(pid) or {}
        recovery_status = status_for_date(state_row, on_date_iso=as_of_iso)
        if recovery_status == "HEALTHY":
            continue
        if recovery_status == "RETURNING" and not include_returning:
            continue

        items.append(
            {
                "player_id": pid,
                "name": row.get("name"),
                "pos": row.get("pos"),
                "recovery_status": recovery_status,
                "is_injured": recovery_status in {"OUT", "RETURNING"},
                "injury_current": {
                    "injury_id": state_row.get("injury_id"),
                    "start_date": state_row.get("start_date"),
                    "body_part": state_row.get("body_part"),
                    "injury_type": state_row.get("injury_type"),
                    "severity": state_row.get("severity"),
                    "out_until_date": state_row.get("out_until_date"),
                    "returning_until_date": state_row.get("returning_until_date"),
                    "temp_debuff": state_row.get("temp_debuff") or {},
                    "perm_drop": state_row.get("perm_drop") or {},
                    "reinjury_count": state_row.get("reinjury_count") or {},
                    "last_processed_date": state_row.get("last_processed_date"),
                },
                "availability": {
                    "out_until_date": state_row.get("out_until_date"),
                    "returning_until_date": state_row.get("returning_until_date"),
                },
                "history": events_by_pid.get(pid, []) if include_event_history else None,
            }
        )

    items.sort(key=lambda x: (str(x.get("availability", {}).get("out_until_date") or ""), str(x.get("name") or "")))
    return {
        "team_id": tid,
        "season_year": sy,
        "as_of_date": as_of_iso,
        "include_returning": bool(include_returning),
        "include_event_history": bool(include_event_history),
        "history_days": history_days,
        "items": items,
    }













@router.get("/api/medical/team/{team_id}/overview")
async def api_medical_team_overview(
    team_id: str,
    season_year: Optional[int] = None,
    history_days: int = 180,
    top_n: int = 5,
):
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(season_year or (league_ctx.get("season_year") or 0))
    as_of = state.get_current_date_as_date()
    return _medical_team_overview_payload(
        team_id=team_id,
        season_year=sy,
        as_of=as_of,
        history_days=history_days,
        top_n=top_n,
    )


@router.get("/api/medical/team/{team_id}/players/{player_id}/timeline")
async def api_medical_player_timeline(
    team_id: str,
    player_id: str,
    season_year: Optional[int] = None,
    history_days: int = 365,
    include_event_history: bool = True,
):
    db_path = state.get_db_path()
    tid = str(normalize_team_id(team_id, strict=True))
    pid = str(normalize_player_id(player_id, strict=False, allow_legacy_numeric=True))
    if not pid:
        raise HTTPException(status_code=400, detail="Invalid player_id")

    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(season_year or (league_ctx.get("season_year") or 0))
    as_of = state.get_current_date_as_date()
    as_of_iso = as_of.isoformat()
    history_days = max(1, min(int(history_days or 365), 730))

    from agency import repo as agency_repo
    from fatigue import repo as fatigue_repo
    from injury import repo as injury_repo
    from readiness import repo as readiness_repo
    from injury.status import status_for_date

    with LeagueRepo(db_path) as repo:
        repo.init_db()
        roster_rows = repo.get_team_roster(tid)
        row_by_pid = {str(r.get("player_id")): r for r in roster_rows}
        row = row_by_pid.get(pid)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Player '{pid}' is not on team '{tid}' active roster")

        with repo.transaction() as cur:
            fatigue_state = fatigue_repo.get_player_fatigue_states(cur, [pid]).get(pid)
            sharp_state = readiness_repo.get_player_sharpness_states(cur, [pid], season_year=sy).get(pid) if sy > 0 else None
            injury_state = injury_repo.get_player_injury_states(cur, [pid]).get(pid)
            agency_state = agency_repo.get_player_agency_states(cur, [pid]).get(pid)
            events = []
            if include_event_history:
                start_iso = (as_of - timedelta(days=history_days)).isoformat()
                end_iso = (as_of + timedelta(days=1)).isoformat()
                events = injury_repo.get_overlapping_injury_events(cur, [pid], start_date=start_iso, end_date=end_iso)

    prof = _build_risk_profile(
        row=row,
        injury_state=injury_state or {},
        fatigue_state=fatigue_state or {},
        sharpness_state=sharp_state or {},
        as_of_date=as_of_iso,
    )
    recovery_status = status_for_date(injury_state or {}, on_date_iso=as_of_iso)

    events_sorted = sorted(events, key=lambda x: str(x.get("date") or ""), reverse=True) if include_event_history else []

    return {
        "team_id": tid,
        "player_id": pid,
        "season_year": sy,
        "as_of_date": as_of_iso,
        "history_days": history_days,
        "include_event_history": bool(include_event_history),
        "player": {
            "name": row.get("name"),
            "pos": row.get("pos"),
            "age": int(row.get("age") or 0),
        },
        "status": {
            "recovery_status": recovery_status,
            "is_injured": recovery_status in {"OUT", "RETURNING"},
            "injury_status": prof.get("injury_status"),
        },
        "current": {
            "injury_state": prof.get("injury_state") or {},
            "availability": {
                "out_until_date": (prof.get("injury_state") or {}).get("out_until_date"),
                "returning_until_date": (prof.get("injury_state") or {}).get("returning_until_date"),
            },
            "condition": prof.get("condition") or {},
            "risk": {
                "risk_score": prof.get("risk_score"),
                "risk_tier": prof.get("risk_tier"),
                "risk_inputs": prof.get("risk_inputs") or {},
            },
            "health_psychology": {
                "health_frustration": _safe_float((agency_state or {}).get("health_frustration"), 0.0),
                "trade_request_level": _safe_int((agency_state or {}).get("trade_request_level"), 0),
                "cooldown_health_until": (agency_state or {}).get("cooldown_health_until"),
                "escalation_health": _safe_int((agency_state or {}).get("escalation_health"), 0),
            },
        },
        "timeline": {
            "events": events_sorted,
        },
    }


@router.get("/api/medical/team/{team_id}/alerts")
async def api_medical_team_alerts(
    team_id: str,
    season_year: Optional[int] = None,
    history_days: int = 180,
    top_n: int = 5,
    as_of_date: Optional[str] = None,
):
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(season_year or (league_ctx.get("season_year") or 0))
    as_of = _parse_iso_date(as_of_date, field="as_of_date") if as_of_date else state.get_current_date_as_date()
    return _build_medical_alerts_payload(
        team_id=team_id,
        season_year=sy,
        as_of=as_of,
        history_days=history_days,
        top_n=top_n,
    )


def _build_medical_alerts_payload(
    *,
    team_id: str,
    season_year: int,
    as_of: date,
    history_days: int,
    top_n: int,
    overview_now: Optional[Dict[str, Any]] = None,
    overview_prev: Optional[Dict[str, Any]] = None,
    schedule: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    overview_now = overview_now or _medical_team_overview_payload(
        team_id=team_id,
        season_year=season_year,
        as_of=as_of,
        history_days=history_days,
        top_n=top_n,
    )
    overview_prev = overview_prev or _medical_team_overview_payload(
        team_id=team_id,
        season_year=season_year,
        as_of=(as_of - timedelta(days=7)),
        history_days=history_days,
        top_n=top_n,
    )

    tid = str(overview_now.get("team_id") or "")
    schedule = schedule or _get_team_schedule_view(tid)
    current_date = str(schedule.get("current_date") or as_of.isoformat())[:10]
    cur_dt = date.fromisoformat(current_date)
    end_dt = cur_dt + timedelta(days=7)

    next_games: List[Dict[str, Any]] = []
    for g in (schedule.get("games") or []):
        d_raw = str(g.get("date") or "")[:10]
        if not d_raw:
            continue
        try:
            gd = date.fromisoformat(d_raw)
        except ValueError:
            continue
        if cur_dt <= gd < end_dt:
            next_games.append({"date": d_raw, "game": g})

    next_games.sort(key=lambda x: x["date"])
    b2b = 0
    for i in range(1, len(next_games)):
        d0 = date.fromisoformat(next_games[i - 1]["date"])
        d1 = date.fromisoformat(next_games[i]["date"])
        if (d1 - d0).days == 1:
            b2b += 1

    high = (overview_now.get("watchlists") or {}).get("highest_risk") or []
    primary = high[0] if high else None

    out_now = int((((overview_now.get("summary") or {}).get("injury_status_counts") or {}).get("OUT") or 0))
    out_prev = int((((overview_prev.get("summary") or {}).get("injury_status_counts") or {}).get("OUT") or 0))
    hr_now = int((((overview_now.get("summary") or {}).get("risk_tier_counts") or {}).get("HIGH") or 0))
    hr_prev = int((((overview_prev.get("summary") or {}).get("risk_tier_counts") or {}).get("HIGH") or 0))
    hf_now = int(((((overview_now.get("summary") or {}).get("health_frustration") or {}).get("high_count")) or 0))
    hf_prev = int(((((overview_prev.get("summary") or {}).get("health_frustration") or {}).get("high_count")) or 0))

    alert_level = "info"
    if primary:
        if str(primary.get("risk_tier") or "").upper() == "HIGH" and str(primary.get("injury_status") or "").upper() in {"OUT", "RETURNING"}:
            alert_level = "critical"
        elif str(primary.get("risk_tier") or "").upper() == "HIGH":
            alert_level = "warn"

    return {
        "team_id": tid,
        "as_of_date": as_of.isoformat(),
        "alert_level": alert_level,
        "primary_alert_player": ({
            "player_id": primary.get("player_id"),
            "name": primary.get("name"),
            "pos": primary.get("pos"),
            "injury_status": primary.get("injury_status"),
            "risk_score": primary.get("risk_score"),
            "risk_tier": primary.get("risk_tier"),
            "out_until_date": (primary.get("injury_state") or {}).get("out_until_date"),
            "returning_until_date": (primary.get("injury_state") or {}).get("returning_until_date"),
        } if primary else None),
        "team_load_context": {
            "next_7d_game_count": len(next_games),
            "next_7d_back_to_back_count": b2b,
        },
        "kpi_delta_7d": {
            "out_count_delta": out_now - out_prev,
            "high_risk_count_delta": hr_now - hr_prev,
            "health_high_count_delta": hf_now - hf_prev,
        },
    }


@router.get("/api/medical/team/{team_id}/risk-calendar")
async def api_medical_team_risk_calendar(
    team_id: str,
    season_year: Optional[int] = None,
    date_from: Optional[str] = None,
    days: int = 14,
):
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(season_year or (league_ctx.get("season_year") or 0))
    d0 = _parse_iso_date(date_from, field="date_from") if date_from else state.get_current_date_as_date()
    return _build_medical_risk_calendar_payload(
        team_id=team_id,
        season_year=sy,
        d0=d0,
        days=days,
    )


def _build_medical_risk_calendar_payload(
    *,
    team_id: str,
    season_year: int,
    d0: date,
    days: int,
    schedule: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    days = max(1, min(int(days or 14), 31))
    d1 = d0 + timedelta(days=days)

    tid = str(normalize_team_id(team_id, strict=True))
    schedule = schedule or _get_team_schedule_view(tid)

    from practice.service import list_team_practice_sessions
    from fatigue import repo as fatigue_repo
    from injury import repo as injury_repo
    from readiness import repo as readiness_repo

    with LeagueRepo(state.get_db_path()) as repo:
        repo.init_db()
        roster_rows = repo.get_team_roster(tid)
        pids = [str(r.get("player_id")) for r in (roster_rows or []) if r.get("player_id")]
        sessions = list_team_practice_sessions(
            repo=repo,
            team_id=tid,
            season_year=season_year,
            date_from=d0.isoformat(),
            date_to=(d1 - timedelta(days=1)).isoformat(),
        )
        fatigue_by_pid: Dict[str, Dict[str, Any]] = {}
        sharp_by_pid: Dict[str, Dict[str, Any]] = {}
        injury_by_pid: Dict[str, Dict[str, Any]] = {}
        recent_events: List[Dict[str, Any]] = []
        with repo.transaction() as cur:
            if pids:
                fatigue_by_pid = fatigue_repo.get_player_fatigue_states(cur, pids)
                if season_year > 0:
                    sharp_by_pid = readiness_repo.get_player_sharpness_states(cur, pids, season_year=season_year)
                injury_by_pid = injury_repo.get_player_injury_states(cur, pids)
                recent_events = injury_repo.get_overlapping_injury_events(
                    cur,
                    pids,
                    start_date=d0.isoformat(),
                    end_date=d1.isoformat(),
                )

    high_pids = set()
    out_pids = set()
    returning_pids = set()
    for row in roster_rows:
        pid = str(row.get("player_id") or "")
        if not pid:
            continue
        injury_state = injury_by_pid.get(pid) or {}
        fatigue_state = fatigue_by_pid.get(pid) or {}
        sharpness_state = sharp_by_pid.get(pid) or {}
        prof = _build_risk_profile(
            row=row,
            injury_state=injury_state,
            fatigue_state=fatigue_state,
            sharpness_state=sharpness_state,
            as_of_date=d0.isoformat(),
        )
        if str(prof.get("risk_tier") or "").upper() == "HIGH":
            high_pids.add(pid)
        status = str(prof.get("injury_status") or "").upper()
        if status == "OUT":
            out_pids.add(pid)
        elif status == "RETURNING":
            returning_pids.add(pid)

    games = []
    for g in (schedule.get("games") or []):
        ds = str(g.get("date") or "")[:10]
        if not ds:
            continue
        try:
            gd = date.fromisoformat(ds)
        except ValueError:
            continue
        if d0 <= gd < d1:
            games.append(g)

    games_by_date: Dict[str, List[Dict[str, Any]]] = {}
    for g in games:
        ds = str(g.get("date"))[:10]
        games_by_date.setdefault(ds, []).append(g)

    game_dates_sorted = sorted(games_by_date.keys())
    b2b_dates = set()
    for i in range(1, len(game_dates_sorted)):
        d_prev = date.fromisoformat(game_dates_sorted[i - 1])
        d_cur = date.fromisoformat(game_dates_sorted[i])
        if (d_cur - d_prev).days == 1:
            b2b_dates.add(game_dates_sorted[i - 1])
            b2b_dates.add(game_dates_sorted[i])

    event_count_by_date: Dict[str, int] = {}
    for e in recent_events:
        ds = str(e.get("date") or "")[:10]
        if not ds:
            continue
        event_count_by_date[ds] = event_count_by_date.get(ds, 0) + 1

    day_rows: List[Dict[str, Any]] = []
    for i in range(days):
        d = d0 + timedelta(days=i)
        ds = d.isoformat()
        games_today = games_by_date.get(ds) or []
        first_game = games_today[0] if games_today else None
        session = (sessions.get(ds) or {}).get("session") or {}
        day_rows.append({
            "date": ds,
            "is_game_day": bool(games_today),
            "opponent_team_id": (first_game or {}).get("opponent_team_id"),
            "is_back_to_back": ds in b2b_dates,
            "practice_session_type": session.get("type"),
            "high_risk_player_count": len(high_pids),
            "out_player_count": len(out_pids),
            "returning_player_count": len(returning_pids),
            "injury_event_count": int(event_count_by_date.get(ds, 0)),
        })

    return {
        "team_id": tid,
        "season_year": season_year,
        "date_from": d0.isoformat(),
        "date_to": d1.isoformat(),
        "days": day_rows,
    }


@router.get("/api/medical/team/{team_id}/players/{player_id}/action-recommendations")
async def api_medical_player_action_recommendations(
    team_id: str,
    player_id: str,
    season_year: Optional[int] = None,
):
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(season_year or (league_ctx.get("season_year") or 0))
    tid = str(normalize_team_id(team_id, strict=True))
    pid = str(normalize_player_id(player_id, strict=False, allow_legacy_numeric=True))
    if not pid:
        raise HTTPException(status_code=400, detail="Invalid player_id")

    base = await api_medical_player_timeline(team_id=tid, player_id=pid, season_year=sy, history_days=365, include_event_history=True)
    current = base.get("current") or {}
    condition = current.get("condition") or {}
    risk = current.get("risk") or {}
    psych = current.get("health_psychology") or {}

    st_now = _safe_float(condition.get("short_term_fatigue"), 0.0)
    lt_now = _safe_float(condition.get("long_term_fatigue"), 0.0)
    sh_now = _safe_float(condition.get("sharpness"), 50.0)

    def projected(action_type: str) -> Dict[str, Any]:
        st_after = st_now
        lt_after = lt_now
        sh_after = sh_now
        if action_type == "RECOVERY":
            st_after = _clamp(st_now - 0.04, 0.0, 1.0)
            sh_after = _clamp(sh_now - 0.5, 0.0, 100.0)
        elif action_type == "REST":
            st_after = _clamp(st_now - 0.025, 0.0, 1.0)
            lt_after = _clamp(lt_now - 0.02, 0.0, 1.0)
            sh_after = _clamp(sh_now - 0.25, 0.0, 100.0)
        elif action_type == "FILM":
            st_after = _clamp(st_now - 0.01, 0.0, 1.0)
            sh_after = _clamp(sh_now + 0.5, 0.0, 100.0)

        attrs = (risk.get("risk_inputs") or {})
        age = _safe_int(attrs.get("age"), 0)
        injury_freq = attrs.get("injury_freq")
        durability = attrs.get("durability")
        reinj_total = sum(_safe_int(v, 0) for v in ((attrs.get("reinjury_count") or {}).values()))
        new_risk = _risk_tier_from_inputs(
            st_fatigue=st_after,
            lt_fatigue=lt_after,
            age=age,
            injury_freq=injury_freq,
            durability=durability,
            reinjury_total=reinj_total,
        )
        risk_now = _safe_int(risk.get("risk_score"), 0)
        return {
            "short_term_fatigue": st_after - st_now,
            "long_term_fatigue": lt_after - lt_now,
            "sharpness": sh_after - sh_now,
            "risk_score": int(new_risk.get("risk_score") or 0) - risk_now,
        }

    recommendations = [
        {
            "action_id": "RECOVERY_SESSION_NEXT_DAY",
            "label": "다음 훈련일 회복 세션 배치",
            "expected_delta": projected("RECOVERY"),
            "basis": {
                "practice_preview_used": False,
                "risk_formula_version": "core._risk_tier_from_inputs",
            },
        },
        {
            "action_id": "REST_SESSION_NEXT_DAY",
            "label": "다음 훈련일 팀 휴식 배치",
            "expected_delta": projected("REST"),
            "basis": {
                "practice_preview_used": False,
                "risk_formula_version": "core._risk_tier_from_inputs",
            },
        },
        {
            "action_id": "FILM_SESSION_NEXT_DAY",
            "label": "다음 훈련일 필름 세션 배치",
            "expected_delta": projected("FILM"),
            "basis": {
                "practice_preview_used": False,
                "risk_formula_version": "core._risk_tier_from_inputs",
            },
        },
    ]

    return {
        "team_id": tid,
        "player_id": pid,
        "as_of_date": base.get("as_of_date"),
        "current": {
            "injury_status": (base.get("status") or {}).get("injury_status"),
            "risk_score": risk.get("risk_score"),
            "risk_tier": risk.get("risk_tier"),
            "short_term_fatigue": st_now,
            "long_term_fatigue": lt_now,
            "sharpness": sh_now,
            "health_frustration": _safe_float(psych.get("health_frustration"), 0.0),
        },
        "recommendations": recommendations,
    }


@router.get("/api/roster-summary/{team_id}")
async def roster_summary(team_id: str):
    """특정 팀의 로스터를 LLM이 보기 좋은 형태로 요약해서 돌려준다."""
    db_path = state.get_db_path()
    team_id = str(normalize_team_id(team_id, strict=True))
    with LeagueRepo(db_path) as repo:
        # DB schema is guaranteed during server startup (state.startup_init_state()).
        roster = repo.get_team_roster(team_id)

    if not roster:
        raise HTTPException(status_code=404, detail=f"Team '{team_id}' not found in roster")

    players: List[Dict[str, Any]] = []
    for row in roster:
        players.append({
            "player_id": row.get("player_id"),
            "name": row.get("name"),
            "pos": str(row.get("pos") or ""),
            "overall": float(row.get("ovr") or 0.0),
        })

    players = sorted(players, key=lambda x: x["overall"], reverse=True)

    return {
        "team_id": team_id,
        "players": players[:12],
    }


@router.get("/api/two-way/summary/{team_id}")
async def two_way_summary(team_id: str):
    """특정 팀의 투웨이 슬롯/출전 가능 경기 수를 요약해서 반환한다."""
    db_path = state.get_db_path()
    tid = str(normalize_team_id(team_id, strict=True))
    season_year = int((state.export_full_state_snapshot().get("league", {}) or {}).get("season_year") or 0)

    with LeagueRepo(db_path) as repo:
        with repo.transaction() as cur:
            rows = cur.execute(
                """
                SELECT c.player_id, p.name,
                       COALESCE(c.contract_type,'') AS contract_type,
                       COALESCE(c.status,'') AS status,
                       COALESCE(c.contract_json,'') AS contract_json
                FROM contracts c
                LEFT JOIN players p ON p.player_id = c.player_id
                WHERE c.team_id=?
                  AND UPPER(COALESCE(c.contract_type,''))='TWO_WAY'
                  AND UPPER(COALESCE(c.status,''))='ACTIVE'
                  AND COALESCE(c.is_active, 0)=1
                ORDER BY p.name ASC;
                """,
                (tid,),
            ).fetchall()

            players: List[Dict[str, Any]] = []
            for r in rows:
                player_id = str(r["player_id"])
                contract_data_raw = r["contract_json"]
                contract_data: Dict[str, Any] = {}
                if contract_data_raw:
                    try:
                        contract_data = json.loads(str(contract_data_raw))
                    except Exception:
                        contract_data = {}

                limit = int(contract_data.get("two_way_game_limit") or 50)
                used = cur.execute(
                    "SELECT COUNT(1) AS n FROM two_way_appearances WHERE player_id=? AND season_year=?;",
                    (player_id, season_year),
                ).fetchone()
                used_i = int((used["n"] if used is not None else 0) or 0)
                players.append(
                    {
                        "player_id": player_id,
                        "name": r["name"],
                        "contract_type": "TWO_WAY",
                        "game_limit": limit,
                        "games_used": used_i,
                        "games_remaining": max(0, int(limit) - int(used_i)),
                    }
                )

    max_slots = 3
    return {
        "team_id": tid,
        "season_year": season_year,
        "max_two_way_slots": max_slots,
        "used_two_way_slots": len(players),
        "open_two_way_slots": max(0, max_slots - len(players)),
        "players": players,
    }


# -------------------------------------------------------------------------
# 팀별 시즌 스케줄 조회 API
# -------------------------------------------------------------------------
def _build_formatted_team_schedule_games(
    *,
    team_id: str,
    games: List[Dict[str, Any]],
    game_results: Mapping[str, Any] | None,
) -> List[Dict[str, Any]]:
    team_games: List[Dict[str, Any]] = [
        g for g in (games or [])
        if g.get("home_team_id") == team_id or g.get("away_team_id") == team_id
    ]
    team_games.sort(key=lambda g: (g.get("date"), g.get("game_id")))

    formatted_games: List[Dict[str, Any]] = []
    wins = 0
    losses = 0
    for g in team_games:
        game_id = g.get("game_id")
        home_team_id = str(g.get("home_team_id") or "")
        away_team_id = str(g.get("away_team_id") or "")
        is_home = team_id == home_team_id
        opponent_team_id = away_team_id if is_home else home_team_id
        home_score = g.get("home_score")
        away_score = g.get("away_score")
        status = str(g.get("status") or "")
        is_completed = home_score is not None and away_score is not None
        result_for_team = None
        record_after_game: Optional[Dict[str, Any]] = None
        leaders: Optional[Dict[str, Any]] = None
        result: Optional[Dict[str, Any]] = None

        if is_completed:
            if is_home:
                result_for_team = "W" if home_score > away_score else "L"
            else:
                result_for_team = "W" if away_score > home_score else "L"

            if result_for_team == "W":
                wins += 1
            else:
                losses += 1

            score_for = int(home_score if is_home else away_score)
            score_against = int(away_score if is_home else home_score)
            result = {
                "wl": result_for_team,
                "score_for": score_for,
                "score_against": score_against,
                "display": f"{result_for_team} {score_for}-{score_against}",
            }
            record_after_game = {
                "wins": wins,
                "losses": losses,
                "display": f"{wins}-{losses}",
            }

            gr = game_results.get(str(game_id)) if isinstance(game_results, Mapping) else None
            team_box = ((gr or {}).get("teams") or {}).get(team_id) if isinstance(gr, dict) else None
            rows = team_box.get("players") if isinstance(team_box, dict) else []
            rows = rows if isinstance(rows, list) else []
            leaders = {
                "points": _pick_leader(rows, ["PTS"]),
                "rebounds": _pick_leader(rows, ["REB", "TRB"]),
                "assists": _pick_leader(rows, ["AST"]),
            }

        if not status:
            status = "final" if is_completed else "scheduled"

        formatted_games.append({
            "game_id": game_id,
            "date": g.get("date"),
            "date_mmdd": _format_mmdd(g.get("date")),
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "is_home": is_home,
            "opponent_team_id": opponent_team_id,
            "opponent_team_name": TEAM_FULL_NAMES.get(opponent_team_id, opponent_team_id),
            "opponent_label": f"{'vs' if is_home else '@'} {opponent_team_id}",
            "status": status,
            "is_completed": is_completed,
            "home_score": home_score,
            "away_score": away_score,
            "result_for_user_team": result_for_team,
            "result": result,
            "record_after_game": record_after_game,
            "leaders": leaders,
            "tipoff_time": None if is_completed else _deterministic_tipoff_time(game_id),
        })

    return formatted_games


def _get_team_schedule_view(team_id: str) -> Dict[str, Any]:
    """Build schedule payload parts for a team using lightweight state accessors.

    Returns the same core shape used by `/api/team-schedule` consumers so
    endpoints can reuse computed schedule data without route-to-route calls.
    """
    team_id = str(team_id or "").upper()
    if team_id not in ALL_TEAM_IDS:
        raise HTTPException(status_code=404, detail=f"Team '{team_id}' not found in league")

    schedule_snapshot = state.get_league_schedule_snapshot() or {}
    master_schedule = schedule_snapshot.get("master_schedule") if isinstance(schedule_snapshot, dict) else {}
    master_schedule = master_schedule if isinstance(master_schedule, dict) else {}
    games = master_schedule.get("games") or []

    if not games:
        raise HTTPException(
            status_code=500,
            detail="Master schedule is not initialized. Expected server startup_init_state() to run.",
        )

    game_results = state.get_game_results_snapshot(phase="regular") or {}
    return {
        "team_id": team_id,
        "season_id": str(schedule_snapshot.get("active_season_id") or ""),
        "current_date": str(schedule_snapshot.get("current_date") or "")[:10],
        "games": _build_formatted_team_schedule_games(
            team_id=team_id,
            games=games,
            game_results=game_results,
        ),
    }


@router.get("/api/team-schedule/{team_id}")
async def team_schedule(team_id: str):
    """마스터 스케줄 기준으로 특정 팀의 전체 시즌 일정을 UI 친화 포맷으로 반환."""
    return _get_team_schedule_view(team_id)


@router.get("/api/home/dashboard/{team_id}")
async def api_home_dashboard(team_id: str):
    """Home 화면 렌더에 필요한 핵심 정보 묶음 조회.

    Notes:
    - 기존 조회 로직을 재사용해 Home에서 자주 필요한 카드 데이터를 한 번에 제공한다.
    - 반환값은 UI 친화 포맷이며, 누락값은 None/기본값으로 안전하게 채운다.
    """
    tid = str(normalize_team_id(team_id, strict=True))
    league_ctx = state.get_league_context_snapshot() or {}
    sy = int(league_ctx.get("season_year") or 0)
    as_of = state.get_current_date_as_date()

    schedule = _get_team_schedule_view(tid)
    team_summary = get_team_summary_light(tid)
    standings_table = get_conference_standings_home_light()
    medical_overview = _medical_team_overview_payload(
        team_id=tid,
        season_year=sy,
        as_of=as_of,
        history_days=180,
        top_n=5,
    )
    medical_overview_prev = _medical_team_overview_payload(
        team_id=tid,
        season_year=sy,
        as_of=(as_of - timedelta(days=7)),
        history_days=180,
        top_n=5,
    )
    medical_alerts = _build_medical_alerts_payload(
        team_id=tid,
        season_year=sy,
        as_of=as_of,
        history_days=180,
        top_n=5,
        overview_now=medical_overview,
        overview_prev=medical_overview_prev,
        schedule=schedule,
    )
    risk_calendar = _build_medical_risk_calendar_payload(
        team_id=tid,
        season_year=sy,
        d0=as_of,
        days=14,
        schedule=schedule,
    )

    current_date = str(schedule.get("current_date") or "")[:10]
    games = schedule.get("games") or []
    next_game = next(
        (
            g
            for g in games
            if str(g.get("date") or "")[:10] >= current_date and not bool(g.get("is_completed"))
        ),
        None,
    )
    recent_results = [g for g in games if bool(g.get("is_completed"))][-5:]
    recent_results = list(reversed(recent_results))

    east_rows = standings_table.get("east") if isinstance(standings_table, dict) else []
    west_rows = standings_table.get("west") if isinstance(standings_table, dict) else []
    all_rows = [r for r in (east_rows or []) + (west_rows or []) if isinstance(r, dict)]
    standings_by_team = {str(r.get("team_id") or "").upper(): r for r in all_rows}
    my_standing = standings_by_team.get(tid)

    opponent_standing = None
    if isinstance(next_game, dict):
        opp_id = str(next_game.get("opponent_team_id") or "").upper()
        opponent_standing = standings_by_team.get(opp_id)

    med_summary = (medical_overview.get("summary") or {}) if isinstance(medical_overview, dict) else {}
    injury_counts = med_summary.get("injury_status_counts") or {}
    risk_counts = med_summary.get("risk_tier_counts") or {}

    feed_items: List[Dict[str, Any]] = []
    for g in recent_results:
        result = g.get("result") if isinstance(g.get("result"), dict) else {}
        feed_items.append(
            {
                "type": "GAME_RESULT",
                "date": str(g.get("date") or "")[:10],
                "title": f"{g.get('opponent_label') or ''} {result.get('display') or '-'}",
                "meta": {
                    "opponent_team_id": g.get("opponent_team_id"),
                    "opponent_team_name": g.get("opponent_team_name"),
                    "result": result,
                },
            }
        )

    recent_events = (((medical_overview.get("watchlists") or {}).get("recent_injury_events")) or []) if isinstance(medical_overview, dict) else []
    for e in recent_events[:5]:
        feed_items.append(
            {
                "type": "INJURY_EVENT",
                "date": str(e.get("date") or "")[:10],
                "title": f"{e.get('player_name') or e.get('name') or 'Unknown'} · {e.get('injury_type') or 'Injury'}",
                "meta": {
                    "player_id": e.get("player_id"),
                    "severity": e.get("severity"),
                    "body_part": e.get("body_part"),
                    "recovery_status": e.get("recovery_status"),
                },
            }
        )

    feed_items.sort(key=lambda x: str(x.get("date") or ""), reverse=True)
    feed_items = feed_items[:7]

    priorities: List[Dict[str, Any]] = []
    alert_level = str((medical_alerts or {}).get("alert_level") or "info")
    primary_alert_player = (medical_alerts or {}).get("primary_alert_player") if isinstance(medical_alerts, dict) else None
    if primary_alert_player:
        priorities.append(
            {
                "kind": "MEDICAL_ALERT",
                "severity": alert_level,
                "text": (
                    f"{primary_alert_player.get('name') or '선수'} "
                    f"({primary_alert_player.get('risk_tier') or '-'}) "
                    f"리스크 모니터링 필요"
                ),
                "cta": "메디컬 확인",
            }
        )

    load_ctx = (medical_alerts or {}).get("team_load_context") if isinstance(medical_alerts, dict) else {}
    b2b_count = int((load_ctx or {}).get("next_7d_back_to_back_count") or 0)
    game_count_7d = int((load_ctx or {}).get("next_7d_game_count") or 0)
    if b2b_count > 0:
        priorities.append(
            {
                "kind": "SCHEDULE_LOAD",
                "severity": "warn",
                "text": f"향후 7일 {game_count_7d}경기 · 백투백 {b2b_count}회",
                "cta": "훈련 조정",
            }
        )

    out_count = int((injury_counts or {}).get("OUT") or 0)
    high_risk_count = int((risk_counts or {}).get("HIGH") or 0)
    if out_count > 0 or high_risk_count > 0:
        priorities.append(
            {
                "kind": "ROSTER_RISK",
                "severity": "critical" if out_count >= 2 else "warn",
                "text": f"결장 {out_count}명 · 고위험 {high_risk_count}명",
                "cta": "로테이션 점검",
            }
        )

    priorities = priorities[:3]

    return {
        "team_id": tid,
        "current_date": current_date,
        "snapshot": {
            "team_name": TEAM_FULL_NAMES.get(tid, tid),
            "record": {
                "wins": team_summary.get("wins"),
                "losses": team_summary.get("losses"),
                "win_pct": team_summary.get("win_pct"),
            },
            "standing": {
                "rank": (my_standing or {}).get("rank"),
                "gb_display": (my_standing or {}).get("gb_display"),
                "l10": (my_standing or {}).get("l10"),
                "streak": (my_standing or {}).get("strk"),
            },
            "finance": {
                "payroll": team_summary.get("payroll"),
                "cap_space": team_summary.get("cap_space"),
            },
            "health": {
                "out_count": out_count,
                "high_risk_count": high_risk_count,
                "returning_count": int((injury_counts or {}).get("RETURNING") or 0),
            },
        },
        "next_game": {
            "game": next_game,
            "my_team_standing": my_standing,
            "opponent_standing": opponent_standing,
        },
        "priorities": priorities,
        "activity_feed": feed_items,
        "risk_calendar": risk_calendar.get("days") if isinstance(risk_calendar, dict) else [],
        "medical_alerts": medical_alerts,
    }


@router.get("/api/game/result/{game_id}")
async def api_game_result(game_id: str, user_team_id: str):
    gid = str(game_id or "").strip()
    if not gid:
        raise HTTPException(status_code=404, detail="GAME_NOT_FOUND")

    try:
        tid = str(normalize_team_id(user_team_id, strict=True))
    except Exception:
        raise HTTPException(status_code=400, detail="INVALID_USER_TEAM")

    schedule_snapshot = state.get_league_schedule_snapshot() or {}
    master_schedule = schedule_snapshot.get("master_schedule") if isinstance(schedule_snapshot, dict) else {}
    master_schedule = master_schedule if isinstance(master_schedule, dict) else {}
    games = master_schedule.get("games") if isinstance(master_schedule, dict) else []
    games = games if isinstance(games, list) else []
    by_id = master_schedule.get("by_id") if isinstance(master_schedule, dict) else None
    schedule_entry = by_id.get(gid) if isinstance(by_id, dict) else None
    if not isinstance(schedule_entry, dict):
        schedule_entry = next((g for g in games if isinstance(g, dict) and str(g.get("game_id") or "") == gid), None)
    if not isinstance(schedule_entry, dict):
        raise HTTPException(status_code=404, detail="GAME_NOT_FOUND")

    home_id = str(schedule_entry.get("home_team_id") or "")
    away_id = str(schedule_entry.get("away_team_id") or "")
    if tid not in {home_id, away_id}:
        raise HTTPException(status_code=400, detail="USER_TEAM_NOT_IN_GAME")

    game_results = state.get_game_results_snapshot(phase="regular") or {}
    game_result = game_results.get(gid) if isinstance(game_results, dict) else None
    if not isinstance(game_result, dict):
        raise HTTPException(status_code=409, detail="GAME_NOT_FINAL")

    final = game_result.get("final") if isinstance(game_result, dict) else {}
    final = final if isinstance(final, dict) else {}
    home_score = _to_int_or_none(final.get(home_id))
    away_score = _to_int_or_none(final.get(away_id))
    if home_score is None or away_score is None:
        raise HTTPException(status_code=409, detail="GAME_NOT_FINAL")

    game_meta = game_result.get("game") if isinstance(game_result, dict) else {}
    game_meta = game_meta if isinstance(game_meta, dict) else {}
    overtime_periods = _to_int_or_none(game_meta.get("overtime_periods")) or 0
    winner_team_id = home_id if home_score >= away_score else away_id

    opponent_id = away_id if tid == home_id else home_id
    user_games = _build_formatted_team_schedule_games(
        team_id=tid,
        games=games,
        game_results=game_results,
    )
    opp_games = _build_formatted_team_schedule_games(
        team_id=opponent_id,
        games=games,
        game_results=game_results,
    )

    user_row = next((g for g in user_games if isinstance(g, dict) and str(g.get("game_id") or "") == gid), None)
    opp_row = next((g for g in opp_games if isinstance(g, dict) and str(g.get("game_id") or "") == gid), None)
    if not isinstance(user_row, dict) or not bool(user_row.get("is_completed")):
        raise HTTPException(status_code=409, detail="GAME_NOT_FINAL")

    user_record_after = (user_row.get("record_after_game") or {}).get("display") if isinstance(user_row, dict) else None
    opp_record_after = (opp_row.get("record_after_game") or {}).get("display") if isinstance(opp_row, dict) else None

    teams_box = game_result.get("teams") if isinstance(game_result, dict) else {}
    teams_box = teams_box if isinstance(teams_box, dict) else {}
    home_box = teams_box.get(home_id) if isinstance(teams_box, dict) else None
    away_box = teams_box.get(away_id) if isinstance(teams_box, dict) else None
    home_players = (home_box.get("players") if isinstance(home_box, dict) else []) or []
    away_players = (away_box.get("players") if isinstance(away_box, dict) else []) or []
    home_players = home_players if isinstance(home_players, list) else []
    away_players = away_players if isinstance(away_players, list) else []

    def _to_num(v: Any) -> float:
        try:
            return float(v)
        except Exception:
            return 0.0

    def _build_team_box_totals(players: List[Dict[str, Any]]) -> Dict[str, Any]:
        totals = {
            "PTS": 0,
            "FGM": 0,
            "FGA": 0,
            "3PM": 0,
            "3PA": 0,
            "FTM": 0,
            "FTA": 0,
            "ORB": 0,
            "DRB": 0,
            "REB": 0,
            "AST": 0,
            "TOV": 0,
            "STL": 0,
            "BLK": 0,
            "PF": 0,
        }
        for p in players:
            if not isinstance(p, dict):
                continue
            for k in totals.keys():
                totals[k] += int(round(_to_num(p.get(k))))

        totals["FG_PCT"] = round((totals["FGM"] / totals["FGA"] * 100.0), 1) if totals["FGA"] else 0.0
        totals["3P_PCT"] = round((totals["3PM"] / totals["3PA"] * 100.0), 1) if totals["3PA"] else 0.0
        totals["FT_PCT"] = round((totals["FTM"] / totals["FTA"] * 100.0), 1) if totals["FTA"] else 0.0
        return totals

    boxscore = {
        "away": {
            "team_id": away_id,
            "team_name": TEAM_FULL_NAMES.get(away_id, away_id),
            "players": away_players,
        },
        "home": {
            "team_id": home_id,
            "team_name": TEAM_FULL_NAMES.get(home_id, home_id),
            "players": home_players,
        },
    }
    teamstats = {
        "away": _build_team_box_totals(away_players),
        "home": _build_team_box_totals(home_players),
    }

    leaders = {
        "points": {
            "home": _pick_leader(home_players, ["PTS"]),
            "away": _pick_leader(away_players, ["PTS"]),
        },
        "rebounds": {
            "home": _pick_leader(home_players, ["REB", "TRB"]),
            "away": _pick_leader(away_players, ["REB", "TRB"]),
        },
        "assists": {
            "home": _pick_leader(home_players, ["AST"]),
            "away": _pick_leader(away_players, ["AST"]),
        },
    }

    replay_events = game_result.get("replay_events") if isinstance(game_result, dict) else []
    replay_events = replay_events if isinstance(replay_events, list) else []
    linescore = game_result.get("linescore") if isinstance(game_result, dict) else []
    linescore = linescore if isinstance(linescore, list) else []
    away_by_period: Dict[str, int] = {}
    home_by_period: Dict[str, int] = {}
    for row in linescore:
        if not isinstance(row, dict):
            continue
        p = _to_int_or_none(row.get("period"))
        if p is None or p <= 0:
            continue
        h = _to_int_or_none(row.get("home"))
        a = _to_int_or_none(row.get("away"))
        if h is not None:
            home_by_period[str(p)] = int(h)
        if a is not None:
            away_by_period[str(p)] = int(a)

    boxscore_quarters: List[Dict[str, Any]] = []
    if home_by_period or away_by_period:
        boxscore_quarters = [
            {
                "team_id": away_id,
                "by_period": away_by_period,
                "total": int(away_score),
            },
            {
                "team_id": home_id,
                "by_period": home_by_period,
                "total": int(home_score),
            },
        ]
    game_flow_series = _build_game_flow_series(
        [e for e in replay_events if isinstance(e, dict)],
        home_final=int(home_score),
        away_final=int(away_score),
        overtime_periods=int(overtime_periods),
    )

    user_w_before, user_l_before = _record_before_game(user_games, game_id=gid)
    opp_w_before, opp_l_before = _record_before_game(opp_games, game_id=gid)
    user_win_pct = float(user_w_before) / float(max(1, user_w_before + user_l_before))
    opp_win_pct = float(opp_w_before) / float(max(1, opp_w_before + opp_l_before))
    pre_game_strength_gap = user_win_pct - opp_win_pct if tid == home_id else opp_win_pct - user_win_pct

    win_probability_series = _build_win_probability_series(
        game_flow_series,
        overtime_periods=int(overtime_periods),
        pre_game_strength_gap=float(pre_game_strength_gap),
    )

    matchup_games = [
        g
        for g in user_games
        if isinstance(g, dict) and str(g.get("opponent_team_id") or "") == opponent_id
    ]
    season_wins = 0
    season_losses = 0
    completed: List[Dict[str, Any]] = []
    upcoming: List[Dict[str, Any]] = []
    for g in matchup_games:
        g_completed = bool(g.get("is_completed"))
        g_is_home = bool(g.get("is_home"))
        g_home_score = _to_int_or_none(g.get("home_score"))
        g_away_score = _to_int_or_none(g.get("away_score"))
        user_score = g_home_score if g_is_home else g_away_score
        opp_score = g_away_score if g_is_home else g_home_score
        if g_completed:
            wl = str(((g.get("result") or {}).get("wl") or ""))
            if wl == "W":
                season_wins += 1
            elif wl == "L":
                season_losses += 1
            completed.append(
                {
                    "game_id": g.get("game_id"),
                    "date": str(g.get("date") or "")[:10],
                    "user_team_home": g_is_home,
                    "user_team_score": user_score,
                    "opponent_score": opp_score,
                    "result": wl if wl in {"W", "L"} else None,
                }
            )
        else:
            upcoming.append(
                {
                    "game_id": g.get("game_id"),
                    "date": str(g.get("date") or "")[:10],
                    "user_team_home": g_is_home,
                    "tipoff_time": g.get("tipoff_time"),
                }
            )

    return {
        "game_id": gid,
        "status": "final",
        "as_of_date": state.get_current_date_as_date().isoformat(),
        "header": {
            "date": str(schedule_entry.get("date") or "")[:10],
            "home_team_id": home_id,
            "away_team_id": away_id,
            "home_team_name": TEAM_FULL_NAMES.get(home_id, home_id),
            "away_team_name": TEAM_FULL_NAMES.get(away_id, away_id),
            "home_score": int(home_score),
            "away_score": int(away_score),
            "winner_team_id": winner_team_id,
            "user_team_id": tid,
            "user_team_record_after_game": user_record_after,
            "opponent_record_after_game": opp_record_after,
            "boxscore_lines": {
                "quarters": boxscore_quarters,
                "note": None if boxscore_quarters else "Quarter split unavailable in current GameResultV2 source",
            },
        },
        "tabs": {
            "default": "gamecast",
            "enabled": ["gamecast", "boxscore", "teamstats"],
            "disabled": [],
        },
        "boxscore": boxscore,
        "teamstats": teamstats,
        "leaders": leaders,
        "gamecast": {
            "win_probability": {
                "model": "heuristic_v1",
                "series": win_probability_series,
                "inputs": {
                    "score_diff": True,
                    "elapsed_seconds": True,
                    "strength_gap_source": "pre_game_win_pct",
                },
                "confidence": "experimental",
            },
            "game_flow": {
                "series": game_flow_series,
                "source": "replay_events",
            },
            "availability": {
                "replay_events_present": bool(replay_events),
                "fallback_used": not bool(replay_events),
            },
        },
        "matchups": {
            "season_record": {
                "user_team_wins": int(season_wins),
                "user_team_losses": int(season_losses),
            },
            "completed": completed,
            "upcoming": upcoming,
        },
    }


# -------------------------------------------------------------------------
# STATE 요약 조회 API (프론트/디버그용)
# -------------------------------------------------------------------------

@router.get("/api/state/summary")
async def state_summary():
    workflow_state: Dict[str, Any] = state.export_workflow_state()
    for k in (
        # Trade assets ledger (DB SSOT)
        "draft_picks",
        "swap_rights",
        "fixed_assets",
        # Transactions ledger (DB SSOT)
        "transactions",
        # Contracts/FA ledger (DB SSOT)
        "contracts",
        "player_contracts",
        "active_contract_id_by_player",
        "free_agents",
        # GM profiles (DB SSOT)
        "gm_profiles",
    ):
        workflow_state.pop(k, None)

    # 2) DB snapshot (SSOT). Fail loud on DB path/schema issues.
    db_path = state.get_db_path()
    try:
        with LeagueRepo(db_path) as repo:
            # DB schema is guaranteed during server startup (state.startup_init_state()).
            db_snapshot: Dict[str, Any] = {
                "ok": True,
                "db_path": db_path,
                "trade_assets": repo.get_trade_assets_snapshot(),
                "contracts_ledger": repo.get_contract_ledger_snapshot(),
                "transactions": repo.list_transactions(limit=200),
                "gm_profiles": repo.get_all_gm_profiles(),
            }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "DB snapshot failed",
                "db_path": db_path,
                "error": str(exc),
            },
        )

    return {
        "workflow_state": workflow_state,
        "db_snapshot": db_snapshot,
    }
