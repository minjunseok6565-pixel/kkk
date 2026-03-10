from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from trades.pick_semantics import resolve_pick_protection, resolve_swap_outcome
from trades.protection import normalize_protection

from .draft_lottery_rules import DraftLotteryRules
from .types import PickSnapshot, SwapSnapshot

_EPS = 1e-9


@dataclass(frozen=True, slots=True)
class PickDistributionBundle:
    pmf: Mapping[int, float]
    cdf: Mapping[int, float]
    ev_pick: float
    variance: float
    scenario_notes: tuple[str, ...]
    compat_expected_pick_number: float | None
    p10_pick: int | None = None
    p50_pick: int | None = None
    p90_pick: int | None = None
    tail_upside_prob: float | None = None
    tail_downside_prob: float | None = None
    source_coverage: Mapping[str, bool] = field(default_factory=dict)


def _normalize_pmf(pmf: Mapping[int, float]) -> dict[int, float]:
    cleaned: dict[int, float] = {}
    total = 0.0
    for k, v in pmf.items():
        pick_num = int(k)
        prob = float(v)
        if prob <= 0.0:
            continue
        cleaned[pick_num] = cleaned.get(pick_num, 0.0) + prob
        total += prob

    if total <= 0.0:
        return {}

    return {k: (v / total) for k, v in sorted(cleaned.items())}


def _validate_prob_mass_one(pmf: Mapping[int, float]) -> bool:
    return abs(sum(float(v) for v in pmf.values()) - 1.0) <= _EPS


def _cdf_from_pmf(pmf: Mapping[int, float]) -> dict[int, float]:
    running = 0.0
    out: dict[int, float] = {}
    for pick_num in sorted(pmf.keys()):
        running += float(pmf[pick_num])
        out[int(pick_num)] = min(1.0, running)
    return out


def _quantile_pick(cdf: Mapping[int, float], q: float) -> int | None:
    if not cdf:
        return None
    target = float(q)
    for pick_num in sorted(cdf.keys()):
        if float(cdf[pick_num]) + _EPS >= target:
            return int(pick_num)
    return int(max(cdf.keys()))


def _ev_variance(pmf: Mapping[int, float]) -> tuple[float, float]:
    if not pmf:
        return 0.0, 0.0
    ev = sum(float(k) * float(v) for k, v in pmf.items())
    var = sum((float(k) - ev) ** 2 * float(v) for k, v in pmf.items())
    return float(ev), float(var)


def _baseline_pmf_for_pick(
    pick: PickSnapshot,
    standings_index: Mapping[str, int],
    season_rules: DraftLotteryRules,
) -> tuple[dict[int, float], list[str], dict[str, bool]]:
    notes: list[str] = []
    coverage = {
        "standings": True,
        "season_rules": True,
        "protection": True,
        "swap": True,
    }

    team_key = str(pick.original_team).upper()
    standing = standings_index.get(team_key)
    if standing is None:
        coverage["standings"] = False
        notes.append(f"MISSING_INPUT_STANDINGS:{pick.pick_id}")
        return ({}, notes, coverage)

    team_count = int(season_rules.team_count)

    if int(pick.round) == 1:
        pmf = dict(season_rules.first_round_pmf_by_standing.get(int(standing), {}))
        pmf_norm = _normalize_pmf(pmf)
        if not pmf_norm:
            coverage["season_rules"] = False
            notes.append(f"MISSING_INPUT_LOTTERY_RULE_PMF:{pick.pick_id}")
            return ({}, notes, coverage)
        return (pmf_norm, notes, coverage)

    overall_pick = (int(pick.round) - 1) * team_count + int(standing)
    return ({int(overall_pick): 1.0}, notes, coverage)


def _validate_supported_protection(
    pmf: Mapping[int, float],
    pick: PickSnapshot,
) -> tuple[dict[int, float], list[str], bool, bool]:
    notes: list[str] = []
    coverage_ok = True
    has_protection = False

    raw_protection = pick.protection
    if raw_protection is None:
        return (dict(pmf), notes, coverage_ok, has_protection)

    has_protection = True

    try:
        normalized = normalize_protection(raw_protection, pick_id=pick.pick_id)
    except Exception:
        notes.append(f"UNSUPPORTED_RULE_PROTECTION:{pick.pick_id}")
        coverage_ok = False
        return (dict(pmf), notes, coverage_ok, has_protection)

    p_type = str(normalized.get("type") or "").upper()
    if p_type != "TOP_N":
        notes.append(f"UNSUPPORTED_RULE_PROTECTION:{pick.pick_id}")
        coverage_ok = False
        return (dict(pmf), notes, coverage_ok, has_protection)

    return (dict(pmf), notes, coverage_ok, has_protection)


