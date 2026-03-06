# 내 팀 트레이드 블록 탭 + 등록/해제 구현 계획 (보강본)

> 범위 고정: **기존 "다른 팀 트레이드 블록 조회" 기능은 유지**하고,
> 이번 작업은 **내 팀 탭 추가 / 탭 전환 / 등록 / 해제** 구현에 집중한다.

## 1) 목표(이번 스코프)
- 트레이드 블록 화면 내부를 `다른 팀` / `내 팀` 2개 뷰로 분리한다.
- 기본 진입은 기존과 동일하게 `다른 팀` 뷰를 먼저 보여준다.
- 버튼으로 `내 팀` 뷰 전환 가능해야 한다.
- `내 팀` 뷰에서 `등록` 버튼으로 내 팀 로스터 선수를 트레이드 블록에 올릴 수 있어야 한다.
- `내 팀` 뷰에서 등록된 선수를 `해제`할 수 있어야 한다.

---

## 2) 파일별 작업 계획 (무엇을, 왜 바꾸는지)

## A. `static/NBA.html`
### 해야 할 작업
1. `market-panel-trade-block` 내부에 **2차 전환 UI** 추가
   - 버튼: `다른 팀`, `내 팀`
   - 접근성 속성(`aria-selected`, `aria-hidden`) 포함
2. 기존 목록 영역을 분리
   - `다른 팀 목록 영역`(기존 테이블 재사용)
   - `내 팀 목록 영역`(신규 테이블: 액션 컬럼에 `해제` 버튼)
3. `내 팀` 영역 상단에 `등록` 버튼 추가
4. `등록 모달` 마크업 추가
   - 로스터 리스트
   - 선택 상태 표시
   - 취소/등록 확정 버튼

### 의도
- 레이아웃/동작의 기준점이 되는 정적 구조를 먼저 확정해서 JS 작업 시 id 충돌/누락을 방지.

---

## B. `static/js/app/dom.js`
### 해야 할 작업
1. 위에서 추가한 id를 `els`에 바인딩
   - 예시
     - `marketTradeBlockScopeOtherBtn`
     - `marketTradeBlockScopeMineBtn`
     - `marketTradeBlockOtherPanel`
     - `marketTradeBlockMinePanel`
     - `marketTradeBlockMineBody`
     - `marketTradeBlockRegisterBtn`
     - `marketTradeBlockRosterModal` 등
2. 기존 네이밍 규칙(`marketTradeBlock...`) 유지

### 의도
- 화면 요소를 중앙에서 관리해 이벤트 누락과 null 접근 에러를 줄임.

---

## C. `static/js/app/state.js`
### 해야 할 작업
1. 트레이드 블록 내부 뷰 상태 추가
   - `marketTradeBlockScope: "other" | "mine"` (기본값 `other`)
2. 내 팀 데이터 상태 추가
   - `marketTradeBlockMyRows`
3. 등록 모달 관련 상태 추가
   - `marketTradeBlockRosterRows`
   - `marketTradeBlockSelectedRosterPlayerId`
   - `marketTradeBlockRosterModalOpen`

### 의도
- 화면 전환/등록 흐름에서 필요한 최소 상태를 명시적으로 분리해 유지보수성을 높임.

---

## D. `static/js/features/market/marketScreen.js` (핵심)
### 해야 할 작업
1. **기존 다른 팀 목록 로직은 최대한 유지**
   - 현재 동작 중인 `GET /api/trade/block` 기반 로직은 회귀 없이 재사용
2. 스코프 전환 함수 추가
   - `switchTradeBlockScope(scope)`
   - 버튼 active 상태 및 패널 aria 속성 토글
3. 내 팀 목록 조회 함수 추가
   - `loadTradeBlockMineList()`
   - API: `GET /api/trade/block?team_id={selectedTeamId}&active_only=true&visibility=PUBLIC...`
4. 내 팀 목록 렌더 함수 추가
   - `renderTradeBlockMineRows(rows)`
   - 각 행 `해제` 버튼 클릭 시 unlist 호출
5. 등록 모달 제어 함수 추가
   - `openTradeBlockRosterModal()` / `closeTradeBlockRosterModal()`
   - 로스터 조회 + 이미 등록된 선수 비활성 처리
6. 등록 액션 함수 추가
   - `listPlayerToTradeBlock(playerId)`
   - API: `POST /api/trade/block/list`
7. 해제 액션 함수 추가
   - `unlistPlayerFromTradeBlock(playerId)`
   - API: `POST /api/trade/block/unlist`
8. 성공 후 후처리
   - 내 팀 목록 즉시 재조회
   - 필요 시 다른 팀 목록 캐시 무효화 또는 조건부 재조회

### 의도
- 변경 영향 범위를 market 화면 모듈로 집중시켜 회귀 위험을 통제.

---

