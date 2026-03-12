import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import { fetchJson, invalidateCachedValuesByPrefix, setLoading, showConfirmModal } from "../../core/api.js";
import { CACHE_EVENT_TYPES, getPrefetchPlanAfterGame, invalidateByEvent, runPrefetchPlan } from "../../app/cachePolicy.js";
import { formatIsoDate, formatWinPct } from "../../core/format.js";
import { num } from "../../core/guards.js";
import { TEAM_FULL_NAMES, applyTeamLogo, getTeamBranding, renderTeamLogoMark, getScheduleVenueText } from "../../core/constants/teams.js";
import { resetNextGameCard, renderHomePriorities, renderHomeActivityFeed, renderHomeRiskCalendar } from "./homeWidgets.js";
import { showGameResultScreenByGameId } from "../gameResult/gameResultScreen.js";

function showTeamSelection() { activateScreen(els.teamScreen); }

function isSameIsoDate(a, b) {
  return String(a || "").slice(0, 10) === String(b || "").slice(0, 10);
}

async function fetchMainDashboardRaw() {
  if (!state.selectedTeamId) throw new Error("먼저 팀을 선택해주세요.");
  return fetchJson(`/api/home/dashboard/${encodeURIComponent(state.selectedTeamId)}`);
}

function showMainScreen() {
  activateScreen(els.mainScreen);
  const teamName = state.selectedTeamName || state.selectedTeamId || "선택 팀";
  els.mainTeamTitle.textContent = teamName;
  void refreshMainDashboard();
}

function randomTipoffTime() {
  const hour24 = 14 + Math.floor(Math.random() * 6);
  const minute = Math.floor(Math.random() * 60);
  const hour12 = String(hour24 > 12 ? hour24 - 12 : hour24).padStart(2, "0");
  return `${hour12}:${String(minute).padStart(2, "0")} PM`;
}

const POST_GAME_PREFETCH_BUDGET_MS = 2000;

function getNowMs() {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return performance.now();
  }
  return Date.now();
}

function isCacheDebugEnabled() {
  return typeof globalThis !== "undefined" && Boolean(globalThis.__CACHE_DEBUG__);
}

function debugPostGamePrefetch(message, meta = {}) {
  if (!isCacheDebugEnabled()) return;
  console.debug(`[cache][post-game] ${message}`, meta);
}

function getPrefetchTier(item) {
  const tier = Number(item?.priorityTier);
  if (Number.isFinite(tier) && tier > 0) return tier;
  return 3;
}

function deferTask(task) {
  if (typeof task !== "function") return;
  if (typeof globalThis !== "undefined" && typeof globalThis.requestIdleCallback === "function") {
    globalThis.requestIdleCallback(() => task(), { timeout: 1500 });
    return;
  }
  window.setTimeout(() => task(), 0);
}

async function runPostGamePrefetchWithBudget(plan = [], { budgetMs = POST_GAME_PREFETCH_BUDGET_MS } = {}) {
  const items = (Array.isArray(plan) ? plan : []).filter((item) => item?.key && item?.url);
  if (!items.length) return;

  const tierMap = new Map();
  items.forEach((item) => {
    const tier = getPrefetchTier(item);
    const list = tierMap.get(tier) || [];
    list.push(item);
    tierMap.set(tier, list);
  });

  const tierOrder = [...tierMap.keys()].sort((a, b) => a - b);
  const startedAt = getNowMs();

  for (let idx = 0; idx < tierOrder.length; idx += 1) {
    const tier = tierOrder[idx];
    const elapsed = getNowMs() - startedAt;
    if (elapsed >= budgetMs) {
      const remainingItems = tierOrder.slice(idx).flatMap((t) => tierMap.get(t) || []);
      debugPostGamePrefetch("budget-exhausted", {
        budgetMs,
        elapsedMs: Math.round(elapsed),
        deferredCount: remainingItems.length,
      });
      deferTask(() => {
        debugPostGamePrefetch("continuation-start", { count: remainingItems.length });
        void runPrefetchPlan(remainingItems);
      });
      return;
    }

    const tierItems = tierMap.get(tier) || [];
    debugPostGamePrefetch("tier-start", { tier, count: tierItems.length, elapsedMs: Math.round(elapsed) });
    await runPrefetchPlan(tierItems);
    debugPostGamePrefetch("tier-complete", { tier, count: tierItems.length });
  }
}

function resolvePostGameTrainingRangeContext(teamId) {
  const tid = String(teamId || state.selectedTeamId || "").toUpperCase();
  if (!tid) return null;
  const cached = state.trainingPrefetchContext;
  if (!cached) return null;
  const cachedTeamId = String(cached.teamId || "").toUpperCase();
  const from = String(cached.from || "").slice(0, 10);
  const to = String(cached.to || "").slice(0, 10);
  if (cachedTeamId !== tid || !from || !to) return null;
  return { from, to };
}

