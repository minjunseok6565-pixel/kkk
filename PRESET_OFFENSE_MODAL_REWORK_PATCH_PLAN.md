# Preset Offense 상세 설정 UI 재구성 패치 계획안

## 목표
- 전술 탭에서 `프리셋 공격 상세 설정` 버튼 클릭 시, 전술 탭 하단 인라인처럼 보이는 구조가 아니라 **앱 내 오버레이 모달**로 명확히 표시되게 만든다.
- 모달 내부 UI를 "플랫 나열"에서 "상위 항목 + 세부 확장(다중 열기 허용)" 구조로 재배치한다.
- 기존 데이터 키/컴파일/검증 로직은 유지하고, **컨트롤 타입(슬라이더 vs 많이/보통/적게)**은 그대로 유지한다.

---

## 확정된 UX 결정사항
- "별도 창"은 브라우저 팝업이 아니라 **앱 내 오버레이 모달**로 구현.
- 상세 열림 방식은 **다중 열기 허용**(여러 섹션 동시 expanded 가능).

---

## 수정 대상 파일 및 변경 상세

## 1) `static/css/screens/tactics.css`

### 1-1. 모달을 확실한 오버레이로 고정
- 현재 `#preset-offense-modal` 하위 스타일만 있고 `.modal/.modal-backdrop/.modal-card`의 보편적인 오버레이 레이아웃 보장이 약하므로, `#tactics-screen` 스코프 안에서 다음 스타일을 명시한다.

추가/정리할 스타일(예시):
```css
#tactics-screen .modal {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: grid;
  place-items: center;
  padding: 16px;
}

#tactics-screen .modal.hidden {
  display: none;
}

#tactics-screen .modal-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(2, 6, 23, 0.58);
  backdrop-filter: blur(2px);
}

#tactics-screen .modal-card {
  position: relative;
  z-index: 1;
  width: min(980px, calc(100vw - 28px));
  max-height: calc(100vh - 28px);
  overflow: hidden;
  display: grid;
  grid-template-rows: auto auto 1fr auto;
}

#tactics-screen .modal-body {
  overflow: auto;
}
```

> 포인트: 기존 오버레이 체감 문제를 구조적으로 해소.

### 1-2. Preset Offense 전용 새 레이아웃 스타일 추가
- 기존 `.preset-offense-grid`, `.preset-offense-toggle-row`를 대체/보완하여 섹션형 UI를 지원.
- 필요 클래스(초안):
  - `.preset-offense-layout`
  - `.preset-offense-summary-grid`
  - `.preset-summary-item`
  - `.preset-offense-sections`
  - `.preset-offense-section`
  - `.preset-offense-section-head`
  - `.preset-offense-section-title`
  - `.preset-offense-section-meta`
  - `.preset-offense-section-body`
  - `.preset-offense-field-grid`
  - `.preset-offense-subgroup`
  - `.preset-offense-subgroup-title`
  - `.preset-offense-section[aria-expanded="false"] .preset-offense-section-body { display: none; }`

### 1-3. 반응형 보정
- 모바일에서 summary grid를 1열, 태블릿 이상 2열로.
- 섹션 내부 슬라이더 라벨/레벨그룹이 줄바꿈되도록 간격 조정.

---

## 2) `static/js/features/tactics/presetOffenseModal.js`

> 핵심 리팩터링 파일. 기존 데이터 경로는 유지하고 렌더 구조만 바꾼다.

### 2-1. 섹션 상태 추가
- 모듈 스코프 상태 추가:
```js
const DEFAULT_OPEN_SECTIONS = {
  pickAndRoll: true,
  drive: false,
  transition: false,
  iso: false,
  postUp: false,
  offball: false,
};

let sectionOpenState = { ...DEFAULT_OPEN_SECTIONS };
```
- `openPresetOffenseModal()` 시 최초 1회 기본값 주입.
- `reset` 시 section 상태는 유지(사용성) 또는 기본 복귀(정책 선택). 본 계획에서는 **유지**.

### 2-2. 상위 요약 카드 렌더 함수 추가
- `renderSummaryCard({ title, controls })` 형태의 헬퍼 추가.
- 상단 요약에서 아래 10개 항목을 표시:
  - 픽앤롤, 드라이브, 속공, 아이솔, 포스트업, 패스 빈도, 오프볼 빈도, 파울 유도, 위험 감수, 템포
