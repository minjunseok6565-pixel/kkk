from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple, Set, Mapping

from derived_formulas import compute_derived
from league_repo import LeagueRepo
from matchengine_v3.models import Player, TeamState
from matchengine_v3.offense_roles import (
    # Canonical C13 role keys (SSOT)
    ROLE_ENGINE_PRIMARY,
    ROLE_ENGINE_SECONDARY,
    ROLE_TRANSITION_ENGINE,
    ROLE_SHOT_CREATOR,
    ROLE_RIM_PRESSURE,
    ROLE_SPOTUP_SPACER,
    ROLE_MOVEMENT_SHOOTER,
    ROLE_CUTTER_FINISHER,
    ROLE_CONNECTOR,
    ROLE_ROLL_MAN,
    ROLE_SHORTROLL_HUB,
    ROLE_POP_THREAT,
    ROLE_POST_ANCHOR,
    ALL_OFFENSE_ROLES,
)
from matchengine_v3.role_fit import role_fit_score
from matchengine_v3.tactics import TacticsConfig, canonical_defense_scheme
import schema


logger = logging.getLogger(__name__)
_WARN_COUNTS: Dict[str, int] = {}


def _warn_limited(code: str, msg: str, *, limit: int = 3) -> None:
    """Log warning with traceback, but cap repeats per code."""
    n = _WARN_COUNTS.get(code, 0)
    if n < limit:
        logger.warning("%s %s", code, msg, exc_info=True)
    _WARN_COUNTS[code] = n + 1

_SIM_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SIM_DIR)


def _find_json_path(filename: str) -> Optional[str]:
    """Find a config json file in common locations.

    Search order (first hit wins):
      1) project root: <project>/<filename>
      2) project data dir: <project>/data/<filename>
      3) project config dir: <project>/config/<filename>
      4) sim dir: <project>/sim/<filename>
    """
    candidates = [
        os.path.join(_PROJECT_DIR, filename),
        os.path.join(_PROJECT_DIR, "data", filename),
        os.path.join(_PROJECT_DIR, "config", filename),
        os.path.join(_SIM_DIR, filename),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


@lru_cache(maxsize=1)
def _load_team_coach_preset_map() -> Dict[str, str]:
    """Load team->preset mapping from team_coach_presets.json (optional).

    Expected format:
      {
        "version": "1.0",
        "teams": { "LAL": "Playoff Tight", ... }
      }
    Also accepts a plain dict {"LAL": "..."} for flexibility.
    Missing file or parse errors -> empty dict (safe no-op).
    """
    path = _find_json_path("team_coach_presets.json")
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("teams"), dict):
            return {str(k).upper(): str(v) for k, v in data["teams"].items()}
        if isinstance(data, dict):
            # allow flat map
            return {str(k).upper(): str(v) for k, v in data.items() if isinstance(v, (str, int, float))}
        return {}
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        _warn_limited("ROSTER_PRESET_LOAD_FAILED", f"path={path!r}")
        return {}


@lru_cache(maxsize=1)
def _load_coach_presets_raw() -> Dict[str, Dict[str, Any]]:
    """Load coach preset definitions from coach_presets.json (optional).

    Expected format:
      {
        "version": "1.0",
        "presets": {
          "Balanced": { ... },
          "Playoff Tight": { ... }
        }
      }

    Also accepts a flat map {"Balanced": {...}, ...} for flexibility.
    Missing file or parse errors -> empty dict (safe no-op).
    """
    path = _find_json_path("coach_presets.json")
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict) and isinstance(data.get("presets"), dict):
            presets = data["presets"]
        elif isinstance(data, dict):
            presets = data
        else:
            return {}

        out: Dict[str, Dict[str, Any]] = {}
        for k, v in presets.items():
            if isinstance(k, str) and isinstance(v, dict):
                out[k] = v
        return out
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        _warn_limited("ROSTER_PRESET_LOAD_FAILED", f"path={path!r}")
        return {}


