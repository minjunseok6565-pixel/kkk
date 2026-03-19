# asset_lock 기능 전역 제거 실행 계획

## 0) 목표/원칙
- 목표: **딜 진행 중인 자산에 대한 `asset_lock` 기반 차단 로직을 코드 전역에서 완전히 제거**한다.
- 원칙:
  - 딜 제안/카운터/협상/검증 어떤 경로에서도 `asset_lock` 때문에 거절되지 않아야 한다.
  - `allow_locked_by_deal_id` 같은 예외 파라미터 자체를 제거해, 잠금 기반 분기 코드를 남기지 않는다.
  - 개발 단계 전제에 따라, 마이그레이션 호환성보다 “기능 흔적 제거 + 앱 실행 가능 상태”를 우선한다.
  - 본 문서에서 말하는 `asset_lock`은 **“진행 중인 거래(agreement)에 묶인 자산의 추가 협상/제안 차단”** 로직을 의미한다.

---

## 1) 삭제 대상 기능 정의 (정확한 범위)
아래를 `asset_lock 기능`으로 정의하고 전부 제거한다.

1. 상태 저장소의 `asset_locks` 상태키 및 접근 API (`asset_locks_get/set`)
2. 룰 엔진의 `AssetLockRule` 및 `rule_id="asset_lock"` 계열 분기
3. 딜 검증/생성/카운터오퍼 전반에 전파된 `allow_locked_by_deal_id` 인자 및 관련 캐시/분기
4. 자산 카탈로그의 `LockInfo` 기반 필터링 (`is_locked`) 및 잠금 예외 처리
5. 계약(agreement) 생성/검증 시점의 자산 잠금 생성/필수성 검증/해제 로직
6. 에러코드/실패분류의 `ASSET_LOCKED` 및 `ASSET_LOCK` 분류
7. 테스트 코드에서 잠금 전제를 가정하는 fixture/assertion

> 참고: `trade_block`(시장 공개 등록), `trade_exec_serial_lock`(동시성 락), `draft_picks.trade_locked`(픽 프리즈)는 목적이 다르므로 본 작업의 1차 제거 범위에서 제외한다. 다만 이름 충돌로 혼동되지 않도록 코드/주석에서 "asset lock" 용어를 명확히 정리한다.

---

## 2) 파일별 구체 수정 계획 (누락 방지 체크리스트)

### A. 상태/초기화 계층

- [ ] `state.py`
  - `__all__`에서 `asset_locks_get`, `asset_locks_set` 제거.
  - `export_trade_context_snapshot()` 반환 payload에서 `asset_locks` 제거.
  - `asset_locks_get()/asset_locks_set()` 함수 자체 삭제.
  - `asset_locks`를 전제한 방어코드(복사/기본값 주입) 제거.

- [ ] `state_modules/state_trade.py`
  - 필수 trade state key 목록에서 `asset_locks` 제거.
  - 타입 보정(`state["asset_locks"] = {}`) 제거.

- [ ] `state_schema.py`
  - top-level schema 허용키/기본 state/검증 로직에서 `asset_locks` 제거.
  - `STATE_SCHEMA_VERSION` 변경 여부를 결정하고(개발단계 기준), 버전 변경 시 관련 로더/검증 경로 동기화.

### B. 룰 엔진/검증 계층

- [ ] `trades/rules/builtin/asset_lock_rule.py`
  - 파일 전체 삭제 (rule class 완전 제거).

- [ ] `trades/rules/builtin/__init__.py`
  - `AssetLockRule` import 제거.
  - `BUILTIN_RULES`에서 `AssetLockRule()` 제거.

- [ ] `trades/rules/base.py`
  - `ctx_state["asset_locks"]` 복사/주입 로직 제거.

- [ ] `trades/rules/tick_context.py`
  - `ctx_state_base["asset_locks"]`를 `_ReadOnlyDict`로 감싸는 처리 제거.

- [ ] `trades/validator.py`
  - 함수 시그니처에서 `allow_locked_by_deal_id` 제거.
  - `extra={"allow_locked_by_deal_id": ...}` 주입 제거.

### C. 계약(agreement)/유지보수 계층

