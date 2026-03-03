# My Team / Player Detail 상업 출시형 UI 스펙 v1.0

- 작성일: 2026-03-04
- 범위: `내 팀(로스터)` 탭, `선수 상세 정보` 탭
- 목표: 상업용 출시 품질(가독성, 의사결정 속도, 브랜딩 일관성, 확장성)

---

## 1) 제품 목표 및 설계 원칙

### 1.1 제품 목표
1. **의사결정 속도 극대화**: 로스터 탭에서 15인 기준 위험/활용 선수 식별을 5초 내 완료.
2. **가독성/접근성 상향**: 텍스트 대비 WCAG AA 이상, 색상 의존도 최소화.
3. **브랜드 일관성**: 기존 프론트의 다크-프리미엄 톤과 NBA 시뮬레이션 감성 유지.
4. **라이브옵스 대응성**: 임계값, 라벨, 색상 정책을 토큰화해 시즌 밸런스 변경에 즉시 대응.

### 1.2 설계 원칙
- **정보 계층 우선**: 중요도(경기 출전/리스크 > 계약/불만 > 세부 능력치) 순서 고정.
- **색 + 텍스트 이중 인코딩**: 위험 상태는 항상 색상 + 라벨 동시 표기.
- **비교 친화 UI**: 동일 유형 데이터는 동일 레이아웃/단위/정렬 사용.
- **정책 분리**: 데이터 노출 정책(비공개 속성 필터)을 렌더링 로직과 분리.

---

## 2) IA(Information Architecture) 및 화면 구조

## 2.1 내 팀(로스터) 화면 구조
- A. 팀 요약 KPI 바(시즌/순위/재정/팀 컨디션)
- B. 컨트롤 바(정렬, 필터)
- C. 로스터 테이블
  - 선수 식별 영역(이름, 포지션, OVR)
  - 박스스코어 핵심 영역(PTS/AST/REB/3PM)
  - **컨디션 영역(ST/LT + 상태 칩)**
  - **경기력 영역(Sharpness 스코어 + 등급)**

## 2.2 선수 상세 화면 구조
- A. Hero 카드(선수 신상, OVR, 경기력, 출전 상태)
- B. 계약 카드
- C. 만족도 리스크 카드
- D. 능력치 인텔리전스 카드
  - 카테고리 평균
  - Top Strengths
  - Needs Attention(정책 필터 적용)
- E. 건강 상태 카드
- F. 시즌 퍼포먼스 카드

---

## 3) 컴포넌트 스펙 (상태도 포함)

## 3.1 RosterConditionCell (로스터 > 컨디션)

### 3.1.1 목적
- 단기 체력(ST), 장기 체력(LT), 종합 위험도를 한 셀에서 빠르게 파악.

### 3.1.2 구성
- `ST bar` + `%`
- `LT bar` + `%`
- `RiskChip` (`GOOD` / `WATCH` / `RISK`)

### 3.1.3 상태도
```text
[init]
  └─ 데이터 유효성 검사
      ├─ 실패/누락 -> [unknown]
      └─ 성공 -> 임계값 평가
                 ├─ RISK 조건 충족 -> [risk]
                 ├─ WATCH 조건 충족 -> [watch]
                 └─ 그 외 -> [good]
```

### 3.1.4 상태별 시각 규칙
- `good`: 녹색 계열 + 라벨 GOOD
- `watch`: 황색/주황 계열 + 라벨 WATCH
- `risk`: 적색 계열 + 라벨 RISK + 행 강조선(좌측 2px)
- `unknown`: 회색 계열 + 라벨 N/A

---

## 3.2 SharpnessBadgeV2 (로스터/상세 > 경기력)

### 3.2.1 목적
- 경기력 숫자만이 아니라 등급과 상태를 함께 제공.

### 3.2.2 구성
- 큰 숫자(0~100)
- 등급 문자(`S`, `A`, `B`, `C`, `D`)
- 컨텍스트 라벨(`Hot`, `Stable`, `Cold`)

### 3.2.3 상태도
```text
[score input]
  └─ clamp(0~100)
      ├─ 85+ -> [hot]
      ├─ 70~84 -> [stable-high]
      ├─ 55~69 -> [stable-low]
      └─ <55 -> [cold]
```

### 3.2.4 등급 매핑
- 95~100: `S`
- 85~94: `A`
- 70~84: `B`
- 55~69: `C`
- 0~54: `D`

---

## 3.3 PlayerHeroCard (선수 상세 Hero)

### 3.3.1 목적
- 단 한 카드에서 출전 판단에 필요한 핵심 정보 제공.

