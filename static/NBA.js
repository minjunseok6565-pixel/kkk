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

const TEAM_LOGO_BASE_PATH = "/static/team_logos";

const TEAM_BRANDING = {
  ATL: { arenaName: "State Farm Arena", logoFile: "ATL.png" },
  BOS: { arenaName: "TD Garden", logoFile: "BOS.png" },
  BKN: { arenaName: "Barclays Center", logoFile: "BKN.png" },
  CHA: { arenaName: "Spectrum Center", logoFile: "CHA.png" },
  CHI: { arenaName: "United Center", logoFile: "CHI.png" },
  CLE: { arenaName: "Rocket Mortgage FieldHouse", logoFile: "CLE.png" },
  DAL: { arenaName: "American Airlines Center", logoFile: "DAL.png" },
  DEN: { arenaName: "Ball Arena", logoFile: "DEN.png" },
  DET: { arenaName: "Little Caesars Arena", logoFile: "DET.png" },
  GSW: { arenaName: "Chase Center", logoFile: "GSW.png" },
  HOU: { arenaName: "Toyota Center", logoFile: "HOU.png" },
  IND: { arenaName: "Gainbridge Fieldhouse", logoFile: "IND.png" },
  LAC: { arenaName: "Intuit Dome", logoFile: "LAC.png" },
  LAL: { arenaName: "Crypto.com Arena", logoFile: "LAL.png" },
  MEM: { arenaName: "FedExForum", logoFile: "MEM.png" },
  MIA: { arenaName: "Kaseya Center", logoFile: "MIA.png" },
  MIL: { arenaName: "Fiserv Forum", logoFile: "MIL.png" },
  MIN: { arenaName: "Target Center", logoFile: "MIN.png" },
  NOP: { arenaName: "Smoothie King Center", logoFile: "NOP.png" },
  NYK: { arenaName: "Madison Square Garden", logoFile: "NYK.png" },
  OKC: { arenaName: "Paycom Center", logoFile: "OKC.png" },
  ORL: { arenaName: "Kia Center", logoFile: "ORL.png" },
  PHI: { arenaName: "Wells Fargo Center", logoFile: "PHI.png" },
  PHX: { arenaName: "Footprint Center", logoFile: "PHX.png" },
  POR: { arenaName: "Moda Center", logoFile: "POR.png" },
  SAC: { arenaName: "Golden 1 Center", logoFile: "SAC.png" },
  SAS: { arenaName: "Frost Bank Center", logoFile: "SAS.png" },
  TOR: { arenaName: "Scotiabank Arena", logoFile: "TOR.png" },
  UTA: { arenaName: "Delta Center", logoFile: "UTA.png" },
  WAS: { arenaName: "Capital One Arena", logoFile: "WAS.png" },
};

function getTeamBranding(teamId) {
  const id = String(teamId || "").toUpperCase();
  const branding = TEAM_BRANDING[id] || { arenaName: "", logoFile: "" };
  const logoUrl = branding.logoFile ? `${TEAM_LOGO_BASE_PATH}/${branding.logoFile}` : "";
  return { ...branding, logoUrl };
}

function applyTeamLogo(el, teamId) {
  if (!el) return;
  const branding = getTeamBranding(teamId);
  if (branding.logoUrl) {
    el.style.backgroundImage = `url("${branding.logoUrl}")`;
    el.classList.add("team-logo-image");
    el.classList.add("has-team-logo");
    return;
  }
  el.style.backgroundImage = "";
  el.classList.remove("team-logo-image");
  el.classList.remove("has-team-logo");
}

function renderTeamLogoMark(teamId, extraClass = "") {
  const branding = getTeamBranding(teamId);
  const classes = ["team-logo-mark", extraClass, branding.logoUrl ? "has-image" : ""]
    .filter(Boolean)
    .join(" ");
  const style = branding.logoUrl ? ` style="background-image:url('${branding.logoUrl}')"` : "";
  return `<span class="${classes}" aria-hidden="true"${style}></span>`;
}

function getScheduleVenueText(game) {
  const label = String(game?.opponent_label || "").trim().toLowerCase();
  const isAwayGame = label.startsWith("@");
  const venueTeamId = isAwayGame
    ? String(game?.opponent_team_id || "").toUpperCase()
    : String(state.selectedTeamId || "").toUpperCase();
  return getTeamBranding(venueTeamId).arenaName || game?.opponent_team_name || game?.opponent_team_id || "";
}

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
  trainingGameByDate: {},
  trainingRoster: [],
  trainingFamiliarity: { offense: [], defense: [] },
  trainingDraftSession: null,
  trainingActiveType: null,
  standingsData: null,
  tacticsDraft: null,
  medicalOverview: null,
  medicalSelectedPlayerId: null,
  myTeamSortKey: "ovr",
  myTeamFilters: { risk: false, highsalary: false },
  selectedCollegeLeaderPlayerId: null,
  selectedCollegeBigboardExpertId: null,
  collegeBigboardLastTriggerExpertId: null,
  collegeBigboardOverview: [],
  collegeBigboardByExpert: {},
  scoutingScouts: [],
  scoutingReports: [],
  scoutingPlayers: [],
  scoutingPlayerLookup: {},
  scoutingPlayerSearch: "",
  scoutingPlayerSearchStatus: "ALL",
  scoutingPlayerSearchResults: [],
  scoutingPlayerSearchTotal: 0,
  scoutingPlayerSearchOffset: 0,
  scoutingPlayerSearchLimit: 30,
  scoutingPlayerSearchLoading: false,
  scoutingPlayerSearchError: "",
  scoutingPlayerSearchHasSearched: false,
  scoutingPlayerSearchRequestSeq: 0,
  scoutingPlayerSearchDebounceTimer: null,
  scoutingActiveScoutId: "",
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
  teamALogo: document.getElementById("team-a-logo"),
  teamBLogo: document.getElementById("team-b-logo"),
  nextGameArena: document.getElementById("next-game-arena"),
  nextGameDatetime: document.getElementById("next-game-datetime"),
  nextGamePlayBtn: document.getElementById("next-game-play-btn"),
  nextGameQuickBtn: document.getElementById("next-game-quick-btn"),
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
  tacticsHeroSub: document.getElementById("tactics-hero-sub"),
  tacticsKpiTotal: document.getElementById("tactics-kpi-total"),
  tacticsKpiStarters: document.getElementById("tactics-kpi-starters"),
  tacticsKpiRotation: document.getElementById("tactics-kpi-rotation"),
  tacticsKpiDiversity: document.getElementById("tactics-kpi-diversity"),
  tacticsTotalBalance: document.getElementById("tactics-total-balance"),
  tacticsTotalBar: document.getElementById("tactics-total-bar"),
  tacticsTotalMessage: document.getElementById("tactics-total-message"),
  tacticsRoleCoverage: document.getElementById("tactics-role-coverage"),
  tacticsWarningList: document.getElementById("tactics-warning-list"),
  standingsMenuBtn: document.getElementById("standings-menu-btn"),
  trainingScreen: document.getElementById("training-screen"),
  standingsScreen: document.getElementById("standings-screen"),
  collegeScreen: document.getElementById("college-screen"),
  collegeBigboardDetailScreen: document.getElementById("college-bigboard-detail-screen"),
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
  collegeLeaderPosFilter: document.getElementById("college-leader-pos-filter"),
  collegeLeaderTeamFilter: document.getElementById("college-leader-team-filter"),
  collegeLeadersBody: document.getElementById("college-leaders-body"),
  collegeLeaderInsight: document.getElementById("college-leader-insight"),
  collegeLeaderInsightEmpty: document.getElementById("college-leader-insight-empty"),
  collegeBigboardOverview: document.getElementById("college-bigboard-overview"),
  collegeBigboardEmpty: document.getElementById("college-bigboard-empty"),
  collegeBigboardLoading: document.getElementById("college-bigboard-loading"),
  collegeBigboardError: document.getElementById("college-bigboard-error"),
  collegeBigboardBody: document.getElementById("college-bigboard-body"),
  collegeBigboardSummary: document.getElementById("college-bigboard-summary"),
  collegeBigboardDetailBackBtn: document.getElementById("college-bigboard-detail-back-btn"),
  collegeBigboardDetailTitle: document.getElementById("college-bigboard-detail-title"),
  collegeBigboardDetailSummary: document.getElementById("college-bigboard-detail-summary"),
  collegeBigboardDetailBody: document.getElementById("college-bigboard-detail-body"),
  collegeScoutSelect: document.getElementById("college-scout-select"),
  collegeScoutPlayerSelect: document.getElementById("college-scout-player-select"),
  collegeAssignBtn: document.getElementById("college-assign-btn"),
  collegeUnassignBtn: document.getElementById("college-unassign-btn"),
  collegeScoutingFeedback: document.getElementById("college-scouting-feedback"),
  collegeScoutingSummary: document.getElementById("college-scouting-summary"),
  collegeScoutCards: document.getElementById("college-scout-cards"),
  confirmModal: document.getElementById("confirm-modal"),
  confirmModalBackdrop: document.getElementById("confirm-modal-backdrop"),
  confirmModalTitle: document.getElementById("confirm-modal-title"),
  confirmModalBody: document.getElementById("confirm-modal-body"),
  confirmModalOk: document.getElementById("confirm-modal-ok"),
  confirmModalCancel: document.getElementById("confirm-modal-cancel"),
  collegeScoutPlayerModal: document.getElementById("college-scout-player-modal"),
  collegeScoutPlayerModalBackdrop: document.getElementById("college-scout-player-modal-backdrop"),
  collegeScoutPlayerModalClose: document.getElementById("college-scout-player-modal-close"),
  collegeScoutPlayerModalTitle: document.getElementById("college-scout-player-modal-title"),
  collegeScoutPlayerModalMeta: document.getElementById("college-scout-player-modal-meta"),
  collegeScoutPlayerSearch: document.getElementById("college-scout-player-search"),
  collegeScoutPlayerStatus: document.getElementById("college-scout-player-status"),
  collegeScoutPlayerSearchMeta: document.getElementById("college-scout-player-search-meta"),
  collegeScoutPlayerList: document.getElementById("college-scout-player-list"),
  collegeScoutPlayerLoadMore: document.getElementById("college-scout-player-load-more"),
  collegeScoutReportsModal: document.getElementById("college-scout-reports-modal"),
  collegeScoutReportsModalBackdrop: document.getElementById("college-scout-reports-modal-backdrop"),
  collegeScoutReportsModalClose: document.getElementById("college-scout-reports-modal-close"),
  collegeScoutReportsModalTitle: document.getElementById("college-scout-reports-modal-title"),
  collegeScoutReportsModalMeta: document.getElementById("college-scout-reports-modal-meta"),
  collegeScoutReportsList: document.getElementById("college-scout-reports-list"),
  collegeTeamsKpi: document.getElementById("college-teams-kpi"),
  collegeRosterSummary: document.getElementById("college-roster-summary"),
  teamTrainingTabBtn: document.getElementById("team-training-tab-btn"),
  playerTrainingTabBtn: document.getElementById("player-training-tab-btn"),
  trainingCalendarGrid: document.getElementById("training-calendar-grid"),
  trainingTypeButtons: document.getElementById("training-type-buttons"),
  trainingSummaryStrip: document.getElementById("training-summary-strip"),
  trainingContextPanel: document.getElementById("training-context-panel"),
  trainingDetailPanel: document.getElementById("training-detail-panel"),
  standingsEastBody: document.getElementById("standings-east-body"),
  standingsWestBody: document.getElementById("standings-west-body"),
  backToMainBtn: document.getElementById("back-to-main-btn"),
  backToRosterBtn: document.getElementById("back-to-roster-btn"),
  rosterBody: document.getElementById("my-team-roster-body"),
  playerDetailTitle: document.getElementById("player-detail-title"),
  playerDetailPanel: document.getElementById("player-detail-panel"),
  playerDetailContent: document.getElementById("player-detail-content"),
  myTeamRecord: document.getElementById("myteam-record"),
  myTeamWinPct: document.getElementById("myteam-winpct"),
  myTeamRank: document.getElementById("myteam-rank"),
  myTeamGb: document.getElementById("myteam-gb"),
  myTeamPayroll: document.getElementById("myteam-payroll"),
  myTeamCapspace: document.getElementById("myteam-capspace"),
  myTeamAvgSharp: document.getElementById("myteam-avg-sharp"),
  myTeamRiskCount: document.getElementById("myteam-risk-count"),
  myTeamSortControls: document.getElementById("myteam-sort-controls"),
  myTeamFilterControls: document.getElementById("myteam-filter-controls"),
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
  homeKpiRecord: document.getElementById("home-kpi-record"),
  homeKpiWinpct: document.getElementById("home-kpi-winpct"),
  homeKpiRank: document.getElementById("home-kpi-rank"),
  homeKpiGb: document.getElementById("home-kpi-gb"),
  homeKpiL10: document.getElementById("home-kpi-l10"),
  homeKpiStreak: document.getElementById("home-kpi-streak"),
  homeKpiOut: document.getElementById("home-kpi-out"),
  homeKpiRisk: document.getElementById("home-kpi-risk"),
  homePriorityList: document.getElementById("home-priority-list"),
  homeActivityFeed: document.getElementById("home-activity-feed"),
  homeRiskCalendar: document.getElementById("home-risk-calendar"),
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

