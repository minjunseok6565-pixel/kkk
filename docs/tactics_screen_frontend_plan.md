# 전술 설정 화면(`tactics-screen`) 프론트엔드 구현 기획안 v2.0

> 목적: 다음 작업에서 `static/NBA.html`, `static/NBA.css`, `static/NBA.js`만 수정하여 **전술 설정 화면 단일 영역**을 상업용 게임 수준으로 개선할 수 있도록, 구현 가능한 상세 명세를 고정한다.

---

## 0) 프로젝트 제약(반드시 준수)

- 수정 허용 파일: `static/NBA.html`, `static/NBA.css`, `static/NBA.js`
- 수정 허용 화면: `#tactics-screen`만
- 타 화면(`start/team/main/schedule/training/standings/college/medical`) DOM, 스타일, 로직 변경 금지
- 신규 API/엔드포인트/백엔드 스키마 추가 금지
- 기존 조회 API 응답 + 프론트 파생 계산만 사용

---

## 1) 현재 화면 핵심 문제와 개선 목표

### 현재 문제
1. 입력 폼 중심 배치로 인해 “게임 전략 툴”이 아니라 “관리용 관리자 폼”처럼 보임
2. 변경 결과(좋아졌는지/나빠졌는지)를 보여주는 피드백이 약함
3. 정보 계층이 낮아 시선 동선이 불명확함
4. 내부 키 네이밍(`PnR_POA_Defender`)이 UI 텍스트로 노출되어 몰입감 저하

### 개선 목표
1. **Command Center 경험**: 한눈에 상태 파악 + 즉시 수정 + 결과 확인
2. **결정 가속화 UI**: 전술/라인업/출전시간을 2~3번 클릭 내 조정
3. **상업용 완성도**: 일관된 디자인 토큰, 인터랙션, 상태 피드백
4. **API 무변경**: 모든 인사이트를 클라이언트 계산으로 제공

---

## 2) UX 구조(최종 레이아웃)

전술 화면은 4개 블록으로 재구성한다.

```
[Tactics Hero Bar]
[Scheme Strip]
[Workbench: Lineup Editor | Insight Panel]
[Roster Drawer]
```

### A. Tactics Hero Bar (상단 고정 요약)
- 좌측: 화면 타이틀 + 서브카피
- 중앙: 현재 공격/수비 스킴 배지, “변경됨” 상태
- 우측: 핵심 KPI
  - 총 출전시간(목표 240)
  - 스타터 평균 분
  - 벤치 평균 분
  - 역할 다양성 지수
- 액션 버튼: `되돌리기`, `변경 저장`

### B. Scheme Strip (스킴 카드 영역)
- `공격 스킴`, `수비 스킴`을 카드형 토글로 개선
- 각 카드: 스킴명(표시 라벨), 요약 설명, 선택 상태
- 선택 변경 시 Workbench/Insight 즉시 갱신

### C. Workbench (핵심 편집 2열)
- 좌측(넓음): 라인업 편집
  - 선발 5 / 로테이션 5를 카드형 행으로 구성
  - 각 행 컬럼: 선수, 공격 역할, 수비 역할, 출전시간, 상태
- 우측(좁음): 인사이트 패널
  - 전술 적합도(공격/수비)
  - 포지션/역할 커버리지
  - 경고 피드(중복/과부하/시간합 불일치)
  - 이번 변경 영향 요약 3줄

### D. Roster Drawer (하단)
- 기본 접힘 + 필요시 펼침
- 로스터 목록 + 필터(가능하면 포지션/역할 기준)
- 선수 선택 시 라인업 슬롯에 즉시 적용 가능

---

## 3) 화면별 세부 명세 (HTML 구조 기준)

## 3-1. `#tactics-screen` 하위 DOM 권장 구조

```html
<section id="tactics-screen">
  <header class="tactics-hero">...</header>
  <section class="tactics-schemes">...</section>
  <section class="tactics-workbench">
    <div class="tactics-lineup-panel">...</div>
    <aside class="tactics-insight-panel">...</aside>
  </section>
  <section class="tactics-roster-drawer">...</section>
</section>
```

### id/class 규칙
- 기존 JS가 참조하는 `id`는 유지 우선
- 신규 스타일/구조는 `tactics-*` 네임스페이스 사용
- 타 화면 공통 클래스 오염 금지(전역 선택자 최소화)

---

## 4) 컴포넌트 스펙

## 4-1. Hero KPI Chip
- 요소: 라벨 + 숫자 + 상태점
- 상태
  - normal: 중립색
  - warn: 목표 이탈(예: 총합 240 아님)
  - good: 기준 충족

## 4-2. Scheme Card
- 상태: default / hover / selected / disabled
- 카드 내부: 이름, 한줄 설명, 선택 체크 아이콘
- 키보드 포커스 제공 (`:focus-visible`)

## 4-3. Lineup Row Card
- 높이 64~72px
- 5열 그리드(선수/공역할/수역할/MIN/상태)
- 행 상태 배지
  - OK / WARN / ERROR
- 변경 시 600ms 강조 애니메이션

## 4-4. Insight Blocks
- `Tactical Fit` (공/수 게이지)
- `Role Coverage` (중복/공백 표시)
- `Warnings` (최대 5개 우선순위 노출)
- `Impact Summary` (이번 변경 영향 3줄)

