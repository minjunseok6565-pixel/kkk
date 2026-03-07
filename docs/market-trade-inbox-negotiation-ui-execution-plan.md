# Market 제안/협상 UI 상용화 구현 설계 (Frontend-first, Backend SSOT)

## 0. 목표/전제

- **목표**: 시장 > 제안/협상 탭과 협상 모달을 상용 서비스 수준 UX로 재설계하고, 백엔드 SSOT 데이터를 왜곡 없이 시각화한다.
- **전제**:
  - 데이터 계약의 기준은 **백엔드 응답(SSOT)** 으로 고정한다.
  - 프론트는 SSOT 형태를 해석/정규화해서 렌더링한다.
  - 초기 제안 패키지는 **협상 화면 최초 진입 시점의 시작점 안내용**으로 표시하고,
    이후 협상이 진행되면 `last_offer` 갱신값 기준으로 패키지 표시를 덮어쓴다.

---

## 1. 변경 대상 파일(예정) 및 역할

### 1) `static/NBA.html`

- 제안/협상 탭 리스트 아이템용 마크업 템플릿 컨테이너 구조 재정비.
- 협상 모달 내부 레이아웃을 3열 편집기에서 아래 구조로 개편:
  - 상단 헤더: 좌(상대 로고/팀명) · 우(우리 로고/팀명)
  - 상단 패키지 영역: 상대 제안 패키지(좌/우)
  - 하단 에셋 영역: 팀별 에셋 패널(탭: 선수/픽/스왑/고정 자산)
  - 하단 액션 바: 거절/제출/닫기
- 접근성(ARIA):
  - 탭 role/aria-selected/aria-controls
  - 패키지 영역 live region 최소화(불필요한 과도 announce 방지)

### 2) `static/css/screens/market.css`

- trade inbox 카드형 UI 스타일 신규 정의:
  - 좌측 팀 로고
  - 중앙 2열 텍스트 패키지(우리 팀이 내줄 자산 vs 상대가 줄 자산)
  - 우측 날짜 + 버튼 스택
- deal modal 상용 퀄리티 스타일 신규 정의:
  - 폭이 넓은 직사각형 모달, max-width/height, 내부 스크롤 전략
  - 상단 제안 패키지 카드(강조, 배경 대비, 라벨 명확화)
  - 하단 팀별 탭/리스트/선택 상태(hover/focus/active)
  - 모바일/저해상도 반응형(열 축소, 세로 스택)
- 디자인 토큰 준수:
  - 기존 버튼/색상 체계와 일관성 유지
  - 글자 대비(AA 이상), 클릭 타깃 최소 높이 36px 이상

### 3) `static/js/features/market/marketScreen.js`

핵심 변경 파일. 기능 단위로 분해:

#### A. SSOT 정규화 계층 추가

- `normalizeInboxOfferDeal(row)`
  - `row.offer.deal` 우선
  - fallback: `row.offer.offer`, `row.offer` 형태 안전 파싱
- `normalizeSessionDeal(session)`
  - 우선순위: `session.draft_deal` > `session.last_offer.deal` > `session.last_offer.offer` > `session.last_offer`
  - `teams/legs` 구조 검증
- `normalizeTradeAssetsSnapshot(summary.db_snapshot.trade_assets)`
  - 백엔드 map/dict 형태를 배열로 변환
  - draft_picks / swap_rights / fixed_assets 전부 대응

#### B. 제안(인박스) 카드 렌더 고도화

- 세션/상태 중심 텍스트 제거 → 제안 내용 중심 카드 렌더링
- 카드 표시 요소:
  - 상대 팀 로고
  - "상대가 원하는 우리 에셋" 목록
  - "상대가 주는 에셋" 목록
  - 제안일(또는 최신 업데이트일)
  - 협상/거절 버튼
- 에셋 라벨링 유틸:
  - 선수: `풀네임 (POS) · 연봉`
  - 픽: `2026 1라운드 픽 (팀)` + 보호 여부 뱃지
  - 스왑/고정 자산도 사람이 읽을 텍스트화

#### C. 협상 모달 레이아웃/상호작용 재설계

- 최초 진입 시점:
  - 초기 제안 패키지 스냅샷 저장 (`state.marketTradeInitialOfferSnapshot`)
  - 상단 패키지 영역에 표시
