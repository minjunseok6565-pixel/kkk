import sys
from types import SimpleNamespace


class _APIRouterStub:
    def post(self, *_args, **_kwargs):
        def _decorator(fn):
            return fn

        return _decorator


sys.modules.setdefault(
    "fastapi",
    SimpleNamespace(APIRouter=lambda: _APIRouterStub(), HTTPException=Exception),
)
sys.modules.setdefault(
    "pydantic",
    SimpleNamespace(
        BaseModel=object,
        Field=lambda default=None, **_kwargs: default,
    ),
)

from app.api.routes import sim


def test_iter_dates_exclusive_to_inclusive_includes_every_day():
    days = sim._iter_dates_exclusive_to_inclusive(
        from_exclusive="2026-02-01",
        to_inclusive="2026-02-04",
    )
    assert [d.isoformat() for d in days] == ["2026-02-02", "2026-02-03", "2026-02-04"]


def test_run_trade_orchestration_catchup_keeps_all_daily_runs(monkeypatch):
    calls = []

    def _fake_tick(**kwargs):
        calls.append(kwargs["current_date"].isoformat())
        d = kwargs["current_date"].isoformat()
        return SimpleNamespace(
            tick_date=d,
            skipped=False,
            skip_reason="",
            active_teams=["LAL"],
            promotion=SimpleNamespace(
                user_offer_sessions=[{"session_id": f"session-{d}"}],
                executed_trade_events=[],
                errors=[],
            ),
        )

    monkeypatch.setattr(sim, "run_trade_orchestration_tick", _fake_tick)

    out = sim._run_trade_orchestration_catchup(
        user_team_id="LAL",
        from_exclusive="2026-02-01",
        to_inclusive="2026-02-03",
    )

    assert calls == ["2026-02-02", "2026-02-03"]
    assert out["summary"]["requested_days"] == 2
    assert out["summary"]["executed_days"] == 2
    assert [r["tick_date"] for r in out["runs"]] == ["2026-02-02", "2026-02-03"]
    assert [r["user_offer_sessions_created"] for r in out["runs"]] == [1, 1]
    assert out["allow_backfill_state_mutation"] is True
