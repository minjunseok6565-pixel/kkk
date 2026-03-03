from __future__ import annotations

"""User response logic for player agency events.

This module is *pure business logic* (no DB I/O). It turns:
  - a player-generated agency event (complaint/demand/request)
  - the player's current agency state
  - player mental traits
  - a user-chosen response

into:
  - immediate agency state adjustments (trust/frustrations/trade_request_level)
  - an optional PromiseSpec to be persisted and evaluated later
  - explainable reasons and meta for UI/analytics

Important
---------
- Mental traits are modulators, not absolute rules.
- Leverage gates the *strength* of reactions.
- Effects are intentionally conservative by default; tune in playtests.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Mapping, Optional

from .config import AgencyConfig, DEFAULT_CONFIG
from .escalation import STAGE_AGENT, STAGE_PRIVATE, STAGE_PUBLIC, stage_i_from_payload, stage_label
from .promises import PromiseSpec, PromiseType, due_month_from_now
from .promise_negotiation import Offer, evaluate_offer, make_negotiation_event, make_thread_id
from .stance import apply_stance_deltas, stance_deltas_on_offer_decision
from .utils import clamp, clamp01, mental_norm, norm_date_iso, safe_float, safe_float_opt


AgencyEventType = Literal[
    "MINUTES_COMPLAINT",
    "HELP_DEMAND",
    "TRADE_REQUEST",
    "TRADE_REQUEST_PUBLIC",

    # v2 issue families
    "ROLE_PRIVATE",
    "ROLE_AGENT",
    "ROLE_PUBLIC",

    "CONTRACT_PRIVATE",
    "CONTRACT_AGENT",
    "CONTRACT_PUBLIC",

    "HEALTH_PRIVATE",
    "HEALTH_AGENT",
    "HEALTH_PUBLIC",

    "TEAM_PRIVATE",
    "TEAM_PUBLIC",

    "CHEMISTRY_PRIVATE",
    "CHEMISTRY_AGENT",
    "CHEMISTRY_PUBLIC",

    # team pass
    "LOCKER_ROOM_MEETING",

    # v3 negotiation + broken promise reactions
    "PROMISE_NEGOTIATION",
    "BROKEN_PROMISE_PRIVATE",
    "BROKEN_PROMISE_AGENT",
    "BROKEN_PROMISE_PUBLIC",
]

ResponseType = Literal[
    # Generic
    "ACKNOWLEDGE",
    "DISMISS",

    # Minutes complaint
    "PROMISE_MINUTES",

    # v2 axis promises
    "PROMISE_ROLE",
    "PROMISE_LOAD",
    "PROMISE_EXTENSION_TALKS",

    # Help demand
    "PROMISE_HELP",
    "REFUSE_HELP",

    # Trade request
    "SHOP_TRADE",
    "REFUSE_TRADE",
    "PROMISE_COMPETE",

    # Team meeting
    "TEAM_TALK_CALM",
    "TEAM_TALK_FIRM",
    "TEAM_TALK_IGNORE",

    # v3 negotiation follow-ups
    "ACCEPT_COUNTER",
    "MAKE_NEW_OFFER",
    "END_TALKS",
]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResponseConfig:
    """Tunable parameters for immediate response impacts."""

    # Trust deltas (base; scaled by leverage + mental + event severity)
    trust_acknowledge: float = 0.03
    trust_promise: float = 0.06
    trust_dismiss_penalty: float = 0.06
    trust_refuse_help_penalty: float = 0.06
    trust_refuse_trade_penalty: float = 0.10

    # Frustration deltas (base; scaled)
    minutes_relief_acknowledge: float = 0.04
    minutes_relief_promise: float = 0.06
    minutes_bump_dismiss: float = 0.03

    team_relief_acknowledge: float = 0.02
    team_relief_promise: float = 0.03
    team_bump_refuse_help: float = 0.03
    team_bump_refuse_trade: float = 0.04

    # Secondary frustration bump when trade is refused
    minutes_bump_refuse_trade: float = 0.02

    # v3 scaling knobs (kept backward-compatible with v2 behavior)
    impact_min: float = 0.45
    impact_per_leverage: float = 0.55
    sev_mult_scale: float = 0.40
    pos_mult_scale: float = 0.35
    neg_mult_scale: float = 0.50


    # v2: generic deltas for non-v1 axis events (role/contract/health/chemistry)
    axis_relief_acknowledge: float = 0.03
    axis_relief_promise: float = 0.05
    role_relief_promise: float = 0.05
    contract_relief_promise: float = 0.05
    health_relief_promise: float = 0.05
    chemistry_relief_promise: float = 0.05
    axis_bump_dismiss: float = 0.03

    # v2: team-level locker room meeting talk
    team_talk_trust_calm: float = 0.03
    team_talk_trust_firm: float = -0.01
    team_talk_trust_ignore: float = -0.06

    team_talk_chem_relief_calm: float = 0.05
    team_talk_chem_relief_firm: float = 0.02
    team_talk_chem_bump_ignore: float = 0.03

    team_talk_team_relief_calm: float = 0.02
    team_talk_team_relief_firm: float = 0.01
    team_talk_team_bump_ignore: float = 0.02
    team_talk_team_bump_calm: float = -0.02
    team_talk_team_bump_firm: float = -0.01

    # Promise due months
    promise_minutes_due_months: int = 1
    promise_role_due_months: int = 1
    promise_load_due_months: int = 1
    promise_extension_due_months: int = 1
    promise_help_due_months: int = 2
    promise_trade_due_months: int = 2

    # v2 promise defaults
    promise_role_min_starts_rate: float = 0.60
    promise_role_min_closes_rate: float = 0.35
    promise_load_default_max_mpg: float = 30.0
    promise_role_starts_rate: float = 0.60
    promise_role_closes_rate: float = 0.35
    promise_load_max_mpg: float = 30.0

    # Minutes promise target bounds (safety)
    promise_minutes_min_mpg: float = 8.0
    promise_minutes_max_mpg: float = 40.0

    # Escalation thresholds
    dismiss_escalate_trade_fr_threshold: float = 0.80
    refuse_trade_public_ego_threshold: float = 0.72
    refuse_trade_public_leverage_threshold: float = 0.65

    # v3 negotiation outcomes (applies when a promise offer is countered/rejected)
    negotiation_counter_trust_penalty: float = 0.01
    negotiation_reject_trust_penalty: float = 0.03
    negotiation_walkout_trust_penalty: float = 0.06
    negotiation_counter_frustration_bump: float = 0.01
    negotiation_reject_frustration_bump: float = 0.02
    negotiation_walkout_frustration_bump: float = 0.04

    # v3 broken promise apology (stance relief)
    broken_promise_apology_resentment_relief: float = 0.03
    broken_promise_apology_skepticism_relief: float = 0.02
    broken_promise_apology_hardball_relief: float = 0.02


DEFAULT_RESPONSE_CONFIG = ResponseConfig()


# ---------------------------------------------------------------------------
# Public output
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResponseOutcome:
    ok: bool

    event_type: str = ""
    response_type: str = ""
    error: str = ""

    # New absolute values to write to player_agency_state (only for affected fields).
    # The DB layer should clamp once more.
    state_updates: Dict[str, Any] = field(default_factory=dict)

    # Team events may affect multiple players (deltas, not absolutes).
    bulk_state_deltas: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    promise: Optional[PromiseSpec] = None
    follow_up_events: List[Dict[str, Any]] = field(default_factory=list)

    # Explainability
    tone: Literal["CALM", "FIRM", "ANGRY"] = "CALM"
    player_reply: str = ""
    reasons: List[Dict[str, Any]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def apply_user_response(
    *,
    event: Mapping[str, Any],
    state: Mapping[str, Any],
    mental: Mapping[str, Any],
    response_type: str,
    response_payload: Optional[Mapping[str, Any]] = None,
    now_date_iso: Optional[str] = None,
    cfg: AgencyConfig = DEFAULT_CONFIG,
    rcfg: ResponseConfig = DEFAULT_RESPONSE_CONFIG,
) -> ResponseOutcome:
    """Apply a user response to a player agency event (pure logic).

    v3 adds:
    - credibility + negotiation for promises (counter / reject / walkout)
    - broken-promise reaction handling
    - stance deltas that shape future interactions
    """

    et = str(event.get("event_type") or "").upper()
    rt = str(response_type or "").upper()
    ep = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    payload = response_payload if isinstance(response_payload, dict) else {}
    now_d = norm_date_iso(now_date_iso) or "2000-01-01"
    mental = mental or {}

    allowed = _allowed_responses_for_event(et, ep)
    if rt not in allowed:
        return ResponseOutcome(
            ok=False,
            error=f"Response '{rt}' not allowed for event '{et}'.",
            state_updates={},
        )

    # Baseline state (clamped)
    trust0 = float(clamp01(safe_float(state.get("trust"), 0.5)))
    mfr0 = float(clamp01(safe_float(state.get("minutes_frustration"), 0.0)))
    rfr0 = float(clamp01(safe_float(state.get("role_frustration"), 0.0)))
    cfr0 = float(clamp01(safe_float(state.get("contract_frustration"), 0.0)))
    hfr0 = float(clamp01(safe_float(state.get("health_frustration"), 0.0)))
    tfr0 = float(clamp01(safe_float(state.get("team_frustration"), 0.0)))
    chfr0 = float(clamp01(safe_float(state.get("chemistry_frustration"), 0.0)))
    tr_level0 = int(state.get("trade_request_level") or 0)

    # v3: stances
    sk0 = float(clamp01(safe_float(state.get("stance_skepticism"), 0.0)))
    rs0 = float(clamp01(safe_float(state.get("stance_resentment"), 0.0)))
    hb0 = float(clamp01(safe_float(state.get("stance_hardball"), 0.0)))

    trust1 = trust0
    mfr1, rfr1, cfr1, hfr1, tfr1, chfr1 = mfr0, rfr0, cfr0, hfr0, tfr0, chfr0
    tr_level1 = tr_level0
    sk1, rs1, hb1 = sk0, rs0, hb0

    promise: Optional[PromiseSpec] = None
    follow_up_events: List[Dict[str, Any]] = []
    reasons: List[Dict[str, Any]] = []
    negotiation_meta: Dict[str, Any] = {}

    # Tone / reply can be overridden by negotiation outcomes
    tone_override: Optional[str] = None
    reply_override: Optional[str] = None

    # Mental multipliers (same as v2)
    ego = float(clamp01(mental_norm(mental, "ego")))
    amb = float(clamp01(mental_norm(mental, "ambition")))
    loy = float(clamp01(mental_norm(mental, "loyalty")))
    coach = float(clamp01(mental_norm(mental, "coachability")))
    adapt = float(clamp01(mental_norm(mental, "adaptability")))

    lev = float(clamp01(safe_float(state.get("leverage"), 0.0)))
    sev = float(clamp01(safe_float(event.get("severity"), 0.5)))
    impact = float(clamp(rcfg.impact_min + rcfg.impact_per_leverage * lev, 0.10, 2.00))
    sev_mult = float(clamp(1.0 + rcfg.sev_mult_scale * (sev - 0.5), 0.70, 1.40))
    pos_mult = float(clamp(1.0 + rcfg.pos_mult_scale * coach - rcfg.pos_mult_scale * ego, 0.60, 1.20))
    neg_mult = float(clamp(1.0 + rcfg.neg_mult_scale * ego + 0.25 * amb, 0.70, 1.50))

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

    def _apply_axis_delta(axis: str, delta: float) -> None:
        nonlocal mfr1, rfr1, cfr1, hfr1, tfr1, chfr1
        ax = str(axis or "").upper()
        if ax == "MINUTES":
            mfr1 = float(clamp01(mfr1 + float(delta)))
        elif ax == "ROLE":
            rfr1 = float(clamp01(rfr1 + float(delta)))
        elif ax == "CONTRACT":
            cfr1 = float(clamp01(cfr1 + float(delta)))
        elif ax == "HEALTH":
            hfr1 = float(clamp01(hfr1 + float(delta)))
        elif ax == "TEAM":
            tfr1 = float(clamp01(tfr1 + float(delta)))
        elif ax == "CHEMISTRY":
            chfr1 = float(clamp01(chfr1 + float(delta)))

    def _neg_penalties(verdict: str) -> tuple[float, float]:
        v = str(verdict or "").upper()
        if v == "COUNTER":
            return float(rcfg.negotiation_counter_trust_penalty), float(rcfg.negotiation_counter_frustration_bump)
        if v == "REJECT":
            return float(rcfg.negotiation_reject_trust_penalty), float(rcfg.negotiation_reject_frustration_bump)
        return float(rcfg.negotiation_walkout_trust_penalty), float(rcfg.negotiation_walkout_frustration_bump)

    # ------------------------------------------------------------------
    # Negotiation thread event
    # ------------------------------------------------------------------
    if et == "PROMISE_NEGOTIATION":
        thread_id = str(ep.get("thread_id") or "")
        offer_dict = ep.get("offer") if isinstance(ep.get("offer"), dict) else {}
        decision_dict = ep.get("decision") if isinstance(ep.get("decision"), dict) else {}
        axis = str(ep.get("axis") or offer_dict.get("axis") or "").upper()
        promise_type = str(ep.get("promise_type") or offer_dict.get("promise_type") or "").upper()
        round_index = int(ep.get("round_index") or 0)
        max_rounds = int(ep.get("max_rounds") or 2)
        expires_month = str(ep.get("expires_month") or "")

        now_mk = due_month_from_now(now_d, 0)

        # Expired negotiation threads should auto-resolve as END_TALKS (no error).
        # This avoids UI dead-ends and allows monthly tick to sweep unresponded threads.
        expired = bool(_is_valid_month_key(expires_month) and now_mk > expires_month)
        if expired:
            original_rt = rt
            rt = "END_TALKS"

            reasons.append(
                {
                    "code": "NEGOTIATION_EXPIRED_AUTO_END",
                    "evidence": {
                        "expires_month": str(expires_month),
                        "now_month": str(now_mk),
                        "user_response_type": str(original_rt),
                        "thread_id": thread_id,
                        "round_index": int(round_index),
                        "max_rounds": int(max_rounds),
                    },
                }
            )

            negotiation_meta.update(
                {
                    "expired": True,
                    "expires_month": str(expires_month),
                    "now_month": str(now_mk),
                    "user_response_type": str(original_rt),
                    "applied_response_type": "END_TALKS",
                    "thread_id": thread_id,
                    "round_index": int(round_index),
                    "max_rounds": int(max_rounds),
                }
            )

            # Slightly different tone for timeouts vs explicit walkouts.
            tone_override = "FIRM"
            reply_override = "You waited too long. We're done."

        if rt == "ACCEPT_COUNTER":
            counter = decision_dict.get("counter_offer") if isinstance(decision_dict.get("counter_offer"), dict) else None
            if not counter:
                return ResponseOutcome(ok=False, error="No counter-offer to accept.", state_updates={})
            off2 = _offer_from_dict(counter)

            if str(off2.promise_type).upper() == "HELP":
                tj = off2.target_json or {}
                tags = tj.get("need_tags")
                if not isinstance(tags, list):
                    tags = tj.get("offer_need_tags")
                if not isinstance(tags, list):
                    tags = tj.get("ask_need_tags")
                if not isinstance(tags, list):
                    tags = []
                promise = PromiseSpec(
                    promise_type="HELP",
                    due_month=str(off2.due_month),
                    target={"need_tags": tags},
                )
            else:
                promise = PromiseSpec(
                    promise_type=str(off2.promise_type),
                    due_month=str(off2.due_month),
                    target_value=safe_float_opt(off2.target_value),
                    target=off2.target_json,
                )
            reasons.append({"code": "NEGOTIATION_ACCEPT_COUNTER", "evidence": {"thread_id": thread_id}})
            trust1 = float(clamp01(trust1 + rcfg.trust_promise * impact * pos_mult * sev_mult * 0.85))

            # Relief matches axis
            if axis == "MINUTES":
                _apply_axis_delta(axis, -(rcfg.minutes_relief_promise * impact * pos_mult * sev_mult * 0.85))
            elif axis == "ROLE":
                _apply_axis_delta(axis, -(rcfg.role_relief_promise * impact * pos_mult * sev_mult * 0.85))
            elif axis == "CONTRACT":
                _apply_axis_delta(axis, -(rcfg.contract_relief_promise * impact * pos_mult * sev_mult * 0.85))
            elif axis == "HEALTH":
                _apply_axis_delta(axis, -(rcfg.health_relief_promise * impact * pos_mult * sev_mult * 0.85))
            elif axis == "TEAM":
                _apply_axis_delta(axis, -(rcfg.team_relief_promise * impact * pos_mult * sev_mult * 0.85))
            elif axis == "CHEMISTRY":
                _apply_axis_delta(axis, -(rcfg.chemistry_relief_promise * impact * pos_mult * sev_mult * 0.85))

        elif rt == "MAKE_NEW_OFFER":
            if round_index >= (max_rounds - 1):
                return ResponseOutcome(ok=False, error="Max negotiation rounds reached.", state_updates={})

            new_offer_dict = payload.get("offer") if isinstance(payload.get("offer"), dict) else None
            if not new_offer_dict:
                return ResponseOutcome(ok=False, error="MAKE_NEW_OFFER requires payload.offer.", state_updates={})

            off = _offer_from_dict(new_offer_dict)
            if str(off.promise_type).upper() != promise_type:
                return ResponseOutcome(ok=False, error="Offer promise_type mismatch.", state_updates={})
            if not _is_valid_month_key(off.due_month):
                return ResponseOutcome(ok=False, error="MAKE_NEW_OFFER requires valid offer.due_month (YYYY-MM).", state_updates={})

            # Preserve LOAD negotiation ask SSOT across rounds when client omits ask_max_mpg.
            if str(off.promise_type).upper() == "LOAD":
                tj = dict(off.target_json or {})
                if safe_float_opt(tj.get("ask_max_mpg")) is None:
                    prev_ask = safe_float_opt((offer_dict.get("target_json") or {}).get("ask_max_mpg"))
                    if prev_ask is None:
                        prev_ask = safe_float_opt((decision_dict.get("meta") or {}).get("ask_max_mpg"))
                    if prev_ask is not None:
                        tj["ask_max_mpg"] = float(prev_ask)
                        off = Offer(
                            promise_type=off.promise_type,
                            axis=off.axis,
                            due_month=off.due_month,
                            target_value=off.target_value,
                            target_json=tj,
                        )


            decision = evaluate_offer(
                offer=off,
                state=state,
                mental=mental,
                round_index=round_index + 1,
                max_rounds=max_rounds,
                cfg=cfg,
            )
            meta = getattr(decision, "meta", {})
            negotiation_meta = dict(getattr(decision, "meta", {}) or {})
            negotiation_meta["decision"] = getattr(decision, "to_dict", lambda: {})()

            st_updates, st_meta = _apply_offer_decision_stances(
                state={"stance_skepticism": sk1, "stance_resentment": rs1, "stance_hardball": hb1},
                mental=mental,
                verdict=str(getattr(decision, "verdict", "")),
                insulting=bool(getattr(decision, "insulting", False)),
                base_scale=float(impact * sev_mult),
                cfg=cfg,
            )
            _apply_stance_updates(st_updates)
            if st_meta:
                negotiation_meta.setdefault("stance", st_meta)

            verdict = str(getattr(decision, "verdict", "")).upper()
            if verdict == "ACCEPT":
                if str(off.promise_type).upper() == "HELP":
                    tj = off.target_json or {}
                    tags = tj.get("need_tags")
                    if not isinstance(tags, list):
                        tags = tj.get("offer_need_tags")
                    if not isinstance(tags, list):
                        tags = tj.get("ask_need_tags")
                    if not isinstance(tags, list):
                        tags = []
                    promise = PromiseSpec(
                        promise_type="HELP",
                        due_month=str(off.due_month),
                        target={"need_tags": tags},
                    )
                else:
                    promise = PromiseSpec(
                        promise_type=str(off.promise_type),
                        due_month=str(off.due_month),
                        target_value=safe_float_opt(off.target_value),
                        target=off.target_json,
                    )
                reasons.append({"code": "NEGOTIATION_ACCEPT", "evidence": {"thread_id": thread_id}})
                trust1 = float(clamp01(trust1 + rcfg.trust_promise * impact * pos_mult * sev_mult * 0.80))
                # relief
                if axis == "MINUTES":
                    _apply_axis_delta(axis, -(rcfg.minutes_relief_promise * impact * pos_mult * sev_mult * 0.80))
                elif axis == "ROLE":
                    _apply_axis_delta(axis, -(rcfg.role_relief_promise * impact * pos_mult * sev_mult * 0.80))
                elif axis == "CONTRACT":
                    _apply_axis_delta(axis, -(rcfg.contract_relief_promise * impact * pos_mult * sev_mult * 0.80))
                elif axis == "HEALTH":
                    _apply_axis_delta(axis, -(rcfg.health_relief_promise * impact * pos_mult * sev_mult * 0.80))
                elif axis == "TEAM":
                    _apply_axis_delta(axis, -(rcfg.team_relief_promise * impact * pos_mult * sev_mult * 0.80))
                elif axis == "CHEMISTRY":
                    _apply_axis_delta(axis, -(rcfg.chemistry_relief_promise * impact * pos_mult * sev_mult * 0.80))
            else:
                tp, fb = _neg_penalties(verdict)
                trust1 = float(clamp01(trust1 - tp * impact * neg_mult * sev_mult))
                _apply_axis_delta(axis, fb * impact * neg_mult * sev_mult)
                reasons.append({"code": "NEGOTIATION_CONTINUES", "evidence": {"verdict": verdict, "thread_id": thread_id}})
                fu = _maybe_make_negotiation_followup(
                    decision=decision,
                    offer=off,
                    event=event,
                    now_date_iso=now_d,
                    cfg=cfg,
                    expires_month=expires_month,
                )
                if fu:
                    follow_up_events.append(fu)

                if verdict == "COUNTER":
                    tone_override = "FIRM"
                    reply_override = "Then meet me in the middle."
                elif verdict == "REJECT":
                    tone_override = "FIRM"
                    reply_override = "That is not acceptable."
                else:
                    tone_override = "ANGRY"
                    reply_override = "We are done."

        else:  # END_TALKS
            reasons.append({"code": "NEGOTIATION_END_TALKS"})
            trust1 = float(clamp01(trust1 - rcfg.trust_dismiss_penalty * impact * neg_mult * sev_mult))
            _apply_axis_delta(axis, rcfg.negotiation_reject_frustration_bump * impact * neg_mult * sev_mult)

            st_updates, _ = _apply_offer_decision_stances(
                state={"stance_skepticism": sk1, "stance_resentment": rs1, "stance_hardball": hb1},
                mental=mental,
                verdict="WALKOUT",
                insulting=False,
                base_scale=float(impact * sev_mult),
                cfg=cfg,
            )
            _apply_stance_updates(st_updates)

    # ------------------------------------------------------------------
    # Broken promise reaction events (actionable)
    # ------------------------------------------------------------------
    elif et.startswith("BROKEN_PROMISE_"):
        ptype = str(ep.get("promise_type") or "").upper()
        axis = str(ep.get("axis") or "").upper()

        if rt == "ACKNOWLEDGE":
            trust1 = float(clamp01(trust1 + rcfg.trust_acknowledge * impact * pos_mult * sev_mult * 0.55))
            _apply_axis_delta(axis, -0.03 * impact * pos_mult * sev_mult)

            deltas = {
                "resentment": -float(rcfg.broken_promise_apology_resentment_relief) * (0.5 + 0.5 * coach),
                "skepticism": -float(rcfg.broken_promise_apology_skepticism_relief) * (0.5 + 0.5 * coach),
                "hardball": -float(rcfg.broken_promise_apology_hardball_relief) * (0.5 + 0.5 * coach),
            }
            _apply_stance_updates(
                apply_stance_deltas(
                    state={"stance_skepticism": sk1, "stance_resentment": rs1, "stance_hardball": hb1},
                    deltas=deltas,
                )
            )
            reasons.append({"code": "BROKEN_PROMISE_ACKNOWLEDGED", "evidence": {"promise_type": ptype, "axis": axis}})

        elif rt == "DISMISS":
            trust1 = float(clamp01(trust1 - rcfg.trust_dismiss_penalty * impact * neg_mult * sev_mult))
            _apply_axis_delta(axis, 0.04 * impact * neg_mult * sev_mult)
            deltas = {"resentment": 0.04 * neg_mult, "skepticism": 0.03 * neg_mult, "hardball": 0.02 * neg_mult}
            _apply_stance_updates(
                apply_stance_deltas(
                    state={"stance_skepticism": sk1, "stance_resentment": rs1, "stance_hardball": hb1},
                    deltas=deltas,
                )
            )
            reasons.append({"code": "BROKEN_PROMISE_DISMISSED", "evidence": {"promise_type": ptype, "axis": axis}})

        else:
            # Re-offer a promise (goes through credibility + negotiation).
            # Use the original promise snapshot when available to keep the conversation consistent.
            snap_tv = safe_float_opt(ep.get("promise_target_value"))
            snap_t = ep.get("promise_target") if isinstance(ep.get("promise_target"), dict) else {}

            broken_snap = {
                "promise_id": str(ep.get("promise_id") or ""),
                "promise_type": ptype,
                "axis": axis,
                "due_month": str(ep.get("due_month") or ""),
                "promise_target_value": snap_tv,
                "promise_target": dict(snap_t),
            }

            # Safety: ensure the user is re-offering the same kind of promise that was broken.
            if ptype == "MINUTES" and rt != "PROMISE_MINUTES":
                return ResponseOutcome(ok=False, error="Wrong promise type for this broken promise.", state_updates={})
            if ptype == "ROLE" and rt != "PROMISE_ROLE":
                return ResponseOutcome(ok=False, error="Wrong promise type for this broken promise.", state_updates={})
            if ptype == "HELP" and rt != "PROMISE_HELP":
                return ResponseOutcome(ok=False, error="Wrong promise type for this broken promise.", state_updates={})
            if ptype == "LOAD" and rt != "PROMISE_LOAD":
                return ResponseOutcome(ok=False, error="Wrong promise type for this broken promise.", state_updates={})
            if ptype == "EXTENSION_TALKS" and rt != "PROMISE_EXTENSION_TALKS":
                return ResponseOutcome(ok=False, error="Wrong promise type for this broken promise.", state_updates={})

            repair_scale = 0.90

            offer: Optional[Offer] = None
            promise_target_value: Optional[float] = None
            promise_target: Dict[str, Any] = {}

            if ptype == "MINUTES":
                target = safe_float_opt(payload.get("target_mpg"))
                if target is None:
                    target = safe_float_opt(snap_t.get("target_mpg"))
                if target is None:
                    target = snap_tv
                if target is None:
                    target = (
                        safe_float_opt(ep.get("self_expected_mpg"))
                        or safe_float_opt(ep.get("expected_mpg"))
                        or safe_float_opt(state.get("self_expected_mpg"))
                        or safe_float_opt(state.get("minutes_expected_mpg"))
                    )
                target = float(clamp(target or 0.0, 0.0, 48.0))
                due = due_month_from_now(now_d, int(rcfg.promise_minutes_due_months))
                offer = Offer(
                    promise_type="MINUTES",
                    axis="MINUTES",
                    due_month=due,
                    target_value=target,
                    target_json={"target_mpg": target},
                )
                promise_target_value = float(target)
                promise_target = {"target_mpg": float(target)}

            elif ptype == "ROLE":
                role_tag = str(
                    payload.get("role_tag") or payload.get("role") or snap_t.get("role") or snap_t.get("role_tag") or "ROTATION"
                ).upper()

                role_focus = str(
                    payload.get("role_focus") or payload.get("focus") or snap_t.get("role_focus") or snap_t.get("focus") or "STARTS"
                ).upper()
                if role_focus not in {"STARTS", "CLOSES"}:
                    role_focus = "STARTS"

                starts_rate = safe_float_opt(payload.get("starts_rate"))
                if starts_rate is None:
                    starts_rate = safe_float_opt(payload.get("min_starts_rate"))
                if starts_rate is None:
                    starts_rate = safe_float_opt(snap_t.get("min_starts_rate")) or safe_float_opt(snap_t.get("starts_rate"))
                if starts_rate is None:
                    starts_rate = float(rcfg.promise_role_starts_rate)

                closes_rate = safe_float_opt(payload.get("closes_rate"))
                if closes_rate is None:
                    closes_rate = safe_float_opt(payload.get("min_closes_rate"))
                if closes_rate is None:
                    closes_rate = safe_float_opt(snap_t.get("min_closes_rate")) or safe_float_opt(snap_t.get("closes_rate"))
                if closes_rate is None:
                    closes_rate = float(rcfg.promise_role_closes_rate)

                starts_rate = float(clamp01(starts_rate))
                closes_rate = float(clamp01(closes_rate))

                due = due_month_from_now(now_d, int(rcfg.promise_role_due_months))
                offer = Offer(
                    promise_type="ROLE",
                    axis="ROLE",
                    due_month=due,
                    target_value=None,
                    target_json={
                        "role": role_tag,
                        "role_focus": role_focus,
                        "min_starts_rate": starts_rate,
                        "min_closes_rate": closes_rate,
                    },
                )
                promise_target = dict(offer.target_json or {})

            elif ptype == "HELP":
                ask_tags = ep.get("need_tags") if isinstance(ep.get("need_tags"), list) else []
                if not ask_tags:
                    ask_tags = snap_t.get("need_tags") if isinstance(snap_t.get("need_tags"), list) else []
                offer_tags = payload.get("need_tags") if isinstance(payload.get("need_tags"), list) else ask_tags

                due = due_month_from_now(now_d, int(rcfg.promise_help_due_months))
                offer = Offer(
                    promise_type="HELP",
                    axis="TEAM",
                    due_month=due,
                    target_value=None,
                    target_json={"ask_need_tags": ask_tags, "offer_need_tags": offer_tags, "need_tags": offer_tags},
                )
                promise_target = {"need_tags": list(offer_tags) if isinstance(offer_tags, list) else []}

            elif ptype == "LOAD":
                max_mpg = safe_float_opt(payload.get("max_mpg"))
                if max_mpg is None:
                    max_mpg = safe_float_opt(snap_t.get("max_mpg"))
                if max_mpg is None:
                    max_mpg = snap_tv
                if max_mpg is None:
                    max_mpg = float(rcfg.promise_load_max_mpg)
                max_mpg = float(clamp(max_mpg, 0.0, 48.0))

                ask_payload = dict(ep)
                if ask_payload.get("health_frustration") is None:
                    ask_payload["health_frustration"] = safe_float_opt(state.get("health_frustration"))

                ask_max_mpg = _health_load_ask_max_mpg(event_payload=ask_payload, default_max_mpg=max_mpg)

                due = due_month_from_now(now_d, int(rcfg.promise_load_due_months))
                offer = Offer(
                    promise_type="LOAD",
                    axis="HEALTH",
                    due_month=due,
                    target_value=max_mpg,
                    target_json={"max_mpg": max_mpg, "ask_max_mpg": ask_max_mpg},
                )
                promise_target = {"max_mpg": float(max_mpg)}

            elif ptype == "EXTENSION_TALKS":
                years_left = safe_float_opt(payload.get("seasons_left")) or safe_float_opt(payload.get("years_left"))
                if years_left is None:
                    years_left = safe_float_opt(snap_t.get("seasons_left")) or safe_float_opt(snap_t.get("years_left"))
                if years_left is None:
                    years_left = safe_float_opt(state.get("contract_seasons_left")) or 1.0
                years_left = float(clamp(years_left, 0.0, 10.0))

                due = due_month_from_now(now_d, int(rcfg.promise_extension_due_months))
                offer = Offer(
                    promise_type="EXTENSION_TALKS",
                    axis="CONTRACT",
                    due_month=due,
                    target_value=None,
                    target_json={"years_left": years_left, "now_month_key": due_month_from_now(now_d, 0)},
                )
                promise_target = {"years_left": float(years_left)}

            else:
                reasons.append({"code": "BROKEN_PROMISE_REPAIR_UNSUPPORTED", "evidence": {"promise_type": ptype}})

            if offer is not None:
                decision = evaluate_offer(offer=offer, state=state, mental=mental, cfg=cfg, round_index=0, max_rounds=2)
                negotiation_meta = dict(getattr(decision, "meta", {}) or {})
                negotiation_meta["decision"] = getattr(decision, "to_dict", lambda: {})()
                negotiation_meta.setdefault("broken_promise", broken_snap)
                verdict = str(getattr(decision, "verdict", "")).upper()

                st_updates, st_meta = _apply_offer_decision_stances(
                    state={"stance_skepticism": sk1, "stance_resentment": rs1, "stance_hardball": hb1},
                    mental=mental,
                    verdict=verdict,
                    insulting=bool(getattr(decision, "insulting", False)),
                    base_scale=float(impact * sev_mult),
                    cfg=cfg,
                )
                _apply_stance_updates(st_updates)
                if st_meta:
                    negotiation_meta.setdefault("stance", st_meta)

                if verdict == "ACCEPT":
                    promise = PromiseSpec(
                        promise_type=str(offer.promise_type),
                        due_month=str(offer.due_month),
                        target_value=promise_target_value,
                        target=dict(promise_target or {}),
                    )
                    trust1 = float(clamp01(trust1 + rcfg.trust_promise * impact * pos_mult * sev_mult * repair_scale))

                    ax = str(offer.axis).upper()
                    if ax == "MINUTES":
                        relief = float(rcfg.minutes_relief_promise)
                    elif ax == "ROLE":
                        relief = float(rcfg.role_relief_promise)
                    elif ax == "CONTRACT":
                        relief = float(rcfg.contract_relief_promise)
                    elif ax == "HEALTH":
                        relief = float(rcfg.health_relief_promise)
                    elif ax == "TEAM":
                        relief = float(rcfg.team_relief_promise)
                    elif ax == "CHEMISTRY":
                        relief = float(rcfg.chemistry_relief_promise)
                    else:
                        relief = float(rcfg.axis_relief_promise)
                    _apply_axis_delta(ax, -(relief * impact * pos_mult * sev_mult * repair_scale))

                    reasons.append({"code": "BROKEN_PROMISE_REPAIR_ACCEPTED", "evidence": {"promise_type": ptype, "axis": ax}})
                else:
                    tp, fb = _neg_penalties(verdict)
                    trust1 = float(clamp01(trust1 - tp * impact * neg_mult * sev_mult))
                    _apply_axis_delta(str(offer.axis).upper(), fb * impact * neg_mult * sev_mult)
                    reasons.append({"code": "BROKEN_PROMISE_REPAIR_NEGOTIATION", "evidence": {"promise_type": ptype, "verdict": verdict}})
                    fu = _maybe_make_negotiation_followup(decision=decision, offer=offer, event=event, now_date_iso=now_d, cfg=cfg)
                    if fu:
                        follow_up_events.append(fu)

                    tone_override = "FIRM" if verdict in {"COUNTER", "REJECT"} else "ANGRY"
                    reply_override = "You already said that once."

    # ------------------------------------------------------------------
    # Existing v2 event handlers
    # ------------------------------------------------------------------
    elif et == "MINUTES_COMPLAINT":
        if rt == "PROMISE_MINUTES":
            target = safe_float_opt(payload.get("target_mpg"))
            if target is None:
                target = safe_float_opt(ep.get("expected_mpg")) or safe_float_opt(state.get("minutes_expected_mpg"))
            target = float(clamp(target or 0.0, 0.0, 48.0))
            due = due_month_from_now(now_d, int(rcfg.promise_minutes_due_months))
            offer = Offer(
                promise_type="MINUTES",
                axis="MINUTES",
                due_month=due,
                target_value=target,
                target_json={"target_mpg": target},
            )
            decision = evaluate_offer(offer=offer, state=state, mental=mental, cfg=cfg, round_index=0, max_rounds=2)
            negotiation_meta = dict(getattr(decision, "meta", {}) or {})
            negotiation_meta["decision"] = getattr(decision, "to_dict", lambda: {})()
            verdict = str(getattr(decision, "verdict", "")).upper()

            st_updates, st_meta = _apply_offer_decision_stances(
                state={"stance_skepticism": sk1, "stance_resentment": rs1, "stance_hardball": hb1},
                mental=mental,
                verdict=verdict,
                insulting=bool(getattr(decision, "insulting", False)),
                base_scale=float(impact * sev_mult),
                cfg=cfg,
            )
            _apply_stance_updates(st_updates)
            if st_meta:
                negotiation_meta.setdefault("stance", st_meta)

            if verdict == "ACCEPT":
                promise = PromiseSpec(promise_type="MINUTES", due_month=due, target_value=target, target={"target_mpg": target})
                trust1 = float(clamp01(trust1 + rcfg.trust_promise * impact * pos_mult * sev_mult))
                mfr1 = float(clamp01(mfr1 - rcfg.minutes_relief_promise * impact * pos_mult * sev_mult))
                reasons.append({"code": "PROMISE_MINUTES_ACCEPTED"})
            else:
                tp, fb = _neg_penalties(verdict)
                trust1 = float(clamp01(trust1 - tp * impact * neg_mult * sev_mult))
                mfr1 = float(clamp01(mfr1 + fb * impact * neg_mult * sev_mult))
                reasons.append({"code": "PROMISE_MINUTES_NEGOTIATION", "evidence": {"verdict": verdict}})
                fu = _maybe_make_negotiation_followup(decision=decision, offer=offer, event=event, now_date_iso=now_d, cfg=cfg)
                if fu:
                    follow_up_events.append(fu)

                if verdict == "COUNTER":
                    tone_override = "FIRM"
                    co = getattr(decision, "counter_offer", None)
                    cm = None
                    if co and getattr(co, "target_json", None):
                        cm = safe_float_opt(co.target_json.get("target_mpg"))
                    reply_override = f"I need closer to {cm:.1f} mpg." if cm is not None else "That's not enough."
                elif verdict == "REJECT":
                    tone_override = "FIRM"
                    reply_override = "That's not enough."
                else:
                    tone_override = "ANGRY"
                    reply_override = "Don't waste my time."

        else:
            trust1, mfr1, tfr1, tr_level1, promise, reasons = _apply_minutes_complaint(
                rt,
                payload,
                now_d,
                trust0=trust0,
                mfr0=mfr0,
                tfr0=tfr0,
                tr_level0=tr_level0,
                lev=lev,
                ego=ego,
                amb=amb,
                loy=loy,
                coach=coach,
                impact=impact,
                pos_mult=pos_mult,
                neg_mult=neg_mult,
                sev_mult=sev_mult,
                rcfg=rcfg,
                event=event,
                state=state,
            )

    elif et == "HELP_DEMAND":
        if rt == "PROMISE_HELP":
            ask_tags = ep.get("need_tags") if isinstance(ep.get("need_tags"), list) else []
            offer_tags = payload.get("need_tags") if isinstance(payload.get("need_tags"), list) else ask_tags
            due = due_month_from_now(now_d, int(rcfg.promise_help_due_months))
            offer = Offer(
                promise_type="HELP",
                axis="TEAM",
                due_month=due,
                target_value=None,
                target_json={"ask_need_tags": ask_tags, "offer_need_tags": offer_tags, "need_tags": offer_tags},
            )
            decision = evaluate_offer(offer=offer, state=state, mental=mental, cfg=cfg, round_index=0, max_rounds=2)
            negotiation_meta = dict(getattr(decision, "meta", {}) or {})
            negotiation_meta["decision"] = getattr(decision, "to_dict", lambda: {})()
            verdict = str(getattr(decision, "verdict", "")).upper()

            st_updates, st_meta = _apply_offer_decision_stances(
                state={"stance_skepticism": sk1, "stance_resentment": rs1, "stance_hardball": hb1},
                mental=mental,
                verdict=verdict,
                insulting=bool(getattr(decision, "insulting", False)),
                base_scale=float(impact * sev_mult),
                cfg=cfg,
            )
            _apply_stance_updates(st_updates)
            if st_meta:
                negotiation_meta.setdefault("stance", st_meta)

            if verdict == "ACCEPT":
                promise = PromiseSpec(promise_type="HELP", due_month=due, target={"need_tags": offer_tags})
                trust1 = float(clamp01(trust1 + rcfg.trust_promise * impact * pos_mult * sev_mult))
                tfr1 = float(clamp01(tfr1 - rcfg.team_relief_promise * impact * pos_mult * sev_mult))
                reasons.append({"code": "PROMISE_HELP_ACCEPTED"})
            else:
                tp, fb = _neg_penalties(verdict)
                trust1 = float(clamp01(trust1 - tp * impact * neg_mult * sev_mult))
                tfr1 = float(clamp01(tfr1 + fb * impact * neg_mult * sev_mult))
                reasons.append({"code": "PROMISE_HELP_NEGOTIATION", "evidence": {"verdict": verdict}})
                fu = _maybe_make_negotiation_followup(decision=decision, offer=offer, event=event, now_date_iso=now_d, cfg=cfg)
                if fu:
                    follow_up_events.append(fu)
                tone_override = "FIRM" if verdict in {"COUNTER", "REJECT"} else "ANGRY"
                reply_override = "Prove it."

        else:
            trust1, _mfr_tmp, tfr1, _tr_tmp, promise, reasons = _apply_help_demand(
                rt,
                payload,
                now_d,
                trust0=trust0,
                mfr0=mfr0,
                tfr0=tfr0,
                tr_level0=tr_level0,
                lev=lev,
                ego=ego,
                amb=amb,
                loy=loy,
                coach=coach,
                impact=impact,
                pos_mult=pos_mult,
                neg_mult=neg_mult,
                sev_mult=sev_mult,
                rcfg=rcfg,
                event=event,
            )

    elif et in {"TRADE_REQUEST", "TRADE_REQUEST_PUBLIC"}:
        trust1, mfr1, tfr1, tr_level1, promise, reasons = _apply_trade_request(
            et,
            rt,
            payload,
            now_d,
            trust0=trust0,
            mfr0=mfr0,
            tfr0=tfr0,
            tr_level0=tr_level0,
            lev=lev,
            ego=ego,
            amb=amb,
            loy=loy,
            coach=coach,
            impact=impact,
            pos_mult=pos_mult,
            neg_mult=neg_mult,
            sev_mult=sev_mult,
            rcfg=rcfg,
            event=event,
        )

    elif et == "LOCKER_ROOM_MEETING":
        attendees = ep.get("attendees") if isinstance(ep.get("attendees"), list) else []
        bulk_deltas: Dict[str, Dict[str, Any]] = {}
        for a in attendees:
            pid = str(a.get("player_id") or "")
            if not pid:
                continue
            dt = float(rcfg.team_talk_trust_calm) * impact * pos_mult * sev_mult
            df = float(rcfg.team_talk_team_bump_calm) * impact * pos_mult * sev_mult
            if rt == "TEAM_TALK_FIRM":
                dt = float(rcfg.team_talk_trust_firm) * impact * pos_mult * sev_mult
                df = float(rcfg.team_talk_team_bump_firm) * impact * pos_mult * sev_mult
            if rt == "TEAM_TALK_IGNORE":
                dt = -float(rcfg.team_talk_trust_ignore) * impact * neg_mult * sev_mult
                df = float(rcfg.team_talk_team_bump_ignore) * impact * neg_mult * sev_mult
            bulk_deltas[pid] = {"trust_delta": dt, "team_frustration_delta": df}

        tone = _tone_for_response(et, rt)
        reply = _player_reply(et, rt, tone)

        return ResponseOutcome(
            ok=True,
            event_type=et,
            response_type=rt,
            state_updates={},
            bulk_state_deltas=bulk_deltas,
            promise=None,
            follow_up_events=[],
            reasons=[{"code": "LOCKER_ROOM_MEETING_HANDLED", "evidence": {"attendees": len(attendees)}}],
            meta={"bulk_deltas": bulk_deltas},
            tone=tone,
            player_reply=reply,
        )

    else:
        axis = _axis_for_v2_event(et)

        # Prefer canonical numeric stage_i when available (new payload format).
        # Fall back to legacy `stage` which may be a label ("PRIVATE") or an int.
        # If both are missing, derive a reasonable default from the event type suffix.
        default_stage = STAGE_PRIVATE
        if et.endswith("_PUBLIC"):
            default_stage = STAGE_PUBLIC
        elif et.endswith("_AGENT"):
            default_stage = STAGE_AGENT
        elif et.endswith("_PRIVATE"):
            default_stage = STAGE_PRIVATE

        stage_i = stage_i_from_payload(ep, default=default_stage)
        stage = stage_label(stage_i)

        if rt == "ACKNOWLEDGE":
            trust1 = float(clamp01(trust1 + rcfg.trust_acknowledge * impact * pos_mult * sev_mult))
            _apply_axis_delta(axis, -(rcfg.axis_relief_acknowledge * impact * pos_mult * sev_mult))
            reasons.append({"code": "ACKNOWLEDGED", "evidence": {"axis": axis, "stage_i": stage_i, "stage": stage}})

        elif rt == "DISMISS":
            trust1 = float(clamp01(trust1 - rcfg.trust_dismiss_penalty * impact * neg_mult * sev_mult))
            _apply_axis_delta(axis, rcfg.axis_bump_dismiss * impact * neg_mult * sev_mult)
            reasons.append({"code": "DISMISSED", "evidence": {"axis": axis, "stage_i": stage_i, "stage": stage}})

        elif rt == "PROMISE_ROLE" and axis == "ROLE":
            role_tag = str(payload.get("role_tag") or payload.get("role") or "ROTATION").upper()
            role_focus = str(
                payload.get("role_focus")
                or payload.get("focus")
                or ep.get("role_focus")
                or ep.get("focus")
                or "STARTS"
            ).upper()
            if role_focus not in {"STARTS", "CLOSES"}:
                role_focus = "STARTS"
            starts_rate = safe_float_opt(payload.get("starts_rate"))
            closes_rate = safe_float_opt(payload.get("closes_rate"))
            if starts_rate is None:
                starts_rate = float(rcfg.promise_role_starts_rate)
            if closes_rate is None:
                closes_rate = float(rcfg.promise_role_closes_rate)
            starts_rate = float(clamp01(starts_rate))
            closes_rate = float(clamp01(closes_rate))

            due = due_month_from_now(now_d, int(rcfg.promise_role_due_months))
            offer = Offer(
                promise_type="ROLE",
                axis="ROLE",
                due_month=due,
                target_value=None,
                target_json={
                    "role": role_tag,
                    "role_focus": role_focus,
                    "min_starts_rate": starts_rate,
                    "min_closes_rate": closes_rate,
                },
            )
            decision = evaluate_offer(offer=offer, state=state, mental=mental, cfg=cfg, round_index=0, max_rounds=2)
            negotiation_meta = dict(getattr(decision, "meta", {}) or {})
            negotiation_meta["decision"] = getattr(decision, "to_dict", lambda: {})()
            verdict = str(getattr(decision, "verdict", "")).upper()

            st_updates, st_meta = _apply_offer_decision_stances(
                state={"stance_skepticism": sk1, "stance_resentment": rs1, "stance_hardball": hb1},
                mental=mental,
                verdict=verdict,
                insulting=bool(getattr(decision, "insulting", False)),
                base_scale=float(impact * sev_mult),
                cfg=cfg,
            )
            _apply_stance_updates(st_updates)
            if st_meta:
                negotiation_meta.setdefault("stance", st_meta)

            if verdict == "ACCEPT":
                promise = PromiseSpec(
                    promise_type="ROLE",
                    due_month=due,
                    target={
                        "role": role_tag,
                        "role_focus": role_focus,
                        "min_starts_rate": starts_rate,
                        "min_closes_rate": closes_rate,
                    },
                )
                trust1 = float(clamp01(trust1 + rcfg.trust_promise * impact * pos_mult * sev_mult))
                _apply_axis_delta(axis, -(rcfg.role_relief_promise * impact * pos_mult * sev_mult))
                reasons.append({"code": "PROMISE_ROLE_ACCEPTED"})
            else:
                tp, fb = _neg_penalties(verdict)
                trust1 = float(clamp01(trust1 - tp * impact * neg_mult * sev_mult))
                _apply_axis_delta(axis, fb * impact * neg_mult * sev_mult)
                reasons.append({"code": "PROMISE_ROLE_NEGOTIATION", "evidence": {"verdict": verdict}})
                fu = _maybe_make_negotiation_followup(decision=decision, offer=offer, event=event, now_date_iso=now_d, cfg=cfg)
                if fu:
                    follow_up_events.append(fu)
                tone_override = "FIRM" if verdict in {"COUNTER", "REJECT"} else "ANGRY"
                reply_override = "That's not the role I want."

        elif rt == "PROMISE_LOAD" and axis == "HEALTH":
            max_mpg = safe_float_opt(payload.get("max_mpg"))
            if max_mpg is None:
                max_mpg = float(rcfg.promise_load_max_mpg)
            max_mpg = float(clamp(max_mpg, 0.0, 48.0))
            due = due_month_from_now(now_d, int(rcfg.promise_load_due_months))
            ask_max_mpg = _health_load_ask_max_mpg(event_payload=ep, default_max_mpg=max_mpg)
            offer = Offer(
                promise_type="LOAD",
                axis="HEALTH",
                due_month=due,
                target_value=max_mpg,
                target_json={"max_mpg": max_mpg, "ask_max_mpg": ask_max_mpg},
            )
            decision = evaluate_offer(offer=offer, state=state, mental=mental, cfg=cfg, round_index=0, max_rounds=2)
            negotiation_meta = dict(getattr(decision, "meta", {}) or {})
            negotiation_meta["decision"] = getattr(decision, "to_dict", lambda: {})()
            verdict = str(getattr(decision, "verdict", "")).upper()

            st_updates, st_meta = _apply_offer_decision_stances(
                state={"stance_skepticism": sk1, "stance_resentment": rs1, "stance_hardball": hb1},
                mental=mental,
                verdict=verdict,
                insulting=bool(getattr(decision, "insulting", False)),
                base_scale=float(impact * sev_mult),
                cfg=cfg,
            )
            _apply_stance_updates(st_updates)
            if st_meta:
                negotiation_meta.setdefault("stance", st_meta)

            if verdict == "ACCEPT":
                promise = PromiseSpec(promise_type="LOAD", due_month=due, target={"max_mpg": max_mpg})
                trust1 = float(clamp01(trust1 + rcfg.trust_promise * impact * pos_mult * sev_mult))
                _apply_axis_delta(axis, -(rcfg.health_relief_promise * impact * pos_mult * sev_mult))
                reasons.append({"code": "PROMISE_LOAD_ACCEPTED"})
            else:
                tp, fb = _neg_penalties(verdict)
                trust1 = float(clamp01(trust1 - tp * impact * neg_mult * sev_mult))
                _apply_axis_delta(axis, fb * impact * neg_mult * sev_mult)
                reasons.append({"code": "PROMISE_LOAD_NEGOTIATION", "evidence": {"verdict": verdict}})
                fu = _maybe_make_negotiation_followup(decision=decision, offer=offer, event=event, now_date_iso=now_d, cfg=cfg)
                if fu:
                    follow_up_events.append(fu)
                tone_override = "FIRM" if verdict in {"COUNTER", "REJECT"} else "ANGRY"
                reply_override = "I'm not risking my body for this."

        elif rt == "PROMISE_HELP" and axis == "TEAM":
            ask_tags = ep.get("need_tags") if isinstance(ep.get("need_tags"), list) else []
            offer_tags = payload.get("need_tags") if isinstance(payload.get("need_tags"), list) else ask_tags
            due = due_month_from_now(now_d, int(rcfg.promise_help_due_months))
            offer = Offer(
                promise_type="HELP",
                axis="TEAM",
                due_month=due,
                target_value=None,
                target_json={"ask_need_tags": ask_tags, "offer_need_tags": offer_tags, "need_tags": offer_tags},
            )
            decision = evaluate_offer(offer=offer, state=state, mental=mental, cfg=cfg, round_index=0, max_rounds=2)
            negotiation_meta = dict(getattr(decision, "meta", {}) or {})
            negotiation_meta["decision"] = getattr(decision, "to_dict", lambda: {})()
            verdict = str(getattr(decision, "verdict", "")).upper()

            st_updates, st_meta = _apply_offer_decision_stances(
                state={"stance_skepticism": sk1, "stance_resentment": rs1, "stance_hardball": hb1},
                mental=mental,
                verdict=verdict,
                insulting=bool(getattr(decision, "insulting", False)),
                base_scale=float(impact * sev_mult),
                cfg=cfg,
            )
            _apply_stance_updates(st_updates)
            if st_meta:
                negotiation_meta.setdefault("stance", st_meta)

            if verdict == "ACCEPT":
                promise = PromiseSpec(promise_type="HELP", due_month=due, target={"need_tags": offer_tags})
                trust1 = float(clamp01(trust1 + rcfg.trust_promise * impact * pos_mult * sev_mult))
                _apply_axis_delta(axis, -rcfg.team_relief_promise * impact * pos_mult * sev_mult)
                reasons.append({"code": "PROMISE_HELP_ACCEPTED"})
            else:
                tp, fb = _neg_penalties(verdict)
                trust1 = float(clamp01(trust1 - tp * impact * neg_mult * sev_mult))
                _apply_axis_delta(axis, fb * impact * neg_mult * sev_mult)
                reasons.append({"code": "PROMISE_HELP_NEGOTIATION", "evidence": {"verdict": verdict}})
                fu = _maybe_make_negotiation_followup(decision=decision, offer=offer, event=event, now_date_iso=now_d, cfg=cfg)
                if fu:
                    follow_up_events.append(fu)
                tone_override = "FIRM" if verdict in {"COUNTER", "REJECT"} else "ANGRY"
                reply_override = "We need real help, not words."

        elif rt == "PROMISE_EXTENSION_TALKS" and axis == "CONTRACT":
            years_left = safe_float_opt(ep.get("seasons_left")) or safe_float_opt(ep.get("years_left"))
            if years_left is None:
                years_left = safe_float_opt(state.get("contract_seasons_left")) or 1.0
            years_left = float(clamp(years_left, 0.0, 10.0))
            due = due_month_from_now(now_d, int(rcfg.promise_extension_due_months))
            offer = Offer(
                promise_type="EXTENSION_TALKS",
                axis="CONTRACT",
                due_month=due,
                target_value=None,
                target_json={"years_left": years_left, "now_month_key": due_month_from_now(now_d, 0)},
            )
            decision = evaluate_offer(offer=offer, state=state, mental=mental, cfg=cfg, round_index=0, max_rounds=2)
            negotiation_meta = dict(getattr(decision, "meta", {}) or {})
            negotiation_meta["decision"] = getattr(decision, "to_dict", lambda: {})()
            verdict = str(getattr(decision, "verdict", "")).upper()

            st_updates, st_meta = _apply_offer_decision_stances(
                state={"stance_skepticism": sk1, "stance_resentment": rs1, "stance_hardball": hb1},
                mental=mental,
                verdict=verdict,
                insulting=bool(getattr(decision, "insulting", False)),
                base_scale=float(impact * sev_mult),
                cfg=cfg,
            )
            _apply_stance_updates(st_updates)
            if st_meta:
                negotiation_meta.setdefault("stance", st_meta)

            if verdict == "ACCEPT":
                promise = PromiseSpec(promise_type="EXTENSION_TALKS", due_month=due, target={"years_left": years_left})
                trust1 = float(clamp01(trust1 + rcfg.trust_promise * impact * pos_mult * sev_mult))
                _apply_axis_delta(axis, -(rcfg.contract_relief_promise * impact * pos_mult * sev_mult))
                reasons.append({"code": "PROMISE_EXTENSION_TALKS_ACCEPTED"})
            else:
                tp, fb = _neg_penalties(verdict)
                trust1 = float(clamp01(trust1 - tp * impact * neg_mult * sev_mult))
                _apply_axis_delta(axis, fb * impact * neg_mult * sev_mult)
                reasons.append({"code": "PROMISE_EXTENSION_TALKS_NEGOTIATION", "evidence": {"verdict": verdict}})
                fu = _maybe_make_negotiation_followup(decision=decision, offer=offer, event=event, now_date_iso=now_d, cfg=cfg)
                if fu:
                    follow_up_events.append(fu)
                tone_override = "FIRM" if verdict in {"COUNTER", "REJECT"} else "ANGRY"
                reply_override = "Talk is cheap."

        else:
            return ResponseOutcome(ok=False, error=f"Unhandled response '{rt}' for axis event '{et}'.", state_updates={})

    # ------------------------------------------------------------------
    # Build updates + meta
    # ------------------------------------------------------------------
    state_updates: Dict[str, Any] = {
        "trust": float(clamp01(trust1)),
        "minutes_frustration": float(clamp01(mfr1)),
        "role_frustration": float(clamp01(rfr1)),
        "contract_frustration": float(clamp01(cfr1)),
        "health_frustration": float(clamp01(hfr1)),
        "team_frustration": float(clamp01(tfr1)),
        "chemistry_frustration": float(clamp01(chfr1)),
        "trade_request_level": int(tr_level1),
        "stance_skepticism": float(clamp01(sk1)),
        "stance_resentment": float(clamp01(rs1)),
        "stance_hardball": float(clamp01(hb1)),
    }

    tone = tone_override or _tone_for_response(et, rt)
    reply = reply_override or _player_reply(et, rt, tone)

    meta: Dict[str, Any] = {
        "event_type": et,
        "response_type": rt,
        "before": {
            "trust": trust0,
            "minutes_frustration": mfr0,
            "role_frustration": rfr0,
            "contract_frustration": cfr0,
            "health_frustration": hfr0,
            "team_frustration": tfr0,
            "chemistry_frustration": chfr0,
            "trade_request_level": tr_level0,
            "stance_skepticism": sk0,
            "stance_resentment": rs0,
            "stance_hardball": hb0,
        },
        "after": {
            "trust": state_updates["trust"],
            "minutes_frustration": state_updates["minutes_frustration"],
            "role_frustration": state_updates["role_frustration"],
            "contract_frustration": state_updates["contract_frustration"],
            "health_frustration": state_updates["health_frustration"],
            "team_frustration": state_updates["team_frustration"],
            "chemistry_frustration": state_updates["chemistry_frustration"],
            "trade_request_level": state_updates["trade_request_level"],
            "stance_skepticism": state_updates["stance_skepticism"],
            "stance_resentment": state_updates["stance_resentment"],
            "stance_hardball": state_updates["stance_hardball"],
        },
        "deltas": {
            "trust": float(state_updates["trust"] - trust0),
            "minutes_frustration": float(state_updates["minutes_frustration"] - mfr0),
            "role_frustration": float(state_updates["role_frustration"] - rfr0),
            "contract_frustration": float(state_updates["contract_frustration"] - cfr0),
            "health_frustration": float(state_updates["health_frustration"] - hfr0),
            "team_frustration": float(state_updates["team_frustration"] - tfr0),
            "chemistry_frustration": float(state_updates["chemistry_frustration"] - chfr0),
            "trade_request_level": float(state_updates["trade_request_level"] - tr_level0),
            "stance_skepticism": float(state_updates["stance_skepticism"] - sk0),
            "stance_resentment": float(state_updates["stance_resentment"] - rs0),
            "stance_hardball": float(state_updates["stance_hardball"] - hb0),
        },
    }
    if negotiation_meta:
        meta["negotiation"] = negotiation_meta

    return ResponseOutcome(
        ok=True,
        event_type=et,
        response_type=rt,
        state_updates=state_updates,
        promise=promise,
        follow_up_events=follow_up_events,
        reasons=reasons,
        meta=meta,
        tone=tone,
        player_reply=reply,
    )

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _allowed_responses_for_event(event_type: str, event_payload: Optional[Mapping[str, Any]] = None) -> set[str]:
    et = str(event_type or "").upper()

    if et == "PROMISE_NEGOTIATION":
        return {"ACCEPT_COUNTER", "MAKE_NEW_OFFER", "END_TALKS"}

    if et.startswith("BROKEN_PROMISE_"):
        base = {"ACKNOWLEDGE", "DISMISS"}
        ep = event_payload if isinstance(event_payload, Mapping) else {}
        ptype = str(ep.get("promise_type") or "").upper()

        if ptype == "MINUTES":
            base.add("PROMISE_MINUTES")
        elif ptype == "ROLE":
            base.add("PROMISE_ROLE")
        elif ptype == "HELP":
            base.add("PROMISE_HELP")
        elif ptype == "LOAD":
            base.add("PROMISE_LOAD")
        elif ptype == "EXTENSION_TALKS":
            base.add("PROMISE_EXTENSION_TALKS")
        return base

    if et == "MINUTES_COMPLAINT":
        return {"ACKNOWLEDGE", "PROMISE_MINUTES", "DISMISS"}
    if et == "HELP_DEMAND":
        return {"ACKNOWLEDGE", "PROMISE_HELP", "REFUSE_HELP"}
    if et in {"TRADE_REQUEST", "TRADE_REQUEST_PUBLIC"}:
        return {"ACKNOWLEDGE", "SHOP_TRADE", "REFUSE_TRADE", "PROMISE_COMPETE"}

    # v2 issue families
    if et.startswith("ROLE_"):
        return {"ACKNOWLEDGE", "PROMISE_ROLE", "DISMISS"}
    if et.startswith("CONTRACT_"):
        return {"ACKNOWLEDGE", "PROMISE_EXTENSION_TALKS", "DISMISS"}
    if et.startswith("HEALTH_"):
        return {"ACKNOWLEDGE", "PROMISE_LOAD", "DISMISS"}
    if et.startswith("TEAM_"):
        return {"ACKNOWLEDGE", "PROMISE_HELP", "DISMISS"}
    if et.startswith("CHEMISTRY_"):
        return {"ACKNOWLEDGE", "DISMISS"}

    if et == "LOCKER_ROOM_MEETING":
        return {"TEAM_TALK_CALM", "TEAM_TALK_FIRM", "TEAM_TALK_IGNORE"}

    return {"ACKNOWLEDGE"}


def _axis_for_v2_event(event_type: str) -> str:
    et = str(event_type or "").upper()
    if et.startswith("ROLE_"):
        return "ROLE"
    if et.startswith("CONTRACT_"):
        return "CONTRACT"
    if et.startswith("HEALTH_"):
        return "HEALTH"
    if et.startswith("CHEMISTRY_"):
        return "CHEMISTRY"
    if et.startswith("TEAM_"):
        return "TEAM"
    return "UNKNOWN"


def _extract_leverage(*, state: Mapping[str, Any], event: Mapping[str, Any]) -> float:
    # Prefer state leverage (current roster), then event payload leverage.
    lev = safe_float_opt(state.get("leverage"))
    if lev is None:
        payload = event.get("payload")
        if isinstance(payload, Mapping):
            lev = safe_float(payload.get("leverage"), 0.0)
        else:
            lev = 0.0
    return float(clamp01(lev))


def _tone_for_response(
    event_type: str,
    response_type: str,
    *,
    ego: float = 0.5,
    sev: float = 0.5,
) -> Literal["CALM", "FIRM", "ANGRY"]:
    rt = str(response_type).upper()

    # Promises and positive team talks
    if rt in {
        "PROMISE_MINUTES",
        "PROMISE_HELP",
        "SHOP_TRADE",
        "PROMISE_COMPETE",
        "PROMISE_ROLE",
        "PROMISE_LOAD",
        "PROMISE_EXTENSION_TALKS",
        "TEAM_TALK_CALM",
        "ACCEPT_COUNTER",
    }:
        return "CALM"

    if rt == "TEAM_TALK_FIRM":
        return "FIRM"
    if rt == "TEAM_TALK_IGNORE":
        return "ANGRY"

    if rt == "MAKE_NEW_OFFER":
        return "FIRM"
    if rt == "END_TALKS":
        return "ANGRY"

    if rt in {"ACKNOWLEDGE"}:
        return "FIRM" if sev > 0.65 else "CALM"

    # Negative responses
    if ego > 0.70 or sev > 0.75:
        return "ANGRY"
    return "FIRM"


def _player_reply(event_type: str, response_type: str, tone: str) -> str:
    # Keep this short; UI can localize or override.
    rt = str(response_type).upper()

    if rt == "ACKNOWLEDGE":
        return "Alright. I hear you." if tone != "ANGRY" else "You better mean that."

    if rt in {
        "PROMISE_MINUTES",
        "PROMISE_HELP",
        "SHOP_TRADE",
        "PROMISE_COMPETE",
        "PROMISE_ROLE",
        "PROMISE_LOAD",
        "PROMISE_EXTENSION_TALKS",
        "ACCEPT_COUNTER",
    }:
        return "Okay. I'll hold you to that." if tone != "ANGRY" else "Don't waste my time."

    if rt == "TEAM_TALK_CALM":
        return "Okay. Let's reset." if tone != "ANGRY" else "We'll see."
    if rt == "TEAM_TALK_FIRM":
        return "We'll respond the right way." if tone != "ANGRY" else "Don't lecture me."
    if rt == "TEAM_TALK_IGNORE":
        return "..." if tone != "ANGRY" else "Unbelievable."

    if rt == "MAKE_NEW_OFFER":
        return "Then show me with actions." if tone != "ANGRY" else "Stop talking."

    if rt == "END_TALKS":
        return "We're done here." if tone == "ANGRY" else "Fine."

    if rt in {"DISMISS", "REFUSE_HELP", "REFUSE_TRADE"}:
        return "So that's how it is." if tone != "ANGRY" else "This is disrespectful."

    return "Understood."


# ---------------------------------------------------------------------------
# v3 negotiation helpers
# ---------------------------------------------------------------------------


def _offer_from_dict(d: Mapping[str, Any]) -> Offer:
    tj = d.get("target_json") if isinstance(d.get("target_json"), dict) else None
    due_month = str(d.get("due_month") or "").strip()
    if not due_month and isinstance(tj, Mapping):
        due_month = str(tj.get("due_month") or "").strip()
    return Offer(
        promise_type=str(d.get("promise_type") or "").upper(),
        axis=str(d.get("axis") or "").upper(),
        due_month=due_month,
        target_value=safe_float_opt(d.get("target_value")),
        target_json=tj,
    )


def _is_valid_month_key(value: Any) -> bool:
    s = str(value or "").strip()
    if len(s) != 7 or s[4] != "-":
        return False
    try:
        y = int(s[:4])
        m = int(s[5:7])
    except Exception:
        return False
    return y >= 1 and 1 <= m <= 12


def _health_load_ask_max_mpg(*, event_payload: Mapping[str, Any], default_max_mpg: float) -> float:
    """Derive LOAD ask cap from health context (fatigue/frustration)."""
    fatigue_lvl = None
    fat = event_payload.get("fatigue")
    if isinstance(fat, Mapping):
        fatigue_lvl = safe_float_opt(fat.get("fatigue"))
    if fatigue_lvl is None:
        fatigue_lvl = safe_float_opt(event_payload.get("fatigue_level"))

    health_fr = safe_float_opt(event_payload.get("health_frustration"))

    if fatigue_lvl is None and health_fr is None:
        return float(clamp(default_max_mpg, 12.0, 48.0))

    fat_term = clamp01(fatigue_lvl if fatigue_lvl is not None else 0.0)
    fr_term = clamp01(health_fr if health_fr is not None else fat_term)

    # High fatigue/frustration should request a stricter cap.
    ask = 34.0 - 10.0 * float(fat_term) - 6.0 * float(fr_term)
    return float(clamp(ask, 12.0, 40.0))


def _maybe_make_negotiation_followup(
    *,
    decision: Any,
    offer: Offer,
    event: Mapping[str, Any],
    now_date_iso: str,
    cfg: AgencyConfig,
    expires_month: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    verdict = str(getattr(decision, "verdict", "") or "").upper()
    if verdict not in {"COUNTER", "REJECT"}:
        return None

    source_event_id = str(event.get("event_id") or "")
    ep = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    thread_id = str(ep.get("thread_id") or "") or make_thread_id(source_event_id, offer.promise_type)

    # Negotiation threads expire quickly (prevents backlog spam).
    if not expires_month:
        try:
            expire_m = int(getattr(cfg.negotiation, "expire_months", 1))
        except Exception:
            expire_m = 1
        expires_month = due_month_from_now(now_date_iso, expire_m)

    return make_negotiation_event(
        thread_id=thread_id,
        source_event_id=source_event_id,
        player_id=str(event.get("player_id") or ""),
        team_id=str(event.get("team_id") or ""),
        season_year=int(event.get("season_year") or 0),
        now_date_iso=now_date_iso,
        offer=offer,
        decision=decision,
        cfg=cfg,
    )


def _apply_offer_decision_stances(
    *,
    state: Mapping[str, Any],
    mental: Mapping[str, Any],
    verdict: str,
    insulting: bool,
    base_scale: float,
    cfg: AgencyConfig,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Return stance state_updates + meta for an offer decision."""
    deltas, meta = stance_deltas_on_offer_decision(
        verdict=str(verdict).upper(),
        insulting=bool(insulting),
        mental=mental,
        cfg=cfg,
    )
    if not deltas:
        return {}, dict(meta or {})
    updates = apply_stance_deltas(state=state, deltas=deltas)
    return updates, dict(meta or {})


