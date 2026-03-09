# SURPLUS_EXPENDABLE 구체 수정안 (파일별 구현 명세)

본 문서는 `docs/SURPLUS_EXPENDABLE_통합_적용_계획.md`를 구현 가능한 작업 단위로 분해한 **실행 명세서**다.
목표는 기존 시스템 호환성을 유지하면서 `SURPLUS_LOW_FIT`/`SURPLUS_REDUNDANT`를 `SURPLUS_EXPENDABLE`로 통합하는 것이다.

---

## 0) 구현 범위와 원칙

- 런타임 파손 방지를 위해 **2단계 배포**를 전제로 한다.
  - Step A: 내부 신호/점수/게이트 도입 + alias 유지
  - Step B: 외부 기본 버킷을 `SURPLUS_EXPENDABLE`로 전환
- 기존 문자열 의존 코드가 많으므로, 최소 1 릴리즈 동안 아래 alias를 유지한다.
  - `SURPLUS_LOW_FIT` → `SURPLUS_EXPENDABLE`
  - `SURPLUS_REDUNDANT` → `SURPLUS_EXPENDABLE`

---

## 1) 데이터 구조 변경 명세

## 1.1 `trades/generation/asset_catalog.py`

### A. Bucket 타입 확장

- `BucketId`에 `SURPLUS_EXPENDABLE` 추가.
- 기존 2개 버킷은 제거하지 않고 유지(호환 기간).

### B. `PlayerTradeCandidate` 필드 확장

기존 필드 유지 + 아래 필드 추가:

- `fit_vs_peers: float`
- `misfit_peer: float`
- `redundancy_peer: float`
- `redundancy_peer_norm: float`
- `peer_cover: float`
- `dependence_risk: float`
- `core_proxy: float`
- `identity_risk_proxy: float`
- `minutes_squeeze_proxy: float`
- `contract_pressure: float`
- `raw_trade_block_score: float`
- `trade_block_score: float`  # normalized/clamped view
- `surplus_reason_flags: Tuple[str, ...]`
- `surplus_protection_flags: Tuple[str, ...]`

호환을 위해 기존 `fit_vs_team`, `surplus_score`, `buckets`는 그대로 유지.

### C. Configurable threshold 상수 추가

파일 상단 상수 또는 config 연동(권장: `DealGeneratorConfig`로 이동):

- `REDUNDANCY_GATE = 0.52`
- `REPLACEABLE_GATE = 0.58`
- `SQUEEZE_GATE = 0.55`
- `CONTRACT_GATE = 0.60`
- `TRADE_BLOCK_SCORE_GATE_BY_POSTURE`
  - `SELL: 0.48`
  - `SOFT_SELL: 0.52`
  - `STAND_PAT: 0.58`
  - `SOFT_BUY: 0.64`
  - `AGGRESSIVE_BUY: 0.68`

---

## 2) 계산 로직 상세 명세

## 2.1 leave-one-out 계산 최적화

팀 단위 전처리:

- `team_supply_total[tag] = Σ supply_i[tag]`

선수 `p`별:

- `peer_supply_without_p[tag] = max(0, team_supply_total[tag] - supply_p[tag])`
- `peer_need_map(p)`는 기존 `fit_engine` 사용 경로에 맞춰 아래 중 하나 선택
  1) 권장: `fit_engine.compute_team_need_from_supply(peer_supply_without_p, roster_meta)`
  2) 대안: 기존 `dc.need_map`을 기준으로 `supply_p` 기여분을 역보정하는 근사 함수

> 구현 우선순위: 1) 가능하면 정확 계산, 불가 시 2)로 시작하고 TODO 남김.

## 2.2 신호 정의

`eps = 1e-9`

- `fit_vs_peers = fit_engine.score_fit(peer_need_map, supply_p)`
- `misfit_peer = 1 - fit_vs_peers`

- `redundancy_peer = Σ (supply_p[tag] * (1 - peer_need_map[tag]))`
- `redundancy_peer_norm = redundancy_peer / (Σ supply_p[tag] + eps)`

- `peer_cover = Σ min(supply_p[tag], peer_supply_without_p[tag]) / (Σ supply_p[tag] + eps)`

