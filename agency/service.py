from __future__ import annotations

"""DB-backed orchestration for the agency subsystem.

This module wires together:
- DB reads (players/roster + existing agency state)
- expectation computation (role/leverage/expected minutes)
- monthly tick logic (tick.apply_monthly_player_tick)
- DB writes (upsert state + append events)

It is designed to be called from a checkpoint trigger (see agency/checkpoints.py)
when a month finishes.
"""

import calendar
import logging
from typing import Any, Dict, Mapping, Optional, Tuple

from league_repo import LeagueRepo

from contract_codec import derive_contract_end_season_id

from .config import AgencyConfig, DEFAULT_CONFIG
from .escalation import stage_fields
from .help_needs import compute_team_need_tags
from .expectations import compute_expectations_for_league
from .locker_room import build_locker_room_meeting_event, compute_contagion_deltas, compute_team_temperature
from .behavior_profile import compute_behavior_profile
from .stance import apply_stance_deltas, stance_deltas_on_promise_outcome
from .promises import DEFAULT_PROMISE_CONFIG, PromiseEvaluationContext, add_months, due_month_from_now, evaluate_promise
from .expectations_month import compute_month_expectations
from .month_context import (
    PlayerMonthSplit,
    TeamSlice,
    build_split_summary,
    finalize_player_month_split,
    players_by_team_from_splits,
)
from .repo import (
    get_player_agency_states,
    insert_agency_events,
    list_active_promises_due,
    table_exists,
    update_promises,
    upsert_player_agency_states,
)
from .team_transition import apply_team_transition
from .tick import apply_monthly_player_tick
from .responses import DEFAULT_RESPONSE_CONFIG, apply_user_response
from .trade_offer_grievance import (
    PlayerSnapshot,
    TradeOfferGrievanceConfig as TradeOfferGrievanceRuntimeConfig,
    compute_trade_offer_grievances,
)
from .types import MonthlyPlayerInputs
from .utils import clamp01, date_add_days, extract_mental_from_attrs, json_dumps, json_loads, make_event_id, norm_date_iso, norm_month_key, safe_float, safe_int


logger = logging.getLogger(__name__)


def apply_trade_offer_grievances(
    *,
    db_path: str,
    season_year: int,
    now_date_iso: str,
    proposer_team_id: str,
    outgoing_player_ids: list[str],
    incoming_player_ids: list[str],
    trigger_source: str,
    session_id: Optional[str] = None,
    source_path: Optional[str] = None,
    cfg: AgencyConfig = DEFAULT_CONFIG,
) -> Dict[str, Any]:
    """Apply trade-offer grievance effects to agency SSOT.

    This function orchestrates DB I/O only. Core grievance computation stays in
    agency.trade_offer_grievance (pure logic).
    """

    team_id = str(proposer_team_id or "").upper()
    date_iso = str(now_date_iso or "")[:10]
    out_ids = [str(x) for x in (outgoing_player_ids or []) if str(x)]
    in_ids = [str(x) for x in (incoming_player_ids or []) if str(x)]

    if not team_id:
        return {"ok": False, "reason": "missing_team_id"}
    if not out_ids and not in_ids:
        return {"ok": True, "applied": False, "reason": "no_candidate_players", "team_id": team_id}

    with LeagueRepo(db_path) as repo:
        repo.init_db()

        # Active roster snapshots for proposer team (incumbent pool)
        rows = repo._conn.execute(
            """
            SELECT
                p.player_id,
                r.team_id,
                p.pos,
                p.ovr,
                p.attrs_json,
                r.salary_amount
            FROM roster r
            JOIN players p ON p.player_id = r.player_id
            WHERE r.status='active' AND UPPER(r.team_id)=?
            ORDER BY p.player_id ASC;
            """,
            (team_id,),
        ).fetchall()

        roster_rows: list[Dict[str, Any]] = []
        roster_ids: list[str] = []
        for r in rows:
            pid = str(r[0] or "")
            if not pid:
                continue
            roster_ids.append(pid)
            roster_rows.append(
                {
                    "player_id": pid,
                    "team_id": str(r[1] or "").upper(),
                    "pos": str(r[2] or "").upper(),
                    "ovr": safe_int(r[3], 0),
                    "attrs_json": r[4],
                    "salary_amount": safe_float(r[5], 0.0),
                }
            )

        if not roster_rows:
            return {"ok": True, "applied": False, "reason": "no_active_roster_for_team", "team_id": team_id}

        # Incoming targets may be outside proposer roster; fetch minimal info.
        uniq_in = sorted({x for x in in_ids if x})
        incoming_rows: Dict[str, Dict[str, Any]] = {}
        if uniq_in:
            placeholders = ",".join(["?"] * len(uniq_in))
            inq = repo._conn.execute(
                f"""
                SELECT p.player_id, p.pos, p.ovr, p.attrs_json, r.team_id
                FROM players p
                LEFT JOIN roster r ON r.player_id = p.player_id
                WHERE p.player_id IN ({placeholders});
                """,
                [*uniq_in],
            ).fetchall()
            for r in inq:
                pid = str(r[0] or "")
                if not pid:
                    continue
                incoming_rows[pid] = {
                    "player_id": pid,
                    "pos": str(r[1] or "").upper(),
                    "ovr": safe_int(r[2], 0),
                    "attrs_json": r[3],
                    "team_id": str(r[4] or "").upper(),
                }

        # Expectations -> role_bucket/leverage for proposer roster players.
        exp = compute_expectations_for_league(roster_rows, config=cfg.expectations)

        # Agency state for proposer roster (targeted + incumbent candidates)
        with repo.transaction() as cur:
            states = get_player_agency_states(cur, roster_ids)

        players_by_id: Dict[str, PlayerSnapshot] = {}

        for rr in roster_rows:
            pid = str(rr["player_id"])
            st = states.get(pid) or {}
            ex = exp.get(pid)
            mental = extract_mental_from_attrs(rr.get("attrs_json"), keys=cfg.mental_attr_keys)

            role_bucket = str((st.get("role_bucket") if isinstance(st, Mapping) else None) or (getattr(ex, "role_bucket", "UNKNOWN")))
            leverage = float(clamp01((st.get("leverage") if isinstance(st, Mapping) else None) if st else getattr(ex, "leverage", 0.0)))

            players_by_id[pid] = PlayerSnapshot(
                player_id=pid,
                team_id=team_id,
                pos=str(rr.get("pos") or "").upper(),
                ovr=int(safe_int(rr.get("ovr"), 0)),
                mental=mental,
                role_bucket=role_bucket,
                leverage=leverage,
                trade_request_level=int(safe_int(st.get("trade_request_level") if isinstance(st, Mapping) else 0, 0)),
                team_frustration=float(clamp01(safe_float(st.get("team_frustration") if isinstance(st, Mapping) else 0.0, 0.0))),
                role_frustration=float(clamp01(safe_float(st.get("role_frustration") if isinstance(st, Mapping) else 0.0, 0.0))),
            )

        for pid in uniq_in:
            if pid in players_by_id:
                continue
            rr = incoming_rows.get(pid)
            if not rr:
                continue
            mental = extract_mental_from_attrs(rr.get("attrs_json"), keys=cfg.mental_attr_keys)
            players_by_id[pid] = PlayerSnapshot(
                player_id=pid,
                team_id=str(rr.get("team_id") or "").upper(),
                pos=str(rr.get("pos") or "").upper(),
                ovr=int(safe_int(rr.get("ovr"), 0)),
                mental=mental,
            )

        tuning = cfg.trade_offer_grievance
        evt = cfg.event_types if isinstance(cfg.event_types, Mapping) else {}
        runtime_cfg = TradeOfferGrievanceRuntimeConfig(
            public_targeted_delta_base=float(getattr(tuning, "public_targeted_delta_base", 0.055)),
            public_targeted_delta_mental_weight=float(getattr(tuning, "public_targeted_delta_mental_weight", 0.055)),
            public_targeted_delta_status_weight=float(getattr(tuning, "public_targeted_delta_status_weight", 0.040)),
            public_targeted_delta_context_weight=float(getattr(tuning, "public_targeted_delta_context_weight", 0.020)),
            public_targeted_delta_resilience_weight=float(getattr(tuning, "public_targeted_delta_resilience_weight", 0.040)),
            public_targeted_delta_min=float(getattr(tuning, "public_targeted_delta_min", 0.025)),
            public_targeted_delta_max=float(getattr(tuning, "public_targeted_delta_max", 0.140)),
            leaked_targeted_delta_base=float(getattr(tuning, "leaked_targeted_delta_base", 0.12)),
            leaked_targeted_delta_mental_weight=float(getattr(tuning, "leaked_targeted_delta_mental_weight", 0.10)),
            leaked_targeted_delta_status_weight=float(getattr(tuning, "leaked_targeted_delta_status_weight", 0.08)),
            leaked_targeted_delta_context_weight=float(getattr(tuning, "leaked_targeted_delta_context_weight", 0.05)),
            leaked_targeted_delta_resilience_weight=float(getattr(tuning, "leaked_targeted_delta_resilience_weight", 0.06)),
            leaked_targeted_delta_min=float(getattr(tuning, "leaked_targeted_delta_min", 0.08)),
            leaked_targeted_delta_max=float(getattr(tuning, "leaked_targeted_delta_max", 0.30)),
            trade_request_level_max=int(getattr(tuning, "trade_request_level_max", 2)),
            leaked_targeted_active_request_dampen=float(getattr(tuning, "leaked_targeted_active_request_dampen", 0.45)),
            same_pos_base_prob=float(getattr(tuning, "same_pos_base_prob", 0.18)),
            same_pos_delta_base=float(getattr(tuning, "same_pos_delta_base", 0.03)),
            same_pos_delta_scale=float(getattr(tuning, "same_pos_delta_scale", 0.08)),
            same_pos_min_leverage=float(getattr(tuning, "same_pos_min_leverage", 0.28)),
            same_pos_max_ovr_gap=int(getattr(tuning, "same_pos_max_ovr_gap", 3)),
            same_pos_max_role_tier_gap=int(getattr(tuning, "same_pos_max_role_tier_gap", 2)),
            event_type_targeted_public=str(evt.get("trade_targeted_offer_public") or "TRADE_TARGETED_OFFER_PUBLIC").upper(),
            event_type_targeted_leaked=str(evt.get("trade_targeted_offer_leaked") or "TRADE_TARGETED_OFFER_LEAKED").upper(),
            event_type_same_pos_recruit=str(evt.get("same_pos_recruit_attempt") or "SAME_POS_RECRUIT_ATTEMPT").upper(),
        )

        result = compute_trade_offer_grievances(
            proposer_team_id=team_id,
            outgoing_player_ids=out_ids,
            incoming_player_ids=in_ids,
            players_by_id=players_by_id,
            season_year=int(season_year),
            now_date_iso=date_iso,
            trigger_source=str(trigger_source or "PUBLIC_OFFER"),
            session_id=session_id,
            cfg=runtime_cfg,
        )

        if not result.updates and not result.events:
            return {
                "ok": True,
                "applied": False,
                "team_id": team_id,
                "trigger_source": str(trigger_source or "PUBLIC_OFFER").upper(),
                "reason": "no_effect",
                "skipped": result.skipped,
                "meta": dict(result.meta or {}),
                "source_path": str(source_path) if source_path else None,
            }

        # Merge updated values back into SSOT rows.
        states_upsert: Dict[str, Dict[str, Any]] = {}
        roster_by_id = {str(r["player_id"]): r for r in roster_rows}
        for u in result.updates:
            pid = str(u.player_id)
            base = dict(states.get(pid) or {})
            if not base:
                rr = roster_by_id.get(pid) or {}
                ex = exp.get(pid)
                base = {
                    "player_id": pid,
                    "team_id": team_id,
                    "season_year": int(season_year),
                    "role_bucket": str(getattr(ex, "role_bucket", "UNKNOWN")),
                    "leverage": float(clamp01(getattr(ex, "leverage", 0.0))),
                    "minutes_expected_mpg": float(getattr(ex, "expected_mpg", 0.0) or 0.0),
                    "minutes_actual_mpg": 0.0,
                    "trust": 0.5,
                    "trade_request_level": 0,
                    "context": {},
                    "team_frustration": float(clamp01(safe_float(rr.get("team_frustration"), 0.0))),
                    "role_frustration": float(clamp01(safe_float(rr.get("role_frustration"), 0.0))),
                }

            base["team_frustration"] = float(clamp01(u.team_frustration))
            base["role_frustration"] = float(clamp01(u.role_frustration))

            ctx = base.get("context") if isinstance(base.get("context"), dict) else {}
            tg = ctx.get("trade_grievance") if isinstance(ctx.get("trade_grievance"), dict) else {}
            tg["last_trigger_date"] = date_iso
            tg["last_trigger_source"] = str(trigger_source or "PUBLIC_OFFER").upper()
            tg["last_session_id"] = str(session_id) if session_id else None
            tg["last_team_frustration_delta"] = float(u.team_frustration_delta)
            tg["last_role_frustration_delta"] = float(u.role_frustration_delta)
            ctx["trade_grievance"] = tg
            base["context"] = ctx

            states_upsert[pid] = base

        ev_rows: list[Dict[str, Any]] = []
        for ev in result.events:
            ev_rows.append(
                {
                    "event_id": str(ev.event_id),
                    "player_id": str(ev.player_id),
                    "team_id": str(ev.team_id).upper(),
                    "season_year": int(ev.season_year),
                    "date": str(ev.date)[:10],
                    "event_type": str(ev.event_type).upper(),
                    "severity": float(clamp01(ev.severity)),
                    "payload": dict(ev.payload or {}),
                }
            )

        with repo.transaction() as cur:
            upsert_player_agency_states(cur, states_upsert, now=str(now_date_iso))
            insert_agency_events(cur, ev_rows, now=str(now_date_iso))

    return {
        "ok": True,
        "applied": True,
        "team_id": team_id,
        "trigger_source": str(trigger_source or "PUBLIC_OFFER").upper(),
        "updates": [
            {
                "player_id": str(u.player_id),
                "team_frustration_delta": float(u.team_frustration_delta),
                "role_frustration_delta": float(u.role_frustration_delta),
            }
            for u in result.updates
        ],
        "event_ids": [str(ev.event_id) for ev in result.events],
        "skipped": result.skipped,
        "meta": dict(result.meta or {}),
        "source_path": str(source_path) if source_path else None,
    }


