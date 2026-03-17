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


TIER = "STARTER"


def build_starter_player_heavy(ctx: BuildContext) -> List[DealCandidate]:
    return build_tier_style_skeleton(ctx, tier=TIER, style=STYLE_PLAYER_HEAVY, skeleton_id="starter.player_heavy")


def build_starter_pick_heavy(ctx: BuildContext) -> List[DealCandidate]:
    return build_tier_style_skeleton(ctx, tier=TIER, style=STYLE_PICK_HEAVY, skeleton_id="starter.pick_heavy")


def build_starter_mixed(ctx: BuildContext) -> List[DealCandidate]:
    return build_tier_style_skeleton(ctx, tier=TIER, style=STYLE_MIXED, skeleton_id="starter.mixed")


__all__ = ["build_starter_player_heavy", "build_starter_pick_heavy", "build_starter_mixed"]
