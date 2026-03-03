from __future__ import annotations

from ...identity import canonical_deal_payload, deal_identity_hash
from ...models import Deal

# =============================================================================
# Dedupe / misc
# =============================================================================

def deal_signature_payload(deal: Deal):
    """Canonical payload used for signature comparisons (includes meta).

    NOTE: sweetener 등에서 '딜이 실제로 변했는지' 비교 용도로 사용.
    """
    try:
        return canonical_deal_payload(deal, include_meta=True)
    except Exception:
        return repr(deal)


def dedupe_hash(deal: Deal) -> str:
    """Deal identity hash for dedupe.

    IMPORTANT:
    - MUST ignore deal.meta (tags/debug fields) so the same transaction (teams+legs)
      does not survive as duplicates with only meta differences.
    """
    return deal_identity_hash(deal)
