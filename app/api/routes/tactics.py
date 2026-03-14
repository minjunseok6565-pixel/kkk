from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping

from fastapi import APIRouter, HTTPException

import state
from app.schemas.tactics import TeamTacticsUpsertRequest

router = APIRouter()


def _rows_from_payload(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for row in raw:
        if isinstance(row, Mapping):
            out.append(dict(row))
    return out


def _coerce_float(v: Any) -> float | None:
    try:
        num = float(v)
    except Exception:
        return None
    if not math.isfinite(num):
        return None
    return num


def _sanitize_number_dict(raw: Any) -> Dict[str, float]:
    if not isinstance(raw, Mapping):
        return {}
    out: Dict[str, float] = {}
    for k, v in raw.items():
        key = str(k or "").strip()
        if not key:
            continue
        num = _coerce_float(v)
        if num is None:
            continue
        out[key] = num
    return out


def _sanitize_nested_number_dict(raw: Any) -> Dict[str, Dict[str, float]]:
    if not isinstance(raw, Mapping):
        return {}
    out: Dict[str, Dict[str, float]] = {}
    for action, values in raw.items():
        action_key = str(action or "").strip()
        if not action_key:
            continue
        parsed = _sanitize_number_dict(values)
        if parsed:
            out[action_key] = parsed
    return out




def _sanitize_preset_defense_role_by_pid(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    out: Dict[str, str] = {}
    allowed = {"preset-role-G", "preset-role-W", "preset-role-B"}
    for pid, role in raw.items():
        p = str(pid or "").strip()
        r = str(role or "").strip()
        if not p or r not in allowed:
            continue
        out[p] = r
    return out

def _sanitize_context(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    src = dict(raw)
    out: Dict[str, Any] = {}

    tempo = _coerce_float(src.get("tempo_mult"))
    if tempo is not None:
        out["tempo_mult"] = tempo

    draft_snapshot = src.get("USER_PRESET_OFFENSE_DRAFT_V1")
    if isinstance(draft_snapshot, Mapping):
        out["USER_PRESET_OFFENSE_DRAFT_V1"] = dict(draft_snapshot)

    preset_def_roles = _sanitize_preset_defense_role_by_pid(src.get("USER_PRESET_DEFENSE_ROLE_BY_PID_V1"))
    if preset_def_roles:
        out["USER_PRESET_DEFENSE_ROLE_BY_PID_V1"] = preset_def_roles

    for k, v in src.items():
        key = str(k or "").strip()
        if key in {"tempo_mult", "USER_PRESET_OFFENSE_DRAFT_V1", "USER_PRESET_DEFENSE_ROLE_BY_PID_V1"}:
            continue
        out[key] = v
    return out


def _to_engine_tactics(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize frontend tactics payload into engine SSOT format."""
    raw = dict(payload or {})

    action_weight_mult = _sanitize_number_dict(raw.get("action_weight_mult"))
    outcome_by_action_mult = _sanitize_nested_number_dict(raw.get("outcome_by_action_mult"))
    outcome_global_mult = _sanitize_number_dict(raw.get("outcome_global_mult"))
    context = _sanitize_context(raw.get("context"))

    # Already-engine payload: keep shape but ensure dict types.
    if "offense_scheme" in raw or "defense_scheme" in raw or "lineup" in raw:
        out = dict(raw)
        if not isinstance(out.get("lineup"), dict):
            out["lineup"] = {}
        if not isinstance(out.get("minutes"), dict):
            out["minutes"] = {}
        if not isinstance(out.get("rotation_offense_role_by_pid"), dict):
            out["rotation_offense_role_by_pid"] = {}
        if not isinstance(out.get("defense_role_overrides"), dict):
            out["defense_role_overrides"] = {}
        out["action_weight_mult"] = action_weight_mult
        out["outcome_by_action_mult"] = outcome_by_action_mult
        out["outcome_global_mult"] = outcome_global_mult
        out["context"] = context
        return out

    starters = _rows_from_payload(raw.get("starters"))
    rotation = _rows_from_payload(raw.get("rotation"))
    rows = starters + rotation

    starter_pids: List[str] = []
    bench_pids: List[str] = []
    seen: set[str] = set()
    for row in starters:
        pid = str(row.get("pid") or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        starter_pids.append(pid)
    for row in rotation:
        pid = str(row.get("pid") or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        bench_pids.append(pid)

    minutes: Dict[str, float] = {}
    off_by_pid: Dict[str, str] = {}
    def_by_role: Dict[str, str] = {}
    preset_def_by_pid: Dict[str, str] = {}
    for row in rows:
        pid = str(row.get("pid") or "").strip()
        if not pid:
            continue
        try:
            mins = float(row.get("minutes") or 0.0)
        except Exception:
            mins = 0.0
        minutes[pid] = max(0.0, min(48.0, mins))

        off_role = str(row.get("offenseRole") or "").strip()
        if off_role:
            off_by_pid[pid] = off_role

        def_role = str(row.get("defenseRole") or "").strip()
        if def_role:
            if def_role.startswith("preset-role-"):
                preset_def_by_pid[pid] = def_role
            if def_role not in def_by_role:
                def_by_role[def_role] = pid

    rotation_size = int(raw.get("rotation_size") or len(starter_pids) + len(bench_pids) or 10)

    if str(raw.get("defense_scheme") or raw.get("defenseScheme") or "") == "Preset_Defense" and preset_def_by_pid:
        context["USER_PRESET_DEFENSE_ROLE_BY_PID_V1"] = dict(preset_def_by_pid)

    return {
        "offense_scheme": str(raw.get("offense_scheme") or raw.get("offenseScheme") or "Spread_HeavyPnR"),
        "defense_scheme": str(raw.get("defense_scheme") or raw.get("defenseScheme") or "Drop"),
        "lineup": {
            "starters": starter_pids,
            "bench": bench_pids,
        },
        "rotation_size": max(5, rotation_size),
        "minutes": minutes,
        "rotation_offense_role_by_pid": off_by_pid,
        "defense_role_overrides": def_by_role,
        "action_weight_mult": action_weight_mult,
        "outcome_by_action_mult": outcome_by_action_mult,
        "outcome_global_mult": outcome_global_mult,
        "context": context,
    }


def _to_ui_tactics(payload: Any) -> Any:
    if not isinstance(payload, Mapping):
        return payload
    raw = dict(payload)
    if "offenseScheme" in raw or "defenseScheme" in raw:
        return raw

    lineup = raw.get("lineup") if isinstance(raw.get("lineup"), Mapping) else {}
    starter_pids = lineup.get("starters") if isinstance(lineup.get("starters"), list) else []
    bench_pids = lineup.get("bench") if isinstance(lineup.get("bench"), list) else []

    minutes = raw.get("minutes") if isinstance(raw.get("minutes"), Mapping) else {}
    off_map = raw.get("rotation_offense_role_by_pid") if isinstance(raw.get("rotation_offense_role_by_pid"), Mapping) else {}
    def_overrides = raw.get("defense_role_overrides") if isinstance(raw.get("defense_role_overrides"), Mapping) else {}

    def_by_pid: Dict[str, str] = {}
    for role_name, pid in def_overrides.items():
        rp = str(pid or "").strip()
        rr = str(role_name or "").strip()
        if rp and rr and rp not in def_by_pid:
            def_by_pid[rp] = rr

    context = _sanitize_context(raw.get("context"))
    preset_def_by_pid = _sanitize_preset_defense_role_by_pid(context.get("USER_PRESET_DEFENSE_ROLE_BY_PID_V1"))

    def _row_for_pid(pid: Any) -> Dict[str, Any]:
        p = str(pid or "").strip()
        defense_role = str(def_by_pid.get(p) or "")
        if str(raw.get("defense_scheme") or "") == "Preset_Defense":
            defense_role = str(preset_def_by_pid.get(p) or defense_role)
        return {
            "pid": p,
            "offenseRole": str(off_map.get(p) or ""),
            "defenseRole": defense_role,
            "minutes": float(minutes.get(p) or 0.0),
        }

    starters = [_row_for_pid(pid) for pid in starter_pids]
    rotation = [_row_for_pid(pid) for pid in bench_pids]
    preset_draft = None
    if isinstance(context.get("USER_PRESET_OFFENSE_DRAFT_V1"), Mapping):
        preset_draft = dict(context.get("USER_PRESET_OFFENSE_DRAFT_V1") or {})

    return {
        "offenseScheme": str(raw.get("offense_scheme") or "Spread_HeavyPnR"),
        "defenseScheme": str(raw.get("defense_scheme") or "Drop"),
        "starters": starters,
        "rotation": rotation,
        "baselineHash": "",
        "action_weight_mult": _sanitize_number_dict(raw.get("action_weight_mult")),
        "outcome_by_action_mult": _sanitize_nested_number_dict(raw.get("outcome_by_action_mult")),
        "outcome_global_mult": _sanitize_number_dict(raw.get("outcome_global_mult")),
        "context": context,
        "presetOffenseDraft": preset_draft,
    }


@router.get("/api/tactics/{team_id}")
async def api_get_team_tactics(team_id: str):
    tid = str(team_id or "").strip().upper()
    if not tid:
        raise HTTPException(status_code=400, detail="team_id is required")

    record = state.get_team_tactics_snapshot(tid)
    tactics_raw = record.get("tactics") if isinstance(record, dict) else None
    return {
        "team_id": tid,
        "tactics": _to_ui_tactics(tactics_raw) if tactics_raw is not None else None,
        "updated_at_turn": (record.get("updated_at_turn") if isinstance(record, dict) else None),
    }


@router.put("/api/tactics/{team_id}")
async def api_put_team_tactics(team_id: str, req: TeamTacticsUpsertRequest):
    tid = str(team_id or "").strip().upper()
    if not tid:
        raise HTTPException(status_code=400, detail="team_id is required")

    try:
        normalized = _to_engine_tactics(req.tactics)
        saved = state.set_team_tactics(tid, normalized)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    tactics_raw = saved.get("tactics") if isinstance(saved, dict) else {}
    return {
        "ok": True,
        "team_id": tid,
        "tactics": _to_ui_tactics(tactics_raw),
        "updated_at_turn": saved.get("updated_at_turn") if isinstance(saved, dict) else None,
    }