def _meta_key_for_month(month_key: str) -> str:
    return f"nba_agency_tick_done_{str(month_key)}"


def _best_effort_injury_status_by_pid(repo: LeagueRepo, player_ids: list[str]) -> Dict[str, str]:
    """Return mapping pid -> injury status (HEALTHY/OUT/RETURNING).

    This is best-effort: if injury tables are missing, returns empty.
    """
    if not player_ids:
        return {}

    try:
        placeholders = ",".join(["?"] * len(player_ids))
        rows = repo._conn.execute(
            f"""
            SELECT player_id, status
            FROM player_injury_state
            WHERE player_id IN ({placeholders});
            """,
            [*player_ids],
        ).fetchall()
        out: Dict[str, str] = {}
        for pid, status in rows:
            pid_s = str(pid)
            if not pid_s:
                continue
            out[pid_s] = str(status or "HEALTHY").upper()
        return out
    except Exception:
        return {}


def _typed_splits(month_splits_by_player: Optional[Mapping[str, Any]]) -> Dict[str, PlayerMonthSplit]:
    out: Dict[str, PlayerMonthSplit] = {}
    for pid, sp in (month_splits_by_player or {}).items():
        if isinstance(sp, PlayerMonthSplit):
            out[str(pid)] = sp
    return out


def _slice_minutes_games(split: Optional[PlayerMonthSplit], team_id: str) -> Tuple[float, int]:
    """Return (minutes, games_played) for player on a specific team in the month."""
    if split is None:
        return (0.0, 0)
    tid = str(team_id or "").upper()
    if not tid:
        return (0.0, 0)
    sl = (split.teams or {}).get(tid)
    if sl is None:
        return (0.0, 0)
    return (float(sl.minutes), int(sl.games_played))


def _slice_role_usage(split: Optional[PlayerMonthSplit], team_id: str) -> Tuple[int, int, float]:
    """Return (starts, closes, usage_est) for player on a specific team in the month."""
    if split is None:
        return (0, 0, 0.0)
    tid = str(team_id or "").upper()
    if not tid:
        return (0, 0, 0.0)
    sl = (split.teams or {}).get(tid)
    if sl is None:
        return (0, 0, 0.0)
    # TeamSlice in month_context carries these fields in v2; default defensively.
    starts = int(getattr(sl, "games_started", 0) or 0)
    closes = int(getattr(sl, "games_closed", 0) or 0)
    usage_est = float(getattr(sl, "usage_est", 0.0) or 0.0)
    return (starts, closes, usage_est)


def _month_start_end_dates(month_key: str) -> Tuple[str, str]:
    """Return (month_start_date_iso, month_end_date_iso) for a YYYY-MM key."""
    mk = str(month_key or "")
    try:
        y_s, m_s = mk.split("-", 1)
        y = int(y_s)
        m = int(m_s)
        last = int(calendar.monthrange(y, m)[1])
        return (f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last:02d}")
    except Exception:
        # Defensive fallback; caller should still handle invalid mk.
        return (f"{mk}-01", f"{mk}-28")


def _team_move_events_by_pid_since(
    repo: LeagueRepo,
    *,
    player_ids: list[str],
    since_date_iso: str,
    limit: int = 20000,
) -> Dict[str, list[Tuple[str, str, str]]]:
    """Collect team-change events (trade/sign/release) from SSOT transactions_log.

    Returns mapping pid -> list[(event_date_iso, from_team, to_team)] sorted DESC by event_date_iso.
    """
    pids = [str(pid) for pid in (player_ids or []) if str(pid)]
    if not pids:
        return {}

    pid_set = set(pids)
    since_d = str(since_date_iso or "")[:10]
    if not since_d:
        return {}

    # NOTE: transactions_log.tx_type is the SSOT discriminator.
    # We only need types that can change roster.team_id.
    tx_types = [
        "trade",
        "TRADE",
        "SIGN_FREE_AGENT",
        "RELEASE_TO_FA",
        "WAIVE_TO_FA",
        "STRETCH_TO_FA",
        # legacy/dev spellings (safety)
        "signing",
        "release_to_free_agency",
        "waive_to_fa",
        "stretch_to_fa",
    ]
    placeholders = ",".join(["?"] * len(tx_types))

    try:
        rows = repo._conn.execute(
            f"""
            SELECT payload_json
            FROM transactions_log
            WHERE tx_date IS NOT NULL
              AND substr(tx_date, 1, 10) >= ?
              AND tx_type IN ({placeholders})
            ORDER BY COALESCE(tx_date,'') DESC, created_at DESC
            LIMIT ?;
            """,
            [since_d, *tx_types, max(1, int(limit))],
        ).fetchall()
    except Exception:
        return {}

    out: Dict[str, list[Tuple[str, str, str]]] = {}

    def _tx_date(tx: Mapping[str, Any]) -> Optional[str]:
        v = tx.get("action_date")
        if v is None:
            v = tx.get("date")
        if v is None:
            v = tx.get("trade_date")
        if v is None:
            return None
        s = str(v).strip()
        if not s or len(s) < 10:
            return None
        if str(s)[:10] < since_d:
            return None
        return s

    for (payload_json,) in rows:
        tx = json_loads(payload_json, default=None)
        if not isinstance(tx, Mapping):
            continue
        tdate = _tx_date(tx)
        if tdate is None:
            continue

        ttype = str(tx.get("type") or tx.get("tx_type") or "")
        if str(ttype).lower() == "trade":
            pm = tx.get("player_moves")
            if not isinstance(pm, list):
                continue
            for m in pm:
                if not isinstance(m, Mapping):
                    continue
                pid = str(m.get("player_id") or "")
                if not pid or pid not in pid_set:
                    continue
                ft = str(m.get("from_team") or "").upper()
                tt = str(m.get("to_team") or "").upper()
                if not ft or not tt:
                    continue
                out.setdefault(pid, []).append((str(tdate), ft, tt))
            continue

        # Contract-style tx: top-level player_id + from_team/to_team
        pid = tx.get("player_id")
        if pid is None:
            continue
        pid_s = str(pid)
        if not pid_s or pid_s not in pid_set:
            continue
        ft = str(tx.get("from_team") or "").upper()
        tt = str(tx.get("to_team") or "").upper()
        if not ft or not tt:
            continue
        out.setdefault(pid_s, []).append((str(tdate), ft, tt))

    # Sort desc for stable reverse application.
    for pid in list(out.keys()):
        out[pid].sort(key=lambda e: str(e[0]), reverse=True)
    return out


def _teams_as_of_dates(
    *,
    roster_team_by_pid: Mapping[str, str],
    move_events_by_pid: Mapping[str, list[Tuple[str, str, str]]],
    target_dates_desc: list[str],
) -> Dict[str, Dict[str, str]]:
    """Compute team_id as-of end-of-day for each target date, per player."""

    def _reverse_team(cur_team: str, ft: str, tt: str) -> str:
        cur = str(cur_team or "").upper()
        if not cur:
            return cur
        if str(tt or "").upper() == cur:
            return str(ft or "").upper()
        return cur

    out: Dict[str, Dict[str, str]] = {}
    for pid, team_now in (roster_team_by_pid or {}).items():
        cur_team = str(team_now or "").upper()
        ev = list(move_events_by_pid.get(pid) or [])
        i = 0
        by_date: Dict[str, str] = {}
        for d in target_dates_desc:
            cutoff = f"{str(d)[:10]}T23:59:59Z"
            while i < len(ev) and str(ev[i][0]) > cutoff:
                _dt, ft, tt = ev[i]
                cur_team = _reverse_team(cur_team, ft, tt)
                i += 1
            by_date[str(d)[:10]] = str(cur_team)
        out[str(pid)] = by_date
    return out


