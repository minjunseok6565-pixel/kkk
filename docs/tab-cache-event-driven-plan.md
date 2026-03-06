# 탭 캐시 전략 전환 구현 계획 (이벤트 기반 무효화 + 백그라운드 프리페치)

## 1) 목표 요약

사용자 목표를 다음과 같이 구체화한다.

1. **탭 진입 시 로딩 최소화**
   - 경기 직후에도 사용자가 탭을 열면 가능하면 즉시 캐시 데이터로 화면을 표시한다.
2. **데이터 정합성 유지**
   - 데이터가 실제로 바뀌는 이벤트(경기 진행, 전술 저장, 훈련 저장, 스카우터 배정, 향후 트레이드/FA)에서만 관련 캐시를 무효화한다.
3. **TTL 의존도 축소**
   - 짧은 TTL 때문에 반복 로딩이 발생하지 않도록 기본 정책을 이벤트 기반으로 전환한다.
   - 단, TTL은 “안전망”으로 길게 유지한다(완전 제거는 하지 않음).
4. **경기 결과 화면에서 백그라운드 갱신**
   - 경기 완료 후 결과 화면 표시 시점에 다음 탭 데이터 프리페치를 병렬로 시작해 체감 속도를 개선한다.

---

## 2) 설계 원칙

- **원칙 A: 데이터 변경 시점 중심 캐시 정책**
  - “탭 진입 시 재요청”이 아니라 “변경 이벤트 발생 시 무효화 + 프리페치”로 전환.
- **원칙 B: 화면 진입은 조회만 담당**
  - 각 `show*Screen()`은 캐시 hit 시 즉시 렌더, miss일 때만 로딩.
- **원칙 C: 캐시 안전장치 유지**
  - TTL을 7~12초 같은 짧은 값에서 충분히 긴 값으로 조정.
  - 예기치 못한 무효화 누락을 대비.
- **원칙 D: 중앙 오케스트레이션**
  - 무효화/프리페치 키 체계를 한 곳에서 관리해 누락을 줄인다.

---

## 3) 변경 대상 파일 및 수정 계획

### A. 캐시 코어 레이어

#### 1) `static/js/core/api.js`

**수정 목적**
- 이벤트 기반 캐시 운용을 쉽게 하기 위한 코어 기능 확장.

**구상 변경점**
1. 캐시 엔트리 메타 보강
   - `setCachedValue`에 `sourceEventVersion`, `domainTag` 같은 선택 메타를 담을 수 있도록 옵션 확장.
   - 디버깅 시 “왜 이 캐시가 살아있는지” 추적 가능하게 한다.
2. 프리페치 유틸 추가
   - `prefetchCachedJson({ key, url, ... })` 형태의 helper 추가.
   - 내부적으로 `fetchCachedJson`을 사용하되 UI 로딩 오버레이를 건드리지 않도록 표준화.
3. 도메인 무효화 보강
   - 현재 prefix 무효화 함수(`invalidateCachedValuesByPrefix`)를 유지하되,
   - 도메인별 키 목록을 받아 일괄 무효화할 수 있는 wrapper 유틸 추가(예: `invalidateCacheKeys(keys[])`).
4. 관측성(선택)
   - `__CACHE_DEBUG__` 활성화 시 hit/miss 외에 `invalidation reason` 로그를 선택적으로 출력.

**주의사항**
- 기존 API 시그니처 호환 유지.
- 기존 호출부가 깨지지 않도록 optional 파라미터 중심으로 확장.

---

### B. 캐시 정책/키 중앙화 레이어 (신규 파일)

#### 2) `static/js/app/cachePolicy.js` (신규)

**수정 목적**
- 지금은 캐시 키/TTL/무효화 prefix가 파일별로 흩어져 있어 누락 리스크가 있음.
- 이를 중앙 관리로 전환.

**구상 변경점**
1. 탭별 캐시 키 팩토리 정의
   - schedule, standings, team-detail, tactics, training, college, medical 키 생성 함수 집합.
2. 이벤트별 영향 도메인 매핑
   - `GAME_PROGRESS`, `TACTICS_SAVE`, `TRAINING_SAVE`, `SCOUT_ASSIGN`, `ROSTER_CHANGE` 등 이벤트에 대해
     무효화해야 하는 키(prefix 포함) 목록 정의.
