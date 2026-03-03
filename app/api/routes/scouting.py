from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException

import game_time
import state
from league_repo import LeagueRepo
from schema import normalize_player_id, normalize_team_id
from app.schemas.scouting import ScoutingAssignRequest, ScoutingUnassignRequest

router = APIRouter()





@router.get("/api/scouting/scouts/{team_id}")
async def api_scouting_list_scouts(team_id: str):
    """List scouts for a given team (seeded staff) + current ACTIVE assignment if any."""
    try:
        tid = str(normalize_team_id(team_id, strict=True))
        db_path = state.get_db_path()

        scouts: List[Dict[str, Any]] = []
        with LeagueRepo(db_path) as repo:
            repo.init_db()
            rows = repo._conn.execute(
                """
                SELECT
                    s.scout_id, s.display_name, s.specialty_key, s.profile_json, s.is_active,
                    a.assignment_id, a.target_player_id, a.assigned_date
                FROM scouting_scouts s
                LEFT JOIN scouting_assignments a
                    ON a.scout_id = s.scout_id
                   AND a.status = 'ACTIVE'
                WHERE s.team_id = ?
                ORDER BY s.specialty_key ASC, s.display_name ASC;
                """,
                (tid,),
            ).fetchall()

        for r in rows:
            scout_id = str(r[0])
            display_name = str(r[1] or "Scout")
            specialty_key = str(r[2] or "GENERAL")
            profile_raw = r[3]
            is_active = int(r[4] or 0)

            assignment_id = r[5]
            target_player_id = r[6]
            assigned_date = r[7]

            profile: Dict[str, Any] = {}
            try:
                if profile_raw:
                    profile = json.loads(str(profile_raw))
            except Exception:
                profile = {}

            # Expose only high-level profile info (avoid leaking raw tuning numbers).
            profile_public = {
                "focus_axes": profile.get("focus_axes") if isinstance(profile.get("focus_axes"), list) else [],
                "style_tags": profile.get("style_tags") if isinstance(profile.get("style_tags"), list) else [],
            }

            scouts.append(
                {
                    "scout_id": scout_id,
                    "display_name": display_name,
                    "specialty_key": specialty_key,
                    "is_active": bool(is_active),
                    "profile": profile_public,
                    "active_assignment": (
                        {
                            "assignment_id": str(assignment_id),
                            "target_player_id": str(target_player_id),
                            "assigned_date": str(assigned_date)[:10] if assigned_date else None,
                        }
                        if assignment_id
                        else None
                    ),
                }
            )

        return {"ok": True, "team_id": tid, "scouts": scouts}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list scouts: {e}") from e