def _apply_protection_semantics_probability_wrapper(
    *,
    pick: PickSnapshot,
    pmf: Mapping[int, float],
) -> tuple[dict[int, float], list[str], bool]:
    notes: list[str] = []
    coverage_ok = True
    if not pmf or pick.protection is None:
        return (dict(pmf), notes, coverage_ok)

    conveyed_prob = 0.0
    protected_prob = 0.0
    out: dict[int, float] = {}
    for slot, prob in pmf.items():
        try:
            res = resolve_pick_protection(
                pick_id=pick.pick_id,
                slot=int(slot),
                owner_team=pick.owner_team,
                original_team=pick.original_team,
                protection=pick.protection,
            )
        except Exception:
            notes.append(f"UNSUPPORTED_RULE_PROTECTION:{pick.pick_id}")
            coverage_ok = False
            return (dict(pmf), notes, coverage_ok)

        if res.owner_team_after == str(pick.owner_team).upper():
            conveyed_prob += float(prob)
        if res.protected:
            protected_prob += float(prob)
        # protection 정산은 owner/보상에 영향, slot 값 자체는 유지된다.
        out[int(slot)] = out.get(int(slot), 0.0) + float(prob)

    notes.append(f"PROTECTION_PROTECTED_PROB:{pick.pick_id}:{protected_prob:.6f}")
    notes.append(f"PROTECTION_CONVEYED_PROB:{pick.pick_id}:{conveyed_prob:.6f}")
    return (_normalize_pmf(out), notes, coverage_ok)


def _apply_swaps(
    *,
    pick: PickSnapshot,
    current_pmf: Mapping[int, float],
    pick_by_id: Mapping[str, PickSnapshot],
    pmf_by_pick_id: Mapping[str, Mapping[int, float]],
    swaps: Sequence[SwapSnapshot],
) -> tuple[dict[int, float], list[str], bool]:
    notes: list[str] = []
    coverage_ok = True
    pmf = dict(current_pmf)

    for swap in swaps:
        if not bool(swap.active):
            continue
        if int(swap.year or 0) != int(pick.year):
            continue
        if int(swap.round or 0) != int(pick.round):
            continue
        if pick.pick_id not in {swap.pick_id_a, swap.pick_id_b}:
            continue

        other_pick_id = swap.pick_id_b if pick.pick_id == swap.pick_id_a else swap.pick_id_a
        other_pick = pick_by_id.get(other_pick_id)
        other_pmf = pmf_by_pick_id.get(other_pick_id)
        if other_pick is None or not other_pmf:
            notes.append(f"MISSING_INPUT_SWAP_COUNTERPART:{swap.swap_id}")
            coverage_ok = False
            continue

        swap_out: dict[int, float] = {}
        swap_applied = False
        for slot_self, prob_self in pmf.items():
            for slot_other, prob_other in other_pmf.items():
                joint = float(prob_self) * float(prob_other)
                if joint <= 0.0:
                    continue

                try:
                    prot_self = resolve_pick_protection(
                        pick_id=pick.pick_id,
                        slot=int(slot_self),
                        owner_team=pick.owner_team,
                        original_team=pick.original_team,
                        protection=pick.protection,
                    )
                    prot_other = resolve_pick_protection(
                        pick_id=other_pick.pick_id,
                        slot=int(slot_other),
                        owner_team=other_pick.owner_team,
                        original_team=other_pick.original_team,
                        protection=other_pick.protection,
                    )
                    swap_res = resolve_swap_outcome(
                        swap_id=swap.swap_id,
                        pick_id_a=swap.pick_id_a,
                        pick_id_b=swap.pick_id_b,
                        slot_a=int(slot_self) if pick.pick_id == swap.pick_id_a else int(slot_other),
                        slot_b=int(slot_other) if pick.pick_id == swap.pick_id_a else int(slot_self),
                        owner_team=swap.owner_team,
                        owner_a=(prot_self.owner_team_after if pick.pick_id == swap.pick_id_a else prot_other.owner_team_after),
                        owner_b=(prot_other.owner_team_after if pick.pick_id == swap.pick_id_a else prot_self.owner_team_after),
                    )
                except Exception:
                    notes.append(f"UNSUPPORTED_RULE_SWAP:{swap.swap_id}")
                    coverage_ok = False
                    swap_out = dict(pmf)
                    break

                if not swap_res.exercisable:
                    resolved = int(slot_self)
                elif not swap_res.swap_executed:
                    resolved = int(slot_self)
                else:
                    pick_owner_before_swap = prot_self.owner_team_after
                    owner_team = str(swap.owner_team).upper()
                    if pick_owner_before_swap == owner_team:
                        resolved = min(int(slot_self), int(slot_other))
                    else:
                        resolved = max(int(slot_self), int(slot_other))
                    swap_applied = True

                swap_out[resolved] = swap_out.get(resolved, 0.0) + joint
            else:
                continue
            break

        if not swap_out:
            notes.append(f"MISSING_INPUT_SWAP_OUTCOME:{swap.swap_id}")
            coverage_ok = False
            continue

        pmf = _normalize_pmf(swap_out)
        notes.append(f"APPLIED_SWAP:{swap.swap_id}" if swap_applied else f"SWAP_NOOP:{swap.swap_id}")

    return (pmf, notes, coverage_ok)