- 이후 커밋/카운터 응답 시:
  - 최신 `last_offer` 기준으로 패키지 갱신 가능
  - 사용자 요구대로 덮어쓰기 허용
- 하단 에셋 패널:
  - 팀별 탭 상태 관리(`player | pick | swap | fixed_asset`)
  - 탭 클릭 시 해당 자산 목록만 렌더
  - 자산 클릭 시 상단 패키지로 이동(= draft legs 토글)

#### D. 픽 UX 규칙 반영

- 보호픽 라벨링:
  - `pick.protection != null`이면 보호픽 배지 표시
- 보호 버튼 노출 조건:
  - `owner_team == original_team`인 픽만 보호 조건 입력 활성화
  - 그 외 픽은 읽기 전용(기존 보호조건만 표시)
- 보호조건 편집:
  - 기존 JSON 입력 UX는 유지하되 상용 UI용 단순화 옵션 병행(Top-N selector)
  - 내부 저장은 SSOT 규칙에 맞는 protection payload로 변환

#### E. 스왑 목록 UX 고도화

- 보유 스왑권(`swap_rights`) 목록 표기
- 생성 가능 스왑 후보 계산(프론트 계산):
  - 동일 year/round pick pair 탐색
  - from_team이 적어도 한쪽 pick owner인 경우만 후보 생성
  - 기존 swap_rights와 중복 제거
- 후보 스왑과 확정 스왑을 시각적으로 구분(“생성 가능” 뱃지)

#### F. 상태관리/에러처리

- `NEGOTIATION_ENDED_BY_AI`, `NEGOTIATION_NOT_ACTIVE`, `PROTECTION_CONFLICT` 등
  사용자 친화 메시지 정리
- optimistic UI 최소화(상업용 안정성 우선), 실패 rollback 철저
- 모달 진입/종료 시 상태 초기화 규칙 명확화

### 4) `static/js/app/state.js`

- 신규 UI 상태 필드 추가:
  - inbox 카드 확장 상태(필요 시)
  - 모달 팀별 현재 탭
  - 초기 제안 스냅샷
  - 최신 제안 스냅샷

### 5) `static/js/app/dom.js`

- HTML에서 추가되는 DOM id 참조 확장:
  - 상단 패키지 좌/우 컨테이너
  - 팀별 탭 버튼 그룹
  - 탭별 자산 리스트 컨테이너
  - 로고 영역 엘리먼트

### 6) `static/js/core/constants/teams.js` (필요 시)

- 현재 로고 유틸 재사용 중심.
- 필요 시 렌더 도우미(로고 + 팀명 템플릿) 경량 확장.

### 7) `static/js/core/format.js` (필요 시)

- 에셋 라벨링용 포맷 함수 추가 검토:
  - 픽 텍스트 포맷(`YYYY 1st/2nd round pick`)
  - 보호조건 요약 텍스트 포맷

### 8) 백엔드 파일 (원칙적으로 비수정)

- 이번 작업의 원칙은 프론트 정합화이므로 API 계약 변경은 하지 않는다.
- 단, 실제 구현 중 치명적 계약 모호성이 확인되면 별도 이슈로 분리:
  - `last_offer` payload shape 표준화 제안
  - inbox row에 canonical deal 필드 노출 보강 제안

---

## 2. 데이터 매핑 설계 (SSOT -> UI)

### 인박스 row 기준

- 입력: `/api/trade/negotiation/inbox` `rows[]`
- 사용 필드:
  - `other_team_id`, `created_at`, `updated_at`, `status`, `phase`
  - `offer.deal` (또는 fallback shape)
- 출력:
  - 카드 좌/중/우 영역 렌더 데이터

### 협상 모달 기준

- 입력:
  - `/api/trade/negotiation/open`의 `session`
  - `/api/team-detail/{team_id}` 양팀 roster
  - `/api/state/summary`의 trade_assets
- 처리:
  - `session`에서 base deal 정규화
  - trade_assets map->array 변환
  - roster join으로 player_id -> 풀네임/샐러리 해석

---

## 3. UI/UX 품질 기준 (상업 출시 기준)

