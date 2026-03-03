const TEAM_FULL_NAMES = {
  ATL: "애틀랜타 호크스", BOS: "보스턴 셀틱스", BKN: "브루클린 네츠", CHA: "샬럿 호네츠",
  CHI: "시카고 불스", CLE: "클리블랜드 캐벌리어스", DAL: "댈러스 매버릭스", DEN: "덴버 너기츠",
  DET: "디트로이트 피스톤스", GSW: "골든 스테이트 워리어스", HOU: "휴스턴 로키츠", IND: "인디애나 페이서스",
  LAC: "LA 클리퍼스", LAL: "LA 레이커스", MEM: "멤피스 그리즐리스", MIA: "마이애미 히트",
  MIL: "밀워키 벅스", MIN: "미네소타 팀버울브스", NOP: "뉴올리언스 펠리컨스", NYK: "뉴욕 닉스",
  OKC: "오클라호마시티 썬더", ORL: "올랜도 매직", PHI: "필라델피아 세븐티식서스", PHX: "피닉스 선즈",
  POR: "포틀랜드 트레일블레이저스", SAC: "새크라멘토 킹스", SAS: "샌안토니오 스퍼스", TOR: "토론토 랩터스",
  UTA: "유타 재즈", WAS: "워싱턴 위저즈"
};

const TACTICS_OFFENSE_SCHEMES = [
  { key: "Spread_HeavyPnR", label: "heavy_pnr" },
  { key: "Drive_Kick", label: "drive_kick" },
  { key: "FiveOut", label: "five_out" },
  { key: "Motion_SplitCut", label: "motion_split" },
  { key: "DHO_Chicago", label: "dho_chicago" },
  { key: "Post_InsideOut", label: "post_inside_out" },
  { key: "Horns_Elbow", label: "horns_elbow" },
  { key: "Transition_Early", label: "transition_early" }
];

const TACTICS_DEFENSE_SCHEMES = [
  { key: "Drop", label: "drop" },
  { key: "Switch_Everything", label: "switch_everything" },
  { key: "Switch_1_4", label: "switch_1_4" },
  { key: "Hedge_ShowRecover", label: "hedge_show_recover" },
  { key: "Blitz_TrapPnR", label: "blitz_trap" },
  { key: "AtTheLevel", label: "at_the_level" },
  { key: "Zone", label: "zone" }
];

const TACTICS_OFFENSE_ROLES = [
  "Engine_Primary", "Engine_Secondary", "Transition_Engine", "Shot_Creator", "Rim_Pressure",
  "SpotUp_Spacer", "Movement_Shooter", "Cutter_Finisher", "Connector",
  "Roll_Man", "ShortRoll_Hub", "Pop_Threat", "Post_Anchor"
];

const TACTICS_DEFENSE_ROLE_BY_SCHEME = {
  Drop: ["PnR_POA_Defender", "PnR_Cover_Big_Drop", "Lowman_Helper", "Nail_Helper", "Weakside_Rotator"],
  Switch_Everything: ["PnR_POA_Switch", "PnR_Cover_Big_Switch", "Switch_Wing_Strong", "Switch_Wing_Weak", "Backline_Anchor"],
  Switch_1_4: ["PnR_POA_Switch_1_4", "PnR_Cover_Big_Switch_1_4", "Switch_Wing_Strong_1_4", "Switch_Wing_Weak_1_4", "Backline_Anchor"],
  Hedge_ShowRecover: ["PnR_POA_Defender", "PnR_Cover_Big_HedgeRecover", "Lowman_Helper", "Nail_Helper", "Weakside_Rotator"],
  Blitz_TrapPnR: ["PnR_POA_Blitz", "PnR_Cover_Big_Blitz", "Lowman_Helper", "Nail_Helper", "Weakside_Rotator"],
  AtTheLevel: ["PnR_POA_AtTheLevel", "PnR_Cover_Big_AtTheLevel", "Lowman_Helper", "Nail_Helper", "Weakside_Rotator"],
  Zone: ["Zone_Top_Left", "Zone_Top_Right", "Zone_Bottom_Left", "Zone_Bottom_Right", "Zone_Bottom_Center"]
};

const state = {
  lastSaveSlotId: null,
  selectedTeamId: null,
  selectedTeamName: "",
  currentDate: "",
  rosterRows: [],
  selectedPlayerId: null,
  trainingSelectedDates: new Set(),
  trainingCalendarDays: [],
  trainingSessionsByDate: {},
  trainingRoster: [],
  trainingFamiliarity: { offense: [], defense: [] },
  trainingDraftSession: null,
  trainingRiskCalendarByDate: {},
  trainingKpi: null,
  standingsData: null,
  tacticsDraft: null,
  tacticsSnapshot: null,
  medicalOverview: null,
  medicalSelectedPlayerId: null,
};

const els = {
  startScreen: document.getElementById("start-screen"),
  teamScreen: document.getElementById("team-screen"),
  mainScreen: document.getElementById("main-screen"),
  scheduleScreen: document.getElementById("schedule-screen"),
  myTeamScreen: document.getElementById("my-team-screen"),
  playerDetailScreen: document.getElementById("player-detail-screen"),
  newGameBtn: document.getElementById("new-game-btn"),
  continueBtn: document.getElementById("continue-btn"),
  continueHint: document.getElementById("continue-hint"),
  teamGrid: document.getElementById("team-grid"),
  mainTeamTitle: document.getElementById("main-team-title"),
  mainCurrentDate: document.getElementById("main-current-date"),
  teamAName: document.getElementById("team-a-name"),
  teamBName: document.getElementById("team-b-name"),
  nextGameDatetime: document.getElementById("next-game-datetime"),
  teamALogo: document.getElementById("team-a-logo"),
  teamBLogo: document.getElementById("team-b-logo"),
  nextGameArena: document.getElementById("next-game-arena"),
  nextGameCountdown: document.getElementById("next-game-countdown"),
  nextGameSubline: document.getElementById("next-game-subline"),
  commandSeasonStage: document.getElementById("command-season-stage"),
  commandTeamSummary: document.getElementById("command-team-summary"),
  kpiRecord: document.getElementById("kpi-record"),
  kpiWinRate: document.getElementById("kpi-win-rate"),
  kpiLast10: document.getElementById("kpi-last10"),
  kpiStreak: document.getElementById("kpi-streak"),
  statusInjury: document.getElementById("status-injury"),
  statusFatigue: document.getElementById("status-fatigue"),
  statusSchedule: document.getElementById("status-schedule"),
  homeKpiOvr: document.getElementById("home-kpi-ovr"),
  homeKpiAge: document.getElementById("home-kpi-age"),
  homeKpiSalary: document.getElementById("home-kpi-salary"),
  homeKpiCap: document.getElementById("home-kpi-cap"),
  homeKpiAlert: document.getElementById("home-kpi-alert"),
  quickFocusPlayer: document.getElementById("quick-focus-player"),
  quickFocusRole: document.getElementById("quick-focus-role"),
  quickFocusCondition: document.getElementById("quick-focus-condition"),
  quickFocusHealth: document.getElementById("quick-focus-health"),
  quickFocusContract: document.getElementById("quick-focus-contract"),
  quickFocusStrengths: document.getElementById("quick-focus-strengths"),
  quickFocusRisks: document.getElementById("quick-focus-risks"),
  compareWinrateBar: document.getElementById("compare-winrate-bar"),
  compareWinrateText: document.getElementById("compare-winrate-text"),
  compareLast10Bar: document.getElementById("compare-last10-bar"),
  compareLast10Text: document.getElementById("compare-last10-text"),
  conditionList: document.getElementById("condition-list"),
  strategyList: document.getElementById("strategy-list"),
  priorityList: document.getElementById("priority-list"),
  quickIntelList: document.getElementById("quick-intel-list"),
  timelineList: document.getElementById("timeline-list"),
  activityFeed: document.getElementById("activity-feed"),
  myTeamTitle: document.getElementById("my-team-title"),
  myTeamBtn: document.getElementById("my-team-btn"),
  tacticsMenuBtn: document.getElementById("tactics-menu-btn"),
  nextGameTacticsBtn: document.getElementById("next-game-tactics-btn"),
  scheduleBtn: document.getElementById("schedule-btn"),
  scheduleBackBtn: document.getElementById("schedule-back-btn"),
  scheduleTitle: document.getElementById("schedule-title"),
  scheduleCompletedBody: document.getElementById("schedule-completed-body"),
  scheduleUpcomingBody: document.getElementById("schedule-upcoming-body"),
  trainingMenuBtn: document.getElementById("training-menu-btn"),
  tacticsScreen: document.getElementById("tactics-screen"),
  tacticsBackBtn: document.getElementById("tactics-back-btn"),
  tacticsOffenseBtn: document.getElementById("tactics-offense-btn"),
  tacticsDefenseBtn: document.getElementById("tactics-defense-btn"),
  tacticsOffenseOptions: document.getElementById("tactics-offense-options"),
  tacticsDefenseOptions: document.getElementById("tactics-defense-options"),
  tacticsOffenseCurrent: document.getElementById("tactics-offense-current"),
  tacticsDefenseCurrent: document.getElementById("tactics-defense-current"),
  tacticsStarters: document.getElementById("tactics-starters"),
  tacticsRotation: document.getElementById("tactics-rotation"),
  tacticsRosterList: document.getElementById("tactics-roster-list"),
  tacticsTeamContext: document.getElementById("tactics-team-context"),
  tacticsDirtyBadge: document.getElementById("tactics-dirty-badge"),
  tacticsUndoBtn: document.getElementById("tactics-undo-btn"),
  tacticsKpiTotalMinutes: document.getElementById("tactics-kpi-total-minutes"),
  tacticsKpiTotalCaption: document.getElementById("tactics-kpi-total-caption"),
  tacticsKpiStarterMinutes: document.getElementById("tactics-kpi-starter-minutes"),
  tacticsKpiBenchMinutes: document.getElementById("tactics-kpi-bench-minutes"),
  tacticsKpiRoleDiversity: document.getElementById("tactics-kpi-role-diversity"),
  tacticsStarterShare: document.getElementById("tactics-starter-share"),
  tacticsBenchShare: document.getElementById("tactics-bench-share"),
  tacticsStarterShareBar: document.getElementById("tactics-starter-share-bar"),
  tacticsBenchShareBar: document.getElementById("tactics-bench-share-bar"),
  tacticsAlertFeed: document.getElementById("tactics-alert-feed"),
  standingsMenuBtn: document.getElementById("standings-menu-btn"),
  trainingScreen: document.getElementById("training-screen"),
  standingsScreen: document.getElementById("standings-screen"),
  collegeScreen: document.getElementById("college-screen"),
  medicalScreen: document.getElementById("medical-screen"),
  trainingBackBtn: document.getElementById("training-back-btn"),
  standingsBackBtn: document.getElementById("standings-back-btn"),
  collegeMenuBtn: document.getElementById("college-menu-btn"),
  medicalMenuBtn: document.getElementById("medical-menu-btn"),
  medicalBackBtn: document.getElementById("medical-back-btn"),
  collegeBackBtn: document.getElementById("college-back-btn"),
  collegeMetaLine: document.getElementById("college-meta-line"),
  collegeTabTeams: document.getElementById("college-tab-teams"),
  collegeTabLeaders: document.getElementById("college-tab-leaders"),
  collegeTabBigboard: document.getElementById("college-tab-bigboard"),
  collegeTabScouting: document.getElementById("college-tab-scouting"),
  collegePanelTeams: document.getElementById("college-panel-teams"),
  collegePanelLeaders: document.getElementById("college-panel-leaders"),
  collegePanelBigboard: document.getElementById("college-panel-bigboard"),
  collegePanelScouting: document.getElementById("college-panel-scouting"),
  collegeTeamsBody: document.getElementById("college-teams-body"),
  collegeRosterTitle: document.getElementById("college-roster-title"),
  collegeRosterBody: document.getElementById("college-roster-body"),
  collegeLeaderSort: document.getElementById("college-leader-sort"),
  collegeLeadersBody: document.getElementById("college-leaders-body"),
  collegeExpertSelect: document.getElementById("college-expert-select"),
  collegeBigboardBody: document.getElementById("college-bigboard-body"),
  collegeScoutSelect: document.getElementById("college-scout-select"),
  collegeScoutPlayerSelect: document.getElementById("college-scout-player-select"),
  collegeAssignBtn: document.getElementById("college-assign-btn"),
  collegeUnassignBtn: document.getElementById("college-unassign-btn"),
  collegeReportsBody: document.getElementById("college-reports-body"),
  collegeKpiSeason: document.getElementById("college-kpi-season"),
  collegeKpiTeams: document.getElementById("college-kpi-teams"),
  collegeKpiDraft: document.getElementById("college-kpi-draft"),
  collegeLeadersPreseasonNote: document.getElementById("college-leaders-preseason-note"),
  collegeProspectName: document.getElementById("college-prospect-name"),
  collegeProspectRank: document.getElementById("college-prospect-rank"),
  collegeProspectPos: document.getElementById("college-prospect-pos"),
  collegeProspectTier: document.getElementById("college-prospect-tier"),
  collegeProspectStrengths: document.getElementById("college-prospect-strengths"),
  collegeProspectConcerns: document.getElementById("college-prospect-concerns"),
  collegeProspectSummary: document.getElementById("college-prospect-summary"),
  collegeScoutHint: document.getElementById("college-scout-hint"),
  teamTrainingTabBtn: document.getElementById("team-training-tab-btn"),
  playerTrainingTabBtn: document.getElementById("player-training-tab-btn"),
  trainingCalendarGrid: document.getElementById("training-calendar-grid"),
  trainingTypeButtons: document.getElementById("training-type-buttons"),
  trainingDetailPanel: document.getElementById("training-detail-panel"),
  trainingContextRange: document.getElementById("training-context-range"),
  trainingSelectionSummary: document.getElementById("training-selection-summary"),
  trainingKpiSharpnessCard: document.getElementById("training-kpi-sharpness-card"),
  trainingKpiLowCard: document.getElementById("training-kpi-low-card"),
  trainingKpiLoadCard: document.getElementById("training-kpi-load-card"),
  trainingKpiRiskCard: document.getElementById("training-kpi-risk-card"),
  trainingKpiSharpness: document.getElementById("training-kpi-sharpness"),
  trainingKpiSharpnessMeta: document.getElementById("training-kpi-sharpness-meta"),
  trainingKpiLow: document.getElementById("training-kpi-low"),
  trainingKpiLowMeta: document.getElementById("training-kpi-low-meta"),
  trainingKpiLoad: document.getElementById("training-kpi-load"),
  trainingKpiLoadMeta: document.getElementById("training-kpi-load-meta"),
  trainingKpiRisk: document.getElementById("training-kpi-risk"),
  trainingKpiRiskMeta: document.getElementById("training-kpi-risk-meta"),
  standingsEastBody: document.getElementById("standings-east-body"),
  standingsWestBody: document.getElementById("standings-west-body"),
  backToMainBtn: document.getElementById("back-to-main-btn"),
  backToRosterBtn: document.getElementById("back-to-roster-btn"),
  rosterBody: document.getElementById("my-team-roster-body"),
  playerDetailTitle: document.getElementById("player-detail-title"),
  playerDetailPanel: document.getElementById("player-detail-panel"),
  playerDetailContent: document.getElementById("player-detail-content"),
  medicalTitle: document.getElementById("medical-title"),
  medicalAsOf: document.getElementById("medical-as-of"),
  medicalRosterCount: document.getElementById("medical-roster-count"),
  medicalOutCount: document.getElementById("medical-out-count"),
  medicalReturningCount: document.getElementById("medical-returning-count"),
  medicalHighRiskCount: document.getElementById("medical-high-risk-count"),
  medicalHealthFrustrationCount: document.getElementById("medical-health-frustration-count"),
  medicalRiskBody: document.getElementById("medical-risk-body"),
  medicalInjuredBody: document.getElementById("medical-injured-body"),
  medicalHealthBody: document.getElementById("medical-health-body"),
  medicalTimelineTitle: document.getElementById("medical-timeline-title"),
  medicalTimelineList: document.getElementById("medical-timeline-list"),
  medicalAlertBar: document.getElementById("medical-alert-bar"),
  medicalAlertText: document.getElementById("medical-alert-text"),
  medicalAlertMeta: document.getElementById("medical-alert-meta"),
  medicalAlertLevel: document.getElementById("medical-alert-level"),
  medicalAlertOpenPlayer: document.getElementById("medical-alert-open-player"),
  medicalAlertOpenAction: document.getElementById("medical-alert-open-action"),
  medicalRosterDelta: document.getElementById("medical-roster-delta"),
  medicalOutDelta: document.getElementById("medical-out-delta"),
  medicalHighRiskDelta: document.getElementById("medical-high-risk-delta"),
  medicalHealthDelta: document.getElementById("medical-health-delta"),
  medicalRiskCalendarList: document.getElementById("medical-risk-calendar-list"),
  medicalActionList: document.getElementById("medical-action-list"),
  loadingOverlay: document.getElementById("loading-overlay"),
  loadingText: document.getElementById("loading-text")
};