### 3.3.2 구성
- 좌측: 이름, 포지션, 나이, 신체 정보
- 우측: OVR 메달, SharpnessBadgeV2, AvailabilityChip
- 하단 요약: "출전 가능/관리 필요 + 컨디션 요약 + 불만 신호"

### 3.3.3 상태도
```text
[load detail]
  ├─ injury OUT/RETURNING -> [availability:limited]
  ├─ injury HEALTHY + sharp>=70 -> [availability:ready]
  └─ 그 외 -> [availability:monitor]
```

---

## 3.4 AttributeIntelPanel (선수 상세 > 능력치 인텔리전스)

### 3.4.1 목적
- 수십 개 능력치 데이터를 "카테고리 + 강점 + 약점"으로 압축.

### 3.4.2 구성
- `CategoryMeters`: Shooting / Playmaking / Defense / Physical / Mental
- `TopStrengths`: 상위 5개
- `NeedsAttention`: 하위 3개 (정책 필터 적용 후)

### 3.4.3 Needs Attention 정책 상태도
```text
[raw attrs]
  └─ 노출 허용 필터 적용
      ├─ 비허용 키 제거 (Potential, I_InjuryFreq, M_*)
      └─ 남은 키 정렬(오름차순)
           ├─ 3개 이상 -> 하위 3개 표시
           └─ 3개 미만 -> N개만 표시 + "데이터 부족" 안내
```

---

## 3.5 HealthStatusCard (선수 상세 > 건강 상태)

### 3.5.1 목적
- 건강 관련 의사결정(휴식/기용/분 단위 조정) 근거 제공.

### 3.5.2 필수 항목
- 장기 체력, 단기 체력
- 부상 여부(정상/부상/복귀)
- 부상 상세(유형/부위/복귀 예상 경기 수)

### 3.5.3 상태 라벨
- `HEALTHY` / `MANAGED` / `OUT` / `RETURNING`

---

## 4) 디자인 토큰 스펙 (색상/타이포/간격)

## 4.1 Color Tokens

| Token | Value | 용도 |
|---|---:|---|
| `--surface-0` | `#0B1220` | 앱 배경 |
| `--surface-1` | `#111A2B` | 카드 기본 배경 |
| `--surface-2` | `#172338` | 보조 카드/칩 배경 |
| `--surface-3` | `#1F2E46` | hover/selected |
| `--text-primary` | `#E8EEF8` | 본문/헤더 |
| `--text-secondary` | `#A8B6CC` | 보조 텍스트 |
| `--text-muted` | `#7E8CA4` | 캡션/메타 |
| `--accent-blue` | `#4DA3FF` | 인포/링크/미터 |
| `--accent-gold` | `#F4B53F` | OVR 메달/핵심 KPI |
| `--state-good` | `#22C55E` | 정상/양호 |
| `--state-watch` | `#F59E0B` | 주의 |
| `--state-risk` | `#EF4444` | 위험 |
| `--state-info` | `#38BDF8` | 안내 |

### 4.1.1 접근성 규칙
- 본문 텍스트 대비: **최소 4.5:1**
- 대형 텍스트(18px+/bold): **최소 3:1**
- 상태 표현은 색상 외 라벨 동시 노출 필수

## 4.2 Typography Tokens

| Token | Size/Weight | Line Height | 용도 |
|---|---|---|---|
| `--font-h1` | 32 / 800 | 1.2 | 화면 메인 타이틀 |
| `--font-h2` | 24 / 750 | 1.25 | 섹션 타이틀 |
| `--font-h3` | 20 / 700 | 1.3 | 카드 타이틀 |
| `--font-body` | 14 / 500 | 1.5 | 본문 |
| `--font-caption` | 12 / 600 | 1.4 | 라벨/메타 |
| `--font-kpi` | 28 / 800 | 1.1 | 핵심 수치 |

## 4.3 Spacing & Radius Tokens

| Token | Value |
|---|---:|
| `--space-1` | 4px |
| `--space-2` | 8px |
| `--space-3` | 12px |
| `--space-4` | 16px |
| `--space-5` | 20px |
| `--space-6` | 24px |
| `--radius-sm` | 8px |
| `--radius-md` | 12px |
| `--radius-lg` | 16px |
| `--radius-xl` | 20px |

---

## 5) 수치 임계값 표 (의사결정 정책)

## 5.1 컨디션/경기력 임계값

| 구분 | GOOD | WATCH | RISK |
|---|---|---|---|
| Sharpness | `>= 70` | `55 ~ 69` | `< 55` |
| ST(단기 체력) | `>= 0.75` | `0.60 ~ 0.74` | `< 0.60` |
| LT(장기 체력) | `>= 0.80` | `0.65 ~ 0.79` | `< 0.65` |