def _apply_coach_preset_tactics(
    team_id: str,
    cfg: TacticsConfig,
    raw_tactics: Optional[Dict[str, Any]],
) -> None:
    """Apply tactics values from coach_presets.json based on cfg.context['COACH_PRESET'].

    Rules:
      - If USER_COACH is enabled in context, do nothing (user controls tactics).
      - Never override values explicitly provided by the caller in raw_tactics.
      - Supports preset fields either at top-level or under a nested "tactics" dict.
      - (A안) Reads scheme_weight_sharpness + scheme_outcome_strength.
    """
    if not isinstance(getattr(cfg, "context", None), dict):
        return
    if cfg.context.get("USER_COACH"):
        return

    preset_name = cfg.context.get("COACH_PRESET")
    if not preset_name:
        return

    presets = _load_coach_presets_raw()
    if not presets:
        return

    key = str(preset_name).strip()
    preset = presets.get(key)
    if preset is None:
        # case-insensitive fallback
        lower_map = {k.lower(): k for k in presets.keys() if isinstance(k, str)}
        canon = lower_map.get(key.lower())
        preset = presets.get(canon) if canon else None
    if not isinstance(preset, dict):
        return

    src = preset.get("tactics") if isinstance(preset.get("tactics"), dict) else preset

    raw = raw_tactics or {}

    # Do not override explicit caller inputs.
    if "offense_scheme" not in raw and "offense_scheme" in src:
        cfg.offense_scheme = str(src.get("offense_scheme") or cfg.offense_scheme)
    if "defense_scheme" not in raw and "defense_scheme" in src:
        cfg.defense_scheme = str(src.get("defense_scheme") or cfg.defense_scheme)

    # Strength knobs: treat offense+defense as a pair.
    caller_set_sharp = ("scheme_weight_sharpness" in raw) or ("def_scheme_weight_sharpness" in raw)
    if not caller_set_sharp:
        if "scheme_weight_sharpness" in src:
            v = float(src["scheme_weight_sharpness"])
            cfg.scheme_weight_sharpness = v
            # If preset doesn't specify defense separately, mirror offense value.
            if "def_scheme_weight_sharpness" not in src:
                cfg.def_scheme_weight_sharpness = v
        if "def_scheme_weight_sharpness" in src:
            cfg.def_scheme_weight_sharpness = float(src["def_scheme_weight_sharpness"])

    caller_set_outcome = ("scheme_outcome_strength" in raw) or ("def_scheme_outcome_strength" in raw)
    if not caller_set_outcome:
        if "scheme_outcome_strength" in src:
            v = float(src["scheme_outcome_strength"])
            cfg.scheme_outcome_strength = v
            if "def_scheme_outcome_strength" not in src:
                cfg.def_scheme_outcome_strength = v
        if "def_scheme_outcome_strength" in src:
            cfg.def_scheme_outcome_strength = float(src["def_scheme_outcome_strength"])


def _apply_default_coach_preset(team_id: str, cfg: TacticsConfig) -> None:
    """Inject COACH_PRESET into tactics.context if not explicitly provided."""
    if not isinstance(getattr(cfg, "context", None), dict):
        return
    # Respect explicit preset from caller/tactics input.
    if "COACH_PRESET" in cfg.context:
        return

    mapping = _load_team_coach_preset_map()
    preset = mapping.get(str(team_id).upper())
    if preset:
        cfg.context["COACH_PRESET"] = str(preset)


def _build_tactics_config(raw: Optional[Dict[str, Any]]) -> TacticsConfig:
    if not raw:
        return TacticsConfig()

    cfg = TacticsConfig(
        offense_scheme=str(raw.get("offense_scheme") or "Spread_HeavyPnR"),
        defense_scheme=str(raw.get("defense_scheme") or "Drop"),
    )

    if "scheme_weight_sharpness" in raw:
        cfg.scheme_weight_sharpness = float(raw["scheme_weight_sharpness"])
    if "scheme_outcome_strength" in raw:
        cfg.scheme_outcome_strength = float(raw["scheme_outcome_strength"])
    if "def_scheme_weight_sharpness" in raw:
        cfg.def_scheme_weight_sharpness = float(raw["def_scheme_weight_sharpness"])
    if "def_scheme_outcome_strength" in raw:
        cfg.def_scheme_outcome_strength = float(raw["def_scheme_outcome_strength"])

    cfg.action_weight_mult = dict(raw.get("action_weight_mult") or {})
    cfg.outcome_global_mult = dict(raw.get("outcome_global_mult") or {})
    cfg.outcome_by_action_mult = dict(raw.get("outcome_by_action_mult") or {})
    cfg.def_action_weight_mult = dict(raw.get("def_action_weight_mult") or {})
    cfg.opp_action_weight_mult = dict(raw.get("opp_action_weight_mult") or {})
    cfg.opp_outcome_global_mult = dict(raw.get("opp_outcome_global_mult") or {})
    cfg.opp_outcome_by_action_mult = dict(raw.get("opp_outcome_by_action_mult") or {})

    # Allow caller to pass arbitrary context (e.g., USER_COACH, ROTATION_POOL_PIDS, etc.)
    raw_ctx = raw.get("context")
    if isinstance(raw_ctx, dict) and raw_ctx:
        cfg.context.update(raw_ctx)
        
    pace = raw.get("pace")
    if pace is not None:
        cfg.context["PACE"] = pace

    return cfg


