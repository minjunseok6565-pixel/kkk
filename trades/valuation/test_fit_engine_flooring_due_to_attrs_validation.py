from __future__ import annotations

from types import SimpleNamespace

from trades.valuation.fit_engine import FitEngine
from trades.valuation.types import PlayerSnapshot


def _ctx(*, threshold: float = 0.70, fit_scale: float = 1.80, need_map: dict[str, float] | None = None):
    return SimpleNamespace(
        need_map=(need_map or {"OFF_SPOTUP_SPACER": 1.0}),
        knobs=SimpleNamespace(min_fit_threshold=threshold, fit_scale=fit_scale),
        policies=None,
    )


def test_potential_string_is_ignored_for_supply_computation() -> None:
    engine = FitEngine()
    snap = PlayerSnapshot(
        kind="player",
        player_id="p1",
        attrs={
            "Three-Point Shot": 92,
            "Shot IQ": 88,
            "Mid-Range Shot": 84,
            "Offensive Consistency": 86,
            "Hands": 80,
            "Pass Accuracy": 78,
            "Stamina": 85,
            "Potential": "A+",  # SSOT non-numeric key should be ignored
        },
    )

    result = engine.assess_player_fit(snap, _ctx())

    assert "Potential" not in result.supply
    assert result.fit.fit_score > 0.70
    assert result.fit_factor > 1.0
    assert result.threshold_penalty == 1.0


def test_numeric_attrs_only_produce_non_floor_fit_when_skill_is_high() -> None:
    engine = FitEngine()
    snap = PlayerSnapshot(
        kind="player",
        player_id="p2",
        attrs={
            "Three-Point Shot": 92,
            "Shot IQ": 88,
            "Mid-Range Shot": 84,
            "Offensive Consistency": 86,
            "Hands": 80,
            "Pass Accuracy": 78,
            "Stamina": 85,
        },
    )

    result = engine.assess_player_fit(snap, _ctx())

    assert result.fit.fit_score > 0.70
    assert result.fit_factor > 1.0
    assert result.threshold_penalty == 1.0


def test_other_non_numeric_attr_still_falls_back_to_empty_supply() -> None:
    engine = FitEngine()
    snap = PlayerSnapshot(
        kind="player",
        player_id="p3",
        attrs={
            "Three-Point Shot": 92,
            "Shot IQ": 88,
            "Nickname": "Splash",  # not filtered by Potential-only guard
        },
    )

    result = engine.assess_player_fit(snap, _ctx())

    assert result.supply == {}
    assert result.fit.fit_score == 0.0
    assert result.fit_factor == 0.70
    assert result.threshold_penalty == 0.35