@router.post("/api/scouting/assign")
async def api_scouting_assign(req: ScoutingAssignRequest):
    """Assign a scout to a college player (user-driven).

    Policy:
      - If the scout already has an ACTIVE assignment, return 409 (user should unassign first).
      - This endpoint does NOT generate reports immediately; reports are generated at month end.
    """
    try:
        tid = str(normalize_team_id(req.team_id, strict=True))
        scout_id = str(req.scout_id or "").strip()
        if not scout_id:
            raise HTTPException(status_code=400, detail="scout_id is required.")

        pid = str(normalize_player_id(req.player_id, strict=True))

        # assigned_date defaults to in-game date
        assigned_date = game_time.require_date_iso(req.assigned_date or state.get_current_date_as_date().isoformat(), field="assigned_date")
        now = game_time.now_utc_like_iso()
        db_path = state.get_db_path()

        created_assignment: Dict[str, Any] = {}

        with LeagueRepo(db_path) as repo:
            repo.init_db()
            with repo.transaction() as cur:
                # Validate scout exists + belongs to team
                srow = cur.execute(
                    """
                    SELECT scout_id, team_id, is_active
                    FROM scouting_scouts
                    WHERE scout_id=?
                    LIMIT 1;
                    """,
                    (scout_id,),
                ).fetchone()
                if not srow:
                    raise HTTPException(status_code=404, detail=f"Scout not found: {scout_id}")
                if str(srow[1]) != tid:
                    raise HTTPException(status_code=400, detail="Scout does not belong to the given team_id.")
                if int(srow[2] or 0) != 1:
                    raise HTTPException(status_code=409, detail="Scout is inactive.")

                # Guard: one active assignment per scout
                arow = cur.execute(
                    """
                    SELECT assignment_id, target_player_id
                    FROM scouting_assignments
                    WHERE team_id=? AND scout_id=? AND status='ACTIVE'
                    LIMIT 1;
                    """,
                    (tid, scout_id),
                ).fetchone()
                if arow:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Scout already assigned (assignment_id={arow[0]}, target_player_id={arow[1]}). Unassign first.",
                    )

                # Validate player exists (college only for now)
                prow = cur.execute(
                    """
                    SELECT player_id, name, pos, college_team_id, class_year, status
                    FROM college_players
                    WHERE player_id=?
                    LIMIT 1;
                    """,
                    (pid,),
                ).fetchone()
                if not prow:
                    raise HTTPException(status_code=404, detail=f"College player not found: {pid}")

                assignment_id = f"SASN_{uuid4().hex}"
                # Assignment progress state (scouting v2):
                #   - "signals": per-signal mu/sigma updated by monthly checkpoints
                progress = {
                    "schema_version": 2,
                    "signals": {},
                    "last_obs_date": None,
                    "total_obs_days": 0,
                }

                try:
                    cur.execute(
                        """
                        INSERT INTO scouting_assignments(
                            assignment_id, team_id, scout_id, target_player_id, target_kind,
                            assigned_date, status, ended_date, progress_json,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', NULL, ?, ?, ?);
                        """,
                        (
                            assignment_id,
                            tid,
                            scout_id,
                            pid,
                            str(req.target_kind or "COLLEGE"),
                            assigned_date,
                            json.dumps(progress, ensure_ascii=False),
                            now,
                            now,
                        ),
                    )
                except sqlite3.IntegrityError as e:
                    # Covers the partial unique index: uq_scouting_active_assignment_per_scout
                    raise HTTPException(status_code=409, detail=f"Assignment conflict: {e}") from e

                created_assignment = {
                    "assignment_id": assignment_id,
                    "team_id": tid,
                    "scout_id": scout_id,
                    "target_player_id": pid,
                    "target_kind": str(req.target_kind or "COLLEGE"),
                    "assigned_date": assigned_date,
                    "player": {
                        "player_id": str(prow[0]),
                        "name": str(prow[1] or ""),
                        "pos": str(prow[2] or ""),
                        "college_team_id": str(prow[3] or ""),
                        "class_year": int(prow[4] or 0),
                        "status": str(prow[5] or ""),
                    },
                }

        return {"ok": True, "assignment": created_assignment}

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to assign scout: {e}") from e


@router.post("/api/scouting/unassign")
async def api_scouting_unassign(req: ScoutingUnassignRequest):
    """End an ACTIVE scouting assignment (user-driven).

    You can specify either:
      - assignment_id
      - (team_id + scout_id) to end the active assignment for that scout
    """
    try:
        tid = str(normalize_team_id(req.team_id, strict=True))
        ended_date = game_time.require_date_iso(req.ended_date or state.get_current_date_as_date().isoformat(), field="ended_date")
        now = game_time.now_utc_like_iso()
        db_path = state.get_db_path()

        assignment_id = str(req.assignment_id).strip() if req.assignment_id else ""
        scout_id = str(req.scout_id).strip() if req.scout_id else ""
        if not assignment_id and not scout_id:
            raise HTTPException(status_code=400, detail="assignment_id or scout_id is required.")

        ended: Dict[str, Any] = {}

        with LeagueRepo(db_path) as repo:
            repo.init_db()
            with repo.transaction() as cur:
                if assignment_id:
                    row = cur.execute(
                        """
                        SELECT assignment_id, scout_id, target_player_id, status
                        FROM scouting_assignments
                        WHERE assignment_id=? AND team_id=?
                        LIMIT 1;
                        """,
                        (assignment_id, tid),
                    ).fetchone()
                    if not row:
                        raise HTTPException(status_code=404, detail=f"Assignment not found: {assignment_id}")
                    if str(row[3]) != "ACTIVE":
                        raise HTTPException(status_code=409, detail="Assignment is not ACTIVE.")

                    cur.execute(
                        """
                        UPDATE scouting_assignments
                        SET status='ENDED', ended_date=?, updated_at=?
                        WHERE assignment_id=? AND team_id=?;
                        """,
                        (ended_date, now, assignment_id, tid),
                    )
                    ended = {
                        "assignment_id": str(row[0]),
                        "scout_id": str(row[1]),
                        "target_player_id": str(row[2]),
                        "ended_date": ended_date,
                    }
                else:
                    # End active assignment for scout_id
                    row = cur.execute(
                        """
                        SELECT assignment_id, target_player_id
                        FROM scouting_assignments
                        WHERE team_id=? AND scout_id=? AND status='ACTIVE'
                        LIMIT 1;
                        """,
                        (tid, scout_id),
                    ).fetchone()
                    if not row:
                        raise HTTPException(status_code=404, detail="No ACTIVE assignment found for this scout.")

                    assignment_id2 = str(row[0])
                    cur.execute(
                        """
                        UPDATE scouting_assignments
                        SET status='ENDED', ended_date=?, updated_at=?
                        WHERE assignment_id=? AND team_id=?;
                        """,
                        (ended_date, now, assignment_id2, tid),
                    )
                    ended = {
                        "assignment_id": assignment_id2,
                        "scout_id": scout_id,
                        "target_player_id": str(row[1]),
                        "ended_date": ended_date,
                    }

        return {"ok": True, "ended": ended}

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to unassign scout: {e}") from e


