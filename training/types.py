from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Literal, Mapping, Optional

from .config import INTENSITY_MULTIPLIER


TrainingIntensity = Literal["LOW", "MED", "HIGH"]
TrainingCategory = Literal[
    "BALANCED",
    "SHOOTING",
    "FINISHING",
    "PLAYMAKING",
    "DEFENSE",
    "REBOUNDING",
    "PHYSICAL",
    "IQ",
    "POST",
]


def stable_seed(*parts: str) -> int:
    """Deterministic seed from string parts (stable across runs)."""
    h = hashlib.sha256("|".join([str(p) for p in parts]).encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def _norm_intensity(v: Any) -> str:
    s = str(v or "").strip().upper()
    if s in INTENSITY_MULTIPLIER:
        return s
    if s in {"M", "MID", "MEDIUM"}:
        return "MED"
    if s in {"L", "LOW"}:
        return "LOW"
    if s in {"H", "HIGH"}:
        return "HIGH"
    return "MED"


def intensity_multiplier(v: Any) -> float:
    return float(INTENSITY_MULTIPLIER.get(_norm_intensity(v), 1.0))


def _norm_category(v: Any) -> str:
    s = str(v or "").strip().upper()
    if not s:
        return "BALANCED"
    aliases = {
        "BAL": "BALANCED",
        "SHOOT": "SHOOTING",
        "FIN": "FINISHING",
        "PLAY": "PLAYMAKING",
        "DEF": "DEFENSE",
        "REB": "REBOUNDING",
        "PHY": "PHYSICAL",
        "POSTUP": "POST",
        "POST": "POST",
    }
    s = aliases.get(s, s)
    if s in {
        "BALANCED",
        "SHOOTING",
        "FINISHING",
        "PLAYMAKING",
        "DEFENSE",
        "REBOUNDING",
        "PHYSICAL",
        "IQ",
        "POST",
    }:
        return s
    return "BALANCED"


def normalize_team_plan(plan: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Normalize a team plan dict.

    Expected shape (JSON):
      {"focus": <category>, "intensity": <LOW|MED|HIGH>, "weights": {cat: w, ...}}
    """
    if not isinstance(plan, Mapping):
        plan = {}
    focus = _norm_category(plan.get("focus"))
    intensity = _norm_intensity(plan.get("intensity"))
    weights_raw = plan.get("weights")
    weights: Dict[str, float] = {}
    if isinstance(weights_raw, Mapping):
        for k, v in weights_raw.items():
            kk = _norm_category(k)
            if kk == "BALANCED":
                continue
            try:
                weights[kk] = float(v)
            except Exception:
                continue
    return {"focus": focus, "intensity": intensity, "weights": weights}


def normalize_player_plan(plan: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Normalize a player plan dict.

    Expected shape (JSON):
      {"primary": <category>, "secondary": <category|None>, "intensity": <LOW|MED|HIGH>}
    """
    if not isinstance(plan, Mapping):
        plan = {}
    primary = _norm_category(plan.get("primary"))
    if primary == "BALANCED":
        primary = "BALANCED"
    secondary = _norm_category(plan.get("secondary")) if plan.get("secondary") else None
    if secondary == "BALANCED":
        secondary = None
    intensity = _norm_intensity(plan.get("intensity"))
    return {"primary": primary, "secondary": secondary, "intensity": intensity}


@dataclass(frozen=True, slots=True)
class GrowthProfile:
    player_id: str
    ceiling_proxy: float
    peak_age: float
    decline_start_age: float
    late_decline_age: float