function invalidatePostGameViewCaches(teamId) {
  const tid = String(teamId || state.selectedTeamId || "").toUpperCase();
  if (!tid) return;
  invalidateByEvent(CACHE_EVENT_TYPES.GAME_PROGRESS, { teamId: tid });
}

function queuePostGamePrefetch(teamId) {
  const tid = String(teamId || state.selectedTeamId || "").toUpperCase();
  if (!tid) return;
  const trainingRange = resolvePostGameTrainingRangeContext(tid);
  const plan = getPrefetchPlanAfterGame({
    teamId: tid,
    currentDate: state.currentDate,
    trainingRange,
  });
  if (!plan.length) return;

  const criticalPlan = plan.filter((item) => getPrefetchTier(item) <= 2 || item?.critical);
  const deferredPlan = plan.filter((item) => !criticalPlan.includes(item));

  if (criticalPlan.length) {
    debugPostGamePrefetch("critical-start", {
      count: criticalPlan.length,
      hasTrainingRangeContext: Boolean(trainingRange),
    });
    void runPrefetchPlan(criticalPlan);
  }

  if (deferredPlan.length) {
    void runPostGamePrefetchWithBudget(deferredPlan, { budgetMs: POST_GAME_PREFETCH_BUDGET_MS });
  }
}

function invalidateAllTeamDetailCaches() {
  invalidateCachedValuesByPrefix("team-detail:");
}

async function fetchInGameDate() {
  const summary = await fetchJson("/api/state/summary");
  const currentDate = summary?.workflow_state?.league?.current_date;
  return formatIsoDate(currentDate);
}

async function refreshMainDashboard() {
  if (!state.selectedTeamId) return;

  if (els.nextGameQuickBtn) {
    els.nextGameQuickBtn.textContent = "경기가 있는 날짜까지 자동진행";
  }

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

async function progressNextGameFromHome() {
  if (!state.selectedTeamId) {
    alert("먼저 팀을 선택해주세요.");
    return;
  }

  setLoading(true, "경기 시뮬레이션 중...");
  try {
    const dashboard = await fetchMainDashboardRaw();
    const currentDate = String(dashboard?.current_date || "").slice(0, 10);
    const nextGameDate = String(dashboard?.next_game?.game?.date || "").slice(0, 10);

    if (!nextGameDate) {
      alert("예정된 다음 경기가 없습니다.");
      return;
    }

    if (!isSameIsoDate(currentDate, nextGameDate)) {
      const confirmed = await showConfirmModal({
        title: "자동 진행 확인",
        body: "경기 날짜까지 리그 일정은 자동 진행됩니다.",
        okLabel: "확인",
        cancelLabel: "취소",
      });
      if (!confirmed) return;
    }

    const progressResult = await fetchJson("/api/game/progress-next-user-game-day", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_team_id: state.selectedTeamId,
        mode: "auto_if_needed",
      }),
    });

    invalidatePostGameViewCaches(state.selectedTeamId);
    queuePostGamePrefetch(state.selectedTeamId);

    const playedGameId = progressResult?.game_day?.user_game?.game_id;
    if (playedGameId) {
      await showGameResultScreenByGameId(playedGameId);
      await refreshMainDashboard();
      return;
    }

    await refreshMainDashboard();
    alert("경기 진행이 완료되었습니다.");
  } catch (e) {
    alert(`경기 진행 실패: ${e.message}`);
  } finally {
    setLoading(false);
  }
}

async function autoAdvanceToNextGameDayFromHome() {
  if (!state.selectedTeamId) {
    alert("먼저 팀을 선택해주세요.");
    return;
  }

  const confirmed = await showConfirmModal({
    title: "자동 진행 확인",
    body: "경기 날짜까지 리그 일정은 자동 진행됩니다.",
    okLabel: "확인",
    cancelLabel: "취소",
  });
  if (!confirmed) return;

  setLoading(true, "경기가 있는 날짜까지 자동 진행 중...");
  try {
    await fetchJson("/api/game/auto-advance-to-next-user-game-day", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_team_id: state.selectedTeamId,
      }),
    });
    invalidatePostGameViewCaches(state.selectedTeamId);
    queuePostGamePrefetch(state.selectedTeamId);
    await refreshMainDashboard();
    alert("경기가 있는 날짜까지 자동 진행이 완료되었습니다.");
  } catch (e) {
    alert(`자동 진행 실패: ${e.message}`);
  } finally {
    setLoading(false);
  }
}


