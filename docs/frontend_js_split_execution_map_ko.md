# NBA.js 단순 분할 실행 설계서 (1:1 이동 매핑)

본 문서는 `static/NBA.js`를 **동작 변경 없이 단순 분할**하기 위한 실행용 매핑이다.

- 기준 원칙
  1. 함수 바디/조건문/문자열/DOM id/API 경로를 **절대 수정하지 않는다**.
  2. 식별자명(함수명/변수명)도 **절대 변경하지 않는다**.
  3. 한 식별자는 정확히 한 파일로만 이동한다(중복 금지).
  4. 이동 후 호출 관계는 `import/export`로만 연결한다.

---

## 0) 소스 기준점
- 원본: `static/NBA.js`
- 상위 레벨 식별자(상수/함수/초기 실행 블록) 전체를 대상으로 매핑한다.

---

## 1) 파일별 1:1 이동 매핑

## `static/js/app/state.js`
- `state` (line 129)

## `static/js/app/dom.js`
- `els` (line 173)

## `static/js/app/router.js`
- `activateScreen` (line 412)

## `static/js/app/events.js`
- 파일 하단 이벤트 바인딩/초기 실행 블록 전체 이관
  - `els.newGameBtn.addEventListener(...)` 부터
  - `if (els.myTeamFilterControls) { ... }` 까지
- 전역 키 이벤트 블록 이관
  - `document.addEventListener("keydown", ...)`
- 단, 아래 2개는 `bootstrap.js`에서 호출/등록
  - `loadSavesStatus();`
  - `window.__debugRenderMyTeam = ...`

## `static/js/app/bootstrap.js`
- 앱 시작 시퀀스 조립(새 함수만 추가):
  - DOM/state/router/features 초기화
  - `bindEvents()` 호출
  - `loadSavesStatus()` 1회 호출
- 디버그 훅 이관
  - `window.__debugRenderMyTeam = function __debugRenderMyTeam() { ... }`

---

## `static/js/core/api.js`
- `fetchJson` (line 360)
- `setLoading` (line 367)
- `showConfirmModal` (line 372)

## `static/js/core/guards.js`
- `escapeHtml` (line 443)
- `safeNum` (line 452)
- `num` (line 1422)
- `clamp` (line 1427)

## `static/js/core/format.js`
- `formatIsoDate` (line 1204)
- `formatHeightIn` (line 1431)
- `formatWeightLb` (line 1438)
- `formatMoney` (line 1440)
- `formatPercent` (line 1444)
- `seasonLabelByYear` (line 1448)
- `getOptionTypeLabel` (line 1456)
- `formatWinPct` (line 1523)
- `dateToIso` (line 1950)
- `parseIsoDate` (line 1957)
- `startOfWeek` (line 1963)
- `addDays` (line 1971)
- `formatSignedDiff` (line 2657)
- `formatSignedDelta` (line 2999)

## `static/js/core/constants/teams.js`
- `TEAM_FULL_NAMES` (line 1)
- `TEAM_LOGO_BASE_PATH` (line 12)
- `TEAM_BRANDING` (line 14)
- `getTeamBranding` (line 47)
- `applyTeamLogo` (line 54)
- `renderTeamLogoMark` (line 74)
- `getScheduleVenueText` (line 83)

## `static/js/core/constants/tactics.js`
- `TACTICS_OFFENSE_SCHEMES` (line 92)
- `TACTICS_DEFENSE_SCHEMES` (line 103)
- `TACTICS_OFFENSE_ROLES` (line 113)
- `TACTICS_DEFENSE_ROLE_BY_SCHEME` (line 119)

---

## `static/js/features/main/homeWidgets.js`
- `resetNextGameCard` (line 1226)
- `renderHomePriorities` (line 1235)
- `renderHomeActivityFeed` (line 1254)
- `renderHomeRiskCalendar` (line 1272)
- `formatLeader` (line 1295)

## `static/js/features/main/mainScreen.js`
- `showTeamSelection` (line 1195)
- `showMainScreen` (line 1197)
- `randomTipoffTime` (line 1209)
- `fetchInGameDate` (line 1220)
- `refreshMainDashboard` (line 1366)
- `loadSavesStatus` (line 2569)
- `renderTeams` (line 2590)
- `createNewGame` (line 2705)
- `continueGame` (line 2727)
- `confirmTeamSelection` (line 1926)

## `static/js/features/schedule/scheduleScreen.js`
- `isCompletedGame` (line 1216)
- `renderEmptyScheduleRow` (line 1300)
- `renderScheduleTables` (line 1304)
- `showScheduleScreen` (line 1344)

## `static/js/features/myteam/myTeamScreen.js`
- `ratioToColor` (line 1462)
- `getConditionState` (line 1468)
- `sharpnessGrade` (line 1478)
- `renderSharpnessBadgeV2` (line 1487)
- `renderConditionCell` (line 1500)
- `renderConditionRing` (line 1514)
- `renderMyTeamOverview` (line 1528)
- `myTeamRowMetric` (line 1557)
- `getMyTeamDisplayRows` (line 1564)
- `syncMyTeamControlState` (line 1585)
- `rerenderMyTeamBoard` (line 1599)
- `renderRosterRows` (line 1604)
- `showMyTeamScreen` (line 1901)

## `static/js/features/myteam/playerDetail.js`
- `getDissatisfactionSummary` (line 1648)
- `renderAttrGrid` (line 1668)
- `buildContractRows` (line 1684)
- `attrCategoryKey` (line 1728)
- `buildAttrIntelligence` (line 1737)
- `renderPlayerDetail` (line 1777)
- `loadPlayerDetail` (line 1890)

## `static/js/features/training/trainingCalendar.js`
- `buildCalendar4Weeks` (line 2162)
- `renderTrainingCalendar` (line 2218)

