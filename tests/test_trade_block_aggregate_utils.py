from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from app.api.routes import trades


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