async function progressTenGamesFromHome() {
  if (!state.selectedTeamId) {
    alert("먼저 팀을 선택해주세요.");
    return;
  }

  const confirmed = await showConfirmModal({
    title: "개발용 10경기 진행",
    body: "다음 10번의 유저 경기를 실제 시뮬레이션합니다. 경기 결과 화면은 표시하지 않고 날짜만 진행됩니다.",
    okLabel: "진행",
    cancelLabel: "취소",
  });
  if (!confirmed) return;

  setLoading(true, "10경기 진행 중...");
  try {
    const result = await fetchJson("/api/game/progress-user-games-batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_team_id: state.selectedTeamId,
        games_to_play: 10,
      }),
    });

    invalidatePostGameViewCaches(state.selectedTeamId);
    queuePostGamePrefetch(state.selectedTeamId);
    await refreshMainDashboard();

    const played = Number(result?.played_games || 0);
    const requested = Number(result?.requested_games || 10);
    const currentDateAfter = String(result?.current_date_after || "-");
    const stoppedReason = String(result?.stopped_reason || "");

    if (played < requested && stoppedReason.startsWith("NO_NEXT_USER_GAME")) {
      alert(`시즌에 남은 유저 경기가 부족하여 ${played}경기만 진행했습니다. 현재 날짜: ${currentDateAfter}`);
      return;
    }

    alert(`${played}경기 진행 완료. 현재 날짜: ${currentDateAfter}`);
  } catch (e) {
    alert(`10경기 진행 실패: ${e.message}`);
  } finally {
    setLoading(false);
  }
}

async function ensurePostseasonChampionForDevFlow() {
  if (!state.selectedTeamId) throw new Error("먼저 팀을 선택해주세요.");

  const resolved = await fetchJson("/api/dev/postseason/fast-resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      my_team_id: state.selectedTeamId,
      use_random_field: true,
    }),
  });
  const champion = String(resolved?.champion || "").toUpperCase();
  if (!champion) {
    throw new Error("DEV 포스트시즌 임의 확정에 실패했습니다.");
  }
  return champion;
}

function showOffseasonDevChampionScreen({ champion } = {}) {
  const cid = String(champion || "").toUpperCase();
  const championName = TEAM_FULL_NAMES[cid] || cid || "미확정";
  if (els.offseasonDevChampionTitle) {
    els.offseasonDevChampionTitle.textContent = `플레이오프 우승 - ${championName}`;
  }
  if (els.offseasonDevChampionSubtitle) {
    els.offseasonDevChampionSubtitle.textContent = `DEV 임의 진행으로 ${championName} 우승이 확정되었습니다.`;
  }
  if (els.offseasonDevChampionSummary) {
    els.offseasonDevChampionSummary.textContent = "다음 단계에서 오프시즌 진입 API를 연결할 예정입니다.";
  }
  activateScreen(els.offseasonDevChampionScreen);
}

async function startOffseasonDevRunFromHome() {
  if (!state.selectedTeamId) {
    alert("먼저 팀을 선택해주세요.");
    return;
  }

  const confirmed = await showConfirmModal({
    title: "오프시즌 임의 진행 (DEV)",
    body: "실경기 시뮬 없이 포스트시즌 16시드/우승팀을 임의 확정한 뒤 챔피언 화면으로 이동합니다.",
    okLabel: "진행",
    cancelLabel: "취소",
  });
  if (!confirmed) return;

  setLoading(true, "DEV 오프시즌 임의 진행 준비 중...");
  try {
    const champion = await ensurePostseasonChampionForDevFlow();
    if (state.offseasonDev && typeof state.offseasonDev === "object") {
      state.offseasonDev.championTeamId = String(champion || "").toUpperCase();
      state.offseasonDev.step = "IDLE";
      state.offseasonDev.error = "";
    }
    showOffseasonDevChampionScreen({ champion });
  } catch (e) {
    alert(`오프시즌 임의 진행 준비 실패: ${e.message}`);
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
    invalidateAllTeamDetailCaches();
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

    invalidateAllTeamDetailCaches();

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

  invalidateAllTeamDetailCaches();

  showMainScreen();
}

export {
  showTeamSelection,
  showMainScreen,
  randomTipoffTime,
  fetchInGameDate,
  refreshMainDashboard,
  progressNextGameFromHome,
  autoAdvanceToNextGameDayFromHome,
  progressTenGamesFromHome,
  startOffseasonDevRunFromHome,
  showOffseasonDevChampionScreen,
  loadSavesStatus,
  renderTeams,
  createNewGame,
  continueGame,
  confirmTeamSelection,
};
