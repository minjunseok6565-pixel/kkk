import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import { fetchJson, setLoading } from "../../core/api.js";
import { num, clamp } from "../../core/guards.js";
import { formatHeightIn, formatWeightLb, formatMoney, formatPercent, seasonLabelByYear, getOptionTypeLabel } from "../../core/format.js";
import { renderSharpnessBadgeV2 } from "./myTeamScreen.js";

function getDissatisfactionSummary(d) {
  if (!d || !d.is_dissatisfied) return { text: "불만: 없음", details: [] };
  const st = d.state || {};
  const axes = [
    ["팀", num(st.team_frustration, 0)],
    ["역할", num(st.role_frustration, 0)],
    ["계약", num(st.contract_frustration, 0)],
    ["건강", num(st.health_frustration, 0)],
    ["케미", num(st.chemistry_frustration, 0)],
    ["사용률", num(st.usage_frustration, 0)],
  ].sort((a, b) => b[1] - a[1]);

  const top = axes.filter(([, v]) => v > 0.1).slice(0, 3).map(([k, v]) => `${k} ${Math.round(v * 100)}%`);
  const level = clamp(num(st.trade_request_level, 0), 0, 10);
  return {
    text: `불만: 있음 (강도 ${Math.round(axes[0][1] * 100)}%, TR ${level})`,
    details: top,
  };
}

function renderAttrGrid(attrs) {
  const entries = Object.entries(attrs || {}).sort((a, b) => String(a[0]).localeCompare(String(b[0])));
  if (!entries.length) return '<p class="empty-copy">능력치 데이터가 없습니다.</p>';
  return entries
    .map(([k, v]) => {
      const value = typeof v === "number" ? (Math.abs(v) <= 1 ? `${Math.round(v * 100)}` : `${Math.round(v)}`) : String(v);
      return `
        <div class="attr-card">
          <span class="attr-name">${k}</span>
          <strong class="attr-value">${value}</strong>
        </div>
      `;
    })
    .join("");
}

function buildContractRows(contractActive, fallbackSalary) {
  if (!contractActive) {
    return [{ label: "계약", value: "활성 계약 정보 없음", emphasis: true }];
  }

  const salaryByYear = contractActive.salary_by_year || {};
  const salaryYears = Object.keys(salaryByYear)
    .map((y) => Number(y))
    .filter((y) => Number.isFinite(y))
    .sort((a, b) => a - b);

  const optionByYear = new Map((contractActive.options || []).map((opt) => [Number(opt.season_year), opt]));
  const rows = [];

  const initialSalary = salaryYears.length ? salaryByYear[salaryYears[0]] : fallbackSalary;
  rows.push({ label: "샐러리", value: formatMoney(initialSalary), emphasis: true });

  salaryYears.forEach((year, idx) => {
    if (idx === 0) return;
    const option = optionByYear.get(year);
    const optionText = option ? ` (${getOptionTypeLabel(option.type)})` : "";
    rows.push({
      label: seasonLabelByYear(year),
      value: `${formatMoney(salaryByYear[year])}${optionText}`,
      emphasis: false,
    });
  });

  const outstandingOptionRows = (contractActive.options || [])
    .map((option) => ({
      year: Number(option.season_year),
      option,
    }))
    .filter(({ year }) => Number.isFinite(year) && !(year in salaryByYear))
    .sort((a, b) => a.year - b.year)
    .map(({ year, option }) => ({
      label: seasonLabelByYear(year),
      value: `${getOptionTypeLabel(option.type)} (${option.status || "PENDING"})`,
      emphasis: false,
    }));

  return rows.concat(outstandingOptionRows);
}

function attrCategoryKey(name) {
  const k = String(name || "").toLowerCase();
  if (["shot", "shoot", "free_throw", "layup", "inside", "outside", "close"].some((x) => k.includes(x))) return "Shooting";
  if (["pass", "handle", "play", "iq", "vision"].some((x) => k.includes(x))) return "Playmaking";
  if (["def", "rebound", "block", "steal", "hustle"].some((x) => k.includes(x))) return "Defense";
  if (["agility", "athletic", "durability", "injury", "strength", "speed"].some((x) => k.includes(x))) return "Physical";
  return "Mental";
}

