import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import { fetchCachedJson, getCachedValue, setLoading } from "../../core/api.js";
import { fetchInGameDate } from "../main/mainScreen.js";
import { buildCalendar4Weeks, renderTrainingCalendar } from "./trainingCalendar.js";
import { renderTrainingSummaryStrip, renderTrainingContextPanel, refreshTrainingTypeButtonsState } from "./trainingDetail.js";

const TRAINING_CACHE_TTL_MS = 10000;
let trainingRequestSeq = 0;

async function loadTrainingData() {
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
  const stored = await fetchCachedJson({
    key: `training:sessions:${teamId}:${from}:${to}`,
    url: `/api/practice/team/${encodeURIComponent(teamId)}/sessions?date_from=${encodeURIComponent(from)}&date_to=${encodeURIComponent(to)}`,
    ttlMs: TRAINING_CACHE_TTL_MS,
    staleWhileRevalidate: true,
  });
  const sessions = { ...(stored.sessions || {}) };

  const previewDates = allDays.filter((d) => d >= currentDate && !gameByDate[d]);
  await Promise.all(previewDates.map(async (d) => {
    if (sessions[d]) return;
    try {
      const res = await fetchCachedJson({
        key: `training:session:${teamId}:${d}`,
        url: `/api/practice/team/${encodeURIComponent(teamId)}/session?date_iso=${encodeURIComponent(d)}`,
        ttlMs: TRAINING_CACHE_TTL_MS,
        staleWhileRevalidate: true,
      });
      sessions[d] = { session: res.session, is_user_set: res.is_user_set };
    } catch (e) {
      // fail-soft
    }
  }));

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
    await loadTrainingData();
    if (requestSeq !== trainingRequestSeq) return;
    renderTrainingSummaryStrip();
    renderTrainingCalendar();
    refreshTrainingTypeButtonsState();
    renderTrainingContextPanel();
    els.trainingDetailPanel.innerHTML = '<p class="empty-copy">캘린더에서 날짜를 선택하고 훈련 버튼을 눌러 세부 설정을 확인하세요.</p>';
    activateScreen(els.trainingScreen);
  } finally {
    if (requestSeq === trainingRequestSeq) setLoading(false);
  }
}

export { loadTrainingData, showTrainingScreen };
