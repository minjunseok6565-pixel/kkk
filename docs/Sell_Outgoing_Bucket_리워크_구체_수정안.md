# Sell Outgoing Bucket 리워크 구체 수정안 (구현용)

본 문서는 `Sell_Outgoing_Bucket_리워크_계획_정합버전.md`를 실제 코드 변경 단위로 분해한 구현 계획서다.
목표는 동일하다:
- `FILLER_BAD_CONTRACT`를 계약 부담 중심으로 재정의
- `VETERAN_SALE`를 타임라인 불일치 중심으로 재정의

---

## 1. 수정 대상 파일과 변경 범위

## 1-1) `trades/generation/asset_catalog.py` (핵심 로직)

### A. 상수/헬퍼 추가
- 위치: 기존 threshold 상수(`_TOP_TAG_MIN`, `_LOW_FIT_MAX`) 근처
- 추가 항목:
  - 정규화용 상수
    - `_NEGATIVE_MONEY_NORM_M = 12.0`
    - `_CONTRACT_GAP_PRESSURE_NORM = 0.08`  (cap share 8%를 강한 부담 기준으로 가정)
    - `_MARKET_NOW_NORM = 12.0`
  - 게이트 상수
    - `_BAD_CONTRACT_NEGATIVE_MONEY_MIN_M = 2.5`
    - `_BAD_CONTRACT_YEARS_MIN = 2.0`
    - `_BAD_CONTRACT_FLEX_PRESSURE_MIN = 0.55`
    - `_BAD_CONTRACT_CAP_GAP_PRESSURE_MIN = 0.35`
    - `_VETERAN_MARKET_NOW_MIN = 6.0` (기존 유지)
    - `_TIMELINE_MISMATCH_MIN = 0.45`

- 헬퍼 함수 추가:
  - `_clamp01(x: float) -> float`
  - `_norm(x: float, denom: float) -> float` (`clamp01(x/denom)`)
  - `_safe_team_signal(ts, name: str, default: float=0.0) -> float`

### B. 팀/선수별 스코어 함수 추가

#### (1) `FILLER_BAD_CONTRACT`용
```python
@dataclass(frozen=True, slots=True)
class BadContractEval:
    enter: bool
    score: float
    negative_money: float
    years_factor: float
    cap_gap_pressure: float
    team_flex_pressure: float
    expendability: float
```

```python
def _eval_bad_contract_candidate(*, c: PlayerTradeCandidate, ts: Any, contract_gap_cap_share: float) -> BadContractEval:
    negative_money = max(0.0, float(c.salary_m - c.market.total))
    years_factor = _clamp01(float(c.remaining_years) / 4.0)
    cap_gap_pressure = _norm(max(0.0, float(-contract_gap_cap_share)), _CONTRACT_GAP_PRESSURE_NORM)

    flexibility = _safe_team_signal(getattr(ts, "signals", None), "flexibility", 0.5)
    team_flex_pressure = _clamp01(1.0 - flexibility)
    expendability = _clamp01(float(c.surplus_score))

    negative_money_norm = _norm(negative_money, _NEGATIVE_MONEY_NORM_M)

    score = (
        0.45 * negative_money_norm
        + 0.20 * years_factor
        + 0.15 * cap_gap_pressure
        + 0.10 * team_flex_pressure
        + 0.10 * expendability
    )

    support_gate = (
        float(c.remaining_years) >= _BAD_CONTRACT_YEARS_MIN
        or team_flex_pressure >= _BAD_CONTRACT_FLEX_PRESSURE_MIN
        or cap_gap_pressure >= _BAD_CONTRACT_CAP_GAP_PRESSURE_MIN
    )
    enter = (negative_money >= _BAD_CONTRACT_NEGATIVE_MONEY_MIN_M) and support_gate

    return BadContractEval(
        enter=bool(enter),
        score=float(score),
        negative_money=float(negative_money),
        years_factor=float(years_factor),
        cap_gap_pressure=float(cap_gap_pressure),
        team_flex_pressure=float(team_flex_pressure),
        expendability=float(expendability),
    )
```

