# Fit Engine 고정 바닥값(0.70/0.35) 수정 브리핑

## 문제 요약
- `FitEngine.compute_player_supply_vector()`는 `tag_supply(..., strict=True)`가 예외를 내면 공급 벡터를 `{}`로 대체한다.
- 공급 벡터가 비면 `fit_score`가 0에 수렴하고, 이후 `FIT_FACTOR`와 `FIT_BELOW_THRESHOLD_PENALTY`가 바닥값으로 고정된다.

## 왜 strict 검증이 깨지는가
- `need_attr_profiles.validate_attrs_0_99()`는 attrs의 모든 값을 `float(v)`로 변환한다.
- SSOT인 `ratings_2k`는 `attrs_json`에 `"Potential"`을 문자열 등급(`"C-".."A+"`)으로 저장한다.
- 따라서 `Potential`이 포함된 attrs를 strict 검증에 통과시키면 예외가 발생한다.

## 선택지 비교

### 선택지 A) 엑셀/SSOT에서 Potential을 숫자로 변경
**장점**
- strict 검증과 충돌이 사라짐.

**단점**
- 데이터 스키마/호환성 파급이 큼(기존 `Potential` 등급 문자열을 소비하는 모듈 전반 수정 필요).
- 역사 데이터/외부 입력(엑셀)과의 호환성 비용이 큼.

### 선택지 B) fit 계산에서 비수치 key를 사전 필터링
**장점**
- 최소 변경으로 root cause 해결.
- SSOT/엑셀 포맷을 바꾸지 않음.
- fit 계산은 실제 필요한 수치 rating만 소비하므로 의미적으로도 타당.

**단점**
- 필터 기준을 명확히 유지해야 함(예: 숫자 변환 가능 + 0..99 범위).

## 권장안 (Best)
**단기 권장(이번 수정 반영)**: `Potential`만 fit 계산 입력에서 제외한다.

- 현재 문자열 attrs가 사실상 `Potential` 하나라면 가장 작은 변경으로 문제를 해결한다.
- SSOT/엑셀 포맷을 그대로 유지하면서 floor 고정 이슈를 즉시 완화할 수 있다.

**중장기 권장**: `FitEngine` 진입 시 attrs를 정규화해서 `tag_supply`에 전달.

구체안:
1. `compute_player_supply_vector()`에서 attrs를 다음 규칙으로 정제한 dict를 만든다.
   - 값이 숫자 변환 가능해야 함.
   - 0..99 범위를 만족하는 값만 유지.
2. 정제된 attrs가 비어도 예외로 전체 supply를 날리지 않고, 관측 가능한 rating으로 계산을 계속한다.
3. 디버그 meta에 `filtered_attr_count`, `dropped_non_numeric_keys`를 남겨 추적 가능하게 한다.

## 왜 이렇게 나누는가
- 현재 운영 데이터 기준으로는 `Potential`만 제거해도 체감 문제는 즉시 사라진다(작은 패치).
- 다만 장기적으로는 문자열 key가 추가될 가능성을 고려해 일반화 필터가 더 견고하다.

## 후속 권장 테스트
- `Potential` 문자열이 있어도 supply가 비지 않는지 검증.
- 진짜 비정상 attrs(문자열만 있는 경우)는 graceful fallback 되는지 검증.
- 기존 numeric-only 케이스에서 결과가 변하지 않는지 회귀 검증.
