from __future__ import annotations

"""Role/leverage/expected minutes computation.

This module is pure logic (no DB I/O). It takes simple player snapshots and returns:
- role_bucket (team-internal role)
- leverage (0..1: how much the player can realistically pressure the team)
- expected_mpg (baseline minutes expectation)

Key design goal
---------------
Prevent unrealistic behavior:
- A low-leverage player can be unhappy, but cannot reliably force big actions.
- Mental traits modulate thresholds later; leverage is the gate.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .config import ExpectationsConfig
from .utils import clamp01, safe_float, safe_int


@dataclass(frozen=True, slots=True)
class TeamPlayerSnapshot:
    player_id: str
    ovr: int
    salary_amount: float = 0.0


@dataclass(frozen=True, slots=True)
class PlayerExpectation:
    player_id: str
    rank: int  # 1 = best on team
    role_bucket: str
    leverage: float
    expected_mpg: float


def _role_bucket_for_rank(rank: int, roster_size: int) -> str:
    """Convert OVR rank to a coarse role bucket.

    This uses simple cutoffs tuned for typical NBA rosters (12-15 players).
    For tiny rosters, cutoffs degrade gracefully.
    """
    r = int(rank)
    n = int(roster_size)
    if n <= 0:
        return "UNKNOWN"
    if r <= 1:
        return "FRANCHISE"
    if r <= min(3, n):
        return "STAR"
    if r <= min(5, n):
        return "STARTER"
    if r <= min(8, n):
        return "ROTATION"
    if r <= min(11, n):
        return "BENCH"
    return "GARBAGE"


def compute_team_expectations(
    team_players: Sequence[Mapping[str, Any]] | Sequence[TeamPlayerSnapshot],
    *,
    config: ExpectationsConfig,
) -> Dict[str, PlayerExpectation]:
    """Compute expectations for all players on one team.

    Input snapshots must provide:
    - player_id
    - ovr (int)
    - salary_amount (optional)

    Returns:
        dict[player_id] -> PlayerExpectation

    Notes:
    - Sorting is stable: by OVR desc, then salary desc, then player_id.
    - Leverage blends OVR rank and salary share.
    """

    # Normalize input
    normalized: List[TeamPlayerSnapshot] = []
    for p in team_players or []:
        if isinstance(p, TeamPlayerSnapshot):
            if p.player_id:
                normalized.append(p)
            continue
        if not isinstance(p, Mapping):
            continue
        pid = str(p.get("player_id") or "")
        if not pid:
            continue
        ovr = safe_int(p.get("ovr"), 0)
        sal = safe_float(p.get("salary_amount"), 0.0)
        normalized.append(TeamPlayerSnapshot(player_id=pid, ovr=int(ovr), salary_amount=float(sal)))

    if not normalized:
        return {}

    # Team payroll (only positive salaries)
    team_payroll = sum(max(0.0, float(p.salary_amount)) for p in normalized)

    # Sort by role importance (OVR primary)
    normalized.sort(key=lambda x: (-int(x.ovr), -float(x.salary_amount), str(x.player_id)))

    n = len(normalized)
    out: Dict[str, PlayerExpectation] = {}

    w_ovr = float(config.leverage_weight_ovr)
    w_sal = float(config.leverage_weight_salary)
    # Normalize weights defensively
    w_sum = w_ovr + w_sal
    if w_sum <= 0:
        w_ovr, w_sal = 1.0, 0.0
    else:
        w_ovr, w_sal = w_ovr / w_sum, w_sal / w_sum

    star_share = max(1e-9, float(config.salary_share_star))

    for idx, p in enumerate(normalized):
        rank = idx + 1
        role = _role_bucket_for_rank(rank, n)

        # OVR rank score: best -> 1, worst -> 0
        if n <= 1:
            ovr_rank_score = 1.0
        else:
            ovr_rank_score = 1.0 - (rank - 1) / float(n - 1)

        # Salary score: 20% of payroll -> 1.0 (clamped)
        if team_payroll <= 0:
            salary_score = 0.0
        else:
            share = max(0.0, float(p.salary_amount)) / float(team_payroll)
            salary_score = clamp01(share / star_share)

        leverage = clamp01(w_ovr * ovr_rank_score + w_sal * salary_score)

        exp_mpg = float(config.expected_mpg_by_role.get(role, config.expected_mpg_by_role.get("UNKNOWN", 12.0)))

        out[p.player_id] = PlayerExpectation(
            player_id=p.player_id,
            rank=int(rank),
            role_bucket=role,
            leverage=float(leverage),
            expected_mpg=float(exp_mpg),
        )

    return out


def compute_expectations_for_league(
    roster_rows: Iterable[Mapping[str, Any]],
    *,
    config: ExpectationsConfig,
) -> Dict[str, PlayerExpectation]:
    """Convenience helper: compute expectations for many teams.

    Args:
        roster_rows: iterable rows with fields at least (team_id, player_id, ovr, salary_amount)

    Returns:
        dict[player_id] -> PlayerExpectation
    """
    by_team: Dict[str, List[Dict[str, Any]]] = {}
    for r in roster_rows or []:
        if not isinstance(r, Mapping):
            continue
        tid = str(r.get("team_id") or "").upper()
        pid = str(r.get("player_id") or "")
        if not tid or not pid:
            continue
        by_team.setdefault(tid, []).append(
            {
                "player_id": pid,
                "ovr": safe_int(r.get("ovr"), 0),
                "salary_amount": safe_float(r.get("salary_amount"), 0.0),
            }
        )

    out: Dict[str, PlayerExpectation] = {}
    for _tid, players in by_team.items():
        team_out = compute_team_expectations(players, config=config)
        out.update(team_out)
    return out


