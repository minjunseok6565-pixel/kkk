from datetime import date
from types import SimpleNamespace

from trades.generation.asset_catalog import _active_public_trade_block_listings_by_team


def test_active_public_trade_block_listings_grouped_and_sorted() -> None:
    tick_ctx = SimpleNamespace(
        current_date=date(2026, 2, 1),
        team_situation_ctx=SimpleNamespace(
            trade_market={
                "listings": {
                    "p1": {
                        "player_id": "p1",
                        "team_id": "lal",
                        "status": "ACTIVE",
                        "visibility": "PUBLIC",
                        "priority": 0.2,
                    },
                    "p2": {
                        "player_id": "p2",
                        "team_id": "LAL",
                        "status": "ACTIVE",
                        "visibility": "PUBLIC",
                        "priority": 0.9,
                    },
                    "p3": {
                        "player_id": "p3",
                        "team_id": "BOS",
                        "status": "INACTIVE",
                        "visibility": "PUBLIC",
                        "priority": 0.95,
                    },
                    "p4": {
                        "player_id": "p4",
                        "team_id": "BOS",
                        "status": "ACTIVE",
                        "visibility": "PRIVATE",
                        "priority": 1.0,
                    },
                    "p5": {
                        "player_id": "p5",
                        "team_id": "BOS",
                        "status": "ACTIVE",
                        "visibility": "PUBLIC",
                        "priority": 0.7,
                        "expires_on": "2026-02-01",
                    },
                }
            }
        ),
    )

    out = _active_public_trade_block_listings_by_team(tick_ctx)

    assert set(out.keys()) == {"LAL"}
    assert out["LAL"] == [(0.9, "p2"), (0.2, "p1")]

