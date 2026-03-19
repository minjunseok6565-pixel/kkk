# Trade Test Machine 구현 계획서 (패치 직결형)

## 0) 문서 목적

이 문서는 **현재 게임 상태(인게임 날짜/DB/팀 컨텍스트)를 그대로 사용**하는 개발용 Trade Test Machine을 구현하기 위한 상세 패치 계획서다.

목표는 다음 3가지를 동시에 만족하는 것이다.

1. 메인(홈) 화면에서 테스트 머신 진입 가능.
2. 양 팀(예: HOU, MIA) 자산을 선택해 트레이드 패키지 구성.
3. 팀별 밸류에이션 버튼(2개)으로 **단일 팀 관점 평가**를 각각 실행해 결과를 비교.

---

## 1) 현재 코드베이스 기준 핵심 사실 (설계 근거)

- `POST /api/trade/evaluate`는 이미 존재하며 `deal + team_id + include_breakdown`을 입력받아 해당 팀 관점의 `decision/evaluation`을 반환한다.
- 평가 시점은 `state.get_current_date_as_date()`와 `state.get_db_path()`를 사용하므로 인게임 진행 상태를 그대로 반영한다.
- Deal 파싱/정규화/검증(`parse_deal`, `canonicalize_deal`, `validate_deal`)이 표준 경로로 존재한다.

따라서 MVP는 “양 팀 동시 평가 API”를 새로 만들 필요 없이,

- Team A 버튼 클릭 → `team_id=A`로 `/api/trade/evaluate` 호출
- Team B 버튼 클릭 → `team_id=B`로 `/api/trade/evaluate` 호출

로 구현 가능하다.

---

## 2) 구현 범위

### 2.1 MVP 범위 (이번 구현)

- 백엔드:
  - 팀별 트레이드 자산 조회 API 1개 추가.
  - (선택) 트레이드 랩 부트스트랩 API 1개 추가.
  - 기존 `/api/trade/evaluate` 재사용 (수정 최소화).
- 프론트엔드:
  - 홈 화면 진입 버튼 추가.
  - 트레이드 랩 화면 추가.
  - 팀 선택 + 자산 선택 + 패키지 빌더 + 팀별 밸류 버튼 2개 + 결과 패널.
- 문서/테스트:
  - API contract 문서 추가.
  - 최소 API 테스트 추가.

### 2.2 이번 범위에서 제외

- 자동 카운터오퍼 제안 UX.
- 3팀 이상 멀티팀 트레이드 전용 UX.
- 대규모 밸런싱 통계 대시보드.

---

## 3) 새로 추가할 파일 / 수정할 파일 (정확한 작업 목록)

> 아래 경로는 현재 저장소 구조 기준. 프론트 코드가 별도 저장소라면 동일 명세를 해당 FE 저장소로 이관.

## 3-A) 백엔드 변경

### [신규] `app/schemas/trade_lab.py`

#### 목적
Trade Lab 전용 request/response 스키마 분리.

#### 작성 내용

- `TradeLabAssetsQuery`
  - `team_id: str`
- `TradeLabAssetPlayer`
  - `kind: Literal["player"]`
  - `player_id, name, pos, age, ovr, salary, team_id`
  - `injury: Dict[str, Any] | None` (meta.injury 반영)
- `TradeLabAssetPick`
  - `kind: Literal["pick"]`
  - `pick_id, year, round, original_team, owner_team, protection`
- `TradeLabTeamAssetsResponse`
  - `ok: bool`
  - `team_id: str`
  - `current_date: str`
  - `players: List[TradeLabAssetPlayer]`
  - `first_round_picks: List[TradeLabAssetPick]`

#### 구현 디테일
- Pydantic `BaseModel` 기반.
- 문자열 팀 아이디는 항상 upper normalize.

---

### [수정] `app/api/routes/trades.py`

#### 목적
트레이드 랩용 “팀 자산 조회 API” 추가.

