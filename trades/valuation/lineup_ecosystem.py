from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence, Tuple


_COVERAGE_REQUIRED_TAGS: Tuple[str, ...] = ("PRIMARY_INITIATOR", "SHOT_CREATION", "SPACING", "RIM_PRESSURE", "DEFENSE")


@dataclass(frozen=True, slots=True)
class EcosystemComponent:
    key: str
    score: float
    weight: float
    weighted_score: float
    reason_codes_plus: Tuple[str, ...] = tuple()
    reason_codes_minus: Tuple[str, ...] = tuple()
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EcosystemFitResult:
    total_score: float
    raw_total: float
    components: Tuple[EcosystemComponent, ...] = tuple()
    lineup_samples: Tuple[Dict[str, Any], ...] = tuple()
    reason_codes_plus: Tuple[str, ...] = tuple()
    reason_codes_minus: Tuple[str, ...] = tuple()
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _ComponentEval:
    score: float
    plus: Tuple[str, ...]
    minus: Tuple[str, ...]
    meta: Dict[str, Any]


def _clamp(x: Any, lo: float, hi: float) -> float:
    try:
        v = float(x)
    except Exception:
        v = lo
    if v < lo:
        return float(lo)
    if v > hi:
        return float(hi)
    return float(v)


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _player_id(c: Any) -> str:
    return str(getattr(c, "player_id", "") or "")


def _market_total(c: Any) -> float:
    return _safe_float(getattr(getattr(c, "market", None), "total", 0.0), 0.0)


def _fit_vs_team(c: Any) -> float:
    return _safe_float(getattr(c, "fit_vs_team", 0.0), 0.0)


def _candidate_supply(c: Any) -> Dict[str, float]:
    raw = getattr(c, "supply", None)
    if not isinstance(raw, Mapping):
        return {}
    out: Dict[str, float] = {}
    for k, v in raw.items():
        kk = str(k or "").upper()
        if not kk:
            continue
        out[kk] = _clamp(v, 0.0, 1.0)
    return out


def _resolve_need_map(tick_ctx: Any, team_id: str) -> Dict[str, float]:
    tid = str(team_id).upper()
    try:
        dc = tick_ctx.get_decision_context(tid)
        nm = getattr(dc, "need_map", None)
        if isinstance(nm, Mapping):
            out = {str(k).upper(): _safe_float(v, 0.0) for k, v in nm.items() if str(k or "")}
            if out:
                return out
    except Exception:
        pass

    # fallback: team_situation.needs (dict 또는 list)
    try:
        ts = tick_ctx.get_team_situation(tid)
        needs = getattr(ts, "needs", None)
        if isinstance(needs, Mapping):
            return {str(k).upper(): _safe_float(v, 0.0) for k, v in needs.items() if str(k or "")}
        if isinstance(needs, (list, tuple)):
            out: Dict[str, float] = {}
            for n in needs:
                tag = str(getattr(n, "tag", "") or "").upper()
                if not tag:
                    continue
                out[tag] = _safe_float(getattr(n, "weight", 0.0), 0.0)
            return out
    except Exception:
        pass

    return {}


def _receiver_roster_candidates(tick_ctx: Any, team_id: str) -> List[Any]:
    tid = str(team_id).upper()
    try:
        cat = getattr(tick_ctx, "asset_catalog", None)
        if cat is not None:
            team_out = getattr(cat, "outgoing_by_team", {}).get(tid)
            if team_out is not None:
                players = getattr(team_out, "players", None)
                if isinstance(players, Mapping):
                    return [v for v in players.values() if v is not None]
    except Exception:
        pass
    return []


def _merge_roster(
    base_roster: Sequence[Any],
    *,
    incoming_candidates: Sequence[Any],
    outgoing_player_ids: Sequence[str],
) -> List[Any]:
    outgoing = {str(x) for x in (outgoing_player_ids or tuple()) if str(x)}
    merged = [c for c in base_roster if _player_id(c) and _player_id(c) not in outgoing]
    existing = {_player_id(c) for c in merged}
    for c in incoming_candidates:
        pid = _player_id(c)
        if not pid:
            continue
        if pid in existing:
            continue
        merged.append(c)
        existing.add(pid)
    return merged


