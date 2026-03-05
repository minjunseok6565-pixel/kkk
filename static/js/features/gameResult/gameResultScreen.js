import { els } from "../../app/dom.js";
import { state } from "../../app/state.js";
import { activateScreen } from "../../app/router.js";
import { fetchJson, setLoading } from "../../core/api.js";
import { TEAM_FULL_NAMES, applyTeamLogo, renderTeamLogoMark } from "../../core/constants/teams.js";

const TAB_KEYS = ["gamecast", "playbyplay", "boxscore", "teamstats"];
const PBP_INITIAL_RENDER_LIMIT = 80;
const PBP_RENDER_STEP = 80;
let gameResultTabsBound = false;

function toClockLabel(totalSeconds) {
  const sec = Math.max(0, Number(totalSeconds || 0));
  const minute = Math.floor(sec / 60);
  const second = Math.floor(sec % 60);
  return `${String(minute).padStart(2, "0")}:${String(second).padStart(2, "0")}`;
}

function toNumber(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function toStatInt(v) {
  return Math.round(toNumber(v));
}

function ratioLabel(made, att) {
  return `${toStatInt(made)}-${toStatInt(att)}`;
}

function pctLabel(made, att) {
  const a = toStatInt(att);
  if (!a) return "0%";
  return `${Math.round((toStatInt(made) / a) * 100)}%`;
}

function deriveTeamTotals(players = []) {
  const totals = {
    PTS: 0, FGM: 0, FGA: 0, "3PM": 0, "3PA": 0, FTM: 0, FTA: 0,
    ORB: 0, DRB: 0, REB: 0, AST: 0, TOV: 0, STL: 0, BLK: 0, PF: 0,
  };
  for (const p of Array.isArray(players) ? players : []) {
    for (const key of Object.keys(totals)) {
      totals[key] += toStatInt(p?.[key]);
    }
  }
  return totals;
}

function escapeHtml(v) {
  return String(v ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function parseClockToSeconds(clock) {
  const m = String(clock || "").match(/^(\d{1,2}):(\d{2})$/);
  if (!m) return -1;
  return Number(m[1]) * 60 + Number(m[2]);
}

function toPeriodLabel(period) {
  const pNum = Math.max(1, toStatInt(period || 1));
  if (pNum <= 4) return `Q${pNum}`;
  return `OT${pNum - 4}`;
}

function svgLineChart({
  points,
  width = 520,
  height = 180,
  yMin,
  yMax,
  homeColor,
  awayColor,
  yLabelFmt,
  homeDashed = false,
  awayDashed = false,
}) {
  if (!Array.isArray(points) || !points.length) return `<p class="home-empty">그래프 데이터가 없습니다.</p>`;
  const pad = { t: 12, r: 16, b: 24, l: 36 };
  const w = width - pad.l - pad.r;
  const h = height - pad.t - pad.b;
  const xs = points.map((p) => Number(p.t || 0));
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs, minX + 1);

  const x = (v) => pad.l + ((v - minX) / (maxX - minX)) * w;
  const y = (v) => pad.t + (1 - ((v - yMin) / Math.max(1e-6, yMax - yMin))) * h;

  const homeLine = points.map((p, idx) => `${idx ? "L" : "M"}${x(Number(p.t || 0)).toFixed(2)} ${y(Number(p.home || p.home_score || 0)).toFixed(2)}`).join(" ");
  const awayLine = points.map((p, idx) => `${idx ? "L" : "M"}${x(Number(p.t || 0)).toFixed(2)} ${y(Number(p.away || p.away_score || 0)).toFixed(2)}`).join(" ");

  return `
    <svg class="game-result-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="경기 그래프">
      <line x1="${pad.l}" y1="${pad.t}" x2="${pad.l}" y2="${height - pad.b}" class="axis" />
      <line x1="${pad.l}" y1="${height - pad.b}" x2="${width - pad.r}" y2="${height - pad.b}" class="axis" />
      <line x1="${pad.l}" y1="${pad.t}" x2="${width - pad.r}" y2="${pad.t}" class="grid" />
      <line x1="${pad.l}" y1="${pad.t + h / 2}" x2="${width - pad.r}" y2="${pad.t + h / 2}" class="grid" />
      <text x="${pad.l - 6}" y="${pad.t + 4}" text-anchor="end" class="axis-text">${yLabelFmt(yMax)}</text>
      <text x="${pad.l - 6}" y="${pad.t + h / 2 + 4}" text-anchor="end" class="axis-text">${yLabelFmt((yMax + yMin) / 2)}</text>
      <text x="${pad.l - 6}" y="${height - pad.b + 4}" text-anchor="end" class="axis-text">${yLabelFmt(yMin)}</text>
      <text x="${pad.l}" y="${height - 6}" class="axis-text">${toClockLabel(minX)}</text>
      <text x="${width - pad.r}" y="${height - 6}" text-anchor="end" class="axis-text">${toClockLabel(maxX)}</text>
      <path d="${awayLine}" fill="none" stroke="${awayColor}" stroke-width="2.6" stroke-linecap="round" ${awayDashed ? 'stroke-dasharray="6 5"' : ""} />
      <path d="${homeLine}" fill="none" stroke="${homeColor}" stroke-width="2.6" stroke-linecap="round" ${homeDashed ? 'stroke-dasharray="6 5"' : ""} />
    </svg>
  `;
}

function getTeamBrandColor(teamId, fallback) {
  const id = String(teamId || "").toUpperCase();
  const palette = {
    ATL: "#e03a3e", BOS: "#007a33", BKN: "#111827", CHA: "#1d4ed8", CHI: "#ce1141",
    CLE: "#6f263d", DAL: "#00538c", DEN: "#0e2240", DET: "#1d42ba", GSW: "#1d4ed8",
    HOU: "#ce1141", IND: "#f59e0b", LAC: "#c8102e", LAL: "#552583", MEM: "#5d76a9",
    MIA: "#98002e", MIL: "#00471b", MIN: "#0c4a6e", NOP: "#0c2340", NYK: "#2563eb",
    OKC: "#007ac1", ORL: "#1d4ed8", PHI: "#006bb6", PHX: "#1d1160", POR: "#e03a3e",
    SAC: "#5a2d81", SAS: "#111827", TOR: "#ce1141", UTA: "#0f766e", WAS: "#0c4a6e",
  };
  return palette[id] || fallback;
}

function chartTeamLegend({ teamId, teamName, teamColor, lineStyle }) {
  const styleClass = lineStyle === "dashed" ? "is-dashed" : "is-solid";
  return `
    <div class="game-result-team-legend-item">
      <img class="game-result-team-legend-logo" src="/static/team_logos/${escapeHtml(teamId)}.png" alt="${escapeHtml(teamName || teamId)} 로고" loading="lazy" />
      <div class="game-result-team-legend-meta">
        <strong>${escapeHtml(teamId || "---")}</strong>
        <span><i class="game-result-line-chip ${styleClass}" style="--line-chip-color: ${teamColor};"></i>${lineStyle === "dashed" ? "점선" : "실선"}</span>
      </div>
    </div>
  `;
}

function buildLineSegments(points, xFn, yFn, threshold = 50) {
  const segments = [];
  for (let i = 1; i < points.length; i += 1) {
    const prev = points[i - 1];
    const curr = points[i];
    const prevAbove = Number(prev.value) >= threshold;
    const currAbove = Number(curr.value) >= threshold;
    if (prevAbove === currAbove) {
      segments.push({
        x1: xFn(prev.t),
        y1: yFn(prev.value),
        x2: xFn(curr.t),
        y2: yFn(curr.value),
        style: prevAbove ? "dashed" : "solid",
      });
      continue;
    }
    const ratio = (threshold - Number(prev.value)) / Math.max(1e-6, Number(curr.value) - Number(prev.value));
    const crossT = Number(prev.t) + (Number(curr.t) - Number(prev.t)) * ratio;
    const crossX = xFn(crossT);
    const crossY = yFn(threshold);
    segments.push({ x1: xFn(prev.t), y1: yFn(prev.value), x2: crossX, y2: crossY, style: prevAbove ? "dashed" : "solid" });
    segments.push({ x1: crossX, y1: crossY, x2: xFn(curr.t), y2: yFn(curr.value), style: currAbove ? "dashed" : "solid" });
  }
  return segments;
}

function renderWinProbabilityChart({ points, homeId, awayId, homeName, awayName, winnerId, loserId }) {
  if (!Array.isArray(points) || !points.length) return `<p class="home-empty">그래프 데이터가 없습니다.</p>`;
  const width = 620;
  const height = 228;
  const pad = { t: 20, r: 24, b: 34, l: 42 };
  const w = width - pad.l - pad.r;
  const h = height - pad.t - pad.b;
  const maxT = Math.max(...points.map((p) => Number(p.t || 0)), 2880);
  const minT = 0;
  const x = (v) => pad.l + ((Number(v) - minT) / Math.max(1, maxT - minT)) * w;
  const y = (v) => pad.t + (1 - (Number(v) / 100)) * h;
  const winnerColor = getTeamBrandColor(winnerId, "#111827");
  const loserColor = getTeamBrandColor(loserId, "#64748b");
  const transformed = points.map((p) => ({
    t: Number(p.t || 0),
    value: Math.max(0, Math.min(100, Number((String(winnerId) === String(homeId) ? p.home : p.away) || 0) * 100)),
    homePct: Math.max(0, Math.min(100, Number(p.home || 0) * 100)),
    awayPct: Math.max(0, Math.min(100, Number(p.away || 0) * 100)),
  }));
  const segments = buildLineSegments(transformed, x, y, 50);
  const quarterBoundaries = [0, 720, 1440, 2160, 2880].filter((t) => t <= maxT);
  const quarterCenters = [360, 1080, 1800, 2520].filter((t) => t <= maxT);

  return `
    <div class="game-result-chart-header-row">
      ${chartTeamLegend({ teamId: awayId, teamName: awayName, teamColor: String(awayId) === String(winnerId) ? winnerColor : loserColor, lineStyle: String(awayId) === String(winnerId) ? "dashed" : "solid" })}
      ${chartTeamLegend({ teamId: homeId, teamName: homeName, teamColor: String(homeId) === String(winnerId) ? winnerColor : loserColor, lineStyle: String(homeId) === String(winnerId) ? "dashed" : "solid" })}
    </div>
    <div class="game-result-chart-interactive" data-chart-type="winprob" data-points='${escapeHtml(JSON.stringify(transformed))}' data-winner-id="${escapeHtml(winnerId)}" data-loser-id="${escapeHtml(loserId)}" data-home-id="${escapeHtml(homeId)}" data-away-id="${escapeHtml(awayId)}">
      <svg class="game-result-chart game-result-chart-winprob" viewBox="0 0 ${width} ${height}" role="img" aria-label="승률 그래프">
        <line x1="${pad.l}" y1="${pad.t}" x2="${pad.l}" y2="${height - pad.b}" class="axis" />
        <line x1="${pad.l}" y1="${height - pad.b}" x2="${width - pad.r}" y2="${height - pad.b}" class="axis" />
        ${quarterBoundaries.map((t) => `<line x1="${x(t)}" y1="${pad.t}" x2="${x(t)}" y2="${height - pad.b}" class="grid" />`).join("")}
        <line x1="${pad.l}" y1="${y(100)}" x2="${width - pad.r}" y2="${y(100)}" class="grid" />
        <line x1="${pad.l}" y1="${y(50)}" x2="${width - pad.r}" y2="${y(50)}" class="grid is-mid" />
        <line x1="${pad.l}" y1="${y(0)}" x2="${width - pad.r}" y2="${y(0)}" class="grid" />
        <text x="${pad.l - 8}" y="${y(100) + 4}" text-anchor="end" class="axis-text">100%</text>
        <text x="${pad.l - 8}" y="${y(50) + 4}" text-anchor="end" class="axis-text">50%</text>
        <text x="${pad.l - 8}" y="${y(0) + 4}" text-anchor="end" class="axis-text">0%</text>
        ${quarterCenters.map((t, idx) => `<text x="${x(t)}" y="${height - 8}" text-anchor="middle" class="axis-text">${idx + 1}Q</text>`).join("")}
        ${segments.map((seg) => `<line x1="${seg.x1}" y1="${seg.y1}" x2="${seg.x2}" y2="${seg.y2}" stroke="${seg.style === "dashed" ? winnerColor : loserColor}" stroke-width="3" stroke-linecap="round" ${seg.style === "dashed" ? "stroke-dasharray=\"6 5\"" : ""} />`).join("")}
        <line class="chart-hover-line" x1="${pad.l}" y1="${pad.t}" x2="${pad.l}" y2="${height - pad.b}" />
        <circle class="chart-hover-dot" cx="${pad.l}" cy="${y(50)}" r="4" />
      </svg>
      <div class="game-result-chart-tooltip" role="status" aria-live="polite"></div>
    </div>
  `;
}

function renderGameFlowChart({ points, homeId, awayId, homeName, awayName, winnerId }) {
  const flowPoints = Array.isArray(points) ? points : [];
  if (!flowPoints.length) return `<p class="home-empty">그래프 데이터가 없습니다.</p>`;
  const maxScore = Math.max(...flowPoints.flatMap((p) => [Number(p.home_score || 0), Number(p.away_score || 0)]), 100);
  const chartSvg = svgLineChart({
    points: flowPoints,
    yMin: 0,
    yMax: maxScore,
    homeColor: getTeamBrandColor(homeId, "#1d4ed8"),
    awayColor: getTeamBrandColor(awayId, "#ef4444"),
    yLabelFmt: (v) => `${Math.round(v)}`,
    homeDashed: String(homeId) === String(winnerId),
    awayDashed: String(awayId) === String(winnerId),
  });

  return `
    <div class="game-result-chart-header-row">
      ${chartTeamLegend({ teamId: awayId, teamName: awayName, teamColor: getTeamBrandColor(awayId, "#ef4444"), lineStyle: String(awayId) === String(winnerId) ? "dashed" : "solid" })}
      ${chartTeamLegend({ teamId: homeId, teamName: homeName, teamColor: getTeamBrandColor(homeId, "#1d4ed8"), lineStyle: String(homeId) === String(winnerId) ? "dashed" : "solid" })}
    </div>
    ${chartSvg}
  `;
}

function bindWinProbabilityHover(container) {
  if (!container) return;
  const points = JSON.parse(container.dataset.points || "[]");
  if (!points.length) return;
  const svg = container.querySelector("svg");
  const hoverLine = container.querySelector(".chart-hover-line");
  const hoverDot = container.querySelector(".chart-hover-dot");
  const tooltip = container.querySelector(".game-result-chart-tooltip");
  if (!svg || !hoverLine || !hoverDot || !tooltip) return;

  const vb = svg.viewBox.baseVal;
  const pad = { t: 20, r: 24, b: 34, l: 42 };
  const maxT = Math.max(...points.map((p) => Number(p.t || 0)), 2880);
  const w = vb.width - pad.l - pad.r;
  const h = vb.height - pad.t - pad.b;
  const x = (v) => pad.l + ((Number(v) / Math.max(1, maxT)) * w);
  const y = (v) => pad.t + (1 - (Number(v) / 100)) * h;

  const setAtIndex = (idx) => {
    const p = points[Math.max(0, Math.min(points.length - 1, idx))];
    const px = x(p.t);
    hoverLine.setAttribute("x1", String(px));
    hoverLine.setAttribute("x2", String(px));
    hoverDot.setAttribute("cx", String(px));
    hoverDot.setAttribute("cy", String(y(p.value)));
    tooltip.innerHTML = `
      <p><strong>${escapeHtml(container.dataset.awayId)}</strong> ${p.awayPct.toFixed(1)}%</p>
      <p><strong>${escapeHtml(container.dataset.homeId)}</strong> ${p.homePct.toFixed(1)}%</p>
    `;
  };

  const onMove = (event) => {
    const rect = svg.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)));
    const targetT = ratio * maxT;
    let nearest = 0;
    let nearestDist = Math.abs(points[0].t - targetT);
    for (let i = 1; i < points.length; i += 1) {
      const d = Math.abs(points[i].t - targetT);
      if (d < nearestDist) {
        nearest = i;
        nearestDist = d;
      }
    }
    setAtIndex(nearest);
  };

  svg.addEventListener("mousemove", onMove);
  svg.addEventListener("mouseenter", () => {
    container.classList.add("is-hovering");
    setAtIndex(points.length - 1);
  });
  svg.addEventListener("mouseleave", () => {
    container.classList.remove("is-hovering");
  });
  setAtIndex(points.length - 1);
}