# ---------------------------------------------------------------------------
# Event-specific appliers
# ---------------------------------------------------------------------------


def _apply_minutes_complaint(
    rt: str,
    payload: Mapping[str, Any],
    now_date_iso: str,
    *,
    trust0: float,
    mfr0: float,
    tfr0: float,
    tr_level0: int,
    lev: float,
    ego: float,
    amb: float,
    loy: float,
    coach: float,
    impact: float,
    pos_mult: float,
    neg_mult: float,
    sev_mult: float,
    rcfg: ResponseConfig,
    event: Mapping[str, Any],
    state: Mapping[str, Any],
) -> tuple[float, float, float, int, Optional[PromiseSpec], List[Dict[str, Any]]]:
    reasons: List[Dict[str, Any]] = []
    trust1, mfr1, tfr1 = trust0, mfr0, tfr0
    tr_level1 = tr_level0
    promise: Optional[PromiseSpec] = None

    if rt == "ACKNOWLEDGE":
        dt = float(rcfg.trust_acknowledge) * impact * pos_mult * sev_mult
        trust1 += dt
        mfr1 -= float(rcfg.minutes_relief_acknowledge) * impact * (0.65 + 0.35 * coach) * sev_mult
        reasons.append({"code": "ACKNOWLEDGED_MINUTES", "evidence": {"trust_delta": dt}})

    elif rt == "PROMISE_MINUTES":
        dt = float(rcfg.trust_promise) * impact * pos_mult * sev_mult
        trust1 += dt
        mfr1 -= float(rcfg.minutes_relief_promise) * impact * sev_mult

        # Target MPG
        target = safe_float_opt(payload.get("target_mpg"))
        if target is None:
            # Try event/state expected mpg
            evp = event.get("payload")
            target = None
            if isinstance(evp, Mapping):
                target = safe_float_opt(evp.get("expected_mpg"))
            if target is None:
                target = safe_float(state.get("minutes_expected_mpg"), 24.0)

        target = float(clamp(target, float(rcfg.promise_minutes_min_mpg), float(rcfg.promise_minutes_max_mpg)))

        due = due_month_from_now(now_date_iso, int(rcfg.promise_minutes_due_months))
        promise = PromiseSpec(
            promise_type="MINUTES",
            due_month=due,
            target_value=float(target),
            target={"target_mpg": float(target)},
        )

        reasons.append(
            {
                "code": "PROMISE_MINUTES_CREATED",
                "evidence": {"due_month": due, "target_mpg": float(target), "trust_delta": dt},
            }
        )

    elif rt == "DISMISS":
        dt = -float(rcfg.trust_dismiss_penalty) * impact * neg_mult * sev_mult
        trust1 += dt
        mfr1 += float(rcfg.minutes_bump_dismiss) * impact * (0.70 + 0.30 * ego) * sev_mult

        # Dismissal can accelerate a trade request *only* if frustration is already extreme and leverage is meaningful.
        if mfr1 >= float(rcfg.dismiss_escalate_trade_fr_threshold) and lev >= 0.60 and (ego >= 0.65 or amb >= 0.65):
            tr_level1 = max(tr_level1, 1)
            reasons.append({"code": "DISMISS_ESCALATED_TRADE_PRESSURE", "evidence": {"trade_request_level": tr_level1}})

        reasons.append({"code": "DISMISSED_MINUTES", "evidence": {"trust_delta": dt}})

    return trust1, mfr1, tfr1, tr_level1, promise, reasons


