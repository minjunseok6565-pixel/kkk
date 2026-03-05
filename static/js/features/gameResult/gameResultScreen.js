import { els } from "../../app/dom.js";
import { state } from "../../app/state.js";
import { activateScreen } from "../../app/router.js";
import { fetchJson, setLoading } from "../../core/api.js";
import { TEAM_FULL_NAMES, applyTeamLogo } from "../../core/constants/teams.js";

const TAB_KEYS = ["gamecast", "boxscore", "teamstats"];
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

function svgLineChart({ points, width = 520, height = 180, yMin, yMax, homeColor, awayColor, yLabelFmt }) {
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
      <path d="${awayLine}" fill="none" stroke="${awayColor}" stroke-width="2.6" stroke-linecap="round" />
      <path d="${homeLine}" fill="none" stroke="${homeColor}" stroke-width="2.6" stroke-linecap="round" />
    </svg>
  `;
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
      <h4>${teamName}</h4>
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
  const awayTotals = result?.teamstats?.away || deriveTeamTotals(away.players || []);
  const homeTotals = result?.teamstats?.home || deriveTeamTotals(home.players || []);

  const row = (label, awayVal, homeVal) => `<tr><th>${label}</th><td>${awayVal}</td><td>${homeVal}</td></tr>`;
  return `
    <table class="game-result-teamstats-table">
      <thead><tr><th>STAT</th><th>${away.team_name || away.team_id || "AWAY"}</th><th>${home.team_name || home.team_id || "HOME"}</th></tr></thead>
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

function setActiveTab(tab) {
  const tabMap = {
    gamecast: els.gameResultTabGamecast,
    boxscore: els.gameResultTabBoxscore,
    teamstats: els.gameResultTabTeamstats,
  };
  const viewMap = {
    gamecast: els.gameResultViewGamecast,
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
  const tabButtons = [els.gameResultTabGamecast, els.gameResultTabBoxscore, els.gameResultTabTeamstats].filter(Boolean);
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

  const winSeries = result?.gamecast?.win_probability?.series || [];
  els.gameResultWinprobMeta.textContent = winSeries.length ? "시간대별 승률 추정" : "데이터 없음";
  els.gameResultWinprobChart.innerHTML = svgLineChart({
    points: winSeries,
    yMin: 0,
    yMax: 1,
    homeColor: "#1d4ed8",
    awayColor: "#ef4444",
    yLabelFmt: (v) => `${Math.round(v * 100)}%`,
  });

  const flowSeries = result?.gamecast?.game_flow?.series || [];
  const maxScore = Math.max(
    ...flowSeries.flatMap((p) => [Number(p.home_score || 0), Number(p.away_score || 0)]),
    100,
  );
  els.gameResultFlowChart.innerHTML = svgLineChart({
    points: flowSeries,
    yMin: 0,
    yMax: maxScore,
    homeColor: "#1d4ed8",
    awayColor: "#ef4444",
    yLabelFmt: (v) => `${Math.round(v)}`,
  });

  bindGameResultTabs();

  const defaultTab = String(result?.tabs?.default || state.gameResultActiveTab || "gamecast").toLowerCase();
  const activeTab = TAB_KEYS.includes(defaultTab) ? defaultTab : "gamecast";
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
