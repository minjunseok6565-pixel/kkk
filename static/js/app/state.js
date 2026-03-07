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
  marketTradeInboxLoading: false,
  marketTradeInboxLastLoadedAt: 0,
  marketTradeActiveSession: null,
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

function resetMarketTradeInboxState() {
  state.marketTradeInboxRows = [];
  state.marketTradeInboxGrouped = [];
  state.marketTradeInboxLoading = false;
  state.marketTradeInboxLastLoadedAt = 0;
}

function resetMarketTradeDealState() {
  state.marketTradeActiveSession = null;
  state.marketTradeInitialOfferSnapshot = createEmptyMarketTradeOfferSnapshot();
  state.marketTradeLatestOfferSnapshot = createEmptyMarketTradeOfferSnapshot();
  state.marketTradeDealDraft = createEmptyMarketTradeDealDraft();
  state.marketTradeDealTabs = createDefaultMarketTradeDealTabs();
  state.marketTradeAssetPool = createEmptyMarketTradeAssetPool();
  state.marketTradeUi = createEmptyMarketTradeUi();
}

function syncMarketTradeModalSessionState(sessionId, { keepTabsOnReopen = true } = {}) {
  const nextSessionId = sessionId == null ? null : String(sessionId);
  const currentSessionId = state.marketTradeActiveSession?.session_id
    ? String(state.marketTradeActiveSession.session_id)
    : null;
  const isSameSession = Boolean(nextSessionId) && Boolean(currentSessionId) && nextSessionId === currentSessionId;

  // 세션이 바뀌면 스냅샷/탭/UI 상태를 reset한다.
  if (!isSameSession) {
    state.marketTradeInitialOfferSnapshot = createEmptyMarketTradeOfferSnapshot();
    state.marketTradeLatestOfferSnapshot = createEmptyMarketTradeOfferSnapshot();
    state.marketTradeDealTabs = createDefaultMarketTradeDealTabs();
    state.marketTradeUi = createEmptyMarketTradeUi();
    return { isSameSession: false, didReset: true };
  }

  // 동일 세션 재오픈 시 탭 유지 정책을 선택적으로 적용한다.
  if (!keepTabsOnReopen) {
    state.marketTradeDealTabs = createDefaultMarketTradeDealTabs();
  }
  return { isSameSession: true, didReset: false };
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
  resetMarketTradeInboxState,
  resetMarketTradeDealState,
  resetMarketTradeState,
  syncMarketTradeModalSessionState,
};
