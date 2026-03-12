const state = {
  lastSaveSlotId: null,
  selectedTeamId: null,
  selectedTeamName: "",
  currentDate: "",
  viewCache: {},
  inflightRequests: new Map(),
  lastGameResult: null,
  gameResultPbp: {
    period: "ALL",
    team: "ALL",
    tags: new Set(),
    onlyKey: false,
    expandedGroups: new Set(),
    renderLimit: 80,
    hydrated: false,
    hydratedGameId: null,
    cachedResult: null,
    cachedPbp: null,
  },
  rosterRows: [],
  selectedPlayerId: null,
  marketSubTab: "fa",
  marketFaRows: [],
  marketTradeBlockRows: [],
  marketTradeBlockScope: "other",
  marketTradeBlockMyRows: [],
  marketTradeBlockRosterRows: [],
  marketTradeBlockSelectedRosterPlayerId: null,
  marketTradeBlockRosterModalOpen: false,
  marketTradeBlockRosterModalBound: false,
  marketTradeInboxRows: [],
  marketTradeInboxGrouped: [],
  marketTradeContractViolations: [],
  marketTradeContractViolationSeen: {},
  marketTradeInboxLoading: false,
  marketTradeInboxLastLoadedAt: 0,
  marketScreenActive: false,
  tradeDealModalOpen: false,
  marketTradeActiveSession: null,
  marketTradeSessionFsm: {
    status: "idle",
    sessionId: null,
    updatedAt: null,
    reason: "",
  },
  marketTradeRequestSeq: 0,
  marketTradeRequestScopes: {},
  marketTradePendingActions: {},
  marketTaskQueueByScope: {},
  marketTradeInitialOfferSnapshot: null,
  marketTradeLatestOfferSnapshot: null,
  marketTradeDealDraft: null,
  marketTradeDealTabs: {
    myTeamAssetTab: "player",
    otherTeamAssetTab: "player",
  },
  marketTradeAssetPool: {
    myTeam: { players: [], picks: [], swaps: [], fixedAssets: [] },
    otherTeam: { players: [], picks: [], swaps: [], fixedAssets: [] },
  },
  marketTradeUi: {
    selectedAssets: { myTeam: [], otherTeam: [] },
    validationErrors: [],
    submitPending: false,
    rejectPending: false,
  },
  marketSelectedPlayerId: null,
  marketNegotiation: null,
  marketTradeModalPlayerId: null,
  marketTradeModalOtherTeamId: null,
  marketTradeNegotiationSession: null,
  marketTradeModalBound: false,
  playerDetailBackTarget: "myteam",
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
  tacticsDraftTeamId: null,
  tacticsDirty: false,
  tacticsSaving: false,
  medicalOverview: null,
  medicalSelectedPlayerId: null,
  myTeamSortKey: "ovr",
  myTeamFilters: { risk: false, highsalary: false },
  selectedCollegeLeaderPlayerId: null,
  selectedCollegeBigboardExpertId: null,
  collegeBigboardLastTriggerExpertId: null,
  collegeBigboardOverview: [],
  collegeBigboardByExpert: {},
  collegeTabLoaded: { leaders: false, bigboard: false, scouting: false },
  collegeTabLoading: { leaders: false, bigboard: false, scouting: false },
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
  offseasonDev: {
    step: "IDLE", // IDLE | ENTERED_OFFSEASON | COLLEGE_FINALIZED | COLLEGE_RESULTS_LOADED | DECLARERS_LOADED | TEAM_OPTIONS | CONTRACTS_PROCESSED | PLAYER_OPTIONS_LOADED | EXPIRED_CONTRACTS_LOADED | RETIREMENT_PROCESSED | LOTTERY_SETTLED
    loading: false,
    error: "",
    championTeamId: "",
    enterOffseasonResult: null,
    collegeFinalizeResult: null,
    collegeTeamsResult: null,
    collegeLeadersResult: null,
    collegeDeclarersResult: null,
    pendingTeamOptions: [],
    teamOptionDecisions: {},
    contractsProcessResult: null,
    playerOptionResults: null,
    expiredContractsResult: null,
    expiredNegotiationSessions: {},
    retirementProcessResult: null,
    draftLotteryResult: null,
    draftSettleResult: null,
  },
};

function createEmptyMarketTradeDealDraft() {
  return {
    sessionId: null,
    teams: [],
    legs: {},
    meta: {},
  };
}

function createEmptyMarketTradeAssetPool() {
  return {
    myTeam: { players: [], picks: [], swaps: [], fixedAssets: [] },
    otherTeam: { players: [], picks: [], swaps: [], fixedAssets: [] },
  };
}

function createEmptyMarketTradeUi() {
  return {
    selectedAssets: { myTeam: [], otherTeam: [] },
    validationErrors: [],
    submitPending: false,
    rejectPending: false,
  };
}

function createDefaultMarketTradeDealTabs() {
  return {
    myTeamAssetTab: "player",
    otherTeamAssetTab: "player",
  };
}

function createEmptyMarketTradeOfferSnapshot() {
  return {
    sessionId: null,
    deal: null,
    capturedAt: null,
  };
}