def _team_supply(roster: Sequence[Any]) -> Dict[str, float]:
    agg: Dict[str, float] = {}
    cnt: Dict[str, int] = {}
    for c in roster:
        for t, s in _candidate_supply(c).items():
            agg[t] = agg.get(t, 0.0) + float(s)
            cnt[t] = cnt.get(t, 0) + 1
    out: Dict[str, float] = {}
    for t, v in agg.items():
        d = max(1, int(cnt.get(t, 1)))
        out[t] = float(v / d)
    return out


def _build_lineup_samples(roster: Sequence[Any]) -> Tuple[Dict[str, Any], ...]:
    rows = [c for c in roster if _player_id(c)]
    if not rows:
        return tuple()

    by_market = sorted(rows, key=lambda c: (_market_total(c), _fit_vs_team(c), _player_id(c)), reverse=True)
    by_closing = sorted(rows, key=lambda c: (_fit_vs_team(c), _market_total(c), _player_id(c)), reverse=True)

    starting = by_market[:5]
    closing = by_closing[:5]
    second = [c for c in by_market if _player_id(c) not in {_player_id(x) for x in starting}][:5]

    def _sample(name: str, players: Sequence[Any], weight: float) -> Dict[str, Any]:
        return {
            "sample": name,
            "weight": float(weight),
            "player_ids": tuple(_player_id(c) for c in players if _player_id(c)),
            "supply": _team_supply(players),
        }

    return (
        _sample("starting_5_estimate", starting, 0.45),
        _sample("closing_5_estimate", closing, 0.35),
        _sample("second_unit_core", second, 0.20),
    )


def _complementarity_gain(before_supply: Mapping[str, float], after_supply: Mapping[str, float], need_map: Mapping[str, float]) -> _ComponentEval:
    if not need_map:
        return _ComponentEval(0.0, tuple(), tuple(), {"reason": "empty_need_map"})

    rows: List[Tuple[str, float]] = []
    num = 0.0
    den = 0.0
    for tag, weight in need_map.items():
        w = max(0.0, _safe_float(weight))
        if w <= 0:
            continue
        before_gap = max(0.0, w - _safe_float(before_supply.get(tag), 0.0))
        after_gap = max(0.0, w - _safe_float(after_supply.get(tag), 0.0))
        delta = before_gap - after_gap
        num += w * delta
        den += w
        rows.append((str(tag), float(delta)))
    score = _clamp((num / den) if den > 0 else 0.0, -1.0, 1.0)

    plus: List[str] = []
    minus: List[str] = []
    top_up = sorted(rows, key=lambda x: x[1], reverse=True)[:2]
    top_dn = sorted(rows, key=lambda x: x[1])[:2]
    if score >= 0.15:
        for t, v in top_up:
            if v > 0.02:
                plus.append(f"FIT_ECO_COMP_{t}_RELIEF")
    if score <= -0.10:
        for t, v in top_dn:
            if v < -0.02:
                minus.append(f"FIT_ECO_COMP_{t}_WORSEN")
    if not plus and score > 0:
        plus.append("FIT_ECO_COMP_LOW_NEED_IMPACT")
    return _ComponentEval(score, tuple(plus), tuple(minus), {"delta_by_tag": dict(rows)})


def _redundancy_conflict(samples: Sequence[Mapping[str, Any]], need_map: Mapping[str, float]) -> _ComponentEval:
    penalties: List[float] = []
    for s in samples:
        sup = s.get("supply") if isinstance(s, Mapping) else None
        if not isinstance(sup, Mapping):
            continue
        p = 0.0
        # low-need concentration
        for tag, val in sup.items():
            need = _safe_float(need_map.get(str(tag), 0.0), 0.0)
            vv = _safe_float(val, 0.0)
            if need <= 0.25 and vv >= 0.65:
                p += (vv - 0.65) * (0.5 - need)
        # initiator crowding
        init = _safe_float(sup.get("PRIMARY_INITIATOR", 0.0), 0.0)
        create = _safe_float(sup.get("SHOT_CREATION", 0.0), 0.0)
        if (init + create) >= 1.35:
            p += ((init + create) - 1.35) * 0.8
        penalties.append(p)

    raw = sum(penalties) / max(1, len(penalties))
    score = _clamp(raw, 0.0, 1.0)
    plus: List[str] = []
    minus: List[str] = []
    if score <= 0.15:
        plus.append("FIT_ECO_REDUNDANCY_AVOIDED")
    if score >= 0.35:
        minus.append("FIT_ECO_REDUNDANCY_INITIATOR_CROWD")
    return _ComponentEval(score, tuple(plus), tuple(minus), {"penalties": penalties})


