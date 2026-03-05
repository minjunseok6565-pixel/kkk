# 게임 진행 오케스트레이션 API 상세 설계서 (2026-03-05)

## 1. 문서 목적
본 문서는 아래 두 API를 **다음 구현 작업에서 바로 개발 가능한 수준**으로 상세 설계한다.

- `POST /api/game/progress-next-user-game-day`
- `POST /api/game/auto-advance-to-next-user-game-day`

핵심 목적:
- 유저 팀 경기/타팀 경기 누락 없이 진행
- 중복 시뮬레이션 방지
- 프론트 버튼 UX(`경기 진행`, `경기 날짜까지 자동 진행`)를 단순 호출로 안정 연결

---

## 2. 용어 및 전제

- `current_date`: 리그 현재 날짜(SSOT)
- `next_user_game_date (D)`: `current_date` 이상에서 유저 팀이 포함된 다음 scheduled 게임 날짜
- `same_day_other_games`: 날짜 D에서 유저 팀을 제외한 scheduled 게임
- `final`: 이미 시뮬레이션 완료된 게임 상태

전제(기존 엔진 특성):
- `advance_league_until(target, user_team_id)`는 `current_date + 1`부터 순회한다.
- `user_team_id`를 넘기면 해당 팀 포함 경기는 스킵한다.
- 단일 경기 시뮬레이션은 스케줄 매칭(날짜/home/away) 기반이며 final 게임 재실행은 실패한다.

---

## 3. 요구사항 매핑

### 3.1 `경기 진행` 버튼

#### A) 당일 유저 경기 있음 (`current_date == D`)
한 번의 API 호출로:
1. 유저 팀 경기 진행
2. 같은 날짜 타팀 경기 진행

#### B) 당일 유저 경기 없음 (`current_date < D`)
한 번의 API 호출로:
1. `current_date+1 ~ D-1` 타팀 경기 자동 진행
2. 날짜 D에서 유저 팀 경기 진행
3. 날짜 D의 타팀 경기 진행

### 3.2 `경기 날짜까지 자동 진행` 버튼
한 번의 API 호출로:
1. `current_date+1 ~ D-1` 타팀 경기 자동 진행
2. `current_date = D`로 맞춤
3. 날짜 D 경기는 아직 진행하지 않음

이후 `경기 진행` 호출 시 날짜 D 전체(유저+타팀) 진행.

---

## 4. API #1: `POST /api/game/progress-next-user-game-day`

## 4.1 의도
- "다음 유저 경기일 전체 슬레이트를 완결"하는 원자 오케스트레이션 엔드포인트.
- 프론트 `경기 진행` 버튼은 이 API 1회 호출만 수행.

## 4.2 Request JSON
```json
{
  "user_team_id": "BOS",
  "mode": "auto_if_needed",
  "apiKey": "optional-for-scouting-monthly",
  "idempotency_key": "optional-uuid"
}
```

### 필드 정의
- `user_team_id` (required, string)
- `mode` (optional, enum)
  - `auto_if_needed` (default): `current_date < D`면 자동진행 후 D 당일 전체 진행
  - `strict_today_only`: `current_date != D`면 409 반환 (자동진행 금지)
- `apiKey` (optional): 내부 월별 스카우팅 체크포인트 등 후속 처리에 전달
- `idempotency_key` (optional): 중복 클릭/재시도 보호

## 4.3 Response JSON (성공)
```json
{
  "ok": true,
  "mode": "auto_advanced_then_played",
  "user_team_id": "BOS",
  "current_date_before": "2025-11-10",
  "target_user_game_date": "2025-11-13",
  "current_date_after": "2025-11-13",
  "auto_advance": {
    "from_exclusive": "2025-11-10",
    "to_inclusive": "2025-11-12",
    "simulated_count": 18,
    "simulated_game_ids": ["..."]
  },
  "game_day": {
    "date": "2025-11-13",
    "user_game": {
      "game_id": "2025-RS-01023",
      "home_team_id": "BOS",
      "away_team_id": "LAL",
      "status": "final"
    },
    "other_games_simulated_count": 6,
    "other_game_ids": ["..."]
  },
  "totals": {
    "simulated_count": 25
  }
}
```