- `dependence_risk = clamp01(fit_vs_peers - fit_vs_team)`

- `contract_pressure` (계약 전용 신호; expiring 미포함)
  - `contract_gap_cap_share`를 1순위로 사용
  - fallback: `expected_cap_share_avg - actual_cap_share_avg`
  - 예시 구현:

```python
if has(contract_gap_cap_share):
    # gap<0: 실제 연봉 비중이 기대치보다 높음(=부담), gap>0: 팀친화 계약
    contract_pressure = clamp01((-contract_gap_cap_share) / 0.06)
elif has(expected_cap_share_avg) and has(actual_cap_share_avg):
    contract_pressure = clamp01((actual_cap_share_avg - expected_cap_share_avg) / 0.06)
else:
    # 마지막 fallback(정보 부족 시): 순수 salary burden 근사
    contract_pressure = clamp01(max(0.0, salary_m - basketball_fair_value_m) / 10.0)
```

  - `basketball_fair_value_m`는 가능하면 `basketball_total` 기반 역매핑 함수로 추정하고,
    역매핑이 없으면 이 fallback 분기는 비활성화(0.0) 권장.

- `minutes_squeeze_proxy`
  - 동일 `top_tags`를 공유하는 팀 동료 수를 이용
  - `overlap_count = #teammates(top_tag 교집합 >=1)`
  - `minutes_squeeze_proxy = clamp01((overlap_count - 1) / 6)`

- `timing_liquidity` (거래 타이밍/유동성 신호; 계약부담과 분리)
  - `timing_liquidity = 1.0 if is_expiring else 0.0`

- `core_proxy` (농구 코어성 우선; 계약가치와 분리)
  - 1순위: 팀 내 `market.now` 내림차순 rank
  - 보조: `basketball_total` 정규화값을 가산(가능 시)
  - 예시 구현:

```python
rank_now = rank_desc(player.market.now, team_players, key=lambda c: c.market.now)
core_from_now = clamp01(1 - (rank_now - 1) / max(1, team_size - 1))

if has(basketball_total):
    basketball_core = clamp01((basketball_total + 15.0) / 45.0)
    core_proxy = clamp01(0.75 * core_from_now + 0.25 * basketball_core)
else:
    core_proxy = core_from_now
```

  - fallback: `market.now`가 누락된 경우에만 `market.total` rank를 임시 사용.

- `identity_risk_proxy`
  - 팀 정체성 태그: `team_supply_total` 상위 3개 태그
  - 선수의 해당 태그 공급 비중 합산
  - `identity_risk_proxy = clamp01(identity_contrib_share)`

## 2.3 최종 점수

### A. expendable_base (높을수록 매도 적합)

```python
expendable_base = (
    0.40 * redundancy_peer_norm
    + 0.20 * misfit_peer
    + 0.20 * peer_cover
    + 0.10 * contract_pressure
    + 0.10 * minutes_squeeze_proxy
)

# timing_liquidity는 점수 본체에 혼합하지 않고 gate/sort 보정 신호로만 사용
```

### B. protection_score (높을수록 보호 필요)

```python
protection_score = (
    0.40 * core_proxy
    + 0.35 * dependence_risk
    + 0.25 * identity_risk_proxy
)
```

### C. posture 보정 포함 trade_block_score (raw + normalized 분리)

```python
protection_weight_by_posture = {
  "SELL": 0.85,
  "SOFT_SELL": 0.95,
  "STAND_PAT": 1.00,
  "SOFT_BUY": 1.10,
  "AGGRESSIVE_BUY": 1.18,
}

raw_trade_block_score = (
    expendable_base
    - protection_weight_by_posture[posture] * protection_score
    + 0.05  # baseline shift
)

# UI/threshold 호환용 normalized view
trade_block_score = clamp01(raw_trade_block_score)
```

운영 원칙:
- **정렬/랭킹/분포 분석은 `raw_trade_block_score` 우선 사용**
- **임계치 게이트/표시는 `trade_block_score`(normalized) 사용**
- 팀/포지션별 스윕 튜닝 시 raw 분포(p10/p50/p90) 로그를 함께 저장

