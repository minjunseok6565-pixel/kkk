from __future__ import annotations

"""Public data types for the agency subsystem.

We keep these dataclasses intentionally "thin":
- They are simple containers with minimal validation.
- Business rules live in tick.py / expectations.py / options.py.

Why dataclasses?
- Safer refactors and better editor support for a commercial project.
- Makes it easier to log / serialize / unit-test.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Mapping, Optional


RoleBucket = Literal[
    "UNKNOWN",
    "FRANCHISE",
    "STAR",
    "STARTER",
    "ROTATION",
    "BENCH",
    "GARBAGE",
]


@dataclass(frozen=True, slots=True)
class AgencyState:
    """Current agency state for one player (SSOT row)."""

    player_id: str
    team_id: str
    season_year: int

    role_bucket: RoleBucket = "UNKNOWN"
    leverage: float = 0.0

    minutes_expected_mpg: float = 0.0
    minutes_actual_mpg: float = 0.0

    # v1 axes
    minutes_frustration: float = 0.0
    team_frustration: float = 0.0
    trust: float = 0.5

    # v2 axes (additional)
    role_frustration: float = 0.0
    contract_frustration: float = 0.0
    health_frustration: float = 0.0
    chemistry_frustration: float = 0.0
    usage_frustration: float = 0.0

    # v2 monthly role evidence cache (derived)
    starts_rate: float = 0.0
    closes_rate: float = 0.0
    usage_share: float = 0.0

    # v3: self expectations (player self-perception)
    # These are optional and are bootstrapped during the first monthly tick.
    self_expected_mpg: Optional[float] = None
    self_expected_starts_rate: Optional[float] = None
    self_expected_closes_rate: Optional[float] = None

    # v3: dynamic stances (short-to-mid-term attitude; 0..1)
    stance_skepticism: float = 0.0
    stance_resentment: float = 0.0
    stance_hardball: float = 0.0

    trade_request_level: int = 0  # 0 none, 1 private, 2 public

    cooldown_minutes_until: Optional[str] = None
    cooldown_trade_until: Optional[str] = None
    cooldown_help_until: Optional[str] = None
    cooldown_contract_until: Optional[str] = None

    # v2 cooldowns
    cooldown_role_until: Optional[str] = None
    cooldown_health_until: Optional[str] = None
    cooldown_chemistry_until: Optional[str] = None

    # v2 escalation stages (0..4)
    escalation_role: int = 0
    escalation_contract: int = 0
    escalation_team: int = 0
    escalation_health: int = 0
    escalation_chemistry: int = 0

    last_processed_month: Optional[str] = None

    # Debug/telemetry helper: small JSON dict.
    context: Dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> Dict[str, Any]:
        return {
            "player_id": self.player_id,
            "team_id": self.team_id,
            "season_year": int(self.season_year),
            "role_bucket": self.role_bucket,
            "leverage": float(self.leverage),
            "minutes_expected_mpg": float(self.minutes_expected_mpg),
            "minutes_actual_mpg": float(self.minutes_actual_mpg),
            "minutes_frustration": float(self.minutes_frustration),
            "team_frustration": float(self.team_frustration),
            "trust": float(self.trust),
            "role_frustration": float(self.role_frustration),
            "contract_frustration": float(self.contract_frustration),
            "health_frustration": float(self.health_frustration),
            "chemistry_frustration": float(self.chemistry_frustration),
            "usage_frustration": float(self.usage_frustration),
            "starts_rate": float(self.starts_rate),
            "closes_rate": float(self.closes_rate),
            "usage_share": float(self.usage_share),

            "self_expected_mpg": None if self.self_expected_mpg is None else float(self.self_expected_mpg),
            "self_expected_starts_rate": None if self.self_expected_starts_rate is None else float(self.self_expected_starts_rate),
            "self_expected_closes_rate": None if self.self_expected_closes_rate is None else float(self.self_expected_closes_rate),

            "stance_skepticism": float(self.stance_skepticism),
            "stance_resentment": float(self.stance_resentment),
            "stance_hardball": float(self.stance_hardball),
            "trade_request_level": int(self.trade_request_level),
            "cooldown_minutes_until": self.cooldown_minutes_until,
            "cooldown_trade_until": self.cooldown_trade_until,
            "cooldown_help_until": self.cooldown_help_until,
            "cooldown_contract_until": self.cooldown_contract_until,
            "cooldown_role_until": self.cooldown_role_until,
            "cooldown_health_until": self.cooldown_health_until,
            "cooldown_chemistry_until": self.cooldown_chemistry_until,
            "escalation_role": int(self.escalation_role),
            "escalation_contract": int(self.escalation_contract),
            "escalation_team": int(self.escalation_team),
            "escalation_health": int(self.escalation_health),
            "escalation_chemistry": int(self.escalation_chemistry),
            "last_processed_month": self.last_processed_month,
            "context": dict(self.context or {}),
        }


@dataclass(frozen=True, slots=True)
class AgencyEvent:
    """Append-only event log entry (SSOT row)."""

    event_id: str
    player_id: str
    team_id: str
    season_year: int
    date: str  # YYYY-MM-DD

    event_type: str
    severity: float = 0.0

    payload: Dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "player_id": self.player_id,
            "team_id": self.team_id,
            "season_year": int(self.season_year),
            "date": self.date,
            "event_type": self.event_type,
            "severity": float(self.severity),
            "payload": dict(self.payload or {}),
        }


@dataclass(frozen=True, slots=True)
class MonthlyPlayerInputs:
    """Inputs required to process one player's monthly agency tick."""

    player_id: str
    team_id: str
    season_year: int
    month_key: str  # YYYY-MM (the month being processed)

    # Processing date (used for cooldown comparisons and event dates).
    now_date_iso: str

    # Expectations + actuals
    expected_mpg: float
    actual_minutes: float
    games_played: int

    # Schedule presence: number of team games in the processed month while the player
    # was on the evaluated team. Used to compute DNP frequency pressure (optional).
    games_possible: int = 0

    role_bucket: RoleBucket = "UNKNOWN"
    leverage: float = 0.0

    # Team performance in the processed month (0..1). Optional.
    team_win_pct: float = 0.5

    # Injury availability (optional; v1 strings align with injury subsystem)
    injury_status: Optional[str] = None  # HEALTHY/OUT/RETURNING

    # Injury availability multiplier for frustration accumulation.
    #
    # If provided by the service layer, tick.py will prefer this over the coarse
    # injury_status string. This enables month-based injury attribution (e.g., a
    # player was OUT earlier in the processed month but is HEALTHY today) while
    # keeping tick logic DB-free.
    injury_multiplier: Optional[float] = None

    # Player profile
    ovr: Optional[int] = None
    age: Optional[int] = None

    # Mental traits (0..100 expected; missing allowed)
    mental: Mapping[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # v2 evidence inputs (all optional; used by the v2 tick)
    # ------------------------------------------------------------------

    # Simple role evidence (month-level)
    starts: int = 0
    closes: int = 0
    starts_rate: float = 0.0
    closes_rate: float = 0.0

    # Usage evidence (month-level)
    usage_est: float = 0.0
    usage_share: float = 0.0

    # Between-game fatigue state (0..1). Optional.
    fatigue_st: Optional[float] = None
    fatigue_lt: Optional[float] = None

    # Contract/strategy context (SSOT-derived). Optional.
    active_contract_id: Optional[str] = None
    contract_end_season_id: Optional[str] = None
    team_strategy: Optional[str] = None


@dataclass(frozen=True, slots=True)
class PlayerOptionInputs:
    """Inputs to evaluate a PLAYER option / ETO decision."""

    player_id: str
    ovr: int
    age: int
    option_salary: float

    # Optional context
    team_id: Optional[str] = None
    team_win_pct: Optional[float] = None

    # injury risk in [0..1] (0 = healthy, 1 = very risky)
    injury_risk: float = 0.0

    mental: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlayerOptionDecision:
    decision: Literal["EXERCISE", "DECLINE"]
    meta: Dict[str, Any] = field(default_factory=dict)