### mode 값
- `played_today`: 현재 날짜에 유저 경기 존재, 자동진행 없이 당일 전체만 진행
- `auto_advanced_then_played`: 자동진행 후 당일 전체 진행

## 4.4 처리 알고리즘(의사 코드)

1. 입력 검증 및 팀 ID 정규화
2. (선택) `idempotency_key` lock 획득
3. `current_date` 조회
4. `D = find_next_user_game_date(user_team_id, from=current_date)`
   - 없으면 409 `NO_NEXT_USER_GAME`
5. 분기
   - `current_date == D`:
     - 당일 유저 경기 엔트리 1건 조회 (미final이어야 함)
     - 유저 경기 시뮬레이션
     - 같은 날짜 타팀 scheduled 경기 일괄 시뮬레이션
     - `current_date` 유지(D)
   - `current_date < D`:
     - mode가 `strict_today_only`면 409 `USER_GAME_NOT_TODAY`
     - `current_date+1 ~ D-1` 타팀 경기 일괄 시뮬레이션
     - `current_date = D-1`로 맞춘 뒤
     - 날짜 D 유저 경기 시뮬레이션
     - 날짜 D 타팀 경기 일괄 시뮬레이션
     - `current_date = D`
6. 결과 집계/반환
7. (선택) idem 결과 저장 후 lock 해제

> 구현 시 핵심: 날짜 D 타팀 경기 처리 시, 기존 `advance_league_until`의 `current_date+1` 규칙에 걸리지 않도록
> 별도 내부 헬퍼(특정 날짜 슬레이트 직접 순회)가 필요함.

## 4.5 에러 설계

- `400 INVALID_REQUEST` 필드 누락/형식 오류
- `404 TEAM_NOT_FOUND`
- `409 NO_NEXT_USER_GAME` (시즌 내 다음 경기 없음)
- `409 USER_GAME_NOT_TODAY` (`strict_today_only` 위반)
- `409 USER_GAME_ALREADY_FINAL` (당일 유저 경기 이미 종료)
- `423 REQUEST_IN_PROGRESS` (동일 idem key/동일 유저팀 진행 중)
- `500 INTERNAL_ERROR`

에러 바디 표준:
```json
{
  "detail": {
    "code": "USER_GAME_NOT_TODAY",
    "message": "No user game on current_date",
    "meta": {"current_date": "2025-11-10", "next_user_game_date": "2025-11-13"}
  }
}
```

---

## 5. API #2: `POST /api/game/auto-advance-to-next-user-game-day`

## 5.1 의도
- "다음 유저 경기일 직전까지 자동 진행 후 날짜 정렬" 전용.
- 프론트 `경기 날짜까지 자동 진행` 버튼은 이 API만 호출.

## 5.2 Request JSON
```json
{
  "user_team_id": "BOS",
  "apiKey": "optional-for-scouting-monthly",
  "idempotency_key": "optional-uuid"
}
```

## 5.3 Response JSON (성공)
```json
{
  "ok": true,
  "user_team_id": "BOS",
  "current_date_before": "2025-11-10",
  "next_user_game_date": "2025-11-13",
  "current_date_after": "2025-11-13",
  "auto_advance": {
    "from_exclusive": "2025-11-10",
    "to_inclusive": "2025-11-12",
    "simulated_count": 18,
    "simulated_game_ids": ["..."]
  },
  "game_day_status": {
    "date": "2025-11-13",
    "user_game_pending": true,
    "other_games_pending_count": 7
  }
}
```

## 5.4 처리 알고리즘
1. 입력 검증/정규화
2. (선택) idem lock
3. `current_date`, `D` 조회
4. if `current_date < D`:
   - `current_date+1 ~ D-1` 타팀 경기 자동 진행
