import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import { fetchCachedJson, fetchJson, setLoading } from "../../core/api.js";
import { escapeHtml, safeNum } from "../../core/guards.js";
import { renderCollegeEmpty, loadCollegeScouting } from "./scouting.js";
import { teamSeedChip, collegeStat, loadCollegeLeaders } from "./leaders.js";
import { loadCollegeBigboard } from "./bigboard.js";
import { CACHE_TTL_MS, buildCacheKeys } from "../../app/cachePolicy.js";

const COLLEGE_LAZY_TABS = ["leaders", "bigboard", "scouting"];

function resetCollegeLazyTabState() {
  state.collegeTabLoaded = { leaders: false, bigboard: false, scouting: false };
  state.collegeTabLoading = { leaders: false, bigboard: false, scouting: false };
}

async function ensureCollegeTabData(tab) {
  if (!COLLEGE_LAZY_TABS.includes(tab)) return;
  if (!state.collegeTabLoaded || typeof state.collegeTabLoaded !== "object") {
    state.collegeTabLoaded = { leaders: false, bigboard: false, scouting: false };
  }
  if (!state.collegeTabLoading || typeof state.collegeTabLoading !== "object") {
    state.collegeTabLoading = { leaders: false, bigboard: false, scouting: false };
  }
  if (state.collegeTabLoaded[tab] || state.collegeTabLoading[tab]) return;

  state.collegeTabLoading[tab] = true;
  try {
    if (tab === "leaders") {
      await loadCollegeLeaders();
    } else if (tab === "bigboard") {
      await loadCollegeBigboard();
    } else if (tab === "scouting") {
      await loadCollegeScouting();
    }
    state.collegeTabLoaded[tab] = true;
  } catch (error) {
    state.collegeTabLoaded[tab] = false;
    throw error;
  } finally {
    state.collegeTabLoading[tab] = false;
  }
}

function renderCollegeTeamsKpi(teams) {
  if (!els.collegeTeamsKpi) return;
  if (!teams.length) {
    els.collegeTeamsKpi.innerHTML = "";
    return;
  }
  const bestSrsTeam = [...teams].sort((a, b) => safeNum(b?.srs, -9999) - safeNum(a?.srs, -9999))[0];
  const avgSrs = teams.reduce((sum, t) => sum + safeNum(t?.srs), 0) / teams.length;
  const confCount = teams.reduce((acc, t) => {
    const conf = String(t?.conference || "기타");
    acc[conf] = (acc[conf] || 0) + 1;
    return acc;
  }, {});
  const confTop = Object.entries(confCount).sort((a, b) => b[1] - a[1])[0];
  els.collegeTeamsKpi.innerHTML = `
    <article class="college-kpi-card"><span class="college-kpi-label">BEST SRS</span><strong>${escapeHtml(bestSrsTeam?.name || "-")}</strong><span>${safeNum(bestSrsTeam?.srs).toFixed(2)}</span></article>
    <article class="college-kpi-card"><span class="college-kpi-label">AVG SRS</span><strong>${avgSrs.toFixed(2)}</strong><span>전체 ${teams.length}팀 기준</span></article>
    <article class="college-kpi-card"><span class="college-kpi-label">TOP CONFERENCE</span><strong>${escapeHtml(confTop?.[0] || "-")}</strong><span>${confTop?.[1] || 0} teams</span></article>
  `;
}

function switchCollegeTab(tab) {
  const mapping = {
    teams: [els.collegeTabTeams, els.collegePanelTeams],
    leaders: [els.collegeTabLeaders, els.collegePanelLeaders],
    bigboard: [els.collegeTabBigboard, els.collegePanelBigboard],
    scouting: [els.collegeTabScouting, els.collegePanelScouting],
  };
  Object.values(mapping).forEach(([btn, panel]) => {
    const active = btn === mapping[tab][0];
    btn.classList.toggle("is-active", active);
    panel.classList.toggle("active", active);
    panel.setAttribute("aria-hidden", active ? "false" : "true");
  });
}

function renderCollegeTeams(teams) {
  renderCollegeTeamsKpi(teams);
  if (!teams.length) {
    renderCollegeEmpty(els.collegeTeamsBody, 6, "대학 팀 데이터가 없습니다.");
    return;
  }
  const sorted = [...teams].sort((a, b) => {
    const wa = Number(a?.wins ?? -9999);
    const wb = Number(b?.wins ?? -9999);
    if (wb !== wa) return wb - wa;
    const la = Number(a?.losses ?? 9999);
    const lb = Number(b?.losses ?? 9999);
    if (la !== lb) return la - lb;
    return Number(b?.srs ?? -9999) - Number(a?.srs ?? -9999);
  });
  els.collegeTeamsBody.innerHTML = "";
  sorted.forEach((team, idx) => {
    const rank = idx + 1;
    const teamId = team?.college_team_id || "";
    const tr = document.createElement("tr");
    tr.className = "roster-row college-team-row";
    if (state.selectedCollegeTeamId && state.selectedCollegeTeamId === teamId) {
      tr.classList.add("is-selected");
    }
    tr.innerHTML = `
      <td>${rank}${teamSeedChip(rank)}</td>
      <td class="standings-team-cell">${escapeHtml(team?.name || teamId || "-")}</td>
      <td>${escapeHtml(team?.conference || "-")}</td>
      <td>${team?.wins ?? "-"}</td>
      <td>${team?.losses ?? "-"}</td>
      <td>${safeNum(team?.srs).toFixed(2)}</td>
    `;
    tr.addEventListener("click", () => loadCollegeTeamDetail(teamId).catch((e) => alert(e.message)));
    els.collegeTeamsBody.appendChild(tr);
  });
  if (!state.selectedCollegeTeamId && sorted[0]?.college_team_id) {
    state.selectedCollegeTeamId = sorted[0].college_team_id;
  }
}

