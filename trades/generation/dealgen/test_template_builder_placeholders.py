import random
import unittest
from types import SimpleNamespace

from trades.generation.dealgen.skeleton_builders_template import build_template_first_skeletons
from trades.generation.dealgen.skeleton_registry import BuildContext
from trades.generation.dealgen.template_specs import get_templates_for_tier
from trades.generation.dealgen.types import DealGeneratorBudget, DealGeneratorConfig, TargetCandidate


class TemplateBuilderPlaceholdersTests(unittest.TestCase):
    def _budget(self) -> DealGeneratorBudget:
        return DealGeneratorBudget(
            max_targets=8,
            beam_width=6,
            max_attempts_per_target=20,
            max_validations=120,
            max_evaluations=80,
            max_repairs=2,
        )

    def _ctx(self, *, players: dict, picks: dict, pick_buckets: dict) -> BuildContext:
        out_cat = SimpleNamespace(
            players=players,
            picks=picks,
            swaps={},
            pick_ids_by_bucket=pick_buckets,
        )
        catalog = SimpleNamespace(outgoing_by_team={"BOS": out_cat})
        target = TargetCandidate(
            player_id="target_1",
            from_team="LAL",
            need_tag="WING",
            tag_strength=0.9,
            market_total=30.0,
            salary_m=20.0,
            remaining_years=3.0,
            age=27.0,
        )
        return BuildContext(
            mode="BUY",
            buyer_id="BOS",
            seller_id="LAL",
            target=target,
            tick_ctx=SimpleNamespace(),
            catalog=catalog,
            config=DealGeneratorConfig(),
            budget=self._budget(),
            rng=random.Random(7),
            banned_asset_keys=set(),
            banned_players=set(),
            banned_receivers_by_player={},
        )

    def test_placeholder_templates_are_loaded(self):
        templates = get_templates_for_tier("MVP", "FAIR")
        ids = {t.template_id for t in templates}
        self.assertGreaterEqual(len(templates), 4)
        self.assertIn("tpl_mvp_placeholder_1", ids)
        self.assertIn("tpl_mvp_placeholder_4", ids)

    def test_slot_matching_failure_returns_zero_candidates(self):
        ctx = self._ctx(players={}, picks={}, pick_buckets={"FIRST_SAFE": tuple(), "FIRST_SENSITIVE": tuple(), "SECOND": tuple()})
        out = build_template_first_skeletons(
            ctx,
            tier="MVP",
            skeleton_id_prefix="template.mvp",
            max_candidates=4,
        )
        self.assertEqual(out, [])

    def test_slot_match_and_score_gate_builds_candidate(self):
        players = {
            "p_send": SimpleNamespace(
                player_id="p_send",
                market=SimpleNamespace(total=70.0),
                snap=SimpleNamespace(ovr=97.0),
                salary_m=20.0,
                remaining_years=3.0,
                return_ban_teams=tuple(),
            )
        }
        picks = {
            "pick_1": SimpleNamespace(pick_id="pick_1", snap=SimpleNamespace(round=1, protection=None)),
            "pick_2": SimpleNamespace(pick_id="pick_2", snap=SimpleNamespace(round=1, protection=None)),
            "pick_3": SimpleNamespace(pick_id="pick_3", snap=SimpleNamespace(round=2, protection=None)),
        }
        pick_buckets = {
            "FIRST_SAFE": ("pick_1",),
            "FIRST_SENSITIVE": ("pick_2",),
            "SECOND": ("pick_3",),
        }
        ctx = self._ctx(players=players, picks=picks, pick_buckets=pick_buckets)

        out = build_template_first_skeletons(
            ctx,
            tier="MVP",
            skeleton_id_prefix="template.mvp",
            max_candidates=4,
        )

        self.assertGreaterEqual(len(out), 1)
        self.assertIn("template:first", out[0].tags)
        self.assertTrue(any(t.startswith("template_id:") for t in out[0].tags))


if __name__ == "__main__":
    unittest.main()
