# BUY 타겟 점수 percentile 정규화 구현 계획

작성일: 2026-03-15  
대상 이슈: `트레이드_현실성_설득력_코드리뷰_2026-03-14.md`의 **13) 타겟 점수 정규화 상수 하드코딩**

---

## 1) 목표

`trades/generation/dealgen/targets.py`의 하드코딩 스케일

- `basketball_norm = (basketball_total + 15) / 45`

을 제거하고, 리그 현재 분포 기반 percentile 정규화로 교체한다.

핵심 목표:
1. 세이브/시즌 분포 변화에 자동 적응.
2. pre-score와 final-rank가 동일한 정규화 축을 공유.
3. 소표본/결측 상황에서 안정적으로 fallback.
4. 성능 저하 없이(틱당 1회 분포 계산 + 캐시) 동작.

---

## 2) 현재 코드에서 변경이 필요한 파일 목록

### A. 필수 수정 파일

1. `trades/generation/dealgen/targets.py`
   - 신규: 분포 통계 컨텍스트 빌드 함수
   - 신규: percentile 계산/보정 함수
   - 수정: `_player_core_score()`
   - 수정: `_cheap_pre_score()`
   - 수정: `select_targets_buy()`에서 정규화 컨텍스트 생성 및 전달

2. `trades/generation/dealgen/types.py`
   - `DealGeneratorConfig`에 percentile 정규화 관련 설정 추가

3. `trades/generation/dealgen/config.py`
   - config 로드/병합 경로에서 신규 설정 기본값 반영(필요 시)

### B. 테스트 수정/추가 파일

4. `trades/generation/dealgen/test_targets_buy_tiered_retrieval.py`
   - 기존 하드코딩 정규화 가정 테스트가 있으면 percentile 모드 기준으로 갱신
   - pre-score/최종 랭크 일관성 검증 테스트 추가

5. `trades/generation/dealgen/test_targets_buy_contract_value.py`
   - 계약 gap 영향 테스트가 percentile 정규화 도입 후에도 유지되는지 회귀 검증

### C. (선택) 문서/설정 안내

6. `docs/issue/트레이드_현실성_설득력_코드리뷰_2026-03-14.md`
   - 이슈 13 상태를 "해결됨"으로 업데이트(패치 완료 후)

---

## 3) 데이터 소스(어떤 파일/객체에서 정보를 가져올지)

percentile 정규화 계산에 필요한 입력은 아래 경로에서 확보한다.

1. 후보 풀(리그 분포 계산용)
   - 소스: `catalog.incoming_all_players`
   - 타입: `IncomingPlayerRef`
   - 정의 파일: `trades/generation/asset_catalog.py`
   - 사용 필드:
     - `basketball_total`
     - `tag` (role 보정 시)
     - `supply_items` (role 보정 확장 시)

2. 팀 need 연관 정보(기존 유지)
   - 소스: `tick_ctx.get_decision_context(buyer_id).need_map`
   - 파일: `trades/generation/dealgen/targets.py` 내 기존 로직 활용

3. config 값
   - 소스: `DealGeneratorConfig`
   - 파일: `trades/generation/dealgen/types.py`

---

## 4) 구체 수식 설계

아래 수식은 바로 구현 가능한 형태로 정의한다.

### 4.1 기본 percentile 정규화

입력:
- 후보 i의 raw 값: `x_i = basketball_total_i`
- 분포 샘플 집합: `X = {x_1, ..., x_n}` (n >= 1)

정렬:
- `X_sorted = sort(X)`

경험적 누적분포(ECDF) 기반 percentile:
- `count_lt = |{x in X_sorted : x < x_i}|`
- `count_eq = |{x in X_sorted : x = x_i}|`
- `p_i = (count_lt + 0.5 * count_eq) / n`

클램프:
- `p_i = clamp(p_i, eps, 1 - eps)`
- 권장 `eps = 0.01`

결과:
- `basketball_norm_i = p_i`

> 동점값이 많은 경우 `0.5 * count_eq`를 사용해 점프를 완화한다.

