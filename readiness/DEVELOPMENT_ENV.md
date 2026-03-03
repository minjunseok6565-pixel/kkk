# Readiness 개발 환경 최적화 가이드

이 문서는 `readiness/` 패키지 코드를 기준으로, **개발/디버깅/튜닝을 빠르게 반복**하기 위한 실전 체크리스트입니다.

## 1) 패키지 역할과 진입점(먼저 볼 파일)

- 공개 API는 `prepare_game_readiness`, `apply_readiness_to_team_state`, `finalize_game_readiness` 3개입니다.
- 실제 구현은 `readiness.service`, DB I/O는 `readiness.repo`, 수식 SSOT는 `readiness.formulas`, 튜닝 상수는 `readiness.config`에 분리되어 있습니다.
- 준비/적용/확정 단계의 데이터 컨테이너는 `PreparedGameReadiness`, `PreparedTeamSchemes`, `TacticsMultipliers` dataclass로 고정되어 있습니다.

권장 코드 읽기 순서:
1. `types.py` (입출력 구조 파악)
2. `service.py` (게임 전/후 호출 흐름)
3. `formulas.py` (순수 수학 규칙)
4. `config.py` (튜닝 포인트)
5. `repo.py` (SQLite 상태 저장 형태)

## 2) 아키텍처 원칙 (개발 중 반드시 유지)

### SSOT 분리 원칙

- 수학 로직은 `formulas.py`의 순수 함수에 둡니다. (`service.py`가 직접 수식 중복 구현하지 않도록)
- DB 레이어는 `repo.py`에 유지하고, 비즈니스 규칙은 `service.py`로 분리합니다.
- 날짜/시간은 인게임 ISO 값만 사용하고, 호스트 OS 시계를 사용하지 않습니다.

### 안전성 원칙

- `formulas.py`는 입력이 깨져도 예외를 최대한 삼키고 clamp/기본값으로 복구합니다.
- `service.finalize_game_readiness`는 결과 payload가 부족하면 warning 후 no-op 하도록 방어되어 있습니다.
- `apply_readiness_to_team_state`는 tactics 필드가 없거나 비정상이어도 조용히 종료합니다.

## 3) 핵심 데이터 흐름 (디버깅 관점)

## 3-1. 경기 전: `prepare_game_readiness`

1. `game_date_iso`를 검증하고, 홈/원정 팀 ID를 정규화합니다.
2. `resolve_effective_schemes`로 실제 전술 스킴 키(공/수)를 확정합니다.
3. 양 팀 로스터를 읽어 전체 player_id 집합을 만듭니다.
4. `player_sharpness_state`에서 선수 샤프니스를 가져와, 경과일(`days_since`) 기준 decay를 적용합니다.
   - 부상 등으로 unavailable 힌트가 들어온 PID는 `SHARPNESS_DECAY_PER_DAY_OUT`를 사용해 더 빠르게 감소합니다.
5. decay 결과로 `sharpness_attr_mods`를 계산해 `attrs_mods_by_pid`를 구성합니다.
6. 팀별 선택 스킴의 familiarity를 `team_scheme_familiarity_state`에서 읽고 decay 적용 후,
   - tactics multiplier 계산
   - (옵션) team-wide familiarity attribute mod 합산
7. 이 결과를 `PreparedGameReadiness`로 반환합니다.

## 3-2. 경기 중: `apply_readiness_to_team_state`

- `TacticsMultipliers`를 팀 tactics 값에 곱하고, `TACTICS_MULT_MIN~MAX` 범위로 clamp합니다.
- 저장은 하지 않고 메모리 state에만 반영합니다.

## 3-3. 경기 후: `finalize_game_readiness`

1. `raw_result.game_state.minutes_played_sec`를 읽어 선수 minutes를 계산합니다.
2. 경기 전 sharpness(`prepared.sharpness_pre_by_pid`) + minutes 기반 gain으로 post sharpness 계산.
3. 사용한 팀 스킴(offense/defense)에 familiarity gain 적용.
4. `upsert_player_sharpness_states`, `upsert_team_scheme_familiarity_states`로 저장.
5. 저장 시점 타임스탬프도 게임 날짜 기반 UTC-like 문자열을 사용합니다.

## 4) 빠른 튜닝을 위한 변경 우선순위

튜닝 시 아래 순서로 바꾸면 영향 범위를 예측하기 쉽습니다.

1. **감쇠/증가 기울기**
   - 샤프니스: `SHARPNESS_DECAY_PER_DAY`, `SHARPNESS_GAIN_*`
   - familiarity: `FAMILIARITY_DECAY_K`, `FAMILIARITY_GAIN_PER_GAME`
2. **출력 영향도**
   - 샤프니스 속성치 영향: `SHARPNESS_ATTR_WEIGHTS`
   - 전술 multiplier 영향: `OFF_*`, `DEF_*`, `TACTICS_MULT_MIN/MAX`
3. **옵션성 기능 on/off**
   - `ENABLE_FAMILIARITY_ATTR_MODS`
4. **속성별 세부 가중치**
   - `FAMILIARITY_ATTR_WEIGHTS_OFFENSE/DEFENSE`

## 5) 개발환경 최적화용 체크리스트

### A. “수식 수정” 작업

- `formulas.py` 먼저 수정
- `service.py`에서 동일한 수식 중복 구현이 다시 생기지 않았는지 확인
- clamp 범위(0~100, multiplier min/max)가 유지되는지 확인

### B. “DB 스키마/저장” 작업

- `repo.py`의 입력 정규화(`_norm_date_iso`, `_clamp100`)를 유지
- bulk upsert 경로가 깨지지 않는지 확인
- row 누락 시 no-op이 의도대로인지 확인

### C. “게임 시뮬 결과 반영” 작업

- `raw_result.game_state.minutes_played_sec` 경로가 바뀌었는지 확인
- 누락 시 경고 후 스킵되는 기존 안전 동작을 유지

## 6) 추천 로컬 점검 루틴 (반복 개발용)

1. 문법 점검:
```bash
python -m compileall readiness
```

2. readiness 모듈 import 점검:
```bash
python - <<'PY'
import readiness
print(readiness.__all__)
PY
```

3. 수식 smoke check (예외 없이 숫자 반환되는지):
```bash
python - <<'PY'
from readiness import formulas as f
print('sharp decay', f.decay_sharpness_linear(70, days=3))
print('sharp gain', f.apply_sharpness_gain(55, minutes=28))
print('fam decay', f.decay_familiarity_exp(60, days=5))
print('fam gain', f.apply_familiarity_gain(40))
print('mult', f.tactics_mult_from_familiarity(off_fam=45, def_fam=62))
PY
```

## 7) 코드 리뷰 시 자주 보는 포인트

- `service.py`가 `formulas.py`를 통해서만 수학을 적용하는가?
- `repo.py`에서 날짜/수치 정규화가 유지되는가?
- `config.py` 상수 이름 변경 시, 참조부(`formulas.py`, `service.py`)가 모두 갱신됐는가?
- 새로운 로직이 들어와도 `PreparedGameReadiness` 구조를 깨지 않는가?

---

이 문서는 현재 `readiness/` 코드의 실제 구조/함수/데이터 흐름만을 근거로 작성되었습니다.
