# Trade Block Threshold + Weekly Cadence 구현 확정안

본 문서는 `docs/trade-block-threshold-weekly-update-plan-2026-03-08.md`를 실제 코드 패치로 내릴 수 있도록,
**파일별 수정 포인트를 확정 수준으로** 정리한 구현 계획서다.

---

## 0) 구현 목표(확정)

1. 트레이드 블록 proactive listing 후보는 허용 버킷 소속만으로는 부족하며,
   **팀 상황(posture/horizon/urgency) 기반 버킷 임계점(threshold) 이상인 선수만** 후보가 된다.
2. proactive listing 판정 cadence를 옵션으로 분리한다.
   - `DAILY`: 기존과 동일
   - `WEEKLY`: 주 단위(앵커 요일 + 7일 간격)로만 listing 평가
3. **트레이드 제안 생성 cadence는 절대 변경하지 않는다.**
   - listing cadence만 바꾼다.

---

## 1) 파일별 확정 수정사항


## A. `trades/generation/dealgen/types.py`

### A-1. `DealGeneratorConfig` 필드 추가 (확정)

`# --- proactive listing controls (AI)` 블록에 아래 필드를 추가한다.

```python
# listing cadence
ai_proactive_listing_cadence: str = "DAILY"  # DAILY | WEEKLY
ai_proactive_listing_anchor_weekday: int = 0  # 0=Mon .. 6=Sun

# threshold gating
ai_proactive_listing_threshold_enabled: bool = True
ai_proactive_listing_threshold_default: float = 0.55
ai_proactive_listing_bucket_thresholds: Dict[str, Dict[str, float]] = field(default_factory=lambda: {
    "AGGRESSIVE_BUY": {
        "SURPLUS_LOW_FIT": 0.30,
        "SURPLUS_REDUNDANT": 0.35,
        "CONSOLIDATE": 0.55,
        "FILLER_CHEAP": 0.65,
        "FILLER_BAD_CONTRACT": 0.80,
        "VETERAN_SALE": 0.90,
    },
    "SOFT_BUY": {
        "SURPLUS_LOW_FIT": 0.38,
        "SURPLUS_REDUNDANT": 0.42,
        "CONSOLIDATE": 0.60,
        "FILLER_CHEAP": 0.68,
        "FILLER_BAD_CONTRACT": 0.82,
        "VETERAN_SALE": 0.92,
    },
    "STAND_PAT": {
        "SURPLUS_LOW_FIT": 0.50,
        "SURPLUS_REDUNDANT": 0.55,
        "CONSOLIDATE": 0.70,
        "FILLER_CHEAP": 0.72,
        "FILLER_BAD_CONTRACT": 0.86,
        "VETERAN_SALE": 0.95,
    },
    "SOFT_SELL": {
        "SURPLUS_LOW_FIT": 0.40,
        "SURPLUS_REDUNDANT": 0.45,
        "CONSOLIDATE": 0.85,
        "FILLER_CHEAP": 0.62,
        "FILLER_BAD_CONTRACT": 0.70,
        "VETERAN_SALE": 0.45,
    },
    "SELL": {
        "SURPLUS_LOW_FIT": 0.32,
        "SURPLUS_REDUNDANT": 0.38,
        "CONSOLIDATE": 0.90,
        "FILLER_CHEAP": 0.58,
        "FILLER_BAD_CONTRACT": 0.62,
        "VETERAN_SALE": 0.35,
    },
})

# posture threshold modifiers
ai_proactive_listing_threshold_horizon_win_now_delta: float = -0.03
ai_proactive_listing_threshold_horizon_rebuild_delta: float = -0.05
ai_proactive_listing_threshold_urgency_cut: float = 0.75
ai_proactive_listing_threshold_urgency_delta: float = -0.03
ai_proactive_listing_threshold_cooldown_active_delta: float = 0.05
ai_proactive_listing_threshold_min: float = 0.10
ai_proactive_listing_threshold_max: float = 0.95
```

### A-2. 타입/구현 주의사항

- `Dict`, `field` import는 이미 존재하므로 재사용한다.
- cadence는 문자열로 두되, `listing_policy.py`에서 `.upper()`로 정규화한다.

---

## B. `trades/orchestration/market_state.py`

### B-1. trade_market schema 키 추가 (확정)

`_ensure_trade_market_schema()`에 아래 보강을 추가한다.

```python
m.setdefault("proactive_listing_meta", {})
if not isinstance(m.get("proactive_listing_meta"), dict):
    m["proactive_listing_meta"] = {}
```

### B-2. 목적

- 팀별 proactive listing 평가일(`last_eval_at`)을 안정적으로 저장/복원하기 위해 필요.
- 개발 단계 기준으로 기존 save/db 마이그레이션은 고려하지 않는다.
- `proactive_listing_meta`는 신규 실행 데이터 기준으로 생성/사용한다.

---

## C. `trades/orchestration/listing_policy.py`


### C-1. 신규 상수/헬퍼 추가 (확정)

#### (1) 버킷/후보 맵 생성 시 원본 버킷 보존

현재 `candidate_ids`만 모으는 로직을 변경해 `candidate_bucket_by_pid: Dict[str, str]`를 함께 만든다.

