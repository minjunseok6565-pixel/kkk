from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Set

import state
from league_service import LeagueService
from team_utils import ui_cache_refresh_players
from agency.service import apply_trade_offer_grievances

from ..identity import deal_execution_id, deal_identity_hash
from ..models import canonicalize_deal, serialize_deal
from ..agreements import create_committed_deal
from ..negotiation_store import (
    create_session,
    set_last_offer,
    set_committed,
    set_valid_until,
    set_market_context_offer_meta,
)
from ..valuation.types import DealVerdict

from .types import OrchestrationConfig, PromotionResult
from . import policy
from .market_state import (
    add_team_cooldown,
    bump_relationship,
    record_market_event,
    get_rel_meta_date_iso,
    is_private_leak_publicized,
    touch_thread,
    apply_trade_executed_effects,
)


def compute_deal_key(prop: Any) -> str:
    """Compute a stable deal identity key (SSOT).

    This key represents the transactional identity (teams + legs) of a deal and
    MUST ignore deal.meta so meta-only differences don't create duplicates.
    """
    deal_obj = getattr(prop, "deal", prop)
    return deal_identity_hash(deal_obj)


def _stable_deal_key(prop: Any) -> str:
    # Backward-compatible alias (internal callers). Prefer compute_deal_key().
    return compute_deal_key(prop)


def _extract_outgoing_player_ids_for_team(deal_payload: Dict[str, Any], *, from_team_id: str) -> List[str]:
    out: List[str] = []
    legs = deal_payload.get("legs") if isinstance(deal_payload, dict) else None
    if not isinstance(legs, list):
        return out
    src = str(from_team_id).upper()
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        if str(leg.get("from_team") or "").upper() != src:
            continue
        assets = leg.get("assets")
        if not isinstance(assets, list):
            continue
        for a in assets:
            if not isinstance(a, dict):
                continue
            if str(a.get("kind") or "").upper() != "PLAYER":
                continue
            pid = a.get("player_id")
            if pid:
                out.append(str(pid))
    uniq: List[str] = []
    seen: set[str] = set()
    for pid in out:
        if pid in seen:
            continue
        seen.add(pid)
        uniq.append(pid)
    return uniq


def _extract_incoming_player_ids_for_team(deal_payload: Dict[str, Any], *, to_team_id: str) -> List[str]:
    incoming: List[str] = []
    legs = deal_payload.get("legs") if isinstance(deal_payload, dict) else None
    if not isinstance(legs, list):
        return incoming
    dst = str(to_team_id).upper()
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        if str(leg.get("to_team") or "").upper() != dst:
            continue
        assets = leg.get("assets")
        if not isinstance(assets, list):
            continue
        for a in assets:
            if not isinstance(a, dict):
                continue
            if str(a.get("kind") or "").upper() != "PLAYER":
                continue
            pid = a.get("player_id")
            if pid:
                incoming.append(str(pid))
    uniq: List[str] = []
    seen: set[str] = set()
    for pid in incoming:
        if pid in seen:
            continue
        seen.add(pid)
        uniq.append(pid)
    return uniq


def _get_season_year_from_state(*, fallback_year: int) -> int:
    active = state.get_active_season_id()
    if isinstance(active, str) and active.strip():
        s0 = active.strip()
        try:
            return int(s0[:4])
        except Exception:
            pass
    return int(fallback_year)


def _clamp01(x: float) -> float:
    try:
        xf = float(x)
    except Exception:
        return 0.0
    if xf <= 0.0:
        return 0.0
    if xf >= 1.0:
        return 1.0
    return xf


def _stable_roll(*, seed: str, p: float) -> bool:
    """Deterministic Bernoulli(p) using SHA1(seed)."""
    pf = _clamp01(p)
    if pf <= 0.0:
        return False
    if pf >= 1.0:
        return True
    raw = str(seed).encode("utf-8")
    h = hashlib.sha1(raw).digest()
    x = int.from_bytes(h[:4], "big")
    u = x / float(2**32)
    return u < pf


def _team_deadline_pressure(tick_ctx: Any, team_id: str) -> float:
    try:
        ts = (getattr(tick_ctx, "team_situations", {}) or {}).get(str(team_id).upper())
        c = getattr(ts, "constraints", None)
        return float(getattr(c, "deadline_pressure", 0.0) or 0.0)
    except Exception:
        return 0.0


def _user_side_eval_decision(prop: Any, user_team_id: str) -> tuple[Any, Any]:
    u = str(user_team_id).upper()
    if str(getattr(prop, "buyer_id", "")).upper() == u:
        return getattr(prop, "buyer_eval", None), getattr(prop, "buyer_decision", None)
    return getattr(prop, "seller_eval", None), getattr(prop, "seller_decision", None)


def _iso_days_since(today: date, iso_date: str) -> Optional[int]:
    try:
        d0 = date.fromisoformat(str(iso_date)[:10])
        return int((today - d0).days)
    except Exception:
        return None


def _involves_user(prop: Any, user_team_id: Optional[str]) -> bool:
    if not user_team_id:
        return False
    u = str(user_team_id).upper()
    return str(prop.buyer_id).upper() == u or str(prop.seller_id).upper() == u


