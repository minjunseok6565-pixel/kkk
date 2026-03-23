# 팀 분류 로직 개편안 (시즌 진행도 기반 가중치 전환)

## 목표
- 현재 `CONTENDER / PLAYOFF_BUYER / FRINGE / RESET / REBUILD / TANK` 분류에서 시즌 초반 성적 노이즈 과민 반응을 줄인다.
- 시즌 초반에는 **전력(오버롤 평균)** 을 중심으로 분류하고, 시즌이 진행될수록 **실제 성적/퍼포먼스 지표** 비중을 점진적으로 확대한다.
- 개발 단계이므로 점진 마이그레이션/세이브 호환은 고려하지 않고, 기존 분류 로직을 공격적으로 교체한다.

---

## 현재 코드 기준 핵심 변경 지점

### 1) `data/team_situation.py`
가장 중요한 변경 파일.

#### A. 분류의 단일 입력점 정리
- 위치: `TeamSituationEvaluator._classify_and_build_outputs(...)`
- 현재:
  - `roster_score = 0.62 * star_power + 0.38 * depth`
  - `perf_score = _compute_perf_score(...)`
  - `composite = _lerp(roster_score, perf_score, clamp(season_progress, 0.15, 0.85))`
  - 이후 `wp`, `rank`, `nr` 기반 하드 조건으로 tier 결정
- 변경:
  - **tier 판정 기준을 `blended_competitive_score` 중심으로 재구성**
  - 하드 조건(`wp>=...`, `rank<=...`)은 보정/승격/강등 규칙으로 축소

#### B. 가중치 함수 신설
- 신규 함수 추가(파일 하단 유틸 함수 섹션):
  - `_performance_weight_by_progress(season_progress: float) -> float`
  - `_overall_weight_by_progress(season_progress: float) -> float`
- 권장 기본 스케줄(82경기 기준, 선형/구간 선형):
  - 0~15경기(진행도 ~0.18): 성적 0.15 / 전력 0.85
  - 16~40경기(~0.49): 성적 0.40 / 전력 0.60
  - 41~60경기(~0.73): 성적 0.60 / 전력 0.40
  - 61경기 이후: 성적 0.80 / 전력 0.20
- 구현 형태:
  - 계단형보다는 구간 선형 interpolation 권장(경계 점프 방지)

#### C. 전력 점수(Overall) 계산 함수 분리
- 신규 함수 추가:
  - `_compute_overall_strength_score(sig: TeamSituationSignals) -> float`
- 계산식(초안):
  - `overall = 0.55*star_power + 0.30*depth + 0.15*role_fit_health`
  - 결과 0~1 clamp
- 이유:
  - 기존 `roster_score`보다 역할 적합도(`role_fit_health`)를 포함해 초기 분류 신뢰도 강화

#### D. 시즌 성적 점수 함수 보강
- 기존 `_compute_perf_score(...)` 유지하되, 입력 지표 확장/재가중
- 개선안 예시:
  - `win_pct` 0.45
  - `net_rating_norm` 0.25
  - `point_diff_norm` 0.15
  - `trend_norm` 0.10
  - `bubble_context_bonus` 0.05 (후반 한정)
- 주의:
  - 시즌 초반엔 이 점수 자체보다 최종 blend weight로 영향 축소하므로, 함수 내부 early-season 보정은 단순화 가능

#### E. 최종 블렌드 점수 도입
- `_classify_and_build_outputs(...)` 내에서 다음 계산으로 교체:
  1. `overall_score = _compute_overall_strength_score(signals)`
  2. `performance_score = _compute_perf_score(signals, season_progress)`
  3. `w_perf = _performance_weight_by_progress(season_progress)`
  4. `w_overall = 1.0 - w_perf`
  5. `blended = w_overall*overall_score + w_perf*performance_score`

#### F. tier 매핑을 score threshold 기반으로 전면 전환
- 기존 wp/rank 조건 분기 대신, `blended` 기반 1차 tier 부여:
  - `>= 0.74`: CONTENDER
  - `>= 0.62`: PLAYOFF_BUYER
  - `>= 0.50`: FRINGE
  - `>= 0.40`: RESET
  - `>= 0.30`: REBUILD
  - `< 0.30`: TANK
- 이후 보정 규칙 적용:
  - 후반(`season_progress >= 0.55`)에만 bubble 승격/강등
  - `RESET` 특례는 "고전력 + 저성적" 조건 유지 가능하나, `w_perf < 0.3` 구간에서는 발동 금지(시즌 초 낙인 방지)

#### G. reason 텍스트/디버그 근거 업데이트
- 분류 이유 문자열에 아래를 반드시 포함:
  - `overall_score`, `performance_score`
  - `w_overall`, `w_perf`
  - `blended`
- 목적:
  - 운영 중 왜 해당 티어가 나왔는지 즉시 추적 가능

