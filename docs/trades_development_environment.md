# Trades 패키지 개발환경 가이드 (코드 기반)

이 문서는 `trades/` 패키지의 실제 코드 구조를 기준으로, **개발 시 빠르게 검증하고 안전하게 수정**할 수 있도록 정리한 작업 가이드다.

## 1) 현재 구조를 기준으로 한 핵심 개발 루프

트레이드 기능의 메인 흐름은 아래 순서를 기준으로 보면 된다.

1. **입력 정규화**: `parse_deal` + `canonicalize_deal` (`trades.models`)
2. **규칙 검증**: `validate_deal` (`trades.validator`)
3. **DB 반영**: `apply_deal_to_db` (`trades.apply`)
4. **협상/커밋 처리**: `trades.agreements`, `trades.negotiation_store`
5. **AI 평가/카운터**: `trades.valuation.*`, `trades.counter_offer.*`
6. **자동 시장 tick**: `trades.orchestration.tick_loop.run_trade_orchestration_tick`

실제 API 진입점은 `app/api/routes/trades.py`에 있으며, `/api/trade/submit`, `/api/trade/submit-committed`, `/api/trade/negotiation/*`, `/api/trade/evaluate`가 이 파이프라인을 사용한다.

---

## 2) 규칙 검증(Validation) 작업 시 체크포인트

### `validate_deal` 동작 특성

- `canonicalize_deal`을 먼저 수행한 뒤 룰 검증을 진행한다.
- `integrity_check` 플래그를 통해 `repo.validate_integrity()` 실행 여부를 제어한다.
  - standalone 호출은 기본적으로 무결성 검사를 수행.
  - tick context가 있는 경우에는 중복 검사 회피 로직이 있다.
- 예외는 `TradeError`로 변환해서 API 레이어가 500 대신 도메인 에러를 반환하도록 설계되어 있다.

### 현재 기본 룰 실행 순서(`trades/rules/builtin/__init__.py`)

아래 룰들이 registry에 등록된다.

- `AssetLockRule`
- `DeadlineRule`
- `DealShapeRule`
- `DuplicateAssetRule`
- `PickProtectionSchemaRule`
- `SwapUniquenessRule`
- `SwapYearRoundCapacityRule`
- `OwnershipRule`
- `RosterLimitRule`
- `PlayerEligibilityRule`
- `ReturnToTradingTeamRule`
- `PickRulesRule`
- `SalaryMatchingRule`
- `TeamLegsRule`

룰 추가/수정 시에는 **priority + rule_id 정렬로 실행**된다는 점(`validate_all`)을 같이 확인해야 한다.

---

## 3) 동시성/일관성 관련 주의점 (수정 시 가장 중요)

### 단일 프로세스 직렬화 락

- `trades.apply.apply_deal_to_db`는 trade 실행 경로에서 tick과의 충돌을 피하기 위해 직렬화 락을 고려한다.
- `trades.orchestration.tick_loop.run_trade_orchestration_tick`도 동일한 직렬화 락(`trade_exec_serial_lock`)을 사용한다.

즉, **유저 커밋 경로와 GM 오케스트레이션 tick 경로는 같은 락 정책을 공유**한다. 이 영역을 변경할 때는 lock 범위/진입 타이밍을 반드시 함께 점검해야 한다.

---

## 4) 협상(Negotiation) 개발 시 상태 모델 이해

`trades.negotiation_store`의 세션 스키마는 협상 기능 디버깅의 기준점이다.

핵심 필드:

- `status` (예: ACTIVE/CLOSED)
- `phase` (예: INIT/COUNTER_PENDING/ACCEPTED/REJECTED)
- `draft_deal`
- `committed_deal_id`
- `last_offer`
- `last_counter`
- `constraints`
- `valid_until`
- `summary`
- `relationship` (`trust`, `fatigue`, `promises_broken`)
- `market_context`

`/api/trade/negotiation/commit` 경로에서는:

- 먼저 유효한 deal을 저장하고,
- 상대팀 관점 valuation을 수행한 뒤,
- ACCEPT면 committed deal 생성,
- COUNTER면 counter_offer 생성 및 `last_counter` 저장,
- 실패/거절이면 메시지와 phase를 업데이트한다.

특히 `COUNTER_PENDING`에서 `dedupe_hash`가 일치하면 fast-accept 되는 경로가 있으므로, counter 로직 수정 시 이 해시 경로를 함께 봐야 한다.

---

## 5) 자동 트레이드 생성/시장 tick 개발 포인트

- tick 엔트리: `run_trade_orchestration_tick`
- 생성 context: `build_trade_generation_tick_context`
- 생성기: `DealGenerator`
- 정책: `trades.orchestration.policy`
- 시장 상태 저장: `trades.orchestration.market_state`

`dry_run` 옵션, 하루 1회 실행 가드(`ALREADY_RAN_TODAY`), human-controlled team 처리 로직이 포함되어 있어 실험 시 재현성/안전성 확인에 유리하다.

---

## 6) 밸류에이션(valuation) 수정 시 권장 절차

- 서비스 진입점: `trades.valuation.service.evaluate_deal_for_team`
- runtime context: `trades.valuation.env.ValuationEnv`
  - 시즌 연도 + cap model을 함께 들고 다니는 SSOT 컨테이너

수정 시 최소 확인 항목:

1. `validate=False`를 넘기는 호출부에서 사전 검증이 실제로 수행되는지
2. `include_breakdown` 옵션 on/off 결과가 API 응답 스키마에 맞는지
3. 협상 커밋 경로(`negotiation/commit`)에서 경량 평가(summary)가 유지되는지

---

## 7) 개발환경 최적화를 위한 로컬 체크 명령 (빠른 피드백용)

아래 명령은 구조 변경 후 빠르게 문법/임포트 안정성을 확인하는 용도다.

```bash
python -m compileall trades app/api/routes/trades.py app/services/trade_facade.py
```

룰/검증 계층만 빠르게 확인하려면:

```bash
python -m py_compile trades/validator.py trades/rules/registry.py trades/rules/builtin/__init__.py
```

협상/밸류에이션 경로만 수정했을 때 최소 체크:

```bash
python -m py_compile \
  trades/negotiation_store.py \
  trades/counter_offer/init.py \
  trades/counter_offer/builder.py \
  trades/valuation/service.py
```

---

## 8) 실무 권장 변경 단위

변경은 아래 단위로 쪼개는 것이 안전하다.

- **A. 모델/정규화 변경**: `trades.models` + API 파서 입력 영향 확인
- **B. 룰 변경**: `trades.rules.*` + `validate_deal` 호출부 영향 확인
- **C. 반영 변경**: `trades.apply` + 직렬화 락/후처리(캐시, 무결성) 확인
- **D. 협상 변경**: `negotiation_store` + `routes/trades.py` phase 전이 확인
- **E. AI 변경**: `valuation/*`, `counter_offer/*`, `generation/*`를 분리 검증

이 패키지는 검증/적용/협상/자동화가 강하게 연결되어 있으므로, 한 PR에서 여러 축을 동시에 크게 바꾸기보다 위 단위로 분리하는 편이 디버깅 비용이 낮다.