def _other_team(prop: Any, user_team_id: str) -> str:
    u = str(user_team_id).upper()
    if str(prop.buyer_id).upper() == u:
        return str(prop.seller_id).upper()
    return str(prop.buyer_id).upper()


def _ai_side_verdict(prop: Any, user_team_id: str) -> DealVerdict:
    u = str(user_team_id).upper()
    if str(prop.buyer_id).upper() == u:
        return prop.seller_decision.verdict
    return prop.buyer_decision.verdict


def _user_side_verdict(prop: Any, user_team_id: str) -> DealVerdict:
    u = str(user_team_id).upper()
    if str(prop.buyer_id).upper() == u:
        return prop.buyer_decision.verdict
    return prop.seller_decision.verdict


def _count_active_user_sessions(negotiations_snapshot: Dict[str, Any], user_team_id: str, *, today: date) -> int:
    u = str(user_team_id).upper()
    n = 0
    for sid, sess in (negotiations_snapshot or {}).items():
        try:
            if str(sess.get("status", "")).upper() != "ACTIVE":
                continue

            if str(sess.get("user_team_id", "")).upper() == u or str(sess.get("other_team_id", "")).upper() == u:
                n += 1
        except Exception:
            continue
    return n


def _find_reusable_session_id(
    negotiations_snapshot: Dict[str, Any],
    *,
    user_team_id: str,
    other_team_id: str,
    today: date,
) -> Optional[str]:
    u = str(user_team_id).upper()
    o = str(other_team_id).upper()
    for sid, sess in (negotiations_snapshot or {}).items():
        try:
            if str(sess.get("status", "")).upper() != "ACTIVE":
                continue

            if str(sess.get("user_team_id", "")).upper() == u and str(sess.get("other_team_id", "")).upper() == o:
                return str(sid)
        except Exception:
            continue
    return None


def _extract_moved_player_ids(transaction: Dict[str, Any]) -> List[str]:
    """Best-effort extraction of moved player ids from LeagueService.execute_trade() payload."""
    moved: List[str] = []
    for mv in (transaction.get("player_moves") or []):
        if isinstance(mv, dict):
            pid = mv.get("player_id")
            if pid:
                moved.append(str(pid))

    # stable dedupe (preserve order)
    out: List[str] = []
    seen: set[str] = set()
    for pid in moved:
        if pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return out