#### 추가 엔드포인트

1) `GET /api/trade/lab/team-assets`

##### 입력
- query: `team_id`

##### 내부 처리 (순서 엄수)
1. `team_id` normalize + 유효성 체크 (`ALL_TEAM_IDS`)
2. `current_date = state.get_current_date_as_date()`
3. `db_path = state.get_db_path()`
4. `LeagueRepo(db_path)` 열기
5. players 조회:
   - 팀 로스터 active player 목록 + 기본 스냅샷(이름/포지션/나이/OVR/샐러리)
6. picks 조회:
   - `repo.get_draft_picks_map()`에서 `owner_team == team_id` AND `round == 1` 필터
7. 응답 조립:
   - `players[]`, `first_round_picks[]`, `current_date`

##### 에러 처리
- 잘못된 팀 ID: `HTTPException(400)` 또는 `TradeError` 변환 응답
- DB 접근 오류: `HTTPException(500)`

#### 구현 주의
- 이 API는 **실행 시점 상태 조회**가 목적이므로 서버 캐시를 최소화.
- 정렬 기준 고정:
  - 선수: OVR desc, age asc, player_id asc
  - 픽: year asc, pick_id asc

---

### [수정] `app/api/router.py`

#### 목적
별도 route 파일을 만드는 경우 include. (아래 3-B 선택에 따라 달라짐)

- 옵션 1(권장): `trades.py`에 endpoint를 직접 추가 → router.py 수정 없음.
- 옵션 2: `app/api/routes/trade_lab.py` 분리 → `include_router(trade_lab.router)` 추가.

---

### [선택 신규] `app/api/routes/trade_lab.py` (옵션)

`trades.py`가 이미 큰 파일이므로 분리하고 싶다면 아래로 구성.

- `router = APIRouter()`
- `@router.get("/api/trade/lab/team-assets")`
- (선택) `@router.get("/api/trade/lab/bootstrap")`
  - 응답: `current_date`, `teams`(id, name), `default_user_team`

---

### [수정] `app/schemas/__init__.py`

- `trade_lab.py` 내 스키마 export 반영.

---

### [신규 테스트] `tests/test_trade_lab_team_assets_api.py`

#### 목적
팀 자산 조회 API 안정성 확보.

#### 테스트 케이스
1. 정상 케이스:
   - team_id=HOU
   - `ok=True`, `players` 존재, `first_round_picks`는 모두 `round == 1`
2. 팀 ID 오류:
   - 잘못된 team_id → 400
3. 현재 날짜 반영:
   - state current_date 세팅 후 응답 `current_date` 일치
4. 정렬 검증:
   - players 정렬 기준 일치 확인

#### 기술 메모
- 기존 테스트 패턴(`state.transaction`, monkeypatch) 재사용.

---

## 3-B) 프론트엔드 변경 (동일 저장소에 FE가 없을 때의 계약 명세)

> FE 경로는 프로젝트별로 다르므로, 여기서는 **컴포넌트 단위 계약**을 명시한다.

### [수정] Home/Main 화면 컴포넌트

#### 작업
- “Trade Test Machine” 버튼 추가
- 클릭 시 `/trade-lab` 라우팅

#### 버튼 표시 조건
- 개발 빌드 또는 디버그 플래그 활성 시 노출 권장

---

### [신규] Trade Lab 페이지

#### UI 섹션 구성
1. 팀 선택 패널
   - Left Team selector
   - Right Team selector
   - 동일 팀 선택 방지
2. 팀 자산 패널(좌/우)
   - 선수 목록
   - 1라운드 픽 목록
   - 자산 행에 “패키지에 추가” 액션
3. 패키지 패널
   - 좌팀 outgoing 리스트
   - 우팀 outgoing 리스트
   - 자산 제거 버튼
