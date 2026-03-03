from __future__ import annotations

from typing import Any, Dict

from ..ids import make_article_id
from ..models import NewsArticle, NewsEvent


def _team(tid: Any) -> str:
    return str(tid)


def _player(name: Any) -> str:
    s = str(name) if name is not None else ""
    s = s.strip()
    return s if s else "알 수 없음"


def _score_str(facts: Dict[str, Any]) -> str:
    hs = facts.get("home_score")
    as_ = facts.get("away_score")
    try:
        return f"{int(hs)}-{int(as_)}"
    except Exception:
        return "-"


def render_article(event: NewsEvent) -> NewsArticle:
    """Render a UI-ready article from a structured event.

    This renderer is deterministic and fact-grounded. It should never fail
    hard; if facts are missing, it degrades gracefully.
    """
    etype = str(event.get("type") or "")
    facts: Dict[str, Any] = event.get("facts") or {}

    date = str(event.get("date") or "")
    teams = [str(t) for t in (event.get("related_team_ids") or []) if t]
    players = [str(p) for p in (event.get("related_player_names") or []) if p]

    title = ""
    summary = ""
    tags = list(event.get("tags") or [])

    if etype == "UPSET":
        winner = _team(facts.get("winner"))
        loser = _team(facts.get("loser"))
        score = _score_str(facts)
        gap = facts.get("pregame_win_pct_gap")
        try:
            gap_i = int(round(float(gap) * 100))
        except Exception:
            gap_i = None
        title = f"{winner}, {loser} 제압… 이변" if winner and loser else "이변 발생"
        if gap_i is not None:
            summary = (
                f"{date} 경기에서 {winner}가 {loser}를 {score}로 꺾었다. "
                f"경기 전 승률 격차가 약 {gap_i}%p였지만 결과는 뒤집혔다."
            )
        else:
            summary = f"{date} 경기에서 {winner}가 {loser}를 {score}로 꺾으며 이변을 연출했다."
        tags += ["weekly", "upset"]

    elif etype == "CLUTCH_OT":
        winner = _team(facts.get("winner"))
        loser = _team(facts.get("loser"))
        score = _score_str(facts)
        ot = bool(facts.get("is_overtime"))
        margin = facts.get("margin")
        try:
            m = abs(int(margin))
        except Exception:
            m = None
        if ot:
            title = f"연장 혈투 끝 {winner} 승리"
            summary = f"{date} 경기에서 {winner}가 {loser}를 연장 끝에 {score}로 제압했다."
            tags += ["weekly", "overtime", "clutch"]
        else:
            title = f"1~2포제션 접전… {winner} 승리"
            if m is not None:
                summary = f"{date} 경기에서 {winner}가 {loser}를 {score}로 꺾었다. 최종 점수 차는 {m}점." 
            else:
                summary = f"{date} 경기에서 {winner}가 {loser}를 {score}로 꺾었다."
            tags += ["weekly", "clutch"]

    elif etype == "BLOWOUT":
        winner = _team(facts.get("winner"))
        loser = _team(facts.get("loser"))
        score = _score_str(facts)
        margin = facts.get("margin")
        try:
            m = abs(int(margin))
        except Exception:
            m = None
        title = f"{winner}, {loser}에 대승" if winner and loser else "대승"
        if m is not None:
            summary = f"{date} 경기에서 {winner}가 {loser}를 {score}로 꺾었다. {m}점 차 완승." 
        else:
            summary = f"{date} 경기에서 {winner}가 {loser}를 {score}로 꺾으며 크게 이겼다."
        tags += ["weekly", "blowout"]

    elif etype == "STREAK_TEAM":
        team = _team(facts.get("team_id"))
        st = facts.get("streak_len")
        try:
            st_i = int(st)
        except Exception:
            st_i = None
        title = f"{team}, 상승세 지속" if team else "연승/연패 흐름"
        if st_i is not None and st_i > 0:
            summary = f"{team}가 {st_i}연승을 달리며 분위기를 끌어올리고 있다."
        elif st_i is not None and st_i < 0:
            summary = f"{team}가 {-st_i}연패에 빠지며 반등이 필요하다."
        else:
            summary = f"{team}의 최근 흐름이 눈에 띈다."
        tags += ["weekly", "streak"]

    elif etype.startswith("PLAYER_"):
        pname = _player(facts.get("player_name") or (players[0] if players else None))
        team = _team(facts.get("team_id"))
        pts = facts.get("pts")
        reb = facts.get("reb")
        ast = facts.get("ast")
        stl = facts.get("stl")
        blk = facts.get("blk")
        threes = facts.get("3pm")
        line_parts = []
        def _add(label: str, val: Any) -> None:
            try:
                iv = int(val)
            except Exception:
                return
            line_parts.append(f"{iv}{label}")

        _add("득점", pts)
        _add("리바", reb)
        _add("어시", ast)
        _add("스틸", stl)
        _add("블록", blk)
        _add("3점", threes)
        line = " · ".join(line_parts)

        if etype == "PLAYER_40PTS":
            title = f"{pname}, 폭발" if pname else "폭발적인 득점"
            summary = f"{date} {team}의 {pname}가 {int(pts)}득점으로 경기를 지배했다." if pts else f"{date} {team}의 {pname}가 맹활약했다."
            tags += ["weekly", "player", "scoring"]
        elif etype == "PLAYER_TRIPLE_DOUBLE":
            title = f"{pname}, 트리플더블" if pname else "트리플더블"
            summary = f"{date} {team}의 {pname}가 트리플더블을 기록했다. ({line})" if line else f"{date} {team}의 {pname}가 트리플더블을 작성했다."
            tags += ["weekly", "player", "triple_double"]
        else:
            title = f"{pname}, 맹활약" if pname else "선수 맹활약"
            summary = f"{date} {team}의 {pname}가 돋보였다. ({line})" if line else f"{date} {team}의 {pname}가 존재감을 드러냈다."
            tags += ["weekly", "player"]

    elif etype == "TRANSACTION":
        ttype = str(facts.get("tx_type") or "transaction")
        summary_text = str(facts.get("summary") or "")
        title = str(facts.get("title") or "로스터 변동")
        if not title:
            title = "로스터 변동"
        summary = summary_text.strip() or f"{date} {ttype} 관련 트랜잭션이 기록됐다."
        tags += ["weekly", "transaction"]

    elif etype.startswith("PLAYOFF_") or etype == "CHAMPION":
        round_label = str(facts.get("round_label") or "플레이오프")
        winner = _team(facts.get("winner"))
        loser = _team(facts.get("loser"))
        score = _score_str(facts)
        series_score = str(facts.get("series_score") or "")
        game_number = facts.get("game_number")
        try:
            gni = int(game_number)
        except Exception:
            gni = None

        if etype == "CHAMPION":
            champ = _team(facts.get("champion") or winner)
            title = f"{champ}, 우승!"
            summary = f"{champ}가 시즌 최종 우승을 확정지었다."
            tags += ["playoffs", "champion"]

        elif etype == "PLAYOFF_ELIMINATION":
            title = f"{round_label}: {winner} 진출"
            summary = f"{winner}가 {loser}를 {score}로 꺾으며 시리즈를 {series_score}로 마무리했다."
            if gni:
                summary = f"{round_label} G{gni}에서 {winner}가 {loser}를 {score}로 잡고 시리즈를 {series_score}로 끝냈다."
            tags += ["playoffs", "elimination"]

        elif etype == "PLAYOFF_MATCH_POINT":
            title = f"{round_label}: {winner}, 매치포인트"
            summary = f"{winner}가 승리하며 시리즈 스코어를 {series_score}로 만들었다."
            tags += ["playoffs", "match_point"]

        elif etype == "PLAYOFF_SERIES_SWING":
            title = f"{round_label}: 흐름이 바뀌었다"
            summary = f"{winner}가 {score} 승리로 시리즈 판세가 요동쳤다. (현재 {series_score})"
            tags += ["playoffs", "swing"]

        else:
            title = f"{round_label} G{gni}: {winner} 승리" if gni else f"{round_label}: {winner} 승리"
            summary = f"{winner}가 {loser}를 {score}로 꺾었다. (시리즈 {series_score})"
            tags += ["playoffs", "game_result"]

        # Optional top performer callout
        perf = facts.get("top_performers")
        if isinstance(perf, list) and perf:
            # Expect entries like "Name 34PTS".
            perf_line = ", ".join(str(x) for x in perf[:2])
            summary += f"  하이라이트: {perf_line}."

    else:
        title = "리그 소식"
        summary = f"{date} 리그에서 주목할 만한 변화가 있었다."
        tags += ["news"]

    article: NewsArticle = {
        "article_id": make_article_id(str(event.get("event_id"))),
        "event_id": str(event.get("event_id")),
        "date": date,
        "title": title,
        "summary": summary,
        "tags": tags,
        "related_team_ids": teams,
        "related_player_names": players,
    }
    return article