# ---------------------------------------------------------------------------
# Public tactics helpers
# ---------------------------------------------------------------------------


def resolve_effective_schemes(
    team_id: str,
    raw_tactics: Optional[Mapping[str, Any]],
) -> Tuple[str, str]:
    """Resolve effective (offense_scheme, defense_scheme) for a team.

    SSOT:
      Coach preset application lives in roster_adapter. Multiple subsystems
      (injury/readiness/practice/AI) need the *exact* same resolution rules;
      they should call this helper rather than duplicating the logic.

    Rules:
      - Build a TacticsConfig from caller input (if any).
      - Apply default coach preset mapping (team_coach_presets.json) when the
        caller didn't explicitly set COACH_PRESET.
      - Apply coach preset tactics unless USER_COACH is enabled.
      - Never override explicit caller-provided scheme fields.

    Returns:
      (offense_scheme_key, defense_scheme_key) where defense scheme is
      canonicalized.
    """

    raw_dict: Optional[Dict[str, Any]]
    if raw_tactics is None:
        raw_dict = None
    elif isinstance(raw_tactics, dict):
        raw_dict = raw_tactics
    else:
        try:
            raw_dict = dict(raw_tactics)
        except Exception:
            raw_dict = None

    cfg = _build_tactics_config(raw_dict)
    _apply_default_coach_preset(team_id, cfg)
    _apply_coach_preset_tactics(team_id, cfg, raw_dict)

    off = str(cfg.offense_scheme)
    de = canonical_defense_scheme(cfg.defense_scheme)
    return (off, de)