4. 밸류에이션 액션 영역
   - 버튼 2개:
     - `좌팀 관점 밸류에이션`
     - `우팀 관점 밸류에이션`
   - 활성화 조건:
     - 좌팀/우팀 각각 outgoing 자산 1개 이상일 때만 활성화
5. 결과 패널
   - decision.verdict / confidence
   - incoming_total, outgoing_total, net_surplus, surplus_ratio
   - breakdown step 리스트 (stage/code/label/delta/factor)

---

### [신규] FE 상태 모델 (권장 타입)

- `selectedTeams: { left: TeamId | null; right: TeamId | null }`
- `assetsByTeam: Record<TeamId, { players: PlayerAsset[]; first_round_picks: PickAsset[] }>`
- `packageByTeam: Record<TeamId, TradeAsset[]>`
- `evalResultByTeam: Record<TeamId, TeamEvalResult | null>`
- `dealHash: string` (패키지 변경 감지용)

---

### [신규] FE API client 함수

1. `fetchTradeLabTeamAssets(teamId)`
   - `GET /api/trade/lab/team-assets?team_id=...`
2. `evaluateDealForTeam(deal, teamId)`
   - `POST /api/trade/evaluate`
   - body: `{ deal, team_id: teamId, include_breakdown: true }`

---

### [신규] Deal payload 빌더 유틸

#### 함수 시그니처
`buildDealPayload(leftTeamId, rightTeamId, packageByTeam): DealPayload`

#### 생성 규칙
- `teams = [leftTeamId, rightTeamId]`
- `legs[leftTeamId] = packageByTeam[leftTeamId]`
- `legs[rightTeamId] = packageByTeam[rightTeamId]`
- 각 asset은 기존 모델(`player`, `pick`) 규격 준수

---

### [신규] UX 안정장치

1. 패키지 변경 시 기존 평가결과 stale 처리.
2. 평가 결과 카드에 `evaluated_at`, `game_current_date` 표시.
3. 좌/우 결과 동시 비교 레이아웃 제공.
4. 요청 중 버튼 disable + spinner.

---

## 4) API Contract (FE가 그대로 붙일 수 있는 형태)

### 4.1 팀 자산 조회

`GET /api/trade/lab/team-assets?team_id=HOU`

```json
{
  "ok": true,
  "team_id": "HOU",
  "current_date": "2025-12-11",
  "players": [
    {
      "kind": "player",
      "player_id": "P123",
      "name": "Sample Player",
      "pos": "SG",
      "age": 25,
      "ovr": 81,
      "salary": 18500000,
      "team_id": "HOU",
      "injury": {
        "current": {"status": "HEALTHY"}
      }
    }
  ],
  "first_round_picks": [
    {
      "kind": "pick",
      "pick_id": "HOU_2027_R1",
      "year": 2027,
      "round": 1,
      "original_team": "HOU",
      "owner_team": "HOU",
      "protection": null
    }
  ]
}
```

### 4.2 팀 관점 평가 (기존 API)

`POST /api/trade/evaluate`

```json
{
  "deal": {
    "teams": ["HOU", "MIA"],
    "legs": {
      "HOU": [{"kind": "player", "player_id": "P100"}],
      "MIA": [{"kind": "pick", "pick_id": "MIA_2028_R1"}]
    },
    "meta": {"mode": "trade_lab"}
  },
  "team_id": "HOU",
  "include_breakdown": true
}
```

---

## 5) 구현 순서 (작업자 체크리스트)

### Step 1. 스키마 추가
- [ ] `app/schemas/trade_lab.py` 작성
- [ ] `app/schemas/__init__.py` export 반영

### Step 2. API 추가
- [ ] `/api/trade/lab/team-assets` 구현
- [ ] team normalize / validation / sorting / error-path 구현

### Step 3. 테스트
- [ ] `tests/test_trade_lab_team_assets_api.py` 작성
- [ ] 정상/오류/정렬/날짜 반영 케이스 통과