### 5.1.1 종합 판정 규칙
- RISK 조건 하나라도 충족 시 `RISK`
- RISK 없고 WATCH 하나 이상이면 `WATCH`
- 그 외 `GOOD`

## 5.2 경기력 등급 임계값

| Score 범위 | 등급 | 라벨 |
|---|---|---|
| 95~100 | S | Elite |
| 85~94 | A | Hot |
| 70~84 | B | Stable |
| 55~69 | C | Volatile |
| 0~54 | D | Cold |

## 5.3 만족도 리스크 요약 임계값

| 지표 | 조건 | 상태 |
|---|---|---|
| `trade_request_level` | `>= 1` | 위험 신호 |
| frustration axis max | `>= 0.50` | 주의 |
| frustration axis max | `< 0.50` & TR 0 | 안정 |

---

## 6) 데이터 노출/비노출 정책 (Needs Attention)

## 6.1 비노출 키(필수)
- `Potential`
- `I_InjuryFreq`
- 접두사 `M_`로 시작하는 모든 멘탈 능력치

## 6.2 노출 허용 키 원칙
- 경기력에 직접 영향이 있는 기술/신체/수비/플레이메이킹 키 우선
- 내부 튜닝용 메타 키는 기본 비노출

## 6.3 정책 의사코드
```text
isHiddenKey(key):
  if key == "Potential": return true
  if key == "I_InjuryFreq": return true
  if key startsWith "M_": return true
  return false

needsAttentionList = attrs
  -> filter(not isHiddenKey)
  -> normalize score
  -> sort asc
  -> take(3)
```

---

## 7) 인터랙션/애니메이션 스펙

## 7.1 공통 원칙
- 애니메이션 시간: `120ms~180ms`
- easing: `cubic-bezier(0.2, 0.8, 0.2, 1)`
- 정보 의미를 바꾸는 애니메이션 금지(장식은 최소)

## 7.2 컴포넌트별
- 테이블 행 hover: 배경 명도 +4%
- 위험 상태 진입: 칩/좌측 인디케이터 페이드 인(120ms)
- 수치 업데이트: 숫자 카운트 업 최대 240ms

---

## 8) 반응형 스펙

## 8.1 브레이크포인트
- `>= 1440`: 데스크톱 확장
- `1024 ~ 1439`: 데스크톱 표준
- `768 ~ 1023`: 태블릿
- `< 768`: 모바일

## 8.2 레이아웃 규칙
- 로스터 테이블: 1024 미만에서 핵심 열 우선 표시(선수/OVR/컨디션/경기력)
- 선수 상세: 1023 이하에서 2열 -> 1열 스택
- KPI 카드: 4열 -> 2열 -> 1열 순차 축소

---

## 9) QA 체크리스트 (출시 게이트)

## 9.1 시각/브랜드
- [ ] 다크 톤에서 본문 대비 AA 충족
- [ ] 상태 색상 + 텍스트 라벨 동시 제공
- [ ] 카드 반경/간격/테두리 토큰 일관

## 9.2 기능/정책
- [ ] Needs Attention에서 `Potential`, `I_InjuryFreq`, `M_*` 미노출
- [ ] 임계값 경계값(54/55, 69/70 등) 정상 판정
- [ ] 데이터 누락 시 unknown/fallback 정상 표기

## 9.3 성능
- [ ] 로스터 20행 렌더링 < 16ms/frame 목표
- [ ] 상세 패널 첫 렌더 TTI 체감 지연 없음

---

## 10) 구현 우선순위 로드맵

### Phase 1 (핵심 가치)
1. RosterConditionCell 교체
2. SharpnessBadgeV2 적용
3. Needs Attention 비노출 정책 적용

### Phase 2 (완성도)
1. PlayerHeroCard 가독성 개선
2. 카드 톤/타이포 토큰 통일
3. 반응형 재배치 및 QA

### Phase 3 (상업 고도화)
1. 접근성 자동 검사 파이프라인 도입
2. A/B 테스트(위험 선수 탐지 시간)
3. 라이브옵스용 임계값 원격 설정화

---

## 11) 수용 기준(Definition of Done)

1. 로스터에서 위험 선수 식별 태스크 완료 시간 기존 대비 **30% 이상 단축**.
2. 선수 상세 화면 텍스트 대비 규정 충족(AA).
3. Needs Attention 정책 위반 항목 노출 건수 **0건**.
4. 4개 브레이크포인트에서 레이아웃 깨짐/가독성 이슈 **0건**.

