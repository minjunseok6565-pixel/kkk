# Salary Matching Repair 리팩터링 설계안 (다중 후보 validate/evaluate 확장)

## 목표

`repair.py`의 salary matching 수리 로직을 다음 원칙으로 교체한다.

1. **SSOT 기반 완전 일반식 판정**
   - 특정 산술식을 하드코딩하지 않고, 후보 패키지 적용 후 각 팀에 대해 `check_salary_matching()`를 호출해 통과 여부를 판정한다.
   - 즉, `ok_A && ok_B` (멀티팀이면 `all(ok_t)`)를 만족하는 조합만 수리 후보로 인정한다.

2. **최소 인원 우선 계층 탐색**
   - k=1 → 2 → 3 ... 순으로 탐색한다.
   - 어떤 k에서 해를 하나라도 찾으면 **즉시 종료**하고 더 큰 k는 탐색하지 않는다.
   - 각 k에서 수집 개수는 상한 `N`(기본 10).
   - 단독(또는 더 작은 k)으로 조건 충족 시 불필요한 파생(더 큰 k)은 금지.

3. **양 팀 선수 풀 동시 사용**
   - failing team outgoing 추가만 보지 않고, 거래 양 팀 모두에서 추가 패키지 후보를 만들 수 있게 한다.
   - 생성된 패키지 중 일부만 고르는 것이 아니라, validate 통과 가능성이 높은 복수 수리안을 유지한다.

4. **다중 딜 출력으로 evaluate 확장**
   - salary 수리에서 얻은 복수 후보를 **전부 validate 루프에 태운 뒤**, 통과한 후보들을 base evaluate로 넘긴다.
   - 더 이상 단일 `cand.deal` mutate + bool 성공/실패만으로 종료하지 않는다.

5. **레거시 salary repair 완전 대체**
   - `_repair_salary_matching()`의 기존 filler 1명 추가 중심 로직 삭제.
   - `_repair_second_apron_salary_mismatch()` 함수 삭제.
   - SECOND_APRON도 별도 분기 없이 동일 탐색/검증 프레임에서 처리한다.

---

## 진입점/루프 변경 범위

- 유지:
  - `repair_once()`의 salary matching 분기 진입 지점(호출 위치)
- 변경:
  - `repair_until_valid()`가 단일 candidate 반환이 아닌 복수 candidate 반환을 지원
  - salary matching 분기 내부 구현 전체
  - `core.py`에서 repair 결과를 단일 `cand2`가 아닌 리스트로 처리

즉, `repair_once()`에서 `RuleFailureKind.SALARY_MATCHING`을 만나면 새 planner를 호출하고, planner는 복수 수리 후보를 반환한다. 이후 validate를 통과한 모든 후보를 evaluate 단계로 전달한다.

---

## 삭제/교체 대상 (repair.py)

아래는 완전 제거 후 신규 로직으로 대체한다.

1. 기존 `_repair_salary_matching()` 본문 전체
   - `status == SECOND_APRON` 분기
   - `FILLER_CHEAP/FILLER_BAD_CONTRACT` 전수 스캔
   - 1인 append + `repair:add_filler_salary` 태그 부여 방식

2. `_repair_second_apron_salary_mismatch()` 함수 전체
   - raise max single outgoing / reduce incoming split 전략 포함 전부 삭제

3. 위 함수 전용 태그 체계 정리
   - `repair:second_apron_*` 계열 태그를 더 이상 생성하지 않음
   - 새 태그로 통합 (`repair:salary_match_k1`, `repair:salary_match_k2` 등)

---

## 신규 로직 설계

## 1) 데이터 스냅샷 수집

신규 함수(예시):

- `_build_salary_context(cand, catalog, config, failure) -> SalaryRepairContext`

컨텍스트 구성 항목:

