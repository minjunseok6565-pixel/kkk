from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, Mapping, Sequence


_STREAK_NONE = "-"


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _is_regular_final(game: Mapping[str, Any]) -> bool:
    if str(game.get("status") or "") != "final":
        return False
    phase = str(game.get("phase") or "regular").strip().lower()
    return phase == "regular"


def _new_record() -> Dict[str, Any]:
    return {
        "wins": 0,
        "losses": 0,
        "pf": 0,
        "pa": 0,
        "home_wins": 0,
        "home_losses": 0,
        "away_wins": 0,
        "away_losses": 0,
        "div_wins": 0,
        "div_losses": 0,
        "conf_wins": 0,
        "conf_losses": 0,
        "recent10": [],
        "streak_type": _STREAK_NONE,
        "streak_len": 0,
    }


def _append_result(rec: Dict[str, Any], *, is_win: bool) -> None:
    mark = 1 if is_win else 0
    recent = rec.get("recent10")
    if not isinstance(recent, list):
        recent = []
    recent.append(mark)
    if len(recent) > 10:
        recent[:] = recent[-10:]
    rec["recent10"] = recent

    streak_type = str(rec.get("streak_type") or _STREAK_NONE)
    streak_len = _to_int(rec.get("streak_len"), 0)
    target = "W" if is_win else "L"
    if streak_type == target:
        rec["streak_len"] = streak_len + 1
    else:
        rec["streak_type"] = target
        rec["streak_len"] = 1


def create_empty_standings_cache(team_ids: Iterable[str], *, season_id: str | None = None, version: int = 1) -> Dict[str, Any]:
    records_by_team = {str(t).upper(): _new_record() for t in team_ids if str(t).strip()}
    return {
        "version": int(version),
        "built_from": {
            "season_id": None if season_id is None else str(season_id),
            "regular_final_count": 0,
        },
        "applied_game_ids": {},
        "records_by_team": records_by_team,
    }


def apply_final_game(cache: Mapping[str, Any], game: Mapping[str, Any], team_conf_div_map: Mapping[str, Mapping[str, Any]]) -> tuple[Dict[str, Any], bool]:
    """Apply one regular final game into standings cache.

    Returns `(new_cache, applied)` where `applied=False` means skipped
    (non-final/non-regular/duplicate/invalid input).
    """
    out = deepcopy(dict(cache))

    if not _is_regular_final(game):
        return out, False

    game_id = str(game.get("game_id") or "").strip()
    if not game_id:
        return out, False

    applied_game_ids = out.get("applied_game_ids")
    if not isinstance(applied_game_ids, dict):
        applied_game_ids = {}
        out["applied_game_ids"] = applied_game_ids
    if bool(applied_game_ids.get(game_id)):
        return out, False

    records_by_team = out.get("records_by_team")
    if not isinstance(records_by_team, dict):
        records_by_team = {}
        out["records_by_team"] = records_by_team

    home_id = str(game.get("home_team_id") or "").upper()
    away_id = str(game.get("away_team_id") or "").upper()
    if not home_id or not away_id or home_id == away_id:
        return out, False

    hs = game.get("home_score")
    a_s = game.get("away_score")
    if hs is None or a_s is None:
        return out, False
    home_score = _to_int(hs)
    away_score = _to_int(a_s)
    if home_score == away_score:
        return out, False

    home_rec = records_by_team.get(home_id)
    if not isinstance(home_rec, dict):
        home_rec = _new_record()
        records_by_team[home_id] = home_rec
    away_rec = records_by_team.get(away_id)
    if not isinstance(away_rec, dict):
        away_rec = _new_record()
        records_by_team[away_id] = away_rec

    home_rec["pf"] = _to_int(home_rec.get("pf")) + home_score
    home_rec["pa"] = _to_int(home_rec.get("pa")) + away_score
    away_rec["pf"] = _to_int(away_rec.get("pf")) + away_score
    away_rec["pa"] = _to_int(away_rec.get("pa")) + home_score

    home_info = team_conf_div_map.get(home_id) or {}
    away_info = team_conf_div_map.get(away_id) or {}
    home_conf = str(home_info.get("conference") or "")
    away_conf = str(away_info.get("conference") or "")
    home_div = str(home_info.get("division") or "")
    away_div = str(away_info.get("division") or "")
    same_conf = bool(home_conf) and home_conf == away_conf
    same_div = same_conf and bool(home_div) and home_div == away_div

    if home_score > away_score:
        home_rec["wins"] = _to_int(home_rec.get("wins")) + 1
        away_rec["losses"] = _to_int(away_rec.get("losses")) + 1
        home_rec["home_wins"] = _to_int(home_rec.get("home_wins")) + 1
        away_rec["away_losses"] = _to_int(away_rec.get("away_losses")) + 1
        _append_result(home_rec, is_win=True)
        _append_result(away_rec, is_win=False)
        if same_conf:
            home_rec["conf_wins"] = _to_int(home_rec.get("conf_wins")) + 1
            away_rec["conf_losses"] = _to_int(away_rec.get("conf_losses")) + 1
        if same_div:
            home_rec["div_wins"] = _to_int(home_rec.get("div_wins")) + 1
            away_rec["div_losses"] = _to_int(away_rec.get("div_losses")) + 1
    else:
        away_rec["wins"] = _to_int(away_rec.get("wins")) + 1
        home_rec["losses"] = _to_int(home_rec.get("losses")) + 1
        away_rec["away_wins"] = _to_int(away_rec.get("away_wins")) + 1
        home_rec["home_losses"] = _to_int(home_rec.get("home_losses")) + 1
        _append_result(away_rec, is_win=True)
        _append_result(home_rec, is_win=False)
        if same_conf:
            away_rec["conf_wins"] = _to_int(away_rec.get("conf_wins")) + 1
            home_rec["conf_losses"] = _to_int(home_rec.get("conf_losses")) + 1
        if same_div:
            away_rec["div_wins"] = _to_int(away_rec.get("div_wins")) + 1
            home_rec["div_losses"] = _to_int(home_rec.get("div_losses")) + 1

    applied_game_ids[game_id] = True
    built_from = out.get("built_from")
    if not isinstance(built_from, dict):
        built_from = {"season_id": None, "regular_final_count": 0}
        out["built_from"] = built_from
    built_from["regular_final_count"] = _to_int(built_from.get("regular_final_count")) + 1

    return out, True


