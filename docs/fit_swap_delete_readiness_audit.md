# fit_swap.py 삭제 사전 점검 리포트 (공격적 일괄 삭제 기준)

## 목적
`trades/generation/dealgen/fit_swap.py`를 **단계적 마이그레이션 없이 한 번에 제거**할 때,
런타임 에러 없이 게임이 계속 동작하도록 사전 위험요소와 정리 순서를 식별한다.

## 결론 요약
- 현재 상태에서 `fit_swap.py`만 단독 삭제하면 **즉시 ImportError 발생 가능성이 높다**.
- 특히 `trades/counter_offer/builder.py`는 모듈 import 시점에 `fit_swap`를 직접 import하므로,
  서버/앱 부팅 시 바로 깨질 수 있다.
- `dealgen/core.py`는 local import + `ImportError` fallback이 있어 상대적으로 안전하지만,
  fit-swap 전제의 카운터/통계/config 필드는 잔존한다.

---

## 1) 삭제 시 즉시 깨지는 하드 의존성

### A. counter_offer 경로 (가장 치명적)
- 파일: `trades/counter_offer/builder.py`
- 현상: top-level에서 `from ..generation.dealgen.fit_swap import maybe_apply_fit_swap` 실행.
- 영향: `fit_swap.py` 삭제 시 builder import 단계에서 실패 → counter-offer 기능뿐 아니라
  해당 모듈을 import하는 상위 흐름까지 연쇄 실패 가능.

정리 필요:
1. top-level fit_swap import 제거
2. Strategy 1(FIT_SWAP) 분기 자체 제거 또는 no-op 처리
3. 관련 메타(`"FIT_SWAP"`, `candidates_tried`, `swapped`) 정리

### B. fit_swap 전용 테스트
- 파일: `trades/generation/dealgen/test_fit_swap_trigger_policy.py`
- 현상: fit_swap 내부 유틸 함수 직접 import 테스트.
- 영향: 파일 삭제 즉시 테스트 import 실패.

정리 필요:
1. 테스트 파일 삭제 또는 정책 함수가 다른 모듈로 이동되면 테스트도 이동

### C. core 테스트의 patch target
- 파일: `trades/generation/dealgen/test_template_eval_fallback_in_core.py`
- 현상: `patch("trades.generation.dealgen.fit_swap.maybe_apply_fit_swap", ...)` 사용.
- 영향: fit_swap 모듈이 없어지면 patch 대상 import 실패 가능.

정리 필요:
1. 해당 patch 구문 제거
2. core에서 fit-swap 호출이 사라지면 관련 mock도 같이 정리

---

## 2) 삭제 후 남는 소프트 의존성/잔재

### A. dealgen core 내부 fit-swap 루프
- 파일: `trades/generation/dealgen/core.py`
- BUY/SELL 양쪽에 다음 개념이 남아 있음:
  - `fit_swap_trials_by_base`
  - `max_fit_swap_trials_per_base`
  - `fit_swap_enabled` 분기
  - `stats.fit_swap_*` 카운터
  - local import `from .fit_swap import maybe_apply_fit_swap`

현재는 local import `ImportError` fallback으로 **즉시 크래시 회피**는 되지만,
코드 가독성과 유지보수 관점에서 fit_swap 제거 목적과 충돌.

정리 필요:
1. fit-swap 블록 전체 제거 (BUY/SELL 둘 다)
2. 관련 trial 카운터/seed 파생 호출 정리
3. fit-swap telemetry 증감 코드 제거

### B. DealGeneratorConfig / Stats 내 fit_swap 필드
- 파일: `trades/generation/dealgen/types.py`
- fit_swap 관련 설정/튜닝 파라미터와 통계 필드가 다수 존재.

정리 필요:
1. `DealGeneratorConfig`의 `fit_swap_*` 필드 제거
2. `DealGeneratorStats`의 `fit_swap_triggers/candidates_tried/success` 제거
3. 주석/문서 문자열에서 fit_swap 언급 제거

### C. CounterOfferConfig 내 fit_swap 옵션
- 파일: `trades/counter_offer/config.py`
- `enable_fit_swap`, `fit_swap_candidate_pool`, `fit_swap_try_top_n`, `fit_swap_max_repairs`
- `to_dealgen_config()`에서 `fit_swap_*`를 dealgen config로 주입 중

정리 필요:
1. CounterOfferConfig의 fit_swap 옵션 삭제
2. `to_dealgen_config()`에서 fit_swap 관련 override 삭제
3. 설명 주석(`existing dealgen.fit_swap`) 정리

---

## 3) 공격적 일괄 삭제 권장 순서 (한 번에 머지 기준)

1. `counter_offer/builder.py`에서 FIT_SWAP 전략 분기 + import 제거
2. `dealgen/core.py` BUY/SELL의 fit-swap 실행 블록 제거
3. `dealgen/types.py`의 fit-swap config/stats 필드 제거
4. `counter_offer/config.py`의 fit-swap 토글/예산 필드 제거
5. `trades/generation/dealgen/fit_swap.py` 파일 삭제
6. fit_swap 관련 테스트/patch 정리
   - `test_fit_swap_trigger_policy.py` 삭제
   - `test_template_eval_fallback_in_core.py`의 fit_swap patch 제거
7. 전체 검색으로 잔여 문자열 제거
   - 검색 키워드: `fit_swap`, `FIT_SWAP`, `counter:fit_swap`, `fit_swap_mode:`

---

## 4) 런타임 안정성 검증 체크리스트 (삭제 PR 필수)

최소 검증:
1. unit test (dealgen + counter_offer)
2. counter-offer 생성 진입 테스트 (base offer -> counter proposal 생성)
3. 일반 딜 생성(BUY/SELL) smoke test

권장 검증 포인트:
- 앱 시작/모듈 import 단계에서 예외 없는지
- `CounterOfferBuilder` 경로에서 전략 선택이 fit-swap 없이도 정상 fallback 되는지
- `DealGeneratorStats` 직렬화/로깅 소비부가 삭제된 필드를 참조하지 않는지

---

## 5) “흔적도 없이” 삭제 기준 정의

아래를 모두 만족하면 깔끔한 삭제로 볼 수 있다.

- 코드: `trades/` 하위에서 `fit_swap` 식별자 0건
- 테스트: fit_swap 모듈을 import/patch하는 테스트 0건
- 설정: 런타임 config/dataclass에 fit_swap 필드 0건
- 로그/telemetry: fit_swap 카운터 필드 0건
- 문서: 운영 문서에서 “현재 동작”으로 오해될 기술 제거(역사 문서는 별도)

---

## 6) 요약
지금 코드베이스는 `fit_swap.py`를 단독 삭제하면 `counter_offer/builder.py`에서 가장 먼저 깨진다.
따라서 삭제 자체보다 먼저 **import 경로 + 호출부 + config/stats + 테스트 patch 대상**을
동시에 제거하는 “원샷 정리”가 필요하다.
