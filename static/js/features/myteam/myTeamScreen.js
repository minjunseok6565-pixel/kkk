import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import { setLoading } from "../../core/api.js";
import { num, clamp } from "../../core/guards.js";
import { formatHeightIn, formatWeightLb, formatMoney, formatWinPct } from "../../core/format.js";
import { TEAM_FULL_NAMES } from "../../core/constants/teams.js";
import { loadPlayerDetail } from "./playerDetail.js";
import { fetchTeamDetail, hasTeamDetailCache } from "../team/teamDetailCache.js";

let myTeamRequestSeq = 0;

function applyMyTeamDetail(detail, { resetSelection = true } = {}) {
  state.rosterRows = detail?.roster || [];
  if (resetSelection) state.selectedPlayerId = null;

  const teamName = state.selectedTeamName || TEAM_FULL_NAMES[state.selectedTeamId] || state.selectedTeamId;
  els.myTeamTitle.textContent = `${teamName} 선수단`;

  renderMyTeamOverview(detail?.summary || {}, state.rosterRows);
  rerenderMyTeamBoard();
  if (resetSelection) {
    els.playerDetailContent.innerHTML = "";
    els.playerDetailTitle.textContent = "선수 상세 정보";
  }
}

function ratioToColor(ratio) {
  const r = clamp(num(ratio, 0), 0, 1);
  const hue = Math.round(r * 120);
  return `hsl(${hue} 80% 36%)`;
}

function getConditionState({ shortStamina, longStamina, sharpness }) {
  const st = clamp(num(shortStamina, 0), 0, 1);
  const lt = clamp(num(longStamina, 0), 0, 1);
  const sharp = clamp(num(sharpness, 0), 0, 100);

  if (sharp < 55 || st < 0.60 || lt < 0.65) return "risk";
  if (sharp < 70 || st < 0.75 || lt < 0.80) return "watch";
  return "good";
}

function sharpnessGrade(score) {
  const v = clamp(num(score, 0), 0, 100);
  if (v >= 95) return { grade: "S", tone: "elite", label: "Elite" };
  if (v >= 85) return { grade: "A", tone: "hot", label: "Hot" };
  if (v >= 70) return { grade: "B", tone: "stable", label: "Stable" };
  if (v >= 55) return { grade: "C", tone: "volatile", label: "Volatile" };
  return { grade: "D", tone: "cold", label: "Cold" };
}

function renderSharpnessBadgeV2(score, opts = {}) {
  const value = Math.round(clamp(num(score, 0), 0, 100));
  const tier = sharpnessGrade(value);
  const prefix = opts.prefix || "";
  return `
    <span class="sharpness-badge-v2 is-${tier.tone}" title="${prefix}경기력 ${value}% · 등급 ${tier.grade} (${tier.label})">
      <strong>${value}</strong>
      <em>${tier.grade}</em>
      <small>${tier.label}</small>
    </span>
  `;
}

function renderConditionCell(shortStamina, longStamina, sharpness) {
  const st = clamp(num(shortStamina, 0), 0, 1);
  const lt = clamp(num(longStamina, 0), 0, 1);
  const state = getConditionState({ shortStamina: st, longStamina: lt, sharpness });
  const label = state === "risk" ? "RISK" : state === "watch" ? "WATCH" : "GOOD";
  return `
    <div class="condition-cell-v2" title="ST ${Math.round(st * 100)}% · LT ${Math.round(lt * 100)}%">
      <div class="condition-micro-row"><span>ST</span><div class="condition-micro-bar"><i style="width:${Math.round(st * 100)}%"></i></div><strong>${Math.round(st * 100)}%</strong></div>
      <div class="condition-micro-row"><span>LT</span><div class="condition-micro-bar"><i style="width:${Math.round(lt * 100)}%"></i></div><strong>${Math.round(lt * 100)}%</strong></div>
      <span class="condition-chip is-${state}">${label}</span>
    </div>
  `;
}

function renderConditionRing(longStamina, shortStamina) {
  const longPct = clamp(num(longStamina, 0), 0, 1) * 100;
  const shortPct = clamp(num(shortStamina, 0), 0, 1) * 100;
  const longColor = ratioToColor(longStamina);
  const shortColor = ratioToColor(shortStamina);
  return `<div class="condition-ring" style="--long-pct:${longPct};--short-pct:${shortPct};--long-color:${longColor};--short-color:${shortColor};" title="장기 ${Math.round(longPct)}% · 단기 ${Math.round(shortPct)}%"></div>`;
}

function renderMyTeamOverview(summary, rows) {
  const wins = num(summary?.wins, 0);
  const losses = num(summary?.losses, 0);
  const rank = summary?.rank != null ? `#${num(summary.rank, 0)}` : "#-";
  const gb = summary?.gb != null ? Number(summary.gb).toFixed(1) : "-";
  const payroll = formatMoney(summary?.payroll);
  const cap = formatMoney(summary?.cap_space);

  const roster = rows || [];
  const avgSharp = roster.length
    ? Math.round(roster.reduce((acc, r) => acc + clamp(num(r.sharpness, 0), 0, 100), 0) / roster.length)
    : 0;
  const riskCount = roster.filter((r) => {
    const st = num(r.short_term_stamina, 0);
    const lt = num(r.long_term_stamina, 0);
    const sharp = clamp(num(r.sharpness, 0), 0, 100);
    return sharp < 55 || st < 0.55 || lt < 0.6;
  }).length;

  if (els.myTeamRecord) els.myTeamRecord.textContent = `${wins}-${losses}`;
  if (els.myTeamWinPct) els.myTeamWinPct.textContent = formatWinPct(summary?.win_pct);
  if (els.myTeamRank) els.myTeamRank.textContent = rank;
  if (els.myTeamGb) els.myTeamGb.textContent = `GB ${gb}`;
  if (els.myTeamPayroll) els.myTeamPayroll.textContent = payroll;
  if (els.myTeamCapspace) els.myTeamCapspace.textContent = `CAP ${cap}`;
  if (els.myTeamAvgSharp) els.myTeamAvgSharp.textContent = `Sharp ${avgSharp}`;
  if (els.myTeamRiskCount) els.myTeamRiskCount.textContent = `주의 ${riskCount}명`;
}

