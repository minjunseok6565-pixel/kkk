# 탭 캐시 전략 전환 구현 점검 결과 (이벤트 기반 무효화 + 백그라운드 프리페치)

## 결론 (요약)

- **로스터 변경(`ROSTER_CHANGE`) 연동을 제외하면, 계획 문서의 핵심 목표는 현재 코드에 대부분 반영되었습니다.**
- 즉, 질문하신 전제(“로스터 변경 사항 외에는 패치 완료”)를 기준으로 보면 **구현 완성도는 높고 목표 달성도도 높습니다.**
- 다만 “목표 완벽 달성(100%)” 관점에서는 아래 1건 때문에 아직 완전 종료는 아닙니다.
  - `ROSTER_CHANGE` 이벤트는 정책/프리페치 플랜에 정의되어 있으나 실제 emit 경로가 없음.

---

## 검토 범위

- 기준 문서: `docs/tab-cache-event-driven-plan.md`
- 프론트 핵심 구현 파일:
  - `static/js/core/api.js`
  - `static/js/app/cachePolicy.js`
  - `static/js/app/cacheEvents.js`
  - `static/js/app/events.js`
  - `static/js/features/main/mainScreen.js`
  - `static/js/features/schedule/scheduleScreen.js`
  - `static/js/features/standings/standingsScreen.js`
  - `static/js/features/tactics/tacticsScreen.js`
  - `static/js/features/training/trainingScreen.js`
  - `static/js/features/training/trainingDetail.js`
  - `static/js/features/college/collegeScreen.js`
  - `static/js/features/college/scouting.js`
  - `static/js/features/college/bigboard.js`
  - `static/js/features/medical/medicalScreen.js`

---

## 계획 대비 점검 결과

### 1) 캐시 코어 레이어 확장 — **달성**

- `setCachedValue`에 `sourceEventVersion`, `domainTag` optional 메타가 추가됨.
- `prefetchCachedJson` 유틸과 `invalidateCacheKeys` 유틸이 추가되어 이벤트 기반 정책 연결이 쉬워짐.
- 기존 `fetchCachedJson` 시그니처와 호환 유지됨.

### 2) 정책 중앙화(`cachePolicy`) — **달성**

- 이벤트 타입, TTL 정책, 키 팩토리, 이벤트별 invalidate matrix가 중앙 정의됨.
- 경기 후 프리페치 플랜에 schedule/standings/team-detail/tactics/training/medical/college가 우선순위(tier)와 함께 정의됨.
- 이벤트별 프리페치(`getPrefetchPlanForEvent`)도 구성되어 전술/훈련/스카우팅/로스터 변경 후 follow-up 요청 계획이 존재.

### 3) 경기 후 오케스트레이션 — **달성**

- `mainScreen`에서 경기 진행 직후 `GAME_PROGRESS` 기반 무효화 수행.
- 결과 화면 시점에 post-game prefetch를 큐잉.
- **타임버짓(2초) + tier별 처리 + budget 초과 시 continuation(defer)** 로직이 구현되어, 계획의 체류시간 활용 전략을 반영.

### 4) 탭 진입 로직(캐시 우선 + long TTL 안전망) — **달성**

- schedule/standings/tactics/training/college/medical 탭이 정책 TTL 기반 `fetchCachedJson` 패턴으로 정렬됨.
- 캐시 miss 시 로딩, 캐시 hit 시 즉시 렌더 구조가 전반적으로 유지됨.

### 5) 쓰기 이벤트 연동 — **부분 달성 (로스터 제외 시 달성)**

- 전술 저장 성공 후 `TACTICS_SAVE` emit + 후속 prefetch 실행됨.
- 훈련 저장 성공 후 `TRAINING_SAVE` emit + 후속 prefetch 실행됨.
- 스카우트 배정 성공 후 `SCOUT_ASSIGN` emit + 후속 prefetch 실행됨.
- **미완료:** `ROSTER_CHANGE` emit 지점은 아직 연결되지 않음.

---

## 핵심 갭(완벽 달성 관점)

1. **`ROSTER_CHANGE` 발행 경로 부재**
   - 정책 정의(`CACHE_EVENT_TYPES.ROSTER_CHANGE`, invalidate/prefetch plan)는 존재.
   - 그러나 실제 UI 액션 성공 지점에서 `emitCacheEvent(CACHE_EVENT_TYPES.ROSTER_CHANGE, ...)`가 호출되지 않음.

---

## 최종 판단

- 질문하신 전제(“로스터 변경 외 모두 패치”)를 기준으로는 **예상한 개선 목표가 실질적으로 달성된 상태**입니다.
- 다만 “계획서 전체를 완벽하게 100% 달성했는가?”라는 질문에는 **아직 NO**입니다.
  - 남은 작업: 로스터 변경 성공 경로에 `ROSTER_CHANGE` emit 연결 + 필요 시 후속 prefetch 트리거.