async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `요청 실패: ${url}`);
  return data;
}

function setLoading(show, msg = "") {
  els.loadingOverlay.classList.toggle("hidden", !show);
  if (msg) els.loadingText.textContent = msg;
}

function activateScreen(target) {
  [
    els.startScreen,
    els.teamScreen,
    els.mainScreen,
    els.scheduleScreen,
    els.myTeamScreen,
    els.playerDetailScreen,
    els.tacticsScreen,
    els.trainingScreen,
    els.standingsScreen,
    els.collegeScreen,
    els.medicalScreen,
  ].forEach((screen) => {
    const active = screen === target;
    screen.classList.toggle("active", active);
    screen.setAttribute("aria-hidden", active ? "false" : "true");
  });
}

function renderCollegeEmpty(tbody, colspan, msg) {
  tbody.innerHTML = `<tr><td class="schedule-empty" colspan="${colspan}">${msg}</td></tr>`;
}

function collegeStat(player, key) {
  const stats = player?.stats || {};
  const n = Number(stats?.[key]);
  return Number.isFinite(n) ? n : 0;
}


function collegeInitials(name) {
  const tokens = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!tokens.length) return "CL";
  return tokens.slice(0, 2).map((t) => t[0].toUpperCase()).join("");
}

function normalize(value, min, max) {
  if (!Number.isFinite(value) || !Number.isFinite(min) || !Number.isFinite(max) || max <= min) return 0;
  return ((value - min) / (max - min)) * 100;
}

function classYearLabel(v) {
  const n = Number(v);
  if (n === 1) return "FR";
  if (n === 2) return "SO";
  if (n === 3) return "JR";
  if (n === 4) return "SR";
  return "-";
}

function tierClassName(tier) {
  const v = String(tier || "").toLowerCase();
  if (v.includes("tier 1")) return "tier-top";
  if (v.includes("lottery")) return "tier-lottery";
  return "tier-other";
}

function reportStatusClass(status) {
  const v = String(status || "").toLowerCase();
  if (v.includes("done") || v.includes("complete") || v.includes("완료")) return "status-completed";
  if (v.includes("progress") || v.includes("assigned") || v.includes("진행")) return "status-progress";
  return "status-default";
}

function parseBigboardSummary(summary) {
  const text = String(summary || "");
  const strengthMatch = text.match(/Strengths?:\s*([^\.]+)/i);
  const concernMatch = text.match(/Concern[s]?:\s*([^\.]+)/i);
  const strengths = (strengthMatch?.[1] || "").split(",").map((v) => v.trim()).filter(Boolean);
  const concerns = (concernMatch?.[1] || "").split(",").map((v) => v.trim()).filter(Boolean);
  return { strengths, concerns };
}

function renderCollegeProspectFocus(row) {
  if (!row) {
    els.collegeProspectName.textContent = "선수를 선택하세요";
    els.collegeProspectRank.textContent = "RANK -";
    els.collegeProspectPos.textContent = "POS -";
    els.collegeProspectTier.textContent = "TIER -";
    els.collegeProspectStrengths.innerHTML = '<span class="college-tag">선수 선택 시 표시</span>';
    els.collegeProspectConcerns.innerHTML = '<span class="college-tag college-tag-danger">선수 선택 시 표시</span>';
    els.collegeProspectSummary.textContent = "요약 정보가 여기에 표시됩니다.";
    return;
  }
  const parsed = parseBigboardSummary(row.summary);
  els.collegeProspectName.textContent = row.name || "-";
  els.collegeProspectRank.textContent = `RANK ${row.rank ?? "-"}`;
  els.collegeProspectPos.textContent = `POS ${row.pos || "-"}`;
  els.collegeProspectTier.textContent = `TIER ${row.tier || "-"}`;
  els.collegeProspectStrengths.innerHTML = parsed.strengths.length
    ? parsed.strengths.map((t) => `<span class="college-tag">${t}</span>`).join("")
    : '<span class="college-tag">명시된 강점 없음</span>';
  els.collegeProspectConcerns.innerHTML = parsed.concerns.length
    ? parsed.concerns.map((t) => `<span class="college-tag college-tag-danger">${t}</span>`).join("")
    : '<span class="college-tag college-tag-danger">명시된 우려 없음</span>';
  els.collegeProspectSummary.textContent = row.summary || "요약 정보가 없습니다.";
}

function switchCollegeTab(tab) {
  const mapping = {
    teams: [els.collegeTabTeams, els.collegePanelTeams],
    leaders: [els.collegeTabLeaders, els.collegePanelLeaders],
    bigboard: [els.collegeTabBigboard, els.collegePanelBigboard],
    scouting: [els.collegeTabScouting, els.collegePanelScouting],
  };
  Object.values(mapping).forEach(([btn, panel]) => {
    const active = btn === mapping[tab][0];
    btn.classList.toggle("is-active", active);
    panel.classList.toggle("active", active);
    panel.setAttribute("aria-hidden", active ? "false" : "true");
  });
}

function renderCollegeTeams(teams) {
  if (!teams.length) {
    renderCollegeEmpty(els.collegeTeamsBody, 6, "대학 팀 데이터가 없습니다.");
    return;
  }
  const sorted = [...teams].sort((a, b) => {
    const wa = Number(a?.wins ?? -9999);
    const wb = Number(b?.wins ?? -9999);
    if (wb !== wa) return wb - wa;
    const la = Number(a?.losses ?? 9999);
    const lb = Number(b?.losses ?? 9999);
    if (la !== lb) return la - lb;
    return Number(b?.srs ?? -9999) - Number(a?.srs ?? -9999);
  });
  const srsValues = sorted.map((t) => Number(t?.srs ?? 0)).filter((n) => Number.isFinite(n));
  const minSrs = srsValues.length ? Math.min(...srsValues) : 0;
  const maxSrs = srsValues.length ? Math.max(...srsValues) : 1;

  els.collegeTeamsBody.innerHTML = "";
  sorted.forEach((team, idx) => {
    const tr = document.createElement("tr");
    const isSelected = state.selectedCollegeTeamId && team?.college_team_id === state.selectedCollegeTeamId;
    tr.className = `roster-row ${idx < 4 ? "college-row-top" : ""} ${isSelected ? "college-row-active" : ""}`.trim();
    const teamName = team?.name || team?.college_team_id || "-";
    const srs = Number(team?.srs ?? 0);
    const srsPct = normalize(srs, minSrs, maxSrs);
    tr.innerHTML = `
      <td><span class="college-rank-chip">${idx + 1}<small>—</small></span></td>
      <td class="standings-team-cell"><span class="college-team-cell"><span class="college-team-avatar">${collegeInitials(teamName)}</span>${teamName}</span></td>
      <td>${team?.conference || "-"}</td>
      <td>${team?.wins ?? "-"}</td>
      <td>${team?.losses ?? "-"}</td>
      <td class="college-srs-cell college-num-cell"><span class="college-srs-track"><span style="width:${srsPct.toFixed(1)}%"></span></span>${srs.toFixed(2)}</td>
    `;
    tr.addEventListener("click", () => loadCollegeTeamDetail(team?.college_team_id).catch((e) => alert(e.message)));
    els.collegeTeamsBody.appendChild(tr);
  });
  if (!state.selectedCollegeTeamId && sorted[0]?.college_team_id) {
    state.selectedCollegeTeamId = sorted[0].college_team_id;
  }
}

async function loadCollegeTeamDetail(teamId) {
  if (!teamId) return;
  const payload = await fetchJson(`/api/college/team-detail/${encodeURIComponent(teamId)}`);
  const teamName = payload?.team?.name || teamId;
  const roster = payload?.roster || [];
  state.selectedCollegeTeamId = teamId;
  els.collegeRosterTitle.textContent = `${teamName} 로스터`;
  els.collegeRosterBody.innerHTML = roster.length ? roster.map((p) => `
    <tr>
      <td>${p?.name || "-"}</td>
      <td><span class="college-pos-pill">${p?.pos || "-"}</span></td>
      <td>${p?.class_year || "-"} <small>${classYearLabel(p?.class_year)}</small></td>
      <td class="college-num-cell">${collegeStat(p, "pts").toFixed(1)}</td>
      <td class="college-num-cell">${collegeStat(p, "reb").toFixed(1)}</td>
      <td class="college-num-cell">${collegeStat(p, "ast").toFixed(1)}</td>
    </tr>
  `).join("") : `<tr><td class="schedule-empty" colspan="6">로스터 데이터가 없습니다.</td></tr>`;
  renderCollegeTeams(state.collegeTeams || []);
}

async function loadCollegeLeaders() {
  const sort = state.collegeLeadersSort || "pts";
  const payload = await fetchJson(`/api/college/players?sort=${encodeURIComponent(sort)}&order=desc&limit=100`);
  const players = payload?.players || [];
  const statKeys = ["pts", "reb", "ast", "stl", "blk"];
  const valuesByKey = Object.fromEntries(statKeys.map((k) => [k, players.map((p) => collegeStat(p, k))]));
  const minByKey = Object.fromEntries(statKeys.map((k) => [k, Math.min(...valuesByKey[k], 0)]));
  const maxByKey = Object.fromEntries(statKeys.map((k) => [k, Math.max(...valuesByKey[k], 1)]));
  const isPreseason = players.length > 0 && players.every((p) => statKeys.every((k) => collegeStat(p, k) === 0));
  els.collegeLeadersPreseasonNote.hidden = !isPreseason;

  els.collegeLeadersBody.innerHTML = players.length ? players.map((p, idx) => {
    const medals = idx === 0 ? "🥇" : idx === 1 ? "🥈" : idx === 2 ? "🥉" : "";
    const heat = (k) => normalize(collegeStat(p, k), minByKey[k], maxByKey[k]);
    return `
      <tr>
        <td>${medals ? `${medals} ${idx + 1}` : idx + 1}</td>
        <td>${p?.name || "-"}</td>
        <td title="${p?.college_team_name || p?.college_team_id || "-"}">${p?.college_team_name || p?.college_team_id || "-"}</td>
        <td><span class="college-pos-pill">${p?.pos || "-"}</span></td>
        <td class="college-num-cell" style="background:linear-gradient(90deg, rgba(37,99,235,0.14) ${heat("pts").toFixed(1)}%, transparent 0)">${collegeStat(p, "pts").toFixed(1)}</td>
        <td class="college-num-cell" style="background:linear-gradient(90deg, rgba(14,165,233,0.14) ${heat("reb").toFixed(1)}%, transparent 0)">${collegeStat(p, "reb").toFixed(1)}</td>
        <td class="college-num-cell" style="background:linear-gradient(90deg, rgba(124,58,237,0.14) ${heat("ast").toFixed(1)}%, transparent 0)">${collegeStat(p, "ast").toFixed(1)}</td>
        <td class="college-num-cell" style="background:linear-gradient(90deg, rgba(245,158,11,0.14) ${heat("stl").toFixed(1)}%, transparent 0)">${collegeStat(p, "stl").toFixed(1)}</td>
        <td class="college-num-cell" style="background:linear-gradient(90deg, rgba(239,68,68,0.14) ${heat("blk").toFixed(1)}%, transparent 0)">${collegeStat(p, "blk").toFixed(1)}</td>
      </tr>
    `;
  }).join("") : `<tr><td class="schedule-empty" colspan="9">리더보드 데이터가 없습니다.</td></tr>`;
}

async function loadCollegeBigboard() {
  const expertId = state.selectedCollegeExpertId;
  if (!expertId) {
    renderCollegeEmpty(els.collegeBigboardBody, 5, "전문가를 선택하세요.");
    renderCollegeProspectFocus(null);
    return;
  }
  const payload = await fetchJson(`/api/offseason/draft/bigboard/expert?expert_id=${encodeURIComponent(expertId)}&pool_mode=auto`);
  const board = payload?.board || [];
  els.collegeBigboardBody.innerHTML = board.length ? board.map((r, idx) => {
    const tierCls = tierClassName(r?.tier);
    return `
      <tr class="roster-row" data-bigboard-index="${idx}">
        <td>${r?.rank ?? "-"}</td>
        <td>${r?.name || "-"}</td>
        <td><span class="college-pos-pill">${r?.pos || "-"}</span></td>
        <td><span class="college-tier-pill ${tierCls}">${r?.tier || "-"}</span></td>
        <td title="${r?.summary || "-"}">${r?.summary || "-"}</td>
      </tr>
    `;
  }).join("") : `<tr><td class="schedule-empty" colspan="5">빅보드 데이터가 없습니다.</td></tr>`;

  const rows = [...els.collegeBigboardBody.querySelectorAll("tr[data-bigboard-index]")];
  rows.forEach((tr) => {
    tr.addEventListener("click", () => {
      const idx = Number(tr.getAttribute("data-bigboard-index"));
      renderCollegeProspectFocus(board[idx]);
      rows.forEach((r) => r.classList.remove("college-row-active"));
      tr.classList.add("college-row-active");
    });
  });
  if (rows[0]) {
    rows[0].click();
  } else {
    renderCollegeProspectFocus(null);
  }
}

async function loadCollegeScouting() {
  if (!state.selectedTeamId) return;
  const [scoutsPayload, playersPayload, reportsPayload] = await Promise.all([
    fetchJson(`/api/scouting/scouts/${encodeURIComponent(state.selectedTeamId)}`),
    fetchJson("/api/college/players?sort=pts&order=desc&limit=200"),
    fetchJson(`/api/scouting/reports?team_id=${encodeURIComponent(state.selectedTeamId)}&limit=50`),
  ]);
  state.scoutingScouts = scoutsPayload?.scouts || [];
  state.scoutingReports = reportsPayload?.reports || [];
  const players = playersPayload?.players || [];

  els.collegeScoutSelect.innerHTML = state.scoutingScouts.map((s) => `<option value="${s.scout_id}">${s.display_name} (${s.specialty_key})</option>`).join("");
  els.collegeScoutPlayerSelect.innerHTML = players.map((p) => `<option value="${p.player_id}">${p.name} · ${p.college_team_name || p.college_team_id}</option>`).join("");

  const selectedScout = state.scoutingScouts.find((x) => x.scout_id === els.collegeScoutSelect.value) || state.scoutingScouts[0];
  els.collegeScoutHint.textContent = selectedScout
    ? `전문 분야: ${selectedScout.specialty_key || "-"} · 리포트 생성은 월말 진행 시 반영됩니다.`
    : "스카우터를 선택하면 전문 분야를 확인할 수 있습니다.";

  els.collegeReportsBody.innerHTML = state.scoutingReports.length ? state.scoutingReports.map((r) => `
    <tr>
      <td>${String(r?.as_of_date || "-").slice(0, 10)}</td>
      <td>${r?.scout?.display_name || r?.scout?.scout_id || "-"}</td>
      <td>${r?.player_snapshot?.name || r?.target_player_id || "-"}</td>
      <td><span class="college-status-pill ${reportStatusClass(r?.status)}">${r?.status || "-"}</span></td>
      <td title="${r?.report_text || ""}">${(r?.report_text || "").slice(0, 80) || "(텍스트 리포트 없음)"}</td>
    </tr>
  `).join("") : `<tr><td class="schedule-empty" colspan="5">리포트가 없습니다. 배정 후 월말 진행 시 생성됩니다.</td></tr>`;
}

