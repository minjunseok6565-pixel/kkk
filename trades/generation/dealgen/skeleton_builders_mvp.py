from __future__ import annotations

from typing import List

from .skeleton_builders_tier_score_common import (
    STYLE_MIXED,
    STYLE_PICK_HEAVY,
    STYLE_PLAYER_HEAVY,
    build_tier_style_skeleton,
)
from .skeleton_registry import BuildContext
from .types import DealCandidate


TIER = "MVP"


def build_mvp_player_heavy(ctx: BuildContext) -> List[DealCandidate]:
    return build_tier_style_skeleton(ctx, tier=TIER, style=STYLE_PLAYER_HEAVY, skeleton_id="mvp.player_heavy")


def build_mvp_pick_heavy(ctx: BuildContext) -> List[DealCandidate]:
    return build_tier_style_skeleton(ctx, tier=TIER, style=STYLE_PICK_HEAVY, skeleton_id="mvp.pick_heavy")


def build_mvp_mixed(ctx: BuildContext) -> List[DealCandidate]:
    return build_tier_style_skeleton(ctx, tier=TIER, style=STYLE_MIXED, skeleton_id="mvp.mixed")


__all__ = ["build_mvp_player_heavy", "build_mvp_pick_heavy", "build_mvp_mixed"]
