from __future__ import annotations

from datetime import date
import math
import random
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..generation_tick import TradeGenerationTickContext
from ..asset_catalog import TradeAssetCatalog, IncomingPlayerRef, TeamOutgoingCatalog

from .types import DealGeneratorConfig, DealGeneratorBudget, TargetCandidate, SellAssetCandidate
from .config import _scale_buy_retrieval_limits
from .utils import _is_locked_candidate


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _clamp01(x: Any) -> float:
    xf = _safe_float(x, 0.0)
    if xf <= 0.0:
        return 0.0
    if xf >= 1.0:
        return 1.0
    return xf


def _clamp(x: Any, lo: float, hi: float) -> float:
    xf = _safe_float(x, lo)
    if xf <= lo:
        return float(lo)
    if xf >= hi:
        return float(hi)
    return float(xf)


def _parse_iso_ymd(value: Any) -> Optional[date]:
    if value is None:
        return None
    s = str(value).strip()
    if len(s) < 10:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def _active_public_listing_meta_by_player(
    tick_ctx: TradeGenerationTickContext,
) -> Dict[str, Dict[str, Any]]:
    """Best-effort: active PUBLIC listing metadata per player (league-wide)."""
    market = getattr(getattr(tick_ctx, "team_situation_ctx", None), "trade_market", None)
    if not isinstance(market, dict):
        try:
            from trades.orchestration.market_state import load_trade_market

            market = load_trade_market()
        except Exception:
            return {}
    listings = market.get("listings") if isinstance(market.get("listings"), dict) else {}
    today = getattr(tick_ctx, "current_date", None)
    out: Dict[str, Dict[str, Any]] = {}
    for pid, raw in (listings or {}).items():
        if not isinstance(raw, dict):
            continue
        if str(raw.get("status") or "").upper() != "ACTIVE":
            continue
        if str(raw.get("visibility") or "PUBLIC").upper() != "PUBLIC":
            continue
        exp = _parse_iso_ymd(raw.get("expires_on"))
        if isinstance(today, date) and exp is not None and today >= exp:
            continue
        player_id = str(raw.get("player_id") or pid or "")
        if not player_id:
            continue
        out[player_id] = {
            "priority": _clamp01(raw.get("priority")),
            "team_id": str(raw.get("team_id") or "").upper(),
            "updated_at": str(raw.get("updated_at") or raw.get("created_at") or ""),
        }
    return out


def _public_trade_request_level_by_player(tick_ctx: TradeGenerationTickContext) -> Dict[str, int]:
    out: Dict[str, int] = {}
    try:
        provider = getattr(tick_ctx, "provider", None)
        raw = getattr(provider, "agency_state_by_player", {}) if provider is not None else {}
        if not isinstance(raw, dict):
            return out
        for pid, st in raw.items():
            if not isinstance(st, dict):
                continue
            tr = int(_safe_float(st.get("trade_request_level"), 0.0))
            out[str(pid)] = tr
    except Exception:
        return {}
    return out


def _need_similarity_for_ref(ref: IncomingPlayerRef, need_map: Dict[str, float]) -> float:
    """Multi-tag need similarity: Σ(need[tag] * supply[tag]) / Σ need[tag]."""
    total_need = 0.0
    for w in need_map.values():
        total_need += max(0.0, float(w or 0.0))
    if total_need <= 0.0:
        return 0.0

    weighted = 0.0
    items = tuple(getattr(ref, "supply_items", tuple()) or tuple())
    if items:
        for tag, supply in items:
            t = str(tag or "")
            if not t:
                continue
            need_w = max(0.0, float(need_map.get(t, 0.0) or 0.0))
            if need_w <= 0.0:
                continue
            weighted += need_w * _clamp01(supply)
    else:
        # backward-compatible fallback (tests/stubs without supply profile)
        tag = str(getattr(ref, "tag", "") or "")
        need_w = max(0.0, float(need_map.get(tag, 0.0) or 0.0))
        weighted = need_w * _clamp01(getattr(ref, "tag_strength", 0.0))

    return _clamp01(weighted / total_need)


