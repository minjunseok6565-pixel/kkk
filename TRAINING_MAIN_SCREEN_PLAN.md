# NBA 시뮬레이션 게임 메인 화면(팀 훈련) 프론트엔드 개선 기획안

## 1) 문서 목적
이 문서는 현재 `TRAINING` 메인 화면을 상업용 게임 수준으로 끌어올리기 위한 **실행 가능한 화면 기획서**이다.
다음 작업(HTML/CSS/JS 실제 구현)은 본 문서의 정보구조, 컴포넌트 정의, 상태 규칙, API 매핑을 기준으로 수행한다.

---

## 2) 작업 범위 / 비범위

### 작업 범위 (반드시 준수)
- 개선 대상: 메인 화면의 팀 훈련 화면 (`training-screen`)만.
- 변경 파일 범위: `static/NBA.html`, `static/NBA.css`, `static/NBA.js`.
- 데이터 사용 원칙: **기존 조회 API만 사용** (신규 API 추가 금지).

### 비범위 (절대 금지)
- 백엔드 라우트 추가/수정.
- 데이터 스키마 변경.
- 다른 화면(로스터, 전술, 순위, 의료 등) UI/UX 수정.
- 엔진 로직 변경.

---

## 3) 제품 목표 (UX 관점)
1. 첫 진입 3초 안에 “감독이 이번 주에 무엇을 결정해야 하는지”가 보인다.
2. 캘린더에서 날짜를 고를 때 **위험/효과/일정 맥락**이 즉시 이해된다.
3. 적용 전 결과(익숙도, 샤프니스, 위험)를 보고 안심하고 실행할 수 있다.
4. 화면 완성도가 높아 “바이브 코딩 결과물”이 아니라 “프로덕션 게임 UI”로 인식된다.

---

## 4) 핵심 콘셉트: Training War Room

### 한 줄 정의
훈련 캘린더를 단순 편집 도구가 아니라, 경기 일정·컨디션·의료 리스크를 동시에 보는 **감독 의사결정 워룸**으로 재구성한다.

### 톤 앤 무드
- 키워드: `Premium`, `Tactical`, `Broadcast-grade`, `Data-driven`.
- 시각 방향: 저채도 다크 베이스 + 팀 컬러 하이라이트 + 상태 의미색.
- 화면 인상: “관리 UI”가 아닌 “스포츠 운영실 UI”.

---

## 5) 레이아웃 정보 구조 (IA)

## 최상단: Command Bar (고정 헤더 영역)
- 좌측: 기간/컨텍스트
  - 예: `REG SEASON · WEEK 3 (10/13–11/09)`
- 중앙: KPI 카드 4종
  1) Team Sharpness Avg
  2) Low Sharpness Count (<45)
  3) Next 7D Games / B2B
  4) High Risk / OUT / Returning
- 우측: 모드 토글 및 빠른 액션
  - `AUTO 추천 보기` / `수동 편집 모드`

### 중단 좌측(메인): Smart Calendar Board
- 7x4 캘린더 유지 (학습 비용 최소화)
- 각 날짜 셀 정보:
  - 날짜
  - 경기 정보(상대팀)
  - 훈련 타입
  - AUTO/USER 배지
  - 위험도 히트 인디케이터
  - B2B 표식

### 중단 우측: Decision Panel
- 선택된 날짜 집합 요약
  - 선택 개수, 연속 일수, 게임 인접 여부
- 훈련 타입별 설정 UI
- 프리뷰 결과 카드
  - 익숙도 상승량, 평균 샤프니스 변화량, 위험 경고
- 최종 실행 CTA

### 하단: Training Action Rail
- 훈련 타입 버튼 6개를 “동등 버튼”에서 “목적형 버튼”으로 재설계
- 타입별 목적/효과 태그(예: `익숙도↑`, `회복`, `밸런스`)

---

## 6) 컴포넌트 상세 명세

## A. KPI 카드 (`training-kpi-card`)
- 구성: 라벨 / 값 / 보조텍스트 / 전주 대비 증감
- 상태:
  - normal
  - warn (예: low sharpness 많음)
  - danger (예: high risk + out 증가)
- 시각 규칙:
  - 값 숫자 28~32px, 라벨 11~12px uppercase
  - 증감은 `+/-`와 의미색 동시 사용

## B. 캘린더 셀 (`training-day-card`)
- 최소 높이: 96~112px (현재보다 확대)
- 내부 슬롯:
  1) `top`: 날짜 + 상태 배지
  2) `mid`: 경기/훈련 핵심 라인
  3) `bottom`: 리스크/강도 바
- 상태 클래스:
  - `is-game`, `is-selected`, `is-disabled`, `is-user-set`, `is-auto-set`, `is-b2b`, `risk-low|mid|high`

## C. 배지/칩 시스템
- `GAME`, `B2B`, `AUTO`, `USER`, `REST`, `HIGH RISK`를 칩 컴포넌트로 통일
- 텍스트만으로 구분하지 않고 아이콘+색+라벨 함께 사용

