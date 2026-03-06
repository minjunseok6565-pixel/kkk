# 트레이드 블록 전체 목록 집계 Endpoint 구현 계획

## 목표
- 마켓의 `트레이드 블록` 탭에서 **리그 전체 팀의 블록 등록 선수**를 한 번에 조회할 수 있도록 백엔드 집계 endpoint를 추가한다.
- FA/투웨이 리스트와 유사한 수준의 선수 정보(표 컬럼용)를 제공하되, 요청사항에 따라 **체력/경기력(컨디션 계열)** 정보는 제외한다.
- 프론트가 추가 조인 호출 없이 바로 표를 렌더링할 수 있는 응답 스키마를 제공한다.

---

## 범위

### 포함
1. 신규 API: `GET /api/trade/block`
2. 쿼리 파라미터 기반 필터/정렬/페이지네이션
3. Trade-market listing + 선수 메타/시즌 스탯 조인
4. 응답 스키마 문서화 및 프론트 연동 가이드
5. 단위/통합 테스트 추가

### 제외
- 트레이드 제안/협상 로직 변경
- 기존 `GET /api/trade/block/{team_id}` 제거/대체
- 컨디션(피로, 샤프니스, 부상복귀확률 등) 노출

---

## 현 상태 요약
- 현재는 팀별 조회 endpoint(`GET /api/trade/block/{team_id}`)만 존재.
- `trade_market.listings`에는 listing 메타(공개여부, 우선순위, 사유, 만료일 등)가 저장됨.
- Market 화면은 `trade-block` 탭 UI만 있고 실제 데이터 fetch/render는 미구현 상태.

이 계획은 **신규 집계 endpoint를 먼저 안정적으로 추가**하고, 프론트는 해당 endpoint를 바로 붙일 수 있는 형식으로 응답을 설계한다.

---

## API 설계

## 1) Endpoint
- `GET /api/trade/block`

## 2) Query Parameters
- `active_only: bool = true`
  - 만료/비활성 listing 제외 여부
- `visibility: str = "PUBLIC"`
  - 기본은 PUBLIC만 노출
  - 확장값: `ALL | PUBLIC | PRIVATE`
- `team_id: Optional[str]`
  - 특정 팀 필터 (기존 team endpoint 대체용으로도 사용 가능)
- `limit: int = 300`
  - 최대 500으로 캡
- `offset: int = 0`
- `sort: str = "priority_desc"`
  - 후보: `priority_desc`, `ovr_desc`, `updated_desc`, `age_asc`, `age_desc`

## 3) Response Shape (초안)
```json
{
  "ok": true,
  "filters": {
    "active_only": true,
    "visibility": "PUBLIC",
    "team_id": null,
    "limit": 300,
    "offset": 0,
    "sort": "priority_desc"
  },
  "total": 42,
  "rows": [
    {
      "player_id": "P123",
      "team_id": "BOS",
      "team_name": "Boston Celtics",
      "name": "Player Name",
      "pos": "SF",
      "overall": 82,
      "age": 27,
      "height_in": 80,
      "weight_lb": 225,
      "salary": 18500000,
      "pts": 18.4,
      "ast": 4.1,
      "reb": 6.0,
      "three_pm": 2.2,
      "listing": {
        "status": "ACTIVE",
        "visibility": "PUBLIC",
        "priority": 0.62,
        "reason_code": "PUBLIC_OFFER",
        "listed_by": "AUTO_PUBLIC_OFFER",
        "created_at": "2026-01-10",
        "updated_at": "2026-01-10",
        "expires_on": "2026-01-21"
      }
    }
  ]
}
```

> `listing` 서브객체는 트레이드 블록 맥락 정보, 최상단은 표 렌더링용 선수 정보.

---

## 데이터 조합 전략

## 1) 기준 집합
- `load_trade_market()` → `listings`에서 필터 조건에 맞는 엔트리 추출

## 2) 선수 정보 소스
- 1순위: UI 캐시(players)에서 빠른 조회
- 2순위: DB(`roster`, `players`, 시즌 집계 테이블/워크플로우 stats) fallback
- 목적: FA 리스트와 유사한 컬럼(`name/pos/overall/age/height/weight/salary/pts/ast/reb/three_pm`) 안정 제공

## 3) 조인 키
- `player_id`

## 4) 팀 이름 매핑
- `team_id` → 서버 상수 맵(`TEAM_FULL_NAMES`) 사용

