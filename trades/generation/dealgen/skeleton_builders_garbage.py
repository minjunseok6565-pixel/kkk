from __future__ import annotations

from typing import List

from .skeleton_builders_tier_score_common import STYLE_MIXED, build_tier_style_skeleton
from .skeleton_registry import BuildContext
from .types import DealCandidate


TIER = "GARBAGE"


def build_garbage_garbage(ctx: BuildContext) -> List[DealCandidate]:
    return build_tier_style_skeleton(ctx, tier=TIER, style=STYLE_MIXED, skeleton_id="garbage.garbage")


__all__ = ["build_garbage_garbage"]
