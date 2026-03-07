from __future__ import annotations

import hashlib
import math
from datetime import date
from typing import Any, Dict, Optional

from .types import OrchestrationConfig
from .. import negotiation_store


def _clamp01(x: Any) -> float:
    try:
        xf = float(x)
    except Exception:
        return 0.0
    if xf <= 0.0:
        return 0.0
    if xf >= 1.0:
        return 1.0
    return xf


def _parse_iso_date(raw: Any) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw).strip()[:10])
    except Exception:
        return None


def _sigmoid(x: float) -> float:
    if x >= 35.0:
        return 1.0
    if x <= -35.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def _stable_u01(seed: str) -> float:
    digest = hashlib.sha1(str(seed).encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big", signed=False)
    return value / float(2**64)


def _normalize_auto_end_context(ctx: Any) -> Dict[str, Any]:
    if isinstance(ctx, dict):
        out = dict(ctx)
    else:
        out = {}

    cfg = out.get("config")
    if not isinstance(cfg, OrchestrationConfig):
        cfg = OrchestrationConfig()
    out["config"] = cfg

    out["deadline_pressure"] = _clamp01(out.get("deadline_pressure", 0.0))
    out["seed_salt"] = str(out.get("seed_salt") or getattr(cfg, "seed_salt", "trade_orchestration_v2"))
    return out


def _silence_days(session: Dict[str, Any], *, today: date) -> int:
    start = (
        _parse_iso_date(session.get("last_user_action_at"))
        or _parse_iso_date(session.get("updated_at"))
        or _parse_iso_date(session.get("created_at"))
        or today
    )
    return max(0, int((today - start).days))


def _tone_bias(*, tone: str, config: OrchestrationConfig) -> float:
    t = str(tone or "").upper()
    if t == "LOWBALL":
        return float(getattr(config, "ai_auto_end_tone_bias_lowball", 0.30) or 0.30)
    if t == "PROBE":
        return float(getattr(config, "ai_auto_end_tone_bias_probe", 0.18) or 0.18)
    return float(getattr(config, "ai_auto_end_tone_bias_serious", 0.00) or 0.00)


def compute_auto_end_probability(session: Dict[str, Any], today: date, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = _normalize_auto_end_context(ctx)
    config: OrchestrationConfig = payload["config"]

    relationship = session.get("relationship") if isinstance(session.get("relationship"), dict) else {}
    market_context = session.get("market_context") if isinstance(session.get("market_context"), dict) else {}
    offer_meta = market_context.get("offer_meta") if isinstance(market_context.get("offer_meta"), dict) else {}

    silence_days = _silence_days(session, today=today)
    grace = max(0, int(getattr(config, "ai_auto_end_grace_days", 5) or 5))
    slope_pre5 = max(0.0, float(getattr(config, "ai_auto_end_time_slope_pre5", 0.06) or 0.06))
    post5_add = max(0.0, float(getattr(config, "ai_auto_end_time_post5_max_add", 0.55) or 0.55))
    post5_half_life = max(0.1, float(getattr(config, "ai_auto_end_time_post5_half_life", 6.0) or 6.0))

    if silence_days <= grace:
        f_time = float(slope_pre5) * float(silence_days)
    else:
        f_time = (float(slope_pre5) * float(grace)) + (float(post5_add) * (1.0 - math.exp(-float(silence_days - grace) / float(post5_half_life))))

    trust_now = max(-100.0, min(100.0, float(relationship.get("trust", 0.0) or 0.0)))
    fatigue_now = max(0.0, float(relationship.get("fatigue", 0.0) or 0.0))
    broken_now = max(0.0, float(relationship.get("promises_broken", 0.0) or 0.0))

    f_low_trust = _clamp01((-trust_now) / 100.0)
    f_fatigue = _clamp01(fatigue_now / 20.0)
    f_broken = _clamp01(broken_now / 5.0)

    tone = str(offer_meta.get("offer_tone") or "SERIOUS").upper()
    leak_status = str(offer_meta.get("leak_status") or "NONE").upper()
    leak_bias = float(getattr(config, "ai_auto_end_leak_bias", 0.20) or 0.20) if leak_status != "NONE" else 0.0

    pressure = _clamp01(payload.get("deadline_pressure", 0.0))

    z = float(getattr(config, "ai_auto_end_logit_base", -2.35) or -2.35)
    z += float(getattr(config, "ai_auto_end_weight_time", 1.55) or 1.55) * f_time
    z += float(getattr(config, "ai_auto_end_weight_low_trust", 0.90) or 0.90) * f_low_trust
    z += float(getattr(config, "ai_auto_end_weight_fatigue", 0.55) or 0.55) * f_fatigue
    z += float(getattr(config, "ai_auto_end_weight_promises_broken", 1.05) or 1.05) * f_broken
    z += float(getattr(config, "ai_auto_end_weight_deadline_pressure", 0.45) or 0.45) * pressure
    z += _tone_bias(tone=tone, config=config)
    z += leak_bias

    p_raw = _sigmoid(float(z))

    p_min = _clamp01(getattr(config, "ai_auto_end_probability_min", 0.01))
    p_cap = _clamp01(getattr(config, "ai_auto_end_probability_cap", 0.88))
    if tone == "SERIOUS" and trust_now >= 20.0:
        p_cap = min(p_cap, _clamp01(getattr(config, "ai_auto_end_probability_cap_serious_offer", 0.75)))

    p = max(p_min, min(p_cap, p_raw))

    early_days_until = max(0, int(getattr(config, "ai_auto_end_early_days_penalty_until", 2) or 2))
    if silence_days <= early_days_until:
        p *= max(0.0, float(getattr(config, "ai_auto_end_early_days_multiplier", 0.35) or 0.35))

    reasons: Dict[str, float] = {
        "NO_RESPONSE_TIMEOUT": float(getattr(config, "ai_auto_end_weight_time", 1.55) or 1.55) * float(f_time),
        "LOW_TRUST": float(getattr(config, "ai_auto_end_weight_low_trust", 0.90) or 0.90) * float(f_low_trust),
        "NEGOTIATION_FATIGUE": float(getattr(config, "ai_auto_end_weight_fatigue", 0.55) or 0.55) * float(f_fatigue),
        "PROMISES_BROKEN": float(getattr(config, "ai_auto_end_weight_promises_broken", 1.05) or 1.05) * float(f_broken),
        "DEADLINE_PRESSURE": float(getattr(config, "ai_auto_end_weight_deadline_pressure", 0.45) or 0.45) * float(pressure),
    }
    if tone in {"PROBE", "LOWBALL"}:
        reasons["OFFER_TONE"] = _tone_bias(tone=tone, config=config)
    if leak_status != "NONE":
        reasons["PRIVATE_OFFER_LEAK"] = leak_bias

    reason_code = max(reasons.items(), key=lambda kv: kv[1])[0] if reasons else "AI_DECISION"

    return {
        "probability": _clamp01(p),
        "reason_code": str(reason_code),
        "silence_days": int(silence_days),
        "tone": tone,
        "leak_status": leak_status,
        "deadline_pressure": float(pressure),
        "features": {
            "f_time": float(f_time),
            "f_low_trust": float(f_low_trust),
            "f_fatigue": float(f_fatigue),
            "f_promises_broken": float(f_broken),
        },
        "weights": reasons,
    }


def evaluate_and_maybe_end(session_id: str, today: date, seed_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ctx = _normalize_auto_end_context(seed_context)
    if not bool(getattr(ctx["config"], "enable_ai_auto_end_user_offers", True)):
        return {"session_id": str(session_id), "evaluated": False, "ended": False, "reason": "DISABLED"}

    session = negotiation_store.get_session(session_id)
    status = str(session.get("status") or "ACTIVE").upper()
    if status != "ACTIVE":
        return {"session_id": str(session_id), "evaluated": False, "ended": False, "reason": "NOT_ACTIVE", "status": status}

    auto_end = session.get("auto_end") if isinstance(session.get("auto_end"), dict) else {}
    if str(auto_end.get("status") or "").upper() == "ENDED":
        return {"session_id": str(session_id), "evaluated": False, "ended": True, "reason": "ALREADY_ENDED"}

    prob = compute_auto_end_probability(session, today=today, ctx=ctx)
    p = _clamp01(prob.get("probability", 0.0))

    seed_parts = [
        str(ctx.get("seed_salt") or "trade_orchestration_v2"),
        str(today.isoformat()),
        str(session_id),
        str(session.get("user_team_id") or ""),
        str(session.get("other_team_id") or ""),
    ]
    if isinstance(seed_context, dict):
        for key in sorted(seed_context.keys()):
            if key == "config":
                continue
            seed_parts.append(f"{key}={seed_context.get(key)}")

    roll = _stable_u01("|".join(seed_parts))
    ended = bool(roll < p)

    out: Dict[str, Any] = {
        "session_id": str(session_id),
        "evaluated": True,
        "ended": ended,
        "probability": float(p),
        "roll": float(roll),
        "reason_code": str(prob.get("reason_code") or "AI_DECISION"),
        "analysis": prob,
    }

    if ended:
        ended_out = negotiation_store.mark_auto_ended(
            session_id,
            reason=str(prob.get("reason_code") or "AI_DECISION"),
            score=float(p),
            detail={
                "roll": float(roll),
                "silence_days": int(prob.get("silence_days") or 0),
                "tone": str(prob.get("tone") or "SERIOUS"),
                "deadline_pressure": float(prob.get("deadline_pressure") or 0.0),
            },
        )
        out["session"] = ended_out.get("session")
        out["idempotent"] = bool(ended_out.get("idempotent"))

    return out