def promote_and_commit(
    proposals: Sequence[Any],
    *,
    today: date,
    tick_ctx: Any,
    league_service: LeagueService,
    config: OrchestrationConfig,
    human_team_ids: Set[str],
    initiator_by_deal_id: Optional[Dict[str, str]],
    trade_market: Dict[str, Any],
    trade_memory: Dict[str, Any],
    allow_state_mutation: bool,
    dry_run: bool,
    validate_integrity: bool,
    ai_ai_trade_cap: int,
    effective_pressure_by_team: Optional[Dict[str, float]] = None,
    pressure_tier_by_team: Optional[Dict[str, str]] = None,
    rush_scalar: Optional[float] = None,
) -> PromotionResult:
    """
    DealProposal들을 '오퍼/체결'로 승격.

    개선 반영:
    (2) 유저 REJECT 딜도 'PROBE/LOWBALL' 톤으로 제한적으로 오퍼 가능(스팸/모욕 방지).
    (3) AI-AI 체결 cap을 tick_ctx deadline_pressure 기반으로 동적으로 적용.
    (4) 커밋 전에 office_gate(score/margin 일관성 컷)로 극단/버그성 체결 방지.
    """
    result = PromotionResult()

    props = list(proposals or [])
    props.sort(key=lambda p: float(getattr(p, "score", 0.0) or 0.0), reverse=True)

    seen_deals: Set[str] = set()
    ai_ai_committed = 0
    team_trade_counts_today: Dict[str, int] = {}

    user_offered_by_user: Dict[str, int] = {}
    user_serious_sent_by_user: Dict[str, int] = {}
    user_probe_sent_by_user: Dict[str, int] = {}
    user_lowball_sent_by_user: Dict[str, int] = {}
    user_offered_other_teams_by_user: Dict[str, Set[str]] = {}

    human_ids: Set[str] = {str(x).upper() for x in (human_team_ids or set()) if x}

    eff_map: Dict[str, float] = {}
    if isinstance(effective_pressure_by_team, dict):
        try:
            eff_map = {str(k).upper(): float(v or 0.0) for k, v in effective_pressure_by_team.items()}
        except Exception:
            eff_map = {}
    # pressure_tier_by_team is accepted for forward-compat/telemetry, but not required in promotion.
    _ = pressure_tier_by_team

    rush_s = None
    try:
        rush_s = float(rush_scalar) if rush_scalar is not None else None
    except Exception:
        rush_s = None
    if rush_s is None:
        try:
            avg = float(getattr(policy, "_avg_deadline_pressure")(tick_ctx))  # type: ignore[attr-defined]
        except Exception:
            avg = 0.0
        try:
            rush_s = float(policy.rush_scalar_from_avg_pressure(avg, config=config))
        except Exception:
            rush_s = 0.0
    try:
        rush_s = float(rush_s or 0.0)
    except Exception:
        rush_s = 0.0

    def _effective_pressure(team_id: str) -> float:
        tid = str(team_id).upper()
        if tid in eff_map:
            try:
                return float(eff_map.get(tid, 0.0) or 0.0)
            except Exception:
                return 0.0
        try:
            ts = (getattr(tick_ctx, "team_situations", {}) or {}).get(tid)
            fn = getattr(policy, "effective_pressure_from_team_situation", None)
            if ts is not None and callable(fn):
                ep, _dbg = fn(ts, config=config)
                return float(ep or 0.0)
        except Exception:
            pass
        return float(_team_deadline_pressure(tick_ctx, tid) or 0.0)

    negotiations_snapshot: Dict[str, Any] = {}
    active_sessions_cache: Dict[str, int] = {}
    if human_ids and allow_state_mutation and not dry_run:
        negotiations_snapshot = state.negotiations_get() or {}
        if not isinstance(negotiations_snapshot, dict):
            negotiations_snapshot = {}

    # --- Market realism: RUMOR + threads (AI-AI only)
    rumor_pairs_emitted_today: Set[str] = set()

    def _pair_key(a: str, b: str) -> str:
        aa = str(a).upper()
        bb = str(b).upper()
        return f"{aa}|{bb}" if aa <= bb else f"{bb}|{aa}"

    def _emit_ai_ai_rumor(
        *,
        prop: Any,
        deal_id: str,
        buyer_id: str,
        seller_id: str,
        score: float,
        reason_code: str,
        rumor_kind: str,
        gate_ok: Optional[bool] = None,
    ) -> None:
        # Gameplay-facing market memory should only be created when we actually mutate state.
        if not allow_state_mutation or dry_run:
            return
        if not bool(getattr(config, "enable_market_rumors", True)):
            return
        try:
            if score < float(getattr(config, "rumor_min_score", 0.25)):
                return
        except Exception:
            return
        try:
            if int(getattr(config, "max_rumors_per_tick", 3)) <= int(result.rumors_created):
                return
        except Exception:
            return

        a = str(buyer_id).upper()
        b = str(seller_id).upper()
        if a == b:
            return

        pk = _pair_key(a, b)
        if pk in rumor_pairs_emitted_today:
            return

        # Pair cooldown: default 하루 1회. Symmetric read to tolerate legacy direction.
        try:
            cd_days = int(getattr(config, "rumor_pair_cooldown_days", 1) or 0)
        except Exception:
            cd_days = 1
        if cd_days > 0:
            last_iso = get_rel_meta_date_iso(trade_memory, team_a=a, team_b=b, key="last_rumor_at") or get_rel_meta_date_iso(trade_memory, team_a=b, team_b=a, key="last_rumor_at")
            if last_iso:
                ds = _iso_days_since(today, last_iso)
                if ds is not None and ds < max(0, cd_days):
                    return

        # Only rumorize deals that pass the office gate (quality guardrail for commercial UX).
        if gate_ok is None:
            ok_gate, _ = policy.office_gate_ok(prop, config=config)
        else:
            ok_gate = bool(gate_ok)
        if not ok_gate:
            return

        # Thread touch (optional)
        try:
            ttl_days = int(getattr(config, "thread_ttl_days", 5) or 5)
        except Exception:
            ttl_days = 5

        thread_key = pk
        thread_expires_on: Optional[str] = None
        threads_opened = False

        if bool(getattr(config, "enable_threads", True)):
            try:
                entry = touch_thread(
                    trade_market,
                    today=today,
                    team_a=a,
                    team_b=b,
                    deal_id=deal_id,
                    score=score,
                    reason_code=str(reason_code),
                    ttl_days=ttl_days,
                )
                threads_opened = bool(entry.pop("_created", False))
                # Ensure we don't persist transient keys (touch_thread returns a copy).
                thread_key = str(entry.get("pair_key") or pk)
                exp = entry.get("expires_on")
                thread_expires_on = str(exp) if isinstance(exp, str) else None
                result.threads_touched += 1
                if threads_opened:
                    result.threads_opened += 1
            except Exception:
                # Thread failure should never block the tick.
                pass
        else:
            # Still provide an expiry hint in the rumor payload for UI/telemetry.
            try:
                d = int(ttl_days)
            except Exception:
                d = 5
            if d <= 0:
                d = 1
            thread_expires_on = date.fromordinal(today.toordinal() + d + 1).isoformat()

        initiator = initiator_by_deal_id.get(deal_id) if initiator_by_deal_id else None
        initiator_team_id = str(initiator).upper() if initiator else a
        if initiator_team_id == a:
            initiator_role = "BUYER"
        elif initiator_team_id == b:
            initiator_role = "SELLER"
        else:
            initiator_role = "UNKNOWN"

        try:
            deal_payload = serialize_deal(canonicalize_deal(prop.deal))
        except Exception:
            deal_payload = {}

        record_market_event(
            trade_market,
            today=today,
            event_type="RUMOR_CREATED",
            payload={
                "deal_id": deal_id,
                "buyer_id": a,
                "seller_id": b,
                "score": float(score or 0.0),
                "reason_code": str(reason_code),
                "rumor_kind": str(rumor_kind),
                "initiator_team_id": initiator_team_id,
                "initiator_role": initiator_role,
                "thread_key": thread_key,
                "thread_expires_on": thread_expires_on,
                "deal": deal_payload,
            },
        )

        bump_relationship(
            trade_memory,
            team_a=a,
            team_b=b,
            today=today,
            patch={
                "counts": {"rumor_created": 1},
                "meta": {
                    "last_deal_id": deal_id,
                    "last_rumor_at": today.isoformat(),
                    "last_rumor_reason_code": str(reason_code),
                },
            },
        )

        rumor_pairs_emitted_today.add(pk)
        result.rumors_created += 1

    for prop in props:
        deal_key = compute_deal_key(prop)
        if deal_key in seen_deals:
            result.skipped += 1
            continue
        seen_deals.add(deal_key)

        buyer = str(prop.buyer_id).upper()
        seller = str(prop.seller_id).upper()
        buyer_h = buyer in human_ids
        seller_h = seller in human_ids

        if buyer_h and seller_h:
            result.skipped += 1
            record_market_event(
                trade_market,
                today=today,
                event_type="HUMAN_HUMAN_PROPOSAL_SUPPRESSED",
                payload={
                    "deal_id": deal_key,
                    "buyer_id": buyer,
                    "seller_id": seller,
                    "score": float(getattr(prop, "score", 0.0) or 0.0),
                },
            )
            continue

        if buyer_h or seller_h:
            user_tid = buyer if buyer_h else seller
            other = seller if buyer_h else buyer

            offered = int(user_offered_by_user.get(user_tid, 0))

            if user_tid not in active_sessions_cache:
                active_sessions_cache[user_tid] = _count_active_user_sessions(negotiations_snapshot, user_tid, today=today)

            try:
                uv = _user_side_verdict(prop, user_tid)
                av = _ai_side_verdict(prop, user_tid)
            except Exception:
                result.skipped += 1
                continue

            if av != DealVerdict.ACCEPT:
                result.skipped += 1
                continue

            user_eval, user_dec = _user_side_eval_decision(prop, user_tid)
            try:
                score = float(getattr(prop, "score", 0.0) or 0.0)
            except Exception:
                score = 0.0
            try:
                outgoing_total = float(getattr(user_eval, "outgoing_total", 0.0) or 0.0) if user_eval is not None else 0.0
            except Exception:
                outgoing_total = 0.0
            try:
                net_surplus = float(getattr(user_eval, "net_surplus", 0.0) or 0.0) if user_eval is not None else 0.0
            except Exception:
                net_surplus = 0.0
            try:
                overpay_allowed = float(getattr(user_dec, "overpay_allowed", 0.0) or 0.0) if user_dec is not None else 0.0
            except Exception:
                overpay_allowed = 0.0

            deadline_pressure = _team_deadline_pressure(tick_ctx, user_tid)
            tone = "SERIOUS"
            exceed_overpay = 0.0

            if uv == DealVerdict.REJECT:
                if bool(getattr(config, "disable_reject_offers_if_any_serious_sent", True)) and int(user_serious_sent_by_user.get(user_tid, 0)) > 0:
                    result.skipped += 1
                    record_market_event(trade_market, today=today, event_type="USER_OFFER_SUPPRESSED_REJECT_SERIOUS_ALREADY_SENT", payload={"deal_id": deal_key, "user_team_id": user_tid, "other_team_id": other})
                    continue

                try:
                    scale = max(outgoing_total, float(getattr(config, "user_offer_min_outgoing_scale", 6.0)))
                    probe_max = min(float(getattr(config, "probe_exceed_abs_cap", 1.2)), max(float(getattr(config, "probe_exceed_abs_min", 0.12)), float(getattr(config, "probe_exceed_scale_ratio", 0.02)) * scale))
                except Exception:
                    result.skipped += 1
                    continue

                exceed_overpay = (-overpay_allowed) - net_surplus
                if exceed_overpay <= 0.0:
                    result.skipped += 1
                    record_market_event(trade_market, today=today, event_type="USER_OFFER_SUPPRESSED_REJECT_INCONSISTENT", payload={"deal_id": deal_key, "user_team_id": user_tid, "other_team_id": other})
                    continue

                if exceed_overpay <= probe_max:
                    tone = "PROBE"
                else:
                    # 기존에는 lowball_max 초과 시 즉시 억제했으나,
                    # 실험 목적상 REJECT 제안은 상한 없이 LOWBALL로 취급한다.
                    tone = "LOWBALL"

                if tone == "PROBE":
                    meta_key = "last_user_offer_probe_at"
                    cooldown_days = int(getattr(config, "user_pair_probe_cooldown_days", 4))
                else:
                    meta_key = "last_user_offer_lowball_at"
                    cooldown_days = int(getattr(config, "user_pair_lowball_cooldown_days", 10))

                last_iso = get_rel_meta_date_iso(trade_memory, team_a=user_tid, team_b=other, key=meta_key)
                if last_iso:
                    ds = _iso_days_since(today, last_iso)
                    if ds is not None and ds < max(0, int(cooldown_days)):
                        result.skipped += 1
                        record_market_event(trade_market, today=today, event_type="USER_OFFER_SUPPRESSED_REJECT_PAIR_COOLDOWN", payload={"deal_id": deal_key, "user_team_id": user_tid, "other_team_id": other, "tone": tone, "last_at": last_iso, "cooldown_days": cooldown_days})
                        continue

            other_set = user_offered_other_teams_by_user.setdefault(user_tid, set())

            if allow_state_mutation and not dry_run:
                try:
                    sess_id = _find_reusable_session_id(negotiations_snapshot, user_team_id=user_tid, other_team_id=other, today=today)
                    if sess_id is not None and tone != "SERIOUS" and bool(getattr(config, "skip_reject_offer_if_active_session_exists", True)):
                        result.skipped += 1
                        record_market_event(trade_market, today=today, event_type="USER_OFFER_SUPPRESSED_REJECT_ACTIVE_SESSION_EXISTS", payload={"deal_id": deal_key, "session_id": sess_id, "user_team_id": user_tid, "other_team_id": other, "tone": tone})
                        continue

                    if sess_id is None:
                        sess = create_session(user_team_id=user_tid, other_team_id=other)
                        sess_id = str(sess["session_id"])
                        negotiations_snapshot[sess_id] = sess
                        active_sessions_cache[user_tid] = int(active_sessions_cache.get(user_tid, 0)) + 1

                    deal_payload = serialize_deal(canonicalize_deal(prop.deal))
                    set_last_offer(sess_id, deal_payload)

                    committed_deal_id = None
                    valid_until_iso = None
                    if bool(config.lock_user_offers):
                        try:
                            entry = create_committed_deal(canonicalize_deal(prop.deal), int(config.user_offer_valid_days), today, validate=True, db_path=str(getattr(tick_ctx, "db_path", None) or state.get_db_path()), integrity_check=bool(validate_integrity))
                            committed_deal_id = str(entry.get("deal_id"))
                            valid_until_iso = str(entry.get("expires_at")) if entry.get("expires_at") else None
                            if committed_deal_id:
                                set_committed(sess_id, committed_deal_id)
                            set_valid_until(sess_id, valid_until_iso)
                        except Exception as exc:
                            result.errors.append({"type": "USER_OFFER_LOCK_FAILED", "deal_id": deal_key, "exc": type(exc).__name__})

                    initiator = initiator_by_deal_id.get(deal_key) if initiator_by_deal_id else None
                    initiator_team_id = str(initiator).upper() if initiator else other
                    proposer_team_id = initiator_team_id if initiator_team_id != user_tid else other
                    recipient_team_id = user_tid
                    if initiator_team_id == buyer:
                        initiator_role = "BUYER"
                    elif initiator_team_id == seller:
                        initiator_role = "SELLER"
                    else:
                        initiator_role = "UNKNOWN"

                    # Offer privacy default: PRIVATE for AI-generated user offers.
                    offer_privacy = str(getattr(config, "default_offer_privacy", "PRIVATE") or "PRIVATE").upper()
                    if offer_privacy != "PUBLIC":
                        offer_privacy = "PRIVATE"

                    leak_status = "NONE"
                    leaked_player_ids: List[str] = []
                    if offer_privacy == "PRIVATE" and bool(getattr(config, "enable_private_offer_leaks", True)):
                        allow_leak = True
                        try:
                            pair_cd = int(getattr(config, "private_leak_pair_cooldown_days", 7) or 0)
                        except Exception:
                            pair_cd = 0
                        if pair_cd > 0:
                            last_iso = (
                                get_rel_meta_date_iso(trade_memory, team_a=recipient_team_id, team_b=proposer_team_id, key="last_private_offer_leak_at")
                                or get_rel_meta_date_iso(trade_memory, team_a=proposer_team_id, team_b=recipient_team_id, key="last_private_offer_leak_at")
                            )
                            if last_iso:
                                ds = _iso_days_since(today, last_iso)
                                if ds is not None and ds < max(0, pair_cd):
                                    allow_leak = False

                        if allow_leak:
                            try:
                                p_base = float(getattr(config, "ai_private_leak_base_prob", 0.08) or 0.0)
                            except Exception:
                                p_base = 0.08
                            try:
                                p_bonus = float(getattr(config, "ai_private_leak_pressure_bonus", 0.22) or 0.0)
                            except Exception:
                                p_bonus = 0.22
                            try:
                                p_cap = float(getattr(config, "ai_private_leak_prob_cap", 0.30) or 0.30)
                            except Exception:
                                p_cap = 0.30
                            p_leak = _clamp01(min(p_cap, max(0.0, p_base + p_bonus * float(deadline_pressure or 0.0))))
                            if _stable_roll(seed=f"LEAK|{today.isoformat()}|{deal_key}|{proposer_team_id}|{recipient_team_id}", p=p_leak):
                                leak_status = "LEAKED_BY_AI"
                        else:
                            record_market_event(
                                trade_market,
                                today=today,
                                event_type="PRIVATE_OFFER_LEAK_SUPPRESSED",
                                payload={
                                    "deal_id": deal_key,
                                    "session_id": sess_id,
                                    "user_team_id": recipient_team_id,
                                    "other_team_id": proposer_team_id,
                                    "reason": "PAIR_COOLDOWN",
                                },
                            )

                    offer_meta = {
                        "deal_id": deal_key, "tick_date": today.isoformat(), "score": score,
                        "offer_tone": tone,
                        "user_verdict": getattr(uv, "value", str(uv)),
                        "ai_verdict": getattr(av, "value", str(av)),
                        "user_net_surplus": float(net_surplus or 0.0),
                        "user_outgoing_total": float(outgoing_total or 0.0),
                        "user_overpay_allowed": float(overpay_allowed or 0.0),
                        "exceed_overpay": float(exceed_overpay or 0.0),
                        "deadline_pressure": float(deadline_pressure or 0.0),
                        "source": "AI_GM_ORCHESTRATION", "buyer_id": buyer, "seller_id": seller,
                        "proposer_team_id": proposer_team_id, "recipient_team_id": recipient_team_id,
                        "from_team_id": proposer_team_id, "to_team_id": recipient_team_id,
                        "initiator_team_id": initiator_team_id, "initiator_role": initiator_role,
                        "offer_privacy": offer_privacy, "leak_status": leak_status,
                        "leak_at": today.isoformat() if leak_status != "NONE" else None,
                        "private_offer_exposed_player_ids": leaked_player_ids,
                        "publicized_from_leak": False,
                    }
                    if leak_status == "LEAKED_BY_AI":
                        leaked_player_ids = _extract_outgoing_player_ids_for_team(deal_payload, from_team_id=proposer_team_id)
                        offer_meta["private_offer_exposed_player_ids"] = list(leaked_player_ids)

                    if leak_status == "LEAKED_BY_AI":
                        record_market_event(
                            trade_market,
                            today=today,
                            event_type="PRIVATE_OFFER_LEAKED",
                            payload={
                                "deal_id": deal_key,
                                "session_id": sess_id,
                                "leaked_by": "AI",
                                "user_team_id": recipient_team_id,
                                "other_team_id": proposer_team_id,
                                "player_ids": leaked_player_ids,
                            },
                        )

                        offer_meta["publicized_from_leak"] = bool(
                            is_private_leak_publicized(trade_market, session_id=str(sess_id), deal_id=str(deal_key))
                        )

                        if bool(getattr(config, "enable_ai_leak_grievance", True)):
                            try:
                                incoming_ids = _extract_incoming_player_ids_for_team(deal_payload, to_team_id=proposer_team_id)
                                db_path = str(getattr(tick_ctx, "db_path", None) or state.get_db_path())
                                apply_trade_offer_grievances(
                                    db_path=db_path,
                                    season_year=_get_season_year_from_state(fallback_year=today.year),
                                    now_date_iso=today.isoformat(),
                                    proposer_team_id=proposer_team_id,
                                    outgoing_player_ids=leaked_player_ids,
                                    incoming_player_ids=incoming_ids,
                                    trigger_source="PRIVATE_OFFER_LEAKED",
                                    session_id=str(sess_id),
                                    source_path="ORCHESTRATION_AI_PROMOTION",
                                )
                            except Exception as exc:
                                result.errors.append({
                                    "type": "AI_LEAK_GRIEVANCE_APPLY_FAILED",
                                    "deal_id": deal_key,
                                    "session_id": sess_id,
                                    "exc": type(exc).__name__,
                                })

                        bump_relationship(
                            trade_memory,
                            team_a=recipient_team_id,
                            team_b=proposer_team_id,
                            today=today,
                            patch={
                                "counts": {"private_offer_leaked_by_ai": 1},
                                "meta": {
                                    "last_private_offer_leak_at": today.isoformat(),
                                    "last_private_offer_leak_by": "AI",
                                    "last_private_offer_leak_session_id": str(sess_id),
                                },
                            },
                        )

                    set_market_context_offer_meta(sess_id, offer_meta)

                    add_team_cooldown(trade_market, team_id=other, today=today, days=int(config.cooldown_days_after_user_offer), reason="USER_OFFER_SENT", meta={"session_id": sess_id, "deal_id": deal_key})

                    record_market_event(
                        trade_market,
                        today=today,
                        event_type="USER_OFFER_SENT",
                        payload={
                            "deal_id": deal_key, "session_id": sess_id, "user_team_id": recipient_team_id,
                            "other_team_id": other, "buyer_id": buyer, "seller_id": seller,
                            "proposer_team_id": proposer_team_id, "recipient_team_id": recipient_team_id,
                            "from_team_id": proposer_team_id, "to_team_id": recipient_team_id,
                            "initiator_team_id": initiator_team_id, "initiator_role": initiator_role,
                            "committed_deal_id": committed_deal_id, "valid_until": valid_until_iso,
                            "score": score,
                            "offer_tone": tone,
                            "user_verdict": getattr(uv, "value", str(uv)),
                            "ai_verdict": getattr(av, "value", str(av)),
                            "user_net_surplus": float(net_surplus or 0.0),
                            "user_outgoing_total": float(outgoing_total or 0.0),
                            "user_overpay_allowed": float(overpay_allowed or 0.0),
                            "exceed_overpay": float(exceed_overpay or 0.0),
                            "deadline_pressure": float(deadline_pressure or 0.0),
                            "offer_privacy": offer_privacy,
                            "leak_status": leak_status,
                        },
                    )

                    counts_patch = {"user_offer_sent": 1}
                    meta_patch = {"last_deal_id": deal_key, "last_user_offer_any_at": today.isoformat(), "last_user_offer_tone": tone}
                    if tone == "PROBE":
                        counts_patch["user_offer_sent_probe"] = 1
                        meta_patch["last_user_offer_probe_at"] = today.isoformat()
                    elif tone == "LOWBALL":
                        counts_patch["user_offer_sent_lowball"] = 1
                        meta_patch["last_user_offer_lowball_at"] = today.isoformat()
                    else:
                        counts_patch["user_offer_sent_serious"] = 1
                        meta_patch["last_user_offer_serious_at"] = today.isoformat()

                    bump_relationship(trade_memory, team_a=recipient_team_id, team_b=other, today=today, patch={"counts": counts_patch, "meta": meta_patch})

                    result.user_offer_sessions.append({"session_id": sess_id, "other_team_id": other, "deal_id": deal_key, "offer_tone": tone, "committed_deal_id": committed_deal_id, "valid_until": valid_until_iso})

                    user_offered_by_user[user_tid] = offered + 1
                    if tone == "PROBE":
                        user_probe_sent_by_user[user_tid] = int(user_probe_sent_by_user.get(user_tid, 0)) + 1
                    elif tone == "LOWBALL":
                        user_lowball_sent_by_user[user_tid] = int(user_lowball_sent_by_user.get(user_tid, 0)) + 1
                    else:
                        user_serious_sent_by_user[user_tid] = int(user_serious_sent_by_user.get(user_tid, 0)) + 1
                    other_set.add(other)
                except Exception as exc:
                    result.errors.append({"type": "USER_OFFER_FAILED", "deal_id": deal_key, "exc": type(exc).__name__})
            else:
                user_offered_by_user[user_tid] = offered + 1
                if tone == "PROBE":
                    user_probe_sent_by_user[user_tid] = int(user_probe_sent_by_user.get(user_tid, 0)) + 1
                elif tone == "LOWBALL":
                    user_lowball_sent_by_user[user_tid] = int(user_lowball_sent_by_user.get(user_tid, 0)) + 1
                else:
                    user_serious_sent_by_user[user_tid] = int(user_serious_sent_by_user.get(user_tid, 0)) + 1
                result.user_offer_sessions.append({"session_id": None, "other_team_id": other, "deal_id": deal_key, "committed_deal_id": None, "valid_until": None, "offer_tone": tone})
                other_set.add(other)
            continue

        t1 = buyer
        t2 = seller
        try:
            score = float(getattr(prop, "score", 0.0) or 0.0)
        except Exception:
            score = 0.0

        if ai_ai_committed >= int(ai_ai_trade_cap):
            # Cap 때문에 못한 "좋은 딜"은 루머로 남겨서 시장이 살아있는 느낌을 만든다.
            if policy.is_ai_ai_auto_commit(prop):
                _emit_ai_ai_rumor(
                    prop=prop,
                    deal_id=deal_key,
                    buyer_id=buyer,
                    seller_id=seller,
                    score=score,
                    reason_code="PRIORITY_SHIFT",
                    rumor_kind="TIMING",
                )
            result.skipped += 1
            continue

        auto_commit = policy.is_ai_ai_auto_commit(prop)
        if not auto_commit:
            # COUNTER(거절 없이) 상태만 "협상 중"으로 루머화한다. (REJECT는 루머화 금지)
            try:
                bv = prop.buyer_decision.verdict
                sv = prop.seller_decision.verdict
            except Exception:
                bv = None
                sv = None
            if bv is not None and sv is not None:
                if DealVerdict.REJECT not in {bv, sv} and DealVerdict.COUNTER in {bv, sv}:
                    _emit_ai_ai_rumor(
                        prop=prop,
                        deal_id=deal_key,
                        buyer_id=buyer,
                        seller_id=seller,
                        score=score,
                        reason_code="NEGOTIATION_ONGOING",
                        rumor_kind="TALKS",
                    )
            result.skipped += 1
            continue

        ok, info = policy.office_gate_ok(prop, config=config)
        if not ok:
            result.vetoed += 1
            record_market_event(trade_market, today=today, event_type="TRADE_OFFICE_VETO", payload={"deal_id": deal_key, **info})
            continue
        if bool(config.prevent_multiple_trades_per_team_per_tick):
            c1 = int(team_trade_counts_today.get(t1, 0) or 0)
            c2 = int(team_trade_counts_today.get(t2, 0) or 0)
            if c1 > 0 or c2 > 0:
                p1 = _effective_pressure(t1)
                p2 = _effective_pressure(t2)

                allow1 = True
                allow2 = True
                meta1: Dict[str, Any] = {}
                meta2: Dict[str, Any] = {}
                try:
                    allow1, meta1 = policy.allow_second_trade_today(
                        tick_ctx=tick_ctx,
                        team_id=t1,
                        traded_count_today=c1,
                        team_pressure=p1,
                        rush_scalar=float(rush_s),
                        config=config,
                        human_team_ids=human_ids,
                    )
                except Exception:
                    allow1 = (c1 <= 0)
                try:
                    allow2, meta2 = policy.allow_second_trade_today(
                        tick_ctx=tick_ctx,
                        team_id=t2,
                        traded_count_today=c2,
                        team_pressure=p2,
                        rush_scalar=float(rush_s),
                        config=config,
                        human_team_ids=human_ids,
                    )
                except Exception:
                    allow2 = (c2 <= 0)

                if not (bool(allow1) and bool(allow2)):
                    _emit_ai_ai_rumor(
                        prop=prop,
                        deal_id=deal_key,
                        buyer_id=buyer,
                        seller_id=seller,
                        score=score,
                        reason_code="TIMING_CONFLICT",
                        rumor_kind="COOLED",
                        gate_ok=True,
                    )
                    # Optional: record why blocked (kept compact).
                    record_market_event(
                        trade_market,
                        today=today,
                        event_type="AI_AI_MULTI_TRADE_BLOCKED",
                        payload={
                            "deal_id": deal_key,
                            "buyer_id": t1,
                            "seller_id": t2,
                            "buyer_traded_count_today": c1,
                            "seller_traded_count_today": c2,
                            "buyer_pressure": float(p1 or 0.0),
                            "seller_pressure": float(p2 or 0.0),
                            "rush_scalar": float(rush_s or 0.0),
                            "buyer_allow_meta": meta1,
                            "seller_allow_meta": meta2,
                        },
                    )
                    result.skipped += 1
                    continue
        if allow_state_mutation and not dry_run:
            exec_deal_id = deal_key
            try:
                exec_deal_id = deal_execution_id(prop.deal, trade_date=today)
                ev = league_service.execute_trade(
                    canonicalize_deal(prop.deal),
                    source="AI_GM_ORCHESTRATION",
                    trade_date=today,
                    deal_id=exec_deal_id,
                )
            except Exception as exc:
                result.errors.append(
                    {
                        "type": "AI_AI_EXECUTE_FAILED",
                        "deal_id": deal_key,
                        "exec_deal_id": exec_deal_id,
                        "exc": type(exc).__name__,
                    }
                )
                continue

            result.executed_trade_events.append(ev)
            ai_ai_committed += 1
            team_trade_counts_today[t1] = int(team_trade_counts_today.get(t1, 0) or 0) + 1
            team_trade_counts_today[t2] = int(team_trade_counts_today.get(t2, 0) or 0) + 1

            # UI cache refresh (best-effort)
            if bool(getattr(config, "refresh_ui_cache_after_execute", True)):
                moved_ids = _extract_moved_player_ids(ev or {})
                if moved_ids:
                    try:
                        ui_cache_refresh_players(moved_ids)
                        result.ui_cache_refreshed_players += len(moved_ids)
                    except Exception as exc:
                        result.ui_cache_refresh_failures += 1
                        result.errors.append(
                            {
                                "type": "UI_CACHE_REFRESH_FAILED",
                                "deal_id": deal_key,
                                "exec_deal_id": exec_deal_id,
                                "num_players": len(moved_ids),
                                "exc": type(exc).__name__,
                            }
                        )

            # Project executed-trade effects into market/memory via SSOT projector.
            p1 = _effective_pressure(t1)
            p2 = _effective_pressure(t2)
            eff_map = {t1: float(p1 or 0.0), t2: float(p2 or 0.0)}
            try:
                apply_trade_executed_effects(
                    transaction=ev,
                    trade_market=trade_market,
                    trade_memory=trade_memory,
                    today=today,
                    config=config,
                    score=float(score or 0.0),
                    effective_pressure_by_team=eff_map,
                    rush_scalar=float(rush_s or 0.0),
                    buyer_id=t1,
                    seller_id=t2,
                )
            except Exception as exc:
                result.errors.append(
                    {
                        "type": "MARKET_SYNC_FAILED",
                        "deal_id": deal_key,
                        "exec_deal_id": exec_deal_id,
                        "exc": type(exc).__name__,
                    }
                )
        else:
            ai_ai_committed += 1
            team_trade_counts_today[t1] = int(team_trade_counts_today.get(t1, 0) or 0) + 1
            team_trade_counts_today[t2] = int(team_trade_counts_today.get(t2, 0) or 0) + 1

    return result
