# 경기 결과 화면용 신규 API 설계서 (데이터 소스 제약 반영) (2026-03-05)

## 문서 목적
- 본 문서는 **실제 코드가 현재 내려주는 데이터만 사용**해서, 경기 종료 후 결과 화면을 안정적으로 띄우기 위한 신규 API 계약을 정의한다.
- 이번 단계는 설계만 수행하며, 서버/프론트 코드 패치는 포함하지 않는다.

---

## 1) 설계 원칙

1. **SSOT 준수**
   - 경기 식별/기본 메타: `league.master_schedule` + `game_id`
   - 경기 결과 상세: `workflow_state.game_results[game_id]` (GameResultV2)
   - 시즌 일정/상대전적: `/api/team-schedule/{team_id}` 계산 규칙 재사용

2. **있는 데이터만 노출**
   - `final`, `teams[*].totals`, `teams[*].players`, `replay_events`(있을 때만) 사용
   - 쿼터별 스코어처럼 현재 원천이 불명확한 항목은 `null` 또는 섹션 미제공으로 명시

3. **프론트 단순화**
   - 결과 화면에 필요한 값을 서버 ViewModel로 조립해서 전달
   - 프론트는 렌더링 중심, 복잡한 파생 계산 최소화

---

## 2) 신규 API 제안

## API A. 경기 결과 화면 ViewModel 조회

### Endpoint
`GET /api/game/result/{game_id}`

### Query
- `user_team_id` (required): 사용자 팀 ID (예: `LAL`)

### 역할
- 결과 화면 상단/중단(Game Leaders, Gamecast, Matchups)에 필요한 데이터를 한 번에 반환.
- `progress-next-user-game-day` 성공 직후 받은 `game_id`로 즉시 조회 가능.

### 200 Response (제안)
```json
{
  "game_id": "2026REG000123",
  "status": "final",
  "as_of_date": "2026-11-03",
  "header": {
    "date": "2026-11-03",
    "home_team_id": "LAL",
    "away_team_id": "BOS",
    "home_team_name": "Los Angeles Lakers",
    "away_team_name": "Boston Celtics",
    "home_score": 112,
    "away_score": 108,
    "winner_team_id": "LAL",
    "user_team_id": "LAL",
    "user_team_record_after_game": "5-2",
    "opponent_record_after_game": "4-3",
    "boxscore_lines": {
      "quarters": null,
      "note": "Quarter split unavailable in current GameResultV2 source"
    }
  },
  "tabs": {
    "default": "gamecast",
    "enabled": ["gamecast"],
    "disabled": ["boxscore", "teamstats"]
  },
  "leaders": {
    "points": {
      "home": {"player_id": "2544", "name": "LeBron James", "value": 31},
      "away": {"player_id": "1628369", "name": "Jayson Tatum", "value": 29}
    },
    "rebounds": {
      "home": {"player_id": "203076", "name": "Anthony Davis", "value": 14},
      "away": {"player_id": "1627759", "name": "Jaylen Brown", "value": 9}
    },
    "assists": {
      "home": {"player_id": "2544", "name": "LeBron James", "value": 10},
      "away": {"player_id": "1628369", "name": "Jayson Tatum", "value": 7}
    }
  },
  "gamecast": {
    "win_probability": {
      "model": "heuristic_v1",
      "series": [
        {"t": 0, "home": 0.54, "away": 0.46},
        {"t": 120, "home": 0.58, "away": 0.42}
      ],
      "inputs": {
        "score_diff": true,
        "elapsed_seconds": true,
        "strength_gap_source": "pre_game_win_pct"
      },
      "confidence": "experimental"
    },
    "game_flow": {
      "series": [
        {"t": 0, "home_score": 0, "away_score": 0},
        {"t": 42, "home_score": 2, "away_score": 0}
      ],
      "source": "replay_events"
    },
    "availability": {
      "replay_events_present": true,
      "fallback_used": false
    }
  },
  "matchups": {
    "season_record": {"user_team_wins": 1, "user_team_losses": 0},
    "completed": [
      {
        "game_id": "2026REG000123",
        "date": "2026-11-03",
        "user_team_home": true,
        "user_team_score": 112,
        "opponent_score": 108,
        "result": "W"
      }
    ],
    "upcoming": [
      {
        "game_id": "2026REG000544",
        "date": "2027-01-14",
        "user_team_home": false,
        "tipoff_time": "08:30 PM"
      }
    ]
  }
}
```

### Error
- `404 GAME_NOT_FOUND`: `game_id`가 스케줄/결과에 없음
- `409 GAME_NOT_FINAL`: 아직 final 아님
- `400 INVALID_USER_TEAM`: `user_team_id` 유효하지 않음
- `400 USER_TEAM_NOT_IN_GAME`: 해당 `game_id`에 user team 미포함

