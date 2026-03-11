# SURPLUS_EXPENDABLE 완전 전환 실행계획 (레거시 제거 버전)

## 1) 목표와 전제

### 목표
- `SURPLUS_LOW_FIT`, `SURPLUS_REDUNDANT`를 완전히 제거하고, outgoing surplus는 `SURPLUS_EXPENDABLE` 단일 버킷으로 운영한다.
- 정렬/리스팅/딜생성/카운터오퍼/리페어 전 경로에서 신 버킷과 신 점수(`raw_trade_block_score`, `trade_block_score`)를 SSOT로 만든다.
- 레거시 fallback/alias/dual-read 코드를 남기지 않는다.

### 전제
- 기존 세이브/DB 호환성은 고려하지 않는다.
- 개발 단계 기준으로, "현재 코드가 깨지지 않고 게임이 정상 동작"하는 것을 최우선으로 한다.

---

## 2) 파일별 수정안

## 2.1 `trades/generation/asset_catalog.py`

### 변경 사항
1. `BucketId`에서 아래 제거
   - `SURPLUS_LOW_FIT`
   - `SURPLUS_REDUNDANT`
2. `_bucket_caps_for_posture()`에서 legacy key 제거
   - `SURPLUS_EXPENDABLE`만 유지
3. `_outgoing_priority_for_posture()`에서 legacy 버킷 제거
   - 우선순위 튜플에서 `SURPLUS_EXPENDABLE`만 남김
4. 버킷 산출 로직 단일화
   - `low_fit_ids`, `redundant_ids` 리스트 생성/정렬/cap 적용 제거
   - reason flag는 유지하되 버킷은 `SURPLUS_EXPENDABLE`만 생성
5. `bucket_members`에서 legacy key 제거
   - `SURPLUS_EXPENDABLE` 단일 항목만 유지

### 유의점
- `reason_flags` (`LOW_PEER_FIT`, `REDUNDANT_DEPTH`)는 디버그/설명성 목적이므로 삭제하지 않는다.
- 후보 재생성 시(`PlayerTradeCandidate(...)` 재할당) 필드 누락이 없도록 현 필드 전체 유지.

---

## 2.2 `trades/generation/dealgen/types.py`

### 변경 사항
1. `ai_use_expendable_priority_signals` 제거
   - 이미 신 로직 상시 사용 전제가 됐으므로 플래그 자체를 삭제
2. `ai_proactive_listing_bucket_thresholds`에서 legacy key 제거
   - posture별로 `SURPLUS_EXPENDABLE`, `FILLER_BAD_CONTRACT`, `VETERAN_SALE`(및 필요한 키)만 유지
3. 관련 주석에서 dual-read/호환 문구 제거

### 유의점
- 설정 생성부/테스트 fixture에서 legacy key를 직접 넣는 케이스가 있을 수 있으므로 테스트 전수 갱신 필요.

---

## 2.3 `trades/generation/dealgen/targets.py`

### 변경 사항
1. `use_expendable` 분기 제거
2. 정렬 점수 산출 고정
   - `priority_score = raw_trade_block_score if present else trade_block_score`
   - (필요 시 예외 안전장치로 `0.0`만 허용, `surplus_score` fallback 금지)
3. 정렬 키를 신 방식으로 고정
   - `(bucket_pri, -signal_boost, -priority_score, -timing_liquidity, -contract_pressure, market_total, player_id)`
4. `bucket_pri`에서 legacy 버킷 key 제거

### 유의점
- 문서화되지 않은 외부 호출이 `surplus_score`를 기대할 수 있으므로, 최소한 테스트 범위에서 SELL 타깃 추출 회귀 확인 필수.

---

## 2.4 `trades/orchestration/listing_policy.py`

### 변경 사항
1. `_PROACTIVE_ALLOWED_BUCKETS`에서 legacy 버킷 제거
2. `_priority_signal()`에서 플래그 분기 제거
   - raw(비정규) / normalized(trade_block_score) 기준으로 고정
3. `_resolve_bucket_threshold()`에서 `SURPLUS_EXPENDABLE` legacy fallback 제거
   - `SURPLUS_EXPENDABLE` 키를 직접 읽고, 없으면 `default`만 사용
4. horizon 보정 조건에서 legacy 버킷 체크 제거
   - `SURPLUS_EXPENDABLE` 기준으로 적용

### 유의점
- 리스팅 결과가 급격히 줄거나 늘 수 있으므로 posture별 threshold 값 재점검 필요.

---

## 2.5 `trades/generation/dealgen/utils.py`

### 변경 사항
1. `SURPLUS_BUCKETS_EFFECTIVE` 제거 또는 단일화
   - 상수명을 유지한다면 값은 `("SURPLUS_EXPENDABLE",)`로 축소
2. 버킷 순회 루프에서 legacy 버킷 가정 제거

### 유의점
- 상수 import를 쓰는 모든 모듈(repair/fit_swap/skeletons 등)에서 빈 튜플/인덱싱 오류가 나지 않는지 확인.

---

## 2.6 `trades/generation/dealgen/repair.py`