- **가독성**: 정보 위계(팀/제안패키지/액션) 명확화
- **일관성**: 기존 버튼/타이포/컬러 시스템 일치
- **접근성**:
  - 키보드 탭 이동 가능
  - 포커스 링 보장
  - 탭 ARIA 준수
- **반응형**:
  - 1280px 이상: 2열/3영역 풍부 UI
  - 1024px 이하: 카드 간격 축소
  - 모바일: 수직 스택 + 고정 액션 바
- **성능**:
  - 대량 리스트 렌더 최소화
  - 필요 시 문자열 템플릿 -> document fragment로 개선

---

## 4. 구현 순서(권장, 파일 단위 상세)

1. **DOM 골격 재설계 (마크업 선반영)**
   - 수정 파일: `static/NBA.html`
   - 작업:
     - 인박스 카드(좌 로고/중앙 패키지/우 날짜+버튼) 렌더를 위한 컨테이너/서브영역 id 추가.
     - 협상 모달을 상단(로고+초기/최신 패키지) + 하단(팀별 탭 에셋) 구조로 재배치.
     - 탭 및 패널에 `role="tablist"`, `role="tab"`, `aria-selected`, `aria-controls` 부여.

2. **DOM 바인딩 확장 (JS가 새 마크업 참조 가능하도록 준비)**
   - 수정 파일: `static/js/app/dom.js`
   - 작업:
     - 인박스 카드 서브영역, 모달 상단 패키지 좌/우, 팀별 탭 버튼/리스트 DOM 참조 추가.
     - 기존 id와 충돌 없는 네이밍으로 통일 (`market-trade-*`).

3. **화면 상태 모델 확장 (모달/탭/스냅샷 상태 정의)**
   - 수정 파일: `static/js/app/state.js`
   - 작업:
     - `marketTradeInitialOfferSnapshot`, `marketTradeLatestOfferSnapshot` 상태 추가.
     - 팀별 탭 상태(`myTeamAssetTab`, `otherTeamAssetTab`) 추가.
     - 모달 오픈/클로즈 시 초기화 규칙(세션 변경 시 reset, 동일 세션 reopen 시 유지 범위) 정의.

4. **디자인 시스템 적용 (상용 품질 UI 스타일 구현)**
   - 수정 파일: `static/css/screens/market.css`
   - 작업:
     - 인박스 카드형 레이아웃/타이포/버튼/로고/패키지 리스트 스타일 추가.
     - 협상 모달 대형 레이아웃(상단 패키지 + 하단 탭 패널) 스타일 추가.
     - 포커스/호버/선택 상태, 스크롤 영역, 반응형 브레이크포인트(1366/1024/768) 정의.

5. **팀 브랜딩 렌더 보조 확장 (필요 시 최소 변경)**
   - 수정 파일: `static/js/core/constants/teams.js` *(필요 시)*
   - 작업:
     - 로고+팀명 마크업 생성 헬퍼를 보강하거나 기존 `renderTeamLogoMark` 재활용 경로 정리.

6. **표시 포맷 유틸 추가 (에셋 텍스트 상용화)**
   - 수정 파일: `static/js/core/format.js` *(필요 시)*
   - 작업:
     - 픽 라벨(`2026 1st round pick`) 및 보호조건 요약 텍스트 포맷 유틸 추가.
     - 스왑/고정자산 사용자 친화 문자열 포맷 함수 추가.

7. **SSOT 정규화 계층 구현 (핵심 데이터 파싱)**
   - 수정 파일: `static/js/features/market/marketScreen.js`
   - 작업:
     - `normalizeInboxOfferDeal(row)` 구현 (`offer.deal` 우선 + fallback).
     - `normalizeSessionDeal(session)` 구현 (`draft_deal`/`last_offer.*` 다형성 흡수).
     - `normalizeTradeAssetsSnapshot(summary.db_snapshot.trade_assets)` 구현 (map/dict → array).

8. **인박스 카드 렌더 교체 (제안 내용 중심 UI)**
   - 수정 파일: `static/js/features/market/marketScreen.js`
   - 작업:
     - `renderMarketTradeInbox()`를 카드형 렌더로 교체.
     - 카드 내 양측 패키지(우리팀이 줄 자산/상대팀이 줄 자산) 텍스트 생성.
     - 날짜 표기 정책(`created_at` 우선, 없으면 `updated_at`) 및 CTA 배치 반영.

