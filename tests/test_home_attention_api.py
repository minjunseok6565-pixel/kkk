from __future__ import annotations

import asyncio

import pytest

import state

fastapi = pytest.importorskip("fastapi")
from app.api.routes import core


def test_injury_duration_label_days_under_equal_7() -> None:
    assert core._format_injury_duration_label(current_date_iso="2026-01-01", out_until_iso="2026-01-02") == "1일"
    assert core._format_injury_duration_label(current_date_iso="2026-01-01", out_until_iso="2026-01-08") == "7일"


def test_injury_duration_label_weeks_and_months() -> None:
    assert core._format_injury_duration_label(current_date_iso="2026-01-01", out_until_iso="2026-01-11") == "1~2주"
    assert core._format_injury_duration_label(current_date_iso="2026-01-01", out_until_iso="2026-02-20") == "1~2개월"


def test_dissatisfaction_event_mapping_table() -> None:
    assert core._build_attention_dissatisfaction_text(normalized_type="MINUTES", player_name="Stephen Curry") == "Stephen Curry (이)가 출전 시간에 대해 불만을 제기했습니다."
    assert core._build_attention_dissatisfaction_text(normalized_type="LOCKER_ROOM_MEETING", player_name="-") == "선수단이 현재 팀 상황과 관련하여 정식으로 팀 미팅을 가질 것을 요구했습니다."
    assert core._normalize_attention_dissatisfaction_type("trade_request_public") == "TRADE"


def test_trade_offer_title_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        core.state,
        "negotiations_get",
        lambda: {
            "S1": {
                "session_id": "S1",
                "user_team_id": "GSW",
                "other_team_id": "MIN",
                "status": "ACTIVE",
                "phase": "INIT",
                "updated_at": "2026-01-15T09:00:00Z",
                "last_offer": {"teams": ["GSW", "MIN"], "legs": {"GSW": [], "MIN": []}},
            }
        },
    )
    rows = core._build_attention_trade_items(team_id="GSW", fallback_date_iso="2026-01-15")
    assert rows and rows[0]["title"] == "Minnesota Timberwolves (으)로부터 온 트레이드 제안"


def test_home_attention_returns_merged_items_sorted_desc(monkeypatch: pytest.MonkeyPatch) -> None:
    state.reset_state_for_dev()

    monkeypatch.setattr(core, "_build_attention_trade_items", lambda **_kwargs: [
        {
            "issue_id": "trade:s1:2026-01-10",
            "type": "TRADE_OFFER",
            "occurred_at": "2026-01-10",
            "title": "T",
            "detail": None,
            "meta": {},
        }
    ])
    monkeypatch.setattr(core, "_build_attention_injury_items", lambda **_kwargs: [
        {
            "issue_id": "injury:p1:2026-01-12:2026-01-20",
            "type": "INJURY",
            "occurred_at": "2026-01-12",
            "title": "I",
            "detail": None,
            "meta": {},
        }
    ])
    monkeypatch.setattr(core, "_build_attention_dissatisfaction_items", lambda **_kwargs: [
        {
            "issue_id": "agency:e1",
            "type": "DISSATISFACTION",
            "occurred_at": "2026-01-11",
            "title": "D",
            "detail": None,
            "meta": {},
        }
    ])

    out = asyncio.run(core.api_home_attention("GSW", limit=10, offset=0))
    assert out["ok"] is True
    assert out["total"] == 3
    assert [x["occurred_at"] for x in out["items"]] == ["2026-01-12", "2026-01-11", "2026-01-10"]
