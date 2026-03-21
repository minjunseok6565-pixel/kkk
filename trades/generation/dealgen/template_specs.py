from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Sequence, Tuple


TemplateAssetType = Literal["PLAYER", "PICK", "SWAP"]


@dataclass(frozen=True, slots=True)
class TemplateSlot:
    """A single required/optional component inside a package template.

    `constraints` is intentionally schema-light so planners can extend slot-level
    matching rules without changing this data model.
    """

    slot_id: str
    asset_type: TemplateAssetType
    constraints: Dict[str, Any]
    required: bool = True


@dataclass(frozen=True, slots=True)
class PackageTemplate:
    """Template-first package spec consumed by `skeleton_builders_template.py`."""

    template_id: str
    tier_scope: Tuple[str, ...]
    priority: int
    slots: Tuple[TemplateSlot, ...]
    min_score_ratio: float = 1.0
    max_assets_from_buyer: int = 6


ALL_TIERS: Tuple[str, ...] = (
    "MVP",
    "ALL_NBA",
    "ALL_STAR",
    "HIGH_STARTER",
    "STARTER",
    "HIGH_ROTATION",
    "ROTATION",
    "GARBAGE",
)
def _normalize_tier(tier: str) -> str:
    t = str(tier or "").upper().strip()
    return t if t in ALL_TIERS else "STARTER"


_PLAYER = "PLAYER"
_PICK = "PICK"


def _player_slot(slot_id: str, tier: str) -> TemplateSlot:
    tu = _normalize_tier(tier)
    return TemplateSlot(
        slot_id=slot_id,
        asset_type=_PLAYER,
        constraints={"min_tier": tu, "__placeholder__": True},
        required=True,
    )


def _pick_slot(slot_id: str, round_no: int) -> TemplateSlot:
    r = int(round_no)
    constraints: Dict[str, Any] = {"round": r, "__placeholder__": True}
    if r == 1:
        constraints["bucket_prefer"] = ("FIRST_SAFE", "FIRST_SENSITIVE")
    elif r == 2:
        constraints["bucket_prefer"] = "SECOND"
    return TemplateSlot(
        slot_id=slot_id,
        asset_type=_PICK,
        constraints=constraints,
        required=True,
    )


def _expand_combo(combo: Sequence[Tuple[str, str, int]]) -> Tuple[TemplateSlot, ...]:
    slots: List[TemplateSlot] = []
    for kind, value, count in combo:
        n = max(0, int(count))
        if kind == _PLAYER:
            for i in range(1, n + 1):
                slots.append(_player_slot(slot_id=f"player_{value.lower()}_{i}", tier=value))
        elif kind == _PICK:
            for i in range(1, n + 1):
                slots.append(_pick_slot(slot_id=f"pick_r{int(value)}_{i}", round_no=int(value)))
    return tuple(slots)


_PLACEHOLDER_COMBOS: Dict[str, Tuple[Tuple[Tuple[str, str, int], ...], ...]] = {
    "MVP": (
        ((_PLAYER, "ALL_NBA", 1), (_PICK, "1", 2)),
        ((_PLAYER, "ALL_NBA", 1), (_PLAYER, "ROTATION", 1), (_PICK, "1", 2)),
        ((_PLAYER, "HIGH_STARTER", 1), (_PLAYER, "STARTER", 1), (_PICK, "1", 4)),
        ((_PLAYER, "STARTER", 3), (_PICK, "1", 4)),
    ),
    "ALL_NBA": (
        ((_PLAYER, "ALL_STAR", 1), (_PICK, "1", 2)),
        ((_PLAYER, "HIGH_STARTER", 1), (_PLAYER, "STARTER", 1), (_PICK, "1", 2)),
        ((_PLAYER, "STARTER", 2), (_PLAYER, "HIGH_ROTATION", 1), (_PICK, "1", 2)),
        ((_PLAYER, "HIGH_STARTER", 1), (_PLAYER, "HIGH_ROTATION", 1), (_PLAYER, "ROTATION", 1), (_PICK, "1", 2)),
    ),
    "ALL_STAR": (
        ((_PLAYER, "HIGH_STARTER", 1), (_PLAYER, "HIGH_ROTATION", 1), (_PICK, "1", 1)),
        ((_PLAYER, "STARTER", 2), (_PLAYER, "HIGH_ROTATION", 1), (_PICK, "1", 1)),
        ((_PLAYER, "STARTER", 2), (_PLAYER, "ROTATION", 1), (_PICK, "1", 1)),
        ((_PLAYER, "HIGH_ROTATION", 4), (_PICK, "1", 1)),
    ),
    "HIGH_STARTER": (
        ((_PLAYER, "STARTER", 2),),
        ((_PLAYER, "HIGH_ROTATION", 3), (_PICK, "1", 1)),
        ((_PLAYER, "STARTER", 1), (_PLAYER, "ROTATION", 2), (_PICK, "1", 1)),
        ((_PLAYER, "HIGH_ROTATION", 2), (_PLAYER, "ROTATION", 2), (_PICK, "1", 1)),
    ),
    "STARTER": (
        ((_PLAYER, "STARTER", 1),),
        ((_PLAYER, "HIGH_ROTATION", 1), (_PLAYER, "ROTATION", 1), (_PICK, "2", 2)),
        ((_PICK, "1", 1),),
    ),
    "HIGH_ROTATION": (
        ((_PLAYER, "HIGH_ROTATION", 1), (_PICK, "2", 1)),
        ((_PICK, "2", 4),),
    ),
    "ROTATION": (
        ((_PLAYER, "ROTATION", 1), (_PICK, "2", 1)),
        ((_PICK, "2", 2),),
    ),
    "GARBAGE": (
        ((_PLAYER, "GARBAGE", 1),),
    ),
}


def _placeholder_templates_for_tier(tier: str) -> Tuple[PackageTemplate, ...]:
    tu = _normalize_tier(tier)
    combos = _PLACEHOLDER_COMBOS.get(tu, tuple())
    if not combos:
        return tuple()

    templates: List[PackageTemplate] = []
    for idx, combo in enumerate(combos, start=1):
        slots = _expand_combo(combo)
        templates.append(
            PackageTemplate(
                template_id=f"tpl_{tu.lower()}_placeholder_{idx}",
                tier_scope=(tu,),
                priority=9 + idx,
                slots=slots,
                min_score_ratio=1.0,
                max_assets_from_buyer=max(6, len(slots)),
            )
        )
    return tuple(templates)


_TEMPLATE_INDEX: Dict[str, Tuple[PackageTemplate, ...]] = {
    tier: _placeholder_templates_for_tier(tier) for tier in ALL_TIERS
}


def get_templates_for_tier(tier: str) -> List[PackageTemplate]:
    tier_u = _normalize_tier(tier)
    candidates = list(_TEMPLATE_INDEX.get(tier_u, tuple()))
    out = list(candidates)
    out.sort(key=lambda x: (int(x.priority), x.template_id))
    return out


def list_all_templates() -> Tuple[PackageTemplate, ...]:
    merged: List[PackageTemplate] = []
    for _tier, templates in _TEMPLATE_INDEX.items():
        merged.extend(list(templates))
    merged.sort(key=lambda x: (x.template_id, x.priority))
    return tuple(merged)


__all__ = [
    "TemplateAssetType",
    "TemplateSlot",
    "PackageTemplate",
    "ALL_TIERS",
    "get_templates_for_tier",
    "list_all_templates",
]