def _star_synergy(before_roster: Sequence[Any], after_roster: Sequence[Any], need_map: Mapping[str, float]) -> _ComponentEval:
    # core proxy: receiver roster market 상위 3인
    core = sorted(before_roster, key=lambda c: (_market_total(c), _fit_vs_team(c), _player_id(c)), reverse=True)[:3]
    if not core:
        return _ComponentEval(0.0, tuple(), tuple(), {"reason": "no_core_proxy"})

    def _support_score(roster: Sequence[Any], core_ids: set[str]) -> float:
        teammates = [c for c in roster if _player_id(c) and _player_id(c) not in core_ids]
        if not teammates:
            return 0.0
        supply = _team_supply(teammates)
        spacing = _safe_float(supply.get("SPACING", 0.0), 0.0)
        rim = _safe_float(supply.get("RIM_PRESSURE", 0.0), 0.0)
        defense = _safe_float(supply.get("DEFENSE", 0.0), 0.0)
        need_bias = _safe_float(need_map.get("SPACING", 0.0), 0.0) + _safe_float(need_map.get("RIM_PRESSURE", 0.0), 0.0)
        return (0.45 * spacing) + (0.35 * rim) + (0.20 * defense) + (0.10 * need_bias)

    core_ids = {_player_id(c) for c in core if _player_id(c)}
    before = _support_score(before_roster, core_ids)
    after = _support_score(after_roster, core_ids)
    score = _clamp((after - before) * 1.6, -1.0, 1.0)

    plus: List[str] = []
    minus: List[str] = []
    if score >= 0.20:
        plus.append("FIT_ECO_STAR_PRIMARY_RELIEF")
    if score <= -0.12:
        minus.append("FIT_ECO_STAR_POOR_MATCH")
    return _ComponentEval(score, tuple(plus), tuple(minus), {"before": before, "after": after})


def _touches_friction(samples: Sequence[Mapping[str, Any]]) -> _ComponentEval:
    frictions: List[float] = []
    for s in samples:
        sup = s.get("supply") if isinstance(s, Mapping) else None
        if not isinstance(sup, Mapping):
            continue
        onball = _safe_float(sup.get("PRIMARY_INITIATOR", 0.0), 0.0) + _safe_float(sup.get("SHOT_CREATION", 0.0), 0.0)
        offball = _safe_float(sup.get("SPACING", 0.0), 0.0)
        friction = max(0.0, onball - 1.20) - (0.25 * offball)
        frictions.append(max(0.0, friction))

    raw = sum(frictions) / max(1, len(frictions))
    score = _clamp(raw, 0.0, 1.0)
    plus: List[str] = []
    minus: List[str] = []
    if score <= 0.12:
        plus.append("FIT_ECO_TOUCHES_BALANCED")
    if score >= 0.30:
        minus.append("FIT_ECO_TOUCHES_USAGE_CLASH")
    return _ComponentEval(score, tuple(plus), tuple(minus), {"frictions": frictions})


def _coverage_resilience(samples: Sequence[Mapping[str, Any]], need_map: Mapping[str, float]) -> _ComponentEval:
    if not samples:
        return _ComponentEval(0.0, tuple(), tuple(), {"reason": "no_samples"})

    scen_scores: List[float] = []
    for s in samples:
        sup = s.get("supply") if isinstance(s, Mapping) else None
        if not isinstance(sup, Mapping):
            continue
        num = 0.0
        den = 0.0
        for tag in _COVERAGE_REQUIRED_TAGS:
            weight = max(0.2, _safe_float(need_map.get(tag, 0.0), 0.0))
            cov = _clamp(_safe_float(sup.get(tag, 0.0), 0.0), 0.0, 1.0)
            num += weight * cov
            den += weight
        scen_scores.append((num / den) if den > 0 else 0.0)

    if not scen_scores:
        return _ComponentEval(0.0, tuple(), tuple(), {"reason": "empty_coverage_scores"})

    mean_score = sum(scen_scores) / len(scen_scores)
    bottleneck = min(scen_scores)
    score = _clamp((0.65 * mean_score) + (0.35 * bottleneck), 0.0, 1.0)

    plus: List[str] = []
    minus: List[str] = []
    if score >= 0.62:
        plus.append("FIT_ECO_COVERAGE_BENCH_STABLE")
    if score <= 0.38:
        minus.append("FIT_ECO_COVERAGE_NO_POA_BACKUP")
    return _ComponentEval(score, tuple(plus), tuple(minus), {"scenario_scores": scen_scores})