## 4-5. Toast/Inline Alert
- 기존 `alert()` 지양
- 우상단 토스트 + 행 인라인 메시지 병행

---

## 5) 데이터 사용 규칙 (신규 API 금지 대응)

## 5-1. 재사용 데이터
- 팀/로스터/선수 목록
- 현재 선택된 공격/수비 스킴
- 선발/로테이션 슬롯 정보
- 각 슬롯의 역할/출전시간

## 5-2. 프론트 파생 계산(필수)

1. `totalMinutes`
- 모든 슬롯 출전시간 합

2. `minutesDelta`
- `240 - totalMinutes`

3. `starterAvg` / `benchAvg`
- 그룹 평균 분

4. `roleDiversity`
- 고유 공격역할 수 / 전체 슬롯 수

5. `defenseRoleConflictCount`
- 중복 수비역할 수

6. `warningList`
- 규칙 기반 생성
  - 분배합 불일치
  - 역할 과밀/공백
  - 특정 유닛(벤치) creator 부족 등

---

## 6) 비주얼 디자인 시스템

## 6-1. 컬러 토큰 (전술 화면 전용)
- `--t-bg-0: #0B1220`
- `--t-bg-1: #121C2F`
- `--t-bg-2: #1A2740`
- `--t-line: #2A3B59`
- `--t-text-strong: #F2F6FF`
- `--t-text-muted: #9FB0CD`
- `--t-accent: #4DA3FF`
- `--t-accent-off: #FF8A4C`
- `--t-accent-def: #5EC8FF`
- `--t-ok: #35D08A`
- `--t-warn: #F5B94C`
- `--t-err: #F16363`

## 6-2. 타이포 스케일
- Hero Title: 28/32, 700
- Section Title: 18/24, 700
- Body: 14/20, 500
- Caption: 12/16, 500
- 숫자 표시: `font-variant-numeric: tabular-nums`

## 6-3. 간격/모서리/그림자
- spacing: 4/8/12/16/24/32
- radius: 10(컨트롤), 16(카드)
- shadow: 2단계 제한(카드/팝업)

---

## 7) 인터랙션 규칙 (JS 구현 기준)

1. 스킴 변경
- 선택 즉시 상태 반영
- 충돌 역할 자동 교정(가능 시) + 토스트 안내

2. 출전시간 조정
- 입력값 clamp: 0~48
- 합계/경고 실시간 반영
- 240 미달/초과 시 Hero KPI 경고

3. 역할 변경
- 중복 금지 규칙 유지
- 위반 시 인라인 에러 + 기존 값 복귀/대체

4. 변경 추적
- 최초 로드 스냅샷 저장
- 변경 시 “변경됨” 배지 점등
- `되돌리기`로 스냅샷 복원

5. 접근성/입력성
- 키보드 탭 순서 보장
- select/input/button 포커스 링 통일

---

## 8) 표시 텍스트 정제 규칙(몰입감 강화)

- 내부 키는 사용자 표시 라벨로 매핑해서 출력
  - 예: `PnR_POA_Defender` → `POA PnR Defender`
- 한영 혼용 금지: 화면 단위 언어 정책 통일(권장: 한국어 기본 + 고유 농구용어 영문 보조)
- 문구 예시
  - `공격 스킴: Heavy PnR`
  - `예상 출전시간 합계: 240분`
  - `수비 역할이 중복되어 커버리지가 낮아질 수 있습니다`

---

## 9) 구현 순서 (실제 작업용)

### Phase 1 — 구조/스타일 기반 공사
- HTML 구조를 Hero/Scheme/Workbench/Roster로 재배치
- CSS 토큰 + 카드/배지/칩/행 스타일 구축
- 타 화면 영향 없는 범위로 선택자 스코프 제한

### Phase 2 — 상태 계산/피드백
- JS 파생 계산 함수 추가
- KPI/경고/영향 요약 렌더링 연결
- alert 제거 및 인라인/토스트 피드백 도입

### Phase 3 — 폴리싱
- hover/focus/transition 정교화
- 숫자 정렬/행 간격/밀도 미세조정
- 1920x1080 기준 시선 동선 최종 점검

---

## 10) 완료 기준(Definition of Done)

### 기능
- 기존 전술 편집 기능 정상 동작(선수/역할/시간)
- 수비 역할 중복 제약 유지
- 총 출전시간 계산 정확

### UI/UX
- 전술 화면만 고급화, 타 화면 시각 변화 없음
- 핵심 정보(스킴/라인업/시간/경고) 1스크린 내 파악 가능
- “폼” 느낌이 아닌 게임 전술 보드 느낌 달성

### 기술
- 콘솔 에러 0
- 기존 화면 전환 흐름 정상
- 기존 API 호출 경로 무변경

---

## 11) 다음 작업 시작 시 체크리스트

1. `NBA.html`: `#tactics-screen` 영역만 구조 재배치했는가?
2. `NBA.css`: `tactics-*` 스코프 안에서만 스타일 추가/수정했는가?
3. `NBA.js`: 기존 API 호출을 건드리지 않고 파생 계산만 추가했는가?
4. 타 화면에 클래스 충돌/스타일 누수 없는가?
5. 240분, 역할 중복, 변경 추적 UI가 즉시 반영되는가?

이 체크리스트를 모두 통과하면, 다음 단계에서 실제 구현을 진행해도 안전하다.