`surplus_score`는 호환상 기존대로 유지(`1 - fit_vs_team`).
단, 신규 정책에서는 `trade_block_score`/`raw_trade_block_score`를 우선 사용.

---

## 3) 버킷 진입 규칙 명세

## 3.1 SURPLUS_EXPENDABLE 진입

```python
enter_expendable = (
    not hard_protected
    and trade_block_score >= posture_score_gate  # normalized gate
    and (
        redundancy_peer_norm >= REDUNDANCY_GATE
        or peer_cover >= REPLACEABLE_GATE
        or minutes_squeeze_proxy >= SQUEEZE_GATE
        or contract_pressure >= CONTRACT_GATE
        or (timing_liquidity >= 1.0 and posture in {"SELL", "SOFT_SELL"})
    )
)
```

### hard_protected 정의

```python
hard_protected = (
    core_proxy >= 0.82
    or identity_risk_proxy >= 0.78
    or dependence_risk >= 0.68
)
```

> 주의: `core_proxy`는 `market.total`이 아니라 `market.now` 중심으로 계산해
> 계약 효율/미래가치가 코어 보호에 과도하게 개입하지 않도록 한다.

핵심 제약: **misfit_peer 단독으로는 진입 불가**.

## 3.2 reason/protection flags 부여

- reason flags
  - `LOW_PEER_FIT` if `misfit_peer >= 0.55`
  - `REDUNDANT_DEPTH` if `redundancy_peer_norm >= 0.55`
  - `ROLE_BLOCKED` if `minutes_squeeze_proxy >= 0.55`
  - `EXPENSIVE_FOR_ROLE` if `contract_pressure >= 0.60`
  - `TIMING_WINDOW` if `timing_liquidity >= 1.0`
- protection flags
  - `CORE_PLAYER` if `core_proxy >= 0.75`
  - `IDENTITY_ANCHOR` if `identity_risk_proxy >= 0.70`
  - `WEAKNESS_EXPOSURE_RISK` if `dependence_risk >= 0.60`

---

## 4) 버킷/정렬/정책 반영 파일별 수정

## 4.1 `trades/generation/asset_catalog.py`

1. `BucketId`에 `SURPLUS_EXPENDABLE` 추가
2. 신규 신호 계산 함수 추가
   - `_compute_peer_signals(...)`
   - `_compute_protection_signals(...)`
3. 기존 `low_fit_ids`, `redundant_ids` 계산은 내부 reason 분류로 축소
4. `expendable_ids`를 신규 게이트로 생성
5. `bucket_members` 구성
   - 신규: `SURPLUS_EXPENDABLE`
   - 호환: `SURPLUS_LOW_FIT`, `SURPLUS_REDUNDANT`는 `expendable_ids`의 서브셋 매핑으로 제공
6. `PlayerTradeCandidate` 재생성 시 신규 필드 채움

## 4.2 `trades/generation/dealgen/targets.py`

SELL 후보 정렬키 변경:

기존
- `(..., -surplus_score, -expiring, market_total, player_id)`

변경
- `(..., -raw_trade_block_score, -timing_liquidity, -contract_pressure, market_total, player_id)`

버킷 priority에 `SURPLUS_EXPENDABLE` 추가(기존 surplus 둘보다 상위).

## 4.3 `trades/orchestration/listing_policy.py`

- `_PROACTIVE_ALLOWED_BUCKETS`에 `SURPLUS_EXPENDABLE` 추가
- bucket threshold 조회 시 fallback 순서:
  1) `SURPLUS_EXPENDABLE`
  2) (없으면) `SURPLUS_LOW_FIT`/`SURPLUS_REDUNDANT` 평균 또는 max
- `_bucket_priority_key`가 `raw_trade_block_score` 우선 사용하도록 변경
  - 없으면 `trade_block_score` -> `surplus_score` 순 fallback

## 4.4 `trades/generation/dealgen/types.py`

`ai_proactive_listing_bucket_thresholds`에 `SURPLUS_EXPENDABLE` 키 추가:

