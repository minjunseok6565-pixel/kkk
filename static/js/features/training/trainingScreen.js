import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import { fetchCachedJson, getCachedValue, setLoading } from "../../core/api.js";
import { CACHE_TTL_MS, buildCacheKeys } from "../../app/cachePolicy.js";
import { fetchInGameDate } from "../main/mainScreen.js";
import { buildCalendar4Weeks, renderTrainingCalendar } from "./trainingCalendar.js";
import { renderTrainingSummaryStrip, renderTrainingContextPanel, refreshTrainingTypeButtonsState } from "./trainingDetail.js";
import { activateTrainingTab } from "./playerTrainingTab.js";

const TRAINING_SESSION_FETCH_CONCURRENCY = 4;
const TRAINING_SESSION_RENDER_BATCH_MS = 120;
let trainingRequestSeq = 0;

function normalizeTrainingTeamId(teamId) {
  return String(teamId || state.selectedTeamId || "").toUpperCase();
}

function buildTrainingRangeFromCurrentDate(currentDate) {
  const normalizedCurrentDate = String(currentDate || "").slice(0, 10);
  if (!normalizedCurrentDate) return null;
  const allDays = buildCalendar4Weeks(normalizedCurrentDate);
  if (!allDays.length) return null;
  const from = allDays[0];
  const to = allDays[allDays.length - 1];
  if (!from || !to) return null;
  return {
    from,
    to,
    allDays,
  };
}

function setTrainingPrefetchContext(context = {}) {
  const normalizedTeamId = normalizeTrainingTeamId(context.teamId);
  const from = String(context.from || "").slice(0, 10);
  const to = String(context.to || "").slice(0, 10);
  const currentDate = String(context.currentDate || "").slice(0, 10);
  if (!normalizedTeamId || !from || !to) return null;
  state.trainingPrefetchContext = {
    teamId: normalizedTeamId,
    from,
    to,
    currentDate,
    updatedAt: Date.now(),
  };
  return state.trainingPrefetchContext;
}

function getTrainingPrefetchContext({ teamId = state.selectedTeamId, currentDate = state.currentDate } = {}) {
  const normalizedTeamId = normalizeTrainingTeamId(teamId);
  if (!normalizedTeamId) return null;

  const cached = state.trainingPrefetchContext;
  if (cached
    && String(cached.teamId || "").toUpperCase() === normalizedTeamId
    && cached.from
    && cached.to) {
    return {
      teamId: normalizedTeamId,
      from: String(cached.from),
      to: String(cached.to),
      currentDate: String(cached.currentDate || currentDate || "").slice(0, 10),
    };
  }

  const normalizedCurrentDate = String(currentDate || "").slice(0, 10);
  if (!normalizedCurrentDate) return null;
  const computed = buildTrainingRangeFromCurrentDate(normalizedCurrentDate);
  if (!computed) return null;
  return {
    teamId: normalizedTeamId,
    currentDate: normalizedCurrentDate,
    from: computed.from,
    to: computed.to,
  };
}

function createSessionUpdateScheduler(requestSeq) {
  let timerId = null;
  return (flush) => {
    if (timerId != null) return;
    timerId = window.setTimeout(() => {
      timerId = null;
      if (requestSeq !== trainingRequestSeq) return;
      flush();
    }, TRAINING_SESSION_RENDER_BATCH_MS);
  };
}

async function runWithConcurrency(items, worker, concurrency = 4) {
  const queue = Array.isArray(items) ? items : [];
  const limit = Math.max(1, Number(concurrency) || 1);
  let index = 0;

  async function next() {
    while (index < queue.length) {
      const current = index;
      index += 1;
      await worker(queue[current]);
    }
  }

  await Promise.all(Array.from({ length: Math.min(limit, queue.length) }, () => next()));
}

async function hydrateMissingSessions({
  teamId,
  sessions,
  missingDates,
  requestSeq,
  onSessionsUpdated,
}) {
  if (!missingDates.length) return;
  const scheduleUpdate = createSessionUpdateScheduler(requestSeq);

  await runWithConcurrency(missingDates, async (dateIso) => {
    try {
      const res = await fetchCachedJson({
        key: `training:session:${teamId}:${dateIso}`,
        url: `/api/practice/team/${encodeURIComponent(teamId)}/session?date_iso=${encodeURIComponent(dateIso)}`,
        ttlMs: CACHE_TTL_MS.training,
        staleWhileRevalidate: true,
      });
      sessions[dateIso] = { session: res.session, is_user_set: res.is_user_set };
      if (typeof onSessionsUpdated === "function") {
        scheduleUpdate(() => {
          onSessionsUpdated();
        });
      }
    } catch (e) {
      // fail-soft
    }
  }, TRAINING_SESSION_FETCH_CONCURRENCY);

  if (requestSeq === trainingRequestSeq && typeof onSessionsUpdated === "function") {
    onSessionsUpdated();
  }
}