def compute_ecosystem_fit_score(
    *,
    receiver_team_id: str,
    incoming_candidates: Sequence[Any],
    outgoing_player_ids: Sequence[str],
    tick_ctx: Any,
    cfg: Any,
) -> EcosystemFitResult:
    """Compute lineup-ecosystem fit score for fit-swap/counter ranking.

    Design constraints:
    - consume only existing project signals (candidate supply/top_tags/market/fit, need_map, team_situation posture)
    - no external data/model assumptions
    """
    tid = str(receiver_team_id).upper()
    need_map = _resolve_need_map(tick_ctx, tid)

    base_roster = _receiver_roster_candidates(tick_ctx, tid)
    before_roster = _merge_roster(base_roster, incoming_candidates=tuple(), outgoing_player_ids=outgoing_player_ids)
    after_roster = _merge_roster(base_roster, incoming_candidates=incoming_candidates, outgoing_player_ids=outgoing_player_ids)

    before_supply = _team_supply(before_roster)
    after_supply = _team_supply(after_roster)
    samples = _build_lineup_samples(after_roster)

    c1 = _complementarity_gain(before_supply, after_supply, need_map)
    c2 = _redundancy_conflict(samples, need_map)
    c3 = _star_synergy(before_roster, after_roster, need_map)
    c4 = _touches_friction(samples)
    c5 = _coverage_resilience(samples, need_map)

    mode = "NEUTRAL"
    try:
        ts = tick_ctx.get_team_situation(tid)
        horizon = str(getattr(ts, "time_horizon", "") or "").upper()
        posture = str(getattr(ts, "trade_posture", "") or "").upper()
        tier = str(getattr(ts, "competitive_tier", "") or "").upper()
        if horizon in {"REBUILD", "RE_TOOL", "RETOOL"} or tier in {"REBUILD", "RESET", "TANK"}:
            mode = "REBUILD"
        elif horizon in {"WIN_NOW", "CONTEND", "COMPETE"} or posture in {"AGGRESSIVE_BUY", "SOFT_BUY"} or tier in {"CONTENDER", "PLAYOFF_BUYER"}:
            mode = "WIN_NOW"
    except Exception:
        mode = "NEUTRAL"

    w1, w2, w3, w4, w5 = 0.30, 0.20, 0.20, 0.15, 0.15
    if mode == "WIN_NOW":
        w3 *= 1.10
        w5 *= 1.10
    elif mode == "REBUILD":
        w4 *= 0.90
        w5 *= 1.10

    weighted_1 = w1 * c1.score
    weighted_2 = -w2 * c2.score
    weighted_3 = w3 * c3.score
    weighted_4 = -w4 * c4.score
    weighted_5 = w5 * c5.score
    raw_total = _clamp(weighted_1 + weighted_2 + weighted_3 + weighted_4 + weighted_5, -1.0, 1.0)
    total_score = _clamp((raw_total + 1.0) / 2.0, 0.0, 1.0)

    comps = (
        EcosystemComponent("complementarity_gain", c1.score, w1, weighted_1, c1.plus, c1.minus, c1.meta),
        EcosystemComponent("redundancy_conflict", c2.score, w2, weighted_2, c2.plus, c2.minus, c2.meta),
        EcosystemComponent("star_synergy", c3.score, w3, weighted_3, c3.plus, c3.minus, c3.meta),
        EcosystemComponent("touches_friction", c4.score, w4, weighted_4, c4.plus, c4.minus, c4.meta),
        EcosystemComponent("coverage_resilience", c5.score, w5, weighted_5, c5.plus, c5.minus, c5.meta),
    )

    plus: List[str] = []
    minus: List[str] = []
    for c in comps:
        plus.extend(list(c.reason_codes_plus))
        minus.extend(list(c.reason_codes_minus))

    return EcosystemFitResult(
        total_score=total_score,
        raw_total=raw_total,
        components=comps,
        lineup_samples=tuple(dict(x) for x in samples),
        reason_codes_plus=tuple(dict.fromkeys(plus)),
        reason_codes_minus=tuple(dict.fromkeys(minus)),
        meta={
            "receiver_team_id": tid,
            "mode": mode,
            "need_map_size": len(need_map),
            "before_roster_size": len(before_roster),
            "after_roster_size": len(after_roster),
        },
    )
