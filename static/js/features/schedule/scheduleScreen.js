import { els } from "../../app/dom.js";
import { state } from "../../app/state.js";
import { activateScreen } from "../../app/router.js";
import { fetchCachedJson, getCachedValue, setLoading } from "../../core/api.js";
import { CACHE_TTL_MS, buildCacheKeys } from "../../app/cachePolicy.js";
import { TEAM_FULL_NAMES, getScheduleVenueText, renderTeamLogoMark } from "../../core/constants/teams.js";
import { formatLeader } from "../main/homeWidgets.js";

let scheduleRequestSeq = 0;

function isCompletedGame(game) {
  return game?.home_score != null && game?.away_score != null;
}

function renderEmptyScheduleRow(colSpan, text) {
  return `<tr><td colspan="${colSpan}" class="schedule-empty">${text}</td></tr>`;
}

function formatScheduleResult(result) {
  const wl = String(result?.wl || "").toUpperCase();
  const display = String(result?.display || "").trim();
  if (!display) {
    return { wl, score: "" };
  }

  if (wl && display.toUpperCase().startsWith(wl)) {
    return { wl, score: display.slice(wl.length).trim() };
  }

  const matched = display.match(/^([WL])\s*(.*)$/i);
  if (matched) {
    return { wl: matched[1].toUpperCase(), score: (matched[2] || "").trim() };
  }

  return { wl, score: display };
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
      const formattedResult = formatScheduleResult(result);
      const wlClass = formattedResult.wl === "W" ? "schedule-result-win" : "schedule-result-loss";
      return `
        <tr>
          <td>${g.date_mmdd || "--/--"}</td>
          <td class="schedule-opponent-cell">
            <div class="schedule-opponent-main">${renderTeamLogoMark(opponentTeamId, "schedule-team-logo")}<span class="schedule-opponent-label">${g.opponent_label || "-"}</span></div>
            <span class="schedule-opponent-name">${venueName}</span>
          </td>
          <td>
            <span class="schedule-result-badge">
              <span class="schedule-result-flag ${wlClass}">${formattedResult.wl || "-"}</span>
              <span class="schedule-result-score">${formattedResult.score || "-"}</span>
            </span>
          </td>
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
          <td class="schedule-opponent-cell">
            <div class="schedule-opponent-main">${renderTeamLogoMark(opponentTeamId, "schedule-team-logo")}<span class="schedule-opponent-label">${g.opponent_label || "-"}</span></div>
            <span class="schedule-opponent-name">${venueName}</span>
          </td>
          <td><span class="schedule-time-chip">${g.tipoff_time || "--:-- --"}</span></td>
        </tr>
      `;
    }).join("")
    : renderEmptyScheduleRow(3, "예정된 경기가 없습니다.");
}

function scheduleCacheKey(teamId) {
  return buildCacheKeys(teamId).schedule;
}

function renderScheduleScreen(schedule, teamId) {
  const normalizedTeamId = String(teamId || state.selectedTeamId || "").toUpperCase();
  const teamName = state.selectedTeamName || TEAM_FULL_NAMES[normalizedTeamId] || normalizedTeamId;
  els.scheduleTitle.textContent = `${teamName} 정규 시즌 일정`;
  renderScheduleTables(schedule?.games || []);
  activateScreen(els.scheduleScreen);
}

async function showScheduleScreen() {
  if (!state.selectedTeamId) {
    alert("먼저 팀을 선택해주세요.");
    return;
  }

  const teamId = String(state.selectedTeamId || "").toUpperCase();
  const url = `/api/team-schedule/${encodeURIComponent(teamId)}`;
  const cacheKey = scheduleCacheKey(teamId);
  const requestSeq = scheduleRequestSeq + 1;
  scheduleRequestSeq = requestSeq;
  const cached = getCachedValue(cacheKey);

  if (!cached) {
    setLoading(true, "스케줄 정보를 불러오는 중...");
  }

  try {
    const schedule = await fetchCachedJson({
      key: cacheKey,
      url,
      ttlMs: CACHE_TTL_MS.schedule,
      staleWhileRevalidate: true,
      onRevalidated: (freshSchedule) => {
        const isSameTeam = String(state.selectedTeamId || "").toUpperCase() === teamId;
        if (!isSameTeam) return;
        if (!els.scheduleScreen?.classList?.contains("active")) return;
        renderScheduleScreen(freshSchedule, teamId);
      },
    });

    if (requestSeq !== scheduleRequestSeq) return;
    renderScheduleScreen(schedule, teamId);
  } catch (e) {
    if (requestSeq !== scheduleRequestSeq) return;
    els.scheduleCompletedBody.innerHTML = renderEmptyScheduleRow(7, `스케줄 로딩 실패: ${e.message}`);
    els.scheduleUpcomingBody.innerHTML = renderEmptyScheduleRow(3, "-");
    activateScreen(els.scheduleScreen);
  } finally {
    if (requestSeq === scheduleRequestSeq) setLoading(false);
  }
}

export { isCompletedGame, renderEmptyScheduleRow, renderScheduleTables, showScheduleScreen };