- 요약은 "현재 값 미리보기" 용도로만 사용(실제 조작도 가능하게 해도 됨). 본 계획에서는 **상단에서도 조작 가능**하게 구현.

### 2-3. 섹션형 상세 렌더 함수 추가
- 새 헬퍼 함수:
  - `renderSectionShell(sectionKey, title, meta, bodyHtml, expanded)`
  - `renderPickAndRollSection(d, validation)`
  - `renderDriveSection(d, validation)`
  - `renderTransitionSection(d, validation)`
  - `renderIsoSection(d, validation)`
  - `renderPostUpSection(d, validation)`
  - `renderOffballSection(d, validation)`

- 섹션별 구성(컨트롤 타입 고정):
  1. **픽앤롤**
     - 슬라이더: `actionVolume.pnrFamily`, `pnrSplit.pnr`, `pnrSplit.pnp`
     - 레벨/슬라이더 조합:
       - PnR: `outcomes.pnr.handlerDirect`, `outcomes.pnr.rollPass`, `outcomes.pnr.rimVsFloater.*`, `outcomes.pnr.pullupSplit.*`
       - PnP: `outcomes.pnp.handlerDirect`, `outcomes.pnp.popOut`, `outcomes.pnp.rimVsFloater.*`, `outcomes.pnp.pullupSplit.*`
  2. **드라이브**
     - 슬라이더: `actionVolume.drive`
     - 레벨: `outcomes.drive.rim`, `outcomes.drive.kickout`, `outcomes.drive.pull2`
  3. **속공**
     - 슬라이더: `actionVolume.transition`, `outcomes.transitionEarly.directSplit.*`
     - 레벨: `outcomes.transitionEarly.handlerDirect`, `outcomes.transitionEarly.openChance3`
  4. **아이솔**
     - 슬라이더: `actionVolume.iso`, `outcomes.iso.pullupSplit.*`
     - 레벨: `outcomes.iso.rim`, `outcomes.iso.floater`, `outcomes.iso.pullup`, `outcomes.iso.kickout`
  5. **포스트업**
     - 슬라이더: `actionVolume.postUp`
     - 레벨: `outcomes.postUp.postFinish`, `outcomes.postUp.postFadeway`, `outcomes.postUp.pass`
  6. **오프볼**
     - 레벨: `offballFreq`
     - 슬라이더: `offballSplit.cut`, `offballSplit.spotUp`, `offballSplit.dho`
     - 레벨: `outcomes.cut.finish`, `outcomes.cut.pass`

### 2-4. 전역 컨트롤 블록 배치
- `passFreq`, `foulDraw`, `riskTaking`, `tempo`는 상단 summary + 하단 전역 섹션(선택) 중복 배치 가능.
- 본 계획에서는 중복 피하기 위해:
  - `passFreq`, `offballFreq`, `foulDraw`, `riskTaking`, `tempo`는 summary에서 노출하고,
  - 상세 섹션에서는 해당 맥락에서 필요한 항목만 재노출(예: offballFreq는 offball 섹션 내부에서도 노출).

### 2-5. 다중 열기 토글 이벤트 바인딩
- 섹션 헤더 버튼에 `data-preset-section-toggle="pickAndRoll"` 등 부여.
- 클릭 시:
```js
sectionOpenState[key] = !sectionOpenState[key];
renderPresetOffenseModal(currentDraft, validationOrNull);
```
- "다중 열기"이므로 다른 섹션 상태는 변경하지 않음.

### 2-6. 기존 입력 이벤트 재사용
- 현재 `input[data-preset-field]`, `button[data-preset-level]` 이벤트 루프는 그대로 유지.
- 렌더 후 재바인딩 방식도 유지.

### 2-7. 접근성
- 섹션 토글 버튼에 `aria-expanded`, `aria-controls` 부여.
- 섹션 body에는 대응 `id` 부여.
- 키보드 focus 흐름은 기존 모달 trap 로직 유지.

---

## 3) `static/NBA.html`

