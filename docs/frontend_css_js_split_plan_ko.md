# NBA 프론트엔드 CSS/JS 분할 계획 (안전 분할 + 장기 확장 중심)

## 1) 목표와 전제
- **목표 1: 기존 기능 무중단** — 현재 동작 중인 화면 전환, API 호출, 이벤트 바인딩이 분할 과정에서 깨지지 않도록 한다.
- **목표 2: 장기 확장성** — 화면/도메인 단위로 파일 경계를 만들고, 신규 기능 추가 시 기존 파일을 최소 수정하도록 구조를 설계한다.
- **목표 3: 점진적 이전** — 한 번에 대규모 리라이트하지 않고, “작게 나누고 즉시 검증”하는 방식으로 진행한다.

---

## 2) 현재 상태 요약 (분할 근거)
- 단일 진입 JS(`static/NBA.js`)에 공용 유틸 + 여러 화면 로직 + 이벤트 바인딩 + 초기화가 모두 섞여 있다.
- 단일 CSS(`static/NBA.css`)에 공통 스타일, 각 스크린 스타일, 반응형 규칙이 함께 존재한다.
- HTML(`static/NBA.html`)은 여러 스크린을 한 문서에서 토글하는 구조다.

즉, 현재는 “한 파일 수정이 전체에 영향”을 주기 쉬운 구조이므로, **공통/도메인/화면/초기화** 레이어로 분리하는 것이 안전하다.

---

## 3) 최종 목표 디렉터리 구조

### 3-1. JavaScript
```text
static/js/
  app/
    bootstrap.js           # 앱 시작점(초기화 순서, 전역 에러 처리)
    state.js               # 단일 상태 저장소 + 초기 state
    dom.js                 # DOM 참조(기존 els) 수집
    router.js              # 화면 전환/active panel 제어
    events.js              # 전역 이벤트 등록
  core/
    api.js                 # fetchJson, 공통 에러/응답 처리
    format.js              # 날짜/숫자/통화 포맷
    guards.js              # safeNum, clamp, escapeHtml 등
    constants/
      teams.js             # TEAM_FULL_NAMES, TEAM_BRANDING
      tactics.js           # 전술 스킴/역할 상수
  features/
    main/
      mainScreen.js
      homeWidgets.js
    schedule/
      scheduleScreen.js
    myteam/
      myTeamScreen.js
      playerDetail.js
    training/
      trainingScreen.js
      trainingCalendar.js
      trainingDetail.js
    tactics/
      tacticsScreen.js
      tacticsInsights.js
    standings/
      standingsScreen.js
    college/
      collegeScreen.js
      leaders.js
      bigboard.js
      scouting.js
    medical/
      medicalScreen.js
  index.js                 # HTML에서 type="module"로 로드하는 엔트리
```

### 3-2. CSS
```text
static/css/
  base/
    reset.css              # (선택) 브라우저 기본값 보정
    tokens.css             # 색상, spacing, radius, z-index 변수
    typography.css
    utilities.css          # .hidden, .text-accent 같은 유틸
  layout/
    shell.css              # 앱 외곽 레이아웃
    grid.css               # 공용 grid/flex 레이아웃
    responsive.css         # 공용 반응형 규칙
  components/
    buttons.css
    cards.css
    tables.css
    modal.css
    logos.css
    chips.css
  screens/
    start.css
    main.css
    schedule.css
    myteam.css
    training.css
    tactics.css
    standings.css
    college.css
    medical.css
  index.css                # import 순서 제어용 집합 파일
```

> 핵심 원칙: **“화면 전용 스타일은 screens/, 재사용 가능 스타일은 components/”**에 둔다.

---

## 4) 안전한 분할 순서 (기능 안 깨지게)

### Phase 0. 기준선 확보 (변경 전)
1. 화면별 수동 QA 체크리스트를 만든다.
   - 시작/팀선택/메인/스케줄/마이팀/훈련/전술/순위/대학/메디컬.