#### (2) `VETERAN_SALE`용
```python
@dataclass(frozen=True, slots=True)
class VeteranSaleEval:
    enter: bool
    score: float
    timeline_mismatch: float
    market_now_norm: float
    age_decline: float
    contract_window_risk: float
```

```python
def _timeline_horizon_pressure(ts: Any) -> float:
    horizon = str(getattr(ts, "time_horizon", "") or "").upper()
    if horizon == "REBUILD":
        return 1.0
    if horizon == "RE_TOOL":
        return 0.75
    if horizon == "WIN_NOW":
        return 0.20
    return 0.45
```

```python
def _eval_veteran_sale_candidate(*, c: PlayerTradeCandidate, ts: Any) -> VeteranSaleEval:
    age = float(c.snap.age or 0.0)
    age_decline = _clamp01((age - 28.0) / 8.0)   # 28~36 구간 점증

    horizon_pressure = _timeline_horizon_pressure(ts)
    years_factor = _clamp01(float(c.remaining_years) / 4.0)

    # horizon이 리빌드 성향일수록 고연령/장기계약 mismatch가 커짐
    timeline_mismatch = _clamp01(horizon_pressure * (0.60 * age_decline + 0.40 * years_factor))

    market_now_norm = _norm(float(c.market.now), _MARKET_NOW_NORM)

    re_sign_pressure = _safe_team_signal(getattr(ts, "signals", None), "re_sign_pressure", 0.0)
    expiring_risk = 1.0 if bool(c.is_expiring) else 0.0
    contract_window_risk = _clamp01(0.65 * expiring_risk + 0.35 * _clamp01(re_sign_pressure))

    score = (
        0.40 * timeline_mismatch
        + 0.25 * market_now_norm
        + 0.20 * age_decline
        + 0.15 * contract_window_risk
    )

    team_ok = (
        str(getattr(ts, "trade_posture", "") or "").upper() in {"SELL", "SOFT_SELL"}
        or str(getattr(ts, "time_horizon", "") or "").upper() == "REBUILD"
    )

    enter = team_ok and (float(c.market.now) >= _VETERAN_MARKET_NOW_MIN) and (timeline_mismatch >= _TIMELINE_MISMATCH_MIN)

    return VeteranSaleEval(
        enter=bool(enter),
        score=float(score),
        timeline_mismatch=float(timeline_mismatch),
        market_now_norm=float(market_now_norm),
        age_decline=float(age_decline),
        contract_window_risk=float(contract_window_risk),
    )
```

### C. 기존 bucket 선정부 교체
- 기존 `filler_bad` 계산 블록 교체:
  - `market.total <= 6.0` 단독 진입 제거
  - `_extract_player_value_breakdown`에서 이미 수집한 `incoming_player_value_by_id[pid]["contract_gap_cap_share"]` 사용
  - `score` 중심 정렬로 변경

정렬 키 제안:
```python
filler_bad.sort(key=lambda t: (-t[0].score, -t[0].negative_money, -t[1].salary_m, t[1].player_id))
```
(여기서 `t[0]`은 `BadContractEval`, `t[1]`은 candidate)

- 기존 `veteran` 계산 블록 교체:
  - `age>=29` 하드 게이트 제거
  - `_eval_veteran_sale_candidate`의 `enter` 조건 사용

정렬 키 제안:
```python
veteran.sort(key=lambda t: (-t[0].score, -t[1].market.now, -(t[1].snap.age or 0.0), t[1].player_id))
```

### D. 디버그 가시성(선택 권장)
- `PlayerTradeCandidate`에 필드 추가 대신, 최소 변경으로 `asset_catalog` 내부 로컬 맵 사용:
  - `bad_contract_eval_by_pid: Dict[str, BadContractEval]`
  - `veteran_eval_by_pid: Dict[str, VeteranSaleEval]`
- 추후 필요 시 logger에서 해당 맵을 사용해 reason code 출력.

---

