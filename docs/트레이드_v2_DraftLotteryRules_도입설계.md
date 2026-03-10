# 트레이드 v2 `DraftLotteryRules` 도입 설계 (재검토 반영본)

작성일: 2026-03-10  
연계 문서:
- `docs/트레이드_잔여_단순화_확정_리포트.md` (이슈 1,2,3,7,8)
- `docs/트레이드_개편_신규시스템_구현구상.md`
- `docs/트레이드_v2_핵심5모듈_상세설계.md`

---

## 0) 재검토 요약

이 문서는 기존 초안을 다음 기준으로 재검토해 보완했다.

1. **프로젝트 실재 값만 사용**: `types.py`, `data_context.py`, `protection.py`, `pick_settlement.py`에 실제 존재하는 필드/의미만 사용
2. **NBA 유사성 확보**: “standings 선형 매핑” 대신 시즌 룰 기반 분포를 쓰되, 실제 NBA 추첨 구조(상위 추첨 슬롯 + 나머지 순번 배치)와 같은 방향으로 설계
3. **운영 안전성**: 룰 누락/불량 시 실패 대신 coverage 하락 + 호환 fallback

---

## 1) 현재 코드 기준 사실 점검 (가정 제거)

### 1-1. 현재 프로젝트에 **있는 값**

- 픽 입력: `PickSnapshot.year`, `round`, `original_team`, `owner_team`, `protection`.
- 스왑 입력: `SwapSnapshot.pick_id_a`, `pick_id_b`, `year`, `round`, `owner_team`, `active`.
- 보호 규칙 SSOT: `normalize_protection()`이 현재 `TOP_N`만 허용.
- 정산 의미론 SSOT: `pick_settlement.py` (보호 먼저 정산, 이후 swap 행사).
- 기존 기대값: standings 순서 기반 `expected_pick_number` (선형, 분산 없음).

### 1-2. 기존 초안에서 조정한 부분

- "미지원 보호 유형 대응"을 핵심 플로우로 두지 않는다.
  - 이유: 현재 SSOT가 `TOP_N` 외 타입을 입력 단계에서 거부하기 때문.
  - 단, 방어적 코드로 diagnostics는 유지 가능.
- 로터리 확률 생성을 "임의 산식"에 의존하지 않는다.
  - 시즌별 공식 룰 테이블을 명시적으로 저장/조회한다.

---

## 2) 왜 `DraftLotteryRules`가 필요한가

## 2-1. 이슈 1 해결의 필수 전제

이슈 1의 본질은 "standings -> 단일 기대순번" 고정이다.
이를 해소하려면 시즌 룰 기반의 `pmf/cdf/variance` 계산이 필요하고,
이를 공급하는 명시 타입이 `DraftLotteryRules`다.

## 2-2. 이슈 7/8 정밀화 기반

픽의 테일 리스크(업사이드/다운사이드)는 CAP_FLEX/패키지 평가에 직접 영향을 준다.
분포 없는 단일 값으로는 동일 평균값 픽들의 리스크 차이를 표현할 수 없다.

## 2-3. SSOT/무가정 운영 준수

룰이 없는 시즌은 "계산 중단"이 아니라 `source_coverage=false`와 함께 호환 경로를 타게 해야 한다.
이 방식이 v2 전환(dual-read/feature flag)에서 가장 안전하다.

---

## 3) NBA 유사 설계 원칙

## 3-1. R1 범위(이번 구현)

- 대상: **1라운드 픽(`round == 1`)**
- 로터리 팀 수: 시즌 룰이 정의한 `lottery_team_count`(현대 NBA 기준 14)
- 추첨 슬롯 수: 시즌 룰이 정의한 `lottery_pick_count`(현대 NBA 기준 4)
- 2라운드(`round != 1`): 로터리 확률 미적용(standings 기반 deterministic 순번)

## 3-2. 확률 입력은 "완성형 PMF 테이블"로 관리

NBA 유사성을 위해 R1에서는 `odds_by_standing`(부분 정보)만 저장하지 않고,
**standing별 최종 pick PMF(1~30)를 시즌별로 저장**한다.

이유:
- 부분 odds에서 최종 PMF를 재구성하면 구현 편의에 따라 왜곡될 수 있음
- 최종 PMF를 테이블로 두면 계산 경로가 단순하고 재현 가능
- 시즌 제도 변경 시 데이터만 교체하면 됨

---

## 4) 타입/API 설계

신규 파일: `trades/valuation/draft_lottery_rules.py`

```python
from dataclasses import dataclass
from typing import Mapping

@dataclass(frozen=True)
class DraftLotteryRules:
    season_year: int
    team_count: int                 # 기본 30
    lottery_team_count: int         # 예: 14
    lottery_pick_count: int         # 예: 4
    # standing_index: 1(최하위)~team_count
    # value: {pick_number: probability} ; round=1 기준 최종 PMF(1~team_count)
    first_round_pmf_by_standing: Mapping[int, Mapping[int, float]]

    def validate(self) -> tuple[bool, tuple[str, ...]]:
        ...

@dataclass(frozen=True)
class DraftLotteryRulesRegistry:
    rules_by_season: Mapping[int, DraftLotteryRules]

    def get(self, season_year: int) -> DraftLotteryRules | None:
        ...
```

조회 함수:

```python
def get_draft_lottery_rules(season_year: int) -> DraftLotteryRules | None:
    ...
```

---

## 5) 검증 규칙 (`validate()`)

