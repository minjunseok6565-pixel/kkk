from __future__ import annotations

import pytest
from datetime import date

pytest.importorskip("fastapi")

from app.api.routes import trades
from trades import negotiation_store


def test_parse_trade_block_aggregate_query_clamps_and_normalizes() -> None:
    q = trades._parse_trade_block_aggregate_query(
        active_only=False,
        visibility="invalid",
        team_id=" bos ",
        limit=9999,
        offset=-7,
        sort="OVR_DESC",
    )

    assert q.active_only is False
    assert q.visibility == "PUBLIC"
    assert q.team_id == "BOS"
    assert q.limit == 500
    assert q.offset == 0
    assert q.sort == "ovr_desc"


def test_build_trade_block_row_sets_numeric_defaults() -> None:
    listing = {
        "player_id": "P1",
        "team_id": "LAL",
        "status": "active",
        "visibility": "public",
        "priority": None,
        "reason_code": None,
        "listed_by": None,
        "created_at": "2026-01-01",
        "updated_at": "2026-01-02",
        "expires_on": None,
    }

    row = trades._build_trade_block_row(
        listing,
        player_snapshots={},
        workflow_state={"player_stats": {}},
    )

    assert row["player_id"] == "P1"
    assert row["team_id"] == "LAL"
    assert row["name"] == "-"
    assert row["overall"] == 0
    assert row["pts"] == 0
    assert row["three_pm"] == 0
    assert row["listing"]["status"] == "ACTIVE"
    assert row["listing"]["visibility"] == "PUBLIC"
    assert row["listing"]["priority"] == 0.5
    assert row["listing"]["reason_code"] == "MANUAL"
    assert row["listing"]["listed_by"] == "USER"


def test_build_trade_block_row_handles_none_player_stats_payload() -> None:
    listing = {
        "player_id": "P1",
        "team_id": "GSW",
        "status": "active",
        "visibility": "public",
    }

    row = trades._build_trade_block_row(
        listing,
        player_snapshots={"P1": {"name": "Player One", "overall": 81}},
        workflow_state={"player_stats": {"P1": None}},
    )

    assert row["player_id"] == "P1"
    assert row["name"] == "Player One"
    assert row["pts"] == 0
    assert row["ast"] == 0
    assert row["reb"] == 0
    assert row["three_pm"] == 0


def test_apply_trade_block_visibility_filter() -> None:
    rows = [
        {"player_id": "P1", "visibility": "PUBLIC"},
        {"player_id": "P2", "visibility": "PRIVATE"},
    ]

    pub = trades._apply_trade_block_visibility_filter(rows, visibility="PUBLIC")
    all_rows = trades._apply_trade_block_visibility_filter(rows, visibility="ALL")

    assert [r["player_id"] for r in pub] == ["P1"]
    assert [r["player_id"] for r in all_rows] == ["P1", "P2"]


def test_sort_trade_block_rows_priority_desc_default() -> None:
    rows = [
        {"player_id": "P1", "overall": 90, "listing": {"priority": 0.3, "updated_at": "2026-01-01"}},
        {"player_id": "P2", "overall": 80, "listing": {"priority": 0.9, "updated_at": "2026-01-02"}},
        {"player_id": "P3", "overall": 95, "listing": {"priority": 0.9, "updated_at": "2026-01-01"}},
    ]

    out = trades._sort_trade_block_rows(rows, sort_key="priority_desc")
    assert [r["player_id"] for r in out] == ["P3", "P2", "P1"]



def test_parse_trade_negotiation_inbox_query_clamps_and_normalizes() -> None:
    q = trades._parse_trade_negotiation_inbox_query(
        team_id=" lal ",
        status="bad",
        phase="counter_pending",
        include_expired=True,
        limit=999,
        offset=-2,
        sort="CREATED_DESC",
    )

    assert q.team_id == "LAL"
    assert q.status == "ACTIVE"
    assert q.phase == "COUNTER_PENDING"
    assert q.include_expired is True
    assert q.limit == 200
    assert q.offset == 0
    assert q.sort == "created_desc"


