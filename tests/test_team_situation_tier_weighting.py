from __future__ import annotations

from datetime import date
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from data.team_situation import (
    TeamConstraints,
    TeamSituationContext,
    TeamSituationEvaluator,
    TeamSituationSignals,
    _performance_weight_by_progress,
)


_TIER_ORDER = {
    "CONTENDER": 0,
    "PLAYOFF_BUYER": 1,
    "FRINGE": 2,
    "RESET": 3,
    "REBUILD": 4,
    "TANK": 5,
}


def _ctx(progress: float) -> TeamSituationContext:
    return TeamSituationContext(
        current_date=date(2026, 1, 15),
        league_ctx={},
        workflow_state={},
        trade_state={},
        assets_snapshot={},
        contract_ledger={},
        standings={"east": [], "west": []},
        records_index={"BOS": {"season_progress": progress}},
        team_stats={},
        player_stats={},
        trade_market={},
        trade_memory={},
        negotiations={},
        team_ratings_index={},
        team_ban_index={},
    )


def _signals(
    *,
    win_pct: float,
    net_rating: float,
    point_diff_pg: float,
    trend: float,
    star_power: float,
    depth: float,
    role_fit_health: float,
    gb_to_6th: float | None = None,
    gb_to_10th: float | None = None,
) -> TeamSituationSignals:
    return TeamSituationSignals(
        win_pct=win_pct,
        conf_rank=12,
        gb=5.0,
        gb_to_6th=gb_to_6th,
        gb_to_10th=gb_to_10th,
        point_diff_pg=point_diff_pg,
        last10_win_pct=max(0.0, min(1.0, win_pct + trend)),
        trend=trend,
        net_rating=net_rating,
        ortg=112.0,
        drtg=113.0,
        ortg_pct=0.5,
        def_pct=0.5,
        net_pct=0.5,
        star_power=star_power,
        depth=depth,
        core_age=27.0,
        young_core=0.45,
        asset_score=0.5,
        flexibility=0.5,
        style_3_rate=0.38,
        style_rim_rate=0.32,
        role_fit_health=role_fit_health,
        expiring_top8_count=0,
        expiring_top8_ovr_sum=0.0,
        re_sign_pressure=0.0,
    )


def _constraints() -> TeamConstraints:
    return TeamConstraints(
        payroll=160_000_000,
        cap_space=-20_000_000,
        apron_status="OVER_CAP",
        hard_flags={},
        cooldown_active=False,
        deadline_pressure=0.2,
    )


def _classify(progress: float, sig: TeamSituationSignals) -> str:
    evaluator = TeamSituationEvaluator(ctx=_ctx(progress), db_path=":memory:")
    tier, *_ = evaluator._classify_and_build_outputs(
        tid="BOS",
        signals=sig,
        constraints=_constraints(),
        role_needs=[],
        roster_sig={"top3_avg": 80.0, "top8_avg": 76.0},
        asset_sig={"asset_score": sig.asset_score, "max_years": 7, "firsts": 2, "seconds": 3, "swaps": 1},
        style_sig={"three_rate": sig.style_3_rate, "rim_rate": sig.style_rim_rate},
    )
    return tier


def test_early_season_high_overall_losing_streak_is_lifted_to_playoff_buyer() -> None:
    sig = _signals(
        win_pct=0.20,
        net_rating=-8.0,
        point_diff_pg=-9.0,
        trend=-0.10,
        star_power=0.90,
        depth=0.82,
        role_fit_health=0.78,
    )

    tier = _classify(0.10, sig)

    assert tier == "PLAYOFF_BUYER"


def test_late_season_poor_results_pull_tier_down_vs_early_season() -> None:
    sig = _signals(
        win_pct=0.20,
        net_rating=-8.0,
        point_diff_pg=-9.0,
        trend=-0.10,
        star_power=0.80,
        depth=0.75,
        role_fit_health=0.78,
    )

    tier_early = _classify(0.10, sig)
    tier_late = _classify(0.75, sig)

    assert _TIER_ORDER[tier_late] >= _TIER_ORDER[tier_early]


def test_late_bubble_rule_promotes_rebuild_or_tank_to_fringe() -> None:
    sig = _signals(
        win_pct=0.42,
        net_rating=-2.5,
        point_diff_pg=-2.0,
        trend=0.01,
        star_power=0.20,
        depth=0.20,
        role_fit_health=0.20,
        gb_to_6th=6.0,
        gb_to_10th=1.5,
    )

    tier = _classify(0.60, sig)

    assert tier == "FRINGE"


def test_performance_weight_is_monotonic_by_progress() -> None:
    ps = [0.0, 0.1, 0.18, 0.30, 0.49, 0.60, 0.73, 0.90, 1.0]
    ws = [_performance_weight_by_progress(p) for p in ps]

    assert ws == sorted(ws)


def test_weight_schedule_is_stable_near_breakpoints() -> None:
    eps = 0.001
    near_049 = abs(_performance_weight_by_progress(0.49 - eps) - _performance_weight_by_progress(0.49 + eps))
    near_073 = abs(_performance_weight_by_progress(0.73 - eps) - _performance_weight_by_progress(0.73 + eps))

    assert near_049 < 0.01
    assert near_073 < 0.01
