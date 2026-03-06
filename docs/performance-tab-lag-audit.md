# 탭 전환 버벅임 병목 점검 노트

## 결론 요약
- **남아있는 병목은 여전히 존재**하며, 특히 "탭 진입 시 매번 대량 API 호출 + 화면 전체 재렌더" 패턴이 반복됩니다.
- 경기 진행 횟수가 늘수록(누적 데이터 증가) 각 API 응답/가공 시간이 길어질 수 있어, 탭 클릭 체감 지연이 다시 커지기 쉬운 구조입니다.
- 게임 로직을 크게 바꾸지 않고도, **캐시 TTL/무효화 조정, 단계적 렌더링, 중복 호출 축소**로 저비용 고효율 개선 여지가 큽니다.

## 확인된 병목 후보

### 1) College 화면 진입 시 호출량 과다
- `showCollegeScreen()` 진입 시점에 아래가 순차적으로/연쇄적으로 실행됩니다.
  - 메타/팀/전문가 목록 로드
  - 리더보드 로드 (`/api/college/players?limit=100`)
  - 빅보드 로드 (전문가 수만큼 병렬 호출)
  - 스카우팅 데이터 로드
- 특히 빅보드는 `loadCollegeBigboard()`에서 전문가별 API를 모두 호출하므로, 전문가 수가 늘면 한 번의 탭 진입 비용이 급격히 커집니다.
- 관련 위치
  - `static/js/features/college/collegeScreen.js`
  - `static/js/features/college/leaders.js`
  - `static/js/features/college/bigboard.js`

**저비용 개선안**
- College 메인 진입 시에는 Teams 탭 데이터만 우선 렌더하고, Leaders/Bigboard/Scouting은 탭 클릭 시 lazy load.
- Bigboard는 overview 카드 수를 제한하고, 상세는 클릭 시 fetch 유지.
- Leaders는 기본 100건 대신 30~50건 + "더 보기".

### 2) Training 화면의 다중 호출(날짜별 세션 조회)
- `loadTrainingData()`에서 4주 캘린더를 만든 뒤, 저장된 세션이 없는 날짜마다 `Promise.all`로 개별 세션 API를 병렬 조회합니다.
- 경기 진행으로 날짜가 바뀌면 캐시 무효화 후 다시 동일 패턴이 반복되어, 탭 진입 순간 부하가 큽니다.
- 관련 위치
  - `static/js/features/training/trainingScreen.js`

**저비용 개선안**
- 날짜별 개별 조회 동시성 제한(예: 3~4개) 적용.
- 최초 진입은 캘린더/요약만 먼저 그리고, 세션 상세 배지는 백그라운드로 점진 갱신.
- 가능하면 백엔드에 일괄 endpoint(기간 내 누락 세션 포함)를 추가해 호출 수 자체를 축소.

### 3) MyTeam / Tactics 의 `team-detail` 중복 조회
- `showMyTeamScreen()`과 `showTacticsScreen()` 모두 진입 시 `team-detail`를 매번 새로 조회합니다.
- 경기 후 데이터가 커질수록 같은 payload를 여러 탭에서 반복 계산/전송할 가능성이 높습니다.
- 관련 위치
  - `static/js/features/myteam/myTeamScreen.js`
  - `static/js/features/tactics/tacticsScreen.js`

**저비용 개선안**
- `team-detail`를 짧은 TTL(예: 5~10초)로 공용 캐시화하고, 경기 진행 직후만 invalidate.
- 이미 Training에는 캐시 사용 패턴이 있어 동일 방식 재사용 가능.

### 4) 게임 결과 화면 데이터/DOM 규모 문제 가능성
- 경기 결과 렌더링은 플레이바이플레이, 박스스코어, 차트 등 대형 DOM을 한 번에 교체합니다.
- `state.lastGameResult`에 마지막 경기 전체 payload를 유지하고, PBP 필터링/재렌더를 반복 수행합니다.
- "한 경기만 저장"이라 누적 메모리 폭증은 제한적이지만, 단일 payload가 크면 탭 전환 직후 메인 스레드 점유가 체감될 수 있습니다.
- 관련 위치
  - `static/js/features/gameResult/gameResultScreen.js`

**저비용 개선안**
- 초기 진입 시 기본 탭을 Gamecast로 고정하고, Play-by-Play는 탭 클릭 시 첫 렌더.
- PBP 초기 렌더 개수(`PBP_INITIAL_RENDER_LIMIT`)를 상황별 동적으로 축소.

### 5) 캐시 저장소의 eviction 부재
- `viewCache`는 key prefix invalidate 방식은 있으나, 전역적인 개수/메모리 상한 관리가 없습니다.
- 긴 세션에서 다양한 화면을 오가면 cache entry가 점진적으로 누적될 수 있습니다.
- 관련 위치
  - `static/js/core/api.js`
  - `static/js/app/state.js`

**저비용 개선안**
- 간단한 LRU/최대 엔트리 수 상한(예: 100~200) 적용.
- 큰 payload(예: bigboard 상세)는 짧은 TTL/별도 상한.

## 우선순위 제안 (저비용/고효율 순)
1. **College lazy load 전환** (진입 시 전체 로드 제거)
2. **Training 날짜별 세션 조회 동시성 제한 + 점진 렌더링**
3. **MyTeam/Tactics `team-detail` 공용 캐시**
4. **Game Result 초기 렌더 경량화(PBP 지연 렌더)**
5. **viewCache 상한 정책 도입**

## 측정 포인트(적용 전후 비교 권장)
- 탭 클릭 → 첫 페인트(ms)
- 탭 클릭 → 로딩 오버레이 종료(ms)
- 탭 진입 시 발생 API 수(특히 College/Training)
- 메인 스레드 Long Task(50ms+) 개수
- JS heap 추세(경기 1, 5, 10회 진행 후)
