# 팀 티어 분류 전환 실행계획 (순위 기반 + 소프트 경계, 레거시 즉시 삭제)

작성일: 2026-03-21

---

## 0) 목표/원칙

### 목표
- `blended score + 고정 threshold` 방식에서 벗어나,
  **리그 30팀 상대 순위(rank) 기반 티어 분류**로 전환한다.
- 경계는 하드 컷이 아니라 **소프트 경계(완충 구간)**로 처리한다.
- 개발 단계 전제에 맞춰 **점진 마이그레이션 없이 레거시 로직 즉시 제거**한다.

### 비목표
- feature flag, dual-read, fallback 경로 추가하지 않는다.
- 기존 threshold 방식과 동시 운영하지 않는다.

---

## 1) 신규 분류 모델(확정안)

## 1-1. 공통 점수는 유지
- 기존 `overall_score`, `performance_score`, `w_perf`, `blended` 계산은 유지.
- 단, `blended -> tier` 매핑만 교체.

## 1-2. 티어 기준은 rank quota로 정의
- 기본 quota(합 30):
  - CONTENDER: 5팀
  - PLAYOFF_BUYER: 5팀
  - FRINGE: 8팀
  - REBUILD: 7팀
  - TANK: 5팀
- `RESET`은 기본 quota에 포함하지 않고 **별도 특수 규칙**으로 유지.

## 1-3. 소프트 경계 규칙
- 경계 지점(예: 5/6, 10/11, 18/19, 25/26)마다 완충 폭 `boundary_width_rank`를 둔다.
- 팀의 `rank_float`(동점 처리 후 실수 순위)와 경계 거리 `d`로 경계 강도 계산:
  - `soft = sigmoid(-(d / tau_rank))`
- 경계 인접 구간에서는 상/하 티어를 점진 전환:
  - 상위 티어 점수 `p_up`, 하위 티어 점수 `p_down = 1-p_up`
  - 최종 티어는 deterministic tie-break(팀 ID)로 선택하거나, 확률 대신 점수 우세 선택.
- 권장 초기값:
  - `boundary_width_rank = 1.25`
  - `tau_rank = 0.55`

> 구현 단순화를 위해 1차는 “확률 샘플링” 대신 “연속 점수 비교 후 argmax” 방식 권장.

## 1-4. RESET 적용 순서
- 순위 기반 기본 tier 산출 후,
- 기존 reset 조건 충족 시 `tier in {CONTENDER, PLAYOFF_BUYER, FRINGE}`에서만 `RESET`으로 override.
- `REBUILD/TANK`는 reset으로 바꾸지 않음(기존 의미 유지).

---

## 2) 파일별 수정 계획 (레거시 삭제 포함)

## 2-1. `data/team_situation_config.py`

### A. 삭제할 항목
- `tier_threshold_contender`
- `tier_threshold_playoff_buyer`
- `tier_threshold_fringe`
- `tier_threshold_rebuild`

### B. 추가할 항목
`TeamSituationTierModelConfig`에 아래 필드 추가:
- `rank_quota_contender: int = 5`
- `rank_quota_playoff_buyer: int = 5`
- `rank_quota_fringe: int = 8`
- `rank_quota_rebuild: int = 7`
- `rank_quota_tank: int = 5`
- `rank_boundary_width: float = 1.25`
- `rank_boundary_tau: float = 0.55`
- `rank_tie_breaker: str = "team_id"`  # deterministic

### C. 검증 함수 추가
- quota 합이 30인지 검증하는 내부 helper 추가(초기화 시 assert/ValueError).

---

## 2-2. `data/team_situation.py`

### A. 삭제할 함수/로직
1. `_map_blended_score_to_tier(blended)` 함수 **완전 삭제**.
2. `_classify_and_build_outputs()` 내부의 아래 레거시 호출 삭제:
   - `tier = _map_blended_score_to_tier(blended)`

### B. 신규 helper 추가
1. `_build_league_blended_table(self) -> List[Dict[str, Any]]`
   - 30팀 전체의 아래를 계산/수집:
     - `team_id`, `overall_score`, `performance_score`, `w_perf`, `blended`
   - 정렬 기준: `blended desc`, 동점 시 `team_id asc`.

2. `_compute_rank_float_map(rows) -> Dict[str, float]`
   - 동점 처리 포함 rank_float 계산.
   - 동일 blended면 평균순위(예: 10,11위 동점 -> 10.5).

3. `_map_rank_to_tier_soft(rank_float, cfg) -> CompetitiveTier`
   - quota 경계(5,10,18,25)와의 거리로 소프트 점수 계산.
   - argmax로 최종 tier 선택.

4. `_classify_tier_by_league_rank(self, tid, signals) -> Tuple[tier, diagnostics]`
   - 팀 단건 분류 시에도 내부적으로 30팀 table을 구성해 일관성 확보.

### C. 기존 분류 플로우 교체
- `_classify_and_build_outputs()`에서
  - 기존 threshold 매핑 제거,
  - `tier = _classify_tier_by_league_rank(... )` 결과 사용.
- early-season protection, reset, bubble nuance는 **현행 규칙 유지**하되,
  - 입력 tier가 rank 기반 산출값이라는 점만 변경.