## 1-2) `trades/generation/dealgen/types.py` (샘플/테스트 데이터 정합)
- mock `bucket_scores`를 쓰는 테스트/샘플이 있으므로 값 범위가 새 점수 스케일(0~1)에 맞는지 점검.
- 필수 변경은 아님. 다만 회귀 테스트에서 경계값 민감하면 fixture 값 조정.

## 1-3) `trades/orchestration/test_proactive_listing.py` (회귀 안정화)
- 버킷 우선순위/고정 점수에 의존한 테스트가 있으면,
  - 기존 의도(우선순위, posture 제약)는 유지하고,
  - 구체 점수값은 덜 하드코딩하도록 완화(assert 범위 또는 상대순위 중심).

---

## 2. 계산 로직 요약 (최종안)

## 2-1) FILLER_BAD_CONTRACT
- 핵심: `negative_money` 필수 + (기간/유연성압박/cap gap) 보조게이트


negative_money = max(0, salary_m - market.total)
negative_money_norm = clamp01(negative_money / 12.0)
years_factor = clamp01(remaining_years / 4.0)
cap_gap_pressure = clamp01(max(0, -contract_gap_cap_share) / 0.08)
team_flex_pressure = clamp01(1 - flexibility)
expendability = clamp01(surplus_score)

bad_contract_score =
    0.45*negative_money_norm +
    0.20*years_factor +
    0.15*cap_gap_pressure +
    0.10*team_flex_pressure +
    0.10*expendability

enter if:
    negative_money >= 2.5
    and (
        remaining_years >= 2.0
        or team_flex_pressure >= 0.55
        or cap_gap_pressure >= 0.35
    )

## 2-2) VETERAN_SALE
- 핵심: 팀 타임라인과 선수 나이/계약창 mismatch + 현재가치 필터

horizon_pressure = {REBUILD:1.0, RE_TOOL:0.75, WIN_NOW:0.20, default:0.45}
age_decline = clamp01((age - 28.0) / 8.0)
years_factor = clamp01(remaining_years / 4.0)
timeline_mismatch = clamp01(horizon_pressure * (0.60*age_decline + 0.40*years_factor))
market_now_norm = clamp01(market.now / 12.0)
contract_window_risk = clamp01(0.65*is_expiring + 0.35*re_sign_pressure)

veteran_sale_score =
    0.40*timeline_mismatch +
    0.25*market_now_norm +
    0.20*age_decline +
    0.15*contract_window_risk

team_ok = posture in {SELL, SOFT_SELL} or horizon == REBUILD
enter if:
    team_ok
    and market.now >= 6.0
    and timeline_mismatch >= 0.45

---

## 3. 테스트 수정안 (구체)

## 3-1) 신규 테스트 파일
- `trades/generation/test_asset_catalog_outgoing_rework.py` 추가

테스트 케이스:
1. `test_bad_contract_excludes_low_value_cheap_bench_when_not_overpaid`
2. `test_bad_contract_prefers_negative_money_plus_long_term`
3. `test_veteran_sale_depends_on_timeline_not_age_cut_only`
4. `test_veteran_sale_requires_market_now_and_mismatch_gate`

## 3-2) 기존 테스트 영향
- `asset_catalog` 버킷 결과를 직접 단정하는 테스트가 있다면,
  - 변경 전 하드컷 기준을 새 게이트 기준으로 대체.
  - 단, posture cap / priority 관련 테스트는 그대로 유지.

---

## 4. 단계별 적용 순서
1. `asset_catalog.py`에 helper/eval dataclass/함수 추가
2. `FILLER_BAD_CONTRACT`/`VETERAN_SALE` bucket 선정 블록 교체
3. 신규 테스트 추가 + 기존 깨진 테스트 보정
4. 튜닝: threshold 상수만 조정(로직 구조는 고정)

---

## 5. 적용 후 기대 동작 변화 (한 줄 요약)
- 이전: "싼 선수"도 bad contract로 잡히는 경우가 있었음 / 29세 이상은 비교적 자동으로 veteran sale에 걸렸음
- 이후: "팀이 실제로 정리하고 싶은 계약"과 "팀 타임라인과 안 맞는 가치 자산"이 더 우선적으로 포착됨
