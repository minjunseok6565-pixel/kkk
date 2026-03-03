# data 패키지 개발 환경 최적화 가이드

이 문서는 현재 `data/` 디렉토리에 포함된 정적 데이터와 파이썬 모듈을 **코드 기준으로만** 정리한 개발 가이드입니다.

## 1) 현재 `data/` 구성

- `data/gm_profiles.json`
  - 30개 팀 GM 프로필 시드 데이터(JSON list[object]).
  - 각 항목은 `Team` 키와 GM 성향 수치(`CompetitiveWindow`, `PickPreference`, `RiskTolerance` 등)를 포함.
- `data/team_situation.py`
  - 팀 상황(경쟁 티어, 트레이드 포지셔닝, 니즈 등) 계산 모듈.
  - `TeamSituation`, `TeamNeed`, `TeamConstraints`, `TeamSituationSignals` 같은 dataclass 기반 출력 구조 제공.
- `data/names/first_names.json`, `data/names/last_names.json`
  - 이름 생성용 은행(JSON list[str]).
  - 현재 각 파일 300개 항목.

---

## 2) 실제 런타임 연결 지점(개발 시 꼭 알아야 할 부분)

### GM 프로필 시드

- `config.py`의 `GM_PROFILES_SEED_PATH`가 `data/gm_profiles.json`을 가리킴.
- `state_modules/state_bootstrap.py`에서 초기화 시 `_load_gm_profiles_seed(...)`를 통해 파일을 읽고 검증 후 DB에 upsert.
- 검증 규칙(코드상):
  - 루트는 list여야 함.
  - 각 항목은 object여야 함.
  - `Team` 필수.
  - `Team`은 `ALL_TEAM_IDS` 안에 있어야 함.
  - 모든 팀이 빠짐없이 포함되어야 함.

### 이름 데이터

- `college/names.py`가 `config.DATA_DIR`를 기반으로 `data/names/*.json` 경로를 계산.
- 두 파일이 모두 있으면 JSON을 읽어 정제(공백/중복 제거) 후 캐시.
- 파일이 없으면(기본 strict=False) fallback 이름 목록으로 동작하며 경고 출력.
- `strict=True`일 때는 누락 시 예외 발생.

### team_situation 모듈

- draft/trade 쪽에서 `data.team_situation`을 직접 import해 팀 니즈/컨텍스트 계산에 사용.
- 순환 import 방지를 위해 트레이드 룰 헬퍼는 `_load_trade_rule_helpers()`에서 lazy-load.
- 로그 스팸을 줄이기 위해 `_warn_limited(...)`로 경고 출력 횟수 제한.

---

## 3) 개발 환경 최적화 체크리스트

아래는 **실제 코드 동작과 맞춘 최소 검증 루틴**입니다.

### A. 데이터 무결성 빠른 점검

```bash
python - <<'PY'
import json
from pathlib import Path

p = Path('data/gm_profiles.json')
arr = json.loads(p.read_text(encoding='utf-8'))
print('gm_profiles count =', len(arr))
print('keys(sample) =', sorted(arr[0].keys()))

for q in [Path('data/names/first_names.json'), Path('data/names/last_names.json')]:
    names = json.loads(q.read_text(encoding='utf-8'))
    print(q.name, 'count =', len(names), 'type =', type(names).__name__)
PY
```

목적:
- 데이터 타입/개수 이상 여부를 로컬에서 즉시 확인.
- 대규모 시뮬레이션 전에 파일 깨짐(JSON 형식 오류)을 조기 발견.

### B. 부트스트랩 유효성 점검(강한 검증)

`state_modules/state_bootstrap.py`의 `_load_gm_profiles_seed(...)`는 포맷 오류를 fail-fast로 바로 예외 처리합니다.
개발 중 `gm_profiles.json`을 수정했다면, 최소 1회는 게임 초기화 루틴을 실행해 이 검증을 통과하는지 확인하는 것이 안전합니다.

### C. 이름 생성 strict 모드 점검

이름 파일을 편집했거나 교체했다면:

```bash
python - <<'PY'
from college.names import get_name_bank
first, last = get_name_bank(strict=True)
print('first/last =', len(first), len(last))
PY
```

목적:
- fallback에 의존하지 않고 실제 운영용 이름 파일이 정상 로드되는지 확인.

---

## 4) 변경 시 권장 워크플로우

1. `data/*.json` 수정
2. JSON 문법 검사(간단 Python 로드)
3. `get_name_bank(strict=True)` 확인(이름 데이터 수정 시)
4. 게임 초기화 루틴 1회 실행으로 GM 시드 검증 확인
5. 트레이드/드래프트 관련 기능에서 team_situation 동작 smoke test

이 순서로 진행하면, 데이터 변경으로 인해 늦게 터지는 런타임 에러를 대부분 초기에 잡을 수 있습니다.

---

## 5) 주의사항(코드 기준)

- `gm_profiles.json`은 DB 시드 성격이므로 팀 누락/오탈자(`Team`)가 있으면 초기화 단계에서 실패할 수 있습니다.
- 이름 파일이 없어도 기본적으로는 fallback으로 실행되지만, 이는 개발 편의 경로입니다. 운영 품질 점검 시에는 `strict=True`를 사용해 누락을 강제로 탐지하는 것이 좋습니다.
- `team_situation.py`는 여러 서브시스템에서 참조되므로, 이 파일 수정 시 import 경로 및 순환 참조 안전성(lazy-load 유지)을 함께 확인해야 합니다.

