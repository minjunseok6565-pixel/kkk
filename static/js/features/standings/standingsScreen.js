import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import { fetchJson, setLoading } from "../../core/api.js";
import { formatSignedDiff } from "../../core/format.js";
import { renderTeamLogoMark } from "../../core/constants/teams.js";

function renderStandingsRows(tbody, rows) {
  tbody.innerHTML = "";
  (rows || []).forEach((row) => {
    const tr = document.createElement("tr");
    const teamId = String(row?.team_id || "").toUpperCase();
    const diff = Number(row?.diff || 0);
    const diffClass = diff > 0 ? "standings-diff-positive" : diff < 0 ? "standings-diff-negative" : "";
    tr.innerHTML = `
      <td>${row?.rank ?? "-"}</td>
      <td class="standings-team-cell">${renderTeamLogoMark(teamId, "standings-team-logo")}${TEAM_FULL_NAMES[teamId] || teamId || "-"}</td>
      <td>${row?.wins ?? 0}</td>
      <td>${row?.losses ?? 0}</td>
      <td>${row?.pct || ".000"}</td>
      <td>${row?.gb_display ?? "-"}</td>
      <td>${row?.home || "0-0"}</td>
      <td>${row?.away || "0-0"}</td>
      <td>${row?.div || "0-0"}</td>
      <td>${row?.conf || "0-0"}</td>
      <td>${Number(row?.ppg || 0).toFixed(1)}</td>
      <td>${Number(row?.opp_ppg || 0).toFixed(1)}</td>
      <td class="${diffClass}">${formatSignedDiff(row?.diff)}</td>
      <td>${row?.strk || "-"}</td>
      <td>${row?.l10 || "0-0"}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function showStandingsScreen() {
  setLoading(true, "순위 데이터를 불러오는 중입니다...");
  try {
    const payload = await fetchJson("/api/standings/table");
    state.standingsData = payload;
    renderStandingsRows(els.standingsEastBody, payload?.east || []);
    renderStandingsRows(els.standingsWestBody, payload?.west || []);
    activateScreen(els.standingsScreen);
  } finally {
    setLoading(false);
  }
}

export { renderStandingsRows, showStandingsScreen };
