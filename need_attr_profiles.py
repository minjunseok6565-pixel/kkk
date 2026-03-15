from __future__ import annotations

"""SSOT for new need-tag attribute profiles.

- Defines the new OFF_*/DEF_* tag namespace.
- Keeps per-tag attr profile placeholders for future formula tuning.
- Validates attrs with strict 0..99 contract.
"""

from typing import Dict, Final, Mapping, Optional, Tuple

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

POSITION_PREFIXES: Final[Tuple[str, ...]] = ("G_", "W_", "B_")

# Role-family mapping by position prefix.
# A role can belong to multiple positions (e.g. OFF_TRANSITION_ENGINE -> G/W).
PREFIX_TO_POSITION: Final[Dict[str, str]] = {"G_": "G", "W_": "W", "B_": "B"}

TAG_POSITIONS: Final[Dict[NeedTag, frozenset[str]]] = {
    # Guard
    "OFF_ENGINE_PRIMARY": frozenset({"G"}),
    "OFF_ENGINE_SECONDARY": frozenset({"G"}),
    "OFF_TRANSITION_ENGINE": frozenset({"G", "W"}),
    "OFF_SHOT_CREATOR": frozenset({"G", "W"}),
    "OFF_SPOTUP_SPACER": frozenset({"G", "W", "B"}),
    "OFF_MOVEMENT_SHOOTER": frozenset({"G", "W"}),
    "DEF_ZONE_TOP_LEFT": frozenset({"G"}),
    "DEF_ZONE_TOP_RIGHT": frozenset({"G"}),
    "DEF_PNR_POA_DEFENDER": frozenset({"G"}),
    "DEF_PNR_POA_BLITZ": frozenset({"G"}),
    "DEF_PNR_POA_SWITCH": frozenset({"G"}),
    "DEF_PNR_POA_SWITCH_1_4": frozenset({"G"}),
    "DEF_PNR_POA_AT_THE_LEVEL": frozenset({"G"}),
    # Wing
    "OFF_RIM_PRESSURE": frozenset({"W", "B"}),
    "OFF_CUTTER_FINISHER": frozenset({"W"}),
    "OFF_CONNECTOR": frozenset({"W", "B"}),
    "OFF_ROLL_MAN": frozenset({"W", "B"}),
    "DEF_LOWMAN_HELPER": frozenset({"W"}),
    "DEF_NAIL_HELPER": frozenset({"W"}),
    "DEF_WEAKSIDE_ROTATOR": frozenset({"W"}),
    "DEF_SWITCH_WING_STRONG": frozenset({"W"}),
    "DEF_SWITCH_WING_WEAK": frozenset({"W"}),
    "DEF_SWITCH_WING_STRONG_1_4": frozenset({"W"}),
    "DEF_SWITCH_WING_WEAK_1_4": frozenset({"W"}),
    # Big
    "OFF_SHORTROLL_HUB": frozenset({"B"}),
    "OFF_POP_THREAT": frozenset({"B"}),
    "OFF_POST_ANCHOR": frozenset({"B"}),
    "DEF_ZONE_BOTTOM_LEFT": frozenset({"B"}),
    "DEF_ZONE_BOTTOM_RIGHT": frozenset({"B"}),
    "DEF_ZONE_BOTTOM_CENTER": frozenset({"B"}),
    "DEF_PNR_COVER_BIG_DROP": frozenset({"B"}),
    "DEF_PNR_COVER_BIG_BLITZ": frozenset({"B"}),
    "DEF_BACKLINE_ANCHOR": frozenset({"B"}),
    "DEF_PNR_COVER_BIG_SWITCH": frozenset({"B"}),
    "DEF_PNR_COVER_BIG_SWITCH_1_4": frozenset({"B"}),
    "DEF_PNR_COVER_BIG_HEDGE_RECOVER": frozenset({"B"}),
    "DEF_PNR_COVER_BIG_AT_THE_LEVEL": frozenset({"B"}),
}


def _w(**weights: float) -> Dict[str, float]:
    total = sum(float(v) for v in weights.values())
    if total <= 0.0:
        return {}
    return {k: float(v) / total for k, v in weights.items()}