5. `current_date = D`
6. 날짜 D에서 유저/타팀 pending 상태 계산 후 반환

### 에지 케이스
- `current_date == D`: 자동진행 0건, 단순 날짜 유지 응답
- `current_date > D`: 409 `PAST_NEXT_GAME_POINTER_BROKEN` (데이터 정합성 이상)

## 5.5 에러 설계
- `400 INVALID_REQUEST`
- `404 TEAM_NOT_FOUND`
- `409 NO_NEXT_USER_GAME`
- `423 REQUEST_IN_PROGRESS`
- `500 INTERNAL_ERROR`

---

## 6. 원자성/동시성/멱등성 설계

## 6.1 원자성
- 단일 요청 내에서 상태 반영 단위를 트랜잭션 경계로 관리.
- 최소 보장:
  - "일자 단위 완료"가 보장되어 중간 일자의 partial final 방지.

## 6.2 동시성
- 유저 팀 단위 뮤텍스(예: `game_progress:{user_team_id}`)
- 이미 진행 중이면 `423 REQUEST_IN_PROGRESS`

## 6.3 멱등성
- `idempotency_key`가 같으면 동일 결과 재반환.
- 만료 TTL(예: 10분) 후 자연 정리.

---

## 7. 내부 헬퍼(구현 권장)

다음 구현 패치에서 복잡도/누락 위험을 낮추기 위해 내부 함수를 분리한다.

1. `find_next_user_game_date(user_team_id, from_date)`
2. `list_scheduled_games_on_date(date)`
3. `simulate_non_user_games_in_range(start_exclusive, end_inclusive, user_team_id)`
4. `simulate_user_game_on_date(date, user_team_id)`
5. `simulate_other_games_on_date(date, user_team_id)`
6. `summarize_pending_on_date(date, user_team_id)`

---

## 8. 프론트 연동 계약

## 8.1 `경기 진행` 버튼
- 기본 호출: `/api/game/progress-next-user-game-day` with `mode=auto_if_needed`
- 경고 모달 노출 조건:
  - 서버 사전조회 없이도 가능하나, UX를 위해 호출 전 `next_game_date` 조회 API(선택)를 두면 좋음
- 서버 응답 `mode=auto_advanced_then_played`면
  - "경기 날짜까지 자동 진행 후 당일 경기를 완료했습니다" 메시지 표시

## 8.2 `경기 날짜까지 자동 진행` 버튼
- 클릭 시 경고:
  - "경기 날짜까지 리그 일정은 자동 진행됩니다"
- 확인 시 `/api/game/auto-advance-to-next-user-game-day` 호출
- 성공 후:
  - 현재 날짜 라벨을 `next_user_game_date`로 업데이트
  - CTA를 `경기 진행`으로 유도

---

## 9. 검증 시나리오(수용 기준)

1. **당일 유저 경기**
   - API #1 1회 호출 후 해당 날짜 전 경기 final
2. **유저 경기 3일 후**
   - API #1 호출 후 중간일 타팀 final + 유저 경기일 전체 final
3. **자동 진행 후 경기 진행**
   - API #2 후 `current_date = D`, D 경기는 pending
   - 이어 API #1 후 D 전 경기 final
4. **중복 클릭**
   - 동일 idem key 재호출 시 동일 응답
   - 다른 key 동시 호출은 한쪽 423
5. **시즌 마지막 경기 이후**
   - `NO_NEXT_USER_GAME` 반환

---

## 10. 롤아웃 제안

1. 백엔드 API 구현 (feature flag optional)
2. 내부 로그 계측
   - `simulated_count`, `mode`, `elapsed_ms`, `date_span`
3. 프론트 버튼 교체
4. QA: 시나리오 1~5 전수

---

## 11. 비목표(이번 설계 범위 밖)

- 포스트시즌(`play-in`, `playoffs`) 오케스트레이션 통합
- 경기 애니메이션/플레이바이플레이 UX
- 세이브 슬롯 충돌 해결 정책의 상세 제품 설계

