from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping

from agency.utils import make_event_id
from injury.status import status_for_date


def _dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def _loads_obj(v: Any) -> Dict[str, Any]:
    if isinstance(v, dict):
        return dict(v)
    if v is None:
        return {}
    try:
        obj = json.loads(v)
    except Exception:
        return {}
    if isinstance(obj, dict):
        return obj
    return {}


def list_player_inputs(cur, *, season_year: int, decision_date_iso: str) -> List[Dict[str, Any]]:
    rows = cur.execute(
        """
        SELECT p.player_id, p.age, p.ovr, p.attrs_json,
               COALESCE(r.team_id, 'FA') AS team_id,
               COALESCE(i.status, 'HEALTHY') AS injury_status,
               COALESCE(i.severity, 0) AS injury_severity,
               i.out_until_date, i.returning_until_date,
               i.injury_type, i.body_part,
               i.perm_drop_json, i.reinjury_count_json
        FROM players p
        JOIN roster r ON r.player_id = p.player_id AND r.status='active'
        LEFT JOIN player_injury_state i ON i.player_id = p.player_id
        ORDER BY p.player_id
        """
    ).fetchall()

    # Recent injury event aggregates (last 3 seasons before target season)
    sy = int(season_year)
    y1 = sy - 1
    y3 = sy - 3
    ev_rows = cur.execute(
        """
        SELECT player_id, season_year, severity, duration_days
        FROM injury_events
        WHERE season_year BETWEEN ? AND ?
        """,
        (int(y3), int(y1)),
    ).fetchall()
    ev_by_pid: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "missed_days_1y": 0,
        "missed_days_3y": 0,
        "events_3y": 0,
        "severe_count_3y": 0,
        "severity_sum_3y": 0,
    })
    for e in ev_rows:
        pid = str(e["player_id"])
        esy = int(e["season_year"] or 0)
        sev = int(e["severity"] or 0)
        dur = int(e["duration_days"] or 0)
        d = ev_by_pid[pid]
        d["events_3y"] += 1
        d["missed_days_3y"] += max(0, dur)
        d["severity_sum_3y"] += max(0, sev)
        if sev >= 4:
            d["severe_count_3y"] += 1
        if esy == y1:
            d["missed_days_1y"] += max(0, dur)

    out: List[Dict[str, Any]] = []
    for r in rows:
        pid = str(r["player_id"])
        team_id = str(r["team_id"] or "FA").upper()
        perm_drop = _loads_obj(r["perm_drop_json"])
        reinjury_count = _loads_obj(r["reinjury_count_json"])
        reinjury_total = 0
        for v in reinjury_count.values():
            try:
                reinjury_total += int(v or 0)
            except Exception:
                continue
        perm_drop_total = 0
        for v in perm_drop.values():
            try:
                perm_drop_total += abs(int(v or 0))
            except Exception:
                continue

        # Normalize status for the actual decision date from canonical date boundaries.
        norm_status = status_for_date(
            {
                "out_until_date": r["out_until_date"],
                "returning_until_date": r["returning_until_date"],
            },
            on_date_iso=str(decision_date_iso),
        )
        # Keep stored status as fallback signal when date bounds are empty.
        status_fallback = str(r["injury_status"] or "HEALTHY").upper()
        injury_status = str(norm_status or status_fallback or "HEALTHY").upper()

        ev = ev_by_pid.get(pid) or {}
        events_3y = int(ev.get("events_3y") or 0)
        severity_sum_3y = int(ev.get("severity_sum_3y") or 0)
        avg_severity_3y = (float(severity_sum_3y) / float(events_3y)) if events_3y > 0 else 0.0

        out.append(
            {
                "player_id": pid,
                "season_year": int(season_year),
                "age": int(r["age"] or 0),
                "ovr": int(r["ovr"] or 0),
                "attrs_json": r["attrs_json"],
                "team_id": team_id,
                "injury_status": injury_status,
                "injury_severity": int(r["injury_severity"] or 0),
                "injury_context": {
                    "out_until_date": str(r["out_until_date"] or ""),
                    "returning_until_date": str(r["returning_until_date"] or ""),
                    "injury_type": str(r["injury_type"] or ""),
                    "body_part": str(r["body_part"] or ""),
                    "perm_drop_total": int(max(0, perm_drop_total)),
                    "reinjury_total": int(max(0, reinjury_total)),
                    "missed_days_1y": int(max(0, ev.get("missed_days_1y") or 0)),
                    "missed_days_3y": int(max(0, ev.get("missed_days_3y") or 0)),
                    "events_3y": int(max(0, events_3y)),
                    "severe_count_3y": int(max(0, ev.get("severe_count_3y") or 0)),
                    "avg_severity_3y": float(max(0.0, avg_severity_3y)),
                },
            }
        )
    return out