### 4.2 소표본 fallback

`n < min_samples` 일 때 percentile 신뢰도가 낮으므로 sigmoid fallback을 혼합한다.

sigmoid fallback:
- `s_i = sigmoid((x_i - c) / k)`
- `sigmoid(z) = 1 / (1 + exp(-z))`

파라미터:
- `c = buy_target_basketball_norm_fallback_center`
- `k = buy_target_basketball_norm_fallback_scale`

혼합 계수:
- `alpha = clamp((n - n0) / (n1 - n0), 0, 1)`
- 권장: `n0 = min_samples/2`, `n1 = min_samples`

최종:
- `basketball_norm_i = alpha * p_i + (1 - alpha) * s_i`

### 4.3 (옵션) role 보정 블렌드

리그 전체 분포 percentile `p_league`와 role 분포 percentile `p_role`를 블렌딩:

- `basketball_norm_i = (1 - beta) * p_league_i + beta * p_role_i`
- `beta = buy_target_basketball_norm_role_blend_alpha`

role 분포는 우선 `ref.tag` 기준으로 구현하고,
샘플 부족(`n_role < role_min_samples`)이면 `p_role = p_league`로 fallback.

### 4.4 pre-score / final-rank 적용 위치

- `_cheap_pre_score`:
  - 기존: `core_pre = 0.62 * basketball_norm + 0.38 * need_similarity`
  - 변경: 동일(단, `basketball_norm` 계산만 percentile 기반으로 대체)

- `_player_core_score`:
  - 기존: `w_fit * fit + w_market * basketball_norm + need_term`
  - 변경: 동일(단, `basketball_norm` 계산만 percentile 기반으로 대체)

즉, **가중치 체계는 유지하고 입력 정규화만 치환**하여 회귀 리스크를 줄인다.

---

## 5) 파일별 상세 작업 지시서

## 5.1 `trades/generation/dealgen/types.py`

`DealGeneratorConfig`에 아래 필드 추가:

```python
buy_target_basketball_norm_mode: str = "PERCENTILE"  # FIXED | PERCENTILE | HYBRID
buy_target_basketball_norm_eps: float = 0.01
buy_target_basketball_norm_min_samples: int = 40

buy_target_basketball_norm_fallback_center: float = 10.0
buy_target_basketball_norm_fallback_scale: float = 12.0

buy_target_basketball_norm_role_blend_alpha: float = 0.0
buy_target_basketball_norm_role_min_samples: int = 20
```

모드 의미:
- `FIXED`: 기존 `(x+15)/45` (호환/롤백용)
- `PERCENTILE`: ECDF percentile만 사용
- `HYBRID`: 소표본일 때 sigmoid fallback 혼합

## 5.2 `trades/generation/dealgen/config.py`

- config merge path에서 신규 필드가 누락되지 않도록 반영.
- 외부 사용자 설정 파일에서 값 override 가능하도록 기존 패턴 준수.

## 5.3 `trades/generation/dealgen/targets.py`

### (a) 신규 내부 구조/함수 추가

권장 함수 시그니처:

```python
@dataclass(frozen=True)
class _BasketballNormContext:
    mode: str
    eps: float
    values_sorted: Tuple[float, ...]
    by_tag_sorted: Dict[str, Tuple[float, ...]]
    min_samples: int
    role_min_samples: int
    fallback_center: float
    fallback_scale: float
    role_blend_alpha: float
```

```python
def _build_basketball_norm_context(
    refs: Sequence[IncomingPlayerRef],
    config: DealGeneratorConfig,
) -> _BasketballNormContext:
    ...
```

```python
def _percentile_from_sorted(values_sorted: Sequence[float], x: float, eps: float) -> float:
    ...
```

```python
def _sigmoid_fallback(x: float, center: float, scale: float) -> float:
    ...
```

```python
def _resolve_basketball_norm(
    ref: IncomingPlayerRef,
    ctx: _BasketballNormContext,
) -> float:
    ...
```