function getInitialMarketTradeSessionFsm() {
  return {
    status: "idle",
    sessionId: null,
    updatedAt: null,
    reason: "",
  };
}

function canTransitionMarketTradeSessionFsm(fromStatus, toStatus) {
  const from = String(fromStatus || "idle");
  const to = String(toStatus || "");
  if (!to) return false;

  const allowedMap = {
    idle: new Set(["opening"]),
    opening: new Set(["ready", "closed"]),
    ready: new Set(["submitting", "closed"]),
    submitting: new Set(["ready", "closed"]),
    closed: new Set(["opening", "idle"]),
  };

  return Boolean(allowedMap[from]?.has(to));
}

function transitionMarketTradeSessionFsm(nextStatus, { sessionId = null, reason = "", strict = true } = {}) {
  const current = state.marketTradeSessionFsm || getInitialMarketTradeSessionFsm();
  const from = String(current?.status || "idle");
  const to = String(nextStatus || "");
  const valid = canTransitionMarketTradeSessionFsm(from, to);

  if (!valid && strict) {
    console.warn("[trade-fsm] ignored invalid transition", {
      from,
      to,
      currentSessionId: current?.sessionId ?? null,
      nextSessionId: sessionId == null ? null : String(sessionId),
      reason: String(reason || ""),
    });
    return {
      ok: false,
      ignored: true,
      from,
      to,
      current: state.marketTradeSessionFsm,
    };
  }

  const next = {
    status: to || from,
    sessionId: sessionId == null ? null : String(sessionId),
    updatedAt: new Date().toISOString(),
    reason: String(reason || ""),
  };
  state.marketTradeSessionFsm = next;
  return {
    ok: true,
    ignored: false,
    from,
    to: next.status,
    current: next,
  };
}

function resetMarketTradeInboxState() {
  state.marketTradeInboxRows = [];
  state.marketTradeInboxGrouped = [];
  state.marketTradeContractViolations = [];
  state.marketTradeContractViolationSeen = {};
  state.marketTradeInboxLoading = false;
  state.marketTradeInboxLastLoadedAt = 0;
}

function resetTradeDealModalContext({ includeSession = true, includeTabs = true } = {}) {
  if (includeSession) {
    state.marketTradeActiveSession = null;
    state.marketTradeSessionFsm = getInitialMarketTradeSessionFsm();
  }
  state.marketTradeDealDraft = createEmptyMarketTradeDealDraft();
  state.marketTradeAssetPool = createEmptyMarketTradeAssetPool();
  state.marketTradeInitialOfferSnapshot = createEmptyMarketTradeOfferSnapshot();
  state.marketTradeLatestOfferSnapshot = createEmptyMarketTradeOfferSnapshot();
  state.marketTradeUi = createEmptyMarketTradeUi();
  if (includeTabs) {
    state.marketTradeDealTabs = createDefaultMarketTradeDealTabs();
  }
}

function resetMarketTradeDealState() {
  state.marketTradeActiveSession = null;
  state.tradeDealModalOpen = false;
  state.marketTradeSessionFsm = getInitialMarketTradeSessionFsm();
  state.marketTradeRequestScopes = {};
  state.marketTradePendingActions = {};
  state.marketTaskQueueByScope = {};
  state.marketTradeContractViolations = [];
  state.marketTradeContractViolationSeen = {};
  resetTradeDealModalContext({ includeSession: true, includeTabs: true });
}

function syncMarketTradeModalSessionState(sessionId, { keepTabsOnReopen = true } = {}) {
  const nextSessionId = sessionId == null ? null : String(sessionId);
  const prevSessionId = state.marketTradeActiveSession?.session_id
    ? String(state.marketTradeActiveSession.session_id)
    : null;
  const isSameSession = Boolean(nextSessionId) && Boolean(prevSessionId) && nextSessionId === prevSessionId;

  // 세션이 바뀌면 협상 모달 컨텍스트를 reset한다.
  if (!isSameSession) {
    resetTradeDealModalContext({ includeSession: true, includeTabs: true });
    return {
      isSameSession: false,
      didReset: true,
      prevSessionId,
      nextSessionId,
    };
  }

  // 동일 세션 재오픈 시 탭 유지 정책을 선택적으로 적용한다.
  if (!keepTabsOnReopen) {
    state.marketTradeDealTabs = createDefaultMarketTradeDealTabs();
  }
  return {
    isSameSession: true,
    didReset: false,
    prevSessionId,
    nextSessionId,
  };
}

function resetMarketTradeState() {
  resetMarketTradeInboxState();
  resetMarketTradeDealState();
}

resetMarketTradeDealState();

export {
  state,
  createEmptyMarketTradeDealDraft,
  createEmptyMarketTradeAssetPool,
  createEmptyMarketTradeUi,
  createDefaultMarketTradeDealTabs,
  createEmptyMarketTradeOfferSnapshot,
  getInitialMarketTradeSessionFsm,
  canTransitionMarketTradeSessionFsm,
  transitionMarketTradeSessionFsm,
  resetMarketTradeInboxState,
  resetTradeDealModalContext,
  resetMarketTradeDealState,
  resetMarketTradeState,
  syncMarketTradeModalSessionState,
};