@router.get("/api/scouting/reports")
async def api_scouting_reports(
    team_id: str,
    player_id: Optional[str] = None,
    scout_id: Optional[str] = None,
    period_key: Optional[str] = None,  # YYYY-MM
    include_payload: bool = True,
    include_text: bool = True,
    limit: int = 50,
    offset: int = 0,
):
    """List scouting reports for a team (private). Supports filters."""
    try:
        tid = str(normalize_team_id(team_id, strict=True))

        pid: Optional[str] = None
        if player_id:
            pid = str(normalize_player_id(player_id, strict=True))

        sid: Optional[str] = str(scout_id).strip() if scout_id else None
        pk: Optional[str] = str(period_key).strip() if period_key else None

        lim = int(limit)
        off = int(offset)
        if lim <= 0:
            lim = 50
        if lim > 200:
            lim = 200
        if off < 0:
            off = 0

        db_path = state.get_db_path()

        where = ["r.team_id = ?"]
        params: List[Any] = [tid]
        if pid:
            where.append("r.target_player_id = ?")
            params.append(pid)
        if sid:
            where.append("r.scout_id = ?")
            params.append(sid)
        if pk:
            where.append("r.period_key = ?")
            params.append(pk)

        sql = f"""
            SELECT
                r.report_id, r.assignment_id, r.scout_id,
                s.display_name, s.specialty_key,
                r.target_player_id, r.target_kind,
                r.season_year, r.period_key, r.as_of_date,
                r.days_covered, r.player_snapshot_json,
                r.payload_json, r.report_text, r.status,
                r.created_at, r.updated_at
            FROM scouting_reports r
            LEFT JOIN scouting_scouts s ON s.scout_id = r.scout_id
            WHERE {' AND '.join(where)}
            ORDER BY r.as_of_date DESC, r.scout_id ASC
            LIMIT ? OFFSET ?;
        """
        params.extend([lim, off])

        out_reports: List[Dict[str, Any]] = []
        with LeagueRepo(db_path) as repo:
            repo.init_db()
            rows = repo._conn.execute(sql, tuple(params)).fetchall()

        for r in rows:
            snapshot = {}
            payload = {}
            try:
                snapshot = json.loads(str(r[11] or "{}"))
            except Exception:
                snapshot = {}
            if include_payload:
                try:
                    payload = json.loads(str(r[12] or "{}"))
                except Exception:
                    payload = {}

            out_reports.append(
                {
                    "report_id": str(r[0]),
                    "assignment_id": str(r[1]),
                    "scout": {
                        "scout_id": str(r[2]),
                        "display_name": str(r[3] or ""),
                        "specialty_key": str(r[4] or ""),
                    },
                    "target_player_id": str(r[5]),
                    "target_kind": str(r[6] or ""),
                    "season_year": int(r[7] or 0),
                    "period_key": str(r[8] or ""),
                    "as_of_date": str(r[9] or "")[:10],
                    "days_covered": int(r[10] or 0),
                    "player_snapshot": snapshot,
                    "payload": payload if include_payload else None,
                    "report_text": (str(r[13]) if (include_text and r[13] is not None) else None),
                    "status": str(r[14] or ""),
                    "created_at": str(r[15] or ""),
                    "updated_at": str(r[16] or ""),
                }
            )

        return {
            "ok": True,
            "team_id": tid,
            "filters": {
                "player_id": pid,
                "scout_id": sid,
                "period_key": pk,
                "limit": lim,
                "offset": off,
                "include_payload": bool(include_payload),
                "include_text": bool(include_text),
            },
            "count": len(out_reports),
            "reports": out_reports,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list scouting reports: {e}") from e
