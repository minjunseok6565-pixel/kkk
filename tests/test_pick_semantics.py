from trades.pick_semantics import resolve_pick_protection, resolve_swap_outcome


def test_resolve_pick_protection_reverts_owner_and_compensation() -> None:
    res = resolve_pick_protection(
        pick_id="P1",
        slot=4,
        owner_team="LAL",
        original_team="NOP",
        protection={"type": "TOP_N", "n": 10, "compensation": {"label": "2nd", "value": 7.0}},
    )

    assert res.protected is True
    assert res.owner_team_before == "LAL"
    assert res.owner_team_after == "NOP"
    assert res.compensation_required is True


def test_resolve_swap_outcome_tie_no_execute() -> None:
    res = resolve_swap_outcome(
        swap_id="S1",
        pick_id_a="A",
        pick_id_b="B",
        slot_a=8,
        slot_b=8,
        owner_team="BOS",
        owner_a="BOS",
        owner_b="MIA",
    )

    assert res.exercisable is True
    assert res.swap_executed is False
    assert res.chosen_pick_id == "A"
    assert res.owner_a_after == "BOS"
    assert res.owner_b_after == "MIA"


def test_resolve_swap_outcome_other_owner_rule() -> None:
    res = resolve_swap_outcome(
        swap_id="S2",
        pick_id_a="A",
        pick_id_b="B",
        slot_a=3,
        slot_b=11,
        owner_team="BOS",
        owner_a="BOS",
        owner_b="PHX",
    )

    assert res.exercisable is True
    assert res.swap_executed is True
    assert res.chosen_pick_id == "A"
    assert res.other_owner_team == "PHX"
    assert res.owner_a_after == "BOS"
    assert res.owner_b_after == "PHX"