### Step 4. FE 연결
- [ ] 홈 화면 버튼 추가
- [ ] Trade Lab 화면 + 상태모델 + API client
- [ ] 좌/우 밸류 버튼 각각 `/api/trade/evaluate` 연결

### Step 5. QA
- [ ] 같은 패키지에서 좌/우 평가가 각각 다른 결과로 정상 노출되는지
- [ ] 인게임 날짜 이동 후 평가 결과가 즉시 바뀌는지
- [ ] invalid asset / 팀 변경 / stale 처리 정상 동작

---

## 6) 핵심 구현 디테일 (패치 옮기기 쉽게)

### 6.1 백엔드: team-assets 핸들러 의사코드

```python
@router.get("/api/trade/lab/team-assets")
async def api_trade_lab_team_assets(team_id: str):
    tid = str(team_id or "").upper().strip()
    if tid not in ALL_TEAM_IDS:
        raise HTTPException(status_code=400, detail=f"Invalid team_id: {tid}")

    current_date = state.get_current_date_as_date()
    db_path = state.get_db_path()

    with LeagueRepo(db_path) as repo:
        # 1) roster players
        roster = repo.get_team_roster(tid)  # 프로젝트 SSOT 함수명에 맞게 교체
        players = []
        for row in roster:
            players.append({
                "kind": "player",
                "player_id": ...,
                "name": ...,
                "pos": ...,
                "age": ...,
                "ovr": ...,
                "salary": ...,
                "team_id": tid,
                "injury": ...,
            })

        # 2) first-round picks
        picks_map = repo.get_draft_picks_map() or {}
        first_round_picks = []
        for pick_id, p in picks_map.items():
            if str(p.get("owner_team") or "").upper() != tid:
                continue
            if int(p.get("round") or 0) != 1:
                continue
            first_round_picks.append({...})

    players.sort(key=lambda x: (-int(x.get("ovr") or 0), int(x.get("age") or 99), str(x.get("player_id") or "")))
    first_round_picks.sort(key=lambda x: (int(x.get("year") or 9999), str(x.get("pick_id") or "")))

    return {
        "ok": True,
        "team_id": tid,
        "current_date": current_date.isoformat(),
        "players": players,
        "first_round_picks": first_round_picks,
    }
```

### 6.2 프론트: 버튼별 평가 흐름

```ts
async function onEvaluateFor(teamId: TeamId) {
  const deal = buildDealPayload(leftTeamId, rightTeamId, packageByTeam);
  const hash = stableHash(deal);

  const res = await evaluateDealForTeam(deal, teamId);

  if (hash !== currentDealHash()) {
    markResultStale(teamId);
    return;
  }

  setEvalResult(teamId, res);
}
```

---

## 7) 리스크 및 대응

1. **Deal payload 불일치 리스크**
   - 대응: 공용 `buildDealPayload` 유틸 단일화.
2. **결과 stale 리스크**
   - 대응: `dealHash` 비교 후 stale 표시.
3. **API 과호출 리스크**
   - 대응: 버튼 debounce, 요청 중 재클릭 차단.
4. **자산 데이터 누락 리스크**
   - 대응: player/pick 누락 시 빈 배열 + warning toast.

---

## 8) 확장 계획 (MVP 이후)

- `/api/trade/lab/evaluate-both` 추가(서버에서 양 팀 동시 평가 묶음)
- 변수 기여도(need/risk/finance/fit) 시각화 강화
- 평가 결과 저장/비교(스냅샷 A vs B)

---

## 9) 완료 기준 (Definition of Done)

1. 홈 화면에서 Trade Test Machine 진입 가능.
2. 양 팀 선택 후 선수/1R 픽 자산 확인 가능.
3. 패키지 구성 후 좌/우 팀 밸류 버튼 각각 동작.
4. 각 버튼은 해당 팀 관점 verdict + breakdown을 정상 반환.
5. 인게임 날짜 변경 시 평가가 현재 상태를 반영.
6. 기본 API 테스트 통과.

