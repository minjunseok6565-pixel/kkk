from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel


class TeamTacticsUpsertRequest(BaseModel):
    tactics: Dict[str, Any]