function renderLinescore(lines = {}, homeTeamId = "", awayTeamId = "") {
  const quarters = Array.isArray(lines.quarters) ? lines.quarters : [];
  if (!quarters.length) return `<p class="home-empty">쿼터 박스스코어 데이터가 없습니다.</p>`;
  const maxQ = Math.max(...quarters.map((q) => Number(q.period || 0)), 4);
  const periods = Array.from({ length: maxQ }, (_, i) => i + 1);

  const scoreCell = (obj, p) => {
    const value = Number(obj?.by_period?.[String(p)] ?? obj?.by_period?.[p]);
    return `<td>${Number.isFinite(value) ? value : "-"}</td>`;
  };

  const home = quarters.find((q) => String(q.team_id || "") === String(homeTeamId)) || {};
  const away = quarters.find((q) => String(q.team_id || "") === String(awayTeamId)) || {};

  return `
    <table class="game-result-linescore-table">
      <thead><tr><th>TEAM</th>${periods.map((p) => `<th>${p}</th>`).join("")}<th>T</th></tr></thead>
      <tbody>
        <tr><th>${away.team_id || "AWY"}</th>${periods.map((p) => scoreCell(away, p)).join("")}<td>${away.total ?? "-"}</td></tr>
        <tr><th>${home.team_id || "HME"}</th>${periods.map((p) => scoreCell(home, p)).join("")}<td>${home.total ?? "-"}</td></tr>
      </tbody>
    </table>
  `;
}

