# 부상 데이터 valuation 주입 연결 사전작업 명세 (구현 확정본)

작성일: 2026-03-15  
목적: 트레이드 valuation에서 부상 로직을 바로 사용할 수 있도록 **입력 파이프라인(SSOT→PlayerSnapshot)** 을 먼저 완성한다.

---

## 0) 목표/비목표

## 목표
- `player_injury_state`, `injury_events` SSOT를 valuation data context에 연결한다.
- `PlayerSnapshot.meta["injury"]` 주입 스키마를 고정한다.
- 시장가/리스크 로직 추가 전, 필요한 데이터가 **한 번에 주입**되도록 사전작업을 끝낸다.
- 이후 단계는 `market_pricing.py`, `team_utility.py`에서 계산식만 추가하면 되게 만든다.

## 비목표
- 본 문서 단계에서는 시장가/리스크 계산식 자체는 수정하지 않는다.
- UI/API 응답 스키마 확장은 하지 않는다.

---

## 1) 최종 주입 스키마 (확정)

valuation 엔진이 소비할 표준 구조를 아래로 확정한다.

```python
PlayerSnapshot.meta["injury"] = {
  "version": 1,
  "as_of_date": "YYYY-MM-DD",   # valuation current_date
  "source": {
    "current": "player_injury_state",   # 없으면 "none"
    "history": "injury_events",         # 없으면 "none"
  },

  "current": {
    "status": "HEALTHY|OUT|RETURNING|UNKNOWN",
    "is_out": bool,
    "is_returning": bool,
    "days_to_return": int,               # HEALTHY/UNKNOWN이면 0
    "body_part": str | None,
    "severity": int | None,              # DB severity
    "out_until_date": "YYYY-MM-DD" | None,
    "returning_until_date": "YYYY-MM-DD" | None,
  },

  "history": {
    "window_days": 365,
    "recent_count_30d": int,
    "recent_count_180d": int,
    "recent_count_365d": int,
    "critical_count_365d": int,
    "same_part_repeat_365d_max": int,
    "same_part_counts_365d": {"KNEE": int, "BACK": int, ...},
    "avg_severity_365d": float,
    "weighted_severity_365d": float,     # recency 가중 평균
    "last_injury_date": "YYYY-MM-DD" | None,
    "days_since_last_injury": int | None,
  },

  "health_credit_inputs": {
    "availability_rate_365d": float,     # 0..1
    "healthy_days_365d": int,
    "out_days_365d": int,
    "returning_days_365d": int,
  },

  "flags": {
    "current_missing": bool,
    "history_missing": bool,
    "fallback_used": bool,
  }
}
```

### 규칙
- 부상 데이터가 전혀 없어도 키 구조는 유지하고, 값은 안전한 기본값으로 채운다.
- 모든 body part는 대문자 정규화(`str.upper().strip()`).
- 음수 일수는 0으로 clamp.
- `days_to_return`는 `status=OUT`이면 `out_until_date - as_of_date`, `RETURNING`이면 0.

---

## 2) 치명 부위(critical body parts) 정책

하드코딩 분산을 막기 위해 **단일 상수 위치**를 만든다.

### 신규 파일
- `trades/valuation/injury_features.py` (신규)

### 내용
- `DEFAULT_CRITICAL_BODY_PARTS: frozenset[str]`
  - 1차 기본값: `{"KNEE", "BACK", "SPINE", "ACHILLES", "FOOT", "HIP", "NECK"}`
- body part 정규화 함수
- 날짜 구간/겹침 계산 유틸
- 이벤트 집계 함수(최근 빈도, 동일 부위 반복, severity/recency)
- 현재 상태 파서 함수
- 최종 `build_injury_payload_for_player(...)` 함수

> 이후 단계에서 `trade_rules.valuation.critical_body_parts`를 붙여 덮어쓸 수 있게 설계한다(이번 단계는 기본 상수만 도입).

---

## 3) 파일별 수정 명세 (정확)

## A. `trades/valuation/data_context.py`

### A-1) import 추가
- `from .injury_features import build_injury_payloads_for_players` (신규 헬퍼)

### A-2) dataclass 필드 추가 (`RepoValuationDataContext`)
- `injury_payload_by_player: Dict[str, Dict[str, Any]] = field(default_factory=dict)`

### A-3) `get_player_snapshot()` 주입
- 현재 `meta=self._merge_player_meta_with_agency(...)` 결과에 `injury`를 주입.
- 방식:
  1. 기존 meta merge
  2. `inj = self.injury_payload_by_player.get(pid)`
  3. dict이면 `meta["injury"] = inj`

### A-4) `_preload_players()` 경로 동일 적용
- 캐시 프리로드 경로에서도 동일하게 `meta["injury"]` 주입해야 한다.

### A-5) builder 경로 확장 (`build_repo_valuation_data_context`)
- DB에서 선수 id 목록 확보 후, 한 번에 injury payload map 생성:
  - `injury_payload_by_player = build_injury_payloads_for_players(...)`
