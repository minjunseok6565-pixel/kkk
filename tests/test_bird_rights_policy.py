from __future__ import annotations

from contracts.policy.bird_rights_policy import (
    BIRD_EARLY,
    BIRD_FULL,
    BIRD_NON,
    BIRD_NONE,
    cap_hold_multiplier,
    classify_bird_type,
    first_year_limit_for_bird_type,
    max_raise_pct_for_bird_type,
    max_years_for_bird_type,
)


def test_classify_bird_type_boundaries() -> None:
    assert classify_bird_type(None) == BIRD_NONE
    assert classify_bird_type(0) == BIRD_NONE
    assert classify_bird_type(1) == BIRD_NON
    assert classify_bird_type(2) == BIRD_EARLY
    assert classify_bird_type(3) == BIRD_FULL


def test_cap_hold_multiplier_defaults() -> None:
    assert cap_hold_multiplier(BIRD_FULL) == 1.5
    assert cap_hold_multiplier(BIRD_EARLY) == 1.3
    assert cap_hold_multiplier(BIRD_NON) == 1.2
    assert cap_hold_multiplier(BIRD_NONE) == 0.0


def test_years_and_raise_limits_defaults() -> None:
    assert max_years_for_bird_type(BIRD_FULL) == 5
    assert max_years_for_bird_type(BIRD_EARLY) == 4
    assert max_years_for_bird_type(BIRD_NON) == 4

    assert max_raise_pct_for_bird_type(BIRD_FULL) == 0.08
    assert max_raise_pct_for_bird_type(BIRD_EARLY) == 0.08
    assert max_raise_pct_for_bird_type(BIRD_NON) == 0.05


def test_first_year_limit_non_bird_prev_salary_only() -> None:
    out = first_year_limit_for_bird_type(
        bird_type=BIRD_NON,
        prev_salary=10_000_000,
        exp=5,
        season_year=2027,
        trade_rules=None,
        league_average_salary=None,
    )
    assert out.max_first_year_salary == 12_000_000


def test_first_year_limit_early_bird_max_of_prev_or_avg() -> None:
    out = first_year_limit_for_bird_type(
        bird_type=BIRD_EARLY,
        prev_salary=8_000_000,
        exp=5,
        season_year=2027,
        trade_rules=None,
        league_average_salary=12_000_000,
    )
    # max(8m*1.75, 12m*1.05) = max(14m, 12.6m) = 14m
    assert out.max_first_year_salary == 14_000_000


def test_first_year_limit_full_bird_reuses_exp_based_cap_pct() -> None:
    out = first_year_limit_for_bird_type(
        bird_type=BIRD_FULL,
        prev_salary=20_000_000,
        exp=8,
        season_year=2027,
        trade_rules={"salary_cap": 200_000_000},
        league_average_salary=None,
    )
    # exp 7~9 bucket = 30% by default
    assert out.max_first_year_salary == 60_000_000


def test_first_year_limit_handles_none_values_safely() -> None:
    out_non = first_year_limit_for_bird_type(
        bird_type=BIRD_NON,
        prev_salary=None,
        exp=None,
        season_year=2027,
        trade_rules=None,
        league_average_salary=None,
    )
    assert out_non.max_first_year_salary == 0

    out_early = first_year_limit_for_bird_type(
        bird_type=BIRD_EARLY,
        prev_salary=None,
        exp=None,
        season_year=2027,
        trade_rules=None,
        league_average_salary=None,
    )
    assert out_early.max_first_year_salary == 0

    out_full = first_year_limit_for_bird_type(
        bird_type=BIRD_FULL,
        prev_salary=None,
        exp=None,
        season_year=2027,
        trade_rules=None,
        league_average_salary=None,
    )
    assert out_full.max_first_year_salary == 0