def _coerce_str_map(raw: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not isinstance(raw, Mapping):
        return out
    for k, v in raw.items():
        try:
            kk = str(schema.normalize_player_id(k))
        except Exception:
            continue
        vv = str(v or '').strip()
        if not kk or not vv:
            continue
        out[kk] = vv
    return out


def _normalize_offense_role_payload(
    *,
    lineup: List[Player],
    raw_tactics: Optional[Dict[str, Any]],
    auto_role_by_pid: Mapping[str, str],
) -> Dict[str, str]:
    """Return effective pid->offense-role mapping (auto base + optional user overrides).

    Priority:
      1) USER_OFFENSE_ROLE_BY_PID / ROTATION_OFFENSE_ROLE_BY_PID / OFFENSE_ROLE_BY_PID
         (top-level and context), if present and valid
      2) auto_role_by_pid fallback

    Invalid pids/roles are ignored with warnings.
    """
    effective: Dict[str, str] = {str(pid): str(role) for pid, role in (auto_role_by_pid or {}).items()}
    if not raw_tactics:
        return effective

    roster_set = {str(p.pid) for p in (lineup or [])}
    ctx = raw_tactics.get('context') if isinstance(raw_tactics, dict) else None
    ctx = ctx if isinstance(ctx, dict) else {}

    merged_raw: Dict[str, str] = {}
    candidates = [
        raw_tactics.get('user_offense_role_by_pid'),
        raw_tactics.get('rotation_offense_role_by_pid'),
        raw_tactics.get('offense_role_by_pid'),
        ctx.get('USER_OFFENSE_ROLE_BY_PID'),
        ctx.get('ROTATION_OFFENSE_ROLE_BY_PID'),
        ctx.get('OFFENSE_ROLE_BY_PID'),
    ]
    for src in candidates:
        merged_raw.update(_coerce_str_map(src))

    for pid, role in merged_raw.items():
        if pid not in roster_set:
            _warn_limited('ROSTER_USER_OFF_ROLE_INVALID_PID', f'pid={pid} not in lineup; ignored')
            continue
        if role not in ALL_OFFENSE_ROLES:
            _warn_limited('ROSTER_USER_OFF_ROLE_INVALID_KEY', f'pid={pid} role={role} invalid; ignored')
            continue
        effective[pid] = role

    return effective


def _normalize_defense_role_override_payload(
    *,
    lineup: List[Player],
    raw_tactics: Optional[Dict[str, Any]],
) -> Dict[str, str]:
    """Return validated defensive scheme-role override mapping (role_name -> pid)."""
    if not raw_tactics:
        return {}

    roster_set = {str(p.pid) for p in (lineup or [])}
    ctx = raw_tactics.get('context') if isinstance(raw_tactics, dict) else None
    ctx = ctx if isinstance(ctx, dict) else {}

    raw = (
        raw_tactics.get('defense_role_overrides')
        or raw_tactics.get('defense_roles')
        or ctx.get('DEFENSE_ROLE_OVERRIDES')
        or ctx.get('DEFENSE_ROLES')
    )
    if not isinstance(raw, Mapping):
        return {}

    out: Dict[str, str] = {}
    pid_used: Dict[str, str] = {}
    for role_name_raw, pid_raw in raw.items():
        role_name = str(role_name_raw or '').strip()
        pid = str(pid_raw or '').strip()
        if not role_name or not pid:
            continue
        if pid not in roster_set:
            _warn_limited('ROSTER_USER_DEF_ROLE_INVALID_PID', f'role={role_name} pid={pid} not in lineup; ignored')
            continue
        prev_role = pid_used.get(pid)
        if prev_role is not None and prev_role != role_name:
            _warn_limited('ROSTER_USER_DEF_ROLE_DUP_PID', f'pid={pid} assigned to multiple defensive roles; ignored role={role_name}')
            continue
        out[role_name] = pid
        pid_used[pid] = role_name

    return out


def _assign_rotation_offense_role_by_pid(players: List[Player]) -> Dict[str, str]:
    """Assign a single canonical offensive role (C13) to each player.

    Output:
      { pid: role_key }

    Notes:
    - Uses matchengine_v3.role_fit.role_fit_score so tuning stays centralized in role_fit_data.py.
    - This is the roster-level SSOT role identity (used by rotation/fatigue subsystems).
    """
    out: Dict[str, str] = {}
    if not players:
        return out

    for p in players:
        best_role: Optional[str] = None
        best_fit = -1.0
        for role in ALL_OFFENSE_ROLES:
            try:
                fit = float(role_fit_score(p, role))
            except Exception:
                fit = 50.0
            if fit > best_fit:
                best_fit = fit
                best_role = role
        out[str(p.pid)] = str(best_role or ROLE_SPOTUP_SPACER)
    return out


def _build_offense_role_slots_for_on_court(
    on_court: List[Player],
    role_by_pid: Mapping[str, str],
) -> Dict[str, str]:
    """Build TeamState.roles (role->pid) mapping for the *current* on-court 5.

    This mapping is consumed by shot_diet / participants subsystems.
    We populate *all* canonical C13 role keys so the engine can always find a slot.

    Duplicates are allowed (e.g., one player may be both SpotUp_Spacer and Movement_Shooter
    if the lineup lacks specialists).
    """
    if not on_court:
        return {}

    # Small helper for robustness.
    def _fit(p: Player, role: str) -> float:
        try:
            return float(role_fit_score(p, role))
        except Exception:
            return 50.0

    def _best_pid_for_role(role: str, *, exclude: Optional[Set[str]] = None) -> str:
        exclude = exclude or set()

        # 1) Prefer players whose SSOT rotation role matches this role (if any).
        preferred = [p for p in on_court if p.pid not in exclude and role_by_pid.get(p.pid) == role]
        candidates = preferred if preferred else [p for p in on_court if p.pid not in exclude]
        if not candidates:
            candidates = list(on_court)

        best = max(candidates, key=lambda p: _fit(p, role))
        return best.pid

    roles: Dict[str, str] = {}

    # Handlers (try to avoid forcing the same pid for both primary/secondary when possible).
    primary = _best_pid_for_role(ROLE_ENGINE_PRIMARY)
    roles[ROLE_ENGINE_PRIMARY] = primary
    secondary = _best_pid_for_role(ROLE_ENGINE_SECONDARY, exclude={primary})
    roles[ROLE_ENGINE_SECONDARY] = secondary

    # Creation / pressure
    roles[ROLE_TRANSITION_ENGINE] = _best_pid_for_role(ROLE_TRANSITION_ENGINE)
    roles[ROLE_SHOT_CREATOR] = _best_pid_for_role(ROLE_SHOT_CREATOR)
    roles[ROLE_RIM_PRESSURE] = _best_pid_for_role(ROLE_RIM_PRESSURE)

    # Spacing / off-ball
    roles[ROLE_SPOTUP_SPACER] = _best_pid_for_role(ROLE_SPOTUP_SPACER)
    roles[ROLE_MOVEMENT_SHOOTER] = _best_pid_for_role(ROLE_MOVEMENT_SHOOTER)
    roles[ROLE_CUTTER_FINISHER] = _best_pid_for_role(ROLE_CUTTER_FINISHER)
    roles[ROLE_CONNECTOR] = _best_pid_for_role(ROLE_CONNECTOR)

    # Screen / big roles
    roles[ROLE_ROLL_MAN] = _best_pid_for_role(ROLE_ROLL_MAN)
    roles[ROLE_SHORTROLL_HUB] = _best_pid_for_role(ROLE_SHORTROLL_HUB)
    roles[ROLE_POP_THREAT] = _best_pid_for_role(ROLE_POP_THREAT)
    roles[ROLE_POST_ANCHOR] = _best_pid_for_role(ROLE_POST_ANCHOR)

    return roles


def _select_lineup(
    roster: List[Player],
    starters: Optional[List[str]],
    bench: Optional[List[str]],
    max_players: int,
) -> List[Player]:
    roster_by_pid = {p.pid: p for p in roster}
    chosen: List[Player] = []
    chosen_ids = set()

    for pid in starters or []:
        player = roster_by_pid.get(str(pid))
        if player and player.pid not in chosen_ids:
            chosen.append(player)
            chosen_ids.add(player.pid)

    for pid in bench or []:
        player = roster_by_pid.get(str(pid))
        if player and player.pid not in chosen_ids:
            chosen.append(player)
            chosen_ids.add(player.pid)

    for player in roster:
        if len(chosen) >= max_players:
            break
        if player.pid not in chosen_ids:
            chosen.append(player)
            chosen_ids.add(player.pid)

    if len(chosen) < 5:
        raise ValueError(f"team has fewer than 5 players (got {len(chosen)})")

    return chosen[:max_players]


def _normalize_pid_list(values: Optional[List[Any]]) -> List[str]:
    out: List[str] = []
    for v in values or []:
        out.append(str(schema.normalize_player_id(v)))
    return out


def load_team_players_from_db(
    repo: LeagueRepo,
    team_id: str,
    *,
    exclude_pids: Optional[Set[str]] = None,
    attrs_mods_by_pid: Optional[Mapping[str, Mapping[str, float]]] = None,
) -> Tuple[List[Player], Optional[str]]:
    """Load team roster from DB and adapt to matchengine_v3 Players.

    Args:
        exclude_pids:
            Players who are unavailable (e.g., OUT injuries) and should be excluded from
            the returned roster.

            Commercial safety: if exclusion would drop the roster below 5 players, the
            exclusion is ignored (the game must be simulatable).
        attrs_mods_by_pid:
            Temporary attribute deltas (e.g., RETURNING injury debuffs) to apply before
            computing derived ratings. Keys must match ``players.attrs_json`` rating keys.
    """
    tid = schema.normalize_team_id(team_id)
    roster_rows = repo.get_team_roster(str(tid))
    if not roster_rows:
        raise ValueError(f"Team '{str(tid)}' not found in roster DB")

    # Best-effort display name: if roster rows provide a team display name, use it.
    # If absent, caller should fall back to team_id (display-only default).
    team_display_name: Optional[str] = None
    try:
        first = roster_rows[0]
        if isinstance(first, dict):
            raw = first.get("team_name") or first.get("teamName") or first.get("team_display_name")
            if raw is not None and str(raw).strip():
                team_display_name = str(raw).strip()
    except Exception:
        # Display-only; do not hide real data problems elsewhere.
        team_display_name = None

    mods_map: Mapping[str, Mapping[str, float]] = attrs_mods_by_pid or {}

    players_all: List[Player] = []
    for row in roster_rows:
        pid = schema.normalize_player_id(row.get("player_id"))

        attrs0 = row.get("attrs") or {}
        attrs = dict(attrs0) if isinstance(attrs0, dict) else {}

        # Apply temporary attribute modifiers (e.g., RETURNING debuffs) before derived.
        mods = mods_map.get(str(pid))
        if isinstance(mods, Mapping) and mods:
            for k, delta in mods.items():
                key = str(k)
                try:
                    base = float(attrs.get(key, 50.0))
                except Exception:
                    base = 50.0
                try:
                    d = float(delta)
                except Exception:
                    continue
                v = base + d
                # 2K-style clamp. Keep floats; derived formulas handle numeric types.
                if v < 0.0:
                    v = 0.0
                if v > 99.0:
                    v = 99.0
                attrs[key] = v

        derived = compute_derived(attrs)

        # Age is stored in players table and is needed by fatigue/injury subsystems.
        # Keep this robust even if upstream data is missing or malformed.
        age_i = 0
        try:
            age_i = int(row.get("age") or attrs0.get("Age") or 0)
        except Exception:
            age_i = 0

        # Injury traits (I_InjuryFreq 1..10, Overall Durability 1..100).
        injury_freq = 5.0
        try:
            injury_freq = float(attrs0.get("I_InjuryFreq", 5.0) or 5.0)
        except Exception:
            injury_freq = 5.0
        injury_freq = max(1.0, min(10.0, injury_freq))

        durability = 70.0
        try:
            durability = float(attrs0.get("Overall Durability", attrs0.get("Durability", 70.0)) or 70.0)
        except Exception:
            durability = 70.0
        durability = max(1.0, min(100.0, durability))

        players_all.append(
            Player(
                pid=str(pid),
                name=str(row.get("name") or attrs0.get("Name") or ""),
                pos=str(row.get("pos") or attrs0.get("POS") or attrs0.get("Position") or "G"),
                derived=derived,
                age=age_i,
                injury_freq=injury_freq,
                durability=durability,
            )
        )

    # Apply hard exclusions (OUT injuries) at the roster level.
    if exclude_pids:
        try:
            excl = {str(schema.normalize_player_id(x)) for x in exclude_pids if x is not None and str(x).strip()}
        except Exception:
            excl = set()

        if excl:
            filtered = [p for p in players_all if p.pid not in excl]
            if len(filtered) >= 5:
                return filtered, team_display_name

            _warn_limited(
                "ROSTER_EXCLUDE_TOO_SMALL",
                f"team={str(tid)} exclude={len(excl)} would leave {len(filtered)} players; ignoring exclusions for safety",
            )

    return players_all, team_display_name


def build_team_state_from_db(
    *,
    repo: LeagueRepo,
    team_id: str,
    tactics: Optional[Dict[str, Any]] = None,
    exclude_pids: Optional[Set[str]] = None,
    attrs_mods_by_pid: Optional[Mapping[str, Mapping[str, float]]] = None,
) -> TeamState:
    tid = schema.normalize_team_id(team_id)
    lineup_info = tactics.get("lineup", {}) if tactics else {}
    starters = _normalize_pid_list(lineup_info.get("starters") or [])
    bench = _normalize_pid_list(lineup_info.get("bench") or [])
    max_players = int((tactics or {}).get("rotation_size") or 10)

    players, team_display_name = load_team_players_from_db(
        repo,
        str(tid),
        exclude_pids=exclude_pids,
        attrs_mods_by_pid=attrs_mods_by_pid,
    )
    max_players = max(5, min(max_players, len(players)))
    lineup = _select_lineup(players, starters, bench, max_players=max_players)

    # New canonical offensive roles (C13): roster-level SSOT + optional user overrides.
    auto_rotation_role_by_pid = _assign_rotation_offense_role_by_pid(lineup)
    rotation_role_by_pid = _normalize_offense_role_payload(
        lineup=lineup,
        raw_tactics=tactics,
        auto_role_by_pid=auto_rotation_role_by_pid,
    )
    roles = _build_offense_role_slots_for_on_court(lineup[:5], rotation_role_by_pid)
    defense_role_overrides = _normalize_defense_role_override_payload(
        lineup=lineup,
        raw_tactics=tactics,
    )
    tactics_cfg = _build_tactics_config(tactics)
    _apply_default_coach_preset(str(tid), tactics_cfg)
    _apply_coach_preset_tactics(str(tid), tactics_cfg, tactics)

    # SSOT: team identity is *always* team_id. name is display-only.
    display_name = str(team_display_name).strip() if team_display_name else ""
    if not display_name:
        display_name = str(tid)

    team_state = TeamState(
        team_id=str(tid),
        name=display_name,
        lineup=lineup,
        tactics=tactics_cfg,
        roles=roles,
        rotation_offense_role_by_pid=dict(rotation_role_by_pid),
        defense_role_overrides=dict(defense_role_overrides),
    )
    minutes = (tactics or {}).get("minutes") or {}
    if isinstance(minutes, dict) and minutes:
        team_state = replace(
            team_state,
            rotation_target_sec_by_pid={
                str(schema.normalize_player_id(pid)): int(float(mins) * 60)
                for pid, mins in minutes.items()
                if pid is not None and mins is not None
            },
        )

    return team_state