- `RepoValuationDataContext(...)` 생성 시 필드 전달.

### A-6) 실패 내성
- injury 테이블이 없거나 쿼리 실패 시:
  - 빈 dict 반환
  - `flags.current_missing/history_missing=True` payload를 기본 생성해 주입

---

## B. `trades/valuation/types.py`

### B-1) 타입 확장(선택이지만 권장)
- `TypedDict` 추가:
  - `InjuryCurrentPayload`
  - `InjuryHistoryPayload`
  - `InjuryHealthCreditInputs`
  - `InjuryPayload`

### B-2) PlayerSnapshot 자체 필드는 유지
- `meta: Dict[str, Any]` 유지 (하위호환)
- 즉, 런타임 호환성 깨지 않음.

---

## C. 신규 `trades/valuation/injury_features.py`

### C-1) 공개 함수 시그니처

```python
def build_injury_payloads_for_players(
    *,
    conn: Any,
    player_ids: Sequence[str],
    as_of_date_iso: str,
    lookback_days: int = 365,
    critical_body_parts: Optional[Collection[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    ...
```

### C-2) 내부 단계
1. 입력 player_ids 정규화/중복제거
2. `player_injury_state` bulk 조회 (current)
3. `injury_events` bulk 조회 (lookback)
4. player별 집계
5. 기본 payload와 merge
6. 누락 flag 채움

### C-3) DB 쿼리 기준
- current:
  - `SELECT player_id, status, out_until_date, returning_until_date, body_part, severity FROM player_injury_state WHERE player_id IN (...)`
- history:
  - `SELECT player_id, date, body_part, severity, out_until_date, returning_until_date FROM injury_events WHERE player_id IN (...) AND date >= ? AND date <= ?`

### C-4) 성능
- 반드시 bulk query 2회(상태 1, 이력 1) 원칙.
- player별 루프 내 DB 접근 금지.

---

## D. `trades/valuation/service.py` (옵션)

현 단계 필수는 아님. 다만 향후 튜닝 고려 시 아래를 준비 가능.
- `trade_rules.valuation.injury_lookback_days`
- `trade_rules.valuation.critical_body_parts`

지금은 기본값(365일 + DEFAULT_CRITICAL_BODY_PARTS)으로 고정해도 충분.

---

## 4) 주입 완료 판정 기준 (Done Criteria)

다음이 만족되면 “사전작업 완료”로 본다.

1. valuation에서 생성되는 모든 `PlayerSnapshot.meta`에 `injury` 키가 존재.
2. injury 테이블 없는 환경에서도 예외 없이 기본 payload 주입.
3. 동일 입력(as_of_date/player_ids)에서 payload가 결정적으로 동일.
4. player별 DB N+1 쿼리가 발생하지 않음.
5. 아직 market/risk 로직을 건드리지 않아 기존 valuation 총점 회귀가 유지.

---

## 5) 테스트 명세 (이 단계에서 반드시 추가)

테스트 파일: `trades/valuation/test_injury_payload_injection.py` (신규)

## 케이스
1. **기본 주입**: current+history 존재 시 expected schema 채워짐
2. **current only**: history 누락 flag 확인
3. **history only**: current 누락 flag 확인
4. **테이블 없음**: 예외 없이 기본 payload
5. **일자 경계**: `as_of_date` 기준 days 계산 clamp 확인
6. **동일 부위 반복**: `same_part_repeat_365d_max` 정확
7. **치명 부위 카운트**: critical set 기준 카운트 정확
8. **벌크 쿼리 보장**: mock/spy로 per-player query 미발생 확인

---

## 6) 구현 순서 (작업자용)

1. `injury_features.py` 생성 + 집계 함수 작성
2. `data_context.py`에 `injury_payload_by_player` 필드/주입 경로 추가
3. builder에서 payload map 생성 연결
4. 테스트 추가
5. 회귀 실행 후 머지

---

## 7) 후속 단계(다음 PR)

사전작업 완료 후 다음 PR에서는 아래만 하면 된다.
- `market_pricing._price_player()`
  - `INJURY_CURRENT_DISCOUNT` step
  - `INJURY_HISTORY_DISCOUNT` step
  - `HEALTH_CREDIT` step
- `team_utility._apply_risk_discount()`
  - 기존 age/term에 injury current/history risk 축 추가

즉, 다음 단계는 **수식 추가만** 하면 되도록 입력 인프라를 본 단계에서 완결한다.

---

## 8) 리스크 및 대응

- 리스크: injury_events 대량 조회 성능 저하
  - 대응: lookback 제한 + bulk query + 최소 컬럼 select
- 리스크: 저장 데이터 품질(잘못된 날짜/부위명)
  - 대응: 정규화+clamp+UNKNOWN 처리
- 리스크: 스키마 누락 환경
  - 대응: try/except + 기본 payload fail-open

