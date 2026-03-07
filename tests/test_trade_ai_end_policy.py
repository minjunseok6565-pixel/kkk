from datetime import date

from trades.orchestration.ai_end_policy import compute_auto_end_probability, evaluate_and_maybe_end
from trades.orchestration.types import OrchestrationConfig


def _base_session(**overrides):
    base = {
        "session_id": "s1",
        "user_team_id": "LAL",
        "other_team_id": "BKN",
        "status": "ACTIVE",
        "phase": "INBOX_PENDING",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "last_user_action_at": "2026-01-01T00:00:00Z",
        "relationship": {"trust": 0, "fatigue": 0, "promises_broken": 0},
        "market_context": {"offer_meta": {"offer_tone": "SERIOUS", "leak_status": "NONE"}},
        "auto_end": {"status": "PENDING", "ended_at": None, "reason": None, "score": None, "detail": None},
    }
    base.update(overrides)
    return base


def test_compute_auto_end_probability_increases_after_grace_days():
    sess_short = _base_session(last_user_action_at="2026-01-05T00:00:00Z")
    sess_long = _base_session(last_user_action_at="2025-12-20T00:00:00Z")
    today = date(2026, 1, 10)

    out_short = compute_auto_end_probability(sess_short, today=today, ctx={"config": OrchestrationConfig()})
    out_long = compute_auto_end_probability(sess_long, today=today, ctx={"config": OrchestrationConfig()})

    assert out_long["probability"] > out_short["probability"]
    assert out_short["silence_days"] <= 5
    assert out_long["silence_days"] > 5


def test_evaluate_and_maybe_end_marks_closed_when_probability_is_forced(monkeypatch):
    session = _base_session()

    monkeypatch.setattr("trades.orchestration.ai_end_policy.negotiation_store.get_session", lambda _sid: dict(session))

    called = {}

    def _mark(session_id, reason, score=None, detail=None):
        called["session_id"] = session_id
        called["reason"] = reason
        called["score"] = score
        called["detail"] = dict(detail or {})
        return {"session": {"session_id": session_id, "status": "CLOSED", "phase": "EXPIRED_BY_AI"}, "idempotent": False}

    monkeypatch.setattr("trades.orchestration.ai_end_policy.negotiation_store.mark_auto_ended", _mark)

    cfg = OrchestrationConfig(
        ai_auto_end_logit_base=20.0,
        ai_auto_end_probability_min=1.0,
        ai_auto_end_probability_cap=1.0,
        ai_auto_end_early_days_multiplier=1.0,
    )
    out = evaluate_and_maybe_end(
        "s1",
        today=date(2026, 1, 10),
        seed_context={"config": cfg, "seed_salt": "test"},
    )

    assert out["evaluated"] is True
    assert out["ended"] is True
    assert called["session_id"] == "s1"
    assert called["reason"]