def _listing_interest_boost(
    *,
    config: DealGeneratorConfig,
    tick_ctx: TradeGenerationTickContext,
    listed: Dict[str, Any],
    need_similarity: float,
) -> float:
    try:
        base = float(getattr(config, "buy_target_listing_interest_boost_base", 0.25) or 0.0)
        pri_scale = float(getattr(config, "buy_target_listing_interest_priority_scale", 0.35) or 0.0)
        need_scale = float(getattr(config, "buy_target_listing_interest_need_weight_scale", 0.25) or 0.0)
        cap = float(getattr(config, "buy_target_listing_interest_cap", 0.85) or 0.0)
        half_life = float(getattr(config, "buy_target_listing_interest_recency_half_life_days", 7.0) or 0.0)
    except Exception:
        base, pri_scale, need_scale, cap, half_life = 0.25, 0.35, 0.25, 0.85, 7.0

    pr = _clamp01(listed.get("priority"))
    raw_boost = max(0.0, base + pri_scale * pr)
    need_factor = 1.0 + max(0.0, need_scale) * min(max(float(need_similarity), 0.0), 1.5)

    recency = 1.0
    if half_life > 0.0 and isinstance(getattr(tick_ctx, "current_date", None), date):
        d_upd = _parse_iso_ymd(listed.get("updated_at"))
        if d_upd is not None:
            age_days = max(0, (tick_ctx.current_date - d_upd).days)
            recency = math.pow(0.5, float(age_days) / float(half_life))

    interest_boost = raw_boost * need_factor * recency
    return min(max(0.0, interest_boost), max(0.0, cap))


def _contract_gap_score(gap_cap_share: float, config: DealGeneratorConfig) -> float:
    soft = max(0.005, float(getattr(config, "buy_target_contract_gap_softness_cap_share", 0.060) or 0.060))
    return float(math.tanh(float(gap_cap_share) / soft))


def _team_contract_sensitivity(team_situation: Any, config: DealGeneratorConfig) -> float:
    constraints = getattr(team_situation, "constraints", None)
    apron_status = str(getattr(constraints, "apron_status", "OVER_CAP") or "OVER_CAP").upper()
    posture = str(getattr(team_situation, "trade_posture", "STAND_PAT") or "STAND_PAT").upper()
    deadline_pressure = _clamp01(getattr(constraints, "deadline_pressure", 0.0))

    apron_mult = {
        "BELOW_CAP": float(getattr(config, "buy_target_contract_apron_mult_below_cap", 0.55) or 0.55),
        "OVER_CAP": float(getattr(config, "buy_target_contract_apron_mult_over_cap", 0.90) or 0.90),
        "ABOVE_1ST_APRON": float(getattr(config, "buy_target_contract_apron_mult_above_1st", 1.25) or 1.25),
        "ABOVE_2ND_APRON": float(getattr(config, "buy_target_contract_apron_mult_above_2nd", 1.70) or 1.70),
    }.get(apron_status, float(getattr(config, "buy_target_contract_apron_mult_over_cap", 0.90) or 0.90))

    posture_mult = {
        "AGGRESSIVE_BUY": float(getattr(config, "buy_target_contract_posture_mult_aggressive_buy", 1.10) or 1.10),
        "SOFT_BUY": float(getattr(config, "buy_target_contract_posture_mult_soft_buy", 1.00) or 1.00),
        "STAND_PAT": float(getattr(config, "buy_target_contract_posture_mult_stand_pat", 0.90) or 0.90),
        "SOFT_SELL": float(getattr(config, "buy_target_contract_posture_mult_soft_sell", 0.70) or 0.70),
        "SELL": float(getattr(config, "buy_target_contract_posture_mult_sell", 0.60) or 0.60),
    }.get(posture, float(getattr(config, "buy_target_contract_posture_mult_stand_pat", 0.90) or 0.90))

    d_min = float(getattr(config, "buy_target_contract_deadline_mult_min", 0.90) or 0.90)
    d_max = float(getattr(config, "buy_target_contract_deadline_mult_max", 1.15) or 1.15)
    deadline_mult = d_min + (d_max - d_min) * deadline_pressure

    sens = float(apron_mult) * float(posture_mult) * float(deadline_mult)
    s_min = float(getattr(config, "buy_target_contract_team_sensitivity_min", 0.35) or 0.35)
    s_max = float(getattr(config, "buy_target_contract_team_sensitivity_max", 2.20) or 2.20)
    return _clamp(sens, s_min, s_max)


