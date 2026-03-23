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


TIER = "ROTATION"


def build_rotation_player_heavy(ctx: BuildContext) -> List[DealCandidate]:
    return build_tier_style_skeleton(ctx, tier=TIER, style=STYLE_PLAYER_HEAVY, skeleton_id="rotation.player_heavy")


def build_rotation_pick_heavy(ctx: BuildContext) -> List[DealCandidate]:
    return build_tier_style_skeleton(ctx, tier=TIER, style=STYLE_PICK_HEAVY, skeleton_id="rotation.pick_heavy")


def build_rotation_mixed(ctx: BuildContext) -> List[DealCandidate]:
    return build_tier_style_skeleton(ctx, tier=TIER, style=STYLE_MIXED, skeleton_id="rotation.mixed")


__all__ = ["build_rotation_player_heavy", "build_rotation_pick_heavy", "build_rotation_mixed"]
