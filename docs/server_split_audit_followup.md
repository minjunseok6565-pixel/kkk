# server.py 분할 검증 후속 점검 (inline 지적 검토)

## 결론 요약
주어진 지적은 **사실**로 확인됨.

- 함수/클래스 본문 이동 자체는 이전 검증처럼 일치하더라도,
- 라우트 모듈별 import 분리가 미완료되어
- `server.py` 전역 import에 의존하던 심볼이 각 모듈에서 미정의(`F821`) 상태로 남아 있음.

따라서 `server.py`를 제거하고 `app.main` + 패키지 라우트만으로 운용할 경우,
해당 코드 경로 진입 시 런타임 `NameError`가 발생할 가능성이 높음.

## 검증 방법
- 정적 분석 도구 `ruff`로 대상 라우트 파일의 undefined name(`F821`) 점검:
  - `app/api/routes/core.py`
  - `app/api/routes/contracts.py`
  - `app/api/routes/college.py`
  - `app/api/routes/trades.py`

## 지적 항목별 사실 확인

### 1) `app/api/routes/core.py`
지적된 항목이 실제로 미정의로 검출됨:
- `normalize_player_id`
- `LeagueRepo`
- `json`
- `normalize_team_id`
- `ALL_TEAM_IDS`

### 2) `app/api/routes/contracts.py`
지적된 항목이 실제로 미정의로 검출됨:
- `LeagueRepo`
- `LeagueService`
- `game_time`

### 3) `app/api/routes/college.py`
지적된 항목이 실제로 미정의로 검출됨:
- `state`

### 4) `app/api/routes/trades.py`
지적된 항목이 실제로 미정의로 검출됨:
- `canonicalize_deal`
- `parse_deal`
- `validate_deal`
- `serialize_deal`
- `negotiation_store`

추가로 `HTTPException`도 미정의로 검출됨.

## 참고
- 이번 문서는 “문제가 사실인지” 검토 결과만 기록하며, 수정 작업(누락 import 보완)은 포함하지 않음.
