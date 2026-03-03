from __future__ import annotations

from dataclasses import dataclass

from ...models import PickAsset
from ...protection import normalize_protection
from ..base import TradeContext


@dataclass
class PickProtectionSchemaRule:
    rule_id: str = "pick_protection_schema"
    priority: int = 25
    enabled: bool = True

    def validate(self, deal, ctx: TradeContext) -> None:
        for assets in deal.legs.values():
            for asset in assets:
                if not isinstance(asset, PickAsset):
                    continue
                if asset.protection is None:
                    continue
                # SSOT validation/normalization (raises TradeError(PROTECTION_INVALID) on failure).
                normalize_protection(asset.protection, pick_id=asset.pick_id)
