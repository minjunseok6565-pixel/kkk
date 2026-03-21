from trades.valuation.market_pricing import MarketPricer, MarketPricingConfig
from trades.valuation.types import PlayerSnapshot


def _snap(*, age: float, ovr: float, potential: str) -> PlayerSnapshot:
    return PlayerSnapshot(
        kind="player",
        player_id=f"p-{age}-{ovr}-{potential}",
        age=age,
        ovr=ovr,
        attrs={"Potential": potential},
    )


def test_pre_peak_future_prefers_ceiling_and_headroom_over_current_ovr() -> None:
    pricer = MarketPricer(config=MarketPricingConfig())
    high_pot_low_ovr = _snap(age=22, ovr=76, potential="A")
    low_pot_high_ovr = _snap(age=22, ovr=82, potential="B-")

    f1, _ = pricer._player_future_component(snap=high_pot_low_ovr, age=22.0, ovr=76.0)
    f2, _ = pricer._player_future_component(snap=low_pot_high_ovr, age=22.0, ovr=82.0)

    assert f1 > f2
    assert f1 > 0.0
    assert f2 >= 0.0


def test_peak_band_is_future_neutral_and_now_decay_starts_after_peak_end() -> None:
    pricer = MarketPricer(config=MarketPricingConfig())
    peak_snap = _snap(age=28, ovr=84, potential="A-")

    future_peak, meta_peak = pricer._player_future_component(snap=peak_snap, age=28.0, ovr=84.0)
    assert future_peak == 0.0
    assert meta_peak["branch"] == "peak_neutral"

    assert pricer._age_to_now_decay_factor(29.0) == 1.0
    assert pricer._age_to_now_decay_factor(30.0) < 1.0


def test_potential_ceiling_mapping_supports_c_tier_and_fallback() -> None:
    pricer = MarketPricer(config=MarketPricingConfig())
    assert pricer._potential_to_ceiling("C-") == 60.0
    assert pricer._potential_to_ceiling("C") == 65.0
    assert pricer._potential_to_ceiling("C+") == 70.0
    # unknown grade -> fallback to B ceiling
    assert pricer._potential_to_ceiling("Z") == 80.0

