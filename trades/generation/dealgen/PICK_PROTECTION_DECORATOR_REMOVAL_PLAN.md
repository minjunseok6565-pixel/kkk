# pick_protection_decorator.py 제거 사전 점검 (일괄 삭제용)

목표: `trades/generation/dealgen/pick_protection_decorator.py`를 **점진적 호환 없이 한 번에** 제거해도 런타임 에러 없이 동작하도록, 사전에 깨지는 지점을 식별한다.

## 1) 직접 import/호출 의존성 (삭제 시 즉시 깨짐)

### A. `core.py`
- `from .pick_protection_decorator import maybe_apply_pick_protection_variants`
- BUY/SELL 생성 루프에서 `maybe_apply_pick_protection_variants(...)` 호출 2곳
- 파일 삭제만 하면 ImportError/NameError 발생 가능

조치:
1. 위 import 삭제
2. 두 호출부를 제거하고 `pv_used`, `pe_used` 누적 로직도 함께 정리
3. 보호변형 단계가 사라져도 점수/태그/budget 집계가 깨지지 않게 호출부 주변 변수 흐름 점검

### B. `sweetener.py`
- `from .pick_protection_decorator import default_sweetener_protection`
- sweetener 후보 생성 시 픽 보호 variant를 붙이는 경로에서 호출

조치:
1. import 삭제
2. `default_sweetener_protection(...)` 호출 분기 전체 삭제
3. 해당 분기 전용 태그/카운터가 있으면 같이 제거

## 2) 테스트 의존성 (삭제 후 테스트 실패 예상)

### A. `test_skeleton_modifiers_protection_swap.py`
- 모듈을 직접 import: `choose_top_n_options`
- 삭제 시 테스트 import 단계에서 즉시 실패

조치(둘 중 하나 선택):
1. 이 테스트를 제거하거나,
2. 테스트 목적을 `types.py`의 modifier 기본값 검증만 남기도록 축소하고 decorator 함수 의존 제거

### B. `test_template_eval_fallback_in_core.py`
- `patch("trades.generation.dealgen.core.maybe_apply_pick_protection_variants", ...)`
- core에서 해당 심볼이 사라지면 patch 대상이 없어 실패

조치:
1. patch 항목 제거
2. fallback 동작 검증 자체에는 보호 decorator가 필수 아님을 기준으로 테스트 fixture 정리

## 3) 설정/플래그 정리 포인트 (고아 설정 방지)

`pick_protection_decorator.py` 내부에서만 읽는 동적 config 키:
- `pick_protection_decorator_enabled`
- `pick_protection_max_variants`
- `pick_protection_topn_options`
- `pick_protection_comp_base`

`sweetener.py` 경유 키:
- `sweetener_include_pick_protection_variant`

조치:
1. 코드베이스에서 위 키 참조를 전부 제거
2. 사용자 설정 파일/운영 문서/예시 config에 남아있다면 같이 삭제

## 4) 남겨야 하는 것 vs 같이 지워도 되는 것

### 남겨야 함 (SSOT/런타임 핵심)
- `trades/protection.py` (정규화/검증 SSOT)
- `trades/rules/builtin/pick_protection_schema_rule.py`
- `trades/pick_settlement.py`
- `trades/models.py` 내 protection 파싱/정규화

이 영역은 **실행/정산 규칙**이고, decorator 제거와 별개로 계속 필요.

### 같이 지워도 무방 (decorator 전용)
- `pick_protection_decorator.py`
- 해당 모듈에만 의존한 테스트/patch/import
- decorator 전용 observability 태그/통계 키(있다면)

## 5) 일괄 삭제 순서 (권장)

1. `core.py`에서 decorator import/호출 제거
2. `sweetener.py`에서 decorator import/호출 제거
3. 관련 테스트 수정/삭제
4. `pick_protection_decorator.py` 파일 삭제
5. `rg`로 잔존 참조 0 확인
6. 트레이드 생성 관련 테스트 묶음 실행

권장 확인 명령:
- `rg -n "pick_protection_decorator|maybe_apply_pick_protection_variants|default_sweetener_protection|choose_top_n_options" trades`
- `python -m pytest trades/generation/dealgen -q`

## 6) 리스크 요약

- 가장 큰 리스크는 "파일 삭제 자체"보다 **import/patch 대상 심볼 잔존**이다.
- 반대로, SSOT 보호 규칙(`trades/protection.py`, rules, settlement)은 decorator와 독립적이므로 유지하면 된다.
- 즉, 생성기(dealgen) 계층에서 decorator 연결선만 정확히 끊으면 런타임 안정성은 확보 가능하다.
