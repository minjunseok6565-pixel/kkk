from __future__ import annotations

"""Defaults for the team practice subsystem.

Keep defaults minimal and conservative:
- Default plan: AUTO
- Default auto session (when no schedule is set): FILM

This is intentionally separate from practice.ai, which contains rule-based CPU
logic. Defaults here are used for missing data, error recovery, and MANUAL mode.
"""

from typing import Any, Dict, Mapping, Optional

from . import config as p_cfg


def default_team_practice_plan(*, team_id: str, season_year: int) -> Dict[str, Any]:  # noqa: ARG001
    """Return a default practice plan for a team.

    The plan is season-scoped but contains only policy for now.
    """

    return {
        "mode": "AUTO",
    }


def default_session(
    *,
    typ: str = "FILM",
    offense_scheme_key: Optional[str] = None,
    defense_scheme_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a conservative default session dict (not normalized)."""

    t = str(typ).upper().strip() or "FILM"
    if t not in p_cfg.PRACTICE_TYPES:
        t = "FILM"
    return {
        "type": t,
        "offense_scheme_key": offense_scheme_key,
        "defense_scheme_key": defense_scheme_key,
        "participant_pids": [],
        "non_participant_type": p_cfg.SCRIMMAGE_NON_PARTICIPANT_DEFAULT,
    }