3. TTL 정책 정의
   - “짧은 TTL”을 기본으로 하지 않고, 도메인별 long TTL(안전망) 값 정의.
   - 예: 스케줄/순위/팀디테일/훈련 조회 캐시를 분 단위로 상향.
4. 헬퍼 제공
   - `invalidateByEvent(eventType, context)`
   - `getPrefetchPlanAfterGame(context)`

**주의사항**
- 점진 적용: 기존 코드의 상수와 병행 가능하게 시작하고, 이후 단계적으로 이동.

---

### C. 경기 후 무효화/프리페치 오케스트레이션

#### 3) `static/js/features/main/mainScreen.js`

**수정 목적**
- 현재 경기 진행 후 `invalidatePostGameViewCaches`는 있으나,
  프리페치 오케스트레이션은 없음.

**구상 변경점**
1. `invalidatePostGameViewCaches` 리팩토링
   - 직접 prefix 나열 대신 `cachePolicy`의 이벤트 기반 함수 호출로 대체.
2. 경기 완료 후 프리페치 호출 추가
   - `progressNextGameFromHome`, `autoAdvanceToNextGameDayFromHome`에서
     invalidate 직후 `queuePostGamePrefetch()` 실행.
3. 프리페치 실행 전략
   - 우선순위 높은 탭부터 병렬/순차 혼합:
     1) schedule
     2) standings
     3) team-detail/tactics
     4) training 핵심 번들
     5) medical overview/alerts
     6) college summary
4. 타임 버짓 설정
   - 결과 화면 체류 시간을 활용하되, 일정 시간(예: 1.5~2.5초) 초과 작업은 백그라운드 continuation.

**주의사항**
- 사용자 인터랙션 블로킹 금지 (`await` 전략 주의).
- 프리페치 실패는 fail-soft 처리(화면 에러로 전파 금지).

---

### D. 각 탭 Screen 진입 로직 정비

#### 4) `static/js/features/schedule/scheduleScreen.js`

**수정 목적**
- “경기/관련 이벤트 이후 1회 갱신, 그 외 무로딩” 정책에 맞춘 진입 동작 최적화.

**구상 변경점**
1. TTL 상향 또는 정책 위임
   - 파일 상수 대신 `cachePolicy`를 통해 TTL/키를 사용.
2. 진입 시 로딩 조건 정밀화
   - 캐시가 있으면 즉시 렌더 + 필요 시 SWR 백그라운드.
   - 캐시 miss일 때만 로딩 표시(현행 유지).
3. 경기 후 프리페치와 충돌 방지
   - in-flight request 공유를 활용해 중복 요청 최소화.

---

#### 5) `static/js/features/tactics/tacticsScreen.js`

**수정 목적**
- 전술 저장 이벤트 기반 무효화 + 저장 직후 캐시 동기화.

**구상 변경점**
1. `/api/tactics/{team}` 조회 캐싱 도입 검토
   - 현재 `fetchJson` 직접 호출을 `fetchCachedJson` 패턴으로 전환.
2. 전술 저장 성공 시점 처리
   - `TACTICS_SAVE` 이벤트 무효화 수행.
   - 필요 시 `team-detail`/`training:familiarity` 관련 캐시도 함께 invalidation.
3. 저장 직후 prefetch
   - 저장 완료 직후 즉시 최신 전술/팀 디테일 재프리페치해 다음 진입 무로딩 보장.

---

#### 6) `static/js/features/training/trainingScreen.js`

**수정 목적**
- 훈련 데이터 fan-out 요청의 체감 지연 감소.

**구상 변경점**
1. 훈련 핵심 데이터 프리페치 진입점 분리
   - `loadTrainingData`의 일부를 프리페치 가능 함수로 분리.
2. 저장 이벤트 연동
   - 훈련 설정 변경 API 성공 시 `TRAINING_SAVE` 이벤트 무효화 + 재프리페치.
3. 세션 hydrate 전략 유지 + 초기 표시 단축
   - 현재 progressive hydration 장점은 유지하되,
   - 경기 후 프리페치로 `training:team-detail`, `training:schedule`, `sessions-resolve`를 미리 채움.

---

#### 7) `static/js/features/college/*` (특히 `collegeScreen.js`, `scouting.js`, `bigboard.js`)

**수정 목적**
- 스카우터 배정/빅보드/리더보드 데이터 변경 이벤트를 명시적으로 캐시에 반영.