async function showCollegeScreen() {
  if (!state.selectedTeamId) {
    alert("먼저 팀을 선택해주세요.");
    return;
  }
  setLoading(true, "대학 리그 정보를 불러오는 중입니다...");
  try {
    const [meta, teams, experts] = await Promise.all([
      fetchJson("/api/college/meta"),
      fetchJson("/api/college/teams"),
      fetchJson("/api/offseason/draft/experts"),
    ]);
    state.collegeMeta = meta;
    state.collegeTeams = teams || [];
    state.collegeExperts = experts?.experts || [];

    els.collegeMetaLine.textContent = `시즌 ${meta?.season_year || "-"} · 대학팀 ${meta?.college?.teams || 0}개 · 예정 드래프트 ${meta?.upcoming_draft_year || "-"}`;
    els.collegeKpiSeason.textContent = String(meta?.season_year || "-");
    els.collegeKpiTeams.textContent = `${meta?.college?.teams || 0}개`;
    els.collegeKpiDraft.textContent = String(meta?.upcoming_draft_year || "-");
    renderCollegeTeams(state.collegeTeams);
    if (state.selectedCollegeTeamId) {
      await loadCollegeTeamDetail(state.selectedCollegeTeamId);
    }

    const sortOptions = ["pts", "reb", "ast", "stl", "blk", "mpg", "games", "ts_pct", "usg", "fg_pct"];
    els.collegeLeaderSort.innerHTML = sortOptions.map((k) => `<option value="${k}">${k.toUpperCase()}</option>`).join("");
    els.collegeLeaderSort.value = state.collegeLeadersSort;
    await loadCollegeLeaders();

    els.collegeExpertSelect.innerHTML = state.collegeExperts.map((e) => `<option value="${e.expert_id}">${e.display_name}</option>`).join("");
    if (!state.selectedCollegeExpertId && state.collegeExperts[0]?.expert_id) {
      state.selectedCollegeExpertId = state.collegeExperts[0].expert_id;
    }
    els.collegeExpertSelect.value = state.selectedCollegeExpertId;
    await loadCollegeBigboard();

    await loadCollegeScouting();
    switchCollegeTab("teams");
    activateScreen(els.collegeScreen);
  } finally {
    setLoading(false);
  }
}

function showTeamSelection() { activateScreen(els.teamScreen); }

function showMainScreen() {
  activateScreen(els.mainScreen);
  const teamName = state.selectedTeamName || state.selectedTeamId || "선택 팀";
  els.mainTeamTitle.textContent = teamName;
  bindMainPreviewTabs();
  void refreshMainDashboard();
}

function formatIsoDate(dateString) {
  const raw = String(dateString || "").slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw : "YYYY-MM-DD";
}

function formatCountdownLabel(gameDate) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(gameDate || ""))) return "TIP-OFF 예정";
  const today = new Date();
  const target = new Date(`${gameDate}T00:00:00`);
  const todayMidnight = new Date(`${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}T00:00:00`);
  const days = Math.round((target - todayMidnight) / (1000 * 60 * 60 * 24));
  if (days <= 0) return "TODAY · GAME DAY";
  return `D-${days}`;
}

function teamInitial(teamName) {
  const name = String(teamName || "").trim();
  if (!name) return "NBA";
  const parts = name.split(/\s+/).filter(Boolean);
  const token = (parts[0]?.[0] || "") + (parts[1]?.[0] || "");
  return token.toUpperCase() || name.slice(0, 2).toUpperCase();
}

function setLogoPlaceholder(el, teamName) {
  if (!el) return;
  el.textContent = teamInitial(teamName);
}

function classifyScheduleStress(upcomingGames) {
  const cnt = (upcomingGames || []).slice(0, 4).length;
  if (cnt >= 4) return { label: "일정 강도 높음", cls: "danger" };
  if (cnt >= 3) return { label: "일정 강도 주의", cls: "warn" };
  return { label: "일정 강도 안정", cls: "" };
}

function streakFromRecent(results) {
  if (!results.length) return "-";
  const first = results[0];
  let n = 0;
  for (const r of results) {
    if (r === first) n += 1;
    else break;
  }
  return `${first}${n}`;
}

function normalizeRecord(row) {
  if (!row) return { wins: 0, losses: 0, pct: 0, l10: "0-0", strk: "-" };
  return {
    wins: Number(row?.wins || 0),
    losses: Number(row?.losses || 0),
    pct: Number(row?.pct || 0),
    l10: row?.l10 || "0-0",
    strk: row?.strk || "-",
  };
}

function setBadgeState(el, text, cls = "") {
  if (!el) return;
  el.textContent = text;
  el.classList.remove("warn", "danger");
  if (cls) el.classList.add(cls);
}

function updatePreviewCompare(homeRecord, awayRecord) {
  const homePct = clamp(num(homeRecord?.pct, 0.5), 0, 1);
  const awayPct = clamp(num(awayRecord?.pct, 0.5), 0, 1);
  const share = (homePct + awayPct) > 0 ? (homePct / (homePct + awayPct)) * 100 : 50;
  if (els.compareWinrateBar) els.compareWinrateBar.style.width = `${share.toFixed(1)}%`;
  if (els.compareWinrateText) {
    els.compareWinrateText.textContent = `${Math.round(homePct * 100)} : ${Math.round(awayPct * 100)}`;
  }

  const [homeL10W, homeL10L] = String(homeRecord?.l10 || "0-0").split("-").map((v) => Number(v || 0));
  const [awayL10W, awayL10L] = String(awayRecord?.l10 || "0-0").split("-").map((v) => Number(v || 0));
  const homeL10Pct = (homeL10W + homeL10L) > 0 ? homeL10W / (homeL10W + homeL10L) : 0.5;
  const awayL10Pct = (awayL10W + awayL10L) > 0 ? awayL10W / (awayL10W + awayL10L) : 0.5;
  const l10Share = (homeL10Pct + awayL10Pct) > 0 ? (homeL10Pct / (homeL10Pct + awayL10Pct)) * 100 : 50;
  if (els.compareLast10Bar) els.compareLast10Bar.style.width = `${l10Share.toFixed(1)}%`;
  if (els.compareLast10Text) {
    els.compareLast10Text.textContent = `${homeL10W} : ${awayL10W}`;
  }
}

function renderListItems(el, items, emptyText) {
  if (!el) return;
  const rows = (items || []).filter(Boolean);
  el.innerHTML = rows.length
    ? rows.map((text) => `<li>${text}</li>`).join("")
    : `<li>${emptyText}</li>`;
}

function bindMainPreviewTabs() {
  const tabs = document.querySelectorAll('#main-screen [data-preview-tab]');
  const panels = document.querySelectorAll('#main-screen [data-preview-panel]');
  tabs.forEach((tab) => {
    if (tab.dataset.bound === "true") return;
    tab.dataset.bound = "true";
    tab.addEventListener("click", () => {
      const key = tab.dataset.previewTab;
      tabs.forEach((btn) => btn.classList.toggle("is-active", btn === tab));
      panels.forEach((panel) => panel.classList.toggle("is-active", panel.dataset.previewPanel === key));
    });
  });
}

function randomTipoffTime() {
  const hour24 = 14 + Math.floor(Math.random() * 6);
  const minute = Math.floor(Math.random() * 60);
  const hour12 = String(hour24 > 12 ? hour24 - 12 : hour24).padStart(2, "0");
  return `${hour12}:${String(minute).padStart(2, "0")} PM`;
}

function isCompletedGame(game) {
  return game?.home_score != null && game?.away_score != null;
}

async function fetchInGameDate() {
  const summary = await fetchJson("/api/state/summary");
  const currentDate = summary?.workflow_state?.league?.current_date;
  return formatIsoDate(currentDate);
}