---

## API B. 마지막 유저 경기 결과 빠른 조회 (선택)

### Endpoint
`GET /api/game/result-latest`

### Query
- `user_team_id` (required)

### 역할
- 프론트가 `progress-next-user-game-day` 응답을 잃어버린 경우(새로고침 등) 대비.
- 현재 시즌에서 user team의 최근 final game을 찾아 `game_id`와 간단 메타를 반환.

### 200 Response (제안)
```json
{
  "user_team_id": "LAL",
  "game_id": "2026REG000123",
  "date": "2026-11-03",
  "home_team_id": "LAL",
  "away_team_id": "BOS",
  "home_score": 112,
  "away_score": 108,
  "status": "final"
}
```

### Error
- `404 NO_COMPLETED_USER_GAME`
- `400 INVALID_USER_TEAM`

---

## 3) 필드별 데이터 출처(코드 기준)

- 경기 식별/기본 메타(`game_id`, `date`, `home_team_id`, `away_team_id`)
  - 출처: `league.master_schedule.games/by_id`
- 최종 점수(`home_score`, `away_score`, winner)
  - 출처: `workflow_state.game_results[game_id].final` + 팀 매핑
- 팀/선수 박스 데이터(`totals`, `players`)
  - 출처: `workflow_state.game_results[game_id].teams[team_id]`
- Game Leaders(PTS/REB/AST)
  - 출처: 동일 `players[]`에서 max 계산 (이미 `_pick_leader` 패턴 사용 중)
- Matchups/상대전적/예정 경기
  - 출처: `/api/team-schedule/{user_team_id}` 반환 games 필터링
- tipoff_time
  - 출처: 스케줄 API 규칙의 deterministic tipoff 재사용
- replay 기반 타임라인
  - 출처: `workflow_state.game_results[game_id].replay_events` (있을 때)

---

## 4) 계산 규칙(서버 ViewModel 내부)

## 4.1 Leaders
- 각 팀 `players[]`에서
  - points: `PTS`
  - rebounds: `REB` 우선, 없으면 `TRB`
  - assists: `AST`
- 값 동률 시 tie-break (명시 필요)
  1) 출전시간 높은 선수
  2) 그래도 동률이면 roster order(입력 순서)

## 4.2 Game Flow
- 전제: `replay_events` 각 이벤트에서 시간/점수 변화 추출 가능해야 함.
- 출력: `t`(경기 시작 기준 누적 초), `home_score`, `away_score` 누적 시계열.
- 이벤트가 없으면:
  - `availability.replay_events_present=false`
  - `series=[]`
  - 프론트는 "데이터 없음" 메시지 렌더.

## 4.3 Win Probability (heuristic_v1)
- 입력(모두 현재 시스템에서 확보 가능한 값만):
  - 시간 경과(`t`)
  - 점수차(`home_score-away_score`)
  - 전력차 proxy: 경기 시작 시점 양팀 승률(`team_schedule` 누적 기록 기반)
- 출력: 각 시점 home/away 확률 합 1.0
- 라벨: `confidence="experimental"` 고정 표기

## 4.4 Matchups
- `team_schedule(user_team_id)`에서 상대팀 동일 경기만 필터.
- `is_completed` 기준으로 `completed/upcoming` 분리.
- completed에서 W/L 누적으로 `season_record` 계산.

---

## 5) 현재 플로우와의 연결

1. 프론트 `경기 진행` 버튼
   - `POST /api/game/progress-next-user-game-day`
2. 응답의 `game_day.user_game.game_id` 확보
3. 즉시 `GET /api/game/result/{game_id}?user_team_id=...` 호출
4. 로딩 오버레이 종료 후 결과 화면 렌더

> 즉, 기존 진행 API는 유지하고 결과 화면 전용 조회 API를 추가하는 방식이 가장 안전하다.

---

## 6) 비범위(이번 설계에서 제외)

- 박스스코어 탭 상세(선수 풀 테이블 정교 UI)
- Teamstats 탭 상세
- 쿼터별 박스스코어 보장(원천 데이터 확장 필요)
- Play-by-play 텍스트 완성형 리캡

---

## 7) 구현 전 검증 체크리스트

- [ ] `game_results[game_id]` 조회 실패 시 에러코드 일관성
- [ ] `user_team_id`가 해당 게임 참여팀인지 검증
- [ ] `replay_events` 부재 시도 정상 응답(그래프만 비활성)
- [ ] `team_schedule` 기반 matchups 계산에서 홈/원정 방향 정확성
- [ ] 승률 계산 모델의 입력값 누락 시 안전 기본값 처리