function renderLeaders(leaders = {}) {
  const row = (label, home, away) => `
    <div class="game-result-leader-row">
      <div><strong>${away?.value ?? "-"}</strong><p>${away?.name || "-"}</p></div>
      <span>${label}</span>
      <div><strong>${home?.value ?? "-"}</strong><p>${home?.name || "-"}</p></div>
    </div>
  `;
  return [
    row("Points", leaders?.points?.home, leaders?.points?.away),
    row("Rebounds", leaders?.rebounds?.home, leaders?.rebounds?.away),
    row("Assists", leaders?.assists?.home, leaders?.assists?.away),
  ].join("");
}

function renderMatchups(matchups = {}) {
  const season = matchups?.season_record || {};
  const completed = Array.isArray(matchups.completed) ? matchups.completed : [];
  const upcoming = Array.isArray(matchups.upcoming) ? matchups.upcoming : [];

  const completedRows = completed.length
    ? completed.map((g) => `<li><strong>${g.date || "-"}</strong><span>${g.user_team_home ? "vs" : "@"} ${g.user_team_score ?? "-"}-${g.opponent_score ?? "-"} · ${g.result || "-"}</span></li>`).join("")
    : `<li class="home-empty">이번 시즌 완료된 맞대결이 없습니다.</li>`;

  const upcomingRows = upcoming.length
    ? upcoming.map((g) => `<li><strong>${g.date || "-"}</strong><span>${g.user_team_home ? "vs" : "@"} ${g.tipoff_time || "--:-- --"}</span></li>`).join("")
    : `<li class="home-empty">예정된 맞대결이 없습니다.</li>`;

  return `
    <div class="game-result-matchup-record">시즌 상대전적 <strong>${season.user_team_wins ?? 0}-${season.user_team_losses ?? 0}</strong></div>
    <h4>Completed</h4>
    <ul>${completedRows}</ul>
    <h4>Upcoming</h4>
    <ul>${upcomingRows}</ul>
  `;
}

