from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List

from .types import GeneratedBatch, PromotionResult, TickReport
from .market_state import record_market_event


def _count_user_offer_tones(user_offer_sessions: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Count offer tones in PromotionResult.user_offer_sessions.

    Expected tone values:
      - SERIOUS
      - PROBE
      - LOWBALL

    Missing/unknown tones are treated as SERIOUS for backward compatibility.
    """
    serious = 0
    probe = 0
    lowball = 0
    for s in user_offer_sessions or []:
        try:
            t = str((s or {}).get("offer_tone") or "SERIOUS").upper()
        except Exception:
            t = "SERIOUS"
        if t == "PROBE":
            probe += 1
        elif t == "LOWBALL":
            lowball += 1
        else:
            serious += 1
    return {"serious": serious, "probe": probe, "lowball": lowball}


def build_tick_summary_payload(
    report: TickReport,
    *,
    batches: List[GeneratedBatch],
    promotion: PromotionResult,
) -> Dict[str, Any]:
    batch_rows = []
    for b in batches:
        batch_rows.append(
            {
                "initiator_team_id": b.initiator_team_id,
                "num_proposals": len(b.proposals or []),
                "stats": asdict(b.stats) if b.stats is not None else None,
            }
        )

    tone_counts = _count_user_offer_tones(list(promotion.user_offer_sessions or []))

    # Keep meta stable but lift actor activity summary to a dedicated top-level field.
    meta = dict(report.meta or {})
    actor_activity = None
    try:
        actor_activity = meta.pop("actor_activity", None)
    except Exception:
        actor_activity = None

    payload: Dict[str, Any] = {
        "tick_date": report.tick_date,
        "skipped": bool(report.skipped),
        "skip_reason": str(report.skip_reason or ""),
        "active_teams": list(report.active_teams or []),
        "ai_ai_trade_cap": int(report.ai_ai_trade_cap),
        "cleanup": asdict(report.cleanup),
        "batches": batch_rows,
        "promotion": {
            "executed": len(promotion.executed_trade_events or []),
            "user_offers": len(promotion.user_offer_sessions or []),
            "user_offers_probe": int(tone_counts["probe"]),
            "user_offers_lowball": int(tone_counts["lowball"]),
            "skipped": int(promotion.skipped),
            "vetoed": int(getattr(promotion, "vetoed", 0)),
            "rumors_created": int(getattr(promotion, "rumors_created", 0)),
            "threads_touched": int(getattr(promotion, "threads_touched", 0)),
            "threads_opened": int(getattr(promotion, "threads_opened", 0)),
            "ui_cache_refreshed_players": int(getattr(promotion, "ui_cache_refreshed_players", 0)),
            "ui_cache_refresh_failures": int(getattr(promotion, "ui_cache_refresh_failures", 0)),
            "errors": list(promotion.errors or []),
        },
        "meta": meta,
    }

    if actor_activity is not None:
        payload["actor_activity"] = actor_activity

    return payload


def emit_tick_summary_event(trade_market: Dict[str, Any], *, today_iso: str, payload: Dict[str, Any]) -> None:
    from datetime import date
    try:
        d = date.fromisoformat(today_iso[:10])
    except Exception:
        return
    record_market_event(trade_market, today=d, event_type="GM_TICK_SUMMARY", payload=payload)