def test_build_trade_negotiation_inbox_row_asset_counts() -> None:
    session = {
        "session_id": "s1",
        "user_team_id": "LAL",
        "other_team_id": "BKN",
        "status": "active",
        "phase": "inbox_pending",
        "valid_until": "2026-01-02",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "last_offer": {
            "teams": ["LAL", "BKN"],
            "legs": [
                {
                    "from_team": "LAL",
                    "to_team": "BKN",
                    "assets": [{"kind": "PLAYER", "player_id": "P1"}, {"kind": "PICK"}],
                },
                {
                    "from_team": "BKN",
                    "to_team": "LAL",
                    "assets": [{"kind": "PLAYER", "player_id": "P2"}],
                },
            ],
        },
        "market_context": {"offer_meta": {"offer_privacy": "public", "leak_status": "none"}},
    }

    row = trades._build_trade_negotiation_inbox_row(session, today=date(2026, 1, 1))

    assert row["session_id"] == "s1"
    assert row["phase"] == "INBOX_PENDING"
    assert row["is_expired"] is False
    assert row["summary"]["offer_privacy"] == "PUBLIC"
    assert row["offer"]["asset_counts"] == {
        "user_outgoing_players": 1,
        "user_incoming_players": 1,
        "user_outgoing_picks": 1,
        "user_incoming_picks": 0,
    }
    assert row["actions"]["can_open"] is True
    assert row["actions"]["can_commit"] is False


def test_sort_trade_negotiation_inbox_rows_expires_asc() -> None:
    rows = [
        {"session_id": "s1", "valid_until": "2026-01-05", "updated_at": "2026-01-04T00:00:00Z"},
        {"session_id": "s2", "valid_until": "2026-01-03", "updated_at": "2026-01-02T00:00:00Z"},
        {"session_id": "s3", "valid_until": None, "updated_at": "2026-01-06T00:00:00Z"},
    ]

    out = trades._sort_trade_negotiation_inbox_rows(rows, sort_key="expires_asc")
    assert [r["session_id"] for r in out] == ["s2", "s1", "s3"]