def upsert_decisions(cur, *, decisions: Iterable[Mapping[str, Any]], now_iso: str) -> int:
    rows = []
    for d in decisions:
        rows.append(
            (
                int(d["season_year"]),
                str(d["player_id"]),
                str(d["decision"]),
                1 if bool(d.get("considered")) else 0,
                float(d.get("consideration_prob") or 0.0),
                float(d.get("retirement_prob") or 0.0),
                float(d.get("random_roll") or 0.0),
                int(d.get("age") or 0),
                str(d.get("team_id") or ""),
                str(d.get("injury_status") or ""),
                _dumps(d.get("inputs") or {}),
                _dumps(d.get("explanation") or {}),
                str(d.get("decided_at") or now_iso),
                str(d.get("processed_at") or ""),
                str(d.get("source") or "offseason"),
                str(now_iso),
                str(now_iso),
            )
        )
    if not rows:
        return 0
    cur.executemany(
        """
        INSERT INTO player_retirement_decisions(
            season_year, player_id, decision, considered,
            consideration_prob, retirement_prob, random_roll,
            age, team_id, injury_status,
            inputs_json, explanation_json,
            decided_at, processed_at, source,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(season_year, player_id) DO UPDATE SET
            decision=excluded.decision,
            considered=excluded.considered,
            consideration_prob=excluded.consideration_prob,
            retirement_prob=excluded.retirement_prob,
            random_roll=excluded.random_roll,
            age=excluded.age,
            team_id=excluded.team_id,
            injury_status=excluded.injury_status,
            inputs_json=excluded.inputs_json,
            explanation_json=excluded.explanation_json,
            decided_at=excluded.decided_at,
            source=excluded.source,
            updated_at=excluded.updated_at
        ;
        """,
        rows,
    )
    return int(len(rows))


def list_decisions(cur, *, season_year: int) -> List[Dict[str, Any]]:
    rows = cur.execute(
        """
        SELECT season_year, player_id, decision, considered,
               consideration_prob, retirement_prob, random_roll,
               age, team_id, injury_status,
               inputs_json, explanation_json, decided_at, processed_at
        FROM player_retirement_decisions
        WHERE season_year=?
        ORDER BY player_id
        """,
        (int(season_year),),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "season_year": int(r["season_year"]),
                "player_id": str(r["player_id"]),
                "decision": str(r["decision"]),
                "considered": bool(int(r["considered"] or 0)),
                "consideration_prob": float(r["consideration_prob"] or 0.0),
                "retirement_prob": float(r["retirement_prob"] or 0.0),
                "random_roll": float(r["random_roll"] or 0.0),
                "age": int(r["age"] or 0),
                "team_id": str(r["team_id"] or ""),
                "injury_status": str(r["injury_status"] or ""),
                "inputs": json.loads(r["inputs_json"] or "{}"),
                "explanation": json.loads(r["explanation_json"] or "{}"),
                "decided_at": str(r["decided_at"] or ""),
                "processed_at": str(r["processed_at"] or ""),
            }
        )
    return out


def append_retirement_events(cur, *, season_year: int, date_iso: str, player_ids: Iterable[str], now_iso: str) -> int:
    rows = []
    for pid in player_ids:
        event_id = make_event_id("retire", season_year, str(pid), str(date_iso))
        payload = {
            "type": "retirement",
            "season_year": int(season_year),
            "player_id": str(pid),
            "date": str(date_iso),
        }
        rows.append((event_id, int(season_year), str(pid), str(date_iso), "RETIREMENT", _dumps(payload), str(now_iso)))
    if not rows:
        return 0
    cur.executemany(
        """
        INSERT OR IGNORE INTO retirement_events(
            event_id, season_year, player_id, date, event_type, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return int(len(rows))