2. 콘솔 에러 0건을 기준선으로 기록한다.
3. 주요 API 호출 경로(예: `/api/*`)를 목록화한다.

### Phase 1. “동작 변화 없는 파일 이동”부터
1. `NBA.js`에서 **상수/유틸 함수**만 먼저 분리한다.
   - 예: 팀 브랜딩 상수, 숫자/날짜 포맷, 안전 유틸.
2. 분리 후에도 함수 시그니처와 반환값은 동일하게 유지한다.
3. 한 번에 하나의 묶음만 이동하고 바로 QA한다.

### Phase 2. 화면 단위 로직 분리
1. 화면별로 `showXScreen`, `renderX`, `loadX` 묶음을 feature 파일로 옮긴다.
2. 각 feature는 외부에 최소 API만 노출한다.
   - 예: `showTrainingScreen()`, `initTrainingEvents()`.
3. 기존 `state`, `els` 직접 접근을 줄이고, 필요한 의존성을 인자로 주입한다.

### Phase 3. 이벤트 바인딩 집약
1. 파일 하단의 대규모 `addEventListener`를 `events.js`로 이동한다.
2. 이벤트 등록 순서를 고정한다.
   - `initDOM -> initState -> initFeatures -> bindEvents -> firstRender`.
3. 중복 바인딩 방지를 위해 `bindOnce` 패턴(또는 init 플래그)을 둔다.

### Phase 4. CSS 분할
1. 먼저 `tokens/utilities/components`를 분리한다.
2. 그 다음 화면별 블록을 `screens/*.css`로 이동한다.
3. 스타일 우선순위 깨짐 방지를 위해 import 순서를 고정한다.
   - `base -> layout -> components -> screens -> overrides`.
4. 분리 직후 화면별 시각 QA를 수행한다.

### Phase 5. 정리/확장 준비
1. 네이밍 규칙 통일(예: `screen-`, `component-` 접두사 또는 BEM).
2. 새 기능 템플릿(새 스크린 추가 시 파일 세트) 문서화.
3. “어느 파일에 무엇을 넣는지” 결정표 유지.

---

## 5) 의존성 경계 규칙 (장기 유지보수 핵심)
- `core/*`는 `features/*`를 import하지 않는다. (단방향 의존)
- `features/*`는 다른 feature 내부 구현을 직접 import하지 않는다.
  - 필요한 경우 `app/router` 또는 공용 서비스 계층을 통해 간접 호출.
- DOM selector 문자열은 `dom.js`에만 둔다.
- API endpoint 문자열은 `core/api` 또는 feature별 `api.js`로 모은다.
- `state` 직접 변경은 feature 내부 helper를 통해서만 수행한다.

---

## 6) 무중단을 위한 체크리스트

### 분할 작업마다 공통 확인
- 콘솔 에러/경고 신규 발생 여부
- 화면 전환 정상 여부 (`activateScreen` 동등 동작)
- 버튼 클릭 이벤트 중복 등록 여부
- 모달 ESC 닫힘 동작 유지 여부
- API 실패 시 기존 에러 메시지 UX 유지 여부

### 화면별 핵심 시나리오
- **메인**: 다음 경기 카드/로고/일정/대시보드 렌더
- **스케줄**: 완료/예정 테이블 렌더
- **마이팀**: 정렬/필터/선수 상세
- **트레이닝**: 캘린더 선택/세부 패널/추천 문구
- **전술**: 스킴 선택/라인업 수정/인사이트 갱신
- **대학**: 탭 전환/리더 정렬/빅보드 모달/스카우팅 배정
- **메디컬**: 위험도 표시/선수 컨텍스트 로딩

---

## 7) 실제 분할 시 권장 작업 단위 (PR 단위)
- PR 1: JS 상수/유틸 분리 + 무동작 변경
- PR 2: app bootstrap/state/dom/router 정리
- PR 3: 스크린 2개(예: schedule, standings) 분리
- PR 4: 복잡 스크린(training, tactics) 분리
- PR 5: college/medical 분리
- PR 6: CSS base/components/screens 분리
- PR 7: 네이밍/문서/죽은 코드 제거

