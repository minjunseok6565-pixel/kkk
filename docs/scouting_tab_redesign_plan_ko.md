# 스카우팅 탭 개편 구상안 (기획 단계)

## 1) 현재 구조 요약 및 문제 정의

- 현재 UI는 `스카우터` + `대상 선수`를 각각 드롭다운으로 고른 뒤 배정/해제하는 단일 폼 구조다.
- 스카우팅 리포트는 우측 테이블에 일괄 표시되며, 스카우터별/읽음 여부 중심의 UX는 없다.
- 배정 API는 "스카우터당 ACTIVE 배정 1개"를 강하게 제한한다.

### 확인된 원인(중복 배정 오류 체감의 실제 원인)

프론트에서 스카우터를 바꿔 다른 선수를 배정하려고 할 때 "중복 배정 불가"처럼 보이는 핵심 원인은 아래 조합이다.

1. **서버 정책 자체가 스카우터당 ACTIVE 1개만 허용**
   - `/api/scouting/assign`에서 동일 `team_id+scout_id`의 ACTIVE assignment가 있으면 409를 반환한다.
2. **DB도 동일 정책을 partial unique index로 강제**
   - `uq_scouting_active_assignment_per_scout`가 `team_id, scout_id` + `status='ACTIVE'` 조합을 unique로 묶는다.
3. **현 프론트 UX가 사전 가드/설명 없이 바로 assign 요청**
   - 배정 버튼 핸들러는 현재 ACTIVE 여부를 사전 안내 없이 바로 POST하고, 실패 시 메시지 컨텍스트가 빈약해 "다른 스카우터 배정도 막히는 것처럼" 인지될 수 있다.

즉, 현재 구조에서 실제 제한은 "다른 스카우터에게 다른 선수"가 아니라, "이미 배정된 동일 스카우터 재배정"이다. 다만 UI가 이를 명확히 드러내지 못해 사용자 체감 오류로 보인다.

---

## 2) 목표 UX (요청사항 반영)

스카우팅 탭 진입 시 7명의 스카우터를 카드로 동시에 노출한다.

각 카드 구성:
- 상단: 스카우터 프로필(이름, 전문분야, 스타일 태그, 현재 배정 상태)
- 중단: **선수 선택 버튼**
- 하단: **스카우팅 리포트 버튼**

동작:
- 선수 선택 버튼 클릭 → 해당 스카우터 전용 "선수 선택 패널/모달" 오픈
  - 검색 인풋 + 결과 리스트(가상 스크롤 또는 페이지네이션 권장)
  - 현재 해당 스카우터 배정 선수 표시, 다른 선수 선택 시 교체 정책 안내
- 스카우팅 리포트 버튼 클릭 → 해당 스카우터 관련 리포트 목록 표시
  - 최신순 정렬
  - 신규 리포트가 있으면 버튼 배지(● 또는 숫자) 표시

---

## 3) 정보 구조(IA) 제안

### A. 기본 레이아웃
- 상위: `Scouting Overview Header`
  - "활성 배정 7/7", "미확인 리포트 n건" 등 요약 KPI
- 본문: `Scout Card Grid` (desktop 3~4열 / tablet 2열 / mobile 1열)
- 보조 패널(우측 드로어 or 모달):
  - `Player Picker Panel`
  - `Reports Panel`

### B. 카드 단위 상태
카드마다 아래 로컬 상태를 가진다.
- `assignedPlayer` (없으면 "미배정")
- `isBusy` (배정 API 요청 중)
- `unreadReportCount`
- `lastReportAt`

전역 상태는 최소화:
- `scouts[]`, `playersIndex`, `reportsByScout`, `unreadByScout`

---

## 4) 프론트엔드 컴포넌트 설계안 (현 UI 컨셉 유지)

현재 College 화면의 `college-card`, `college-inline-meta`, `btn` 계열 스타일을 재사용해 톤을 맞춘다.

### 컴포넌트 트리(개념)
- `ScoutingTab`
  - `ScoutingSummaryBar`
  - `ScoutCardList`
    - `ScoutCard` x 7
      - `ScoutMiniProfile`
      - `AssignPlayerButton`
      - `OpenReportsButton` (+ unread badge)
  - `ScoutPlayerPickerModal`
  - `ScoutReportsModal`

### 스타일 가이드
- 기존 college card radius/shadow/spacing 유지
- 버튼은 기존 `btn btn-primary/secondary` 우선 사용
- unread 배지는 기존 status chip과 유사 톤으로 작은 pill/badge 추가
- 스카우터 전문분야는 `college-status-chip` 변형으로 시각 강조

