from __future__ import annotations

"""
draft/ai_needs.py

Draft AI policy that blends:
- BPA/consensus anchoring (especially for top picks)
- team needs / fit (via FitEngine + DecisionContext.need_map)
- upside (Potential points) vs win-now (OVR / production)
- GM personality (risk tolerance, star focus, system fit priority)
- controlled randomness (softmax sampling with temperature)

This module is intentionally self-contained and "read-only" with respect to the DB:
it reads TeamSituation + GM profiles to compute a DecisionContext, then chooses from
DraftPool.list_available().

Integration expectation
-----------------------
- draft/ai.py has been updated to provide:
    - DraftAISelection
    - DraftAIPolicy.choose(pool, ctx) -> DraftAISelection
- draft/engine.py passes DraftAIContext.meta with:
    - db_path (str)
    - rng_seed (int)
    - total_picks (int)
  Optionally:
    - debug (bool)  # include heavier breakdown info
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Set
import math
import random

from .ai import DraftAIPolicy, DraftAIContext, DraftAISelection
from .pool import DraftPool, Prospect

from derived_formulas import compute_derived

from league_repo import LeagueRepo
from decision_context import build_decision_context, gm_traits_from_profile_json

from data.team_situation import build_team_situation_context, TeamSituationEvaluator

from trades.valuation.fit_engine import FitEngine, FitEngineConfig
from trades.valuation.types import PlayerSnapshot


# -----------------------------------------------------------------------------
# Small math helpers
# -----------------------------------------------------------------------------

def _clamp01(x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x



_SENSITIVE_META_KEYS: Set[str] = {
    # Fog-of-war: do not expose internal ratings/potential signals to user-facing meta.
    "ovr",
    "attrs",
    "Potential",
    "potential",
    "potential_points",
    "potential_grade",
}


def _scrub_sensitive_keys(obj: Any) -> Any:
    """Recursively remove sensitive keys from dict/list structures.

    NOTE: This is defensive. We still intentionally avoid placing these keys into meta_out,
    but breakdown sub-objects (e.g., FitEngine breakdown) could carry unexpected keys.
    """
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            ks = str(k)
            if ks in _SENSITIVE_META_KEYS:
                continue
            out[ks] = _scrub_sensitive_keys(v)
        return out
    if isinstance(obj, list):
        return [_scrub_sensitive_keys(v) for v in obj]
    return obj


def _z(x: float, mean: float, std: float, eps: float = 1e-6) -> float:
    s = std if std > eps else eps
    return (x - mean) / s


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _pick_progress(overall_no: int, total_picks: int) -> float:
    if total_picks <= 1:
        return 0.0
    return _clamp01((float(overall_no) - 1.0) / (float(total_picks) - 1.0))


def _softmax_pick(rng: random.Random, items: List[Tuple[str, float]], temperature: float) -> str:
    """Pick one item from [(id, score), ...] proportional to exp(score / T)."""
    t = max(1e-4, float(temperature))
    m = max(s for _, s in items)
    weights = [math.exp((s - m) / t) for _, s in items]
    tot = sum(weights)
    r = rng.random() * tot
    acc = 0.0
    for (tid, _), w in zip(items, weights):
        acc += w
        if acc >= r:
            return tid
    return items[0][0]


# -----------------------------------------------------------------------------
# FitEngine supply extraction (derived -> canonical tags)
# -----------------------------------------------------------------------------

def _derived_to_supply(derived: Dict[str, Any]) -> Dict[str, float]:
    """Convert derived_formulas outputs (0..100-ish) into FitEngine supply tags (0..1)."""

    def n(key: str) -> float:
        try:
            v = float(derived.get(key, 0.0) or 0.0)
        except Exception:
            return 0.0
        # derived are usually 0..100
        if v > 1.5:
            v = v / 100.0
        return _clamp01(v)

    spacing = max(n("SHOT_3_CS"), n("SHOT_3_OD"))
    rim = max(n("FIN_RIM"), n("FIN_DUNK"), n("FIN_CONTACT"))
    initiator = max(n("PASS_CREATE"), n("PNR_READ"), n("HANDLE_SAFE"))
    creation = max(n("SHOT_MID_PU"), n("SHOT_3_OD"), n("DRIVE_CREATE"))
    defense = max(n("DEF_POA"), n("DEF_HELP"), n("DEF_STEAL"), n("DEF_RIM"))

    out: Dict[str, float] = {}
    if spacing > 0.0:
        out["SPACING"] = spacing
    if rim > 0.0:
        out["RIM_PRESSURE"] = rim
    if initiator > 0.0:
        out["PRIMARY_INITIATOR"] = initiator
    if creation > 0.0:
        out["SHOT_CREATION"] = creation
    if defense > 0.0:
        out["DEFENSE"] = defense
    return out


def draft_supply_extractor(snap: PlayerSnapshot) -> Dict[str, float]:
    """FitEngineConfig.custom_player_supply_extractor hook."""
    meta = snap.meta if isinstance(snap.meta, dict) else {}
    derived = meta.get("derived")
    if not isinstance(derived, dict) or not derived:
        # fallback: derive from attrs
        try:
            derived = compute_derived(snap.attrs or {})
        except Exception:
            derived = {}
    return _derived_to_supply(derived if isinstance(derived, dict) else {})


def prospect_to_snapshot(
    p: Prospect,
    *,
    team_id: str,
    derived_cache: Optional[Dict[str, Dict[str, Any]]] = None,
) -> PlayerSnapshot:
    """Convert a draft Prospect to PlayerSnapshot for FitEngine."""
    tid = str(p.temp_id)
    if derived_cache is not None and tid in derived_cache:
        derived = derived_cache[tid]
    else:
        try:
            derived = compute_derived(p.attrs or {})
        except Exception:
            derived = {}
        if derived_cache is not None:
            derived_cache[tid] = derived

    return PlayerSnapshot(
        kind="player",
        player_id=str(p.temp_id),
        name=str(p.name),
        pos=str(p.pos),
        age=float(p.age),
        ovr=float(p.ovr),
        team_id=str(team_id),
        salary_amount=None,
        attrs=dict(p.attrs or {}),
        contract=None,
        meta={"derived": derived},
    )


# -----------------------------------------------------------------------------
# Scoring: pooling normalization & production composite
# -----------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _PoolNorm:
    ovr_mean: float
    ovr_std: float
    pot_mean: float
    pot_std: float
    prod_mean: float
    prod_std: float


def _prod_raw(p: Prospect) -> float:
    """Lightweight production composite from Prospect.meta['season_stats']."""
    meta = p.meta if isinstance(p.meta, dict) else {}
    s = meta.get("season_stats")
    if not isinstance(s, dict):
        return 0.0

    # Common keys (defensive parsing)
    try:
        pts = float(s.get("pts") or s.get("points") or 0.0)
    except Exception:
        pts = 0.0

    try:
        ts = float(s.get("ts_pct") or s.get("ts") or 0.0)
    except Exception:
        ts = 0.0

    try:
        usg = float(s.get("usg") or s.get("usg_pct") or 0.0)
    except Exception:
        usg = 0.0

    # scale normalization (0..1 vs 0..100)
    if ts > 1.5:
        ts = ts / 100.0
    if usg > 1.5:
        usg = usg / 100.0

    # Simple composite: scoring + efficiency + involvement
    return pts * 0.60 + (ts * 100.0) * 0.30 + (usg * 100.0) * 0.10


def _mean_std(xs: List[float]) -> Tuple[float, float]:
    if not xs:
        return 0.0, 1.0
    m = sum(xs) / len(xs)
    if len(xs) <= 1:
        return m, 1.0
    v = sum((x - m) * (x - m) for x in xs) / max(1, len(xs) - 1)
    s = math.sqrt(v) if v > 1e-8 else 1.0
    return m, s


def _build_consensus_rank(pool: DraftPool) -> Dict[str, int]:
    out: Dict[str, int] = {}
    ranked = pool.ranked_temp_ids or []
    for i, tid in enumerate(ranked):
        out[str(tid)] = int(i)  # 0-based
    return out


# -----------------------------------------------------------------------------
# Reach + temperature rules (pick slot × GM personality)
# -----------------------------------------------------------------------------

def _reach_limit(overall_no: int, gm_traits: Any) -> int:
    # Base policy: allow small reach at top, larger later.
    if overall_no <= 3:
        base = 2
    elif overall_no <= 10:
        base = 4
    elif overall_no <= 20:
        base = 7
    elif overall_no <= 30:
        base = 10
    else:
        base = 20

    r = float(getattr(gm_traits, "risk_tolerance", 0.5) or 0.5)
    s = float(getattr(gm_traits, "star_focus", 0.5) or 0.5)
    cw = float(getattr(gm_traits, "competitive_window", 0.5) or 0.5)

    # Risk/star -> more reach; win-now -> less reach
    mult = (1.0 + 0.70 * r + 0.40 * s) * (1.15 - 0.30 * cw)
    return max(1, int(round(base * mult)))


def _temperature(pick_prog: float, gm_traits: Any) -> float:
    r = float(getattr(gm_traits, "risk_tolerance", 0.5) or 0.5)
    s = float(getattr(gm_traits, "star_focus", 0.5) or 0.5)
    base = 0.25 * (1.0 + 1.20 * r + 0.40 * s)
    return base * _lerp(0.70, 1.40, pick_prog)


def _seed_for_pick(rng_seed: int, overall_no: int, team_id: str) -> int:
    h = 0
    for ch in str(team_id):
        h = (h * 131 + ord(ch)) & 0x7FFFFFFF
    return int(rng_seed) * 1000003 + int(overall_no) * 9176 + h


# -----------------------------------------------------------------------------
# Policy implementation
# -----------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _TeamCtx:
    team_id: str
    gm_traits: Any
    decision_context: Any
    need_map: Dict[str, float]


class NeedsPotentialGmPolicy(DraftAIPolicy):
    """Draft AI policy using TeamSituation + GM profile + FitEngine."""

    def __init__(self) -> None:
        self._db_path: Optional[str] = None

        self._ts_ctx = None
        self._ts_eval: Optional[TeamSituationEvaluator] = None

        self._team_cache: Dict[str, _TeamCtx] = {}

        self._cons_rank: Dict[str, int] = {}
        self._pool_norm: Optional[_PoolNorm] = None
        self._pool_size: int = -1

        self._derived_cache: Dict[str, Dict[str, Any]] = {}

        self._fit_engine = FitEngine(FitEngineConfig(custom_player_supply_extractor=draft_supply_extractor))

    # ---- internal: (re)initialize caches when db/pool changes

    def _ensure_world(self, *, db_path: str, pool: DraftPool) -> None:
        db_path = str(db_path or "")
        if self._db_path != db_path:
            self._db_path = db_path
            self._ts_ctx = build_team_situation_context(db_path=self._db_path)
            self._ts_eval = TeamSituationEvaluator(ctx=self._ts_ctx, db_path=self._db_path)
            self._team_cache.clear()
            self._derived_cache.clear()
            self._cons_rank.clear()
            self._pool_norm = None
            self._pool_size = -1

        # Refresh pool-based caches when pool changed.
        size = len(pool.prospects_by_temp_id or {})
        if self._pool_size != size or not self._cons_rank:
            self._pool_size = size
            self._cons_rank = _build_consensus_rank(pool)
            self._pool_norm = None  # force re-norm

        if self._pool_norm is None:
            ovrs: List[float] = []
            pots: List[float] = []
            prods: List[float] = []
            for p in (pool.prospects_by_temp_id or {}).values():
                ovrs.append(float(p.ovr))
                meta = p.meta if isinstance(p.meta, dict) else {}
                try:
                    pots.append(float(meta.get("potential_points") or 0.0))
                except Exception:
                    pots.append(0.0)
                prods.append(float(_prod_raw(p)))

            o_m, o_s = _mean_std(ovrs)
            p_m, p_s = _mean_std(pots)
            d_m, d_s = _mean_std(prods)
            self._pool_norm = _PoolNorm(o_m, o_s, p_m, p_s, d_m, d_s)

    def _team_context(self, team_id: str) -> _TeamCtx:
        tid = str(team_id)
        cached = self._team_cache.get(tid)
        if cached is not None:
            return cached

        # GM traits
        prof: Dict[str, Any] = {}
        try:
            with LeagueRepo(self._db_path) as repo:
                got = repo.get_gm_profile(tid)
            if isinstance(got, dict):
                prof = got
        except Exception:
            prof = {}
        gm_traits = gm_traits_from_profile_json(prof or {})

        # TeamSituation (fallback-safe)
        try:
            assert self._ts_eval is not None
            team_sit = self._ts_eval.evaluate_team(tid)
        except Exception:
            class _DummySituation:  # minimal shape for decision_context
                team_id = tid
                needs = []
                trade_posture = "STAND_PAT"
                time_horizon = "RE_TOOL"
                competitive_tier = "FRINGE"
                urgency = 0.5
                preferences = {}
                apron_status = "NONE"
                hard_flags = {}
                locks_count = 0
            team_sit = _DummySituation()

        # DecisionContext (need_map + knobs)
        dec_ctx = build_decision_context(team_situation=team_sit, gm_traits=gm_traits, team_id=tid)

        need_map = dict(getattr(dec_ctx, "need_map", {}) or {})
        tc = _TeamCtx(team_id=tid, gm_traits=gm_traits, decision_context=dec_ctx, need_map=need_map)
        self._team_cache[tid] = tc
        return tc

    # ---- main policy API

    def choose(self, pool: DraftPool, ctx: DraftAIContext) -> DraftAISelection:
        meta = ctx.meta if isinstance(ctx.meta, dict) else {}

        db_path = str(meta.get("db_path") or "")
        total_picks = int(meta.get("total_picks") or 60)
        rng_seed = int(meta.get("rng_seed") or 0)
        debug = bool(meta.get("debug") or False)

        self._ensure_world(db_path=db_path, pool=pool)
        tc = self._team_context(ctx.team_id)

        turn = ctx.turn
        overall_no = int(turn.overall_no)
        pick_prog = _pick_progress(overall_no, total_picks)

        rng = random.Random(_seed_for_pick(rng_seed, overall_no, tc.team_id))

        avail = pool.list_available()
        if not avail:
            raise RuntimeError("draft pool exhausted")

        # Candidate restriction (BPA lock behavior emerges from small candidate pool up top)
        reach = _reach_limit(overall_no, tc.gm_traits)
        cand_n = min(len(avail), max(1, overall_no + reach))
        candidates = avail[:cand_n]

        norm = self._pool_norm
        assert norm is not None

        scored: List[Tuple[Prospect, float, Dict[str, Any]]] = []
        for p in candidates:
            p_meta = p.meta if isinstance(p.meta, dict) else {}

            # Core features (z-scored within class)
            now_z = _z(float(p.ovr), norm.ovr_mean, norm.ovr_std)
            try:
                pot_points = float(p_meta.get("potential_points") or 0.0)
            except Exception:
                pot_points = 0.0
            up_z = _z(pot_points, norm.pot_mean, norm.pot_std)

            prod_raw = float(_prod_raw(p))
            prod_z = _z(prod_raw, norm.prod_mean, norm.prod_std)

            # Consensus score/risk (ranked_temp_ids is already a "big board"-like ordering)
            r = self._cons_rank.get(str(p.temp_id), 999999)
            denom = max(1.0, float(len(pool.ranked_temp_ids or []) or 1))
            cons01 = _clamp01(1.0 - (float(r) / denom))
            cons_risk = max(0.0, (float(r) - float(overall_no)) / max(1.0, float(reach)))

            # Fit (0..1, neutral around ~0.5)
            snap = prospect_to_snapshot(p, team_id=tc.team_id, derived_cache=self._derived_cache)
            fit_res = self._fit_engine.assess_player_fit(snap, tc.decision_context)
            fit01 = float(getattr(getattr(fit_res, "fit", None), "fit_score", 0.50) or 0.50)

            # Depth needs are not part of FitEngine tags; apply a small manual bonus.
            depth_bonus = 0.0
            pos = str(p.pos or "")
            nm = tc.need_map
            if "GUARD_DEPTH" in nm and ("PG" in pos or "SG" in pos or pos == "G"):
                depth_bonus = max(depth_bonus, float(nm.get("GUARD_DEPTH") or 0.0))
            if "WING_DEPTH" in nm and ("SF" in pos or "PF" in pos or pos == "F"):
                depth_bonus = max(depth_bonus, float(nm.get("WING_DEPTH") or 0.0))
            if "BIG_DEPTH" in nm and ("C" in pos or pos == "C"):
                depth_bonus = max(depth_bonus, float(nm.get("BIG_DEPTH") or 0.0))
            fit01 = _clamp01(fit01 + 0.15 * _clamp01(depth_bonus))

            # Weights from DecisionContext + GM traits
            knobs = getattr(tc.decision_context, "knobs", None)
            w_now = float(getattr(knobs, "w_now", 0.55) or 0.55)
            w_future = float(getattr(knobs, "w_future", 0.45) or 0.45)
            fit_scale = float(getattr(knobs, "fit_scale", 1.0) or 1.0)
            risk_scale = float(getattr(knobs, "risk_discount_scale", 1.0) or 1.0)

            sysfit = float(getattr(tc.gm_traits, "system_fit_priority", 0.5) or 0.5)
            risk = float(getattr(tc.gm_traits, "risk_tolerance", 0.5) or 0.5)

            # Pick-position drift: later picks care more about fit/needs; top picks care more about consensus.
            w_fit = 0.20 * fit_scale * _lerp(0.70, 1.80, pick_prog) * _lerp(0.80, 1.40, sysfit)
            w_cons = _lerp(0.80, 0.15, pick_prog) * (1.0 - 0.50 * risk)
            w_prod = 0.10 * (1.0 - 0.60 * risk)
            w_risk = 0.25 * risk_scale * (1.0 - risk)

            # Compose score
            score = (
                w_now * now_z +
                w_future * up_z +
                w_fit * ((fit01 - 0.5) * 2.0) +
                w_prod * prod_z +
                w_cons * ((cons01 * 2.0) - 1.0) -
                w_risk * (0.70 * cons_risk)
            )

            breakdown = {
                "now_z": float(now_z),
                "up_z": float(up_z),
                "prod_z": float(prod_z),
                "fit01": float(fit01),
                "cons01": float(cons01),
                "cons_risk": float(cons_risk),
                "w": {"now": w_now, "future": w_future, "fit": w_fit, "prod": w_prod, "cons": w_cons, "risk": w_risk},
            }
            if debug:
                breakdown["fit_breakdown"] = getattr(fit_res, "breakdown", None)
                breakdown["need_map"] = dict(tc.need_map)

            scored.append((p, float(score), breakdown))

        scored.sort(key=lambda x: x[1], reverse=True)
        top1 = scored[0]
        top2 = scored[1] if len(scored) > 1 else None
        gap = (top1[1] - top2[1]) if top2 else 999.0

        # BPA lock for very top picks when there's a clear separation and GM is not ultra-risky.
        method = "softmax"
        chosen = top1
        if overall_no <= 3 and gap >= 0.85 and float(getattr(tc.gm_traits, "risk_tolerance", 0.5) or 0.5) < 0.75:
            method = "bpa_lock"
            chosen = top1
        else:
            T = _temperature(pick_prog, tc.gm_traits)
            top_k = min(8, len(scored))
            chosen_tid = _softmax_pick(rng, [(str(pp.temp_id), ss) for pp, ss, _ in scored[:top_k]], T)
            for it in scored:
                if str(it[0].temp_id) == str(chosen_tid):
                    chosen = it
                    break

        p, sc, bd = chosen

        # Build AI meta for DraftPick.meta["ai"] (keep lightweight by default)
        T = _temperature(pick_prog, tc.gm_traits)
        meta_out: Dict[str, Any] = {
            "policy": "needs_potential_gm_v1",
            "method": method,
            "team_id": tc.team_id,
            "overall_no": overall_no,
            "candidate_n": int(cand_n),
            "reach_limit": int(reach),
            "pick_progress": float(pick_prog),
            "temperature": float(T),
            "chosen": {
                "temp_id": str(p.temp_id),
                "name": str(p.name),
                "pos": str(p.pos),
                "score": float(sc),
                "fit01": float(bd.get("fit01", 0.5)),
            },
            "top3": [
                {
                    "temp_id": str(pp.temp_id),
                    "name": str(pp.name),
                    "pos": str(pp.pos),
                    "score": float(ss),
                    "fit01": float(bb.get("fit01", 0.5)),
                }
                for (pp, ss, bb) in scored[:3]
            ],
        }
        if debug:
            meta_out["breakdown"] = bd
            meta_out["top5_breakdowns"] = [
                {"temp_id": str(pp.temp_id), "score": float(ss), "bd": bb} for (pp, ss, bb) in scored[:5]
            ]

        # Defensive scrub (ensures fog-of-war even if nested breakdowns contain sensitive keys).
        meta_out = _scrub_sensitive_keys(meta_out)
        return DraftAISelection(prospect_temp_id=str(p.temp_id), meta=meta_out)
