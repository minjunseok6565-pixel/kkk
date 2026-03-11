from __future__ import annotations

from typing import Any

from .types import DealGeneratorConfig, DealGeneratorBudget
from .utils import compute_buy_retrieval_caps

# =============================================================================
# Budget scaling
# =============================================================================


def _scale_budget(cfg: DealGeneratorConfig, team_situation: Any) -> DealGeneratorBudget:
    posture = str(getattr(team_situation, "trade_posture", "STAND_PAT") or "STAND_PAT").upper()
    urgency = float(getattr(team_situation, "urgency", 0.0) or 0.0)
    deadline = 0.0
    try:
        deadline = float(getattr(getattr(team_situation, "constraints", None), "deadline_pressure", 0.0) or 0.0)
    except Exception:
        deadline = 0.0

    posture_scale = {
        "AGGRESSIVE_BUY": 1.25,
        "SOFT_BUY": 1.00,
        "SELL": 1.05,
        "SOFT_SELL": 0.95,
        "STAND_PAT": 0.55,
    }.get(posture, 0.75)

    # urgency/deadline (0..1) -> intensity (0.85..1.35)
    u = max(0.0, min(1.0, urgency))
    d = max(0.0, min(1.0, deadline))
    intensity = 0.85 + 0.35 * u + 0.25 * d
    scale = posture_scale * intensity

    def _cap(val: int, hard: int) -> int:
        return max(1, min(int(val), int(hard)))

    return DealGeneratorBudget(
        max_targets=_cap(int(cfg.base_max_targets * scale), cfg.max_targets_hard),
        beam_width=_cap(int(cfg.base_beam_width * scale), 24),
        max_attempts_per_target=_cap(int(cfg.base_max_attempts_per_target * scale), cfg.max_attempts_per_target_hard),
        max_validations=_cap(int(cfg.base_max_validations * scale), cfg.max_validations_hard),
        max_evaluations=_cap(int(cfg.base_max_evaluations * scale), cfg.max_evaluations_hard),
        max_repairs=_cap(int(cfg.base_max_repairs), 3),
    )




def _scale_buy_retrieval_limits(cfg: DealGeneratorConfig, team_situation: Any) -> dict[str, float]:
    """Return deadline/urgency-shaped BUY retrieval limits for tiered candidate scan."""
    caps = compute_buy_retrieval_caps(team_situation, cfg)

    listed_min = max(0, int(getattr(cfg, "buy_target_listed_min_quota", 0) or 0))
    listed_max_share = float(getattr(cfg, "buy_target_listed_max_share", 0.75) or 0.75)
    if listed_max_share < 0.0:
        listed_max_share = 0.0
    elif listed_max_share > 1.0:
        listed_max_share = 1.0

    caps["listed_min_quota"] = float(listed_min)
    caps["listed_max_share"] = float(listed_max_share)
    caps["tier2_enabled"] = 1.0 if bool(getattr(cfg, "buy_target_expand_tier2_enabled", True)) else 0.0
    caps["tier2_budget_share"] = max(0.0, min(1.0, float(getattr(cfg, "buy_target_expand_tier2_budget_share", 0.35) or 0.0)))
    caps["retrieval_iteration_cap"] = float(max(1, int(getattr(cfg, "buy_target_retrieval_iteration_cap", 1) or 1)))
    return caps


