# Trade Market Realism 패치 검증 메모 (branch check, 2026-03-08)

요청하신 변경 의도와 현재 브랜치 코드를 대조해 점검한 결과입니다.

## 1) AI proactive listing이 매일 자동 등록되지 않고 주 단위로 가능해졌는가?

- **부분 충족**.
- `listing_policy.py`에 cadence gate가 도입되어 `WEEKLY`일 때는
  - 앵커 요일이 아니면 skip,
  - 마지막 평가일(`last_eval_at`) 기준 7일 미만이면 skip 하도록 구현되어 있습니다.
- 다만 `DealGeneratorConfig` 기본값은 아직 `ai_proactive_listing_cadence = "DAILY"`입니다.
  - 즉, 런타임에서 WEEKLY를 주입하지 않으면 기존처럼 매 tick 평가가 수행됩니다.

## 2) outgoing 버킷 진입만으로 상장되지 않고, 임계 점수(압력) 통과 시에만 상장되는가?

- **충족**.
- proactive 후보는 허용 버킷 소속만으로 끝나지 않고,
  - `_passes_listing_threshold(...)`를 반드시 통과해야 listing 됩니다.
- `_passes_listing_threshold`는 `surplus_score >= bucket/team 상황별 threshold`를 검사합니다.

## 3) 팀별로 proactive listing 임계점이 다르게 동작하는가?

- **충족**.
- 임계값은 posture별 bucket threshold table(`ai_proactive_listing_bucket_thresholds`)을 사용합니다.
- 추가로 팀 상황 modifier가 적용됩니다.
  - `WIN_NOW` + `SURPLUS_*`일 때 threshold 완화,
  - `REBUILD` + `VETERAN_SALE`일 때 threshold 완화,
  - high urgency일 때 완화,
  - cooldown_active일 때 강화.

## 4) BUY 타깃 탐색이 need-tag 하드 필터에서 벗어나 전체 선수 풀까지 확장되었는가?

- **충족**.
- BUY 탐색은 `catalog.incoming_all_players`(리그 전체 incoming index)를 순회합니다.
- 예전의 need-tag 인덱스 기반 포함/제외 방식이 아니라,
  - 선수별 `need_similarity`를 계산해 점수(우선순위)에 반영합니다.
- 즉 need-tag는 gate가 아니라 ranking bonus/penalty로 작동합니다.

## 5) scan_limit = need_n * 3 같은 조기 절단 상한이 제거되었는가?

- **충족(의도와 부합)**.
- 현재 BUY retrieval에는 `scan_limit = need_n * 3` 로직이 없습니다.
- 대신 retrieval cap 체계가
  - `teams_cap`,
  - `players_cap`,
  - `iteration_cap`,
  - listed/non-listed quota
  로 분리되어 동작합니다.

## 6) 시즌 초 트레이드 압력이 낮을 때 listing 위주, 압력이 높아지면 non-listed까지 확장되는가?

- **대체로 충족**.
- 구조상 listed 후보는 별도 트랙으로 항상 수집되고,
- non-listed는 pressure/urgency 기반으로 증가하는 cap(`non_listed_quota`, teams/players cap bonus)을 통해 확장됩니다.
- 따라서 초기 저압력 구간에서는 listed 중심이 되고, 압력이 올라갈수록 non-listed 비중이 늘어나는 설계입니다.

## 7) 종합 판단

- 설명하신 핵심 방향(“강한 매도 의지가 있는 선수만 block 노출”, “need-tag 미일치 선수도 고려하되 need 일치 시 우대”, “좁은 스캔 상한 제거”)은 코드에 **대체로 반영**되어 있습니다.
- **확인 필요한 1개 포인트**:
  - “listing을 주 단위로 바꿨다”를 기본 동작으로 의도했다면,
    현재 기본 config가 DAILY라서 추가 설정/오버라이드가 필요합니다.
