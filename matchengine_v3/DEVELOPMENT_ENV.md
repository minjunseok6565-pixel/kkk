# matchengine_v3 개발환경 가이드 (코드 기반)

이 문서는 `matchengine_v3` 관련 코드를 실제로 읽고, **로컬 개발 루프를 빠르게/안정적으로** 만들기 위한 체크리스트만 정리한 문서입니다.

## 1) 엔진 호출 경로(디버깅 진입점)

`matchengine_v3`는 API 레이어에서 직접 호출되지 않고, `sim.league_sim.simulate_single_game()`를 통해 호출됩니다.

- API `/api/simulate-game` → `simulate_single_game(...)` 호출. (`app/api/routes/sim.py`)
- `simulate_single_game(...)`는 `master_schedule`에서 해당 경기(날짜+home/away)를 찾아야만 진행됩니다. (`sim/league_sim.py`)
- 실제 엔진 호출은 `_run_match(...)` 내부의 `matchengine_v3.sim_game.simulate_game(...)`입니다. (`sim/league_sim.py`)

즉, 엔진 이슈를 볼 때는 **API → league_sim → matchengine_v3.sim_game** 순으로 따라가면 가장 빠릅니다.

---

## 2) 개발 중 가장 먼저 맞춰야 하는 전제조건

### 2-1. 스케줄/상태 초기화 전제

`simulate_single_game(...)`는 `master_schedule`이 비어 있으면 바로 예외를 던집니다. 따라서 개발 세션 시작 시 상태 초기화가 필수입니다. (`sim/league_sim.py`, `state.py`)

권장 순서:

1. `state.startup_init_state()` 실행
2. 그 다음 `simulate_single_game(...)` 혹은 API 테스트

### 2-2. ID/컨텍스트 SSOT 전제

엔진 상위 계층은 `schema.GameContext`를 통해 home/away SSOT를 유지합니다.

- `GameContext`는 빈 값/비정규 팀 ID/동일 팀 매치업을 엄격하게 거부합니다. (`schema.py`)
- 따라서 디버깅용 호출에서도 팀 ID는 정규 포맷(예: 3글자 팀 코드)으로 넣는 습관이 좋습니다.

---

## 3) 코드 구조를 활용한 빠른 디버그 포인트

### 3-1. `sim_game.py`: 경기 루프/검증/리포트 중심

- `simulate_game(...)`가 전체 경기 오케스트레이션을 담당합니다. (`matchengine_v3/sim_game.py`)
- era/game config는 LRU 캐시(`_cached_era_and_game_config`)를 사용하므로 동일 era 반복 실험 시 비용이 줄어듭니다. (`matchengine_v3/sim_game.py`)

**의미:**
- 밸런싱 실험(같은 era 다회 실행)은 `simulate_game` 경로를 유지해야 캐시 이점을 그대로 얻습니다.

### 3-2. `sim_possession.py`: 포제션 단위 로직 분기

- action priors 빌드, quality/role fit 반영, resolve 호출, 턴오버 dead/live-ball 정책까지 포제션 단에서 처리됩니다. (`matchengine_v3/sim_possession.py`)
- 포제션별 컨텍스트(`ctx`)에 오류를 축적하는 내부 경로가 있어, 로깅 시 `ctx['errors']`를 보는 것이 유효합니다. (`matchengine_v3/sim_possession.py`)

### 3-3. `resolve.py`: outcome 확정 핸들러

- `resolve_outcome(...)`가 shot/pass/turnover/foul/reset 처리 분기를 최종 수행합니다. (`matchengine_v3/resolve.py`)
- home/away team_id 정합성 검사를 수행하므로, 팀 ID mismatch류 버그는 이 레이어에서 빠르게 드러납니다. (`matchengine_v3/resolve.py`)

### 3-4. `validation.py`: 입력 전처리/전술 정규화

- 전술(`TacticsConfig`) sanitization, multiplier clamp, 허용 키 검증이 모여 있습니다. (`matchengine_v3/validation.py`)
- defense scheme alias 정규화는 `tactics.canonical_defense_scheme()`을 기준으로 동작합니다. (`matchengine_v3/tactics.py`, `matchengine_v3/validation.py`)

---

## 4) 개발환경 최적화를 위한 실전 체크 명령

아래는 "빠른 실패"를 위한 최소 루프입니다.

```bash
# 1) 문법/임포트 깨짐 조기 탐지
python -m compileall matchengine_v3 sim app

# 2) 엔진 핵심 모듈 import 스모크
python - <<'PY'
from matchengine_v3.sim_game import simulate_game
from matchengine_v3.sim_possession import simulate_possession
from matchengine_v3.resolve import resolve_outcome
print('import-ok')
PY
```

필요 시 일정 기반 단일 경기 루프(상태 초기화 포함):

```bash
python - <<'PY'
import state
from sim.league_sim import simulate_single_game

state.startup_init_state()
# 실제 팀 ID/날짜는 현재 master_schedule에 있는 값으로 맞춰 호출해야 함
print('state-init-ok')
PY
```

---

## 5) 작업 원칙(이 레포에서 특히 중요한 부분)

1. **server/API에서 엔진 직접 호출 금지**
   - 현재 계약은 `league_sim.simulate_single_game` 경유입니다. (`app/api/routes/sim.py`)
2. **home/away SSOT는 GameContext 중심으로 유지**
   - ad-hoc로 side를 추정/수정하는 패치는 버그를 만들 가능성이 큽니다. (`schema.py`, `sim/league_sim.py`)
3. **전술 문자열은 canonicalization 통과를 전제로 처리**
   - 별칭 추가가 필요하면 `tactics.py` alias 테이블을 먼저 갱신하는 편이 안전합니다. (`matchengine_v3/tactics.py`)
4. **반복 실험은 동일 era를 유지해 캐시 이점 활용**
   - 무의미한 설정 재로딩 비용을 줄일 수 있습니다. (`matchengine_v3/sim_game.py`)

---

## 6) 권장 파일별 담당 영역(온보딩 단축용)

- `matchengine_v3/sim_game.py`: 경기 전체 루프/오버타임/결과 구성
- `matchengine_v3/sim_possession.py`: 포제션 단위 진행
- `matchengine_v3/resolve.py`: 결과(outcome) 확정 처리
- `matchengine_v3/validation.py`: 입력/전술 sanitize
- `matchengine_v3/tactics.py`: 전술 데이터 구조 + defense scheme canonicalization
- `sim/league_sim.py`: 실제 서비스 진입점(상태/스케줄/부상/피로/컨텍스트 연결)

위 파일들만 먼저 고정해서 보면, 현재 엔진 수정 시 탐색 시간이 크게 줄어듭니다.
