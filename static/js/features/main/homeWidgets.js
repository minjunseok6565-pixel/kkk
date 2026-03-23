import { els } from "../../app/dom.js";
import { state } from "../../app/state.js";
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

function getHomeAttentionReadStorageKey(teamId) {
  return `nba.home.attention.read.${String(teamId || "")}`;
}

function getHomeAttentionReadMap(teamId = state.selectedTeamId) {
  const key = getHomeAttentionReadStorageKey(teamId);
  try {
    const raw = localStorage.getItem(key);
    const parsed = raw ? JSON.parse(raw) : {};
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return parsed;
  } catch {
    return {};
  }
}

function markHomeAttentionIssueRead(teamId = state.selectedTeamId, issueId = "") {
  const tid = String(teamId || "");
  const iid = String(issueId || "");
  if (!tid || !iid) return;
  const key = getHomeAttentionReadStorageKey(tid);
  const map = getHomeAttentionReadMap(tid);
  map[iid] = new Date().toISOString();
  try {
    localStorage.setItem(key, JSON.stringify(map));
  } catch (_) {
    // noop: storage quota/privacy mode failure should not block UI update
  }
}

function isHomeAttentionIssueRead(teamId = state.selectedTeamId, issueId = "") {
  const tid = String(teamId || "");
  const iid = String(issueId || "");
  if (!tid || !iid) return false;
  const map = getHomeAttentionReadMap(tid);
  return Object.prototype.hasOwnProperty.call(map, iid);
}

function formatAttentionTypeLabel(type) {
  const normalized = String(type || "").toUpperCase();
  if (normalized === "TRADE_OFFER") return "트레이드";
  if (normalized === "INJURY") return "부상";
  if (normalized === "DISSATISFACTION") return "불만";
  return "이슈";
}

function formatAttentionTypeClass(type) {
  const normalized = String(type || "").toUpperCase();
  if (normalized === "TRADE_OFFER") return "trade";
  if (normalized === "INJURY") return "injury";
  if (normalized === "DISSATISFACTION") return "dissatisfaction";
  return "default";
}

function formatAttentionDate(isoLike) {
  const raw = String(isoLike || "").slice(0, 10);
  if (raw.length === 10 && raw[4] === "-" && raw[7] === "-") return `${raw.slice(5, 7)}/${raw.slice(8, 10)}`;
  return "--/--";
}

function getUnreadAttentionItems(items, teamId = state.selectedTeamId) {
  const rows = Array.isArray(items) ? items : [];
  return rows.filter((item) => !isHomeAttentionIssueRead(teamId, item?.issue_id));
}

function renderHomeAttentionPreview(items, teamId = state.selectedTeamId) {
  if (!els.homeAttentionPreview) return;
  const preview = getUnreadAttentionItems(items, teamId).slice(0, 5);
  if (!preview.length) {
    els.homeAttentionPreview.innerHTML = '<li class="home-empty">확인이 필요한 이슈가 없습니다.</li>';
    return;
  }
  els.homeAttentionPreview.innerHTML = preview.map((item) => `
    <li class="home-activity-item">
      <span class="home-activity-date">${escapeHtml(formatAttentionDate(item?.occurred_at))}</span>
      <div>
        <strong class="home-attention-type home-attention-type-${escapeHtml(formatAttentionTypeClass(item?.type))}">${escapeHtml(formatAttentionTypeLabel(item?.type))}</strong>
        <p>${escapeHtml(item?.title || "-")}</p>
      </div>
    </li>
  `).join("");
}

function renderHomeAttentionFullList(items, teamId = state.selectedTeamId) {
  if (!els.homeAttentionList) return;
  const list = getUnreadAttentionItems(items, teamId);
  if (!list.length) {
    els.homeAttentionList.innerHTML = '<li class="home-empty">아직 이슈가 없습니다.</li>';
    return;
  }
  els.homeAttentionList.innerHTML = list.map((item) => {
    const issueId = String(item?.issue_id || "");
    return `
      <li class="home-priority-item">
        <span class="home-attention-type home-attention-type-${escapeHtml(formatAttentionTypeClass(item?.type))}">${escapeHtml(formatAttentionTypeLabel(item?.type))}</span>
        <p>${escapeHtml(item?.title || "-")}</p>
        <button type="button" class="home-inline-cta home-attention-read-btn" data-home-attention-read="${escapeHtml(issueId)}">읽음</button>
      </li>
    `;
  }).join("");

  els.homeAttentionList.querySelectorAll("[data-home-attention-read]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const issueId = btn.getAttribute("data-home-attention-read") || "";
      markHomeAttentionIssueRead(teamId, issueId);
      renderHomeAttentionPreview(items, teamId);
      renderHomeAttentionFullList(items, teamId);
    });
  });
}

export {
  resetNextGameCard,
  renderHomePriorities,
  renderHomeActivityFeed,
  renderHomeRiskCalendar,
  formatLeader,
  getHomeAttentionReadStorageKey,
  getHomeAttentionReadMap,
  markHomeAttentionIssueRead,
  isHomeAttentionIssueRead,
  renderHomeAttentionPreview,
  renderHomeAttentionFullList,
};
