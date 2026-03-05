# 경기 결과 화면 프론트 구현 가능성 검토 및 실행 계획 (2026-03-05)

## 1) 결론 (TL;DR)
- **현재 코드베이스 기준으로 구현 가능**합니다.
- 다만 현재 `경기 진행` 버튼 플로우는 완료 후 `alert`만 띄우고 끝나므로, 결과 화면으로 자연스럽게 전환하려면
  1) 프론트 라우팅에 `game-result-screen` 추가,
  2) `progress-next-user-game-day` 응답에서 유저 경기 식별자(`game_id`)를 사용해 상세 데이터를 조회하는 API,
  3) 결과 화면 전용 ViewModel 조립 레이어가 필요합니다.

---

## 2) 현재 상태 기준 구현 가능성 점검

### A. 경기 진행 중 로딩 → 완료 후 화면 전환
- 이미 `setLoading(true, "다음 경기 진행 중...")` 오버레이가 존재하고 `progressNextGameFromHome()`에서 사용 중입니다.
- 즉, **로딩 UX 자체는 이미 충족**되어 있으며, 완료 시점의 동작을 `alert("경기 진행이 완료되었습니다.")`에서 결과 화면 이동으로 교체하면 됩니다.

### B. 경기 결과 핵심 데이터(스코어/팀/박스스코어)
- `progress-next-user-game-day` 응답에는 `game_day.user_game.game_id/home_team_id/away_team_id`가 포함됩니다.
- 시뮬레이션 결과 원본은 `state.ingest_game_result()`를 통해 `game_results[game_id]`에 `GameResultV2`로 저장됩니다.
- `GameResultV2`에는 `final`, `teams[team_id].totals`, `teams[team_id].players`가 있어 상단 스코어/팀스탯/리더 추출이 가능합니다.

### C. Gamecast(그래프/플로우) 데이터
- 어댑터가 `replay_events`를 `game_result`에 포함하도록 설계되어 있어, 이벤트 타임라인 기반 그래프 계산이 가능합니다.
- 따라서 `Game flow(시간-득점 누적)`는 이벤트 누적 점수로 직접 계산 가능합니다.
- `Win probability`는 엔진의 명시 지표가 없다면 **휴리스틱 모델(점수차+시간+사전 전력차)**로 1차 구현이 가능합니다.

### D. Matchups 패널
- 시즌 스케줄 전체는 `/api/team-schedule/{team_id}`로 확보 가능하여,
  - 이미 열린 맞대결 결과,
  - 예정된 맞대결 날짜,
  - 시즌 상대전적(몇 승 몇 패)
  계산이 가능합니다.

---

## 3) 요구사항별 데이터 매핑 (요청 UI 기준)

### 상단 (로고/팀명/기록/스코어/박스스코어)
- 로고/팀명: 기존 `TEAM_FULL_NAMES`, `renderTeamLogoMark`, `applyTeamLogo` 재사용.
- 현재 기준 승/패 기록: `team_schedule`에서 해당 경기 시점 `record_after_game` 또는 직전 누적 기반 계산.
- 박스스코어(쿼터별):
  - 엔진 raw에 쿼터 스플릿이 없으면 1차는 총점/핵심 팀스탯 중심으로 대체,
  - 추후 엔진/어댑터 확장으로 쿼터 스코어를 정식 노출.

### 중단 좌측 (Game leaders)
- 양 팀 `players[]`에서 `PTS/AST/REB` max 추출.
- 동률 시 분(min) 또는 FGM/usage 기준 tie-break 규칙 명시.

### 중단 중앙 (Win probability, Game flow)
- Game flow:
  - `replay_events`를 시간축(쿼터+게임클럭 → 절대 시간)으로 정규화,
  - 누적 점수 선 그래프 2개(home/away).
- Win probability:
  - 1차: 로지스틱 함수 기반 간이 모델
    - 입력: 시간 경과율, 현재 점수차, 팀 전력차(예: 팀 OVR/최근 승률).
  - 2차: 시뮬레이션 다회 반복 또는 사전 학습 계수로 보정.

### 중단 우측 (Matchups)
- 시즌 스케줄에서 동일 상대 매치만 필터링.
- 과거 경기: 점수/승패/날짜 노출.
- 미래 경기: 예정 날짜 + 현재 상대전적 표시.

### 상단 아래 탭 (Gamecast / boxscore / Teamstats)
- 이번 단계는 **Gamecast만 활성**.
- `boxscore`, `Teamstats`는 disabled/placeholder 탭으로 UI 껍데기만 제공.

---

## 4) 실제 구현 시 권장 작업 순서 (프론트 중심)

## Phase 1 — 최소 동작 경로(MVP)
1. `NBA.html`에 `game-result-screen` 섹션 추가.
2. `dom.js`에 결과 화면 element refs 등록.
3. `router.js`에 결과 화면 활성화 대상 추가.
4. `mainScreen.js`의 `progressNextGameFromHome()`를 수정:
   - 진행 API 성공 후 `user_game.game_id` 획득,
   - 결과 상세 API 호출,
   - 결과 화면으로 이동(기존 alert 제거).

## Phase 2 — 결과 데이터 API(ViewModel)
1. 신규 API 예시: `GET /api/game/result/{game_id}?user_team_id=...`
2. 반환 스키마(프론트 최적화):
   - `header`: 팀/점수/기록/기본 메타
   - `leaders`: points/assists/rebounds
   - `gamecast`: `win_probability_series`, `game_flow_series`
   - `matchups`: `previous_games[]`, `upcoming_games[]`, `h2h_record`
3. 서버에서 `workflow_state.game_results[game_id]` + `team_schedule` 조합으로 조립.

## Phase 3 — 시각 컴포넌트
1. Gamecast 탭과 패널 레이아웃(좌/중/우 3열).
2. SVG 기반 라인차트 2개(외부 차트 라이브러리 없이 구현 가능).
3. 반응형 처리(데스크톱 우선 + 태블릿 단일열 폴백).

## Phase 4 — 안정화
1. 데이터 누락 폴백(`replay_events` 없음, 리더 없음 등).
2. 중복 클릭 방지(진행 버튼 disable + in-flight guard).
3. 결과 화면 진입 실패 시 홈 복귀 + 토스트 에러.

---

## 5) 백엔드/프론트 리스크와 대응

- 리스크 1: 쿼터별 박스스코어 원천 부재 가능
  - 대응: 1차는 총점/팀 스탯 중심, 쿼터 스플릿은 후속 확장.

- 리스크 2: Win probability 정확도
  - 대응: 초기엔 설명 가능한 단순 모델로 제공하고, "예측 지표" 라벨 명시.

- 리스크 3: replay_events 품질 편차
  - 대응: 이벤트 정규화 유틸 + 값 검증 실패 시 차트 숨김/대체 문구.

- 리스크 4: 일정/상대전적 계산 비용
  - 대응: 서버 ViewModel API에서 선계산 후 프론트는 렌더만 수행.

---

## 6) 구현 가능 여부 최종 판단
- 요청하신 화면 구성(상단 헤더 + 중단 3열 + Gamecast 기본 탭)은 **현재 아키텍처에서 충분히 구현 가능**합니다.
- 특히 "경기 진행 중 로딩 → 완료 후 결과 화면" 전환은 이미 존재하는 로딩 오버레이/진행 API를 재활용하면 되므로 난이도가 낮은 편입니다.
- 핵심은 **결과 화면 전용 API(ViewModel)와 차트 데이터 정규화**를 먼저 정의하는 것입니다.