- 대상 팀 집합(기본 2팀, 구조상 멀티팀 확장 가능 형태)
- 팀별 현재 outgoing/incoming salary(달러)
- 팀별 outgoing/incoming player 수
- 팀별 max single outgoing salary(SECOND_APRON 대응)
- receiver 관계(자산별 실제 수취팀)
- 팀별 candidate pool(추가 가능한 선수 목록)
- 필터 정보
  - `return_ban_teams`
  - `aggregation_solo_only`
  - `max_players_per_side`
  - 현재 딜에 이미 포함된 player id

주의:
- 금액 계산은 모두 달러 정수 기준으로 통일한다.
- 팀별 incoming/outgoing 집계는 `resolve_asset_receiver()` 기준으로 계산한다.

## 2) 후보 선수 풀 생성 (양 팀 동시)

신규 함수(예시):

- `_iter_salary_add_candidates(ctx, team_id) -> Iterator[CandidatePlayer]`

원칙:
- 버킷은 우선 filler 계열부터 시작하되, 필요 시 설정 기반으로 확장 가능하도록 추상화한다.
- 즉시 필터링:
  - 현재 딜 중복 player 제외
  - return-ban 위반 제외
  - solo-only 충돌 제외
  - 팀별 max players 초과 유발 제외

## 3) 최소 인원 우선 계층 탐색

신규 함수(예시):

- `_search_min_k_salary_packages(ctx, max_k=3, per_k_limit=10) -> List[SalaryPackage]`

탐색 정의:
- k는 "추가할 총 선수 수".
- k=1부터 시작하여, 양 팀 후보 풀에서 조합을 만든다.
- 각 조합마다 임시 적용 -> 팀별 SSOT 검증(`check_salary_matching`) 실행.
- 통과 조합을 최대 `per_k_limit`까지 수집.
- 해당 k에서 하나라도 수집되면 즉시 반환(최소 인원 원칙).

조합 생성 방식:
- 작은 k는 완전탐색 가능.
- k 증가 시 조합 폭발 방지를 위해 다음 가지치기 적용:
  - salary delta 상/하한 기반 사전 컷
  - 동일 salary signature 중복 제거
  - 팀별 후보 풀 상위 M명 제한(시장가치/연봉 기반)

## 4) SSOT 검증기 (완전 일반식)

신규 함수(예시):

- `_validate_salary_for_all_teams(ctx, tentative_deal) -> SalaryValidationOutcome`

검증 절차:
1. tentative_deal 기준 팀별 outgoing/incoming/players/max_single_outgoing 재집계
2. 팀별 `check_salary_matching()` 호출
3. `all(ok)`이면 통과

핵심:
- SECOND_APRON 예외 처리를 수리 함수 내부 분기에서 하지 않는다.
- `check_salary_matching()`가 상태/메서드 분기를 모두 담당하도록 위임한다.

## 5) 패키지 선택 및 반영

신규 함수(예시):

- `_materialize_salary_repaired_candidates(cand, packages, ctx) -> List[DealCandidate]`
- `_rank_salary_packages(packages, ctx) -> List[SalaryPackage]`

정렬 기준(동률 순차):
1. 최소 k (이미 보장)
2. salary 여유(slack) 최소
3. 시장가치 손실 최소
4. 사전 정의된 안정성 tie-breaker(player_id lexical)

반영:
- 상위 `top_n`개(예: 10개) 패키지를 각각 clone된 candidate에 적용
- 각 후보에 태그: `repair:salary_match_k{k}`, `repair:salary_variant:{rank}` 부여
- 반환값은 단일 bool이 아니라 `List[DealCandidate]`

## 6) 다중 validate + evaluate 전달

신규 함수(예시):

- `repair_until_valid_many(...) -> Tuple[List[DealCandidate], int]`

동작:
1. 시작 후보 큐에 base `cand` 1개 적재
2. validate 실패 시 salary failure이면 복수 수리 후보 생성
3. 생성된 후보를 다음 validate 라운드 큐에 적재(예산/중복 해시 가드 적용)
4. validate 통과 후보는 `valid_candidates`에 누적
5. 루프 종료 시 `valid_candidates` 전부 반환