**구상 변경점**
1. 스카우터 배정 성공 시점 이벤트 발행
   - `SCOUT_ASSIGN` 이벤트로 관련 prefix 무효화.
2. 탭별 캐시 키 정책 통합
   - 현재 일부만 캐싱 중인 흐름을 통합 정책으로 맞춤.
3. 경기 후 프리페치 범위 최소화
   - 대학 탭은 우선순위를 낮춰 핵심 요약 데이터만 선프리페치.

---

#### 8) `static/js/features/medical/*` (overview/alerts/risk-calendar 진입 파일)

**수정 목적**
- 경기 후 변화가 큰 도메인(부상/리스크)을 이벤트 기반으로 정확히 갱신.

**구상 변경점**
1. `GAME_PROGRESS`, `ROSTER_CHANGE` 이벤트 무효화 적용.
2. 결과 화면 체류 중 선프리페치로 진입 로딩 최소화.

---

### E. 이벤트 발행 레이어 (신규 또는 기존 확장)

#### 9) `static/js/app/events.js` 또는 `static/js/app/cacheEvents.js` (신규)

**수정 목적**
- “어떤 액션이 어떤 캐시 이벤트를 발생시키는지”를 UI 레벨에서 일관 관리.

**구상 변경점**
1. 캐시 이벤트 버스 도입(경량)
   - `emitCacheEvent(type, payload)`
2. 액션 성공 지점 연결
   - 전술 저장 성공
   - 훈련 저장 성공
   - 스카우터 배정 성공
   - 향후 트레이드/FA/로스터 변경 성공
3. 소비자 연결
   - `cachePolicy` 무효화 및 프리페치 트리거.

---

## 4) 서버(API) 변경 검토 포인트

> 본 단계는 “클라이언트 중심 계획”이지만, 체감 개선을 크게 하려면 일부 API 경량화 병행이 필요.

### 10) `app/api/routes/core.py` (검토)

- `/api/team-schedule/{team_id}` 응답에서 탭별 필요한 필드만 가져오는 light 모드(쿼리 옵션) 검토.
- 홈 대시보드/의료/훈련에서 중복 스케줄 계산 비용이 큰지 확인 후 경량 accessor 분리.

### 11) `team_utils.py` (검토)

- `get_team_detail`가 전술/훈련 진입에서 과도한 데이터까지 계산하는지 검토.
- “탭 초기 렌더용 팀 요약+로스터 최소셋” 경량 함수 추가 검토.

---

## 5) 구현 순서(권장, 파일 단위 상세)

아래 순서는 **이 순서대로 작업하면 수정/검토 대상 파일(1~11번)을 전부 빠짐없이 다루도록** 구성했다.

### 0단계: 변경 매트릭스 먼저 확정(문서화)

- 목적: 구현 중 누락 방지.
- 작업:
  - 이벤트(`GAME_PROGRESS`, `TACTICS_SAVE`, `TRAINING_SAVE`, `SCOUT_ASSIGN`, `ROSTER_CHANGE`)별로
    “무효화 키 + 프리페치 키” 표를 먼저 작성.
- 반영 파일:
  - `docs/tab-cache-event-driven-plan.md` (본 문서 표 보강)

### 0-1) 이벤트 변경 매트릭스

| 이벤트 | 무효화 키(prefix) | 경기 직후/이벤트 직후 프리페치 키(대표) |
|---|---|---|
| `GAME_PROGRESS` | `schedule:{TEAM}`, `standings:`, `team-detail:{TEAM}`, `training:*:{TEAM}`, `medical:*:{TEAM}` | `schedule:{TEAM}`, `standings:table`, `team-detail:{TEAM}` |
| `TACTICS_SAVE` | `tactics:{TEAM}`, `team-detail:{TEAM}`, `training:familiarity:{TEAM}:*` | `tactics:{TEAM}`, `team-detail:{TEAM}` |
| `TRAINING_SAVE` | `training:sessions*:{TEAM}`, `training:session:{TEAM}:*`, `medical:*:{TEAM}` | `training:schedule:{TEAM}`, `training:sessions-resolve:{TEAM}:{FROM}:{TO}:nogame:missing` |
| `SCOUT_ASSIGN` | `college:` (스카우팅/빅보드/리더보드 관련) | `college:meta`, `college:teams`, `college:experts` |
| `ROSTER_CHANGE` | `team-detail:{TEAM}`, `tactics:{TEAM}`, `training:*:{TEAM}`, `medical:*:{TEAM}`, `college:` | `team-detail:{TEAM}`, `tactics:{TEAM}`, `training:schedule:{TEAM}` |

