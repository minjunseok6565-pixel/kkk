# Analytics 개발 환경 가이드

이 문서는 `analytics/` 패키지의 **현재 코드 구현**을 기준으로, 리더보드/어워드 로직을 빠르게 검증하고 안전하게 수정하기 위한 개발 환경 최적화 가이드입니다.

## 1) 패키지 구조 요약

- `analytics/__init__.py`
  - 최상위에서 `stats` 패키지를 노출합니다.
  - 분석 모듈은 게임의 source of truth(DB/state)를 직접 변경하지 않는 read-only 성격임을 명시합니다.
- `analytics/stats/__init__.py`
  - 주 API로 `compute_leaderboards`, `get_or_build_cached_leaderboards`를 노출합니다.
- `analytics/stats/types.py`
  - `PlayerLine`, `Metric` 데이터 구조와 `LeaderboardConfig`, `LeaderboardsBundle` 타입을 정의합니다.
  - `normalized_player_lines`, `coerce_float`, `safe_div` 같은 방어적 유틸이 핵심 입구입니다.
- `analytics/stats/metrics.py`
  - 지표 레지스트리(`build_metric_registry`)와 지표 계산(`compute_metric_value`)을 담당합니다.
- `analytics/stats/qualifiers.py`
  - phase/시즌 길이 기반 qualifier rule 계산 및 선수 자격 판정(`player_qualifies`)을 담당합니다.
- `analytics/stats/leaders.py`
  - 리더보드 생성 파이프라인의 중심입니다.
  - 정렬, 동률 처리, rank 부여, 출력 번들(meta 포함)을 만듭니다.
- `analytics/stats/cache.py`
  - `state` 모듈이 있을 때 캐시 read/build/write를 수행하고, 없으면 방어적으로 fallback 합니다.
- `analytics/stats/awards.py`
  - 가중 z-score 기반 award 후보 산출 로직(`compute_award_candidates`)을 제공합니다.

## 2) 개발 시 꼭 이해해야 하는 동작 포인트

### 2-1. 입력 정규화가 가장 앞단에서 수행됨

`normalized_player_lines(player_stats)`는 다음을 보장합니다.

- `player_stats`가 Mapping이 아니면 빈 리스트 반환
- `games`, `totals` 값을 안전하게 숫자로 변환
- 누락/비정상 값은 기본값(0)으로 보정

따라서 신규 기능을 추가할 때는 가능한 한 이 정규화 결과(`PlayerLine`)를 기준으로 로직을 작성하면 예외 처리가 단순해집니다.

### 2-2. 리더보드는 deterministic 하도록 설계됨

`leaders.py`의 정렬은 다음 우선순위를 가집니다.

1. metric 값 (`sort_desc` 반영)
2. metric별 tie-break total
3. MIN total
4. `player_id`

그리고 rank는 동일 값일 때 같은 rank를 부여하며, `top_n` + `include_ties=True`면 컷오프 동률까지 포함합니다.

### 2-3. qualifier는 phase와 시즌 길이에 따라 자동 조정됨

`build_qualifier_rules`는 `auto` 프로파일일 때:

- `playoffs`/`play_in`이면 playoffs 기준
- 팀 최대 경기수가 20 이하이면 relaxed
- 그 외 regular

으로 선택됩니다. 이 동작은 시즌 초반/단축 시즌에서 빈 리더보드 발생을 줄이는 데 중요합니다.

### 2-4. cache 모듈은 state 의존성을 느슨하게 유지함

`cache.py`는 `import state` 실패 시에도 예외를 던지지 않고 fallback 번들을 반환합니다. 로컬/오프라인 테스트에서 analytics만 독립 검증하기 좋습니다.

## 3) 빠른 검증 루틴 (로컬 개발용)

아래 스니펫은 리더보드와 어워드 계산이 동작하는지 빠르게 확인하는 최소 루틴입니다.

```bash
python - <<'PY'
from analytics.stats import compute_leaderboards
from analytics.stats.awards import compute_award_candidates

player_stats = {
    "p1": {"player_id": "p1", "name": "A", "team_id": "T1", "games": 10, "totals": {"PTS": 250, "AST": 70, "REB": 60, "STL": 12, "BLK": 8, "TOV": 20, "MIN": 320, "FGM": 90, "FGA": 180, "3PM": 30, "3PA": 80, "FTM": 40, "FTA": 50}},
    "p2": {"player_id": "p2", "name": "B", "team_id": "T2", "games": 10, "totals": {"PTS": 210, "AST": 95, "REB": 40, "STL": 18, "BLK": 2, "TOV": 28, "MIN": 300, "FGM": 70, "FGA": 150, "3PM": 20, "3PA": 60, "FTM": 50, "FTA": 65}},
}
team_stats = {
    "T1": {"games": 10},
    "T2": {"games": 10},
}

lb = compute_leaderboards(player_stats, team_stats, phase="regular")
print("per_game keys:", sorted(lb["per_game"].keys())[:5])
print("PTS leaders:", [(r["player_id"], r["value"]) for r in lb["per_game"].get("PTS", [])])

mvp = compute_award_candidates(player_stats, team_stats, award="MVP", top_n=3)
print("MVP:", [(r["player_id"], r["score"]) for r in mvp])
PY
```

## 4) 변경 작업 체크리스트

리더보드/어워드 로직 변경 시 아래를 최소 체크로 권장합니다.

1. **숫자 안정성**
   - 분모 0 상황을 `safe_div`로 처리했는지
   - 반올림 자릿수(`metric.decimals`) 의도와 일치하는지
2. **결정성(determinism)**
   - 정렬/동률 처리 결과가 입력 순서와 무관한지
   - 최종 tie-break에 `player_id`가 유지되는지
3. **qualifier 영향도**
   - regular/playoffs/relaxed에서 결과가 과도하게 비거나 과도하게 넓어지지 않는지
4. **캐시 호환성**
   - `LeaderboardsBundle`의 최상위 shape(`meta`, `per_game`, `totals`, `per_36`, `advanced`)를 깨지 않았는지
   - `cache.py`가 기대하는 `by_phase` 저장 형태를 유지하는지

## 5) 확장 시 권장 패턴

- 새 지표 추가:
  1. `metrics.py`의 `build_metric_registry()`에 `Metric` 정의 추가
  2. 필요한 계산식을 `compute_metric_value()`에 추가
  3. qualifier 종류(`gp_min`, `fga_min` 등) 선택
- 새 어워드 추가:
  1. `awards.py`의 `AWARD_MODELS`에 모델 추가
  2. feature 키가 레지스트리와 정합되는지 확인
  3. `top_n`, qualifier 적용 여부(`require_qualifier`)를 의도대로 설정

## 6) 운영 관점 참고

- 이 패키지는 예외 대신 기본값/fallback을 많이 사용하므로, 장애 전파보다는 결과 품질 저하 형태로 문제가 드러날 가능성이 큽니다.
- 따라서 개발 환경에서 **샘플 데이터 기반 스모크 실행**을 습관화하면 회귀를 빠르게 잡을 수 있습니다.
