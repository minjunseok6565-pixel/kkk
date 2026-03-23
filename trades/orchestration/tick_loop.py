from __future__ import annotations

import hashlib
import random
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Set

import state
from league_service import LeagueService

from ..generation.generation_tick import build_trade_generation_tick_context
from ..generation.deal_generator import DealGenerator  # type: ignore

from .types import OrchestrationConfig, TickReport, GeneratedBatch
from .market_state import (
    load_trade_market,
    save_trade_market,
    load_trade_memory,
    save_trade_memory,
    pre_tick_cleanup,
    prune_market_events,
    get_human_controlled_team_ids,
    set_human_controlled_team_ids,
    get_active_thread_team_ids,
)
from .actor_selection import select_trade_actors
from .promotion import promote_and_commit, compute_deal_key
from .telemetry import build_tick_summary_payload, emit_tick_summary_event
from .locks import trade_exec_serial_lock
from . import policy
from .listing_policy import apply_ai_proactive_listings
from ..trade_rules import parse_trade_deadline, is_trade_window_open, offseason_trade_reopen_date


def _allow_state_mutation(
    current_date_override: Optional[date],
    *,
    allow_backfill_state_mutation: bool = False,
) -> bool:
    if bool(allow_backfill_state_mutation):
        return True
    ssot = state.get_current_date_as_date()
    if current_date_override is None:
        return True
    return current_date_override == ssot


def _get_last_gm_tick_date() -> Optional[str]:
    snap = state.export_workflow_state() or {}
    league = snap.get("league") if isinstance(snap, dict) else None
    if isinstance(league, dict):
        v = league.get("last_gm_tick_date")
        return str(v) if v else None
    return None


def _stable_seed_int(*parts: str) -> int:
    raw = "|".join([str(p) for p in parts]).encode("utf-8")
    digest = hashlib.sha1(raw).digest()
    return int.from_bytes(digest[:8], "big", signed=False)

def run_trade_orchestration_tick(
    *,
    current_date: Optional[date] = None,
    db_path: Optional[str] = None,
    user_controlled_team_ids: Optional[Sequence[str]] = None,
    user_team_id: Optional[str] = None,
    config: Optional[OrchestrationConfig] = None,
    generator: Optional[DealGenerator] = None,
    validate_integrity: bool = False,
    dry_run: bool = False,
    allow_backfill_state_mutation: bool = False,
) -> TickReport:
    """
    Entry point for GM trade-orchestration ticks.

    Single-process safety: serialize tick execution with user trade commits
    (trades.apply.apply_deal_to_db) so that derived market/memory state does not
    suffer lost updates and tick-time constraints remain consistent.
    """
    cfg = config or OrchestrationConfig()

    # When disabled, return the normal report without taking the global serial lock.
    if not bool(cfg.enabled):
        return _run_trade_orchestration_tick_impl(
            current_date=current_date,
            db_path=db_path,
            user_controlled_team_ids=user_controlled_team_ids,
            user_team_id=user_team_id,
            config=cfg,
            generator=generator,
            validate_integrity=validate_integrity,
            dry_run=dry_run,
            allow_backfill_state_mutation=bool(allow_backfill_state_mutation),
        )

    allow_mut = _allow_state_mutation(
        current_date,
        allow_backfill_state_mutation=bool(allow_backfill_state_mutation),
    )
    if allow_mut and not dry_run:
        today = current_date or state.get_current_date_as_date()
        today_iso = today.isoformat()
        with trade_exec_serial_lock(reason=f"GM_TICK:{today_iso}"):
            return _run_trade_orchestration_tick_impl(
                current_date=current_date,
                db_path=db_path,
                user_controlled_team_ids=user_controlled_team_ids,
                user_team_id=user_team_id,
                config=cfg,
                generator=generator,
                validate_integrity=validate_integrity,
                dry_run=dry_run,
                allow_backfill_state_mutation=bool(allow_backfill_state_mutation),
            )

    return _run_trade_orchestration_tick_impl(
        current_date=current_date,
        db_path=db_path,
        user_controlled_team_ids=user_controlled_team_ids,
        user_team_id=user_team_id,
        config=cfg,
        generator=generator,
        validate_integrity=validate_integrity,
        dry_run=dry_run,
        allow_backfill_state_mutation=bool(allow_backfill_state_mutation),
    )


