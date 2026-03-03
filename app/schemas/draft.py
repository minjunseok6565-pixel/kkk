from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DraftCombineRequest(BaseModel):
    rng_seed: Optional[int] = None


class DraftWorkoutsRequest(BaseModel):
    # User-controlled workouts:
    # - If invited_prospect_temp_ids is empty => skip (no DB writes)
    # - Otherwise, run only for this team (no league-wide generation)
    team_id: str
    invited_prospect_temp_ids: List[str] = Field(default_factory=list)
    max_invites: int = 12
    rng_seed: Optional[int] = None


class DraftInterviewItem(BaseModel):
    prospect_temp_id: str
    selected_question_ids: List[str] = Field(default_factory=list)


class DraftInterviewsRequest(BaseModel):
    # User-controlled interviews:
    # - 'interviews' contains per-prospect selected questions (typically 3)
    # - This endpoint only writes results for this team (private info)
    team_id: str
    interviews: List[DraftInterviewItem] = Field(default_factory=list)
    rng_seed: Optional[int] = None


class DraftAutoSelectionsRequest(BaseModel):
    max_picks: Optional[int] = None
    stop_on_user_controlled_team_ids: Optional[List[str]] = None
    allow_autopick_user_team: bool = False


class DraftRecordPickRequest(BaseModel):
    prospect_temp_id: str
    source: str = "draft_user"
    meta: Optional[Dict[str, Any]] = None


class DraftWatchRecomputeRequest(BaseModel):
    draft_year: Optional[int] = None
    as_of_date: Optional[str] = None       # YYYY-MM-DD (default: current in-game date)
    period_key: Optional[str] = None       # YYYY-MM (default: as_of_date[:7])
    season_year: Optional[int] = None      # stats season used (default: draft_year - 1)
    min_inclusion_prob: Optional[float] = None  # default: 0.35
    force: bool = False
