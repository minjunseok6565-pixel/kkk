from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from defense_role_groups import normalized_defense_role_groups

from .fit_engine import FitEngine
from .types import PlayerSnapshot


@dataclass(frozen=True, slots=True)
class RoleTexture:
    creation_proxy: float
    spacing_proxy: float
    rim_pressure_proxy: float
    defense_proxy: float
    connector_index: float
    source_coverage: Mapping[str, bool]
    notes: tuple[str, ...] = tuple()



ROLE_FIT_KEYS_BY_AXIS: Mapping[str, tuple[str, ...]] = {
    "creation": (
        "Engine_Primary",
        "Engine_Secondary",
        "Transition_Engine",
        "Shot_Creator",
    ),
    "spacing": (
        "SpotUp_Spacer",
        "Movement_Shooter",
    ),
    "rim_pressure": (
        "Rim_Pressure",
        "Cutter_Finisher",
        "Roll_Man",
    ),
}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _sf(x: object, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _extract_role_fit(player: PlayerSnapshot) -> tuple[Mapping[str, float], str | None]:
    meta = player.meta if isinstance(player.meta, dict) else {}
    attrs = player.attrs if isinstance(player.attrs, dict) else {}

    role_fit_meta = meta.get("role_fit")
    if isinstance(role_fit_meta, Mapping):
        return ({str(k): _clamp01(_sf(v)) for k, v in role_fit_meta.items()}, "meta.role_fit")

    role_fit_attrs = attrs.get("role_fit")
    if isinstance(role_fit_attrs, Mapping):
        return ({str(k): _clamp01(_sf(v)) for k, v in role_fit_attrs.items()}, "attrs.role_fit")

    return ({}, None)


def _score_from_role_fit(role_fit: Mapping[str, float], keys: tuple[str, ...]) -> float:
    if not role_fit:
        return 0.0
    vals = [
        _clamp01(_sf(role_fit.get(k), 0.0))
        for k in keys
        if k in role_fit
    ]
    if not vals:
        return 0.0
    return float(sum(vals) / len(vals))


def _score_from_role_fit_groups(
    role_fit: Mapping[str, float],
    groups: Mapping[str, Sequence[str]],
) -> float:
    if not role_fit:
        return 0.0

    group_scores: list[float] = []
    for roles in groups.values():
        vals = [_clamp01(_sf(role_fit.get(role), 0.0)) for role in roles if role in role_fit]
        if not vals:
            continue
        group_scores.append(max(vals))

    if not group_scores:
        return 0.0
    return float(sum(group_scores) / len(group_scores))


def build_role_textures(
    players: Sequence[PlayerSnapshot],
    *,
    fit_engine: FitEngine,
    injected_supply: Mapping[str, Mapping[str, float]] | None = None,
    defense_role_groups: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, RoleTexture]:
    out: dict[str, RoleTexture] = {}
    normalized_groups = normalized_defense_role_groups(defense_role_groups)

    for p in players:
        notes: list[str] = []
        coverage = {
            "role_fit": True,
            "supply_vector": True,
        }

        role_fit, source = _extract_role_fit(p)
        if source is None:
            coverage["role_fit"] = False
            notes.append(f"MISSING_INPUT_ROLE_FIT:{p.player_id}")

        if source is not None:
            notes.append(f"ROLE_FIT_SOURCE:{source}")

        creation = _score_from_role_fit(role_fit, ROLE_FIT_KEYS_BY_AXIS["creation"])
        spacing = _score_from_role_fit(role_fit, ROLE_FIT_KEYS_BY_AXIS["spacing"])
        rim_pressure = _score_from_role_fit(role_fit, ROLE_FIT_KEYS_BY_AXIS["rim_pressure"])
        defense = _score_from_role_fit_groups(role_fit, normalized_groups)

        supply: Mapping[str, float] | None = None
        if injected_supply is not None:
            supply = injected_supply.get(p.player_id)
        if supply is None:
            try:
                supply = fit_engine.compute_player_supply_vector(p)
            except Exception:
                supply = None

        if not supply:
            coverage["supply_vector"] = False
            notes.append(f"MISSING_INPUT_SUPPLY_VECTOR:{p.player_id}")
            supply = {}

        # role_fit 우선, supply는 보강만 수행
        creation = max(creation, _clamp01(_sf(supply.get("SHOT_CREATION") or supply.get("PRIMARY_INITIATOR"), 0.0)))
        spacing = max(spacing, _clamp01(_sf(supply.get("SPACING"), 0.0)))
        rim_pressure = max(rim_pressure, _clamp01(_sf(supply.get("RIM_PRESSURE"), 0.0)))
        defense = max(defense, _clamp01(_sf(supply.get("DEFENSE"), 0.0)))

        connector = _clamp01((creation + spacing + rim_pressure + defense) / 4.0)

        out[p.player_id] = RoleTexture(
            creation_proxy=float(creation),
            spacing_proxy=float(spacing),
            rim_pressure_proxy=float(rim_pressure),
            defense_proxy=float(defense),
            connector_index=float(connector),
            source_coverage=coverage,
            notes=tuple(notes),
        )

    return out
