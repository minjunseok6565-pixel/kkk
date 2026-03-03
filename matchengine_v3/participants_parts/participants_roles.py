from __future__ import annotations

"""participants_roles.py

Local re-exports for the participants subsystem.

The match engine uses the canonical C13 offensive role keys defined in
:mod:`matchengine_v3.offense_roles`.

This module exists to keep imports short inside participants_* modules.
"""

from ..offense_roles import (
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
    ALL_OFFENSE_ROLES,
)