async function prefetchTrainingCoreData({
  teamId,
  currentDate = null,
  requestSeq = trainingRequestSeq,
  onSessionsUpdated = null,
  progressiveSessionHydration = true,
} = {}) {
  const normalizedTeamId = normalizeTrainingTeamId(teamId);
  if (!normalizedTeamId) return null;

  const resolvedCurrentDate = String(currentDate || state.currentDate || await fetchInGameDate() || "").slice(0, 10);
  state.currentDate = resolvedCurrentDate;
  const computedRange = buildTrainingRangeFromCurrentDate(resolvedCurrentDate);
  if (!computedRange) return null;
  const { allDays, from, to } = computedRange;
  state.trainingCalendarDays = allDays;
  setTrainingPrefetchContext({ teamId: normalizedTeamId, currentDate: resolvedCurrentDate, from, to });

  const keys = buildCacheKeys(normalizedTeamId, { from, to });

  const schedule = await fetchCachedJson({
    key: keys.trainingSchedule,
    url: `/api/team-schedule/${encodeURIComponent(normalizedTeamId)}?view=light`,
    ttlMs: CACHE_TTL_MS.training,
    staleWhileRevalidate: true,
  });
  const gameByDate = {};
  (schedule.games || []).forEach((g) => {
    const d = String(g.date || "").slice(0, 10);
    if (!d) return;
    const opp = g.home_team_id === normalizedTeamId ? g.away_team_id : g.home_team_id;
    gameByDate[d] = String(opp || "").toUpperCase();
  });

  const resolved = await fetchCachedJson({
    key: keys.trainingSessionsResolve,
    url: `/api/practice/team/${encodeURIComponent(normalizedTeamId)}/sessions/resolve?date_from=${encodeURIComponent(from)}&date_to=${encodeURIComponent(to)}&only_missing=true&include_games=false`,
    ttlMs: CACHE_TTL_MS.training,
    staleWhileRevalidate: true,
  });
  const sessions = { ...(resolved.sessions || {}) };

  const previewDates = allDays.filter((d) => d >= resolvedCurrentDate && !gameByDate[d]);
  const missingDates = previewDates.filter((d) => !sessions[d]);

  const teamDetail = await fetchCachedJson({
    key: keys.trainingTeamDetail,
    url: `/api/team-detail/${encodeURIComponent(normalizedTeamId)}?view=light`,
    ttlMs: CACHE_TTL_MS.training,
    staleWhileRevalidate: true,
  });
  state.trainingRoster = teamDetail.roster || [];
  state.playerTrainingRoster = state.trainingRoster.map((row) => ({ ...row }));

  const [offFam, defFam] = await Promise.all([
    fetchCachedJson({
      key: keys.trainingFamiliarityOffense,
      url: `/api/readiness/team/${encodeURIComponent(normalizedTeamId)}/familiarity?scheme_type=offense`,
      ttlMs: CACHE_TTL_MS.training,
      staleWhileRevalidate: true,
    }).catch(() => ({ items: [] })),
    fetchCachedJson({
      key: keys.trainingFamiliarityDefense,
      url: `/api/readiness/team/${encodeURIComponent(normalizedTeamId)}/familiarity?scheme_type=defense`,
      ttlMs: CACHE_TTL_MS.training,
      staleWhileRevalidate: true,
    }).catch(() => ({ items: [] })),
  ]);
  state.trainingFamiliarity = { offense: offFam.items || [], defense: defFam.items || [] };

  state.trainingSessionsByDate = sessions;
  state.trainingGameByDate = gameByDate;

  if (progressiveSessionHydration) {
    void hydrateMissingSessions({
      teamId: normalizedTeamId,
      sessions,
      missingDates,
      requestSeq,
      onSessionsUpdated,
    });
  } else {
    await hydrateMissingSessions({
      teamId: normalizedTeamId,
      sessions,
      missingDates,
      requestSeq,
      onSessionsUpdated,
    });
  }

  return {
    teamId: normalizedTeamId,
    currentDate: resolvedCurrentDate,
    from,
    to,
    missingDates,
  };
}

async function loadTrainingData({
  progressiveSessionHydration = false,
  requestSeq = trainingRequestSeq,
  onSessionsUpdated = null,
} = {}) {
  if (!state.selectedTeamId) return;
  const teamId = String(state.selectedTeamId || "").toUpperCase();
  await prefetchTrainingCoreData({
    teamId,
    currentDate: state.currentDate,
    requestSeq,
    onSessionsUpdated,
    progressiveSessionHydration,
  });
}

async function showTrainingScreen() {
  if (!state.selectedTeamId) {
    alert("먼저 팀을 선택해주세요.");
    return;
  }
  const teamId = String(state.selectedTeamId || "").toUpperCase();
  const cacheKey = buildCacheKeys(teamId).trainingTeamDetail;
  const requestSeq = trainingRequestSeq + 1;
  trainingRequestSeq = requestSeq;
  const hasCached = Boolean(getCachedValue(cacheKey));
  if (!hasCached) setLoading(true, "훈련 화면 데이터를 불러오는 중...");
  try {
    state.trainingSelectedDates = new Set();
    state.trainingActiveType = null;
    state.trainingTab = "team";
    state.playerTrainingSelectedPlayerId = null;
    state.playerTrainingPlansByPlayerId = {};
    state.playerTrainingDraft = { primary: "BALANCED", secondary: "", intensity: "MED" };
    state.playerTrainingSaving = false;
    state.playerTrainingStatus = { tone: "", message: "" };
    const rerenderTrainingScreen = () => {
      if (requestSeq !== trainingRequestSeq) return;
      renderTrainingSummaryStrip();
      renderTrainingCalendar();
      refreshTrainingTypeButtonsState();
      renderTrainingContextPanel();
    };

    await loadTrainingData({
      progressiveSessionHydration: true,
      requestSeq,
      onSessionsUpdated: rerenderTrainingScreen,
    });
    if (requestSeq !== trainingRequestSeq) return;
    rerenderTrainingScreen();
    els.trainingDetailPanel.innerHTML = '<p class="empty-copy">캘린더에서 날짜를 선택하고 훈련 버튼을 눌러 세부 설정을 확인하세요.</p>';
    await activateTrainingTab("team");
    activateScreen(els.trainingScreen);
  } finally {
    if (requestSeq === trainingRequestSeq) setLoading(false);
  }
}

export { prefetchTrainingCoreData, loadTrainingData, showTrainingScreen, getTrainingPrefetchContext, setTrainingPrefetchContext };