- 최초로 발견된 허용 버킷을 대표 버킷으로 저장한다.
- 이미 `seen` 처리 있으므로 deterministic 동작 유지.

#### (2) cadence gate 헬퍼

```python
def _should_run_proactive_listing_today(*, trade_market, team_id: str, today: date, config: Any) -> bool:
    cadence = str(getattr(config, "ai_proactive_listing_cadence", "DAILY") or "DAILY").upper()
    if cadence != "WEEKLY":
        return True

    anchor = int(getattr(config, "ai_proactive_listing_anchor_weekday", 0) or 0)
    anchor = max(0, min(6, anchor))
    if int(today.weekday()) != anchor:
        return False

    meta = trade_market.get("proactive_listing_meta") if isinstance(trade_market.get("proactive_listing_meta"), dict) else {}
    team_meta = meta.get(str(team_id).upper()) if isinstance(meta, dict) else None
    last_iso = team_meta.get("last_eval_at") if isinstance(team_meta, dict) else None
    last_d = _parse_iso_ymd(last_iso)
    if last_d is not None and (today - last_d).days < 7:
        return False
    return True
```

#### (3) cadence stamp 헬퍼

```python
def _stamp_proactive_listing_eval(*, trade_market: Dict[str, Any], team_id: str, today: date) -> None:
    meta = trade_market.get("proactive_listing_meta")
    if not isinstance(meta, dict):
        meta = {}
        trade_market["proactive_listing_meta"] = meta
    tid = str(team_id).upper()
    cur = meta.get(tid)
    if not isinstance(cur, dict):
        cur = {}
    cur["last_eval_at"] = today.isoformat()
    meta[tid] = cur
```

#### (4) threshold resolve 헬퍼

```python
def _resolve_bucket_threshold(*, bucket: str, team_situation: Any, config: Any) -> float:
    posture = str(getattr(team_situation, "trade_posture", "STAND_PAT") or "STAND_PAT").upper()
    horizon = str(getattr(team_situation, "time_horizon", "RE_TOOL") or "RE_TOOL").upper()
    urgency = _clamp01(getattr(team_situation, "urgency", 0.0))
    constraints = getattr(team_situation, "constraints", None)
    cooldown_active = bool(getattr(constraints, "cooldown_active", False))

    table = getattr(config, "ai_proactive_listing_bucket_thresholds", {}) or {}
    row = table.get(posture, {}) if isinstance(table, dict) else {}
    base = _safe_float((row.get(bucket) if isinstance(row, dict) else None), _safe_float(getattr(config, "ai_proactive_listing_threshold_default", 0.55), 0.55))

    if horizon == "WIN_NOW" and bucket in {"SURPLUS_LOW_FIT", "SURPLUS_REDUNDANT"}:
        base += _safe_float(getattr(config, "ai_proactive_listing_threshold_horizon_win_now_delta", -0.03), -0.03)
    elif horizon == "REBUILD" and bucket == "VETERAN_SALE":
        base += _safe_float(getattr(config, "ai_proactive_listing_threshold_horizon_rebuild_delta", -0.05), -0.05)

    u_cut = _safe_float(getattr(config, "ai_proactive_listing_threshold_urgency_cut", 0.75), 0.75)
    if urgency >= u_cut:
        base += _safe_float(getattr(config, "ai_proactive_listing_threshold_urgency_delta", -0.03), -0.03)

    if cooldown_active:
        base += _safe_float(getattr(config, "ai_proactive_listing_threshold_cooldown_active_delta", 0.05), 0.05)

    lo = _safe_float(getattr(config, "ai_proactive_listing_threshold_min", 0.10), 0.10)
    hi = _safe_float(getattr(config, "ai_proactive_listing_threshold_max", 0.95), 0.95)
    if lo > hi:
        lo, hi = hi, lo
    return max(lo, min(hi, base))
```

#### (5) threshold pass 헬퍼

```python
def _passes_listing_threshold(*, player: Any, bucket: str, team_situation: Any, config: Any) -> bool:
    if not bool(getattr(config, "ai_proactive_listing_threshold_enabled", True)):
        return True
    score = _clamp01(getattr(player, "surplus_score", 0.0))
    th = _resolve_bucket_threshold(bucket=bucket, team_situation=team_situation, config=config)
    return float(score) >= float(th)
```

> 1차 구현은 `surplus_score` 단일 기준으로 고정한다. (추가 프록시는 후속)


### C-2. `apply_ai_proactive_listings()` 본문 변경 (확정)

1. 함수 시작 직후 cadence gate:
   - `_should_run_proactive_listing_today(...)` false면 `[]` return.

2. 팀 상황 1회 조회:
   - `team_situation = tick_ctx.get_team_situation(tid)`
   - 기존 posture 계산(`ttl_days`)도 `team_situation.trade_posture` 사용으로 통일.

3. 후보 수집 단계:
   - `candidate_ids` + `candidate_bucket_by_pid` 동시 생성.

4. 필터 단계 후 threshold gate 추가:
   - `bucket = candidate_bucket_by_pid.get(p)`
   - bucket 없으면 skip
   - `_passes_listing_threshold(...)` false면 skip