### 변경 사항
1. `SURPLUS_BUCKETS_EFFECTIVE[::-1]` 사용부가 legacy 의미를 전제로 하지 않도록 조정
   - 단일 버킷일 때도 동일 동작하도록 순회 로직 단순화
2. 스캔 버킷 우선순위에서 신 버킷 중심 재정렬

### 유의점
- repair는 실패 시 딜 생성 전체에 영향이 크므로, second-apron 경로 테스트를 반드시 재실행.

---

## 2.7 `trades/generation/dealgen/fit_swap.py`

### 변경 사항
1. 교체 후보 버킷 목록에서 legacy 제거
   - `("SURPLUS_EXPENDABLE", "CONSOLIDATE", "FILLER_CHEAP")` 순으로 단순화

### 유의점
- 교체 후보 풀이 줄어들어 스왑 성사율이 낮아질 수 있으니 test fixture의 기대값 보정 필요.

---

## 2.8 `trades/generation/dealgen/skeletons.py` (존재 시)

### 변경 사항
1. 버킷 순회/우선순위 하드코딩 중 legacy 버킷 제거
2. 신 버킷 단일 경로로 skeleton 구성

### 유의점
- skeleton 후보 수가 줄면 proposal 수량 저하가 생길 수 있어 캡/풀 파라미터 점검 필요.

---

## 2.9 `trades/counter_offer/config.py`

### 변경 사항
1. `player_sweetener_buckets`에서 legacy 제거
   - 예: `("FILLER_CHEAP", "SURPLUS_EXPENDABLE")`

### 유의점
- sweetener 후보가 줄어 counter 성공률이 떨어질 수 있으므로 조합 보정(후보 풀 수, market cap) 동시 검토.

---

## 2.10 테스트 파일 일괄 갱신

### 대상
- `trades/generation/test_asset_catalog_expendable.py`
- `trades/orchestration/test_proactive_listing.py`
- `trades/generation/dealgen/test_targets_priority_signals.py`
- `trades/generation/dealgen/*` 내 legacy 버킷 문자열 fixture

### 변경 사항
1. legacy 버킷 문자열 기대값 제거
2. fallback/dual-read 테스트 제거 또는 신 로직 고정 테스트로 대체
3. 핵심 회귀 테스트 추가
   - `SURPLUS_EXPENDABLE` 단일 버킷에서도 딜 생성이 유지되는지
   - SELL 정렬이 raw score 중심으로 안정 동작하는지
   - proactive listing threshold가 posture별로 과도하지 않은지

---

## 3) 리스크와 대응

## 리스크 A: 딜 생성량 급감
- 원인: legacy 버킷 제거로 후보 풀/순회 순서 축소
- 대응:
  1. dealgen smoke test에서 팀별 proposal 수 비교
  2. 필요 시 `SURPLUS_EXPENDABLE` cap/threshold 완화
  3. sweetener candidate_pool 상향으로 보정

## 리스크 B: 정렬 왜곡(예상과 다른 매물 노출)
- 원인: `surplus_score` fallback 제거 후 raw score 분포 편향
- 대응:
  1. posture별 `raw_trade_block_score` 분포(p10/p50/p90) 출력
  2. extreme 치우침 시 `protection_weight`/gate 상수 재튜닝

## 리스크 C: 런타임 KeyError/누락
- 원인: 설정/테스트 fixture에 legacy key 잔존
- 대응:
  1. 코드 변경 직후 `rg "SURPLUS_LOW_FIT|SURPLUS_REDUNDANT|ai_use_expendable_priority_signals" trades` 0건 확인
  2. 테스트 fixture 전수 치환

## 리스크 D: 리스팅 급증/급감
- 원인: listing threshold fallback 제거 영향
- 대응:
  1. posture별 threshold 재기준화
  2. AI 팀당 daily/active cap 유지로 폭주 방지

## 리스크 E: repair/fit_swap 성사율 하락
- 원인: 후보 버킷 단순화로 교체군 감소
- 대응:
  1. repair 실패 태그 카운트 추적
  2. 필요 시 `FILLER_BAD_CONTRACT`, `CONSOLIDATE` 우선순위 재조정

---

## 4) 안전한 작업 순서 (권장)

1. **타입/상수 정리**
   - `BucketId`, config keys, 공통 상수에서 legacy 제거
2. **생산 경로 정리**
   - `asset_catalog` 버킷 산출 단일화
3. **소비 경로 정리**
   - targets/listing_policy/repair/fit_swap/counter_offer를 신 버킷으로 고정
4. **테스트 정리**
   - legacy 관련 테스트 제거 + 신 로직 회귀 테스트 추가
5. **통합 검증**
   - `PYTHONPATH=. pytest -q trades`
   - 가능하면 트레이드 tick/smoke 시나리오 1회 실행

---

## 5) 완료 기준 (DoD)

- 코드에서 `SURPLUS_LOW_FIT`, `SURPLUS_REDUNDANT`, `ai_use_expendable_priority_signals` 문자열이 제거된다.
- outgoing surplus 관련 의사결정은 `SURPLUS_EXPENDABLE` + `raw/trade_block_score`만 사용한다.
- trades 테스트 스위트가 통과한다.
- 수동 smoke에서 트레이드 블록 노출/딜 생성/카운터오퍼/리페어가 정상 동작한다.
