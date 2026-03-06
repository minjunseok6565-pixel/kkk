import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import { fetchCachedJson, getCachedValue, setLoading } from "../../core/api.js";
import { fetchInGameDate } from "../main/mainScreen.js";
import { buildCalendar4Weeks, renderTrainingCalendar } from "./trainingCalendar.js";
import { renderTrainingSummaryStrip, renderTrainingContextPanel, refreshTrainingTypeButtonsState } from "./trainingDetail.js";

const TRAINING_CACHE_TTL_MS = 10000;
const TRAINING_SESSION_FETCH_CONCURRENCY = 4;
const TRAINING_SESSION_RENDER_BATCH_MS = 120;
let trainingRequestSeq = 0;

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
        ttlMs: TRAINING_CACHE_TTL_MS,
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

async function loadTrainingData({
  progressiveSessionHydration = false,
  requestSeq = trainingRequestSeq,
  onSessionsUpdated = null,
} = {}) {
  if (!state.selectedTeamId) return;
  const teamId = String(state.selectedTeamId || "").toUpperCase();
  const currentDate = state.currentDate || await fetchInGameDate();
  state.currentDate = currentDate;
  const allDays = buildCalendar4Weeks(currentDate);
  state.trainingCalendarDays = allDays;

  const schedule = await fetchCachedJson({
    key: `training:schedule:${teamId}`,
    url: `/api/team-schedule/${encodeURIComponent(teamId)}`,
    ttlMs: TRAINING_CACHE_TTL_MS,
    staleWhileRevalidate: true,
  });
  const gameByDate = {};
  (schedule.games || []).forEach((g) => {
    const d = String(g.date || "").slice(0, 10);
    if (!d) return;
    const opp = g.home_team_id === teamId ? g.away_team_id : g.home_team_id;
    gameByDate[d] = String(opp || "").toUpperCase();
  });

  const from = allDays[0];
  const to = allDays[allDays.length - 1];
  const resolved = await fetchCachedJson({
    key: `training:sessions-resolve:${teamId}:${from}:${to}:nogame:missing`,
    url: `/api/practice/team/${encodeURIComponent(teamId)}/sessions/resolve?date_from=${encodeURIComponent(from)}&date_to=${encodeURIComponent(to)}&only_missing=true&include_games=false`,
    ttlMs: TRAINING_CACHE_TTL_MS,
    staleWhileRevalidate: true,
  });
  const sessions = { ...(resolved.sessions || {}) };

  const previewDates = allDays.filter((d) => d >= currentDate && !gameByDate[d]);
  const missingDates = previewDates.filter((d) => !sessions[d]);

  const teamDetail = await fetchCachedJson({
    key: `training:team-detail:${teamId}`,
    url: `/api/team-detail/${encodeURIComponent(teamId)}`,
    ttlMs: TRAINING_CACHE_TTL_MS,
    staleWhileRevalidate: true,
  });
  state.trainingRoster = teamDetail.roster || [];

  const [offFam, defFam] = await Promise.all([
    fetchCachedJson({
      key: `training:familiarity:${teamId}:offense`,
      url: `/api/readiness/team/${encodeURIComponent(teamId)}/familiarity?scheme_type=offense`,
      ttlMs: TRAINING_CACHE_TTL_MS,
      staleWhileRevalidate: true,
    }).catch(() => ({ items: [] })),
    fetchCachedJson({
      key: `training:familiarity:${teamId}:defense`,
      url: `/api/readiness/team/${encodeURIComponent(teamId)}/familiarity?scheme_type=defense`,
      ttlMs: TRAINING_CACHE_TTL_MS,
      staleWhileRevalidate: true,
    }).catch(() => ({ items: [] })),
  ]);
  state.trainingFamiliarity = { offense: offFam.items || [], defense: defFam.items || [] };

  state.trainingSessionsByDate = sessions;
  state.trainingGameByDate = gameByDate;

  if (progressiveSessionHydration) {
    void hydrateMissingSessions({
      teamId,
      sessions,
      missingDates,
      requestSeq,
      onSessionsUpdated,
    });
    return;
  }

  await hydrateMissingSessions({
    teamId,
    sessions,
    missingDates,
    requestSeq,
    onSessionsUpdated,
  });
}

async function showTrainingScreen() {
  if (!state.selectedTeamId) {
    alert("먼저 팀을 선택해주세요.");
    return;
  }
  const teamId = String(state.selectedTeamId || "").toUpperCase();
  const cacheKey = `training:team-detail:${teamId}`;
  const requestSeq = trainingRequestSeq + 1;
  trainingRequestSeq = requestSeq;
  const hasCached = Boolean(getCachedValue(cacheKey));
  if (!hasCached) setLoading(true, "훈련 화면 데이터를 불러오는 중...");
  try {
    state.trainingSelectedDates = new Set();
    state.trainingActiveType = null;
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
    activateScreen(els.trainingScreen);
  } finally {
    if (requestSeq === trainingRequestSeq) setLoading(false);
  }
}

export { loadTrainingData, showTrainingScreen };
