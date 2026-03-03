# docs/ 재검토 리포트 (2026-03-01)

요청사항에 따라 `docs/*.md`를 현재 코드 구조 기준으로 재검토했다.

## 검토 범위

- `docs/` 내 13개 문서
- 실제 코드 기준: 패키지 구조, API 라우터, 파일 존재 여부

## 핵심 결론

1. **가장 큰 불일치 지점은 API 문서 2건**이었다.
   - 기존 문서는 단일 `server.py`를 소스로 가정했으나, 현재는 `app/main.py` + `app/api/routes/*.py` 분리 구조다.
2. `trade_block_and_grievance_design_ko.md`는 **설계 문서 성격**이 강해, 일부 미구현 모듈 경로가 포함되어 있다.
   - 이는 “현재 구현 설명”으로 읽히면 오해를 유발할 수 있다.
3. 나머지 개발환경 가이드 문서는 대체로 현재 패키지 구조와 정합적이다.

## 문서별 점검 결과

- `agency_development_environment.md`: 대체로 정합. (모듈 지칭이 상대 파일명 중심이라 문맥 의존성이 있음)
- `api_endpoint_function_summary.md`: **수정 완료** (router split 반영).
- `api_endpoints_inventory.md`: **수정 완료** (GET/POST/총 개수 및 엔드포인트 목록 갱신).
- `db_schema_dev_environment_guide.md`: 정합. 테이블명/함수명은 코드 객체가 아닌 DB/개념 이름 포함.
- `draft_dev_environment.md`: 정합.
- `practice_development_environment.md`: 정합.
- `server_split_audit.md`: 감사(audit) 성격 문서로 허용 가능한 범위.
- `server_split_audit_followup.md`: 감사(follow-up) 성격 문서로 허용 가능한 범위.
- `server_split_import_fix_plan.md`: 계획(plan) 성격 문서로 허용 가능한 범위.
- `state_modules_dev_environment_guide.md`: 정합.
- `trade_agency_tradeblock_design.md`: 설계 성격으로 정합.
- `trade_block_and_grievance_design_ko.md`: 설계 제안 문서이며 미구현 경로가 있어, 구현 문서로 오해 가능.
- `trades_development_environment.md`: 정합.

## 환각 가능성/오해 포인트

- API 문서에서 `server.py`를 소스라고 단정하던 부분은 현재 구조와 불일치했으며, 실제로 endpoint 누락/추가 불일치가 존재했다.
- 설계 문서에 등장하는 미구현 경로(예: grievance 전용 신규 모듈)는 “현재 구현됨”이 아니라 “제안/설계”로 읽어야 한다.

## 이번 반영 사항

- `docs/api_endpoints_inventory.md` 최신화
- `docs/api_endpoint_function_summary.md` 최신화
- 본 리뷰 문서 추가