9. **협상 모달 상단 패키지 영역 구현 (초기 진입 스냅샷 + 최신 덮어쓰기)**
   - 수정 파일: `static/js/features/market/marketScreen.js`
   - 작업:
     - 세션 최초 오픈 시 초기 패키지 스냅샷 저장/표기.
     - 협상 진행 후 `last_offer` 갱신 시 최신 패키지로 덮어쓰기.
     - 상단 좌/우 패키지에 선수/픽/스왑/고정자산 텍스트 표기.

10. **하단 탭형 에셋 패널 구현 (팀별 자산 전환/선택)**
    - 수정 파일: `static/js/features/market/marketScreen.js`
    - 작업:
      - 팀별 탭(선수/픽/스왑/고정자산) 전환 상태 및 렌더 구현.
      - 에셋 클릭 시 draft legs 반영(상단 패키지 동기화).
      - roster/team-detail/state-summary 결합하여 실명/연봉/자산 메타 노출.

11. **픽/스왑/고정자산 상세 규칙 반영**
    - 수정 파일: `static/js/features/market/marketScreen.js`
    - 작업:
      - 보호픽 뱃지 표기, `owner_team == original_team`일 때만 보호 편집 활성화.
      - 생성 가능한 스왑 후보 계산 및 기존 `swap_rights`와 구분 표기.
      - 고정자산 라벨을 `asset_id` 중심에서 사용자 친화 텍스트로 개선.

12. **에러 UX/회귀 안정화 및 연결 점검**
    - 수정 파일: `static/js/features/market/marketScreen.js`, `static/js/core/api.js`
    - 작업:
      - 주요 에러 코드별 사용자 메시지 정교화.
      - 낙관적 갱신 실패 시 롤백/재조회 루틴 점검.
      - API 호출 순서(인박스→open→summary/team-detail) 실패 내구성 강화.

13. **최종 QA & 릴리즈 체크**
    - 점검 파일: `static/NBA.html`, `static/css/screens/market.css`, `static/js/app/dom.js`, `static/js/app/state.js`, `static/js/core/constants/teams.js`, `static/js/core/format.js`, `static/js/features/market/marketScreen.js`, `static/js/core/api.js`
    - 작업:
      - 기능/접근성/반응형/성능 회귀 검증.
      - 스크린샷 캡처 및 문서(체크리스트) 업데이트.

---

## 5. 테스트/검증 계획

### 기능 검증

- 인박스 카드:
  - 제안 0건/1건/다건
  - 팀별 그룹 정렬
  - 협상/거절 동작
- 모달:
  - 최초 진입 초기 패키지 노출
  - 이후 제안 제출/응답 후 패키지 갱신
  - 탭 전환(선수/픽/스왑/고정자산)
  - 에셋 클릭 시 패키지 반영/해제

### 규칙 검증

- 보호픽 버튼 노출 조건(`owner_team == original_team`) 검증
- 보호조건 충돌 시 사용자 메시지 검증
- 생성 가능 스왑 후보 계산 결과 검증

### 품질 검증

- 반응형 뷰포트(1366, 1024, 768)
- 키보드 접근성(tab/focus)
- 긴 선수명/다수 자산 overflow 처리

---

## 6. 리스크 및 대응

- **리스크 1**: `last_offer` 형태 다양성으로 패키지 파싱 실패
  - 대응: 정규화 함수 + 방어적 fallback + 로깅
- **리스크 2**: trade_assets map/array 혼재
  - 대응: map/array 모두 수용하는 단일 정규화 계층
- **리스크 3**: UI 복잡도 증가로 유지보수 어려움
  - 대응: 렌더 함수 분리, 작은 순수 유틸 중심 구조
- **리스크 4**: 상업용 품질 기준 미달
  - 대응: 디자인 리뷰 체크리스트/접근성 체크리스트 포함

---

## 7. 산출물 정의

- 코드 산출물:
  - 재설계된 inbox 카드 UI
  - 재설계된 협상 모달 UI
  - SSOT 정합 정규화 계층
  - 탭 기반 에셋 선택 UX + 보호픽/스왑 규칙 반영
- 문서 산출물:
  - 본 실행 설계 문서
  - 구현 후 QA 체크리스트(별도 md)