function buildAttrIntelligence(attrs) {
  const entries = Object.entries(attrs || {}).map(([k, v]) => ({
    key: k,
    value: Math.abs(num(v, 0)) <= 1 ? num(v, 0) * 100 : num(v, 0),
  }));

  if (!entries.length) {
    return {
      categoryHtml: '<p class="empty-copy">능력치 데이터가 없습니다.</p>',
      strengthsHtml: '<p class="empty-copy">데이터 없음</p>',
      weaknessesHtml: '<p class="empty-copy">데이터 없음</p>',
    };
  }

  const grouped = { Shooting: [], Playmaking: [], Defense: [], Physical: [], Mental: [] };
  entries.forEach((it) => grouped[attrCategoryKey(it.key)].push(it.value));

  const categoryHtml = Object.entries(grouped)
    .map(([name, vals]) => {
      const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
      return `<div class="attr-intel-row"><span>${name}</span><div class="attr-meter"><i style="width:${clamp(avg, 0, 100)}%"></i></div><strong>${Math.round(avg)}</strong></div>`;
    })
    .join("");

  const sorted = [...entries].sort((a, b) => b.value - a.value);
  const isHiddenNeedsAttentionKey = (key) => {
    const k = String(key || "").trim();
    const lower = k.toLowerCase();
    return lower === "potential" || lower === "i_injuryfreq" || k.startsWith("M_");
  };
  const attentionPool = sorted.filter((item) => !isHiddenNeedsAttentionKey(item.key));
  const strengthsHtml = `<ul class="intel-list">${sorted.slice(0, 5).map((x) => `<li><span>${x.key}</span><strong>${Math.round(x.value)}</strong></li>`).join("")}</ul>`;
  const weaknessItems = attentionPool.slice(-3).reverse();
  const weaknessesHtml = weaknessItems.length
    ? `<ul class="intel-list">${weaknessItems.map((x) => `<li><span>${x.key}</span><strong>${Math.round(x.value)}</strong></li>`).join("")}</ul>`
    : '<p class="empty-copy">노출 가능한 약점 데이터가 없습니다.</p>';

  return { categoryHtml, strengthsHtml, weaknessesHtml };
}

