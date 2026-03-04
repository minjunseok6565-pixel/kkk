# 팀 로고/홈구장 매핑 가이드

## 1) 로고 파일 저장 위치
- 폴더: `static/team_logos/`
- 파일명 규칙: **팀 ID 대문자 + `.png`**
  - 예: `ATL.png`, `BOS.png`, `LAL.png`

> 실제 화면에서 로고는 `/static/team_logos/<파일명>` 경로로 불러옵니다.

## 2) 팀별 홈구장 이름 입력 위치
- 파일: `static/NBA.js`
- 객체: `TEAM_BRANDING`

형식:

```js
ATL: { arenaName: "State Farm Arena", logoFile: "ATL.png" },
BOS: { arenaName: "TD Garden", logoFile: "BOS.png" },
```

- `arenaName`: 팀 홈구장 이름
- `logoFile`: `static/team_logos/` 안에 저장한 로고 파일명

## 3) 새 팀/커스텀 팀 추가할 때
1. `static/team_logos/XXX.png` 파일 추가
2. `static/NBA.js`의 `TEAM_BRANDING`에 아래처럼 추가

```js
XXX: { arenaName: "Your Arena Name", logoFile: "XXX.png" },
```

## 4) 매핑이 실제로 쓰이는 곳
- `getTeamBranding(teamId)`가 팀 ID로 `arenaName`/`logoUrl`을 반환
- `applyTeamLogo(...)`가 로고를 화면에 렌더링
- 메인 화면 "다음 경기" 카드에서 홈/원정 로고와 홈구장명을 표시