function showConfirmModal({ title, body, okLabel = "확인", cancelLabel = "취소" }) {
  if (!els.confirmModal) return Promise.resolve(window.confirm(body || title || "진행하시겠습니까?"));
  return new Promise((resolve) => {
    const active = document.activeElement;
    if (els.confirmModalTitle) els.confirmModalTitle.textContent = title || "확인";
    if (els.confirmModalBody) els.confirmModalBody.textContent = body || "";
    if (els.confirmModalOk) els.confirmModalOk.textContent = okLabel;
    if (els.confirmModalCancel) els.confirmModalCancel.textContent = cancelLabel;

    els.confirmModal.classList.remove("hidden");
    document.body.classList.add("is-modal-open");

    const close = (result) => {
      els.confirmModal.classList.add("hidden");
      document.body.classList.remove("is-modal-open");
      els.confirmModalOk?.removeEventListener("click", onOk);
      els.confirmModalCancel?.removeEventListener("click", onCancel);
      els.confirmModalBackdrop?.removeEventListener("click", onCancel);
      document.removeEventListener("keydown", onKeydown);
      if (active instanceof HTMLElement) active.focus();
      resolve(result);
    };

    const onOk = () => close(true);
    const onCancel = () => close(false);
    const onKeydown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close(false);
      }
    };

    els.confirmModalOk?.addEventListener("click", onOk);
    els.confirmModalCancel?.addEventListener("click", onCancel);
    els.confirmModalBackdrop?.addEventListener("click", onCancel);
    document.addEventListener("keydown", onKeydown);
    els.confirmModalOk?.focus();
  });
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
    els.collegeBigboardDetailScreen,
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