def _player_core_score(ref: IncomingPlayerRef, need_similarity: float, config: DealGeneratorConfig) -> float:
    fit = _clamp01(getattr(ref, "tag_strength", 0.0))
    basketball_total = float(getattr(ref, "basketball_total", 0.0) or 0.0)
    basketball_norm = _clamp01((basketball_total + 15.0) / 45.0)

    w_fit = max(0.0, float(getattr(config, "buy_target_player_core_weight_fit", 0.50) or 0.50))
    w_market = max(0.0, float(getattr(config, "buy_target_player_core_weight_market", 0.35) or 0.35))
    w_need = max(0.0, float(getattr(config, "buy_target_player_core_weight_need", 0.35) or 0.35))

    need_term = max(
        float(getattr(config, "buy_target_need_mismatch_floor", -0.2) or -0.2),
        w_need * (_clamp01(need_similarity) - 0.5),
    )

    return float((w_fit * fit) + (w_market * basketball_norm) + need_term)


def _cheap_pre_score(ref: IncomingPlayerRef, need_similarity: float, config: DealGeneratorConfig) -> float:
    basketball_total = float(getattr(ref, "basketball_total", 0.0) or 0.0)
    basketball_norm = _clamp01((basketball_total + 15.0) / 45.0)
    core_pre = 0.62 * basketball_norm + 0.38 * _clamp01(need_similarity)

    gap = float(getattr(ref, "contract_gap_cap_share", 0.0) or 0.0)
    contract_pre_w = float(getattr(config, "buy_target_pre_score_contract_weight", 0.18) or 0.18)
    contract_pre = contract_pre_w * _contract_gap_score(gap, config)
    return float(core_pre + contract_pre)


def _final_rank(
    *,
    ref: IncomingPlayerRef,
    need_similarity: float,
    listing_boost: float,
    buyer_ts: Any,
    config: DealGeneratorConfig,
    rng: random.Random,
) -> float:
    player_core = _player_core_score(ref, need_similarity, config)

    contract_gap = float(getattr(ref, "contract_gap_cap_share", 0.0) or 0.0)
    contract_score = _contract_gap_score(contract_gap, config)
    team_sens = _team_contract_sensitivity(buyer_ts, config)
    contract_base_w = float(getattr(config, "buy_target_contract_base_weight", 0.30) or 0.30)
    contract_term = contract_base_w * contract_score * team_sens

    # Step-4 wiring: reuse catalog-exposed market/contract proxies when available.
    market_pct = _clamp01(float(getattr(ref, "market_percentile_league", 0.5) or 0.5))
    market_pct_term = 0.08 * (market_pct - 0.5)

    contract_proxy_match = _clamp01(float(getattr(ref, "contract_proxy_matching", 0.0) or 0.0))
    contract_proxy_toxic = _clamp01(float(getattr(ref, "contract_proxy_toxic", 0.0) or 0.0))
    contract_proxy_term = 0.06 * (contract_proxy_match - contract_proxy_toxic)

    rank = player_core + contract_term + market_pct_term + contract_proxy_term + max(0.0, float(listing_boost or 0.0))
    rank += rng.random() * 0.01
    return float(rank)


