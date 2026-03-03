from __future__ import annotations

import logging
from typing import List

from team_utils import ui_cache_refresh_players

logger = logging.getLogger(__name__)


def _try_ui_cache_refresh_players(player_ids: List[str], *, context: str) -> None:
    """Best-effort UI cache refresh. Never fails the API call.

    Policy: DB SSOT write APIs should succeed even if UI cache refresh fails.
    """
    try:
        if not player_ids:
            return
        ui_cache_refresh_players(player_ids)
    except Exception:
        logger.warning(
            "UI cache refresh failed (%s): player_ids=%r",
            context,
            player_ids,
            exc_info=True,
        )
