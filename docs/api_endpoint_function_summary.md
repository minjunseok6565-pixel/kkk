# API Endpoint Function Summary

이 문서는 현재 FastAPI 라우터 기준으로 엔드포인트를 요약한 문서다. 단일 `server.py` 기반 설명은 더 이상 유효하지 않으며, 실제 라우트는 `app/api/routes/*.py`에 분산되어 있다.

## 라우팅 구조 요약

- 앱 엔트리: `app/main.py`
- 라우터 집합: `app/api/router.py`
- 실제 엔드포인트: `app/api/routes/*.py`

## 도메인별 엔드포인트 맵

- Core 조회: `app/api/routes/core.py`
  - `/`, `/api/stats/*`, `/api/standings`, `/api/teams`, `/api/team-detail/{team_id}`, `/api/player-detail/{player_id}`
  - `/api/roster-summary/{team_id}`, `/api/two-way/summary/{team_id}`, `/api/team-schedule/{team_id}`, `/api/state/summary`
- 경기 진행: `app/api/routes/sim.py`
  - `/api/simulate-game`, `/api/advance-league`
- 트레이닝/프랙티스: `app/api/routes/training.py`
  - `/api/training/*`, `/api/practice/*`
- 대학/드래프트워치: `app/api/routes/college.py`
  - `/api/college/*`
- 스카우팅: `app/api/routes/scouting.py`
  - `/api/scouting/*`
- 포스트시즌: `app/api/routes/postseason.py`
  - `/api/postseason/*`
- 오프시즌 + Agency + Draft 메인 플로우: `app/api/routes/offseason.py`
  - `/api/season/enter-offseason`, `/api/offseason/*`, `/api/agency/*`, `/api/season/start-regular-season`
- 계약: `app/api/routes/contracts.py`
  - `/api/contracts/*`
- 트레이드: `app/api/routes/trades.py`
  - `/api/trade/block/*`, `/api/trade/submit*`, `/api/trade/negotiation/*`, `/api/trade/evaluate`
- 뉴스/LLM: `app/api/routes/news.py`
  - `/api/news/*`, `/api/season-report`, `/api/validate-key`, `/api/chat-main`, `/api/main-llm`
- 세이브/로드: `app/api/routes/game_saves.py`
  - `/api/game/*`, `/api/debug/schedule-summary`

## 현재 집계

- GET 38개
- POST 62개
- 총 100개

상세 목록은 `docs/api_endpoints_inventory.md`를 참조.
