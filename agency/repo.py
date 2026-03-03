from __future__ import annotations

"""DB access layer for the agency subsystem.

This module is intentionally *pure DB I/O*:
- No imports from sim/matchengine to avoid circular dependencies.
- No business logic besides defensive normalization.

Tables (SSOT):
- player_agency_state
- agency_events
- agency_event_responses
- player_agency_promises

Dates are stored as ISO strings.
"""

import sqlite3
from typing import Any, Dict, Mapping, Optional, Sequence

from .utils import clamp01, json_dumps, json_loads, norm_date_iso, norm_month_key, safe_float, safe_float_opt, safe_int


def _uniq_str_ids(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in ids:
        s = str(x)
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def get_player_agency_states(
    cur: sqlite3.Cursor,
    player_ids: list[str],
) -> Dict[str, Dict[str, Any]]:
    """Bulk-load agency states for the given players.

    Returns:
        dict[player_id] -> state dict (JSON fields decoded).

    Notes:
    - Missing rows are omitted.
    - Invalid JSON is tolerated.
    """
    uniq = _uniq_str_ids([str(pid) for pid in (player_ids or []) if str(pid)])
    if not uniq:
        return {}

    placeholders = ",".join(["?"] * len(uniq))
    rows = cur.execute(
        f"""
        SELECT
            player_id,
            team_id,
            season_year,
            role_bucket,
            leverage,
            minutes_expected_mpg,
            minutes_actual_mpg,
            minutes_frustration,
            team_frustration,
            trust,

            role_frustration,
            contract_frustration,
            health_frustration,
            chemistry_frustration,
            usage_frustration,

            starts_rate,
            closes_rate,
            usage_share,

            self_expected_mpg,
            self_expected_starts_rate,
            self_expected_closes_rate,
            stance_skepticism,
            stance_resentment,
            stance_hardball,

            trade_request_level,

            cooldown_minutes_until,
            cooldown_trade_until,
            cooldown_help_until,
            cooldown_contract_until,

            cooldown_role_until,
            cooldown_health_until,
            cooldown_chemistry_until,

            escalation_role,
            escalation_contract,
            escalation_team,
            escalation_health,
            escalation_chemistry,

            last_processed_month,
            context_json
        FROM player_agency_state
        WHERE player_id IN ({placeholders});
        """,
        uniq,
    ).fetchall()

    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        pid = str(r[0])
        out[pid] = {
            "player_id": pid,
            "team_id": str(r[1] or "").upper(),
            "season_year": safe_int(r[2], 0),
            "role_bucket": str(r[3] or "UNKNOWN"),
            "leverage": safe_float(r[4], 0.0),
            "minutes_expected_mpg": safe_float(r[5], 0.0),
            "minutes_actual_mpg": safe_float(r[6], 0.0),
            "minutes_frustration": safe_float(r[7], 0.0),
            "team_frustration": safe_float(r[8], 0.0),
            "trust": safe_float(r[9], 0.5),
            "role_frustration": safe_float(r[10], 0.0),
            "contract_frustration": safe_float(r[11], 0.0),
            "health_frustration": safe_float(r[12], 0.0),
            "chemistry_frustration": safe_float(r[13], 0.0),
            "usage_frustration": safe_float(r[14], 0.0),
            "starts_rate": safe_float(r[15], 0.0),
            "closes_rate": safe_float(r[16], 0.0),
            "usage_share": safe_float(r[17], 0.0),

            # v3: self expectations (optional)
            "self_expected_mpg": safe_float_opt(r[18]),
            "self_expected_starts_rate": safe_float_opt(r[19]),
            "self_expected_closes_rate": safe_float_opt(r[20]),

            # v3: dynamic stances (0..1)
            "stance_skepticism": float(clamp01(safe_float(r[21], 0.0))),
            "stance_resentment": float(clamp01(safe_float(r[22], 0.0))),
            "stance_hardball": float(clamp01(safe_float(r[23], 0.0))),

            "trade_request_level": safe_int(r[24], 0),
            "cooldown_minutes_until": norm_date_iso(r[25]),
            "cooldown_trade_until": norm_date_iso(r[26]),
            "cooldown_help_until": norm_date_iso(r[27]),
            "cooldown_contract_until": norm_date_iso(r[28]),
            "cooldown_role_until": norm_date_iso(r[29]),
            "cooldown_health_until": norm_date_iso(r[30]),
            "cooldown_chemistry_until": norm_date_iso(r[31]),
            "escalation_role": safe_int(r[32], 0),
            "escalation_contract": safe_int(r[33], 0),
            "escalation_team": safe_int(r[34], 0),
            "escalation_health": safe_int(r[35], 0),
            "escalation_chemistry": safe_int(r[36], 0),
            "last_processed_month": norm_month_key(r[37]),
            "context": json_loads(r[38], default={}) or {},
        }

    return out


def upsert_player_agency_states(
    cur: sqlite3.Cursor,
    states_by_pid: Mapping[str, Mapping[str, Any]],
    *,
    now: str,
) -> None:
    """Upsert agency states.

    Args:
        states_by_pid: mapping[player_id] -> state dict.
        now: timestamp string (UTC-like in-game time)

    Commercial safety:
    - invalid entries are skipped silently.
    """
    if not states_by_pid:
        return

    rows: list[tuple[Any, ...]] = []
    for pid, st in states_by_pid.items():
        pid_s = str(pid)
        if not pid_s:
            continue
        try:
            team_id = str(st.get("team_id") or "").upper()
            if not team_id:
                continue
            season_year = safe_int(st.get("season_year"), 0)
            if season_year <= 0:
                continue

            role_bucket = str(st.get("role_bucket") or "UNKNOWN")
            leverage = float(clamp01(st.get("leverage")))

            exp_mpg = float(max(0.0, safe_float(st.get("minutes_expected_mpg"), 0.0)))
            act_mpg = float(max(0.0, safe_float(st.get("minutes_actual_mpg"), 0.0)))

            minutes_fr = float(clamp01(st.get("minutes_frustration")))
            team_fr = float(clamp01(st.get("team_frustration")))
            trust = float(clamp01(st.get("trust")))

            # v2 axis persistence (tick v1 ignores these but we keep them for v2+)
            role_fr = float(clamp01(st.get("role_frustration")))
            contract_fr = float(clamp01(st.get("contract_frustration")))
            health_fr = float(clamp01(st.get("health_frustration")))
            chemistry_fr = float(clamp01(st.get("chemistry_frustration")))
            usage_fr = float(clamp01(st.get("usage_frustration")))

            starts_rate = float(clamp01(st.get("starts_rate")))
            closes_rate = float(clamp01(st.get("closes_rate")))
            usage_share = float(clamp01(st.get("usage_share")))

            # v3: self expectations (optional)
            self_exp_mpg = safe_float_opt(st.get("self_expected_mpg"))
            if self_exp_mpg is not None:
                self_exp_mpg = float(max(0.0, self_exp_mpg))

            self_exp_starts = safe_float_opt(st.get("self_expected_starts_rate"))
            if self_exp_starts is not None:
                self_exp_starts = float(clamp01(self_exp_starts))

            self_exp_closes = safe_float_opt(st.get("self_expected_closes_rate"))
            if self_exp_closes is not None:
                self_exp_closes = float(clamp01(self_exp_closes))

            # v3: dynamic stances (0..1)
            stance_skepticism = float(clamp01(st.get("stance_skepticism")))
            stance_resentment = float(clamp01(st.get("stance_resentment")))
            stance_hardball = float(clamp01(st.get("stance_hardball")))

            tr_level = safe_int(st.get("trade_request_level"), 0)

            cd_minutes = norm_date_iso(st.get("cooldown_minutes_until"))
            cd_trade = norm_date_iso(st.get("cooldown_trade_until"))
            cd_help = norm_date_iso(st.get("cooldown_help_until"))
            cd_contract = norm_date_iso(st.get("cooldown_contract_until"))

            cd_role = norm_date_iso(st.get("cooldown_role_until"))
            cd_health = norm_date_iso(st.get("cooldown_health_until"))
            cd_chem = norm_date_iso(st.get("cooldown_chemistry_until"))

            def _clamp_stage(x: Any) -> int:
                v = safe_int(x, 0)
                if v < 0:
                    return 0
                if v > 4:
                    return 4
                return int(v)

            esc_role = _clamp_stage(st.get("escalation_role"))
            esc_contract = _clamp_stage(st.get("escalation_contract"))
            esc_team = _clamp_stage(st.get("escalation_team"))
            esc_health = _clamp_stage(st.get("escalation_health"))
            esc_chem = _clamp_stage(st.get("escalation_chemistry"))

            last_month = norm_month_key(st.get("last_processed_month"))

            context = st.get("context") or {}
            if not isinstance(context, Mapping):
                context = {}

        except Exception:
            continue

        rows.append(
            (
                pid_s,
                team_id,
                int(season_year),
                role_bucket,
                float(leverage),
                float(exp_mpg),
                float(act_mpg),
                float(minutes_fr),
                float(team_fr),
                float(trust),
                float(role_fr),
                float(contract_fr),
                float(health_fr),
                float(chemistry_fr),
                float(usage_fr),
                float(starts_rate),
                float(closes_rate),
                float(usage_share),

                self_exp_mpg,
                self_exp_starts,
                self_exp_closes,
                float(stance_skepticism),
                float(stance_resentment),
                float(stance_hardball),

                int(tr_level),
                cd_minutes,
                cd_trade,
                cd_help,
                cd_contract,
                cd_role,
                cd_health,
                cd_chem,
                int(esc_role),
                int(esc_contract),
                int(esc_team),
                int(esc_health),
                int(esc_chem),
                last_month,
                json_dumps(dict(context)),
                str(now),
                str(now),
            )
        )

    if not rows:
        return

    cur.executemany(
        """
        INSERT INTO player_agency_state(
            player_id,
            team_id,
            season_year,
            role_bucket,
            leverage,
            minutes_expected_mpg,
            minutes_actual_mpg,
            minutes_frustration,
            team_frustration,
            trust,
            role_frustration,
            contract_frustration,
            health_frustration,
            chemistry_frustration,
            usage_frustration,
            starts_rate,
            closes_rate,
            usage_share,

            self_expected_mpg,
            self_expected_starts_rate,
            self_expected_closes_rate,
            stance_skepticism,
            stance_resentment,
            stance_hardball,

            trade_request_level,
            cooldown_minutes_until,
            cooldown_trade_until,
            cooldown_help_until,
            cooldown_contract_until,
            cooldown_role_until,
            cooldown_health_until,
            cooldown_chemistry_until,
            escalation_role,
            escalation_contract,
            escalation_team,
            escalation_health,
            escalation_chemistry,
            last_processed_month,
            context_json,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_id) DO UPDATE SET
            team_id=excluded.team_id,
            season_year=excluded.season_year,
            role_bucket=excluded.role_bucket,
            leverage=excluded.leverage,
            minutes_expected_mpg=excluded.minutes_expected_mpg,
            minutes_actual_mpg=excluded.minutes_actual_mpg,
            minutes_frustration=excluded.minutes_frustration,
            team_frustration=excluded.team_frustration,
            trust=excluded.trust,
            role_frustration=excluded.role_frustration,
            contract_frustration=excluded.contract_frustration,
            health_frustration=excluded.health_frustration,
            chemistry_frustration=excluded.chemistry_frustration,
            usage_frustration=excluded.usage_frustration,
            starts_rate=excluded.starts_rate,
            closes_rate=excluded.closes_rate,
            usage_share=excluded.usage_share,

            self_expected_mpg=excluded.self_expected_mpg,
            self_expected_starts_rate=excluded.self_expected_starts_rate,
            self_expected_closes_rate=excluded.self_expected_closes_rate,
            stance_skepticism=excluded.stance_skepticism,
            stance_resentment=excluded.stance_resentment,
            stance_hardball=excluded.stance_hardball,

            trade_request_level=excluded.trade_request_level,
            cooldown_minutes_until=excluded.cooldown_minutes_until,
            cooldown_trade_until=excluded.cooldown_trade_until,
            cooldown_help_until=excluded.cooldown_help_until,
            cooldown_contract_until=excluded.cooldown_contract_until,
            cooldown_role_until=excluded.cooldown_role_until,
            cooldown_health_until=excluded.cooldown_health_until,
            cooldown_chemistry_until=excluded.cooldown_chemistry_until,
            escalation_role=excluded.escalation_role,
            escalation_contract=excluded.escalation_contract,
            escalation_team=excluded.escalation_team,
            escalation_health=excluded.escalation_health,
            escalation_chemistry=excluded.escalation_chemistry,
            last_processed_month=excluded.last_processed_month,
            context_json=excluded.context_json,
            updated_at=excluded.updated_at;
        """,
        rows,
    )


def insert_agency_events(
    cur: sqlite3.Cursor,
    events: list[Mapping[str, Any]],
    *,
    now: str,
) -> None:
    """Insert agency events (append-only).

    Args:
        events: list of dict rows matching agency_events columns.
        now: created_at timestamp

    Invalid entries are skipped silently.
    """
    if not events:
        return

    rows: list[tuple[Any, ...]] = []
    for e in events:
        try:
            event_id = str(e.get("event_id") or "")
            if not event_id:
                continue
            player_id = str(e.get("player_id") or "")
            team_id = str(e.get("team_id") or "").upper()
            if not player_id or not team_id:
                continue
            season_year = safe_int(e.get("season_year"), 0)
            if season_year <= 0:
                continue
            date_iso = norm_date_iso(e.get("date"))
            if not date_iso:
                continue
            event_type = str(e.get("event_type") or "")
            if not event_type:
                continue
            severity = float(safe_float(e.get("severity"), 0.0))
            payload = e.get("payload") or {}
            if not isinstance(payload, Mapping):
                payload = {}
        except Exception:
            continue

        rows.append(
            (
                event_id,
                player_id,
                team_id,
                int(season_year),
                date_iso,
                event_type,
                float(severity),
                json_dumps(dict(payload)),
                str(now),
            )
        )

    if not rows:
        return

    cur.executemany(
        """
        INSERT OR IGNORE INTO agency_events(
            event_id,
            player_id,
            team_id,
            season_year,
            date,
            event_type,
            severity,
            payload_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        rows,
    )


def list_agency_events(
    cur: sqlite3.Cursor,
    *,
    team_id: Optional[str] = None,
    player_id: Optional[str] = None,
    season_year: Optional[int] = None,
    event_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Dict[str, Any]]:
    """List events for UI feeds.

    This is intentionally simple (SQLite). For large-scale needs, add proper
    pagination and richer filtering.
    """
    where: list[str] = []
    args: list[Any] = []

    if team_id:
        where.append("team_id = ?")
        args.append(str(team_id).upper())
    if player_id:
        where.append("player_id = ?")
        args.append(str(player_id))
    if season_year is not None:
        where.append("season_year = ?")
        args.append(int(season_year))
    if event_type:
        where.append("event_type = ?")
        args.append(str(event_type))

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    lim = int(limit) if int(limit) > 0 else 50
    off = int(offset) if int(offset) >= 0 else 0

    rows = cur.execute(
        f"""
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
        {where_sql}
        ORDER BY date DESC, created_at DESC
        LIMIT ? OFFSET ?;
        """,
        args + [lim, off],
    ).fetchall()

    out: list[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "event_id": str(r[0]),
                "player_id": str(r[1]),
                "team_id": str(r[2]),
                "season_year": safe_int(r[3], 0),
                "date": norm_date_iso(r[4]),
                "event_type": str(r[5]),
                "severity": safe_float(r[6], 0.0),
                "payload": json_loads(r[7], default={}) or {},
                "created_at": r[8],
            }
        )

    return out




# -----------------------------------------------------------------------------
# Optional tables: user responses + promises
# -----------------------------------------------------------------------------


def table_exists(cur: sqlite3.Cursor, table_name: str) -> bool:
    """Return True if a table exists in this SQLite DB."""
    try:
        row = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1;",
            (str(table_name),),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def get_agency_event_response(
    cur: sqlite3.Cursor,
    *,
    source_event_id: str,
) -> Optional[Dict[str, Any]]:
    """Return a stored user response for a given agency event, if present."""
    sid = str(source_event_id or "")
    if not sid:
        return None
    if not table_exists(cur, "agency_event_responses"):
        return None

    r = cur.execute(
        """
        SELECT
            response_id,
            source_event_id,
            player_id,
            team_id,
            season_year,
            response_type,
            response_payload_json,
            created_at
        FROM agency_event_responses
        WHERE source_event_id = ?
        LIMIT 1;
        """,
        (sid,),
    ).fetchone()

    if not r:
        return None

    return {
        "response_id": str(r[0]),
        "source_event_id": str(r[1]),
        "player_id": str(r[2]),
        "team_id": str(r[3]),
        "season_year": safe_int(r[4], 0),
        "response_type": str(r[5]),
        "response_payload": json_loads(r[6], default={}) or {},
        "created_at": str(r[7]),
    }


def list_active_promises_due(
    cur: sqlite3.Cursor,
    *,
    month_key: str,
    team_id: Optional[str] = None,
    player_id: Optional[str] = None,
    limit: int = 500,
) -> list[Dict[str, Any]]:
    """List ACTIVE promises whose due_month is <= month_key.

    This is used by the monthly agency tick to resolve promises.

    Returns:
        list of promise rows with JSON fields decoded.
    """
    if not table_exists(cur, "player_agency_promises"):
        return []

    mk = norm_month_key(month_key)
    if not mk:
        return []

    where: list[str] = ["status = 'ACTIVE'", "due_month <= ?"]
    args: list[Any] = [mk]

    if team_id:
        where.append("team_id = ?")
        args.append(str(team_id).upper())
    if player_id:
        where.append("player_id = ?")
        args.append(str(player_id))

    where_sql = "WHERE " + " AND ".join(where)

    lim = int(limit) if int(limit) > 0 else 500

    rows = cur.execute(
        f"""
        SELECT
            promise_id,
            player_id,
            team_id,
            season_year,
            source_event_id,
            response_id,
            promise_type,
            status,
            created_date,
            due_month,
            target_value,
            target_json,
            evidence_json,
            resolved_at
        FROM player_agency_promises
        {where_sql}
        ORDER BY due_month ASC, created_date ASC
        LIMIT ?;
        """,
        args + [lim],
    ).fetchall()

    out: list[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "promise_id": str(r[0]),
                "player_id": str(r[1]),
                "team_id": str(r[2]).upper(),
                "season_year": safe_int(r[3], 0),
                "source_event_id": str(r[4]) if r[4] is not None else None,
                "response_id": str(r[5]) if r[5] is not None else None,
                "promise_type": str(r[6]).upper(),
                "status": str(r[7]).upper(),
                "created_date": norm_date_iso(r[8]),
                "due_month": norm_month_key(r[9]),
                "target_value": (None if r[10] is None else float(safe_float(r[10], 0.0))),
                "target": json_loads(r[11], default={}) or {},
                "evidence": json_loads(r[12], default={}) or {},
                "resolved_at": norm_date_iso(r[13]),
            }
        )

    return out


def update_promises(
    cur: sqlite3.Cursor,
    updates: Sequence[Mapping[str, Any]],
) -> None:
    """Apply updates to promise rows.

    Each update mapping must include:
      - promise_id
    Optional fields:
      - status
      - due_month
      - target_value
      - target (dict) -> stored in target_json
      - evidence (dict) -> stored in evidence_json
      - resolved_at

    Invalid rows are skipped.
    """
    if not updates:
        return
    if not table_exists(cur, "player_agency_promises"):
        return

    for u in updates:
        try:
            pid = str(u.get("promise_id") or "")
            if not pid:
                continue

            sets: list[str] = []
            args: list[Any] = []

            if "status" in u and u.get("status") is not None:
                sets.append("status=?")
                args.append(str(u.get("status")).upper())

            if "due_month" in u and u.get("due_month") is not None:
                dm = norm_month_key(u.get("due_month"))
                if dm:
                    sets.append("due_month=?")
                    args.append(dm)

            if "target_value" in u:
                sets.append("target_value=?")
                args.append(u.get("target_value"))

            if "target" in u and u.get("target") is not None:
                t = u.get("target")
                if not isinstance(t, Mapping):
                    t = {}
                sets.append("target_json=?")
                args.append(json_dumps(dict(t)))

            if "evidence" in u and u.get("evidence") is not None:
                ev = u.get("evidence")
                if not isinstance(ev, Mapping):
                    ev = {}
                sets.append("evidence_json=?")
                args.append(json_dumps(dict(ev)))

            if "resolved_at" in u:
                ra = norm_date_iso(u.get("resolved_at"))
                sets.append("resolved_at=?")
                args.append(ra)

            if not sets:
                continue

            args.append(pid)

            cur.execute(
                f"""UPDATE player_agency_promises SET {", ".join(sets)} WHERE promise_id=?;""",
                args,
            )
        except Exception:
            continue