def _apply_help_demand(
    rt: str,
    payload: Mapping[str, Any],
    now_date_iso: str,
    *,
    trust0: float,
    mfr0: float,
    tfr0: float,
    tr_level0: int,
    lev: float,
    ego: float,
    amb: float,
    loy: float,
    coach: float,
    impact: float,
    pos_mult: float,
    neg_mult: float,
    sev_mult: float,
    rcfg: ResponseConfig,
    event: Mapping[str, Any],
) -> tuple[float, float, float, int, Optional[PromiseSpec], List[Dict[str, Any]]]:
    reasons: List[Dict[str, Any]] = []
    trust1, mfr1, tfr1 = trust0, mfr0, tfr0
    tr_level1 = tr_level0
    promise: Optional[PromiseSpec] = None

    if rt == "ACKNOWLEDGE":
        dt = float(rcfg.trust_acknowledge) * impact * pos_mult * sev_mult * 0.70
        trust1 += dt
        tfr1 -= float(rcfg.team_relief_acknowledge) * impact * sev_mult
        reasons.append({"code": "ACKNOWLEDGED_HELP", "evidence": {"trust_delta": dt}})

    elif rt == "PROMISE_HELP":
        dt = float(rcfg.trust_promise) * impact * pos_mult * sev_mult * 0.85
        trust1 += dt
        tfr1 -= float(rcfg.team_relief_promise) * impact * sev_mult

        due = due_month_from_now(now_date_iso, int(rcfg.promise_help_due_months))
      
        need_tags = payload.get("need_tags")
        need_tag = payload.get("need_tag")

        if (not isinstance(need_tags, list) or not need_tags) and (need_tag is None or not str(need_tag).strip()):
            evp = event.get("payload")
            if isinstance(evp, Mapping):
                if not isinstance(need_tags, list) or not need_tags:
                    need_tags = evp.get("need_tags")
                if need_tag is None:
                    need_tag = evp.get("need_tag")

        norm_tags: List[str] = []
        if isinstance(need_tags, list):
            for t in need_tags:
                if t is None:
                    continue
                s = str(t).strip().upper()
                if s:
                    norm_tags.append(s)

        if not norm_tags and need_tag is not None:
            s = str(need_tag).strip().upper()
            if s:
                norm_tags = [s]
      
        target: Dict[str, Any] = {}
        if norm_tags:
            target["need_tags"] = norm_tags

        promise = PromiseSpec(
            promise_type="HELP",
            due_month=due,
            target_value=None,
            target=target,
        )
        reasons.append({"code": "PROMISE_HELP_CREATED", "evidence": {"due_month": due, "need_tags": norm_tags or None}})

    elif rt == "REFUSE_HELP":
        dt = -float(rcfg.trust_refuse_help_penalty) * impact * neg_mult * sev_mult
        trust1 += dt
        tfr1 += float(rcfg.team_bump_refuse_help) * impact * (0.60 + 0.40 * amb) * sev_mult

        # Strong stars may move towards trade talk when team refuses help.
        if lev >= 0.70 and amb >= 0.70 and trust1 < 0.40:
            tr_level1 = max(tr_level1, 1)
            reasons.append({"code": "REFUSE_HELP_INCREASED_TRADE_PRESSURE", "evidence": {"trade_request_level": tr_level1}})

        reasons.append({"code": "REFUSED_HELP", "evidence": {"trust_delta": dt}})

    return trust1, mfr1, tfr1, tr_level1, promise, reasons