### 3-1. 마크업 최소 수정 원칙
- 구조적 대공사는 피하고, `#preset-offense-form` 내부를 JS 렌더로 완전 교체하므로 HTML은 최소 수정.
- 권장 변경:
  - 모달 설명 문구를 새 구조에 맞춰 수정.
  - 필요 시 모달 카드에 식별 클래스 추가(예: `preset-offense-modal-card`) 정도만 반영.

예시:
```html
<p id="preset-offense-modal-desc" class="subtitle">상위 전술 항목과 세부 설정을 확장해 프리셋 공격을 조정합니다.</p>
```

---

## 4) `static/js/features/tactics/tacticsScreen.js`

### 4-1. 로직 변경 최소화
- `openPresetOffenseModal()` 흐름은 그대로 유지.
- 단, 적용 완료 메시지를 새 UI 용어에 맞게 미세 조정 가능:
  - "상세 설정이 적용되었습니다..."

> 컴파일/저장 파이프라인은 변경하지 않는다.

---

## 5) (선택) `static/js/app/dom.js`

### 5-1. 필요 시 참조 추가
- 이번 계획은 기존 DOM 참조로 충분하므로 필수 수정 아님.
- 만약 섹션 컨테이너를 외부 요소로 분리하면 DOM id 추가가 필요.

---

## 패치 순서(실행 절차)
1. `tactics.css`에 모달 오버레이 강제 스타일 + 섹션형 UI 스타일 추가.
2. `presetOffenseModal.js` 리팩터링:
   - 섹션 상태
   - summary 렌더
   - 섹션 렌더
   - 토글 이벤트
   - 기존 필드/레벨 이벤트 재연결
3. `NBA.html` 설명 문구 최소 수정.
4. 수동 테스트 및 동작 점검.

---

## 수동 테스트 체크리스트 (패치 직후)

### A. 오버레이 표시 검증
- [ ] 전술 탭에서 `Preset_Offense` 선택 시 버튼 노출.
- [ ] 버튼 클릭 시 화면 중앙 오버레이 + 배경 어둡게 처리.
- [ ] 모달이 전술 카드 하단 흐름을 밀어내지 않음.

### B. 섹션 동작 검증(다중 열기)
- [ ] 픽앤롤/드라이브/속공/아이솔/포스트업/오프볼 섹션 각각 펼침/접힘 가능.
- [ ] 섹션 2개 이상 동시 expanded 가능.
- [ ] 토글 후 다른 섹션 상태가 보존됨.

### C. 필드 타입 검증
- [ ] 슬라이더 항목은 여전히 range 입력.
- [ ] passFreq/offballFreq 및 레벨형 결과 항목은 많이/보통/적게 버튼 유지.

### D. 데이터/검증/저장 검증
- [ ] 값 변경 시 자동조정 규칙 경고가 기존처럼 동작.
- [ ] 적용 클릭 시 draft 상태 반영.
- [ ] 전술 저장 시 payload compile 경로 정상.
- [ ] 저장 후 재진입 시 값 유지.

### E. 접근성/UX 검증
- [ ] ESC 닫기 동작.
- [ ] Tab 포커스 트랩 동작.
- [ ] 닫기 후 트리거 버튼으로 포커스 복귀.

---

## 리스크 및 대응
- 리스크: 렌더 재귀(입력 이벤트 -> render -> 재이벤트)로 인해 성능 저하 가능.
  - 대응: 섹션 수가 제한적이라 우선 허용, 필요 시 이벤트 위임으로 최적화.
- 리스크: 섹션 토글 상태가 렌더마다 초기화될 수 있음.
  - 대응: 모듈 스코프 `sectionOpenState`를 단일 소스로 유지.
- 리스크: 키 이름 변경 유혹(`postFadeway` typo).
  - 대응: 이번 패치에서 키는 유지하고 라벨만 관리.

---

## 완료 기준(Definition of Done)
- 오버레이 모달이 명확히 동작하고, 전술 탭 하단 인라인처럼 보이지 않는다.
- 요청한 상위 10개 항목이 모두 노출된다.
- 요청한 6개 섹션이 클릭 시 확장되고 다중 열기를 지원한다.
- 각 항목의 컨트롤 타입은 기존과 동일하다.
- 기존 compile/validation/save 플로우를 깨지 않는다.
