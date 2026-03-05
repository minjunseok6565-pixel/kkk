# 경기 결과 화면 PBP 로그 프론트엔드 구현 실행 계획 (2026-03-06)

## 문서 목적
- `docs/game_result_pbp_design_ko.md`의 서버/도메인 설계를 바탕으로, **현재 프로젝트 구조에 맞는 프론트엔드 구현 단계**를 파일 단위로 구체화한다.
- 이번 문서는 실제 코드 패치가 아니라, 이후 구현 시 바로 작업 티켓으로 분해 가능한 수준의 실행 설계서다.

---

## 0) 현재 기준점(As-Is)
- 화면 구조: `static/NBA.html`
  - `Gamecast / Boxscore / Teamstats` 3탭 구성
  - PBP 전용 영역/탭은 아직 없음
- 상태/DOM 참조: `static/js/app/dom.js`
  - PBP 관련 element reference 없음
- 경기결과 렌더러: `static/js/features/gameResult/gameResultScreen.js`
  - 탭 전환 로직이 `TAB_KEYS = ["gamecast", "boxscore", "teamstats"]` 하드코딩
  - `renderGameResult()`에서 leaders/charts/matchups 렌더까지만 수행
- 스타일: `static/css/screens/game-result.css`
  - Gamecast 레이아웃(3열), 표/차트 위주 스타일만 존재

---

## 1) 목표 화면(TO-BE)

## 1.1 IA/탭 구조
- 결과 탭을 4개로 확장
  1. `Gamecast`
  2. `Play by Play` (신규)
  3. `Boxscore`
  4. `Teamstats`
- 기본 활성 탭 우선순위
  - API에서 `tabs.default` 제공 시 그대로 사용
  - 미제공 시 `play_by_play.available === true`이면 `playbyplay`
  - 그 외 `gamecast`

## 1.2 PBP 패널 구성
- 상단 컨트롤 바
  - 쿼터 필터: `ALL / Q1 / Q2 / Q3 / Q4 / OT`
  - 팀 필터: `ALL / HOME / AWAY`
  - 이벤트 필터(칩): `SCORING / FOUL / TURNOVER / REBOUND / TIMEOUT / SUB`
  - 토글: `핵심 이벤트만`(collapsed group 우선)
- 메인 로그 리스트
  - 좌측: 시간/쿼터 (`11:18 · Q1`)
  - 중단: 타이틀 + 설명
  - 우측: 스코어 스냅샷 (`LAL 42 - 39 BOS`)
  - 보조 배지: `LEAD CHANGE`, `TIE`, `CLUTCH`
- 그룹 이벤트(클러스터) UX
  - 기본: 접힌 요약 1줄 + "상세 n개" 버튼
  - 확장: 하위 이벤트 목록 표시

## 1.3 빈 상태/오류 상태
- `available=false` 또는 `items=[]`
  - 빈 상태 카드 + 원인 문구(`source`, `meta.filtered_out`) 노출
- 텍스트 누락/ID 누락
  - 설명 fallback: `이벤트 상세 정보를 준비 중입니다.`

---

## 2) API 계약(프론트 관점)

`GET /api/game/result/{game_id}` 응답에 아래 구조를 사용한다고 가정한다.

```json
"play_by_play": {
  "available": true,
  "source": "replay_events",
  "items": [
    {
      "seq": 381,
      "period": 1,
      "clock": "11:18",
      "team_id": "CHA",
      "event_key": "free_throw_made",
      "title": "+1 Point",
      "description": "Miles Bridges makes free throw 1 of 2",
      "score": {"home": 4, "away": 2},
      "score_change": 1,
      "tags": ["scoring", "free_throw"],
      "badges": ["lead_change"],
      "group": {
        "id": "g-120",
        "collapsed": true,
        "size": 3,
        "children": []
      }
    }
  ],
  "meta": {
    "total_replay_events": 612,
    "exposed_pbp_items": 178,
    "filtered_out": 434,
    "collapsed_groups": 29
  }
}
```

### 프론트 안전 규칙
- `play_by_play`가 없으면 `available=false`로 간주
- `items`가 배열이 아니면 빈 배열로 강등
- `score.home/away`는 숫자 강제 변환 실패 시 `-`

---

## 3) 파일별 수정 설계

## 3.1 `static/NBA.html`

### 수정 포인트
1. 탭 버튼 추가
- 기존 탭 영역에 `Play by Play` 버튼 삽입
- ID 제안: `game-result-tab-playbyplay`
- `data-tab="playbyplay"`

