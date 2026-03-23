import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import {
  fetchJson,
  setLoading,
  fetchStateSummary,
} from "../../core/api.js";
import { num, clamp } from "../../core/guards.js";
import { formatHeightIn, formatWeightLb, formatMoney, formatPercent, seasonLabelByYear, getOptionTypeLabel } from "../../core/format.js";
import { renderSharpnessBadgeV2 } from "./myTeamScreen.js";
import { getSeasonStartYearFromSummary } from "../contracts/offerBuilder.js";
import {
  startReSignNegotiation,
  submitReSignOffer,
  acceptReSignCounter,
  commitReSign,
  resolveCurrentSeasonStartYear,
} from "../contracts/reSignNegotiation.js";

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

function getCurrentSeasonStartYear(detail, summary) {
  const fromDetail = Number(detail?.contract?.current_season_start_year || 0);
  if (Number.isFinite(fromDetail) && fromDetail > 0) return fromDetail;

  const fromSummary = getSeasonStartYearFromSummary(summary, 0);
  if (Number.isFinite(fromSummary) && fromSummary > 0) return fromSummary;

  const fromContract = Number(detail?.contract?.active?.start_season_year || 0);
  if (Number.isFinite(fromContract) && fromContract > 0) return fromContract;
  return 0;
}

function getActiveContractEndStartYear(contractActive) {
  if (!contractActive || typeof contractActive !== "object") return 0;
  const salaryByYear = contractActive.salary_by_year || {};
  const salaryYears = Object.keys(salaryByYear)
    .map((y) => Number(y))
    .filter((y) => Number.isFinite(y) && y > 0)
    .sort((a, b) => a - b);
  if (salaryYears.length) return salaryYears[salaryYears.length - 1];

  const startYear = Number(contractActive.start_season_year || 0);
  const years = Number(contractActive.years || 0);
  if (Number.isFinite(startYear) && Number.isFinite(years) && startYear > 0 && years > 0) {
    return Math.max(startYear, startYear + years - 1);
  }
  return 0;
}