> 각 PR은 “작은 범위 + 즉시 회귀검증” 원칙을 지켜야 리스크가 낮다.

---

## 8) 지금 당장 시작할 때의 실전 가이드
1. HTML 로딩 방식을 `type="module"` 기반으로 전환 가능한지 먼저 확인.
2. 번들러 없이 진행한다면, 상대경로 import 구조를 먼저 고정.
3. 번들러(Vite/esbuild) 도입 예정이면 **도입 PR**과 **분할 PR**을 분리.
4. 테스트 자동화가 없다면 최소한 스모크 테스트 스크립트(화면 진입/클릭/탭 전환)부터 준비.

---

## 9) 결론
- 현재 구조에서는 **한 번에 크게 나누는 방식**보다, **상수/유틸 → 화면 로직 → 이벤트 → CSS** 순서의 점진 분할이 가장 안전하다.
- 장기적으로는 `app/core/features` + `base/components/screens` 이중 축 구조가, 기능 추가 시 충돌을 가장 적게 만든다.
- 핵심은 파일 개수보다 **의존성 방향과 초기화 순서 고정**이다.

---

## 10) `static/NBA.css` → 분할 파일 **정밀 매핑 설계안**

> 원칙: 아래 매핑은 **이동(move)이 아니라 복사(copy) 기준**이다. `static/NBA.css`는 분할 작업 전/후로 원본을 그대로 유지한다. 각 규칙은 대응 분할 파일에 복사 배치하되, 최종 단계에서 `NBA.css`와 분할 파일 전체를 비교해 누락·변형이 없는지 검증한다.

### 10-1. 집합 파일(`static/css/index.css`) import 순서
```css
@import "./base/tokens.css";
@import "./base/typography.css";
@import "./base/utilities.css";

@import "./layout/shell.css";
@import "./layout/grid.css";
@import "./layout/responsive.css";

@import "./components/buttons.css";
@import "./components/cards.css";
@import "./components/tables.css";
@import "./components/modal.css";
@import "./components/logos.css";
@import "./components/chips.css";

@import "./screens/start.css";
@import "./screens/main.css";
@import "./screens/schedule.css";
@import "./screens/myteam.css";
@import "./screens/training.css";
@import "./screens/tactics.css";
@import "./screens/standings.css";
@import "./screens/college.css";
@import "./screens/medical.css";
```

---

### 10-2. 베이스/레이아웃/컴포넌트 매핑

#### `base/tokens.css`
- `:root` 커스텀 프로퍼티 전체 (`L1–L14`)
- 화면 스코프 변수 선언
  - `#college-screen` 변수 블록 (`L1094–L1098`)
  - `#training-screen` 변수 블록 (`L2002–L2007`)

#### `base/typography.css`
- `body`의 글꼴/기본 글자색 (`L17–L21`) + 라이트 테마 영역의 `color` 오버라이드
- `h1, h2, h3` 기본 마진 (`L110`)
- `.eyebrow`, `.subtitle`, `.hint`, `.section-note`, `.section-copy`, `.empty-copy` 같은 텍스트 유틸/문구 클래스
- 상태 텍스트: `.status-ok`, `.status-danger`, `.status-warn`, `.text-accent`

#### `base/utilities.css`
- 전역 유틸: `.hidden`, `.panel`, `.panel.active`, `body.is-modal-open`
- `* { box-sizing: border-box; }`
- `.ui-input`(college 스코프 내부 입력 포함)은 스타일 성격상 유틸에 고정
- 로딩 관련 공통 유틸: `.loading-overlay`, `.loading-box`, `.spinner`, `@keyframes spin`

#### `layout/shell.css`
- 페이지 배경/오버레이/셸:
  - `body` 배경 그래디언트
  - `.bg-overlay`
  - `.app-shell`
  - `body:has(...active) .app-shell` 계열(초기 3개 + unified light mood 8개)
