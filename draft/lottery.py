from __future__ import annotations

"""NBA Draft Lottery (pure).

We follow the modern NBA odds (post-2019 reform) for the 14 lottery-eligible
teams (i.e., the teams that missed the playoffs).
  seeds 1..3: 14.0%
  seed 4:     12.5%
  seed 5:     10.5%
  seed 6:      9.0%
  seed 7:      7.5%
  seed 8:      6.0%
  seed 9:      4.5%
  seed 10:     3.0%
  seed 11:     2.0%
  seed 12:     1.5%
  seed 13:     1.0%
  seed 14:     0.5%

This module draws top-4 winners without replacement.

Input contract:
  - seed_order: 14 team_ids ordered worst -> best (after deterministic tie-break).
Output:
  - LotteryResult from draft.types.
"""

import random
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .types import LotteryResult, TeamId, TeamRecord, norm_team_id
from .standings import iter_tie_groups_in_order


NBA_LOTTERY_ODDS_2019: Tuple[float, ...] = (
    14.0, 14.0, 14.0,
    12.5,
    10.5,
    9.0,
    7.5,
    6.0,
    4.5,
    3.0,
    2.0,
    1.5,
    1.0,
    0.5,
)


# NBA lottery is commonly described in terms of 1000 combinations.
# These are the post-2019 reform allocations by pre-lottery seed (1..14).
NBA_LOTTERY_COMBINATIONS_2019: Tuple[int, ...] = (
    140, 140, 140,
    125,
    105,
    90,
    75,
    60,
    45,
    30,
    20,
    15,
    10,
    5,
)


def compute_effective_lottery_odds_2019_with_ties(
    seed_order: Sequence[TeamId],
    records: Mapping[TeamId, TeamRecord],
    *,
    combinations_by_seed: Sequence[int] = NBA_LOTTERY_COMBINATIONS_2019,
) -> Tuple[Tuple[float, ...], Dict[str, Any]]:
    """Compute tie-adjusted lottery odds for the given seed_order.

    NBA ties are resolved via random drawings. One effect is that when teams are
    tied in record, their *combinations* are shared across the tied seed slots,
    and because there are 1000 total combinations, the split can produce small
    differences (e.g., 3.8% vs 3.7%) depending on remainder allocation.

    This helper approximates that behavior by:
      - grouping consecutive tied teams along the provided seed_order,
      - summing the base combinations across the occupied seed slots,
      - splitting them evenly across the tied teams,
      - assigning any remainder to earlier teams in seed_order (which should
        already reflect a deterministic tie-break drawing in this codebase).

    Returns
    -------
    (odds, audit)
        odds: 14-length tuple of percent odds aligned with seed_order.
        audit: details about tie groups and combination splits.
    """
    seed = [norm_team_id(t) for t in list(seed_order)]
    seed = [t for t in seed if t and t != "FA"]
    if len(seed) != 14:
        raise ValueError(f"seed_order must contain exactly 14 teams, got {len(seed)}")

    base = list(int(x) for x in list(combinations_by_seed))
    if len(base) != 14:
        raise ValueError(f"combinations_by_seed must have length 14, got {len(base)}")

    groups = iter_tie_groups_in_order(records, seed)

    combos_by_team: Dict[TeamId, int] = {}
    audit_groups: List[Dict[str, Any]] = []

    cursor = 0
    for frac, ids in groups:
        k = len(ids)
        start = cursor
        end = cursor + k
        if end > 14:
            raise RuntimeError("tie group cursor exceeded seed list")

        total = int(sum(base[start:end]))
        per = total // k
        rem = total % k

        assigned: Dict[str, int] = {}
        for j, tid in enumerate(ids):
            # Allocate remainders to earlier teams in seed_order.
            c = int(per + (1 if j < rem else 0))
            combos_by_team[tid] = c
            assigned[tid] = c

        audit_groups.append(
            {
                "win_fraction": f"{frac.numerator}/{frac.denominator}",
                "teams": list(ids),
                "seed_range": [int(start + 1), int(end)],
                "base_combinations": list(base[start:end]),
                "total_combinations": int(total),
                "split": dict(assigned),
            }
        )

        cursor = end

    # Convert combinations (out of 1000) to percentage odds.
    odds = tuple(float(combos_by_team[t]) / 10.0 for t in seed)

    audit: Dict[str, Any] = {
        "method": "combinations_1000",
        "base_combinations_by_seed": list(base),
        "tie_groups": audit_groups,
        "total_combinations": int(sum(base)),
    }

    return odds, audit


def _weighted_choice(rng: random.Random, items: Sequence[TeamId], weights: Sequence[float]) -> TeamId:
    total = 0.0
    cum: List[float] = []
    for w in weights:
        try:
            ww = float(w)
        except (TypeError, ValueError):
            ww = 0.0
        if ww < 0:
            ww = 0.0
        total += ww
        cum.append(total)

    if total <= 0:
        # Fallback to uniform deterministic choice.
        return items[int(rng.random() * len(items))]

    x = rng.random() * total
    # linear scan is fine (len <= 14)
    for i, c in enumerate(cum):
        if x <= c:
            return items[i]
    return items[-1]


def run_lottery_top4(
    seed_order: Sequence[TeamId],
    *,
    rng_seed: int,
    odds: Sequence[float] = NBA_LOTTERY_ODDS_2019,
    include_audit: bool = False,
    audit_extras: Optional[Mapping[str, Any]] = None,
) -> LotteryResult:
    """Run the top-4 lottery draw.

    Parameters
    ----------
    seed_order:
        14 teams (worst -> best).
    rng_seed:
        Deterministic RNG seed for reproducibility.
    odds:
        14 odds values corresponding to seed_order.
    include_audit:
        If True, includes draw steps in result.audit.
    """
    seed = [norm_team_id(t) for t in list(seed_order)]
    seed = [t for t in seed if t and t != "FA"]
    if len(seed) != 14:
        raise ValueError(f"seed_order must contain exactly 14 teams, got {len(seed)}")

    odds_list = [float(x) for x in list(odds)]
    if len(odds_list) != 14:
        raise ValueError(f"odds must have length 14, got {len(odds_list)}")

    rng = random.Random(int(rng_seed))

    remaining_items = list(seed)
    remaining_odds = list(odds_list)

    winners: List[TeamId] = []
    audit: Dict[str, Any] = {}

    for draw_no in range(1, 5):
        winner = _weighted_choice(rng, remaining_items, remaining_odds)
        winners.append(winner)
        if include_audit:
            audit.setdefault("draws", []).append(
                {
                    "draw_no": draw_no,
                    "candidates": list(remaining_items),
                    "weights": list(remaining_odds),
                    "winner": winner,
                }
            )
        # remove winner
        idx = remaining_items.index(winner)
        remaining_items.pop(idx)
        remaining_odds.pop(idx)

    if audit_extras is not None:
        # Keep the base shape stable: caller-controlled extras live under a
        # dedicated key to avoid clobbering draw telemetry.
        audit.setdefault("extras", {}).update(dict(audit_extras))

    odds_by_team = {seed[i]: float(odds_list[i]) for i in range(14)}

    return LotteryResult(
        rng_seed=int(rng_seed),
        seed_order=tuple(seed),
        odds_by_team=odds_by_team,
        winners_top4=(winners[0], winners[1], winners[2], winners[3]),
        audit=audit,
    )