---

### 2) `data/team_situation_config.py`
가중치/임계값 하드코딩 최소화를 위한 설정 상향.

#### A. 신규 설정 섹션 추가
- 예시 키 구조:
  - `TEAM_SITUATION_TIER_MODEL = { ... }`
- 포함 항목:
  - 시즌 진행도 구간별 성적 가중치 breakpoints
  - overall/performance 하위 지표 가중치
  - tier threshold
  - reset 특례 enable 플래그/최소 진행도

#### B. `team_situation.py`에서 해당 설정 참조
- 파일 상단 import 확장:
  - `from data.team_situation_config import TEAM_SITUATION_NEED_TAG_CONFIG, TEAM_SITUATION_TIER_MODEL`

---

### 3) `decision_context.py`
직접 분류를 계산하진 않지만, downstream에서 tier를 강하게 참조하므로 확인/수정 필요.

#### A. tier 민감도 계수 재점검
- `TIER_CONTEXT_ELASTICITY`(또는 tier별 가중치 맵)에서
  - `RESET/REBUILD/TANK`의 급격한 성향 차이가 과도하면 완화
- 의도:
  - 분류 로직 변경 후에도 후속 의사결정이 과민하게 흔들리지 않도록 균형 조정

#### B. 로깅/디버그 필드 확장(선택)
- team_situation에 추가되는 `blended` 근거를 decision context debug payload에 전달하도록 연결

---

### 4) 테스트 파일 신설/수정
현재 `team_situation` 전용 테스트가 부족하므로 신규 작성 권장.

#### A. 신규 테스트 파일
- `tests/test_team_situation_tier_weighting.py`

#### B. 필수 테스트 케이스
1. **시즌 초반 고전력 연패 보호**
   - high overall + low win_pct + low progress(<=0.12)
   - 기대: `REBUILD/TANK`로 즉시 떨어지지 않음
2. **시즌 후반 성적 반영 강화**
   - 같은 전력 조건에서 progress 0.75, 성적 부진
   - 기대: 초반 대비 tier 하향
3. **후반 bubble 승격 규칙**
   - progress>=0.55, `gb_to_6th`/`gb_to_10th` 조건 충족 시 승격
4. **가중치 단조성**
   - progress 증가에 따라 `w_perf` 단조 증가 검증
5. **경계값 안정성**
   - breakpoint 인접값에서 tier가 비정상 점프하지 않는지 검증

---

## 구현 순서 (권장)
1. `data/team_situation_config.py`에 tier 모델 설정 추가
2. `data/team_situation.py`에 유틸 함수 3종 추가
   - `_compute_overall_strength_score`
   - `_performance_weight_by_progress`
   - `_map_blended_score_to_tier` (선택)
3. `_classify_and_build_outputs`의 tier 산정 블록 전면 교체
4. reason/debug 문자열 업데이트
5. `decision_context.py`의 tier 탄성 계수 미세조정
6. 테스트 파일 추가 및 기존 관련 테스트 스모크

---

## 코드 레벨 치환 가이드 (핵심)

### 교체 대상 블록
- `data/team_situation.py`의 `_classify_and_build_outputs()` 내 아래 흐름을 교체:
  - `composite = _lerp(roster_score, perf_score, _clamp(season_progress, 0.15, 0.85))`
  - `if composite >= ...` + `elif wp/rank ...` tier 결정 블록

### 치환 후 목표 형태
- `blended` 계산 + threshold mapping + late-season 보정 규칙
- `rank/wp`는 1차 분류가 아닌 2차 보정 변수로 제한

---

## 파라미터 초기값(추천)

### 성적 가중치 함수 초기값
- `w_perf(progress)`:
  - p<=0.18: 0.15
  - p=0.49: 0.40
  - p=0.73: 0.60
  - p>=0.85: 0.80
  - 구간 선형 보간

### blended tier threshold 초기값
- CONTENDER: 0.74+
- PLAYOFF_BUYER: 0.62+
- FRINGE: 0.50+
- RESET: 0.40+
- REBUILD: 0.30+
- TANK: <0.30

### reset 특례 가드
- `season_progress >= 0.35`에서만 활성
- 조건: `overall_score>=0.68` AND `performance_score<=0.42`

---

## 기대 효과
- 시즌 초반 일정/부상/샘플 노이즈로 인한 과도한 `REBUILD/TANK` 분류 감소
- 중후반으로 갈수록 실제 성적 반영이 커져 현실적인 시장 행동 유도
- 분류 근거가 점수/가중치로 노출되어 디버깅 및 튜닝 효율 상승

---

## 비목표 (이번 변경에서 제외)
- 기존 세이브 데이터와의 역호환 처리
- 점진적 배포용 feature flag
- UI/프론트 분류 표시 로직 개편