function myTeamRowMetric(row, key) {
  if (key === "sharpness") return clamp(num(row.sharpness, 0), 0, 100);
  if (key === "salary") return num(row.salary, 0);
  if (key === "pts") return num(row.pts, 0);
  return num(row.ovr, 0);
}

function getMyTeamDisplayRows(rows) {
  let out = [...(rows || [])];
  if (state.myTeamFilters.risk) {
    out = out.filter((r) => {
      const st = num(r.short_term_stamina, 0);
      const lt = num(r.long_term_stamina, 0);
      const sharp = clamp(num(r.sharpness, 0), 0, 100);
      return sharp < 60 || st < 0.6 || lt < 0.65;
    });
  }
  if (state.myTeamFilters.highsalary) {
    const avgSalary = out.length ? out.reduce((acc, r) => acc + num(r.salary, 0), 0) / out.length : 0;
    const threshold = Math.max(avgSalary * 1.35, 12000000);
    out = out.filter((r) => num(r.salary, 0) >= threshold);
  }

  const sortKey = state.myTeamSortKey || "ovr";
  out.sort((a, b) => myTeamRowMetric(b, sortKey) - myTeamRowMetric(a, sortKey));
  return out;
}

function syncMyTeamControlState() {
  if (els.myTeamSortControls) {
    [...els.myTeamSortControls.querySelectorAll(".myteam-chip[data-sort]")].forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.sort === state.myTeamSortKey);
    });
  }
  if (els.myTeamFilterControls) {
    [...els.myTeamFilterControls.querySelectorAll(".myteam-chip[data-filter]")].forEach((btn) => {
      const key = btn.dataset.filter;
      btn.classList.toggle("is-active", !!state.myTeamFilters[key]);
    });
  }
}

function rerenderMyTeamBoard() {
  renderRosterRows(getMyTeamDisplayRows(state.rosterRows));
  syncMyTeamControlState();
}

function renderRosterRows(rows) {
  els.rosterBody.innerHTML = "";
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.className = "roster-row";
    tr.dataset.playerId = row.player_id;

    const shortStamina = row.short_term_stamina ?? (1 - num(row.short_term_fatigue, 0));
    const longStamina = row.long_term_stamina ?? (1 - num(row.long_term_fatigue, 0));
    const sharpness = clamp(num(row.sharpness, 50), 0, 100);
    const conditionState = getConditionState({ shortStamina, longStamina, sharpness });
    const riskClass = conditionState === "risk" ? "is-risk" : "";
    if (conditionState === "risk") tr.classList.add("is-risk-row");

    tr.innerHTML = `
      <td>
        <div class="myteam-name-cell">
          <strong>${row.name || "-"}</strong>
        </div>
      </td>
      <td>${row.pos || "-"}</td>
      <td><span class="myteam-ovr-pill">${Math.round(num(row.ovr, 0))}</span></td>
      <td>${num(row.age, 0)}</td>
      <td>${formatHeightIn(row.height_in)}</td>
      <td>${formatWeightLb(row.weight_lb)}</td>
      <td>${formatMoney(row.salary)}</td>
      <td>${num(row.pts, 0).toFixed(1)}</td>
      <td>${num(row.ast, 0).toFixed(1)}</td>
      <td>${num(row.reb, 0).toFixed(1)}</td>
      <td>${num(row.three_pm, 0).toFixed(1)}</td>
      <td class="condition-cell">${renderConditionCell(shortStamina, longStamina, sharpness)}</td>
      <td>${renderSharpnessBadgeV2(sharpness, { prefix: "로스터 " })}</td>
    `;

    tr.addEventListener("click", () => {
      state.selectedPlayerId = row.player_id;
      loadPlayerDetail(row.player_id).catch((e) => alert(e.message));
    });

    els.rosterBody.appendChild(tr);
  }
}

async function showMyTeamScreen() {
  if (!state.selectedTeamId) {
    alert("먼저 팀을 선택해주세요.");
    return;
  }

  const teamId = String(state.selectedTeamId || "").trim();
  const requestSeq = myTeamRequestSeq + 1;
  myTeamRequestSeq = requestSeq;
  const hasCached = hasTeamDetailCache(teamId);
  if (!hasCached) setLoading(true, "내 팀 로스터를 불러오는 중...");
  try {
    const detail = await fetchTeamDetail(teamId, {
      onRevalidated: (freshDetail) => {
        if (requestSeq !== myTeamRequestSeq) return;
        if (String(state.selectedTeamId || "").trim() !== teamId) return;
        if (!els.myTeamScreen?.classList.contains("active")) return;
        applyMyTeamDetail(freshDetail, { resetSelection: false });
      },
    });
    if (requestSeq !== myTeamRequestSeq) return;

    applyMyTeamDetail(detail);
    activateScreen(els.myTeamScreen);
  } finally {
    if (requestSeq === myTeamRequestSeq) setLoading(false);
  }
}

export { ratioToColor, getConditionState, sharpnessGrade, renderSharpnessBadgeV2, renderConditionCell, renderConditionRing, renderMyTeamOverview, myTeamRowMetric, getMyTeamDisplayRows, syncMyTeamControlState, rerenderMyTeamBoard, renderRosterRows, showMyTeamScreen };
