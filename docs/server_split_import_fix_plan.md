# server.py 분할 후 모듈 import 완결성 점검 및 수정 확정안

## 범위
- 점검 대상: `app/**` 전체
- 점검 규칙: `ruff` `F821`(Undefined name)
- 목적: 코드 수정 전, **미정의 심볼 전수 식별** + **파일별 import 보완안 확정**

## 전수 점검 결과 요약
- 총 `F821` 건수: **85건**
- 영향 파일: **7개**
  - `app/api/routes/college.py`
  - `app/api/routes/contracts.py`
  - `app/api/routes/core.py`
  - `app/api/routes/offseason.py`
  - `app/api/routes/scouting.py`
  - `app/api/routes/trades.py`
  - `app/api/routes/training.py`

## 파일별 확정 수정안 (코드 작업 전 확정)

### 1) `app/api/routes/college.py`
- 증상 심볼: `state`
- 확정 수정:
  - `import state` 추가

### 2) `app/api/routes/contracts.py`
- 증상 심볼: `LeagueRepo`, `LeagueService`, `game_time`
- 확정 수정:
  - `from league_repo import LeagueRepo` 추가
  - `from league_service import LeagueService` 추가
  - `import game_time` 추가

### 3) `app/api/routes/core.py`
- 증상 심볼: `ALL_TEAM_IDS`, `LeagueRepo`, `json`, `normalize_player_id`, `normalize_team_id`
- 확정 수정:
  - `import json` 추가
  - `from config import BASE_DIR, ALL_TEAM_IDS`로 변경(또는 `ALL_TEAM_IDS` 별도 import 추가)
  - `from league_repo import LeagueRepo` 추가
  - `from schema import normalize_player_id, normalize_team_id` 추가

### 4) `app/api/routes/offseason.py`
- 증상 심볼: `LeagueService`, `normalize_player_id`, `normalize_team_id`, `ui_cache_rebuild_all`
- 확정 수정:
  - `from league_service import LeagueService` 추가
  - `from schema import normalize_player_id, normalize_team_id` 추가
  - `from team_utils import ui_cache_refresh_players, ui_cache_rebuild_all`로 변경
    (현재 `ui_cache_refresh_players`만 import 중)

### 5) `app/api/routes/scouting.py`
- 증상 심볼: `sqlite3`, `uuid4`
- 확정 수정:
  - `import sqlite3` 추가
  - `from uuid import uuid4` 추가

### 6) `app/api/routes/trades.py`
- 증상 심볼:
  - `HTTPException`, `agreements`, `apply_deal_to_db`, `canonicalize_deal`,
    `negotiation_store`, `parse_deal`, `serialize_deal`, `timedelta`, `validate_deal`
- 확정 수정:
  - `from fastapi import APIRouter, HTTPException`로 변경
  - `from datetime import timedelta` 추가
  - `from trades import agreements, negotiation_store` 추가
  - `from trades.apply import apply_deal_to_db` 추가
  - `from trades.models import canonicalize_deal, parse_deal, serialize_deal` 추가
  - `from trades.validator import validate_deal` 추가

### 7) `app/api/routes/training.py`
- 증상 심볼: `logger`
- 확정 수정:
  - `import logging` 추가
  - `logger = logging.getLogger(__name__)` 선언 추가

## 추가 메모
- 본 문서는 “수정 실행 전 확정안”이며, 실제 코드 변경은 포함하지 않음.
- 실제 수정 단계에서는 위 import 보완 후 `ruff check app/api/routes --select F821` 재실행으로 0건 확인 필요.