def _merge_tier_candidates(
    *,
    listed_rows: List[Dict[str, Any]],
    tier1_rows: List[Dict[str, Any]],
    tier2_rows: List[Dict[str, Any]],
    max_targets: int,
    listed_min_quota: int,
    non_listed_quota: int,
    listed_max_share: float,
) -> List[Dict[str, Any]]:
    if max_targets <= 0:
        return []

    listed_cap = int(max(0, min(max_targets, math.floor(max_targets * max(0.0, min(1.0, listed_max_share))))))
    listed_cap = max(listed_cap, min(max_targets, max(0, int(listed_min_quota))))

    selected: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    listed_count = 0

    def _add(row: Dict[str, Any]) -> bool:
        nonlocal listed_count
        pid = str(row.get("player_id") or "")
        if not pid or pid in seen:
            return False
        if len(selected) >= max_targets:
            return False
        is_listed = bool(row.get("is_listed", False))
        if is_listed and listed_count >= listed_cap:
            return False
        seen.add(pid)
        selected.append(row)
        if is_listed:
            listed_count += 1
        return True

    for row in listed_rows:
        if listed_count >= listed_min_quota:
            break
        if len(selected) >= max_targets:
            break
        _add(row)

    non_rows = tier1_rows + tier2_rows
    non_added = 0
    for row in non_rows:
        if non_added >= non_listed_quota:
            break
        if len(selected) >= max_targets:
            break
        if _add(row):
            non_added += 1

    remaining = sorted((r for r in (listed_rows + non_rows) if str(r.get("player_id") or "") not in seen), key=lambda r: float(r.get("pre_score", 0.0)), reverse=True)
    for row in remaining:
        if len(selected) >= max_targets:
            break
        _add(row)

    return selected




# =============================================================================
# Target selection
# =============================================================================