# Per-tag raw-attribute formulas (0..99 domain).
# 철학:
# - OFF_*: 볼핸들/슈팅/의사결정/피니시/피지컬 축의 역할별 비중
# - DEF_*: POA(외곽), Help/Rotation(약측), Rim/Post(골밑) 축으로 분기
TAG_ATTR_WEIGHTS: Final[Dict[NeedTag, Dict[str, float]]] = {
    # ---- OFF ----
    "OFF_ENGINE_PRIMARY": _w(**{"Pass IQ": 0.19, "Pass Vision": 0.15, "Pass Accuracy": 0.14, "Ball Handle": 0.16, "Speed with Ball": 0.12, "Shot IQ": 0.12, "Agility": 0.07, "Three-Point Shot": 0.05}),
    "OFF_ENGINE_SECONDARY": _w(**{"Three-Point Shot": 0.20, "Pass Accuracy": 0.15, "Pass IQ": 0.14, "Pass Vision": 0.12, "Ball Handle": 0.13, "Shot IQ": 0.10, "Speed with Ball": 0.08, "Agility": 0.08}),
    "OFF_TRANSITION_ENGINE": _w(**{"Speed": 0.18, "Stamina": 0.16, "Speed with Ball": 0.16, "Ball Handle": 0.13, "Pass Vision": 0.13, "Pass Accuracy": 0.11, "Layup": 0.08, "Shot IQ": 0.05}),
    "OFF_SHOT_CREATOR": _w(**{"Three-Point Shot": 0.19, "Mid-Range Shot": 0.17, "Ball Handle": 0.16, "Speed with Ball": 0.12, "Agility": 0.10, "Shot IQ": 0.10, "Free Throw": 0.08, "Pass Vision": 0.08}),
    "OFF_RIM_PRESSURE": _w(**{"Speed with Ball": 0.19, "Ball Handle": 0.15, "Speed": 0.13, "Agility": 0.11, "Layup": 0.12, "Draw Foul": 0.10, "Strength": 0.10, "Vertical": 0.10}),
    "OFF_SPOTUP_SPACER": _w(**{"Three-Point Shot": 0.46, "Shot IQ": 0.14, "Mid-Range Shot": 0.11, "Offensive Consistency": 0.09, "Hands": 0.08, "Pass Accuracy": 0.06, "Stamina": 0.06}),
    "OFF_MOVEMENT_SHOOTER": _w(**{"Three-Point Shot": 0.34, "Stamina": 0.18, "Agility": 0.12, "Shot IQ": 0.10, "Offensive Consistency": 0.09, "Mid-Range Shot": 0.08, "Speed": 0.05, "Ball Handle": 0.04}),
    "OFF_CUTTER_FINISHER": _w(**{"Layup": 0.22, "Driving Dunk": 0.18, "Close Shot": 0.15, "Speed": 0.13, "Vertical": 0.12, "Hands": 0.08, "Strength": 0.07, "Offensive Rebound": 0.05}),
    "OFF_CONNECTOR": _w(**{"Pass Accuracy": 0.22, "Pass IQ": 0.18, "Pass Vision": 0.17, "Hands": 0.12, "Ball Handle": 0.12, "Shot IQ": 0.10, "Three-Point Shot": 0.09}),
    "OFF_ROLL_MAN": _w(**{"Close Shot": 0.17, "Standing Dunk": 0.17, "Driving Dunk": 0.12, "Vertical": 0.13, "Strength": 0.12, "Offensive Rebound": 0.12, "Hands": 0.09, "Stamina": 0.08}),
    "OFF_SHORTROLL_HUB": _w(**{"Pass IQ": 0.22, "Pass Accuracy": 0.18, "Pass Vision": 0.14, "Hands": 0.13, "Close Shot": 0.10, "Ball Handle": 0.09, "Shot IQ": 0.08, "Mid-Range Shot": 0.06}),
    "OFF_POP_THREAT": _w(**{"Three-Point Shot": 0.30, "Mid-Range Shot": 0.18, "Shot IQ": 0.14, "Hands": 0.11, "Pass Accuracy": 0.09, "Pass IQ": 0.08, "Defensive Rebound": 0.06, "Stamina": 0.04}),
    "OFF_POST_ANCHOR": _w(**{"Post Control": 0.22, "Post Hook": 0.17, "Post Fade": 0.14, "Close Shot": 0.11, "Strength": 0.12, "Hands": 0.10, "Pass IQ": 0.08, "Shot IQ": 0.06}),
    # ---- DEF ----
    "DEF_ZONE_TOP_LEFT": _w(**{"Perimeter Defense": 0.31, "Help Defense IQ": 0.18, "Steal": 0.17, "Pass Perception": 0.16, "Agility": 0.10, "Defensive Consistency": 0.08}),
    "DEF_ZONE_TOP_RIGHT": _w(**{"Perimeter Defense": 0.31, "Help Defense IQ": 0.18, "Steal": 0.17, "Pass Perception": 0.16, "Agility": 0.10, "Defensive Consistency": 0.08}),
    "DEF_PNR_POA_DEFENDER": _w(**{"Perimeter Defense": 0.39, "Agility": 0.18, "Speed": 0.14, "Steal": 0.11, "Help Defense IQ": 0.10, "Defensive Consistency": 0.08}),
    "DEF_PNR_POA_BLITZ": _w(**{"Perimeter Defense": 0.29, "Steal": 0.23, "Pass Perception": 0.18, "Speed": 0.12, "Agility": 0.10, "Help Defense IQ": 0.08}),
    "DEF_PNR_POA_SWITCH": _w(**{"Perimeter Defense": 0.31, "Agility": 0.19, "Strength": 0.17, "Speed": 0.12, "Help Defense IQ": 0.11, "Defensive Consistency": 0.10}),
    "DEF_PNR_POA_SWITCH_1_4": _w(**{"Perimeter Defense": 0.33, "Agility": 0.19, "Strength": 0.13, "Speed": 0.11, "Help Defense IQ": 0.12, "Interior Defense": 0.07, "Defensive Consistency": 0.05}),
    "DEF_PNR_POA_AT_THE_LEVEL": _w(**{"Perimeter Defense": 0.30, "Agility": 0.17, "Speed": 0.15, "Block": 0.14, "Help Defense IQ": 0.12, "Interior Defense": 0.12}),
    "DEF_LOWMAN_HELPER": _w(**{"Help Defense IQ": 0.33, "Interior Defense": 0.19, "Block": 0.15, "Pass Perception": 0.12, "Defensive Consistency": 0.11, "Hustle": 0.10}),
    "DEF_NAIL_HELPER": _w(**{"Help Defense IQ": 0.30, "Pass Perception": 0.21, "Perimeter Defense": 0.17, "Steal": 0.12, "Defensive Consistency": 0.10, "Agility": 0.10}),
    "DEF_WEAKSIDE_ROTATOR": _w(**{"Help Defense IQ": 0.30, "Hustle": 0.18, "Pass Perception": 0.16, "Agility": 0.14, "Speed": 0.12, "Defensive Consistency": 0.10}),
    "DEF_SWITCH_WING_STRONG": _w(**{"Perimeter Defense": 0.27, "Strength": 0.23, "Agility": 0.14, "Interior Defense": 0.12, "Help Defense IQ": 0.12, "Block": 0.12}),
    "DEF_SWITCH_WING_WEAK": _w(**{"Perimeter Defense": 0.26, "Agility": 0.20, "Help Defense IQ": 0.17, "Pass Perception": 0.14, "Speed": 0.13, "Defensive Consistency": 0.10}),
    "DEF_SWITCH_WING_STRONG_1_4": _w(**{"Perimeter Defense": 0.28, "Strength": 0.20, "Agility": 0.15, "Interior Defense": 0.12, "Help Defense IQ": 0.15, "Block": 0.10}),
    "DEF_SWITCH_WING_WEAK_1_4": _w(**{"Perimeter Defense": 0.27, "Agility": 0.20, "Help Defense IQ": 0.18, "Pass Perception": 0.14, "Speed": 0.11, "Defensive Consistency": 0.10}),
    "DEF_ZONE_BOTTOM_LEFT": _w(**{"Interior Defense": 0.28, "Block": 0.22, "Help Defense IQ": 0.18, "Defensive Rebound": 0.14, "Strength": 0.10, "Vertical": 0.08}),
    "DEF_ZONE_BOTTOM_RIGHT": _w(**{"Interior Defense": 0.28, "Block": 0.22, "Help Defense IQ": 0.18, "Defensive Rebound": 0.14, "Strength": 0.10, "Vertical": 0.08}),
    "DEF_ZONE_BOTTOM_CENTER": _w(**{"Interior Defense": 0.25, "Block": 0.24, "Help Defense IQ": 0.18, "Defensive Rebound": 0.15, "Strength": 0.10, "Vertical": 0.08}),
    "DEF_PNR_COVER_BIG_DROP": _w(**{"Interior Defense": 0.24, "Block": 0.24, "Help Defense IQ": 0.19, "Defensive Rebound": 0.13, "Strength": 0.11, "Vertical": 0.09}),
    "DEF_PNR_COVER_BIG_BLITZ": _w(**{"Help Defense IQ": 0.26, "Perimeter Defense": 0.21, "Block": 0.17, "Steal": 0.13, "Agility": 0.13, "Pass Perception": 0.10}),
    "DEF_BACKLINE_ANCHOR": _w(**{"Block": 0.31, "Interior Defense": 0.25, "Help Defense IQ": 0.18, "Defensive Rebound": 0.11, "Strength": 0.09, "Vertical": 0.06}),
    "DEF_PNR_COVER_BIG_SWITCH": _w(**{"Perimeter Defense": 0.23, "Agility": 0.18, "Interior Defense": 0.16, "Strength": 0.16, "Help Defense IQ": 0.14, "Block": 0.13}),
    "DEF_PNR_COVER_BIG_SWITCH_1_4": _w(**{"Perimeter Defense": 0.24, "Agility": 0.19, "Interior Defense": 0.15, "Help Defense IQ": 0.15, "Strength": 0.14, "Block": 0.13}),
    "DEF_PNR_COVER_BIG_HEDGE_RECOVER": _w(**{"Perimeter Defense": 0.24, "Agility": 0.20, "Speed": 0.16, "Help Defense IQ": 0.15, "Block": 0.14, "Stamina": 0.11}),
    "DEF_PNR_COVER_BIG_AT_THE_LEVEL": _w(**{"Perimeter Defense": 0.23, "Agility": 0.19, "Speed": 0.16, "Help Defense IQ": 0.15, "Block": 0.14, "Interior Defense": 0.13}),
}

