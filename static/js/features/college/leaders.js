import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { fetchCachedJson } from "../../core/api.js";
import { escapeHtml } from "../../core/guards.js";

const COLLEGE_LEADERS_LIMIT = 50;
const COLLEGE_LEADERS_TTL_MS = 15000;

function parseSummaryTags(summary) {
  const raw = String(summary || "");
  const strengths = [];
  const concerns = [];
  const strengthMatch = raw.match(/Strengths?:\s*([^\.]+)/i);
  const concernMatch = raw.match(/Concern:\s*([^\.]+)/i);
  if (strengthMatch?.[1]) {
    strengths.push(...strengthMatch[1].split(",").map((v) => v.trim()).filter(Boolean));
  }
  if (concernMatch?.[1]) {
    concerns.push(...concernMatch[1].split(",").map((v) => v.trim()).filter(Boolean));
  }
  return { strengths, concerns };
}

function teamSeedChip(rank) {
  if (rank > 4) return "";
  return `<span class="college-seed-chip">TOP ${rank}</span>`;
}

function tierChip(tier) {
  const t = String(tier || "-");
  let cls = "";
  if (/tier\s*1/i.test(t)) cls = "is-tier1";
  else if (/lottery/i.test(t)) cls = "is-lottery";
  else if (/1st/i.test(t)) cls = "is-round1";
  else if (/2nd/i.test(t)) cls = "is-round2";
  return `<span class="college-tier-chip ${cls}">${escapeHtml(t)}</span>`;
}

function renderLeaderInsight(player) {
  if (!els.collegeLeaderInsight || !els.collegeLeaderInsightEmpty) return;
  if (!player) {
    els.collegeLeaderInsight.innerHTML = "";
    els.collegeLeaderInsightEmpty.style.display = "block";
    return;
  }
  els.collegeLeaderInsightEmpty.style.display = "none";
  const impact = (collegeStat(player, "pts") * 0.5) + (collegeStat(player, "reb") * 0.25) + (collegeStat(player, "ast") * 0.25);
  els.collegeLeaderInsight.innerHTML = `
    <div class="college-kv-row"><span>선수</span><span>${escapeHtml(player?.name || "-")}</span></div>
    <div class="college-kv-row"><span>팀</span><span>${escapeHtml(player?.college_team_name || player?.college_team_id || "-")}</span></div>
    <div class="college-kv-row"><span>포지션</span><span>${escapeHtml(player?.pos || "-")}</span></div>
    <div class="college-kv-row"><span>PTS / REB / AST</span><span>${collegeStat(player, "pts").toFixed(1)} / ${collegeStat(player, "reb").toFixed(1)} / ${collegeStat(player, "ast").toFixed(1)}</span></div>
    <div class="college-kv-row"><span>Impact Index</span><span>${impact.toFixed(2)}</span></div>
  `;
}

function collegeStat(player, key) {
  const stats = player?.stats || {};
  const n = Number(stats?.[key]);
  return Number.isFinite(n) ? n : 0;
}

function getCollegeLeadersCacheKey(sort) {
  return `college:leaders:sort=${String(sort || "pts")}:limit=${COLLEGE_LEADERS_LIMIT}`;
}

async function loadCollegeLeaders() {
  const sort = state.collegeLeadersSort || "pts";
  const payload = await fetchCachedJson({
    key: getCollegeLeadersCacheKey(sort),
    url: `/api/college/players?sort=${encodeURIComponent(sort)}&order=desc&limit=${COLLEGE_LEADERS_LIMIT}`,
    ttlMs: COLLEGE_LEADERS_TTL_MS,
    staleWhileRevalidate: true,
  });
  const allPlayers = payload?.players || [];

  const allPos = ["ALL", ...new Set(allPlayers.map((p) => String(p?.pos || "-").toUpperCase()))];
  const allTeams = ["ALL", ...new Set(allPlayers.map((p) => p?.college_team_name || p?.college_team_id || "-"))];
  if (els.collegeLeaderPosFilter && !els.collegeLeaderPosFilter.options.length) {
    els.collegeLeaderPosFilter.innerHTML = allPos.map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
  }
  if (els.collegeLeaderTeamFilter && !els.collegeLeaderTeamFilter.options.length) {
    els.collegeLeaderTeamFilter.innerHTML = allTeams.map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
  }
  state.collegeLeaderPosFilter = state.collegeLeaderPosFilter || "ALL";
  state.collegeLeaderTeamFilter = state.collegeLeaderTeamFilter || "ALL";
  if (els.collegeLeaderPosFilter) els.collegeLeaderPosFilter.value = state.collegeLeaderPosFilter;
  if (els.collegeLeaderTeamFilter) els.collegeLeaderTeamFilter.value = state.collegeLeaderTeamFilter;

  const players = allPlayers.filter((p) => {
    const posOk = state.collegeLeaderPosFilter === "ALL" || String(p?.pos || "-").toUpperCase() === state.collegeLeaderPosFilter;
    const teamName = p?.college_team_name || p?.college_team_id || "-";
    const teamOk = state.collegeLeaderTeamFilter === "ALL" || teamName === state.collegeLeaderTeamFilter;
    return posOk && teamOk;
  });

  const hasSelectedPlayer = players.some((p) => p?.player_id && p.player_id === state.selectedCollegeLeaderPlayerId);
  if (!hasSelectedPlayer) {
    state.selectedCollegeLeaderPlayerId = players[0]?.player_id || null;
  }

  let selectedPlayer = null;
  els.collegeLeadersBody.innerHTML = players.length ? players.map((p, idx) => {
    const selected = state.selectedCollegeLeaderPlayerId === p?.player_id;
    if (selected) selectedPlayer = p;
    return `
      <tr class="college-data-row ${selected ? "is-selected" : ""}" data-player-id="${escapeHtml(p?.player_id || "")}">
        <td>${idx + 1}</td>
        <td>${escapeHtml(p?.name || "-")}</td>
        <td>${escapeHtml(p?.college_team_name || p?.college_team_id || "-")}</td>
        <td><span class="college-pos-chip">${escapeHtml(p?.pos || "-")}</span></td>
        <td>${collegeStat(p, "pts").toFixed(1)}</td>
        <td>${collegeStat(p, "reb").toFixed(1)}</td>
        <td>${collegeStat(p, "ast").toFixed(1)}</td>
        <td>${collegeStat(p, "stl").toFixed(1)}</td>
        <td>${collegeStat(p, "blk").toFixed(1)}</td>
      </tr>
    `;
  }).join("") : `<tr><td class="schedule-empty" colspan="9">리더보드 데이터가 없습니다.</td></tr>`;

  els.collegeLeadersBody.querySelectorAll("tr[data-player-id]").forEach((tr) => {
    tr.addEventListener("click", () => {
      state.selectedCollegeLeaderPlayerId = tr.dataset.playerId;
      loadCollegeLeaders().catch((e) => alert(e.message));
    });
  });
  renderLeaderInsight(selectedPlayer || players[0] || null);
}

export { parseSummaryTags, teamSeedChip, tierChip, renderLeaderInsight, collegeStat, loadCollegeLeaders };
