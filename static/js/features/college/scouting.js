import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { fetchJson } from "../../core/api.js";
import { escapeHtml } from "../../core/guards.js";

function renderCollegeEmpty(tbody, colspan, msg) {
  tbody.innerHTML = `<tr><td class="schedule-empty" colspan="${colspan}">${msg}</td></tr>`;
}

function setCollegeScoutingFeedback(message, tone = "info") {
  if (!els.collegeScoutingFeedback) return;
  els.collegeScoutingFeedback.textContent = message;
  els.collegeScoutingFeedback.dataset.tone = tone;
}

async function loadCollegeScouting() {
  if (!state.selectedTeamId) return;
  const [scoutsPayload, reportsPayload] = await Promise.all([
    fetchJson(`/api/scouting/scouts/${encodeURIComponent(state.selectedTeamId)}`),
    fetchJson(`/api/scouting/reports?team_id=${encodeURIComponent(state.selectedTeamId)}&limit=50`),
  ]);
  state.scoutingScouts = scoutsPayload?.scouts || [];
  state.scoutingReports = reportsPayload?.reports || [];
  state.scoutingPlayers = [];
  renderCollegeScoutCards();
}

function getScoutingReadStorageKey(teamId) {
  return `nba.scouting.read.${String(teamId || "")}`;
}

