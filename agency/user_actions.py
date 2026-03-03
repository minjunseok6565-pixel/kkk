from __future__ import annotations

"""User-initiated (proactive) interactions for the agency system.

This module is *pure business logic* (no DB I/O). It mirrors the design of
agency/responses.py but is used when the user initiates an action without a
player-generated event.

Examples:
- MEET_PLAYER: private talk to calm an issue before it becomes an event.
- PRAISE / WARN: relationship management.
- SET_EXPECTATION: proactively set a promise (e.g., ROLE/LOAD/HELP/EXTENSION_TALKS).
- START_EXTENSION_TALKS: record that talks actually started (SSOT: agency_events).

The DB layer (agency/interaction_service.py) is responsible for:
- validating roster/team ownership
- persisting agency_events and player_agency_state
- persisting promises when created
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple

from .config import AgencyConfig, DEFAULT_CONFIG
from .promise_negotiation import Offer, evaluate_offer, make_negotiation_event, make_thread_id
from .promises import PromiseSpec, PromiseType, due_month_from_now
from .responses import DEFAULT_RESPONSE_CONFIG
from .stance import apply_stance_deltas, stance_deltas_on_offer_decision
from .utils import clamp, clamp01, make_event_id, mental_norm, norm_date_iso, safe_float, safe_float_opt


UserActionType = Literal[
    "MEET_PLAYER",
    "PRAISE",
    "WARN",
    "SET_EXPECTATION",
    "START_EXTENSION_TALKS",
]


@dataclass(frozen=True, slots=True)
class UserActionOutcome:
    ok: bool
    action_type: str

    # Event written to agency_events.
    event_type: str
    severity: float = 0.0

    # State updates to write (absolute values; DB layer clamps).
    state_updates: Dict[str, Any] = field(default_factory=dict)

    # Optional promise to persist.
    promise: Optional[PromiseSpec] = None

    # Optional follow-up events to persist (e.g., negotiation thread).
    follow_up_events: List[Dict[str, Any]] = field(default_factory=list)

    # Explainability
    reasons: List[Dict[str, Any]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)


def apply_user_action(
    *,
    action_type: str,
    state: Mapping[str, Any],
    mental: Mapping[str, Any],
    action_payload: Optional[Mapping[str, Any]] = None,
    now_date_iso: Optional[str] = None,
    cfg: AgencyConfig = DEFAULT_CONFIG,
) -> UserActionOutcome:
    """Apply a user-initiated action (pure logic).

    v3:
    - SET_EXPECTATION offers are evaluated for credibility; the player can counter/reject,
      producing a PROMISE_NEGOTIATION follow-up event.
    - Offer decisions can update dynamic stances.
    """

    at = str(action_type or "").upper()
    payload0 = dict(action_payload or {})
    payload = dict(payload0)  # local copy

    if now_date_iso is None:
        now_date_iso = str(payload.get("now_date") or "")
    now_d = norm_date_iso(now_date_iso) or "2000-01-01"

    if at not in {"MEET_PLAYER", "PRAISE", "WARN", "SET_EXPECTATION", "START_EXTENSION_TALKS"}:
        return UserActionOutcome(
            ok=False,
            action_type=at,
            event_type="USER_ACTION",
            reasons=[{"code": "USER_ACTION_UNKNOWN", "evidence": {"action_type": at}}],
        )

    trust0 = float(clamp01(safe_float(state.get("trust"), 0.5)))
    mfr0 = float(clamp01(safe_float(state.get("minutes_frustration"), 0.0)))
    tfr0 = float(clamp01(safe_float(state.get("team_frustration"), 0.0)))
    rfr0 = float(clamp01(safe_float(state.get("role_frustration"), 0.0)))
    cfr0 = float(clamp01(safe_float(state.get("contract_frustration"), 0.0)))
    hfr0 = float(clamp01(safe_float(state.get("health_frustration"), 0.0)))
    chfr0 = float(clamp01(safe_float(state.get("chemistry_frustration"), 0.0)))

    # v3: stances
    sk0 = float(clamp01(safe_float(state.get("stance_skepticism"), 0.0)))
    rs0 = float(clamp01(safe_float(state.get("stance_resentment"), 0.0)))
    hb0 = float(clamp01(safe_float(state.get("stance_hardball"), 0.0)))

    lev = float(clamp01(safe_float(state.get("leverage"), 0.0)))

    ego = float(clamp01(mental_norm(mental, "ego")))
    loy = float(clamp01(mental_norm(mental, "loyalty")))
    coach = float(clamp01(mental_norm(mental, "coachability")))
    adapt = float(clamp01(mental_norm(mental, "adaptability")))

    impact = 0.45 + 0.55 * lev
    pos_mult = float(clamp(0.85 + 0.35 * coach + 0.25 * loy + 0.10 * adapt - 0.15 * ego, 0.55, 1.65))
    neg_mult = float(clamp(0.90 + 0.50 * ego - 0.25 * loy - 0.10 * coach, 0.55, 2.10))

    trust1 = trust0
    mfr1, tfr1, rfr1, cfr1, hfr1, chfr1 = mfr0, tfr0, rfr0, cfr0, hfr0, chfr0
    sk1, rs1, hb1 = sk0, rs0, hb0

    promise: Optional[PromiseSpec] = None
    follow_up_events: List[Dict[str, Any]] = []
    reasons: List[Dict[str, Any]] = []
    meta_extra: Dict[str, Any] = {}

    # Default talk tone for user actions (can be overridden by negotiation verdicts)
    tone: str = "CALM"
    player_reply: str = ""

    def _apply_stance_updates(upd: Mapping[str, Any]) -> None:
        nonlocal sk1, rs1, hb1
        if not upd:
            return
        if "stance_skepticism" in upd:
            sk1 = float(clamp01(safe_float(upd.get("stance_skepticism"), sk1)))
        if "stance_resentment" in upd:
            rs1 = float(clamp01(safe_float(upd.get("stance_resentment"), rs1)))
        if "stance_hardball" in upd:
            hb1 = float(clamp01(safe_float(upd.get("stance_hardball"), hb1)))

    def _action_source_event_id() -> str:
        """Mirror interaction_service action_event_id generation for stable thread IDs."""
        pid = str(payload.get("player_id") or state.get("player_id") or "")
        # Keep the same key contract as interaction_service.apply_user_agency_action:
        #   make_event_id('agency', 'user_action', player_id, date, action_type)
        action_key = str(at or "").upper()
        return make_event_id("agency", "user_action", pid, str(now_d)[:10], action_key)

    # ------------------------------------------------------------------
    # Action behaviors
    # ------------------------------------------------------------------

    if at == "MEET_PLAYER":
        dt = float(getattr(cfg.frustration, "trust_recovery", 0.03)) * impact * pos_mult
        trust1 += dt

        focus = str(payload.get("focus_axis") or "").upper()
        axis_vals: List[Tuple[str, float]] = [
            ("MINUTES", mfr0),
            ("TEAM", tfr0),
            ("ROLE", rfr0),
            ("CONTRACT", cfr0),
            ("HEALTH", hfr0),
            ("CHEMISTRY", chfr0),
        ]
        if focus not in {a for a, _ in axis_vals}:
            focus = max(axis_vals, key=lambda kv: kv[1])[0]

        calm = 0.03 * impact * pos_mult
        if focus == "MINUTES":
            mfr1 -= calm
        elif focus == "TEAM":
            tfr1 -= calm
        elif focus == "ROLE":
            rfr1 -= calm
        elif focus == "CONTRACT":
            cfr1 -= calm
        elif focus == "HEALTH":
            hfr1 -= calm
        elif focus == "CHEMISTRY":
            chfr1 -= calm

        reasons.append({"code": "USER_MEET_PLAYER", "evidence": {"trust_delta": dt, "focus_axis": focus, "calm": calm}})

    elif at == "PRAISE":
        dt = 0.02 * impact * pos_mult
        trust1 += dt
        chfr1 -= 0.01 * impact * pos_mult
        reasons.append({"code": "USER_PRAISE", "evidence": {"trust_delta": dt}})

    elif at == "WARN":
        dt = -0.02 * impact * neg_mult
        trust1 += dt
        reasons.append({"code": "USER_WARN", "evidence": {"trust_delta": dt}})

    elif at == "SET_EXPECTATION":
        # Create a promise without a triggering player event, but still run credibility/negotiation.
        ptype = str(payload.get("promise_type") or "").upper()
        if ptype not in {"MINUTES", "ROLE", "HELP", "LOAD", "EXTENSION_TALKS"}:
            return UserActionOutcome(
                ok=False,
                action_type=at,
                event_type="USER_ACTION",
                reasons=[{"code": "USER_EXPECTATION_BAD_PROMISE_TYPE", "evidence": {"promise_type": ptype}}],
            )

        # Default due windows (can be tuned later via config).
        due_months = 1
        if ptype == "HELP":
            due_months = 2

        due = due_month_from_now(now_d, int(payload.get("due_months") or due_months))

        tv = safe_float_opt(payload.get("target_value"))
        target: Dict[str, Any] = dict(payload.get("target") or {}) if isinstance(payload.get("target"), Mapping) else {}

        if ptype == "ROLE" and "role" not in target:
            tr = str(payload.get("target_role") or payload.get("role") or "STARTER").upper()
            target["role"] = tr

        if ptype == "MINUTES" and tv is None:
            tv = safe_float_opt(payload.get("target_mpg"))

        if ptype == "LOAD" and tv is None:
            tv = safe_float_opt(payload.get("max_mpg"))
            if tv is None:
                tv = safe_float_opt(payload.get("target_mpg"))

        if ptype == "HELP" and "need_tags" not in target:
            nt = payload.get("need_tags")
            if isinstance(nt, list) and nt:
                target["need_tags"] = [str(x).upper() for x in nt if str(x).strip()]

        if ptype == "EXTENSION_TALKS" and "years_left" not in target:
            yl = safe_float_opt(payload.get("years_left"))
            if yl is None:
                yl = safe_float_opt(state.get("contract_seasons_left")) or 1.0
            target["years_left"] = float(clamp(yl, 0.0, 10.0))
            target.setdefault("now_month_key", due_month_from_now(now_d, 0))

        if ptype in {"MINUTES", "LOAD"} and tv is None:
            return UserActionOutcome(
                ok=False,
                action_type=at,
                event_type="USER_ACTION",
                reasons=[{
                    "code": "USER_EXPECTATION_MISSING_TARGET_VALUE",
                    "evidence": {"promise_type": ptype, "required": True},
                }],
            )

        # Normalize targets
        if ptype == "MINUTES" and tv is not None:
            tv = float(clamp(tv, 0.0, 48.0))
            target.setdefault("target_mpg", float(tv))

        if ptype == "LOAD" and tv is not None:
            tv = float(clamp(tv, 8.0, 40.0))
            target.setdefault("max_mpg", float(tv))
            target.setdefault("mode", "MAX_MPG")

        # Build an offer for credibility evaluation.
        axis_map = {
            "MINUTES": "MINUTES",
            "ROLE": "ROLE",
            "HELP": "TEAM",
            "LOAD": "HEALTH",
            "EXTENSION_TALKS": "CONTRACT",
        }
        axis = axis_map.get(ptype, "TEAM")

        offer = Offer(
            promise_type=ptype,
            axis=axis,
            due_month=due,
            target_value=tv,
            target_json=target,
        )

        decision = evaluate_offer(
            offer=offer,
            state=state,
            mental=mental,
            cfg=cfg,
            round_index=0,
            max_rounds=None,
        )
        verdict = str(getattr(decision, "verdict", "")).upper()
        meta_extra["negotiation"] = dict(getattr(decision, "meta", {}) or {})
        meta_extra["negotiation"]["decision"] = getattr(decision, "to_dict", lambda: {})()

        # Stance effects for contentious bargaining
        deltas, st_meta = stance_deltas_on_offer_decision(
            verdict=verdict,
            insulting=bool(getattr(decision, "insulting", False)),
            mental=mental,
            cfg=cfg,
        )
        if deltas:
            _apply_stance_updates(apply_stance_deltas(state={"stance_skepticism": sk1, "stance_resentment": rs1, "stance_hardball": hb1}, deltas=deltas))
            meta_extra["negotiation"].setdefault("stance", st_meta)

        if verdict == "ACCEPT":
            promise = PromiseSpec(
                promise_type=ptype,  # type: ignore[arg-type]
                due_month=due,
                target_value=tv,
                target=target,
            )
            trust1 += 0.01 * impact * pos_mult
            tone = "CALM"
            player_reply = "Okay. We'll see."
            reasons.append({"code": "USER_SET_EXPECTATION_ACCEPTED", "evidence": {"promise_type": ptype, "due_month": due}})

        else:
            # Not accepted: small trust hit + follow-up negotiation event when applicable.
            rcfg = getattr(cfg, "response", DEFAULT_RESPONSE_CONFIG)
            if verdict == "COUNTER":
                trust1 -= float(rcfg.negotiation_counter_trust_penalty) * impact * neg_mult
                tone = "FIRM"
                player_reply = "That's not enough."
            elif verdict == "REJECT":
                trust1 -= float(rcfg.negotiation_reject_trust_penalty) * impact * neg_mult
                tone = "FIRM"
                player_reply = "No."
            else:
                trust1 -= float(rcfg.negotiation_walkout_trust_penalty) * impact * neg_mult
                tone = "ANGRY"
                player_reply = "I'm done talking."

            reasons.append({"code": "USER_SET_EXPECTATION_NEGOTIATION", "evidence": {"promise_type": ptype, "verdict": verdict}})

            if verdict in {"COUNTER", "REJECT"}:
                # Spawn a negotiation thread event so the user can respond in the same UX.
                pid = str(payload.get("player_id") or state.get("player_id") or "")
                tid = str(payload.get("team_id") or state.get("team_id") or "")
                sy = int(payload.get("season_year") or state.get("season_year") or 0)

                source_event_id = _action_source_event_id()
                thread_id = make_thread_id(source_event_id, ptype)

                neg_event = make_negotiation_event(
                    thread_id=thread_id,
                    source_event_id=source_event_id,
                    player_id=pid,
                    team_id=tid,
                    season_year=sy,
                    now_date_iso=now_d,
                    offer=offer,
                    decision=decision,
                    cfg=cfg,
                )
                follow_up_events.append(neg_event)

    elif at == "START_EXTENSION_TALKS":
        # No promise created; this event is used as SSOT evidence.
        dt = 0.02 * impact * pos_mult
        trust1 += dt
        reasons.append({"code": "USER_START_EXTENSION_TALKS", "evidence": {"trust_delta": dt}})

    trust1 = float(clamp01(trust1))
    mfr1 = float(clamp01(mfr1))
    tfr1 = float(clamp01(tfr1))
    rfr1 = float(clamp01(rfr1))
    cfr1 = float(clamp01(cfr1))
    hfr1 = float(clamp01(hfr1))
    chfr1 = float(clamp01(chfr1))
    sk1 = float(clamp01(sk1))
    rs1 = float(clamp01(rs1))
    hb1 = float(clamp01(hb1))

    updates: Dict[str, Any] = {
        "trust": float(trust1),
        "minutes_frustration": float(mfr1),
        "team_frustration": float(tfr1),
        "role_frustration": float(rfr1),
        "contract_frustration": float(cfr1),
        "health_frustration": float(hfr1),
        "chemistry_frustration": float(chfr1),
        "stance_skepticism": float(sk1),
        "stance_resentment": float(rs1),
        "stance_hardball": float(hb1),
    }

    event_type = "USER_ACTION"
    sev = 0.10
    if at == "START_EXTENSION_TALKS":
        event_type = "CONTRACT_TALKS_STARTED"
        sev = 0.20

    meta = {
        "action_type": at,
        "now_date": now_d,
        "tone": tone,
        "player_reply": player_reply,
        "scales": {
            "leverage": float(lev),
            "impact": float(impact),
            "pos_mult": float(pos_mult),
            "neg_mult": float(neg_mult),
        },
        "before": {
            "trust": float(trust0),
            "minutes_frustration": float(mfr0),
            "team_frustration": float(tfr0),
            "role_frustration": float(rfr0),
            "contract_frustration": float(cfr0),
            "health_frustration": float(hfr0),
            "chemistry_frustration": float(chfr0),
            "stance_skepticism": float(sk0),
            "stance_resentment": float(rs0),
            "stance_hardball": float(hb0),
        },
        "after": {
            "trust": float(trust1),
            "minutes_frustration": float(mfr1),
            "team_frustration": float(tfr1),
            "role_frustration": float(rfr1),
            "contract_frustration": float(cfr1),
            "health_frustration": float(hfr1),
            "chemistry_frustration": float(chfr1),
            "stance_skepticism": float(sk1),
            "stance_resentment": float(rs1),
            "stance_hardball": float(hb1),
        },
    }
    meta.update(meta_extra)

    return UserActionOutcome(
        ok=True,
        action_type=at,
        event_type=event_type,
        severity=float(clamp01(sev)),
        state_updates=updates,
        promise=promise,
        follow_up_events=follow_up_events,
        reasons=reasons,
        meta=meta,
    )
