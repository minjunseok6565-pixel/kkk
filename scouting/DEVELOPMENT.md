# Scouting 개발 환경 가이드

이 문서는 `scouting/` 패키지의 **현재 코드 구현**을 기준으로, 개발/디버깅 생산성을 높이기 위한 실무 가이드입니다.

## 1) 모듈 역할 분리 (빠른 진입)

- `scouting/service.py`
  - DB 중심 오케스트레이션 담당.
  - 팀별 스카우트 시드(`ensure_scouts_seeded`)와 월말 체크포인트(`run_monthly_scouting_checkpoints`)를 수행.
  - 핵심 룰: assignment가 없으면 no-op, 월말 기준 생성, 배정 14일 이내면 보고서 미생성(기본값). 
- `scouting/signals_v2.py`
  - 신호축(SIGNALS) 정의, true signal 계산, evidence/watchlist/delta 텍스트 재료 생성.
  - 사용자 노출은 raw 점수 대신 tier/confidence/range 중심.
- `scouting/report_ai.py`
  - Gemini 기반 보고서 텍스트 생성기.
  - 네트워크 의존 코드를 서비스/DB 로직과 분리해 오프라인 테스트를 쉽게 유지.

## 2) 개발 환경 기본 원칙

### A. 오프라인 우선 개발

`service.py`는 LLM 키가 없으면 보고서 텍스트 생성만 `FAILED_TEXT`로 처리하고 파이프라인 자체는 진행할 수 있게 구성되어 있습니다. 
즉, 로컬 개발에서는 **DB/신호 계산/체크포인트 로직부터 검증**하고, 마지막에만 LLM을 붙이는 것이 효율적입니다.

### B. 결정론(Determinism) 유지

스카우트/바이어스 룰의 난수는 `_stable_seed(...)` 기반으로 고정 시드화되어 있습니다. 
동일 입력 조건에서 재현 가능한 결과를 얻기 좋으므로, 디버깅 시에는 입력(assignment, 기간, 선수 데이터)을 고정해 비교하세요.

### C. 월말 기반 시뮬레이션

`run_monthly_scouting_checkpoints`는 `from_date~to_date` 사이에서 **월말이 지난 달만** 처리합니다. 
개발 중 “왜 생성이 안 되지?”가 자주 발생하므로, 테스트 날짜를 월말 포함 범위로 잡는 습관이 중요합니다.

## 3) 환경 변수/실행 파라미터 체크리스트

LLM 텍스트 생성은 아래 순서로 API 키를 찾습니다.

1. 함수 인자 `api_key`
2. `GEMINI_API_KEY`
3. `GOOGLE_API_KEY`
4. `GENAI_API_KEY`

키가 없으면 텍스트 생성은 스킵되며, 관련 상태/메타가 DB에 남습니다. 
따라서 로컬 개발에서는 아래 2단계 전략을 권장합니다.

- 1차: 키 없이 체크포인트 실행(로직 검증)
- 2차: 키 설정 후 동일 구간 재실행(텍스트 생성 검증)

## 4) 로컬 디버깅 루틴 (권장)

1. `ensure_scouts_seeded(...)`로 팀별 스카우트 시드 보장
2. assignment를 명시적으로 생성/활성화 (`status='ACTIVE'`)
3. `run_monthly_scouting_checkpoints(...)` 실행
4. 결과 요약(`created/skipped`) 확인
5. DB에서 `scouting_reports`, `scouting_assignments.progress_json` 확인

### 디버깅 포인트

- 보고서가 0건이면 우선 아래를 확인:
  - ACTIVE assignment 존재 여부
  - `assigned_date`와 월말 간격(기본 14일 제한)
  - 해당 month/assignment에 기존 report 존재 여부(멱등성)
  - 대상 선수 row 존재 여부
- 신호 이상치 확인:
  - `signals_v2.compute_true_signals` 입력(stats/attrs/derived/context) 누락 여부
  - `compute_stat_weight(games, mpg)`에 따라 raw vs production 반영 비중이 달라짐

## 5) 코드 수정 시 안전장치

- `service.py`의 month-end/assignment gating 순서를 바꾸면 게임 규칙 체감이 크게 변하므로, 조건식 순서를 유지한 채 수정하세요.
- `SIGNALS`의 key를 변경하면 service 쪽 state(`progress_json["signals"]`) 및 리포트 payload에 연쇄 영향이 있으므로, key rename은 마이그레이션 계획과 함께 진행하세요.
- `report_ai.py`는 네트워크 의존성이 optional import로 분리되어 있으므로, 서비스 로직 테스트와 AI 호출 코드를 섞지 않는 구조를 유지하세요.

## 6) 빠른 품질 점검 명령 (최소)

```bash
python -m compileall scouting
```

- 문법/기본 import 깨짐을 빠르게 탐지할 수 있습니다.
- LLM/외부 API 없이도 수행 가능해 로컬 반복 속도가 빠릅니다.

---

필요하면 다음 단계로, 실제 DB 샘플을 대상으로 월말 기간별 생성 건수(`created`, `skipped` 사유) 대시보드 스크립트를 추가해 병목(예: recent_assignment 비율 과다)을 계량적으로 추적하는 것을 권장합니다.
