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
  marketTradeDealDraft: null,
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

function resetMarketTradeInboxState() {
  state.marketTradeInboxRows = [];
  state.marketTradeInboxGrouped = [];
  state.marketTradeInboxLoading = false;
  state.marketTradeInboxLastLoadedAt = 0;
}

function resetMarketTradeDealState() {
  state.marketTradeActiveSession = null;
  state.marketTradeDealDraft = createEmptyMarketTradeDealDraft();
  state.marketTradeAssetPool = createEmptyMarketTradeAssetPool();
  state.marketTradeUi = createEmptyMarketTradeUi();
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
  resetMarketTradeInboxState,
  resetMarketTradeDealState,
  resetMarketTradeState,
};