- `SELL: 0.40`
- `SOFT_SELL: 0.46`
- `STAND_PAT: 0.56`
- `SOFT_BUY: 0.62`
- `AGGRESSIVE_BUY: 0.66`

구버킷 키는 호환 기간 유지.

## 4.5 `trades/counter_offer/config.py`

`player_sweetener_buckets` 기본값 앞쪽에 `SURPLUS_EXPENDABLE` 추가:

```python
("FILLER_CHEAP", "SURPLUS_EXPENDABLE", "SURPLUS_LOW_FIT", "SURPLUS_REDUNDANT")
```

## 4.6 `trades/generation/dealgen/utils.py`, `repair.py`, `fit_swap.py`, `skeletons.py`

버킷 순회 공통 상수 도입:

```python
SURPLUS_BUCKETS_EFFECTIVE = ("SURPLUS_EXPENDABLE", "SURPLUS_LOW_FIT", "SURPLUS_REDUNDANT")
```

기존 하드코딩 tuple을 공통 상수로 치환.

---

## 5) 테스트 수정 명세

## 5.1 신규 테스트

1. `trades/generation/test_asset_catalog_expendable.py`
   - `test_low_fit_alone_does_not_enter_expendable`
   - `test_core_identity_player_is_protected`
   - `test_redundant_replaceable_enters_expendable`

2. `trades/orchestration/test_proactive_listing.py` 확장
   - `SURPLUS_EXPENDABLE` threshold 적용 검증
   - 구버킷 fallback 검증

3. `trades/generation/dealgen/test_targets_priority_signals.py` 확장
   - `raw_trade_block_score` 정렬 우선 검증

## 5.2 회귀 테스트 포인트

- alias 모드에서 기존 `SURPLUS_LOW_FIT` 참조 테스트가 계속 통과하는지 확인
- 버킷 비어도 딜 생성 파이프라인이 fail-open이 아닌 deterministic fallback 하는지 확인

---

## 6) 마이그레이션/배포 순서

1. 릴리즈 R1: 신규 신호/점수/게이트 + alias + 로그
2. 릴리즈 R2: 정책 기본값을 `SURPLUS_EXPENDABLE` 중심으로 전환
3. 릴리즈 R3: 구버킷 내부 전용화(외부 노출 deprecate)

각 릴리즈에서 KPI 비교:

- SELL 상위 10명 중 high-core 비율
- trade block 등록 선수의 평균 `trade_block_score` + raw 분포(p10/p50/p90)
- posture별 딜 생성 수/성사율

---

## 7) 구현 체크리스트

- [ ] `BucketId`/config/priority에 `SURPLUS_EXPENDABLE` 추가
- [ ] `PlayerTradeCandidate` 신규 필드 추가 및 직렬화 영향 확인
- [ ] leave-one-out 계산 도입 (성능 O(N·T) 유지)
- [ ] 게이트/보호 플래그/사유 플래그 적용
- [ ] SELL 정렬 키 `raw_trade_block_score` 전환
- [ ] raw/normalized 동시 저장 및 로깅
- [ ] listing threshold resolver에 신규 키 + fallback 추가
- [ ] counter-offer/dealgen 버킷 순회 상수화
- [ ] 테스트 추가/갱신 및 회귀 통과


---

## 8) 권장 작업 순서 (배치 단위)

아래 순서는 구현 리스크를 낮추면서도, 한 번에 의미 있는 단위를 완성할 수 있도록 묶은 배치 기준이다.
각 배치마다 **수정 파일**과 **문서 내 참조 섹션**을 함께 명시한다.

### Batch 1 — 데이터 모델/점수 기반 확장 (기초 토대)

**목표**
- 신규 신호/점수 계산이 가능한 구조를 먼저 만든다.
- 하위 모듈 변경 전, `asset_catalog`에서 필요한 값을 모두 생산/보관한다.

**작업 파일**
- `trades/generation/asset_catalog.py`

**반드시 참조할 문서 섹션**
- `1.1 trades/generation/asset_catalog.py` (Bucket 타입/후보 필드 확장)
- `2.1 leave-one-out 계산 최적화`
- `2.2 신호 정의`
- `2.3 최종 점수` (raw/normalized 분리 포함)
- `3.1 SURPLUS_EXPENDABLE 진입`
- `3.2 reason/protection flags`
- `4.1 trades/generation/asset_catalog.py`

