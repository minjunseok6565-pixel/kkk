from __future__ import annotations

"""package_effects.py

Deal-level (package-level) valuation adjustments.

Why this module exists
----------------------
team_utility.py intentionally operates at *single-asset* granularity.
However, a trade is a *package* of assets, and several realistic effects cannot
be captured by per-player fit/needs matching:

  5) Consolidation / dispersion structure (many-for-1, 1-for-many)
  6) Diminishing returns for redundant incoming players (similar roles/positions)
  7) Soft roster slot / rotation limit waste (too many incoming players)
  8) Outgoing "hole" penalty (sharp loss in guard/wing/big resources)

This module consumes DecisionContext.need_map (already computed by team_situation)
and only measures the *delta from the deal*, never re-evaluating the team.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from decision_context import DecisionContext

from .env import ValuationEnv

try:  # path safety (project integration will likely use relative import)
    from need_attr_profiles import ALL_NEW_NEED_TAGS, tag_supply
except Exception:  # pragma: no cover
    from need_attr_profiles import ALL_NEW_NEED_TAGS, tag_supply

from .types import (
    AssetKind,
    AssetSnapshot,
    PlayerSnapshot,
    TeamValuation,
    ValueComponents,
    ValuationStep,
    ValuationStage,
    StepMode,
)


# -----------------------------------------------------------------------------
# Constants (need tags)
# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# Helpers (pure)
# -----------------------------------------------------------------------------
def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _clamp(x: float, lo: float, hi: float) -> float:
    xf = _safe_float(x, lo)
    if xf < lo:
        return float(lo)
    if xf > hi:
        return float(hi)
    return float(xf)


def _vc(now: float = 0.0, future: float = 0.0) -> ValueComponents:
    return ValueComponents(float(now), float(future))


def _split_total_to_components(total_delta: float, *, w_now: float, w_future: float) -> ValueComponents:
    """Split a scalar delta into ValueComponents using weights."""
    wn = max(0.0, float(w_now))
    wf = max(0.0, float(w_future))
    s = wn + wf
    if s <= 1e-9:
        return _vc(total_delta, 0.0)
    return _vc(total_delta * (wn / s), total_delta * (wf / s))


def _pos_tokens(pos: Optional[str]) -> List[str]:
    if not pos:
        return []
    s = str(pos).upper().replace(" ", "")
    # common formats: "PG", "SG/SF", "PF,C" etc.
    for sep in [",", "|", ";"]:
        s = s.replace(sep, "/")
    parts = [p for p in s.split("/") if p]
    return parts


def _infer_frontcourt_by_height(attrs: Mapping[str, Any]) -> Optional[str]:
    """Fallback: classify by height if pos is missing."""
    if not isinstance(attrs, Mapping):
        return None
    for k in ("height_in", "height_inches", "HeightInches", "HEIGHT_IN", "HEIGHT_INCHES"):
        if k in attrs:
            h = _safe_float(attrs.get(k), 0.0)
            if h >= 82:  # ~6'10
                return "BIG"
            if h >= 79:  # ~6'7
                return "WING"
            return "GUARD"
    return None


def classify_depth_buckets(player: PlayerSnapshot) -> List[str]:
    """Return one or more of {GUARD,WING,BIG} for depth accounting."""
    tokens = _pos_tokens(player.pos)
    out: List[str] = []

    if any(t in ("PG", "SG", "G") for t in tokens):
        out.append("GUARD")
    if any(t in ("SF", "WF", "F") for t in tokens):
        out.append("WING")
    if any(t in ("PF", "C", "FC", "B") for t in tokens):
        out.append("BIG")

    if not out:
        by_h = _infer_frontcourt_by_height(player.attrs)
        if by_h:
            out.append(by_h)

    # Still unknown -> treat as wing-ish by default (neutral)
    if not out:
        out.append("WING")
    return out


def _market_now_grade(tv: TeamValuation) -> float:
    """Rotation grade proxy: market now component (team-agnostic)."""
    return _safe_float(tv.market_value.now, 0.0)


def _team_total_grade(tv: TeamValuation) -> float:
    """Total grade proxy for ordering players in package heuristics."""
    return _safe_float(tv.team_value.total, 0.0)


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class PackageEffectsConfig:
    # --- 5) Consolidation / dispersion
    consolidation_neutral: float = 0.5  # knobs.consolidation_bias baseline
    consolidation_scale: float = 0.10   # relative strength vs package totals
    consolidation_cap_ratio: float = 0.18

    # --- 7) Soft roster slot / rotation waste
    roster_excess_waste_rate: float = 0.85  # fraction of bottom incoming players' value wasted
    roster_excess_cap_ratio: float = 0.22

    # --- 8) Outgoing hole penalty
    hole_penalty_scale: float = 0.22
    hole_penalty_exponent: float = 1.15
    hole_penalty_cap_ratio: float = 0.18

    # --- New need/supply package balance (attrs-tag based)
    need_supply_excess_scale: float = 0.90
    need_supply_excess_exponent: float = 1.35
    need_supply_excess_cap_ratio: float = 0.28

    # Need-weighted soft gating for package incoming supply contribution
    need_supply_gate_enabled: bool = True
    need_supply_gate_threshold_min: float = 0.25
    need_supply_gate_threshold_max: float = 0.45
    need_supply_gate_hard_floor_cap: float = 0.12
    need_supply_gate_soft_width: float = 0.20

    # Optional slot-efficiency bonus
    slot_efficiency_enabled: bool = True
    slot_efficiency_scale: float = 0.20
    slot_efficiency_cap_ratio: float = 0.10
    slot_efficiency_depth_stress_dampen: float = 0.45

    # --- Agency distress valuation link (trade request / grievance)
    # SSOT policy: valuation uses only trade_request_level and applies discount
    # only when the request becomes public (level >= 2).
    agency_public_trade_request_discount: float = 0.12
    # Backward-compat knobs kept for older configs; no longer used in valuation.
    agency_trade_request_weight: float = 0.08
    agency_team_frustration_weight: float = 0.08
    agency_role_frustration_weight: float = 0.05
    agency_distress_cap: float = 0.22

    eps: float = 1e-9


# -----------------------------------------------------------------------------
# Engine
# -----------------------------------------------------------------------------
@dataclass(slots=True)
class PackageEffects:
    """Compute deal-level adjustments for a single team perspective."""

    config: PackageEffectsConfig = field(default_factory=PackageEffectsConfig)

    def apply(
        self,
        *,
        team_id: str,
        incoming: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        outgoing: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        ctx: DecisionContext,
        env: ValuationEnv,
    ) -> Tuple[ValueComponents, Tuple[ValuationStep, ...], Dict[str, Any]]:
        """Return (package_delta, steps, meta).

        package_delta is added to the summed TeamValuation.team_value totals.
        """
        steps: List[ValuationStep] = []
        meta: Dict[str, Any] = {}

        base_in = self._sum_team_values(incoming)
        base_out = self._sum_team_values(outgoing)
        # NOTE: With negative assets enabled (bad contracts), totals can be negative or cancel out.
        # For deal-structure caps we use absolute mass (|now|+|future|) to keep scaling stable.
        def _side_mass(items: Sequence[Tuple[TeamValuation, AssetSnapshot]]) -> float:
            m = 0.0
            for tv, _ in items:
                m += abs(_safe_float(tv.team_value.now, 0.0)) + abs(_safe_float(tv.team_value.future, 0.0))
            return float(m)

        base_out_mass = max(_side_mass(outgoing), self.config.eps)
        base_in_mass = max(_side_mass(incoming), self.config.eps)
        package_scale_total = max(base_in_mass, base_out_mass)

        # 5) Consolidation / dispersion structure
        delta1 = self._consolidation_effect(incoming, outgoing, ctx, package_scale_total, steps)

        # 6) Soft roster slot / rotation waste (too many incoming players)
        delta2 = self._roster_excess_waste(incoming, outgoing, base_out_mass, steps)

        # 7) Outgoing hole penalty (position discontinuity)
        delta3 = self._outgoing_hole_penalty(incoming, outgoing, base_out_mass, steps)

        # 8) New need-supply balance adjustment (attrs-tag based)
        delta4, ns_meta = self._need_supply_balance_adjustment(
            incoming=incoming,
            outgoing=outgoing,
            ctx=ctx,
            base_out_mass=base_out_mass,
            steps=steps,
        )

        # 9) Agency distress (trade-request / grievance) impacts perceived value.
        delta5 = self._agency_distress_adjustment(incoming, outgoing, steps)

        total = delta1 + delta2 + delta3 + delta4 + delta5
        meta["base_in"] = {"now": base_in.now, "future": base_in.future, "total": base_in.total, "mass": base_in_mass}
        meta["base_out"] = {"now": base_out.now, "future": base_out.future, "total": base_out.total, "mass": base_out_mass}
        meta["package_delta"] = {"now": total.now, "future": total.future, "total": total.total}
        meta["team_id"] = str(team_id)
        if isinstance(locals().get("ns_meta"), dict):
            meta["need_supply"] = ns_meta

        return total, tuple(steps), meta

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    def _sum_team_values(self, items: Sequence[Tuple[TeamValuation, AssetSnapshot]]) -> ValueComponents:
        out = ValueComponents.zero()
        for tv, _ in items:
            out = out + tv.team_value
        return out

    def _players(
        self, items: Sequence[Tuple[TeamValuation, AssetSnapshot]]
    ) -> List[Tuple[TeamValuation, PlayerSnapshot]]:
        out: List[Tuple[TeamValuation, PlayerSnapshot]] = []
        for tv, snap in items:
            if tv.kind == AssetKind.PLAYER and isinstance(snap, PlayerSnapshot):
                out.append((tv, snap))
        return out

    # ------------------------------------------------------------------
    # 5) Consolidation / dispersion
    # ------------------------------------------------------------------
    def _consolidation_effect(
        self,
        incoming: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        outgoing: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        ctx: DecisionContext,
        package_total: float,
        steps: List[ValuationStep],
    ) -> ValueComponents:
        cfg = self.config

        in_vals = [max(_team_total_grade(tv), 0.0) for tv, _ in incoming if _team_total_grade(tv) > cfg.eps]
        out_vals = [max(_team_total_grade(tv), 0.0) for tv, _ in outgoing if _team_total_grade(tv) > cfg.eps]

        if not in_vals or not out_vals:
            return ValueComponents.zero()

        in_total = sum(in_vals)
        out_total = sum(out_vals)

        if in_total <= cfg.eps or out_total <= cfg.eps:
            return ValueComponents.zero()

        in_count = len(in_vals)
        out_count = len(out_vals)

        top_in_share = max(in_vals) / max(in_total, cfg.eps)
        top_out_share = max(out_vals) / max(out_total, cfg.eps)

        # consolidation shape: positive when this team gives many / gets concentrated
        count_shape = (out_count - in_count) / max(float(max(out_count, in_count)), 1.0)
        concentration_shape = (top_in_share - top_out_share)
        shape = _clamp(count_shape * concentration_shape, -1.0, 1.0)

        # preference: consolidation_bias in 0..1, neutral around 0.5
        pref = (_safe_float(ctx.knobs.consolidation_bias, cfg.consolidation_neutral) - cfg.consolidation_neutral) * 2.0
        pref = _clamp(pref, -1.0, 1.0)

        # star focus derived from exponent (1.0..~1.8)
        exp = _safe_float(ctx.knobs.star_premium_exponent, 1.0)
        star_focus = _clamp((exp - 1.0) / 0.8, 0.0, 1.0)

        # scale: bounded and proportional to package size
        raw = cfg.consolidation_scale * (0.60 + 0.40 * star_focus) * pref * shape * package_total
        cap = cfg.consolidation_cap_ratio * package_total
        raw = _clamp(raw, -cap, cap)

        if abs(raw) <= cfg.eps:
            return ValueComponents.zero()

        delta = _split_total_to_components(
            raw,
            w_now=_safe_float(ctx.knobs.w_now, 1.0),
            w_future=_safe_float(ctx.knobs.w_future, 1.0),
        )

        steps.append(
            ValuationStep(
                stage=ValuationStage.PACKAGE,
                mode=StepMode.ADD,
                code="CONSOLIDATION_STRUCTURE",
                label="콘솔리데이션/분산(딜 구조) 선호 보정",
                delta=delta,
                meta={
                    "pref": pref,
                    "shape": shape,
                    "count_shape": count_shape,
                    "concentration_shape": concentration_shape,
                    "incoming_count": in_count,
                    "outgoing_count": out_count,
                    "top_in_share": top_in_share,
                    "top_out_share": top_out_share,
                    "star_focus": star_focus,
                },
            )
        )
        return delta

    # ------------------------------------------------------------------
    # 6) Diminishing returns
    # ------------------------------------------------------------------

    def _roster_excess_waste(
        self,
        incoming: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        outgoing: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        base_out_total: float,
        steps: List[ValuationStep],
    ) -> ValueComponents:
        cfg = self.config
        in_players = self._players(incoming)
        out_players = self._players(outgoing)

        excess = max(0, len(in_players) - len(out_players))
        if excess <= 0:
            return ValueComponents.zero()

        # The least valuable incoming players are most likely to be waived / unused.
        in_sorted = sorted(in_players, key=lambda x: _team_total_grade(x[0]))
        targets = in_sorted[:excess]
        if not targets:
            return ValueComponents.zero()

        wasted = ValueComponents.zero()
        detail: List[Dict[str, Any]] = []
        for tv, p in targets:
            wasted = wasted + tv.team_value.scale(cfg.roster_excess_waste_rate)
            detail.append(
                {
                    "player_name": str(p.name or p.player_id),
                    "asset_key": tv.asset_key,
                    "team_value_total": tv.team_value.total,
                }
            )

        # cap penalty
        cap = cfg.roster_excess_cap_ratio * max(base_out_total, cfg.eps)
        wasted_total = _clamp(wasted.total, 0.0, cap)
        if wasted_total <= cfg.eps:
            return ValueComponents.zero()

        # rescale to match cap if needed
        scale = wasted_total / max(wasted.total, cfg.eps)
        pen = wasted.scale(scale)
        pen = _vc(-abs(pen.now), -abs(pen.future))

        steps.append(
            ValuationStep(
                stage=ValuationStage.PACKAGE,
                mode=StepMode.ADD,
                code="ROSTER_EXCESS_WASTE",
                label="로스터 슬롯/회전 한계로 인한 효용 누수(soft)",
                delta=pen,
                meta={
                    "excess_incoming_players": excess,
                    "waive_candidates": detail,
                    "waste_rate": cfg.roster_excess_waste_rate,
                },
            )
        )
        return pen

    # ------------------------------------------------------------------
    # Depth needs (structural, need_map-driven)
    # ------------------------------------------------------------------

    def _outgoing_hole_penalty(
        self,
        incoming: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        outgoing: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        base_out_total: float,
        steps: List[ValuationStep],
    ) -> ValueComponents:
        cfg = self.config

        def supply(items: Sequence[Tuple[TeamValuation, AssetSnapshot]]) -> Dict[str, float]:
            s = {"GUARD": 0.0, "WING": 0.0, "BIG": 0.0}
            for tv, snap in items:
                if tv.kind != AssetKind.PLAYER or not isinstance(snap, PlayerSnapshot):
                    continue
                grade = max(_market_now_grade(tv), 0.0)
                buckets = classify_depth_buckets(snap)
                if not buckets:
                    continue
                share = grade / max(len(buckets), 1)
                for b in buckets:
                    if b in s:
                        s[b] += share
            return s

        in_s = supply(incoming)
        out_s = supply(outgoing)

        penalties: Dict[str, float] = {}
        pen_total = 0.0
        for b in ("GUARD", "WING", "BIG"):
            delta = in_s[b] - out_s[b]
            if delta < -cfg.eps:
                p = (abs(delta) ** cfg.hole_penalty_exponent) * cfg.hole_penalty_scale
                penalties[b] = p
                pen_total += p

        cap = cfg.hole_penalty_cap_ratio * max(base_out_total, cfg.eps)
        pen_total = _clamp(pen_total, 0.0, cap)
        if pen_total <= cfg.eps:
            return ValueComponents.zero()

        delta = _vc(now=-pen_total, future=0.0)
        steps.append(
            ValuationStep(
                stage=ValuationStage.PACKAGE,
                mode=StepMode.ADD,
                code="OUTGOING_HOLE_PENALTY",
                label="아웃바운드 구멍(포지션/롤 단절) 페널티",
                delta=delta,
                meta={"penalties": penalties, "incoming_supply": in_s, "outgoing_supply": out_s},
            )
        )
        return delta

    def _resolve_need_map_new(self, ctx: DecisionContext) -> Dict[str, float]:
        need_map = dict(ctx.need_map or {})
        if not need_map and getattr(ctx, "policies", None) is not None:
            try:
                need_map = dict(ctx.policies.fit.need_map or {})
            except Exception:
                need_map = {}
        out: Dict[str, float] = {}
        for k, v in need_map.items():
            t = str(k or "").strip().upper()
            if not t:
                continue
            ok = t in ALL_NEW_NEED_TAGS
            if not ok:
                for pref in ("G_", "W_", "B_"):
                    if t.startswith(pref) and t[len(pref):] in ALL_NEW_NEED_TAGS:
                        ok = True
                        break
            if not ok:
                continue
            out[t] = _clamp(_safe_float(v, 0.0), 0.0, 1.0)
        return out

    def _package_supply_vector(self, items: Sequence[Tuple[TeamValuation, AssetSnapshot]]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for tv, snap in items:
            if tv.kind != AssetKind.PLAYER or not isinstance(snap, PlayerSnapshot):
                continue
            attrs = snap.attrs if isinstance(snap.attrs, dict) else {}
            try:
                sv = tag_supply(attrs, strict=True) or {}
            except Exception:
                sv = {}
            for k, v in sv.items():
                t = str(k or "").strip().upper()
                if not t:
                    continue
                out[t] = out.get(t, 0.0) + _clamp(_safe_float(v, 0.0), 0.0, 1.0)
        return out

    def _need_threshold(self, need_w: float) -> float:
        cfg = self.config
        n = _clamp(_safe_float(need_w, 0.0), 0.0, 1.0)

        t_min = _clamp(_safe_float(cfg.need_supply_gate_threshold_min, 0.0), 0.0, 1.0)
        t_max = _clamp(_safe_float(cfg.need_supply_gate_threshold_max, 1.0), 0.0, 1.0)
        if t_min > t_max:
            t_min, t_max = t_max, t_min

        return t_max - (t_max - t_min) * n

    def _effective_supply(self, raw_supply: float, need_w: float) -> Tuple[float, float, float, bool]:
        """Return (effective, gate, threshold, hard_floor_blocked)."""
        cfg = self.config
        s_raw = _clamp(_safe_float(raw_supply, 0.0), 0.0, 1.0)

        if not bool(cfg.need_supply_gate_enabled):
            return s_raw, 1.0, self._need_threshold(need_w), False

        floor_cap = _clamp(_safe_float(cfg.need_supply_gate_hard_floor_cap, 0.0), 0.0, 1.0)
        if s_raw < floor_cap:
            return 0.0, 0.0, self._need_threshold(need_w), True

        threshold = self._need_threshold(need_w)
        soft_width = max(_safe_float(cfg.need_supply_gate_soft_width, 0.0), cfg.eps)

        x = (s_raw - threshold) / soft_width
        u = _clamp(0.5 + 0.5 * x, 0.0, 1.0)
        gate = _clamp(u * u * (3.0 - 2.0 * u), 0.0, 1.0)
        return _clamp(s_raw * gate, 0.0, 1.0), gate, threshold, False

    def _need_supply_balance_adjustment(
        self,
        *,
        incoming: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        outgoing: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        ctx: DecisionContext,
        base_out_mass: float,
        steps: List[ValuationStep],
    ) -> Tuple[ValueComponents, Dict[str, Any]]:
        cfg = self.config
        need_map = self._resolve_need_map_new(ctx)
        in_supply = self._package_supply_vector(incoming)
        out_supply = self._package_supply_vector(outgoing)

        tags = sorted(set(need_map.keys()) | set(in_supply.keys()) | set(out_supply.keys()))
        if not tags:
            return ValueComponents.zero(), {"need_map": {}, "incoming_supply": {}, "outgoing_supply": {}}

        fulfilled: Dict[str, float] = {}
        excess: Dict[str, float] = {}
        incoming_supply_raw: Dict[str, float] = {}
        incoming_supply_effective: Dict[str, float] = {}
        threshold_by_tag: Dict[str, float] = {}
        gate_by_tag: Dict[str, float] = {}
        hard_floor_blocked_tags: List[str] = []
        need_w_excess_sum = 0.0
        fulfilled_mass = 0.0

        for t in tags:
            need = _clamp(_safe_float(need_map.get(t, 0.0), 0.0), 0.0, 1.0)
            s_raw = max(0.0, _safe_float(in_supply.get(t, 0.0), 0.0))
            s_eff, gate, thr, hard_floor_blocked = self._effective_supply(s_raw, need)

            incoming_supply_raw[t] = s_raw
            incoming_supply_effective[t] = s_eff
            threshold_by_tag[t] = thr
            gate_by_tag[t] = gate
            if hard_floor_blocked:
                hard_floor_blocked_tags.append(t)

            f = min(need, s_eff)
            e = max(0.0, s_eff - need)
            fulfilled[t] = f
            excess[t] = e
            fulfilled_mass += f
            if need > 0.0 and e > 0.0:
                need_w_excess_sum += need * e

        raw_pen = cfg.need_supply_excess_scale * (need_w_excess_sum ** cfg.need_supply_excess_exponent)
        cap = cfg.need_supply_excess_cap_ratio * max(base_out_mass, cfg.eps)
        pen_total = _clamp(raw_pen, 0.0, cap)
        penalty = _split_total_to_components(-pen_total, w_now=float(ctx.knobs.w_now), w_future=float(ctx.knobs.w_future))

        bonus = ValueComponents.zero()
        bonus_total = 0.0
        in_players = len(self._players(incoming))
        # depth stress proxy from prefixed positional needs
        depth_stress = 0.0
        for pref in ("G_", "W_", "B_"):
            depth_stress = max(depth_stress, max((_safe_float(v, 0.0) for k, v in need_map.items() if str(k).startswith(pref)), default=0.0))

        if cfg.slot_efficiency_enabled and in_players > 0 and fulfilled_mass > cfg.eps:
            raw_bonus = cfg.slot_efficiency_scale * (fulfilled_mass / max(1.0, float(in_players)))
            damp = 1.0 - cfg.slot_efficiency_depth_stress_dampen * _clamp(depth_stress, 0.0, 1.0)
            raw_bonus *= _clamp(damp, 0.0, 1.0)
            bcap = cfg.slot_efficiency_cap_ratio * max(base_out_mass, cfg.eps)
            bonus_total = _clamp(raw_bonus, 0.0, bcap)
            bonus = _split_total_to_components(bonus_total, w_now=float(ctx.knobs.w_now), w_future=float(ctx.knobs.w_future))

        delta = penalty + bonus
        if abs(delta.total) > cfg.eps:
            steps.append(
                ValuationStep(
                    stage=ValuationStage.PACKAGE,
                    mode=StepMode.ADD,
                    code="NEED_SUPPLY_BALANCE_DELTA",
                    label="니즈 충족/초과공급 패키지 보정",
                    delta=delta,
                    meta={
                        "fulfilled_mass": fulfilled_mass,
                        "need_weighted_excess_sum": need_w_excess_sum,
                        "penalty_total": pen_total,
                        "slot_efficiency_bonus_total": bonus_total,
                        "depth_stress": depth_stress,
                        "incoming_players": in_players,
                    },
                )
            )

        meta = {
            "need_map": {k: _safe_float(v, 0.0) for k, v in need_map.items()},
            # Backward-compat: keep legacy incoming_supply as raw (pre-gating) aggregated supply.
            "incoming_supply": {k: _safe_float(v, 0.0) for k, v in incoming_supply_raw.items()},
            "incoming_supply_raw": {k: _safe_float(v, 0.0) for k, v in incoming_supply_raw.items()},
            "incoming_supply_effective": {k: _safe_float(v, 0.0) for k, v in incoming_supply_effective.items()},
            "outgoing_supply": {k: _safe_float(v, 0.0) for k, v in out_supply.items()},
            "gate_by_tag": {k: _safe_float(v, 0.0) for k, v in gate_by_tag.items()},
            "threshold_by_tag": {k: _safe_float(v, 0.0) for k, v in threshold_by_tag.items()},
            "hard_floor_blocked_tags": tuple(dict.fromkeys(str(k) for k in hard_floor_blocked_tags)),
            "fulfilled": {k: _safe_float(v, 0.0) for k, v in fulfilled.items()},
            "excess": {k: _safe_float(v, 0.0) for k, v in excess.items()},
            "penalty_total": pen_total,
            "slot_efficiency_bonus_total": bonus_total,
        }
        return delta, meta



    def _agency_distress_adjustment(
        self,
        incoming: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        outgoing: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        steps: List[ValuationStep],
    ) -> ValueComponents:
        cfg = self.config

        def _distress_ratio(snap: PlayerSnapshot) -> float:
            st = snap.meta.get("agency_state") if isinstance(snap.meta, dict) else None
            if not isinstance(st, Mapping):
                return 0.0
            tr = int(_safe_float(st.get("trade_request_level"), 0.0))
            if tr < 2:
                return 0.0
            raw = float(cfg.agency_public_trade_request_discount)
            return _clamp(raw, 0.0, cfg.agency_distress_cap)

        # incoming distress lowers willingness to pay
        in_delta = 0.0
        out_delta = 0.0
        for tv, snap in incoming:
            if tv.kind != AssetKind.PLAYER or not isinstance(snap, PlayerSnapshot):
                continue
            ratio = _distress_ratio(snap)
            if ratio <= cfg.eps:
                continue
            in_delta -= max(0.0, _team_total_grade(tv)) * ratio

        # outgoing distress lowers seller's reservation value (easier to move)
        for tv, snap in outgoing:
            if tv.kind != AssetKind.PLAYER or not isinstance(snap, PlayerSnapshot):
                continue
            ratio = _distress_ratio(snap)
            if ratio <= cfg.eps:
                continue
            out_delta += max(0.0, _team_total_grade(tv)) * ratio

        raw = in_delta + out_delta
        if abs(raw) <= cfg.eps:
            return ValueComponents.zero()

        delta = _vc(now=raw, future=0.0)
        steps.append(
            ValuationStep(
                stage=ValuationStage.PACKAGE,
                mode=StepMode.ADD,
                code="AGENCY_DISTRESS_VALUE_ADJUST",
                label="에이전시 불만/트레이드요청 기반 가치 조정",
                delta=delta,
                meta={
                    "incoming_delta": float(in_delta),
                    "outgoing_delta": float(out_delta),
                    "public_trade_request_discount": float(cfg.agency_public_trade_request_discount),
                    "distress_cap": float(cfg.agency_distress_cap),
                    "policy": "PUBLIC_TRADE_REQUEST_ONLY",
                },
            )
        )
        return delta


# -----------------------------------------------------------------------------
# Convenience functional API (for deal_evaluator)
# -----------------------------------------------------------------------------
def apply_package_effects(
    *,
    team_id: str,
    incoming: Sequence[Tuple[TeamValuation, AssetSnapshot]],
    outgoing: Sequence[Tuple[TeamValuation, AssetSnapshot]],
    ctx: DecisionContext,
    env: ValuationEnv,
    config: Optional[PackageEffectsConfig] = None,
) -> Tuple[ValueComponents, Tuple[ValuationStep, ...], Dict[str, Any]]:
    """Stateless wrapper."""
    eng = PackageEffects(config=config or PackageEffectsConfig())
    return eng.apply(
        team_id=team_id,
        incoming=incoming,
        outgoing=outgoing,
        ctx=ctx,
        env=env,
    )