function formatCompactMoney(value) {
  const n = num(value, 0);
  const abs = Math.abs(n);
  if (abs >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
  return `$${Math.round(n)}`;
}

function formatSignedCompactMoney(value) {
  const n = num(value, 0);
  const sign = n > 0 ? '+' : '';
  return `${sign}${formatCompactMoney(n)}`;
}

function average(arr, key) {
  if (!arr.length) return 0;
  const total = arr.reduce((sum, row) => sum + num(row?.[key], 0), 0);
  return total / arr.length;
}

function topRosterFocus(rosterRows) {
  if (!rosterRows.length) return null;
  const sorted = [...rosterRows].sort((a, b) => num(b.ovr, 0) - num(a.ovr, 0));
  return sorted[0] || null;
}

function roleLabelByScore(ovr) {
  const score = num(ovr, 0);
  if (score >= 86) return 'Franchise Core';
  if (score >= 79) return 'Primary Rotation';
  if (score >= 73) return 'Core Bench';
  return 'Development Unit';
}

function renderQuickFocusList(el, items, empty) {
  if (!el) return;
  const rows = (items || []).filter(Boolean);
  el.innerHTML = rows.length ? rows.map((item) => `<li>${item}</li>`).join('') : `<li>${empty}</li>`;
}

function renderHomeInsightBar(rosterRows, teamSummary, injuryCount, fatigueCount, stressLabel) {
  const avgOvr = average(rosterRows, 'ovr');
  const avgAge = average(rosterRows, 'age');
  const totalSalary = rosterRows.reduce((sum, row) => sum + num(row?.salary, 0), 0);
  const capSpace = num(teamSummary?.cap_space, 0);

  if (els.homeKpiOvr) els.homeKpiOvr.textContent = rosterRows.length ? avgOvr.toFixed(1) : '--';
  if (els.homeKpiAge) els.homeKpiAge.textContent = rosterRows.length ? `${avgAge.toFixed(1)}세` : '--';
  if (els.homeKpiSalary) els.homeKpiSalary.textContent = formatCompactMoney(totalSalary);
  if (els.homeKpiCap) els.homeKpiCap.textContent = formatSignedCompactMoney(capSpace);
  if (els.homeKpiAlert) els.homeKpiAlert.textContent = `부상 ${injuryCount} · 피로 ${fatigueCount}`;

  const focus = topRosterFocus(rosterRows);
  if (!focus) {
    if (els.quickFocusPlayer) els.quickFocusPlayer.textContent = '핵심 선수 분석 대기';
    if (els.quickFocusRole) els.quickFocusRole.textContent = stressLabel || '로스터 로딩 중...';
    if (els.quickFocusCondition) els.quickFocusCondition.textContent = '--';
    if (els.quickFocusHealth) els.quickFocusHealth.textContent = '--';
    if (els.quickFocusContract) els.quickFocusContract.textContent = '--';
    renderQuickFocusList(els.quickFocusStrengths, [], '강점 데이터 없음');
    renderQuickFocusList(els.quickFocusRisks, [], '리스크 데이터 없음');
    return;
  }

  const shortSt = num(focus.short_term_stamina, 1);
  const longSt = num(focus.long_term_stamina, 1);
  const health = ((shortSt + longSt) / 2) * 100;
  const condition = num(focus.sharpness, 50);
  const contractIndex = focus.salary > 0 ? (num(focus.ovr, 0) / (focus.salary / 1_000_000)) : 0;

  if (els.quickFocusPlayer) els.quickFocusPlayer.textContent = `${focus.name || '-'} (${focus.pos || '-'})`;
  if (els.quickFocusRole) els.quickFocusRole.textContent = `${roleLabelByScore(focus.ovr)} · OVR ${num(focus.ovr, 0).toFixed(1)}`;
  if (els.quickFocusCondition) els.quickFocusCondition.textContent = `${Math.round(condition)}%`;
  if (els.quickFocusHealth) els.quickFocusHealth.textContent = `${Math.round(health)}%`;
  if (els.quickFocusContract) els.quickFocusContract.textContent = `${contractIndex.toFixed(2)} CEI`;

  const strengths = [
    `볼륨 점수 OVR ${num(focus.ovr, 0).toFixed(1)}`,
    `컨디션 ${Math.round(condition)}%`,
    `건강 지수 ${Math.round(health)}%`,
  ];
  const risks = [];
  if (focus.age >= 34) risks.push(`고령 구간 ${focus.age}세`);
  if (shortSt < 0.6 || longSt < 0.7) risks.push(`체력 관리 필요 (단기 ${Math.round(shortSt * 100)} / 장기 ${Math.round(longSt * 100)})`);
  if (focus.salary >= 40_000_000) risks.push(`고연봉 자원 ${formatCompactMoney(focus.salary)}`);

  renderQuickFocusList(els.quickFocusStrengths, strengths.slice(0, 3), '강점 데이터 없음');
  renderQuickFocusList(els.quickFocusRisks, risks.slice(0, 2), '현재 리스크 낮음');
}

function resetNextGameCard() {
  els.teamAName.textContent = "Team A";
  els.teamBName.textContent = "Team B";
  setLogoPlaceholder(els.teamALogo, "Team A");
  setLogoPlaceholder(els.teamBLogo, "Team B");
  if (els.nextGameArena) els.nextGameArena.textContent = "";
  if (els.nextGameCountdown) els.nextGameCountdown.textContent = "TIP-OFF 예정";
  if (els.nextGameSubline) els.nextGameSubline.textContent = "경기 준비 데이터를 분석 중입니다.";
  els.nextGameDatetime.textContent = "YYYY-MM-DD --:-- PM";
  if (els.commandTeamSummary) els.commandTeamSummary.textContent = "팀 운영 데이터를 불러오는 중...";
  if (els.commandSeasonStage) els.commandSeasonStage.textContent = "정규 시즌";
  if (els.kpiRecord) els.kpiRecord.textContent = "--";
  if (els.kpiWinRate) els.kpiWinRate.textContent = "--";
  if (els.kpiLast10) els.kpiLast10.textContent = "--";
  if (els.kpiStreak) els.kpiStreak.textContent = "--";
  setBadgeState(els.statusInjury, "부상 --명");
  setBadgeState(els.statusFatigue, "피로 주의 --명");
  setBadgeState(els.statusSchedule, "일정 강도 확인중");
  if (els.homeKpiOvr) els.homeKpiOvr.textContent = "--";
  if (els.homeKpiAge) els.homeKpiAge.textContent = "--";
  if (els.homeKpiSalary) els.homeKpiSalary.textContent = "--";
  if (els.homeKpiCap) els.homeKpiCap.textContent = "--";
  if (els.homeKpiAlert) els.homeKpiAlert.textContent = "--";
  if (els.quickFocusPlayer) els.quickFocusPlayer.textContent = "핵심 선수 분석 대기";
  if (els.quickFocusRole) els.quickFocusRole.textContent = "로스터 로딩 중...";
  if (els.quickFocusCondition) els.quickFocusCondition.textContent = "--";
  if (els.quickFocusHealth) els.quickFocusHealth.textContent = "--";
  if (els.quickFocusContract) els.quickFocusContract.textContent = "--";
  renderQuickFocusList(els.quickFocusStrengths, [], "강점 데이터 없음");
  renderQuickFocusList(els.quickFocusRisks, [], "리스크 데이터 없음");
  updatePreviewCompare(null, null);
  renderListItems(els.conditionList, ["컨디션 데이터를 불러오는 중입니다."], "컨디션 데이터가 없습니다.");
  renderListItems(els.strategyList, ["전략 데이터를 준비 중입니다."], "전략 힌트가 없습니다.");
  renderListItems(els.priorityList, ["우선 과제를 계산 중입니다."], "우선 과제가 없습니다.");
  renderListItems(els.quickIntelList, ["핵심 체크 포인트를 준비 중입니다."], "표시할 정보가 없습니다.");
  if (els.timelineList) els.timelineList.innerHTML = '<div class="timeline-item"><span class="tag">INFO</span><span>주간 일정 정보를 불러오는 중...</span><span>-</span></div>';
  renderListItems(els.activityFeed, ["최근 활동 정보를 불러오는 중입니다."], "활동 데이터가 없습니다.");
}

function formatLeader(leader) {
  if (!leader || !leader.name) return "-";
  return `${leader.name} ${num(leader.value, 0)}`;
}

function renderEmptyScheduleRow(colSpan, text) {
  return `<tr><td colspan="${colSpan}" class="schedule-empty">${text}</td></tr>`;
}

function renderScheduleTables(games) {
  const completed = (games || []).filter((g) => g?.is_completed);
  const upcoming = (games || []).filter((g) => !g?.is_completed);

  els.scheduleCompletedBody.innerHTML = completed.length
    ? completed.map((g) => {
      const result = g.result || {};
      const record = g.record_after_game || {};
      const leaders = g.leaders || {};
      return `
        <tr>
          <td>${g.date_mmdd || "--/--"}</td>
          <td class="schedule-opponent-cell">${g.opponent_label || "-"} <span class="schedule-opponent-name">${g.opponent_team_name || g.opponent_team_id || ""}</span></td>
          <td><span class="schedule-result-badge ${result.wl === "W" ? "schedule-result-win" : "schedule-result-loss"}">${result.display || "-"}</span></td>
          <td>${record.display || "-"}</td>
          <td>${formatLeader(leaders.points)}</td>
          <td>${formatLeader(leaders.rebounds)}</td>
          <td>${formatLeader(leaders.assists)}</td>
        </tr>
      `;
    }).join("")
    : renderEmptyScheduleRow(7, "완료된 경기가 없습니다.");

  els.scheduleUpcomingBody.innerHTML = upcoming.length
    ? upcoming.map((g) => `
      <tr>
        <td>${g.date_mmdd || "--/--"}</td>
        <td class="schedule-opponent-cell">${g.opponent_label || "-"} <span class="schedule-opponent-name">${g.opponent_team_name || g.opponent_team_id || ""}</span></td>
        <td><span class="schedule-time-chip">${g.tipoff_time || "--:-- --"}</span></td>
      </tr>
    `).join("")
    : renderEmptyScheduleRow(3, "예정된 경기가 없습니다.");
}

async function showScheduleScreen() {
  if (!state.selectedTeamId) {
    alert("먼저 팀을 선택해주세요.");
    return;
  }

  setLoading(true, "스케줄 정보를 불러오는 중...");
  try {
    const schedule = await fetchJson(`/api/team-schedule/${encodeURIComponent(state.selectedTeamId)}`);
    const teamName = state.selectedTeamName || TEAM_FULL_NAMES[state.selectedTeamId] || state.selectedTeamId;
    els.scheduleTitle.textContent = `${teamName} 정규 시즌 일정`;
    renderScheduleTables(schedule?.games || []);
    activateScreen(els.scheduleScreen);
  } catch (e) {
    els.scheduleCompletedBody.innerHTML = renderEmptyScheduleRow(7, `스케줄 로딩 실패: ${e.message}`);
    els.scheduleUpcomingBody.innerHTML = renderEmptyScheduleRow(3, "-");
    activateScreen(els.scheduleScreen);
  } finally {
    setLoading(false);
  }
}

async function refreshMainDashboard() {
  if (!state.selectedTeamId) return;

  resetNextGameCard();

  try {
    const currentDate = await fetchInGameDate();
    state.currentDate = currentDate;
    els.mainCurrentDate.textContent = currentDate;

    const [schedule, teamDetail, standingsPayload] = await Promise.all([
      fetchJson(`/api/team-schedule/${encodeURIComponent(state.selectedTeamId)}`),
      fetchJson(`/api/team-detail/${encodeURIComponent(state.selectedTeamId)}`).catch(() => ({})),
      fetchJson("/api/standings/table").catch(() => null),
    ]);

    const games = schedule?.games || [];
    const completed = games.filter((g) => g?.is_completed);
    const upcoming = games.filter((g) => !g?.is_completed);
    const teamId = String(state.selectedTeamId || "").toUpperCase();

    const recentResults = completed
      .slice(-10)
      .reverse()
      .map((g) => {
        const isHome = String(g?.home_team_id || "").toUpperCase() === teamId;
        const myScore = Number(isHome ? g?.home_score : g?.away_score);
        const oppScore = Number(isHome ? g?.away_score : g?.home_score);
        if (!Number.isFinite(myScore) || !Number.isFinite(oppScore)) return "";
        return myScore >= oppScore ? "W" : "L";
      })
      .filter(Boolean);

    const wins = recentResults.filter((r) => r === "W").length;
    const losses = recentResults.filter((r) => r === "L").length;

    let standingsRow = null;
    if (standingsPayload) {
      const pool = [...(standingsPayload?.east || []), ...(standingsPayload?.west || [])];
      standingsRow = pool.find((row) => String(row?.team_id || "").toUpperCase() === teamId) || null;
    }

    const baseRecord = normalizeRecord(standingsRow);
    if (els.kpiRecord) els.kpiRecord.textContent = `${baseRecord.wins}-${baseRecord.losses}`;
    if (els.kpiWinRate) els.kpiWinRate.textContent = `${(baseRecord.pct * 100).toFixed(1)}%`;
    if (els.kpiLast10) els.kpiLast10.textContent = recentResults.length ? `${wins}-${losses}` : baseRecord.l10;
    if (els.kpiStreak) els.kpiStreak.textContent = baseRecord.strk !== "-" ? baseRecord.strk : streakFromRecent(recentResults);

    const seasonYear = Number(currentDate.slice(0, 4));
    if (els.commandSeasonStage) {
      els.commandSeasonStage.textContent = Number.isFinite(seasonYear) ? `${seasonYear}-${String((seasonYear + 1) % 100).padStart(2, "0")} 정규 시즌` : "정규 시즌";
    }
    if (els.commandTeamSummary) {
      const summaryParts = [];
      if (standingsRow?.rank) summaryParts.push(`${standingsRow.rank}위`);
      if (standingsRow?.conf) summaryParts.push(`컨퍼런스 ${standingsRow.conf}`);
      if (standingsRow?.gb_display) summaryParts.push(`GB ${standingsRow.gb_display}`);
      els.commandTeamSummary.textContent = summaryParts.length ? summaryParts.join(" · ") : "순위 데이터 집계중";
    }

    const rosterRows = teamDetail?.roster || [];
    const injuryCount = rosterRows.filter((p) => {
      const status = String(p?.injury_status || p?.availability || "").toUpperCase();
      return ["OUT", "QUESTIONABLE", "DOUBTFUL", "INJURED"].some((token) => status.includes(token));
    }).length;
    const fatigueCount = rosterRows.filter((p) => num(p?.short_term_stamina, 1) < 0.55 || num(p?.long_term_stamina, 1) < 0.65).length;
    setBadgeState(els.statusInjury, `부상 ${injuryCount}명`, injuryCount >= 2 ? "danger" : injuryCount >= 1 ? "warn" : "");
    setBadgeState(els.statusFatigue, `피로 주의 ${fatigueCount}명`, fatigueCount >= 4 ? "danger" : fatigueCount >= 2 ? "warn" : "");

    const stress = classifyScheduleStress(upcoming);
    setBadgeState(els.statusSchedule, stress.label, stress.cls);

    renderHomeInsightBar(rosterRows, teamDetail?.summary || {}, injuryCount, fatigueCount, stress.label);

    const nextGame = games.find((g) => {
      const date = String(g?.date || "").slice(0, 10);
      return date >= currentDate && !isCompletedGame(g);
    });

    if (!nextGame) {
      els.nextGameDatetime.textContent = "예정된 다음 경기가 없습니다.";
      if (els.nextGameSubline) els.nextGameSubline.textContent = "일정이 비어 있어 다음 매치업을 생성할 수 없습니다.";
      return;
    }

    const homeId = String(nextGame.home_team_id || "").toUpperCase();
    const awayId = String(nextGame.away_team_id || "").toUpperCase();
    const homeName = TEAM_FULL_NAMES[homeId] || homeId || "Team A";
    const awayName = TEAM_FULL_NAMES[awayId] || awayId || "Team B";
    const gameDate = formatIsoDate(nextGame.date);
    const tipoffTime = nextGame.tipoff_time || "TBD";

    els.teamAName.textContent = homeName;
    els.teamBName.textContent = awayName;
    setLogoPlaceholder(els.teamALogo, homeName);
    setLogoPlaceholder(els.teamBLogo, awayName);
    if (els.nextGameArena) els.nextGameArena.textContent = nextGame.arena_name || nextGame.location || "경기장 정보 미제공";
    if (els.nextGameCountdown) els.nextGameCountdown.textContent = formatCountdownLabel(gameDate);
    els.nextGameDatetime.textContent = `${gameDate} ${tipoffTime}`;
    if (els.nextGameSubline) {
      const homeAway = homeId === teamId ? "홈" : "원정";
      const statusLine = stress?.label ? ` · 일정 ${stress.label}` : "";
      els.nextGameSubline.textContent = `${homeAway} 경기 준비 브리핑${statusLine}`;
    }

    const byTeam = {};
    [...(standingsPayload?.east || []), ...(standingsPayload?.west || [])].forEach((row) => {
      byTeam[String(row?.team_id || "").toUpperCase()] = normalizeRecord(row);
    });
    updatePreviewCompare(byTeam[homeId], byTeam[awayId]);

    const conditionLines = [];
    if (injuryCount > 0) conditionLines.push(`현재 출전 이슈 ${injuryCount}명 · 결장/주의 로테이션 확인 필요`);
    else conditionLines.push("현재 결장 이슈 없음 · 로테이션 안정 상태");
    conditionLines.push(`피로 주의 선수 ${fatigueCount}명 · 벤치 분배 조정 권장`);
    conditionLines.push(`다음 경기 ${nextGame.home_team_id === teamId ? "홈" : "원정"} 일정`);
    renderListItems(els.conditionList, conditionLines, "컨디션 데이터가 없습니다.");

    const strategyLines = [];
    const myRecord = byTeam[teamId] || baseRecord;
    const oppId = homeId === teamId ? awayId : homeId;
    const oppRecord = byTeam[oppId] || normalizeRecord(null);
    if (num(myRecord.pct, 0.5) < num(oppRecord.pct, 0.5)) strategyLines.push("상대 승률 우세 · 경기 초반 템포를 낮춰 턴오버 관리에 집중하세요.");
    else strategyLines.push("전력 우세 구간입니다 · 초반 주전 라인업으로 리드를 선점하세요.");
    strategyLines.push(fatigueCount >= 3 ? "피로 누적 구간 · 2Q 중반부터 로테이션 폭을 넓히는 것이 안전합니다." : "컨디션 양호 · 클러치 전술 리허설을 유지하세요.");
    strategyLines.push(stress.cls ? "일정 압박 구간 · 경기 후 회복 중심 훈련으로 전환 권장." : "일정 부담이 낮아 전술 디테일 훈련을 병행할 수 있습니다.");
    renderListItems(els.strategyList, strategyLines, "전략 힌트가 없습니다.");

    renderListItems(els.priorityList, [
      `다음 경기 대비 핵심 과제: ${nextGame.home_team_id === teamId ? "홈 어드밴티지 활용" : "원정 초반 집중"}`,
      `부상/피로 관리: ${injuryCount}명 / ${fatigueCount}명 모니터링`,
      `최근 흐름: ${recentResults.length ? `${wins}-${losses}` : "데이터 부족"} · 스트릭 ${els.kpiStreak?.textContent || "-"}`,
    ], "우선 과제가 없습니다.");

    renderListItems(els.quickIntelList, [
      `다음 4경기 ${Math.min(upcoming.length, 4)}경기 예정`,
      `주간 핵심: ${stress.label}`,
      `탭 이동 없이 홈에서 주요 리스크 점검 가능`,
    ], "표시할 정보가 없습니다.");

    if (els.timelineList) {
      const timelineGames = upcoming.slice(0, 6);
      els.timelineList.innerHTML = timelineGames.length
        ? timelineGames.map((g, idx) => {
          const tDate = formatIsoDate(g?.date);
          const isHome = String(g?.home_team_id || "").toUpperCase() === teamId;
          const opp = TEAM_FULL_NAMES[String(isHome ? g?.away_team_id : g?.home_team_id || "").toUpperCase()] || "상대 미정";
          return `<div class="timeline-item ${idx === 0 ? "is-next" : ""}"><span>${tDate}</span><span>${isHome ? "vs" : "@"} ${opp}</span><span class="tag">${idx === 0 ? "NEXT" : "UPCOMING"}</span></div>`;
        }).join("")
        : '<div class="timeline-item"><span>-</span><span>예정 일정이 없습니다.</span><span class="tag">INFO</span></div>';
    }

    const feedItems = completed.slice(-3).reverse().map((g) => {
      const isHome = String(g?.home_team_id || "").toUpperCase() === teamId;
      const my = Number(isHome ? g?.home_score : g?.away_score);
      const opp = Number(isHome ? g?.away_score : g?.home_score);
      const wl = Number.isFinite(my) && Number.isFinite(opp) ? (my >= opp ? "승리" : "패배") : "결과 미정";
      const oppName = TEAM_FULL_NAMES[String(isHome ? g?.away_team_id : g?.home_team_id || "").toUpperCase()] || "상대 미정";
      return `${formatIsoDate(g?.date)} ${oppName}전 ${wl} (${Number.isFinite(my) ? my : "-"}-${Number.isFinite(opp) ? opp : "-"})`;
    });
    feedItems.push(`다음 경기 준비 상태: ${stress.label}`);
    renderListItems(els.activityFeed, feedItems, "최근 활동이 없습니다.");
  } catch (e) {
    resetNextGameCard();
    els.mainCurrentDate.textContent = "YYYY-MM-DD";
    els.nextGameDatetime.textContent = `다음 경기 정보를 불러오지 못했습니다: ${e.message}`;
    if (els.nextGameSubline) els.nextGameSubline.textContent = "네트워크 상태를 확인하고 다시 시도해주세요.";
    renderListItems(els.activityFeed, [`대시보드 로딩 실패: ${e.message}`], "활동 데이터가 없습니다.");
  }
}

function num(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function clamp(v, min, max) {
  return Math.min(max, Math.max(min, v));
}

function formatHeightIn(inches) {
  const inch = Math.max(0, Math.round(num(inches, 0)));
  const feet = Math.floor(inch / 12);
  const rem = inch % 12;
  return `${feet}'${String(rem).padStart(2, "0")}"`;
}

function formatWeightLb(lb) { return `${Math.round(num(lb, 0))} lb`; }

function formatMoney(n) {
  return `$${Math.round(num(n, 0)).toLocaleString("en-US")}`;
}

function formatPercent(value) {
  return `${Math.round(clamp(num(value, 0), 0, 1) * 100)}%`;
}

function seasonLabelByYear(year) {
  const y = Number(year);
  if (!Number.isFinite(y)) return "시즌 미정";
  const start = String(y).slice(-2);
  const end = String(y + 1).slice(-2).padStart(2, "0");
  return `${start}-${end} 시즌`;
}

function getOptionTypeLabel(optionType) {
  if (optionType === "PLAYER") return "플레이어 옵션";
  if (optionType === "TEAM") return "팀 옵션";
  return "옵션";
}

function ratioToColor(ratio) {
  const r = clamp(num(ratio, 0), 0, 1);
  const hue = Math.round(r * 120);
  return `hsl(${hue} 80% 36%)`;
}

function renderConditionRing(longStamina, shortStamina) {
  const longPct = clamp(num(longStamina, 0), 0, 1) * 100;
  const shortPct = clamp(num(shortStamina, 0), 0, 1) * 100;
  const longColor = ratioToColor(longStamina);
  const shortColor = ratioToColor(shortStamina);
  return `<div class="condition-ring" style="--long-pct:${longPct};--short-pct:${shortPct};--long-color:${longColor};--short-color:${shortColor};" title="장기 ${Math.round(longPct)}% · 단기 ${Math.round(shortPct)}%"></div>`;
}

function renderRosterRows(rows) {
  els.rosterBody.innerHTML = "";
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.className = "roster-row";
    tr.dataset.playerId = row.player_id;

    const shortStamina = row.short_term_stamina ?? (1 - num(row.short_term_fatigue, 0));
    const longStamina = row.long_term_stamina ?? (1 - num(row.long_term_fatigue, 0));
    const sharpness = clamp(num(row.sharpness, 50), 0, 100);

    tr.innerHTML = `
      <td>${row.name || "-"}</td>
      <td>${row.pos || "-"}</td>
      <td>${num(row.age, 0)}</td>
      <td>${formatHeightIn(row.height_in)}</td>
      <td>${formatWeightLb(row.weight_lb)}</td>
      <td>${formatMoney(row.salary)}</td>
      <td class="condition-cell">${renderConditionRing(longStamina, shortStamina)}</td>
      <td><span class="sharpness-badge" style="background:${ratioToColor(sharpness / 100)}">${Math.round(sharpness)}%</span></td>
    `;

    tr.addEventListener("click", () => {
      state.selectedPlayerId = row.player_id;
      loadPlayerDetail(row.player_id).catch((e) => alert(e.message));
    });

    els.rosterBody.appendChild(tr);
  }
}

function getDissatisfactionSummary(d) {
  if (!d || !d.is_dissatisfied) return { text: "불만: 없음", details: [] };
  const st = d.state || {};
  const axes = [
    ["팀", num(st.team_frustration, 0)],
    ["역할", num(st.role_frustration, 0)],
    ["계약", num(st.contract_frustration, 0)],
    ["건강", num(st.health_frustration, 0)],
    ["케미", num(st.chemistry_frustration, 0)],
    ["사용률", num(st.usage_frustration, 0)],
  ].sort((a, b) => b[1] - a[1]);

  const top = axes.filter(([, v]) => v > 0.1).slice(0, 3).map(([k, v]) => `${k} ${Math.round(v * 100)}%`);
  const level = clamp(num(st.trade_request_level, 0), 0, 10);
  return {
    text: `불만: 있음 (강도 ${Math.round(axes[0][1] * 100)}%, TR ${level})`,
    details: top,
  };
}

function renderAttrGrid(attrs) {
  const entries = Object.entries(attrs || {}).sort((a, b) => String(a[0]).localeCompare(String(b[0])));
  if (!entries.length) return '<p class="empty-copy">능력치 데이터가 없습니다.</p>';
  return entries
    .map(([k, v]) => {
      const value = typeof v === "number" ? (Math.abs(v) <= 1 ? `${Math.round(v * 100)}` : `${Math.round(v)}`) : String(v);
      return `
        <div class="attr-card">
          <span class="attr-name">${k}</span>
          <strong class="attr-value">${value}</strong>
        </div>
      `;
    })
    .join("");
}

function buildContractRows(contractActive, fallbackSalary) {
  if (!contractActive) {
    return [{ label: "계약", value: "활성 계약 정보 없음", emphasis: true }];
  }

  const salaryByYear = contractActive.salary_by_year || {};
  const salaryYears = Object.keys(salaryByYear)
    .map((y) => Number(y))
    .filter((y) => Number.isFinite(y))
    .sort((a, b) => a - b);

  const optionByYear = new Map((contractActive.options || []).map((opt) => [Number(opt.season_year), opt]));
  const rows = [];

  const initialSalary = salaryYears.length ? salaryByYear[salaryYears[0]] : fallbackSalary;
  rows.push({ label: "샐러리", value: formatMoney(initialSalary), emphasis: true });

  salaryYears.forEach((year, idx) => {
    if (idx === 0) return;
    const option = optionByYear.get(year);
    const optionText = option ? ` (${getOptionTypeLabel(option.type)})` : "";
    rows.push({
      label: seasonLabelByYear(year),
      value: `${formatMoney(salaryByYear[year])}${optionText}`,
      emphasis: false,
    });
  });

  const outstandingOptionRows = (contractActive.options || [])
    .map((option) => ({
      year: Number(option.season_year),
      option,
    }))
    .filter(({ year }) => Number.isFinite(year) && !(year in salaryByYear))
    .sort((a, b) => a.year - b.year)
    .map(({ year, option }) => ({
      label: seasonLabelByYear(year),
      value: `${getOptionTypeLabel(option.type)} (${option.status || "PENDING"})`,
      emphasis: false,
    }));

  return rows.concat(outstandingOptionRows);
}

function renderPlayerDetail(detail) {
  const p = detail.player || {};
  const contract = detail.contract || {};
  const diss = getDissatisfactionSummary(detail.dissatisfaction);
  const injury = detail.injury || {};
  const condition = detail.condition || {};
  const seasonStats = detail.season_stats || {};
  const totals = seasonStats.totals || {};
  const twoWay = detail.two_way || {};
  const contractActive = contract.active || null;
  const contractRows = buildContractRows(contractActive, detail.roster?.salary_amount);
  const dissatisfactionDescription = (detail.dissatisfaction?.reasons || []).length
    ? detail.dissatisfaction.reasons
    : diss.details;

  const injuryState = injury.state || {};
  const injuryDetails = [
    injuryState.injury_type && `부상 유형: ${injuryState.injury_type}`,
    injuryState.body_part && `부위: ${injuryState.body_part}`,
    injuryState.games_remaining != null && `복귀 예상: ${num(injuryState.games_remaining, 0)}경기 후`,
    injuryState.note && `메모: ${injuryState.note}`,
  ].filter(Boolean);

  const totalsEntries = Object.entries(totals || {});
  const statsSummary = totalsEntries.length
    ? `<div class="stats-grid">${totalsEntries
      .sort((a, b) => String(a[0]).localeCompare(String(b[0])))
      .map(([k, v]) => `<div class="stat-chip"><span>${k}</span><strong>${typeof v === "number" ? (Math.round(v * 100) / 100) : v}</strong></div>`)
      .join("")}</div>`
    : '<p class="empty-copy">누적 스탯 데이터가 없습니다.</p>';

  const healthText = injury.is_injured
    ? `${injury.status || "부상"} · ${(injury.state?.injury_type || "")}`
    : "건강함";


  const playerName = p.name || "선수";
  els.playerDetailTitle.textContent = `${playerName} 상세 정보`;
  els.playerDetailContent.innerHTML = `
    <div class="player-layout">
      <section class="detail-card detail-card-header">
        <div class="detail-head detail-head-main">
          <div>
            <h3>${playerName}</h3>
            <p class="detail-subline">${p.pos || "-"} · ${num(p.age, 0)}세 · ${formatHeightIn(p.height_in)} / ${formatWeightLb(p.weight_lb)}</p>
          </div>
          <span class="sharpness-badge" style="background:${ratioToColor(num(condition.sharpness, 50) / 100)}">경기력 ${Math.round(num(condition.sharpness, 50))}%</span>
        </div>
      </section>

      <section class="detail-card detail-card-contract">
        <h4>계약 정보</h4>
        <ul class="compact-kv-list">
          ${contractRows.map((row) => `<li><span>${row.label}</span><strong${row.emphasis ? ' class="text-accent"' : ""}>${row.value}</strong></li>`).join("")}
        </ul>
        ${twoWay.is_two_way ? `<p class="section-note">투웨이 계약 · 남은 경기 ${num(twoWay.games_remaining, 0)} / ${num(twoWay.game_limit, 0)}</p>` : ""}
      </section>

      <section class="detail-card detail-card-dissatisfaction">
        <h4>불만 여부</h4>
        <p class="status-line ${detail.dissatisfaction?.is_dissatisfied ? "status-danger" : "status-ok"}">${detail.dissatisfaction?.is_dissatisfied ? "불만 있음" : "불만 없음"}</p>
        <p class="section-copy">${diss.text}</p>
        ${dissatisfactionDescription.length ? `<ul class="kv-list">${dissatisfactionDescription.map((x) => `<li>${x}</li>`).join("")}</ul>` : ""}
      </section>

      <section class="detail-card detail-card-attr">
        <h4>능력치 (ATTR)</h4>
        <div class="attr-grid">${renderAttrGrid(p.attrs || {})}</div>
      </section>

      <section class="detail-card detail-card-health">
        <h4>건강 상태</h4>
        <ul class="compact-kv-list compact-kv-list-health">
          <li><span>장기 체력</span><strong>${formatPercent(condition.long_term_stamina)}</strong></li>
          <li><span>단기 체력</span><strong>${formatPercent(condition.short_term_stamina)}</strong></li>
          <li><span>부상 여부</span><strong>${injury.is_injured ? "부상" : "정상"}</strong></li>
        </ul>
        <p class="section-copy">${healthText}</p>
        ${injuryDetails.length ? `<ul class="kv-list">${injuryDetails.map((item) => `<li>${item}</li>`).join("")}</ul>` : ""}
      </section>

      <section class="detail-card detail-card-stats">
        <h4>누적 스탯</h4>
        <p class="section-copy">출전 경기 수: ${num(seasonStats.games, 0)}경기</p>
        ${statsSummary}
      </section>
    </div>
  `;
}

async function loadPlayerDetail(playerId) {
  setLoading(true, "선수 상세 정보를 불러오는 중...");
  try {
    const detail = await fetchJson(`/api/player-detail/${encodeURIComponent(playerId)}`);
    renderPlayerDetail(detail);
    activateScreen(els.playerDetailScreen);
  } finally {
    setLoading(false);
  }
}

async function showMyTeamScreen() {
  if (!state.selectedTeamId) {
    alert("먼저 팀을 선택해주세요.");
    return;
  }

  setLoading(true, "내 팀 로스터를 불러오는 중...");
  try {
    const detail = await fetchJson(`/api/team-detail/${encodeURIComponent(state.selectedTeamId)}`);
    state.rosterRows = detail.roster || [];
    state.selectedPlayerId = null;

    const teamName = state.selectedTeamName || TEAM_FULL_NAMES[state.selectedTeamId] || state.selectedTeamId;
    els.myTeamTitle.textContent = `${teamName} 선수단`;

    renderRosterRows(state.rosterRows);
    els.playerDetailContent.innerHTML = "";
    els.playerDetailTitle.textContent = "선수 상세 정보";
    activateScreen(els.myTeamScreen);
  } finally {
    setLoading(false);
  }
}

async function confirmTeamSelection(teamId, fullName) {
  const confirmed = window.confirm(`${fullName}을(를) 선택하시겠습니까?`);
  if (!confirmed) return;

  state.selectedTeamId = teamId;
  state.selectedTeamName = fullName;

  if (state.lastSaveSlotId) {
    await fetchJson("/api/game/set-user-team", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slot_id: state.lastSaveSlotId, user_team_id: teamId })
    });
  }

  showMainScreen();
}