function renderBoxscoreTable(team = {}) {
  const teamName = team?.team_name || team?.team_id || "TEAM";
  const teamId = String(team?.team_id || "").toUpperCase();
  const players = Array.isArray(team?.players) ? [...team.players] : [];
  players.sort((a, b) => toNumber(b?.MIN) - toNumber(a?.MIN));

  const totals = team?.totals || deriveTeamTotals(players);

  const playerRows = players.length
    ? players.map((p) => `
      <tr>
        <th>${p?.Name || p?.PlayerID || "-"}</th>
        <td>${toNumber(p?.MIN).toFixed(1)}</td>
        <td>${toStatInt(p?.PTS)}</td>
        <td>${ratioLabel(p?.FGM, p?.FGA)}</td>
        <td>${ratioLabel(p?.["3PM"], p?.["3PA"])}</td>
        <td>${ratioLabel(p?.FTM, p?.FTA)}</td>
        <td>${toStatInt(p?.ORB)}</td>
        <td>${toStatInt(p?.DRB)}</td>
        <td>${toStatInt(p?.REB)}</td>
        <td>${toStatInt(p?.AST)}</td>
        <td>${toStatInt(p?.TOV)}</td>
        <td>${toStatInt(p?.STL)}</td>
        <td>${toStatInt(p?.BLK)}</td>
        <td>${toStatInt(p?.PF)}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="14" class="home-empty">선수 박스스코어가 없습니다.</td></tr>`;

  return `
    <section class="game-result-boxscore-team">
      <h4 class="game-result-team-heading">${renderTeamLogoMark(teamId, "game-result-inline-team-logo")}<span>${teamName}</span></h4>
      <div class="game-result-boxscore-table-wrap">
        <table class="game-result-boxscore-table">
          <thead>
            <tr>
              <th>PLAYER</th><th>MIN</th><th>PTS</th><th>FGM-FGA</th><th>3PM-3PA</th><th>FTM-FTA</th>
              <th>ORB</th><th>DRB</th><th>REB</th><th>AST</th><th>TOV</th><th>STL</th><th>BLK</th><th>PF</th>
            </tr>
          </thead>
          <tbody>
            ${playerRows}
            <tr class="team-total-row">
              <th>TEAM</th><td>-</td><td>${toStatInt(totals.PTS)}</td>
              <td>${ratioLabel(totals.FGM, totals.FGA)}</td>
              <td>${ratioLabel(totals["3PM"], totals["3PA"])}</td>
              <td>${ratioLabel(totals.FTM, totals.FTA)}</td>
              <td>${toStatInt(totals.ORB)}</td><td>${toStatInt(totals.DRB)}</td><td>${toStatInt(totals.REB)}</td>
              <td>${toStatInt(totals.AST)}</td><td>${toStatInt(totals.TOV)}</td><td>${toStatInt(totals.STL)}</td><td>${toStatInt(totals.BLK)}</td><td>${toStatInt(totals.PF)}</td>
            </tr>
            <tr class="team-pct-row">
              <th></th><td></td><td></td>
              <td>${pctLabel(totals.FGM, totals.FGA)}</td>
              <td>${pctLabel(totals["3PM"], totals["3PA"])}</td>
              <td>${pctLabel(totals.FTM, totals.FTA)}</td>
              <td colspan="8"></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function renderBoxscore(result = {}) {
  const boxscore = result?.boxscore || {};
  const away = boxscore?.away || {};
  const home = boxscore?.home || {};

  return `${renderBoxscoreTable(away)}${renderBoxscoreTable(home)}`;
}

function renderTeamstats(result = {}) {
  const away = result?.boxscore?.away || {};
  const home = result?.boxscore?.home || {};
  const awayTeamId = String(away?.team_id || "").toUpperCase();
  const homeTeamId = String(home?.team_id || "").toUpperCase();
  const awayTotals = result?.teamstats?.away || deriveTeamTotals(away.players || []);
  const homeTotals = result?.teamstats?.home || deriveTeamTotals(home.players || []);

  const row = (label, awayVal, homeVal) => `<tr><th>${label}</th><td>${awayVal}</td><td>${homeVal}</td></tr>`;
  return `
    <table class="game-result-teamstats-table">
      <thead>
        <tr>
          <th>STAT</th>
          <th><span class="game-result-team-heading">${renderTeamLogoMark(awayTeamId, "game-result-inline-team-logo")}<span>${away.team_name || away.team_id || "AWAY"}</span></span></th>
          <th><span class="game-result-team-heading">${renderTeamLogoMark(homeTeamId, "game-result-inline-team-logo")}<span>${home.team_name || home.team_id || "HOME"}</span></span></th>
        </tr>
      </thead>
      <tbody>
        ${row("PTS", toStatInt(awayTotals.PTS), toStatInt(homeTotals.PTS))}
        ${row("FG", ratioLabel(awayTotals.FGM, awayTotals.FGA), ratioLabel(homeTotals.FGM, homeTotals.FGA))}
        ${row("FG%", pctLabel(awayTotals.FGM, awayTotals.FGA), pctLabel(homeTotals.FGM, homeTotals.FGA))}
        ${row("3P", ratioLabel(awayTotals["3PM"], awayTotals["3PA"]), ratioLabel(homeTotals["3PM"], homeTotals["3PA"]))}
        ${row("3P%", pctLabel(awayTotals["3PM"], awayTotals["3PA"]), pctLabel(homeTotals["3PM"], homeTotals["3PA"]))}
        ${row("FT", ratioLabel(awayTotals.FTM, awayTotals.FTA), ratioLabel(homeTotals.FTM, homeTotals.FTA))}
        ${row("FT%", pctLabel(awayTotals.FTM, awayTotals.FTA), pctLabel(homeTotals.FTM, homeTotals.FTA))}
        ${row("ORB", toStatInt(awayTotals.ORB), toStatInt(homeTotals.ORB))}
        ${row("DRB", toStatInt(awayTotals.DRB), toStatInt(homeTotals.DRB))}
        ${row("REB", toStatInt(awayTotals.REB), toStatInt(homeTotals.REB))}
        ${row("AST", toStatInt(awayTotals.AST), toStatInt(homeTotals.AST))}
        ${row("TOV", toStatInt(awayTotals.TOV), toStatInt(homeTotals.TOV))}
        ${row("STL", toStatInt(awayTotals.STL), toStatInt(homeTotals.STL))}
        ${row("BLK", toStatInt(awayTotals.BLK), toStatInt(homeTotals.BLK))}
        ${row("PF", toStatInt(awayTotals.PF), toStatInt(homeTotals.PF))}
      </tbody>
    </table>
  `;
}

function normalizePbpItem(raw = {}, index = 0) {
  const scoreHome = Number(raw?.score?.home);
  const scoreAway = Number(raw?.score?.away);
  const rawTags = Array.isArray(raw?.tags) ? raw.tags : [];
  const normalizedGroupChildren = Array.isArray(raw?.group?.children)
    ? raw.group.children.map((child, childIdx) => normalizePbpItem(child, childIdx))
    : [];

  return {
    seq: Number.isFinite(Number(raw?.seq)) ? Number(raw.seq) : index + 1,
    period: Math.max(1, toStatInt(raw?.period || 1)),
    clock: String(raw?.clock || "--:--"),
    teamId: String(raw?.team_id || "").toUpperCase(),
    eventKey: String(raw?.event_key || "play"),
    title: String(raw?.title || "Play"),
    description: String(raw?.description || "이벤트 상세 정보를 준비 중입니다."),
    tags: rawTags.map((tag) => String(tag || "").toLowerCase()).filter(Boolean),
    badges: Array.isArray(raw?.badges) ? raw.badges.map((badge) => String(badge || "").toLowerCase()).filter(Boolean) : [],
    scoreChange: toStatInt(raw?.score_change),
    score: {
      home: Number.isFinite(scoreHome) ? scoreHome : null,
      away: Number.isFinite(scoreAway) ? scoreAway : null,
    },
    group: raw?.group
      ? {
        id: String(raw.group.id || `group-${index + 1}`),
        collapsed: Boolean(raw.group.collapsed),
        size: Math.max(0, toStatInt(raw.group.size || normalizedGroupChildren.length)),
        children: normalizedGroupChildren,
      }
      : null,
  };
}

function comparePbpItems(a, b) {
  if (a.period !== b.period) return a.period - b.period;
  const clockDiff = parseClockToSeconds(b.clock) - parseClockToSeconds(a.clock);
  if (clockDiff !== 0) return clockDiff;
  return a.seq - b.seq;
}

function normalizePbp(result = {}) {
  const raw = result?.play_by_play || {};
  const source = String(raw?.source || "unknown");
  const items = Array.isArray(raw?.items)
    ? raw.items.map((item, idx) => normalizePbpItem(item, idx)).sort(comparePbpItems)
    : [];
  const available = Boolean(raw?.available) && items.length > 0;

  return {
    available,
    source,
    items,
    meta: {
      totalReplayEvents: toStatInt(raw?.meta?.total_replay_events),
      exposedPbpItems: toStatInt(raw?.meta?.exposed_pbp_items || items.length),
      filteredOut: toStatInt(raw?.meta?.filtered_out),
      collapsedGroups: toStatInt(raw?.meta?.collapsed_groups),
    },
  };
}

function getPbpUiState() {
  if (!state.gameResultPbp) {
    state.gameResultPbp = {
      period: "ALL",
      team: "ALL",
      tags: new Set(),
      onlyKey: false,
      expandedGroups: new Set(),
      renderLimit: PBP_INITIAL_RENDER_LIMIT,
      cachedResult: null,
      cachedPbp: null,
    };
  }
  return state.gameResultPbp;
}

function getNormalizedPbp(result = {}) {
  const uiState = getPbpUiState();
  if (uiState.cachedResult === result && uiState.cachedPbp) {
    return uiState.cachedPbp;
  }
  const normalized = normalizePbp(result);
  uiState.cachedResult = result;
  uiState.cachedPbp = normalized;
  return normalized;
}

function isPeriodMatch(item, periodFilter) {
  if (periodFilter === "ALL") return true;
  if (periodFilter === "OT") return item.period >= 5;
  if (periodFilter.startsWith("Q")) return item.period === toStatInt(periodFilter.slice(1));
  return true;
}

function isTeamMatch(item, teamFilter, homeId, awayId) {
  if (teamFilter === "ALL") return true;
  if (teamFilter === "HOME") return item.teamId === String(homeId || "").toUpperCase();
  if (teamFilter === "AWAY") return item.teamId === String(awayId || "").toUpperCase();
  return true;
}

function isTagMatch(item, selectedTags) {
  if (!selectedTags || !selectedTags.size) return true;
  return item.tags.some((tag) => selectedTags.has(tag));
}

function filterPbpItems(items = [], uiState = {}, homeId = "", awayId = "") {
  return (Array.isArray(items) ? items : []).filter((item) => {
    if (!isPeriodMatch(item, uiState.period)) return false;
    if (!isTeamMatch(item, uiState.team, homeId, awayId)) return false;
    if (!isTagMatch(item, uiState.tags)) return false;
    if (uiState.onlyKey && item.scoreChange <= 0 && !item.badges?.length) return false;
    return true;
  });
}

function renderPbpSummary(pbp = {}, uiState = {}, filteredItems = []) {
  if (!pbp.available) {
    return `PBP 로그를 제공하지 않는 경기입니다. (source: ${escapeHtml(pbp?.source || "unknown")})`;
  }
  const exposed = toStatInt(pbp?.meta?.exposedPbpItems || pbp?.items?.length || 0);
  const filteredOut = toStatInt(pbp?.meta?.filteredOut || 0);
  const source = escapeHtml(pbp?.source || "unknown");
  const applied = [];
  if (uiState.period !== "ALL") applied.push(`period:${uiState.period}`);
  if (uiState.team !== "ALL") applied.push(`team:${uiState.team}`);
  if (uiState.tags?.size) applied.push(`tags:${Array.from(uiState.tags).join(",")}`);
  if (uiState.onlyKey) applied.push("onlyKey:on");
  const appliedLabel = applied.length ? ` · filters[${applied.join(" / ")}]` : "";
  return `표시 ${filteredItems.length}/${exposed}개 · 제외 ${filteredOut}개 · source:${source}${appliedLabel}`;
}

function renderPbpEmpty(pbp = {}, uiState = {}) {
  if (!pbp?.available) {
    return `<li class="game-result-pbp-empty">PBP 데이터가 없습니다. (${escapeHtml(pbp?.source || "unknown")})</li>`;
  }
  return `<li class="game-result-pbp-empty">현재 필터(${escapeHtml(uiState.period || "ALL")}/${escapeHtml(uiState.team || "ALL")})에서 표시 가능한 이벤트가 없습니다.</li>`;
}

function badgeLabel(badge = "") {
  if (badge === "lead_change") return "LEAD CHANGE";
  if (badge === "tie") return "TIE";
  if (badge === "clutch") return "CLUTCH";
  return badge.toUpperCase();
}

function renderPbpBadges(item = {}) {
  const badges = Array.isArray(item?.badges) ? item.badges : [];
  if (!badges.length) return "";
  return `<div class="game-result-pbp-badges">${badges.map((badge) => `<span class="game-result-pbp-badge is-${escapeHtml(badge)}">${escapeHtml(badgeLabel(badge))}</span>`).join("")}</div>`;
}

function pbpImportance(item = {}) {
  const badges = Array.isArray(item?.badges) ? item.badges : [];
  if (badges.includes("clutch") || badges.includes("lead_change") || item.scoreChange >= 3) return "critical";
  if (item.scoreChange > 0 || ["turnover", "foul", "timeout", "substitution"].includes(item.eventKey)) return "key";
  return "normal";
}

function pbpTeamSide(teamId = "", homeId = "", awayId = "") {
  const t = String(teamId || "").toUpperCase();
  if (t && t === String(homeId || "").toUpperCase()) return "home";
  if (t && t === String(awayId || "").toUpperCase()) return "away";
  return "neutral";
}

function renderPbpMarker(item = {}, homeId = "", awayId = "") {
  const teamId = String(item?.teamId || "").toUpperCase();
  const side = pbpTeamSide(teamId, homeId, awayId);
  const src = teamId ? `/static/team_logos/${teamId}.png` : "";
  const alt = teamId ? `${teamId} logo` : "team logo";
  const fallbackLabel = side === "home" ? "H" : (side === "away" ? "A" : "-");
  const logoHtml = teamId
    ? `<img class="game-result-pbp-marker-logo" src="${escapeHtml(src)}" alt="${escapeHtml(alt)}" loading="lazy" />`
    : `<span class="game-result-pbp-marker-logo-fallback">${escapeHtml(fallbackLabel)}</span>`;
  return `<div class="game-result-pbp-marker" aria-hidden="true">${logoHtml}</div>`;
}

function renderSinglePbpItem(item = {}, homeId = "", awayId = "") {
  const scoreLabel = Number.isFinite(item?.score?.home) && Number.isFinite(item?.score?.away)
    ? `${homeId} ${item.score.home} - ${item.score.away} ${awayId}`
    : "-";
  const tagsHtml = item.tags.length
    ? `<div class="game-result-pbp-tags">${item.tags.map((tag) => `<span class="game-result-pbp-tag">${escapeHtml(tag)}</span>`).join("")}</div>`
    : "";
  const importance = pbpImportance(item);
  return `
    <li class="game-result-pbp-item is-${importance}" data-event-key="${escapeHtml(item.eventKey)}">
      ${renderPbpMarker(item, homeId, awayId)}
      <div class="game-result-pbp-time">${escapeHtml(item.clock)} · ${escapeHtml(toPeriodLabel(item.period))}</div>
      <div class="game-result-pbp-body">
        <p class="game-result-pbp-title">${escapeHtml(item.title)}</p>
        <p class="game-result-pbp-desc">${escapeHtml(item.description)}</p>
        ${renderPbpBadges(item)}
        ${tagsHtml}
      </div>
      <div class="game-result-pbp-score">${escapeHtml(scoreLabel)}</div>
    </li>
  `;
}

function renderPbpGroup(item = {}, uiState = {}, homeId = "", awayId = "") {
  const groupId = item?.group?.id || `group-${item.seq}`;
  const isExpanded = uiState.expandedGroups?.has(groupId);
  const childItems = Array.isArray(item?.group?.children) ? item.group.children : [];
  const importance = pbpImportance(item);
  const childHtml = isExpanded
    ? childItems.map((child) => renderSinglePbpItem(child, homeId, awayId)).join("")
    : "";
  return `
    <li class="game-result-pbp-group is-${importance}" data-group-id="${escapeHtml(groupId)}">
      <div class="game-result-pbp-group-row">
        ${renderPbpMarker(item, homeId, awayId)}
        <button type="button" class="game-result-pbp-group-toggle" data-action="toggle-group" data-group-id="${escapeHtml(groupId)}" aria-expanded="${isExpanded ? "true" : "false"}">
          ${escapeHtml(item.clock)} · ${escapeHtml(toPeriodLabel(item.period))} · ${escapeHtml(item.title)} (${toStatInt(item?.group?.size || childItems.length)}개)
        </button>
      </div>
      <ul class="game-result-pbp-group-children ${isExpanded ? "is-open" : ""}">${childHtml}</ul>
    </li>
  `;
}

function renderPbpList(items = [], uiState = {}, homeId = "", awayId = "") {
  if (!Array.isArray(items) || !items.length) return "";
  return items.map((item) => (
    item?.group ? renderPbpGroup(item, uiState, homeId, awayId) : renderSinglePbpItem(item, homeId, awayId)
  )).join("");
}

function syncPbpFilterUi(uiState = {}) {
  const toolbar = els.gameResultPbpToolbar;
  if (!toolbar) return;
  const chips = toolbar.querySelectorAll("[data-filter-type]");
  chips.forEach((chip) => {
    const type = chip.dataset.filterType;
    const value = chip.dataset.filterValue;
    if (type === "period") chip.classList.toggle("is-active", uiState.period === value);
    if (type === "team") chip.classList.toggle("is-active", uiState.team === value);
    if (type === "tag") chip.classList.toggle("is-active", uiState.tags?.has(value));
    if (type === "only-key") chip.classList.toggle("is-active", Boolean(uiState.onlyKey));
    chip.setAttribute("aria-pressed", chip.classList.contains("is-active") ? "true" : "false");
  });
}

function rerenderPbpSection(result = {}, options = {}) {
  const { resetLimit = false } = options;
  const header = result?.header || {};
  const homeId = String(header.home_team_id || "HOME").toUpperCase();
  const awayId = String(header.away_team_id || "AWAY").toUpperCase();
  const uiState = getPbpUiState();
  if (resetLimit || !Number.isFinite(uiState.renderLimit) || uiState.renderLimit <= 0) {
    uiState.renderLimit = PBP_INITIAL_RENDER_LIMIT;
  }

  const pbp = getNormalizedPbp(result);
  const filteredItems = filterPbpItems(pbp.items, uiState, homeId, awayId);
  const visibleItems = filteredItems.slice(0, uiState.renderLimit);

  if (els.gameResultPbpSummary) {
    els.gameResultPbpSummary.textContent = renderPbpSummary(pbp, uiState, filteredItems);
  }
  if (els.gameResultPbpList) {
    els.gameResultPbpList.innerHTML = visibleItems.length
      ? renderPbpList(visibleItems, uiState, homeId, awayId)
      : renderPbpEmpty(pbp, uiState);
  }
  if (els.gameResultPbpLoadMore) {
    const hasMore = filteredItems.length > visibleItems.length;
    els.gameResultPbpLoadMore.hidden = !hasMore;
    if (hasMore) {
      els.gameResultPbpLoadMore.textContent = `더 보기 (${visibleItems.length}/${filteredItems.length})`;
    }
  }
  syncPbpFilterUi(uiState);
  return pbp;
}

function bindPbpControls() {
  if (bindPbpControls.bound) return;
  const toolbar = els.gameResultPbpToolbar;
  const list = els.gameResultPbpList;

  const applyToolbarAction = (button) => {
    if (!button) return;
    const uiState = getPbpUiState();
    const type = button.dataset.filterType;
    const value = button.dataset.filterValue;
    if (type === "period") uiState.period = value || "ALL";
    if (type === "team") uiState.team = value || "ALL";
    if (type === "tag" && value) {
      if (uiState.tags.has(value)) uiState.tags.delete(value);
      else uiState.tags.add(value);
    }
    if (type === "only-key") uiState.onlyKey = !uiState.onlyKey;
    rerenderPbpSection(state.lastGameResult || {}, { resetLimit: true });
  };

  if (toolbar) {
    toolbar.addEventListener("click", (event) => {
      const button = event.target.closest("[data-filter-type]");
      applyToolbarAction(button);
    });

    toolbar.addEventListener("keydown", (event) => {
      const button = event.target.closest("[data-filter-type]");
      if (!button) return;
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        const group = button.closest(".game-result-pbp-filter-group");
        const buttons = Array.from(group?.querySelectorAll("[data-filter-type]") || []);
        const currentIndex = buttons.indexOf(button);
        if (currentIndex < 0 || !buttons.length) return;
        const nextIndex = event.key === "ArrowRight"
          ? (currentIndex + 1) % buttons.length
          : (currentIndex - 1 + buttons.length) % buttons.length;
        buttons[nextIndex]?.focus();
        event.preventDefault();
      }
      if (event.key === "Enter" || event.key === " ") {
        applyToolbarAction(button);
        event.preventDefault();
      }
    });
  }

  if (els.gameResultPbpLoadMore) {
    els.gameResultPbpLoadMore.addEventListener("click", () => {
      const uiState = getPbpUiState();
      uiState.renderLimit += PBP_RENDER_STEP;
      rerenderPbpSection(state.lastGameResult || {});
    });
  }

  if (list) {
    list.addEventListener("click", (event) => {
      const toggle = event.target.closest('[data-action="toggle-group"]');
      if (!toggle) return;
      const groupId = String(toggle.dataset.groupId || "");
      if (!groupId) return;
      const uiState = getPbpUiState();
      if (uiState.expandedGroups.has(groupId)) uiState.expandedGroups.delete(groupId);
      else uiState.expandedGroups.add(groupId);
      rerenderPbpSection(state.lastGameResult || {});
    });

    list.addEventListener("keydown", (event) => {
      const toggle = event.target.closest('[data-action="toggle-group"]');
      if (!toggle) return;
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      const toggles = Array.from(list.querySelectorAll('[data-action="toggle-group"]'));
      const currentIndex = toggles.indexOf(toggle);
      if (currentIndex < 0) return;
      const nextIndex = event.key === "ArrowDown"
        ? Math.min(currentIndex + 1, toggles.length - 1)
        : Math.max(currentIndex - 1, 0);
      toggles[nextIndex]?.focus();
      event.preventDefault();
    });
  }
  bindPbpControls.bound = true;
}

function setActiveTab(tab) {
  const tabMap = {
    gamecast: els.gameResultTabGamecast,
    playbyplay: els.gameResultTabPlaybyplay,
    boxscore: els.gameResultTabBoxscore,
    teamstats: els.gameResultTabTeamstats,
  };
  const viewMap = {
    gamecast: els.gameResultViewGamecast,
    playbyplay: els.gameResultViewPlaybyplay,
    boxscore: els.gameResultViewBoxscore,
    teamstats: els.gameResultViewTeamstats,
  };

  for (const key of TAB_KEYS) {
    const button = tabMap[key];
    const view = viewMap[key];
    if (button) {
      button.classList.toggle("is-active", key === tab);
      button.setAttribute("aria-selected", key === tab ? "true" : "false");
    }
    if (view) {
      view.classList.toggle("is-active", key === tab);
      view.setAttribute("aria-hidden", key === tab ? "false" : "true");
    }
  }
}

function bindGameResultTabs() {
  if (gameResultTabsBound) return;
  const tabButtons = [els.gameResultTabGamecast, els.gameResultTabPlaybyplay, els.gameResultTabBoxscore, els.gameResultTabTeamstats].filter(Boolean);
  tabButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.tab;
      if (!TAB_KEYS.includes(key)) return;
      state.gameResultActiveTab = key;
      setActiveTab(key);
    });
  });
  gameResultTabsBound = true;
}

function renderGameResult(result) {
  state.lastGameResult = result;
  const header = result?.header || {};

  const homeId = String(header.home_team_id || "").toUpperCase();
  const awayId = String(header.away_team_id || "").toUpperCase();
  const homeScore = Number(header.home_score || 0);
  const awayScore = Number(header.away_score || 0);
  const winnerId = homeScore >= awayScore ? homeId : awayId;
  const loserId = winnerId === homeId ? awayId : homeId;

  els.gameResultTitle.textContent = `${header.date || "-"} 경기 결과`;
  els.gameResultSubtitle.textContent = `${TEAM_FULL_NAMES[awayId] || awayId} at ${TEAM_FULL_NAMES[homeId] || homeId}`;

  els.gameResultHomeName.textContent = header.home_team_name || TEAM_FULL_NAMES[homeId] || homeId;
  els.gameResultAwayName.textContent = header.away_team_name || TEAM_FULL_NAMES[awayId] || awayId;
  els.gameResultHomeRecord.textContent = header.user_team_id === homeId
    ? (header.user_team_record_after_game || "-")
    : (header.opponent_record_after_game || "-");
  els.gameResultAwayRecord.textContent = header.user_team_id === awayId
    ? (header.user_team_record_after_game || "-")
    : (header.opponent_record_after_game || "-");
  els.gameResultHomeScore.textContent = String(header.home_score ?? "-");
  els.gameResultAwayScore.textContent = String(header.away_score ?? "-");
  els.gameResultFinalTag.textContent = "Final";

  applyTeamLogo(els.gameResultHomeLogo, homeId);
  applyTeamLogo(els.gameResultAwayLogo, awayId);

  els.gameResultLinescore.innerHTML = renderLinescore(header.boxscore_lines || {}, homeId, awayId);
  els.gameResultLeaders.innerHTML = renderLeaders(result?.leaders || {});
  els.gameResultMatchups.innerHTML = renderMatchups(result?.matchups || {});
  els.gameResultBoxscore.innerHTML = renderBoxscore(result);
  els.gameResultTeamstats.innerHTML = renderTeamstats(result);

  const uiState = getPbpUiState();
  uiState.period = "ALL";
  uiState.team = "ALL";
  uiState.tags = new Set();
  uiState.onlyKey = false;
  uiState.expandedGroups = new Set();
  uiState.renderLimit = PBP_INITIAL_RENDER_LIMIT;
  uiState.cachedResult = null;
  uiState.cachedPbp = null;

  const pbp = rerenderPbpSection(result, { resetLimit: true });

  const winSeries = result?.gamecast?.win_probability?.series || [];
  els.gameResultWinprobMeta.textContent = winSeries.length ? "쿼터 단위 승률 추정" : "데이터 없음";
  els.gameResultWinprobChart.innerHTML = renderWinProbabilityChart({
    points: winSeries,
    homeId,
    awayId,
    homeName: header.home_team_name,
    awayName: header.away_team_name,
    winnerId,
    loserId,
  });
  bindWinProbabilityHover(els.gameResultWinprobChart.querySelector('[data-chart-type="winprob"]'));

  const flowSeries = result?.gamecast?.game_flow?.series || [];
  els.gameResultFlowChart.innerHTML = renderGameFlowChart({
    points: flowSeries,
    homeId,
    awayId,
    homeName: header.home_team_name,
    awayName: header.away_team_name,
    winnerId,
  });

  bindGameResultTabs();
  bindPbpControls();

  const suggestedTab = result?.tabs?.default
    ? String(result.tabs.default).toLowerCase()
    : (pbp.available ? "playbyplay" : (state.gameResultActiveTab || "gamecast"));
  const activeTab = TAB_KEYS.includes(suggestedTab) ? suggestedTab : "gamecast";
  state.gameResultActiveTab = activeTab;
  setActiveTab(activeTab);

  activateScreen(els.gameResultScreen);
}

async function showGameResultScreenByGameId(gameId) {
  if (!state.selectedTeamId || !gameId) return;
  setLoading(true, "경기 리포트를 생성 중...");
  try {
    const result = await fetchJson(`/api/game/result/${encodeURIComponent(gameId)}?user_team_id=${encodeURIComponent(state.selectedTeamId)}`);
    renderGameResult(result);
  } finally {
    setLoading(false);
  }
}

export { renderGameResult, showGameResultScreenByGameId };