5. 최종 반환 직전 cadence stamp:
   - 평가가 실행된 경우(0건 listing 포함) `_stamp_proactive_listing_eval(...)` 호출.
   - 구현 위치는 `if not rows: return []` 직전/직후를 조정해 반드시 stamp되게 구성:
     - `rows` 비어도 stamp 후 return
     - `rows` 있어 listing 수행 후 stamp

6. 기존 기능 유지:
   - active listing 제외, lock 제외, recent signing ban 제외, proactive cooldown 제외
   - team cap/daily cap 및 우선순위 정렬은 유지

---

## D. `trades/orchestration/test_proactive_listing.py`

### D-1. 설정 헬퍼 확장

`_cfg()` 기본값에 신규 config 필드 반영:
- cadence 관련
- threshold 관련
- bucket threshold table 기본값

### D-2. 테스트 케이스 추가 (확정)

1. `test_threshold_excludes_below_cutoff`
   - posture=`STAND_PAT`
   - bucket=`SURPLUS_LOW_FIT`, `surplus_score=0.4`
   - threshold=0.5로 설정
   - 기대: listing 안 됨

2. `test_threshold_allows_above_cutoff`
   - 같은 조건에서 `surplus_score=0.8`
   - 기대: listing 됨

3. `test_posture_specific_threshold_diff`
   - 동일 선수/score에 대해 posture=`AGGRESSIVE_BUY` vs `STAND_PAT`
   - 한쪽은 통과, 한쪽은 탈락하도록 threshold 구성

4. `test_weekly_cadence_skips_non_anchor_day`
   - cadence=`WEEKLY`, anchor=0(Mon), today=Tue
   - 기대: listing 안 됨

5. `test_weekly_cadence_stamps_last_eval_even_when_no_rows`
   - cadence=`WEEKLY`, anchor day,
   - rows 없도록 lock/ban 조건 설정
   - 기대: `trade_market["proactive_listing_meta"]["LAL"]["last_eval_at"] == today.isoformat()`

6. `test_weekly_cadence_skips_within_7_days_since_last_eval`
   - `last_eval_at`를 3일 전으로 prefill
   - 기대: skip

7. `test_daily_cadence_ignores_weekly_meta`
   - cadence=`DAILY`
   - `last_eval_at`가 있어도 기존처럼 평가 진행

### D-3. 회귀 보장

- 기존 3개 테스트는 유지하고, 신규 필드 추가로 깨지지 않게 `_cfg()`에서 기본값 제공.

---

## E. `trades/orchestration/tick_loop.py` (수정 없음)

- **코드 변경 없음(명시적 결정)**
- 이유: listing cadence 분리는 `apply_ai_proactive_listings()` 내부 gate로 해결한다.
- 트레이드 제안 생성(`gen.generate_for_team`) 흐름은 untouched 유지.

---

## 2) 권장 작업 순서 (비슷한 작업끼리 그룹화)


### Phase 1 — Config/State 스키마 기반 만들기

1. `trades/generation/dealgen/types.py`
   - 신규 config 필드 추가
2. `trades/orchestration/market_state.py`
   - `proactive_listing_meta` schema 기본값 추가

> 이유: listing_policy 구현 시 참조할 config/state 기반을 먼저 고정.


### Phase 2 — Listing 정책 로직 구현

3. `trades/orchestration/listing_policy.py`
   - cadence gate/stamp helper 추가
   - threshold resolve/pass helper 추가
   - `apply_ai_proactive_listings()` 후보 수집/필터/평가 흐름 반영

> 이유: 핵심 기능 변경이 한 파일에 집중되므로, 이 단계에서 동작을 완성.


### Phase 3 — 테스트 보강 및 회귀 확인

4. `trades/orchestration/test_proactive_listing.py`
   - 신규 케이스 7종 추가
   - 기존 테스트 회귀 유지

5. 테스트 실행
   - `python -m unittest trades.orchestration.test_proactive_listing`
   - 필요 시 관련 suite 추가 실행

> 이유: 변경 기능 대부분이 listing_policy + config 의존이므로, 단위 테스트를 마지막에 일괄 검증.


### Phase 4 — 문서 정합성 업데이트(선택)

6. `docs/trade-block-threshold-weekly-update-plan-2026-03-08.md`
   - 구현 완료 후 “제안” 표현을 “반영됨” 상태로 갱신(선택)

> 이유: 코드 우선, 문서 후정리.

---

## 3) 수용 기준(Definition of Done)

- [ ] threshold enabled 시, 임계점 미달 선수는 버킷에 있어도 listing 되지 않는다.
- [ ] posture/horizon/urgency/cooldown 보정이 threshold 계산에 반영된다.
- [ ] WEEKLY cadence에서 앵커 요일/7일 간격 규칙이 적용된다.
- [ ] WEEKLY 평가가 수행된 날은 결과 0건이어도 `last_eval_at`가 갱신된다.
- [ ] DAILY cadence는 기존 동작과 동일하다.
- [ ] 트레이드 제안 생성 경로/빈도는 기존과 동일하다.