function dateToIso(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function parseIsoDate(iso) {
  const v = String(iso || "").slice(0, 10);
  const d = new Date(`${v}T00:00:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}

function startOfWeek(date) {
  const d = new Date(date.getTime());
  const day = d.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  d.setDate(d.getDate() + diff);
  return d;
}

function addDays(date, n) {
  const d = new Date(date.getTime());
  d.setDate(d.getDate() + n);
  return d;
}

function trainingTypeLabel(t) {
  const m = {
    OFF_TACTICS: "공격",
    DEF_TACTICS: "수비",
    FILM: "필름",
    SCRIMMAGE: "청백전",
    RECOVERY: "휴식",
    REST: "없음"
  };
  return m[String(t || "").toUpperCase()] || "-";
}



function setTrainingCardState(el, level) {
  if (!el) return;
  el.classList.remove("is-warn", "is-danger");
  if (level === "danger") el.classList.add("is-danger");
  else if (level === "warn") el.classList.add("is-warn");
}

function trainingRangeLabel(days) {
  if (!days || !days.length) return "기간 정보 없음";
  const first = parseIsoDate(days[0]);
  const last = parseIsoDate(days[days.length - 1]);
  if (!first || !last) return `${days[0]} ~ ${days[days.length - 1]}`;
  return `REG SEASON · ${first.getMonth() + 1}/${first.getDate()} ~ ${last.getMonth() + 1}/${last.getDate()}`;
}

function getTrainingRiskInfo(iso) {
  const row = state.trainingRiskCalendarByDate?.[iso] || {};
  const riskPlayers = Number(row.high_risk_player_count || 0);
  const outPlayers = Number(row.out_player_count || 0);
  const score = Math.min(100, riskPlayers * 28 + outPlayers * 18 + (row.is_back_to_back ? 18 : 0) + (row.injury_event_count ? 12 : 0));
  const cls = score >= 65 ? "risk-high" : score >= 35 ? "risk-mid" : "risk-low";
  return { score, cls, row };
}

function updateTrainingSelectionSummary() {
  if (!els.trainingSelectionSummary) return;
  const selected = [...state.trainingSelectedDates].sort();
  if (!selected.length) {
    els.trainingSelectionSummary.textContent = "선택한 날짜가 없습니다. 캘린더에서 훈련 적용 날짜를 선택하세요.";
    return;
  }
  const gameAdj = selected.filter((d) => {
    const dt = parseIsoDate(d);
    if (!dt) return false;
    const prev = dateToIso(addDays(dt, -1));
    const next = dateToIso(addDays(dt, 1));
    return !!state.trainingGameByDate?.[prev] || !!state.trainingGameByDate?.[next];
  }).length;
  els.trainingSelectionSummary.textContent = `선택 ${selected.length}일 · 경기 인접일 ${gameAdj}일 · 기간 ${selected[0]} ~ ${selected[selected.length - 1]}`;
}

function renderTrainingKpiBar() {
  const k = state.trainingKpi || {};
  if (els.trainingContextRange) els.trainingContextRange.textContent = trainingRangeLabel(state.trainingCalendarDays);

  const sharpAvg = Number(k.sharpnessAvg || 0);
  const lowCount = Number(k.lowSharpCount || 0);
  const games7d = Number(k.games7d || 0);
  const b2b7d = Number(k.b2b7d || 0);
  const highRisk = Number(k.highRisk || 0);
  const out = Number(k.out || 0);
  const ret = Number(k.returning || 0);

  if (els.trainingKpiSharpness) els.trainingKpiSharpness.textContent = sharpAvg ? sharpAvg.toFixed(1) : "-";
  if (els.trainingKpiSharpnessMeta) els.trainingKpiSharpnessMeta.textContent = `최저 ${Number(k.sharpnessMin || 0).toFixed(1)} · 최고 ${Number(k.sharpnessMax || 0).toFixed(1)}`;
  if (els.trainingKpiLow) els.trainingKpiLow.textContent = `${lowCount}명`;
  if (els.trainingKpiLowMeta) els.trainingKpiLowMeta.textContent = lowCount > 3 ? "경기력 저하 구간" : "관리 가능 범위";
  if (els.trainingKpiLoad) els.trainingKpiLoad.textContent = `${games7d} / ${b2b7d}`;
  if (els.trainingKpiLoadMeta) els.trainingKpiLoadMeta.textContent = "경기수 / 백투백";
  if (els.trainingKpiRisk) els.trainingKpiRisk.textContent = `${highRisk}/${out}/${ret}`;
  if (els.trainingKpiRiskMeta) els.trainingKpiRiskMeta.textContent = "HIGH / OUT / RETURN";

  setTrainingCardState(els.trainingKpiSharpnessCard, sharpAvg < 53 ? "warn" : "normal");
  setTrainingCardState(els.trainingKpiLowCard, lowCount >= 4 ? "danger" : lowCount >= 2 ? "warn" : "normal");
  setTrainingCardState(els.trainingKpiLoadCard, games7d >= 4 || b2b7d >= 2 ? "warn" : "normal");
  setTrainingCardState(els.trainingKpiRiskCard, out >= 2 || highRisk >= 4 ? "danger" : highRisk >= 2 ? "warn" : "normal");
}
function buildCalendar4Weeks(currentDateIso) {
  const today = parseIsoDate(currentDateIso) || new Date();
  const first = startOfWeek(today);
  const days = [];
  for (let i = 0; i < 28; i += 1) {
    const date = addDays(first, i);
    days.push(dateToIso(date));
  }
  return days;
}

async function loadTrainingData() {
  if (!state.selectedTeamId) return;
  const currentDate = state.currentDate || await fetchInGameDate();
  state.currentDate = currentDate;
  const allDays = buildCalendar4Weeks(currentDate);
  state.trainingCalendarDays = allDays;

  const schedule = await fetchJson(`/api/team-schedule/${encodeURIComponent(state.selectedTeamId)}`);
  const gameByDate = {};
  (schedule.games || []).forEach((g) => {
    const d = String(g.date || "").slice(0, 10);
    if (!d) return;
    const opp = g.home_team_id === state.selectedTeamId ? g.away_team_id : g.home_team_id;
    gameByDate[d] = String(opp || "").toUpperCase();
  });

  const from = allDays[0];
  const to = allDays[allDays.length - 1];
  const stored = await fetchJson(`/api/practice/team/${encodeURIComponent(state.selectedTeamId)}/sessions?date_from=${encodeURIComponent(from)}&date_to=${encodeURIComponent(to)}`);
  const sessions = { ...(stored.sessions || {}) };

  const previewDates = allDays.filter((d) => d >= currentDate && !gameByDate[d]);
  await Promise.all(previewDates.map(async (d) => {
    if (sessions[d]) return;
    try {
      const res = await fetchJson(`/api/practice/team/${encodeURIComponent(state.selectedTeamId)}/session?date_iso=${encodeURIComponent(d)}`);
      sessions[d] = { session: res.session, is_user_set: res.is_user_set };
    } catch (e) {
      // fail-soft
    }
  }));

  const teamDetail = await fetchJson(`/api/team-detail/${encodeURIComponent(state.selectedTeamId)}`);
  state.trainingRoster = teamDetail.roster || [];

  const [offFam, defFam, sharpness, alerts, riskCalendar] = await Promise.all([
    fetchJson(`/api/readiness/team/${encodeURIComponent(state.selectedTeamId)}/familiarity?scheme_type=offense`).catch(() => ({ items: [] })),
    fetchJson(`/api/readiness/team/${encodeURIComponent(state.selectedTeamId)}/familiarity?scheme_type=defense`).catch(() => ({ items: [] })),
    fetchJson(`/api/readiness/team/${encodeURIComponent(state.selectedTeamId)}/sharpness`).catch(() => ({ distribution: {} })),
    fetchJson(`/api/medical/team/${encodeURIComponent(state.selectedTeamId)}/alerts`).catch(() => ({ team_load_context: {}, primary_alert_player: null })),
    fetchJson(`/api/medical/team/${encodeURIComponent(state.selectedTeamId)}/risk-calendar?date_from=${encodeURIComponent(from)}&days=28`).catch(() => ({ days: [] })),
  ]);
  state.trainingFamiliarity = { offense: offFam.items || [], defense: defFam.items || [] };

  const riskByDate = {};
  (riskCalendar.days || []).forEach((row) => {
    if (!row?.date) return;
    riskByDate[String(row.date).slice(0, 10)] = row;
  });

  const riskRows = Object.values(riskByDate);
  const highRisk = riskRows.reduce((a, x) => a + Number(x.high_risk_player_count || 0), 0);
  const out = riskRows.reduce((a, x) => a + Number(x.out_player_count || 0), 0);
  const ret = riskRows.reduce((a, x) => a + Number(x.returning_player_count || 0), 0);

  const dist = sharpness.distribution || {};
  state.trainingKpi = {
    sharpnessAvg: Number(dist.avg || 0),
    sharpnessMin: Number(dist.min || 0),
    sharpnessMax: Number(dist.max || 0),
    lowSharpCount: Number(dist.low_sharp_count || 0),
    games7d: Number(alerts.team_load_context?.next_7d_game_count || 0),
    b2b7d: Number(alerts.team_load_context?.next_7d_back_to_back_count || 0),
    highRisk,
    out,
    returning: ret,
  };

  state.trainingSessionsByDate = sessions;
  state.trainingGameByDate = gameByDate;
  state.trainingRiskCalendarByDate = riskByDate;
}

function renderTrainingCalendar() {
  const container = els.trainingCalendarGrid;
  const today = state.currentDate;
  container.innerHTML = "";

  state.trainingCalendarDays.forEach((iso) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "training-day-cell";

    const d = parseIsoDate(iso);
    const label = `${d.getMonth() + 1}/${d.getDate()}`;
    const gameOpp = state.trainingGameByDate?.[iso];
    const isPast = iso < today;
    const isGameDay = !!gameOpp;
    const selectable = !isPast && !isGameDay;

    if (isGameDay) btn.classList.add("is-game");
    if (state.trainingSelectedDates.has(iso)) btn.classList.add("is-selected");

    const risk = getTrainingRiskInfo(iso);
    if (risk.cls) btn.classList.add(risk.cls);

    const sessInfo = state.trainingSessionsByDate?.[iso];
    const sessType = sessInfo?.session?.type;
    const isUserSet = !!sessInfo?.is_user_set;
    const tags = [];
    if (isGameDay) tags.push('<span class="training-chip is-game">GAME</span>');
    if (risk.row?.is_back_to_back) tags.push('<span class="training-chip is-b2b">B2B</span>');
    if (!isGameDay && sessInfo) tags.push(`<span class="training-chip ${isUserSet ? "is-user" : "is-auto"}">${isUserSet ? "USER" : "AUTO"}</span>`);

    const sessionLine = sessInfo ? `${trainingTypeLabel(sessType)}${risk.score >= 65 ? " · 리스크 높음" : ""}` : "";
    const riskWidth = Math.max(4, Math.round(risk.score));

    btn.innerHTML = `
      <div class="training-day-top">
        <div>
          <div class="training-day-date">${label}</div>
          <div class="training-day-note">${gameOpp ? `vs ${gameOpp}` : ""}</div>
        </div>
        <div class="training-day-tags">${tags.join("")}</div>
      </div>
      <div class="training-day-sub">${!gameOpp ? sessionLine : "경기일 · 훈련 비활성"}</div>
      <div class="training-risk-track"><div class="training-risk-bar" style="width:${riskWidth}%;"></div></div>
    `;

    if (!selectable) {
      btn.disabled = true;
    } else {
      btn.addEventListener("click", () => {
        if (state.trainingSelectedDates.has(iso)) state.trainingSelectedDates.delete(iso);
        else state.trainingSelectedDates.add(iso);
        renderTrainingCalendar();
        updateTrainingSelectionSummary();
      });
    }

    container.appendChild(btn);
  });
}

function optionsHtml(list, fallback = []) {
  const merged = [...new Set([...(list || []), ...fallback])];
  return merged.map((x) => `<option value="${x}">${x}</option>`).join("");
}

async function renderTrainingDetail(type) {
  const selected = [...state.trainingSelectedDates].sort();
  if (!selected.length) {
    els.trainingDetailPanel.innerHTML = '<p class="empty-copy">적용할 날짜를 먼저 선택하세요.</p>';
    return;
  }

  const baseSession = {
    type,
    offense_scheme_key: null,
    defense_scheme_key: null,
    participant_pids: [],
    non_participant_type: "RECOVERY"
  };

  const offSchemes = state.trainingFamiliarity.offense.map((x) => x.scheme_key);
  const defSchemes = state.trainingFamiliarity.defense.map((x) => x.scheme_key);

  if (type === "OFF_TACTICS") baseSession.offense_scheme_key = offSchemes[0] || "PACE_5OUT";
  if (type === "DEF_TACTICS") baseSession.defense_scheme_key = defSchemes[0] || "MAN_TO_MAN";
  if (type === "FILM") {
    baseSession.offense_scheme_key = offSchemes[0] || "PACE_5OUT";
    baseSession.defense_scheme_key = defSchemes[0] || "MAN_TO_MAN";
  }
  if (type === "SCRIMMAGE") {
    baseSession.participant_pids = state.trainingRoster.slice(0, 10).map((r) => String(r.player_id));
    baseSession.non_participant_type = "RECOVERY";
  }

  state.trainingDraftSession = baseSession;

  const firstDate = selected[0];
  const preview = await fetchJson(`/api/practice/team/${encodeURIComponent(state.selectedTeamId)}/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ season_year: null, date_iso: firstDate, ...baseSession })
  }).catch(() => null);

  const famRows = (type === "OFF_TACTICS" ? state.trainingFamiliarity.offense : type === "DEF_TACTICS" ? state.trainingFamiliarity.defense : []);
  const famHtml = famRows.length
    ? `<ul class="kv-list">${famRows.map((r) => `<li>${r.scheme_key}: ${Math.round(Number(r.value || 0))}</li>`).join("")}</ul>`
    : '<p class="empty-copy">숙련도 데이터가 없습니다.</p>';

  let extra = "";
  if (type === "OFF_TACTICS") {
    extra = `<div class="training-inline-row"><label>공격 스킴</label><select id="training-off-scheme">${optionsHtml(offSchemes, ["PACE_5OUT"])}</select></div>${famHtml}`;
  } else if (type === "DEF_TACTICS") {
    extra = `<div class="training-inline-row"><label>수비 스킴</label><select id="training-def-scheme">${optionsHtml(defSchemes, ["MAN_TO_MAN"])}</select></div>${famHtml}`;
  } else if (type === "SCRIMMAGE") {
    const rosterRows = state.trainingRoster.map((r) => `
      <tr>
        <td>${r.name || r.player_id}</td>
        <td>${Math.round(Number((r.short_term_stamina ?? 1) * 100))}%</td>
        <td>${Math.round(Number((r.long_term_stamina ?? 1) * 100))}%</td>
        <td>${Math.round(Number(r.sharpness ?? 50))}</td>
      </tr>
    `).join("");
    extra = `
      <p>5대5 라인업(참가자 PID 콤마 구분, 기본 10명):</p>
      <textarea id="training-scrimmage-pids" rows="3" style="width:100%;">${baseSession.participant_pids.join(",")}</textarea>
      <table class="training-player-table">
        <thead><tr><th>선수</th><th>단기 체력</th><th>장기 체력</th><th>샤프니스</th></tr></thead>
        <tbody>${rosterRows}</tbody>
      </table>
    `;
  }

  const prevText = preview
    ? `<ul class="kv-list"><li>공격 익숙도 gain: ${preview.preview?.familiarity_gain?.offense_gain ?? 0}</li><li>수비 익숙도 gain: ${preview.preview?.familiarity_gain?.defense_gain ?? 0}</li><li>평균 샤프니스 delta: ${Object.values(preview.preview?.intensity_mult_by_pid || {}).length ? (Object.values(preview.preview.intensity_mult_by_pid).reduce((a, x) => a + Number(x.sharpness_delta || 0), 0) / Object.values(preview.preview.intensity_mult_by_pid).length).toFixed(2) : "0.00"}</li></ul>`
    : '<p class="empty-copy">효과 프리뷰를 불러오지 못했습니다.</p>';

  const selectedRiskStats = selected.reduce((acc, iso) => {
    const r = state.trainingRiskCalendarByDate?.[iso] || {};
    acc.highRisk += Number(r.high_risk_player_count || 0);
    acc.out += Number(r.out_player_count || 0);
    return acc;
  }, { highRisk: 0, out: 0 });

  const warningText = selectedRiskStats.highRisk > 0 || selectedRiskStats.out > 0
    ? `<p class="training-detail-meta">주의: 선택 구간에 고위험 누적 ${selectedRiskStats.highRisk}명, 결장 누적 ${selectedRiskStats.out}명이 포함됩니다.</p>`
    : '<p class="training-detail-meta">선택 구간은 상대적으로 안정적입니다.</p>';

  els.trainingDetailPanel.innerHTML = `
    <div class="training-detail-grid">
      <h3>${trainingTypeLabel(type)} 훈련 설정</h3>
      <p class="training-detail-meta">선택 날짜: ${selected.join(", ")}</p>
      ${warningText}
      ${extra}
      <div><strong>연습 효과 프리뷰</strong>${prevText}</div>
      <div class="training-inline-row"><button id="training-apply-btn" class="btn btn-primary" type="button">선택 날짜에 적용</button></div>
    </div>
  `;

  const offSel = document.getElementById("training-off-scheme");
  const defSel = document.getElementById("training-def-scheme");
  const scrimmagePids = document.getElementById("training-scrimmage-pids");
  if (offSel) offSel.addEventListener("change", () => { state.trainingDraftSession.offense_scheme_key = offSel.value; });
  if (defSel) defSel.addEventListener("change", () => { state.trainingDraftSession.defense_scheme_key = defSel.value; });
  if (scrimmagePids) scrimmagePids.addEventListener("input", () => {
    state.trainingDraftSession.participant_pids = scrimmagePids.value.split(",").map((x) => x.trim()).filter(Boolean);
  });

  const applyBtn = document.getElementById("training-apply-btn");
  applyBtn.addEventListener("click", async () => {
    const dates = [...state.trainingSelectedDates];
    await Promise.all(dates.map((dateIso) => fetchJson("/api/practice/team/session/set", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        team_id: state.selectedTeamId,
        date_iso: dateIso,
        ...state.trainingDraftSession
      })
    })));
    await loadTrainingData();
    renderTrainingKpiBar();
    renderTrainingCalendar();
    updateTrainingSelectionSummary();
    alert(`${dates.length}일에 훈련을 적용했습니다.`);
  });
}

