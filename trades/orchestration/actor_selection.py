from __future__ import annotations

import random
from datetime import date
from typing import Any, Dict, List, Optional, Set

from .types import ActorPlan, OrchestrationConfig
from . import policy
from .market_state import get_active_thread_team_ids, get_active_listing_team_ids


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _public_trade_request_counts_by_team(
    tick_ctx: Any,
    *,
    excluded_team_ids: Set[str],
) -> Dict[str, int]:
    """Best-effort map: team_id -> count of players with public trade requests(level>=2)."""
    out: Dict[str, int] = {}
    try:
        provider = getattr(tick_ctx, "provider", None)
        if provider is None:
            return out
        agency = getattr(provider, "agency_state_by_player", {})
        if not isinstance(agency, dict):
            return out
    except Exception:
        return out

    public_pids: List[str] = []
    for pid, st in agency.items():
        if not isinstance(st, dict):
            continue
        tr = int(_safe_float(st.get("trade_request_level"), 0.0))
        if tr >= 2:
            public_pids.append(str(pid))

    if not public_pids:
        return out

    team_map: Dict[str, str] = {}
    try:
        repo = getattr(provider, "repo", None)
        if repo is not None and hasattr(repo, "get_team_ids_by_players"):
            team_map = {
                str(k): str(v).upper()
                for k, v in dict(repo.get_team_ids_by_players(public_pids) or {}).items()
                if k
            }
    except Exception:
        team_map = {}

    for pid in public_pids:
        tid = str(team_map.get(pid) or "").upper()
        if not tid:
            try:
                snap = provider.get_player_snapshot(str(pid))
                tid = str(getattr(snap, "team_id", "") or "").upper()
            except Exception:
                tid = ""
        if not tid or (excluded_team_ids and tid in excluded_team_ids):
            continue
        out[tid] = int(out.get(tid, 0)) + 1
    return out


