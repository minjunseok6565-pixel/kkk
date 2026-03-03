"""Contract option decision policies.

This module contains the *default* option policy used by the offseason contract
processor, plus optional helper policies.

Design constraint:
- The default policy must be conservative/stable (never surprising).
- More "gamey"/AI policies should be opt-in from the caller (e.g. server flow).

Option decision hook signature (used by LeagueService.expire_contracts_for_season_transition):
    (option: dict, player_id: str, contract: dict, game_state: dict) -> "EXERCISE" | "DECLINE"

NOTE:
- When called via contracts.offseason.process_offseason(), the policy receives the
  *full* exported game_state snapshot (so it can use ui_cache, stats, etc.).
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import sqlite3
from typing import Any, Callable, Dict, Literal, Mapping, Optional, Tuple

# SSOT: season-based salary cap numbers (no duplicated cap math in this module)
try:
    from cap_model import CapModel
except Exception:  # pragma: no cover
    CapModel = None  # type: ignore
    
from schema import normalize_player_id

Decision = Literal["EXERCISE", "DECLINE"]


# NOTE: This module is invoked inside LeagueService.expire_contracts_for_season_transition,
# which runs inside a DB transaction. Option policies MUST be:
# - fast
# - deterministic
# - side-effect free
# - resilient (never crash the offseason pipeline)


_PLAYER_ROW_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}
_PLAYER_ROW_CACHE_MAX = 4096


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _get_db_path_from_game_state(game_state: Mapping[str, Any]) -> Optional[str]:
    league = game_state.get("league") if isinstance(game_state, Mapping) else None
    if not isinstance(league, Mapping):
        return None
    db_path = league.get("db_path")
    return str(db_path) if db_path else None


def _get_ui_player_meta(game_state: Mapping[str, Any], player_id: str) -> Dict[str, Any]:
    ui_cache = game_state.get("ui_cache") if isinstance(game_state, Mapping) else None
    if not isinstance(ui_cache, Mapping):
        return {}
    players = ui_cache.get("players")
    if not isinstance(players, Mapping):
        return {}
    row = players.get(str(player_id))
    return dict(row) if isinstance(row, Mapping) else {}


def _load_player_row_ro(db_path: str, player_id: str) -> Dict[str, Any]:
    """Load minimal player info from DB (read-only) with a small in-process cache."""
    key = (str(db_path), str(player_id))
    cached = _PLAYER_ROW_CACHE.get(key)
    if isinstance(cached, dict):
        return cached

    if len(_PLAYER_ROW_CACHE) >= _PLAYER_ROW_CACHE_MAX:
        # Simple protection against unbounded memory growth.
        _PLAYER_ROW_CACHE.clear()

    # Read-only connection to avoid interacting with the active write txn.
    # WAL mode (enabled in LeagueRepo) allows readers during writes.
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(
            "SELECT age, ovr, attrs_json FROM players WHERE player_id=?;",
            (str(player_id),),
        ).fetchone()
        if not r:
            out: Dict[str, Any] = {}
        else:
            out = {
                "age": _safe_int(r["age"], 0),
                "ovr": _safe_int(r["ovr"], 0),
                "attrs_json": r["attrs_json"],
            }
    finally:
        try:
            conn.close()
        except Exception:
            pass

    _PLAYER_ROW_CACHE[key] = out
    return out


def _extract_mental_map(attrs_json: Any) -> Mapping[str, Any]:
    """Return logical mental mapping expected by agency.options (0..100 ints)."""
    try:
        from agency.config import DEFAULT_CONFIG
        from agency.utils import extract_mental_from_attrs

        return extract_mental_from_attrs(attrs_json, keys=DEFAULT_CONFIG.mental_attr_keys)
    except Exception:
        # Be conservative: missing mental => neutral defaults in agency.options.
        return {}


def normalize_option_type(value: str) -> str:
    normalized = str(value).strip().upper()
    if normalized not in {"TEAM", "PLAYER", "ETO"}:
        return "PLAYER"
    return normalized


def default_option_decision_policy(
    option: dict,
    player_id: str,
    contract: dict,
    game_state: dict,
) -> Decision:
    """Default policy for contract options.

    Design intent
    -------------
    - TEAM option: always EXERCISE (user-facing decisions are handled elsewhere).
    - PLAYER option / ETO: use agency.options (market-aware, deterministic, mental-modulated).

    Safety
    ------
    - Never crash the offseason pipeline.
    - If required context is missing, fall back to EXERCISE for stability.
    """
    normalize_player_id(player_id, strict=False, allow_legacy_numeric=True)

    try:
        option_type = normalize_option_type(str(option.get("type") or option.get("option_type") or ""))
    except Exception:
        return "EXERCISE"

    # TEAM options are handled explicitly for the user team; for AI we keep it simple.
    if option_type == "TEAM":
        return "EXERCISE"

    # Resolve option salary from the contract salary_by_year for that season.
    season_year = _safe_int(option.get("season_year"), 0)
    if season_year <= 0:
        return "EXERCISE"

    salary_by_year = contract.get("salary_by_year") if isinstance(contract, Mapping) else None
    if not isinstance(salary_by_year, Mapping):
        return "EXERCISE"

    option_salary = _safe_float(salary_by_year.get(str(season_year)), 0.0)
    if option_salary <= 0.0:
        # If salary is missing/invalid, exercising is the safest non-destructive choice.
        return "EXERCISE"

    # Prefer UI cache for speed; fall back to DB read when necessary.
    ui = _get_ui_player_meta(game_state, str(player_id))
    age = _safe_int(ui.get("age"), 0)
    ovr = _safe_int(ui.get("overall") or ui.get("ovr"), 0)
    team_id = str(ui.get("team_id") or "") or None

    # If a future UI cache includes mental values, prefer it.
    mental: Mapping[str, Any] = ui.get("mental") if isinstance(ui.get("mental"), Mapping) else {}

    # Use DB fallback for missing age/ovr and to extract mental from attrs_json.
    db_path = _get_db_path_from_game_state(game_state)
    if db_path and (age <= 0 or ovr <= 0 or not mental):
        try:
            row = _load_player_row_ro(db_path, str(player_id))
            if age <= 0:
                age = _safe_int(row.get("age"), age)
            if ovr <= 0:
                ovr = _safe_int(row.get("ovr"), ovr)
            if not mental:
                mental = _extract_mental_map(row.get("attrs_json"))
        except Exception:
            mental = mental or {}

    # Still missing critical values -> stable fallback.
    if age <= 0 or ovr <= 0:
        return "EXERCISE"

    # Agency-driven decision.
    try:
        from agency.config import DEFAULT_CONFIG
        from agency.options import decide_player_option
        from agency.types import PlayerOptionInputs

        # Cap-normalized option curve: anchor market AAV to the cap for that season.
        opt_cfg = DEFAULT_CONFIG.options
        try:
            cap_y = float(_cap_for_season_year_from_state(game_state, int(season_year)))
            if cap_y > 0.0:
                opt_cfg = replace(opt_cfg, salary_cap=float(cap_y))
        except Exception:
            opt_cfg = DEFAULT_CONFIG.options
        
        res = decide_player_option(
            PlayerOptionInputs(
                player_id=str(player_id),
                ovr=int(ovr),
                age=int(age),
                option_salary=float(option_salary),
                team_id=str(team_id).upper() if team_id else None,
                team_win_pct=None,
                injury_risk=0.0,
                mental=mental or {},
            ),
            cfg=opt_cfg,
            seed_salt=str(season_year),
        )
        return res.decision
    except Exception:
        # Never crash option processing.
        return "EXERCISE"


# ---------------------------------------------------------------------------
# Optional: AI TEAM option policy helpers
# ---------------------------------------------------------------------------

def _stable_u32(text: str) -> int:
    """Deterministic 32-bit unsigned integer from text."""
    h = hashlib.blake2b(str(text).encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(h, "little", signed=False)


def _stable_rand01(*parts: object) -> float:
    """Deterministic pseudo-random float in [0, 1)."""
    key = "|".join(str(p) for p in parts)
    return float(_stable_u32(key) % 1_000_000) / 1_000_000.0


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        x = float(value)  # type: ignore[arg-type]
        if x != x:  # NaN
            return float(default)
        return x
    except Exception:
        return float(default)


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except Exception:
        return int(default)


def _cap_for_season_year_from_state(game_state: dict, season_year: int) -> int:
    """Return salary cap for `season_year` using the SSOT CapModel + state trade_rules.

    IMPORTANT (SSOT)
    ---------------
    This function intentionally avoids duplicating cap math (growth/rounding)
    and defers to `cap_model.CapModel`.
    """
    y = int(season_year)
    league = game_state.get("league") if isinstance(game_state, dict) else None
    trade_rules = league.get("trade_rules") if isinstance(league, dict) else None
    if not isinstance(trade_rules, dict):
        trade_rules = {}

    # Best-effort "current season" extraction for frozen-cap semantics.
    cur_sy: Optional[int] = None
    if isinstance(league, dict):
        cur_sy_raw = league.get("season_year") or league.get("current_season_year") or league.get("year")
        cur_sy_i = _coerce_int(cur_sy_raw, 0)
        if cur_sy_i > 0:
            cur_sy = int(cur_sy_i)

    # If CapModel isn't available for some reason, fall back to state-provided cap only.
    # (No formula duplication here.)
    if CapModel is None:
        cap_now = _coerce_int(trade_rules.get("salary_cap"), 0)
        return int(cap_now) if cap_now > 0 else 0

    try:
        cap_model = CapModel.from_trade_rules(trade_rules, current_season_year=cur_sy)
        return int(cap_model.salary_cap_for_season(int(y)))
    except Exception:
        # Last-resort fallback: use state cap if present.
        cap_now = _coerce_int(trade_rules.get("salary_cap"), 0)
        return int(cap_now) if cap_now > 0 else 0

def make_ai_team_option_decision_policy(
    *,
    user_team_id: Optional[str] = None,
    baseline_decline_threshold: float = 2.2,
) -> Callable[[dict, str, dict, dict], Decision]:
    """Create an opt-in policy: AI teams evaluate TEAM options and may DECLINE.

    Scope:
    - Only affects TEAM options.
    - Only affects AI teams (team_id != user_team_id).
    - Leaves PLAYER/ETO options as default (EXERCISE) for now.

    The scoring is intentionally simple and uses only the passed-in game_state
    snapshot (ui_cache + season stats). It is deterministic and cheap.

    Tuning tip:
    - Increase baseline_decline_threshold to make AI *more* likely to decline.
    - Decrease it to make AI *more* likely to exercise.
    """

    user_team_norm = str(user_team_id).strip().upper() if user_team_id else None
    base_threshold = float(baseline_decline_threshold)

    def _policy(option: dict, player_id: str, contract: dict, game_state: dict) -> Decision:
        # Defensive: keep default stability.
        try:
            opt_type = normalize_option_type(option.get("type"))
        except Exception:
            return "EXERCISE"
        if opt_type != "TEAM":
            return "EXERCISE"

        team_id = str(contract.get("team_id") or "").strip().upper()
        if not team_id or team_id == "FA":
            return "EXERCISE"
        if user_team_norm and team_id == user_team_norm:
            # Safety: user TEAM options should be decided by the user flow (hard gate).
            return "EXERCISE"

        opt_year = _coerce_int(option.get("season_year"), 0)
        salary = _coerce_float((contract.get("salary_by_year") or {}).get(str(opt_year)), 0.0)
        salary_m = float(salary) / 1_000_000.0

        # Player UI meta (derived from DB; kept in runtime state for UI).
        ui_players = ((game_state.get("ui_cache") or {}).get("players") or {})
        p = ui_players.get(str(player_id)) if isinstance(ui_players, dict) else None
        if not isinstance(p, dict):
            return "EXERCISE"  # conservative fallback

        ovr = _coerce_float(p.get("overall"), _coerce_float(p.get("ovr"), 0.0))
        age = _coerce_int(p.get("age"), 0)
        potential = _coerce_float(p.get("potential"), 0.6)  # 0.4~1.0-ish

        # Last-season usage signal (minutes per game).
        mpg = 0.0
        ps = (game_state.get("player_stats") or {}).get(str(player_id))
        if isinstance(ps, dict):
            g = _coerce_int(ps.get("games"), 0)
            totals = ps.get("totals")
            if g > 0 and isinstance(totals, dict):
                mpg = _coerce_float(totals.get("MIN"), 0.0) / float(g)

        cap = _cap_for_season_year_from_state(game_state, opt_year)
        salary_pct = (float(salary) / float(cap)) if cap > 0 else 0.0

        # Contract type biases (only present when written into contract_json).
        ct = str(contract.get("contract_type") or "").strip().upper()
        start_year = _coerce_int(contract.get("start_season_year"), 0)
        option_offset = opt_year - start_year  # e.g. 2 => 3rd season of deal
        bias = 0.0
        if ct == "ROOKIE_SCALE":
            # NBA-like feel: 3rd-year option should be exercised most of the time.
            bias += 0.8
            if option_offset == 2:
                bias += 0.6
            elif option_offset == 3:
                bias += 0.2
        elif ct == "SECOND_ROUND_EXCEPTION":
            bias += 0.4

        # Simple quality proxy.
        quality = (ovr - 55.0)
        quality += (potential - 0.6) * 12.0
        quality += (mpg - 10.0) * 0.2
        quality -= max(0.0, float(age) - 25.0) * 0.5

        # Expensive deals get a harsher penalty (esp. top picks who bust).
        quality -= max(0.0, salary_pct - 0.03) * 200.0  # penalty starts above ~3% of cap

        value_score = quality / max(float(salary_m), 0.75)
        value_score += bias

        # Hard keep / hard cut rules (helps avoid bizarre outcomes).
        if ovr >= 78.0:
            return "EXERCISE"
        if (ovr <= 60.0 and potential < 0.65 and mpg < 6.0 and salary_pct > 0.015):
            return "DECLINE"

        # Team-level patience tweak (if present; UI-only but good enough for flavor).
        threshold = base_threshold
        ui_teams = ((game_state.get("ui_cache") or {}).get("teams") or {})
        t = ui_teams.get(team_id) if isinstance(ui_teams, dict) else None
        if isinstance(t, dict):
            pat = _coerce_float(t.get("patience"), 0.5)  # 0~1
            # patient => lower threshold => more EXERCISE
            threshold -= (pat - 0.5) * 0.6

        # Add small deterministic "human error" noise for borderline cases.
        noise = (_stable_rand01(team_id, player_id, opt_year) - 0.5) * 0.6  # [-0.3, +0.3]

        return "EXERCISE" if (value_score + noise) >= threshold else "DECLINE"

    return _policy
