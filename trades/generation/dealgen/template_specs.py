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
    contract_tags: Tuple[str, ...]
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
ALL_CONTRACT_TAGS: Tuple[str, ...] = ("OVERPAY", "FAIR", "VALUE")


def _normalize_tier(tier: str) -> str:
    t = str(tier or "").upper().strip()
    return t if t in ALL_TIERS else "STARTER"


def _normalize_tag(tag: str) -> str:
    t = str(tag or "").upper().strip()
    return t if t in ALL_CONTRACT_TAGS else "FAIR"


def _placeholder_templates_for_tier(tier: str) -> Tuple[PackageTemplate, ...]:
    """Default placeholders.

    These are intentionally generic starter templates. Product planners can replace
    slot constraints/template composition later without changing execution code.
    """

    tu = _normalize_tier(tier)
    return (
        PackageTemplate(
            template_id=f"tpl_{tu.lower()}_placeholder_1",
            tier_scope=(tu,),
            contract_tags=ALL_CONTRACT_TAGS,
            priority=10,
            slots=(
                TemplateSlot(
                    slot_id="core_player",
                    asset_type="PLAYER",
                    constraints={"min_tier": "STARTER", "__placeholder__": True},
                    required=True,
                ),
                TemplateSlot(
                    slot_id="first_pick_a",
                    asset_type="PICK",
                    constraints={"round": 1, "bucket_prefer": "FIRST_SAFE", "__placeholder__": True},
                    required=True,
                ),
                TemplateSlot(
                    slot_id="first_pick_b",
                    asset_type="PICK",
                    constraints={"round": 1, "bucket_prefer": "FIRST_SENSITIVE", "__placeholder__": True},
                    required=True,
                ),
            ),
            min_score_ratio=1.0,
            max_assets_from_buyer=5,
        ),
        PackageTemplate(
            template_id=f"tpl_{tu.lower()}_placeholder_2",
            tier_scope=(tu,),
            contract_tags=ALL_CONTRACT_TAGS,
            priority=11,
            slots=(
                TemplateSlot(
                    slot_id="impact_player",
                    asset_type="PLAYER",
                    constraints={"min_tier": "HIGH_STARTER", "__placeholder__": True},
                    required=True,
                ),
                TemplateSlot(
                    slot_id="first_pick",
                    asset_type="PICK",
                    constraints={"round": 1, "__placeholder__": True},
                    required=True,
                ),
                TemplateSlot(
                    slot_id="second_pick",
                    asset_type="PICK",
                    constraints={"round": 2, "bucket_prefer": "SECOND", "__placeholder__": True},
                    required=True,
                ),
            ),
            min_score_ratio=0.95,
            max_assets_from_buyer=5,
        ),
        PackageTemplate(
            template_id=f"tpl_{tu.lower()}_placeholder_3",
            tier_scope=(tu,),
            contract_tags=ALL_CONTRACT_TAGS,
            priority=12,
            slots=(
                TemplateSlot(
                    slot_id="player_a",
                    asset_type="PLAYER",
                    constraints={"min_tier": "STARTER", "__placeholder__": True},
                    required=True,
                ),
                TemplateSlot(
                    slot_id="player_b",
                    asset_type="PLAYER",
                    constraints={"min_tier": "ROTATION", "__placeholder__": True},
                    required=True,
                ),
                TemplateSlot(
                    slot_id="first_pick",
                    asset_type="PICK",
                    constraints={"round": 1, "__placeholder__": True},
                    required=True,
                ),
            ),
            min_score_ratio=0.9,
            max_assets_from_buyer=6,
        ),
        PackageTemplate(
            template_id=f"tpl_{tu.lower()}_placeholder_4",
            tier_scope=(tu,),
            contract_tags=ALL_CONTRACT_TAGS,
            priority=13,
            slots=(
                TemplateSlot(
                    slot_id="core_player",
                    asset_type="PLAYER",
                    constraints={"min_tier": "HIGH_ROTATION", "__placeholder__": True},
                    required=True,
                ),
                TemplateSlot(
                    slot_id="second_pick_a",
                    asset_type="PICK",
                    constraints={"round": 2, "bucket_prefer": "SECOND", "__placeholder__": True},
                    required=True,
                ),
                TemplateSlot(
                    slot_id="second_pick_b",
                    asset_type="PICK",
                    constraints={"round": 2, "bucket_prefer": "SECOND", "__placeholder__": True},
                    required=True,
                ),
                TemplateSlot(
                    slot_id="second_pick_c",
                    asset_type="PICK",
                    constraints={"round": 2, "bucket_prefer": "SECOND", "__placeholder__": True},
                    required=False,
                ),
            ),
            min_score_ratio=0.85,
            max_assets_from_buyer=6,
        ),
    )


_TEMPLATE_INDEX: Dict[str, Tuple[PackageTemplate, ...]] = {
    tier: _placeholder_templates_for_tier(tier) for tier in ALL_TIERS
}


def get_templates_for_tier(tier: str, contract_tag: str) -> List[PackageTemplate]:
    tier_u = _normalize_tier(tier)
    tag_u = _normalize_tag(contract_tag)

    candidates = list(_TEMPLATE_INDEX.get(tier_u, tuple()))
    out = [t for t in candidates if tag_u in set(t.contract_tags)]
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
    "ALL_CONTRACT_TAGS",
    "get_templates_for_tier",
    "list_all_templates",
]