2. 신규 View 섹션 추가
- ID: `game-result-view-playbyplay`
- 내부 구조:
  - `game-result-pbp-toolbar`
  - `game-result-pbp-summary`
  - `game-result-pbp-list`

3. 접근성 속성
- 탭 버튼에 `aria-controls`
- 각 view에 `role="tabpanel"` + `aria-labelledby`

### 산출 HTML 스켈레톤(요약)
- toolbar: 필터 버튼 그룹(버튼 기반, 추후 select로 교체 가능)
- list: `<ul>` + `<li>` 구조
- empty: `<div class="game-result-pbp-empty">`

---

## 3.2 `static/js/app/dom.js`

### 추가 element reference
- 탭/뷰
  - `gameResultTabPlaybyplay`
  - `gameResultViewPlaybyplay`
- PBP 컨테이너
  - `gameResultPbpToolbar`
  - `gameResultPbpSummary`
  - `gameResultPbpList`
- 필터/토글 버튼
  - `gameResultPbpFilterPeriodAll`, `...Q1`, `...OT`
  - `gameResultPbpFilterTeamAll`, `...Home`, `...Away`
  - `gameResultPbpOnlyKeyToggle`

### 주의
- 기존 `els` export 패턴 유지(단일 객체)
- null-safe는 소비자(`gameResultScreen.js`)에서 처리

---

## 3.3 `static/js/features/gameResult/gameResultScreen.js` (핵심)

### A. 탭 시스템 확장
- `TAB_KEYS`를 `['gamecast', 'playbyplay', 'boxscore', 'teamstats']`로 변경
- `setActiveTab()`의 `tabMap`, `viewMap`에 PBP 항목 추가
- `bindGameResultTabs()`에서 신규 버튼 바인딩

### B. PBP 정규화 함수 추가
파일 내부 private 함수로 추가:
- `normalizePbp(result)`
  - 입력: API result 전체
  - 출력: `{ available, source, items, meta }`
- `normalizePbpItem(raw, index)`
  - 필수 필드 fallback
  - `seq` 없으면 index 기반 surrogate seq 부여
- `formatPbpTime(period, clock)`
  - `Q1/Q2/Q3/Q4/OT1...` 표기

### C. PBP 렌더 함수 추가
- `renderPbpSummary(pbp)`
  - 총 노출 수 / 필터링 수 / source 텍스트
- `renderPbpList(items, context)`
  - 필터 적용 후 HTML 생성
  - 그룹 이벤트/단일 이벤트 분기 렌더
- `renderPbpEmpty(reason)`

### D. 화면 상태(state) 확장
`state`에 아래 필드 추가(다른 모듈과 충돌 없게 네임스페이스화)
- `state.gameResultPbp = {
    period: 'ALL',
    team: 'ALL',
    tags: new Set(),
    onlyKey: false,
    expandedGroups: new Set()
  }`

### E. 이벤트 바인딩
- `bindPbpControls()` 신규
  - period/team/tag 토글 버튼 클릭 이벤트
  - group expand/collapse delegation
- 화면 재진입 시 중복 바인딩 방지를 위한 `pbpControlsBound` 플래그

### F. 렌더 진입점 연결
- `renderGameResult(result)` 말미에서
  - `const pbp = normalizePbp(result)`
  - summary/list 렌더
  - 초기 탭 선택 로직에 `playbyplay` 고려

### G. 성능/안정성
- item 수가 많을 때(예: 300+)를 대비한 1차 페이징
  - 초깃값 80개 + `더 보기` 버튼
- 문자열 escape 유틸 사용(기존 코드 패턴에 맞춰 간단 escape 함수 추가)

---

## 3.4 `static/css/screens/game-result.css`

### 신규 스타일 블록
1. toolbar
- `.game-result-pbp-toolbar`
- `.game-result-pbp-filter-group`
- `.game-result-pbp-chip`

2. summary
- `.game-result-pbp-summary`
- `.game-result-pbp-kpi`

3. list item
- `.game-result-pbp-list`
- `.game-result-pbp-item`
- `.game-result-pbp-time`
- `.game-result-pbp-body`
- `.game-result-pbp-score`
- `.game-result-pbp-badges`

4. group(expand)
- `.game-result-pbp-group`
- `.game-result-pbp-group-toggle`
- `.game-result-pbp-children`

5. empty/fallback
- `.game-result-pbp-empty`

