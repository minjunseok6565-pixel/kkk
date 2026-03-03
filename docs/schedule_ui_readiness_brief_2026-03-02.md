# 스케줄 화면 구현 준비도 점검 브리프 (2026-03-02)

## 결론(요약)
- **기본 밑바탕은 부분적으로 준비됨**:
  - 팀별 시즌 일정 조회(`date`, `home/away`, `score`, `W/L`)는 이미 API로 제공됨.
  - 경기 결과 ingest 시점에 게임별 상세 결과(`game_results`)와 선수 박스스코어 row는 상태에 저장됨.
- **요구 UI를 바로 구현하기엔 부족한 부분이 명확함**:
  - 메인 메뉴의 `스케줄` 탭은 아직 식별자/이벤트가 없어 화면 전환 불가.
  - 스케줄 API에 `TIME`, `is_home_away_label`, `opponent_team_id/name`, `누적 W-L(시점 기준)`, `Hi Points/Rebounds/Assists`가 없음.
  - 다음 경기 시간은 현재 매번 랜덤 생성되어 스케줄 화면과 일관된 연동이 불가능.

## 현재 구현 상태 체크

### 1) 프론트 탭/화면 전환
- 메인 메뉴에 `스케줄` 버튼은 있으나 id가 없고(단순 `.menu-card`) 별도 스크린 섹션도 없음.
- JS는 화면 활성화 대상이 `start/team/main/my-team/player-detail`로 고정되어 있어 스케줄 화면 자체가 미등록 상태.
- 실제 이벤트 바인딩은 `내 팀` 버튼만 연결되어 있음.

### 2) 일정/결과 데이터 소스
- `/api/team-schedule/{team_id}`는 이미 존재하며 팀 경기 목록을 날짜순으로 반환.
- 반환 필드는 `game_id/date/home_team_id/away_team_id/home_score/away_score/result_for_user_team` 중심으로 최소 구성.
- 즉, **지난 경기의 승패(W/L) + 점수 표시는 가능**하지만, UI 요구 컬럼 전체를 채우기엔 필드가 부족.

### 3) 경기 상세 기록(Hi Points/Rebounds/Assists 가능성)
- 게임 ingest 시 `container["game_results"][game_id] = game_result` 형태로 **게임별 상세 결과 원본이 저장**됨.
- 동일 로직에서 팀별 선수 rows(`teams[tid].players`)를 누적 통계에 반영.
- 따라서 서버 내부 상태만 보면 경기별 팀 선수의 PTS/REB/AST 최대치 계산은 가능.
- 다만 이를 UI 친화적으로 노출하는 전용 조회 API가 현재 없음.

### 4) TIME(경기 시간) 연동
- 현재 메인 화면의 다음 경기 시간은 `randomTipoffTime()`으로 렌더 시점 랜덤 생성.
- 이 방식은 새로고침/재진입마다 값이 바뀔 수 있어 스케줄 화면과 동일 시간 보장을 못함.
- 스케줄 엔트리 최소 계약에도 `tipoff_time` 같은 필드는 없음.

## 요구사항 기준 갭 분석

### 이미 가능한 항목
- 날짜 기반 일정 정렬
- 완료 경기 식별(home_score/away_score 존재 여부)
- 우리 팀 기준 W/L 판정
- RESULT에 점수 표기(우리 팀 점수 왼쪽 정렬은 프론트 포맷팅으로 해결 가능)

### 추가 구현이 필요한 항목
1. **스케줄 탭 클릭 시 화면 전환**
2. **완료 경기용 컬럼 데이터**
   - `W-L(해당 시점 누적 전적)`
   - `Hi Points / Hi Rebounds / Hi Assists`
3. **미완료 경기용 TIME 컬럼**
   - 스케줄 화면 생성값과 메인 "다음 경기" 카드 표기를 같은 소스로 통일

## 권장 API 설계(신규)

### A안: 단일 확장 조회 API (권장)
`GET /api/team-schedule/{team_id}` 확장 (기존 엔드포인트 유지)

응답 예시(개념):
```json
{
  "team_id": "BOS",
  "season_id": "2025-26",
  "current_date": "2025-11-12",
  "games": [
    {
      "game_id": "2025-26-RS-00123",
      "date": "2025-11-10",
      "date_mmdd": "11/10",
      "is_home": true,
      "opponent_team_id": "LAL",
      "opponent_label": "vs LAL",
      "status": "final",
      "is_completed": true,
      "result": {
        "wl": "W",
        "score_for": 114,
        "score_against": 100,
        "display": "W 114-100"
      },
      "record_after_game": "10-1",
      "leaders": {
        "points": {"player_id": "p123", "name": "A Player", "value": 35},
        "rebounds": {"player_id": "p777", "name": "B Player", "value": 13},
        "assists": {"player_id": "p123", "name": "A Player", "value": 6}
      },
      "tipoff_time": null
    },
    {
      "game_id": "2025-26-RS-00124",
      "date": "2025-11-14",
      "date_mmdd": "11/14",
      "is_home": false,
      "opponent_team_id": "DEN",
      "opponent_label": "@ DEN",
      "status": "scheduled",
      "is_completed": false,
      "result": null,
      "record_after_game": null,
      "leaders": null,
      "tipoff_time": "07:30 PM"
    }
  ]
}
```

핵심 포인트:
- **프론트 포맷팅 부담 최소화**: `date_mmdd`, `opponent_label`, `result.display` 제공.
- **미래 경기 시간의 단일 소스화**: `tipoff_time`을 API에서 안정적으로 반환.
- **메인 다음 경기 카드도 동일 API의 첫 예정 경기값 재사용**.

### B안: 기존 API 유지 + 보조 API 분리
- `/api/team-schedule/{team_id}` 최소 필드 유지
- 신규 `/api/team-schedule-extras/{team_id}`에서 `record_after_game`, `leaders`, `tipoff_time`만 반환
- 프론트에서 game_id 조인

> 장기 유지보수 관점에선 A안(단일 응답)이 화면 구현과 테스트가 단순함.

## TIME 생성/저장 방식 권장

1) **결정론적 생성(서버)**
- `game_id` 해시 기반으로 시간 슬롯(예: 7:00/7:30/8:00/8:30 PM) 선택
- 같은 game_id는 항상 같은 시간
- 별도 저장 없이도 일관성 보장

2) **영속 저장(선택)**
- 시즌 시작 시 `master_schedule.games[*].tipoff_time`을 한 번 생성해 박아두기
- 스냅샷 저장/로드와 함께 따라가므로 가장 직관적

현재 구조상 빠른 적용은 1) (결정론)이고, 리그 현실감(전국 중계 시간대 등) 확장 생각하면 2)가 유리.

## 구현 우선순위 제안
1. 스케줄 스크린 DOM/라우팅(탭 클릭 전환)
2. 서버 조회 API(완료/미완료 unified row)
3. 메인 다음 경기 시간 표시를 스케줄 API 값으로 교체(랜덤 제거)
4. UI 테이블 렌더(완료/예정 row 타입 분기)

## 최종 판단
- **"지금 당장 UI만 붙일 수 있는가?" → 부분적으로 가능하지만, 요구한 컬럼을 정확히 채우려면 조회 API 보강이 필요**.
- 특히 `Hi Pts/Reb/Ast`, `시점 전적 W-L`, `일관된 TIME`은 백엔드 응답 확장이 사실상 필수.