### D. diagnostics 확장
- `tier_diagnostics`에 아래 필드 추가:
  - `league_rank_float`
  - `league_rank_int`
  - `tier_soft_scores` (티어별 연속 점수)
  - `rank_boundaries` (예: [5,10,18,25])
  - `rank_quota` (현재 quota 설정)

---

## 2-3. `tests/test_team_situation_tier_weighting.py`

### A. 수정/삭제
- threshold 숫자 자체를 전제로 하는 assertion 제거.
- `blended threshold` 직접 맵핑을 암묵 가정한 테스트 제거/수정.

### B. 신규 테스트 추가
1. `test_rank_quota_count_distribution_is_respected()`
   - synthetic 30팀 데이터로 quota(5/5/8/7/5) 충족 확인.

2. `test_soft_boundary_prevents_hard_flip_on_tiny_delta()`
   - 경계 근처 미세 점수 차이에서 티어가 극단적으로 뒤집히지 않는지 검증.

3. `test_tie_blended_uses_deterministic_tie_breaker()`
   - 동일 blended 시 team_id tie-break 일관성 검증.

4. `test_reset_override_still_applies_after_rank_tiering()`
   - rank 기반 기본 tier 이후 reset override 동작 검증.

5. `test_late_season_bubble_adjustment_still_works_with_rank_base()`
   - 기존 bubble nuance가 rank 기반에서도 유지되는지 검증.

---

## 2-4. `docs/team_tier_dynamic_weighting_implementation_plan.md`

### A. 본문 갱신
- threshold 기반 설명을 rank quota + soft boundary 모델로 전면 교체.
- 예시 수식/다이어그램 업데이트.

### B. 삭제
- threshold 숫자(`0.74/0.62/0.50` 등) 중심 설명 제거.

---

## 2-5. (선택) `docs/issue/team_tier_threshold_drift_validation_2026-03-21.md`

- 후속 참고 문서로 유지하되,
- “해결 방향” 섹션에 본 실행계획 링크 추가.

---

## 3) 구현 순서 (바로 작업 가능한 체크리스트)

1. `data/team_situation_config.py`
   - threshold 필드 삭제
   - rank quota/soft 파라미터 추가
   - quota 합 검증 추가

2. `data/team_situation.py`
   - rank table/rank map/soft tier helper 추가
   - `_map_blended_score_to_tier` 삭제
   - `_classify_and_build_outputs` 매핑 구간 교체
   - diagnostics 필드 확장

3. 테스트 정비
   - 기존 테스트 수정/삭제
   - rank/soft/reset/bubble 신규 테스트 추가

4. 문서 정리
   - `docs/team_tier_dynamic_weighting_implementation_plan.md` 본문 전환

---

## 4) 상세 구현 가이드 (의사코드)

```python
# quota -> cumulative boundaries
b1 = q_contender
b2 = b1 + q_playoff_buyer
b3 = b2 + q_fringe
b4 = b3 + q_rebuild
# tank = remainder

# hard anchor tier by rank
if r <= b1: anchor = CONTENDER
elif r <= b2: anchor = PLAYOFF_BUYER
elif r <= b3: anchor = FRINGE
elif r <= b4: anchor = REBUILD
else: anchor = TANK

# soft scores initialized
score = {tier: -inf ...}
score[anchor] = 0

# for each boundary, blend neighbor tiers softly
# boundary between upper tier U and lower tier D at b
# signed distance: d = r - b
p_upper = sigmoid(-(d / tau))
p_lower = 1 - p_upper

# apply only when abs(d) <= width
# combine with anchor prior (or max pooling)

final_tier = argmax(score)
```

권장 단순화:
- 1차는 각 boundary에서 “인접 2개 tier”만 점수 보정.
- non-adjacent tier로 점프는 금지.

---

## 5) 완료 기준(DoD)

- 코드에서 threshold 기반 tier 매핑 함수/필드가 완전히 제거되었다.
- 30팀 synthetic 케이스에서 quota 분포가 정확히 맞는다.
- 경계 인접팀에서 소프트 전환이 동작하며 하드 플립 빈도가 감소한다.
- reset/bubble/early protection이 회귀 없이 동작한다.
- 문서가 최신 설계(순위+소프트 경계)와 일치한다.

---

## 6) 리스크 및 즉시 대응

1. **전 리그 재계산 비용 증가**
- 단건 평가(`evaluate_team`)에서도 30팀 점수 계산 필요.
- 대응: evaluator 인스턴스 단위 캐시(한 tick/한 호출 스코프) 적용.

2. **동점 처리 분쟁**
- tie-break 기준이 없으면 결과 비결정적.
- 대응: `team_id` 오름차순 고정 tie-break 강제.

3. **소프트 폭 과대/과소 튜닝**
- 폭이 너무 크면 tier 식별력 저하, 너무 작으면 하드컷과 동일.
- 대응: 테스트에서 경계 구간 샘플 세트로 민감도 고정 검증.

---

## 7) 이번 작업 범위 선언

- 이 문서는 **즉시 코딩 착수 가능한 공격적 치환 계획**이다.
- 레거시 threshold 경로는 유지하지 않는다.
- 마이그레이션/호환 레이어는 만들지 않는다.