def _normalize_tier_dynamic_knobs(cfg: DealGeneratorConfig) -> dict[str, float]:
    """Return runtime-safe tier policy knobs for classifier entry.

    - 정책 레버를 clamp/정렬해 분류 진입 시 일관된 입력으로 사용한다.
    - strictness 기반 percentile 컷 파생값(shifted cuts)을 함께 제공한다.
    """

    def _clamp01(v: Any, default: float) -> float:
        try:
            x = float(v)
        except Exception:
            x = float(default)
        return max(0.0, min(1.0, x))

    def _clamp11(v: Any, default: float) -> float:
        try:
            x = float(v)
        except Exception:
            x = float(default)
        return max(-1.0, min(1.0, x))

    # base knobs
    strictness = _clamp11(getattr(cfg, "tier_strictness", 0.0), 0.0)
    strategy_w = _clamp01(getattr(cfg, "tier_strategy_weight", 0.20), 0.20)
    contract_w = _clamp01(getattr(cfg, "tier_contract_weight", 0.15), 0.15)
    market_w = _clamp01(getattr(cfg, "tier_market_percentile_weight", 0.35), 0.35)

    # hysteresis는 완충 구간 폭이므로 0.0~0.25로 제한
    hysteresis = max(0.0, min(0.25, float(getattr(cfg, "tier_hysteresis_band", 0.05) or 0.05)))

    # percentile cut baselines + validity ordering
    star_cut = _clamp01(getattr(cfg, "tier_star_pct_cut", 0.90), 0.90)
    high_cut = _clamp01(getattr(cfg, "tier_high_starter_pct_cut", 0.72), 0.72)
    starter_cut = _clamp01(getattr(cfg, "tier_starter_pct_cut", 0.48), 0.48)

    # enforce ordering: star > high > starter (minimum spacing)
    min_gap = 0.01
    starter_cut = min(starter_cut, 1.0 - (2.0 * min_gap))
    high_cut = max(high_cut, starter_cut + min_gap)
    high_cut = min(high_cut, 1.0 - min_gap)
    star_cut = max(star_cut, high_cut + min_gap)
    star_cut = min(star_cut, 1.0)

    # strictness-derived cut shift (parallel move, bounded to ±0.08)
    strict_shift = max(-0.08, min(0.08, 0.08 * strictness))
    star_cut_shifted = _clamp01(star_cut + strict_shift, star_cut)
    high_cut_shifted = _clamp01(high_cut + strict_shift, high_cut)
    starter_cut_shifted = _clamp01(starter_cut + strict_shift, starter_cut)

    # re-enforce ordering after shift
    starter_cut_shifted = min(starter_cut_shifted, 1.0 - (2.0 * min_gap))
    high_cut_shifted = max(high_cut_shifted, starter_cut_shifted + min_gap)
    high_cut_shifted = min(high_cut_shifted, 1.0 - min_gap)
    star_cut_shifted = max(star_cut_shifted, high_cut_shifted + min_gap)
    star_cut_shifted = min(star_cut_shifted, 1.0)

    # PICK_ONLY gates
    pick_kw_w = _clamp01(getattr(cfg, "tier_pick_only_keyword_weight", 0.45), 0.45)
    pick_top_w = _clamp01(getattr(cfg, "tier_pick_only_top_tags_weight", 0.20), 0.20)
    pick_inv_w = _clamp01(getattr(cfg, "tier_pick_only_inventory_weight", 0.35), 0.35)
    pick_stepien_pen = _clamp01(getattr(cfg, "tier_pick_only_stepien_penalty", 0.20), 0.20)
    pick_th = _clamp01(getattr(cfg, "tier_pick_only_threshold", 0.75), 0.75)

    return {
        "tier_strictness": strictness,
        "tier_strategy_weight": strategy_w,
        "tier_contract_weight": contract_w,
        "tier_market_percentile_weight": market_w,
        "tier_hysteresis_band": hysteresis,
        "tier_star_pct_cut": star_cut,
        "tier_high_starter_pct_cut": high_cut,
        "tier_starter_pct_cut": starter_cut,
        "tier_strict_shift": strict_shift,
        "tier_star_pct_cut_shifted": star_cut_shifted,
        "tier_high_starter_pct_cut_shifted": high_cut_shifted,
        "tier_starter_pct_cut_shifted": starter_cut_shifted,
        "tier_pick_only_keyword_weight": pick_kw_w,
        "tier_pick_only_top_tags_weight": pick_top_w,
        "tier_pick_only_inventory_weight": pick_inv_w,
        "tier_pick_only_stepien_penalty": pick_stepien_pen,
        "tier_pick_only_threshold": pick_th,
    }


def resolve_tier_dynamic_knobs(cfg: DealGeneratorConfig) -> dict[str, float]:
    """Single entrypoint for classifier callers to fetch normalized tier knobs."""
    return _normalize_tier_dynamic_knobs(cfg)
