from __future__ import annotations

"""Typed helpers and defensive normalization for practice plans/sessions.

Practice data is stored as JSON in SQLite. This module provides:
- normalize_plan / normalize_session: fail-soft normalization
- intensity_for_session_type / intensity_for_pid: compute per-player intensity

We keep this module free of DB I/O.
"""

from typing import Any, Dict, Mapping, Optional

import game_time
from matchengine_v3.tactics import canonical_defense_scheme

from . import config as p_cfg


def normalize_plan(raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Normalize a practice plan dict.

    Plan schema (v1):
      - mode: AUTO | MANUAL

    Unknown keys are preserved to allow forward-compatible extensions.
    """
    base: Dict[str, Any] = {}
    if isinstance(raw, Mapping):
        base.update(dict(raw))

    mode = str(base.get("mode") or "AUTO").upper().strip()
    if mode not in ("AUTO", "MANUAL"):
        mode = "AUTO"
    base["mode"] = mode
    return base


def normalize_session(raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Normalize a practice session dict (fail-soft).

    Session schema (v1):
      - type: OFF_TACTICS | DEF_TACTICS | FILM | SCRIMMAGE | RECOVERY | REST
      - offense_scheme_key: str | None
      - defense_scheme_key: str | None
      - participant_pids: list[str] (only relevant for SCRIMMAGE)
      - non_participant_type: practice type (default: RECOVERY)

    Unknown keys are preserved for forward compatibility.
    """
    base: Dict[str, Any] = {}
    if isinstance(raw, Mapping):
        base.update(dict(raw))

    typ = str(base.get("type") or "FILM").upper().strip()
    if typ not in p_cfg.PRACTICE_TYPES:
        typ = "FILM"
    base["type"] = typ

    # Optional fields
    off = base.get("offense_scheme_key")
    off = str(off).strip() if off else None
    base["offense_scheme_key"] = off

    de = base.get("defense_scheme_key")
    de = canonical_defense_scheme(de) if de else None
    base["defense_scheme_key"] = de

    pids = base.get("participant_pids")
    if not isinstance(pids, list):
        pids = []
    # Normalize to strings, de-duplicate deterministically (preserve order)
    uniq: list[str] = []
    seen: set[str] = set()
    for v in pids:
        s = str(v)
        if not s or s in seen:
            continue
        seen.add(s)
        uniq.append(s)
    base["participant_pids"] = uniq

    nonp = str(base.get("non_participant_type") or p_cfg.SCRIMMAGE_NON_PARTICIPANT_DEFAULT).upper().strip()
    if nonp not in p_cfg.PRACTICE_TYPES:
        nonp = p_cfg.SCRIMMAGE_NON_PARTICIPANT_DEFAULT
    base["non_participant_type"] = nonp

    # Optional embedded date (some callers may include it); validate if present.
    if "date_iso" in base and base.get("date_iso") is not None:
        try:
            base["date_iso"] = game_time.require_date_iso(base.get("date_iso"), field="date_iso")
        except Exception:
            base["date_iso"] = None

    return base


def intensity_for_session_type(typ: str) -> float:
    """Return intensity multiplier for a practice session type."""
    t = str(typ).upper().strip()
    try:
        v = float(p_cfg.INTENSITY_MULT.get(t, 1.0))
    except Exception:
        v = 1.0
    return float(max(0.01, v))


def effective_type_for_pid(session: Mapping[str, Any], pid: str) -> str:
    """Return effective practice type for a player on a given date.

    Scrimmage supports a participant list:
      - participants use SCRIMMAGE
      - non-participants use non_participant_type (default: RECOVERY)

    For other session types, everybody uses the session's type.
    """
    s = normalize_session(session)
    typ = str(s.get("type") or "FILM").upper()
    if typ != "SCRIMMAGE":
        return typ

    pids = set(s.get("participant_pids") or [])
    if str(pid) in pids:
        return "SCRIMMAGE"

    nonp = str(s.get("non_participant_type") or p_cfg.SCRIMMAGE_NON_PARTICIPANT_DEFAULT).upper().strip()
    if nonp not in p_cfg.PRACTICE_TYPES:
        nonp = p_cfg.SCRIMMAGE_NON_PARTICIPANT_DEFAULT
    return nonp


def intensity_for_pid(session: Mapping[str, Any], pid: str) -> float:
    """Return effective practice intensity multiplier for a player on a given date."""
    return intensity_for_session_type(effective_type_for_pid(session, pid))
