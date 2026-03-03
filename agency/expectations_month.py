from __future__ import annotations

"""Month-specific expectations (role/leverage/expected minutes).

This module bridges *month attribution* to the existing expectations logic.

Why a separate expectations pass?
---------------------------------
When a player is traded mid-month, their "role bucket" and leverage should be
computed against the roster context they experienced *in that month*.

Example:
- Player A was a starter on Team X for 8 games, then traded to Team Y and played
  1 game.
- The correct expectation context for that month is Team X (dominant sample),
  not Team Y (current roster).

We do NOT attempt to rebuild historical rosters from transactions.
Instead, we build a month roster per team from boxscore appearances and compute
rank/role/leverage from that cohort.

Design principles
-----------------
- Read-only DB access: caller supplies a sqlite3.Cursor.
- Deterministic and stable: sorting/tie-breaking is stable.
- Compatible: reuses agency.expectations.compute_team_expectations.

Returned values are intended to be used as inputs into `agency.tick` via the
service layer.
"""

import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .config import ExpectationsConfig
from .expectations import compute_team_expectations
from .utils import safe_float, safe_int


@dataclass(frozen=True, slots=True)
class MonthPlayerExpectation:
    """Expectation computed against a month roster for a specific team."""

    player_id: str
    team_id: str

    rank: int
    roster_size: int

    role_bucket: str
    leverage: float
    expected_mpg: float

    source: str = "MONTH_BOX_ROSTER"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "player_id": self.player_id,
            "team_id": self.team_id,
            "rank": int(self.rank),
            "roster_size": int(self.roster_size),
            "role_bucket": str(self.role_bucket),
            "leverage": float(self.leverage),
            "expected_mpg": float(self.expected_mpg),
            "source": str(self.source),
        }


def _chunked(seq: Sequence[str], chunk_size: int) -> Iterable[list[str]]:
    n = max(1, int(chunk_size))
    for i in range(0, len(seq), n):
        yield list(seq[i : i + n])


def fetch_player_snapshots(
    cur: sqlite3.Cursor,
    *,
    player_ids: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    """Fetch minimal player snapshots needed for month expectations.

    Returns dict[player_id] -> {ovr:int, age:int, salary_amount:float}

    Notes:
    - Salary is read from the current roster row; it is used as a proxy for
      contract importance and is acceptable for month leverage ranking.
    - Missing rows are tolerated; callers should handle defaults.
    """
    pids = [str(pid) for pid in (player_ids or []) if str(pid)]
    if not pids:
        return {}

    out: Dict[str, Dict[str, Any]] = {}

    # SQLite has a default variable limit; chunk defensively.
    for chunk in _chunked(pids, 400):
        placeholders = ",".join(["?"] * len(chunk))
        rows = cur.execute(
            f"""
            SELECT
                p.player_id,
                p.ovr,
                p.age,
                r.salary_amount
            FROM players p
            LEFT JOIN roster r ON r.player_id = p.player_id
            WHERE p.player_id IN ({placeholders});
            """,
            list(chunk),
        ).fetchall()

        for pid, ovr, age, salary_amount in rows:
            pid_s = str(pid or "")
            if not pid_s:
                continue
            out[pid_s] = {
                "player_id": pid_s,
                "ovr": safe_int(ovr, 0),
                "age": safe_int(age, 0),
                "salary_amount": safe_float(salary_amount, 0.0),
            }

    return out


def compute_month_expectations(
    cur: sqlite3.Cursor,
    *,
    players_by_team: Mapping[str, Sequence[str]],
    config: ExpectationsConfig,
) -> Dict[Tuple[str, str], MonthPlayerExpectation]:
    """Compute role/leverage/expected minutes for month rosters.

    Args:
        cur: sqlite3 cursor (read-only)
        players_by_team: mapping team_id -> list[player_id] for the processed month
        config: agency ExpectationsConfig (shared with the normal league expectations)

    Returns:
        dict[(player_id, team_id)] -> MonthPlayerExpectation
    """
    # Normalize and dedupe inputs
    team_to_pids: Dict[str, list[str]] = {}
    all_pids: list[str] = []

    for tid_raw, pids in (players_by_team or {}).items():
        tid = str(tid_raw or "").upper()
        if not tid:
            continue
        # Deduplicate while keeping stable ordering
        uniq = sorted({str(pid) for pid in (pids or []) if str(pid)}, key=lambda x: str(x))
        if not uniq:
            continue
        team_to_pids[tid] = uniq
        all_pids.extend(uniq)

    if not team_to_pids:
        return {}

    # Fetch player snapshots once
    snap = fetch_player_snapshots(cur, player_ids=sorted(set(all_pids), key=lambda x: str(x)))

    out: Dict[Tuple[str, str], MonthPlayerExpectation] = {}

    for tid, pids in team_to_pids.items():
        team_players: list[Dict[str, Any]] = []
        for pid in pids:
            s = snap.get(pid)
            if s is None:
                # Defensive default if player row missing
                team_players.append({"player_id": pid, "ovr": 0, "salary_amount": 0.0})
            else:
                team_players.append(
                    {
                        "player_id": pid,
                        "ovr": safe_int(s.get("ovr"), 0),
                        "salary_amount": safe_float(s.get("salary_amount"), 0.0),
                    }
                )

        # Reuse existing pure logic
        team_exp = compute_team_expectations(team_players, config=config)
        roster_size = len(team_players)

        for pid in pids:
            exp = team_exp.get(pid)
            if exp is None:
                # Should not happen (same ids), but fail safe
                continue
            out[(pid, tid)] = MonthPlayerExpectation(
                player_id=pid,
                team_id=tid,
                rank=int(exp.rank),
                roster_size=int(roster_size),
                role_bucket=str(exp.role_bucket),
                leverage=float(exp.leverage),
                expected_mpg=float(exp.expected_mpg),
                source="MONTH_BOX_ROSTER",
            )

    return out
