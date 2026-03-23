from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, List, Sequence, Tuple

if TYPE_CHECKING:
    from .skeleton_registry import SkeletonSpec


ROUTE_PHASE_COMBINED = "combined"
ROUTE_PHASE_TEMPLATE_ONLY = "template_only"
ROUTE_PHASE_FALLBACK_ONLY = "fallback_only"

VALID_ROUTE_PHASES: Tuple[str, ...] = (
    ROUTE_PHASE_COMBINED,
    ROUTE_PHASE_TEMPLATE_ONLY,
    ROUTE_PHASE_FALLBACK_ONLY,
)


@dataclass(frozen=True, slots=True)
class TemplateFallbackPlan:
    """Partitioned skeleton specs for template-first orchestration.

    - template_specs: stage-A candidates (domain == "template")
    - fallback_specs: stage-B candidates (everything else by default)
    """

    template_specs: Tuple[SkeletonSpec, ...]
    fallback_specs: Tuple[SkeletonSpec, ...]

    @property
    def has_template(self) -> bool:
        return bool(self.template_specs)

    @property
    def has_fallback(self) -> bool:
        return bool(self.fallback_specs)


@dataclass(frozen=True, slots=True)
class TemplateFallbackDecision:
    """Decision output for runtime policy checks in core loop."""

    should_fallback: bool
    reason: str = ""


def normalize_route_phase(route_phase: str) -> str:
    """Normalize route phase selector.

    Unknown values are treated as "combined" to preserve backward compatibility.
    """

    rp = str(route_phase or "").strip().lower()
    if rp in VALID_ROUTE_PHASES:
        return rp
    return ROUTE_PHASE_COMBINED


def is_template_spec(spec: SkeletonSpec) -> bool:
    return str(getattr(spec, "domain", "") or "").strip().lower() == "template"


def is_fallback_spec(spec: SkeletonSpec) -> bool:
    return not is_template_spec(spec)


def partition_specs_for_template_fallback(specs: Sequence[SkeletonSpec]) -> TemplateFallbackPlan:
    """Split mixed specs into template-stage and fallback-stage lists.

    Input ordering is preserved in each output bucket.
    """

    template_specs: List[SkeletonSpec] = []
    fallback_specs: List[SkeletonSpec] = []

    for spec in list(specs or tuple()):
        if is_template_spec(spec):
            template_specs.append(spec)
        else:
            fallback_specs.append(spec)

    return TemplateFallbackPlan(
        template_specs=tuple(template_specs),
        fallback_specs=tuple(fallback_specs),
    )


def select_specs_by_route_phase(specs: Sequence[SkeletonSpec], route_phase: str) -> Tuple[SkeletonSpec, ...]:
    """Filter specs according to `route_phase`.

    - combined: return specs as-is (tuple)
    - template_only: return template specs only
    - fallback_only: return fallback specs only
    """

    rp = normalize_route_phase(route_phase)
    plan = partition_specs_for_template_fallback(specs)

    if rp == ROUTE_PHASE_TEMPLATE_ONLY:
        return plan.template_specs
    if rp == ROUTE_PHASE_FALLBACK_ONLY:
        return plan.fallback_specs
    return tuple(specs or tuple())


def decide_fallback_after_template_stage(
    *,
    template_enabled: bool,
    fallback_enabled: bool,
    template_candidates_built: int,
    template_proposals_kept: int,
    min_keep_after_eval: int,
) -> TemplateFallbackDecision:
    """Runtime policy for entering fallback stage.

    Rules:
    1) template disabled -> no template stage; no fallback-by-template-policy trigger
    2) fallback disabled -> never fallback
    3) template stage built 0 candidates -> fallback (reason: template_stage_empty)
    4) template stage built candidates but kept < min_keep_after_eval -> fallback
       (reason: template_stage_all_discarded)
    5) otherwise do not fallback
    """

    if not bool(template_enabled):
        return TemplateFallbackDecision(False, "template_disabled")
    if not bool(fallback_enabled):
        return TemplateFallbackDecision(False, "fallback_disabled")

    built = max(0, int(template_candidates_built))
    kept = max(0, int(template_proposals_kept))
    min_keep = max(0, int(min_keep_after_eval))

    if built <= 0:
        return TemplateFallbackDecision(True, "template_stage_empty")

    if kept < max(1, min_keep):
        return TemplateFallbackDecision(True, "template_stage_all_discarded")

    return TemplateFallbackDecision(False, "template_stage_sufficient")


def iter_template_ids(specs: Iterable[SkeletonSpec]) -> Tuple[str, ...]:
    """Utility: stable list of template skeleton IDs for telemetry/debugging."""

    out: List[str] = []
    for spec in specs:
        if not is_template_spec(spec):
            continue
        sid = str(getattr(spec, "skeleton_id", "") or "")
        if sid:
            out.append(sid)
    return tuple(out)


__all__ = [
    "ROUTE_PHASE_COMBINED",
    "ROUTE_PHASE_TEMPLATE_ONLY",
    "ROUTE_PHASE_FALLBACK_ONLY",
    "VALID_ROUTE_PHASES",
    "TemplateFallbackPlan",
    "TemplateFallbackDecision",
    "normalize_route_phase",
    "is_template_spec",
    "is_fallback_spec",
    "partition_specs_for_template_fallback",
    "select_specs_by_route_phase",
    "decide_fallback_after_template_stage",
    "iter_template_ids",
]
