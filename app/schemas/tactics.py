from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class PresetOffenseDraftModel(BaseModel):
    """Optional typed model for preset offense draft snapshot payloads."""

    class Config:
        extra = "allow"


class PresetDefenseDraftModel(BaseModel):
    """Optional typed model for preset defense draft snapshot payloads."""

    class Config:
        extra = "allow"


class TacticsContextModel(BaseModel):
    tempo_mult: Optional[float] = None
    USER_PRESET_OFFENSE_DRAFT_V1: Optional[PresetOffenseDraftModel] = None
    USER_PRESET_DEFENSE_DRAFT_V1: Optional[PresetDefenseDraftModel] = None

    class Config:
        extra = "allow"


class TeamTacticsPayloadModel(BaseModel):
    """Flexible tactics payload with optional typed keys for preset offense extensions."""

    context: Optional[TacticsContextModel] = None
    action_weight_mult: Dict[str, float] = Field(default_factory=dict)
    outcome_by_action_mult: Dict[str, Dict[str, float]] = Field(default_factory=dict)
    outcome_global_mult: Dict[str, float] = Field(default_factory=dict)

    class Config:
        extra = "allow"


class TeamTacticsUpsertRequest(BaseModel):
    tactics: Dict[str, Any]