## D. Decision Panel 카드
- `Selection Summary Card`
- `Session Config Card`
- `Effect Preview Card`
- `Apply Confirmation Card`

---

## 7) 데이터 매핑 (신규 API 없이)

## 이미 사용 중 / 그대로 활용
- 일정: `/api/team-schedule/{team_id}`
- 저장된 훈련 세션: `/api/practice/team/{team_id}/sessions`
- 일자별 세션 해석: `/api/practice/team/{team_id}/session`
- 팀 로스터: `/api/team-detail/{team_id}`
- 익숙도: `/api/readiness/team/{team_id}/familiarity`
- 훈련 프리뷰: `/api/practice/team/{team_id}/preview`

## 추가 조회 가능(기존 라우트)
- 팀 샤프니스 분포: `/api/readiness/team/{team_id}/sharpness`
- 의료 알림 요약: `/api/medical/team/{team_id}/alerts`
- 의료 리스크 캘린더: `/api/medical/team/{team_id}/risk-calendar`

## KPI 계산 예시
- Team Sharpness Avg = sharpness distribution `avg`
- Low Sharp Count = sharpness distribution `low_sharp_count`
- Next 7D Games/B2B = medical alerts `team_load_context`
- High Risk/OUT/Returning = risk-calendar 일자 데이터 집계

---

## 8) 인터랙션 시나리오

## 시나리오 1: 화면 진입
1. 헤더 KPI 먼저 로드(빠른 skeleton).
2. 캘린더 로드 후 각 셀에 일정/세션/위험 배지 렌더.
3. 기본 안내: “날짜를 선택하면 우측 패널에서 훈련 효과를 확인할 수 있습니다.”

## 시나리오 2: 날짜 멀티 선택
1. 유효 날짜 클릭 시 선택 토글.
2. 상단 서브바에 `선택 n일` 표시.
3. 우측 패널 `Selection Summary` 실시간 반영.

## 시나리오 3: 훈련 타입 선택
1. 타입 버튼 클릭 시 우측 `Session Config` 전환.
2. 가능한 스킴/참가자 설정 노출.
3. 첫 선택 날짜 기준 프리뷰 요청 후 `Effect Preview` 표시.

## 시나리오 4: 적용
1. `적용` 클릭 시 경고 조건 검사(고위험/연속 고강도).
2. 문제 없으면 멀티 날짜 일괄 적용.
3. 성공 토스트 + 변경 셀 하이라이트 애니메이션.

---

## 9) 시각 디자인 시스템 가이드

## 타입 스케일
- Display: 32
- H2: 24
- H3: 18
- Body: 14/16
- Caption: 12

## 간격 시스템
- 4/8pt 기반만 사용 (4, 8, 12, 16, 20, 24, 32)

## 컬러 역할
- Base BG
- Elevated Surface
- Border Subtle
- Text Primary/Secondary
- Semantic: success/warn/danger/info

## 모션 원칙
- hover: 120~160ms
- panel transition: 180~220ms
- apply feedback pulse: 500~700ms

---

## 10) 카피라이팅 가이드
- 짧고 명령형.
- 숫자 중심.
- 판단을 돕는 문장 우선.

예시:
- “다음 7일 일정 압박이 높습니다. 회복 세션 비중을 늘리세요.”
- “선택한 4일 중 2일은 경기 인접일입니다.”
- “현재 설정은 샤프니스 하락 위험이 있습니다.”

---

## 11) 구현 단계 계획 (다음 작업용)

## Phase 1 (구조/가시성)
- HTML: Command Bar + 우측 Decision Panel 구조 추가
- CSS: 카드/칩/상태색/레이아웃 시스템 정비
- JS: KPI/리스크 데이터 fetch + 렌더 파이프라인

## Phase 2 (품질/완성도)
- 캘린더 셀 상태 다층 표현
- 프리뷰 카드 고도화
- 에러/로딩/빈 상태 품질 개선

## Phase 3 (폴리싱)
- 모션/토스트/세부 상호작용
- 반응형(최소 너비 기준) 튜닝
- 접근성 라벨/키보드 포커스 강화

---

## 12) 수용 기준 (Acceptance Criteria)
1. 신규 API 없이 KPI 4종이 렌더된다.
2. 캘린더 셀에서 `GAME/AUTO/USER/B2B/리스크`를 즉시 인지할 수 있다.
3. 날짜 선택 → 훈련 선택 → 프리뷰 확인 → 적용 흐름이 3클릭 내 완료된다.
4. 스타일 일관성(타입/간격/색/상태)이 화면 전역에서 유지된다.
5. 기존 기능(세션 조회/설정, 프리뷰)이 깨지지 않는다.

---

## 13) 개발 시 주의사항
- 이번 문서 기반 실제 구현 작업에서도 변경 파일은 `static/NBA.html`, `static/NBA.css`, `static/NBA.js`로 제한한다.
- 기존 함수/상태를 최대 재사용하고, 신규 코드는 트레이닝 화면 네임스페이스로 격리한다.
- 백엔드 수정 유혹이 있어도 금지(기획 조건 위반).

