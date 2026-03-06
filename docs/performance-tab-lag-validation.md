# 탭 전환 병목 개선 검증 보고서

## 검증 범위
- 기준 문서: `docs/performance-tab-lag-audit.md`
- 대상 이슈 5개(College/Training/MyTeam-Tactics/GameResult/viewCache)
- 방법: 정적 코드 리뷰 + 회귀 테스트 실행

## 결론 요약
- **5개 병목 항목 모두 코드 레벨에서 실제 개선 반영이 확인됨.**
- 특히 초기 진입에서의 불필요한 동시 대량 호출이 줄고, 캐시/지연 로딩/점진 렌더링이 도입되어 **탭 클릭 직후 메인 스레드 부담 및 API burst 가능성이 낮아짐**.
- 다만, 캐시 TTL 기반 구조 특성상 **짧은 시간 내 데이터 최신성 지연** 같은 트레이드오프가 존재하며, 일부 UI 선택 상태/요청 경쟁 조건 관련 경계 케이스는 추가 모니터링 권장.

---

## 1) College 화면 진입 호출량 과다
### 적용 확인
- `showCollegeScreen()`이 초기 진입 시 `meta/teams/experts`만 먼저 로드하고, Leaders/Bigboard/Scouting은 즉시 호출하지 않음.
- `ensureCollegeTabData(tab)`를 통해 leaders/bigboard/scouting 탭은 **클릭 시 최초 1회 lazy load**.
- Leaders API 기본 limit를 100에서 50으로 축소.

### 기대 효과
- College 진입 시 API burst 감소.
- Bigboard 전문가 다중 호출이 메인 진입 경로에서 제거되어 초기 체감 지연 완화.

### 잠재 부작용/리스크
- 탭 첫 클릭 시 로딩 체감이 생길 수 있음(진입이 아닌 탭 전환 시점으로 비용 이동).
- lazy load 실패 시 해당 탭만 데이터가 비어 보일 수 있어, 실패 안내 UI 강화 여지 있음.

---

## 2) Training 날짜별 세션 다중 호출
### 적용 확인
- 누락 세션 hydrate 로직이 `hydrateMissingSessions()`로 분리됨.
- `runWithConcurrency()` + `TRAINING_SESSION_FETCH_CONCURRENCY = 4`로 **동시성 제한** 적용.
- `progressiveSessionHydration` 경로 도입으로 캘린더/요약을 먼저 보여주고 세션을 점진 반영.

### 기대 효과
- 탭 진입 순간 네트워크/메인스레드 압력 완화.
- 대량 날짜 누락 상황에서도 요청 폭주 리스크 감소.

### 잠재 부작용/리스크
- 백그라운드 hydrate 중 화면이 여러 번 갱신되므로 저사양 환경에서 미세한 깜빡임 가능.
- fail-soft 처리로 일부 날짜 세션 누락이 조용히 지나갈 수 있어, 운영 로그 계측 권장.

---

## 3) MyTeam / Tactics `team-detail` 중복 조회
### 적용 확인
- `teamDetailCache` 모듈 신설.
- MyTeam/Tactics 모두 `fetchTeamDetail()` 공용 사용.
- TTL 7초 + SWR(onRevalidated)로 화면 활성 상태에서 안전하게 최신화.
- 경기 진행 후 `team-detail:*` cache invalidation 경로가 메인 화면에 반영됨.

### 기대 효과
- 인접 탭 전환 시 동일 payload 중복 fetch 감소.
- 데이터 최신성은 SWR로 보완.

### 잠재 부작용/리스크
- TTL 구간 내 stale 데이터가 잠시 보일 수 있음.
- Tactics 편집 중(`tacticsDirty`)에는 재검증 반영을 막아두므로, 사용자는 저장 전 최신 변경을 즉시 못 볼 수 있음(의도된 안전장치).

---

## 4) Game Result 대형 DOM/PBP 렌더 부담
### 적용 확인
- 초기 활성 탭이 `gamecast`로 고정.
- PBP는 placeholder 후 `playbyplay` 탭 진입 시 `hydratePbpIfNeeded()` 수행.
- 초기 렌더 한도를 `PBP_INITIAL_RENDER_LIMIT`(80)로 제한하고 `더 보기` 방식 사용.

### 기대 효과
- 경기 결과 화면 첫 진입 시 대형 PBP DOM 생성/필터 연산 지연.
- 초기 프레임 안정성 향상 기대.

### 잠재 부작용/리스크
- PBP 탭 첫 진입 시 데이터 정규화 비용이 집중될 수 있음.
- 매우 긴 PBP의 경우도 단계적 로딩이라 UX는 좋아지나, 전체 탐색 완료까지 사용자 추가 동작 필요.

---

## 5) viewCache eviction 부재
### 적용 확인
- `core/api.js`에 전역 캐시 관리 정책 도입:
  - 최대 엔트리 상한(`MAX_CACHE_ENTRIES = 220`)
  - 목표 축소치(`TARGET_CACHE_ENTRIES_AFTER_EVICTION = 170`)
  - 만료 sweep, LRU eviction, prefix(group)별 제한
  - 캐시 metrics 카운터 및 주기적 유지보수 훅

### 기대 효과
- 긴 세션에서 cache 무한 증가 방지.
- 메모리/객체 수 증가로 인한 장기 성능 저하 완화.

### 잠재 부작용/리스크
- 공격적인 eviction 상황에서 재요청 증가 가능.
- 그룹 제한이 작은 경우 특정 화면의 캐시 hit율이 예상보다 낮아질 수 있음.

---

## 회귀 테스트 결과
- Python API/상태 관련 핵심 테스트 셋에서 통과(14 passed, 5 skipped).
- 전체 `pytest -q`는 환경 PYTHONPATH 미설정으로 수집 단계 실패(테스트 코드 결함 아님).

## 종합 판단
- 현재 브랜치 기준으로, `performance-tab-lag-audit.md`의 5개 병목 개선 시도는 **구현 의도에 맞게 반영되었고 효과가 기대되는 상태**.
- 치명적(crash/데이터 손상) 수준의 신규 결함 징후는 코드 리뷰와 회귀 테스트에서 발견되지 않음.
- 다만 실사용 체감과 장기 안정성 보장을 위해 다음 관측을 권장:
  - 탭별 first paint / overlay 해제 시간
  - College/Training API 호출 수
  - cache hit/miss 및 eviction 비율
  - GameResult의 PBP 첫 진입 latency
