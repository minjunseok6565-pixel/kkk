import random
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from trades.generation.dealgen import core
from trades.generation.dealgen.types import (
    DealCandidate,
    DealGeneratorBudget,
    DealGeneratorConfig,
    DealGeneratorStats,
    TargetCandidate,
)
from trades.models import Deal, PlayerAsset


class _TickCtxStub:
    def __init__(self):
        self.current_date = date(2026, 2, 10)

    def get_team_situation(self, team_id: str):
        return SimpleNamespace(
            trade_posture="BUY",
            constraints=SimpleNamespace(
                cooldown_active=False,
                cap_space=30_000_000,
                apron_status="OVER_CAP",
                deadline_pressure=0.0,
            ),
            needs=[],
            time_horizon="RE_TOOL",
        )


def _budget(max_validations: int = 80, max_evaluations: int = 80) -> DealGeneratorBudget:
    return DealGeneratorBudget(
        max_targets=8,
        beam_width=6,
        max_attempts_per_target=20,
        max_validations=max_validations,
        max_evaluations=max_evaluations,
        max_repairs=2,
    )


def _target() -> TargetCandidate:
    return TargetCandidate(
        player_id="target_1",
        from_team="LAL",
        need_tag="WING",
        tag_strength=0.8,
        market_total=35.0,
        salary_m=18.0,
        remaining_years=2.0,
        age=26.0,
    )


def _candidate(stage_tag: str) -> DealCandidate:
    deal = Deal(
        teams=["BOS", "LAL"],
        legs={
            "BOS": [PlayerAsset(kind="player", player_id="send_1")],
            "LAL": [PlayerAsset(kind="player", player_id="target_1")],
        },
    )
    return DealCandidate(
        deal=deal,
        buyer_id="BOS",
        seller_id="LAL",
        focal_player_id="target_1",
        archetype="template_first",
        skeleton_id="s1",
        skeleton_domain="template" if stage_tag == "stage:template" else "tier_score",
        target_tier="MVP",
        contract_tag="FAIR",
        compat_archetype="template_first",
        tags=[stage_tag],
    )


def _proposal_from_tags(deal: Deal, tags):
    return SimpleNamespace(
        deal=deal,
        buyer_id="BOS",
        seller_id="LAL",
        score=1.0,
        tags=tuple(tags),
        buyer_decision=SimpleNamespace(required_surplus=0.0),
        seller_decision=SimpleNamespace(required_surplus=0.0),
        buyer_eval=SimpleNamespace(net_surplus=1.0),
        seller_eval=SimpleNamespace(net_surplus=1.0),
    )


class TemplateEvalFallbackInCoreTests(unittest.TestCase):
    def _run_buy_mode(
        self,
        *,
        cfg: DealGeneratorConfig,
        budget: DealGeneratorBudget,
        discard_template: bool,
        repair_v_used: int = 0,
    ):
        stats = DealGeneratorStats(mode="BUY")
        build_calls = []

        def build_side_effect(*args, **kwargs):
            phase = kwargs.get("generation_phase", "combined")
            build_calls.append(phase)
            if phase == "template":
                return [_candidate("stage:template")]
            if phase == "fallback":
                return [_candidate("stage:fallback")]
            return [_candidate("stage:fallback")]

        def discard_side_effect(prop, _cfg):
            tags = tuple(getattr(prop, "tags", tuple()) or tuple())
            if "stage:template" in tags:
                return bool(discard_template)
            return False

        with (
            patch("trades.generation.dealgen.core.select_targets_buy", return_value=[_target()]),
            patch("trades.generation.dealgen.core._active_public_listing_meta_by_player", return_value={}),
            patch("trades.generation.dealgen.core.build_offer_skeletons_buy", side_effect=build_side_effect),
            patch("trades.generation.dealgen.core.expand_variants", side_effect=lambda *a, **k: a[3]),
            patch("trades.generation.dealgen.core._beam_select_candidates", side_effect=lambda c, **k: c),
            patch("trades.generation.dealgen.core.repair_until_valid", side_effect=lambda c, *a, **k: (True, c, int(repair_v_used))),
            patch("trades.generation.dealgen.core.evaluate_and_score", side_effect=lambda deal, **k: (_proposal_from_tags(deal, k.get("tags", tuple())), 0)),
            patch("trades.generation.dealgen.core.maybe_apply_pick_protection_variants", side_effect=lambda p, **k: (p, 0, 0)),
            patch("trades.generation.dealgen.core.maybe_apply_sweeteners", side_effect=lambda p, **k: (p, 0, 0)),
            patch("trades.generation.dealgen.core._apply_target_repeat_penalty", side_effect=lambda p, **k: p),
            patch("trades.generation.dealgen.core._should_discard_prop", side_effect=discard_side_effect),
        ):
            core._generate_buy_mode(
                initiator_buyer_id="BOS",
                tick_ctx=_TickCtxStub(),
                catalog=SimpleNamespace(outgoing_by_team={"BOS": SimpleNamespace(players={}, picks={}, swaps={})}),
                config=cfg,
                budget=budget,
                rng=random.Random(7),
                max_results=5,
                stats=stats,
            )

        return stats, build_calls

    def test_fallback_invoked_when_template_candidates_are_all_discarded(self):
        cfg = DealGeneratorConfig(template_first_enabled=True, template_first_fallback_enabled=True)
        stats, build_calls = self._run_buy_mode(cfg=cfg, budget=_budget(), discard_template=True)

        self.assertIn("template", build_calls)
        self.assertIn("fallback", build_calls)
        self.assertEqual(stats.failures_by_kind.get("template_stage_all_discarded", 0), 1)
        self.assertEqual(stats.failures_by_kind.get("fallback_stage_invoked", 0), 1)

    def test_fallback_skipped_when_template_keeps_a_proposal(self):
        cfg = DealGeneratorConfig(template_first_enabled=True, template_first_fallback_enabled=True)
        stats, build_calls = self._run_buy_mode(cfg=cfg, budget=_budget(), discard_template=False)

        self.assertIn("template", build_calls)
        self.assertNotIn("fallback", build_calls)
        self.assertEqual(stats.failures_by_kind.get("fallback_stage_invoked", 0), 0)

    def test_fallback_skipped_when_budget_is_exhausted_after_pass1(self):
        cfg = DealGeneratorConfig(template_first_enabled=True, template_first_fallback_enabled=True)
        tight_budget = _budget(max_validations=1, max_evaluations=80)

        # pass1 중 validation 소모로 budget 초과를 만들고 fallback 진입을 막는다.
        stats, build_calls = self._run_buy_mode(
            cfg=cfg,
            budget=tight_budget,
            discard_template=True,
            repair_v_used=2,
        )

        self.assertIn("template", build_calls)
        self.assertNotIn("fallback", build_calls)
        self.assertEqual(stats.failures_by_kind.get("template_stage_all_discarded", 0), 1)
        self.assertEqual(stats.failures_by_kind.get("fallback_stage_invoked", 0), 0)


if __name__ == "__main__":
    unittest.main()
