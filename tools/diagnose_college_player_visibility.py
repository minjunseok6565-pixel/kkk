#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter

from college.ui import list_college_players
from draft.expert_bigboard import DEFAULT_EXPERT_IDS, generate_expert_bigboard
import state


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnose player visibility mismatch across bigboard/leaderboard/scouting pools")
    ap.add_argument("--db-path", required=True)
    ap.add_argument("--draft-year", type=int, required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--leader-limit", type=int, default=100)
    ap.add_argument("--scout-limit", type=int, default=200)
    ap.add_argument("--expert-id", default=DEFAULT_EXPERT_IDS[0])
    args = ap.parse_args()

    state.set_db_path(str(args.db_path))

    # Leaderboard pool (frontend currently fetches top 100)
    leaders = list_college_players(sort="pts", order="desc", limit=args.leader_limit).get("players", [])
    leader_ids = {str(p.get("player_id")) for p in leaders}

    # Scouting assignment pool (frontend currently fetches top 200)
    scouts = list_college_players(sort="pts", order="desc", limit=args.scout_limit).get("players", [])
    scout_ids = {str(p.get("player_id")) for p in scouts}

    # Expert bigboard pool (default auto: declared -> watch fallback)
    board = generate_expert_bigboard(
        db_path=str(args.db_path),
        draft_year=int(args.draft_year),
        expert_id=str(args.expert_id),
        pool_mode="auto",
    )
    board_rows = board.get("board", [])
    board_ids = {str(r.get("temp_id")) for r in board_rows}

    print("=== Pool overview ===")
    print(f"expert={args.expert_id} pool_mode_used={board.get('pool_mode_used')} board_size={len(board_rows)}")
    print(f"leaders(limit={args.leader_limit})={len(leaders)} scouting(limit={args.scout_limit})={len(scouts)}")

    overlap = Counter()
    for pid in board_ids:
        if pid in leader_ids:
            overlap["board∩leaders"] += 1
        if pid in scout_ids:
            overlap["board∩scouting"] += 1
    print(dict(overlap))

    if args.name:
        q = args.name.strip().lower()
        board_hit = [r for r in board_rows if q in str(r.get("name", "")).lower()]
        leader_hit = [p for p in leaders if q in str(p.get("name", "")).lower()]
        scout_hit = [p for p in scouts if q in str(p.get("name", "")).lower()]

        print("\n=== Name lookup ===")
        print(f"query={args.name!r}")
        print(f"bigboard_hits={len(board_hit)}")
        print(f"leaderboard_hits={len(leader_hit)}")
        print(f"scouting_hits={len(scout_hit)}")

        if board_hit:
            row = board_hit[0]
            print(f"bigboard_first: rank={row.get('rank')} id={row.get('temp_id')} name={row.get('name')}")
        if leader_hit:
            row = leader_hit[0]
            print(f"leader_first: id={row.get('player_id')} name={row.get('name')} pts={(row.get('stats') or {}).get('pts')}")
        if scout_hit:
            row = scout_hit[0]
            print(f"scout_first: id={row.get('player_id')} name={row.get('name')} pts={(row.get('stats') or {}).get('pts')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
