from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple

from contracts.options import normalize_option_record

from .utils import clamp01, json_dumps, safe_float, safe_int


ContractNegotiationMode = Literal["SIGN_FA", "RE_SIGN", "EXTEND"]
ContractNegotiationPhase = Literal[
    "INIT",
    "NEGOTIATING",
    "ACCEPTED",
    "REJECTED",
    "WALKED",
    "EXPIRED",
]
ContractNegotiationStatus = Literal["ACTIVE", "CLOSED", "EXPIRED"]

NegotiationSpeaker = Literal["TEAM", "PLAYER", "SYSTEM"]
NegotiationVerdict = Literal["ACCEPT", "COUNTER", "REJECT", "WALK"]


@dataclass(frozen=True, slots=True)
class Reason:
    code: str
    message: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "code": str(self.code),
            "message": str(self.message),
            "evidence": dict(self.evidence or {}),
        }


def _coerce_salary_by_year_map(value: Any) -> Dict[int, float]:
    if not isinstance(value, Mapping):
        return {}
    out: Dict[int, float] = {}
    for k, v in value.items():
        try:
            y = int(k)
        except Exception:
            # JSON keys are strings; tolerate but skip non-int.
            continue
        out[y] = float(safe_float(v, 0.0))
    return out


def _infer_start_year_and_years(salary_by_year: Dict[int, float]) -> Tuple[int, int]:
    if not salary_by_year:
        return (0, 0)
    years_sorted = sorted(int(y) for y in salary_by_year.keys())
    start = years_sorted[0]
    years = len(years_sorted)
    return (int(start), int(years))


@dataclass(frozen=True, slots=True)
class ContractOffer:
    """Normalized internal representation of a contract offer."""

    start_season_year: int
    years: int
    salary_by_year: Dict[int, float]
    options: List[Dict[str, Any]] = field(default_factory=list)
    non_monetary: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ContractOffer":
        if not isinstance(payload, Mapping):
            raise TypeError("offer payload must be a mapping")

        # Accept multiple key aliases for forward compatibility.
        sy = payload.get("start_season_year")
        if sy is None:
            sy = payload.get("start_year")
        years = payload.get("years")

        salary_by_year = _coerce_salary_by_year_map(payload.get("salary_by_year") or {})

        # Convenience: allow aav + years (flat salary curve)
        if not salary_by_year:
            aav = payload.get("aav")
            if aav is None:
                aav = payload.get("salary")  # alias
            if aav is not None and years is not None and sy is not None:
                try:
                    y_i = int(years)
                    s_i = int(sy)
                    aav_f = float(safe_float(aav, 0.0))
                except Exception:
                    y_i, s_i, aav_f = 0, 0, 0.0
                if y_i > 0 and s_i > 0 and aav_f > 0:
                    salary_by_year = {int(s_i + i): float(aav_f) for i in range(int(y_i))}

        if (sy is None or years is None) and salary_by_year:
            inferred_start, inferred_years = _infer_start_year_and_years(salary_by_year)
            if sy is None:
                sy = inferred_start
            if years is None:
                years = inferred_years

        start_season_year = safe_int(sy, 0)
        years_i = safe_int(years, 0)
        if start_season_year <= 0:
            raise ValueError("offer.start_season_year is required")
        if years_i <= 0:
            raise ValueError("offer.years must be >= 1")

        # Canonicalize salary_by_year: contiguous years, length == years
        if not salary_by_year:
            raise ValueError("offer.salary_by_year is required")
        # Ensure every year exists; fill missing years by reusing the first-year salary (defensive).
        first = float(safe_float(salary_by_year.get(start_season_year), 0.0))
        if first <= 0:
            # fallback to any provided salary
            first = float(safe_float(next(iter(salary_by_year.values()), 0.0), 0.0))
        if first <= 0:
            raise ValueError("offer.salary_by_year values must be > 0")

        canonical: Dict[int, float] = {}
        for y in range(int(start_season_year), int(start_season_year) + int(years_i)):
            canonical[y] = float(safe_float(salary_by_year.get(y, first), first))

        raw_options = payload.get("options")
        options: List[Dict[str, Any]] = []
        if isinstance(raw_options, list):
            for opt in raw_options:
                if not isinstance(opt, Mapping):
                    continue
                rec = dict(opt)
                rec.setdefault("status", "PENDING")
                rec.setdefault("decision_date", None)
                try:
                    normalized = normalize_option_record(rec)
                except Exception:
                    continue
                oy = int(normalized.get("season_year") or 0)
                if oy < int(start_season_year) or oy >= int(start_season_year) + int(years_i):
                    continue
                options.append(normalized)
        # dedupe by season year: keep first to preserve sender ordering
        dedup: Dict[int, Dict[str, Any]] = {}
        for opt in options:
            sy = int(opt.get("season_year") or 0)
            if sy not in dedup:
                dedup[sy] = opt

        non_monetary = payload.get("non_monetary") if isinstance(payload.get("non_monetary"), Mapping) else {}
        return cls(
            start_season_year=int(start_season_year),
            years=int(years_i),
            salary_by_year=canonical,
            options=[dict(v) for _, v in sorted(dedup.items(), key=lambda kv: kv[0])],
            non_monetary=dict(non_monetary),
        )

    def aav(self) -> float:
        vals = [float(v) for v in (self.salary_by_year or {}).values() if float(v) > 0]
        if not vals:
            return 0.0
        return float(sum(vals) / float(len(vals)))

    def to_payload(self) -> Dict[str, Any]:
        # JSON encoder will convert int keys to strings; keep ints here for internal consistency.
        return {
            "start_season_year": int(self.start_season_year),
            "years": int(self.years),
            "salary_by_year": {int(k): float(v) for k, v in (self.salary_by_year or {}).items()},
            "options": [dict(x) for x in (self.options or [])],
            "non_monetary": dict(self.non_monetary or {}),
        }