def test_close_as_rejected_sets_closed_and_is_idempotent(monkeypatch) -> None:
    sessions = {
        "s1": {
            "session_id": "s1",
            "user_team_id": "LAL",
            "other_team_id": "BKN",
            "messages": [],
            "status": "ACTIVE",
            "phase": "INBOX_PENDING",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    }

    def _update(session_id, mutator):
        item = sessions[session_id]
        mutator(item)
        return dict(item)

    monkeypatch.setattr(negotiation_store.state, "negotiation_session_update", _update)
    monkeypatch.setattr(negotiation_store, "_now_iso", lambda: "2026-01-03T00:00:00Z")

    out1 = negotiation_store.close_as_rejected("s1", reason="NOT_INTERESTED")
    out2 = negotiation_store.close_as_rejected("s1", reason="NOT_INTERESTED")

    assert out1["idempotent"] is False
    assert out1["session"]["status"] == "CLOSED"
    assert out1["session"]["phase"] == "REJECTED"
    assert out1["session"]["messages"][-1]["speaker"] == "USER_GM"

    assert out2["idempotent"] is True


def test_api_trade_negotiation_reject_team_authorization(monkeypatch) -> None:
    async def _run() -> None:
        req = trades.TradeNegotiationRejectRequest(session_id="s1", team_id="LAL", reason="NO")

        monkeypatch.setattr(
            trades.negotiation_store,
            "get_session",
            lambda _sid: {"session_id": "s1", "user_team_id": "BOS", "status": "ACTIVE", "phase": "INBOX_PENDING"},
        )

        out = await trades.api_trade_negotiation_reject(req)
        assert out.status_code == 400
        assert out.body

    import asyncio
    asyncio.run(_run())


def test_open_inbox_session_transitions_and_idempotent(monkeypatch) -> None:
    sessions = {
        "s1": {
            "session_id": "s1",
            "user_team_id": "LAL",
            "other_team_id": "BKN",
            "messages": [],
            "status": "ACTIVE",
            "phase": "INBOX_PENDING",
            "updated_at": "2026-01-01T00:00:00Z",
        },
        "s2": {
            "session_id": "s2",
            "user_team_id": "LAL",
            "other_team_id": "BKN",
            "messages": [],
            "status": "ACTIVE",
            "phase": "NEGOTIATING",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    }

    def _update(session_id, mutator):
        item = sessions[session_id]
        mutator(item)
        return dict(item)

    monkeypatch.setattr(negotiation_store.state, "negotiation_session_update", _update)

    out1 = negotiation_store.open_inbox_session("s1")
    out2 = negotiation_store.open_inbox_session("s2")

    assert out1["idempotent"] is False
    assert out1["session"]["phase"] == "NEGOTIATING"
    assert out2["idempotent"] is True


def test_api_trade_negotiation_open_ai_ended_session(monkeypatch) -> None:
    async def _run() -> None:
        req = trades.TradeNegotiationOpenRequest(session_id="s1", team_id="LAL")

        monkeypatch.setattr(
            trades.negotiation_store,
            "get_session",
            lambda _sid: {
                "session_id": "s1",
                "user_team_id": "LAL",
                "status": "ACTIVE",
                "phase": "INBOX_PENDING",
                "valid_until": "2026-01-01",
            },
        )
        monkeypatch.setattr(trades.state, "get_current_date_as_date", lambda: date(2026, 1, 3))
        monkeypatch.setattr(
            trades,
            "evaluate_and_maybe_end",
            lambda _sid, today, seed_context=None: {"ended": True, "reason_code": "NO_RESPONSE_TIMEOUT", "probability": 0.7, "roll": 0.2},
        )

        out = await trades.api_trade_negotiation_open(req)
        assert out.status_code == 400
        assert out.body

    import asyncio
    asyncio.run(_run())


def test_ensure_session_ready_for_commit_guards() -> None:
    today = date(2026, 1, 5)

    # idempotent accepted path
    out = trades._ensure_session_ready_for_commit(
        {"status": "CLOSED", "phase": "ACCEPTED", "committed_deal_id": "D1", "valid_until": "2026-01-06"},
        session_id="s1",
        today=today,
    )
    assert isinstance(out, dict) and out["idempotent"] is True

    # invalid phase
    with pytest.raises(trades.TradeError):
        trades._ensure_session_ready_for_commit(
            {"status": "ACTIVE", "phase": "INBOX_PENDING", "committed_deal_id": None},
            session_id="s2",
            today=today,
        )

    # valid_until is no longer a hard commit guard
    out2 = trades._ensure_session_ready_for_commit(
        {"status": "ACTIVE", "phase": "NEGOTIATING", "valid_until": "2026-01-01"},
        session_id="s3",
        today=today,
    )
    assert out2 is None


def test_mark_committed_and_close_transitions_and_idempotent(monkeypatch) -> None:
    sessions = {
        "s1": {
            "session_id": "s1",
            "status": "ACTIVE",
            "phase": "COUNTER_PENDING",
            "committed_deal_id": None,
        },
        "s2": {
            "session_id": "s2",
            "status": "CLOSED",
            "phase": "ACCEPTED",
            "committed_deal_id": "D-1",
            "valid_until": "2026-01-07",
        },
    }

    def _update(session_id, mutator):
        item = sessions[session_id]
        mutator(item)
        return dict(item)

    monkeypatch.setattr(negotiation_store.state, "negotiation_session_update", _update)

    out1 = negotiation_store.mark_committed_and_close("s1", deal_id="D-1", expires_at="2026-01-08")
    assert out1["idempotent"] is False
    assert out1["session"]["status"] == "CLOSED"
    assert out1["session"]["phase"] == "ACCEPTED"
    assert out1["session"]["committed_deal_id"] == "D-1"

    out2 = negotiation_store.mark_committed_and_close("s2", deal_id="D-1", expires_at="2026-01-07")
    assert out2["idempotent"] is True