def _apply_trade_request(
    event_type: str,
    rt: str,
    payload: Mapping[str, Any],
    now_date_iso: str,
    *,
    trust0: float,
    mfr0: float,
    tfr0: float,
    tr_level0: int,
    lev: float,
    ego: float,
    amb: float,
    loy: float,
    coach: float,
    impact: float,
    pos_mult: float,
    neg_mult: float,
    sev_mult: float,
    rcfg: ResponseConfig,
    event: Mapping[str, Any],
) -> tuple[float, float, float, int, Optional[PromiseSpec], List[Dict[str, Any]]]:
    reasons: List[Dict[str, Any]] = []
    trust1, mfr1, tfr1 = trust0, mfr0, tfr0
    tr_level1 = max(tr_level0, 1)  # a trade request implies at least private level
    promise: Optional[PromiseSpec] = None

    if rt == "ACKNOWLEDGE":
        # Talking without committing: small trust gain, little/no frustration relief.
        dt = float(rcfg.trust_acknowledge) * impact * pos_mult * sev_mult * 0.55
        trust1 += dt
        reasons.append({"code": "ACKNOWLEDGED_TRADE_REQUEST", "evidence": {"trust_delta": dt}})

    elif rt == "SHOP_TRADE":
        dt = float(rcfg.trust_promise) * impact * pos_mult * sev_mult * 0.85
        trust1 += dt
        # Slight relief: being heard matters.
        tfr1 -= float(rcfg.team_relief_promise) * impact * sev_mult * 0.70
        mfr1 -= float(rcfg.minutes_relief_acknowledge) * impact * sev_mult * 0.35

        due_months = int(rcfg.promise_trade_due_months)
        # If already public, shorten the window slightly.
        if str(event_type).upper() == "TRADE_REQUEST_PUBLIC":
            due_months = max(1, due_months - 1)

        due = due_month_from_now(now_date_iso, due_months)
        promise = PromiseSpec(
            promise_type="SHOP_TRADE",
            due_month=due,
            target={"source": "trade_request", "public": str(event_type).upper() == "TRADE_REQUEST_PUBLIC"},
        )
        reasons.append({"code": "PROMISE_SHOP_TRADE_CREATED", "evidence": {"due_month": due, "trust_delta": dt}})

    elif rt == "PROMISE_COMPETE":
        dt = float(rcfg.trust_promise) * impact * pos_mult * sev_mult * 0.75
        trust1 += dt
        tfr1 -= float(rcfg.team_relief_promise) * impact * sev_mult

        due = due_month_from_now(now_date_iso, int(rcfg.promise_help_due_months))
        promise = PromiseSpec(
            promise_type="HELP",
            due_month=due,
            target={"source": "promise_compete"},
        )
        reasons.append({"code": "PROMISE_COMPETE_CREATED_HELP", "evidence": {"due_month": due, "trust_delta": dt}})

    elif rt == "REFUSE_TRADE":
        dt = -float(rcfg.trust_refuse_trade_penalty) * impact * neg_mult * sev_mult
        trust1 += dt
        tfr1 += float(rcfg.team_bump_refuse_trade) * impact * (0.55 + 0.45 * ego) * sev_mult
        mfr1 += float(rcfg.minutes_bump_refuse_trade) * impact * (0.55 + 0.45 * ego) * sev_mult

        # Escalate to public if high-ego/high-leverage and refusal feels like disrespect.
        if ego >= float(rcfg.refuse_trade_public_ego_threshold) and lev >= float(rcfg.refuse_trade_public_leverage_threshold):
            tr_level1 = max(tr_level1, 2)
            reasons.append({"code": "REFUSE_TRADE_ESCALATED_PUBLIC", "evidence": {"trade_request_level": tr_level1}})

        reasons.append({"code": "REFUSED_TRADE", "evidence": {"trust_delta": dt}})

    # Once public, never de-escalate automatically.
    if str(event_type).upper() == "TRADE_REQUEST_PUBLIC":
        tr_level1 = max(tr_level1, 2)

    return trust1, mfr1, tfr1, tr_level1, promise, reasons
