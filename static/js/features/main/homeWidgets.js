import { els } from "../../app/dom.js";
import { applyTeamLogo } from "../../core/constants/teams.js";
import { escapeHtml, num } from "../../core/guards.js";

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

export { resetNextGameCard, renderHomePriorities, renderHomeActivityFeed, renderHomeRiskCalendar, formatLeader };