def select_targets_buy(
    buyer_id: str,
    tick_ctx: TradeGenerationTickContext,
    catalog: TradeAssetCatalog,
    config: DealGeneratorConfig,
    *,
    budget: DealGeneratorBudget,
    rng: random.Random,
    banned_players: Set[str],
) -> List[TargetCandidate]:
    """BUY 타깃 후보 구성(Tier0/1/2 retrieval + quota merge + final ranking)."""

    dc = tick_ctx.get_decision_context(buyer_id)
    need_map = dict(getattr(dc, "need_map", {}) or {})

    if not need_map:
        ts = tick_ctx.get_team_situation(buyer_id)
        for n in getattr(ts, "needs", []) or []:
            try:
                need_map[str(getattr(n, "tag", ""))] = float(getattr(n, "weight", 0.0) or 0.0)
            except Exception:
                continue

    buyer_ts = tick_ctx.get_team_situation(buyer_id)
    lim = _scale_buy_retrieval_limits(config, buyer_ts)

    max_targets = max(0, int(getattr(budget, "max_targets", 0) or 0))
    if max_targets <= 0:
        return []

    listed_min_quota = max(0, int(lim.get("listed_min_quota", 0) or 0))
    non_listed_quota = max(0, int(lim.get("non_listed_quota", 0) or 0))
    listed_max_share = _clamp01(lim.get("listed_max_share", 0.75))

    teams_cap = max(1, int(lim.get("teams_cap", 1) or 1))
    players_cap = max(1, int(lim.get("players_cap", 1) or 1))
    tier2_enabled = bool((lim.get("tier2_enabled", 0.0) or 0.0) >= 0.5)
    tier2_budget_share = _clamp01(lim.get("tier2_budget_share", 0.0))
    iteration_cap = max(1, int(lim.get("retrieval_iteration_cap", 1) or 1))

    buyer_u = str(buyer_id).upper()
    seller_out_cache: Dict[str, Optional[TeamOutgoingCatalog]] = {}
    seller_cooldown_cache: Dict[str, bool] = {}

    listing_meta_by_player: Dict[str, Dict[str, Any]] = {}
    if bool(getattr(config, "buy_target_listing_interest_enabled", True)):
        listing_meta_by_player = _active_public_listing_meta_by_player(tick_ctx)

    refs: Sequence[IncomingPlayerRef] = tuple(getattr(catalog, "incoming_all_players", tuple()) or tuple())

    listed_rows: List[Dict[str, Any]] = []
    non_listed_rows_all: List[Dict[str, Any]] = []

    # Team scan cap for non-listed path: keep highest potential teams only.
    non_listed_team_best: Dict[str, float] = {}

    for r in refs:
        from_team = str(r.from_team).upper()
        if from_team == buyer_u:
            continue
        if r.player_id in banned_players:
            continue

        seller_out = seller_out_cache.get(from_team)
        if seller_out is None and from_team not in seller_out_cache:
            seller_out = catalog.outgoing_by_team.get(from_team)
            seller_out_cache[from_team] = seller_out
        if seller_out is None:
            continue

        cd = seller_cooldown_cache.get(from_team)
        if cd is None:
            ts_seller = tick_ctx.get_team_situation(from_team)
            cd = bool(getattr(ts_seller, "constraints", None) and ts_seller.constraints.cooldown_active)
            seller_cooldown_cache[from_team] = cd
        if cd:
            continue

        listed = listing_meta_by_player.get(str(r.player_id), {}) if listing_meta_by_player else {}
        is_public_listing = bool(listed) and str(listed.get("team_id") or "").upper() == from_team

        need_similarity = _need_similarity_for_ref(r, need_map)
        listing_boost = _listing_interest_boost(
            config=config,
            tick_ctx=tick_ctx,
            listed=listed,
            need_similarity=need_similarity,
        ) if is_public_listing else 0.0

        row = {
            "player_id": str(r.player_id),
            "ref": r,
            "from_team": from_team,
            "is_listed": is_public_listing,
            "listed_meta": listed,
            "need_similarity": need_similarity,
            "listing_boost": listing_boost,
            "pre_score": _cheap_pre_score(r, need_similarity, config),
        }

        if is_public_listing:
            listed_rows.append(row)
        else:
            non_listed_rows_all.append(row)
            prev = non_listed_team_best.get(from_team)
            if prev is None or row["pre_score"] > prev:
                non_listed_team_best[from_team] = float(row["pre_score"])

    listed_rows.sort(key=lambda row: (float(row.get("listing_boost", 0.0)), float(row.get("pre_score", 0.0)), float(getattr(row.get("ref"), "market_total", 0.0))), reverse=True)

    # Non-listed team cap selection
    allowed_non_listed_teams: Set[str] = set()
    ranked_teams = sorted(non_listed_team_best.items(), key=lambda kv: (-kv[1], kv[0]))
    for team_id, _ in ranked_teams[:teams_cap]:
        allowed_non_listed_teams.add(str(team_id).upper())

    non_listed_filtered = [row for row in non_listed_rows_all if str(row.get("from_team") or "").upper() in allowed_non_listed_teams]
    non_listed_filtered.sort(key=lambda row: float(row.get("pre_score", 0.0)), reverse=True)

    # Player scan cap + iteration cap for non-listed retrieval
    scan_cap = min(players_cap, iteration_cap, len(non_listed_filtered))
    scanned_non_listed = non_listed_filtered[:scan_cap]

    # Tier1: base non-listed seed
    tier1_n = min(len(scanned_non_listed), max(0, non_listed_quota))
    tier1_rows = scanned_non_listed[:tier1_n]

    # Tier2: expanded non-listed when pressure/budget allow
    tier2_rows: List[Dict[str, Any]] = []
    if tier2_enabled and len(scanned_non_listed) > tier1_n:
        residual = scanned_non_listed[tier1_n:]
        tier2_limit = max(0, int(round(max_targets * tier2_budget_share)))
        if tier2_limit > 0:
            tier2_rows = residual[:tier2_limit]

    merged = _merge_tier_candidates(
        listed_rows=listed_rows,
        tier1_rows=tier1_rows,
        tier2_rows=tier2_rows,
        max_targets=max_targets,
        listed_min_quota=listed_min_quota,
        non_listed_quota=non_listed_quota,
        listed_max_share=listed_max_share,
    )

    out: List[TargetCandidate] = []
    for row in merged:
        ref = row.get("ref")
        if not isinstance(ref, IncomingPlayerRef):
            continue
        rank = _final_rank(
            ref=ref,
            need_similarity=float(row.get("need_similarity", 0.0) or 0.0),
            listing_boost=float(row.get("listing_boost", 0.0) or 0.0),
            buyer_ts=buyer_ts,
            config=config,
            rng=rng,
        )
        out.append(
            TargetCandidate(
                player_id=ref.player_id,
                from_team=str(ref.from_team).upper(),
                need_tag=str(ref.tag or ""),
                tag_strength=float(rank),
                market_total=float(ref.market_total),
                salary_m=float(ref.salary_m),
                remaining_years=float(ref.remaining_years),
                age=ref.age,
            )
        )

    out.sort(key=lambda t: (-t.tag_strength, -t.market_total, t.salary_m, t.player_id))
    return out[:max_targets]


