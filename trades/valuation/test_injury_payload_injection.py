import sqlite3
import unittest
from unittest.mock import patch

from trades.valuation.injury_features import build_injury_payloads_for_players
from trades.valuation.data_context import build_repo_valuation_data_context


class InjuryPayloadInjectionTests(unittest.TestCase):
    def _conn_with_injury_tables(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE player_injury_state (
                player_id TEXT PRIMARY KEY,
                team_id TEXT,
                status TEXT,
                injury_id TEXT,
                start_date TEXT,
                out_until_date TEXT,
                returning_until_date TEXT,
                body_part TEXT,
                injury_type TEXT,
                severity INTEGER,
                temp_debuff_json TEXT,
                perm_drop_json TEXT,
                reinjury_count_json TEXT,
                last_processed_date TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE injury_events (
                injury_id TEXT PRIMARY KEY,
                player_id TEXT,
                team_id TEXT,
                season_year INTEGER,
                date TEXT,
                context TEXT,
                game_id TEXT,
                quarter INTEGER,
                clock_sec INTEGER,
                body_part TEXT,
                injury_type TEXT,
                severity INTEGER,
                duration_days INTEGER,
                out_until_date TEXT,
                returning_days INTEGER,
                returning_until_date TEXT,
                temp_debuff_json TEXT,
                perm_drop_json TEXT,
                created_at TEXT
            );
            """
        )
        conn.commit()
        return conn

    def test_basic_injection_schema_with_current_and_history(self):
        conn = self._conn_with_injury_tables()
        conn.execute(
            """
            INSERT INTO player_injury_state(
                player_id, team_id, status, out_until_date, returning_until_date, body_part, severity
            ) VALUES ('p1', 'LAL', 'OUT', '2026-03-30', '2026-04-10', 'knee', 3);
            """
        )
        conn.execute(
            """
            INSERT INTO injury_events(
                injury_id, player_id, team_id, season_year, date, context, body_part,
                injury_type, severity, duration_days, out_until_date, returning_days,
                returning_until_date, temp_debuff_json, perm_drop_json, created_at
            ) VALUES (
                'e1', 'p1', 'LAL', 2026, '2026-03-01', 'game', 'knee',
                'SPRAIN', 3, 20, '2026-03-21', 10, '2026-03-31', '{}', '{}', '2026-03-01'
            );
            """
        )
        conn.commit()

        payload = build_injury_payloads_for_players(
            conn=conn,
            player_ids=["p1"],
            as_of_date_iso="2026-03-15",
        )["p1"]

        self.assertIn("current", payload)
        self.assertIn("history", payload)
        self.assertIn("health_credit_inputs", payload)
        self.assertEqual(payload["current"]["status"], "OUT")
        self.assertGreater(payload["current"]["days_to_return"], 0)
        self.assertEqual(payload["history"]["recent_count_30d"], 1)
        self.assertFalse(payload["flags"]["current_missing"])
        self.assertFalse(payload["flags"]["history_missing"])

    def test_current_only_sets_history_missing(self):
        conn = self._conn_with_injury_tables()
        conn.execute(
            """
            INSERT INTO player_injury_state(player_id, team_id, status, body_part, severity)
            VALUES ('p1', 'LAL', 'RETURNING', 'BACK', 2);
            """
        )
        conn.commit()

        payload = build_injury_payloads_for_players(
            conn=conn,
            player_ids=["p1"],
            as_of_date_iso="2026-03-15",
        )["p1"]

        self.assertFalse(payload["flags"]["current_missing"])
        self.assertTrue(payload["flags"]["history_missing"])

    def test_history_only_sets_current_missing(self):
        conn = self._conn_with_injury_tables()
        conn.execute(
            """
            INSERT INTO injury_events(
                injury_id, player_id, team_id, season_year, date, context, body_part,
                injury_type, severity, duration_days, out_until_date, returning_days,
                returning_until_date, temp_debuff_json, perm_drop_json, created_at
            ) VALUES (
                'e1', 'p1', 'LAL', 2026, '2026-02-01', 'game', 'hip',
                'STRAIN', 2, 14, '2026-02-15', 7, '2026-02-22', '{}', '{}', '2026-02-01'
            );
            """
        )
        conn.commit()

        payload = build_injury_payloads_for_players(
            conn=conn,
            player_ids=["p1"],
            as_of_date_iso="2026-03-15",
        )["p1"]

        self.assertTrue(payload["flags"]["current_missing"])
        self.assertFalse(payload["flags"]["history_missing"])
        self.assertEqual(payload["current"]["status"], "HEALTHY")

    def test_missing_tables_fallback_default_payload(self):
        conn = sqlite3.connect(":memory:")
        payload = build_injury_payloads_for_players(
            conn=conn,
            player_ids=["p1"],
            as_of_date_iso="2026-03-15",
        )["p1"]

        self.assertTrue(payload["flags"]["current_missing"])
        self.assertTrue(payload["flags"]["history_missing"])
        self.assertTrue(payload["flags"]["fallback_used"])

    def test_date_boundary_clamps_negative_days_to_zero(self):
        conn = self._conn_with_injury_tables()
        conn.execute(
            """
            INSERT INTO player_injury_state(player_id, team_id, status, out_until_date, body_part, severity)
            VALUES ('p1', 'LAL', 'OUT', '2026-03-01', 'knee', 2);
            """
        )
        conn.commit()

        payload = build_injury_payloads_for_players(
            conn=conn,
            player_ids=["p1"],
            as_of_date_iso="2026-03-15",
        )["p1"]
        self.assertEqual(payload["current"]["days_to_return"], 0)

    def test_same_part_repeat_and_critical_counts(self):
        conn = self._conn_with_injury_tables()
        rows = [
            ("e1", "2026-01-01", "knee", 2),
            ("e2", "2026-01-20", "knee", 3),
            ("e3", "2026-02-10", "knee", 1),
            ("e4", "2026-02-15", "wrist", 1),
        ]
        for eid, d, bp, sev in rows:
            conn.execute(
                """
                INSERT INTO injury_events(
                    injury_id, player_id, team_id, season_year, date, context, body_part,
                    injury_type, severity, duration_days, out_until_date, returning_days,
                    returning_until_date, temp_debuff_json, perm_drop_json, created_at
                ) VALUES (?, 'p1', 'LAL', 2026, ?, 'game', ?, 'TYPE', ?, 10, ?, 5, ?, '{}', '{}', ?)
                """,
                (eid, d, bp, sev, d, d, d),
            )
        conn.commit()

        payload = build_injury_payloads_for_players(
            conn=conn,
            player_ids=["p1"],
            as_of_date_iso="2026-03-15",
        )["p1"]

        self.assertEqual(payload["history"]["same_part_repeat_365d_max"], 3)
        self.assertEqual(payload["history"]["critical_count_365d"], 3)



    def test_lookback_days_applies_to_window_and_health_metrics(self):
        conn = self._conn_with_injury_tables()
        # 20-day OUT inside a 30-day lookback window.
        conn.execute(
            """
            INSERT INTO injury_events(
                injury_id, player_id, team_id, season_year, date, context, body_part,
                injury_type, severity, duration_days, out_until_date, returning_days,
                returning_until_date, temp_debuff_json, perm_drop_json, created_at
            ) VALUES (
                'e1', 'p1', 'LAL', 2026, '2026-02-20', 'game', 'knee',
                'SPRAIN', 2, 20, '2026-03-12', 0, '2026-03-12', '{}', '{}', '2026-02-20'
            );
            """
        )
        conn.commit()

        payload = build_injury_payloads_for_players(
            conn=conn,
            player_ids=['p1'],
            as_of_date_iso='2026-03-15',
            lookback_days=30,
        )['p1']

        self.assertEqual(payload['history']['window_days'], 30)
        self.assertEqual(payload['health_credit_inputs']['out_days_365d'], 20)
        self.assertEqual(payload['health_credit_inputs']['healthy_days_365d'], 10)
        self.assertAlmostEqual(payload['health_credit_inputs']['availability_rate_365d'], 10.0 / 30.0, places=6)

    def test_custom_critical_body_parts_override(self):
        conn = self._conn_with_injury_tables()
        conn.execute(
            """
            INSERT INTO injury_events(
                injury_id, player_id, team_id, season_year, date, context, body_part,
                injury_type, severity, duration_days, out_until_date, returning_days,
                returning_until_date, temp_debuff_json, perm_drop_json, created_at
            ) VALUES (
                'e1', 'p1', 'LAL', 2026, '2026-03-01', 'game', 'wrist',
                'SPRAIN', 2, 7, '2026-03-08', 3, '2026-03-11', '{}', '{}', '2026-03-01'
            );
            """
        )
        conn.commit()

        payload = build_injury_payloads_for_players(
            conn=conn,
            player_ids=['p1'],
            as_of_date_iso='2026-03-15',
            critical_body_parts=['WRIST'],
        )['p1']

        self.assertEqual(payload['history']['critical_count_365d'], 1)

    def test_severity_is_normalized_and_current_severity_norm_is_present(self):
        conn = self._conn_with_injury_tables()
        conn.execute(
            """
            INSERT INTO player_injury_state(player_id, team_id, status, body_part, severity)
            VALUES ('p1', 'LAL', 'OUT', 'BACK', 9);
            """
        )
        conn.execute(
            """
            INSERT INTO injury_events(
                injury_id, player_id, team_id, season_year, date, context, body_part,
                injury_type, severity, duration_days, out_until_date, returning_days,
                returning_until_date, temp_debuff_json, perm_drop_json, created_at
            ) VALUES (
                'e1', 'p1', 'LAL', 2026, '2026-03-01', 'game', 'back',
                'SPASM', 12, 7, '2026-03-08', 0, '2026-03-08', '{}', '{}', '2026-03-01'
            );
            """
        )
        conn.commit()

        payload = build_injury_payloads_for_players(
            conn=conn,
            player_ids=['p1'],
            as_of_date_iso='2026-03-15',
        )['p1']

        self.assertEqual(payload['current']['severity'], 5)
        self.assertAlmostEqual(payload['current']['severity_norm'], 1.0, places=6)
        self.assertLessEqual(payload['history']['avg_severity_365d'], 5.0)

    def test_history_includes_recent_weighted_count_signal(self):
        conn = self._conn_with_injury_tables()
        for i, d in enumerate(("2026-03-10", "2026-02-20"), start=1):
            conn.execute(
                """
                INSERT INTO injury_events(
                    injury_id, player_id, team_id, season_year, date, context, body_part,
                    injury_type, severity, duration_days, out_until_date, returning_days,
                    returning_until_date, temp_debuff_json, perm_drop_json, created_at
                ) VALUES (?, 'p1', 'LAL', 2026, ?, 'game', 'knee', 'SPRAIN', 2, 5, ?, 0, ?, '{}', '{}', ?)
                """,
                (f"e{i}", d, d, d, d),
            )
        conn.commit()

        payload = build_injury_payloads_for_players(
            conn=conn,
            player_ids=['p1'],
            as_of_date_iso='2026-03-15',
        )['p1']

        self.assertIn('recent_weighted_count_180d', payload['history'])
        self.assertGreater(payload['history']['recent_weighted_count_180d'], 0.0)


    def test_default_critical_body_parts_include_hamstring(self):
        conn = self._conn_with_injury_tables()
        conn.execute(
            """
            INSERT INTO injury_events(
                injury_id, player_id, team_id, season_year, date, context, body_part,
                injury_type, severity, duration_days, out_until_date, returning_days,
                returning_until_date, temp_debuff_json, perm_drop_json, created_at
            ) VALUES (
                'e_ham', 'p1', 'LAL', 2026, '2026-03-01', 'game', 'hamstring',
                'STRAIN', 2, 7, '2026-03-08', 3, '2026-03-11', '{}', '{}', '2026-03-01'
            );
            """
        )
        conn.commit()

        payload = build_injury_payloads_for_players(
            conn=conn,
            player_ids=['p1'],
            as_of_date_iso='2026-03-15',
        )['p1']

        self.assertEqual(payload['history']['critical_count_365d'], 1)


    def test_builder_loads_injury_payload_even_when_assets_and_ledger_are_injected(self):
        class _FakeRepo:
            def __init__(self, db_path: str):
                self.db_path = db_path

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def get_trade_assets_snapshot(self):
                return {"draft_picks": {}, "swap_rights": {}, "fixed_assets": {}}

            def get_contract_ledger_snapshot(self):
                return {"contracts": {}, "active_contract_id_by_player": {}}

        fake_injury = {
            "p1": {
                "version": 1,
                "as_of_date": "2026-03-15",
                "source": {"current": "player_injury_state", "history": "injury_events"},
                "current": {
                    "status": "OUT",
                    "is_out": True,
                    "is_returning": False,
                    "days_to_return": 10,
                    "body_part": "KNEE",
                    "severity": 3,
                    "out_until_date": "2026-03-25",
                    "returning_until_date": "2026-03-30",
                },
                "history": {
                    "window_days": 365,
                    "recent_count_30d": 1,
                    "recent_count_180d": 1,
                    "recent_count_365d": 1,
                    "critical_count_365d": 1,
                    "same_part_repeat_365d_max": 1,
                    "same_part_counts_365d": {"KNEE": 1},
                    "avg_severity_365d": 3.0,
                    "weighted_severity_365d": 3.0,
                    "last_injury_date": "2026-03-01",
                    "days_since_last_injury": 14,
                },
                "health_credit_inputs": {
                    "availability_rate_365d": 0.9,
                    "healthy_days_365d": 329,
                    "out_days_365d": 20,
                    "returning_days_365d": 16,
                },
                "flags": {
                    "current_missing": False,
                    "history_missing": False,
                    "fallback_used": False,
                },
            }
        }

        with patch("trades.valuation.data_context.LeagueRepo", _FakeRepo),              patch("trades.valuation.data_context._load_agency_state_snapshot", return_value={}),              patch("trades.valuation.data_context._load_injury_payload_snapshot", return_value=fake_injury) as m_inj:
            ctx = build_repo_valuation_data_context(
                db_path="dummy.db",
                current_season_year=2026,
                current_date_iso="2026-03-15",
                assets_snapshot={"draft_picks": {}, "swap_rights": {}, "fixed_assets": {}},
                contract_ledger={"contracts": {}, "active_contract_id_by_player": {}},
            )

        self.assertEqual(m_inj.call_count, 1)
        self.assertIn("p1", ctx.injury_payload_by_player)
        self.assertEqual(ctx.injury_payload_by_player["p1"]["current"]["status"], "OUT")

    def test_bulk_query_no_n_plus_one(self):
        conn = self._conn_with_injury_tables()
        for i in range(1, 6):
            pid = f"p{i}"
            conn.execute(
                """
                INSERT INTO player_injury_state(player_id, team_id, status, body_part, severity)
                VALUES (?, 'LAL', 'HEALTHY', NULL, NULL);
                """,
                (pid,),
            )
        conn.commit()

        queries = []
        conn.set_trace_callback(lambda q: queries.append(q))
        build_injury_payloads_for_players(
            conn=conn,
            player_ids=[f"p{i}" for i in range(1, 6)],
            as_of_date_iso="2026-03-15",
        )
        conn.set_trace_callback(None)

        lower = "\n".join(queries).lower()
        self.assertEqual(lower.count("from player_injury_state"), 1)
        self.assertEqual(lower.count("from injury_events"), 1)


if __name__ == "__main__":
    unittest.main()