**산출물 체크 포인트**
- `PlayerTradeCandidate`에 신규 필드(`raw_trade_block_score`, `trade_block_score` 포함) 반영
- `SURPLUS_EXPENDABLE` 생성 + 구버킷 alias 매핑 동시 제공
- `hard_protected`/gate/reason flag 계산이 후보 객체에 반영

---

### Batch 2 — 소비 경로 정렬/정책 동기화 (노출 동작 정합)

**목표**
- Catalog에서 만든 신규 점수를 실제 SELL 후보 정렬/공개 매물 정책이 사용하도록 연결한다.
- raw score 우선 정렬 원칙을 적용한다.

**작업 파일**
- `trades/generation/dealgen/targets.py`
- `trades/orchestration/listing_policy.py`
- `trades/generation/dealgen/types.py`

**반드시 참조할 문서 섹션**
- `2.3 최종 점수`의 운영 원칙(raw 우선 / normalized gate)
- `4.2 trades/generation/dealgen/targets.py`
- `4.3 trades/orchestration/listing_policy.py`
- `4.4 trades/generation/dealgen/types.py`

**산출물 체크 포인트**
- SELL 정렬 키: `raw_trade_block_score` 우선 적용
- proactive listing이 `SURPLUS_EXPENDABLE` + fallback 체인을 사용
- posture별 threshold 테이블에 `SURPLUS_EXPENDABLE` 키 반영

---

### Batch 3 — 주변 모듈 호환/버킷 순회 통일 (연쇄 파손 방지)

**목표**
- counter-offer/repair/skeleton 등 버킷 소비 지점이 새 버킷과 alias를 함께 처리하도록 통일한다.

**작업 파일**
- `trades/counter_offer/config.py`
- `trades/generation/dealgen/utils.py`
- `trades/generation/dealgen/repair.py`
- `trades/generation/dealgen/fit_swap.py`
- `trades/generation/dealgen/skeletons.py`

**반드시 참조할 문서 섹션**
- `4.5 trades/counter_offer/config.py`
- `4.6 ... utils.py, repair.py, fit_swap.py, skeletons.py`

**산출물 체크 포인트**
- `SURPLUS_BUCKETS_EFFECTIVE` 상수 기반으로 순회 통일
- 새 버킷 우선 + 구버킷 fallback 동작 확인

---

### Batch 4 — 테스트/회귀 고정 + 관측 지표 연결 (안정화)

**목표**
- 신규 로직의 핵심 위험(LOW_FIT 단독 진입, 코어 보호 실패, 정렬 역전)을 테스트로 고정한다.
- rollout에서 필요한 KPI/raw 분포 로그를 확인 가능하게 만든다.

**작업 파일**
- `trades/generation/test_asset_catalog_expendable.py` (신규)
- `trades/orchestration/test_proactive_listing.py` (확장)
- `trades/generation/dealgen/test_targets_priority_signals.py` (확장)

**반드시 참조할 문서 섹션**
- `5.1 신규 테스트`
- `5.2 회귀 테스트 포인트`
- `6) 마이그레이션/배포 순서`
- `7) 구현 체크리스트`

**산출물 체크 포인트**
- `raw_trade_block_score` 정렬 우선 테스트 통과
- alias 모드 회귀 테스트 통과
- KPI 수집 항목(평균 + raw p10/p50/p90) 확인

---

### Batch 5 — 릴리즈 단계 적용 (운영 전환)

**목표**
- 코드 반영 후 운영에서 안전하게 단계 전환한다.

**작업 파일**
- 코드 변경 없음(설정/운영 플래그/릴리즈 체크리스트 중심)

**반드시 참조할 문서 섹션**
- `6) 마이그레이션/배포 순서`
- `7) 구현 체크리스트`

**산출물 체크 포인트**
- R1: 신호/게이트/alias + 로그
- R2: `SURPLUS_EXPENDABLE` 중심 정책 기본값 전환
- R3: 구버킷 외부 노출 축소(deprecate)