function renderPlayerDetail(detail) {
  const p = detail.player || {};
  const contract = detail.contract || {};
  const diss = getDissatisfactionSummary(detail.dissatisfaction);
  const injury = detail.injury || {};
  const condition = detail.condition || {};
  const seasonStats = detail.season_stats || {};
  const totals = seasonStats.totals || {};
  const twoWay = detail.two_way || {};
  const contractActive = contract.active || null;
  const contractRows = buildContractRows(contractActive, detail.roster?.salary_amount);
  const dissatisfactionDescription = (detail.dissatisfaction?.reasons || []).length
    ? detail.dissatisfaction.reasons
    : diss.details;

  const injuryState = injury.state || {};
  const injuryDetails = [
    injuryState.injury_type && `부상 유형: ${injuryState.injury_type}`,
    injuryState.body_part && `부위: ${injuryState.body_part}`,
    injuryState.games_remaining != null && `복귀 예상: ${num(injuryState.games_remaining, 0)}경기 후`,
    injuryState.note && `메모: ${injuryState.note}`,
  ].filter(Boolean);

  const totalsEntries = Object.entries(totals || {});
  const highlightStats = [
    ["PTS", num(totals.PTS, 0)],
    ["AST", num(totals.AST, 0)],
    ["REB", num(totals.REB, 0)],
    ["3PM", num(totals["3PM"], 0)],
  ];

  const statsSummary = totalsEntries.length
    ? `<div class="stats-grid">${totalsEntries
      .sort((a, b) => String(a[0]).localeCompare(String(b[0])))
      .map(([k, v]) => `<div class="stat-chip"><span>${k}</span><strong>${typeof v === "number" ? (Math.round(v * 100) / 100) : v}</strong></div>`)
      .join("")}</div>`
    : '<p class="empty-copy">누적 스탯 데이터가 없습니다.</p>';

  const healthText = injury.is_injured
    ? `${injury.status || "부상"} · ${(injury.state?.injury_type || "")}`
    : "건강함";

  const playerName = p.name || "선수";
  const ovr = Math.round(num(p.ovr, 0));
  const sharp = clamp(num(condition.sharpness, 50), 0, 100);
  const { categoryHtml, strengthsHtml, weaknessesHtml } = buildAttrIntelligence(p.attrs || {});

  els.playerDetailTitle.textContent = `${playerName} 상세 정보`;
  els.playerDetailContent.innerHTML = `
    <div class="player-layout player-layout-v2">
      <section class="detail-card detail-card-header detail-card-hero">
        <div class="detail-head detail-head-main">
          <div>
            <p class="detail-eyebrow">FRANCHISE PLAYER CARD</p>
            <h3>${playerName}</h3>
            <p class="detail-subline">${p.pos || "-"} · ${num(p.age, 0)}세 · ${formatHeightIn(p.height_in)} / ${formatWeightLb(p.weight_lb)}</p>
            <p class="hero-summary">${injury.is_injured ? "건강 관리 필요" : "출전 가능"} · Sharp ${Math.round(sharp)} · ${detail.dissatisfaction?.is_dissatisfied ? "불만 관리 필요" : "불만 낮음"}</p>
          </div>
          <div class="hero-kpi-stack">
            <span class="ovr-medal">OVR ${ovr}</span>
            ${renderSharpnessBadgeV2(sharp)}
            <span class="status-line ${injury.is_injured ? "status-danger" : "status-ok"}">${injury.is_injured ? "Injured" : "Available"}</span>
          </div>
        </div>
      </section>

      <section class="detail-card detail-card-contract">
        <h4>계약 트랙</h4>
        <ul class="compact-kv-list">
          ${contractRows.map((row) => `<li><span>${row.label}</span><strong${row.emphasis ? ' class="text-accent"' : ""}>${row.value}</strong></li>`).join("")}
        </ul>
        ${twoWay.is_two_way ? `<p class="section-note">투웨이 계약 · 남은 경기 ${num(twoWay.games_remaining, 0)} / ${num(twoWay.game_limit, 0)}</p>` : ""}
      </section>

      <section class="detail-card detail-card-dissatisfaction">
        <h4>만족도 리스크</h4>
        <p class="status-line ${detail.dissatisfaction?.is_dissatisfied ? "status-danger" : "status-ok"}">${detail.dissatisfaction?.is_dissatisfied ? "불만 있음" : "불만 없음"}</p>
        <p class="section-copy">${diss.text}</p>
        ${dissatisfactionDescription.length ? `<ul class="kv-list">${dissatisfactionDescription.map((x) => `<li>${x}</li>`).join("")}</ul>` : ""}
      </section>

      <section class="detail-card detail-card-attr">
        <h4>능력치 인텔리전스</h4>
        <div class="attr-intel-grid">${categoryHtml}</div>
        <div class="attr-intel-columns">
          <div><p class="detail-eyebrow">TOP STRENGTHS</p>${strengthsHtml}</div>
          <div><p class="detail-eyebrow">NEEDS ATTENTION</p>${weaknessesHtml}</div>
        </div>
      </section>

      <section class="detail-card detail-card-health">
        <h4>건강 상태</h4>
        <ul class="compact-kv-list compact-kv-list-health">
          <li><span>장기 체력</span><strong>${formatPercent(condition.long_term_stamina)}</strong></li>
          <li><span>단기 체력</span><strong>${formatPercent(condition.short_term_stamina)}</strong></li>
          <li><span>부상 여부</span><strong>${injury.is_injured ? "부상" : "정상"}</strong></li>
        </ul>
        <p class="section-copy">${healthText}</p>
        ${injuryDetails.length ? `<ul class="kv-list">${injuryDetails.map((item) => `<li>${item}</li>`).join("")}</ul>` : ""}
      </section>

      <section class="detail-card detail-card-stats">
        <h4>시즌 퍼포먼스</h4>
        <div class="hero-stat-grid">
          ${highlightStats.map(([k, v]) => `<article class="hero-stat"><p>${k}</p><strong>${Math.round(v * 10) / 10}</strong></article>`).join("")}
        </div>
        <p class="section-copy">출전 경기 수: ${num(seasonStats.games, 0)}경기</p>
        ${statsSummary}
      </section>
    </div>
  `;
}

async function loadPlayerDetail(playerId) {
  setLoading(true, "선수 상세 정보를 불러오는 중...");
  try {
    const detail = await fetchJson(`/api/player-detail/${encodeURIComponent(playerId)}`);
    renderPlayerDetail(detail);
    activateScreen(els.playerDetailScreen);
  } finally {
    setLoading(false);
  }
}

export { getDissatisfactionSummary, renderAttrGrid, buildContractRows, attrCategoryKey, buildAttrIntelligence, renderPlayerDetail, loadPlayerDetail };