function canOpenReSignNegotiation(detail, currentSeasonYear) {
  const active = detail?.contract?.active || null;
  if (!active) return false;
  const teamId = String(detail?.roster?.team_id || "").toUpperCase();
  if (!teamId || teamId === "FA") return false;
  const now = Number(currentSeasonYear || 0);
  const endYear = getActiveContractEndStartYear(active);
  if (!Number.isFinite(now) || now <= 0) return false;
  if (!Number.isFinite(endYear) || endYear <= 0) return false;
  return endYear <= now + 1;
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

function renderPlayerDetail(detail, options = {}) {
  const context = String(options?.context || "myteam").toLowerCase();
  const isMarketFa = context === "market-fa";
  const summaryPayload = options?.summaryPayload || null;
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
  const currentSeasonStartYear = getCurrentSeasonStartYear(detail, summaryPayload);
  const contractEndStartYear = getActiveContractEndStartYear(contractActive);
  const canStartReSign = canOpenReSignNegotiation(detail, currentSeasonStartYear);
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
  const marketNegotiation = (isMarketFa && state.marketNegotiation && state.marketNegotiation.player_id === p.player_id)
    ? state.marketNegotiation
    : null;
  const marketMode = String(marketNegotiation?.mode || "").toUpperCase();
  const marketPhase = String(marketNegotiation?.phase || "").toUpperCase();
  const marketStatus = String(marketNegotiation?.status || "").toUpperCase();
  const marketVerdict = String(marketNegotiation?.last_decision?.verdict || "").toUpperCase();
  const isActive = marketStatus === "ACTIVE";
  const isAccepted = marketPhase === "ACCEPTED";
  const isNegotiating = marketPhase === "INIT" || marketPhase === "NEGOTIATING";
  const canOfferFa = marketMode === "SIGN_FA" && isActive && isNegotiating;
  const canAcceptCounter = marketMode === "SIGN_FA" && isActive && isNegotiating && Boolean(marketNegotiation?.last_counter);
  const canCommitFa = marketMode === "SIGN_FA" && isActive && isAccepted;
  const canTwoWayDecision = marketMode === "SIGN_TWO_WAY" && isActive && isNegotiating;
  const canCommitTwoWay = marketMode === "SIGN_TWO_WAY" && isActive && isAccepted;
  const myTeamNegotiation = (!isMarketFa && state.myTeamReSignNegotiation && state.myTeamReSignNegotiation.player_id === p.player_id)
    ? state.myTeamReSignNegotiation
    : null;
  const myTeamMode = String(myTeamNegotiation?.mode || "").toUpperCase();
  const myTeamPhase = String(myTeamNegotiation?.phase || "").toUpperCase();
  const myTeamStatus = String(myTeamNegotiation?.status || "").toUpperCase();
  const myTeamVerdict = String(myTeamNegotiation?.last_decision?.verdict || "").toUpperCase();
  const myTeamActive = myTeamStatus === "ACTIVE";
  const myTeamNegotiating = myTeamPhase === "INIT" || myTeamPhase === "NEGOTIATING";
  const myTeamAccepted = myTeamPhase === "ACCEPTED";
  const canSubmitReSignOffer = myTeamMode === "RE_SIGN" && myTeamActive && myTeamNegotiating;
  const canAcceptReSignCounter = canSubmitReSignOffer && Boolean(myTeamNegotiation?.last_counter);
  const canCommitReSign = myTeamMode === "RE_SIGN" && myTeamActive && myTeamAccepted;
  const defaultAskAav = Math.max(750000, Math.round(Number(myTeamNegotiation?.player_position?.ask_aav || 0)));
  const defaultIdealYears = Math.max(1, Math.min(5, Number(myTeamNegotiation?.player_position?.ideal_years || 1)));

  const marketRulesHtml = isMarketFa
    ? `
      <ul class="kv-list market-rule-list">
        <li>FA 계약은 선수 수락(ACCEPTED) 이후에만 <strong>계약 확정</strong>이 활성화됩니다.</li>
        <li>샐러리캡 부족 시 FA 계약 확정이 거절될 수 있습니다.</li>
        <li>투웨이 계약은 팀당 최대 3명, 수락 시에만 계약 확정이 가능합니다.</li>
      </ul>
    `
    : "";

  const marketStatusHtml = isMarketFa
    ? `
      <ul class="compact-kv-list">
        <li><span>협상 모드</span><strong>${marketMode || "-"}</strong></li>
        <li><span>진행 상태</span><strong>${marketPhase || "-"} / ${marketStatus || "-"}</strong></li>
        <li><span>최근 응답</span><strong>${marketVerdict || "-"}</strong></li>
      </ul>
      ${marketNegotiation?.info ? `<p class="section-copy status-ok">${marketNegotiation.info}</p>` : ""}
      ${marketNegotiation?.error ? `<p class="section-copy status-danger">${marketNegotiation.error}</p>` : ""}
    `
    : "";

  const marketActionHtml = isMarketFa
    ? (() => {
      if (!marketNegotiation) {
        return `
          <div class="market-action-row">
            <button type="button" class="btn btn-primary" data-market-action="start-fa">FA 협상 시작</button>
            <button type="button" class="btn btn-secondary" data-market-action="start-two-way">투웨이 협상 시작</button>
          </div>
        `;
      }

      if (marketMode === "SIGN_FA") {
        return `
          <div class="market-action-row">
            <button type="button" class="btn btn-primary" data-market-action="offer-fa" ${canOfferFa ? "" : "disabled aria-disabled=\"true\""}>오퍼 제출</button>
            <button type="button" class="btn btn-secondary" data-market-action="accept-counter" ${canAcceptCounter ? "" : "disabled aria-disabled=\"true\""}>카운터 수락</button>
            <button type="button" class="btn btn-primary" data-market-action="commit-fa" ${canCommitFa ? "" : "disabled aria-disabled=\"true\""}>계약 확정</button>
          </div>
        `;
      }

      if (marketMode === "SIGN_TWO_WAY") {
        return `
          <div class="market-action-row">
            <button type="button" class="btn btn-secondary" data-market-action="two-way-accept" ${canTwoWayDecision ? "" : "disabled aria-disabled=\"true\""}>투웨이 수락 확인</button>
            <button type="button" class="btn btn-secondary" data-market-action="two-way-reject" ${canTwoWayDecision ? "" : "disabled aria-disabled=\"true\""}>투웨이 거절 확인</button>
            <button type="button" class="btn btn-primary" data-market-action="commit-two-way" ${canCommitTwoWay ? "" : "disabled aria-disabled=\"true\""}>계약 확정</button>
          </div>
        `;
      }

      return `
        <div class="market-action-row">
          <button type="button" class="btn btn-primary" data-market-action="start-fa">FA 협상 재시작</button>
          <button type="button" class="btn btn-secondary" data-market-action="start-two-way">투웨이 협상 재시작</button>
        </div>
      `;
    })()
    : "";

  const reSignActionHtml = !isMarketFa
    ? (() => {
      if (!myTeamNegotiation) {
        return `
          <div class="contract-track-actions">
            <button
              type="button"
              class="btn btn-primary"
              data-contract-action="start-resign"
              data-contract-end-year="${contractEndStartYear || ""}"
              data-current-season-year="${currentSeasonStartYear || ""}"
              ${canStartReSign ? "" : "disabled aria-disabled=\"true\""}
            >재계약 제의</button>
          </div>
          <p class="section-note contract-track-hint">${canStartReSign ? "재계약 협상을 시작할 수 있습니다." : "계약 만료 1년 전부터 제의 가능"}</p>
        `;
      }
      return `
        <div class="contract-track-actions">
          <button type="button" class="btn btn-primary" data-contract-action="offer-resign-auto" ${canSubmitReSignOffer ? "" : "disabled aria-disabled=\"true\""}>그대로 제안</button>
          <button type="button" class="btn btn-secondary" data-contract-action="accept-resign-counter" ${canAcceptReSignCounter ? "" : "disabled aria-disabled=\"true\""}>카운터 수락</button>
          <button type="button" class="btn btn-primary" data-contract-action="commit-resign" ${canCommitReSign ? "" : "disabled aria-disabled=\"true\""}>재계약 확정</button>
        </div>
        <div class="contract-track-actions">
          <label class="section-note">AAV
            <input type="number" data-contract-input="aav" min="750000" step="50000" value="${defaultAskAav}">
          </label>
          <label class="section-note">연차
            <input type="number" data-contract-input="years" min="1" max="5" step="1" value="${defaultIdealYears}">
          </label>
          <button type="button" class="btn btn-secondary" data-contract-action="offer-resign-custom" ${canSubmitReSignOffer ? "" : "disabled aria-disabled=\"true\""}>조정 제안</button>
        </div>
      `;
    })()
    : "";

  const reSignStatusHtml = !isMarketFa
    ? `
      <ul class="compact-kv-list">
        <li><span>재계약 협상</span><strong>${myTeamMode || "-"}</strong></li>
        <li><span>진행 상태</span><strong>${myTeamPhase || "-"} / ${myTeamStatus || "-"}</strong></li>
        <li><span>최근 응답</span><strong>${myTeamVerdict || "-"}</strong></li>
        <li><span>희망 연봉(AAV)</span><strong>${myTeamNegotiation?.player_position?.ask_aav ? formatMoney(myTeamNegotiation.player_position.ask_aav) : "-"}</strong></li>
        <li><span>희망 계약 기간</span><strong>${myTeamNegotiation?.player_position?.ideal_years ? `${num(myTeamNegotiation.player_position.ideal_years, 0)}년` : "-"}</strong></li>
      </ul>
      ${myTeamNegotiation?.info ? `<p class="section-copy status-ok">${myTeamNegotiation.info}</p>` : ""}
      ${myTeamNegotiation?.error ? `<p class="section-copy status-danger">${myTeamNegotiation.error}</p>` : ""}
    `
    : "";

  els.playerDetailTitle.textContent = `${playerName} 상세 정보`;
  els.playerDetailContent.innerHTML = `
    <div class="player-layout player-layout-v2">
      <section class="detail-card detail-card-header detail-card-hero">
        <div class="detail-head detail-head-main">
          <div>
            <p class="detail-eyebrow">FRANCHISE PLAYER CARD</p>
            <h3>${playerName}</h3>
            <p class="detail-subline">${p.pos || "-"} · ${num(p.age, 0)}세 · ${formatHeightIn(p.height_in)} / ${formatWeightLb(p.weight_lb)}</p>
            <p class="hero-summary">${injury.is_injured ? "건강 관리 필요" : "출전 가능"} · ${isMarketFa ? "FA 시장 모드" : `Sharp ${Math.round(sharp)}`} · ${detail.dissatisfaction?.is_dissatisfied ? "불만 관리 필요" : "불만 낮음"}</p>
          </div>
          <div class="hero-kpi-stack">
            <span class="ovr-medal">OVR ${ovr}</span>
            ${isMarketFa ? "" : renderSharpnessBadgeV2(sharp)}
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
        ${reSignStatusHtml}
        ${reSignActionHtml}
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

      ${isMarketFa ? `
      <section class="detail-card detail-card-health">
        <h4>시장 액션</h4>
        ${marketStatusHtml}
        ${marketRulesHtml}
        ${marketActionHtml}
      </section>
      ` : `
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
      `}

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

async function loadPlayerDetail(playerId, options = {}) {
  const backTarget = String(options?.backTarget || "myteam").toLowerCase();
  state.playerDetailBackTarget = backTarget === "market" ? "market" : "myteam";
  setLoading(true, "선수 상세 정보를 불러오는 중...");
  try {
    const [detail, summaryPayload] = await Promise.all([
      fetchJson(`/api/player-detail/${encodeURIComponent(playerId)}`),
      fetchStateSummary().catch(() => null),
    ]);
    renderPlayerDetail(detail, {
      ...options,
      summaryPayload,
    });
    activateScreen(els.playerDetailScreen);
  } finally {
    setLoading(false);
  }
}

async function handleMyTeamContractAction(action) {
  const act = String(action || "").trim().toLowerCase();
  const playerId = String(state.selectedPlayerId || "").trim();
  if (!playerId) throw new Error("선수를 먼저 선택해주세요.");
  if (!state.selectedTeamId) throw new Error("팀을 먼저 선택해주세요.");

  const current = state.myTeamReSignNegotiation || {};

  const refresh = async () => {
    await loadPlayerDetail(playerId, { backTarget: "myteam" });
  };

  try {
    setLoading(true, "재계약 협상 진행 중...");

    if (act === "start-resign") {
      const endYear = Number(els.playerDetailContent?.querySelector('[data-contract-action=\"start-resign\"]')?.dataset?.contractEndYear || 0);
      const seasonYear = Number(els.playerDetailContent?.querySelector('[data-contract-action=\"start-resign\"]')?.dataset?.currentSeasonYear || 0);
      state.myTeamReSignNegotiation = await startReSignNegotiation(playerId, {
        teamId: state.selectedTeamId,
        contractEndStartYear: endYear,
        currentSeasonStartYear: seasonYear,
      });
    } else if (act === "offer-resign-auto" || act === "offer-resign-custom") {
      if (!current?.session_id) throw new Error("진행 중인 재계약 협상이 없습니다.");
      const currentSeasonYear = await resolveCurrentSeasonStartYear();
      const askAav = Number(current?.player_position?.ask_aav || 0);
      const idealYears = Number(current?.player_position?.ideal_years || 1);

      const inputAav = Number(els.playerDetailContent?.querySelector('input[data-contract-input=\"aav\"]')?.value || askAav || 0);
      const inputYears = Number(els.playerDetailContent?.querySelector('input[data-contract-input=\"years\"]')?.value || idealYears || 1);
      const selectedAav = act === "offer-resign-auto" ? askAav : inputAav;
      const selectedYears = act === "offer-resign-auto" ? idealYears : inputYears;
      const result = await submitReSignOffer({
        session: current,
        seasonYear: currentSeasonYear,
        aav: selectedAav,
        years: selectedYears,
        playerId,
      });
      state.myTeamReSignNegotiation = {
        ...result.session,
        info: act === "offer-resign-auto" ? "선수 희망 조건으로 제안했습니다." : "조정된 조건으로 제안했습니다.",
      };
    } else if (act === "accept-resign-counter") {
      if (!current?.session_id) throw new Error("진행 중인 재계약 협상이 없습니다.");
      state.myTeamReSignNegotiation = await acceptReSignCounter(current.session_id, {
        playerId,
      });
    } else if (act === "commit-resign") {
      if (!current?.session_id) throw new Error("확정 가능한 재계약 협상이 없습니다.");
      state.myTeamReSignNegotiation = await commitReSign(current, {
        teamId: state.selectedTeamId,
        playerId,
      });
    } else {
      return;
    }
  } catch (e) {
    state.myTeamReSignNegotiation = {
      ...(state.myTeamReSignNegotiation || {}),
      error: e?.message || "재계약 협상 처리에 실패했습니다.",
      info: null,
    };
    throw e;
  } finally {
    setLoading(false);
    await refresh();
  }
}

export {
  getDissatisfactionSummary,
  renderAttrGrid,
  buildContractRows,
  getCurrentSeasonStartYear,
  getActiveContractEndStartYear,
  canOpenReSignNegotiation,
  attrCategoryKey,
  buildAttrIntelligence,
  renderPlayerDetail,
  loadPlayerDetail,
  handleMyTeamContractAction,
};
