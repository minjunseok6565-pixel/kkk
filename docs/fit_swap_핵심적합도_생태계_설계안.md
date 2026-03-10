# Fit-swap 핵심 적합도 고도화 전용 설계안 (이슈 3 확정 대응)

작성일: 2026-03-10  
연계 문서:
- `docs/트레이드_잔여_단순화_확정_리포트.md` (이슈 3)
- `docs/트레이드_개편_신규시스템_구현구상.md` (4장)

---

## 0) 목표와 비목표

### 목표
- `trades/generation/dealgen/fit_swap.py`의 `_need_fit_score()`(단순 가중 오버랩) 경로를 폐기한다.
- `trades/valuation/lineup_ecosystem.py`를 신설하고, `compute_ecosystem_fit_score()`를 SSOT로 사용한다.
- fit-swap/counter 제안 시 “플러스/마이너스 이유”를 문자열이 아닌 **구조화된 reason code**로 반환한다.

### 비목표
- 팀 need 자체를 새로 생성/재정의하지 않는다. (`DecisionContext.need_map` 소비 원칙 유지)
- 외부 모델(신규 ML, 외부 API) 의존을 추가하지 않는다.

---

## 1) 설계 원칙 (반드시 준수)

1. **기존 프로젝트 값 우선 사용**
   - 이미 존재하는 `PlayerTradeCandidate`, `DecisionContext.need_map`, `team_situation`, `FitEngine` 산출값을 1순위 입력으로 사용.
2. **신규 지표가 필요하면 “프로젝트 내부 공급 경로”를 먼저 정의**
   - 계산에 필요한 값이 없으면 `lineup_ecosystem.py` 내부에서 임의 추정하지 않는다.
   - `asset_catalog` 빌드 단계 또는 `team_situation`/`decision_context` 단계에서 해당 지표를 내려주도록 명시하고, 누락 시 보수적 fallback(0 또는 neutral)만 허용.
3. **설명 가능성 우선**
   - 최종 점수뿐 아니라 서브점수와 reason code를 동시에 반환한다.
4. **즉시 교체 구조**
   - 개발 단계에서는 dual-read를 두지 않고, `_need_fit_score()`를 제거한 뒤 `compute_ecosystem_fit_score()`로 단일화한다.

---

## 2) `lineup_ecosystem.py` 제안 인터페이스

```python
@dataclass(frozen=True, slots=True)
class EcosystemComponent:
    key: str
    score: float                    # -1.0 ~ +1.0
    weight: float
    weighted_score: float
    reason_codes_plus: tuple[str, ...]
    reason_codes_minus: tuple[str, ...]
    meta: dict[str, Any]

@dataclass(frozen=True, slots=True)
class EcosystemFitResult:
    total_score: float              # 0.0 ~ 1.0 (counter ranking용)
    raw_total: float                # -1.0 ~ +1.0 (진단용)
    components: tuple[EcosystemComponent, ...]
    lineup_samples: tuple[dict[str, Any], ...]
    reason_codes_plus: tuple[str, ...]
    reason_codes_minus: tuple[str, ...]
    meta: dict[str, Any]


def compute_ecosystem_fit_score(
    *,
    receiver_team_id: str,
    incoming_candidates: Sequence[PlayerTradeCandidate],
    outgoing_player_ids: Sequence[str],
    tick_ctx: TradeGenerationTickContext,
    cfg: DealGeneratorConfig,
) -> EcosystemFitResult:
    ...
```

핵심: fit-swap은 `result.total_score`로 정렬하고, counter 설명/메시징은 `reason_codes_plus/minus`를 직접 사용한다.

---

## 3) 데이터 소스 매핑 (어디서 정보를 끌어올지)

## 3-1. 즉시 재사용 가능한 기존 값

- `PlayerTradeCandidate.supply`, `top_tags`, `fit_vs_team`, `market`, `salary_m`, `remaining_years`  
  → 현재 `asset_catalog` 생성 시 이미 계산됨.
- `PlayerTradeCandidate.snap.meta["role_fit"]` + `snap.attrs`  
  → `FitEngine.compute_player_supply_vector()`의 입력과 동일 계열.
- `DecisionContext.need_map`  
  → 팀 니즈 SSOT.
- `tick_ctx.get_team_situation(team_id)`의 `trade_posture`, `time_horizon`, `competitive_tier`, `urgency`, `constraints`  
  → 컨텐더/리빌드 가중치 분기와 리스크 허용도 조정에 사용.

## 3-2. 데이터 가정 금지 규칙 (확정)

- 본 설계는 아래 값만 사용한다.
  - `PlayerTradeCandidate`: `supply`, `top_tags`, `fit_vs_team`, `market`, `salary_m`, `remaining_years`, `snap.meta["role_fit"]`, `snap.attrs`
  - `DecisionContext.need_map`
  - `team_situation`: `trade_posture`, `time_horizon`, `competitive_tier`, `urgency`, `constraints`
