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

In addition, team_situation may output need tags that are *deal-structural* and
should NOT be part of per-player fit scoring:

  - GUARD_DEPTH / WING_DEPTH / BIG_DEPTH / BENCH_DEPTH
  - CAP_FLEX
  - OFFENSE_UPGRADE / DEFENSE_UPGRADE

This module consumes DecisionContext.need_map (already computed by team_situation)
and only measures the *delta from the deal*, never re-evaluating the team.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from decision_context import DecisionContext

from contracts.terms import player_contract_terms

from .env import ValuationEnv
from .fit_engine import FitEngine
from .contract_texture import ContractTexture, build_contract_textures
from .role_texture import RoleTexture, build_role_textures
from .cap_ledger import build_cap_ledgers, score_cap_flex_delta

from .types import (
    AssetKind,
    AssetSnapshot,
    ContractSnapshot,
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
GUARD_DEPTH = "GUARD_DEPTH"
WING_DEPTH = "WING_DEPTH"
BIG_DEPTH = "BIG_DEPTH"
BENCH_DEPTH = "BENCH_DEPTH"
CAP_FLEX = "CAP_FLEX"
OFFENSE_UPGRADE = "OFFENSE_UPGRADE"
DEFENSE_UPGRADE = "DEFENSE_UPGRADE"


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


def _defense_signal(player: PlayerSnapshot) -> float:
    """0..1 defense signal proxy based on attrs/meta if available."""
    # 1) explicit meta/attrs override
    if isinstance(player.meta, dict) and "defense" in player.meta:
        return _clamp(_safe_float(player.meta.get("defense"), 0.5), 0.0, 1.0)
    if isinstance(player.attrs, dict) and "defense" in player.attrs:
        return _clamp(_safe_float(player.attrs.get("defense"), 0.5), 0.0, 1.0)

    # 2) typical attribute keys (2K-ish)
    keys = (
        "PerimeterDefense",
        "InteriorDefense",
        "Steal",
        "Block",
        "DefIQ",
        "DEF",
        "Defense",
    )
    best = 0.0
    if isinstance(player.attrs, dict):
        for k in keys:
            if k in player.attrs:
                v = _safe_float(player.attrs.get(k), 0.0)
                if v > 1.5:
                    v = v / 99.0
                best = max(best, _clamp(v, 0.0, 1.0))

    # default: neutral
    if best <= 1e-9:
        return 0.5
    return best


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class PackageEffectsConfig:
    # --- 5) Consolidation / dispersion
    consolidation_neutral: float = 0.5  # knobs.consolidation_bias baseline
    consolidation_scale: float = 0.10   # relative strength vs package totals
    consolidation_cap_ratio: float = 0.18

    # --- 6) Diminishing returns
    diminishing_factors: Tuple[float, ...] = (1.00, 0.78, 0.62, 0.50, 0.42)
    diminishing_now_weight: float = 0.85
    diminishing_future_weight: float = 0.35
    diminishing_min_bucket_grade: float = 1.5  # ignore very low value players

    # --- 7) Soft roster slot / rotation waste
    roster_excess_waste_rate: float = 0.85  # fraction of bottom incoming players' value wasted
    roster_excess_cap_ratio: float = 0.22

    # --- 8) Outgoing hole penalty
    hole_penalty_scale: float = 0.22
    hole_penalty_exponent: float = 1.15
    hole_penalty_cap_ratio: float = 0.18
    hole_texture_parallel_weight: float = 0.30

    # --- Depth needs (structural)
    depth_need_scale: float = 1.00
    bench_low_grade: float = 1.5
    starter_cutoff_grade: float = 8.0
    depth_texture_parallel_weight: float = 0.35

    # Texture-axis demand mapping for v2 parallel deficits
    depth_texture_creation_need_scale: float = 0.70
    depth_texture_rim_need_scale: float = 0.70
    depth_texture_defense_need_scale: float = 0.45
    depth_texture_spacing_need_scale: float = 0.35

    # --- CAP_FLEX
    cap_flex_scale: float = 0.00000006  # scale salary*years (dollars) -> value units
    cap_flex_cap_ratio: float = 0.16

    # Cap-room usage cost ("cap space is an asset")
    # - Applies when net incoming salary is absorbed using positive cap space.
    # - This should NOT be clamped by outgoing package value; it is its own real cost.
    cap_room_weight_base: float = 0.35  # even if CAP_FLEX need is low, cap room still has some cost
    cap_room_value_per_cap_fraction: float = 90.0  # using 100% of cap (hypothetical) => ~90 value units before weighting
    cap_room_cost_exponent: float = 1.25
    cap_room_abs_cap: float = 22.0

    # Commitment delta cap (future)
    cap_commit_abs_cap: float = 18.0

    # CAP_FLEX v2 ledger path (before/after balance sheet optionality)
    cap_flex_use_ledger_delta: bool = True
    cap_ledger_score_scale: float = 4.0
    cap_ledger_abs_cap: float = 18.0

    # --- Upgrade needs
    upgrade_scale: float = 0.75
    defense_proxy_floor: float = 0.35
    defense_proxy_cap: float = 1.00

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

    # --- texture injection
    dual_read_v2_components: bool = True
    texture_overlap_weight: float = 1.0
    contract_texture_control_weight: float = 0.20
    contract_texture_trigger_weight: float = 0.10
    contract_texture_toxic_weight: float = 0.10

    # --- rotation context injection (roster waste)
    rotation_context_weight: float = 0.55
    rotation_min_waste_multiplier: float = 0.45


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
        valuation_context_v2: Any | None = None,
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

        prefetched_role_textures = self._prefetched_role_textures(
            incoming=incoming,
            outgoing=outgoing,
            valuation_context_v2=valuation_context_v2,
        )
        prefetched_contract_textures = self._prefetched_contract_textures(
            incoming=incoming,
            outgoing=outgoing,
            valuation_context_v2=valuation_context_v2,
        )

        # 5) Consolidation / dispersion structure
        delta1 = self._consolidation_effect(incoming, outgoing, ctx, package_scale_total, steps)

        # 6) Basketball component (RoleTexture score matrix / overlap 기반)
        delta2 = self._diminishing_returns_texture(incoming, steps=steps, prefetched_role_textures=prefetched_role_textures)
        if abs(delta2.total) > self.config.eps:
            steps.append(self._build_basketball_component_step(delta2, source="role_texture_matrix"))

        # 7) Soft roster slot / rotation waste (too many incoming players)
        delta3 = self._roster_excess_waste(incoming, outgoing, base_out_mass, ctx, steps, prefetched_role_textures=prefetched_role_textures)

        # Depth needs (GUARD/WING/BIG/BENCH) based on deal delta *only*
        delta4 = self._depth_need_adjustment(incoming, outgoing, ctx, steps, prefetched_role_textures=prefetched_role_textures)

        # 8) Outgoing hole penalty (position discontinuity)
        delta5 = self._outgoing_hole_penalty(incoming, outgoing, base_out_mass, steps, prefetched_role_textures=prefetched_role_textures)

        # CAP_FLEX adjustment (contract component injection)
        delta6, contract_dual_meta = self._cap_flex_adjustment(incoming, outgoing, ctx, env, steps, prefetched_contract_textures=prefetched_contract_textures)

        # OFF/DEF upgrade adjustments
        delta7 = self._upgrade_adjustment(incoming, outgoing, ctx, steps)

        # Agency distress (trade-request / grievance) impacts perceived value.
        delta8 = self._agency_distress_adjustment(incoming, outgoing, steps)

        total = delta1 + delta2 + delta3 + delta4 + delta5 + delta6 + delta7 + delta8
        meta["base_in"] = {"now": base_in.now, "future": base_in.future, "total": base_in.total, "mass": base_in_mass}
        meta["base_out"] = {"now": base_out.now, "future": base_out.future, "total": base_out.total, "mass": base_out_mass}
        meta["package_delta"] = {"now": total.now, "future": total.future, "total": total.total}
        meta["team_id"] = str(team_id)
        if self.config.dual_read_v2_components:
            meta["v2_texture_diff"] = {
                "basketball_component_delta": {
                    "now": 0.0,
                    "future": 0.0,
                    "total": 0.0,
                },
                "contract_component_delta": contract_dual_meta,
            }

        return total, tuple(steps), meta

    def _build_basketball_component_step(self, delta: ValueComponents, *, source: str) -> ValuationStep:
        return ValuationStep(
            stage=ValuationStage.PACKAGE,
            mode=StepMode.ADD,
            code="BASKETBALL_COMPONENT",
            label="농구 컴포넌트(역할 질감/중복) 보정",
            delta=delta,
            meta={"source": source},
        )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    def _sum_team_values(self, items: Sequence[Tuple[TeamValuation, AssetSnapshot]]) -> ValueComponents:
        out = ValueComponents.zero()
        for tv, _ in items:
            out = out + tv.team_value
        return out

    def _prefetched_role_textures(
        self,
        *,
        incoming: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        outgoing: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        valuation_context_v2: Any | None,
    ) -> Dict[str, RoleTexture]:
        if valuation_context_v2 is None:
            return {}
        src = getattr(valuation_context_v2, "role_textures", None)
        if not isinstance(src, Mapping):
            return {}

        ids = {
            p.player_id
            for _, p in self._players(incoming)
        } | {
            p.player_id
            for _, p in self._players(outgoing)
        }
        out: Dict[str, RoleTexture] = {}
        for pid in ids:
            tex = src.get(pid)
            if isinstance(tex, RoleTexture):
                out[pid] = tex
        return out

    def _prefetched_contract_textures(
        self,
        *,
        incoming: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        outgoing: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        valuation_context_v2: Any | None,
    ) -> Dict[str, ContractTexture]:
        if valuation_context_v2 is None:
            return {}
        src = getattr(valuation_context_v2, "contract_textures", None)
        if not isinstance(src, Mapping):
            return {}

        by_player: Dict[str, ContractTexture] = {}
        players = [p for _, p in self._players(incoming)] + [p for _, p in self._players(outgoing)]
        for p in players:
            c = p.contract
            if c is None:
                continue
            tex = src.get(c.contract_id)
            if isinstance(tex, ContractTexture):
                by_player[p.player_id] = tex
        return by_player

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
    def _diminishing_returns_texture(
        self,
        incoming: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        steps: Optional[List[ValuationStep]],
        prefetched_role_textures: Optional[Mapping[str, RoleTexture]] = None,
    ) -> ValueComponents:
        cfg = self.config
        players = self._players(incoming)
        if len(players) <= 1:
            return ValueComponents.zero()

        role_textures: Dict[str, RoleTexture] = dict(prefetched_role_textures or {})
        missing = [p for _, p in players if p.player_id not in role_textures]
        if missing:
            role_textures.update(build_role_textures(missing, fit_engine=FitEngine()))

        items_sorted = sorted(
            [(tv, p) for tv, p in players if _team_total_grade(tv) >= cfg.diminishing_min_bucket_grade],
            key=lambda x: _team_total_grade(x[0]),
            reverse=True,
        )
        if len(items_sorted) <= 1:
            return ValueComponents.zero()

        factors = list(cfg.diminishing_factors)
        if len(items_sorted) > len(factors):
            factors.extend([factors[-1]] * (len(items_sorted) - len(factors)))

        pairwise_overlap: dict[str, dict[str, float]] = {}
        for _, p in items_sorted:
            pairwise_overlap[p.player_id] = {}
        for i, (_, p1) in enumerate(items_sorted):
            for j in range(i + 1, len(items_sorted)):
                _, p2 = items_sorted[j]
                ov = self._texture_overlap(p1.player_id, p2.player_id, role_textures)
                pairwise_overlap[p1.player_id][p2.player_id] = ov
                pairwise_overlap[p2.player_id][p1.player_id] = ov

        penalty = ValueComponents.zero()
        details: list[dict[str, Any]] = []
        for i, (tv, p) in enumerate(items_sorted):
            if i == 0:
                continue
            f = _clamp(factors[i], 0.0, 1.0)
            if f >= 0.999:
                continue
            prev_ids = [pp.player_id for _, pp in items_sorted[:i]]
            overlap = max((pairwise_overlap[p.player_id].get(pid, 0.0) for pid in prev_ids), default=0.0)
            overlap = _clamp(overlap * cfg.texture_overlap_weight, 0.0, 1.0)
            w = (1.0 - f) * overlap
            if w <= cfg.eps:
                continue
            pen = _vc(
                now=tv.team_value.now * w * cfg.diminishing_now_weight,
                future=tv.team_value.future * w * cfg.diminishing_future_weight,
            )
            penalty = penalty - pen
            details.append(
                {
                    "player_id": p.player_id,
                    "rank": i + 1,
                    "factor": f,
                    "texture_overlap": overlap,
                    "weighted_waste_ratio": w,
                    "team_value_total": tv.team_value.total,
                }
            )

        if abs(penalty.total) <= cfg.eps:
            return ValueComponents.zero()

        if steps is not None:
            steps.append(
                ValuationStep(
                    stage=ValuationStage.PACKAGE,
                    mode=StepMode.ADD,
                    code="DIMINISHING_RETURNS",
                    label="중복/체감 감소(RoleTexture matrix/overlap)",
                    delta=penalty,
                    meta={"details": details, "pairwise_overlap": pairwise_overlap},
                )
            )
        return penalty

    def _texture_overlap(
        self,
        left_player_id: str,
        right_player_id: str,
        role_textures: Mapping[str, RoleTexture],
    ) -> float:
        left = role_textures.get(left_player_id)
        right = role_textures.get(right_player_id)
        if left is None or right is None:
            return 0.0

        def vec(rt: RoleTexture) -> tuple[float, float, float, float, float]:
            return (
                _clamp(rt.creation_proxy, 0.0, 1.0),
                _clamp(rt.spacing_proxy, 0.0, 1.0),
                _clamp(rt.rim_pressure_proxy, 0.0, 1.0),
                _clamp(rt.defense_proxy, 0.0, 1.0),
                _clamp(rt.connector_index, 0.0, 1.0),
            )

        left_vec = vec(left)
        right_vec = vec(right)
        sim = 1.0 - (sum(abs(a - b) for a, b in zip(left_vec, right_vec)) / float(len(left_vec)))
        return _clamp(sim, 0.0, 1.0)

    # ------------------------------------------------------------------
    # 7) Soft roster slot / rotation waste
    # ------------------------------------------------------------------
    def _roster_excess_waste(
        self,
        incoming: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        outgoing: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        base_out_total: float,
        ctx: DecisionContext,
        steps: List[ValuationStep],
        prefetched_role_textures: Optional[Mapping[str, RoleTexture]] = None,
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
        role_textures: Dict[str, RoleTexture] = dict(prefetched_role_textures or {})
        missing = [p for _, p in in_players if p.player_id not in role_textures]
        if missing:
            role_textures.update(build_role_textures(missing, fit_engine=FitEngine()))
        rot = self._rotation_context_weights(ctx)
        detail: List[Dict[str, Any]] = []
        for tv, p in targets:
            context_relief = self._rotation_context_relief(p, role_textures, rot)
            mult = _clamp(
                1.0 - cfg.rotation_context_weight * context_relief,
                cfg.rotation_min_waste_multiplier,
                1.0,
            )
            wasted = wasted + tv.team_value.scale(cfg.roster_excess_waste_rate * mult)
            detail.append(
                {
                    "player_id": p.player_id,
                    "asset_key": tv.asset_key,
                    "team_value_total": tv.team_value.total,
                    "rotation_context_relief": context_relief,
                    "waste_multiplier": mult,
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
                    "rotation_context": rot,
                },
            )
        )
        return pen

    def _rotation_context_weights(self, ctx: DecisionContext) -> Dict[str, float]:
        need_map = dict(getattr(ctx, "need_map", {}) or {})
        d = getattr(ctx, "debug", {}) if isinstance(getattr(ctx, "debug", None), dict) else {}

        rc = d.get("rotation_context") if isinstance(d.get("rotation_context"), Mapping) else {}
        tactic = d.get("tactics") if isinstance(d.get("tactics"), Mapping) else {}

        primary_handler_need = _clamp(
            _safe_float(rc.get("primary_handler_need"), _safe_float(tactic.get("primary_handler_need"), _safe_float(need_map.get(OFFENSE_UPGRADE), 0.0))),
            0.0,
            1.0,
        )
        big_wing_pressure = _clamp(
            _safe_float(
                rc.get("big_wing_balance_pressure"),
                _safe_float(tactic.get("big_wing_balance_pressure"), max(_safe_float(need_map.get(WING_DEPTH), 0.0), _safe_float(need_map.get(BIG_DEPTH), 0.0))),
            ),
            0.0,
            1.0,
        )

        return {
            "primary_handler_need": float(primary_handler_need),
            "big_wing_balance_pressure": float(big_wing_pressure),
        }

    def _rotation_context_relief(
        self,
        player: PlayerSnapshot,
        role_textures: Mapping[str, RoleTexture],
        rotation_context: Mapping[str, float],
    ) -> float:
        handler_need = _clamp(_safe_float(rotation_context.get("primary_handler_need"), 0.0), 0.0, 1.0)
        balance_pressure = _clamp(_safe_float(rotation_context.get("big_wing_balance_pressure"), 0.0), 0.0, 1.0)

        rt = role_textures.get(player.player_id)
        handler_signal = _clamp(float(getattr(rt, "creation_proxy", 0.0) if rt is not None else 0.0), 0.0, 1.0)

        buckets = classify_depth_buckets(player)
        wing_big_signal = 0.0
        if "WING" in buckets:
            wing_big_signal += 0.5
        if "BIG" in buckets:
            wing_big_signal += 0.5
        wing_big_signal = _clamp(wing_big_signal, 0.0, 1.0)

        relief = (0.6 * handler_need * handler_signal) + (0.4 * balance_pressure * wing_big_signal)
        return _clamp(relief, 0.0, 1.0)

    def _texture_axis_supply(
        self,
        items: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        prefetched_role_textures: Optional[Mapping[str, RoleTexture]] = None,
    ) -> Dict[str, float]:
        players = self._players(items)
        if not players:
            return {"creation": 0.0, "rim_pressure": 0.0, "defense": 0.0, "spacing": 0.0}

        role_textures: Dict[str, RoleTexture] = dict(prefetched_role_textures or {})
        missing = [p for _, p in players if p.player_id not in role_textures]
        if missing:
            role_textures.update(build_role_textures(missing, fit_engine=FitEngine()))
        out = {"creation": 0.0, "rim_pressure": 0.0, "defense": 0.0, "spacing": 0.0}
        for tv, p in players:
            grade = max(_market_now_grade(tv), 0.0)
            rt = role_textures.get(p.player_id)
            if rt is None:
                continue
            out["creation"] += grade * _clamp(rt.creation_proxy, 0.0, 1.0)
            out["rim_pressure"] += grade * _clamp(rt.rim_pressure_proxy, 0.0, 1.0)
            out["defense"] += grade * _clamp(rt.defense_proxy, 0.0, 1.0)
            out["spacing"] += grade * _clamp(rt.spacing_proxy, 0.0, 1.0)
        return out

    # ------------------------------------------------------------------
    # Depth needs (structural, need_map-driven)
    # ------------------------------------------------------------------
    def _depth_need_adjustment(
        self,
        incoming: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        outgoing: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        ctx: DecisionContext,
        steps: List[ValuationStep],
        prefetched_role_textures: Optional[Mapping[str, RoleTexture]] = None,
    ) -> ValueComponents:
        cfg = self.config
        need_map = dict(ctx.need_map or {})
        if not need_map and getattr(ctx, "policies", None) is not None:
            try:
                need_map = dict(ctx.policies.fit.need_map or {})
            except Exception:
                need_map = {}

        w_guard = _clamp(_safe_float(need_map.get(GUARD_DEPTH), 0.0), 0.0, 1.0)
        w_wing = _clamp(_safe_float(need_map.get(WING_DEPTH), 0.0), 0.0, 1.0)
        w_big = _clamp(_safe_float(need_map.get(BIG_DEPTH), 0.0), 0.0, 1.0)
        w_bench = _clamp(_safe_float(need_map.get(BENCH_DEPTH), 0.0), 0.0, 1.0)

        if (w_guard + w_wing + w_big + w_bench) <= cfg.eps:
            return ValueComponents.zero()

        def depth_supply(items: Sequence[Tuple[TeamValuation, AssetSnapshot]]) -> Dict[str, float]:
            s = {"GUARD": 0.0, "WING": 0.0, "BIG": 0.0, "BENCH": 0.0}
            for tv, snap in items:
                if tv.kind != AssetKind.PLAYER or not isinstance(snap, PlayerSnapshot):
                    continue
                grade = max(_market_now_grade(tv), 0.0)
                buckets = classify_depth_buckets(snap)
                if not buckets:
                    continue
                share = grade / max(len(buckets), 1)
                for b in buckets:
                    if b in ("GUARD", "WING", "BIG"):
                        s[b] += share

                # bench: only count mid-tier players
                if cfg.bench_low_grade <= grade <= cfg.starter_cutoff_grade:
                    s["BENCH"] += grade
            return s

        in_s = depth_supply(incoming)
        out_s = depth_supply(outgoing)

        delta_guard = in_s["GUARD"] - out_s["GUARD"]
        delta_wing = in_s["WING"] - out_s["WING"]
        delta_big = in_s["BIG"] - out_s["BIG"]
        delta_bench = in_s["BENCH"] - out_s["BENCH"]

        bucket_bonus_total = (
            cfg.depth_need_scale
            * (w_guard * delta_guard + w_wing * delta_wing + w_big * delta_big + w_bench * delta_bench)
        )

        # v2 parallel: RoleTexture axis deficits/surpluses in parallel with G/W/B buckets
        in_tex = self._texture_axis_supply(incoming, prefetched_role_textures=prefetched_role_textures)
        out_tex = self._texture_axis_supply(outgoing, prefetched_role_textures=prefetched_role_textures)
        tex_delta = {
            "creation": in_tex["creation"] - out_tex["creation"],
            "rim_pressure": in_tex["rim_pressure"] - out_tex["rim_pressure"],
            "defense": in_tex["defense"] - out_tex["defense"],
            "spacing": in_tex["spacing"] - out_tex["spacing"],
        }
        texture_need_weights = {
            "creation": _clamp(w_guard * cfg.depth_texture_creation_need_scale, 0.0, 1.0),
            "rim_pressure": _clamp(w_big * cfg.depth_texture_rim_need_scale, 0.0, 1.0),
            "defense": _clamp(max(w_wing, w_big) * cfg.depth_texture_defense_need_scale, 0.0, 1.0),
            "spacing": _clamp(max(w_guard, w_wing) * cfg.depth_texture_spacing_need_scale, 0.0, 1.0),
        }
        texture_bonus_total = cfg.depth_need_scale * cfg.depth_texture_parallel_weight * sum(
            texture_need_weights[k] * tex_delta[k] for k in ("creation", "rim_pressure", "defense", "spacing")
        )

        bonus_total = bucket_bonus_total + texture_bonus_total

        if abs(bonus_total) <= cfg.eps:
            return ValueComponents.zero()

        delta = _vc(now=bonus_total, future=0.0)
        steps.append(
            ValuationStep(
                stage=ValuationStage.PACKAGE,
                mode=StepMode.ADD,
                code="DEPTH_NEED_DELTA",
                label="뎁스 니즈(G/W/B/Bench) 딜 델타 보정",
                delta=delta,
                meta={
                    "weights": {
                        GUARD_DEPTH: w_guard,
                        WING_DEPTH: w_wing,
                        BIG_DEPTH: w_big,
                        BENCH_DEPTH: w_bench,
                    },
                    "delta": {
                        "guard": delta_guard,
                        "wing": delta_wing,
                        "big": delta_big,
                        "bench": delta_bench,
                    },
                    "delta_texture": tex_delta,
                    "texture_need_weights": texture_need_weights,
                    "bucket_bonus_total": bucket_bonus_total,
                    "texture_bonus_total": texture_bonus_total,
                    "incoming_supply": in_s,
                    "outgoing_supply": out_s,
                    "incoming_texture_supply": in_tex,
                    "outgoing_texture_supply": out_tex,
                },
            )
        )
        return delta

    # ------------------------------------------------------------------
    # 8) Outgoing hole penalty (delta-only, need_map-independent)
    # ------------------------------------------------------------------
    def _outgoing_hole_penalty(
        self,
        incoming: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        outgoing: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        base_out_total: float,
        steps: List[ValuationStep],
        prefetched_role_textures: Optional[Mapping[str, RoleTexture]] = None,
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

        in_tex = self._texture_axis_supply(incoming, prefetched_role_textures=prefetched_role_textures)
        out_tex = self._texture_axis_supply(outgoing, prefetched_role_textures=prefetched_role_textures)
        tex_delta = {
            "creation": in_tex["creation"] - out_tex["creation"],
            "rim_pressure": in_tex["rim_pressure"] - out_tex["rim_pressure"],
            "defense": in_tex["defense"] - out_tex["defense"],
            "spacing": in_tex["spacing"] - out_tex["spacing"],
        }
        texture_penalties: Dict[str, float] = {}
        for axis, dv in tex_delta.items():
            if dv < -cfg.eps:
                texture_penalties[axis] = (abs(dv) ** cfg.hole_penalty_exponent) * cfg.hole_penalty_scale * cfg.hole_texture_parallel_weight
        pen_total += float(sum(texture_penalties.values()))

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
                meta={
                    "penalties": penalties,
                    "texture_penalties": texture_penalties,
                    "incoming_supply": in_s,
                    "outgoing_supply": out_s,
                    "incoming_texture_supply": in_tex,
                    "outgoing_texture_supply": out_tex,
                    "delta_texture": tex_delta,
                },
            )
        )
        return delta

    # ------------------------------------------------------------------
    # CAP_FLEX (need_map-driven)
    # ------------------------------------------------------------------
    def _cap_flex_adjustment(
        self,
        incoming: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        outgoing: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        ctx: DecisionContext,
        env: ValuationEnv,
        steps: List[ValuationStep],
        prefetched_contract_textures: Optional[Mapping[str, ContractTexture]] = None,
    ) -> tuple[ValueComponents, Dict[str, Any]]:
        cfg = self.config

        need_map = dict(ctx.need_map or {})
        if not need_map and getattr(ctx, "policies", None) is not None:
            try:
                need_map = dict(ctx.policies.fit.need_map or {})
            except Exception:
                need_map = {}
        w_need = _clamp(_safe_float(need_map.get(CAP_FLEX), 0.0), 0.0, 1.0)

        # --------------------------------------------------------------
        # (A) Cap-room usage cost (NOW): using cap space is a real resource.
        # --------------------------------------------------------------
        cap_space_before = 0.0
        if isinstance(getattr(ctx, "debug", None), dict):
            cap_space_before = _safe_float(ctx.debug.get("cap_space"), 0.0)
        cap_space_before = max(0.0, float(cap_space_before))

        cur_sy = int(env.current_season_year)
        if cur_sy <= 0:
            raise ValueError("ValuationEnv.current_season_year must be a positive integer")

        cap_now = float(env.cap_model.salary_cap_for_season(int(cur_sy)))
        cap_source = "env"

        def salary_now(p: PlayerSnapshot) -> float:
            s = _safe_float(getattr(p, "salary_amount", None), 0.0)
            if s > cfg.eps:
                return float(s)
            t = player_contract_terms(p, current_season_year=int(cur_sy))
            return float(max(0.0, _safe_float(t.salary_now, 0.0)))

        def sum_salary_now(items: Sequence[Tuple[TeamValuation, AssetSnapshot]]) -> float:
            acc = 0.0
            for tv, snap in items:
                if tv.kind != AssetKind.PLAYER or not isinstance(snap, PlayerSnapshot):
                    continue
                acc += salary_now(snap)
            return float(acc)

        in_sal = sum_salary_now(incoming)
        out_sal = sum_salary_now(outgoing)
        net_added = in_sal - out_sal

        cap_room_used = min(max(net_added, 0.0), cap_space_before)
        used_frac = 0.0
        if float(cap_now) > cfg.eps:
            used_frac = cap_room_used / float(cap_now)

        # even if CAP_FLEX need is low, cap room isn't free
        w_room = _clamp(cfg.cap_room_weight_base + (1.0 - cfg.cap_room_weight_base) * w_need, 0.0, 1.0)
        raw_now = 0.0
        if float(cap_now) > cfg.eps:
            raw_now = -w_room * cfg.cap_room_value_per_cap_fraction * (used_frac ** cfg.cap_room_cost_exponent)
        raw_now = _clamp(raw_now, -cfg.cap_room_abs_cap, 0.0)

        delta_now = ValueComponents.zero()
        if abs(raw_now) > cfg.eps:
            delta_now = _vc(now=raw_now, future=0.0)
            steps.append(
                ValuationStep(
                    stage=ValuationStage.PACKAGE,
                    mode=StepMode.ADD,
                    code="CAP_ROOM_USED_COST",
                    label="캡스페이스 사용 비용(자원)",
                    delta=delta_now,
                    meta={
                        "weight_need": w_need,
                        "weight_effective": w_room,
                        "cap_space_before": cap_space_before,
                        "cap_now": cap_now,
                        "cap_source": cap_source,
                        "cur_season_year": cur_sy,
                        "incoming_salary_now": in_sal,
                        "outgoing_salary_now": out_sal,
                        "net_added_salary_now": net_added,
                        "cap_room_used": cap_room_used,
                        "used_frac": used_frac,
                        "value_per_cap_fraction": cfg.cap_room_value_per_cap_fraction,
                        "exponent": cfg.cap_room_cost_exponent,
                    },
                )
            )

        # --------------------------------------------------------------
        # (B) CAP_FLEX FUTURE component
        #   - Preferred: before/after CapLedger delta (v2)
        #   - Fallback: legacy commitment delta (contract texture weighted)
        # --------------------------------------------------------------
        contract_textures = self._build_player_contract_textures(
            incoming,
            outgoing,
            current_season_year=int(cur_sy),
            env=env,
            prefetched_contract_textures=prefetched_contract_textures,
        )
        posture = str(getattr(ctx, "posture", "STAND_PAT"))

        in_players = [snap for tv, snap in incoming if tv.kind == AssetKind.PLAYER and isinstance(snap, PlayerSnapshot)]
        out_players = [snap for tv, snap in outgoing if tv.kind == AssetKind.PLAYER and isinstance(snap, PlayerSnapshot)]

        in_team_textures = [contract_textures[p.player_id] for p in in_players if p.player_id in contract_textures]
        out_team_textures = [contract_textures[p.player_id] for p in out_players if p.player_id in contract_textures]

        debug = getattr(ctx, "debug", {}) if isinstance(getattr(ctx, "debug", None), dict) else {}
        payroll_before = _safe_float(debug.get("payroll"), 0.0)
        has_payroll = float(payroll_before) > cfg.eps
        team_norm = str(getattr(ctx, "team_id", "")).upper() or "TEAM"

        ledger_raw = 0.0
        ledger_ok = False
        ledger_meta: Dict[str, Any] = {
            "enabled": bool(cfg.cap_flex_use_ledger_delta),
            "team_id": team_norm,
            "has_payroll": bool(has_payroll),
        }
        if cfg.cap_flex_use_ledger_delta and has_payroll:
            payroll_after = float(payroll_before + net_added)
            before_ledgers = build_cap_ledgers(
                team_contract_textures={team_norm: tuple(out_team_textures)},
                posture_by_team={team_norm: posture},
                current_season_year=int(cur_sy),
                cap_model=env.cap_model,
                team_payroll_map={team_norm: float(payroll_before)},
            )
            after_ledgers = build_cap_ledgers(
                team_contract_textures={team_norm: tuple(in_team_textures)},
                posture_by_team={team_norm: posture},
                current_season_year=int(cur_sy),
                cap_model=env.cap_model,
                team_payroll_map={team_norm: float(payroll_after)},
            )
            before = before_ledgers.get(team_norm)
            after = after_ledgers.get(team_norm)
            if before is not None and after is not None:
                ledger_ok = True
                ledger_raw = score_cap_flex_delta(before, after, posture=posture)
                ledger_meta.update(
                    {
                        "score_delta": float(ledger_raw),
                        "score_scale": float(cfg.cap_ledger_score_scale),
                        "payroll_before": float(payroll_before),
                        "payroll_after": float(payroll_after),
                        "before_flex": float(before.posture_adjusted_flex),
                        "after_flex": float(after.posture_adjusted_flex),
                    }
                )

        def sum_commit_texture(items: Sequence[Tuple[TeamValuation, AssetSnapshot]]) -> float:
            acc = 0.0
            for tv, snap in items:
                if tv.kind != AssetKind.PLAYER or not isinstance(snap, PlayerSnapshot):
                    continue
                tex = contract_textures.get(snap.player_id)
                if tex is None:
                    continue
                multiplier = 1.0
                multiplier += cfg.contract_texture_control_weight * max(0.0, -float(tex.control_direction))
                multiplier += cfg.contract_texture_trigger_weight * _clamp(float(tex.trigger_risk), 0.0, 1.0)
                multiplier += cfg.contract_texture_toxic_weight * _clamp(float(tex.toxic_risk), 0.0, 1.0)
                acc += max(0.0, float(tex.guaranteed_commitment)) * max(0.0, multiplier)
            return float(acc)

        in_c = sum_commit_texture(incoming)
        out_c = sum_commit_texture(outgoing)
        source = "contract_texture"
        delta_commit = in_c - out_c

        raw_fut = 0.0
        if ledger_ok:
            source = "cap_ledger"
            raw_fut = w_need * ledger_raw * cfg.cap_ledger_score_scale
            raw_fut = _clamp(raw_fut, -cfg.cap_ledger_abs_cap, cfg.cap_ledger_abs_cap)
        else:
            source = "cap_ledger"
            raw_fut = 0.0

        delta_fut = ValueComponents.zero()
        if abs(raw_fut) > cfg.eps:
            delta_fut = _vc(now=0.0, future=raw_fut)
            steps.append(
                ValuationStep(
                    stage=ValuationStage.PACKAGE,
                    mode=StepMode.ADD,
                    code="CAP_FLEX_COMMITMENT_DELTA",
                    label="유연성(CAP_FLEX) 커미트먼트 델타",
                    delta=delta_fut,
                    meta={
                        "weight": w_need,
                        "cur_season_year": cur_sy,
                        "delta_commitment": delta_commit,
                        "scale": cfg.cap_flex_scale,
                        "incoming_commit": in_c,
                        "outgoing_commit": out_c,
                        "abs_cap": cfg.cap_commit_abs_cap,
                        "source": source,
                        "ledger": ledger_meta,
                    },
                )
            )
        component_delta = delta_now + delta_fut
        if abs(component_delta.total) > cfg.eps:
            steps.append(
                ValuationStep(
                    stage=ValuationStage.PACKAGE,
                    mode=StepMode.ADD,
                    code="CONTRACT_COMPONENT",
                    label="계약 컴포넌트(계약 질감/CAP_FLEX) 보정",
                    delta=component_delta,
                    meta={"source": source},
                )
            )

        dual_meta = {
            "texture": {
                "incoming_commit": float(in_c),
                "outgoing_commit": float(out_c),
                "delta_commit": float(delta_commit),
            },
            "ledger": ledger_meta,
            "selected_source": source,
        }
        return delta_now + delta_fut, dual_meta

    def _build_player_contract_textures(
        self,
        incoming: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        outgoing: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        *,
        current_season_year: int,
        env: ValuationEnv,
        prefetched_contract_textures: Optional[Mapping[str, ContractTexture]] = None,
    ) -> Dict[str, ContractTexture]:
        players: list[PlayerSnapshot] = []
        for tv, snap in list(incoming) + list(outgoing):
            if tv.kind == AssetKind.PLAYER and isinstance(snap, PlayerSnapshot):
                players.append(snap)

        out: Dict[str, ContractTexture] = {}
        prefetched = dict(prefetched_contract_textures or {})
        for p in players:
            tex = prefetched.get(p.player_id)
            if tex is not None:
                out[p.player_id] = tex

        contracts: list[ContractSnapshot] = []
        seen_contract_ids: set[str] = set()
        for p in players:
            if p.player_id in out:
                continue
            c = p.contract
            if c is None or c.contract_id in seen_contract_ids:
                continue
            seen_contract_ids.add(c.contract_id)
            contracts.append(c)
        if not contracts:
            return out

        salary_cap = float(env.cap_model.salary_cap_for_season(int(current_season_year)))
        textures = build_contract_textures(
            contracts,
            current_season_year=int(current_season_year),
            salary_cap=salary_cap,
        )
        for p in players:
            if p.player_id in out:
                continue
            if p.contract is None:
                continue
            tex = textures.get(p.contract.contract_id)
            if tex is not None:
                out[p.player_id] = tex
        return out

    # ------------------------------------------------------------------
    # OFF/DEF upgrade (need_map-driven)
    # ------------------------------------------------------------------
    def _upgrade_adjustment(
        self,
        incoming: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        outgoing: Sequence[Tuple[TeamValuation, AssetSnapshot]],
        ctx: DecisionContext,
        steps: List[ValuationStep],
    ) -> ValueComponents:
        cfg = self.config
        need_map = dict(ctx.need_map or {})
        if not need_map and getattr(ctx, "policies", None) is not None:
            try:
                need_map = dict(ctx.policies.fit.need_map or {})
            except Exception:
                need_map = {}

        w_off = _clamp(_safe_float(need_map.get(OFFENSE_UPGRADE), 0.0), 0.0, 1.0)
        w_def = _clamp(_safe_float(need_map.get(DEFENSE_UPGRADE), 0.0), 0.0, 1.0)
        if (w_off + w_def) <= cfg.eps:
            return ValueComponents.zero()

        def sum_off(items: Sequence[Tuple[TeamValuation, AssetSnapshot]]) -> float:
            acc = 0.0
            for tv, snap in items:
                if tv.kind != AssetKind.PLAYER or not isinstance(snap, PlayerSnapshot):
                    continue
                acc += max(_market_now_grade(tv), 0.0)
            return acc

        def sum_def(items: Sequence[Tuple[TeamValuation, AssetSnapshot]]) -> float:
            acc = 0.0
            for tv, snap in items:
                if tv.kind != AssetKind.PLAYER or not isinstance(snap, PlayerSnapshot):
                    continue
                base = max(_market_now_grade(tv), 0.0)
                sig = _clamp(_defense_signal(snap), cfg.defense_proxy_floor, cfg.defense_proxy_cap)
                acc += base * sig
            return acc

        delta_off = sum_off(incoming) - sum_off(outgoing)
        delta_def = sum_def(incoming) - sum_def(outgoing)

        raw = cfg.upgrade_scale * (w_off * delta_off + w_def * delta_def)
        if abs(raw) <= cfg.eps:
            return ValueComponents.zero()

        delta = _vc(now=raw, future=0.0)
        steps.append(
            ValuationStep(
                stage=ValuationStage.PACKAGE,
                mode=StepMode.ADD,
                code="OFF_DEF_UPGRADE_DELTA",
                label="공격/수비 업그레이드(딜 조합) 보정",
                delta=delta,
                meta={
                    "weights": {OFFENSE_UPGRADE: w_off, DEFENSE_UPGRADE: w_def},
                    "delta_off": delta_off,
                    "delta_def": delta_def,
                    "upgrade_scale": cfg.upgrade_scale,
                },
            )
        )
        return delta


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
    valuation_context_v2: Any | None = None,
) -> Tuple[ValueComponents, Tuple[ValuationStep, ...], Dict[str, Any]]:
    """Stateless wrapper."""
    eng = PackageEffects(config=config or PackageEffectsConfig())
    return eng.apply(
        team_id=team_id,
        incoming=incoming,
        outgoing=outgoing,
        ctx=ctx,
        env=env,
        valuation_context_v2=valuation_context_v2,
    )