def select_targets_sell(
    seller_id: str,
    tick_ctx: TradeGenerationTickContext,
    catalog: TradeAssetCatalog,
    config: DealGeneratorConfig,
    *,
    budget: DealGeneratorBudget,
    rng: random.Random,
    banned_players: Set[str],
    allow_locked_by_deal_id: Optional[str] = None,
) -> List[SellAssetCandidate]:
    """SELL 모드: initiator가 내놓을 매물(선수) 후보를 고른다.

    v2 정합 로직:
    - locked(allow_locked 예외 포함) 선필터
    - recent_signing_banned_until 선필터
    - 정렬: bucket priority -> public request signal(desc) -> raw_trade_block_score(desc), timing_liquidity(desc), contract_pressure(desc)
      -> market_total(asc) -> player_id
    - 상위 head만 소폭 셔플해 매번 같은 쇼핑리스트가 되지 않게 한다
    """

    seller_u = str(seller_id).upper()
    out_cat = catalog.outgoing_by_team.get(seller_u)
    if out_cat is None:
        return []

    max_targets = int(getattr(budget, "max_targets", 0) or 0)
    if max_targets <= 0:
        return []

    allow_id = str(allow_locked_by_deal_id or "").strip() or None

    # v2와 동일한 우선순위(숫자 낮을수록 우선)
    bucket_pri: Dict[str, int] = {
        "VETERAN_SALE": 0,
        "SURPLUS_EXPENDABLE": 1,
        "FILLER_CHEAP": 2,
        "FILLER_BAD_CONTRACT": 3,
        "CONSOLIDATE": 4,
    }

    rows: List[Tuple[Tuple[Any, ...], SellAssetCandidate]] = []

    trade_request_level_by_player = _public_trade_request_level_by_player(tick_ctx)

    for pid, c in (out_cat.players or {}).items():
        if not pid:
            continue
        if pid in banned_players:
            continue

        # (1) locked 선필터 (allow_locked 예외 포함)
        if _is_locked_candidate(getattr(c, "lock", None), allow_locked_by_deal_id=allow_id):
            continue

        # (2) recent signing ban 선필터
        if _is_ban_active(tick_ctx.current_date, getattr(c, "recent_signing_banned_until", None)):
            continue

        buckets = tuple(getattr(c, "buckets", None) or ())

        # (3) 정렬 키 구성 (v2와 동일)
        if buckets:
            pri = min(bucket_pri.get(b, 50) for b in buckets)
        else:
            pri = bucket_pri.get("FILLER_CHEAP", 4)

        raw_trade_block_score = getattr(c, "raw_trade_block_score", None)
        trade_block_score = getattr(c, "trade_block_score", None)
        priority_score = float(
            raw_trade_block_score
            if raw_trade_block_score is not None
            else (trade_block_score if trade_block_score is not None else 0.0)
        )
        exp = 1.0 if bool(getattr(c, "is_expiring", False)) else 0.0
        timing_liquidity = float(getattr(c, "timing_liquidity", exp) or exp)
        contract_pressure = float(getattr(c, "contract_pressure", 0.0) or 0.0)
        value = float(getattr(getattr(c, "market", None), "total", 0.0) or 0.0)

        sale_cand = SellAssetCandidate(
            player_id=str(pid),
            market_total=float(value),
            salary_m=float(getattr(c, "salary_m", 0.0) or 0.0),
            remaining_years=float(getattr(c, "remaining_years", 0.0) or 0.0),
            is_expiring=bool(getattr(c, "is_expiring", False)),
            top_tags=tuple(getattr(c, "top_tags", None) or ()),
        )

        tr_level = int(trade_request_level_by_player.get(str(pid), 0) or 0)
        is_public_request = tr_level >= 2

        signal_boost = 0.0
        if is_public_request:
            signal_boost += float(config.public_request_priority_boost)
        signal_boost = min(max(0.0, signal_boost), max(0.0, float(config.public_request_priority_boost_cap)))

        sort_key = (pri, -signal_boost, -priority_score, -timing_liquidity, -contract_pressure, value, str(pid))
        rows.append((sort_key, sale_cand))

    if not rows:
        return []

    rows.sort(key=lambda x: x[0])
    sale: List[SellAssetCandidate] = [cand for _, cand in rows]

    # v2 스타일: head만 셔플(과도한 순위 붕괴 방지)
    head_n = max(6, min(len(sale), max_targets))
    head = sale[:head_n]
    rng.shuffle(head)
    sale = head + sale[head_n:]

    return sale[:max_targets]