function setCollegeScoutingFeedback(message, tone = "info") {
  if (!els.collegeScoutingFeedback) return;
  els.collegeScoutingFeedback.textContent = message;
  els.collegeScoutingFeedback.dataset.tone = tone;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function safeNum(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function parseSummaryTags(summary) {
  const raw = String(summary || "");
  const strengths = [];
  const concerns = [];
  const strengthMatch = raw.match(/Strengths?:\s*([^\.]+)/i);
  const concernMatch = raw.match(/Concern:\s*([^\.]+)/i);
  if (strengthMatch?.[1]) {
    strengths.push(...strengthMatch[1].split(",").map((v) => v.trim()).filter(Boolean));
  }
  if (concernMatch?.[1]) {
    concerns.push(...concernMatch[1].split(",").map((v) => v.trim()).filter(Boolean));
  }
  return { strengths, concerns };
}

function renderCollegeTeamsKpi(teams) {
  if (!els.collegeTeamsKpi) return;
  if (!teams.length) {
    els.collegeTeamsKpi.innerHTML = "";
    return;
  }
  const bestSrsTeam = [...teams].sort((a, b) => safeNum(b?.srs, -9999) - safeNum(a?.srs, -9999))[0];
  const avgSrs = teams.reduce((sum, t) => sum + safeNum(t?.srs), 0) / teams.length;
  const confCount = teams.reduce((acc, t) => {
    const conf = String(t?.conference || "기타");
    acc[conf] = (acc[conf] || 0) + 1;
    return acc;
  }, {});
  const confTop = Object.entries(confCount).sort((a, b) => b[1] - a[1])[0];
  els.collegeTeamsKpi.innerHTML = `
    <article class="college-kpi-card"><span class="college-kpi-label">BEST SRS</span><strong>${escapeHtml(bestSrsTeam?.name || "-")}</strong><span>${safeNum(bestSrsTeam?.srs).toFixed(2)}</span></article>
    <article class="college-kpi-card"><span class="college-kpi-label">AVG SRS</span><strong>${avgSrs.toFixed(2)}</strong><span>전체 ${teams.length}팀 기준</span></article>
    <article class="college-kpi-card"><span class="college-kpi-label">TOP CONFERENCE</span><strong>${escapeHtml(confTop?.[0] || "-")}</strong><span>${confTop?.[1] || 0} teams</span></article>
  `;
}

function teamSeedChip(rank) {
  if (rank > 4) return "";
  return `<span class="college-seed-chip">TOP ${rank}</span>`;
}

function tierChip(tier) {
  const t = String(tier || "-");
  let cls = "";
  if (/tier\s*1/i.test(t)) cls = "is-tier1";
  else if (/lottery/i.test(t)) cls = "is-lottery";
  else if (/1st/i.test(t)) cls = "is-round1";
  else if (/2nd/i.test(t)) cls = "is-round2";
  return `<span class="college-tier-chip ${cls}">${escapeHtml(t)}</span>`;
}

function renderLeaderInsight(player) {
  if (!els.collegeLeaderInsight || !els.collegeLeaderInsightEmpty) return;
  if (!player) {
    els.collegeLeaderInsight.innerHTML = "";
    els.collegeLeaderInsightEmpty.style.display = "block";
    return;
  }
  els.collegeLeaderInsightEmpty.style.display = "none";
  const impact = (collegeStat(player, "pts") * 0.5) + (collegeStat(player, "reb") * 0.25) + (collegeStat(player, "ast") * 0.25);
  els.collegeLeaderInsight.innerHTML = `
    <div class="college-kv-row"><span>선수</span><span>${escapeHtml(player?.name || "-")}</span></div>
    <div class="college-kv-row"><span>팀</span><span>${escapeHtml(player?.college_team_name || player?.college_team_id || "-")}</span></div>
    <div class="college-kv-row"><span>포지션</span><span>${escapeHtml(player?.pos || "-")}</span></div>
    <div class="college-kv-row"><span>PTS / REB / AST</span><span>${collegeStat(player, "pts").toFixed(1)} / ${collegeStat(player, "reb").toFixed(1)} / ${collegeStat(player, "ast").toFixed(1)}</span></div>
    <div class="college-kv-row"><span>Impact Index</span><span>${impact.toFixed(2)}</span></div>
  `;
}

function collegeStat(player, key) {
  const stats = player?.stats || {};
  const n = Number(stats?.[key]);
  return Number.isFinite(n) ? n : 0;
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
  renderCollegeTeamsKpi(teams);
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
  els.collegeTeamsBody.innerHTML = "";
  sorted.forEach((team, idx) => {
    const rank = idx + 1;
    const teamId = team?.college_team_id || "";
    const tr = document.createElement("tr");
    tr.className = "roster-row college-team-row";
    if (state.selectedCollegeTeamId && state.selectedCollegeTeamId === teamId) {
      tr.classList.add("is-selected");
    }
    tr.innerHTML = `
      <td>${rank}${teamSeedChip(rank)}</td>
      <td class="standings-team-cell">${escapeHtml(team?.name || teamId || "-")}</td>
      <td>${escapeHtml(team?.conference || "-")}</td>
      <td>${team?.wins ?? "-"}</td>
      <td>${team?.losses ?? "-"}</td>
      <td>${safeNum(team?.srs).toFixed(2)}</td>
    `;
    tr.addEventListener("click", () => loadCollegeTeamDetail(teamId).catch((e) => alert(e.message)));
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
  const rows = [...els.collegeTeamsBody.querySelectorAll("tr")];
  rows.forEach((row) => {
    const cell = row.querySelector("td:nth-child(2)");
    const active = cell && String(cell.textContent || "").trim() === String(teamName).trim();
    row.classList.toggle("is-selected", active);
  });
  if (els.collegeRosterSummary) {
    const byPos = roster.reduce((acc, p) => {
      const pos = String(p?.pos || "-");
      acc[pos] = (acc[pos] || 0) + 1;
      return acc;
    }, {});
    const posText = Object.entries(byPos).map(([k, v]) => `${k} ${v}`).join(" · ");
    const avgPts = roster.length ? (roster.reduce((sum, p) => sum + collegeStat(p, "pts"), 0) / roster.length) : 0;
    els.collegeRosterSummary.textContent = `로스터 ${roster.length}명 · 평균 PTS ${avgPts.toFixed(1)}${posText ? ` · ${posText}` : ""}`;
  }
  els.collegeRosterBody.innerHTML = roster.length ? roster.map((p) => `
    <tr class="college-data-row">
      <td>${escapeHtml(p?.name || "-")}</td>
      <td><span class="college-pos-chip">${escapeHtml(p?.pos || "-")}</span></td>
      <td>${escapeHtml(p?.class_year || "-")}</td>
      <td>${collegeStat(p, "pts").toFixed(1)}</td>
      <td>${collegeStat(p, "reb").toFixed(1)}</td>
      <td>${collegeStat(p, "ast").toFixed(1)}</td>
    </tr>
  `).join("") : `<tr><td class="schedule-empty" colspan="6">로스터 데이터가 없습니다.</td></tr>`;
}


async function loadCollegeLeaders() {
  const sort = state.collegeLeadersSort || "pts";
  const payload = await fetchJson(`/api/college/players?sort=${encodeURIComponent(sort)}&order=desc&limit=100`);
  const allPlayers = payload?.players || [];

  const allPos = ["ALL", ...new Set(allPlayers.map((p) => String(p?.pos || "-").toUpperCase()))];
  const allTeams = ["ALL", ...new Set(allPlayers.map((p) => p?.college_team_name || p?.college_team_id || "-"))];
  if (els.collegeLeaderPosFilter && !els.collegeLeaderPosFilter.options.length) {
    els.collegeLeaderPosFilter.innerHTML = allPos.map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
  }
  if (els.collegeLeaderTeamFilter && !els.collegeLeaderTeamFilter.options.length) {
    els.collegeLeaderTeamFilter.innerHTML = allTeams.map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
  }
  state.collegeLeaderPosFilter = state.collegeLeaderPosFilter || "ALL";
  state.collegeLeaderTeamFilter = state.collegeLeaderTeamFilter || "ALL";
  if (els.collegeLeaderPosFilter) els.collegeLeaderPosFilter.value = state.collegeLeaderPosFilter;
  if (els.collegeLeaderTeamFilter) els.collegeLeaderTeamFilter.value = state.collegeLeaderTeamFilter;

  const players = allPlayers.filter((p) => {
    const posOk = state.collegeLeaderPosFilter === "ALL" || String(p?.pos || "-").toUpperCase() === state.collegeLeaderPosFilter;
    const teamName = p?.college_team_name || p?.college_team_id || "-";
    const teamOk = state.collegeLeaderTeamFilter === "ALL" || teamName === state.collegeLeaderTeamFilter;
    return posOk && teamOk;
  });

  if (!state.selectedCollegeLeaderPlayerId && players[0]?.player_id) {
    state.selectedCollegeLeaderPlayerId = players[0].player_id;
  }
  let selectedPlayer = null;
  els.collegeLeadersBody.innerHTML = players.length ? players.map((p, idx) => {
    const selected = state.selectedCollegeLeaderPlayerId === p?.player_id;
    if (selected) selectedPlayer = p;
    return `
      <tr class="college-data-row ${selected ? "is-selected" : ""}" data-player-id="${escapeHtml(p?.player_id || "")}">
        <td>${idx + 1}</td>
        <td>${escapeHtml(p?.name || "-")}</td>
        <td>${escapeHtml(p?.college_team_name || p?.college_team_id || "-")}</td>
        <td><span class="college-pos-chip">${escapeHtml(p?.pos || "-")}</span></td>
        <td>${collegeStat(p, "pts").toFixed(1)}</td>
        <td>${collegeStat(p, "reb").toFixed(1)}</td>
        <td>${collegeStat(p, "ast").toFixed(1)}</td>
        <td>${collegeStat(p, "stl").toFixed(1)}</td>
        <td>${collegeStat(p, "blk").toFixed(1)}</td>
      </tr>
    `;
  }).join("") : `<tr><td class="schedule-empty" colspan="9">리더보드 데이터가 없습니다.</td></tr>`;

  els.collegeLeadersBody.querySelectorAll("tr[data-player-id]").forEach((tr) => {
    tr.addEventListener("click", () => {
      state.selectedCollegeLeaderPlayerId = tr.dataset.playerId;
      loadCollegeLeaders().catch((e) => alert(e.message));
    });
  });
  renderLeaderInsight(selectedPlayer || players[0] || null);
}


async function loadCollegeBigboard() {
  if (!state.collegeExperts.length) {
    if (els.collegeBigboardSummary) els.collegeBigboardSummary.textContent = "전문가 목록이 없습니다.";
    if (els.collegeBigboardEmpty) els.collegeBigboardEmpty.classList.remove("hidden");
    if (els.collegeBigboardOverview) els.collegeBigboardOverview.innerHTML = "";
    return;
  }

  if (els.collegeBigboardLoading) els.collegeBigboardLoading.classList.remove("hidden");
  if (els.collegeBigboardError) {
    els.collegeBigboardError.classList.add("hidden");
    els.collegeBigboardError.textContent = "";
  }
  if (els.collegeBigboardEmpty) els.collegeBigboardEmpty.classList.add("hidden");

  const overviewPayloads = await Promise.all(state.collegeExperts.map(async (expert) => {
    try {
      const payload = await fetchJson(`/api/offseason/draft/bigboard/expert?expert_id=${encodeURIComponent(expert.expert_id)}&pool_mode=auto&limit=10`);
      const board = payload?.board || [];
      return { ok: true, expert, board };
    } catch (error) {
      return { ok: false, expert, error };
    }
  }));

  state.collegeBigboardOverview = overviewPayloads;
  renderCollegeBigboardOverview();
  if (els.collegeBigboardLoading) els.collegeBigboardLoading.classList.add("hidden");
}

function renderCollegeBigboardOverview() {
  const rows = state.collegeBigboardOverview || [];
  const success = rows.filter((row) => row.ok);
  const failed = rows.filter((row) => !row.ok);

  if (els.collegeBigboardSummary) {
    els.collegeBigboardSummary.textContent = `전문가 ${rows.length}명 · 로드 성공 ${success.length}명${failed.length ? ` · 실패 ${failed.length}명` : ""}`;
  }

  if (els.collegeBigboardError) {
    if (failed.length) {
      const names = failed.map((row) => row.expert?.display_name || row.expert?.expert_id).join(", ");
      els.collegeBigboardError.textContent = `일부 전문가 데이터 로드에 실패했습니다: ${names}`;
      els.collegeBigboardError.classList.remove("hidden");
    } else {
      els.collegeBigboardError.classList.add("hidden");
      els.collegeBigboardError.textContent = "";
    }
  }

  if (!success.length) {
    if (els.collegeBigboardEmpty) els.collegeBigboardEmpty.classList.remove("hidden");
    if (els.collegeBigboardOverview) els.collegeBigboardOverview.innerHTML = "";
    return;
  }

  if (els.collegeBigboardEmpty) els.collegeBigboardEmpty.classList.add("hidden");
  if (els.collegeBigboardOverview) {
    els.collegeBigboardOverview.innerHTML = success.map((row) => {
      const topBoard = (row.board || []).slice(0, 10);
      const rowsHtml = topBoard.map((p) => `
        <tr>
          <td>${p?.rank ?? "-"}</td>
          <td>${escapeHtml(p?.name || "-")}</td>
          <td>${escapeHtml(p?.pos || "-")}</td>
        </tr>
      `).join("");
      return `
        <button type="button" class="college-bigboard-card" data-expert-id="${escapeHtml(row.expert.expert_id)}" role="listitem" aria-label="${escapeHtml(row.expert.display_name)} 상세 빅보드 보기">
          <p class="college-bigboard-card-title">
            <strong>${escapeHtml(row.expert.display_name || row.expert.expert_id)}</strong>
            <span class="college-inline-meta">Top 10</span>
          </p>
          <table class="college-bigboard-mini-table">
            <thead><tr><th>#</th><th>선수</th><th>POS</th></tr></thead>
            <tbody>${rowsHtml || `<tr><td colspan="3">데이터 없음</td></tr>`}</tbody>
          </table>
        </button>
      `;
    }).join("");

    els.collegeBigboardOverview.querySelectorAll(".college-bigboard-card").forEach((card) => {
      card.addEventListener("click", () => {
        const expertId = card.dataset.expertId || "";
        state.collegeBigboardLastTriggerExpertId = expertId;
        showCollegeBigboardDetailScreen(expertId).catch((e) => alert(e.message));
      });
    });
  }
}

function renderCollegeBigboardDetailRows(board) {
  return board.length ? board.map((r) => {
    const { strengths, concerns } = parseSummaryTags(r?.summary || "");
    const strengthTags = strengths.map((tag) => `<span class="college-tag is-strength">${escapeHtml(tag)}</span>`).join("");
    const concernTags = concerns.map((tag) => `<span class="college-tag is-concern">${escapeHtml(tag)}</span>`).join("");
    return `
      <tr class="college-data-row">
        <td>${r?.rank ?? "-"}</td>
        <td>${escapeHtml(r?.name || "-")}</td>
        <td><span class="college-pos-chip">${escapeHtml(r?.pos || "-")}</span></td>
        <td>${tierChip(r?.tier)}</td>
        <td><div class="college-tag-wrap">${strengthTags}${concernTags || `<span class="college-tag">${escapeHtml(r?.summary || "-")}</span>`}</div></td>
      </tr>
    `;
  }).join("") : `<tr><td class="schedule-empty" colspan="5">빅보드 데이터가 없습니다.</td></tr>`;
}

async function fetchCollegeBigboardByExpert(expertId) {
  if (!expertId) return [];
  let board = state.collegeBigboardByExpert[expertId];
  if (!board) {
    const payload = await fetchJson(`/api/offseason/draft/bigboard/expert?expert_id=${encodeURIComponent(expertId)}&pool_mode=auto`);
    board = payload?.board || [];
    state.collegeBigboardByExpert[expertId] = board;
  }
  return board;
}

async function showCollegeBigboardDetailScreen(expertId) {
  if (!expertId || !els.collegeBigboardDetailScreen) return;
  const expert = state.collegeExperts.find((item) => item.expert_id === expertId);
  const board = await fetchCollegeBigboardByExpert(expertId);

  const tier1 = board.filter((r) => /tier\s*1/i.test(String(r?.tier || ""))).length;
  const lottery = board.filter((r) => /lottery/i.test(String(r?.tier || ""))).length;
  if (els.collegeBigboardDetailTitle) {
    els.collegeBigboardDetailTitle.textContent = `${expert?.display_name || expertId} 상세 빅보드`;
  }
  if (els.collegeBigboardDetailSummary) {
    els.collegeBigboardDetailSummary.textContent = `Tier1 ${tier1}명 · Lottery ${lottery}명 · 전체 ${board.length}명`;
  }
  if (els.collegeBigboardDetailBody) {
    els.collegeBigboardDetailBody.innerHTML = renderCollegeBigboardDetailRows(board);
  }

  state.selectedCollegeBigboardExpertId = expertId;
  activateScreen(els.collegeBigboardDetailScreen);
  els.collegeBigboardDetailBackBtn?.focus();
}

function closeCollegeBigboardDetailScreen() {
  activateScreen(els.collegeScreen);
  switchCollegeTab("bigboard");
  const selector = state.collegeBigboardLastTriggerExpertId
    ? `.college-bigboard-card[data-expert-id="${CSS.escape(state.collegeBigboardLastTriggerExpertId)}"]`
    : ".college-bigboard-card";
  const trigger = els.collegeBigboardOverview?.querySelector(selector);
  trigger?.focus();
}


async function loadCollegeScouting() {
  if (!state.selectedTeamId) return;
  const [scoutsPayload, reportsPayload] = await Promise.all([
    fetchJson(`/api/scouting/scouts/${encodeURIComponent(state.selectedTeamId)}`),
    fetchJson(`/api/scouting/reports?team_id=${encodeURIComponent(state.selectedTeamId)}&limit=50`),
  ]);
  state.scoutingScouts = scoutsPayload?.scouts || [];
  state.scoutingReports = reportsPayload?.reports || [];
  state.scoutingPlayers = [];
  renderCollegeScoutCards();
}

function getScoutingReadStorageKey(teamId) {
  return `nba.scouting.read.${String(teamId || "")}`;
}

function getScoutingReadMap() {
  const key = getScoutingReadStorageKey(state.selectedTeamId);
  try {
    const raw = localStorage.getItem(key);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function markScoutReportsRead(scoutId) {
  if (!scoutId || !state.selectedTeamId) return;
  const key = getScoutingReadStorageKey(state.selectedTeamId);
  const readMap = getScoutingReadMap();
  readMap[scoutId] = new Date().toISOString();
  localStorage.setItem(key, JSON.stringify(readMap));
}

function getScoutUnreadCount(scoutId) {
  if (!scoutId) return 0;
  const readMap = getScoutingReadMap();
  const lastRead = Date.parse(String(readMap[scoutId] || ""));
  const threshold = Number.isFinite(lastRead) ? lastRead : 0;
  return state.scoutingReports.filter((report) => {
    if (String(report?.scout?.scout_id || "") !== scoutId) return false;
    const created = Date.parse(String(report?.created_at || report?.updated_at || report?.as_of_date || ""));
    return Number.isFinite(created) ? created > threshold : threshold === 0;
  }).length;
}

function getScoutingPlayerName(playerId, fallback = "-") {
  if (!playerId) return fallback;
  const player = state.scoutingPlayerLookup[playerId];
  return player?.name || fallback;
}

function renderCollegeScoutCards() {
  if (!els.collegeScoutCards) return;
  if (!state.scoutingScouts.length) {
    els.collegeScoutCards.innerHTML = `<article class="college-card"><p class="college-inline-meta">가용 스카우터가 없습니다.</p></article>`;
    return;
  }

  const activeAssignments = state.scoutingScouts.filter((scout) => scout?.active_assignment).length;
  const unreadTotal = state.scoutingScouts.reduce((acc, scout) => acc + getScoutUnreadCount(String(scout?.scout_id || "")), 0);
  if (els.collegeScoutingSummary) {
    els.collegeScoutingSummary.textContent = `활성 배정 ${activeAssignments}/${state.scoutingScouts.length} · 미확인 리포트 ${unreadTotal}건`;
  }

  els.collegeScoutCards.innerHTML = state.scoutingScouts.map((scout) => {
    const scoutId = String(scout?.scout_id || "");
    const assignment = scout?.active_assignment;
    const targetId = String(assignment?.target_player_id || "");
    const playerName = assignment ? getScoutingPlayerName(targetId, targetId || "-") : "미배정";
    const unread = getScoutUnreadCount(scoutId);
    const focusAxes = Array.isArray(scout?.profile?.focus_axes) ? scout.profile.focus_axes.slice(0, 2) : [];
    const styleTags = Array.isArray(scout?.profile?.style_tags) ? scout.profile.style_tags.slice(0, 2) : [];
    return `
      <article class="college-card college-scout-card" data-scout-id="${escapeHtml(scoutId)}" role="listitem">
        <div class="college-card-head-inline">
          <div>
            <h4>${escapeHtml(scout?.display_name || scoutId)}</h4>
            <p class="college-inline-meta">${escapeHtml(scout?.specialty_key || "GENERAL")}</p>
          </div>
          ${unread > 0 ? `<span class="college-scout-unread-badge">NEW ${unread}</span>` : ""}
        </div>
        <p class="college-inline-meta">
          현재 배정: ${escapeHtml(playerName)}
        </p>
        <div class="college-tag-wrap">
          ${focusAxes.map((axis) => `<span class="college-tag">${escapeHtml(axis)}</span>`).join("")}
          ${styleTags.map((tag) => `<span class="college-tag is-strength">${escapeHtml(tag)}</span>`).join("")}
        </div>
        <div class="college-actions-row college-scout-actions">
          <button type="button" class="btn btn-primary" data-action="pick-player" data-scout-id="${escapeHtml(scoutId)}">선수 배정</button>
          <button type="button" class="btn btn-secondary" data-action="open-reports" data-scout-id="${escapeHtml(scoutId)}">스카우팅 리포트${unread > 0 ? ` (${unread})` : ""}</button>
        </div>
      </article>
    `;
  }).join("");
}

function resetScoutPlayerSearchState() {
  state.scoutingPlayerSearch = "";
  state.scoutingPlayerSearchStatus = "ALL";
  state.scoutingPlayerSearchResults = [];
  state.scoutingPlayerSearchTotal = 0;
  state.scoutingPlayerSearchOffset = 0;
  state.scoutingPlayerSearchLoading = false;
  state.scoutingPlayerSearchError = "";
  state.scoutingPlayerSearchHasSearched = false;
}

function renderScoutPlayerList() {
  if (!els.collegeScoutPlayerList) return;

  const keyword = String(state.scoutingPlayerSearch || "").trim();
  const hasKeyword = keyword.length >= 2;
  const loading = !!state.scoutingPlayerSearchLoading;
  const err = String(state.scoutingPlayerSearchError || "");
  const rows = Array.isArray(state.scoutingPlayerSearchResults) ? state.scoutingPlayerSearchResults : [];
  const scout = state.scoutingScouts.find((item) => String(item?.scout_id || "") === String(state.scoutingActiveScoutId || ""));

  if (els.collegeScoutPlayerSearchMeta) {
    if (!hasKeyword) {
      els.collegeScoutPlayerSearchMeta.textContent = "2글자 이상 입력하면 전체 대학 선수 대상 검색이 시작됩니다.";
    } else if (loading) {
      els.collegeScoutPlayerSearchMeta.textContent = "선수를 검색 중입니다...";
    } else if (err) {
      els.collegeScoutPlayerSearchMeta.textContent = err;
    } else {
      const total = Number(state.scoutingPlayerSearchTotal || 0);
      const shown = rows.length;
      els.collegeScoutPlayerSearchMeta.textContent = `검색어 '${keyword}' · ${total}명 중 ${shown}명 표시`;
    }
  }

  if (els.collegeScoutPlayerLoadMore) {
    const hasMore = rows.length < Number(state.scoutingPlayerSearchTotal || 0);
    els.collegeScoutPlayerLoadMore.classList.toggle("hidden", !hasKeyword || loading || !!err || !hasMore);
    els.collegeScoutPlayerLoadMore.disabled = loading;
  }

  if (!hasKeyword) {
    els.collegeScoutPlayerList.innerHTML = `<p class="college-inline-meta">선수명을 2글자 이상 입력해주세요.</p>`;
    return;
  }
  if (loading && !rows.length) {
    els.collegeScoutPlayerList.innerHTML = `<p class="college-inline-meta">검색 결과를 불러오는 중입니다...</p>`;
    return;
  }
  if (err && !rows.length) {
    els.collegeScoutPlayerList.innerHTML = `<p class="college-inline-meta">${escapeHtml(err)}</p>`;
    return;
  }
  if (!rows.length) {
    els.collegeScoutPlayerList.innerHTML = `<p class="college-inline-meta">검색 결과가 없습니다.</p>`;
    return;
  }

  els.collegeScoutPlayerList.innerHTML = rows.map((player) => {
    const pid = String(player?.player_id || "");
    const assignedNow = String(scout?.active_assignment?.target_player_id || "") === pid;
    return `
      <button type="button" role="option" class="college-player-option ${assignedNow ? "is-current" : ""}" data-player-id="${escapeHtml(pid)}">
        <span class="college-player-option-main">
          <strong>${escapeHtml(player?.name || "-")}</strong>
          <small>${escapeHtml(player?.college_team_name || player?.college_team_id || "-")} · ${escapeHtml(player?.pos || "-")} · ${escapeHtml(player?.status || "-")}</small>
        </span>
        ${assignedNow ? `<span class="college-player-option-badge">현재 배정</span>` : ""}
      </button>
    `;
  }).join("");
}

async function searchScoutingPlayers({ append = false } = {}) {
  const keyword = String(state.scoutingPlayerSearch || "").trim();
  if (keyword.length < 2) {
    state.scoutingPlayerSearchResults = [];
    state.scoutingPlayerSearchTotal = 0;
    state.scoutingPlayerSearchOffset = 0;
    state.scoutingPlayerSearchError = "";
    state.scoutingPlayerSearchLoading = false;
    state.scoutingPlayerSearchHasSearched = false;
    renderScoutPlayerList();
    return;
  }

  const nextOffset = append ? state.scoutingPlayerSearchResults.length : 0;
  const reqSeq = Number(state.scoutingPlayerSearchRequestSeq || 0) + 1;
  state.scoutingPlayerSearchRequestSeq = reqSeq;
  state.scoutingPlayerSearchLoading = true;
  state.scoutingPlayerSearchError = "";
  if (!append) state.scoutingPlayerSearchHasSearched = true;
  renderScoutPlayerList();

  try {
    const query = new URLSearchParams({
      q: keyword,
      status: String(state.scoutingPlayerSearchStatus || "ALL"),
      limit: String(state.scoutingPlayerSearchLimit || 30),
      offset: String(nextOffset),
    });
    const payload = await fetchJson(`/api/scouting/players/search?${query.toString()}`);
    if (reqSeq !== state.scoutingPlayerSearchRequestSeq) return;

    const rows = Array.isArray(payload?.players) ? payload.players : [];
    state.scoutingPlayerSearchOffset = Number(payload?.offset || nextOffset);
    state.scoutingPlayerSearchTotal = Number(payload?.total || 0);
    state.scoutingPlayerSearchResults = append
      ? [...state.scoutingPlayerSearchResults, ...rows]
      : rows;

    rows.forEach((p) => {
      const pid = String(p?.player_id || "");
      if (pid) state.scoutingPlayerLookup[pid] = p;
    });
  } catch (error) {
    if (reqSeq !== state.scoutingPlayerSearchRequestSeq) return;
    state.scoutingPlayerSearchError = error?.message || "선수 검색 중 오류가 발생했습니다.";
    if (!append) state.scoutingPlayerSearchResults = [];
  } finally {
    if (reqSeq === state.scoutingPlayerSearchRequestSeq) {
      state.scoutingPlayerSearchLoading = false;
      renderScoutPlayerList();
    }
  }
}

function queueScoutingPlayerSearch() {
  if (state.scoutingPlayerSearchDebounceTimer) {
    clearTimeout(state.scoutingPlayerSearchDebounceTimer);
  }
  state.scoutingPlayerSearchDebounceTimer = setTimeout(() => {
    searchScoutingPlayers({ append: false }).catch((e) => {
      state.scoutingPlayerSearchError = e?.message || "선수 검색 중 오류가 발생했습니다.";
      state.scoutingPlayerSearchLoading = false;
      renderScoutPlayerList();
    });
  }, 280);
}

function openScoutPlayerModal(scoutId) {
  if (!els.collegeScoutPlayerModal) return;
  state.scoutingActiveScoutId = scoutId;
  resetScoutPlayerSearchState();
  const scout = state.scoutingScouts.find((item) => String(item?.scout_id || "") === scoutId);
  if (els.collegeScoutPlayerModalTitle) {
    els.collegeScoutPlayerModalTitle.textContent = `${scout?.display_name || scoutId} · 선수 배정`;
  }
  if (els.collegeScoutPlayerModalMeta) {
    els.collegeScoutPlayerModalMeta.textContent = `전문분야 ${scout?.specialty_key || "GENERAL"} · 전체 대학 선수 검색으로 대상 선수를 배정합니다.`;
  }
  if (els.collegeScoutPlayerSearch) {
    els.collegeScoutPlayerSearch.value = "";
  }
  if (els.collegeScoutPlayerStatus) {
    els.collegeScoutPlayerStatus.value = "ALL";
  }
  renderScoutPlayerList();
  els.collegeScoutPlayerModal.classList.remove("hidden");
  document.body.classList.add("is-modal-open");
  els.collegeScoutPlayerSearch?.focus();
}

function closeScoutPlayerModal() {
  if (!els.collegeScoutPlayerModal) return;
  if (state.scoutingPlayerSearchDebounceTimer) {
    clearTimeout(state.scoutingPlayerSearchDebounceTimer);
    state.scoutingPlayerSearchDebounceTimer = null;
  }
  els.collegeScoutPlayerModal.classList.add("hidden");
  document.body.classList.remove("is-modal-open");
}

function openScoutReportsModal(scoutId) {
  if (!els.collegeScoutReportsModal) return;
  state.scoutingActiveScoutId = scoutId;
  const scout = state.scoutingScouts.find((item) => String(item?.scout_id || "") === scoutId);
  if (els.collegeScoutReportsModalTitle) {
    els.collegeScoutReportsModalTitle.textContent = `${scout?.display_name || scoutId} · 스카우팅 리포트`;
  }
  renderScoutReportsList();
  markScoutReportsRead(scoutId);
  renderCollegeScoutCards();
  els.collegeScoutReportsModal.classList.remove("hidden");
  document.body.classList.add("is-modal-open");
  els.collegeScoutReportsModalClose?.focus();
}

function closeScoutReportsModal() {
  if (!els.collegeScoutReportsModal) return;
  els.collegeScoutReportsModal.classList.add("hidden");
  document.body.classList.remove("is-modal-open");
}

function renderScoutReportsList() {
  if (!els.collegeScoutReportsList) return;
  const reports = state.scoutingReports
    .filter((report) => String(report?.scout?.scout_id || "") === String(state.scoutingActiveScoutId || ""));
  if (els.collegeScoutReportsModalMeta) {
    els.collegeScoutReportsModalMeta.textContent = `총 ${reports.length}건 · 최신순`;
  }
  if (!reports.length) {
    els.collegeScoutReportsList.innerHTML = `<p class="college-inline-meta">리포트가 없습니다. 월말 시뮬레이션 이후 생성됩니다.</p>`;
    return;
  }
  els.collegeScoutReportsList.innerHTML = reports.map((report) => {
    const statusRaw = String(report?.status || "-");
    const statusClass = /complete|done|finished/i.test(statusRaw) ? "is-complete" : (/pending|in_progress|active/i.test(statusRaw) ? "is-pending" : "");
    return `
      <article class="college-report-item">
        <div class="college-card-head-inline">
          <strong>${escapeHtml(report?.player_snapshot?.name || report?.target_player_id || "-")}</strong>
          <span class="college-status-chip ${statusClass}">${escapeHtml(statusRaw)}</span>
        </div>
        <p class="college-inline-meta">${escapeHtml(report?.as_of_date || "-")} · ${escapeHtml(report?.period_key || "-")}</p>
        <p>${escapeHtml((report?.report_text || "텍스트 리포트가 아직 생성되지 않았습니다.").slice(0, 240))}</p>
      </article>
    `;
  }).join("");
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
    state.collegeBigboardOverview = [];
    state.collegeBigboardByExpert = {};

    els.collegeMetaLine.textContent = `시즌 ${meta?.season_year || "-"} · 대학팀 ${meta?.college?.teams || 0}개 · 예정 드래프트 ${meta?.upcoming_draft_year || "-"}`;
    renderCollegeTeams(state.collegeTeams);
    if (state.selectedCollegeTeamId) {
      await loadCollegeTeamDetail(state.selectedCollegeTeamId);
    }

    const sortOptions = ["pts", "reb", "ast", "stl", "blk", "mpg", "games", "ts_pct", "usg", "fg_pct"];
    els.collegeLeaderSort.innerHTML = sortOptions.map((k) => `<option value="${k}">${k.toUpperCase()}</option>`).join("");
    els.collegeLeaderSort.value = state.collegeLeadersSort;
    if (els.collegeLeaderPosFilter) els.collegeLeaderPosFilter.innerHTML = "";
    if (els.collegeLeaderTeamFilter) els.collegeLeaderTeamFilter.innerHTML = "";
    await loadCollegeLeaders();

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
  void refreshMainDashboard();
}

function formatIsoDate(dateString) {
  const raw = String(dateString || "").slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw : "YYYY-MM-DD";
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

function resetNextGameCard() {
  els.teamAName.textContent = "Team A";
  els.teamBName.textContent = "Team B";
  applyTeamLogo(els.teamALogo, "");
  applyTeamLogo(els.teamBLogo, "");
  if (els.nextGameArena) els.nextGameArena.textContent = "";
  els.nextGameDatetime.textContent = "YYYY-MM-DD --:-- PM";
}

function renderHomePriorities(items) {
  if (!els.homePriorityList) return;
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) {
    els.homePriorityList.innerHTML = '<li class="home-empty">우선 확인할 알림이 없습니다.</li>';
    return;
  }
  els.homePriorityList.innerHTML = rows.map((p) => {
    const severity = String(p?.severity || "info").toLowerCase();
    return `
      <li class="home-priority-item">
        <span class="home-badge home-badge-${severity}">${severity.toUpperCase()}</span>
        <p>${escapeHtml(p?.text || "-")}</p>
        <button type="button" class="home-inline-cta">${escapeHtml(p?.cta || "확인")}</button>
      </li>
    `;
  }).join("");
}

function renderHomeActivityFeed(items) {
  if (!els.homeActivityFeed) return;
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) {
    els.homeActivityFeed.innerHTML = '<li class="home-empty">최근 활동 데이터가 없습니다.</li>';
    return;
  }
  els.homeActivityFeed.innerHTML = rows.map((r) => `
    <li class="home-activity-item">
      <span class="home-activity-date">${escapeHtml(String(r?.date || "").slice(5, 10) || "--/--")}</span>
      <div>
        <strong>${escapeHtml(r?.type || "EVENT")}</strong>
        <p>${escapeHtml(r?.title || "-")}</p>
      </div>
    </li>
  `).join("");
}

function renderHomeRiskCalendar(days) {
  if (!els.homeRiskCalendar) return;
  const rows = Array.isArray(days) ? days : [];
  if (!rows.length) {
    els.homeRiskCalendar.innerHTML = '<p class="home-empty">캘린더 데이터가 없습니다.</p>';
    return;
  }
  els.homeRiskCalendar.innerHTML = rows.map((d) => {
    const ds = String(d?.date || "").slice(5, 10) || "--/--";
    const isGame = !!d?.is_game_day;
    const b2b = !!d?.is_back_to_back;
    const out = num(d?.out_player_count, 0);
    const high = num(d?.high_risk_player_count, 0);
    return `
      <article class="home-day-chip ${isGame ? "is-game" : ""} ${b2b ? "is-b2b" : ""}">
        <p>${ds}</p>
        <span>OUT ${out}</span>
        <span>HIGH ${high}</span>
      </article>
    `;
  }).join("");
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
      const opponentTeamId = String(g.opponent_team_id || "").toUpperCase();
      const venueName = getScheduleVenueText(g);
      return `
        <tr>
          <td>${g.date_mmdd || "--/--"}</td>
          <td class="schedule-opponent-cell">${g.opponent_label || "-"} ${renderTeamLogoMark(opponentTeamId, "schedule-team-logo")}<span class="schedule-opponent-name">${venueName}</span></td>
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
    ? upcoming.map((g) => {
      const opponentTeamId = String(g.opponent_team_id || "").toUpperCase();
      const venueName = getScheduleVenueText(g);
      return `
        <tr>
          <td>${g.date_mmdd || "--/--"}</td>
          <td class="schedule-opponent-cell">${g.opponent_label || "-"} ${renderTeamLogoMark(opponentTeamId, "schedule-team-logo")}<span class="schedule-opponent-name">${venueName}</span></td>
          <td><span class="schedule-time-chip">${g.tipoff_time || "--:-- --"}</span></td>
        </tr>
      `;
    }).join("")
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

  try {
    const dashboard = await fetchJson(`/api/home/dashboard/${encodeURIComponent(state.selectedTeamId)}`);
    const currentDate = formatIsoDate(dashboard?.current_date);
    state.currentDate = currentDate;
    els.mainCurrentDate.textContent = currentDate;

    const nextGame = dashboard?.next_game?.game;

    if (!nextGame) {
      resetNextGameCard();
      els.nextGameDatetime.textContent = "예정된 다음 경기가 없습니다.";
      renderHomePriorities(dashboard?.priorities || []);
      renderHomeActivityFeed(dashboard?.activity_feed || []);
      renderHomeRiskCalendar(dashboard?.risk_calendar || []);
      return;
    }

    const homeId = String(nextGame.home_team_id || "").toUpperCase();
    const awayId = String(nextGame.away_team_id || "").toUpperCase();
    const gameDate = formatIsoDate(nextGame.date);
    els.teamAName.textContent = TEAM_FULL_NAMES[homeId] || homeId || "Team A";
    els.teamBName.textContent = TEAM_FULL_NAMES[awayId] || awayId || "Team B";
    applyTeamLogo(els.teamALogo, homeId);
    applyTeamLogo(els.teamBLogo, awayId);
    if (els.nextGameArena) {
      els.nextGameArena.textContent = getTeamBranding(homeId).arenaName || "Arena 정보 없음";
    }
    const tipoffTime = nextGame.tipoff_time || randomTipoffTime();
    els.nextGameDatetime.textContent = `${gameDate} ${tipoffTime}`;

    const snapshot = dashboard?.snapshot || {};
    const rec = snapshot?.record || {};
    const standing = snapshot?.standing || {};
    const health = snapshot?.health || {};
    if (els.homeKpiRecord) els.homeKpiRecord.textContent = `${num(rec?.wins, 0)}-${num(rec?.losses, 0)}`;
    if (els.homeKpiWinpct) els.homeKpiWinpct.textContent = formatWinPct(rec?.win_pct);
    if (els.homeKpiRank) els.homeKpiRank.textContent = standing?.rank != null ? `#${num(standing.rank, 0)}` : "#-";
    if (els.homeKpiGb) els.homeKpiGb.textContent = `GB ${standing?.gb_display || "-"}`;
    if (els.homeKpiL10) els.homeKpiL10.textContent = `L10 ${standing?.l10 || "0-0"}`;
    if (els.homeKpiStreak) els.homeKpiStreak.textContent = standing?.streak || "-";
    if (els.homeKpiOut) els.homeKpiOut.textContent = `OUT ${num(health?.out_count, 0)}`;
    if (els.homeKpiRisk) els.homeKpiRisk.textContent = `HIGH ${num(health?.high_risk_count, 0)}`;

    renderHomePriorities(dashboard?.priorities || []);
    renderHomeActivityFeed(dashboard?.activity_feed || []);
    renderHomeRiskCalendar(dashboard?.risk_calendar || []);
  } catch (e) {
    resetNextGameCard();
    els.mainCurrentDate.textContent = "YYYY-MM-DD";
    els.nextGameDatetime.textContent = `다음 경기 정보를 불러오지 못했습니다: ${e.message}`;
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

function getConditionState({ shortStamina, longStamina, sharpness }) {
  const st = clamp(num(shortStamina, 0), 0, 1);
  const lt = clamp(num(longStamina, 0), 0, 1);
  const sharp = clamp(num(sharpness, 0), 0, 100);

  if (sharp < 55 || st < 0.60 || lt < 0.65) return "risk";
  if (sharp < 70 || st < 0.75 || lt < 0.80) return "watch";
  return "good";
}

function sharpnessGrade(score) {
  const v = clamp(num(score, 0), 0, 100);
  if (v >= 95) return { grade: "S", tone: "elite", label: "Elite" };
  if (v >= 85) return { grade: "A", tone: "hot", label: "Hot" };
  if (v >= 70) return { grade: "B", tone: "stable", label: "Stable" };
  if (v >= 55) return { grade: "C", tone: "volatile", label: "Volatile" };
  return { grade: "D", tone: "cold", label: "Cold" };
}

function renderSharpnessBadgeV2(score, opts = {}) {
  const value = Math.round(clamp(num(score, 0), 0, 100));
  const tier = sharpnessGrade(value);
  const prefix = opts.prefix || "";
  return `
    <span class="sharpness-badge-v2 is-${tier.tone}" title="${prefix}경기력 ${value}% · 등급 ${tier.grade} (${tier.label})">
      <strong>${value}</strong>
      <em>${tier.grade}</em>
      <small>${tier.label}</small>
    </span>
  `;
}

function renderConditionCell(shortStamina, longStamina, sharpness) {
  const st = clamp(num(shortStamina, 0), 0, 1);
  const lt = clamp(num(longStamina, 0), 0, 1);
  const state = getConditionState({ shortStamina: st, longStamina: lt, sharpness });
  const label = state === "risk" ? "RISK" : state === "watch" ? "WATCH" : "GOOD";
  return `
    <div class="condition-cell-v2" title="ST ${Math.round(st * 100)}% · LT ${Math.round(lt * 100)}%">
      <div class="condition-micro-row"><span>ST</span><div class="condition-micro-bar"><i style="width:${Math.round(st * 100)}%"></i></div><strong>${Math.round(st * 100)}%</strong></div>
      <div class="condition-micro-row"><span>LT</span><div class="condition-micro-bar"><i style="width:${Math.round(lt * 100)}%"></i></div><strong>${Math.round(lt * 100)}%</strong></div>
      <span class="condition-chip is-${state}">${label}</span>
    </div>
  `;
}

function renderConditionRing(longStamina, shortStamina) {
  const longPct = clamp(num(longStamina, 0), 0, 1) * 100;
  const shortPct = clamp(num(shortStamina, 0), 0, 1) * 100;
  const longColor = ratioToColor(longStamina);
  const shortColor = ratioToColor(shortStamina);
  return `<div class="condition-ring" style="--long-pct:${longPct};--short-pct:${shortPct};--long-color:${longColor};--short-color:${shortColor};" title="장기 ${Math.round(longPct)}% · 단기 ${Math.round(shortPct)}%"></div>`;
}


function formatWinPct(pct) {
  const v = clamp(num(pct, 0), 0, 1);
  return `WIN% ${v.toFixed(3).replace(/^0/, "")}`;
}

function renderMyTeamOverview(summary, rows) {
  const wins = num(summary?.wins, 0);
  const losses = num(summary?.losses, 0);
  const rank = summary?.rank != null ? `#${num(summary.rank, 0)}` : "#-";
  const gb = summary?.gb != null ? Number(summary.gb).toFixed(1) : "-";
  const payroll = formatMoney(summary?.payroll);
  const cap = formatMoney(summary?.cap_space);

  const roster = rows || [];
  const avgSharp = roster.length
    ? Math.round(roster.reduce((acc, r) => acc + clamp(num(r.sharpness, 0), 0, 100), 0) / roster.length)
    : 0;
  const riskCount = roster.filter((r) => {
    const st = num(r.short_term_stamina, 0);
    const lt = num(r.long_term_stamina, 0);
    const sharp = clamp(num(r.sharpness, 0), 0, 100);
    return sharp < 55 || st < 0.55 || lt < 0.6;
  }).length;

  if (els.myTeamRecord) els.myTeamRecord.textContent = `${wins}-${losses}`;
  if (els.myTeamWinPct) els.myTeamWinPct.textContent = formatWinPct(summary?.win_pct);
  if (els.myTeamRank) els.myTeamRank.textContent = rank;
  if (els.myTeamGb) els.myTeamGb.textContent = `GB ${gb}`;
  if (els.myTeamPayroll) els.myTeamPayroll.textContent = payroll;
  if (els.myTeamCapspace) els.myTeamCapspace.textContent = `CAP ${cap}`;
  if (els.myTeamAvgSharp) els.myTeamAvgSharp.textContent = `Sharp ${avgSharp}`;
  if (els.myTeamRiskCount) els.myTeamRiskCount.textContent = `주의 ${riskCount}명`;
}

function myTeamRowMetric(row, key) {
  if (key === "sharpness") return clamp(num(row.sharpness, 0), 0, 100);
  if (key === "salary") return num(row.salary, 0);
  if (key === "pts") return num(row.pts, 0);
  return num(row.ovr, 0);
}

function getMyTeamDisplayRows(rows) {
  let out = [...(rows || [])];
  if (state.myTeamFilters.risk) {
    out = out.filter((r) => {
      const st = num(r.short_term_stamina, 0);
      const lt = num(r.long_term_stamina, 0);
      const sharp = clamp(num(r.sharpness, 0), 0, 100);
      return sharp < 60 || st < 0.6 || lt < 0.65;
    });
  }
  if (state.myTeamFilters.highsalary) {
    const avgSalary = out.length ? out.reduce((acc, r) => acc + num(r.salary, 0), 0) / out.length : 0;
    const threshold = Math.max(avgSalary * 1.35, 12000000);
    out = out.filter((r) => num(r.salary, 0) >= threshold);
  }

  const sortKey = state.myTeamSortKey || "ovr";
  out.sort((a, b) => myTeamRowMetric(b, sortKey) - myTeamRowMetric(a, sortKey));
  return out;
}

function syncMyTeamControlState() {
  if (els.myTeamSortControls) {
    [...els.myTeamSortControls.querySelectorAll(".myteam-chip[data-sort]")].forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.sort === state.myTeamSortKey);
    });
  }
  if (els.myTeamFilterControls) {
    [...els.myTeamFilterControls.querySelectorAll(".myteam-chip[data-filter]")].forEach((btn) => {
      const key = btn.dataset.filter;
      btn.classList.toggle("is-active", !!state.myTeamFilters[key]);
    });
  }
}

function rerenderMyTeamBoard() {
  renderRosterRows(getMyTeamDisplayRows(state.rosterRows));
  syncMyTeamControlState();
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
    const conditionState = getConditionState({ shortStamina, longStamina, sharpness });
    const riskClass = conditionState === "risk" ? "is-risk" : "";
    if (conditionState === "risk") tr.classList.add("is-risk-row");

    tr.innerHTML = `
      <td>
        <div class="myteam-name-cell">
          <strong>${row.name || "-"}</strong>
          <span>${row.player_id || "-"}</span>
        </div>
      </td>
      <td>${row.pos || "-"}</td>
      <td><span class="myteam-ovr-pill">${Math.round(num(row.ovr, 0))}</span></td>
      <td>${num(row.age, 0)}</td>
      <td>${formatHeightIn(row.height_in)}</td>
      <td>${formatWeightLb(row.weight_lb)}</td>
      <td>${formatMoney(row.salary)}</td>
      <td>${num(row.pts, 0).toFixed(1)}</td>
      <td>${num(row.ast, 0).toFixed(1)}</td>
      <td>${num(row.reb, 0).toFixed(1)}</td>
      <td>${num(row.three_pm, 0).toFixed(1)}</td>
      <td class="condition-cell">${renderConditionCell(shortStamina, longStamina, sharpness)}</td>
      <td>${renderSharpnessBadgeV2(sharpness, { prefix: "로스터 " })}</td>
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

function attrCategoryKey(name) {
  const k = String(name || "").toLowerCase();
  if (["shot", "shoot", "free_throw", "layup", "inside", "outside", "close"].some((x) => k.includes(x))) return "Shooting";
  if (["pass", "handle", "play", "iq", "vision"].some((x) => k.includes(x))) return "Playmaking";
  if (["def", "rebound", "block", "steal", "hustle"].some((x) => k.includes(x))) return "Defense";
  if (["agility", "athletic", "durability", "injury", "strength", "speed"].some((x) => k.includes(x))) return "Physical";
  return "Mental";
}

function buildAttrIntelligence(attrs) {
  const entries = Object.entries(attrs || {}).map(([k, v]) => ({
    key: k,
    value: Math.abs(num(v, 0)) <= 1 ? num(v, 0) * 100 : num(v, 0),
  }));

  if (!entries.length) {
    return {
      categoryHtml: '<p class="empty-copy">능력치 데이터가 없습니다.</p>',
      strengthsHtml: '<p class="empty-copy">데이터 없음</p>',
      weaknessesHtml: '<p class="empty-copy">데이터 없음</p>',
    };
  }

  const grouped = { Shooting: [], Playmaking: [], Defense: [], Physical: [], Mental: [] };
  entries.forEach((it) => grouped[attrCategoryKey(it.key)].push(it.value));

  const categoryHtml = Object.entries(grouped)
    .map(([name, vals]) => {
      const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
      return `<div class="attr-intel-row"><span>${name}</span><div class="attr-meter"><i style="width:${clamp(avg, 0, 100)}%"></i></div><strong>${Math.round(avg)}</strong></div>`;
    })
    .join("");

  const sorted = [...entries].sort((a, b) => b.value - a.value);
  const isHiddenNeedsAttentionKey = (key) => {
    const k = String(key || "").trim();
    const lower = k.toLowerCase();
    return lower === "potential" || lower === "i_injuryfreq" || k.startsWith("M_");
  };
  const attentionPool = sorted.filter((item) => !isHiddenNeedsAttentionKey(item.key));
  const strengthsHtml = `<ul class="intel-list">${sorted.slice(0, 5).map((x) => `<li><span>${x.key}</span><strong>${Math.round(x.value)}</strong></li>`).join("")}</ul>`;
  const weaknessItems = attentionPool.slice(-3).reverse();
  const weaknessesHtml = weaknessItems.length
    ? `<ul class="intel-list">${weaknessItems.map((x) => `<li><span>${x.key}</span><strong>${Math.round(x.value)}</strong></li>`).join("")}</ul>`
    : '<p class="empty-copy">노출 가능한 약점 데이터가 없습니다.</p>';

  return { categoryHtml, strengthsHtml, weaknessesHtml };
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
  const highlightStats = [
    ["PTS", num(totals.PTS, 0)],
    ["AST", num(totals.AST, 0)],
    ["REB", num(totals.REB, 0)],
    ["3PM", num(totals["3PM"], 0)],
  ];

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
  const ovr = Math.round(num(p.ovr, 0));
  const sharp = clamp(num(condition.sharpness, 50), 0, 100);
  const { categoryHtml, strengthsHtml, weaknessesHtml } = buildAttrIntelligence(p.attrs || {});

  els.playerDetailTitle.textContent = `${playerName} 상세 정보`;
  els.playerDetailContent.innerHTML = `
    <div class="player-layout player-layout-v2">
      <section class="detail-card detail-card-header detail-card-hero">
        <div class="detail-head detail-head-main">
          <div>
            <p class="detail-eyebrow">FRANCHISE PLAYER CARD</p>
            <h3>${playerName}</h3>
            <p class="detail-subline">${p.pos || "-"} · ${num(p.age, 0)}세 · ${formatHeightIn(p.height_in)} / ${formatWeightLb(p.weight_lb)}</p>
            <p class="hero-summary">${injury.is_injured ? "건강 관리 필요" : "출전 가능"} · Sharp ${Math.round(sharp)} · ${detail.dissatisfaction?.is_dissatisfied ? "불만 관리 필요" : "불만 낮음"}</p>
          </div>
          <div class="hero-kpi-stack">
            <span class="ovr-medal">OVR ${ovr}</span>
            ${renderSharpnessBadgeV2(sharp)}
            <span class="status-line ${injury.is_injured ? "status-danger" : "status-ok"}">${injury.is_injured ? "Injured" : "Available"}</span>
          </div>
        </div>
      </section>

      <section class="detail-card detail-card-contract">
        <h4>계약 트랙</h4>
        <ul class="compact-kv-list">
          ${contractRows.map((row) => `<li><span>${row.label}</span><strong${row.emphasis ? ' class="text-accent"' : ""}>${row.value}</strong></li>`).join("")}
        </ul>
        ${twoWay.is_two_way ? `<p class="section-note">투웨이 계약 · 남은 경기 ${num(twoWay.games_remaining, 0)} / ${num(twoWay.game_limit, 0)}</p>` : ""}
      </section>

      <section class="detail-card detail-card-dissatisfaction">
        <h4>만족도 리스크</h4>
        <p class="status-line ${detail.dissatisfaction?.is_dissatisfied ? "status-danger" : "status-ok"}">${detail.dissatisfaction?.is_dissatisfied ? "불만 있음" : "불만 없음"}</p>
        <p class="section-copy">${diss.text}</p>
        ${dissatisfactionDescription.length ? `<ul class="kv-list">${dissatisfactionDescription.map((x) => `<li>${x}</li>`).join("")}</ul>` : ""}
      </section>

      <section class="detail-card detail-card-attr">
        <h4>능력치 인텔리전스</h4>
        <div class="attr-intel-grid">${categoryHtml}</div>
        <div class="attr-intel-columns">
          <div><p class="detail-eyebrow">TOP STRENGTHS</p>${strengthsHtml}</div>
          <div><p class="detail-eyebrow">NEEDS ATTENTION</p>${weaknessesHtml}</div>
        </div>
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
        <h4>시즌 퍼포먼스</h4>
        <div class="hero-stat-grid">
          ${highlightStats.map(([k, v]) => `<article class="hero-stat"><p>${k}</p><strong>${Math.round(v * 10) / 10}</strong></article>`).join("")}
        </div>
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

    renderMyTeamOverview(detail.summary || {}, state.rosterRows);
    rerenderMyTeamBoard();
    els.playerDetailContent.innerHTML = "";
    els.playerDetailTitle.textContent = "선수 상세 정보";
    activateScreen(els.myTeamScreen);
  } finally {
    setLoading(false);
  }
}

async function confirmTeamSelection(teamId, fullName) {
  const confirmed = await showConfirmModal({
    title: "팀 선택 확인",
    body: `${fullName}으로 GM 커리어를 시작하시겠습니까? 선택 후 메인 화면으로 이동합니다.`,
    okLabel: "선택 확정",
    cancelLabel: "취소",
  });
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

function trainingTypeIcon(t, isGameDay) {
  if (isGameDay) return "🏟";
  const m = {
    OFF_TACTICS: "⚔",
    DEF_TACTICS: "🛡",
    FILM: "🎬",
    SCRIMMAGE: "🏀",
    RECOVERY: "🧊",
    REST: "⏸"
  };
  return m[String(t || "").toUpperCase()] || "•";
}

function buildTrainingDerivedMetrics() {
  const today = state.currentDate;
  const next7 = state.trainingCalendarDays.filter((d) => d >= today).slice(0, 7);
  const sessions = next7.map((d) => state.trainingSessionsByDate?.[d]?.session?.type).filter(Boolean);
  const gameCount = next7.filter((d) => !!state.trainingGameByDate?.[d]).length;
  const restCount = sessions.filter((t) => ["RECOVERY", "REST"].includes(String(t || "").toUpperCase())).length;
  const trainCount = sessions.length - restCount;
  const nextGame = next7.find((d) => !!state.trainingGameByDate?.[d]);
  const dDay = nextGame ? Math.max(0, Math.round((parseIsoDate(nextGame) - parseIsoDate(today)) / (1000 * 60 * 60 * 24))) : null;
  const offenseCount = sessions.filter((t) => String(t || "").toUpperCase() === "OFF_TACTICS").length;
  const offenseRatio = sessions.length ? offenseCount / sessions.length : 0;
  const hasBackToBack = next7.some((d) => state.trainingGameByDate?.[d] && state.trainingGameByDate?.[dateToIso(addDays(parseIsoDate(d), 1))]);
  return {
    rangeStart: state.trainingCalendarDays[0],
    rangeEnd: state.trainingCalendarDays[state.trainingCalendarDays.length - 1],
    trainCount,
    gameCount,
    restCount,
    nextGame,
    dDay,
    offenseRatio,
    hasBackToBack,
  };
}

function buildTrainingRiskFlags(iso) {
  const cur = parseIsoDate(iso);
  if (!cur) return { level: "low", reason: "" };
  const prevIso = dateToIso(addDays(cur, -1));
  const nextIso = dateToIso(addDays(cur, 1));
  const prevGame = !!state.trainingGameByDate?.[prevIso];
  const nextGame = !!state.trainingGameByDate?.[nextIso];
  if (prevGame && nextGame) return { level: "high", reason: "연전 사이 일정" };
  if (prevGame || nextGame) return { level: "medium", reason: "경기 인접 일정" };
  return { level: "low", reason: "일반 일정" };
}

function buildTrainingRecommendation(selectedDates, type = null) {
  if (!selectedDates.length) {
    return {
      title: "선택 대기",
      body: "날짜를 선택하면 일정 기반 추천 훈련이 표시됩니다.",
    };
  }

  const sorted = [...selectedDates].sort();
  const hasPreGame = sorted.some((iso) => !!state.trainingGameByDate?.[dateToIso(addDays(parseIsoDate(iso), 1))]);
  const hasPostGame = sorted.some((iso) => !!state.trainingGameByDate?.[dateToIso(addDays(parseIsoDate(iso), -1))]);
  const selectedType = String(type || "").toUpperCase();
  const metrics = buildTrainingDerivedMetrics();
  if (hasPreGame && ["OFF_TACTICS", "DEF_TACTICS", "SCRIMMAGE"].includes(selectedType)) {
    return {
      title: "경기 전날 고강도 경고",
      body: "내일 경기 일정이 있어 필름/회복 훈련이 더 안정적입니다.",
    };
  }
  if (metrics.gameCount >= 3 && metrics.restCount <= 1) {
    return {
      title: "회복 세션 보강 권장",
      body: "7일 내 경기 밀도가 높아 최소 1회 회복 세션을 확보하는 것이 좋습니다.",
    };
  }
  if (metrics.offenseRatio >= 0.6) {
    return {
      title: "훈련 편중 경고",
      body: "공격 전술 비중이 높습니다. 수비/필름 훈련으로 균형을 맞추세요.",
    };
  }
  if (hasPostGame) {
    return {
      title: "경기 다음날 회복 추천",
      body: "경기 다음날은 RECOVERY 배치 시 피로 누적 관리에 유리합니다.",
    };
  }
  return {
    title: "균형 상태 양호",
    body: "현재 일정 밀도 기준으로 선택한 훈련 구성이 무난합니다.",
  };
}

function renderTrainingSummaryStrip() {
  if (!els.trainingSummaryStrip) return;
  const m = buildTrainingDerivedMetrics();
  const range = m.rangeStart && m.rangeEnd
    ? `${String(m.rangeStart).slice(5)} ~ ${String(m.rangeEnd).slice(5)}`
    : "-";
  const nextOpp = m.nextGame ? state.trainingGameByDate?.[m.nextGame] : null;
  const dDay = m.dDay == null ? "-" : `D-${m.dDay}`;
  const risk = [];
  if (m.hasBackToBack) risk.push("연전 구간");
  if (m.restCount <= 1) risk.push("휴식 부족");
  if (m.offenseRatio >= 0.6) risk.push("공격 편중");
  const riskLabel = risk.length ? risk.join(" · ") : "안정";

  els.trainingSummaryStrip.innerHTML = `
    <article class="training-kpi-card">
      <p class="training-kpi-title">캘린더 범위</p>
      <p class="training-kpi-value">${range}</p>
      <p class="training-kpi-sub">4주 훈련 계획 구간</p>
    </article>
    <article class="training-kpi-card">
      <p class="training-kpi-title">이번 7일 요약</p>
      <p class="training-kpi-value">훈련 ${m.trainCount} · 경기 ${m.gameCount}</p>
      <p class="training-kpi-sub">휴식 ${m.restCount}일</p>
    </article>
    <article class="training-kpi-card">
      <p class="training-kpi-title">다음 경기</p>
      <p class="training-kpi-value">${nextOpp ? `vs ${nextOpp}` : "일정 없음"}</p>
      <p class="training-kpi-sub">${m.nextGame || "-"} · ${dDay}</p>
    </article>
    <article class="training-kpi-card">
      <p class="training-kpi-title">리스크 상태</p>
      <p class="training-kpi-value">${riskLabel}</p>
      <p class="training-kpi-sub">일정/편중도 기반</p>
    </article>
  `;
}

function renderTrainingContextPanel(type = null) {
  if (!els.trainingContextPanel) return;
  const selected = [...state.trainingSelectedDates].sort();
  const rec = buildTrainingRecommendation(selected, type || state.trainingActiveType);
  if (!selected.length) {
    els.trainingContextPanel.innerHTML = '<p class="empty-copy">캘린더에서 날짜를 선택하면 일정 맥락과 추천 훈련이 표시됩니다.</p>';
    return;
  }
  const first = selected[0];
  const last = selected[selected.length - 1];
  const firstRisk = buildTrainingRiskFlags(first);
  const prevIso = dateToIso(addDays(parseIsoDate(first), -1));
  const nextIso = dateToIso(addDays(parseIsoDate(last), 1));
  const prevGame = state.trainingGameByDate?.[prevIso];
  const nextGame = state.trainingGameByDate?.[nextIso];

  els.trainingContextPanel.innerHTML = `
    <h3 class="training-context-title">선택 일정 컨텍스트</h3>
    <ul class="training-context-kv">
      <li><span>선택 날짜</span><strong>${selected.length}일</strong></li>
      <li><span>구간</span><strong>${first} ~ ${last}</strong></li>
      <li><span>전날 경기</span><strong>${prevGame ? `vs ${prevGame}` : "없음"}</strong></li>
      <li><span>다음날 경기</span><strong>${nextGame ? `vs ${nextGame}` : "없음"}</strong></li>
      <li><span>대표 위험도</span><strong>${firstRisk.level.toUpperCase()} · ${firstRisk.reason}</strong></li>
    </ul>
    <div class="training-recommend">
      <strong>${rec.title}</strong>
      <p>${rec.body}</p>
    </div>
  `;
}

function refreshTrainingTypeButtonsState() {
  if (!els.trainingTypeButtons) return;
  const hasSelection = state.trainingSelectedDates.size > 0;
  els.trainingTypeButtons.querySelectorAll("button[data-training-type]").forEach((btn) => {
    btn.disabled = !hasSelection;
    btn.setAttribute("aria-disabled", hasSelection ? "false" : "true");
    btn.title = hasSelection ? "" : "날짜를 먼저 선택하세요.";
  });
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

  const [offFam, defFam] = await Promise.all([
    fetchJson(`/api/readiness/team/${encodeURIComponent(state.selectedTeamId)}/familiarity?scheme_type=offense`).catch(() => ({ items: [] })),
    fetchJson(`/api/readiness/team/${encodeURIComponent(state.selectedTeamId)}/familiarity?scheme_type=defense`).catch(() => ({ items: [] })),
  ]);
  state.trainingFamiliarity = { offense: offFam.items || [], defense: defFam.items || [] };

  state.trainingSessionsByDate = sessions;
  state.trainingGameByDate = gameByDate;
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

    const sessInfo = state.trainingSessionsByDate?.[iso];
    const sessType = sessInfo?.session?.type;
    const sessionLine = sessInfo ? trainingTypeLabel(sessType) : "";
    const risk = buildTrainingRiskFlags(iso);
    const badgeClass = sessInfo?.is_user_set ? "is-user" : "is-auto";
    const badgeLabel = sessInfo ? (sessInfo.is_user_set ? "수동" : "AUTO") : "";
    const riskCls = risk.level === "high" ? "is-high" : (risk.level === "medium" ? "is-medium" : "");
    const icon = trainingTypeIcon(sessType, isGameDay);

    btn.innerHTML = `
      <div class="training-day-head"><div class="training-day-date">${label}</div><span class="training-day-icon">${icon}</span></div>
      <div class="training-day-note">${gameOpp ? `vs ${gameOpp}` : ""}</div>
      <div class="training-day-sub">${!gameOpp ? sessionLine : "경기일"}</div>
      ${!gameOpp && sessInfo ? `<span class="training-session-badge ${badgeClass}">${badgeLabel}</span>` : ""}
      ${!isGameDay && !isPast ? `<span class="training-risk-dot ${riskCls}" title="${risk.reason}"></span>` : ""}
    `;

    if (!selectable) {
      btn.disabled = true;
    } else {
      btn.addEventListener("click", () => {
        if (state.trainingSelectedDates.has(iso)) state.trainingSelectedDates.delete(iso);
        else state.trainingSelectedDates.add(iso);
        renderTrainingCalendar();
        refreshTrainingTypeButtonsState();
        renderTrainingContextPanel();
      });
    }

    container.appendChild(btn);
  });
}

function displaySchemeName(key) {
  return String(key || "-").replaceAll("_", " ");
}

function buildSchemeRows(schemeType) {
  const baseList = schemeType === "offense" ? TACTICS_OFFENSE_SCHEMES : TACTICS_DEFENSE_SCHEMES;
  const famList = schemeType === "offense" ? state.trainingFamiliarity.offense : state.trainingFamiliarity.defense;
  const famMap = new Map((famList || []).map((r) => [String(r.scheme_key), Number(r.value || 0)]));
  return (baseList || []).map((s) => ({
    key: s.key,
    value: famMap.has(s.key) ? famMap.get(s.key) : 0,
  }));
}

function trainingImpactLevelLabel(score) {
  if (score >= 2.5) return "매우 높음";
  if (score >= 1.2) return "높음";
  if (score > 0.1) return "보통";
  if (score >= -0.4) return "낮음";
  return "매우 낮음";
}

function trainingRhythmLabel(avgSharpnessDelta) {
  if (avgSharpnessDelta >= 1.2) return "실전 감각이 크게 올라갑니다.";
  if (avgSharpnessDelta >= 0.4) return "실전 감각 유지에 도움이 됩니다.";
  if (avgSharpnessDelta > -0.2) return "실전 감각 변화는 제한적입니다.";
  return "실전 감각이 떨어질 수 있어 경기 투입 전 점검이 필요합니다.";
}

function trainingLoadLabel(avgIntensity) {
  if (avgIntensity >= 1.12) return { label: "높음", tone: "is-caution" };
  if (avgIntensity >= 0.95) return { label: "보통", tone: "is-neutral" };
  return { label: "낮음", tone: "is-positive" };
}

function trainingImpactTone(levelLabel) {
  if (["매우 높음", "높음"].includes(levelLabel)) return "is-positive";
  if (levelLabel === "보통") return "is-neutral";
  return "is-caution";
}

function renderPreviewText(preview) {
  if (!preview) return '<p class="empty-copy">효과 프리뷰를 불러오지 못했습니다.</p>';
  const byPidRows = Object.values(preview.preview?.intensity_mult_by_pid || {});
  const avgSharpnessDelta = byPidRows.length
    ? (byPidRows.reduce((a, x) => a + Number(x.sharpness_delta || 0), 0) / byPidRows.length)
    : 0;
  const avgIntensity = byPidRows.length
    ? (byPidRows.reduce((a, x) => a + Number(x.intensity_mult || 1), 0) / byPidRows.length)
    : 1;

  const offenseGain = Number(preview.preview?.familiarity_gain?.offense_gain || 0);
  const defenseGain = Number(preview.preview?.familiarity_gain?.defense_gain || 0);
  const offenseLevel = trainingImpactLevelLabel(offenseGain);
  const defenseLevel = trainingImpactLevelLabel(defenseGain);
  const rhythmCopy = trainingRhythmLabel(avgSharpnessDelta);
  const load = trainingLoadLabel(avgIntensity);

  const dateIso = String(preview.date_iso || "").slice(0, 10);
  const risk = dateIso ? buildTrainingRiskFlags(dateIso) : { level: "low", reason: "일반 일정" };
  const riskKorean = risk.level === "high" ? "높음" : (risk.level === "medium" ? "주의" : "안정");

  const sessionType = String(preview.session?.type || "").toUpperCase();
  const participantCount = Array.isArray(preview.session?.participant_pids) ? preview.session.participant_pids.length : 0;
  const scopeCopy = sessionType === "SCRIMMAGE"
    ? `청백전 참여 선수 중심으로 강도가 높고, 비참여 선수는 ${trainingTypeLabel(preview.session?.non_participant_type)} 루틴을 따릅니다.`
    : "해당 날짜 로스터 전체에 동일한 훈련 컨셉이 적용됩니다.";

  const coachLine = (() => {
    if (risk.level === "high" && ["OFF_TACTICS", "DEF_TACTICS", "SCRIMMAGE"].includes(sessionType)) {
      return "경기 인접 일정입니다. 고강도 세션보다 필름/회복 중심 구성이 더 안전합니다.";
    }
    if (load.label === "높음") {
      return "훈련 완성도는 좋지만 누적 부담이 큽니다. 다음 일정에 회복 세션을 고려하세요.";
    }
    if (sessionType === "FILM") {
      return "전술 이해도와 경기 집중력을 안정적으로 끌어올리는 선택입니다.";
    }
    return "현재 일정에서는 균형 잡힌 선택입니다.";
  })();

  return `
    <div class="training-preview-report">
      <div class="training-preview-row">
        <p class="training-preview-head">기대 효과</p>
        <ul class="kv-list training-preview-list">
          <li><span>공격 조직력</span><strong class="${trainingImpactTone(offenseLevel)}">${offenseLevel}</strong></li>
          <li><span>수비 조직력</span><strong class="${trainingImpactTone(defenseLevel)}">${defenseLevel}</strong></li>
          <li><span>실전 감각</span><strong class="is-neutral">${rhythmCopy}</strong></li>
        </ul>
      </div>
      <div class="training-preview-row">
        <p class="training-preview-head">부담도</p>
        <ul class="kv-list training-preview-list">
          <li><span>훈련 강도 부담</span><strong class="${load.tone}">${load.label}</strong></li>
          <li><span>일정 리스크</span><strong class="${risk.level === "high" ? "is-caution" : "is-neutral"}">${riskKorean} · ${risk.reason}</strong></li>
        </ul>
      </div>
      <div class="training-preview-row">
        <p class="training-preview-head">적용 범위</p>
        <p class="training-preview-copy">${scopeCopy}</p>
        ${sessionType === "SCRIMMAGE" ? `<p class="training-preview-subcopy">청백전 참여 인원: ${participantCount}명</p>` : ""}
      </div>
      <div class="training-preview-row training-preview-coach">
        <p class="training-preview-head">코치 코멘트</p>
        <p class="training-preview-copy">${coachLine}</p>
      </div>
    </div>
  `;
}

async function renderTrainingDetail(type) {
  state.trainingActiveType = type;
  const selected = [...state.trainingSelectedDates].sort();
  if (!selected.length) {
    els.trainingDetailPanel.innerHTML = '<p class="empty-copy">적용할 날짜를 먼저 선택하세요.</p>';
    renderTrainingContextPanel(type);
    return;
  }

  const baseSession = {
    type,
    offense_scheme_key: null,
    defense_scheme_key: null,
    participant_pids: [],
    non_participant_type: "RECOVERY"
  };

  const offSchemeRows = buildSchemeRows("offense");
  const defSchemeRows = buildSchemeRows("defense");
  const offSchemes = offSchemeRows.map((x) => x.key);
  const defSchemes = defSchemeRows.map((x) => x.key);

  if (type === "OFF_TACTICS") baseSession.offense_scheme_key = offSchemes[0] || "Spread_HeavyPnR";
  if (type === "DEF_TACTICS") baseSession.defense_scheme_key = defSchemes[0] || "Drop";
  if (type === "FILM") {
    baseSession.offense_scheme_key = offSchemes[0] || "Spread_HeavyPnR";
    baseSession.defense_scheme_key = defSchemes[0] || "Drop";
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

  let extra = "";
  if (type === "OFF_TACTICS") {
    extra = `
      <div>
        <p class="training-section-title">공격 스킴 선택 (8개)</p>
        <div class="training-scheme-grid" id="training-off-scheme-grid">
          ${offSchemeRows.map((r) => `
            <button type="button" class="training-scheme-card ${r.key === baseSession.offense_scheme_key ? "is-selected" : ""}" data-off-scheme="${r.key}">
              <strong>${displaySchemeName(r.key)}</strong>
              <span>적응도 ${Math.round(r.value)}%</span>
            </button>
          `).join("")}
        </div>
      </div>
    `;
  } else if (type === "DEF_TACTICS") {
    extra = `
      <div>
        <p class="training-section-title">수비 스킴 선택 (7개)</p>
        <div class="training-scheme-grid" id="training-def-scheme-grid">
          ${defSchemeRows.map((r) => `
            <button type="button" class="training-scheme-card ${r.key === baseSession.defense_scheme_key ? "is-selected" : ""}" data-def-scheme="${r.key}">
              <strong>${displaySchemeName(r.key)}</strong>
              <span>적응도 ${Math.round(r.value)}%</span>
            </button>
          `).join("")}
        </div>
      </div>
    `;
  } else if (type === "SCRIMMAGE") {
    const selectedSet = new Set(baseSession.participant_pids);
    const rosterRows = state.trainingRoster.map((r) => `
      <tr>
        <td>${r.name || r.player_id}</td>
        <td>${Math.round(Number((r.short_term_stamina ?? 1) * 100))}%</td>
        <td>${Math.round(Number((r.long_term_stamina ?? 1) * 100))}%</td>
        <td>${Math.round(Number(r.sharpness ?? 50))}</td>
      </tr>
    `).join("");
    extra = `
      <p class="training-section-title">5대5 라인업 선택 (기본 10명)</p>
      <div class="training-player-select-grid" id="training-scrimmage-player-grid">
        ${state.trainingRoster.map((r) => {
          const pid = String(r.player_id);
          return `<button type="button" class="training-player-chip ${selectedSet.has(pid) ? "is-selected" : ""}" data-scrimmage-pid="${pid}">${r.name || pid}</button>`;
        }).join("")}
      </div>
      <p class="training-selected-copy">선택 선수: <strong id="training-scrimmage-selected-count">${baseSession.participant_pids.length}</strong>명</p>
      <table class="training-player-table">
        <thead><tr><th>선수</th><th>단기 체력</th><th>장기 체력</th><th>샤프니스</th></tr></thead>
        <tbody>${rosterRows}</tbody>
      </table>
    `;
  }

  els.trainingDetailPanel.innerHTML = `
    <div class="training-detail-grid">
      <h3>${trainingTypeLabel(type)} 훈련 설정</h3>
      <p>선택 날짜: ${selected.join(", ")}</p>
      ${extra}
      <div class="training-preview-box"><strong>연습 효과 프리뷰</strong><div id="training-preview-body">${renderPreviewText(preview)}</div></div>
      <div class="training-inline-row"><button id="training-apply-btn" class="btn btn-primary" type="button">선택 날짜에 적용</button></div>
    </div>
  `;
  renderTrainingContextPanel(type);

  async function refreshTrainingPreview() {
    const currentPreview = await fetchJson(`/api/practice/team/${encodeURIComponent(state.selectedTeamId)}/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ season_year: null, date_iso: firstDate, ...state.trainingDraftSession })
    }).catch(() => null);
    const previewBody = document.getElementById("training-preview-body");
    if (previewBody) previewBody.innerHTML = renderPreviewText(currentPreview);
  }

  document.querySelectorAll("[data-off-scheme]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.trainingDraftSession.offense_scheme_key = btn.dataset.offScheme;
      document.querySelectorAll("[data-off-scheme]").forEach((el) => el.classList.toggle("is-selected", el === btn));
      await refreshTrainingPreview();
    });
  });

  document.querySelectorAll("[data-def-scheme]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.trainingDraftSession.defense_scheme_key = btn.dataset.defScheme;
      document.querySelectorAll("[data-def-scheme]").forEach((el) => el.classList.toggle("is-selected", el === btn));
      await refreshTrainingPreview();
    });
  });

  document.querySelectorAll("[data-scrimmage-pid]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const pid = btn.dataset.scrimmagePid;
      const set = new Set(state.trainingDraftSession.participant_pids || []);
      if (set.has(pid)) set.delete(pid);
      else set.add(pid);
      state.trainingDraftSession.participant_pids = [...set];
      btn.classList.toggle("is-selected", set.has(pid));
      const count = document.getElementById("training-scrimmage-selected-count");
      if (count) count.textContent = String(state.trainingDraftSession.participant_pids.length);
      await refreshTrainingPreview();
    });
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
    renderTrainingCalendar();
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
    state.trainingActiveType = null;
    await loadTrainingData();
    renderTrainingSummaryStrip();
    renderTrainingCalendar();
    refreshTrainingTypeButtonsState();
    renderTrainingContextPanel();
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
    conferenceSection.setAttribute("aria-label", conference === "East" ? "동부 컨퍼런스" : "서부 컨퍼런스");

    (divisionOrder[conference] || Object.keys(grouped[conference])).forEach((division) => {
      const divisionTeams = (grouped[conference][division] || []).sort((a, b) => {
        const aName = TEAM_FULL_NAMES[String(a.team_id || "").toUpperCase()] || String(a.team_id || "");
        const bName = TEAM_FULL_NAMES[String(b.team_id || "").toUpperCase()] || String(b.team_id || "");
        return aName.localeCompare(bName);
      });
      if (!divisionTeams.length) return;

      const divisionSection = document.createElement("div");
      divisionSection.className = "team-division";
      divisionSection.setAttribute("data-division", division);
      divisionSection.setAttribute("aria-label", `${conference} ${division}`);

      const divisionGrid = document.createElement("div");
      divisionGrid.className = "team-division-grid";

      divisionTeams.forEach((team) => {
        const id = String(team.team_id || "").toUpperCase();
        const fullName = TEAM_FULL_NAMES[id] || id;
        const arenaName = getTeamBranding(id).arenaName || "홈 경기장 정보 없음";
        const card = document.createElement("button");
        card.className = "team-card";
        card.type = "button";
        card.innerHTML = `
          <div class="team-card-top">${renderTeamLogoMark(id, "team-card-logo")}</div>
          <strong>${fullName}</strong>
          <small>${arenaName}</small>
        `;
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
      <td class="standings-team-cell">${renderTeamLogoMark(teamId, "standings-team-logo")}${TEAM_FULL_NAMES[teamId] || teamId || "-"}</td>
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

function tacticDisplayLabel(raw) {
  return String(raw || "-").replaceAll("_", " ");
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
  return { offenseScheme: "Spread_HeavyPnR", defenseScheme: "Drop", starters, rotation, baselineHash: "" };
}

function renderSchemeOptions(kind) {
  const isOff = kind === "offense";
  const optionsEl = isOff ? els.tacticsOffenseOptions : els.tacticsDefenseOptions;
  const list = isOff ? TACTICS_OFFENSE_SCHEMES : TACTICS_DEFENSE_SCHEMES;
  const selected = isOff ? state.tacticsDraft.offenseScheme : state.tacticsDraft.defenseScheme;
  optionsEl.innerHTML = list.map((s) => `<button type="button" data-key="${s.key}">${tacticDisplayLabel(s.label)}${s.key === selected ? " ✓" : ""}</button>`).join("");
  optionsEl.querySelectorAll("button[data-key]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (isOff) {
        state.tacticsDraft.offenseScheme = btn.dataset.key;
      } else {
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

function computeTacticsInsights() {
  const allRows = [...state.tacticsDraft.starters, ...state.tacticsDraft.rotation];
  const starterMinutes = state.tacticsDraft.starters.reduce((sum, r) => sum + Math.max(0, Number(r.minutes || 0)), 0);
  const rotationMinutes = state.tacticsDraft.rotation.reduce((sum, r) => sum + Math.max(0, Number(r.minutes || 0)), 0);
  const totalMinutes = starterMinutes + rotationMinutes;
  const minutesDelta = 240 - totalMinutes;

  const offenseCount = new Map();
  const defenseCount = new Map();
  allRows.forEach((r) => {
    offenseCount.set(r.offenseRole, (offenseCount.get(r.offenseRole) || 0) + 1);
    defenseCount.set(r.defenseRole, (defenseCount.get(r.defenseRole) || 0) + 1);
  });

  const warnings = [];
  if (minutesDelta !== 0) {
    warnings.push({
      level: Math.abs(minutesDelta) >= 8 ? 'err' : 'warn',
      text: `총 출전시간이 ${Math.abs(minutesDelta)}분 ${minutesDelta > 0 ? '부족' : '초과'}했습니다.`
    });
  }
  const dupDef = [...defenseCount.entries()].filter(([, c]) => c > 1);
  if (dupDef.length) warnings.push({ level: 'warn', text: `수비 역할 중복 ${dupDef.length}개가 있습니다.` });
  const lowCreator = state.tacticsDraft.rotation.filter((r) => String(r.offenseRole || '').includes('Engine') || String(r.offenseRole || '').includes('Shot_Creator')).length;
  if (lowCreator === 0) warnings.push({ level: 'warn', text: '벤치 유닛에 볼 핸들러 역할이 부족합니다.' });

  return {
    allRows,
    totalMinutes,
    minutesDelta,
    starterAvg: starterMinutes / (state.tacticsDraft.starters.length || 1),
    rotationAvg: rotationMinutes / (state.tacticsDraft.rotation.length || 1),
    roleDiversity: offenseCount.size / (allRows.length || 1),
    offenseCount,
    defenseCount,
    warnings,
  };
}

function rowHealthState(row, insights) {
  const minute = Number(row.minutes || 0);
  const dCount = insights.defenseCount.get(row.defenseRole) || 0;
  if (minute < 8 || minute > 40) return { cls: 'warn', text: 'MIN' };
  if (dCount > 1) return { cls: 'err', text: 'DUP' };
  return { cls: 'ok', text: 'OK' };
}

function buildLineupRowHtml(group, idx, row, defenseRoles, insights) {
  const players = state.rosterRows || [];
  const playerOptions = ['<option value="">- 선택 -</option>', ...players.map((r) => `<option value="${r.player_id}" ${String(r.player_id) === String(row.pid) ? "selected" : ""}>${r.name || r.player_id}</option>`)].join("");
  const offOptions = TACTICS_OFFENSE_ROLES.map((role) => `<option value="${role}" ${role === row.offenseRole ? "selected" : ""}>${tacticDisplayLabel(role)}</option>`).join("");
  const defOptions = defenseRoles.map((role) => `<option value="${role}" ${role === row.defenseRole ? "selected" : ""}>${tacticDisplayLabel(role)}</option>`).join("");
  const health = rowHealthState(row, insights);
  return `
    <div class="tactics-lineup-row" data-group="${group}" data-idx="${idx}">
      <select data-field="pid" class="ui-select">${playerOptions}</select>
      <select data-field="offenseRole" class="ui-select">${offOptions}</select>
      <select data-field="defenseRole" class="ui-select">${defOptions}</select>
      <input data-field="minutes" type="number" min="0" max="48" value="${Number(row.minutes || 0)}" />
      <span class="tactics-role-badge ${health.cls}">${health.text}</span>
    </div>
  `;
}

function validateDefenseRoleUnique(changedEl, nextValue) {
  const all = [...document.querySelectorAll('.tactics-lineup-row select[data-field="defenseRole"]')];
  const dup = all.find((el) => el !== changedEl && el.value === nextValue);
  return !dup;
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
            const msg = '수비 역할은 중복 선택할 수 없습니다.';
            if (els.tacticsTotalMessage) els.tacticsTotalMessage.textContent = msg;
            rowEl.classList.add('is-edited');
            setTimeout(() => rowEl.classList.remove('is-edited'), 800);
            renderTacticsScreen();
            return;
          }
          target.defenseRole = control.value;
        } else if (field === 'minutes') {
          target.minutes = Math.max(0, Math.min(48, Number(control.value || 0)));
        } else {
          target[field] = control.value;
        }
        rowEl.classList.add('is-edited');
        setTimeout(() => rowEl.classList.remove('is-edited'), 700);
        renderTacticsScreen();
      });
    });
  });
}

function renderTacticsRosterList() {
  els.tacticsRosterList.innerHTML = (state.rosterRows || []).length
    ? state.rosterRows.map((r) => `<div class="tactics-roster-item"><span>${r.name || r.player_id}</span><span class="tactics-roster-meta">${r.pos || '-'}</span></div>`).join("")
    : '<p class="empty-copy">로스터 데이터가 없습니다.</p>';
}

function renderTacticsInsights(insights) {
  if (!els.tacticsKpiTotal) return;

  els.tacticsKpiTotal.textContent = `${insights.totalMinutes} / 240`;
  els.tacticsKpiStarters.textContent = `${insights.starterAvg.toFixed(1)}분`;
  els.tacticsKpiRotation.textContent = `${insights.rotationAvg.toFixed(1)}분`;
  els.tacticsKpiDiversity.textContent = `${Math.round(insights.roleDiversity * 100)}%`;

  const totalChip = els.tacticsKpiTotal.closest('.tactics-kpi-chip');
  if (totalChip) totalChip.classList.toggle('kpi-warn', insights.minutesDelta !== 0);

  if (els.tacticsTotalBalance) els.tacticsTotalBalance.textContent = `${insights.totalMinutes} / 240`;
  if (els.tacticsTotalBar) {
    const pct = Math.max(0, Math.min(100, Math.round((insights.totalMinutes / 240) * 100)));
    els.tacticsTotalBar.style.width = `${pct}%`;
    els.tacticsTotalBar.classList.toggle('warn', insights.minutesDelta !== 0);
  }
  if (els.tacticsTotalMessage) {
    els.tacticsTotalMessage.textContent = insights.minutesDelta === 0
      ? '출전시간 분배가 안정적입니다.'
      : `240분 기준에서 ${Math.abs(insights.minutesDelta)}분 ${insights.minutesDelta > 0 ? '부족' : '초과'} 상태입니다.`;
  }

  if (els.tacticsRoleCoverage) {
    const topOff = [...insights.offenseCount.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4);
    els.tacticsRoleCoverage.innerHTML = topOff.length
      ? topOff.map(([role, count]) => `<div class="tactics-pill">${tacticDisplayLabel(role)} · ${count}명</div>`).join('')
      : '<p class="empty-copy">역할 데이터가 없습니다.</p>';
  }

  if (els.tacticsWarningList) {
    const warnings = insights.warnings.slice(0, 5);
    els.tacticsWarningList.innerHTML = warnings.length
      ? warnings.map((w) => `<div class="tactics-warning-item ${w.level}">${w.text}</div>`).join('')
      : '<div class="tactics-warning-item">현재 치명적인 전술 경고가 없습니다.</div>';
  }

  if (els.tacticsHeroSub) {
    const offLabel = tacticDisplayLabel(tacticsSchemeLabel(TACTICS_OFFENSE_SCHEMES, state.tacticsDraft.offenseScheme));
    const defLabel = tacticDisplayLabel(tacticsSchemeLabel(TACTICS_DEFENSE_SCHEMES, state.tacticsDraft.defenseScheme));
    els.tacticsHeroSub.textContent = `${offLabel} × ${defLabel} 조합으로 운영 중`;
  }
}

function renderTacticsScreen() {
  if (!state.tacticsDraft) return;
  const defRoles = getDefenseRolesForScheme(state.tacticsDraft.defenseScheme);
  const insights = computeTacticsInsights();

  if (els.tacticsOffenseCurrent) els.tacticsOffenseCurrent.textContent = tacticDisplayLabel(tacticsSchemeLabel(TACTICS_OFFENSE_SCHEMES, state.tacticsDraft.offenseScheme));
  if (els.tacticsDefenseCurrent) els.tacticsDefenseCurrent.textContent = tacticDisplayLabel(tacticsSchemeLabel(TACTICS_DEFENSE_SCHEMES, state.tacticsDraft.defenseScheme));

  els.tacticsStarters.innerHTML = state.tacticsDraft.starters.map((r, i) => buildLineupRowHtml('starters', i, r, defRoles, insights)).join('');
  els.tacticsRotation.innerHTML = state.tacticsDraft.rotation.map((r, i) => buildLineupRowHtml('rotation', i, r, defRoles, insights)).join('');

  renderTacticsRosterList();
  renderTacticsInsights(insights);
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
els.collegeLeaderPosFilter?.addEventListener("change", () => {
  state.collegeLeaderPosFilter = els.collegeLeaderPosFilter.value || "ALL";
  loadCollegeLeaders().catch((e) => alert(e.message));
});
els.collegeLeaderTeamFilter?.addEventListener("change", () => {
  state.collegeLeaderTeamFilter = els.collegeLeaderTeamFilter.value || "ALL";
  loadCollegeLeaders().catch((e) => alert(e.message));
});
els.collegeBigboardModalClose?.addEventListener("click", () => closeCollegeBigboardModal());
els.collegeBigboardModalBackdrop?.addEventListener("click", () => closeCollegeBigboardModal());
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !els.collegeBigboardModal?.classList.contains("hidden")) {
    closeCollegeBigboardModal();
  }
  if (event.key === "Escape" && !els.collegeScoutPlayerModal?.classList.contains("hidden")) {
    closeScoutPlayerModal();
  }
  if (event.key === "Escape" && !els.collegeScoutReportsModal?.classList.contains("hidden")) {
    closeScoutReportsModal();
  }
});
els.collegeScoutCards?.addEventListener("click", async (event) => {
  const target = event.target instanceof HTMLElement ? event.target.closest("button[data-action]") : null;
  if (!target) return;
  const action = target.dataset.action;
  const scoutId = String(target.dataset.scoutId || "");
  if (!scoutId) return;
  if (action === "pick-player") {
    openScoutPlayerModal(scoutId);
    return;
  }
  if (action === "open-reports") {
    openScoutReportsModal(scoutId);
    return;
  }
});

els.collegeBigboardDetailBackBtn?.addEventListener("click", () => closeCollegeBigboardDetailScreen());


els.collegeScoutPlayerSearch?.addEventListener("input", () => {
  state.scoutingPlayerSearch = els.collegeScoutPlayerSearch.value || "";
  queueScoutingPlayerSearch();
});

els.collegeScoutPlayerStatus?.addEventListener("change", () => {
  state.scoutingPlayerSearchStatus = els.collegeScoutPlayerStatus.value || "ALL";
  queueScoutingPlayerSearch();
});

els.collegeScoutPlayerLoadMore?.addEventListener("click", () => {
  searchScoutingPlayers({ append: true }).catch((e) => {
    state.scoutingPlayerSearchError = e?.message || "선수 검색 중 오류가 발생했습니다.";
    state.scoutingPlayerSearchLoading = false;
    renderScoutPlayerList();
  });
});

els.collegeScoutPlayerList?.addEventListener("click", async (event) => {
  const option = event.target instanceof HTMLElement ? event.target.closest(".college-player-option") : null;
  if (!option) return;
  const playerId = String(option.dataset.playerId || "");
  const scoutId = String(state.scoutingActiveScoutId || "");
  if (!scoutId || !playerId) return;

  const scout = state.scoutingScouts.find((item) => String(item?.scout_id || "") === scoutId);
  const player = state.scoutingPlayerSearchResults.find((item) => String(item?.player_id || "") === playerId)
    || state.scoutingPlayerLookup[playerId]
    || null;
  if (String(scout?.active_assignment?.target_player_id || "") === playerId) {
    setCollegeScoutingFeedback("이미 이 선수에게 배정된 스카우터입니다.", "warn");
    closeScoutPlayerModal();
    return;
  }

  if (scout?.active_assignment?.assignment_id) {
    const ok = await showConfirmModal({
      title: "스카우팅 배정 교체",
      body: `${scout?.display_name || scoutId}의 기존 배정을 종료하고 ${player?.name || playerId}로 변경하시겠습니까?`,
      okLabel: "교체",
      cancelLabel: "취소",
    });
    if (!ok) return;
  }

  try {
    if (scout?.active_assignment?.assignment_id) {
      await fetchJson("/api/scouting/unassign", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ team_id: state.selectedTeamId, scout_id: scoutId })
      });
    }
    await fetchJson("/api/scouting/assign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ team_id: state.selectedTeamId, scout_id: scoutId, player_id: playerId, target_kind: "COLLEGE" })
    });
    if (player && playerId) state.scoutingPlayerLookup[playerId] = player;
    await loadCollegeScouting();
    setCollegeScoutingFeedback(`${scout?.display_name || scoutId} → ${player?.name || playerId} 배정 완료`, "ok");
    closeScoutPlayerModal();
  } catch (error) {
    setCollegeScoutingFeedback(error?.message || "배정 처리 중 오류가 발생했습니다.", "warn");
  }
});

els.collegeScoutPlayerModalClose?.addEventListener("click", closeScoutPlayerModal);
els.collegeScoutPlayerModalBackdrop?.addEventListener("click", closeScoutPlayerModal);
els.collegeScoutReportsModalClose?.addEventListener("click", closeScoutReportsModal);
els.collegeScoutReportsModalBackdrop?.addEventListener("click", closeScoutReportsModal);
els.trainingTypeButtons.querySelectorAll("button[data-training-type]").forEach((btn) => {
  btn.addEventListener("click", () => renderTrainingDetail(btn.dataset.trainingType).catch((e) => alert(e.message)));
});
els.backToMainBtn.addEventListener("click", () => showMainScreen());
els.backToRosterBtn.addEventListener("click", () => activateScreen(els.myTeamScreen));

if (els.myTeamSortControls) {
  els.myTeamSortControls.querySelectorAll('.myteam-chip[data-sort]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.myTeamSortKey = btn.dataset.sort || 'ovr';
      rerenderMyTeamBoard();
    });
  });
}
if (els.myTeamFilterControls) {
  els.myTeamFilterControls.querySelectorAll('.myteam-chip[data-filter]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const key = btn.dataset.filter;
      state.myTeamFilters[key] = !state.myTeamFilters[key];
      rerenderMyTeamBoard();
    });
  });
}

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
