from __future__ import annotations

"""asset_catalog.py

Tick-scoped "tradable asset catalog" for deal generation.

Goal
----
Provide NBA-like candidate pools (outgoing buckets per team + league-wide incoming
index by need tags + movable picks/swaps with Stepien/locks preprocessed) so a
deal generator can explore many candidates without repeatedly scanning the DB.

Design
------
- Built once per generation tick (uses TradeGenerationTickContext).
- Deterministic ordering (stable sorting + player_id tie-breaks).
- Uses existing SSOT logic:
  - locks: same semantics as AssetLockRule (expires_at + allow_locked_by_deal_id)
  - player bans: same policy helpers used by rules/team_situation
  - contract terms: contracts/terms.py SSOT (remaining years / salary schedule)
  - fit: FitEngine SSOT
  - market: MarketPricer SSOT
  - Stepien: stepien_policy SSOT (shared with PickRulesRule)
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Literal

# LeagueRepo lives at project root in current layout.
try:
    from league_repo import LeagueRepo  # type: ignore
except Exception:  # pragma: no cover
    from trade.league_repo import LeagueRepo  # type: ignore

# SSOT: contract schedule interpretation (remaining years, salary for a season).
try:
    from contracts.terms import player_contract_terms as _player_contract_terms  # type: ignore
except Exception:  # pragma: no cover
    _player_contract_terms = None  # type: ignore

# Config (team list)
try:
    from config import ALL_TEAM_IDS  # type: ignore
except Exception:  # pragma: no cover
    from trade.config import ALL_TEAM_IDS  # type: ignore

from ..models import PlayerAsset, PickAsset, SwapAsset, asset_key as _asset_key
from ..rules.policies.player_ban_policy import (
    compute_aggregation_banned_until,
    compute_recent_signing_banned_until,
)
from ..rules.policies.stepien_policy import check_stepien_violation

from ..valuation.fit_engine import FitEngine, FitEngineConfig
from ..valuation.market_pricing import MarketPricer, MarketPricingConfig
from ..valuation.env import ValuationEnv
from ..valuation.data_context import contract_snapshot_from_dict
from ..valuation.types import (
    MarketValuation,
    PlayerSnapshot,
    PickSnapshot,
    SwapSnapshot,
    ContractSnapshot,
    ValueComponents,
)

# TradeGenerationTickContext import kept local to avoid circular import at module load.


# =============================================================================
# Small helpers (pure)
# =============================================================================
def _canon_team_id(team_id: Any) -> str:
    raw = str(team_id or "").strip()
    if not raw:
        return ""
    try:
        from schema import normalize_team_id  # type: ignore

        return str(normalize_team_id(raw, strict=False)).strip().upper()
    except Exception:
        return raw.upper()


def _canon_player_id(player_id: Any) -> str:
    raw = str(player_id or "").strip()
    if not raw:
        return ""
    try:
        from schema import normalize_player_id  # type: ignore

        return str(normalize_player_id(raw, strict=False, allow_legacy_numeric=True)).strip()
    except Exception:
        return raw


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return int(default)
        return int(x)
    except Exception:
        return int(default)


def _safe_float(x: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _to_iso(d: Optional[date]) -> Optional[str]:
    if d is None:
        return None
    try:
        return d.isoformat()
    except Exception:
        return None


def _parse_iso_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except Exception:
        return None


def _remaining_years_for_player_snapshot(snap: PlayerSnapshot, season_year: int) -> float:
    """SSOT: remaining seasons >= current season with salary>0.

    Uses `contracts.terms.player_contract_terms` so behavior matches valuation:
    - if `snap.contract` exists: schedule length from contract salary_by_year
    - else: salary_amount fallback (1-year if salary known)
    """
    if _player_contract_terms is None:
        return 0.0
    try:
        terms = _player_contract_terms(snap, current_season_year=int(season_year))
        ry = int(getattr(terms, "remaining_years", 0) or 0)
        return float(max(0, ry))
    except Exception:
        return 0.0


# =============================================================================
# Data structures
# =============================================================================
@dataclass(frozen=True, slots=True)
class MarketValueSummary:
    now: float
    future: float
    total: float

    @staticmethod
    def from_components(vc: ValueComponents) -> "MarketValueSummary":
        now = float(getattr(vc, "now", 0.0) or 0.0)
        fut = float(getattr(vc, "future", 0.0) or 0.0)
        return MarketValueSummary(now=now, future=fut, total=now + fut)


@dataclass(frozen=True, slots=True)
class LockInfo:
    is_locked: bool
    expires_at: Optional[str] = None
    deal_id: Optional[str] = None


BucketId = Literal[
    "FILLER_BAD_CONTRACT",
    "FILLER_CHEAP",
    "SURPLUS_EXPENDABLE",
    "VETERAN_SALE",
    "CONSOLIDATE",
]


@dataclass(frozen=True, slots=True)
class PlayerTradeCandidate:
    player_id: str
    team_id: str

    snap: PlayerSnapshot
    market: MarketValueSummary

    supply: Dict[str, float]
    top_tags: Tuple[str, ...]

    fit_vs_team: float
    surplus_score: float

    salary_m: float
    remaining_years: float
    is_expiring: bool

    lock: LockInfo
    recent_signing_banned_until: Optional[str]
    aggregation_banned_until: Optional[str]
    aggregation_solo_only: bool
    return_ban_teams: Tuple[str, ...]

    fit_vs_peers: float = 0.0
    misfit_peer: float = 0.0
    redundancy_peer: float = 0.0
    redundancy_peer_norm: float = 0.0
    peer_cover: float = 0.0
    dependence_risk: float = 0.0
    core_proxy: float = 0.0
    identity_risk_proxy: float = 0.0
    minutes_squeeze_proxy: float = 0.0
    contract_pressure: float = 0.0
    raw_trade_block_score: float = 0.0
    trade_block_score: float = 0.0
    hard_protected: bool = False
    expendable_gate_passed: bool = False
    surplus_reason_flags: Tuple[str, ...] = field(default_factory=tuple)
    surplus_protection_flags: Tuple[str, ...] = field(default_factory=tuple)

    buckets: Tuple[BucketId, ...] = field(default_factory=tuple)

    def as_asset(self, to_team: Optional[str] = None) -> PlayerAsset:
        return PlayerAsset(kind="player", player_id=self.player_id, to_team=to_team)


PickBucketId = Literal["FIRST_SAFE", "FIRST_SENSITIVE", "SECOND"]


@dataclass(frozen=True, slots=True)
class PickTradeCandidate:
    pick_id: str
    owner_team: str

    snap: PickSnapshot
    market: MarketValueSummary

    lock: LockInfo
    within_max_years: bool

    stepien_safe_if_traded_alone: bool
    stepien_sensitive: bool

    bucket: PickBucketId

    def as_asset(self, to_team: Optional[str] = None) -> PickAsset:
        return PickAsset(kind="pick", pick_id=self.pick_id, to_team=to_team, protection=self.snap.protection)


@dataclass(frozen=True, slots=True)
class SwapTradeCandidate:
    swap_id: str
    owner_team: str

    snap: SwapSnapshot
    lock: LockInfo

    def as_asset(self, to_team: Optional[str] = None) -> SwapAsset:
        return SwapAsset(
            kind="swap",
            swap_id=self.swap_id,
            pick_id_a=self.snap.pick_id_a,
            pick_id_b=self.snap.pick_id_b,
            to_team=to_team,
        )


@dataclass(frozen=True, slots=True)
class TeamOutgoingCatalog:
    team_id: str

    player_ids_by_bucket: Dict[BucketId, Tuple[str, ...]]
    pick_ids_by_bucket: Dict[PickBucketId, Tuple[str, ...]]
    swap_ids: Tuple[str, ...]

    players: Dict[str, PlayerTradeCandidate]
    picks: Dict[str, PickTradeCandidate]
    swaps: Dict[str, SwapTradeCandidate]


@dataclass(frozen=True, slots=True)
class IncomingPlayerRef:
    player_id: str
    from_team: str
    tag: str
    tag_strength: float
    market_total: float
    salary_m: float
    remaining_years: float
    age: Optional[float]
    basketball_total: float = 0.0
    contract_total: float = 0.0
    contract_gap_cap_share: float = 0.0
    expected_cap_share_avg: float = 0.0
    actual_cap_share_avg: float = 0.0
    # multi-tag supply profile for need-similarity scoring (tag, strength in [0,1])
    supply_items: Tuple[Tuple[str, float], ...] = tuple()


@dataclass(frozen=True, slots=True)
class StepienHelper:
    draft_picks: Mapping[str, Mapping[str, Any]]
    current_draft_year: int
    lookahead: int

    def is_compliant_after(
        self,
        *,
        team_id: str,
        outgoing_pick_ids: Set[str],
        incoming_pick_ids: Set[str],
    ) -> bool:
        """True if the team remains Stepien-compliant after pick ownership changes."""
        if self.lookahead <= 0:
            return True

        tid = _canon_team_id(team_id)
        if not tid:
            return True

        # Base owner map from snapshot.
        owner_after: Dict[str, str] = {}
        for pid, pick in self.draft_picks.items():
            if not isinstance(pick, Mapping):
                continue
            owner_after[str(pid)] = _canon_team_id(pick.get("owner_team"))

        # Apply outgoing/incoming mutations.
        for pid in outgoing_pick_ids or set():
            k = str(pid)
            if k not in owner_after:
                continue
            # Remove ownership from this team; the specific destination doesn't matter for Stepien.
            owner_after[k] = "__OUT__"
        for pid in incoming_pick_ids or set():
            k = str(pid)
            if k not in owner_after:
                continue
            owner_after[k] = tid

        violation = check_stepien_violation(
            team_id=tid,
            draft_picks=self.draft_picks,
            current_draft_year=int(self.current_draft_year),
            lookahead=int(self.lookahead),
            owner_after=owner_after,
        )
        return violation is None


@dataclass(frozen=True, slots=True)
class TradeAssetCatalog:
    db_path: str
    built_for_date: date
    season_year: int
    draft_year: int
    trade_rules: Dict[str, Any]

    outgoing_by_team: Dict[str, TeamOutgoingCatalog]
    incoming_all_players: Tuple[IncomingPlayerRef, ...]

    stepien: StepienHelper


# =============================================================================
# Catalog build
# =============================================================================

# Thresholds / constants (deterministic)
_TOP_TAG_MIN = 0.55
_LOW_FIT_MAX = 0.45
_REDUNDANCY_GATE = 0.52
_REPLACEABLE_GATE = 0.58
_SQUEEZE_GATE = 0.55
_CONTRACT_GATE = 0.60
_TRADE_BLOCK_SCORE_GATE_BY_POSTURE = {
    "SELL": 0.48,
    "SOFT_SELL": 0.52,
    "STAND_PAT": 0.58,
    "SOFT_BUY": 0.64,
    "AGGRESSIVE_BUY": 0.68,
}
_PROTECTION_WEIGHT_BY_POSTURE = {
    "SELL": 0.85,
    "SOFT_SELL": 0.95,
    "STAND_PAT": 1.00,
    "SOFT_BUY": 1.10,
    "AGGRESSIVE_BUY": 1.18,
}

_NEGATIVE_MONEY_NORM_CAP_SHARE = 0.05
_MARKET_NOW_NORM = 12.0

_BAD_CONTRACT_NEGATIVE_MONEY_MIN_CAP_SHARE = 0.015
_BAD_CONTRACT_YEARS_MIN = 2.0
_BAD_CONTRACT_FLEX_PRESSURE_MIN = 0.55
_VETERAN_MARKET_NOW_MIN = 6.0
_TIMELINE_MISMATCH_MIN = 0.45


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _norm(x: float, denom: float) -> float:
    d = float(denom)
    if d <= 0.0:
        return 0.0
    return _clamp01(float(x) / d)


def _safe_team_signal(ts: Any, name: str, default: float = 0.0) -> float:
    key = str(name)
    if isinstance(ts, Mapping):
        return float(_safe_float(ts.get(key), float(default)) or float(default))
    return float(_safe_float(getattr(ts, key, default), float(default)) or float(default))


@dataclass(frozen=True, slots=True)
class BadContractEval:
    enter: bool
    score: float
    negative_money: float
    years_factor: float
    team_flex_pressure: float
    expendability: float


def _eval_bad_contract_candidate(
    *,
    c: PlayerTradeCandidate,
    ts: Any,
    expected_cap_share_avg: float,
    actual_cap_share_avg: float,
) -> BadContractEval:
    negative_money = max(0.0, float(actual_cap_share_avg - expected_cap_share_avg))
    years_factor = _clamp01(float(c.remaining_years) / 4.0)

    flexibility = _safe_team_signal(getattr(ts, "signals", None), "flexibility", 0.5)
    team_flex_pressure = _clamp01(1.0 - flexibility)
    expendability = _clamp01(float(c.surplus_score))

    negative_money_norm = _norm(negative_money, _NEGATIVE_MONEY_NORM_CAP_SHARE)

    score = (
        0.55 * negative_money_norm
        + 0.20 * years_factor
        + 0.15 * team_flex_pressure
        + 0.10 * expendability
    )

    support_gate = (
        float(c.remaining_years) >= _BAD_CONTRACT_YEARS_MIN
        or team_flex_pressure >= _BAD_CONTRACT_FLEX_PRESSURE_MIN
    )
    enter = (negative_money >= _BAD_CONTRACT_NEGATIVE_MONEY_MIN_CAP_SHARE) and support_gate

    return BadContractEval(
        enter=bool(enter),
        score=float(score),
        negative_money=float(negative_money),
        years_factor=float(years_factor),
        team_flex_pressure=float(team_flex_pressure),
        expendability=float(expendability),
    )


@dataclass(frozen=True, slots=True)
class VeteranSaleEval:
    enter: bool
    score: float
    timeline_mismatch: float
    market_now_norm: float
    age_decline: float
    contract_window_risk: float


def _timeline_horizon_pressure(ts: Any) -> float:
    horizon = str(getattr(ts, "time_horizon", "") or "").upper()
    if horizon == "REBUILD":
        return 1.0
    if horizon == "RE_TOOL":
        return 0.75
    if horizon == "WIN_NOW":
        return 0.20
    return 0.45


def _eval_veteran_sale_candidate(*, c: PlayerTradeCandidate, ts: Any) -> VeteranSaleEval:
    age = float(c.snap.age or 0.0)
    age_decline = _clamp01((age - 28.0) / 8.0)

    horizon_pressure = _timeline_horizon_pressure(ts)
    years_factor = _clamp01(float(c.remaining_years) / 4.0)

    timeline_mismatch = _clamp01(horizon_pressure * (0.60 * age_decline + 0.40 * years_factor))

    market_now_norm = _norm(float(c.market.now), _MARKET_NOW_NORM)

    re_sign_pressure = _safe_team_signal(getattr(ts, "signals", None), "re_sign_pressure", 0.0)
    expiring_risk = 1.0 if bool(c.is_expiring) else 0.0
    contract_window_risk = _clamp01(0.65 * expiring_risk + 0.35 * _clamp01(re_sign_pressure))

    score = (
        0.40 * timeline_mismatch
        + 0.25 * market_now_norm
        + 0.20 * age_decline
        + 0.15 * contract_window_risk
    )

    team_ok = (
        str(getattr(ts, "trade_posture", "") or "").upper() in {"SELL", "SOFT_SELL"}
        or str(getattr(ts, "time_horizon", "") or "").upper() == "REBUILD"
    )

    mismatch_gate = timeline_mismatch >= _TIMELINE_MISMATCH_MIN
    contract_window_gate = (age_decline >= 0.35) and (contract_window_risk >= 0.55)
    enter = team_ok and (float(c.market.now) >= _VETERAN_MARKET_NOW_MIN) and (mismatch_gate or contract_window_gate)

    return VeteranSaleEval(
        enter=bool(enter),
        score=float(score),
        timeline_mismatch=float(timeline_mismatch),
        market_now_norm=float(market_now_norm),
        age_decline=float(age_decline),
        contract_window_risk=float(contract_window_risk),
    )


def _compute_peer_signals(
    *,
    candidate: PlayerTradeCandidate,
    need_map: Mapping[str, float],
    team_supply_total: Mapping[str, float],
    fit_engine: FitEngine,
) -> Dict[str, float]:
    eps = 1e-9
    peer_supply_without_p: Dict[str, float] = {}
    peer_need_map: Dict[str, float] = {}
    supply = candidate.supply or {}
    for tag in set(tuple(team_supply_total.keys()) + tuple(supply.keys()) + tuple((need_map or {}).keys())):
        total_v = float(team_supply_total.get(tag, 0.0) or 0.0)
        own_v = float(supply.get(tag, 0.0) or 0.0)
        peer_supply_without_p[tag] = max(0.0, total_v - own_v)
        peer_need_map[tag] = _clamp01(float(need_map.get(tag, 0.0) or 0.0) + own_v)

    fit_vs_peers, _, _ = fit_engine.score_fit(peer_need_map, supply)
    misfit_peer = 1.0 - float(fit_vs_peers)

    redundancy_peer = 0.0
    supply_sum = 0.0
    cover_sum = 0.0
    for tag, raw_v in supply.items():
        v = float(raw_v or 0.0)
        supply_sum += v
        need_v = float(peer_need_map.get(tag, 0.0) or 0.0)
        redundancy_peer += v * (1.0 - need_v)
        cover_sum += min(v, float(peer_supply_without_p.get(tag, 0.0) or 0.0))
    redundancy_peer_norm = float(redundancy_peer / (supply_sum + eps))
    peer_cover = float(cover_sum / (supply_sum + eps))
    dependence_risk = _clamp01(float(fit_vs_peers) - float(candidate.fit_vs_team))

    return {
        "fit_vs_peers": float(fit_vs_peers),
        "misfit_peer": float(misfit_peer),
        "redundancy_peer": float(redundancy_peer),
        "redundancy_peer_norm": float(redundancy_peer_norm),
        "peer_cover": float(peer_cover),
        "dependence_risk": float(dependence_risk),
    }


def _compute_protection_signals(
    *,
    candidate: PlayerTradeCandidate,
    team_candidates: Sequence[PlayerTradeCandidate],
    team_supply_total: Mapping[str, float],
    value_breakdown: Mapping[str, float],
) -> Dict[str, float]:
    basket_total = _safe_float(value_breakdown.get("basketball_total"), None)
    gap_cap_share = _safe_float(value_breakdown.get("contract_gap_cap_share"), None)
    expected_cap_share_avg = _safe_float(value_breakdown.get("expected_cap_share_avg"), None)
    actual_cap_share_avg = _safe_float(value_breakdown.get("actual_cap_share_avg"), None)

    if gap_cap_share is not None:
        contract_pressure = _clamp01((-float(gap_cap_share)) / 0.06)
    elif expected_cap_share_avg is not None and actual_cap_share_avg is not None:
        contract_pressure = _clamp01((float(actual_cap_share_avg) - float(expected_cap_share_avg)) / 0.06)
    else:
        contract_pressure = 0.0

    overlap_count = 0
    own_top_tags = set(candidate.top_tags or tuple())
    for c in team_candidates:
        if c.player_id == candidate.player_id:
            continue
        if own_top_tags.intersection(set(c.top_tags or tuple())):
            overlap_count += 1
    minutes_squeeze_proxy = _clamp01((float(overlap_count) - 1.0) / 6.0)

    ranked = sorted(
        team_candidates,
        key=lambda c: (-float(c.market.now if c.market.now is not None else c.market.total), c.player_id),
    )
    rank_now = 1
    for idx, c in enumerate(ranked, start=1):
        if c.player_id == candidate.player_id:
            rank_now = idx
            break
    core_from_now = _clamp01(1.0 - float(rank_now - 1) / float(max(1, len(ranked) - 1)))
    if basket_total is not None:
        basketball_core = _clamp01((float(basket_total) + 15.0) / 45.0)
        core_proxy = _clamp01(0.75 * core_from_now + 0.25 * basketball_core)
    else:
        core_proxy = core_from_now

    identity_tags = sorted(
        [(str(k), float(v or 0.0)) for k, v in (team_supply_total or {}).items()],
        key=lambda t: (-t[1], t[0]),
    )[:3]
    identity_contrib_share = 0.0
    for tag, team_total in identity_tags:
        if team_total <= 0.0:
            continue
        identity_contrib_share += float(candidate.supply.get(tag, 0.0) or 0.0) / float(team_total)
    identity_risk_proxy = _clamp01(identity_contrib_share)

    timing_liquidity = 1.0 if bool(candidate.is_expiring) else 0.0
    return {
        "core_proxy": float(core_proxy),
        "identity_risk_proxy": float(identity_risk_proxy),
        "minutes_squeeze_proxy": float(minutes_squeeze_proxy),
        "contract_pressure": float(contract_pressure),
        "timing_liquidity": float(timing_liquidity),
    }


def _lock_info_for_asset_key(
    *,
    asset_key_value: str,
    asset_locks: Mapping[str, Any],
    current_date: date,
    allow_locked_by_deal_id: Optional[str] = None,
) -> LockInfo:
    lock = asset_locks.get(asset_key_value) if isinstance(asset_locks, Mapping) else None
    if not isinstance(lock, Mapping):
        return LockInfo(is_locked=False, expires_at=None, deal_id=None)

    deal_id = lock.get("deal_id")
    expires_at_raw = lock.get("expires_at")
    expires_at_date = _parse_iso_date(expires_at_raw)
    expires_at_iso = _to_iso(expires_at_date) if expires_at_date else (str(expires_at_raw) if expires_at_raw else None)

    if expires_at_date is not None and current_date > expires_at_date:
        return LockInfo(is_locked=False, expires_at=expires_at_iso, deal_id=str(deal_id) if deal_id else None)

    if allow_locked_by_deal_id and deal_id is not None and str(deal_id) == str(allow_locked_by_deal_id):
        return LockInfo(is_locked=False, expires_at=expires_at_iso, deal_id=str(deal_id))

    return LockInfo(is_locked=True, expires_at=expires_at_iso, deal_id=str(deal_id) if deal_id else None)


def _extract_player_value_breakdown(mv: MarketValuation) -> Dict[str, float]:
    out = {
        "basketball_total": 0.0,
        "contract_total": 0.0,
        "contract_gap_cap_share": 0.0,
        "expected_cap_share_avg": 0.0,
        "actual_cap_share_avg": 0.0,
    }

    meta = mv.meta if isinstance(getattr(mv, "meta", None), Mapping) else {}
    vb = meta.get("value_breakdown") if isinstance(meta.get("value_breakdown"), Mapping) else {}
    basket = vb.get("basketball") if isinstance(vb.get("basketball"), Mapping) else {}
    contract = vb.get("contract") if isinstance(vb.get("contract"), Mapping) else {}
    out["basketball_total"] = float(_safe_float(basket.get("total"), 0.0) or 0.0)
    out["contract_total"] = float(_safe_float(contract.get("total"), 0.0) or 0.0)

    contract_rows: Sequence[Mapping[str, Any]] = tuple()
    for st in tuple(getattr(mv, "steps", tuple()) or tuple()):
        if str(getattr(st, "code", "") or "") != "CONTRACT_SURPLUS_DELTA":
            continue
        st_meta = getattr(st, "meta", None)
        if not isinstance(st_meta, Mapping):
            continue
        rows = st_meta.get("rows")
        if isinstance(rows, Sequence):
            contract_rows = tuple(r for r in rows if isinstance(r, Mapping))
            break

    if not contract_rows:
        return out

    w_sum = 0.0
    gap_sum = 0.0
    expected_sum = 0.0
    actual_sum = 0.0

    for r in contract_rows:
        cap_y = float(_safe_float(r.get("cap"), 0.0) or 0.0)
        if cap_y <= 0.0:
            continue
        fair_salary = float(_safe_float(r.get("fair_salary"), 0.0) or 0.0)
        actual_salary = float(_safe_float(r.get("actual_salary"), 0.0) or 0.0)
        disc = float(_safe_float(r.get("disc"), 0.0) or 0.0)
        w = max(0.35, disc)

        expected_cs = fair_salary / cap_y
        actual_cs = actual_salary / cap_y
        gap_cs = expected_cs - actual_cs

        w_sum += w
        expected_sum += expected_cs * w
        actual_sum += actual_cs * w
        gap_sum += gap_cs * w

    if w_sum <= 0.0:
        return out

    out["expected_cap_share_avg"] = expected_sum / w_sum
    out["actual_cap_share_avg"] = actual_sum / w_sum
    out["contract_gap_cap_share"] = gap_sum / w_sum
    return out


def _market_summary_for_player(
    pricer: MarketPricer,
    snap: PlayerSnapshot,
    *,
    env: Optional[ValuationEnv] = None,
) -> Tuple[MarketValueSummary, Dict[str, float]]:
    a = PlayerAsset(kind="player", player_id=snap.player_id, to_team=None)
    mv = pricer.price_snapshot(snap, asset_key=_asset_key(a), env=env)
    return MarketValueSummary.from_components(mv.value), _extract_player_value_breakdown(mv)


def _market_summary_for_pick(
    pricer: MarketPricer,
    snap: PickSnapshot,
    *,
    env: Optional[ValuationEnv] = None,
    pick_expectation: Optional[Any],
) -> MarketValueSummary:
    a = PickAsset(kind="pick", pick_id=snap.pick_id, to_team=None, protection=snap.protection)
    mv = pricer.price_snapshot(snap, asset_key=_asset_key(a), env=env, pick_expectation=pick_expectation)
    return MarketValueSummary.from_components(mv.value)


def _compute_top_tags(supply: Mapping[str, float]) -> Tuple[str, ...]:
    items = [(str(k), float(v or 0.0)) for k, v in (supply or {}).items()]
    items.sort(key=lambda x: (-x[1], x[0]))
    out: List[str] = []
    for tag, v in items[:4]:
        if v >= _TOP_TAG_MIN:
            out.append(tag)
    return tuple(out)


def _bucket_caps_for_posture(posture: str) -> Dict[BucketId, int]:
    p = str(posture or "").upper()
    # Defaults are deliberately moderate; generator can widen later.
    if p in {"SELL", "SOFT_SELL"}:
        return {
            "FILLER_BAD_CONTRACT": 4,
            "FILLER_CHEAP": 4,
            "SURPLUS_EXPENDABLE": 7,
            "VETERAN_SALE": 5,
            "CONSOLIDATE": 0,
        }
    if p in {"AGGRESSIVE_BUY"}:
        return {
            "FILLER_BAD_CONTRACT": 7,
            "FILLER_CHEAP": 5,
            "SURPLUS_EXPENDABLE": 5,
            "VETERAN_SALE": 0,
            "CONSOLIDATE": 7,
        }
    if p in {"SOFT_BUY"}:
        return {
            "FILLER_BAD_CONTRACT": 7,
            "FILLER_CHEAP": 5,
            "SURPLUS_EXPENDABLE": 5,
            "VETERAN_SALE": 0,
            "CONSOLIDATE": 5,
        }
    # STAND_PAT / unknown
    return {
        "FILLER_BAD_CONTRACT": 6,
        "FILLER_CHEAP": 4,
        "SURPLUS_EXPENDABLE": 6,
        "VETERAN_SALE": 0,
        "CONSOLIDATE": 3,
    }


def _outgoing_priority_for_posture(posture: str) -> Tuple[BucketId, ...]:
    p = str(posture or "").upper()
    if p in {"SELL", "SOFT_SELL"}:
        return (
            "VETERAN_SALE",
            "SURPLUS_EXPENDABLE",
            "FILLER_BAD_CONTRACT",
            "FILLER_CHEAP",
            "CONSOLIDATE",
        )
    if p in {"AGGRESSIVE_BUY", "SOFT_BUY"}:
        return (
            "CONSOLIDATE",
            "FILLER_BAD_CONTRACT",
            "SURPLUS_EXPENDABLE",
            "FILLER_CHEAP",
            "VETERAN_SALE",
        )
    # STAND_PAT / unknown
    return (
        "SURPLUS_EXPENDABLE",
        "FILLER_BAD_CONTRACT",
        "FILLER_CHEAP",
        "CONSOLIDATE",
        "VETERAN_SALE",
    )


def build_trade_asset_catalog(
    *,
    tick_ctx: Any,
    bucket_caps_override: Optional[Dict[str, int]] = None,
    allow_locked_by_deal_id: Optional[str] = None,
) -> TradeAssetCatalog:
    """Build TradeAssetCatalog from an existing TradeGenerationTickContext."""

    # --- Extract tick inputs (defensive)
    db_path = str(getattr(tick_ctx, "db_path", ""))
    current_date = getattr(tick_ctx, "current_date", None)
    if not isinstance(current_date, date):
        raise RuntimeError("build_trade_asset_catalog: tick_ctx.current_date must be a date")

    season_year = _safe_int(getattr(tick_ctx, "season_year", 0), 0)
    if season_year <= 0:
        raise RuntimeError("build_trade_asset_catalog: tick_ctx.season_year missing/invalid")

    rule_tick_ctx = getattr(tick_ctx, "rule_tick_ctx", None)
    if rule_tick_ctx is None:
        raise RuntimeError("build_trade_asset_catalog: tick_ctx.rule_tick_ctx missing")

    ctx_state_base = getattr(rule_tick_ctx, "ctx_state_base", {}) or {}
    if not isinstance(ctx_state_base, Mapping):
        ctx_state_base = {}
    league = ctx_state_base.get("league", {}) if isinstance(ctx_state_base, Mapping) else {}
    if not isinstance(league, Mapping):
        league = {}

    trade_rules = league.get("trade_rules", {}) or {}
    if not isinstance(trade_rules, Mapping):
        trade_rules = {}

    draft_year = _safe_int(league.get("draft_year"), 0)
    if draft_year <= 0:
        # Fallback (should not happen once state.export_trade_context_snapshot includes draft_year)
        draft_year = season_year + 1

    asset_locks = ctx_state_base.get("asset_locks", {}) or {}
    if not isinstance(asset_locks, Mapping):
        asset_locks = {}

    provider = getattr(tick_ctx, "provider", None)
    if provider is None:
        raise RuntimeError("build_trade_asset_catalog: tick_ctx.provider missing")

    repo: LeagueRepo = getattr(tick_ctx, "repo", None)
    if repo is None:
        raise RuntimeError("build_trade_asset_catalog: tick_ctx.repo missing")

    # --- Pricing + fit engines (pure + cached)
    # SSOT: build valuation runtime env once per tick so catalog pricing matches deal evaluation.
    env = ValuationEnv.from_trade_rules(trade_rules, current_season_year=int(season_year))

    # Cap-normalized market config: keep salary_cap populated for legacy code paths.
    salary_cap = _safe_float(trade_rules.get("salary_cap"), 0.0) or 0.0
    if salary_cap <= 0.0:
        try:
            salary_cap = float(env.salary_cap())
        except Exception:
            salary_cap = 0.0
          
    pricer = MarketPricer(
        config=MarketPricingConfig(salary_cap=float(salary_cap)) if salary_cap > 0.0 else MarketPricingConfig()
    )
    fit_engine = FitEngine(config=FitEngineConfig())

    # --- Stepien helper (shared policy)
    max_pick_years_ahead = _safe_int(trade_rules.get("max_pick_years_ahead"), 7)
    stepien_lookahead = _safe_int(trade_rules.get("stepien_lookahead"), 7)
    stepien = StepienHelper(
        draft_picks=getattr(provider, "draft_picks_map", {}) or {},
        current_draft_year=int(draft_year),
        lookahead=int(stepien_lookahead),
    )

    # --- Determine team ids
    ids_raw: Sequence[str]
    try:
        ids_raw = list(getattr(tick_ctx, "team_situations", {}) or {}.keys())
    except Exception:
        ids_raw = []
    if not ids_raw:
        ids_raw = list(getattr(tick_ctx, "gm_profiles", {}) or {}.keys())
    if not ids_raw:
        ids_raw = list(ALL_TEAM_IDS)
    team_ids = sorted({_canon_team_id(t) for t in ids_raw if _canon_team_id(t)})

    # --- Build outgoing catalogs per team
    outgoing_by_team: Dict[str, TeamOutgoingCatalog] = {}

    # Accumulate all eligible player candidates for league-wide BUY incoming index.
    incoming_all_players_by_id: Dict[str, IncomingPlayerRef] = {}
    incoming_player_value_by_id: Dict[str, Dict[str, float]] = {}

    # Precompute for all teams to keep deterministic behavior.
    for tid in team_ids:
        # Team posture/decision inputs
        ts = tick_ctx.get_team_situation(tid)
        dc = tick_ctx.get_decision_context(tid)
        posture = str(getattr(ts, "trade_posture", "") or "")

        caps = _bucket_caps_for_posture(posture)
        if bucket_caps_override:
            # override keys are strings; bucket ids are the same string set.
            for k, v in bucket_caps_override.items():
                kk = str(k or "").strip().upper()
                if kk in caps:
                    caps[kk] = _safe_int(v, caps[kk])

        # --- Roster snapshot (1 query per team)
        roster_rows = repo.get_team_roster(tid) or []
        roster_player_ids: List[str] = []
        for r in roster_rows:
            if not isinstance(r, Mapping):
                continue
            pid = _canon_player_id(r.get("player_id"))
            if pid:
                roster_player_ids.append(pid)

        # Build rule meta once per team roster (cached in tick context).
        players_meta = rule_tick_ctx.ensure_players_meta(roster_player_ids)

        # --- Build player candidates
        players: Dict[str, PlayerTradeCandidate] = {}
        per_team_candidates: List[PlayerTradeCandidate] = []

        for r in roster_rows:
            if not isinstance(r, Mapping):
                continue
            pid = _canon_player_id(r.get("player_id"))
            if not pid:
                continue

            # Lock info (same as AssetLockRule)
            lock = _lock_info_for_asset_key(
                asset_key_value=_asset_key(PlayerAsset(kind="player", player_id=pid)),
                asset_locks=asset_locks,
                current_date=current_date,
                allow_locked_by_deal_id=allow_locked_by_deal_id,
            )

            meta = players_meta.get(pid) or {}
            if not isinstance(meta, Mapping):
                meta = {}

            # Player bans
            recent_until_date, _ = compute_recent_signing_banned_until(
                dict(meta),
                trade_rules=dict(trade_rules),
                season_year=int(season_year),
                strict=False,
            )
            aggregation_until_date, _ = compute_aggregation_banned_until(
                dict(meta),
                trade_rules=dict(trade_rules),
                strict=False,
            )
            recent_until_iso = _to_iso(recent_until_date)
            aggregation_until_iso = _to_iso(aggregation_until_date)
            recent_active = bool(recent_until_date is not None and current_date < recent_until_date)
            aggregation_active = bool(aggregation_until_date is not None and current_date < aggregation_until_date)

            # Recent-signing ban means not tradable at all (per rules).
            # Still store candidate object for debugging? We keep it but it won't go into buckets/indices.
            # Contract attach via provider's injected contract ledger maps.
            contract: Optional[ContractSnapshot] = None
            cid = getattr(provider, "active_contract_id_by_player", {}).get(pid)
            if cid:
                cdict = getattr(provider, "contracts_map", {}).get(cid)
                if isinstance(cdict, Mapping):
                    try:
                        contract = contract_snapshot_from_dict(cdict, current_season_year=int(season_year))
                    except Exception:
                        contract = None

            attrs = r.get("attrs") if isinstance(r.get("attrs"), Mapping) else {}
            snap = PlayerSnapshot(
                kind="player",
                player_id=pid,
                name=(str(r.get("name")) if r.get("name") is not None else None),
                pos=(str(r.get("pos")) if r.get("pos") is not None else None),
                age=_safe_float(r.get("age"), None),
                ovr=_safe_float(r.get("ovr"), None),
                team_id=tid,
                salary_amount=_safe_float(r.get("salary_amount"), None),
                attrs=dict(attrs) if isinstance(attrs, Mapping) else {},
                contract=contract,
                meta={},
            )

            market, value_breakdown = _market_summary_for_player(pricer, snap, env=env)
            supply = fit_engine.compute_player_supply_vector(snap)
            fit_score, _, _ = fit_engine.score_fit(dc.need_map or {}, supply)
            top_tags = _compute_top_tags(supply)

            remaining_years = _remaining_years_for_player_snapshot(snap, int(season_year))
            is_expiring = bool(remaining_years <= 1.0 + 1e-9)
            salary_m = float((snap.salary_amount or 0.0) / 1_000_000.0)

            # Return-to-trading-team bans: season-specific list keyed by str(season_year).
            return_bans = ()
            try:
                trb = meta.get("trade_return_bans") if isinstance(meta, Mapping) else None
                if isinstance(trb, Mapping):
                    teams = trb.get(str(int(season_year))) or []
                    if isinstance(teams, list):
                        return_bans = tuple(sorted({_canon_team_id(t) for t in teams if _canon_team_id(t)}))
            except Exception:
                return_bans = ()

            cand = PlayerTradeCandidate(
                player_id=pid,
                team_id=tid,
                snap=snap,
                market=market,
                supply=dict(supply),
                top_tags=top_tags,
                fit_vs_team=float(fit_score),
                surplus_score=float(1.0 - float(fit_score)),
                salary_m=salary_m,
                remaining_years=float(remaining_years),
                is_expiring=is_expiring,
                lock=lock,
                recent_signing_banned_until=recent_until_iso,
                aggregation_banned_until=aggregation_until_iso,
                aggregation_solo_only=bool(aggregation_active),
                return_ban_teams=return_bans,
                buckets=(),
            )
            players[pid] = cand
            incoming_player_value_by_id[pid] = dict(value_breakdown)
            per_team_candidates.append(cand)

        # --- Compute per-team bucket memberships
        # Exclude locked and recent-signing-banned players from outgoing lists by default.
        eligible_for_outgoing = [
            c
            for c in per_team_candidates
            if not c.lock.is_locked
            and not (c.recent_signing_banned_until and _parse_iso_date(c.recent_signing_banned_until) and current_date < _parse_iso_date(c.recent_signing_banned_until))  # type: ignore[arg-type]
        ]
        eligible_ids = {c.player_id for c in eligible_for_outgoing}

        team_supply_total: Dict[str, float] = {}
        for c in per_team_candidates:
            for tag, raw_v in (c.supply or {}).items():
                team_supply_total[str(tag)] = float(team_supply_total.get(str(tag), 0.0) or 0.0) + float(raw_v or 0.0)

        # BAD_CONTRACT filler: negative_money + support gates
        bad_contract_eval_by_pid: Dict[str, BadContractEval] = {}
        filler_bad: List[Tuple[BadContractEval, PlayerTradeCandidate]] = []
        for c in eligible_for_outgoing:
            vb = incoming_player_value_by_id.get(c.player_id, {}) or {}
            expected_cap_share_avg = float(_safe_float(vb.get("expected_cap_share_avg"), 0.0) or 0.0)
            actual_cap_share_avg = float(_safe_float(vb.get("actual_cap_share_avg"), 0.0) or 0.0)
            eval_result = _eval_bad_contract_candidate(
                c=c,
                ts=ts,
                expected_cap_share_avg=expected_cap_share_avg,
                actual_cap_share_avg=actual_cap_share_avg,
            )
            bad_contract_eval_by_pid[c.player_id] = eval_result
            if eval_result.enter:
                filler_bad.append((eval_result, c))
        filler_bad.sort(key=lambda t: (-t[0].score, -t[0].negative_money, -t[1].salary_m, t[1].player_id))
        filler_bad_ids = [c.player_id for _, c in filler_bad[: max(0, caps.get("FILLER_BAD_CONTRACT", 0))]]

        # CHEAP filler
        filler_cheap = [
            c
            for c in eligible_for_outgoing
            if c.salary_m <= 2.5 and c.market.total <= 4.5
        ]
        filler_cheap.sort(
            key=lambda c: (c.market.total, c.salary_m, c.remaining_years, c.player_id)
        )
        filler_cheap_ids = [c.player_id for c in filler_cheap[: max(0, caps.get("FILLER_CHEAP", 0))]]

        p = str(posture or "").upper()
        posture_gate = float(_TRADE_BLOCK_SCORE_GATE_BY_POSTURE.get(p, _TRADE_BLOCK_SCORE_GATE_BY_POSTURE["STAND_PAT"]))
        protection_weight = float(_PROTECTION_WEIGHT_BY_POSTURE.get(p, _PROTECTION_WEIGHT_BY_POSTURE["STAND_PAT"]))

        expendable_scored: List[Tuple[float, PlayerTradeCandidate]] = []
        for c in per_team_candidates:
            peer = _compute_peer_signals(
                candidate=c,
                need_map=dc.need_map or {},
                team_supply_total=team_supply_total,
                fit_engine=fit_engine,
            )
            protection = _compute_protection_signals(
                candidate=c,
                team_candidates=per_team_candidates,
                team_supply_total=team_supply_total,
                value_breakdown=incoming_player_value_by_id.get(c.player_id, {}) or {},
            )

            expendable_base = (
                0.40 * float(peer["redundancy_peer_norm"])
                + 0.20 * float(peer["misfit_peer"])
                + 0.20 * float(peer["peer_cover"])
                + 0.10 * float(protection["contract_pressure"])
                + 0.10 * float(protection["minutes_squeeze_proxy"])
            )
            protection_score = (
                0.40 * float(protection["core_proxy"])
                + 0.35 * float(peer["dependence_risk"])
                + 0.25 * float(protection["identity_risk_proxy"])
            )
            raw_trade_block_score = float(expendable_base - protection_weight * protection_score + 0.05)
            trade_block_score = _clamp01(raw_trade_block_score)

            hard_protected = bool(
                float(protection["core_proxy"]) >= 0.82
                or float(protection["identity_risk_proxy"]) >= 0.78
                or float(peer["dependence_risk"]) >= 0.68
            )

            reason_flags: List[str] = []
            if float(peer["misfit_peer"]) >= 0.55:
                reason_flags.append("LOW_PEER_FIT")
            if float(peer["redundancy_peer_norm"]) >= 0.55:
                reason_flags.append("REDUNDANT_DEPTH")
            if float(protection["minutes_squeeze_proxy"]) >= 0.55:
                reason_flags.append("ROLE_BLOCKED")
            if float(protection["contract_pressure"]) >= 0.60:
                reason_flags.append("EXPENSIVE_FOR_ROLE")
            if float(protection["timing_liquidity"]) >= 1.0:
                reason_flags.append("TIMING_WINDOW")

            protection_flags: List[str] = []
            if float(protection["core_proxy"]) >= 0.75:
                protection_flags.append("CORE_PLAYER")
            if float(protection["identity_risk_proxy"]) >= 0.70:
                protection_flags.append("IDENTITY_ANCHOR")
            if float(peer["dependence_risk"]) >= 0.60:
                protection_flags.append("WEAKNESS_EXPOSURE_RISK")

            gate = (
                float(peer["redundancy_peer_norm"]) >= _REDUNDANCY_GATE
                or float(peer["peer_cover"]) >= _REPLACEABLE_GATE
                or float(protection["minutes_squeeze_proxy"]) >= _SQUEEZE_GATE
                or float(protection["contract_pressure"]) >= _CONTRACT_GATE
                or (float(protection["timing_liquidity"]) >= 1.0 and p in {"SELL", "SOFT_SELL"})
            )
            enter_expendable = (not hard_protected) and trade_block_score >= posture_gate and gate

            players[c.player_id] = PlayerTradeCandidate(
                player_id=c.player_id,
                team_id=c.team_id,
                snap=c.snap,
                market=c.market,
                supply=c.supply,
                top_tags=c.top_tags,
                fit_vs_team=c.fit_vs_team,
                surplus_score=c.surplus_score,
                fit_vs_peers=float(peer["fit_vs_peers"]),
                misfit_peer=float(peer["misfit_peer"]),
                redundancy_peer=float(peer["redundancy_peer"]),
                redundancy_peer_norm=float(peer["redundancy_peer_norm"]),
                peer_cover=float(peer["peer_cover"]),
                dependence_risk=float(peer["dependence_risk"]),
                core_proxy=float(protection["core_proxy"]),
                identity_risk_proxy=float(protection["identity_risk_proxy"]),
                minutes_squeeze_proxy=float(protection["minutes_squeeze_proxy"]),
                contract_pressure=float(protection["contract_pressure"]),
                raw_trade_block_score=raw_trade_block_score,
                trade_block_score=trade_block_score,
                hard_protected=hard_protected,
                expendable_gate_passed=bool(enter_expendable),
                surplus_reason_flags=tuple(reason_flags),
                surplus_protection_flags=tuple(protection_flags),
                salary_m=c.salary_m,
                remaining_years=c.remaining_years,
                is_expiring=c.is_expiring,
                lock=c.lock,
                recent_signing_banned_until=c.recent_signing_banned_until,
                aggregation_banned_until=c.aggregation_banned_until,
                aggregation_solo_only=c.aggregation_solo_only,
                return_ban_teams=c.return_ban_teams,
                buckets=c.buckets,
            )

            if c.player_id in eligible_ids:
                if enter_expendable:
                    expendable_scored.append((raw_trade_block_score, players[c.player_id]))

        expendable_scored.sort(key=lambda t: (-t[0], t[1].market.total, t[1].player_id))
        expendable_ids = [c.player_id for _, c in expendable_scored[: max(0, caps.get("SURPLUS_EXPENDABLE", 0))]]

        # VETERAN_SALE (timeline mismatch centered)
        veteran_eval_by_pid: Dict[str, VeteranSaleEval] = {}
        veteran: List[Tuple[VeteranSaleEval, PlayerTradeCandidate]] = []
        for c in eligible_for_outgoing:
            eval_result = _eval_veteran_sale_candidate(c=c, ts=ts)
            veteran_eval_by_pid[c.player_id] = eval_result
            if eval_result.enter:
                veteran.append((eval_result, c))
        veteran.sort(key=lambda t: (-t[0].score, -t[1].market.now, -(t[1].snap.age or 0.0), t[1].player_id))
        veteran_ids = [c.player_id for _, c in veteran[: max(0, caps.get("VETERAN_SALE", 0))]]

        # CONSOLIDATE (BUY teams) - mid-tier by team market rank (30%~70%)
        consolidate_ids: List[str] = []
        if p in {"AGGRESSIVE_BUY", "SOFT_BUY"}:
            ranked = sorted(
                eligible_for_outgoing,
                key=lambda c: (-c.market.total, c.player_id),
            )
            if ranked:
                lo = int(len(ranked) * 0.30)
                hi = int(len(ranked) * 0.70)
                hi = max(hi, lo)
                mid = [c for i, c in enumerate(ranked) if lo <= i <= hi]
                mid.sort(key=lambda c: (-c.market.total, -c.salary_m, c.player_id))
                consolidate_ids = [c.player_id for c in mid[: max(0, caps.get("CONSOLIDATE", 0))]]

        # --- Record bucket membership into candidate objects (for debugging/consistency)
        bucket_members: Dict[BucketId, List[str]] = {
            "FILLER_BAD_CONTRACT": list(filler_bad_ids),
            "FILLER_CHEAP": list(filler_cheap_ids),
            "SURPLUS_EXPENDABLE": list(expendable_ids),
            "VETERAN_SALE": list(veteran_ids),
            "CONSOLIDATE": list(consolidate_ids),
        }
        # buckets per player (all satisfied buckets, even if excluded later by priority selection)
        buckets_by_player: Dict[str, List[BucketId]] = {}
        for b, ids in bucket_members.items():
            for pid in ids:
                buckets_by_player.setdefault(pid, []).append(b)
        # Re-create PlayerTradeCandidate with buckets populated (dataclass frozen)
        for pid, c in list(players.items()):
            bs = tuple(buckets_by_player.get(pid, []))
            if bs:
                players[pid] = PlayerTradeCandidate(
                    player_id=c.player_id,
                    team_id=c.team_id,
                    snap=c.snap,
                    market=c.market,
                    supply=c.supply,
                    top_tags=c.top_tags,
                    fit_vs_team=c.fit_vs_team,
                    surplus_score=c.surplus_score,
                    fit_vs_peers=c.fit_vs_peers,
                    misfit_peer=c.misfit_peer,
                    redundancy_peer=c.redundancy_peer,
                    redundancy_peer_norm=c.redundancy_peer_norm,
                    peer_cover=c.peer_cover,
                    dependence_risk=c.dependence_risk,
                    core_proxy=c.core_proxy,
                    identity_risk_proxy=c.identity_risk_proxy,
                    minutes_squeeze_proxy=c.minutes_squeeze_proxy,
                    contract_pressure=c.contract_pressure,
                    raw_trade_block_score=c.raw_trade_block_score,
                    trade_block_score=c.trade_block_score,
                    hard_protected=c.hard_protected,
                    expendable_gate_passed=c.expendable_gate_passed,
                    surplus_reason_flags=c.surplus_reason_flags,
                    surplus_protection_flags=c.surplus_protection_flags,
                    salary_m=c.salary_m,
                    remaining_years=c.remaining_years,
                    is_expiring=c.is_expiring,
                    lock=c.lock,
                    recent_signing_banned_until=c.recent_signing_banned_until,
                    aggregation_banned_until=c.aggregation_banned_until,
                    aggregation_solo_only=c.aggregation_solo_only,
                    return_ban_teams=c.return_ban_teams,
                    buckets=bs,
                )

        # --- Outgoing selection with priority-based de-dup
        priority = _outgoing_priority_for_posture(posture)
        selected: Set[str] = set()
        outgoing_player_ids_by_bucket: Dict[BucketId, Tuple[str, ...]] = {}
        for b in priority:
            ids = bucket_members.get(b, [])
            out: List[str] = []
            for pid in ids:
                if pid in selected:
                    continue
                out.append(pid)
                selected.add(pid)
            outgoing_player_ids_by_bucket[b] = tuple(out)

        # --- Picks (movable, locks + max_years + Stepien "safe alone")
        picks: Dict[str, PickTradeCandidate] = {}
        pick_ids_by_bucket: Dict[PickBucketId, List[str]] = {"FIRST_SAFE": [], "FIRST_SENSITIVE": [], "SECOND": []}
        draft_picks_map = getattr(provider, "draft_picks_map", {}) or {}
        for pick_id, pick_state in draft_picks_map.items():
            if not isinstance(pick_state, Mapping):
                continue
            owner_team = _canon_team_id(pick_state.get("owner_team"))
            if owner_team != tid:
                continue
            pid = str(pick_id)

            # SSOT: trade-locked picks (draft_picks.trade_locked) are not tradable assets.
            if bool(pick_state.get("trade_locked")):
                continue

            lock = _lock_info_for_asset_key(
                asset_key_value=_asset_key(PickAsset(kind="pick", pick_id=pid)),
                asset_locks=asset_locks,
                current_date=current_date,
                allow_locked_by_deal_id=allow_locked_by_deal_id,
            )
            if lock.is_locked:
                continue

            try:
                snap_pick = provider.get_pick_snapshot(pid)
            except Exception:
                continue

            within_max = bool(int(snap_pick.year) <= int(draft_year) + int(max_pick_years_ahead))
            if not within_max:
                continue

            # Never offer already-past picks as trade candidates.
            # (Used picks should be filtered out at the snapshot layer, but keep generation fail-closed.)
            if int(snap_pick.year) < int(draft_year):
                continue

            market = _market_summary_for_pick(
                pricer,
                snap_pick,
                env=env,
                pick_expectation=getattr(provider, "pick_expectations", {}).get(pid),
            )

            if int(snap_pick.round) != 1:
                cand = PickTradeCandidate(
                    pick_id=pid,
                    owner_team=tid,
                    snap=snap_pick,
                    market=market,
                    lock=lock,
                    within_max_years=within_max,
                    stepien_safe_if_traded_alone=True,
                    stepien_sensitive=False,
                    bucket="SECOND",
                )
                picks[pid] = cand
                pick_ids_by_bucket["SECOND"].append(pid)
                continue

            safe_alone = stepien.is_compliant_after(team_id=tid, outgoing_pick_ids={pid}, incoming_pick_ids=set())
            bucket: PickBucketId = "FIRST_SAFE" if safe_alone else "FIRST_SENSITIVE"
            cand = PickTradeCandidate(
                pick_id=pid,
                owner_team=tid,
                snap=snap_pick,
                market=market,
                lock=lock,
                within_max_years=within_max,
                stepien_safe_if_traded_alone=bool(safe_alone),
                stepien_sensitive=bool(not safe_alone),
                bucket=bucket,
            )
            picks[pid] = cand
            pick_ids_by_bucket[bucket].append(pid)

        # Deterministic ordering for pick buckets:
        # - FIRST_SAFE/SENSITIVE: prefer later years? no; keep by (year asc, market desc) to present near-term assets first.
        def _pick_sort_key(pid: str) -> Tuple[int, int, float, str]:
            p = picks.get(pid)
            if not p:
                return (9999, 9, -9999.0, pid)
            return (int(p.snap.year), int(p.snap.round), -float(p.market.total), pid)

        for b in list(pick_ids_by_bucket.keys()):
            pick_ids_by_bucket[b].sort(key=_pick_sort_key)

        pick_ids_by_bucket_final: Dict[PickBucketId, Tuple[str, ...]] = {
            "FIRST_SAFE": tuple(pick_ids_by_bucket["FIRST_SAFE"]),
            "FIRST_SENSITIVE": tuple(pick_ids_by_bucket["FIRST_SENSITIVE"]),
            "SECOND": tuple(pick_ids_by_bucket["SECOND"]),
        }

        # --- Swaps (existing swap_rights only)
        swaps: Dict[str, SwapTradeCandidate] = {}
        swap_ids: List[str] = []
        swap_rights_map = getattr(provider, "swap_rights_map", {}) or {}
        for swap_id, swap_state in swap_rights_map.items():
            if not isinstance(swap_state, Mapping):
                continue
            owner_team = _canon_team_id(swap_state.get("owner_team"))
            if owner_team != tid:
                continue
            if not bool(swap_state.get("active", True)):
                continue
            sid = str(swap_id)

            lock = _lock_info_for_asset_key(
                asset_key_value=_asset_key(SwapAsset(kind="swap", swap_id=sid, pick_id_a=str(swap_state.get("pick_id_a") or ""), pick_id_b=str(swap_state.get("pick_id_b") or ""))),
                asset_locks=asset_locks,
                current_date=current_date,
                allow_locked_by_deal_id=allow_locked_by_deal_id,
            )
            if lock.is_locked:
                continue

            try:
                snap_swap = provider.get_swap_snapshot(sid)
            except Exception:
                continue

            cand = SwapTradeCandidate(
                swap_id=sid,
                owner_team=tid,
                snap=snap_swap,
                lock=lock,
            )
            swaps[sid] = cand
            swap_ids.append(sid)

        swap_ids.sort()

        outgoing_by_team[tid] = TeamOutgoingCatalog(
            team_id=tid,
            player_ids_by_bucket={k: tuple(v) for k, v in outgoing_player_ids_by_bucket.items()},
            pick_ids_by_bucket=pick_ids_by_bucket_final,
            swap_ids=tuple(swap_ids),
            players=players,
            picks=picks,
            swaps=swaps,
        )

        # --- League-wide incoming indices contribution
        # Eligible incoming (by default): not locked, not recent-signing banned.
        for c in players.values():
            if c.team_id != tid:
                continue
            if c.lock.is_locked:
                continue
            if c.recent_signing_banned_until:
                d_ban = _parse_iso_date(c.recent_signing_banned_until)
                if d_ban is not None and current_date < d_ban:
                    continue
            # League-wide all-player index (BUY global retrieval source)
            best_tag = ""
            best_strength = 0.0
            try:
                supply_items = [(str(k), float(v or 0.0)) for k, v in (c.supply or {}).items()]
            except Exception:
                supply_items = []
            if supply_items:
                supply_items.sort(key=lambda kv: (-kv[1], kv[0]))
                best_tag = str(supply_items[0][0])
                best_strength = float(supply_items[0][1])
            supply_items_t: List[Tuple[str, float]] = []
            for k, v in supply_items:
                vv = _safe_float(v, 0.0)
                if vv <= 0.0:
                    continue
                supply_items_t.append((str(k), float(vv)))

            all_ref = IncomingPlayerRef(
                player_id=c.player_id,
                from_team=c.team_id,
                tag=best_tag,
                tag_strength=best_strength,
                market_total=float(c.market.total),
                salary_m=float(c.salary_m),
                remaining_years=float(c.remaining_years),
                age=c.snap.age,
                basketball_total=float((incoming_player_value_by_id.get(c.player_id) or {}).get("basketball_total", 0.0) or 0.0),
                contract_total=float((incoming_player_value_by_id.get(c.player_id) or {}).get("contract_total", 0.0) or 0.0),
                contract_gap_cap_share=float((incoming_player_value_by_id.get(c.player_id) or {}).get("contract_gap_cap_share", 0.0) or 0.0),
                expected_cap_share_avg=float((incoming_player_value_by_id.get(c.player_id) or {}).get("expected_cap_share_avg", 0.0) or 0.0),
                actual_cap_share_avg=float((incoming_player_value_by_id.get(c.player_id) or {}).get("actual_cap_share_avg", 0.0) or 0.0),
                supply_items=tuple(supply_items_t),
            )
            incoming_all_players_by_id[c.player_id] = all_ref


    # --- Finalize league-wide incoming index
    incoming_all_players: List[IncomingPlayerRef] = list(incoming_all_players_by_id.values())
    incoming_all_players.sort(key=lambda r: (-r.market_total, r.salary_m, -r.remaining_years, r.player_id))

    return TradeAssetCatalog(
        db_path=db_path,
        built_for_date=current_date,
        season_year=int(season_year),
        draft_year=int(draft_year),
        trade_rules=dict(trade_rules),
        outgoing_by_team=outgoing_by_team,
        incoming_all_players=tuple(incoming_all_players),
        stepien=stepien,
    )