## E. `static/js/app/events.js`
### 해야 할 작업
1. 신규 UI 이벤트 바인딩
   - 내/외부 스코프 전환 버튼
   - 등록 버튼
   - 등록 모달 닫기/확정 버튼
2. 클릭 전파 처리
   - 행 클릭(선수 상세) vs `해제` 버튼 클릭 충돌 방지 (`stopPropagation`)

### 의도
- 이벤트 연결 위치를 일원화해 누락 없이 동작하게 함.

---

## F. `static/css/screens/market.css` (필요 시)
### 해야 할 작업
1. 트레이드 블록 내부 스코프 버튼/내 팀 헤더/모달 스타일 보강
2. 작은 화면에서 버튼/테이블/모달 레이아웃 깨짐 방지

### 의도
- 기능 추가로 인한 UI 붕괴 방지(가독성/클릭 타깃 확보).

---

## 3) API 연결 기준 (이번 구현에서 확정할 계약)

### 3-1. 내 팀 목록 조회
- `GET /api/trade/block`
- 파라미터
  - `team_id={selectedTeamId}`
  - `active_only=true`
  - `visibility=PUBLIC`
  - `limit=300`
  - `sort=priority_desc`

### 3-2. 등록
- `POST /api/trade/block/list`
- body
  - `team_id`: 현재 사용자 팀
  - `player_id`: 모달에서 선택한 선수
  - 선택: `priority`, `reason_code`

### 3-3. 해제
- `POST /api/trade/block/unlist`
- body
  - `team_id`
  - `player_id`
  - 선택: `reason_code`

### 3-4. 에러 처리 규칙
- API 실패 시 사용자 친화 메시지(alert/토스트)로 변환
- `removed=false`는 UX 상 성공 메시지로 처리(이미 해제된 상태 허용)

---

## 4) 작업 순서 (실행용)

### Step 1. 뼈대(HTML + DOM + state)
- `NBA.html`에 내 팀 탭/버튼/모달 골격 추가
- `dom.js`에 id 바인딩 추가
- `state.js`에 신규 상태 추가

**완료 체크**
- 콘솔 null 참조 없음
- 트레이드 블록 화면 진입 시 렌더 깨짐 없음

### Step 2. 내 팀 탭 전환 로직
- `marketScreen.js`에 `switchTradeBlockScope` 구현
- `events.js`에 전환 버튼 클릭 연결

**완료 체크**
- 버튼 클릭 시 다른 팀/내 팀 패널 전환 정상
- 기본값 `other` 유지

### Step 3. 내 팀 목록 조회/렌더
- `loadTradeBlockMineList` + `renderTradeBlockMineRows` 구현
- 내 팀 탭 최초 진입 시 목록 조회

**완료 체크**
- 내 팀 등록 선수 목록이 화면에 표시됨
- 빈 목록 문구 정상 노출

### Step 4. 등록 모달 + 등록 API
- 등록 버튼 -> 모달 오픈
- 로스터 조회 후 선수 선택
- `POST /api/trade/block/list` 호출

**완료 체크**
- 등록 성공 후 모달 닫힘 + 내 팀 목록 즉시 갱신
- 이미 등록된 선수 중복 등록 UX 방어 동작

### Step 5. 해제 API
- 내 팀 행의 `해제` 버튼 클릭 시 `POST /api/trade/block/unlist`

**완료 체크**
- 해제 성공 후 목록에서 즉시 제거됨
- 행 클릭(상세 이동)과 버튼 클릭 충돌 없음

### Step 6. 마무리 QA
- 재진입/새로고침 후 상태 일관성 확인
- 로딩/오류 메시지/비활성 상태 확인
- 필요 시 스타일 보정

---

## 5) 리스크 및 대응
- **리스크:** 내 팀 목록 API 응답 필드가 기존 다른 팀 목록과 다를 가능성
  - **대응:** mine 렌더러에서 필요한 필드를 안전 파싱(`num`, 기본값) 처리
- **리스크:** 등록 모달에서 로스터 소스 API 선택 불명확
  - **대응:** 기존 my-team에서 쓰는 로스터 조회 경로 재사용 후 market 전용 최소 매핑
- **리스크:** 전환 상태 캐시로 인해 오래된 목록 표시
  - **대응:** 등록/해제 직후 mine 재조회 강제, 탭 재진입 시 조건부 리프레시

---

## 6) 완료 기준 (DoD)
- 트레이드 블록 탭 기본 화면은 기존대로 `다른 팀` 목록이다.
- 버튼으로 `내 팀` 목록 전환이 가능하다.
- `내 팀` 화면에서 `등록`으로 선수 선택 후 실제 등록된다.
- `내 팀` 화면에서 `해제`가 실제 반영된다.
- 등록/해제 후 화면 데이터와 서버 데이터가 일치한다.