### 반응형
- 1080px 이하에서 3열 item을 1열 카드형으로 축소
- score 영역 우측 정렬 해제, 하단 이동

---

## 3.5 (선택) 신규 모듈 분리
현재 `gameResultScreen.js`가 이미 길기 때문에, PBP 관련 코드를 분리하는 안을 권장.

### 추가 파일 제안
- `static/js/features/gameResult/pbpRenderer.js`
  - 정규화/필터/렌더 함수 집약
  - export:
    - `initPbpControls(els, state, onChange)`
    - `buildPbpViewModel(result)`
    - `renderPbp(els, vm, state)`

- `static/js/features/gameResult/pbpFormatters.js`
  - event_key -> icon/chip/title fallback 매핑

### 분리 기준
- 본 파일(`gameResultScreen.js`)에서는 탭 제어 + 데이터 주입만 담당
- PBP 도메인 로직은 별도 모듈로 테스트 가능하게 유지

---

## 4) 구현 순서(프론트)

### Phase 1: 스켈레톤/탭 통합
1. `NBA.html`에 탭/뷰 뼈대 추가
2. `dom.js`에 element 등록
3. `gameResultScreen.js` 탭 전환만 연결
4. CSS 최소 스타일 추가

완료 기준:
- PBP 탭 클릭 시 빈 패널이라도 정상 전환

### Phase 2: 데이터 렌더 MVP
1. `normalizePbp*` 구현
2. summary/list/empty 렌더 구현
3. 기본 정렬(`period ASC, clock DESC, seq ASC`) 적용

완료 기준:
- 서버 mock 없이도 샘플 JSON로 렌더 확인 가능

### Phase 3: 필터/그룹 상호작용
1. period/team/tag 필터 구현
2. group expand/collapse 구현
3. onlyKey toggle 구현

완료 기준:
- 필터 조합 시 즉시 재렌더 + 그룹 상태 유지

### Phase 4: polish
1. badge(lead change/tie/clutch) 스타일
2. 대량 로그(200+) 성능 최적화
3. 접근성/키보드 조작 보완

완료 기준:
- 스크롤/토글 체감 버벅임 없음

---

## 5) 테스트/검수 계획

## 5.1 수동 시나리오
1. PBP 데이터 정상 경기
- 득점/턴오버/파울/타임아웃이 섞인 경기 3개
2. PBP 없음 경기
- `available=false`, `items=[]`
3. OT 경기
- period 라벨이 `OT1`, `OT2`로 표시
4. 극단 경기
- `items` 400+ (더보기/렌더 시간 확인)

## 5.2 프론트 로직 체크리스트
- 탭 전환 후 이전 필터 상태 유지 여부
- 동일 clock 다중 이벤트 seq 순서 보장 여부
- score 누락 item 표시 안전성
- html escape로 description XSS 방지

## 5.3 시각 QA
- 기존 gamecast/grid 파손 없는지
- 모바일(<=1080) 카드 줄바꿈 가독성
- badge/chip 색상 대비(텍스트 대비 4.5:1 목표)

---

## 6) 리스크와 대응
- 리스크 1: 서버 pbp 스키마 변동
  - 대응: `normalizePbp()`에서 강한 방어 파싱 + unknown 필드 무시
- 리스크 2: 이벤트 수 과다로 렌더 지연
  - 대응: chunk 렌더(초기 80개) + 더보기
- 리스크 3: 게임별 event_key 편차
  - 대응: `event_key` 미매핑 시 generic title(`Play`) + description 우선

---

## 7) 구현 완료 정의(Definition of Done)
- [ ] 결과 화면에 PBP 탭이 노출되고, 탭 전환이 안정적으로 동작한다.
- [ ] `play_by_play.available=true` 경기에서 로그가 시간 순서로 표시된다.
- [ ] period/team/tag/핵심이벤트 토글 필터가 동작한다.
- [ ] group 이벤트가 접힘/펼침 가능하다.
- [ ] `available=false` 또는 빈 리스트에서 빈 상태 UI가 깨지지 않는다.
- [ ] 기존 Gamecast/Boxscore/Teamstats 동작 회귀가 없다.

---

## 8) 권장 작업 티켓 분해
1. FE-1: HTML/DOM 탭/뷰 추가
2. FE-2: gameResult 탭 로직 확장
3. FE-3: PBP normalize + list 렌더 MVP
4. FE-4: 필터/그룹 인터랙션
5. FE-5: CSS polish + 반응형
6. FE-6: 회귀 점검 + 샘플 경기 QA