def _tail_probs(pmf: Mapping[int, float]) -> tuple[float | None, float | None]:
    if not pmf:
        return (None, None)
    upside = sum(prob for pick, prob in pmf.items() if int(pick) <= 5)
    downside = sum(prob for pick, prob in pmf.items() if int(pick) >= 26)
    return (float(upside), float(downside))


def build_pick_distributions_from_standings(
    *,
    picks: Sequence[PickSnapshot],
    swaps: Sequence[SwapSnapshot],
    standings_order_worst_to_best: Sequence[str],
    season_rules: DraftLotteryRules,
) -> dict[str, PickDistributionBundle]:
    standings_index = {str(team).upper(): idx + 1 for idx, team in enumerate(standings_order_worst_to_best)}
    pick_by_id = {str(p.pick_id): p for p in picks}

    baseline_by_pick_id: dict[str, dict[int, float]] = {}
    baseline_notes: dict[str, list[str]] = {}
    baseline_coverage: dict[str, dict[str, bool]] = {}

    # 1) PickSnapshot.protection 경로 고정
    for pick in picks:
        base_pmf, notes, coverage = _baseline_pmf_for_pick(pick, standings_index, season_rules)
        protected_pmf, p_notes, protection_ok, has_protection = _validate_supported_protection(base_pmf, pick)
        coverage["protection"] = bool(coverage.get("protection", True) and protection_ok)
        wrapped_pmf, pw_notes, pw_ok = _apply_protection_semantics_probability_wrapper(
            pick=pick,
            pmf=protected_pmf,
        )
        coverage["protection"] = bool(coverage.get("protection", True) and pw_ok)
        baseline_by_pick_id[pick.pick_id] = _normalize_pmf(wrapped_pmf)
        protection_notes: list[str] = []
        if has_protection and protection_ok and pw_ok:
            protection_notes.append(f"APPLIED_TOP_N_PROTECTION_SEMANTICS:{pick.pick_id}")
        baseline_notes[pick.pick_id] = [*notes, *p_notes, *pw_notes, *protection_notes]
        baseline_coverage[pick.pick_id] = coverage

    bundles: dict[str, PickDistributionBundle] = {}

    # 2) SwapSnapshot 경로 고정
    for pick in picks:
        pmf_after_swap, swap_notes, swap_ok = _apply_swaps(
            pick=pick,
            current_pmf=baseline_by_pick_id.get(pick.pick_id, {}),
            pick_by_id=pick_by_id,
            pmf_by_pick_id=baseline_by_pick_id,
            swaps=swaps,
        )

        coverage = dict(baseline_coverage.get(pick.pick_id, {}))
        coverage["swap"] = bool(coverage.get("swap", True) and swap_ok)

        pmf = _normalize_pmf(pmf_after_swap)
        notes = [*baseline_notes.get(pick.pick_id, []), *swap_notes]

        if not _validate_prob_mass_one(pmf):
            notes.append(f"MISSING_INPUT_PMF_NORMALIZATION:{pick.pick_id}")
            coverage["season_rules"] = False
            pmf = _normalize_pmf(pmf)

        cdf = _cdf_from_pmf(pmf)
        ev, var = _ev_variance(pmf)
        tail_upside_prob, tail_downside_prob = _tail_probs(pmf)

        bundles[pick.pick_id] = PickDistributionBundle(
            pmf=pmf,
            cdf=cdf,
            ev_pick=ev,
            variance=var,
            scenario_notes=tuple(notes),
            compat_expected_pick_number=(ev if pmf else None),
            p10_pick=_quantile_pick(cdf, 0.10),
            p50_pick=_quantile_pick(cdf, 0.50),
            p90_pick=_quantile_pick(cdf, 0.90),
            tail_upside_prob=tail_upside_prob,
            tail_downside_prob=tail_downside_prob,
            source_coverage=coverage,
        )

    return bundles