- [ ] `trades/agreements.py`
  - `asset_locks_get`, `asset_locks_set` import 제거.
  - `create_committed_deal()`에서 `_lock_assets_for_deal(...)` 호출 제거.
  - `_lock_assets_for_deal()` 함수 삭제.
  - `verify_committed_deal()`의 “Asset lock missing” 검증 블록 삭제.
  - `release_locks_for_deal()` 삭제 또는 no-op 정리 후 모든 호출부에서 제거.
  - 만료/실행 처리에서 lock 해제를 전제한 호출 경로 정리.

- [ ] `trades/maintenance.py`
  - docstring에서 “releases asset locks” 관련 문구 제거.
  - agreement 만료 정리 설명으로 정합화.

### D. 생성/탐색/협상 파이프라인 (핵심)

- [ ] `trades/generation/asset_catalog.py`
  - `LockInfo` 구조체 및 lock 계산 함수 제거.
  - `build_asset_catalog(... allow_locked_by_deal_id=...)` 인자 제거.
  - players/picks/swaps/fixed assets 구성 시 `lock.is_locked` 필터 분기 제거.
  - 카탈로그 산출 결과에서 lock 관련 필드 제거(필요 시 타입 동시 정리).

- [ ] `trades/generation/generation_tick.py`
  - `allow_locked_by_deal_id` 전달 경로 제거.

- [ ] `trades/generation/__init__.py`
  - export 목록(`__all__`)에서 `LockInfo` 제거.

- [ ] `trades/generation/dealgen/core.py`
  - `allow_locked_by_deal_id` 기반 catalog 재빌드/캐시 키 로직 제거.
  - `_get_asset_catalog_for_call(...)`의 allow-locked 분기 제거.
  - 모든 하위 호출 인자에서 `allow_locked_by_deal_id` 제거.

- [ ] `trades/generation/dealgen/targets.py`
  - `_is_locked_candidate(...)` import/호출 제거.
  - 잠금 선필터 제거 후 정렬/스코어링만 유지.

- [ ] `trades/generation/dealgen/utils.py`
  - `_is_locked_candidate()` 함수 삭제.
  - 잠금 예외 설명 주석 삭제.

- [ ] `trades/generation/dealgen/fit_swap.py`
  - `p.lock.is_locked` 조건 분기 제거.
  - 함수 시그니처의 `allow_locked_by_deal_id` 제거 및 연쇄 호출 정리.

- [ ] `trades/generation/dealgen/sweetener.py`
  - `allow_locked_by_deal_id` 파라미터/전달/검증호출 제거.
  - 잠금 필터링 로직(`_is_locked_candidate`) 제거.

- [ ] `trades/generation/dealgen/repair.py`
  - `tick_ctx.validate_deal(...allow_locked_by_deal_id=...)` 제거.

- [ ] `trades/generation/dealgen/pick_protection_decorator.py`
  - `allow_locked_by_deal_id` 파라미터 및 전달 제거.

- [ ] `trades/counter_offer/init.py`
  - `allow_locked_by_deal_id` 인자 제거.

- [ ] `trades/counter_offer/builder.py`
  - 함수 시그니처/내부 호출의 `allow_locked_by_deal_id` 제거.
  - 후보 필터의 `c.lock.is_locked` 분기 제거.

- [ ] `trades/valuation/service.py`
  - public API와 내부 경로의 `allow_locked_by_deal_id` 제거.

- [ ] `trades/orchestration/tick_loop.py`
  - `validate_deal(... allow_locked_by_deal_id=None ...)` 같은 잔여 호출 제거.

- [ ] `trades/orchestration/listing_policy.py`
  - listing 제외 조건의 `player.lock.is_locked` 분기 제거.

- [ ] `league_service.py`
  - 거래 실행 전 검증 호출에서 `allow_locked_by_deal_id=str(deal_id)` 전달 제거.
  - `validate_deal` 시그니처 변경과 함께 호출부 정합화.

- [ ] `app/api/routes/trades.py`
  - `/api/trade/submit-committed` 등 검증 호출에서 `allow_locked_by_deal_id` 전달 제거.
  - 요청/응답/에러 처리에서 lock 전제 문구 또는 분기가 남아있지 않도록 정리.

