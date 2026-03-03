from __future__ import annotations

"""Draft order construction (pure).

Responsibilities:
  - From team records (regular season) + playoff qualifiers -> determine
    1st-round and 2nd-round original-slot orders for a given draft_year.
  - 1st round:
      * slots 1..14 belong to the 14 teams that missed the playoffs
      * slots 1..4 via lottery draw (NBA odds; ties share combinations)
      * slots 5..14 remaining non-playoff teams in worst->best order
      * slots 15..30 belong to the 16 playoff teams in worst->best order
  - 2nd round:
      * slots 1..30 for all 30 teams in worst->best order

Note:
  This module ONLY outputs "original order" (original teams per slot).
  Actual drafting team (pick owner) is resolved later via DB (draft.finalize).
"""

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .types import DraftOrderPlan, TeamId, TeamRecord, make_pick_id, norm_team_id
from .standings import rank_teams_worst_to_best
from .lottery import compute_effective_lottery_odds_2019_with_ties, run_lottery_top4


def compute_draft_order_plan_from_records(
    *,
    draft_year: int,
    records: Mapping[TeamId, TeamRecord],
    playoff_team_ids: Sequence[TeamId],
    rng_seed: int,
    tie_break_seed: Optional[int] = None,
    use_lottery: bool = True,
    meta: Optional[Dict[str, Any]] = None,
) -> DraftOrderPlan:
    """Compute a DraftOrderPlan from records."""
    draft_year_i = int(draft_year)

    # Normalize and validate playoff qualifiers.
    po_in = [norm_team_id(t) for t in list(playoff_team_ids or [])]
    po_in = [t for t in po_in if t and t != "FA"]
    seen: set[str] = set()
    po: List[TeamId] = []
    for t in po_in:
        if t in seen:
            continue
        seen.add(t)
        po.append(t)

    if len(po) != 16:
        raise ValueError(f"playoff_team_ids must contain 16 unique team ids, got {len(po)}")

    playoff_set = set(po)

    rank = rank_teams_worst_to_best(records, tie_break_seed=tie_break_seed)
    if len(rank) < 30:
        # tolerate partial record dicts by using keys we have
        # (but ordering logic expects 30; caller should provide full 30)
        pass

    # NBA rule: the 14 lottery teams are the teams that miss the playoffs.
    # (Play-in losers are non-playoff teams.)
    rank30 = tuple(rank[:30])
    missing_po = [t for t in po if t not in set(rank30)]
    if missing_po:
        raise ValueError(f"playoff_team_ids contain teams missing from records/rank: {missing_po}")

    lottery_seed_order = tuple([t for t in rank30 if t not in playoff_set])
    playoff_order = tuple([t for t in rank30 if t in playoff_set])

    if len(lottery_seed_order) != 14:
        raise RuntimeError(f"expected 14 lottery teams, got {len(lottery_seed_order)}")
    if len(playoff_order) != 16:
        raise RuntimeError(f"expected 16 playoff teams, got {len(playoff_order)}")

    lottery_result = None
    if use_lottery:
        odds, odds_audit = compute_effective_lottery_odds_2019_with_ties(lottery_seed_order, records)
        lottery_result = run_lottery_top4(
            lottery_seed_order,
            rng_seed=int(rng_seed),
            odds=odds,
            include_audit=False,
            audit_extras={"odds_calc": odds_audit},
        )
        winners = list(lottery_result.winners_top4)
        # slots 1..4 winners in drawn order
        slots_1_4 = tuple(winners)
        # remaining non-playoff teams in worst->best order, excluding winners
        rest = [t for t in lottery_seed_order if t not in set(winners)]
        slots_5_14 = tuple(rest)
        slots_1_14 = slots_1_4 + slots_5_14
    else:
        slots_1_14 = lottery_seed_order

    if len(slots_1_14) != 14:
        raise RuntimeError("round1 bottom14 slots must be length 14")

    slots_15_30 = playoff_order
    if len(slots_15_30) != 16:
        raise RuntimeError("round1 top16 slots must be length 16")

    round1 = tuple(list(slots_1_14) + list(slots_15_30))
    round2 = tuple(rank30)

    pick_order_by_pick_id: Dict[str, int] = {}
    for slot, original_team in enumerate(round1, start=1):
        pick_id = make_pick_id(draft_year_i, 1, original_team)
        pick_order_by_pick_id[pick_id] = int(slot)
    for slot, original_team in enumerate(round2, start=1):
        pick_id = make_pick_id(draft_year_i, 2, original_team)
        pick_order_by_pick_id[pick_id] = int(slot)

    return DraftOrderPlan(
        draft_year=draft_year_i,
        records={str(k): v for k, v in dict(records).items()},
        rank_worst_to_best=tuple(rank30),
        round1_slot_to_original_team=tuple(round1),
        round2_slot_to_original_team=tuple(round2),
        pick_order_by_pick_id=pick_order_by_pick_id,
        lottery_result=lottery_result,
        meta={
            **dict(meta or {}),
            # Persist rule-critical context for debugging/UI.
            "round1_rule": "nba_playoff_based_lottery_14",
            "playoff_team_ids": list(po),
            "lottery_team_ids": list(lottery_seed_order),
        },
    )