- 위 목록에 없는 값(예: `team_core_player_ids`, `expected_rotation_slots`, `usage_proxy`)은 본 단계에서 **사용하지 않는다**.
- 추후 확장이 필요하면 먼저 내부 모듈(`asset_catalog`/`team_situation`/`decision_context`)이 값을 실제로 내려주도록 구현하고, 그 다음에 `lineup_ecosystem.py`에서 사용한다.

---

## 4) 5개 컴포넌트 계산 로직 상세

점수 범위는 공통으로 `-1.0 ~ +1.0`로 계산 후, 최종 합성에서 `0~1`로 리스케일한다.

## 4-1. `complementarity_gain` (코어 보강)

의도: "기존 코어 약점을 메우는지" 측정.

- 입력
  - 팀 need 상위 태그(`need_map` 상위 N개)
  - incoming 후보의 `supply`
  - outgoing 제거 후 가상 로스터의 잔여 공급
- 계산
  - `gap_before[tag] = max(0, need[tag] - current_supply[tag])`
  - `gap_after[tag] = max(0, need[tag] - new_supply[tag])`
  - 태그별 개선량 `delta_gap = gap_before - gap_after`
  - 가중 합: `sum(need_weight * delta_gap) / sum(need_weight)`
- reason code 예시
  - plus: `FIT_ECO_COMP_SPACING_RELIEF`, `FIT_ECO_COMP_POA_RELIEF`
  - minus: `FIT_ECO_COMP_LOW_NEED_IMPACT`

## 4-2. `redundancy_conflict` (기능 과밀/충돌)

의도: 같은 기능 선수가 과도하게 겹치는지 측정.

- 입력
  - incoming + retained roster 후보의 `top_tags`, `supply`
- 계산
  - 태그별 공급 합(`sum_supply_by_tag`)과 need 가중치의 불일치 계산
  - `need`가 낮은 태그에 공급이 집중될수록 페널티 증가
  - 동일 고사용량 역할(예: PRIMARY_INITIATOR 다중) 중복 페널티 강화
  - `conflict = low_need_concentration_penalty + single_role_crowding_penalty`
- reason code 예시
  - plus: `FIT_ECO_REDUNDANCY_AVOIDED`
  - minus: `FIT_ECO_REDUNDANCY_INITIATOR_CROWD`, `FIT_ECO_REDUNDANCY_BIG_STACK`

## 4-3. `star_synergy` (핵심 스타 효율 상승)

의도: 코어 스타 주변 효율(스페이싱/스크린/수비보완) 개선 여부.

- 입력
  - receiver 팀의 retained roster 중 `market.total` 상위 선수군(프로젝트 내 값으로 코어 proxy)
  - 코어 proxy의 role/need profile (`role_fit`, `supply`, need_map)
  - incoming의 complementary tags
- 계산
  - 코어 proxy별 보완 매트릭스 사전 정의(예: on-ball 성향 코어는 SPACING/RIM_PRESSURE 가산)
  - 코어별 `synergy_delta` 산출 후 가중 평균
  - 코어 proxy 산출 불가 시 이 컴포넌트는 0(중립) 처리
- reason code 예시
  - plus: `FIT_ECO_STAR_PRIMARY_RELIEF`, `FIT_ECO_STAR_DEF_COVER_BOOST`
  - minus: `FIT_ECO_STAR_NO_CORE_DATA`, `FIT_ECO_STAR_POOR_MATCH`

## 4-4. `touches_friction` (점유/분배 충돌)

의도: 볼 점유/의사결정 터치 충돌 리스크 측정.

- 입력
  - incoming/outgoing의 `supply` + `top_tags`
  - retained roster의 `supply` + `top_tags`
- 계산
  - `PRIMARY_INITIATOR`, `SHOT_CREATION` 공급 합을 on-ball load proxy로 사용
  - on-ball proxy 과밀 시 비선형 패널티
  - off-ball 가치(SpotUp/Movement)가 높으면 일부 상쇄
- reason code 예시
  - plus: `FIT_ECO_TOUCHES_BALANCED`, `FIT_ECO_TOUCHES_SECONDARY_CREATION`
  - minus: `FIT_ECO_TOUCHES_USAGE_CLASH`, `FIT_ECO_TOUCHES_LOW_OFFBALL`

## 4-5. `coverage_resilience` (운영 안정성)

의도: 부상/벤치/파울트러블 상황까지 고려한 라인업 지속 가능성.

- 입력
  - incoming/outgoing 반영 후 roster 공급 벡터
  - 포지션/기능별 최소 커버 조건 (guard wing big + rim/poa/shooting)
  - need_map 상위 태그