> 참고: 구현 시 실제 키 생성은 `static/js/app/cachePolicy.js`의 키 팩토리 함수를 단일 기준으로 사용한다.

### 1단계: 캐시 코어 + 정책 레이어 선구축

> 이 단계가 끝나야 이후 탭 파일에서 공통 API를 사용할 수 있다.

1. `static/js/core/api.js` 수정
   - `prefetchCachedJson`, `invalidateCacheKeys` 같은 공통 helper 추가.
   - 캐시 메타 optional 확장(`sourceEventVersion`, `domainTag`).
2. `static/js/app/cachePolicy.js` 신규 생성
   - 키 팩토리, TTL 정책, 이벤트→무효화 매핑, 경기 후 프리페치 플랜 정의.
3. `static/js/app/events.js` 또는 `static/js/app/cacheEvents.js` 수정/신규
   - 캐시 이벤트 발행/구독 경량 레이어 연결.

**단계 종료 기준**
- 탭 파일을 건드리지 않아도, 정책 함수 호출로 이벤트별 무효화/프리페치 플랜을 얻을 수 있어야 한다.

### 2단계: 경기 완료 파이프라인 연결

1. `static/js/features/main/mainScreen.js` 수정
   - 기존 `invalidatePostGameViewCaches`를 `cachePolicy.invalidateByEvent("GAME_PROGRESS")` 기반으로 전환.
   - `progressNextGameFromHome`, `autoAdvanceToNextGameDayFromHome`에
     `queuePostGamePrefetch()` 추가.

**단계 종료 기준**
- 경기 직후 캐시 무효화와 프리페치가 중앙 정책으로 동작한다.

### 3단계: 읽기 탭(변경 이벤트가 단순한 탭) 우선 이관

1. `static/js/features/schedule/scheduleScreen.js` 수정
   - 키/TTL 상수 분산 제거(정책 위임), miss 시 로딩 유지.
2. (필요 시) standings 진입 파일(프로젝트 내 standings 화면 파일) 수정
   - schedule과 동일한 정책 위임 패턴 적용.

**단계 종료 기준**
- 경기 전 반복 탭 왕복 시 로딩 재발생이 크게 줄어야 한다.

### 4단계: 쓰기 이벤트 탭(사용자 액션으로 변경되는 탭) 이관

1. `static/js/features/tactics/tacticsScreen.js` 수정
   - `/api/tactics/{team}` 캐시 전략 통합.
   - 전술 저장 성공 시 `TACTICS_SAVE` 이벤트 발행 + 관련 키 무효화 + 재프리페치.
2. `static/js/features/training/trainingScreen.js` 수정
   - 훈련 핵심 데이터 프리페치 가능 구조로 분리.
   - 훈련 저장 성공 시 `TRAINING_SAVE` 이벤트 발행 + 재프리페치.
3. `static/js/features/college/collegeScreen.js` 수정
4. `static/js/features/college/scouting.js` 수정
5. `static/js/features/college/bigboard.js` 수정
   - 스카우터 배정/대학 관련 액션 성공 시 `SCOUT_ASSIGN` 이벤트 발행 + 무효화/프리페치.
6. `static/js/features/medical/*` 관련 진입 파일 수정
   - `GAME_PROGRESS`, `ROSTER_CHANGE` 이벤트 정책 연동.

**단계 종료 기준**
- 전술/훈련/스카우팅 저장 후 첫 재진입 1회 갱신, 이후 무로딩에 가깝게 동작.

### 5단계: TTL 재설정(안전망으로만 사용)

1. `static/js/features/schedule/scheduleScreen.js`의 고정 TTL 상수 제거/축소(정책 위임 완료 확인)
2. `static/js/features/training/trainingScreen.js`의 고정 TTL 상수 제거/축소(정책 위임 완료 확인)
3. `static/js/features/team/teamDetailCache.js`의 고정 TTL 상수 제거/축소(정책 위임 완료 확인)
4. `static/js/app/cachePolicy.js`에서 도메인별 long TTL 최종값 확정