async function showTrainingScreen() {
  if (!state.selectedTeamId) {
    alert("먼저 팀을 선택해주세요.");
    return;
  }
  setLoading(true, "훈련 화면 데이터를 불러오는 중...");
  try {
    state.trainingSelectedDates = new Set();
    await loadTrainingData();
    renderTrainingKpiBar();
    renderTrainingCalendar();
    updateTrainingSelectionSummary();
    els.trainingDetailPanel.innerHTML = '<p class="empty-copy">캘린더에서 날짜를 선택하고 훈련 버튼을 눌러 세부 설정을 확인하세요.</p>';
    activateScreen(els.trainingScreen);
  } finally {
    setLoading(false);
  }
}

async function loadSavesStatus() {
  try {
    const saveResult = await fetchJson("/api/game/saves");
    const slots = saveResult.slots || [];
    if (slots.length > 0) {
      state.lastSaveSlotId = slots[0].slot_id;
      els.continueBtn.disabled = false;
      els.continueBtn.setAttribute("aria-disabled", "false");
      els.continueHint.textContent = `저장된 게임 ${slots.length}개를 찾았습니다.`;
    } else {
      els.continueBtn.disabled = true;
      els.continueBtn.setAttribute("aria-disabled", "true");
      els.continueHint.textContent = "저장된 게임이 없습니다. 새 게임으로 시작해주세요.";
    }
  } catch (e) {
    els.continueBtn.disabled = true;
    els.continueBtn.setAttribute("aria-disabled", "true");
    els.continueHint.textContent = `저장 상태 확인 실패: ${e.message}`;
  }
}

async function renderTeams() {
  const result = await fetchJson("/api/teams");
  const teams = (result || []).slice(0, 30);
  els.teamGrid.innerHTML = "";

  const conferenceOrder = ["East", "West"];
  const divisionOrder = {
    East: ["Atlantic", "Central", "Southeast"],
    West: ["Northwest", "Pacific", "Southwest"],
  };

  const grouped = { East: {}, West: {} };
  teams.forEach((team) => {
    const conference = team.conference === "West" ? "West" : "East";
    const division = String(team.division || "");
    if (!grouped[conference][division]) grouped[conference][division] = [];
    grouped[conference][division].push(team);
  });

  conferenceOrder.forEach((conference) => {
    const conferenceSection = document.createElement("section");
    conferenceSection.className = "team-conference";

    const conferenceTitle = document.createElement("h3");
    conferenceTitle.className = "team-conference-title";
    conferenceTitle.textContent = conference === "East" ? "동부 컨퍼런스" : "서부 컨퍼런스";
    conferenceSection.appendChild(conferenceTitle);

    (divisionOrder[conference] || Object.keys(grouped[conference])).forEach((division) => {
      const divisionTeams = (grouped[conference][division] || []).sort((a, b) => {
        const aName = TEAM_FULL_NAMES[String(a.team_id || "").toUpperCase()] || String(a.team_id || "");
        const bName = TEAM_FULL_NAMES[String(b.team_id || "").toUpperCase()] || String(b.team_id || "");
        return aName.localeCompare(bName);
      });
      if (!divisionTeams.length) return;

      const divisionSection = document.createElement("div");
      divisionSection.className = "team-division";
      divisionSection.innerHTML = `<h4 class="team-division-title">${division}</h4>`;

      const divisionGrid = document.createElement("div");
      divisionGrid.className = "team-division-grid";

      divisionTeams.forEach((team) => {
        const id = String(team.team_id || "").toUpperCase();
        const fullName = TEAM_FULL_NAMES[id] || id;
        const card = document.createElement("button");
        card.className = "team-card";
        card.type = "button";
        card.innerHTML = `<strong>${fullName}</strong><small>${conference} · ${division}</small>`;
        card.addEventListener("click", () => {
          confirmTeamSelection(id, fullName).catch((e) => alert(e.message));
        });
        divisionGrid.appendChild(card);
      });

      divisionSection.appendChild(divisionGrid);
      conferenceSection.appendChild(divisionSection);
    });

    els.teamGrid.appendChild(conferenceSection);
  });
}


function formatSignedDiff(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "0.0";
  if (Math.abs(n) < 0.05) return "0.0";
  return `${n > 0 ? "+" : ""}${n.toFixed(1)}`;
}

