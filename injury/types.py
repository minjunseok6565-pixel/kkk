from __future__ import annotations

"""Public data types for the injury subsystem."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass(frozen=True, slots=True)
class InjuryEvent:
    """A single injury occurrence (game or training)."""

    injury_id: str
    player_id: str
    team_id: str
    season_year: int
    date: str
    context: str  # "game" | "training"

    game_id: Optional[str] = None
    quarter: Optional[int] = None
    clock_sec: Optional[int] = None

    body_part: str = ""
    injury_type: str = ""
    severity: int = 1

    duration_days: int = 0
    out_until_date: str = ""

    returning_days: int = 0
    returning_until_date: str = ""

    temp_debuff: Dict[str, int] = field(default_factory=dict)
    perm_drop: Dict[str, int] = field(default_factory=dict)

    def to_row(self) -> Dict[str, Any]:
        return {
            "injury_id": self.injury_id,
            "player_id": self.player_id,
            "team_id": self.team_id,
            "season_year": int(self.season_year),
            "date": self.date,
            "context": self.context,
            "game_id": self.game_id,
            "quarter": self.quarter,
            "clock_sec": self.clock_sec,
            "body_part": self.body_part,
            "injury_type": self.injury_type,
            "severity": int(self.severity),
            "duration_days": int(self.duration_days),
            "out_until_date": self.out_until_date,
            "returning_days": int(self.returning_days),
            "returning_until_date": self.returning_until_date,
            "temp_debuff": dict(self.temp_debuff or {}),
            "perm_drop": dict(self.perm_drop or {}),
        }


@dataclass(frozen=True, slots=True)
class PreparedGameInjuries:
    """Prepared injury inputs for a single game.

    - unavailable_pids_by_team: players who are OUT (cannot be selected into lineup)
    - attrs_mods_by_pid: temporary returning debuffs to apply at roster load
    - reinjury_counts_by_pid: recent injury counts per body part (bias in injury selection)
    - lt_wear_by_pid: LT wear (0..1) used as an in-game injury risk modifier
    """

    game_id: str
    game_date_iso: str
    season_year: int
    home_team_id: str
    away_team_id: str

    unavailable_pids_by_team: Dict[str, Set[str]] = field(default_factory=dict)
    attrs_mods_by_pid: Dict[str, Dict[str, float]] = field(default_factory=dict)
    reinjury_counts_by_pid: Dict[str, Dict[str, int]] = field(default_factory=dict)
    lt_wear_by_pid: Dict[str, float] = field(default_factory=dict)

    training_new_events: List[InjuryEvent] = field(default_factory=list)