**단계 종료 기준**
- TTL이 UX를 좌우하지 않고, 이벤트 무효화가 주 동작이 된다.

### 6단계: 서버 경량화 검토(필요 시 구현)

> 클라이언트 개선 후에도 병목이 남을 때 실행.

1. `app/api/routes/core.py` 검토/수정
   - `/api/team-schedule/{team_id}` light 응답 옵션 추가 여부 결정.
   - 홈/의료/훈련에서 스케줄 중복 계산 최적화 검토.
2. `team_utils.py` 검토/수정
   - `get_team_detail` 경량화(탭 초기 렌더용 최소 payload 함수 분리) 검토.

**단계 종료 기준**
- 프론트 프리페치가 빨라도 서버 응답이 느린 구간을 줄여 실질 응답시간 단축.

### 7단계: 최종 점검(누락 파일 체크리스트)

아래 파일이 구현 과정에서 **모두 반영 또는 검토 완료**되었는지 체크:

- [x] `static/js/core/api.js` (수정 완료)
- [x] `static/js/app/cachePolicy.js` (신규 추가 완료)
- [x] `static/js/features/main/mainScreen.js` (수정 완료)
- [x] `static/js/features/schedule/scheduleScreen.js` (수정 완료)
- [x] `static/js/features/tactics/tacticsScreen.js` (수정 완료)
- [x] `static/js/features/training/trainingScreen.js` (수정 완료)
- [x] `static/js/features/college/collegeScreen.js` (수정 완료)
- [x] `static/js/features/college/scouting.js` (수정 완료)
- [x] `static/js/features/college/bigboard.js` (수정 완료)
- [x] `static/js/features/medical/*` 관련 진입 파일 (수정 완료; 현재 대상은 `medicalScreen.js` 단일 파일)
- [x] `static/js/app/events.js` 및 `static/js/app/cacheEvents.js` (수정/신규 완료)
- [ ] `app/api/routes/core.py` (6단계 스킵 지시로 보류)
- [ ] `team_utils.py` (6단계 스킵 지시로 보류)

> 위 체크리스트가 비어 있지 않으면, “수정이 필요한 파일을 전부 정확하게 수정/검토했다”는 완료 조건을 충족하지 못한 것으로 간주한다.

### 7-1) 점검 결과 메모

- 4~5단계 범위의 프론트 파일은 체크리스트 기준으로 반영 완료.
- 6단계(서버 경량화 검토)는 이번 작업에서 스킵 지시가 있어 `app/api/routes/core.py`, `team_utils.py`는 보류 상태로 유지.


---

## 6) 검증 시나리오 (수용 기준)

1. **경기 직후 첫 탭 진입**
   - 프리페치 완료 시 로딩 오버레이가 거의 보이지 않음.
2. **경기 전 반복 탭 왕복**
   - schedule/standings는 로딩 재발생 없음.
3. **전술 저장 후 재진입**
   - 변경 내용이 반영되고, 이후 재진입은 무로딩.
4. **훈련 설정 저장 후 재진입**
   - 변경 반영 + 무로딩.
5. **스카우터 배정 후 대학 탭 재진입**
   - 반영 확인 + 불필요한 전역 로딩 없음.
6. **무효화 누락 방어**
   - TTL 안전망 덕분에 장시간 stale 고착 없음.

---

## 7) 리스크 및 대응

1. **이벤트 누락으로 stale 데이터 지속**
   - 대응: 이벤트 매핑 표 문서화 + 저장 API 성공 지점 단위 테스트/로그.
2. **프리페치 과다로 네트워크 혼잡**
   - 대응: 우선순위 큐 + 동시성 제한 + 타임버짓 적용.
3. **중복 요청**
   - 대응: 현재 inflight dedupe를 적극 활용하고 키 설계를 통일.
4. **초기 도입 복잡도 증가**
   - 대응: 탭 단위 점진 도입 (schedule부터).

---

## 8) 최종 제안

- 사용자 목표를 달성하려면 **“이벤트 기반 무효화 + 경기 후 프리페치 + 긴 TTL 안전망”** 조합이 가장 현실적이다.
- 구현은 프론트 중심으로 가능하며, 필요 시 서버 응답 경량화까지 병행하면 체감 개선 폭이 커진다.
- 본 문서 기준으로 다음 단계에서 실제 패치 작업을 진행한다.