1. `team_count >= 2`, `1 <= lottery_pick_count <= lottery_team_count <= team_count`
2. standing key가 `1..team_count`를 모두 포함
3. 각 standing의 PMF key는 `1..team_count` 범위만 허용
4. 각 standing의 확률은 0~1, 합은 `1.0 ± 1e-9`
5. 비로터리 팀(`standing > lottery_team_count`)은 상위 슬롯(`<= lottery_pick_count`) 확률이 0이어야 함

실패 시 `False, ("INVALID_*", ...)` 반환.

---

## 6) `pick_distribution` 계산 로직 (R1)

## 6-1. 기본 PMF 생성

입력: `PickSnapshot`, standings index, `DraftLotteryRules`.

- `round == 1`:
  - `first_round_pmf_by_standing[s]`를 복사해 base PMF로 사용
- `round != 1`:
  - standings 순번 기반 deterministic PMF 사용 (`{slot: 1.0}`)

> 핵심: R1 1라운드는 룰 테이블을 그대로 사용하고, 코드 내 임의 재추정 금지.

## 6-2. 보호 반영 (`TOP_N` only)

- `normalize_protection()` 결과만 사용
- 보호 발동 구간(`pick <= n`)과 비발동 구간(`pick > n`)으로 PMF를 분리
- `pick_settlement.py` 의미론에 맞춰:
  - 발동 시 원소유팀 잔류 + 보상자산(fixed) 발생 가능성 note 기록
  - 비발동 시 권리 이전 시나리오로 평가

## 6-3. 스왑 반영

- `pick_settlement.py`와 동일하게 **보호 반영 후** 스왑 평가
- `SwapSnapshot.owner_team`이 실질 행사 가능할 때만 행사 시나리오 반영
- 행사 시 더 좋은 slot(작은 pick_number) 선택 원칙 적용

## 6-4. 출력

`PickDistributionBundle`:
- 필수: `pmf`, `cdf`, `ev_pick`, `variance`, `scenario_notes`
- 호환: `compat_expected_pick_number = ev_pick`
- 선택: `p10/p50/p90`, `tail_upside_prob`, `tail_downside_prob`

---

## 7) 누락/불량 룰 처리 (운영 안전)

1. `season_rules is None`
- `scenario_notes += ("MISSING_INPUT_LOTTERY_RULES",)`
- `source_coverage["lottery_rules"] = False`
- 1라운드도 standings 기반 deterministic 호환 PMF로 fallback

2. `validate()` 실패
- `scenario_notes += ("INVALID_LOTTERY_RULES",)`
- 동일 fallback

3. 보호 normalize 실패
- `scenario_notes += ("INVALID_PROTECTION_SCHEMA",)`
- 보호 미적용 중립 경로(계산은 지속)

---

## 8) 파일별 수정사항

## 8-1. 신규: `trades/valuation/draft_lottery_rules.py`

- `DraftLotteryRules`, `DraftLotteryRulesRegistry`, `get_draft_lottery_rules()` 구현
- 시즌 룰 상수(최소 현대 NBA 룰 1세트) 추가
- `validate()` + 오류 코드 상수 정의

## 8-2. 변경: `trades/valuation/pick_distribution.py`

- `season_rules: DraftLotteryRules | None` 허용(운영 fallback 반영)
- 내부 분리:
  - `_build_base_pmf(...)`
  - `_apply_top_n_protection(...)`
  - `_apply_swap_semantics(...)`
- `source_coverage["lottery_rules"]`/reason flag 기록

## 8-3. 변경: `trades/valuation/context_v2.py`

- `current_season_year`로 `get_draft_lottery_rules()` 조회
- 룰 coverage를 `diagnostics`/telemetry에 적재
- dual-read 최소 diff 외 보조 지표(variance/tail) optional 기록

## 8-4. 변경: `trades/valuation/types_v2.py` (또는 context 타입 파일)

- `lottery_rules_season: int | None`
- `lottery_rules_coverage: bool`
- 필요 시 `lottery_rules_notes: tuple[str, ...]`

## 8-5. 테스트

신규:
- `tests/trades/valuation/test_draft_lottery_rules.py`
  - season lookup 성공/None
  - validate success/fail

변경:
- `tests/trades/valuation/test_pick_distribution.py`
  - PMF 합 1.0
  - 룰 누락 fallback
  - 보호+스왑 순서 일치(보호 후 스왑)
  - round=2 deterministic 확인

---

## 9) 단계별 구현 순서

1. `draft_lottery_rules.py` 타입/검증/레지스트리
2. 룰 단위테스트 작성
3. `pick_distribution` base PMF 연결
4. 보호/스왑 반영 및 순서 테스트
5. `context_v2` 룩업/coverage telemetry 연결
6. dual-read로 v1/v2 변동 관측 후 flag 점진 on

---

## 10) 완료 기준 (DoD)

1. `DraftLotteryRules` 룩업/검증 코드 존재
2. 1라운드 픽은 시즌 룰 PMF를 사용하고 PMF 검증 통과
3. 룰 누락/불량 시 계산 지속 + coverage flag 기록
4. 보호/스왑 적용 순서가 `pick_settlement` 의미론과 일치
5. feature flag off에서 기존 동작 회귀 0

---

## 11) 기대 효과

- 프로젝트에 존재하는 입력만으로 이슈 1(선형 픽 기대값)을 구조적으로 해소한다.
- NBA 유사한 1라운드 로터리 변동성을 반영해 CAP_FLEX/패키지 평가 연결 기반을 만든다.
- 룰 누락 시즌도 안전하게 운영 가능한 전환 경로를 확보한다.
