from __future__ import annotations

import random
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, HTTPException

import state
from config import TEAM_TO_CONF_DIV
from postseason.director import (
    auto_advance_current_round,
    advance_my_team_one_game,
    build_postseason_field,
    initialize_postseason,
    play_my_team_play_in_game,
    reset_postseason_state,
)
from app.schemas.common import EmptyRequest, PostseasonSetupRequest

router = APIRouter()




@router.get("/api/postseason/field")
async def api_postseason_field():
    return build_postseason_field()


@router.get("/api/postseason/state")
async def api_postseason_state():
    return state.get_postseason_snapshot()


@router.post("/api/postseason/reset")
async def api_postseason_reset():
    return reset_postseason_state()


@router.post("/api/postseason/setup")
async def api_postseason_setup(req: PostseasonSetupRequest):
    try:
        return initialize_postseason(req.my_team_id, use_random_field=req.use_random_field)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/dev/postseason/fast-resolve")
async def api_dev_postseason_fast_resolve(req: PostseasonSetupRequest):
    """DEV 전용: 성적 기반 포스트시즌 최소 정보(16시드 + 챔피언)를 생성한다.

    목적:
    - /api/season/enter-offseason 진입 조건(champion) 충족
    - 드래프트 로터리/오더 계산에 필요한 playoff 16팀 정보(seeds)만 빠르게 제공

    주의:
    - 정규시즌 master_schedule(regular) 게임에 임의 final 스코어를 채워 넣고,
      그 결과 성적 기반으로 플레이인/플레이오프 16팀을 확정한다.
    """

    def _to_int(v: Any, default: int = 0) -> int:
        try:
            return int(v)
        except Exception:
            return int(default)

    def _sim_regular_scores(rng_obj: random.Random) -> Tuple[int, int]:
        # Lightweight DEV scoring model with a modest home-court edge.
        home = max(80, int(round(rng_obj.gauss(111.0, 12.0))))
        away = max(80, int(round(rng_obj.gauss(108.0, 12.0))))
        if home == away:
            if rng_obj.random() < 0.55:
                home += 1
            else:
                away += 1
        return int(home), int(away)

    def _conference_top10_from_games(games: List[Dict[str, Any]], conf_key: str) -> List[Dict[str, Any]]:
        conf_ids = {
            tid
            for tid, meta in TEAM_TO_CONF_DIV.items()
            if str((meta or {}).get("conference") or "").strip().lower() == str(conf_key).lower()
        }
        rows: List[Dict[str, Any]] = [
            {"team_id": tid, "wins": 0, "losses": 0, "pf": 0, "pa": 0}
            for tid in sorted(conf_ids)
        ]
        by_tid = {r["team_id"]: r for r in rows}

        for g in games:
            if not isinstance(g, dict):
                continue
            if str(g.get("phase") or "regular") != "regular":
                continue
            if g.get("status") != "final":
                continue

            h = str(g.get("home_team_id") or "").upper()
            a = str(g.get("away_team_id") or "").upper()
            hs = _to_int(g.get("home_score"), 0)
            as_ = _to_int(g.get("away_score"), 0)
            if h not in by_tid or a not in by_tid:
                continue

            by_tid[h]["pf"] += hs
            by_tid[h]["pa"] += as_
            by_tid[a]["pf"] += as_
            by_tid[a]["pa"] += hs

            if hs > as_:
                by_tid[h]["wins"] += 1
                by_tid[a]["losses"] += 1
            else:
                by_tid[a]["wins"] += 1
                by_tid[h]["losses"] += 1

        def _sort_key(row: Dict[str, Any]):
            wins = int(row.get("wins") or 0)
            losses = int(row.get("losses") or 0)
            gp = wins + losses
            win_pct = (wins / gp) if gp > 0 else 0.0
            diff = int(row.get("pf") or 0) - int(row.get("pa") or 0)
            return (win_pct, diff, row.get("team_id") or "")

        ordered = sorted(rows, key=_sort_key, reverse=True)
        top10 = ordered[:10]
        for idx, row in enumerate(top10, start=1):
            row["seed"] = idx
        return top10

    def _resolve_playin_to_playoff8(top10: List[Dict[str, Any]], rng_obj: random.Random, conf: str) -> Dict[int, Dict[str, Any]]:
        if len(top10) < 10:
            raise HTTPException(status_code=500, detail=f"Insufficient top10 for conference={conf}")

        out: Dict[int, Dict[str, Any]] = {
            i: {"team_id": str(top10[i - 1]["team_id"]), "seed": i, "conference": conf}
            for i in range(1, 7)
        }

        s7 = str(top10[6]["team_id"])
        s8 = str(top10[7]["team_id"])
        s9 = str(top10[8]["team_id"])
        s10 = str(top10[9]["team_id"])

        def _pick_winner(t1: str, t2: str, p_t1: float = 0.5) -> str:
            return t1 if rng_obj.random() < p_t1 else t2

        # play-in mini bracket
        g1_winner = _pick_winner(s7, s8, p_t1=0.57)
        g1_loser = s8 if g1_winner == s7 else s7
        g2_winner = _pick_winner(s9, s10, p_t1=0.58)
        g3_winner = _pick_winner(g1_loser, g2_winner, p_t1=0.55)

        out[7] = {"team_id": g1_winner, "seed": 7, "conference": conf}
        out[8] = {"team_id": g3_winner, "seed": 8, "conference": conf}
        return out
    my_team_id = str(req.my_team_id or "").upper()
    if not my_team_id:
        raise HTTPException(status_code=400, detail="Invalid my_team_id.")

    league_ctx = state.get_league_context_snapshot() or {}
    try:
        season_year = int(league_ctx.get("season_year") or 0)
    except Exception:
        season_year = 0
    if season_year <= 0:
        raise HTTPException(status_code=500, detail="Invalid season_year in state.")

    try:
        state.ensure_schedule_for_active_season(force=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to ensure schedule: {e}")

    rng_seed = int(season_year) * 1009 + 17
    rng = random.Random(rng_seed)

    def _mutate_schedule_and_build_seeds(gs: dict) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]], int]:
        league = gs.get("league") if isinstance(gs, dict) else {}
        league = league if isinstance(league, dict) else {}
        ms = league.get("master_schedule") if isinstance(league.get("master_schedule"), dict) else {}
        games = ms.get("games") if isinstance(ms.get("games"), list) else []
        by_id = ms.get("by_id") if isinstance(ms.get("by_id"), dict) else {}

        if not games:
            raise ValueError("master_schedule.games is empty")

        regular_count = 0
        for g in games:
            if not isinstance(g, dict):
                continue
            if str(g.get("phase") or "regular") != "regular":
                continue
            h = str(g.get("home_team_id") or "").upper()
            a = str(g.get("away_team_id") or "").upper()
            if not h or not a or h == a:
                continue
            hs, as_ = _sim_regular_scores(rng)
            g["status"] = "final"
            g["home_score"] = int(hs)
            g["away_score"] = int(as_)
            gid = str(g.get("game_id") or "")
            if gid and gid in by_id and isinstance(by_id.get(gid), dict):
                by_id[gid]["status"] = "final"
                by_id[gid]["home_score"] = int(hs)
                by_id[gid]["away_score"] = int(as_)
            regular_count += 1

        top10_east = _conference_top10_from_games(games, "east")
        top10_west = _conference_top10_from_games(games, "west")
        east8 = _resolve_playin_to_playoff8(top10_east, rng, "east")
        west8 = _resolve_playin_to_playoff8(top10_west, rng, "west")
        return east8, west8, regular_count

    try:
        east_seeds, west_seeds, regular_game_count = state._mutate_state(
            "dev_postseason_fast_resolve_regularize",
            _mutate_schedule_and_build_seeds,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create regular-season finals: {e}")

    east_seeded = [str(east_seeds[i]["team_id"]) for i in range(1, 9)]
    west_seeded = [str(west_seeds[i]["team_id"]) for i in range(1, 9)]

    playoff_teams = east_seeded + west_seeded
    champion = rng.choice(playoff_teams)

    state.postseason_reset()
    state.postseason_set_my_team_id(my_team_id)
    state.postseason_set_playoffs(
        {
            "season_year": int(season_year),
            "current_round": "Finals",
            "seeds": {"east": east_seeds, "west": west_seeds},
            "bracket": {},
            "meta": {
                "mode": "DEV_FAST_RESOLVE",
                "regular_final_games_marked": int(regular_game_count),
                "resolver": "top10_playin_then_playoff16",
            },
        }
    )
    state.postseason_set_champion(champion)

    return {
        "ok": True,
        "mode": "DEV_FAST_RESOLVE",
        "season_year": int(season_year),
        "my_team_id": my_team_id,
        "champion": champion,
        "playoff_teams": playoff_teams,
        "regular_final_games_marked": int(regular_game_count),
        "postseason": state.get_postseason_snapshot(),
    }


@router.post("/api/postseason/play-in/my-team-game")
async def api_play_in_my_team_game(req: EmptyRequest):
    try:
        return play_my_team_play_in_game()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/postseason/playoffs/advance-my-team-game")
async def api_playoffs_advance_my_team_game(req: EmptyRequest):
    try:
        return advance_my_team_one_game()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/postseason/playoffs/auto-advance-round")
async def api_playoffs_auto_advance_round(req: EmptyRequest):
    try:
        return auto_advance_current_round()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


 # -------------------------------------------------------------------------
# 시즌 전환 (오프시즌 진입 / 정규시즌 시작)
