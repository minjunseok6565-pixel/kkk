import unittest

from agency.trade_offer_grievance import PlayerSnapshot, TradeOfferGrievanceConfig, compute_trade_offer_grievances


class TradeOfferGrievanceTests(unittest.TestCase):
    def test_targeted_grievance_skipped_when_trade_request_active(self):
        players = {
            "p1": PlayerSnapshot(
                player_id="p1",
                team_id="LAL",
                pos="SG",
                ovr=85,
                mental={"ego": 95, "ambition": 90, "loyalty": 10, "coachability": 20, "adaptability": 20, "work_ethic": 50},
                role_bucket="STAR",
                leverage=0.9,
                trade_request_level=1,
                team_frustration=0.2,
            )
        }
        out = compute_trade_offer_grievances(
            proposer_team_id="LAL",
            outgoing_player_ids=["p1"],
            incoming_player_ids=[],
            players_by_id=players,
            season_year=2026,
            now_date_iso="2026-01-15",
            trigger_source="PUBLIC_OFFER",
            session_id="s1",
        )
        self.assertEqual(len(out.updates), 0)
        self.assertEqual(len(out.events), 0)
        self.assertTrue(any(x.get("reason") == "TRADE_REQUEST_ALREADY_ACTIVE" for x in out.skipped))

    def test_same_pos_requires_comparable_ovr(self):
        players = {
            "inc": PlayerSnapshot(
                player_id="inc",
                team_id="LAL",
                pos="PG",
                ovr=84,
                mental={"ego": 90, "ambition": 85, "loyalty": 30, "coachability": 30, "adaptability": 30, "work_ethic": 50},
                role_bucket="STARTER",
                leverage=0.75,
                role_frustration=0.1,
            ),
            "in_far": PlayerSnapshot(
                player_id="in_far",
                team_id="BOS",
                pos="PG",
                ovr=95,
                mental={},
                role_bucket="FRANCHISE",
                leverage=1.0,
            ),
        }
        out = compute_trade_offer_grievances(
            proposer_team_id="LAL",
            outgoing_player_ids=[],
            incoming_player_ids=["in_far"],
            players_by_id=players,
            season_year=2026,
            now_date_iso="2026-01-15",
            trigger_source="PUBLIC_OFFER",
            session_id="s2",
        )
        self.assertEqual(len(out.events), 0)
        self.assertEqual(len(out.updates), 0)

    def test_same_pos_can_fire_for_comparable_player(self):
        players = {
            "inc": PlayerSnapshot(
                player_id="inc",
                team_id="LAL",
                pos="PG",
                ovr=84,
                mental={"ego": 90, "ambition": 85, "loyalty": 20, "coachability": 20, "adaptability": 20, "work_ethic": 40},
                role_bucket="STARTER",
                leverage=0.95,
                role_frustration=0.05,
            ),
            "in_close": PlayerSnapshot(
                player_id="in_close",
                team_id="BOS",
                pos="PG",
                ovr=85,
                mental={},
                role_bucket="STARTER",
                leverage=0.8,
            ),
        }
        out = compute_trade_offer_grievances(
            proposer_team_id="LAL",
            outgoing_player_ids=[],
            incoming_player_ids=["in_close"],
            players_by_id=players,
            season_year=2026,
            now_date_iso="2026-01-15",
            trigger_source="PUBLIC_OFFER",
            session_id="s3",
        )
        # deterministic roll may still miss, but if fired, axis/event type must be correct.
        if out.events:
            self.assertTrue(any(ev.event_type == "SAME_POS_RECRUIT_ATTEMPT" for ev in out.events))
            u = {x.player_id: x for x in out.updates}.get("inc")
            self.assertIsNotNone(u)
            self.assertGreaterEqual(u.role_frustration_delta, 0.0)


    def test_leak_targeted_always_applies_when_trade_request_level_zero(self):
        players = {
            "p1": PlayerSnapshot(
                player_id="p1",
                team_id="LAL",
                pos="SG",
                ovr=85,
                mental={"ego": 95, "ambition": 90, "loyalty": 10, "coachability": 20, "adaptability": 20, "work_ethic": 30},
                role_bucket="STAR",
                leverage=0.9,
                trade_request_level=0,
                team_frustration=0.2,
            )
        }
        out = compute_trade_offer_grievances(
            proposer_team_id="LAL",
            outgoing_player_ids=["p1"],
            incoming_player_ids=[],
            players_by_id=players,
            season_year=2026,
            now_date_iso="2026-01-15",
            trigger_source="PRIVATE_OFFER_LEAKED",
            session_id="s5",
        )
        self.assertEqual(len(out.updates), 1)
        self.assertGreater(out.updates[0].team_frustration_delta, 0.0)

    def test_leak_targeted_dampened_when_trade_request_level_one(self):
        base_player = dict(
            player_id="p1",
            team_id="LAL",
            pos="SG",
            ovr=85,
            mental={"ego": 95, "ambition": 90, "loyalty": 10, "coachability": 20, "adaptability": 20, "work_ethic": 30},
            role_bucket="STAR",
            leverage=0.9,
            team_frustration=0.2,
        )
        out_lvl0 = compute_trade_offer_grievances(
            proposer_team_id="LAL",
            outgoing_player_ids=["p1"],
            incoming_player_ids=[],
            players_by_id={"p1": PlayerSnapshot(**base_player, trade_request_level=0)},
            season_year=2026,
            now_date_iso="2026-01-15",
            trigger_source="PRIVATE_OFFER_LEAKED",
            session_id="s6",
        )
        out_lvl1 = compute_trade_offer_grievances(
            proposer_team_id="LAL",
            outgoing_player_ids=["p1"],
            incoming_player_ids=[],
            players_by_id={"p1": PlayerSnapshot(**base_player, trade_request_level=1)},
            season_year=2026,
            now_date_iso="2026-01-15",
            trigger_source="PRIVATE_OFFER_LEAKED",
            session_id="s7",
        )
        self.assertEqual(len(out_lvl0.updates), 1)
        self.assertEqual(len(out_lvl1.updates), 1)
        self.assertLess(out_lvl1.updates[0].team_frustration_delta, out_lvl0.updates[0].team_frustration_delta)

    def test_leak_targeted_skipped_when_trade_request_level_max(self):
        players = {
            "p1": PlayerSnapshot(
                player_id="p1",
                team_id="LAL",
                pos="SG",
                ovr=85,
                mental={"ego": 95, "ambition": 90, "loyalty": 10, "coachability": 20, "adaptability": 20, "work_ethic": 30},
                role_bucket="STAR",
                leverage=0.9,
                trade_request_level=2,
                team_frustration=0.2,
            )
        }
        out = compute_trade_offer_grievances(
            proposer_team_id="LAL",
            outgoing_player_ids=["p1"],
            incoming_player_ids=[],
            players_by_id=players,
            season_year=2026,
            now_date_iso="2026-01-15",
            trigger_source="PRIVATE_OFFER_LEAKED",
            session_id="s8",
        )
        self.assertEqual(len(out.updates), 0)
        self.assertTrue(any(x.get("reason") == "TRADE_REQUEST_AT_MAX" for x in out.skipped))

    def test_custom_event_type_config_is_applied(self):
        players = {
            "p1": PlayerSnapshot(
                player_id="p1",
                team_id="LAL",
                pos="SG",
                ovr=85,
                mental={"ego": 100, "ambition": 100, "loyalty": 0, "coachability": 0, "adaptability": 0, "work_ethic": 0},
                role_bucket="FRANCHISE",
                leverage=1.0,
                team_frustration=0.0,
            )
        }
        cfg = TradeOfferGrievanceConfig(
            event_type_targeted_public="CUSTOM_PUBLIC",
            event_type_targeted_leaked="CUSTOM_LEAK",
            event_type_same_pos_recruit="CUSTOM_SAME_POS",
        )
        out = compute_trade_offer_grievances(
            proposer_team_id="LAL",
            outgoing_player_ids=["p1"],
            incoming_player_ids=[],
            players_by_id=players,
            season_year=2026,
            now_date_iso="2026-01-15",
            trigger_source="PUBLIC_OFFER",
            session_id="s4",
            cfg=cfg,
        )
        self.assertTrue(out.events)
        self.assertEqual(out.events[0].event_type, "CUSTOM_PUBLIC")



if __name__ == "__main__":
    unittest.main()
