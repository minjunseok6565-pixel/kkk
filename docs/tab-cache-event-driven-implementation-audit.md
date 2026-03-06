# 탭 캐시 전략 전환 구현 점검 결과 (이벤트 기반 무효화 + 백그라운드 프리페치)

## 결론

- **현 시점에서는 "완벽 구현"으로 보기 어렵습니다.**
- 이벤트 기반 무효화/프리페치의 핵심 뼈대는 들어왔지만, 계획 문서의 범위를 100% 충족하지 못하는 항목이 확인되었습니다.

## 확인된 구현 완료 항목

1. **캐시 코어 확장 반영**
   - `sourceEventVersion`, `domainTag` 메타 필드가 캐시 엔트리 shape 및 `setCachedValue` 옵션에 추가됨.
   - `prefetchCachedJson` 유틸과 `invalidateCacheKeys` 유틸이 추가됨.
2. **캐시 정책 중앙화 레이어 도입**
   - `cachePolicy.js`에 이벤트 타입, TTL 정책, 이벤트별 무효화 매트릭스, 프리페치 플랜 함수가 구현됨.
3. **경기 후 오케스트레이션 연결**
   - `mainScreen.js`에서 경기 진행 후 `invalidateByEvent(GAME_PROGRESS)` + post-game prefetch 실행.
4. **탭 진입 시 캐시 우선 렌더 흐름 적용 확대**
   - schedule, standings, tactics, training, medical, college 주요 화면이 `fetchCachedJson` + long TTL 기반으로 동작.
5. **액션 성공 지점 이벤트 연결(일부)**
   - 전술 저장(`TACTICS_SAVE`), 훈련 저장(`TRAINING_SAVE`), 스카우트 배정(`SCOUT_ASSIGN`) 이벤트 발행 및 후속 프리페치 연결.

## 미완료/불완전 항목 (merge blocker)

1. **경기 후 프리페치 범위가 계획 대비 축소됨**
   - 계획은 경기 후 우선순위에 따라 schedule/standings/team-detail+tactics/training/medical/college summary까지 포함.
   - 실제 `getPrefetchPlanAfterGame`는 schedule, standings, team-detail까지만 프리페치.
2. **결과 화면 체류 시간 활용 타임버짓 전략 부재**
   - 계획의 `1.5~2.5초 타임버짓 + continuation` 전략이 코드상 구현되어 있지 않음.
3. **ROSTER_CHANGE 이벤트는 정의만 있고 발행 지점이 없음**
   - 이벤트 타입/정책은 정의되어 있으나, 실제 UI 액션 성공 지점에서 발행되는 코드가 확인되지 않음.
4. **대학/메디컬의 GAME_PROGRESS 후 선프리페치가 정책상 기대 대비 약함**
   - GAME_PROGRESS invalidation에는 medical prefix가 포함되지만, post-game prefetch 대상에는 medical/college가 미포함.

## Merge 판단

- **권고: 지금은 merge 보류**
- "계획이 완벽하게 적용된 상태" 기준으로는 아직 부족합니다.
- 특히 아래 3가지는 완료 후 재검증을 권장합니다.
  1) `getPrefetchPlanAfterGame`에 training/medical/(경량)college/tactics 확장
  2) post-game prefetch 타임버짓/우선순위 실행기 추가
  3) 실제 roster 변경 성공 경로에서 `ROSTER_CHANGE` 이벤트 emit 연결
