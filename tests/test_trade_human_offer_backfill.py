from datetime import date

from trades.orchestration.tick_loop import _compute_backfill_scan_limit


def test_backfill_limit_stays_base_without_human_listing():
    out = _compute_backfill_scan_limit(
        base_limit=10,
        backfill_candidates_count=25,
        human_ids={"LAL"},
        trade_market={"listings": {}},
        today=date(2026, 1, 15),
    )
    assert out == 10


def test_backfill_limit_expands_when_human_has_active_listing():
    out = _compute_backfill_scan_limit(
        base_limit=10,
        backfill_candidates_count=25,
        human_ids={"LAL"},
        trade_market={
            "listings": {
                "p1": {
                    "player_id": "p1",
                    "team_id": "LAL",
                    "status": "ACTIVE",
                    "expires_on": "2026-02-01",
                }
            }
        },
        today=date(2026, 1, 15),
    )
    assert out == 25