ALL_CANONICAL_AND_PREFIXED_TAGS: Final[frozenset[str]] = frozenset(
    set(ALL_NEW_NEED_TAGS)
    | {
        f"{prefix}{tag}"
        for prefix, pos in PREFIX_TO_POSITION.items()
        for tag, positions in TAG_POSITIONS.items()
        if pos in positions
    }
)


def validate_attrs_0_99(attrs: Mapping[str, float]) -> None:
    for k, v in attrs.items():
        fv = float(v)
        if fv < 0.0 or fv > 99.0:
            raise ValueError(f"attr out of range 0..99: {k}={fv}")


def norm_99(v: float) -> float:
    fv = float(v)
    if fv <= 0.0:
        return 0.0
    if fv >= 99.0:
        return 1.0
    return fv / 99.0


def _split_prefixed_tag(tag: str) -> Tuple[str, Optional[str]]:
    t = str(tag or "").strip().upper()
    for pref, pos in PREFIX_TO_POSITION.items():
        if t.startswith(pref):
            return t[len(pref):], pos
    return t, None


def _is_allowed_prefixed_tag(tag: str) -> bool:
    canonical, pos = _split_prefixed_tag(tag)
    if pos is None:
        return canonical in ALL_NEW_NEED_TAGS
    allowed_positions = TAG_POSITIONS.get(canonical)
    return bool(allowed_positions and pos in allowed_positions)