### (b) 기존 함수 수정

1. `_player_core_score()`
   - 인자에 `norm_ctx` 추가
   - `basketball_norm` 계산을 `_resolve_basketball_norm(ref, norm_ctx)`로 교체

2. `_cheap_pre_score()`
   - 인자에 `norm_ctx` 추가
   - 동일 치환

3. `_final_rank()`
   - `_player_core_score()` 호출 시 `norm_ctx` 전달

4. `select_targets_buy()`
   - `refs` 준비 후 1회 `norm_ctx = _build_basketball_norm_context(refs, config)` 생성
   - pre_score 산정 및 final rank 계산 모두 동일 `norm_ctx` 사용

### (c) 성능 가이드

- 정렬은 tick 당 1회만 수행.
- role 분포 dict도 tick 당 1회 구성.
- 후보별 계산은 O(log n) (bisect) 또는 O(1) 근사 캐시를 사용.

---

## 6) 테스트 계획 (파일별)

## 6.1 `trades/generation/dealgen/test_targets_buy_tiered_retrieval.py`

신규 테스트 권장:

1. `test_percentile_norm_orders_by_relative_rank_not_absolute_scale`
   - 같은 상대 순위를 갖는 두 시나리오(전체 raw 값 스케일 다름)에서 타겟 순위가 유사하게 유지되는지 검증.

2. `test_pre_score_and_final_rank_share_same_norm_context`
   - pre-score 상위 후보가 final-rank에서도 급격히 뒤집히지 않는지(정규화 일관성) 검증.

3. `test_small_sample_hybrid_fallback_is_stable`
   - 표본 수가 작을 때 HYBRID 모드에서 점수가 극단으로 몰리지 않는지 검증.

4. `test_fixed_mode_keeps_backward_compat`
   - `mode=FIXED`에서 기존 결과와 동일/유사 동작을 유지하는지 검증.

## 6.2 `trades/generation/dealgen/test_targets_buy_contract_value.py`

회귀 테스트:

5. `test_contract_gap_signal_survives_percentile_norm`
   - 계약 gap이 긍/부정 방향으로 순위에 반영되는 기존 성질이 유지되는지 검증.

---

## 7) 권장 작업 순서 (파일 경로 명시)

1. **설정 확장**
   - `trades/generation/dealgen/types.py`
   - `trades/generation/dealgen/config.py`

2. **정규화 컨텍스트/수식 구현**
   - `trades/generation/dealgen/targets.py`

3. **호출 경로 연결 (pre-score + final-rank 동시 적용)**
   - `trades/generation/dealgen/targets.py`

4. **단위 테스트 보강/회귀 테스트 수정**
   - `trades/generation/dealgen/test_targets_buy_tiered_retrieval.py`
   - `trades/generation/dealgen/test_targets_buy_contract_value.py`

5. **문서 상태 업데이트(선택)**
   - `docs/issue/트레이드_현실성_설득력_코드리뷰_2026-03-14.md`

---

## 8) 리스크 및 완화책

1. **리스크: 분포 계산 비용 증가**
   - 완화: tick 당 1회 캐시, bisect 기반 percentile 계산.

2. **리스크: 기존 밸런스 급변**
   - 완화: `mode=FIXED` 롤백 스위치 제공 + 기본 가중치 유지.

3. **리스크: 소표본/리빌딩 리그에서 percentile 불안정**
   - 완화: HYBRID 모드 + `min_samples` 게이트.

4. **리스크: role 분포 희소성**
   - 완화: role 샘플 부족 시 league percentile로 자동 fallback.

---

## 9) 완료 기준(Definition of Done)

1. `targets.py`에서 `(basketball_total + 15)/45` 직접 사용이 제거된다.
2. percentile 기반 정규화가 pre-score/final-rank에 공통 적용된다.
3. 신규 config로 FIXED/PERCENTILE/HYBRID 전환 가능하다.
4. 관련 테스트(기존+신규)가 통과한다.
5. 이슈 문서(선택)가 해결 상태로 갱신된다.

