import { els } from "../../app/dom.js";
import { state } from "../../app/state.js";
import { activateScreen } from "../../app/router.js";
import { fetchJson, setLoading } from "../../core/api.js";
import { TEAM_FULL_NAMES, applyTeamLogo } from "../../core/constants/teams.js";

function toClockLabel(totalSeconds) {
  const sec = Math.max(0, Number(totalSeconds || 0));
  const minute = Math.floor(sec / 60);
  const second = Math.floor(sec % 60);
  return `${String(minute).padStart(2, "0")}:${String(second).padStart(2, "0")}`;
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