def score_tag(tag: str, attrs: Mapping[str, float], *, strict: bool = True) -> float:
    if strict:
        validate_attrs_0_99(attrs)
    if not _is_allowed_prefixed_tag(tag):
        return 0.0
    t, _ = _split_prefixed_tag(tag)
    weights = TAG_ATTR_WEIGHTS.get(t, {})
    if not weights:
        return 0.0
    acc = 0.0
    for k, w in weights.items():
        v = float(attrs.get(k, 50.0))
        acc += float(w) * norm_99(v)
    if acc <= 0.0:
        return 0.0
    if acc >= 1.0:
        return 1.0
    return float(acc)


def tag_supply(
    player_attrs: Mapping[str, float],
    *,
    strict: bool = True,
    active_tags: Optional[Mapping[str, float] | Tuple[str, ...] | list[str] | set[str]] = None,
) -> Dict[str, float]:
    if strict:
        validate_attrs_0_99(player_attrs)

    if active_tags is None:
        tags = sorted(ALL_CANONICAL_AND_PREFIXED_TAGS)
    else:
        tags = [str(t).strip().upper() for t in active_tags if str(t).strip()]

    out: Dict[str, float] = {}
    for tag in tags:
        sc = score_tag(tag, player_attrs, strict=False)
        if sc > 0.0:
            out[tag] = sc
    return out