- 화면 공통 박스 기본형 (`#start-screen ... #player-detail-screen` 블록)
- 풀블리드 화면 구조 규칙
  - `#start-screen`, `#team-screen`, `#main-screen`
  - `#schedule-screen ... #player-detail-screen`
  - 각 `> *` 내부 width 정렬

#### `layout/grid.css`
- 레이아웃/배치 전용 클래스(화면 비종속):
  - `.button-group`, `.team-grid`, `.main-menu-grid`
  - `.screen-header-row`, `.roster-shell`, `.schedule-shell`
  - `.college-two-col`, `.college-two-col-7-5`
  - `.player-layout`, `.player-layout-v2`
  - 공용 `display:grid/flex` 유틸 성격 블록

#### `layout/responsive.css`
- **모든 `@media` 블록** 이동 (원본 순서 유지):
  - college: `1220`, `640`, `900`
  - main/schedule/player: `1120`, `720`
  - training: `1365`, `900`
  - tactics: `1024`, `1180`, `780`
  - medical: `1320`, `1100`, `760`
  - start/team: `980`, `720`
  - light mood 공통: `720`
  - myteam/player: `1200`, `760`

#### `components/buttons.css`
- `.btn`, `.btn:hover`, `.btn:disabled`, `.btn-primary`, `.btn-secondary`
- 화면 스코프 버튼 오버라이드
  - `#start-screen/#team-screen .btn-*`
  - `#main-screen .btn-secondary`
  - 라이트 무드 공통 `#schedule-screen ... #player-detail-screen .btn-secondary`
  - `.home-inline-cta`, `.training-top-tabs .is-active`

#### `components/cards.css`
- 카드 컨테이너 계열(테이블 제외):
  - `.team-card`, `.menu-card`, `.menu-card-highlight`, hover 계열
  - `.next-game-panel`, `.home-kpi-card`, `.home-panel`
  - `.college-card`, `.training-kpi-card`, `.training-context-panel`, `.training-detail-panel`
  - `.standings-card`, `.medical-card`, `.detail-card`, `#player-detail-screen .detail-card-*`
  - 라이트 무드 공통 카드 오버라이드(`L2997–L3009`)

#### `components/tables.css`
- 모든 table/wrap/thead/tbody/cell 규칙:
  - schedule/college/roster/training-player/standings/medical 관련 `*-table*`
  - `.schedule-opponent-cell`, `.standings-team-cell`, `.roster-row` 등 행/셀 규칙
  - 라이트 무드 공통 표 헤더/셀 오버라이드(`L3017–L3033`)

#### `components/modal.css`
- `.confirm-modal*` 전체
- `#college-screen .college-scout-modal-panel`
- 기타 모달/오버레이 성격 컨테이너

#### `components/logos.css`
- 로고/마크 전용:
  - `.team-logo-image`, `.team-logo-mark*`, `.team-logo-placeholder*`
  - `.schedule-team-logo`, `.standings-team-logo`, `.team-card-logo*`
  - `#team-a-logo`, `#team-b-logo`

#### `components/chips.css`
- 칩/배지/필/상태 태그 전용:
  - `.schedule-result-badge*`, `.schedule-time-chip`
  - `.home-badge*`, `.home-day-chip*`
  - `.sharpness-badge`, `.sharpness-badge-v2*`
  - college chip/tag (`.college-*-chip`, `.college-tag*`)
  - medical chip/badge (`.medical-chip`, `.medical-alert-badge*`)
  - tactics role/pill/warning item
  - training chip/badge (`.training-session-badge*`, `.training-player-chip*`)

---

### 10-3. 화면 파일 매핑 (screen 전용 규칙)

#### `screens/start.css`
- `#start-screen` 및 `.start-*` 전체
- 시작 화면 내부 타이포/버튼/히어로 오버라이드
- `#team-screen`과 시작 플로우를 함께 묶는 규칙 중 **팀 선택 전용은 제외**하고, 시작 전용만 유지

#### `screens/main.css`
- `#main-screen` 전체
- `.top-league-*`, `.main-overview*`, `.franchise-*`
- `.next-game-*`, `.home-*` 위젯 전체