---

## 5) API/데이터 플로우 구상

## 5-1) 초기 로딩
1. `GET /api/scouting/scouts/{team_id}`
2. `GET /api/college/players?...` (검색 대상 풀)
3. `GET /api/scouting/reports?team_id=...`

초기 렌더 시 각 스카우터 카드에 `active_assignment`, 최근 리포트 시각, unread 개수를 매핑한다.

## 5-2) 선수 선택
- 권장 UX 정책: **Replace-on-assign**
  - 현재 스카우터에 ACTIVE 배정이 있으면
    1) 확인 모달("기존 배정을 종료하고 새 선수로 배정할까요?")
    2) `unassign` 후 `assign` 순차 호출
- 대안 UX 정책: **Explicit-unassign-first**
  - 카드 내 "현재 배정 해제"를 먼저 수행하게 하고 이후 배정

기획 관점에서는 Replace-on-assign이 마찰이 적다.

## 5-3) 리포트 알림(unread)
현재 API 스키마에는 read/unread 필드가 없으므로 아래 중 하나 필요:

- **옵션 A (권장): 팀/스카우터별 last_read_at 저장 API 추가**
  - `scouting_report_reads(team_id, scout_id, last_read_at)`
  - unread 계산: `report.created_at > last_read_at`
- **옵션 B (프론트 임시): localStorage 기반 클라이언트 읽음 처리**
  - 빠르지만 저장 일관성/멀티디바이스 동기화 취약

기획 단계에서는 A를 목표, B를 임시 릴리즈 대응으로 둔다.

---

## 6) "중복 배정 오류" 재발 방지 설계

핵심은 "제약을 없애는 것"보다 "제약을 UI 흐름에 자연스럽게 흡수"하는 것이다.

1. **카드에 현재 배정 상태를 항상 노출**
   - 사용자가 이 스카우터가 이미 ACTIVE인지 즉시 인지
2. **선수 선택 직전 사전 체크**
   - 카드 state의 `active_assignment`가 있으면 교체 플로우로 전환
3. **원자적 교체 API 고려(중장기)**
   - `POST /api/scouting/reassign` (서버 트랜잭션에서 end+new insert)
   - 프론트에서 경쟁 상태(race) 및 409 노이즈 감소
4. **에러 메시지 구체화**
   - "해당 스카우터는 이미 OOO 선수 담당 중" 형태로 카드 인라인 피드백
5. **스카우터 단위 optimistic lock(선택)**
   - 요청 중 버튼 disabled + 스피너로 중복 클릭 방지

---

## 7) 단계별 실행 계획 (코드 작업 전 기획 로드맵)

### Phase 1 — UX 와이어 확정
- 카드 정보 밀도/배치 확정
- 모달 vs 우측 드로어 패턴 결정
- 검색 결과 리스트 표시 항목(포지션, 학교, 클래스, 주요 스탯) 결정

### Phase 2 — 상태/데이터 계약 확정
- `scouts` 응답에서 UI에 필요한 필드 체크리스트 확정
- unread 구현 방식(A/B) 결정
- reassign 정책(분리 API vs 클라이언트 2-step) 결정

### Phase 3 — 인터랙션 시나리오 정의
- 미배정 스카우터에 신규 배정
- 배정된 스카우터 재배정
- 배정 해제
- 신규 리포트 도착 시 배지 증가
- 리포트 열람 후 배지 초기화

### Phase 4 — 리스크 점검
- 선수 풀 200명+일 때 검색 성능 (debounce 150~250ms)
- 동시 요청 시 상태 꼬임(중복 클릭, 늦게 온 응답 덮어쓰기)
- 월말 리포트 생성 시점과 unread 계산 타이밍 일관성

---

## 8) 수용 기준(AC) 초안

- 스카우팅 탭 진입 시 7개 스카우터 카드가 1화면 내 스크롤 가능한 형태로 표시된다.
- 각 카드에서 선수 선택 플로우를 독립적으로 실행할 수 있다.
- 스카우팅 리포트 버튼에서 스카우터별 리포트 목록을 확인할 수 있다.
- 신규 리포트가 있으면 카드 버튼에 unread 표시가 나타난다.
- 동일 스카우터 재배정 시 기존처럼 모호한 "중복" 체감이 아니라, 명시적 교체 UX로 처리된다.

