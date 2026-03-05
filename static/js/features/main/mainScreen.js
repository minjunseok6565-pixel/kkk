import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import { fetchJson, setLoading, showConfirmModal } from "../../core/api.js";
import { formatIsoDate, formatWinPct } from "../../core/format.js";
import { num } from "../../core/guards.js";
import { TEAM_FULL_NAMES, applyTeamLogo, getTeamBranding, renderTeamLogoMark, getScheduleVenueText } from "../../core/constants/teams.js";
import { resetNextGameCard, renderHomePriorities, renderHomeActivityFeed, renderHomeRiskCalendar } from "./homeWidgets.js";

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

  setLoading(true, "다음 경기 진행 중...");
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

    await fetchJson("/api/game/progress-next-user-game-day", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_team_id: state.selectedTeamId,
        mode: "auto_if_needed",
      }),
    });

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
    await refreshMainDashboard();
    alert("경기가 있는 날짜까지 자동 진행이 완료되었습니다.");
  } catch (e) {
    alert(`자동 진행 실패: ${e.message}`);
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

export {
  showTeamSelection,
  showMainScreen,
  randomTipoffTime,
  fetchInGameDate,
  refreshMainDashboard,
  progressNextGameFromHome,
  autoAdvanceToNextGameDayFromHome,
  loadSavesStatus,
  renderTeams,
  createNewGame,
  continueGame,
  confirmTeamSelection,
};