async function loadCollegeTeamDetail(teamId) {
  if (!teamId) return;
  const payload = await fetchCachedJson({
    key: `college:team-detail:${encodeURIComponent(String(teamId || ""))}` ,
    url: `/api/college/team-detail/${encodeURIComponent(teamId)}` ,
    ttlMs: CACHE_TTL_MS.college,
    staleWhileRevalidate: true,
  });
  const teamName = payload?.team?.name || teamId;
  const roster = payload?.roster || [];
  state.selectedCollegeTeamId = teamId;
  els.collegeRosterTitle.textContent = `${teamName} 로스터`;
  const rows = [...els.collegeTeamsBody.querySelectorAll("tr")];
  rows.forEach((row) => {
    const cell = row.querySelector("td:nth-child(2)");
    const active = cell && String(cell.textContent || "").trim() === String(teamName).trim();
    row.classList.toggle("is-selected", active);
  });
  if (els.collegeRosterSummary) {
    const byPos = roster.reduce((acc, p) => {
      const pos = String(p?.pos || "-");
      acc[pos] = (acc[pos] || 0) + 1;
      return acc;
    }, {});
    const posText = Object.entries(byPos).map(([k, v]) => `${k} ${v}`).join(" · ");
    const avgPts = roster.length ? (roster.reduce((sum, p) => sum + collegeStat(p, "pts"), 0) / roster.length) : 0;
    els.collegeRosterSummary.textContent = `로스터 ${roster.length}명 · 평균 PTS ${avgPts.toFixed(1)}${posText ? ` · ${posText}` : ""}`;
  }
  els.collegeRosterBody.innerHTML = roster.length ? roster.map((p) => `
    <tr class="college-data-row">
      <td>${escapeHtml(p?.name || "-")}</td>
      <td><span class="college-pos-chip">${escapeHtml(p?.pos || "-")}</span></td>
      <td>${escapeHtml(p?.class_year || "-")}</td>
      <td>${collegeStat(p, "pts").toFixed(1)}</td>
      <td>${collegeStat(p, "reb").toFixed(1)}</td>
      <td>${collegeStat(p, "ast").toFixed(1)}</td>
    </tr>
  `).join("") : `<tr><td class="schedule-empty" colspan="6">로스터 데이터가 없습니다.</td></tr>`;
}

async function showCollegeScreen() {
  if (!state.selectedTeamId) {
    alert("먼저 팀을 선택해주세요.");
    return;
  }
  setLoading(true, "대학 리그 정보를 불러오는 중입니다...");
  try {
    resetCollegeLazyTabState();
    const keys = buildCacheKeys(state.selectedTeamId);
    const [meta, teams, experts] = await Promise.all([
      fetchCachedJson({ key: keys.collegeMeta, url: "/api/college/meta", ttlMs: CACHE_TTL_MS.college, staleWhileRevalidate: true }),
      fetchCachedJson({ key: keys.collegeTeams, url: "/api/college/teams", ttlMs: CACHE_TTL_MS.college, staleWhileRevalidate: true }),
      fetchCachedJson({ key: keys.collegeExperts, url: "/api/offseason/draft/experts", ttlMs: CACHE_TTL_MS.college, staleWhileRevalidate: true }),
    ]);
    state.collegeMeta = meta;
    state.collegeTeams = teams || [];
    state.collegeExperts = experts?.experts || [];
    state.collegeBigboardOverview = [];
    state.collegeBigboardByExpert = {};

    els.collegeMetaLine.textContent = `시즌 ${meta?.season_year || "-"} · 대학팀 ${meta?.college?.teams || 0}개 · 예정 드래프트 ${meta?.upcoming_draft_year || "-"}`;
    renderCollegeTeams(state.collegeTeams);
    if (state.selectedCollegeTeamId) {
      await loadCollegeTeamDetail(state.selectedCollegeTeamId);
    }

    const sortOptions = ["pts", "reb", "ast", "stl", "blk", "mpg", "games", "ts_pct", "usg", "fg_pct"];
    els.collegeLeaderSort.innerHTML = sortOptions.map((k) => `<option value="${k}">${k.toUpperCase()}</option>`).join("");
    els.collegeLeaderSort.value = state.collegeLeadersSort;
    if (els.collegeLeaderPosFilter) els.collegeLeaderPosFilter.innerHTML = "";
    if (els.collegeLeaderTeamFilter) els.collegeLeaderTeamFilter.innerHTML = "";

    switchCollegeTab("teams");
    activateScreen(els.collegeScreen);
  } finally {
    setLoading(false);
  }
}

export {
  renderCollegeTeamsKpi,
  switchCollegeTab,
  renderCollegeTeams,
  loadCollegeTeamDetail,
  ensureCollegeTabData,
  showCollegeScreen,
};