예산/폭발 방지:
- `max_repairs`, `max_validations` 외에 `max_repaired_variants_per_base` 상한 도입
- 해시 중복(`dedupe_hash`) 제거
- salary 수리로 생성되는 분기수 상한 (`salary_repair_branch_cap`) 도입

core 연동:
- `core.py`의 기존 `ok, cand2, v_used = repair_until_valid(...)` 호출부를
  `cand_list, v_used = repair_until_valid_many(...)`로 교체
- `for cand2 in cand_list:`로 evaluate 루프를 수행하여 통과 후보 전부 base evaluate로 전달
- 기존 seen_output/base_eval_cache는 후보별 해시로 동일하게 적용

---

## 함수 구조(예상 시그니처)

```python
@dataclass
class SalaryRepairContext:
    ...

@dataclass
class SalaryPackage:
    added_by_team: Dict[str, List[str]]
    k: int
    score: Tuple[int, float, float, str]


def _repair_salary_matching(...)-> List[DealCandidate]:
    ctx = _build_salary_context(...)
    packages = _search_min_k_salary_packages(ctx, max_k=3, per_k_limit=10)
    if not packages:
        return []
    ranked = _rank_salary_packages(packages, ctx)
    return _materialize_salary_repaired_candidates(cand, ranked[:10], ctx)
```

---

## 변경 시 주의사항

1. **기존 외부 인터페이스 보존**
   - `repair_once()` 진입 시그니처는 유지하되, salary 분기 반환 타입은 리스트 기반으로 확장
   - `repair_until_valid_many()` 신규 도입 후 기존 단일 반환 함수는 래퍼로 축소 또는 제거

2. **정수 달러 일관성**
   - float 사용 금지(반올림/드리프트 재실패 방지)

3. **성능 보호**
   - `max_k`, `per_k_limit`, 팀별 후보 pool cap을 config로 노출
   - `salary_repair_branch_cap`, `max_repaired_variants_per_base` 설정 추가
   - timeout/탐색 상한 초과 시 즉시 fail-safe 반환

4. **관측성(디버깅)**
   - 실패 이유 카운트 태그 추가
   - 탐색된 조합 수, k 레벨 히트 여부를 tag 또는 stats에 반영

---

## 단계별 구현 계획

### 1단계: 뼈대 교체
- 레거시 `_repair_salary_matching`/`_repair_second_apron_salary_mismatch` 제거
- 신규 `_repair_salary_matching` 골격 + 컨텍스트/검증기 연결
- `repair_until_valid_many` 스켈레톤 추가

### 2단계: 최소 인원 탐색(k=1,2)
- k=1/2 우선 구현
- per-k limit, 즉시 종료 정책 반영
- package->candidate materialize 구현

### 3단계: k=3 + 가지치기
- 조합 폭발 대응 로직 추가
- 성능 회귀 점검
- core evaluate 루프를 다중 candidate 입력으로 전환

### 4단계: 품질 튜닝
- `_rank_salary_packages` 스코어 튜닝
- 태그/로깅 강화

---

## 테스트 시나리오(필수)

1. BELOW_FIRST_APRON vs BELOW_FIRST_APRON 양팀 동시 통과
2. FIRST_APRON 한쪽/양쪽 혼합 케이스
3. SECOND_APRON 포함 케이스(단일 max outgoing 반영 확인)
4. 단독(k=1) 해가 있을 때 k=2 이상 미탐색 확인
5. per-k limit(10) 상한 준수
6. return-ban/solo-only 필터 즉시 배제 확인
7. max_players_per_side 초과 방지 확인
8. salary repair 분기 후보 수 상한(`salary_repair_branch_cap`) 준수
9. validate 통과 복수 후보가 모두 evaluate 단계에 전달되는지 확인

---

## 이번 작업의 비범위(후속)

- 다중 딜 출력 이후의 최종 proposal 랭킹 정책 재설계(현재 evaluate/selection 재사용)
- salary 외 룰(로스터/픽)까지 동일 다중 분기 프레임으로 확장
