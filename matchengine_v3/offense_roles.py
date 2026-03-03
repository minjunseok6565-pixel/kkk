from __future__ import annotations

"""matchengine_v3.offense_roles

Single source of truth (SSOT) for the *offensive* role key system.

Design goals:
- Canonical keys reflect modern NBA offensive roles.
- The engine uses *only* the canonical C13 role keys (no legacy aliases / compatibility).

This module is intentionally dependency-light so it can be imported broadly.
"""

from typing import Dict, Tuple

# ---------------------------------------------------------------------------
# Canonical C13 role keys (Modern NBA Offensive Role System v1)
# ---------------------------------------------------------------------------

ROLE_ENGINE_PRIMARY = "Engine_Primary"
ROLE_ENGINE_SECONDARY = "Engine_Secondary"
ROLE_TRANSITION_ENGINE = "Transition_Engine"
ROLE_SHOT_CREATOR = "Shot_Creator"
ROLE_RIM_PRESSURE = "Rim_Pressure"

ROLE_SPOTUP_SPACER = "SpotUp_Spacer"
ROLE_MOVEMENT_SHOOTER = "Movement_Shooter"
ROLE_CUTTER_FINISHER = "Cutter_Finisher"
ROLE_CONNECTOR = "Connector"

ROLE_ROLL_MAN = "Roll_Man"
ROLE_SHORTROLL_HUB = "ShortRoll_Hub"
ROLE_POP_THREAT = "Pop_Threat"
ROLE_POST_ANCHOR = "Post_Anchor"


ALL_OFFENSE_ROLES: Tuple[str, ...] = (
    ROLE_ENGINE_PRIMARY,
    ROLE_ENGINE_SECONDARY,
    ROLE_TRANSITION_ENGINE,
    ROLE_SHOT_CREATOR,
    ROLE_RIM_PRESSURE,
    ROLE_SPOTUP_SPACER,
    ROLE_MOVEMENT_SHOOTER,
    ROLE_CUTTER_FINISHER,
    ROLE_CONNECTOR,
    ROLE_ROLL_MAN,
    ROLE_SHORTROLL_HUB,
    ROLE_POP_THREAT,
    ROLE_POST_ANCHOR,
)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Optional group tags (used by rotation/fatigue systems)
# ---------------------------------------------------------------------------

ROLE_GROUP_HANDLER = "Handler"
ROLE_GROUP_WING = "Wing"
ROLE_GROUP_BIG = "Big"

ROLE_TO_GROUPS: Dict[str, Tuple[str, ...]] = {
    ROLE_ENGINE_PRIMARY: (ROLE_GROUP_HANDLER,),
    ROLE_ENGINE_SECONDARY: (ROLE_GROUP_HANDLER, ROLE_GROUP_WING),
    ROLE_TRANSITION_ENGINE: (ROLE_GROUP_HANDLER,),
    ROLE_SHOT_CREATOR: (ROLE_GROUP_WING, ROLE_GROUP_HANDLER),
    ROLE_RIM_PRESSURE: (ROLE_GROUP_WING, ROLE_GROUP_HANDLER),
    ROLE_SPOTUP_SPACER: (ROLE_GROUP_WING,),
    ROLE_MOVEMENT_SHOOTER: (ROLE_GROUP_WING,),
    ROLE_CUTTER_FINISHER: (ROLE_GROUP_WING,),
    ROLE_CONNECTOR: (ROLE_GROUP_WING, ROLE_GROUP_HANDLER),
    ROLE_ROLL_MAN: (ROLE_GROUP_BIG,),
    ROLE_SHORTROLL_HUB: (ROLE_GROUP_BIG,),
    ROLE_POP_THREAT: (ROLE_GROUP_BIG,),
    ROLE_POST_ANCHOR: (ROLE_GROUP_BIG,),
}


def role_groups(role_key: str) -> Tuple[str, ...]:
    """Return group tags for a canonical role key."""
    k = str(role_key or "").strip()
    if not k:
        return ()
    return ROLE_TO_GROUPS.get(k, ())