def _run_trade_orchestration_tick_impl(
    *,
    current_date: Optional[date] = None,
    db_path: Optional[str] = None,
    user_controlled_team_ids: Optional[Sequence[str]] = None,
    user_team_id: Optional[str] = None,
    config: Optional[OrchestrationConfig] = None,
    generator: Optional[DealGenerator] = None,
    validate_integrity: bool = False,
    dry_run: bool = False,
    allow_backfill_state_mutation: bool = False,
) -> TickReport:
    cfg = config or OrchestrationConfig()
    today = current_date or state.get_current_date_as_date()
    today_iso = today.isoformat()

    report = TickReport(tick_date=today_iso)
    report.meta["dry_run"] = bool(dry_run)

    if not bool(cfg.enabled):
        report.skipped = True
        report.skip_reason = "DISABLED"
        return report

    allow_mut = _allow_state_mutation(
        current_date,
        allow_backfill_state_mutation=bool(allow_backfill_state_mutation),
    )
    report.meta["allow_state_mutation"] = bool(allow_mut)
    report.meta["allow_backfill_state_mutation"] = bool(allow_backfill_state_mutation)

    if allow_mut and not dry_run:
        last = _get_last_gm_tick_date()
        if last and str(last)[:10] == today_iso[:10]:
            report.skipped = True
            report.skip_reason = "ALREADY_RAN_TODAY"
            return report

    trade_market = load_trade_market()
    trade_memory = load_trade_memory()

    # -------------------------------
    # Resolve human-controlled team ids (commercial safety; v2 parity)
    # -------------------------------
    # NOTE:
    # - Treat an explicitly provided empty list as "missing" (fail-closed should trigger).
    # - Combine user_controlled_team_ids and user_team_id rather than treating them as mutually exclusive.
    explicit_input_provided = (user_controlled_team_ids is not None) or bool(user_team_id)

    tmp_human_ids: Set[str] = set()
    if user_controlled_team_ids is not None:
        tmp_human_ids |= {str(x).upper() for x in user_controlled_team_ids if x}
    if user_team_id:
        tmp_human_ids.add(str(user_team_id).upper())

    # Empty set => treat as missing (None) for fail-closed semantics.
    explicit_human_ids: Optional[Set[str]] = tmp_human_ids if tmp_human_ids else None

    human_ids: Set[str] = set(explicit_human_ids or set())

    if bool(cfg.persist_human_team_ids_in_state):
        key = str(cfg.human_team_ids_state_key)
        if explicit_human_ids is not None:
            set_human_controlled_team_ids(trade_market, explicit_human_ids, state_key=key)
        else:
            human_ids = get_human_controlled_team_ids(trade_market, state_key=key)

    report.meta["human_controlled_team_ids"] = sorted(human_ids)
    report.meta["explicit_human_team_ids_input_provided"] = bool(explicit_input_provided)
    report.meta["explicit_human_team_ids_provided"] = bool(explicit_human_ids is not None)
    report.meta["explicit_human_team_ids_effective"] = sorted(tmp_human_ids)

    fail_closed = False
    if bool(cfg.fail_closed_if_human_ids_missing) and explicit_human_ids is None and not human_ids:
        fail_closed = True
        report.meta["fail_closed"] = True

    report.cleanup = pre_tick_cleanup(today=today, trade_market=trade_market, trade_memory=trade_memory, config=cfg)

    if allow_mut and not dry_run:
        save_trade_market(trade_market)
        save_trade_memory(trade_memory)

    if fail_closed:
        report.skipped = True
        report.skip_reason = "MISSING_HUMAN_TEAM_IDS"
        
        # IMPORTANT:
        # A fail-closed tick is NOT a successful run. Do not stamp last_tick_date / last_gm_tick_date,
        # otherwise a later retry on the same day (after fixing human ids) would be blocked.
        #
        # We still optionally record the failure date for operational visibility / spam control.
        if allow_mut and not dry_run:
            if trade_market.get("last_fail_closed_date") != today_iso:
                trade_market["last_fail_closed_date"] = today_iso
                trade_market["last_fail_closed_reason"] = "MISSING_HUMAN_TEAM_IDS"
                save_trade_market(trade_market)
                save_trade_memory(trade_memory)
        return report

    resolved_db_path = str(db_path or state.get_db_path())

    # -------------------------------
    # RNG seed: league fingerprint + tick nonce (옵션)
    # -------------------------------
    league_fp = ""
    if bool(getattr(cfg, "seed_with_league_fingerprint", True)):
        try:
            snap = state.get_league_context_snapshot() or {}
            league_fp = "|".join(
                [
                    str(resolved_db_path),
                    str(snap.get("season_year", "")),
                    str(snap.get("season_start", "")),
                ]
            )
        except Exception:
            league_fp = str(resolved_db_path)

    # tick_nonce는 성공/실패와 관계없이 "시도" 단위로 먼저 bump 해서,
    # 같은 날짜에 재시도/디버깅 시 결과가 완전히 고정되지 않도록 한다.
    tick_nonce = 0
    try:
        tick_nonce = int((trade_market.get("tick_nonce") or 0))  # type: ignore[arg-type]
    except Exception:
        tick_nonce = 0

    if allow_mut and not dry_run:
        tick_nonce += 1
        trade_market["tick_nonce"] = tick_nonce
        save_trade_market(trade_market)

    seed_parts = [str(cfg.seed_salt), str(today_iso)]
    if bool(getattr(cfg, "seed_with_league_fingerprint", True)):
        seed_parts.append(str(league_fp))
    if bool(getattr(cfg, "seed_with_tick_nonce", True)):
        seed_parts.append(str(tick_nonce))

    rng_seed = _stable_seed_int(*seed_parts)
    rng = random.Random(rng_seed)

    try:
        with build_trade_generation_tick_context(
            current_date=today,
            db_path=resolved_db_path,
            validate_integrity=bool(validate_integrity),
        ) as tick_ctx:
            # trade_deadline hard stop (SSOT: trades.trade_rules.is_trade_window_open)
            try:
                league = (tick_ctx.rule_tick_ctx.ctx_state_base or {}).get("league", {})
                tr = (league or {}).get("trade_rules", {}) if isinstance(league, dict) else {}
                deadline_raw = tr.get("trade_deadline")
                if deadline_raw:
                    try:
                        d = parse_trade_deadline(deadline_raw)
                    except ValueError:
                        report.skipped = True
                        report.skip_reason = "DEADLINE_PARSE_ERROR"
                        report.meta["trade_deadline_raw"] = repr(deadline_raw)
                        return report
                    if d is not None and not is_trade_window_open(current_date=today, trade_deadline=d):
                        reopen = offseason_trade_reopen_date(d)
                        report.skipped = True
                        report.skip_reason = "DEADLINE_PASSED"
                        report.meta["trade_deadline"] = d.isoformat()
                        report.meta["trade_reopens"] = reopen.isoformat()
                        return report
            except Exception:
                report.skipped = True
                report.skip_reason = "DEADLINE_PARSE_ERROR"
                return report

            # -------------------------------------------------
            # Team-specific effective pressure maps (SSOT)
            # -------------------------------------------------
            effective_pressure_by_team: Dict[str, float] = {}
            pressure_tier_by_team: Dict[str, str] = {}
            try:
                eff, tier, pm = policy.build_team_pressure_maps(tick_ctx, config=cfg)
                if isinstance(eff, dict):
                    effective_pressure_by_team = {str(k).upper(): float(v or 0.0) for k, v in eff.items()}
                if isinstance(tier, dict):
                    pressure_tier_by_team = {str(k).upper(): str(v or "").upper() for k, v in tier.items()}
                if isinstance(pm, dict):
                    # Keep it compact (no per-team maps in report)
                    if "avg_effective_pressure" in pm:
                        report.meta["avg_effective_pressure"] = float(pm.get("avg_effective_pressure") or 0.0)
                    if "pressure_tier_counts" in pm and isinstance(pm.get("pressure_tier_counts"), dict):
                        report.meta["pressure_tier_counts"] = dict(pm.get("pressure_tier_counts") or {})
            except Exception:
                pass

            # -------------------------------------------------
            # (3) AI-AI cap 동적 계산 (+ deadline rush meta)
            # -------------------------------------------------
            ai_ai_cap = 0
            rush_scalar = 0.0
            try:
                ai_ai_cap, cap_meta = policy.compute_ai_ai_commit_cap_v2(tick_ctx, config=cfg, rng=rng)
                if isinstance(cap_meta, dict):
                    if "avg_deadline_pressure" in cap_meta:
                        report.meta["avg_deadline_pressure"] = float(cap_meta.get("avg_deadline_pressure") or 0.0)
                    if "rush_scalar" in cap_meta:
                        rush_scalar = float(cap_meta.get("rush_scalar") or 0.0)
                        report.meta["rush_scalar"] = float(rush_scalar)
                    report.meta["ai_ai_cap_meta"] = cap_meta
            except Exception:
                ai_ai_cap = policy.compute_ai_ai_commit_cap(tick_ctx, config=cfg, rng=rng)
                try:
                    report.meta["avg_deadline_pressure"] = float(getattr(policy, "_avg_deadline_pressure")(tick_ctx))
                except Exception:
                    report.meta["avg_deadline_pressure"] = 0.0
                try:
                    rush_scalar = float(getattr(policy, "rush_scalar_from_avg_pressure")(report.meta["avg_deadline_pressure"], config=cfg))
                    report.meta["rush_scalar"] = float(rush_scalar)
                except Exception:
                    rush_scalar = 0.0

            report.ai_ai_trade_cap = int(ai_ai_cap)

            excluded = human_ids if bool(cfg.exclude_human_teams_from_initiators) else None
            try:
                actors = select_trade_actors(
                    tick_ctx,
                    config=cfg,
                    excluded_team_ids=excluded,
                    rng=rng,
                    trade_market=trade_market,
                    today=today,
                    effective_pressure_by_team=effective_pressure_by_team,
                    pressure_tier_by_team=pressure_tier_by_team,
                )
            except TypeError:
                # Backward compatibility: older actor_selection without pressure maps.
                actors = select_trade_actors(
                    tick_ctx,
                    config=cfg,
                    excluded_team_ids=excluded,
                    rng=rng,
                    trade_market=trade_market,
                    today=today,
                )
            report.active_teams = [a.team_id for a in actors]

            # Debug/balancing aid: which teams are currently in active negotiation threads (excluding human teams)
            try:
                ex = excluded if excluded is not None else set()
                report.meta["active_thread_team_ids"] = sorted(
                    get_active_thread_team_ids(trade_market, today=today, excluded_team_ids=ex)
                )
            except Exception:
                report.meta["active_thread_team_ids"] = []

            # Actor activity breakdown (small, selected teams only) for telemetry/UI.
            # This is intentionally compact to avoid ballooning GM_TICK_SUMMARY payloads.
            try:
                actor_activity = []
                for a in actors:
                    row = {
                        "team_id": str(getattr(a, "team_id", "")),
                        "activity_score": float(getattr(a, "activity_score", 0.0) or 0.0),
                    }
                    ep = getattr(a, "effective_pressure", None)
                    if ep is not None:
                        try:
                            row["effective_pressure"] = float(ep or 0.0)
                        except Exception:
                            pass
                    tier = getattr(a, "pressure_tier", None)
                    if tier:
                        row["pressure_tier"] = str(tier)
                    tags = getattr(a, "activity_tags", None)
                    if isinstance(tags, list) and tags:
                        row["tags"] = [str(x) for x in tags if x]

                    bd = getattr(a, "activity_breakdown", None)
                    if isinstance(bd, dict):
                        sig = bd.get("signals")
                        if isinstance(sig, dict) and sig:
                            # Snapshot only (already compact)
                            row["signals"] = dict(sig)

                        comps = bd.get("components")
                        if isinstance(comps, dict) and comps:
                            try:
                                b = comps.get("bubble") or {}
                                c_ = comps.get("contract") or {}
                                m = comps.get("momentum") or {}
                                row["components"] = {
                                    "bubble": float((b.get("value") or 0.0)),
                                    "contract": float((c_.get("value") or 0.0)),
                                    "momentum": float((m.get("value") or 0.0)),
                                }
                            except Exception:
                                pass

                        boost = bd.get("boost")
                        if isinstance(boost, dict):
                            try:
                                row["boost_applied"] = float((boost.get("applied") or 0.0))
                            except Exception:
                                pass

                    actor_activity.append(row)

                if actor_activity:
                    report.meta["actor_activity"] = actor_activity
            except Exception:
                pass

            gen = generator or DealGenerator(config=cfg.generator_config)

            all_props = []
            batches = []
            initiator_by_deal_id: Dict[str, str] = {}

            for a in actors:
                props = gen.generate_for_team(
                    a.team_id,
                    tick_ctx,
                    max_results=int(a.max_results),
                )
                stats = getattr(gen, "last_stats", None)
                batch = GeneratedBatch(initiator_team_id=a.team_id, proposals=list(props or []), stats=stats)
                batches.append(batch)
                all_props.extend(list(props or []))
                for p in (props or []):
                    try:
                        did = compute_deal_key(p)
                        initiator_by_deal_id[did] = str(a.team_id).upper()
                    except Exception:
                        continue

                if bool(getattr(cfg, "enable_trade_block", True)) and str(a.team_id).upper() not in human_ids:
                    try:
                        apply_ai_proactive_listings(
                            team_id=str(a.team_id).upper(),
                            tick_ctx=tick_ctx,
                            trade_market=trade_market,
                            today=today,
                            config=getattr(cfg, "generator_config", None),
                        )
                    except Exception:
                        pass

            report.batches = batches

            svc = LeagueService(tick_ctx.repo)
            promote_kwargs: Dict[str, Any] = dict(
                proposals=all_props,
                today=today,
                tick_ctx=tick_ctx,
                league_service=svc,
                config=cfg,
                human_team_ids=human_ids,
                initiator_by_deal_id=initiator_by_deal_id,
                trade_market=trade_market,
                trade_memory=trade_memory,
                allow_state_mutation=allow_mut,
                dry_run=dry_run,
                validate_integrity=bool(validate_integrity),
                ai_ai_trade_cap=int(ai_ai_cap),
            )
            # Forward-compatible: newer promotion may accept team pressure maps + rush_scalar.
            promote_kwargs2 = dict(promote_kwargs)
            promote_kwargs2["effective_pressure_by_team"] = effective_pressure_by_team
            promote_kwargs2["pressure_tier_by_team"] = pressure_tier_by_team
            promote_kwargs2["rush_scalar"] = float(rush_scalar)
            try:
                report.promotion = promote_and_commit(**promote_kwargs2)
            except TypeError:
                report.promotion = promote_and_commit(**promote_kwargs)

    except Exception as exc:
        report.skipped = True
        report.skip_reason = f"EXCEPTION:{type(exc).__name__}"
        report.meta["exception"] = type(exc).__name__
        return report

    summary_payload = build_tick_summary_payload(report, batches=report.batches, promotion=report.promotion)
    emit_tick_summary_event(trade_market, today_iso=today_iso, payload=summary_payload)

    prune_market_events(trade_market, max_kept=int(cfg.max_market_events_kept))

    if allow_mut and not dry_run:
        trade_market["last_tick_date"] = today_iso
        save_trade_market(trade_market)
        save_trade_memory(trade_memory)
        state.set_last_gm_tick_date(today_iso)

    return report