@dataclass(frozen=True, slots=True)
class PlayerPosition:
    """Player's negotiation position for this session."""

    market_aav: float
    ask_aav: float
    floor_aav: float

    min_years: int
    ideal_years: int
    max_years: int

    concession_rate: float
    insult_ratio: float
    patience: float
    max_rounds: int

    required_demands: List[Dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "market_aav": float(self.market_aav),
            "ask_aav": float(self.ask_aav),
            "floor_aav": float(self.floor_aav),
            "min_years": int(self.min_years),
            "ideal_years": int(self.ideal_years),
            "max_years": int(self.max_years),
            "concession_rate": float(clamp01(self.concession_rate)),
            "insult_ratio": float(clamp01(self.insult_ratio)),
            "patience": float(clamp01(self.patience)),
            "max_rounds": int(self.max_rounds),
            "required_demands": list(self.required_demands or []),
        }


@dataclass(frozen=True, slots=True)
class NegotiationDecision:
    verdict: NegotiationVerdict
    reasons: List[Reason] = field(default_factory=list)
    counter_offer: Optional[ContractOffer] = None

    # Suggested state effects (applied by orchestration layer, if desired)
    effects: Dict[str, Any] = field(default_factory=dict)

    tone: Literal["CALM", "FIRM", "ANGRY"] = "FIRM"
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        payload = {
            "verdict": str(self.verdict),
            "reasons": [r.to_payload() for r in (self.reasons or [])],
            "tone": str(self.tone),
            "effects": dict(self.effects or {}),
            "meta": dict(self.meta or {}),
        }
        if self.counter_offer is not None:
            payload["counter_offer"] = self.counter_offer.to_payload()
        try:
            json_dumps(payload)
        except Exception:
            # Make sure we always return JSON-serializable payloads to server.
            payload["meta"] = {"note": "meta_not_serializable"}
        return payload
