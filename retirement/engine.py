from __future__ import annotations

from typing import Any, Dict, Mapping

from agency.utils import clamp, clamp01, mental_norm, sigmoid, stable_u01

from .config import DEFAULT_RETIREMENT_CONFIG, RetirementConfig
from .types import RetirementDecision, RetirementInputs


def _age_factor(age: int) -> float:
    # Slow start in late-20s, ramps through 30s.
    return clamp01((float(age) - 29.0) / 11.0)


def _injury_status_factor(status: str, severity: int) -> float:
    st = str(status or "HEALTHY").upper()
    base = 0.0
    if st == "RETURNING":
        base = 0.30
    elif st == "OUT":
        base = 0.60
    sev_bonus = clamp01(float(max(0, int(severity))) / 25.0) * 0.30
    return clamp01(base + sev_bonus)


def _injury_burden(inp: RetirementInputs, cfg: RetirementConfig) -> tuple[float, Dict[str, float]]:
    ctx: Mapping[str, Any] = inp.injury_context if isinstance(inp.injury_context, Mapping) else {}

    status_f = _injury_status_factor(inp.injury_status, inp.injury_severity)

    # Approx NBA season missed games via days (82 games / ~177 days ≈ 0.46 games/day).
    missed_1y_days = max(0.0, float(ctx.get("missed_days_1y") or 0.0))
    missed_3y_days = max(0.0, float(ctx.get("missed_days_3y") or 0.0))
    recent_missed_f = clamp01(missed_1y_days / 90.0)
    three_year_missed_f = clamp01(missed_3y_days / 240.0)

    severe_count_3y = max(0.0, float(ctx.get("severe_count_3y") or 0.0))
    severe_f = clamp01(severe_count_3y / 4.0)

    reinjury_total = max(0.0, float(ctx.get("reinjury_total") or 0.0))
    reinjury_f = clamp01(reinjury_total / 8.0)

    perm_drop_total = max(0.0, float(ctx.get("perm_drop_total") or 0.0))
    perm_drop_f = clamp01(perm_drop_total / 24.0)

    # Weighted linear combination, then clipped.
    raw = (
        float(cfg.injury_current_status_w) * status_f
        + float(cfg.injury_recent_missed_w) * recent_missed_f
        + float(cfg.injury_three_year_missed_w) * three_year_missed_f
        + float(cfg.injury_severe_w) * severe_f
        + float(cfg.injury_reinjury_w) * reinjury_f
        + float(cfg.injury_perm_drop_w) * perm_drop_f
    )

    features = {
        "status_f": float(status_f),
        "recent_missed_f": float(recent_missed_f),
        "three_year_missed_f": float(three_year_missed_f),
        "severe_f": float(severe_f),
        "reinjury_f": float(reinjury_f),
        "perm_drop_f": float(perm_drop_f),
    }
    return clamp01(raw), features


def evaluate_retirement_candidate(
    inp: RetirementInputs,
    *,
    cfg: RetirementConfig = DEFAULT_RETIREMENT_CONFIG,
) -> RetirementDecision:
    age_f = _age_factor(int(inp.age))
    teamless_f = 1.0 if str(inp.team_id).upper() == "FA" else 0.0
    injury_burden, injury_terms = _injury_burden(inp, cfg)

    work = mental_norm(inp.mental, "work_ethic")
    coach = mental_norm(inp.mental, "coachability")
    amb = mental_norm(inp.mental, "ambition")
    loy = mental_norm(inp.mental, "loyalty")
    ego = mental_norm(inp.mental, "ego")
    adapt = mental_norm(inp.mental, "adaptability")

    consider_z = (
        float(cfg.consider_bias)
        + float(cfg.consider_w_age) * age_f
        + float(cfg.consider_w_injury_burden) * injury_burden
        + float(cfg.consider_w_teamless) * teamless_f
        + float(cfg.consider_w_ambition) * amb
        + float(cfg.consider_w_work_ethic) * work
        + float(cfg.consider_w_adaptability) * adapt
    )
    consider_prob = clamp01(sigmoid(float(consider_z)))
    consider_roll = stable_u01("retire.consider", inp.player_id, int(inp.season_year))
    considered = bool(consider_roll < consider_prob)

    decision_z = (
        float(cfg.decision_bias)
        + float(cfg.decision_w_age) * age_f
        + float(cfg.decision_w_injury_burden) * injury_burden
        + float(cfg.decision_w_teamless) * teamless_f
        + float(cfg.decision_w_loyalty) * loy
        + float(cfg.decision_w_ego) * ego
        + float(cfg.decision_w_ambition) * amb
        + float(cfg.decision_w_work_ethic) * work
        + float(cfg.decision_w_adaptability) * adapt
        + float(cfg.decision_w_coachability) * coach
        + float(cfg.interaction_age_injury_burden) * (age_f * injury_burden)
        + float(cfg.interaction_teamless_loyalty) * (teamless_f * loy)
        + float(cfg.interaction_ambition_adaptability) * (amb * adapt)
    )
    if int(inp.ovr) >= int(cfg.elite_ovr_guard):
        decision_z -= float(cfg.elite_ovr_z_penalty)

    retirement_prob = sigmoid(float(decision_z))

    # Commercial-grade clipping/guards: keep plausible tails.
    retirement_prob = clamp(retirement_prob, float(cfg.hard_floor_prob), float(cfg.hard_ceiling_prob))
    if int(inp.age) <= int(cfg.youth_age_guard):
        retirement_prob = min(float(retirement_prob), float(cfg.youth_prob_cap))

    final_roll = stable_u01("retire.final", inp.player_id, int(inp.season_year))
    retired = bool(considered and final_roll < retirement_prob)

    explanation: Dict[str, Any] = {
        "age_factor": float(age_f),
        "injury_burden": float(injury_burden),
        "injury_terms": dict(injury_terms),
        "teamless_factor": float(teamless_f),
        "mental": {
            "work_ethic": float(work),
            "coachability": float(coach),
            "ambition": float(amb),
            "loyalty": float(loy),
            "ego": float(ego),
            "adaptability": float(adapt),
        },
        "consider_z": float(consider_z),
        "decision_z": float(decision_z),
        "consider_roll": float(consider_roll),
    }
    inputs_json: Dict[str, Any] = {
        "age": int(inp.age),
        "ovr": int(inp.ovr),
        "team_id": str(inp.team_id),
        "injury_status": str(inp.injury_status),
        "injury_severity": int(inp.injury_severity),
        "injury_context": dict(inp.injury_context or {}),
        "mental": dict(inp.mental or {}),
    }

    return RetirementDecision(
        player_id=str(inp.player_id),
        season_year=int(inp.season_year),
        considered=bool(considered),
        decision="RETIRED" if retired else "STAY",
        consider_prob=float(consider_prob),
        retirement_prob=float(retirement_prob),
        random_roll=float(final_roll),
        age=int(inp.age),
        team_id=str(inp.team_id),
        injury_status=str(inp.injury_status),
        inputs=inputs_json,
        explanation=explanation,
    )
