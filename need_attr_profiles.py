from __future__ import annotations

"""SSOT for new need-tag attribute profiles.

- Defines the new OFF_*/DEF_* tag namespace.
- Keeps per-tag attr profile placeholders for future formula tuning.
- Validates attrs with strict 0..99 contract.
"""

from typing import Dict, Final, Mapping, Tuple

NeedTag = str

# ---------------------------------------------------------------------------
# New tag namespace (base tags)
# ---------------------------------------------------------------------------
OFF_NEED_TAGS: Final[Tuple[NeedTag, ...]] = (
    "OFF_ENGINE_PRIMARY",
    "OFF_ENGINE_SECONDARY",
    "OFF_TRANSITION_ENGINE",
    "OFF_SHOT_CREATOR",
    "OFF_RIM_PRESSURE",
    "OFF_SPOTUP_SPACER",
    "OFF_MOVEMENT_SHOOTER",
    "OFF_CUTTER_FINISHER",
    "OFF_CONNECTOR",
    "OFF_ROLL_MAN",
    "OFF_SHORTROLL_HUB",
    "OFF_POP_THREAT",
    "OFF_POST_ANCHOR",
)

DEF_NEED_TAGS: Final[Tuple[NeedTag, ...]] = (
    "DEF_ZONE_TOP_LEFT",
    "DEF_ZONE_TOP_RIGHT",
    "DEF_PNR_POA_DEFENDER",
    "DEF_PNR_POA_BLITZ",
    "DEF_PNR_POA_SWITCH",
    "DEF_PNR_POA_SWITCH_1_4",
    "DEF_PNR_POA_AT_THE_LEVEL",
    "DEF_LOWMAN_HELPER",
    "DEF_NAIL_HELPER",
    "DEF_WEAKSIDE_ROTATOR",
    "DEF_SWITCH_WING_STRONG",
    "DEF_SWITCH_WING_WEAK",
    "DEF_SWITCH_WING_STRONG_1_4",
    "DEF_SWITCH_WING_WEAK_1_4",
    "DEF_ZONE_BOTTOM_LEFT",
    "DEF_ZONE_BOTTOM_RIGHT",
    "DEF_ZONE_BOTTOM_CENTER",
    "DEF_PNR_COVER_BIG_DROP",
    "DEF_PNR_COVER_BIG_BLITZ",
    "DEF_BACKLINE_ANCHOR",
    "DEF_PNR_COVER_BIG_SWITCH",
    "DEF_PNR_COVER_BIG_SWITCH_1_4",
    "DEF_PNR_COVER_BIG_HEDGE_RECOVER",
    "DEF_PNR_COVER_BIG_AT_THE_LEVEL",
)

ALL_NEW_NEED_TAGS: Final[frozenset[str]] = frozenset(set(OFF_NEED_TAGS) | set(DEF_NEED_TAGS))

# Placeholder per-tag attribute weights (to be filled later)
TAG_ATTR_WEIGHTS: Final[Dict[NeedTag, Dict[str, float]]] = {tag: {} for tag in sorted(ALL_NEW_NEED_TAGS)}


def validate_attrs_0_99(attrs: Mapping[str, float]) -> None:
    for k, v in attrs.items():
        fv = float(v)
        if fv < 0.0 or fv > 99.0:
            raise ValueError(f"attr out of range 0..99: {k}={fv}")


def tag_supply(player_attrs: Mapping[str, float], *, strict: bool = True) -> Dict[str, float]:
    if strict:
        validate_attrs_0_99(player_attrs)
    # Placeholder implementation for formula-tuning phase.
    return {}