def apply_monthly_agency_tick(
    *,
    db_path: str,
    season_year: int,
    month_key: str,
    month_splits_by_player: Optional[Mapping[str, Any]] = None,
    # Backward-compatible fallback (deprecated): if month_splits are not provided
    # callers may provide aggregated per-player minutes/games.
    minutes_by_player: Optional[Mapping[str, float]] = None,
    games_by_player: Optional[Mapping[str, int]] = None,
    team_win_pct_by_team: Optional[Mapping[str, float]] = None,
    # date_iso -> {team_id: games_count} for the processed month.
    # Used to synthesize DNP presence for players not appearing in boxscores.
    team_games_by_date: Optional[Mapping[str, Mapping[str, int]]] = None,
    now_iso: str,
    cfg: AgencyConfig = DEFAULT_CONFIG,
) -> Dict[str, Any]:
    """Apply a league-wide monthly agency tick.

    Idempotent via meta key `nba_agency_tick_done_{month_key}`.

    Args:
        db_path: SQLite path
        season_year: current season year
        month_key: YYYY-MM (the month being processed)
        minutes_by_player: total minutes played in that month (regular-season finals)
        games_by_player: games played in that month (optional but recommended)
        team_win_pct_by_team: team win% for that month (optional)
        now_iso: UTC-like timestamp string for created_at/updated_at/meta
        cfg: AgencyConfig
    """

    sy = int(season_year)
    mk = str(month_key)
    meta_key = _meta_key_for_month(mk)

    minutes_map = {str(k): float(v) for k, v in (minutes_by_player or {}).items() if str(k)} if minutes_by_player else {}
    games_map = {str(k): int(v) for k, v in (games_by_player or {}).items() if str(k)} if games_by_player else {}
    team_win_map = {str(k).upper(): float(v) for k, v in (team_win_pct_by_team or {}).items() if str(k)}

    splits_by_pid = _typed_splits(month_splits_by_player)
    
    with LeagueRepo(db_path) as repo:
        repo.init_db()

        # Idempotency
        row = repo._conn.execute("SELECT value FROM meta WHERE key=?;", (meta_key,)).fetchone()
        if row and str(row[0]) == "1":
            return {"ok": True, "skipped": True, "reason": "already_done", "month": mk, "meta_key": meta_key}

        # Ensure agency tables exist. Fail loud with a helpful message.
        try:
            repo._conn.execute("SELECT 1 FROM player_agency_state LIMIT 1;")
        except Exception as exc:
            raise RuntimeError(
                "Agency schema is missing. Did you add db_schema.agency to db_schema.init.DEFAULT_MODULES?"
            ) from exc

        # Load roster + player data.
        rows = repo._conn.execute(
            """
            SELECT
                p.player_id,
                r.team_id,
                r.salary_amount,
                p.ovr,
                p.age,
                p.attrs_json,
                r.updated_at
            FROM players p
            JOIN roster r ON r.player_id = p.player_id
            WHERE r.status='active'
            ORDER BY r.team_id ASC, p.player_id ASC;
            """
        ).fetchall()

        roster_rows: list[Dict[str, Any]] = []
        player_ids: list[str] = []
        for r in rows:
            pid = str(r[0] or "")
            tid = str(r[1] or "").upper()
            if not pid or not tid:
                continue
            player_ids.append(pid)
            roster_rows.append(
                {
                    "player_id": pid,
                    "team_id": tid,
                    "salary_amount": safe_float(r[2], 0.0),
                    "ovr": safe_int(r[3], 0),
                    "age": safe_int(r[4], 0),
                    "attrs_json": r[5],
                    "roster_updated_at": r[6],
                }
            )

        if not roster_rows:
            # Still write meta key to avoid reprocessing empty months.
            with repo.transaction() as cur:
                cur.execute(
                    "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value;",
                    (meta_key, "1"),
                )
            return {"ok": True, "skipped": True, "reason": "no_active_roster", "month": mk, "meta_key": meta_key}

        # Load previous agency states early (used for defensive fallbacks in month attribution).
        with repo.transaction() as cur:
            prev_states = get_player_agency_states(cur, player_ids)

        # ------------------------------------------------------------------
        # v2 SSOT context (inputs only; tick v1 ignores, but we persist for v2+)
        # ------------------------------------------------------------------

        # Fatigue state (best-effort; missing table => empty).
        fatigue_by_pid: Dict[str, Dict[str, Any]] = {}
        try:
            placeholders = ",".join(["?"] * len(player_ids))
            rows_f = repo._conn.execute(
                f"""
                SELECT player_id, st, lt, last_date
                FROM player_fatigue_state
                WHERE player_id IN ({placeholders});
                """,
                [*player_ids],
            ).fetchall()
            for pid_f, st_f, lt_f, last_date_f in rows_f:
                pid_s = str(pid_f)
                if not pid_s:
                    continue
                fatigue_by_pid[pid_s] = {
                    "st": safe_float(st_f, 0.0),
                    "lt": safe_float(lt_f, 0.0),
                    "last_date": norm_date_iso(last_date_f),
                }
        except Exception:
            fatigue_by_pid = {}

        # Contract state (SSOT).
        active_contract_id_by_pid: Dict[str, str] = repo.get_active_contract_id_by_player()
        contracts_by_id: Dict[str, Dict[str, Any]] = repo.get_contracts_map(active_only=False)

        # Team strategy (SSOT). Missing rows fall back to BALANCED.
        try:
            team_strategy_by_team: Dict[str, str] = repo.get_team_strategy_map(season_year=int(sy))
        except Exception:
            team_strategy_by_team = {}

        # Expectations for role/leverage/expected minutes.
        expectations_current = compute_expectations_for_league(roster_rows, config=cfg.expectations)

        # ------------------------------------------------------------------
        # Month context helpers
        # ------------------------------------------------------------------
        month_start_date, month_end_date = _month_start_end_dates(mk)

        # Normalize schedule-derived team games map (optional but recommended).
        team_games_map: Dict[str, Dict[str, int]] = {}
        for d_raw, by_team_raw in (team_games_by_date or {}).items():
            d = norm_date_iso(d_raw) or str(d_raw or "")[:10]
            if not d or not str(d).startswith(str(mk)):
                continue
            if not isinstance(by_team_raw, Mapping):
                continue
            for tid_raw, n_raw in by_team_raw.items():
                tid = str(tid_raw or "").upper()
                if not tid:
                    continue
                try:
                    n = int(n_raw)
                except Exception:
                    continue
                if n <= 0:
                    continue
                team_games_map.setdefault(str(d), {})[tid] = int(n)

        month_game_dates = sorted(team_games_map.keys())

        # Team at end-of-month (calendar) derived from SSOT transactions.
        roster_team_by_pid_now: Dict[str, str] = {str(rr["player_id"]): str(rr["team_id"]).upper() for rr in roster_rows}

        # Target dates: month game dates (for DNP synthesis) + month end (for promises).
        target_dates = list(month_game_dates)
        if month_end_date and month_end_date not in target_dates:
            target_dates.append(month_end_date)
        target_dates_desc = sorted({str(d)[:10] for d in target_dates if str(d)[:10]}, reverse=True)

        move_events_by_pid = _team_move_events_by_pid_since(
            repo,
            player_ids=player_ids,
            since_date_iso=month_start_date,
        )
        teams_asof_by_pid = _teams_as_of_dates(
            roster_team_by_pid=roster_team_by_pid_now,
            move_events_by_pid=move_events_by_pid,
            target_dates_desc=target_dates_desc,
        )

        # ------------------------------------------------------------------
        # Injury context (month-based, SSOT = injury_events)
        # ------------------------------------------------------------------
        # Treat month_end as inclusive for SSOT windows by querying with an exclusive end.
        month_end_excl = date_add_days(month_end_date, 1) if month_end_date else str(month_end_date or '')[:10]

        injury_events_available = False
        injury_windows_by_pid: Dict[str, list[Dict[str, str]]] = {}
        injury_status_eom_by_pid: Dict[str, str] = {}

        try:
            repo._conn.execute("SELECT 1 FROM injury_events LIMIT 1;").fetchone()
            injury_events_available = True
        except Exception:
            injury_events_available = False

        if injury_events_available:
            try:
                placeholders = ",".join(["?"] * len(player_ids))
                rows_inj = repo._conn.execute(
                    f"""
                    SELECT player_id, date, out_until_date, returning_until_date
                    FROM injury_events
                    WHERE player_id IN ({placeholders})
                      AND date < ?
                      AND returning_until_date > ?
                    ORDER BY date ASC;
                    """,
                    [*player_ids, str(month_end_excl)[:10], str(month_start_date)[:10]],
                ).fetchall()

                for pid_i, date_i, out_until_i, ret_until_i in rows_inj:
                    pid_s = str(pid_i or "")
                    sd = norm_date_iso(date_i) or str(date_i or "")[:10]
                    ou = norm_date_iso(out_until_i) or str(out_until_i or "")[:10]
                    ru = norm_date_iso(ret_until_i) or str(ret_until_i or "")[:10]
                    if not pid_s or not sd or not ou or not ru:
                        continue
                    injury_windows_by_pid.setdefault(pid_s, []).append(
                        {"start": str(sd), "out_until": str(ou), "returning_until": str(ru)}
                    )
            except Exception:
                # If the injury schema exists but querying failed (corruption/migration),
                # fall back to current injury state to avoid treating everyone as healthy.
                injury_events_available = False
                injury_windows_by_pid = {}

        def _injury_status_on_date(pid: str, on_date_iso: str) -> str:
            """Return OUT/RETURNING/HEALTHY for the given player on the given date.

            Derived from SSOT injury_events windows for the processed month.
            """
            d = str(on_date_iso)[:10]
            win = injury_windows_by_pid.get(str(pid)) or []
            if not win:
                return "HEALTHY"
            # OUT has priority over RETURNING.
            for w in win:
                s = str(w.get("start") or "")[:10]
                ou = str(w.get("out_until") or "")[:10]
                if s and ou and s <= d < ou:
                    return "OUT"
            for w in win:
                ou = str(w.get("out_until") or "")[:10]
                ru = str(w.get("returning_until") or "")[:10]
                if ou and ru and ou <= d < ru:
                    return "RETURNING"
            return "HEALTHY"

        if injury_events_available:
            injury_status_eom_by_pid = {pid: _injury_status_on_date(pid, month_end_date) for pid in player_ids}
        else:
            # Best-effort fallback (may be inaccurate for past months if injuries resolved since).
            injury_status_eom_by_pid = _best_effort_injury_status_by_pid(repo, player_ids)

        out_games_by_pid_by_team: Dict[str, Dict[str, int]] = {}
        returning_games_by_pid_by_team: Dict[str, Dict[str, int]] = {}


        # Schedule presence: how many team games each player could have appeared in during the month,
        # per team, based on SSOT transactions + the schedule-derived team_games_by_date.
        # This is used to compute DNP frequency pressure while keeping MPG = minutes/games_played.
        schedule_games_by_pid_by_team: Dict[str, Dict[str, int]] = {}
        if team_games_map and month_game_dates:
            for pid in player_ids:
                by_date = teams_asof_by_pid.get(pid) or {}
                by_team: Dict[str, int] = schedule_games_by_pid_by_team.setdefault(pid, {})

                # Injury-adjusted counts are only computed when we have SSOT injury windows.
                win = injury_windows_by_pid.get(pid) or []
                out_by_team: Optional[Dict[str, int]] = None
                ret_by_team: Optional[Dict[str, int]] = None
                if win:
                    out_by_team = out_games_by_pid_by_team.setdefault(pid, {})
                    ret_by_team = returning_games_by_pid_by_team.setdefault(pid, {})
                
                for d in month_game_dates:
                    tid = str(by_date.get(d) or roster_team_by_pid_now.get(pid) or "").upper()
                    if not tid or tid == "FA":
                        continue
                    n = int((team_games_map.get(d) or {}).get(tid, 0) or 0)
                    if n <= 0:
                        continue
                    by_team[tid] = int(by_team.get(tid, 0) + n)

                    if out_by_team is not None and ret_by_team is not None:
                        st_inj = _injury_status_on_date(pid, d)
                        if st_inj == "OUT":
                            out_by_team[tid] = int(out_by_team.get(tid, 0) + n)
                        elif st_inj == "RETURNING":
                            ret_by_team[tid] = int(ret_by_team.get(tid, 0) + n)

        roster_updated_date_by_pid: Dict[str, str] = {}
        for rr in roster_rows:
            pid = str(rr.get("player_id") or "")
            if not pid:
                continue
            ud = norm_date_iso(rr.get("roster_updated_at")) or str(rr.get("roster_updated_at") or "")[:10]
            if ud:
                roster_updated_date_by_pid[pid] = str(ud)

        # Derive end-of-month team_id per player (used for promise evaluation).
        team_eom_by_pid: Dict[str, str] = {}
        for pid in player_ids:
            team_eom = str((teams_asof_by_pid.get(pid) or {}).get(month_end_date) or roster_team_by_pid_now.get(pid) or "").upper()

            # Defensive fallback: if there are *no* move events since month start and roster was updated
            # after month end, the player likely joined after the processed month.
            # In that case, prefer the previous agency_state team to avoid false promise fulfilment.
            if not move_events_by_pid.get(pid):
                upd = roster_updated_date_by_pid.get(pid)
                if upd and str(upd) > str(month_end_date):
                    prev = prev_states.get(pid)
                    prev_tid = str(prev.get("team_id") or "").upper() if isinstance(prev, Mapping) else ""
                    cur_tid = str(roster_team_by_pid_now.get(pid) or "").upper()
                    if prev_tid and cur_tid and prev_tid != cur_tid:
                        team_eom = prev_tid

            if team_eom:
                team_eom_by_pid[pid] = team_eom

        # ------------------------------------------------------------------
        # Synthesize month splits for players who never appear in boxscores.
        # ------------------------------------------------------------------
        if team_games_map and target_dates_desc:
            for pid in player_ids:
                if pid in splits_by_pid:
                    continue

                # If we cannot infer historical team membership and roster was updated after month end,
                # skip DNP synthesis to avoid blaming the current team for a month the player didn't play.
                if not move_events_by_pid.get(pid):
                    upd = roster_updated_date_by_pid.get(pid)
                    if upd and str(upd) > str(month_end_date):
                        prev = prev_states.get(pid)
                        prev_tid = str(prev.get("team_id") or "").upper() if isinstance(prev, Mapping) else ""
                        cur_tid = str(roster_team_by_pid_now.get(pid) or "").upper()
                        if not prev_tid or (prev_tid and cur_tid and prev_tid != cur_tid):
                            continue

                team_slices: Dict[str, TeamSlice] = {}
                by_date = teams_asof_by_pid.get(pid) or {}
                for d in month_game_dates:
                    tid = str(by_date.get(d) or roster_team_by_pid_now.get(pid) or "").upper()
                    if not tid or tid == "FA":
                        continue
                    n = int((team_games_map.get(d) or {}).get(tid, 0) or 0)
                    if n <= 0:
                        continue
                    sl = team_slices.get(tid)
                    if sl is None:
                        sl = TeamSlice(team_id=tid)
                        team_slices[tid] = sl
                    for _ in range(n):
                        sl.add_game(game_date_iso=str(d), minutes=0.0)

                if team_slices:
                    splits_by_pid[pid] = finalize_player_month_split(
                        player_id=pid,
                        month_key=mk,
                        team_slices=team_slices,
                        cfg=cfg.month_context,
                    )

        # Month expectations (role/leverage/expected minutes in the roster context actually experienced).
        # If month split data is unavailable, this will be empty and we will fall back to current expectations.
        month_players_by_team = players_by_team_from_splits(splits_by_pid, min_games_present=1) if splits_by_pid else {}
        with repo.transaction() as cur:
            month_expectations = compute_month_expectations(
                cur,
                players_by_team=month_players_by_team,
                config=cfg.expectations,
            )

        # Team usage totals (derived from month_splits). Used to compute usage_share in inputs.
        usage_total_by_team: Dict[str, float] = {}
        if splits_by_pid:
            for sp in splits_by_pid.values():
                if not sp:
                    continue
                for tid, sl in (sp.teams or {}).items():
                    try:
                        tid_u = str(tid or "").upper()
                        if not tid_u:
                            continue
                        ue = float(getattr(sl, "usage_est", 0.0) or 0.0)
                        if ue <= 0.0:
                            continue
                        usage_total_by_team[tid_u] = float(usage_total_by_team.get(tid_u, 0.0) + ue)
                    except Exception:
                        continue


        # Injury status for the processed month (end-of-month).
        # Preferred SSOT source: injury_events windows.
        # Fallback (older saves): player_injury_state (best-effort).
        injury_status_by_pid = injury_status_eom_by_pid


        # Run tick (pass 1): evaluate each player's month under the team they actually played for.
        # We may later apply a team transition to move the state onto the current roster team.
        states_eval: Dict[str, Dict[str, Any]] = {}
        events: list[Dict[str, Any]] = []
        mental_by_pid: Dict[str, Dict[str, int]] = {}

        eval_team_by_pid: Dict[str, str] = {}
        roster_team_by_pid: Dict[str, str] = {}
        split_summary_by_pid: Dict[str, Dict[str, Any]] = {}
        pre_eval_team_align_meta_by_pid: Dict[str, Dict[str, Any]] = {}

        for rr in roster_rows:
            pid = str(rr["player_id"])
            roster_tid = str(rr["team_id"]).upper()
            roster_team_by_pid[pid] = roster_tid

            prev = prev_states.get(pid)

            split = splits_by_pid.get(pid)
            eval_tid = (split.primary_team if split and split.primary_team else roster_tid) or roster_tid

            # Choose the team context we evaluate this month under.
            # - Prefer primary_team when month attribution exists.
            # - If attribution exists but primary_team is None (small sample), fall back to last/dominant.
            # - If the player has *no* month sample (e.g., joined after month end), anchor to prev_state.team_id
            #   to avoid blaming the current roster team for a month they didn't play.
            eval_tid = None
            if split is not None:
                eval_tid = split.primary_team or split.team_last or split.team_dominant
            if not eval_tid and prev and prev.get("team_id"):
                eval_tid = prev.get("team_id")
            if not eval_tid:
                eval_tid = roster_tid
            
            eval_tid = str(eval_tid).upper() if eval_tid else roster_tid
            eval_team_by_pid[pid] = eval_tid


            if split:
                split_summary_by_pid[pid] = build_split_summary(split)

            # Month expectation for evaluated team (preferred)
            exp_m = month_expectations.get((pid, eval_tid))

            # Fallback: current expectations (useful for players with no appearances in month splits)
            exp_c = expectations_current.get(pid)

            if exp_m is not None:
                role_bucket = str(exp_m.role_bucket)
                leverage = float(exp_m.leverage)
                expected_mpg = float(exp_m.expected_mpg)
            elif exp_c is not None:
                role_bucket = str(exp_c.role_bucket)
                leverage = float(exp_c.leverage)
                expected_mpg = float(exp_c.expected_mpg)
            else:
                role_bucket = "UNKNOWN"
                leverage = 0.0
                expected_mpg = float(cfg.expectations.expected_mpg_by_role.get("UNKNOWN", 12.0))

            mental = extract_mental_from_attrs(rr.get("attrs_json"), keys=cfg.mental_attr_keys)
            mental_by_pid[pid] = dict(mental)

            # Guardrail: players with no processed-month sample (no boxscore rows)
            # should not be treated as a full DNP month. This avoids phantom
            # "last month I didn't play" complaints for players who joined after
            # the month boundary (trade/signing).
            legacy_mins = float(minutes_map.get(pid, 0.0))
            legacy_gp = int(games_map.get(pid, 0))
            has_month_sample = bool(split is not None or legacy_mins > 0.0 or legacy_gp > 0)
            if not has_month_sample:
                if pid not in split_summary_by_pid:
                    split_summary_by_pid[pid] = {
                        "player_id": pid,
                        "month_key": mk,
                        "missing": True,
                        "reason": "NO_MONTH_SAMPLE",
                    }

                # Keep prior state intact; only attach explainability context.
                # (We still allow pass-3 team transitions to reconcile roster changes.)
                base_state: Dict[str, Any]
                if prev and isinstance(prev, Mapping):
                    base_state = dict(prev)
                else:
                    base_state = {
                        "player_id": pid,
                        "team_id": str(eval_tid).upper(),
                        "season_year": int(sy),
                        "role_bucket": str(role_bucket or "UNKNOWN"),
                        "leverage": float(clamp01(leverage)),
                        "minutes_expected_mpg": float(max(0.0, expected_mpg)),
                        "minutes_actual_mpg": 0.0,
                        "minutes_frustration": 0.0,
                        "team_frustration": 0.0,
                        "trust": 0.5,
                        "trade_request_level": 0,
                        "cooldown_minutes_until": None,
                        "cooldown_trade_until": None,
                        "cooldown_help_until": None,
                        "cooldown_contract_until": None,
                        "last_processed_month": None,
                        "context": {},
                    }

                # Normalize identity fields + mark the month as considered.
                base_state["player_id"] = pid
                base_state["team_id"] = str(eval_tid).upper()
                base_state["season_year"] = int(sy)
                base_state["last_processed_month"] = mk

                ctx = base_state.get("context") if isinstance(base_state.get("context"), dict) else {}
                ctx.setdefault("month_attribution", split_summary_by_pid.get(pid) or {})
                ctx.setdefault("month_sample_missing", True)
                ctx.setdefault("evaluation_team_id", eval_tid)
                ctx.setdefault("current_roster_team_id", roster_tid)
                ctx.setdefault("team_end_of_month_id", team_eom_by_pid.get(pid) or str(eval_tid).upper())
                base_state["context"] = ctx

                states_eval[pid] = base_state
                continue
 
            # Month actuals for evaluated team.
            if split is not None:
                mins_eval, gp_eval = _slice_minutes_games(split, eval_tid)
            else:
                # Deprecated fallback (pre-attribution): aggregated per-player minutes/games (may misattribute).
                mins_eval = float(minutes_map.get(pid, 0.0))
                gp_eval = int(games_map.get(pid, 0))


            # Injury-adjusted schedule presence for the evaluated team.
            #
            # We exclude games where the player was OUT from games_possible to avoid
            # treating injury absences as healthy DNPs. RETURNING games remain in
            # the denominator but contribute a partial multiplier to frustration gain.
            gpos_total = int((schedule_games_by_pid_by_team.get(pid) or {}).get(eval_tid, 0) or 0)
            out_g = int((out_games_by_pid_by_team.get(pid) or {}).get(eval_tid, 0) or 0)
            ret_g = int((returning_games_by_pid_by_team.get(pid) or {}).get(eval_tid, 0) or 0)

            gpos_adj = max(0, int(gpos_total) - int(out_g))

            injury_mult: Optional[float] = None
            if gpos_total > 0 and (injury_windows_by_pid.get(pid) or []):
                if gpos_adj <= 0:
                    injury_mult = float(clamp01(cfg.frustration.injury_out_multiplier))
                else:
                    rm = float(clamp01(cfg.frustration.injury_returning_multiplier))
                    rg = max(0, min(int(ret_g), int(gpos_adj)))
                    injury_mult = ((float(gpos_adj - rg) * 1.0) + (float(rg) * rm)) / float(gpos_adj)
                    injury_mult = float(clamp01(injury_mult))

            # v2 derived evidence for evaluated team (starts/closes/usage)
            starts_eval, closes_eval, usage_est_eval = _slice_role_usage(split, eval_tid) if split is not None else (0, 0, 0.0)
            starts_rate = (float(starts_eval) / float(gp_eval)) if int(gp_eval) > 0 else 0.0
            closes_rate = (float(closes_eval) / float(gp_eval)) if int(gp_eval) > 0 else 0.0
            starts_rate = float(clamp01(starts_rate))
            closes_rate = float(clamp01(closes_rate))

            team_usage_total = float(usage_total_by_team.get(str(eval_tid).upper(), 0.0) or 0.0)
            usage_share = (float(usage_est_eval) / float(team_usage_total)) if team_usage_total > 0.0 else 0.0
            usage_share = float(clamp01(usage_share))

            fat = fatigue_by_pid.get(pid) or {}
            fatigue_st = safe_float(fat.get("st"), 0.0) if isinstance(fat, Mapping) else 0.0
            fatigue_lt = safe_float(fat.get("lt"), 0.0) if isinstance(fat, Mapping) else 0.0

            active_cid = active_contract_id_by_pid.get(pid)
            c_row = contracts_by_id.get(str(active_cid)) if active_cid else None
            contract_end_season_id = derive_contract_end_season_id(c_row) if isinstance(c_row, Mapping) else None

            team_strategy = str(team_strategy_by_team.get(str(eval_tid).upper(), "BALANCED") or "BALANCED").upper()

            inp = MonthlyPlayerInputs(

                player_id=pid,
                team_id=eval_tid,
                season_year=sy,
                month_key=mk,
                now_date_iso=str(now_iso)[:10],
                expected_mpg=float(expected_mpg),
                actual_minutes=float(mins_eval),
                games_played=int(gp_eval),
                games_possible=int(gpos_adj),
                role_bucket=role_bucket,  # type: ignore[arg-type]
                leverage=float(leverage),
                team_win_pct=float(team_win_map.get(eval_tid, 0.5)),
                injury_status=injury_status_by_pid.get(pid),
                injury_multiplier=injury_mult,
                ovr=safe_int(rr.get("ovr"), 0),
                age=safe_int(rr.get("age"), 0),
                mental=mental,
                starts=int(starts_eval),
                closes=int(closes_eval),
                starts_rate=float(starts_rate),
                closes_rate=float(closes_rate),
                usage_est=float(usage_est_eval),
                usage_share=float(usage_share),
                fatigue_st=float(fatigue_st),
                fatigue_lt=float(fatigue_lt),
                active_contract_id=str(active_cid) if active_cid else None,
                contract_end_season_id=contract_end_season_id,
                team_strategy=team_strategy,
            )

            # If the previous SSOT state belongs to a different team than the team we are
            # evaluating this month under (common on mid-month trades), align it first.
            # This prevents team-scoped fields like trade_request_level from leaking across teams.
            prev_for_tick = prev
            if prev_for_tick and isinstance(prev_for_tick, Mapping):
                prev_tid = str(prev_for_tick.get("team_id") or "").upper()
                if prev_tid and eval_tid and str(prev_tid) != str(eval_tid).upper():
                    # Best-effort: use the most recent transaction date that moved the player into eval_tid.
                    # Fallback to month_start_date so we don't extend cooldowns unnecessarily.
                    transition_date_iso = norm_date_iso(month_start_date) or str(month_start_date)[:10] or str(now_iso)[:10]
                    for ev_dt, _ft, _tt in (move_events_by_pid.get(pid) or []):
                        if str(_tt or "").upper() == str(eval_tid).upper():
                            transition_date_iso = norm_date_iso(ev_dt) or str(ev_dt)[:10] or transition_date_iso
                            break

                    out_align = apply_team_transition(
                        prev_for_tick,
                        player_id=pid,
                        season_year=sy,
                        from_team_id=prev_tid,
                        to_team_id=eval_tid,
                        month_key=mk,
                        now_date_iso=str(transition_date_iso)[:10],
                        mental=mental,
                        trade_request_level_before=safe_int(prev_for_tick.get("trade_request_level"), 0),
                        split_summary=split_summary_by_pid.get(pid),
                        reason="PRE_EVAL_TEAM_ALIGN",
                        cfg=cfg.transition,
                    )
                    prev_for_tick = out_align.state_after
                    # Keep small explainability metadata in the persisted context (events are optional).
                    pre_eval_team_align_meta_by_pid[pid] = {
                        "from_team_id": prev_tid,
                        "to_team_id": str(eval_tid).upper(),
                        "date": str(transition_date_iso)[:10],
                        "reason": "PRE_EVAL_TEAM_ALIGN",
                        "transition": dict(out_align.meta or {}),
                    }

            new_state, new_events = apply_monthly_player_tick(prev_for_tick, inputs=inp, cfg=cfg)

            # Persist derived evidence caches (v2 UI/explainability). Tick logic may ignore.
            new_state["starts_rate"] = float(starts_rate)
            new_state["closes_rate"] = float(closes_rate)
            new_state["usage_share"] = float(usage_share)

            # Attach attribution context for UI / debugging.
            ctx = new_state.get("context") if isinstance(new_state.get("context"), dict) else {}
            ctx.setdefault("month_attribution", split_summary_by_pid.get(pid) or {})
            ctx.setdefault("evaluation_team_id", eval_tid)
            ctx.setdefault("current_roster_team_id", roster_tid)
            ctx.setdefault("team_end_of_month_id", team_eom_by_pid.get(pid) or str(eval_tid).upper())
            pre_align = pre_eval_team_align_meta_by_pid.get(pid)
            if isinstance(pre_align, dict) and pre_align:
                ctx.setdefault("pre_eval_team_align", pre_align)
            new_state["context"] = ctx

            states_eval[pid] = new_state
            events.extend(new_events)

        # ------------------------------------------------------------------
        # Pass 2: resolve due promises using *month-end team context*, not current roster.
        # ------------------------------------------------------------------
        promise_stats: Dict[str, Any] = {
            "due": 0,
            "resolved": 0,
            "fulfilled": 0,
            "broken": 0,
            "deferred": 0,
            "cancelled": 0,
            "skipped_missing_schema": False,
        }

        promise_events: list[Dict[str, Any]] = []
        promise_updates: list[Dict[str, Any]] = []

        # If a player already surfaced an actionable issue event this month (tick pass),
        # avoid stacking an additional broken-promise reaction event on top. (Spam control)
        players_with_actionable_event = {
            str(ev.get("player_id"))
            for ev in (events or [])
            if isinstance(ev, dict) and ev.get("player_id") and str(ev.get("event_type") or "").upper() not in {"PROMISE_FULFILLED", "PROMISE_BROKEN", "PROMISE_DEFERRED", "PROMISE_DUE"}
        }

        with repo.transaction() as cur:
            # Promise tables are optional for older saves. If missing, we skip cleanly.
            has_promise_schema = bool(
                cur.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='player_agency_promises' LIMIT 1;"
                ).fetchone()
            )
            if not has_promise_schema:
                promise_stats["skipped_missing_schema"] = True
                due_promises = []
            else:
                due_promises = list_active_promises_due(cur, month_key=mk, limit=2000)

            if due_promises:
                promise_stats["due"] = int(len(due_promises))

                # Preload evidence for promise evaluation (best-effort).

                # (A) HELP promises: cache team transactions since the earliest creation date.
                help_since_by_team: Dict[str, str] = {}

                # (B) EXTENSION_TALKS promises: cache player talk-start events since earliest creation date.
                ext_since_by_pid: Dict[str, str] = {}

                for p in due_promises:
                    ptype0 = str(p.get("promise_type") or "").upper()
                    cd0 = norm_date_iso(p.get("created_date")) or str(now_iso)[:10]

                    if ptype0 == "HELP":
                        tid0 = str(p.get("team_id") or "").upper()
                        if not tid0:
                            continue
                        prev = help_since_by_team.get(tid0)
                        if prev is None or str(cd0) < str(prev):
                            help_since_by_team[tid0] = str(cd0)

                    if ptype0 == "EXTENSION_TALKS":
                        pid0 = str(p.get("player_id") or "")
                        if not pid0:
                            continue
                        prev = ext_since_by_pid.get(pid0)
                        if prev is None or str(cd0) < str(prev):
                            ext_since_by_pid[pid0] = str(cd0)

                team_tx_cache: Dict[str, list[Dict[str, Any]]] = {}
                if help_since_by_team:
                    for tid0, since_d in help_since_by_team.items():
                        try:
                            like_pat = f'%"{tid0}"%'
                            rows_tx = cur.execute(
                                """
                                SELECT payload_json
                                FROM transactions_log
                                WHERE tx_date >= ?
                                  AND substr(tx_date, 1, 10) <= ?
                                  AND teams_json LIKE ?
                                ORDER BY COALESCE(tx_date,'') DESC, created_at DESC
                                LIMIT 1000;
                                """,
                                (str(since_d)[:10], str(month_end_date)[:10], str(like_pat)),
                            ).fetchall()
                            tx_list: list[Dict[str, Any]] = []
                            for (payload_json,) in rows_tx:
                                payload = json_loads(payload_json, default=None)
                                if isinstance(payload, dict):
                                    tx_list.append(payload)
                            team_tx_cache[tid0] = tx_list
                        except Exception:
                            team_tx_cache[tid0] = []

                # Compute HELP tag evidence using acquired players and fit tags.
                # This stays SSOT-safe because it derives only from:
                #   - transactions_log (SSOT)
                #   - players.attrs_json (SSOT)
                help_evidence_by_team: Dict[str, Dict[str, Any]] = {}
                if team_tx_cache:
                    try:
                        from trades.valuation.fit_engine import FitEngine
                        from trades.valuation.types import PlayerSnapshot
                    except Exception:
                        FitEngine = None  # type: ignore
                        PlayerSnapshot = None  # type: ignore

                    acquired_by_team: Dict[str, set[str]] = {}

                    def _collect_acquired_player_ids(tid: str, tx_list: list[Dict[str, Any]]) -> set[str]:
                        out: set[str] = set()
                        tid_u = str(tid).upper()
                        for tx in tx_list:
                            if not isinstance(tx, dict):
                                continue

                            # Trade payloads: player_moves list
                            pm = tx.get("player_moves")
                            if isinstance(pm, list):
                                for m in pm:
                                    if not isinstance(m, dict):
                                        continue
                                    to_team = str(m.get("to_team") or "").upper()
                                    pid = str(m.get("player_id") or "")
                                    if pid and to_team == tid_u:
                                        out.add(pid)

                            # Contract actions: player_id + to_team
                            pid2 = tx.get("player_id")
                            if pid2 is not None:
                                pid_s = str(pid2)
                                to_team2 = str(tx.get("to_team") or "").upper()
                                from_team2 = str(tx.get("from_team") or "").upper()
                                if pid_s and to_team2 == tid_u and from_team2 != tid_u:
                                    out.add(pid_s)

                        return out

                    for tid0, tx_list in team_tx_cache.items():
                        acquired_by_team[tid0] = _collect_acquired_player_ids(tid0, tx_list)

                    all_acquired = sorted({pid for s in acquired_by_team.values() for pid in s})
                    attrs_by_pid: Dict[str, Dict[str, Any]] = {}
                    if all_acquired:
                        try:
                            ph = ",".join(["?"] * len(all_acquired))
                            rows_p = cur.execute(
                                f"SELECT player_id, attrs_json FROM players WHERE player_id IN ({ph});",
                                [*all_acquired],
                            ).fetchall()
                            for pid_r, attrs_json in rows_p:
                                pid_s = str(pid_r)
                                attrs = json_loads(attrs_json, default={})
                                attrs_by_pid[pid_s] = attrs if isinstance(attrs, dict) else {}
                        except Exception:
                            attrs_by_pid = {}

                    supply_cache: Dict[str, Dict[str, float]] = {}
                    fe = FitEngine() if (FitEngine is not None) else None

                    for pid_acq in all_acquired:
                        if pid_acq in supply_cache:
                            continue
                        attrs = attrs_by_pid.get(pid_acq) or {}
                        if fe is None or PlayerSnapshot is None or not isinstance(attrs, dict):
                            supply_cache[pid_acq] = {}
                            continue
                        try:
                            snap = PlayerSnapshot(kind="player", player_id=str(pid_acq), attrs=dict(attrs), meta={})
                            sv = fe.compute_player_supply_vector(snap)
                            supply_cache[pid_acq] = {str(k): float(v) for k, v in dict(sv or {}).items()}
                        except Exception:
                            supply_cache[pid_acq] = {}

                    for tid0, pset in acquired_by_team.items():
                        best: Dict[str, float] = {}
                        for pid_acq in pset:
                            sv = supply_cache.get(pid_acq) or {}
                            for tag, val in sv.items():
                                try:
                                    fv = float(val)
                                except Exception:
                                    continue
                                if fv > best.get(str(tag), 0.0):
                                    best[str(tag)] = float(fv)
                        help_evidence_by_team[tid0] = {
                            "acquired_player_ids": sorted(pset),
                            "supply_by_tag": best,
                        }

                # Preload CONTRACT_TALKS_STARTED events for EXTENSION_TALKS promises.
                talks_dates_by_pid: Dict[str, list[str]] = {}
                if ext_since_by_pid:
                    pids = sorted(ext_since_by_pid.keys())
                    if pids:
                        min_d = min(str(v) for v in ext_since_by_pid.values() if v)
                        try:
                            ph = ",".join(["?"] * len(pids))
                            rows_ev = cur.execute(
                                f"""
                                SELECT player_id, date
                                FROM agency_events
                                WHERE event_type='CONTRACT_TALKS_STARTED'
                                  AND date >= ?
                                  AND substr(date, 1, 10) <= ?
                                  AND player_id IN ({ph});
                                """,
                                [str(min_d)[:10], str(month_end_date)[:10], *pids],
                            ).fetchall()
                            for pid_r, d_r in rows_ev:
                                pid_s = str(pid_r)
                                d_s = str(d_r)[:10]
                                if not pid_s or not d_s:
                                    continue
                                talks_dates_by_pid.setdefault(pid_s, []).append(d_s)
                            for pid_s in list(talks_dates_by_pid.keys()):
                                talks_dates_by_pid[pid_s] = sorted(set(talks_dates_by_pid[pid_s]))
                        except Exception:
                            talks_dates_by_pid = {}
                for p in due_promises:
                    try:
                        promise_id = str(p.get("promise_id") or "")
                        ptype = str(p.get("promise_type") or "").upper()
                        pid = str(p.get("player_id") or "")
                        promised_team = str(p.get("team_id") or "").upper()
                        if not promise_id or not pid or not promised_team:
                            continue

                        st = states_eval.get(pid)
                        if not st:
                            continue

                        split = splits_by_pid.get(pid)

                        # Determine the team at end-of-month (EOM) for this processed month.
                        # If we cannot infer it (missing boxscore), anchor to promised team to avoid false cancellations.
                        team_eom = str(team_eom_by_pid.get(pid) or "").upper()
                        if not team_eom:
                            if split and split.team_last:
                                team_eom = str(split.team_last).upper()
                            else:
                                team_eom = promised_team

                        # Cancellation: for non-trade promises, leaving the promised team makes the promise moot.
                        if ptype in {"MINUTES", "HELP", "ROLE", "LOAD", "EXTENSION_TALKS"} and team_eom and promised_team and team_eom != promised_team:
                            promise_updates.append(
                                {
                                    "promise_id": promise_id,
                                    "status": "CANCELLED",
                                    "resolved_at": str(now_iso)[:10],
                                    "evidence": {
                                        "code": "PROMISE_CANCELLED_TEAM_CHANGED",
                                        "promised_team_id": promised_team,
                                        "team_end_of_month_id": team_eom,
                                        "month_key": mk,
                                    },
                                }
                            )
                            promise_events.append(
                                {
                                    "event_id": make_event_id("agency", "promise_cancelled", promise_id, mk),
                                    "player_id": pid,
                                    "team_id": team_eom or str(st.get("team_id") or promised_team).upper(),
                                    "season_year": sy,
                                    "date": str(now_iso)[:10],
                                    "event_type": "PROMISE_CANCELLED",
                                    "severity": 0.10,
                                    "payload": {
                                        "promise_id": promise_id,
                                        "promise_type": ptype,
                                        "promised_team_id": promised_team,
                                        "team_end_of_month_id": team_eom,
                                        "month_key": mk,
                                    },
                                }
                            )
                            promise_stats["cancelled"] = int(promise_stats["cancelled"]) + 1
                            continue

                        mental = mental_by_pid.get(pid) or {}

                        # Build context for evaluation.
                        mins_p, gp_p = _slice_minutes_games(split, promised_team) if split else (0.0, 0)
                        actual_mpg = float(mins_p / float(gp_p) if gp_p > 0 else 0.0)

                        # Role evidence for ROLE promises (promised team slice).
                        starts_p, closes_p, _usage_p = _slice_role_usage(split, promised_team) if split else (0, 0, 0.0)
                        starts_rate_p = float(starts_p / float(gp_p) if gp_p > 0 else 0.0)
                        closes_rate_p = float(closes_p / float(gp_p) if gp_p > 0 else 0.0)

                        # Contract evidence for EXTENSION_TALKS promises.
                        active_cid_p = active_contract_id_by_pid.get(pid)
                        c_row_p = contracts_by_id.get(str(active_cid_p)) if active_cid_p else None
                        contract_end_p = derive_contract_end_season_id(c_row_p) if isinstance(c_row_p, Mapping) else None

                        # Talks started evidence (derived from agency_events, SSOT).
                        created_d = norm_date_iso(p.get("created_date")) or str(now_iso)[:10]
                        talk_dates = talks_dates_by_pid.get(pid) or []
                        month_end_d = str(month_end_date)[:10]
                        talks_started = any(str(created_d) <= str(d) <= month_end_d for d in talk_dates)

                        help_ev = help_evidence_by_team.get(promised_team) if ptype == "HELP" else None
                        help_supply = (help_ev.get("supply_by_tag") if isinstance(help_ev, Mapping) else None)
                        help_acq = (help_ev.get("acquired_player_ids") if isinstance(help_ev, Mapping) else None)

                        ctx = PromiseEvaluationContext(
                            now_date_iso=str(now_iso)[:10],
                            month_key=mk,
                            player_id=pid,
                            # IMPORTANT: this is the team at end of *processed month*, not current roster at evaluation time.
                            team_id_current=team_eom or promised_team,
                            actual_mpg=actual_mpg if ptype in {"MINUTES", "LOAD"} else None,
                            injury_status=injury_status_by_pid.get(pid),
                            leverage=float(safe_float(st.get("leverage"), 0.0)),
                            mental=mental,
                            team_win_pct=float(safe_float(team_win_map.get(promised_team, 0.5), 0.5)),
                            team_transactions=team_tx_cache.get(promised_team) if ptype == "HELP" else None,

                            # v2 promise evidence
                            starts_rate=starts_rate_p,
                            closes_rate=closes_rate_p,
                            active_contract_id=str(active_cid_p) if active_cid_p else None,
                            contract_end_season_id=contract_end_p,
                            contract_talks_started=bool(talks_started) if ptype == "EXTENSION_TALKS" else None,
                            help_supply_by_tag=help_supply if ptype == "HELP" else None,
                            help_acquired_player_ids=help_acq if ptype == "HELP" else None,
                        )

                        res = evaluate_promise(p, ctx=ctx, cfg=DEFAULT_PROMISE_CONFIG)
                        if not res.due:
                            continue

                        deltas = res.state_deltas or {}
                        if deltas:
                            # Common numeric deltas (clamped at write time).
                            for k in (
                                "trust",
                                "minutes_frustration",
                                "team_frustration",
                                "role_frustration",
                                "contract_frustration",
                                "health_frustration",
                                "chemistry_frustration",
                                "usage_frustration",
                            ):
                                if k in deltas:
                                    st[k] = float(clamp01(safe_float(st.get(k), 0.0 if k != "trust" else 0.5) + safe_float(deltas.get(k), 0.0)))

                            if "trade_request_level_min" in deltas:
                                try:
                                    floor_v = int(safe_float(deltas.get("trade_request_level_min"), 0.0))
                                except Exception:
                                    floor_v = 0
                                try:
                                    cur_tr = int(st.get("trade_request_level") or 0)
                                except Exception:
                                    cur_tr = 0
                                st["trade_request_level"] = int(max(cur_tr, floor_v))

                        # ------------------------------------------------------------------
                        # v3 memory + stances: promise outcomes shape the relationship/personality
                        # ------------------------------------------------------------------
                        if res.resolved and str(res.new_status).upper() in {"BROKEN", "FULFILLED"}:
                            ctx0 = st.get("context") if isinstance(st.get("context"), dict) else {}
                            mem = ctx0.get("mem") if isinstance(ctx0.get("mem"), dict) else {}

                            status_u = str(res.new_status).upper()

                            # Aggregate counts
                            if status_u == "BROKEN":
                                mem["broken_promises_total"] = int(mem.get("broken_promises_total") or 0) + 1
                                bpt = mem.get("broken_promises_by_type")
                                if not isinstance(bpt, dict):
                                    bpt = {}
                                bpt[ptype] = int(bpt.get(ptype) or 0) + 1
                                mem["broken_promises_by_type"] = bpt
                            else:
                                mem["fulfilled_promises_total"] = int(mem.get("fulfilled_promises_total") or 0) + 1
                                fpt = mem.get("fulfilled_promises_by_type")
                                if not isinstance(fpt, dict):
                                    fpt = {}
                                fpt[ptype] = int(fpt.get(ptype) or 0) + 1
                                mem["fulfilled_promises_by_type"] = fpt

                            # Small rolling log (for narrative tone + future priors)
                            # Canonical key: promise_outcomes_recent
                            rpo = mem.get("promise_outcomes_recent")
                            if not isinstance(rpo, list):
                                # Backward compatibility for previous key name.
                                rpo = mem.get("recent_promise_outcomes")
                            if not isinstance(rpo, list):
                                rpo = []
                            rpo.append(
                                {
                                    "month_key": str(mk),
                                    "date": str(now_iso)[:10],
                                    "promise_id": str(promise_id),
                                    "promise_type": str(ptype),
                                    "result": str(status_u),
                                    "status": str(status_u),
                                    "team_id": str(team_eom or promised_team),
                                }
                            )
                            # Keep last N
                            try:
                                keep_n = int(getattr(getattr(cfg, "credibility", object()), "recent_window", 6))
                            except Exception:
                                keep_n = 6
                            if keep_n > 0 and len(rpo) > keep_n:
                                rpo = rpo[-keep_n:]
                            mem["promise_outcomes_recent"] = rpo
                            mem["recent_promise_outcomes"] = rpo

                            # Update dynamic stances.
                            # Use leverage as an impact scalar: star players react more strongly.
                            s_deltas, s_meta = stance_deltas_on_promise_outcome(
                                status=str(status_u),
                                base_scale=float(clamp01(safe_float(st.get("leverage"), 0.0))) * 1.15 + 0.20,
                                mental=mental,
                                cfg=cfg,
                            )
                            if s_deltas:
                                st.update(apply_stance_deltas(state=st, deltas=s_deltas))
                                mem["last_stance_change"] = {
                                    "month_key": str(mk),
                                    "promise_id": str(promise_id),
                                    "status": str(status_u),
                                    "deltas": dict(s_deltas),
                                    "meta": dict(s_meta or {}),
                                }

                            ctx0["mem"] = mem
                            st["context"] = ctx0

                            # Reaction event (actionable) for broken promises, if the player
                            # did not already surface a separate issue event this month.
                            if status_u == "BROKEN" and pid not in players_with_actionable_event:
                                axis_map = {
                                    "MINUTES": "MINUTES",
                                    "ROLE": "ROLE",
                                    "HELP": "TEAM",
                                    "LOAD": "HEALTH",
                                    "EXTENSION_TALKS": "CONTRACT",
                                    "SHOP_TRADE": "TRADE",
                                }
                                axis = axis_map.get(str(ptype), "TEAM")

                                # Determine escalation stage based on dynamic stances + publicness.
                                prof, prof_meta = compute_behavior_profile(
                                    mental=mental,
                                    trust=st.get("trust", 0.5),
                                    stance_skepticism=st.get("stance_skepticism", 0.0),
                                    stance_resentment=st.get("stance_resentment", 0.0),
                                    stance_hardball=st.get("stance_hardball", 0.0),
                                )
                                rs = float(clamp01(safe_float(st.get("stance_resentment"), 0.0)))
                                sk = float(clamp01(safe_float(st.get("stance_skepticism"), 0.0)))
                                hb = float(clamp01(safe_float(st.get("stance_hardball"), 0.0)))
                                pub = float(clamp01(getattr(prof, "publicness", 0.5)))
                                lev0 = float(clamp01(safe_float(st.get("leverage"), 0.0)))

                                # Repeated broken promises accelerate escalation.
                                broken_total_prev = int(mem.get("broken_promises_total") or 0)

                                score = 0.40 * rs + 0.20 * sk + 0.15 * hb + 0.15 * pub + 0.10 * lev0
                                score += 0.04 * float(min(3, max(0, broken_total_prev - 1)))
                                score = float(clamp01(score))

                                if score < 0.58:
                                    stage = 1
                                    ev_type = str(cfg.event_types.get("broken_promise_private", "BROKEN_PROMISE_PRIVATE")).upper()
                                elif score < 0.83:
                                    stage = 2
                                    ev_type = str(cfg.event_types.get("broken_promise_agent", "BROKEN_PROMISE_AGENT")).upper()
                                else:
                                    stage = 3
                                    ev_type = str(cfg.event_types.get("broken_promise_public", "BROKEN_PROMISE_PUBLIC")).upper()

                                sev_r = float(clamp01(0.35 + 0.55 * score + 0.20 * lev0))

                                # Snapshot the original promise terms so follow-up actions can
                                # re-offer/renegotiate without DB lookups (responses layer is pure).
                                pt = p.get("target")
                                if not isinstance(pt, Mapping):
                                    pt = {}

                                reaction_event = {
                                    "event_id": make_event_id("agency", "broken_promise", promise_id, mk, ev_type),
                                    "player_id": pid,
                                    "team_id": team_eom or promised_team,
                                    "season_year": sy,
                                    "date": str(now_iso)[:10],
                                    "event_type": ev_type,
                                    "severity": float(sev_r),
                                    "payload": {
                                        "axis": str(axis),
                                        **stage_fields(stage),
                                        "promise_id": str(promise_id),
                                        "promise_type": str(ptype),
                                        "month_key": str(mk),
                                        "due_month": p.get("due_month"),
                                        # Snapshot of original promise terms for follow-up actions.
                                        "promise_target_value": p.get("target_value"),
                                        "promise_target": dict(pt),
                                        "promise_created_date": p.get("created_date"),
                                        "promise_source_event_id": p.get("source_event_id"),
                                        "promise_response_id": p.get("response_id"),
                                        "promised_team_id": promised_team,
                                        "team_end_of_month_id": team_eom,
                                        "reasons": list(res.reasons or []),
                                        "meta": dict(res.meta or {}),
                                        "stance": {"skepticism": sk, "resentment": rs, "hardball": hb},
                                        "behavior_profile": dict(prof_meta or {}),
                                    },
                                }
                                promise_events.append(reaction_event)
                                players_with_actionable_event.add(pid)

                        upd: Dict[str, Any] = {"promise_id": promise_id}
                        pu = res.promise_updates or {}
                        if "status" in pu and pu.get("status") is not None:
                            upd["status"] = str(pu.get("status")).upper()
                        if "due_month" in pu and pu.get("due_month") is not None:
                            upd["due_month"] = pu.get("due_month")
                        if "resolved_at" in pu:
                            upd["resolved_at"] = pu.get("resolved_at")

                        upd["evidence"] = {
                            "month_key": mk,
                            "now_date": str(now_iso)[:10],
                            "promised_team_id": promised_team,
                            "team_end_of_month_id": team_eom,
                            "result": {"due": res.due, "resolved": res.resolved, "new_status": res.new_status},
                            "reasons": res.reasons,
                            "meta": res.meta,
                        }
                        promise_updates.append(upd)

                        sev = float(clamp01(0.10 + abs(float(safe_float(deltas.get("trust"), 0.0))) * 4.0))
                        if res.resolved and str(res.new_status).upper() == "FULFILLED":
                            ev_type = "PROMISE_FULFILLED"
                            promise_stats["fulfilled"] = int(promise_stats["fulfilled"]) + 1
                            promise_stats["resolved"] = int(promise_stats["resolved"]) + 1
                        elif res.resolved and str(res.new_status).upper() == "BROKEN":
                            ev_type = "PROMISE_BROKEN"
                            promise_stats["broken"] = int(promise_stats["broken"]) + 1
                            promise_stats["resolved"] = int(promise_stats["resolved"]) + 1
                        elif not res.resolved and res.due:
                            ev_type = "PROMISE_DEFERRED"
                            promise_stats["deferred"] = int(promise_stats["deferred"]) + 1
                        else:
                            ev_type = "PROMISE_DUE"

                        if ev_type in {"PROMISE_FULFILLED", "PROMISE_BROKEN", "PROMISE_DEFERRED"}:
                            promise_events.append(
                                {
                                    "event_id": make_event_id("agency", "promise", promise_id, mk, ev_type),
                                    "player_id": pid,
                                    "team_id": team_eom or promised_team,
                                    "season_year": sy,
                                    "date": str(now_iso)[:10],
                                    "event_type": ev_type,
                                    "severity": sev,
                                    "payload": {
                                        "promise_id": promise_id,
                                        "promise_type": ptype,
                                        "promised_team_id": promised_team,
                                        "team_end_of_month_id": team_eom,
                                        "month_key": mk,
                                        "due_month": p.get("due_month"),
                                        "new_status": res.new_status,
                                        "state_deltas": deltas,
                                        "reasons": res.reasons,
                                        "meta": res.meta,
                                    },
                                }
                            )
                    except Exception:
                        continue


        if promise_events:
            events.extend(promise_events)

        # ------------------------------------------------------------------
        # Pass 3: apply team transitions for players whose evaluated team != current roster team.
        # ------------------------------------------------------------------
        multi_team_count = 0
        transitioned_count = 0
        states_final: Dict[str, Dict[str, Any]] = {}

        for rr in roster_rows:
            pid = str(rr["player_id"])
            st = states_eval.get(pid)
            if not st:
                continue

            roster_tid = roster_team_by_pid.get(pid) or str(rr.get("team_id") or "").upper()
            eval_tid = str(eval_team_by_pid.get(pid) or st.get("team_id") or "").upper()

            split = splits_by_pid.get(pid)
            if split and split.multi_team():
                multi_team_count += 1

            if roster_tid and eval_tid and roster_tid != eval_tid:
                transitioned_count += 1
                out = apply_team_transition(
                    st,
                    player_id=pid,
                    season_year=sy,
                    from_team_id=eval_tid,
                    to_team_id=roster_tid,
                    month_key=mk,
                    now_date_iso=str(now_iso)[:10],
                    mental=mental_by_pid.get(pid) or {},
                    trade_request_level_before=safe_int(st.get("trade_request_level"), 0),
                    split_summary=split_summary_by_pid.get(pid),
                    reason="POST_MONTH_TRADE",
                    cfg=cfg.transition,
                )
                st2 = out.state_after
                if out.event:
                    events.append(out.event)
                # Keep transition metadata for explainability.
                ctx2 = st2.get("context") if isinstance(st2.get("context"), dict) else {}
                ctx2.setdefault("transition", out.meta)
                st2["context"] = ctx2
                states_final[pid] = st2
            else:
                states_final[pid] = st


        # ------------------------------------------------------------------
        # Pass 3.5: locker room team pass (v2)
        # ------------------------------------------------------------------
        # This pass is derived purely from existing SSOT state (player_agency_state)
        # and emits at most one team-level event per team per month.
        locker_room_stats: Dict[str, Any] = {
            'teams_considered': 0,
            'contagion_updates': 0,
            'meeting_candidates': 0,
            'meetings_emitted': 0,
            'meetings_suppressed_cooldown': 0,
        }

        meeting_candidates_by_team: Dict[str, Dict[str, Any]] = {}

        if states_final:
            pids_by_team: Dict[str, list[str]] = {}
            for pid, st in states_final.items():
                tid = str(st.get('team_id') or '').upper()
                if not tid:
                    continue
                pids_by_team.setdefault(tid, []).append(str(pid))

            for tid, pids in pids_by_team.items():
                team_states: Dict[str, Dict[str, Any]] = {pid: states_final[pid] for pid in pids if pid in states_final}
                if not team_states:
                    continue

                locker_room_stats['teams_considered'] = int(locker_room_stats['teams_considered']) + 1

                tt = compute_team_temperature(team_states)
                team_temp = float(tt.team_temperature)

                # Store the derived temperature for debug/telemetry (small context footprint).
                for pid in pids:
                    st = states_final.get(pid)
                    if not st:
                        continue
                    ctx = st.get('context') if isinstance(st.get('context'), dict) else {}
                    lr = ctx.get('locker_room') if isinstance(ctx.get('locker_room'), dict) else {}
                    lr['team_temperature'] = float(team_temp)
                    # Clear stale leader marker, then set for current leader.
                    if 'is_leader' in lr:
                        lr.pop('is_leader', None)
                    if tt.leader_player_id and pid == str(tt.leader_player_id):
                        lr['is_leader'] = True
                    ctx['locker_room'] = lr
                    st['context'] = ctx

                # Mild social contagion: pull chemistry frustration upward toward team temperature.
                deltas_by_pid = compute_contagion_deltas(team_temp=team_temp, states_by_pid=team_states, cfg=cfg)
                for pid, deltas in (deltas_by_pid or {}).items():
                    st = states_final.get(pid)
                    if not st:
                        continue
                    if 'chemistry_frustration' in deltas:
                        st['chemistry_frustration'] = float(
                            clamp01(safe_float(st.get('chemistry_frustration'), 0.0) + safe_float(deltas.get('chemistry_frustration'), 0.0))
                        )
                        locker_room_stats['contagion_updates'] = int(locker_room_stats['contagion_updates']) + 1

                # Candidate meeting event (cooldown enforced in DB transaction below).
                meet_ev = build_locker_room_meeting_event(
                    team_id=tid,
                    season_year=sy,
                    month_key=mk,
                    now_date_iso=str(now_iso)[:10],
                    states_by_pid=team_states,
                    cfg=cfg,
                )
                if meet_ev:
                    meeting_candidates_by_team[tid] = meet_ev
                    locker_room_stats['meeting_candidates'] = int(locker_room_stats['meeting_candidates']) + 1


        # ------------------------------------------------------------------
        # Persist: write promise updates + state + events + meta key in ONE transaction.
        # ------------------------------------------------------------------
        with repo.transaction() as cur:
            # Apply promise row updates (if any). This must happen in the same transaction
            # as state/events/meta to avoid SSOT inconsistencies on crash/retry.
            if promise_updates:
                update_promises(cur, promise_updates)

            # Team-level meeting events (cooldown enforced via SSOT query).
            if meeting_candidates_by_team:
                meeting_et = str(cfg.event_types.get('locker_room_meeting', 'LOCKER_ROOM_MEETING')).upper()
                try:
                    cd_days = int(getattr(cfg.events, 'locker_room_meeting_cooldown_days', 55))
                except Exception:
                    cd_days = 55
                cutoff = date_add_days(str(now_iso)[:10], -int(cd_days))

                for tid, ev_meet in meeting_candidates_by_team.items():
                    try:
                        row_last = cur.execute(
                            """
                            SELECT date
                            FROM agency_events
                            WHERE team_id=? AND event_type=?
                            ORDER BY date DESC, created_at DESC
                            LIMIT 1;
                            """,
                            (str(tid).upper(), meeting_et),
                        ).fetchone()
                        last_date = str(row_last[0])[:10] if row_last and row_last[0] is not None else None
                        if last_date and str(last_date) >= str(cutoff):
                            locker_room_stats['meetings_suppressed_cooldown'] = int(locker_room_stats['meetings_suppressed_cooldown']) + 1
                            continue
                    except Exception:
                        # If query fails for any reason, still emit deterministically.
                        last_date = None

                    events.append(ev_meet)
                    locker_room_stats['meetings_emitted'] = int(locker_room_stats['meetings_emitted']) + 1

            teams_need_tags: set[str] = set()
            for ev0 in events:
                try:
                    pl = ev0.get("payload")
                    if not isinstance(pl, dict):
                        continue
                    if str(pl.get("axis") or "").upper() != "TEAM":
                        continue
                    if pl.get("need_tags") or pl.get("need_tag"):
                        continue
                    tid0 = str(ev0.get("team_id") or "").upper()
                    if tid0:
                        teams_need_tags.add(tid0)
                except Exception:
                    continue

            if teams_need_tags:
                try:
                    top_n = int(getattr(cfg.events, "help_need_rotation_top_n", 8))
                except Exception:
                    top_n = 8
                try:
                    max_tags = int(getattr(cfg.events, "help_need_tags_max", 3))
                except Exception:
                    max_tags = 3

                allowed = getattr(cfg.events, "help_need_allowed_tags", None)

                weighted_top_by_team: Dict[str, list[tuple[str, float]]] = {}
                all_pids_need: set[str] = set()

                for tid0 in sorted(teams_need_tags):
                    pids0 = month_players_by_team.get(tid0) or []
                    if not pids0:
                        pids0 = [
                            str(rr.get("player_id") or "")
                            for rr in roster_rows
                            if str(rr.get("team_id") or "").upper() == tid0
                        ]
                        pids0 = [pid for pid in pids0 if pid]

                    weighted: list[tuple[str, float]] = []
                    for pid0 in pids0:
                        mins0, _gp0 = _slice_minutes_games(splits_by_pid.get(pid0), tid0)
                        w0 = float(mins0)
                        if w0 <= 0.0:
                            exp0 = month_expectations.get((pid0, tid0))
                            if exp0 is None:
                                exp0 = expectations_current.get(pid0)
                            if exp0 is not None:
                                w0 = float(getattr(exp0, "expected_mpg", 0.0) or 0.0)
                        if w0 <= 0.0:
                            continue
                        weighted.append((str(pid0), float(w0)))

                    weighted.sort(key=lambda x: (-x[1], x[0]))
                    if top_n > 0:
                        weighted = weighted[: top_n]

                    if weighted:
                        weighted_top_by_team[tid0] = weighted
                        for pid0, _w in weighted:
                            all_pids_need.add(str(pid0))

                attrs_by_pid: Dict[str, Dict[str, Any]] = {}
                if all_pids_need:
                    pids_list = sorted(all_pids_need)
                    chunk = 400
                    for i in range(0, len(pids_list), chunk):
                        part = pids_list[i : i + chunk]
                        if not part:
                            continue
                        ph = ",".join(["?"] * len(part))
                        try:
                            rows_p = cur.execute(
                                f"SELECT player_id, attrs_json FROM players WHERE player_id IN ({ph});",
                                list(part),
                            ).fetchall()
                            for pid_r, attrs_json in rows_p:
                                pid_s = str(pid_r or "")
                                if not pid_s:
                                    continue
                                attrs = json_loads(attrs_json, default={})
                                attrs_by_pid[pid_s] = attrs if isinstance(attrs, dict) else {}
                        except Exception:
                            continue

                need_tags_by_team: Dict[str, list[str]] = {}
                for tid0, weighted in weighted_top_by_team.items():
                    players_in = []
                    for pid0, w0 in weighted:
                        attrs = attrs_by_pid.get(pid0) or {}
                        players_in.append((pid0, attrs, float(w0)))

                    try:
                        if isinstance(allowed, (list, tuple)):
                            tags = compute_team_need_tags(players_in, max_tags=max_tags, allowed_tags=list(allowed))
                        else:
                            tags = compute_team_need_tags(players_in, max_tags=max_tags)
                    except Exception:
                        tags = []

                    if tags:
                        need_tags_by_team[str(tid0).upper()] = [str(t).upper() for t in tags if str(t).strip()]

                if need_tags_by_team:
                    for ev0 in events:
                        pl = ev0.get("payload")
                        if not isinstance(pl, dict):
                            continue
                        if str(pl.get("axis") or "").upper() != "TEAM":
                            continue
                        if pl.get("need_tags") or pl.get("need_tag"):
                            continue
                        tid0 = str(ev0.get("team_id") or "").upper()
                        tags = need_tags_by_team.get(tid0)
                        if not tags:
                            continue
                        pl["need_tags"] = list(tags)
                        if "need_tag" not in pl and tags:
                            pl["need_tag"] = str(tags[0])

            # ------------------------------------------------------------------
            # Auto-expire unresponded PROMISE_NEGOTIATION threads (monthly sweep)
            # ------------------------------------------------------------------
            negotiation_expire_stats: Dict[str, Any] = {
                'scanned': 0,
                'expired': 0,
                'auto_ended': 0,
                'skipped_not_expired': 0,
                'skipped_already_resolved': 0,
                'skipped_missing_state': 0,
                'skipped_team_mismatch': 0,
                'marker_only_closed': 0,
                'errors': 0,
            }

            neg_et = str(cfg.event_types.get('promise_negotiation') or 'PROMISE_NEGOTIATION').upper()
            now_date = str(now_iso)[:10]
            now_mk = due_month_from_now(now_date, 0)

            try:
                expire_months = int(getattr(cfg.negotiation, 'expire_months', 1))
            except Exception:
                expire_months = 1
            if expire_months <= 0:
                expire_months = 1

            response_table_ok = table_exists(cur, 'agency_event_responses')

            last_date = '0000-00-00'
            last_created = ''
            batch = 256

            while True:
                if response_table_ok:
                    rows_n = cur.execute(
                        """
                        SELECT
                            e.event_id,
                            e.player_id,
                            e.team_id,
                            e.season_year,
                            e.date,
                            e.event_type,
                            e.severity,
                            e.payload_json,
                            e.created_at
                        FROM agency_events e
                        LEFT JOIN agency_event_responses r
                            ON r.source_event_id = e.event_id
                        WHERE e.event_type = ?
                          AND e.season_year = ?
                          AND r.source_event_id IS NULL
                          AND (e.date > ? OR (e.date = ? AND e.created_at > ?))
                        ORDER BY e.date ASC, e.created_at ASC
                        LIMIT ?;
                        """,
                        (neg_et, int(sy), last_date, last_date, last_created, batch),
                    ).fetchall()
                else:
                    rows_n = cur.execute(
                        """
                        SELECT
                            event_id,
                            player_id,
                            team_id,
                            season_year,
                            date,
                            event_type,
                            severity,
                            payload_json,
                            created_at
                        FROM agency_events
                        WHERE event_type = ?
                          AND season_year = ?
                          AND (date > ? OR (date = ? AND created_at > ?))
                        ORDER BY date ASC, created_at ASC
                        LIMIT ?;
                        """,
                        (neg_et, int(sy), last_date, last_date, last_created, batch),
                    ).fetchall()

                if not rows_n:
                    break

                for row_n in rows_n:
                    negotiation_expire_stats['scanned'] = int(negotiation_expire_stats['scanned']) + 1

                    source_event_id = str(row_n[0] or '')
                    pid = str(row_n[1] or '')
                    tid = str(row_n[2] or '').upper()
                    sy_ev = safe_int(row_n[3], 0)
                    date_ev = norm_date_iso(row_n[4]) or str(row_n[4] or '')[:10]
                    sev_ev = float(clamp01(safe_float(row_n[6], 0.5)))
                    payload_ev = json_loads(row_n[7], default={}) or {}

                    # Determine expires_month: payload preferred, else event date + expire_months.
                    expires_month = norm_month_key(payload_ev.get('expires_month'))
                    if not expires_month and date_ev and len(str(date_ev)) >= 7:
                        expires_month = add_months(str(date_ev)[:7], int(expire_months))

                    if not expires_month or not now_mk or str(now_mk) <= str(expires_month):
                        negotiation_expire_stats['skipped_not_expired'] = int(negotiation_expire_stats['skipped_not_expired']) + 1
                        continue

                    negotiation_expire_stats['expired'] = int(negotiation_expire_stats['expired']) + 1

                    # Idempotency: if deterministic response event already exists, skip.
                    response_event_id = make_event_id('agency', 'response', source_event_id)
                    already = cur.execute(
                        'SELECT 1 FROM agency_events WHERE event_id=? LIMIT 1;',
                        (response_event_id,),
                    ).fetchone()
                    if already:
                        negotiation_expire_stats['skipped_already_resolved'] = int(negotiation_expire_stats['skipped_already_resolved']) + 1
                        continue

                    response_id = make_event_id('agency_resp', source_event_id)
                    resp_payload = {
                        'auto': True,
                        'auto_reason': 'NEGOTIATION_EXPIRED',
                        'source': 'MONTHLY_TICK',
                        'expires_month': str(expires_month),
                        'now_month': str(now_mk),
                    }

                    st = states_final.get(pid)
                    if not st and pid:
                        # Best-effort load to apply END_TALKS penalties even for stale/edge cases.
                        st_loaded = get_player_agency_states(cur, [pid]).get(pid)
                        if st_loaded:
                            st = dict(st_loaded)
                            states_final[pid] = st


                    # If the player has moved teams since this negotiation event,
                    # close the thread but do NOT apply END_TALKS penalties onto the new-team state.
                    if st:
                        st_tid = str(st.get('team_id') or '').upper()
                        if st_tid and st_tid != tid:
                            negotiation_expire_stats['skipped_team_mismatch'] = int(negotiation_expire_stats['skipped_team_mismatch']) + 1

                            if response_table_ok and pid and tid:
                                cur.execute(
                                    """
                                    INSERT OR IGNORE INTO agency_event_responses(
                                        response_id,
                                        source_event_id,
                                        player_id,
                                        team_id,
                                        season_year,
                                        response_type,
                                        response_payload_json,
                                        created_at
                                    )
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                                    """,
                                    (
                                        response_id,
                                        source_event_id,
                                        pid,
                                        tid,
                                        int(sy_ev) if sy_ev > 0 else int(sy),
                                        'END_TALKS',
                                        json_dumps(dict(resp_payload)),
                                        str(now_iso),
                                    ),
                                )

                            events.append(
                                {
                                    'event_id': response_event_id,
                                    'player_id': pid,
                                    'team_id': tid,
                                    'season_year': int(sy_ev) if sy_ev > 0 else int(sy),
                                    'date': now_date,
                                    'event_type': 'USER_RESPONSE',
                                    'severity': 0.35,
                                    'payload': {
                                        'source_event_id': source_event_id,
                                        'source_event_type': neg_et,
                                        'response_id': response_id,
                                        'response_type': 'END_TALKS',
                                        'response_payload': dict(resp_payload),
                                        'tone': 'FIRM',
                                        'player_reply': 'We\'re done here.',
                                        'reasons': [
                                            {
                                                'code': 'NEGOTIATION_EXPIRED_AUTO_END',
                                                'evidence': {
                                                    'expires_month': str(expires_month),
                                                    'now_month': str(now_mk),
                                                    'thread_id': payload_ev.get('thread_id'),
                                                },
                                            },
                                            {
                                                'code': 'AUTO_EXPIRE_TEAM_MISMATCH',
                                                'evidence': {
                                                    'event_team_id': tid,
                                                    'state_team_id': st_tid,
                                                },
                                            },
                                        ],
                                        'state_updates': {},
                                        'bulk_state_deltas': {},
                                        'bulk_skipped_player_ids': [],
                                        'meta': {'auto_expire': {'team_mismatch': True, 'state_team_id': st_tid}},
                                    },
                                }
                            )

                            negotiation_expire_stats['marker_only_closed'] = int(negotiation_expire_stats['marker_only_closed']) + 1
                            continue

                    if not st:
                        negotiation_expire_stats['skipped_missing_state'] = int(negotiation_expire_stats['skipped_missing_state']) + 1

                        # Persist response marker (optional) and close the thread at the event layer.
                        if response_table_ok and pid and tid:
                            cur.execute(
                                """
                                INSERT OR IGNORE INTO agency_event_responses(
                                    response_id,
                                    source_event_id,
                                    player_id,
                                    team_id,
                                    season_year,
                                    response_type,
                                    response_payload_json,
                                    created_at
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                                """,
                                (
                                    response_id,
                                    source_event_id,
                                    pid,
                                    tid,
                                    int(sy_ev) if sy_ev > 0 else int(sy),
                                    'END_TALKS',
                                    json_dumps(dict(resp_payload)),
                                    str(now_iso),
                                ),
                            )

                        events.append(
                            {
                                'event_id': response_event_id,
                                'player_id': pid,
                                'team_id': tid,
                                'season_year': int(sy_ev) if sy_ev > 0 else int(sy),
                                'date': now_date,
                                'event_type': 'USER_RESPONSE',
                                'severity': 0.6,
                                'payload': {
                                    'source_event_id': source_event_id,
                                    'source_event_type': neg_et,
                                    'response_id': response_id,
                                    'response_type': 'END_TALKS',
                                    'response_payload': dict(resp_payload),
                                    'tone': 'FIRM',
                                    'player_reply': 'You waited too long. We\'re done.',
                                    'reasons': [
                                        {
                                            'code': 'NEGOTIATION_EXPIRED_AUTO_END',
                                            'evidence': {
                                                'expires_month': str(expires_month),
                                                'now_month': str(now_mk),
                                                'thread_id': payload_ev.get('thread_id'),
                                            },
                                        },
                                        {'code': 'AUTO_EXPIRE_MISSING_STATE'},
                                    ],
                                    'state_updates': {},
                                    'bulk_state_deltas': {},
                                    'bulk_skipped_player_ids': [],
                                    'meta': {'auto_expire': {'state_missing': True}},
                                },
                            }
                        )
                        negotiation_expire_stats['marker_only_closed'] = int(negotiation_expire_stats['marker_only_closed']) + 1
                        continue

                    # Apply END_TALKS using the same pure logic as normal responses.
                    ev = {
                        'event_id': source_event_id,
                        'player_id': pid,
                        'team_id': tid,
                        'season_year': int(sy_ev) if sy_ev > 0 else int(st.get('season_year') or sy),
                        'date': date_ev or now_date,
                        'event_type': neg_et,
                        'severity': sev_ev,
                        'payload': dict(payload_ev) if isinstance(payload_ev, dict) else {},
                    }

                    try:
                        outcome = apply_user_response(
                            event=ev,
                            state=st,
                            mental=mental_by_pid.get(pid) or {},
                            response_type='END_TALKS',
                            response_payload=resp_payload,
                            now_date_iso=now_date,
                            cfg=cfg,
                            rcfg=DEFAULT_RESPONSE_CONFIG,
                        )
                    except Exception:
                        negotiation_expire_stats['errors'] = int(negotiation_expire_stats['errors']) + 1
                        continue

                    if not outcome.ok:
                        negotiation_expire_stats['errors'] = int(negotiation_expire_stats['errors']) + 1
                        continue

                    for k, v in (outcome.state_updates or {}).items():
                        st[k] = v

                    # Persist response marker (optional).
                    if response_table_ok and pid and tid:
                        cur.execute(
                            """
                            INSERT OR IGNORE INTO agency_event_responses(
                                response_id,
                                source_event_id,
                                player_id,
                                team_id,
                                season_year,
                                response_type,
                                response_payload_json,
                                created_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                            """,
                            (
                                response_id,
                                source_event_id,
                                pid,
                                tid,
                                int(ev['season_year']),
                                str(outcome.response_type),
                                json_dumps(dict(resp_payload)),
                                str(now_iso),
                            ),
                        )

                    trust_delta = safe_float((outcome.meta or {}).get('deltas', {}).get('trust'), 0.0)
                    sev_resp = float(clamp01(abs(trust_delta) * 4.0))

                    events.append(
                        {
                            'event_id': response_event_id,
                            'player_id': pid,
                            'team_id': tid,
                            'season_year': int(ev['season_year']),
                            'date': now_date,
                            'event_type': 'USER_RESPONSE',
                            'severity': sev_resp,
                            'payload': {
                                'source_event_id': source_event_id,
                                'source_event_type': neg_et,
                                'response_id': response_id,
                                'response_type': str(outcome.response_type),
                                'response_payload': dict(resp_payload),
                                'tone': outcome.tone,
                                'player_reply': outcome.player_reply,
                                'reasons': outcome.reasons,
                                'state_updates': outcome.state_updates,
                                'bulk_state_deltas': outcome.bulk_state_deltas,
                                'bulk_skipped_player_ids': [],
                                'meta': outcome.meta,
                            },
                        }
                    )

                    negotiation_expire_stats['auto_ended'] = int(negotiation_expire_stats['auto_ended']) + 1

                # Advance cursor.
                last_date = str(rows_n[-1][4] or last_date)
                last_created = str(rows_n[-1][8] or last_created)

            upsert_player_agency_states(cur, states_final, now=str(now_iso))
            insert_agency_events(cur, events, now=str(now_iso))
            cur.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value;",
                (meta_key, "1"),
            )

        return {
            "ok": True,
            "skipped": False,
            "month": mk,
            "meta_key": meta_key,
            "players_processed": len(roster_rows),
            "states_upserted": len(states_final),
            "events_emitted": len(events),
            "month_attribution": {
                "multi_team_players": int(multi_team_count),
                "transitions_applied": int(transitioned_count),
                "splits_count": int(len(splits_by_pid)),
            },
            "promise_stats": promise_stats,
            "locker_room_stats": locker_room_stats,
            "negotiation_expire_stats": negotiation_expire_stats,
        }