- [ ] `data/team_situation.py`
  - 컨텍스트/데이터클래스에서 `asset_locks` 필드 제거.
  - `trade_state["asset_locks"]` 읽기 제거.
  - `_count_team_related_locks()` 및 lock 기반 평가 신호 제거(또는 비잠금 지표로 대체).

### E. 타입/에러/분류

- [ ] `trades/errors.py`
  - `ASSET_LOCKED` 상수 제거.

- [ ] `trades/generation/dealgen/types.py`
  - `RuleFailureKind.ASSET_LOCK` 제거.
  - `parse_trade_error()`의 `ASSET_LOCKED` 분기 제거.
  - rule priority/defaults에 남은 `asset_lock` rule id 제거.
  - `rebuild_catalog_when_allow_locked` 설정 제거.

### F. 테스트 정리 (자산잠금 전제 제거)

- [ ] `trades/generation/test_asset_catalog_expendable.py`
  - `LockInfo(is_locked=False)` fixture 제거 및 새 구조 반영.

- [ ] `trades/generation/test_asset_catalog_outgoing_rework.py`
  - lock fixture 제거.

- [ ] `trades/generation/dealgen/test_template_eval_fallback_in_core.py`
  - `allow_locked_by_deal_id` 인자 제거 반영.

- [ ] `trades/orchestration/test_proactive_listing.py`
  - `SimpleNamespace(lock=...)` 기반 잠금 차단 assertion 제거/대체.

- [ ] (필요 시) `allow_locked_by_deal_id`, `ASSET_LOCKED`, `asset_lock`를 참조하는 나머지 테스트 파일 일괄 정리.

### G. 잔여 참조 제거용 전체 검색 기준 (완료 게이트)

최종적으로 아래 패턴 검색 결과가 **0건**이어야 한다.

- [ ] `rg -n "asset_locks|asset_lock|ASSET_LOCKED|allow_locked_by_deal_id|is_locked|lock\.deal_id" state.py state_modules trades tests`
- [ ] `rg -n "AssetLockRule|rule_id=\"asset_lock\"|rebuild_catalog_when_allow_locked" trades`
- [ ] `rg -n "asset_locks|allow_locked_by_deal_id|ASSET_LOCKED|LockInfo|asset_lock" state_schema.py league_service.py app/api/routes/trades.py data/team_situation.py trades/generation/__init__.py`
- [ ] `rg -n "asset_lock|allow_locked_by_deal_id|LockInfo" docs/skeleton`

> `is_locked`는 다른 도메인에서 합법적으로 쓰일 수 있으므로, 최종 게이트에서는 `asset_lock` 맥락 파일 범위를 지정해 확인한다.

---

## 3) 작업 순서 (실패 최소화)
1. **룰 제거**: `AssetLockRule` + registry 연결 제거.
2. **검증 인터페이스 정리**: `validate_deal` 시그니처와 호출부에서 `allow_locked_by_deal_id` 삭제.
3. **카탈로그/딜 생성 경로 정리**: `LockInfo`/lock 필터 제거.
4. **agreement 경로 정리**: lock 생성/검증/해제 삭제.
5. **상태 계층 정리**: `asset_locks` 상태키/API 제거.
6. **테스트 대량 정리**: lock 전제 fixture/assertion 제거.
7. **잔여 문자열 검색 게이트 통과**.

---

## 4) 완료 정의 (Definition of Done)
- 코드베이스 어디에서도 `asset_lock` 기능(상태, 룰, 파라미터, 에러코드, lock 필터)이 남아있지 않다.
- 트레이드 제안/협상/카운터오퍼 경로가 `asset_lock` 때문에 중단되지 않는다.
- 전체 테스트(최소 거래 도메인 핵심 테스트)가 통과한다.
- 최종 전역 검색에서 지정 패턴이 0건이다.

---

## 5) 리스크/주의사항 (개발단계 전제 반영)
- 이번 작업은 “마이그레이션 무시 가능” 전제이므로 DB/세이브 하위호환은 후순위로 둔다.
- 다만 런타임 에러 방지를 위해, 함수 시그니처 변경 시 모든 호출부를 같은 커밋에서 동기화해야 한다.
- `trade_block`, `trade_locked(픽 프리즈)`는 다른 기능이므로 실수로 제거하지 않도록 검색 패턴을 분리해 진행한다.