## 5) 제외 필드(요구사항)
- 체력/경기력 관련 값은 응답에서 제외
  - 예: sharpness, fatigue, readiness, injury risk 등

---

## 구현 단계(백엔드)

## Step A. 스키마/유틸 보강
1. `app/schemas/trades.py`
   - `TradeBlockAggregateQuery`(또는 내부 파라미터 파싱용 모델) 추가
   - `visibility/sort/limit` validation(허용값/범위 clamp)

2. `app/api/routes/trades.py`
   - 파라미터 파싱 유틸 추가
   - 응답 행 생성 유틸 분리:
     - listing 정규화
     - 선수 정보 hydrate
     - 수치 기본값 처리

## Step B. 신규 API 추가
1. `@router.get("/api/trade/block")`
2. 내부 흐름:
   - today, db_path 확보
   - market 로드
   - listing 필터(active/visibility/team)
   - player_ids batch 수집
   - 선수 메타 batch 조회
   - row 병합 + 정렬 + offset/limit 적용
   - `total`, `filters`, `rows` 반환

## Step C. 안정성 처리
- listing은 존재하지만 선수 DB 행이 없는 경우:
  - row를 스킵하지 말고 최소 필드로 반환(이름 `"-"`, 수치 0)
  - `meta.missing_player_snapshot=true` 같은 진단 플래그는 내부 로그에만 남기고 API는 단순 유지

## Step D. 기존 API와의 관계
- `GET /api/trade/block/{team_id}` 유지 (하위호환)
- 내부적으로는 신규 집계 유틸을 재사용해 중복 로직 최소화

---

## 정렬/필터 정책

## 기본 필터
- `active_only=true`, `visibility=PUBLIC`

## 기본 정렬
1. `listing.priority` 내림차순
2. `overall` 내림차순
3. `listing.updated_at` 내림차순
4. `player_id` 오름차순(결정성 보장)

## 이유
- 트레이드 시장에서 “우선적으로 내놓은 매물 + 전력 가치”를 상단에 배치하면 UX가 직관적임.

---

## 테스트 계획

## 1) API 단위 테스트
- 파일: `tests/test_trade_block_aggregate_api.py` (신규)
- 케이스:
  1. 기본 호출 시 PUBLIC+ACTIVE만 반환
  2. `visibility=ALL` 시 PRIVATE 포함
  3. `team_id` 필터 정상 동작
  4. `limit/offset` 페이지네이션
  5. `sort=ovr_desc` 정렬 검증
  6. 만료 listing 제외 검증
  7. 선수 메타 누락 시 fallback row 검증

## 2) 회귀 테스트
- 기존 `GET /api/trade/block/{team_id}` 동작 불변 확인

## 3) 성능 스모크
- listing 300건 기준 응답시간/쿼리수 점검
- 필요 시 player batch 조회로 N+1 방지

---

## 프론트 연동 가이드(후속 작업용)

1. `marketScreen.js`에서 `openMarketSubTab("trade-block")` 분기 추가
2. `GET /api/trade/block?active_only=true&visibility=PUBLIC&limit=300` 호출
3. 표 컬럼:
   - 팀, 선수명, 포지션, OVR, 나이, 키, 몸무게, 샐러리, PTS, AST, REB, 3PM, 우선순위, 사유
4. row click 시 기존 player detail 재사용
   - 컨텍스트는 `market-trade-block` 등으로 분리 권장

---

## 리스크 및 대응

1. **데이터 소스 불일치(UI cache vs DB)**
   - 대응: DB를 최종 fallback으로 사용, 값 없으면 안전 기본값
2. **PRIVATE listing 노출 위험**
   - 대응: 기본값 PUBLIC 고정 + visibility 허용값 엄격 검증
3. **N+1 성능 저하**
   - 대응: player_ids batch 조회 유틸화

---

## 완료 기준 (Definition of Done)
- `GET /api/trade/block`가 명세대로 동작
- 기본 호출로 프론트 표 렌더링 가능한 필드가 모두 제공됨
- 체력/경기력 계열 필드는 응답에 없음
- 테스트 통과 + 기존 team endpoint 회귀 이상 없음

---

## 작업 순서 제안
1. API 응답 스키마/정렬 기준 확정
2. 백엔드 endpoint + 유틸 구현
3. 테스트 작성/통과
4. 프론트 탭 연결(다음 단계)
5. 실데이터 QA(시뮬 진행 중/오프시즌 포함)