def select_trade_actors(
    tick_ctx,
    *,
    config: OrchestrationConfig,
    excluded_team_ids: Optional[Set[str]] = None,
    rng: Optional[random.Random] = None,
    trade_market: Optional[Dict[str, Any]] = None,
    today: Optional[date] = None,
    effective_pressure_by_team: Optional[Dict[str, float]] = None,
    pressure_tier_by_team: Optional[Dict[str, str]] = None,
) -> List[ActorPlan]:
    """
    오늘 딜 생성기를 호출할 팀 목록을 선정한다.
    입력: tick_ctx.team_situations(=TeamSituationEvaluator 결과)
    출력: ActorPlan 목록
    """
    rng = rng or random.Random()
    excluded = {str(x).upper() for x in (excluded_team_ids or set()) if x}
    eff_map = effective_pressure_by_team or {}
    tier_map = pressure_tier_by_team or {}

    # Threads boost: 접촉 중인 팀은 며칠간 더 자주 시장에 등장하도록 한다.
    active_thread_team_ids: Set[str] = set()
    active_listing_team_ids: Set[str] = set()
    public_request_counts_by_team = _public_trade_request_counts_by_team(
        tick_ctx,
        excluded_team_ids=excluded,
    )
    if trade_market is not None and today is not None:
        if bool(getattr(config, "enable_threads", True)):
            try:
                active_thread_team_ids = get_active_thread_team_ids(
                    trade_market,
                    today=today,
                    excluded_team_ids=excluded,
                )
            except Exception:
                active_thread_team_ids = set()
        if bool(getattr(config, "enable_trade_block", True)):
            try:
                active_listing_team_ids = {
                    t for t in get_active_listing_team_ids(trade_market, today=today) if t not in excluded
                }
            except Exception:
                active_listing_team_ids = set()

    try:
        tier_w_high = float(getattr(config, "pressure_tier_weight_multiplier_high", 1.15) or 1.0)
    except Exception:
        tier_w_high = 1.0
    try:
        tier_w_rush = float(getattr(config, "pressure_tier_weight_multiplier_rush", 1.35) or 1.0)
    except Exception:
        tier_w_rush = 1.0
    if tier_w_high <= 0:
        tier_w_high = 1.0
    if tier_w_rush <= 0:
        tier_w_rush = 1.0

    candidates: List[ActorPlan] = []
    for tid, ts in (getattr(tick_ctx, "team_situations", {}) or {}).items():
        team_id = str(tid).upper()
        if excluded and team_id in excluded:
            continue

        try:
            c = getattr(ts, "constraints", None)
            if bool(getattr(c, "cooldown_active", False)):
                continue
        except Exception:
            pass

        # Prefer breakdown-aware scoring (for telemetry/UI). Fall back to the simple scalar.
        score = 0.0
        activity_breakdown = None
        activity_tags = None
        try:
            bd_fn = getattr(policy, "team_activity_breakdown", None)
            if callable(bd_fn):
                activity_breakdown = bd_fn(ts)
                if isinstance(activity_breakdown, dict):
                    score = float(activity_breakdown.get("activity_score", 0.0) or 0.0)
                    tags = activity_breakdown.get("tags")
                    if isinstance(tags, (list, tuple)):
                        activity_tags = [str(x) for x in tags if x]
            else:
                score = float(policy.team_activity_score(ts))
        except Exception:
            score = float(policy.team_activity_score(ts))
            activity_breakdown = None
            activity_tags = None
        effective_pressure = None
        pressure_tier = None
        try:
            if team_id in eff_map:
                effective_pressure = float(eff_map.get(team_id, 0.0) or 0.0)
            else:
                ep_fn = getattr(policy, "effective_pressure_from_team_situation", None)
                if callable(ep_fn):
                    effective_pressure, _dbg = ep_fn(ts, config=config)
        except Exception:
            effective_pressure = None

        try:
            if team_id in tier_map:
                pressure_tier = str(tier_map.get(team_id) or "").upper() or None
            elif effective_pressure is not None:
                pt_fn = getattr(policy, "pressure_tier_from_effective_pressure", None)
                if callable(pt_fn):
                    pressure_tier = str(pt_fn(float(effective_pressure), config=config) or "").upper() or None
        except Exception:
            pressure_tier = None

        candidates.append(
            ActorPlan(
                team_id=team_id,
                activity_score=score,
                max_results=int(config.per_team_max_results),
                activity_breakdown=activity_breakdown,
                activity_tags=activity_tags,
                effective_pressure=effective_pressure,
                pressure_tier=pressure_tier,
            )
        )

    if not candidates:
        return []

    candidates.sort(key=lambda a: (a.activity_score, a.team_id), reverse=True)

    # (A-5) market day rhythm: 그 날의 "시장 분위기"를 고정된 stable RNG로 뽑는다.
    # NOTE: 여기서는 day_kind/meta는 사용하지 않지만, 상위 레이어(tick_loop)에서 기록하면 UX/디버깅에 유용하다.
    day_mult, _day_kind, _day_meta = policy.market_day_rhythm(tick_ctx, config=config)

    base_n = policy.compute_active_team_budget(tick_ctx, config=config)
    n = policy.apply_day_rhythm_to_budget(base_n, day_mult=day_mult, config=config)
    n = max(int(config.min_active_teams), min(int(config.max_active_teams), n))
    n = max(0, min(n, len(candidates)))
    if n == 0:
        return []

    pool = candidates[: max(n * 2, n)]

    # Expand pool with active-thread teams even if they are outside the top slice.
    if active_thread_team_ids:
        try:
            extra_n = int(getattr(config, "thread_extra_pool_size", 8) or 0)
        except Exception:
            extra_n = 0
        if extra_n > 0:
            pool_ids = {p.team_id for p in pool}
            extras = [c for c in candidates if c.team_id in active_thread_team_ids and c.team_id not in pool_ids]
            extras.sort(key=lambda a: (a.activity_score, a.team_id), reverse=True)
            pool.extend(extras[:extra_n])

    try:
        thread_mult = float(getattr(config, "thread_activity_weight_multiplier", 1.35) or 1.0)
    except Exception:
        thread_mult = 1.0
    if thread_mult <= 0:
        thread_mult = 1.0

    try:
        listing_mult = float(getattr(config, "trade_block_actor_weight_multiplier", 1.2) or 1.0)
    except Exception:
        listing_mult = 1.0
    if listing_mult <= 0:
        listing_mult = 1.0

    try:
        public_req_mult = float(getattr(config, "trade_request_public_actor_weight_multiplier", 1.15) or 1.0)
    except Exception:
        public_req_mult = 1.0
    if public_req_mult <= 0:
        public_req_mult = 1.0

    try:
        public_req_no_listing_mult = float(getattr(config, "trade_request_public_no_listing_weight_multiplier", 1.08) or 1.0)
    except Exception:
        public_req_no_listing_mult = 1.0
    if public_req_no_listing_mult <= 0:
        public_req_no_listing_mult = 1.0

    try:
        public_req_with_listing_mult = float(getattr(config, "trade_request_public_with_listing_weight_multiplier", 1.06) or 1.0)
    except Exception:
        public_req_with_listing_mult = 1.0
    if public_req_with_listing_mult <= 0:
        public_req_with_listing_mult = 1.0

    try:
        actor_weight_cap = float(getattr(config, "actor_weight_multiplier_cap", 3.0) or 3.0)
    except Exception:
        actor_weight_cap = 3.0
    if actor_weight_cap < 1.0:
        actor_weight_cap = 1.0

    def _weight(p: ActorPlan) -> float:
        w = max(0.001, float(p.activity_score or 0.0))
        tier = (getattr(p, "pressure_tier", None) or "").upper()
        if tier == "HIGH":
            w *= tier_w_high
        elif tier == "RUSH":
            w *= tier_w_rush
        if p.team_id in active_thread_team_ids:
            w *= thread_mult
        if p.team_id in active_listing_team_ids:
            w *= listing_mult

        public_req_count = int(public_request_counts_by_team.get(str(p.team_id).upper(), 0) or 0)
        if public_req_count > 0:
            # Count-aware mild scaling (1,2,3+ requests) without runaway effects.
            req_factor = min(1.0 + (0.20 * min(public_req_count - 1, 2)), 1.4)
            w *= (1.0 + (public_req_mult - 1.0) * req_factor)
            if p.team_id in active_listing_team_ids:
                w *= public_req_with_listing_mult
            else:
                w *= public_req_no_listing_mult

        base_w = max(0.001, float(p.activity_score or 0.0))
        max_w = base_w * actor_weight_cap
        if w > max_w:
            w = max_w
        return w

    picked: List[ActorPlan] = []

    # Minimum quota: if there are active threads, ensure at least one thread team can act.
    if active_thread_team_ids:
        try:
            thread_min = int(getattr(config, "thread_min_actors_per_tick", 1) or 0)
        except Exception:
            thread_min = 0
        if thread_min > 0:
            thread_pool = [p for p in pool if p.team_id in active_thread_team_ids]
            thread_min = min(thread_min, n, len(thread_pool))
            for _ in range(thread_min):
                if not thread_pool:
                    break
                choice = rng.choices(thread_pool, weights=[_weight(p) for p in thread_pool], k=1)[0]
                picked.append(choice)
                pool = [p for p in pool if p.team_id != choice.team_id]
                thread_pool = [p for p in thread_pool if p.team_id != choice.team_id]

    while pool and len(picked) < n:
        choice = rng.choices(pool, weights=[_weight(p) for p in pool], k=1)[0]
        picked.append(choice)
        pool = [p for p in pool if p.team_id != choice.team_id]

    picked.sort(key=lambda a: (a.activity_score, a.team_id), reverse=True)

    # (A-4) per-team max_results 동적 스케일링(+day_mult 반영)
    picked = policy.assign_dynamic_max_results(picked, tick_ctx=tick_ctx, config=config, day_mult=day_mult)

    # Apply small search-budget bonuses AFTER dynamic scaling (so it actually takes effect):
    # - active threads bonus (ongoing negotiations keep teams engaged)
    # - pressure tier bonus (deadline "rush" teams search deeper)
    try:
        thread_bonus = int(getattr(config, "thread_per_team_max_results_bonus", 2) or 0)
    except Exception:
        thread_bonus = 0
    try:
        tier_bonus_high = int(getattr(config, "pressure_tier_max_results_bonus_high", 1) or 0)
    except Exception:
        tier_bonus_high = 0
    try:
        tier_bonus_rush = int(getattr(config, "pressure_tier_max_results_bonus_rush", 2) or 0)
    except Exception:
        tier_bonus_rush = 0

    any_bonus = (thread_bonus > 0 and bool(active_thread_team_ids)) or (tier_bonus_high > 0) or (tier_bonus_rush > 0)
    if any_bonus:
        try:
            cap = int(getattr(config, "per_team_max_results_cap", 9) or 9)
        except Exception:
            cap = 9
        adjusted: List[ActorPlan] = []
        for a in picked:
            add = 0
            if active_thread_team_ids and a.team_id in active_thread_team_ids and thread_bonus > 0:
                add += int(thread_bonus)
            tier = (getattr(a, "pressure_tier", None) or "").upper()
            if tier == "HIGH" and tier_bonus_high > 0:
                add += int(tier_bonus_high)
            elif tier == "RUSH" and tier_bonus_rush > 0:
                add += int(tier_bonus_rush)

            if add > 0:
                new_max = int(getattr(a, "max_results", 0) or 0) + add
                if cap > 0:
                    new_max = min(new_max, cap)
                if new_max < 1:
                    new_max = 1
                adjusted.append(
                    ActorPlan(
                        team_id=a.team_id,
                        activity_score=a.activity_score,
                        max_results=new_max,
                        activity_breakdown=getattr(a, "activity_breakdown", None),
                        activity_tags=getattr(a, "activity_tags", None),
                        effective_pressure=getattr(a, "effective_pressure", None),
                        pressure_tier=getattr(a, "pressure_tier", None),
                    )
                )
            else:
                adjusted.append(a)
        picked = adjusted

    return picked
