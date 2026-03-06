# 경기 후 프리페치 범위 확장 구현 계획

## 1) 배경 및 목표

`docs/tab-cache-event-driven-plan.md` 기준으로 경기 후 프리페치 범위는 아래 우선순위를 포함해야 한다.

1. schedule
2. standings
3. team-detail/tactics
4. training 핵심 번들
5. medical overview/alerts
6. college summary

현재 구현은 `getPrefetchPlanAfterGame()`에서 `schedule`, `standings`, `team-detail`까지만 프리페치하고 있어 계획서 대비 범위가 부족하다.

이 문서의 목표는 **미구현 항목을 정확히 식별**하고, **코드 레벨로 바로 옮길 수 있는 확장 작업 계획**을 제시하는 것이다.

---

## 2) 미구현/부분구현 항목 진단

### A. 경기 후 프리페치 범위 부족
- 현 상태:
  - `cachePolicy.getPrefetchPlanAfterGame()`이 3개 항목만 반환
  - tactics / training / medical / college summary 프리페치가 없음
- 영향:
  - 경기 직후 해당 탭 진입 시 첫 로딩/추가 API fan-out 발생

### B. 우선순위 기반 실행(병렬/순차 혼합) 부재
- 현 상태:
  - `mainScreen.queuePostGamePrefetch()`가 단일 plan을 `runPrefetchPlan()`으로 즉시 병렬 실행
- 영향:
  - 우선순위 제어가 어려움
  - 네트워크 혼잡 시 핵심 탭 체감 속도 개선이 제한될 수 있음

### C. 결과 화면 체류시간 활용 타임 버짓 미적용
- 현 상태:
  - prefetch에 시간 예산(예: 1.5~2.5초) 및 continuation 전략 없음
- 영향:
  - 결과 화면 체류 구간의 선행 로딩 이점을 충분히 활용하지 못함

### D. training 범위 계산 의존성 미정리
- 현 상태:
  - training 핵심 키 중 `training:sessions-resolve`는 date range(from/to) 필요
  - 경기 직후 시점에 안정적으로 사용할 range 생성/공유 경로가 명시되지 않음
- 영향:
  - training 번들 프리페치가 일부 키만 채워지거나 누락될 수 있음

### E. fail-soft 및 관측성(추적) 보강 필요
- 현 상태:
  - prefetch 자체는 fail-soft지만, 단계별 성공/실패/소요시간 추적 지표가 약함
- 영향:
  - 운영 중 "무엇이 실제로 채워졌는지" 확인하기 어려움

---

## 3) 확장 구현 범위 (파일별)

## 3.1 `static/js/app/cachePolicy.js`

### 작업 1) post-game 전용 프리페치 플랜 확장
- `getPrefetchPlanAfterGame(context)` 반환 항목을 아래처럼 확장
  - Tier 1: schedule, standings
  - Tier 2: teamDetail, tactics
  - Tier 3: trainingSchedule, trainingTeamDetail, trainingFamiliarity(off/def), trainingSessionsResolve(가능 시)
  - Tier 4: medicalOverview, medicalAlerts, medicalRiskCalendar
  - Tier 5: collegeMeta, collegeTeams, collegeExperts

### 작업 2) training range 안전 생성 헬퍼 추가
- `resolveTrainingRange(context)` 헬퍼 신설
  - 우선순위: `context.trainingRange` -> `context.currentDate` 기반 4주 계산 -> 미존재 시 sessions-resolve 제외
  - sessions-resolve key/url 생성 여부를 명시적으로 분기

### 작업 3) 우선순위 메타 포함
- plan item 구조를 확장
  - `priorityTier` (1~5)
  - `critical` (true/false)
  - `timeoutMs` (선택)
- 기존 `runPrefetchPlan()` 호환 위해 optional 필드로 설계

---

## 3.2 `static/js/features/main/mainScreen.js`

### 작업 4) post-game prefetch 오케스트레이터 추가
- `queuePostGamePrefetch()`를 아래 구조로 리팩터링
  1. `getPrefetchPlanAfterGame()` 호출
  2. tier별 그룹핑
  3. 실행 정책
     - Tier 1~2: 즉시 실행(핵심)
     - Tier 3~5: 시간 예산 내 순차 시작 + 남은 작업 continuation

