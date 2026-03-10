from types import SimpleNamespace

from trades.valuation.context_v2 import build_valuation_context_v2
from trades.valuation.decision_policy import DecisionPolicyConfig
from trades.valuation.market_pricing import MarketPricingConfig, MarketPricer
from trades.valuation.package_effects import PackageEffectsConfig
from trades.valuation.team_utility import TeamUtilityConfig
from trades.valuation.types import ContractSnapshot, PickExpectation, PickSnapshot, PlayerSnapshot, SwapSnapshot


class _Provider:
    def __init__(self) -> None:
        c_a = ContractSnapshot(
            contract_id="C_A",
            player_id="PA",
            team_id="A",
            status="ACTIVE",
            signed_date=None,
            start_season_year=2026,
            years=2,
            salary_by_year={2026: 10_000_000, 2027: 12_000_000},
            options=[],
            meta={},
        )
        c_b = ContractSnapshot(
            contract_id="C_B",
            player_id="PB",
            team_id="B",
            status="ACTIVE",
            signed_date=None,
            start_season_year=2026,
            years=1,
            salary_by_year={2026: 8_000_000},
            options=[],
            meta={},
        )
        self.players = {
            "PA": PlayerSnapshot(kind="player", player_id="PA", team_id="A", salary_amount=10_000_000, contract=c_a),
            "PB": PlayerSnapshot(kind="player", player_id="PB", team_id="B", salary_amount=8_000_000, contract=c_b),
        }
        self.picks = {
            "P_A": PickSnapshot(
                kind="pick",
                pick_id="P_A",
                year=2026,
                round=1,
                original_team="A",
                owner_team="B",
                protection={"type": "TOP_N", "n": 2},
            ),
            "P_B": PickSnapshot(
                kind="pick",
                pick_id="P_B",
                year=2026,
                round=1,
                original_team="B",
                owner_team="A",
                protection=None,
            ),
        }
        self.swaps = {
            "S_AB": SwapSnapshot(
                kind="swap",
                swap_id="S_AB",
                pick_id_a="P_A",
                pick_id_b="P_B",
                year=2026,
                round=1,
                owner_team="A",
                active=True,
            )
        }
        self.pick_expectations = {
            "P_A": PickExpectation(pick_id="P_A", expected_pick_number=1.0),
            "P_B": PickExpectation(pick_id="P_B", expected_pick_number=2.0),
        }

    def get_player_snapshot(self, player_id: str):
        return self.players[player_id]

    def get_pick_snapshot(self, pick_id: str):
        return self.picks[pick_id]

    def get_swap_snapshot(self, swap_id: str):
        return self.swaps[swap_id]

    def get_fixed_asset_snapshot(self, asset_id: str):
        raise KeyError(asset_id)

    def get_pick_expectation(self, pick_id: str):
        return self.pick_expectations.get(pick_id)

    def get_pick_distribution(self, pick_id: str):
        return None

    @property
    def current_season_year(self) -> int:
        return 2026

    @property
    def current_date_iso(self) -> str:
        return "2026-01-15"


def test_context_v2_dual_read_uses_real_v1_metrics() -> None:
    provider = _Provider()
    out = build_valuation_context_v2(
        provider=provider,
        decision_context_by_team={
            "A": SimpleNamespace(posture="STAND_PAT"),
            "B": SimpleNamespace(posture="SOFT_SELL"),
        },
        current_season_year=2026,
        current_date_iso="2026-01-15",
        market_pricing_config=MarketPricingConfig(salary_cap=150_000_000),
        team_utility_config=TeamUtilityConfig(),
        package_effects_config=PackageEffectsConfig(),
        decision_policy_config=DecisionPolicyConfig(),
        asset_ids_by_kind={
            "players": ["PA", "PB"],
            "picks": ["P_A", "P_B"],
            "swaps": ["S_AB"],
            "standings_order_worst_to_best": ["A", "B"],
        },
        dual_read=True,
    )

    report = out.diagnostics.diff_report
    assert report is not None
    assert report.missing_metrics == tuple()
    # v1 expects [1,2], v2 distribution should differ because protection/swap semantics are applied
    assert abs(report.pick_ev_delta) > 1e-9


def test_v2_pick_distribution_changes_price_and_reflects_tail_risk() -> None:
    pricer = MarketPricer()
    pick = PickSnapshot(
        kind="pick",
        pick_id="P_A",
        year=2026,
        round=1,
        original_team="A",
        owner_team="B",
        protection={"type": "TOP_N", "n": 2},
    )

    v1 = pricer.price_snapshot(
        pick,
        asset_key="pick:P_A",
        env=SimpleNamespace(current_season_year=2026),
    )
    v2 = pricer.price_snapshot(
        pick,
        asset_key="pick:P_A_v2",
        env=SimpleNamespace(current_season_year=2026),
        pick_distribution={
            "ev_pick": 6.0,
            "variance": 5.0,
            "tail_upside_prob": 0.30,
            "tail_downside_prob": 0.05,
            "p10_pick": 1,
            "p50_pick": 6,
            "p90_pick": 15,
        },
    )

    v1_codes = {s.code for s in v1.steps}
    v2_codes = {s.code for s in v2.steps}

    assert "PICK_PROTECTION_EXPECTATION" not in v1_codes
    assert "PICK_PROTECTION_EXPECTATION" not in v2_codes
    assert "PICK_DISTRIBUTION_VARIANCE" in v2_codes
    assert "PICK_DISTRIBUTION_TAIL_ADJUST" in v2_codes
    assert v2.meta.get("uses_distribution") is True
    assert abs(v2.value.future - v1.value.future) > 1e-9