- 계산
  - 시나리오 샘플(주전/클로징/세컨드)의 태그 커버 충족률 계산
  - 낮은 충족률 태그에 need 가중치를 더 크게 적용
  - 최저 시나리오 점수에 추가 가중(병목 우선)
- reason code 예시
  - plus: `FIT_ECO_COVERAGE_BENCH_STABLE`, `FIT_ECO_COVERAGE_INJURY_BUFFER`
  - minus: `FIT_ECO_COVERAGE_THIN_BIG`, `FIT_ECO_COVERAGE_NO_POA_BACKUP`

---

## 5) 라인업 샘플링 방식 (안정성 목적)

- 샘플 단위(최소 3개)
  1. `starting_5_estimate`
  2. `closing_5_estimate`
  3. `second_unit_core`
- 샘플 구성 우선순위
  - 기존 로스터 유지 + incoming 삽입 + outgoing 제거
  - `market.total`, `fit_vs_team`, `role tag diversity`를 tie-breaker로 사용
- 점수 합성
  - `starting` 0.45 / `closing` 0.35 / `second` 0.20 (초기값)
  - 팀 posture가 REBUILD면 second unit 비중 상향 가능

---

## 6) 최종 합성식(초기안)

컴포넌트 raw 합성:

`raw_total = w1*complementarity_gain - w2*redundancy_conflict + w3*star_synergy - w4*touches_friction + w5*coverage_resilience`

초기 가중치:
- `w1=0.30`, `w2=0.20`, `w3=0.20`, `w4=0.15`, `w5=0.15`

정규화:
- `total_score = clamp((raw_total + 1.0) / 2.0, 0.0, 1.0)`

모드별 조정:
- WIN_NOW: `star_synergy`, `coverage_resilience` +10%
- REBUILD: `touches_friction` 패널티 -10%, `coverage_resilience`의 벤치축 +10%

---

## 7) reason code 스키마 (counter “플러스/마이너스 이유” 구조화)

## 7-1. 네이밍 규칙

- prefix: `FIT_ECO_`
- 형식: `FIT_ECO_<COMPONENT>_<DETAIL>`
- polarity는 코드 문자열이 아니라 **plus/minus 배열 위치**로 표현.

## 7-2. 반환 예시

```json
{
  "plus": [
    "FIT_ECO_COMP_SPACING_RELIEF",
    "FIT_ECO_STAR_PRIMARY_RELIEF",
    "FIT_ECO_COVERAGE_BENCH_STABLE"
  ],
  "minus": [
    "FIT_ECO_TOUCHES_USAGE_CLASH"
  ],
  "meta": {
    "component_scores": {
      "complementarity_gain": 0.41,
      "redundancy_conflict": 0.12,
      "star_synergy": 0.35,
      "touches_friction": 0.22,
      "coverage_resilience": 0.28
    }
  }
}
```

## 7-3. 연결 지점

- `fit_swap.py`
  - 후보 비교는 `total_score` 사용
  - 디버그/telemetry에 `component_scores`, plus/minus code 저장
- `counter_offer/builder.py`, `counter_offer/messaging.py`
  - 기존 `FIT_FAILS` 단일 메시지 대신, 코드 매핑 사전으로 자연어 템플릿 생성

---

## 8) 마이그레이션 단계 (이슈 3 전용)

1. `lineup_ecosystem.py` 추가 + pure function 단위 테스트 작성
2. `fit_swap.py`에서 `_need_fit_score()` 호출 경로를 제거하고 `compute_ecosystem_fit_score()`로 즉시 대체
3. 구 로직 함수 `_need_fit_score()` 자체를 삭제
4. reason code를 `counter_offer/builder.py`, `counter_offer/messaging.py`에 즉시 연결
5. 텔레메트리는 신규 점수/컴포넌트 기준으로만 기록

---

## 9) 검증 포인트

- 재현성: 동일 tick/seed에서 동일 후보 순위가 나오는가
- 설명 가능성: top-N counter에 plus/minus reason code가 항상 채워지는가(빈 배열 허용 기준 정의)
- 안전성: 신규 입력 지표 누락 시 예외가 아닌 neutral 처리로 진행되는가
- 품질: 기존 대비 `FIT_FAILS` 재발률/카운터 수락률이 개선되는가

---

## 10) 구현 시 명시해야 할 금지사항

- `lineup_ecosystem.py` 내부에서 임의 상수만으로 코어 스타를 추정하지 말 것.
- 기존 스냅샷에 없는 값을 외부에서 새로 가져오지 말 것.
- 필요한 지표가 없으면 반드시 "어느 내부 모듈에서 내려줄지"를 먼저 확정하고, 해당 공급 경로를 먼저 구현할 것.