## `static/js/features/training/trainingDetail.js`
- `trainingTypeLabel` (line 1977)
- `trainingTypeIcon` (line 1989)
- `buildTrainingDerivedMetrics` (line 2002)
- `buildTrainingRiskFlags` (line 2027)
- `buildTrainingRecommendation` (line 2039)
- `renderTrainingSummaryStrip` (line 2082)
- `renderTrainingContextPanel` (line 2120)
- `refreshTrainingTypeButtonsState` (line 2152)
- `displaySchemeName` (line 2271)
- `buildSchemeRows` (line 2275)
- `trainingImpactLevelLabel` (line 2285)
- `trainingRhythmLabel` (line 2293)
- `trainingLoadLabel` (line 2300)
- `trainingImpactTone` (line 2306)
- `renderPreviewText` (line 2312)
- `renderTrainingDetail` (line 2382)

## `static/js/features/training/trainingScreen.js`
- `loadTrainingData` (line 2173)
- `showTrainingScreen` (line 2548)

## `static/js/features/tactics/tacticsInsights.js`
- `tacticsSchemeLabel` (line 2753)
- `tacticDisplayLabel` (line 2758)
- `getDefenseRolesForScheme` (line 2762)
- `buildTacticsDraft` (line 2766)
- `rosterNameByPid` (line 2814)
- `computeTacticsInsights` (line 2819)
- `rowHealthState` (line 2858)

## `static/js/features/tactics/tacticsScreen.js`
- `renderSchemeOptions` (line 2791)
- `buildLineupRowHtml` (line 2866)
- `validateDefenseRoleUnique` (line 2883)
- `bindLineupEvents` (line 2889)
- `renderTacticsRosterList` (line 2921)
- `renderTacticsInsights` (line 2927)
- `renderTacticsScreen` (line 2971)
- `showTacticsScreen` (line 3222)
- `toggleTacticsOptions` (line 3241)

## `static/js/features/standings/standingsScreen.js`
- `renderStandingsRows` (line 2664)
- `showStandingsScreen` (line 2692)

## `static/js/features/college/collegeScreen.js`
- `renderCollegeTeamsKpi` (line 472)
- `switchCollegeTab` (line 532)
- `renderCollegeTeams` (line 547)
- `loadCollegeTeamDetail` (line 588)
- `showCollegeScreen` (line 1154)

## `static/js/features/college/leaders.js`
- `parseSummaryTags` (line 457)
- `teamSeedChip` (line 493)
- `tierChip` (line 498)
- `renderLeaderInsight` (line 508)
- `collegeStat` (line 526)
- `loadCollegeLeaders` (line 624)

## `static/js/features/college/bigboard.js`
- `loadCollegeBigboard` (line 681)
- `renderCollegeBigboardOverview` (line 711)
- `renderCollegeBigboardDetailRows` (line 772)
- `fetchCollegeBigboardByExpert` (line 789)
- `showCollegeBigboardDetailScreen` (line 800)
- `closeCollegeBigboardDetailScreen` (line 822)

## `static/js/features/college/scouting.js`
- `renderCollegeEmpty` (line 433)
- `setCollegeScoutingFeedback` (line 437)
- `loadCollegeScouting` (line 833)
- `getScoutingReadStorageKey` (line 845)
- `getScoutingReadMap` (line 849)
- `markScoutReportsRead` (line 860)
- `getScoutUnreadCount` (line 868)
- `getScoutingPlayerName` (line 880)
- `renderCollegeScoutCards` (line 886)
- `resetScoutPlayerSearchState` (line 932)
- `renderScoutPlayerList` (line 943)
- `searchScoutingPlayers` (line 1005)
- `queueScoutingPlayerSearch` (line 1059)
- `openScoutPlayerModal` (line 1072)
- `closeScoutPlayerModal` (line 1095)
- `openScoutReportsModal` (line 1105)
- `closeScoutReportsModal` (line 1120)
- `renderScoutReportsList` (line 1126)

## `static/js/features/medical/medicalScreen.js`
- `renderMedicalEmpty` (line 2988)
- `riskTierClass` (line 2992)
- `renderMedicalHero` (line 3008)
- `renderMedicalTimeline` (line 3026)
- `renderMedicalActionRecommendations` (line 3041)
- `renderMedicalRiskCalendar` (line 3062)
- `loadMedicalPlayerContext` (line 3082)
- `renderMedicalOverview` (line 3101)
- `showMedicalScreen` (line 3177)

## `static/js/index.js`
- 기존 `NBA.html`의 단일 엔트리 역할 대체.
- `bootstrap.js`의 `initApp()` 1회 호출.

---

## 2) 누락/중복 방지 체크 규칙 (실제 분할 직전/직후)
1. 함수 개수 체크: 원본의 함수 선언 수와 분할 후 export 함수 수가 일치해야 한다(숫자는 분할 직전 스크립트로 재산출).
2. 상수 체크: 원본 상수군(teams/tactics/state/els)이 정확히 1개 파일씩만 존재해야 한다.
3. 문자열 체크: `/api/`, `document.getElementById`, `classList`, `dataset` 관련 문자열이 변경되지 않아야 한다.
4. 이벤트 체크: 이벤트 핸들러 등록 수가 원본과 동일해야 한다.

---

## 3) 실행 순서(분할 작업 시)
1. `core/constants` + `core/guards` + `core/format` + `core/api` 먼저 이동.
2. `app/state` + `app/dom` + `app/router` 이동.
3. `features/*`를 위 매핑 순서대로 이동.
4. 마지막으로 `app/events` + `app/bootstrap` + `index.js` 연결.
5. 최종적으로 `static/NBA.js`는 제거(또는 백업 브랜치 보관)한다.

