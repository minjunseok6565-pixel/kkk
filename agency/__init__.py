"""Player agency subsystem.

What this package is
--------------------
"Player agency" is the layer that makes players behave like autonomous actors:
- They accumulate (or dissipate) dissatisfaction based on real usage, role, and team context.
- They express complaints, make demands, and may request trades.
- They evaluate contract options (player option / ETO) instead of always complying.
- They negotiate contracts with their own targets and tolerance.

Design principles
-----------------
- Mental attributes (M_* traits) are *modulators*, not absolute rules.
- Real leverage (team role / OVR rank / salary) gates strong actions.
- Deterministic randomness (stable hashing) keeps outcomes reproducible.
- SSOT persistence lives in SQLite:
  - player_agency_state
  - agency_events

This package is intentionally layered:
- repo.py: DB I/O only (no business logic)
- expectations.py: compute role/leverage/expected minutes (pure)
- tick.py: monthly state updates + event generation (pure-ish)
- service.py: orchestrates DB reads/writes for ticks
- options.py: player option / ETO decisions (pure)

Integration points will be added by editing existing modules later.
"""

from .config import AgencyConfig, DEFAULT_CONFIG
from .types import AgencyEvent, AgencyState, MonthlyPlayerInputs

__all__ = [
    "AgencyConfig",
    "DEFAULT_CONFIG",
    "AgencyEvent",
    "AgencyState",
    "MonthlyPlayerInputs",
]
