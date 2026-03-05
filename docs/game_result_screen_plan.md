# 경기 결과 화면(게임캐스트) 구현 가능성 검토 및 프론트 구현 계획

## 결론 (Feasibility)
- **현재 코드베이스로 구현 가능**합니다.
- 백엔드에 이미 경기 결과 전용 API가 존재하며, 요청한 핵심 데이터(헤더/스코어/리더/승률 그래프용 시계열/게임 플로우/맞대결 정보)를 반환합니다.
- 메인 화면의 `경기 진행` 버튼 처리 흐름과 전역 로딩 오버레이도 이미 있어, “경기 진행 중 로딩 → 경기 결과 화면 이동” UX를 붙이기 좋은 상태입니다.

## 현재 코드 기준 확인된 기반

### 1) 경기 진행 버튼 + 로딩 오버레이 기반
- 메인에서 `#next-game-play-btn` 클릭 시 `progressNextGameFromHome()`이 실행됩니다.
- 이 함수는 진행 중 `setLoading(true, "다음 경기 진행 중...")`를 호출하고, 종료 시 `setLoading(false)`를 호출합니다.
- 즉, 경기 시뮬레이션 중 로딩 창을 보여주는 기반은 이미 존재합니다.

### 2) 경기 결과 데이터 API 기반
- `GET /api/game/result/{game_id}?user_team_id=...` 엔드포인트가 이미 구현되어 있습니다.
- 응답에는 다음이 포함됩니다.
  - 상단 헤더: 팀/점수/승패기록/라인스코어
  - 탭 정보: 기본 `gamecast`, `boxscore/teamstats` 비활성
  - Game Leaders: points/rebounds/assists
  - Gamecast: `win_probability.series`, `game_flow.series`
  - Matchups: 시즌 상대전적 + 완료/예정 매치업 목록
- 이번 요구사항과 매우 높은 정합성을 가집니다.

### 3) 경기 결과 화면으로 이동하기 위한 최소 연결 포인트
- `POST /api/game/progress-next-user-game-day` 응답의 `game_day.user_game.game_id`를 받아,
- 이어서 `/api/game/result/{game_id}`를 조회하면 화면 렌더링 데이터 확보가 가능합니다.

## 구현 계획 (실제 작업 전 상세 설계)

## 0. 범위 고정
- 이번 스코프: **Gamecast 화면 1차 구현**
  - 상단 박스스코어/팀 정보
  - 중단 좌측 Game Leaders
  - 중단 중앙 Win Probability + Game Flow
  - 중단 우측 Matchups
  - 탭: `Gamecast`만 활성화 (Boxscore, Teamstats는 UI placeholder만)

## 1. 화면/라우팅 구조 추가
1. `static/NBA.html`
   - 신규 `section#game-result-screen.panel` 추가
   - 내부 블록
     - header(좌/우 로고, 팀명, 전적, 점수, 라인스코어)
     - 탭바(`Gamecast`, `Boxscore`, `Teamstats`)
     - 3열 본문(`leaders`, `probability-flow`, `matchups`)
2. `static/js/app/dom.js`
   - 신규 DOM 참조 등록
3. `static/js/app/router.js`
   - 새 스크린을 `activateScreen()` 대상에 포함
4. `static/js/app/events.js`
   - 필요 시 결과 화면의 back 동작(메인 복귀) 연결

## 2. 데이터 흐름 설계
1. `progressNextGameFromHome()` 성공 후:
   - 응답에서 `gameId = res.game_day.user_game.game_id` 추출
   - `fetchJson(`/api/game/result/${gameId}?user_team_id=${state.selectedTeamId}`)` 호출
2. 결과 payload를 `state.lastGameResult`에 저장
3. `showGameResultScreen(state.lastGameResult)` 호출
4. 오류 정책
   - 결과 API 실패 시: 기존대로 대화상자/토스트로 오류 표시 후 메인 유지
   - `GAME_NOT_FINAL` 등 409 케이스는 사용자 메시지 분기

## 3. 컴포넌트 렌더링 설계
1. Header renderer
   - 양 팀 로고/팀명/기록/점수/쿼터 라인
   - 승리 팀 강조 스타일
2. Tabs renderer
   - 기본 active = `Gamecast`
   - `Boxscore`, `Teamstats`는 disabled 배지(“준비중”)
3. Game Leaders renderer
   - points/rebounds/assists 3행
4. Win Probability 차트
   - 입력: `gamecast.win_probability.series`
   - y축 0~100, x축 elapsed time
5. Game Flow 차트
   - 입력: `gamecast.game_flow.series`
   - x축 game time, y축 누적 득점
6. Matchups renderer
   - `matchups.season_record` + `completed` + `upcoming`
   - 이미 붙은 경기 결과와 예정 경기 날짜/홈원정 표기

## 4. 차트 라이브러리 전략
- 우선순위
  1. 기존 프로젝트 의존성에 이미 차트 라이브러리가 있으면 재사용
  2. 없으면 경량 방식으로 SVG/Canvas 자체 렌더(외부 의존 최소화)
- 이유
  - 현재 코드베이스는 번들러가 가벼운 구조이므로, 초기 1차는 의존성 추가 없이 구현하는 것이 안전

## 5. UI/UX 세부 정책
- 로딩 오버레이 문구 변경
  - 경기 진행 버튼 클릭 시: `경기 시뮬레이션 중...`
  - 결과 조회까지 완료될 때까지 오버레이 유지
- 빈 데이터/fallback
  - `win_probability.series` 비어있으면 “데이터 없음” 카드
  - `game_flow.series` 비어있으면 같은 처리
  - `matchups.upcoming/completed` 비어도 섹션 유지

## 6. 단계별 구현 순서 (권장)
1. 결과 스크린 정적 마크업 + CSS 골격
2. 헤더/리더/매치업 텍스트 렌더러 연결
3. 메인 화면 `경기 진행` 후 결과 화면 전환 연결
4. 차트 2종 렌더링 연결
5. 예외 처리/빈 상태 처리
6. 마무리 QA

## 7. 수용 기준 (Acceptance Criteria)
- 경기 진행 버튼 클릭 시 로딩 오버레이가 뜬다.
- 유저 팀 경기 완료 후 자동으로 결과 화면으로 이동한다.
- 상단에 팀 로고/팀명/전적/점수/라인스코어가 노출된다.
- 탭은 Gamecast가 기본 활성이고, Boxscore/Teamstats는 비활성(준비중)이다.
- 좌측 Game Leaders(PTS/REB/AST) 표시.
- 중앙 Win Probability / Game Flow 그래프 노출.
- 우측 Matchups에 시즌 상대전적 + 완료/예정 정보 노출.

## 8. 리스크 및 선행 확인 항목
- `replay_events`가 부족한 경기의 그래프 품질 편차 가능
- 팀 로고 파일 누락 시 fallback 마크 필요
- overtime 경기에서 x축 라벨 표현 규칙(정규 48분 + OT) 정의 필요

## 9. 이번 요청 기준 비범위
- Boxscore 탭 상세(선수별 스탯 테이블)
- Teamstats 탭 상세
- Play-by-Play 전문 타임라인
- 외부 뉴스/기사 카드
