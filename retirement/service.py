from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from agency.config import DEFAULT_CONFIG as AGENCY_CONFIG
from agency.utils import extract_mental_from_attrs
from league_repo import LeagueRepo
from league_service import LeagueService

from .engine import evaluate_retirement_candidate
from . import repo as retirement_repo
from .types import RetirementInputs


def _coerce_date_iso(v: str | date) -> str:
    if isinstance(v, date):
        return v.isoformat()
    return str(v)[:10]


def _meta_key(season_year: int) -> str:
    return f"retirement_processed_{int(season_year)}"


def _load_or_build_decisions(*, db_path: str, season_year: int, decision_date_iso: str) -> List[Dict[str, Any]]:
    with LeagueRepo(db_path) as repo:
        repo.init_db()
        with repo.transaction() as cur:
            existing = retirement_repo.list_decisions(cur, season_year=int(season_year))
            if existing:
                return list(existing)

            rows = retirement_repo.list_player_inputs(cur, season_year=int(season_year), decision_date_iso=str(decision_date_iso))
            out: List[Dict[str, Any]] = []
            for r in rows:
                mental = extract_mental_from_attrs(r.get("attrs_json"), keys=AGENCY_CONFIG.mental_attr_keys)
                inp = RetirementInputs(
                    player_id=str(r["player_id"]),
                    season_year=int(season_year),
                    age=int(r.get("age") or 0),
                    ovr=int(r.get("ovr") or 0),
                    team_id=str(r.get("team_id") or "FA"),
                    injury_status=str(r.get("injury_status") or "HEALTHY"),
                    injury_severity=int(r.get("injury_severity") or 0),
                    injury_context=dict(r.get("injury_context") or {}),
                    mental=dict(mental or {}),
                )
                dec = evaluate_retirement_candidate(inp)
                out.append(
                    {
                        "season_year": int(dec.season_year),
                        "player_id": str(dec.player_id),
                        "decision": str(dec.decision),
                        "considered": bool(dec.considered),
                        "consideration_prob": float(dec.consider_prob),
                        "retirement_prob": float(dec.retirement_prob),
                        "random_roll": float(dec.random_roll),
                        "age": int(dec.age),
                        "team_id": str(dec.team_id),
                        "injury_status": str(dec.injury_status),
                        "inputs": dict(dec.inputs),
                        "explanation": dict(dec.explanation),
                        "decided_at": str(decision_date_iso),
                        "processed_at": "",
                        "source": "offseason",
                    }
                )
            retirement_repo.upsert_decisions(cur, decisions=out, now_iso=str(decision_date_iso))
            return out


def preview_offseason_retirement(*, db_path: str, season_year: int, decision_date_iso: str) -> Dict[str, Any]:
    decisions = _load_or_build_decisions(
        db_path=str(db_path),
        season_year=int(season_year),
        decision_date_iso=_coerce_date_iso(decision_date_iso),
    )
    retired = [d for d in decisions if str(d.get("decision")) == "RETIRED"]
    considered = [d for d in decisions if bool(d.get("considered"))]
    return {
        "ok": True,
        "season_year": int(season_year),
        "date": _coerce_date_iso(decision_date_iso),
        "count": int(len(decisions)),
        "considered_count": int(len(considered)),
        "retired_count": int(len(retired)),
        "retired_player_ids": [str(d["player_id"]) for d in retired],
        "decisions": decisions,
    }


def process_offseason_retirement(*, db_path: str, season_year: int, decision_date_iso: str) -> Dict[str, Any]:
    sy = int(season_year)
    date_iso = _coerce_date_iso(decision_date_iso)
    mk = _meta_key(sy)

    with LeagueRepo(db_path) as repo:
        repo.init_db()
        row = repo._conn.execute("SELECT value FROM meta WHERE key=?;", (mk,)).fetchone()
        already_done = bool(row is not None and str(row["value"]) == "1")
    if already_done:
        return {
            "ok": True,
            "skipped": True,
            "reason": "already_done",
            "season_year": sy,
            "retired_count": 0,
            "retired_player_ids": [],
        }

    decisions = _load_or_build_decisions(db_path=str(db_path), season_year=sy, decision_date_iso=date_iso)
    retired_ids = [str(d["player_id"]) for d in decisions if str(d.get("decision")) == "RETIRED"]

    with LeagueService.open(db_path) as svc:
        with svc._atomic() as cur:
            now_iso = date_iso
            # 1) deactivate contract/active index + roster status
            for pid in retired_ids:
                cur.execute(
                    "UPDATE contracts SET is_active=0, status='RETIRED', updated_at=? WHERE player_id=? AND is_active=1;",
                    (now_iso, str(pid)),
                )
                cur.execute("DELETE FROM active_contracts WHERE player_id=?;", (str(pid),))
                cur.execute(
                    "UPDATE roster SET status='retired', updated_at=? WHERE player_id=?;",
                    (now_iso, str(pid)),
                )

            # 2) mark processed_at on decision rows
            cur.execute(
                "UPDATE player_retirement_decisions SET processed_at=?, updated_at=? WHERE season_year=? AND decision='RETIRED';",
                (date_iso, now_iso, sy),
            )

            # 3) append retirement events (SSOT)
            retirement_repo.append_retirement_events(
                cur,
                season_year=sy,
                date_iso=date_iso,
                player_ids=retired_ids,
                now_iso=now_iso,
            )

            # 4) idempotency flag
            cur.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value;",
                (mk, "1"),
            )

        # 5) transaction log rows via service helper (same shape/path)
        season_year_ssot = int(sy)
        txs = []
        for pid in retired_ids:
            txs.append(
                {
                    "type": "retirement",
                    "date": date_iso,
                    "source": "offseason_retirement",
                    "player_id": str(pid),
                    "season_year": season_year_ssot,
                }
            )
        if txs:
            svc.append_transactions(txs)

        svc.repo.validate_integrity()

    return {
        "ok": True,
        "season_year": sy,
        "retired_count": int(len(retired_ids)),
        "retired_player_ids": retired_ids,
    }
