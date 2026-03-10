from __future__ import annotations

from typing import Dict, Final, Mapping, Sequence

DefenseRoleGroup = str
DefenseRoleName = str

"""SSOT for mapping scheme-specific defense roles into stable defense role groups.

This module intentionally contains only mapping standards so valuation/fit logic can
consume grouped defense semantics without duplicating role lists per scheme.
"""


DEFENSE_ROLE_GROUPS: Final[Dict[DefenseRoleGroup, tuple[DefenseRoleName, ...]]] = {
    "POA_DEFENDER": (
        "Zone_Top_Left",
        "Zone_Top_Right",
        "PnR_POA_Defender",
        "PnR_POA_Blitz",
        "PnR_POA_Switch",
        "PnR_POA_Switch_1_4",
        "PnR_POA_AtTheLevel",
    ),
    "INTERIOR_ANCHOR": (
        "Zone_Bottom_Left",
        "Zone_Bottom_Right",
        "Zone_Bottom_Center",
        "PnR_Cover_Big_Drop",
        "PnR_Cover_Big_Blitz",
        "Backline_Anchor",
        "PnR_Cover_Big_Switch",
        "PnR_Cover_Big_Switch_1_4",
        "PnR_Cover_Big_HedgeRecover",
        "PnR_Cover_Big_AtTheLevel",
    ),
    "WING_DEFENDER": (
        "Lowman_Helper",
        "Nail_Helper",
        "Weakside_Rotator",
        "Switch_Wing_Strong",
        "Switch_Wing_Weak",
        "Switch_Wing_Strong_1_4",
        "Switch_Wing_Weak_1_4",
    ),
}


ROLE_TO_DEFENSE_GROUP: Final[Dict[DefenseRoleName, DefenseRoleGroup]] = {
    role: group
    for group, roles in DEFENSE_ROLE_GROUPS.items()
    for role in roles
}


def role_to_defense_group(role: str) -> DefenseRoleGroup | None:
    """Map a scheme-specific defense role to a stable defense role group."""
    return ROLE_TO_DEFENSE_GROUP.get(str(role or ""))


def normalized_defense_role_groups(
    groups: Mapping[str, Sequence[str]] | None = None,
) -> Dict[str, tuple[str, ...]]:
    """Return normalized, deduplicated role groups.

    If groups is None, returns DEFENSE_ROLE_GROUPS.
    """
    src = DEFENSE_ROLE_GROUPS if groups is None else groups
    out: Dict[str, tuple[str, ...]] = {}
    for group, roles in src.items():
        deduped = tuple(dict.fromkeys(str(r) for r in roles if str(r or "")))
        if deduped:
            out[str(group)] = deduped
    return out