function getScoutingReadMap() {
  const key = getScoutingReadStorageKey(state.selectedTeamId);
  try {
    const raw = localStorage.getItem(key);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function markScoutReportsRead(scoutId) {
  if (!scoutId || !state.selectedTeamId) return;
  const key = getScoutingReadStorageKey(state.selectedTeamId);
  const readMap = getScoutingReadMap();
  readMap[scoutId] = new Date().toISOString();
  localStorage.setItem(key, JSON.stringify(readMap));
}

function getScoutUnreadCount(scoutId) {
  if (!scoutId) return 0;
  const readMap = getScoutingReadMap();
  const lastRead = Date.parse(String(readMap[scoutId] || ""));
  const threshold = Number.isFinite(lastRead) ? lastRead : 0;
  return state.scoutingReports.filter((report) => {
    if (String(report?.scout?.scout_id || "") !== scoutId) return false;
    const created = Date.parse(String(report?.created_at || report?.updated_at || report?.as_of_date || ""));
    return Number.isFinite(created) ? created > threshold : threshold === 0;
  }).length;
}

function getScoutingPlayerName(playerId, fallback = "-") {
  if (!playerId) return fallback;
  const player = state.scoutingPlayerLookup[playerId];
  return player?.name || fallback;
}

function renderCollegeScoutCards() {
  if (!els.collegeScoutCards) return;
  if (!state.scoutingScouts.length) {
    els.collegeScoutCards.innerHTML = `<article class="college-card"><p class="college-inline-meta">가용 스카우터가 없습니다.</p></article>`;
    return;
  }

  const activeAssignments = state.scoutingScouts.filter((scout) => scout?.active_assignment).length;
  const unreadTotal = state.scoutingScouts.reduce((acc, scout) => acc + getScoutUnreadCount(String(scout?.scout_id || "")), 0);
  if (els.collegeScoutingSummary) {
    els.collegeScoutingSummary.textContent = `활성 배정 ${activeAssignments}/${state.scoutingScouts.length} · 미확인 리포트 ${unreadTotal}건`;
  }

  els.collegeScoutCards.innerHTML = state.scoutingScouts.map((scout) => {
    const scoutId = String(scout?.scout_id || "");
    const assignment = scout?.active_assignment;
    const targetId = String(assignment?.target_player_id || "");
    const playerName = assignment ? getScoutingPlayerName(targetId, targetId || "-") : "미배정";
    const unread = getScoutUnreadCount(scoutId);
    const focusAxes = Array.isArray(scout?.profile?.focus_axes) ? scout.profile.focus_axes.slice(0, 2) : [];
    const styleTags = Array.isArray(scout?.profile?.style_tags) ? scout.profile.style_tags.slice(0, 2) : [];
    return `
      <article class="college-card college-scout-card" data-scout-id="${escapeHtml(scoutId)}" role="listitem">
        <div class="college-card-head-inline">
          <div>
            <h4>${escapeHtml(scout?.display_name || scoutId)}</h4>
            <p class="college-inline-meta">${escapeHtml(scout?.specialty_key || "GENERAL")}</p>
          </div>
          ${unread > 0 ? `<span class="college-scout-unread-badge">NEW ${unread}</span>` : ""}
        </div>
        <p class="college-inline-meta">
          현재 배정: ${escapeHtml(playerName)}
        </p>
        <div class="college-tag-wrap">
          ${focusAxes.map((axis) => `<span class="college-tag">${escapeHtml(axis)}</span>`).join("")}
          ${styleTags.map((tag) => `<span class="college-tag is-strength">${escapeHtml(tag)}</span>`).join("")}
        </div>
        <div class="college-actions-row college-scout-actions">
          <button type="button" class="btn btn-primary" data-action="pick-player" data-scout-id="${escapeHtml(scoutId)}">선수 배정</button>
          <button type="button" class="btn btn-secondary" data-action="open-reports" data-scout-id="${escapeHtml(scoutId)}">리포트 보기${unread > 0 ? ` (${unread})` : ""}</button>
        </div>
      </article>
    `;
  }).join("");
}

function resetScoutPlayerSearchState() {
  state.scoutingPlayerSearch = "";
  state.scoutingPlayerSearchStatus = "ALL";
  state.scoutingPlayerSearchResults = [];
  state.scoutingPlayerSearchTotal = 0;
  state.scoutingPlayerSearchOffset = 0;
  state.scoutingPlayerSearchLoading = false;
  state.scoutingPlayerSearchError = "";
  state.scoutingPlayerSearchHasSearched = false;
}

function renderScoutPlayerList() {
  if (!els.collegeScoutPlayerList) return;

  const keyword = String(state.scoutingPlayerSearch || "").trim();
  const hasKeyword = keyword.length >= 2;
  const loading = !!state.scoutingPlayerSearchLoading;
  const err = String(state.scoutingPlayerSearchError || "");
  const rows = Array.isArray(state.scoutingPlayerSearchResults) ? state.scoutingPlayerSearchResults : [];
  const scout = state.scoutingScouts.find((item) => String(item?.scout_id || "") === String(state.scoutingActiveScoutId || ""));

  if (els.collegeScoutPlayerSearchMeta) {
    if (!hasKeyword) {
      els.collegeScoutPlayerSearchMeta.textContent = "2글자 이상 입력하면 전체 대학 선수 대상 검색이 시작됩니다.";
    } else if (loading) {
      els.collegeScoutPlayerSearchMeta.textContent = "선수를 검색 중입니다...";
    } else if (err) {
      els.collegeScoutPlayerSearchMeta.textContent = err;
    } else {
      const total = Number(state.scoutingPlayerSearchTotal || 0);
      const shown = rows.length;
      els.collegeScoutPlayerSearchMeta.textContent = `검색어 '${keyword}' · ${total}명 중 ${shown}명 표시`;
    }
  }

  if (els.collegeScoutPlayerLoadMore) {
    const hasMore = rows.length < Number(state.scoutingPlayerSearchTotal || 0);
    els.collegeScoutPlayerLoadMore.classList.toggle("hidden", !hasKeyword || loading || !!err || !hasMore);
    els.collegeScoutPlayerLoadMore.disabled = loading;
  }

  if (!hasKeyword) {
    els.collegeScoutPlayerList.innerHTML = `<p class="college-inline-meta">선수명을 2글자 이상 입력해주세요.</p>`;
    return;
  }
  if (loading && !rows.length) {
    els.collegeScoutPlayerList.innerHTML = `<p class="college-inline-meta">검색 결과를 불러오는 중입니다...</p>`;
    return;
  }
  if (err && !rows.length) {
    els.collegeScoutPlayerList.innerHTML = `<p class="college-inline-meta">${escapeHtml(err)}</p>`;
    return;
  }
  if (!rows.length) {
    els.collegeScoutPlayerList.innerHTML = `<p class="college-inline-meta">검색 결과가 없습니다.</p>`;
    return;
  }

  els.collegeScoutPlayerList.innerHTML = rows.map((player) => {
    const pid = String(player?.player_id || "");
    const assignedNow = String(scout?.active_assignment?.target_player_id || "") === pid;
    return `
      <button type="button" role="option" class="college-player-option ${assignedNow ? "is-current" : ""}" data-player-id="${escapeHtml(pid)}">
        <span class="college-player-option-main">
          <strong>${escapeHtml(player?.name || "-")}</strong>
          <small>${escapeHtml(player?.college_team_name || player?.college_team_id || "-")} · ${escapeHtml(player?.pos || "-")} · ${escapeHtml(player?.status || "-")}</small>
        </span>
        ${assignedNow ? `<span class="college-player-option-badge">현재 배정</span>` : ""}
      </button>
    `;
  }).join("");
}

async function searchScoutingPlayers({ append = false } = {}) {
  const keyword = String(state.scoutingPlayerSearch || "").trim();
  if (keyword.length < 2) {
    state.scoutingPlayerSearchResults = [];
    state.scoutingPlayerSearchTotal = 0;
    state.scoutingPlayerSearchOffset = 0;
    state.scoutingPlayerSearchError = "";
    state.scoutingPlayerSearchLoading = false;
    state.scoutingPlayerSearchHasSearched = false;
    renderScoutPlayerList();
    return;
  }

  const nextOffset = append ? state.scoutingPlayerSearchResults.length : 0;
  const reqSeq = Number(state.scoutingPlayerSearchRequestSeq || 0) + 1;
  state.scoutingPlayerSearchRequestSeq = reqSeq;
  state.scoutingPlayerSearchLoading = true;
  state.scoutingPlayerSearchError = "";
  if (!append) state.scoutingPlayerSearchHasSearched = true;
  renderScoutPlayerList();

  try {
    const query = new URLSearchParams({
      q: keyword,
      status: String(state.scoutingPlayerSearchStatus || "ALL"),
      limit: String(state.scoutingPlayerSearchLimit || 30),
      offset: String(nextOffset),
    });
    const payload = await fetchJson(`/api/scouting/players/search?${query.toString()}`);
    if (reqSeq !== state.scoutingPlayerSearchRequestSeq) return;

    const rows = Array.isArray(payload?.players) ? payload.players : [];
    state.scoutingPlayerSearchOffset = Number(payload?.offset || nextOffset);
    state.scoutingPlayerSearchTotal = Number(payload?.total || 0);
    state.scoutingPlayerSearchResults = append
      ? [...state.scoutingPlayerSearchResults, ...rows]
      : rows;

    rows.forEach((p) => {
      const pid = String(p?.player_id || "");
      if (pid) state.scoutingPlayerLookup[pid] = p;
    });
  } catch (error) {
    if (reqSeq !== state.scoutingPlayerSearchRequestSeq) return;
    state.scoutingPlayerSearchError = error?.message || "선수 검색 중 오류가 발생했습니다.";
    if (!append) state.scoutingPlayerSearchResults = [];
  } finally {
    if (reqSeq === state.scoutingPlayerSearchRequestSeq) {
      state.scoutingPlayerSearchLoading = false;
      renderScoutPlayerList();
    }
  }
}

function queueScoutingPlayerSearch() {
  if (state.scoutingPlayerSearchDebounceTimer) {
    clearTimeout(state.scoutingPlayerSearchDebounceTimer);
  }
  state.scoutingPlayerSearchDebounceTimer = setTimeout(() => {
    searchScoutingPlayers({ append: false }).catch((e) => {
      state.scoutingPlayerSearchError = e?.message || "선수 검색 중 오류가 발생했습니다.";
      state.scoutingPlayerSearchLoading = false;
      renderScoutPlayerList();
    });
  }, 280);
}

function openScoutPlayerModal(scoutId) {
  if (!els.collegeScoutPlayerModal) return;
  state.scoutingActiveScoutId = scoutId;
  resetScoutPlayerSearchState();
  const scout = state.scoutingScouts.find((item) => String(item?.scout_id || "") === scoutId);
  if (els.collegeScoutPlayerModalTitle) {
    els.collegeScoutPlayerModalTitle.textContent = `${scout?.display_name || scoutId} · 선수 배정`;
  }
  if (els.collegeScoutPlayerModalMeta) {
    els.collegeScoutPlayerModalMeta.textContent = `전문분야 ${scout?.specialty_key || "GENERAL"} · 전체 대학 선수 검색으로 대상 선수를 배정합니다.`;
  }
  if (els.collegeScoutPlayerSearch) {
    els.collegeScoutPlayerSearch.value = "";
  }
  if (els.collegeScoutPlayerStatus) {
    els.collegeScoutPlayerStatus.value = "ALL";
  }
  renderScoutPlayerList();
  els.collegeScoutPlayerModal.classList.remove("hidden");
  document.body.classList.add("is-modal-open");
  els.collegeScoutPlayerSearch?.focus();
}

function closeScoutPlayerModal() {
  if (!els.collegeScoutPlayerModal) return;
  if (state.scoutingPlayerSearchDebounceTimer) {
    clearTimeout(state.scoutingPlayerSearchDebounceTimer);
    state.scoutingPlayerSearchDebounceTimer = null;
  }
  els.collegeScoutPlayerModal.classList.add("hidden");
  document.body.classList.remove("is-modal-open");
}

function openScoutReportsModal(scoutId) {
  if (!els.collegeScoutReportsModal) return;
  state.scoutingActiveScoutId = scoutId;
  const scout = state.scoutingScouts.find((item) => String(item?.scout_id || "") === scoutId);
  if (els.collegeScoutReportsModalTitle) {
    els.collegeScoutReportsModalTitle.textContent = `${scout?.display_name || scoutId} · 스카우팅 리포트`;
  }
  renderScoutReportsList();
  markScoutReportsRead(scoutId);
  renderCollegeScoutCards();
  els.collegeScoutReportsModal.classList.remove("hidden");
  document.body.classList.add("is-modal-open");
  els.collegeScoutReportsModalClose?.focus();
}

function closeScoutReportsModal() {
  if (!els.collegeScoutReportsModal) return;
  els.collegeScoutReportsModal.classList.add("hidden");
  document.body.classList.remove("is-modal-open");
}

function renderScoutReportsList() {
  if (!els.collegeScoutReportsList) return;
  const reports = state.scoutingReports
    .filter((report) => String(report?.scout?.scout_id || "") === String(state.scoutingActiveScoutId || ""));
  if (els.collegeScoutReportsModalMeta) {
    els.collegeScoutReportsModalMeta.textContent = `총 ${reports.length}건 · 최신순`;
  }
  if (!reports.length) {
    els.collegeScoutReportsList.innerHTML = `<p class="college-inline-meta">리포트가 없습니다. 월말 시뮬레이션 이후 생성됩니다.</p>`;
    return;
  }
  els.collegeScoutReportsList.innerHTML = reports.map((report) => {
    const statusRaw = String(report?.status || "-");
    const statusClass = /complete|done|finished/i.test(statusRaw) ? "is-complete" : (/pending|in_progress|active/i.test(statusRaw) ? "is-pending" : "");
    return `
      <article class="college-report-item">
        <div class="college-card-head-inline">
          <strong>${escapeHtml(report?.player_snapshot?.name || report?.target_player_id || "-")}</strong>
          <span class="college-status-chip ${statusClass}">${escapeHtml(statusRaw)}</span>
        </div>
        <p class="college-inline-meta">${escapeHtml(report?.as_of_date || "-")} · ${escapeHtml(report?.period_key || "-")}</p>
        <p>${escapeHtml((report?.report_text || "텍스트 리포트가 아직 생성되지 않았습니다.").slice(0, 240))}</p>
      </article>
    `;
  }).join("");
}

export { renderCollegeEmpty, setCollegeScoutingFeedback, loadCollegeScouting, getScoutingReadStorageKey, getScoutingReadMap, markScoutReportsRead, getScoutUnreadCount, getScoutingPlayerName, renderCollegeScoutCards, resetScoutPlayerSearchState, renderScoutPlayerList, searchScoutingPlayers, queueScoutingPlayerSearch, openScoutPlayerModal, closeScoutPlayerModal, openScoutReportsModal, closeScoutReportsModal, renderScoutReportsList };