function renderStandingsRows(tbody, rows) {
  tbody.innerHTML = "";
  (rows || []).forEach((row) => {
    const tr = document.createElement("tr");
    const teamId = String(row?.team_id || "").toUpperCase();
    const diff = Number(row?.diff || 0);
    const diffClass = diff > 0 ? "standings-diff-positive" : diff < 0 ? "standings-diff-negative" : "";
    tr.innerHTML = `
      <td>${row?.rank ?? "-"}</td>
      <td class="standings-team-cell">${TEAM_FULL_NAMES[teamId] || teamId || "-"}</td>
      <td>${row?.wins ?? 0}</td>
      <td>${row?.losses ?? 0}</td>
      <td>${row?.pct || ".000"}</td>
      <td>${row?.gb_display ?? "-"}</td>
      <td>${row?.home || "0-0"}</td>
      <td>${row?.away || "0-0"}</td>
      <td>${row?.div || "0-0"}</td>
      <td>${row?.conf || "0-0"}</td>
      <td>${Number(row?.ppg || 0).toFixed(1)}</td>
      <td>${Number(row?.opp_ppg || 0).toFixed(1)}</td>
      <td class="${diffClass}">${formatSignedDiff(row?.diff)}</td>
      <td>${row?.strk || "-"}</td>
      <td>${row?.l10 || "0-0"}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function showStandingsScreen() {
  setLoading(true, "순위 데이터를 불러오는 중입니다...");
  try {
    const payload = await fetchJson("/api/standings/table");
    state.standingsData = payload;
    renderStandingsRows(els.standingsEastBody, payload?.east || []);
    renderStandingsRows(els.standingsWestBody, payload?.west || []);
    activateScreen(els.standingsScreen);
  } finally {
    setLoading(false);
  }
}

async function createNewGame() {
  setLoading(true, "새 게임을 준비하는 중입니다. 엑셀 로스터를 DB로 부팅하고 있습니다...");
  const slotId = `slot_${new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14)}`;
  try {
    const response = await fetchJson("/api/game/new", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        slot_name: `새 게임 ${new Date().toLocaleString("ko-KR")}`,
        slot_id: slotId,
        overwrite_if_exists: false
      })
    });

    state.lastSaveSlotId = response.slot_id;
    await renderTeams();
    showTeamSelection();
  } finally {
    setLoading(false);
  }
}

async function continueGame() {
  if (!state.lastSaveSlotId) return;
  setLoading(true, "저장된 게임을 불러오는 중입니다...");
  try {
    const loaded = await fetchJson("/api/game/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slot_id: state.lastSaveSlotId, strict: true })
    });

    const savedTeamId = String(loaded.user_team_id || "").toUpperCase();
    if (savedTeamId) {
      state.selectedTeamId = savedTeamId;
      state.selectedTeamName = TEAM_FULL_NAMES[savedTeamId] || savedTeamId;
      showMainScreen();
      return;
    }

    await renderTeams();
    showTeamSelection();
  } finally {
    setLoading(false);
  }
}


function tacticsSchemeLabel(schemes, key) {
  const found = (schemes || []).find((x) => x.key === key);
  return found ? found.label : key;
}

function deepClone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

function getDefenseRolesForScheme(key) {
  return TACTICS_DEFENSE_ROLE_BY_SCHEME[key] || TACTICS_DEFENSE_ROLE_BY_SCHEME.Drop;
}

function buildTacticsDraft(roster) {
  const names = (roster || []).map((r) => ({ id: String(r.player_id || ""), name: String(r.name || r.player_id || "-") })).filter((x) => x.id);
  const starters = [];
  const rotation = [];
  for (let i = 0; i < 5; i += 1) {
    const p = names[i];
    starters.push({
      pid: p?.id || "",
      offenseRole: TACTICS_OFFENSE_ROLES[i % TACTICS_OFFENSE_ROLES.length],
      defenseRole: getDefenseRolesForScheme("Drop")[i],
      minutes: 32 - i
    });
  }
  for (let i = 5; i < 10; i += 1) {
    const p = names[i];
    rotation.push({
      pid: p?.id || "",
      offenseRole: TACTICS_OFFENSE_ROLES[i % TACTICS_OFFENSE_ROLES.length],
      defenseRole: getDefenseRolesForScheme("Drop")[i - 5],
      minutes: 18 - (i - 5)
    });
  }
  return { offenseScheme: "Spread_HeavyPnR", defenseScheme: "Drop", starters, rotation };
}

function renderSchemeOptions(kind) {
  const isOff = kind === "offense";
  const optionsEl = isOff ? els.tacticsOffenseOptions : els.tacticsDefenseOptions;
  const list = isOff ? TACTICS_OFFENSE_SCHEMES : TACTICS_DEFENSE_SCHEMES;
  const selected = isOff ? state.tacticsDraft.offenseScheme : state.tacticsDraft.defenseScheme;
  optionsEl.innerHTML = list.map((s) => `<button type="button" data-key="${s.key}"><span>${s.label}</span>${s.key === selected ? '<strong>선택됨</strong>' : ''}</button>`).join("");
  optionsEl.querySelectorAll("button[data-key]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (isOff) state.tacticsDraft.offenseScheme = btn.dataset.key;
      else {
        state.tacticsDraft.defenseScheme = btn.dataset.key;
        const defRoles = getDefenseRolesForScheme(btn.dataset.key);
        [...state.tacticsDraft.starters, ...state.tacticsDraft.rotation].forEach((row, idx) => {
          if (!defRoles.includes(row.defenseRole)) row.defenseRole = defRoles[idx % defRoles.length];
        });
      }
      optionsEl.classList.add("hidden");
      renderTacticsScreen();
    });
  });
}

function rosterNameByPid(pid) {
  const row = (state.rosterRows || []).find((x) => String(x.player_id) === String(pid));
  return row ? String(row.name || row.player_id) : "-";
}

function minutesNum(v) {
  return Math.max(0, Math.min(48, Number(v || 0)));
}

function computeTacticsInsights() {
  const starters = state.tacticsDraft?.starters || [];
  const rotation = state.tacticsDraft?.rotation || [];
  const allRows = [...starters, ...rotation];
  const starterMinutes = starters.reduce((sum, row) => sum + minutesNum(row.minutes), 0);
  const benchMinutes = rotation.reduce((sum, row) => sum + minutesNum(row.minutes), 0);
  const totalMinutes = starterMinutes + benchMinutes;
  const uniqueOffRoles = new Set(allRows.map((x) => x.offenseRole).filter(Boolean));
  const roleDiversity = allRows.length ? (uniqueOffRoles.size / allRows.length) : 0;
  const defenseRoles = allRows.map((x) => x.defenseRole).filter(Boolean);
  const seen = new Set();
  const dup = new Set();
  defenseRoles.forEach((role) => {
    if (seen.has(role)) dup.add(role);
    seen.add(role);
  });
  const alerts = [];
  const delta = 240 - totalMinutes;
  if (delta !== 0) alerts.push({ level: 'warn', text: `총 출전시간이 ${delta > 0 ? `${delta}분 부족` : `${Math.abs(delta)}분 초과`} 상태입니다.` });
  if (dup.size) alerts.push({ level: 'error', text: `수비 역할 중복 감지: ${[...dup].join(', ')}` });
  if (starterMinutes < 145) alerts.push({ level: 'warn', text: '선발 출전시간 총합이 낮아 경기 초반 주도권이 약해질 수 있습니다.' });
  if (roleDiversity < 0.5) alerts.push({ level: 'info', text: '공격 역할이 편중되어 있습니다. 역할 다양성을 높이면 대응력이 좋아집니다.' });
  if (!alerts.length) alerts.push({ level: 'ok', text: '현재 전술은 밸런스가 안정적입니다. 세부 매치업 조정만 진행하세요.' });
  return {
    starterMinutes,
    benchMinutes,
    totalMinutes,
    roleDiversity,
    starterShare: totalMinutes ? (starterMinutes / totalMinutes) : 0,
    benchShare: totalMinutes ? (benchMinutes / totalMinutes) : 0,
    alerts,
  };
}

function buildLineupRowHtml(group, idx, row, defenseRoles) {
  const players = state.rosterRows || [];
  const playerOptions = ['<option value="">- 선택 -</option>', ...players.map((r) => `<option value="${r.player_id}" ${String(r.player_id) === String(row.pid) ? "selected" : ""}>${r.name || r.player_id}</option>`)].join("");
  const offOptions = TACTICS_OFFENSE_ROLES.map((role) => `<option value="${role}" ${role === row.offenseRole ? "selected" : ""}>${role}</option>`).join("");
  const defOptions = defenseRoles.map((role) => `<option value="${role}" ${role === row.defenseRole ? "selected" : ""}>${role}</option>`).join("");
  return `
    <div class="tactics-lineup-row" data-group="${group}" data-idx="${idx}">
      <select data-field="pid">${playerOptions}</select>
      <select data-field="offenseRole">${offOptions}</select>
      <select data-field="defenseRole">${defOptions}</select>
      <input data-field="minutes" type="number" min="0" max="48" value="${minutesNum(row.minutes)}" />
    </div>
  `;
}

function validateDefenseRoleUnique(changedEl, nextValue) {
  const all = [...document.querySelectorAll('.tactics-lineup-row select[data-field="defenseRole"]')];
  const dup = all.find((el) => el !== changedEl && el.value === nextValue);
  return !dup;
}

function updateTacticsDirtyState() {
  const dirty = JSON.stringify(state.tacticsDraft || {}) !== JSON.stringify(state.tacticsSnapshot || {});
  els.tacticsDirtyBadge.textContent = dirty ? '변경됨' : '초기 상태';
  els.tacticsDirtyBadge.classList.toggle('is-dirty', dirty);
}

function bindLineupEvents() {
  document.querySelectorAll('.tactics-lineup-row').forEach((rowEl) => {
    const group = rowEl.dataset.group;
    const idx = Number(rowEl.dataset.idx || 0);
    rowEl.querySelectorAll('select, input').forEach((control) => {
      control.addEventListener('change', () => {
        const field = control.dataset.field;
        const target = group === 'starters' ? state.tacticsDraft.starters[idx] : state.tacticsDraft.rotation[idx];
        if (!target || !field) return;
        if (field === 'defenseRole') {
          if (!validateDefenseRoleUnique(control, control.value)) {
            rowEl.classList.add('is-error');
            setTimeout(() => rowEl.classList.remove('is-error'), 1000);
            renderTacticsScreen();
            return;
          }
          target.defenseRole = control.value;
          renderTacticsScreen();
          return;
        }
        if (field === 'minutes') target.minutes = minutesNum(control.value);
        else target[field] = control.value;
        rowEl.classList.add('is-updated');
        setTimeout(() => rowEl.classList.remove('is-updated'), 700);
        renderTacticsScreen();
      });
    });
  });
}

function renderTacticsRosterList() {
  els.tacticsRosterList.innerHTML = (state.rosterRows || []).length
    ? state.rosterRows.map((r) => `<div class="tactics-roster-item">${r.name || r.player_id}</div>`).join("")
    : '<p class="empty-copy">로스터 데이터가 없습니다.</p>';
}

function renderTacticsInsights() {
  const insight = computeTacticsInsights();
  els.tacticsKpiTotalMinutes.textContent = String(insight.totalMinutes);
  els.tacticsKpiTotalCaption.textContent = insight.totalMinutes === 240 ? '목표 240분 달성' : `목표 대비 ${240 - insight.totalMinutes > 0 ? `${240 - insight.totalMinutes}분 부족` : `${Math.abs(240 - insight.totalMinutes)}분 초과`}`;
  els.tacticsKpiTotalCaption.className = insight.totalMinutes === 240 ? 'tactics-kpi-ok' : 'tactics-kpi-warn';
  els.tacticsKpiStarterMinutes.textContent = (insight.starterMinutes / 5).toFixed(1);
  els.tacticsKpiBenchMinutes.textContent = (insight.benchMinutes / 5).toFixed(1);
  els.tacticsKpiRoleDiversity.textContent = `${Math.round(insight.roleDiversity * 100)}%`;
  const starterShare = Math.round(insight.starterShare * 100);
  const benchShare = Math.round(insight.benchShare * 100);
  els.tacticsStarterShare.textContent = `${starterShare}%`;
  els.tacticsBenchShare.textContent = `${benchShare}%`;
  els.tacticsStarterShareBar.style.width = `${starterShare}%`;
  els.tacticsBenchShareBar.style.width = `${benchShare}%`;
  els.tacticsAlertFeed.innerHTML = insight.alerts
    .map((a) => `<li class="tactics-alert-item ${a.level}">${a.text}</li>`)
    .join('');
}

function renderTacticsScreen() {
  if (!state.tacticsDraft) return;
  const defRoles = getDefenseRolesForScheme(state.tacticsDraft.defenseScheme);
  const teamLabel = state.selectedTeamName || TEAM_FULL_NAMES[state.selectedTeamId] || '선택 팀';
  els.tacticsTeamContext.textContent = `${teamLabel} 전술 디렉팅 보드`;
  els.tacticsOffenseCurrent.textContent = `현재: ${tacticsSchemeLabel(TACTICS_OFFENSE_SCHEMES, state.tacticsDraft.offenseScheme)}`;
  els.tacticsDefenseCurrent.textContent = `현재: ${tacticsSchemeLabel(TACTICS_DEFENSE_SCHEMES, state.tacticsDraft.defenseScheme)}`;
  els.tacticsStarters.innerHTML = state.tacticsDraft.starters.map((r, i) => buildLineupRowHtml('starters', i, r, defRoles)).join('');
  els.tacticsRotation.innerHTML = state.tacticsDraft.rotation.map((r, i) => buildLineupRowHtml('rotation', i, r, defRoles)).join('');
  renderTacticsRosterList();
  renderTacticsInsights();
  updateTacticsDirtyState();
  bindLineupEvents();
}


function renderMedicalEmpty(tbody, colSpan, text) {
  tbody.innerHTML = `<tr><td colspan="${colSpan}" class="schedule-empty">${text}</td></tr>`;
}

function riskTierClass(tier) {
  const t = String(tier || '').toUpperCase();
  if (t === 'HIGH') return 'status-danger';
  if (t === 'MEDIUM') return 'status-warn';
  return 'status-ok';
}

function formatSignedDelta(v) {
  const n = num(v, 0);
  if (!n) return { text: '지난 7일 대비 변동 없음', cls: '' };
  return {
    text: `지난 7일 대비 ${n > 0 ? '+' : ''}${n}`,
    cls: n > 0 ? 'pos' : 'neg',
  };
}

function renderMedicalHero(alerts = {}) {
  const p = alerts?.primary_alert_player;
  const load = alerts?.team_load_context || {};
  const level = String(alerts?.alert_level || 'info').toUpperCase();

  els.medicalAlertLevel.textContent = level;
  els.medicalAlertLevel.className = `medical-alert-badge ${level === 'CRITICAL' ? 'level-critical' : level === 'WARN' ? 'level-warn' : ''}`;

  if (!p) {
    els.medicalAlertText.textContent = '현재 주요 경고가 없습니다.';
    els.medicalAlertMeta.textContent = `다음 7일 경기 ${num(load?.next_7d_game_count, 0)}회 · B2B ${num(load?.next_7d_back_to_back_count, 0)}회`;
    return;
  }

  els.medicalAlertText.textContent = `${p.name || '-'} 리스크 ${p.risk_tier || '-'} (${num(p.risk_score, 0)})`;
  els.medicalAlertMeta.textContent = `${p.injury_status || '-'} · OUT ${p.out_until_date || '-'} / RETURNING ${p.returning_until_date || '-'} · 다음 7일 ${num(load?.next_7d_game_count, 0)}경기 (B2B ${num(load?.next_7d_back_to_back_count, 0)}회)`;
}

function renderMedicalTimeline(playerName, events) {
  els.medicalTimelineTitle.textContent = playerName ? `${playerName} 최근 부상 타임라인` : '워치리스트에서 선수를 선택하세요.';
  if (!events || !events.length) {
    els.medicalTimelineList.innerHTML = '<p class="empty-copy">최근 이벤트가 없습니다.</p>';
    return;
  }
  els.medicalTimelineList.innerHTML = events.map((e) => `
    <article class="medical-timeline-item">
      <p><strong>${e.date || '-'}</strong> · ${e.context || '-'}</p>
      <p>${e.body_part || '-'} / ${e.injury_type || '-'} / severity ${num(e.severity, 0)}</p>
      <p>OUT ~ ${e.out_until_date || '-'} · RETURNING ~ ${e.returning_until_date || '-'}</p>
    </article>
  `).join('');
}

function renderMedicalActionRecommendations(payload, playerName) {
  const items = payload?.recommendations || [];
  if (!items.length) {
    els.medicalActionList.innerHTML = '<p class="empty-copy">권고안이 없습니다.</p>';
    return;
  }
  els.medicalActionList.innerHTML = items.map((it) => {
    const d = it.expected_delta || {};
    const riskDelta = num(d.risk_score, 0);
    const stDelta = num(d.short_term_fatigue, 0);
    const ltDelta = num(d.long_term_fatigue, 0);
    const sharpDelta = num(d.sharpness, 0);
    return `
      <article class="medical-action-item">
        <strong>${it.label || it.action_id || '-'}</strong>
        <p>${playerName || '-'} 예상 변화 · Risk ${riskDelta > 0 ? '+' : ''}${riskDelta} · ST ${stDelta > 0 ? '+' : ''}${stDelta.toFixed(3)} · LT ${ltDelta > 0 ? '+' : ''}${ltDelta.toFixed(3)} · Sharp ${sharpDelta > 0 ? '+' : ''}${sharpDelta.toFixed(2)}</p>
      </article>
    `;
  }).join('');
}

function renderMedicalRiskCalendar(payload) {
  const days = payload?.days || [];
  if (!days.length) {
    els.medicalRiskCalendarList.innerHTML = '<p class="empty-copy">캘린더 데이터가 없습니다.</p>';
    return;
  }
  els.medicalRiskCalendarList.innerHTML = days.map((d) => `
    <article class="medical-day-card ${d.is_game_day ? 'is-game' : ''} ${d.is_back_to_back ? 'is-b2b' : ''}">
      <div class="date">${d.date || '-'}</div>
      <div class="meta">${d.is_game_day ? `vs/@ ${d.opponent_team_id || '-'}` : 'No Game'} · ${d.practice_session_type || '훈련 미정'}</div>
      <div class="badges">
        <span class="badge">HIGH ${num(d.high_risk_player_count, 0)}</span>
        <span class="badge">OUT ${num(d.out_player_count, 0)}</span>
        <span class="badge">RET ${num(d.returning_player_count, 0)}</span>
        <span class="badge">EVT ${num(d.injury_event_count, 0)}</span>
      </div>
    </article>
  `).join('');
}

async function loadMedicalPlayerContext(playerId, playerName) {
  if (!playerId || !state.selectedTeamId) return;
  setLoading(true, '선수 메디컬 컨텍스트를 불러오는 중...');
  try {
    const [timelinePayload, actionPayload] = await Promise.all([
      fetchJson(`/api/medical/team/${encodeURIComponent(state.selectedTeamId)}/players/${encodeURIComponent(playerId)}/timeline`),
      fetchJson(`/api/medical/team/${encodeURIComponent(state.selectedTeamId)}/players/${encodeURIComponent(playerId)}/action-recommendations`),
    ]);
    const resolvedName = playerName || timelinePayload?.player?.name || '-';
    renderMedicalTimeline(resolvedName, timelinePayload?.timeline?.events || []);
    renderMedicalActionRecommendations(actionPayload, resolvedName);
  } catch (e) {
    renderMedicalTimeline(playerName || '-', []);
    els.medicalActionList.innerHTML = `<p class="empty-copy">권고안 로딩 실패: ${e.message}</p>`;
  } finally {
    setLoading(false);
  }
}

function renderMedicalOverview(overview, alerts) {
  const summary = overview?.summary || {};
  const statusCounts = summary?.injury_status_counts || {};
  const riskCounts = summary?.risk_tier_counts || {};
  const watch = overview?.watchlists || {};
  const delta = alerts?.kpi_delta_7d || {};

  els.medicalAsOf.textContent = `기준일 ${overview?.as_of_date || '-'}`;
  els.medicalRosterCount.textContent = num(summary?.roster_count, 0);
  els.medicalOutCount.textContent = num(statusCounts?.OUT, 0);
  els.medicalReturningCount.textContent = `복귀 관리: ${num(statusCounts?.RETURNING, 0)}명`;
  els.medicalHighRiskCount.textContent = num(riskCounts?.HIGH, 0);
  els.medicalHealthFrustrationCount.textContent = num(summary?.health_frustration?.high_count, 0);

  const rosterDelta = formatSignedDelta(0);
  const outDelta = formatSignedDelta(delta?.out_count_delta);
  const hrDelta = formatSignedDelta(delta?.high_risk_count_delta);
  const healthDelta = formatSignedDelta(delta?.health_high_count_delta);
  els.medicalRosterDelta.textContent = rosterDelta.text;
  els.medicalOutDelta.textContent = outDelta.text;
  els.medicalOutDelta.className = `medical-delta ${outDelta.cls}`;
  els.medicalHighRiskDelta.textContent = hrDelta.text;
  els.medicalHighRiskDelta.className = `medical-delta ${hrDelta.cls}`;
  els.medicalHealthDelta.textContent = healthDelta.text;
  els.medicalHealthDelta.className = `medical-delta ${healthDelta.cls}`;

  const riskRows = watch?.highest_risk || [];
  if (!riskRows.length) {
    renderMedicalEmpty(els.medicalRiskBody, 6, '위험 데이터가 없습니다.');
  } else {
    els.medicalRiskBody.innerHTML = '';
    riskRows.forEach((r) => {
      const tr = document.createElement('tr');
      tr.className = 'roster-row';
      const riskScore = num(r.risk_score, 0);
      const reinjuryTotal = Object.values(r?.risk_inputs?.reinjury_count || {}).reduce((acc, v) => acc + num(v, 0), 0);
      tr.innerHTML = `
        <td>${r.name || '-'} <span class="schedule-opponent-name">${r.pos || '-'} · ${num(r.age, 0)}세</span></td>
        <td><span class="status-line ${riskTierClass(r.injury_status)}">${r.injury_status || '-'}</span></td>
        <td>
          <strong class="${riskTierClass(r.risk_tier)}">${r.risk_tier || '-'} (${riskScore})</strong>
          <div class="medical-risk-meter"><span style="width:${clamp(riskScore, 0, 100)}%"></span></div>
        </td>
        <td>${formatPercent(1 - num(r.condition?.short_term_fatigue, 0))} / ${formatPercent(1 - num(r.condition?.long_term_fatigue, 0))}</td>
        <td>${Math.round(num(r.condition?.sharpness, 0))}</td>
        <td>${reinjuryTotal}</td>
      `;
      tr.addEventListener('click', () => {
        state.medicalSelectedPlayerId = r.player_id;
        loadMedicalPlayerContext(r.player_id, r.name).catch((e) => alert(e.message));
      });
      els.medicalRiskBody.appendChild(tr);
    });
  }

  const injuredRows = watch?.currently_unavailable || [];
  els.medicalInjuredBody.innerHTML = injuredRows.length ? injuredRows.map((r) => `
    <tr>
      <td>${r.name || '-'} <span class="schedule-opponent-name">${r.pos || '-'}</span></td>
      <td><span class="status-line ${riskTierClass(r.recovery_status)}">${r.recovery_status || '-'}</span></td>
      <td>${r.injury_current?.body_part || '-'} (${r.injury_current?.injury_type || '-'})</td>
      <td>${r.injury_current?.out_until_date || '-'} ~ ${r.injury_current?.returning_until_date || '-'}</td>
    </tr>
  `).join('') : renderEmptyScheduleRow(4, '결장/복귀 관리 대상이 없습니다.');

  const healthRows = watch?.health_frustration_high || [];
  els.medicalHealthBody.innerHTML = healthRows.length ? healthRows.map((r) => `
    <tr>
      <td>${r.name || '-'} <span class="schedule-opponent-name">${r.pos || '-'}</span></td>
      <td>${num(r.health_frustration, 2)}</td>
      <td>${num(r.trade_request_level, 0)}</td>
      <td>${num(r.escalation_health, 0)}</td>
    </tr>
  `).join('') : renderEmptyScheduleRow(4, '건강 불만 상위 선수가 없습니다.');
}

async function showMedicalScreen() {
  if (!state.selectedTeamId) {
    alert('먼저 팀을 선택해주세요.');
    return;
  }
  setLoading(true, '메디컬 센터 데이터를 불러오는 중...');
  try {
    const [overview, alerts, calendar] = await Promise.all([
      fetchJson(`/api/medical/team/${encodeURIComponent(state.selectedTeamId)}/overview`),
      fetchJson(`/api/medical/team/${encodeURIComponent(state.selectedTeamId)}/alerts`).catch(() => ({})),
      fetchJson(`/api/medical/team/${encodeURIComponent(state.selectedTeamId)}/risk-calendar?days=14`).catch(() => ({ days: [] })),
    ]);
    state.medicalOverview = overview;
    const teamName = state.selectedTeamName || TEAM_FULL_NAMES[state.selectedTeamId] || state.selectedTeamId;
    els.medicalTitle.textContent = `${teamName} 메디컬 센터`;

    renderMedicalHero(alerts);
    renderMedicalOverview(overview, alerts);
    renderMedicalRiskCalendar(calendar);

    const primaryPlayerId = alerts?.primary_alert_player?.player_id;
    const primaryPlayerName = alerts?.primary_alert_player?.name;
    els.medicalAlertOpenPlayer.onclick = () => {
      if (!primaryPlayerId) return;
      state.medicalSelectedPlayerId = primaryPlayerId;
      loadMedicalPlayerContext(primaryPlayerId, primaryPlayerName).catch(() => {});
    };
    els.medicalAlertOpenAction.onclick = els.medicalAlertOpenPlayer.onclick;

    const first = primaryPlayerId ? { player_id: primaryPlayerId, name: primaryPlayerName } : (overview?.watchlists?.highest_risk || [])[0];
    if (first?.player_id) {
      state.medicalSelectedPlayerId = first.player_id;
      await loadMedicalPlayerContext(first.player_id, first.name);
    } else {
      renderMedicalTimeline(null, []);
      els.medicalActionList.innerHTML = '<p class="empty-copy">권고안이 없습니다.</p>';
    }

    activateScreen(els.medicalScreen);
  } finally {
    setLoading(false);
  }
}


async function showTacticsScreen() {
  if (!state.selectedTeamId) {
    alert('먼저 팀을 선택해주세요.');
    return;
  }
  setLoading(true, '전술 데이터를 불러오는 중...');
  try {
    const detail = await fetchJson(`/api/team-detail/${encodeURIComponent(state.selectedTeamId)}`);
    state.rosterRows = detail.roster || [];
    if (!state.tacticsDraft) state.tacticsDraft = buildTacticsDraft(state.rosterRows);
    state.tacticsSnapshot = deepClone(state.tacticsDraft);
    renderSchemeOptions('offense');
    renderSchemeOptions('defense');
    renderTacticsScreen();
    activateScreen(els.tacticsScreen);
  } finally {
    setLoading(false);
  }
}

function toggleTacticsOptions(kind) {
  const target = kind === 'offense' ? els.tacticsOffenseOptions : els.tacticsDefenseOptions;
  const other = kind === 'offense' ? els.tacticsDefenseOptions : els.tacticsOffenseOptions;
  other.classList.add('hidden');
  target.classList.toggle('hidden');
}

els.newGameBtn.addEventListener("click", () => createNewGame().catch((e) => alert(e.message)));
els.continueBtn.addEventListener("click", () => continueGame().catch((e) => alert(e.message)));
els.myTeamBtn.addEventListener("click", () => showMyTeamScreen().catch((e) => alert(e.message)));
els.tacticsMenuBtn.addEventListener("click", () => showTacticsScreen().catch((e) => alert(e.message)));
els.nextGameTacticsBtn.addEventListener("click", () => showTacticsScreen().catch((e) => alert(e.message)));
els.scheduleBtn.addEventListener("click", () => showScheduleScreen().catch((e) => alert(e.message)));
els.scheduleBackBtn.addEventListener("click", () => showMainScreen());
els.trainingMenuBtn.addEventListener("click", () => showTrainingScreen().catch((e) => alert(e.message)));
els.tacticsBackBtn.addEventListener("click", () => showMainScreen());

els.tacticsUndoBtn.addEventListener("click", () => {
  if (!state.tacticsSnapshot) return;
  state.tacticsDraft = deepClone(state.tacticsSnapshot);
  renderSchemeOptions('offense');
  renderSchemeOptions('defense');
  renderTacticsScreen();
});
els.tacticsOffenseBtn.addEventListener("click", () => toggleTacticsOptions("offense"));
els.tacticsDefenseBtn.addEventListener("click", () => toggleTacticsOptions("defense"));
els.standingsMenuBtn.addEventListener("click", () => showStandingsScreen().catch((e) => alert(e.message)));
els.collegeMenuBtn.addEventListener("click", () => showCollegeScreen().catch((e) => alert(e.message)));
els.medicalMenuBtn.addEventListener("click", () => showMedicalScreen().catch((e) => alert(e.message)));
els.trainingBackBtn.addEventListener("click", () => showMainScreen());
els.medicalBackBtn.addEventListener("click", () => showMainScreen());
els.standingsBackBtn.addEventListener("click", () => showMainScreen());
els.collegeBackBtn.addEventListener("click", () => showMainScreen());
els.collegeTabTeams.addEventListener("click", () => switchCollegeTab("teams"));
els.collegeTabLeaders.addEventListener("click", () => switchCollegeTab("leaders"));
els.collegeTabBigboard.addEventListener("click", () => switchCollegeTab("bigboard"));
els.collegeTabScouting.addEventListener("click", () => switchCollegeTab("scouting"));
els.collegeLeaderSort.addEventListener("change", () => {
  state.collegeLeadersSort = els.collegeLeaderSort.value || "pts";
  loadCollegeLeaders().catch((e) => alert(e.message));
});
els.collegeExpertSelect.addEventListener("change", () => {
  state.selectedCollegeExpertId = els.collegeExpertSelect.value || "";
  loadCollegeBigboard().catch((e) => alert(e.message));
});
els.collegeScoutSelect.addEventListener("change", () => {
  const selectedScout = (state.scoutingScouts || []).find((x) => x.scout_id === els.collegeScoutSelect.value);
  els.collegeScoutHint.textContent = selectedScout
    ? `전문 분야: ${selectedScout.specialty_key || "-"} · 리포트 생성은 월말 진행 시 반영됩니다.`
    : "스카우터를 선택하면 전문 분야를 확인할 수 있습니다.";
});
els.collegeAssignBtn.addEventListener("click", async () => {
  const scoutId = els.collegeScoutSelect.value;
  const playerId = els.collegeScoutPlayerSelect.value;
  if (!scoutId || !playerId) {
    alert("스카우터와 선수를 선택하세요.");
    return;
  }
  await fetchJson("/api/scouting/assign", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ team_id: state.selectedTeamId, scout_id: scoutId, player_id: playerId, target_kind: "COLLEGE" })
  });
  await loadCollegeScouting();
  alert("스카우터를 배정했습니다. 리포트는 월말 진행 시 생성됩니다.");
});
els.collegeUnassignBtn.addEventListener("click", async () => {
  const scoutId = els.collegeScoutSelect.value;
  if (!scoutId) {
    alert("해제할 스카우터를 선택하세요.");
    return;
  }
  await fetchJson("/api/scouting/unassign", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ team_id: state.selectedTeamId, scout_id: scoutId })
  });
  await loadCollegeScouting();
  alert("배정을 해제했습니다.");
});
els.trainingTypeButtons.querySelectorAll("button[data-training-type]").forEach((btn) => {
  btn.addEventListener("click", () => renderTrainingDetail(btn.dataset.trainingType).catch((e) => alert(e.message)));
});
els.backToMainBtn.addEventListener("click", () => showMainScreen());
els.backToRosterBtn.addEventListener("click", () => activateScreen(els.myTeamScreen));

loadSavesStatus();

window.__debugRenderMyTeam = function __debugRenderMyTeam() {
  state.selectedTeamId = "BOS";
  state.selectedTeamName = "보스턴 셀틱스";
  state.rosterRows = [
    { player_id: "p1", name: "J. Tatum", pos: "SF", age: 27, height_in: 80, weight_lb: 210, salary: 34000000, short_term_stamina: 0.72, long_term_stamina: 0.86, sharpness: 89 },
    { player_id: "p2", name: "J. Brown", pos: "SG", age: 28, height_in: 78, weight_lb: 223, salary: 32000000, short_term_stamina: 0.51, long_term_stamina: 0.78, sharpness: 61 },
    { player_id: "p3", name: "K. Porzingis", pos: "C", age: 29, height_in: 87, weight_lb: 240, salary: 36000000, short_term_stamina: 0.33, long_term_stamina: 0.62, sharpness: 42 }
  ];
  els.myTeamTitle.textContent = `${state.selectedTeamName} 선수단`;
  renderRosterRows(state.rosterRows);
  els.playerDetailTitle.textContent = "선수 상세 정보";
  els.playerDetailContent.innerHTML = "";
  activateScreen(els.myTeamScreen);
};
