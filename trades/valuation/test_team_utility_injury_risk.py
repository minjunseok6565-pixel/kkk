from __future__ import annotations

from trades.valuation.team_utility import TeamUtilityAdjuster
from trades.valuation.types import PlayerSnapshot


def _mk_adjuster() -> TeamUtilityAdjuster:
    return TeamUtilityAdjuster()


def test_extract_injury_inputs_payload_flags() -> None:
    adj = _mk_adjuster()
    snap = PlayerSnapshot(
        kind="player",
        player_id="p1",
        meta={
            "injury": {
                "flags": {"fallback_used": True},
                "current": {"status": "OUT", "is_out": True, "days_to_return": 45},
                "history": {"recent_count_180d": 2},
                "health_credit_inputs": {"availability_rate_365d": 0.88},
            }
        },
    )

    out = adj._extract_injury_inputs(snap)

    assert out["injury_payload_present"] is True
    assert out["fallback_used"] is True
    assert out["current"]["status"] == "OUT"
    assert out["history"]["recent_count_180d"] == 2


def test_injury_history_risk_increases_with_critical_and_repeat() -> None:
    adj = _mk_adjuster()

    low, _ = adj._injury_history_risk(
        {
            "history": {
                "recent_count_180d": 1,
                "critical_count_365d": 0,
                "same_part_repeat_365d_max": 1,
                "weighted_severity_365d": 1.1,
            }
        }
    )
    high, _ = adj._injury_history_risk(
        {
            "history": {
                "recent_count_180d": 1,
                "critical_count_365d": 3,
                "same_part_repeat_365d_max": 4,
                "weighted_severity_365d": 2.2,
            }
        }
    )

    assert 0.0 <= low <= 1.0
    assert 0.0 <= high <= 1.0
    assert high > low


def test_injury_current_risk_out_critical_severe_gt_returning_mild() -> None:
    adj = _mk_adjuster()

    returning_mild, _ = adj._injury_current_risk(
        {
            "current": {
                "status": "RETURNING",
                "is_out": False,
                "is_returning": True,
                "days_to_return": 20,
                "body_part": "ANKLE",
                "severity": 1,
            }
        }
    )

    out_critical, _ = adj._injury_current_risk(
        {
            "current": {
                "status": "OUT",
                "is_out": True,
                "is_returning": False,
                "days_to_return": 220,
                "body_part": "KNEE",
                "severity": 4,
            }
        }
    )

    assert 0.0 <= returning_mild <= 1.0
    assert 0.0 <= out_critical <= 1.0
    assert out_critical > returning_mild


def test_health_relief_higher_for_available_and_clean_history() -> None:
    adj = _mk_adjuster()

    low, _ = adj._health_relief(
        {
            "history": {"critical_count_365d": 2, "same_part_repeat_365d_max": 4},
            "health": {"availability_rate_365d": 0.70},
        }
    )
    high, _ = adj._health_relief(
        {
            "history": {"critical_count_365d": 0, "same_part_repeat_365d_max": 1},
            "health": {"availability_rate_365d": 0.97},
        }
    )

    assert 0.0 <= low <= 1.0
    assert 0.0 <= high <= 1.0
    assert high > low