### 작업 5) 결과 화면 체류시간 타임 버짓 적용
- `runPostGamePrefetchWithBudget({ budgetMs })` 유틸(내부 함수) 도입
  - 기본 `budgetMs = 2000`
  - budget 내 가능한 tier를 시작
  - budget 초과 시 `setTimeout(0)` 또는 `requestIdleCallback`(가용 시)로 continuation

### 작업 6) UI 블로킹 방지 보장
- prefetch는 전부 `void` fire-and-forget 유지
- 경기 결과 화면 진입/복귀 흐름의 `await` 체인을 막지 않도록 보장

---

## 3.3 `static/js/features/training/trainingScreen.js`

### 작업 7) 경기 후 재사용 가능한 training range 노출
- `prefetchTrainingCoreData()`가 반환하는 `from/to/currentDate`를 상태/컨텍스트에 저장하는 경량 함수 추가
- mainScreen prefetch에서 재사용 가능하도록 연결

### 작업 8) training 핵심 번들 함수 공개(필요시)
- 현재 기능을 유지하되, post-game에서 호출 가능한 얇은 wrapper 추가 고려
  - 예: `buildTrainingPrefetchContext(teamId, currentDate)`

---

## 3.4 `static/js/features/college/*` 및 `static/js/features/medical/*`

### 작업 9) post-game 우선순위와 정합성 확인
- college는 summary 3종(meta/teams/experts)만 post-game 기본 대상으로 유지
- medical은 overview/alerts/risk-calendar(14d)를 post-game 기본 대상으로 지정
- 각 screen 진입 시 캐시 키와 동일한 key/url을 사용하는지 교차 점검

---

## 4) 실행 알고리즘(권장)

1. `invalidateByEvent(GAME_PROGRESS)` 수행
2. post-game plan 생성
3. Tier 1~2 즉시 `Promise.all` 시작
4. `performance.now()` 기준 예산 체크
5. 예산이 남으면 Tier 3 시작
6. 예산 초과 시 Tier 3~5를 continuation 큐에 넣고 비동기 이어서 실행
7. 각 tier는 fail-soft (`prefetchCachedJson` 결과 null 허용)

> 핵심 원칙: **사용자 화면 전환 체인을 절대 지연시키지 않는다.**

---

## 5) 관측성/디버깅 계획

- `__CACHE_DEBUG__` 활성 시 아래 로그 추가
  - post-game prefetch 시작/종료
  - tier별 task 수, 완료 수, 실패 수
  - budget 소진 여부 및 continuation 이관 수
- 로그 prefix 예시
  - `[cache][post-game] tier=2 started=3 completed=3 failed=0`

---

## 6) 테스트/검증 계획

### A. 정적 점검
- `rg`로 post-game prefetch 대상 key/url가 실제 screen fetch와 일치하는지 확인
- `ROSTER_CHANGE` 등 기존 이벤트 매트릭스와 충돌 여부 점검

### B. 수동 시나리오
1. 홈에서 경기 진행 후 결과 화면 진입
2. 즉시 schedule/standings/tactics/training/medical/college 탭 순차 진입
3. 기대값
   - 최초 진입 로딩 오버레이 노출 빈도 감소
   - 캐시 miss 시에도 화면 에러 없이 fail-soft 동작

### C. 성능 확인
- 네트워크 throttle 환경에서
  - Tier 1~2 완료율
  - 결과 화면 체류 중 선행 로딩 비율
  - 탭 전환 평균 대기시간(before/after)

---

## 7) 단계별 작업 순서

1. `cachePolicy.js`: plan 확장 + training range 헬퍼
2. `mainScreen.js`: tier 오케스트레이터 + budget continuation
3. `trainingScreen.js`: range 컨텍스트 보조 함수 연결
4. medical/college key-url 정합성 교차 점검 및 조정
5. 디버그 로그 추가
6. 수동 시나리오 검증

---

## 8) 완료 기준(Definition of Done)

아래를 모두 만족하면 "경기 후 프리페치 범위 확장" 작업 완료로 판단한다.

1. `getPrefetchPlanAfterGame()`에 계획서 우선순위(1~6) 항목이 모두 반영됨
2. post-game prefetch가 tier+budget+continuation 전략으로 동작함
3. training 핵심 번들(`schedule/team-detail/sessions-resolve/familiarity`) 중 가능한 키가 모두 사전 채워짐
4. medical(overview/alerts/risk-calendar), college(summary 3종) 사전 채움 확인
5. 실패 케이스에서 사용자 화면 흐름/오류 메시지에 영향 없음
6. 디버그 로그로 실행 결과를 추적할 수 있음

