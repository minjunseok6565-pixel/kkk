from trades.valuation.draft_lottery_rules import DraftLotteryRules
from trades.valuation.pick_distribution import build_pick_distributions_from_standings
from trades.valuation.types import PickSnapshot, SwapSnapshot


def test_pick_distribution_swap_tie_uses_settlement_noop_semantics() -> None:
    rules = DraftLotteryRules(
        season_year=2026,
        team_count=2,
        lottery_team_count=2,
        lottery_pick_count=1,
        first_round_pmf_by_standing={
            1: {2: 1.0},
            2: {2: 1.0},
        },
    )

    picks = [
        PickSnapshot(
            kind="pick",
            pick_id="P_A",
            year=2026,
            round=1,
            original_team="A",
            owner_team="A",
            protection=None,
        ),
        PickSnapshot(
            kind="pick",
            pick_id="P_B",
            year=2026,
            round=1,
            original_team="B",
            owner_team="B",
            protection=None,
        ),
    ]
    swaps = [
        SwapSnapshot(
            kind="swap",
            swap_id="S_AB",
            pick_id_a="P_A",
            pick_id_b="P_B",
            year=2026,
            round=1,
            owner_team="A",
            active=True,
        )
    ]

    out = build_pick_distributions_from_standings(
        picks=picks,
        swaps=swaps,
        standings_order_worst_to_best=["A", "B"],
        season_rules=rules,
    )

    bundle_a = out["P_A"]
    assert bundle_a.pmf == {2: 1.0}
    assert any(note == "SWAP_NOOP:S_AB" for note in bundle_a.scenario_notes)