#### `screens/schedule.css`
- `.schedule-shell`, `.schedule-card*`, `.schedule-*` (테이블 구조는 `tables.css`로 분리하되 schedule 전용 색/칩은 유지)
- `#schedule-screen` 스코프 오버라이드

#### `screens/myteam.css`
- `#my-team-screen` 스코프 전부
- `.roster-*`, `.condition-*` 중 마이팀 전용
- `L3168` 이후 My Team premium 블록에서 `#my-team-screen` 대상 규칙 전체

#### `screens/training.css`
- `/* Training screen */` 이후 `.training-*` 전부
- `#training-screen` 스코프 오버라이드
- 캘린더/세션/미리보기/선수 선택 관련 규칙

#### `screens/tactics.css`
- `/* tactics screen */` 이후 `.tactics-*` 전부
- `#tactics-screen` 스코프 오버라이드 전부
- `/* Tactics Command Center */` 블록 전체

#### `screens/standings.css`
- `/* Standings screen */` 이후 `.standings-*` 전부
- `#standings-screen` 스코프 규칙

#### `screens/college.css`
- `.college-*` + `#college-screen` + `#college-bigboard-detail-screen` 전부
- 스카우팅/빅보드/리더/배정/피드백 관련 규칙

#### `screens/medical.css`
- `.medical-*` 전부
- `#medical-screen` 스코프 전부
- 메디컬 대시보드/타임라인/캘린더/액션 리스트 관련 규칙

---

### 10-4. 중복·누락 방지 규칙 (필수)
1. **동일 선택자 다중 선언은 동일 파일로 집결**
   - 예: `.tactics-options` 기본 선언 + `#tactics-screen .tactics-options` 선언 + 후반 premium 선언 → 모두 `screens/tactics.css`.
2. **공통 컴포넌트와 화면 오버라이드 분리**
   - 기본 컴포넌트(예: `.btn`, `.team-card`, `.schedule-table`)는 components,
   - 화면 색상/테마 오버라이드는 screens.
3. **라이트 무드 통합 블록(`L2881–L3065`) 처리**
   - 구조/폭/패딩은 `layout/shell.css`.
   - 공통 타이포 색은 `base/typography.css`.
   - 공통 버튼/카드/테이블 오버라이드는 각 components 파일.
   - screen별 전용 문맥 색상은 해당 screen 파일.
4. **`@media`는 전부 `layout/responsive.css`로 이동**
   - 단, 이동 후 내부 선언이 참조하는 기본 규칙이 먼저 import되도록 `index.css` 순서 고정.

---

### 10-5. 실제 분할 작업 최적 순서 (무손실 기준)
1. `static/css/` 트리와 빈 파일 생성 + `index.css` import 뼈대만 구성.
2. `NBA.css`는 **절대 수정/삭제하지 않고 유지**한다. 분할 파일은 `NBA.css`에서 선택자를 복사해 붙여넣는 방식으로만 작성한다.
3. `tokens → typography → utilities` 이동.
4. `shell → grid → responsive` 이동.
5. `buttons/cards/tables/modal/logos/chips` 이동.
6. `screens/start → main → schedule → standings → college → training → tactics → myteam → medical` 순서로 이동.
7. 모든 분할 파일 작성 후 `NBA.css`와 분할 파일 집합을 대조해 누락/변형 여부를 검증한다(선택자/선언/미디어쿼리 단위).
8. 회귀검증: 화면 전환/모달/테이블/로고/칩/반응형 폭(720, 900, 980, 1120, 1365) 순서로 스모크 테스트.
9. `NBA.css` 원본 유지 상태(`git diff static/NBA.css` 무변경)와 분할 파일 커버리지 비교 결과를 기록한다.

> 위 순서를 지키면 “기본토큰/공통컴포넌트/화면오버라이드”의 계층이 먼저 안정화되고, 나중에 스크린별 이전 시 충돌을 최소화할 수 있다.