def rebuild_cache_from_games(
    team_ids: Iterable[str],
    regular_final_games: Sequence[Mapping[str, Any]],
    team_conf_div_map: Mapping[str, Mapping[str, Any]],
    *,
    season_id: str | None = None,
) -> Dict[str, Any]:
    cache = create_empty_standings_cache(team_ids, season_id=season_id)
    for g in regular_final_games:
        cache, _ = apply_final_game(cache, g, team_conf_div_map)
    return cache


def remove_final_game(
    cache: Mapping[str, Any],
    game: Mapping[str, Any],
    team_conf_div_map: Mapping[str, Mapping[str, Any]],
    *,
    regular_final_games: Sequence[Mapping[str, Any]],
    team_ids: Iterable[str] | None = None,
) -> Dict[str, Any]:
    """Rebuild cache excluding a target game.

    Note: remove is implemented via rebuild to keep recent10/streak correctness.
    """
    target_gid = str(game.get("game_id") or "").strip()
    remaining = [g for g in regular_final_games if str(g.get("game_id") or "").strip() != target_gid]
    if team_ids is None:
        src = cache.get("records_by_team") if isinstance(cache, Mapping) else {}
        team_ids = list((src or {}).keys())
    season_id = None
    built_from = cache.get("built_from") if isinstance(cache, Mapping) else None
    if isinstance(built_from, Mapping):
        season_id = built_from.get("season_id")
    return rebuild_cache_from_games(team_ids, remaining, team_conf_div_map, season_id=None if season_id is None else str(season_id))


def _format_gb(gb: float) -> str:
    if abs(float(gb)) < 1e-9:
        return "-"
    rounded = round(float(gb), 1)
    if abs(rounded - int(rounded)) < 1e-9:
        return str(int(rounded))
    return f"{rounded:.1f}"