def select_buyers_for_sale_asset(
    seller_id: str,
    sale_asset: SellAssetCandidate,
    tick_ctx: TradeGenerationTickContext,
    catalog: TradeAssetCatalog,
    *,
    config: DealGeneratorConfig,
    budget: DealGeneratorBudget,
    rng: random.Random,
) -> List[Tuple[str, str]]:
    """SELL 모드: 특정 매물에 대해 관심 가질 가능성이 큰 buyer 팀을 고른다.

    Returns: list[(buyer_id, match_tag_hint)]

    Note:
    - `match_tag`는 SELL 스켈레톤 탐색 우선순위 힌트이며, tier 분류에서 `PICK_ONLY`를 즉시 확정하지 않는다.
    """

    tags = list(sale_asset.top_tags or ())
    if not tags:
        return []

    # 후보 팀: 전체 30팀에서 계산해도 비싸지 않지만, 여기선 상한을 둔다.
    # BUY 의지가 있는 팀을 우선.
    all_teams = list(catalog.outgoing_by_team.keys())
    rng.shuffle(all_teams)

    rows: List[Tuple[float, str, str]] = []
    for tid in all_teams:
        buyer_id = str(tid).upper()
        if buyer_id == str(seller_id).upper():
            continue

        ts = tick_ctx.get_team_situation(buyer_id)
        if bool(getattr(ts, "constraints", None) and ts.constraints.cooldown_active):
            continue

        posture = str(getattr(ts, "trade_posture", "STAND_PAT") or "STAND_PAT").upper()
        posture_bonus = {
            "AGGRESSIVE_BUY": 1.2,
            "SOFT_BUY": 0.7,
            "STAND_PAT": 0.2,
            "SOFT_SELL": -0.3,
            "SELL": -0.6,
        }.get(posture, 0.0)

        dc = tick_ctx.get_decision_context(buyer_id)
        need_map = dict(getattr(dc, "need_map", {}) or {})

        best_tag_hint = tags[0]
        best = 0.0
        # Keep best-tag selection simple; this tag is a routing hint, not a hard tier decision signal.
        for tag in tags[:4]:
            v = float(need_map.get(tag, 0.0) or 0.0)
            if v > best:
                best = v
                best_tag_hint = tag

        urgency = float(getattr(ts, "urgency", 0.0) or 0.0)
        score = best + 0.35 * urgency + posture_bonus

        # 매우 낮으면 제외
        if score <= 0.01:
            continue

        rows.append((score, buyer_id, str(best_tag_hint)))

        # hard cap to avoid worst-case O(teams*targets) blow-up
        if len(rows) >= 24:
            break

    rows.sort(key=lambda r: (-r[0], r[1]))

    # 최대 ~10팀만
    out = [(buyer_id, tag) for _, buyer_id, tag in rows[:10]]
    return out


def _is_ban_active(current_date: date, until_iso: Optional[str]) -> bool:
    """until_iso(YYYY-MM-DD)가 현재 날짜 기준으로 아직 남아있으면 True."""
    d = _parse_iso_ymd(until_iso)
    return bool(d is not None and current_date < d)


def _is_seller_willing_to_move_player(player_id: str, seller_out: TeamOutgoingCatalog) -> bool:
    for b, ids in seller_out.player_ids_by_bucket.items():
        if player_id in ids:
            return True
    return False
