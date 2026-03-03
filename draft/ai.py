from __future__ import annotations

"""Draft AI interfaces.

This module defines the shared protocol types used by draft AI policies.

Project rule
------------
The legacy OVR-only BPA policy has been removed on purpose.
Production draft flows should use NeedsPotentialGmPolicy (draft/ai_needs.py).

If you add new policies in the future, keep them in separate modules and
register them explicitly in draft/engine.py (no silent fallback policies).
"""

from dataclasses import dataclass
from typing import Any, Dict, Protocol

from .types import DraftTurn, TeamId, norm_team_id
from .pool import DraftPool


@dataclass(frozen=True, slots=True)
class DraftAIContext:
    draft_year: int
    team_id: TeamId
    turn: DraftTurn
    meta: Dict[str, Any] = None  # optional misc knobs

    def __post_init__(self) -> None:
        object.__setattr__(self, "draft_year", int(self.draft_year))
        object.__setattr__(self, "team_id", norm_team_id(self.team_id))
        if self.meta is None:
            object.__setattr__(self, "meta", {})


class DraftAIPolicy(Protocol):
    def choose(self, pool: DraftPool, ctx: DraftAIContext) -> "DraftAISelection":
        ...


@dataclass(frozen=True, slots=True)
class DraftAISelection:
    prospect_temp_id: str
    meta: Dict[str, Any] = None

    def __post_init__(self) -> None:
        if self.meta is None:
            object.__setattr__(self, "meta", {})