def compute_standings_rows(
    cache: Mapping[str, Any],
    team_conf_div_map: Mapping[str, Mapping[str, Any]],
    *,
    conference: str,
) -> list[Dict[str, Any]]:
    conf_target = str(conference or "").strip().lower()
    if conf_target not in {"east", "west"}:
        return []

    rows: list[Dict[str, Any]] = []
    records_by_team = cache.get("records_by_team") if isinstance(cache, Mapping) else {}
    records_by_team = records_by_team if isinstance(records_by_team, Mapping) else {}

    for tid, rec_raw in records_by_team.items():
        tid_u = str(tid).upper()
        rec = rec_raw if isinstance(rec_raw, Mapping) else {}
        info = team_conf_div_map.get(tid_u) or {}
        conf = str(info.get("conference") or "").strip().lower()
        if conf != conf_target:
            continue

        wins = _to_int(rec.get("wins"))
        losses = _to_int(rec.get("losses"))
        gp = wins + losses
        win_pct = (wins / gp) if gp > 0 else 0.0
        pf = _to_int(rec.get("pf"))
        pa = _to_int(rec.get("pa"))
        ppg = (pf / gp) if gp > 0 else 0.0
        opp_ppg = (pa / gp) if gp > 0 else 0.0

        recent = rec.get("recent10")
        recent = [int(v) for v in recent] if isinstance(recent, list) else []
        last10_w = sum(recent)
        last10_l = len(recent) - last10_w

        streak_type = str(rec.get("streak_type") or _STREAK_NONE)
        streak_len = _to_int(rec.get("streak_len"))
        strk = "-" if streak_type not in {"W", "L"} or streak_len <= 0 else f"{streak_type}{streak_len}"

        rows.append(
            {
                "team_id": tid_u,
                "conference": str(info.get("conference") or ""),
                "division": info.get("division"),
                "wins": wins,
                "losses": losses,
                "win_pct": win_pct,
                "pct": f"{win_pct:.3f}"[1:],
                "games_played": gp,
                "gb": 0.0,
                "gb_display": "-",
                "home": f"{_to_int(rec.get('home_wins'))}-{_to_int(rec.get('home_losses'))}",
                "away": f"{_to_int(rec.get('away_wins'))}-{_to_int(rec.get('away_losses'))}",
                "div": f"{_to_int(rec.get('div_wins'))}-{_to_int(rec.get('div_losses'))}",
                "conf": f"{_to_int(rec.get('conf_wins'))}-{_to_int(rec.get('conf_losses'))}",
                "ppg": round(ppg, 1),
                "opp_ppg": round(opp_ppg, 1),
                "diff": round(ppg - opp_ppg, 1),
                "strk": strk,
                "l10": f"{last10_w}-{last10_l}",
                "point_diff": pf - pa,
            }
        )

    rows.sort(key=lambda r: (r.get("win_pct", 0), r.get("point_diff", 0)), reverse=True)
    if not rows:
        return rows

    leader_w = _to_int(rows[0].get("wins"))
    leader_l = _to_int(rows[0].get("losses"))
    for idx, r in enumerate(rows, start=1):
        gb = ((leader_w - _to_int(r.get("wins"))) + (_to_int(r.get("losses")) - leader_l)) / 2
        r["gb"] = gb
        r["gb_display"] = _format_gb(gb)
        r["rank"] = idx
    return rows


def ensure_cache_consistency(
    cache: Mapping[str, Any],
    regular_final_games: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    expected_ids = {
        str(g.get("game_id") or "").strip()
        for g in regular_final_games
        if _is_regular_final(g) and str(g.get("game_id") or "").strip()
    }

    applied_game_ids = cache.get("applied_game_ids") if isinstance(cache, Mapping) else {}
    applied_game_ids = applied_game_ids if isinstance(applied_game_ids, Mapping) else {}
    actual_ids = {str(gid).strip() for gid, applied in applied_game_ids.items() if bool(applied) and str(gid).strip()}

    built_from = cache.get("built_from") if isinstance(cache, Mapping) else {}
    built_from = built_from if isinstance(built_from, Mapping) else {}
    built_count = _to_int(built_from.get("regular_final_count"), 0)

    missing = sorted(expected_ids - actual_ids)
    extra = sorted(actual_ids - expected_ids)
    expected_count = len(expected_ids)
    actual_count = len(actual_ids)

    return {
        "is_consistent": (not missing) and (not extra) and built_count == expected_count,
        "expected_count": expected_count,
        "actual_count": actual_count,
        "built_from_count": built_count,
        "missing_game_ids": missing,
        "extra_game_ids": extra,
    }


__all__ = [
    "create_empty_standings_cache",
    "apply_final_game",
    "remove_final_game",
    "rebuild_cache_from_games",
    "compute_standings_rows",
    "ensure_cache_consistency",
]
