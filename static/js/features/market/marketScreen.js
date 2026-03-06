import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import { fetchJson, setLoading } from "../../core/api.js";
import { num } from "../../core/guards.js";
import { formatHeightIn, formatMoney, formatWeightLb } from "../../core/format.js";
import { TEAM_FULL_NAMES } from "../../core/constants/teams.js";
import { loadPlayerDetail } from "../myteam/playerDetail.js";

function toFriendlyRuleMessage(rawMessage) {
  const msg = String(rawMessage || "");
  if (!msg) return "협상 처리 중 오류가 발생했습니다.";
  if (msg.includes("CAP_NO_SPACE_FOR_FA_SIGNING") || msg.includes("Insufficient cap space")) {
    return "샐러리캡이 부족하여 FA 계약을 확정할 수 없습니다.";
  }
  if (msg.includes("max two-way players (3)") || msg.includes("Two-way slots full")) {
    return "투웨이 슬롯이 가득 찼습니다. (최대 3명)";
  }
  if (msg.includes("not a free agent") || msg.includes("Player is not a free agent")) {
    return "해당 선수는 현재 FA 신분이 아닙니다.";
  }
  if (msg.includes("NEGOTIATION_NOT_ACCEPTED")) {
    return "선수가 제안을 수락한 뒤에만 계약 확정이 가능합니다.";
  }
  if (msg.includes("NEGOTIATION_MODE_MISMATCH")) {
    return "협상 모드가 맞지 않습니다. 협상을 다시 시작해주세요.";
  }
  if (msg.includes("NEGOTIATION_NOT_ACTIVE") || msg.includes("Negotiation session is closed")) {
    return "협상이 이미 종료되었습니다. 새 협상을 시작해주세요.";
  }
  return msg;
}

function getNegotiationUiFlags(negotiation) {
  const mode = String(negotiation?.mode || "").toUpperCase();
  const phase = String(negotiation?.phase || "").toUpperCase();
  const status = String(negotiation?.status || "").toUpperCase();
  const active = status === "ACTIVE";
  const accepted = phase === "ACCEPTED";
  const negotiating = phase === "INIT" || phase === "NEGOTIATING";

  return {
    mode,
    phase,
    status,
    active,
    accepted,
    negotiating,
    canOfferFa: mode === "SIGN_FA" && active && negotiating,
    canAcceptCounter: mode === "SIGN_FA" && active && negotiating && Boolean(negotiation?.last_counter),
    canCommitFa: mode === "SIGN_FA" && active && accepted,
    canTwoWayDecision: mode === "SIGN_TWO_WAY" && active && negotiating,
    canCommitTwoWay: mode === "SIGN_TWO_WAY" && active && accepted,
  };
}

function switchMarketSubTab(tab) {
  const next = tab === "trade-block" ? "trade-block" : "fa";
  state.marketSubTab = next;

  const mapping = {
    fa: [els.marketSubtabFa, els.marketPanelFa],
    "trade-block": [els.marketSubtabTradeBlock, els.marketPanelTradeBlock],
  };

  Object.entries(mapping).forEach(([key, [btn, panel]]) => {
    const active = key === next;
    btn?.classList.toggle("is-active", active);
    btn?.setAttribute("aria-selected", active ? "true" : "false");
    panel?.classList.toggle("active", active);
    panel?.setAttribute("aria-hidden", active ? "false" : "true");
  });
}

function getFaDisplayRows(rows) {
  return [...(rows || [])].sort((a, b) => num(b?.overall, 0) - num(a?.overall, 0));
}

function getFirstNumber(...values) {
  for (const value of values) {
    const n = Number(value);
    if (Number.isFinite(n)) return n;
  }
  return 0;
}

function renderFaSummary(rows) {
  if (!els.marketFaSummary) return;
  const count = rows.length;
  const avgOvr = count ? Math.round(rows.reduce((acc, row) => acc + num(row?.overall, 0), 0) / count) : 0;
  els.marketFaSummary.textContent = `총 ${count}명 · 평균 OVR ${avgOvr}`;
}

function renderFaRows(rows) {
  if (!els.marketFaBody) return;
  els.marketFaBody.innerHTML = "";

  if (!rows.length) {
    els.marketFaBody.innerHTML = '<tr><td class="schedule-empty" colspan="11">FA 선수가 없습니다.</td></tr>';
    return;
  }

  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.className = "roster-row";
    tr.dataset.playerId = row.player_id;
    tr.innerHTML = `
      <td><div class="myteam-name-cell"><strong>${row.name || "-"}</strong></div></td>
      <td>${row.pos || "-"}</td>
      <td><span class="myteam-ovr-pill">${Math.round(num(row.overall, 0))}</span></td>
      <td>${num(row.age, 0)}</td>
      <td>${formatHeightIn(row.height_in)}</td>
      <td>${formatWeightLb(row.weight_lb)}</td>
      <td>${formatMoney(row.salary ?? 0)}</td>
      <td>${num(row.pts, 0).toFixed(1)}</td>
      <td>${num(row.ast, 0).toFixed(1)}</td>
      <td>${num(row.reb, 0).toFixed(1)}</td>
      <td>${num(row.three_pm, 0).toFixed(1)}</td>
    `;

    tr.addEventListener("click", () => {
      state.marketSelectedPlayerId = row.player_id;
      if (state.marketNegotiation && state.marketNegotiation.player_id !== row.player_id) {
        state.marketNegotiation = null;
      }
      loadPlayerDetail(row.player_id, {
        context: "market-fa",
        backTarget: "market",
      }).catch((e) => alert(e.message));
    });

    els.marketFaBody.appendChild(tr);
  });
}

function getTradeBlockDisplayRows(rows) {
  return [...(rows || [])].sort((a, b) => {
    const pA = num(a?.listing?.priority, 0);
    const pB = num(b?.listing?.priority, 0);
    if (pA !== pB) return pB - pA;
    return num(b?.overall, 0) - num(a?.overall, 0);
  });
}

function renderTradeBlockSummary(rows) {
  if (!els.marketTradeBlockSummary) return;
  const count = rows.length;
  const teamCount = new Set(rows.map((row) => String(row?.team_id || "").toUpperCase()).filter(Boolean)).size;
  els.marketTradeBlockSummary.textContent = `총 ${count}명 · ${teamCount}개 팀`;
}

function closeTradeNegotiationModal() {
  if (!els.marketTradeModal) return;
  els.marketTradeModal.classList.add("hidden");
  document.body.classList.remove("is-modal-open");
}

function openTradeNegotiationModal(row) {
  if (!els.marketTradeModal) return;
  const teamId = String(row?.team_id || "").toUpperCase();
  const teamName = TEAM_FULL_NAMES[teamId] || teamId || "상대 팀";
  state.marketTradeModalPlayerId = row?.player_id || null;
  state.marketTradeModalOtherTeamId = teamId || null;

  if (els.marketTradeModalTitle) {
    els.marketTradeModalTitle.textContent = `${row?.name || "선수"} 트레이드 협상`;
  }
  if (els.marketTradeModalBody) {
    els.marketTradeModalBody.textContent = `${teamName} (${teamId})과(와) ${row?.name || "선수"} 관련 트레이드 협상을 시작합니다.`;
  }

  els.marketTradeModal.classList.remove("hidden");
  document.body.classList.add("is-modal-open");
}

async function startTradeNegotiationFromModal() {
  const playerId = state.marketTradeModalPlayerId;
  const otherTeamId = state.marketTradeModalOtherTeamId;
  if (!state.selectedTeamId) throw new Error("먼저 팀을 선택해주세요.");
  if (!playerId || !otherTeamId) throw new Error("협상을 시작할 선수 정보를 찾을 수 없습니다.");

  const out = await fetchJson("/api/trade/negotiation/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_team_id: state.selectedTeamId,
      other_team_id: otherTeamId,
      default_offer_privacy: "PRIVATE",
    }),
  });

  state.marketTradeNegotiationSession = out?.session || null;
  const sessionId = state.marketTradeNegotiationSession?.session_id || "-";
  alert(`트레이드 협상이 시작되었습니다.\n세션 ID: ${sessionId}\n(다음 단계: 제안 패키지 작성 UI)`);
  closeTradeNegotiationModal();
}

function renderTradeBlockRows(rows) {
  if (!els.marketTradeBlockBody) return;
  els.marketTradeBlockBody.innerHTML = "";

  if (!rows.length) {
    els.marketTradeBlockBody.innerHTML = '<tr><td class="schedule-empty" colspan="13">트레이드 블록 등록 선수가 없습니다.</td></tr>';
    return;
  }

  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.className = "roster-row";
    tr.dataset.playerId = row.player_id;
    const teamId = String(row?.team_id || "").toUpperCase();
    const teamName = TEAM_FULL_NAMES[teamId] || teamId || "-";
    tr.innerHTML = `
      <td>${teamName}</td>
      <td><div class="myteam-name-cell"><strong>${row.name || "-"}</strong></div></td>
      <td>${row.pos || "-"}</td>
      <td><span class="myteam-ovr-pill">${Math.round(num(row.overall, 0))}</span></td>
      <td>${num(row.age, 0)}</td>
      <td>${formatHeightIn(row.height_in)}</td>
      <td>${formatWeightLb(row.weight_lb)}</td>
      <td>${formatMoney(row.salary ?? 0)}</td>
      <td>${num(row.pts, 0).toFixed(1)}</td>
      <td>${num(row.ast, 0).toFixed(1)}</td>
      <td>${num(row.reb, 0).toFixed(1)}</td>
      <td>${num(row.three_pm, 0).toFixed(1)}</td>
      <td><button type="button" class="btn btn-primary market-trade-offer-btn" data-market-trade-offer="${row.player_id}">트레이드 제안</button></td>
    `;

    tr.addEventListener("click", (ev) => {
      if (ev.target instanceof HTMLElement && ev.target.closest("[data-market-trade-offer]")) return;
      state.marketSelectedPlayerId = row.player_id;
      loadPlayerDetail(row.player_id, {
        context: "market-trade-block",
        backTarget: "market",
      }).catch((e) => alert(e.message));
    });

    const offerBtn = tr.querySelector("[data-market-trade-offer]");
    offerBtn?.addEventListener("click", (ev) => {
      ev.stopPropagation();
      openTradeNegotiationModal(row);
    });

    els.marketTradeBlockBody.appendChild(tr);
  });
}

async function loadTradeBlockList() {
  const payload = await fetchJson("/api/trade/block?active_only=true&visibility=PUBLIC&limit=300&sort=priority_desc");
  const rows = (payload?.rows || []).map((row) => ({
    ...row,
    height_in: getFirstNumber(row?.height_in),
    weight_lb: getFirstNumber(row?.weight_lb),
    salary: getFirstNumber(row?.salary),
    pts: getFirstNumber(row?.pts),
    ast: getFirstNumber(row?.ast),
    reb: getFirstNumber(row?.reb),
    three_pm: getFirstNumber(row?.three_pm),
  }));
  state.marketTradeBlockRows = rows;
  const displayRows = getTradeBlockDisplayRows(rows);
  renderTradeBlockSummary(displayRows);
  renderTradeBlockRows(displayRows);
}

async function loadFaList() {
  const payload = await fetchJson("/api/contracts/free-agents?limit=300");
  const rows = (payload?.players || []).map((row) => ({
    ...row,
    height_in: getFirstNumber(
      row?.height_in,
      row?.attrs?.height_in,
      row?.attrs?.physical?.height_in,
      row?.attrs?.bio?.height_in,
    ),
    weight_lb: getFirstNumber(
      row?.weight_lb,
      row?.attrs?.weight_lb,
      row?.attrs?.physical?.weight_lb,
      row?.attrs?.bio?.weight_lb,
    ),
    salary: getFirstNumber(
      row?.salary,
      row?.attrs?.salary_amount,
      row?.attrs?.contract?.salary,
      row?.attrs?.contract?.salary_amount,
    ),
    pts: getFirstNumber(row?.pts, row?.attrs?.pts, row?.attrs?.stats?.pts),
    ast: getFirstNumber(row?.ast, row?.attrs?.ast, row?.attrs?.stats?.ast),
    reb: getFirstNumber(row?.reb, row?.attrs?.reb, row?.attrs?.stats?.reb),
    three_pm: getFirstNumber(
      row?.three_pm,
      row?.attrs?.three_pm,
      row?.attrs?.stats?.three_pm,
      row?.attrs?.stats?.fg3m,
    ),
  }));
  state.marketFaRows = rows;
  const displayRows = getFaDisplayRows(rows);
  renderFaSummary(displayRows);
  renderFaRows(displayRows);
}

async function openMarketSubTab(tab) {
  switchMarketSubTab(tab);
  if (state.marketSubTab !== "fa" && state.marketSubTab !== "trade-block") return;
  setLoading(true, state.marketSubTab === "fa" ? "FA 명단을 불러오는 중..." : "트레이드 블록 명단을 불러오는 중...");
  try {
    if (state.marketSubTab === "fa") await loadFaList();
    else await loadTradeBlockList();
  } finally {
    setLoading(false);
  }
}

function normalizeNegotiationStateFromSession(session, extra = {}) {
  const s = session || {};
  return {
    ...(state.marketNegotiation || {}),
    ...extra,
    player_id: extra.player_id || s.player_id || state.marketSelectedPlayerId,
    mode: s.mode || extra.mode || null,
    session_id: s.session_id || extra.session_id || null,
    status: s.status || extra.status || null,
    phase: s.phase || extra.phase || null,
    round: Number(s.round || extra.round || 0),
    max_rounds: Number(s.max_rounds || extra.max_rounds || 0),
    valid_until: s.valid_until || extra.valid_until || null,
    last_decision: s.last_decision || extra.last_decision || null,
    last_counter: s.last_counter || extra.last_counter || null,
    agreed_offer: s.agreed_offer || extra.agreed_offer || null,
    player_position: s.player_position || extra.player_position || null,
    info: extra.info || null,
    error: extra.error || null,
  };
}

async function refreshSelectedMarketPlayerDetail() {
  if (!state.marketSelectedPlayerId) return;
  await loadPlayerDetail(state.marketSelectedPlayerId, {
    context: "market-fa",
    backTarget: "market",
  });
}

function getSeasonYearFromSummary(summaryPayload) {
  const ws = summaryPayload?.workflow_state || {};
  const league = ws?.league || {};
  const activeSeasonId = String(ws?.active_season_id || "");
  const seasonIdYear = Number((activeSeasonId.match(/^(\d{4})-/) || [])[1] || 0);
  const direct = Number(league?.season_year || ws?.season_year || seasonIdYear || 0);
  if (Number.isFinite(direct) && direct > 0) return direct;
  return new Date().getFullYear();
}

function buildAutoFaOfferFromSession(session, seasonYear) {
  const pos = session?.player_position || {};
  const years = Math.max(1, Math.min(5, Number(pos.ideal_years || 2)));
  const aav = Math.max(750000, Math.round(Number(pos.ask_aav || 1000000)));
  const salary_by_year = {};
  for (let i = 0; i < years; i += 1) salary_by_year[seasonYear + i] = aav;
  return {
    start_season_year: seasonYear,
    years,
    salary_by_year,
    options: [],
  };
}

async function startFaNegotiation(playerId) {
  if (!state.selectedTeamId) throw new Error("먼저 팀을 선택해주세요.");
  const out = await fetchJson("/api/contracts/negotiation/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      team_id: state.selectedTeamId,
      player_id: playerId,
      mode: "SIGN_FA",
    }),
  });
  state.marketNegotiation = normalizeNegotiationStateFromSession(out, {
    player_id: playerId,
    info: "FA 협상이 시작되었습니다.",
    error: null,
  });
  return out;
}

async function submitFaOfferAuto() {
  const current = state.marketNegotiation || {};
  if (!current.session_id) throw new Error("먼저 FA 협상을 시작해주세요.");
  const summary = await fetchJson("/api/state/summary");
  const seasonYear = getSeasonYearFromSummary(summary);
  const offerPayload = buildAutoFaOfferFromSession(current, seasonYear);

  const out = await fetchJson("/api/contracts/negotiation/offer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: current.session_id,
      offer: offerPayload,
    }),
  });

  state.marketNegotiation = normalizeNegotiationStateFromSession(out?.session, {
    player_id: current.player_id,
    last_decision: out?.decision || null,
    info: "선수에게 제안을 전달했습니다.",
    error: null,
  });
  return out;
}

async function acceptFaCounter() {
  const current = state.marketNegotiation || {};
  if (!current.session_id) throw new Error("진행 중인 협상이 없습니다.");
  const out = await fetchJson("/api/contracts/negotiation/accept-counter", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: current.session_id }),
  });
  state.marketNegotiation = normalizeNegotiationStateFromSession(out?.session, {
    player_id: current.player_id,
    info: "선수의 카운터 오퍼를 수락했습니다.",
    error: null,
  });
  return out;
}

async function commitFaSigning() {
  const current = state.marketNegotiation || {};
  if (!current.session_id || !current.player_id) throw new Error("확정 가능한 협상이 없습니다.");
  const out = await fetchJson("/api/contracts/sign-free-agent", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: current.session_id,
      team_id: state.selectedTeamId,
      player_id: current.player_id,
    }),
  });

  state.marketNegotiation = {
    ...current,
    status: "CLOSED",
    phase: "ACCEPTED",
    committed: true,
    event: out?.event || null,
    info: "FA 계약이 확정되었습니다.",
    error: null,
  };

  await loadFaList();
  return out;
}

async function startTwoWayNegotiation(playerId) {
  if (!state.selectedTeamId) throw new Error("먼저 팀을 선택해주세요.");
  const out = await fetchJson("/api/contracts/two-way/negotiation/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      team_id: state.selectedTeamId,
      player_id: playerId,
    }),
  });
  state.marketNegotiation = normalizeNegotiationStateFromSession(out, {
    mode: "SIGN_TWO_WAY",
    player_id: playerId,
    info: "투웨이 협상이 시작되었습니다.",
    error: null,
  });
  return out;
}

async function decideTwoWayNegotiation(accept) {
  const current = state.marketNegotiation || {};
  if (!current.session_id) throw new Error("먼저 투웨이 협상을 시작해주세요.");
  const out = await fetchJson("/api/contracts/two-way/negotiation/decision", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: current.session_id,
      accept: !!accept,
    }),
  });
  state.marketNegotiation = normalizeNegotiationStateFromSession(out?.session, {
    player_id: current.player_id,
    last_decision: { verdict: accept ? "ACCEPT" : "REJECT", reason: "TWO_WAY_DECISION" },
    info: accept ? "선수가 투웨이 계약을 수락했습니다." : "선수가 투웨이 계약을 거절했습니다.",
    error: null,
  });
  return out;
}

async function commitTwoWaySigning() {
  const current = state.marketNegotiation || {};
  if (!current.session_id) throw new Error("확정 가능한 투웨이 협상이 없습니다.");
  const out = await fetchJson("/api/contracts/two-way/negotiation/commit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: current.session_id }),
  });
  state.marketNegotiation = {
    ...current,
    status: "CLOSED",
    phase: "ACCEPTED",
    committed: true,
    event: out?.event || null,
    info: "투웨이 계약이 확정되었습니다.",
    error: null,
  };
  await loadFaList();
  return out;
}

async function performMarketNegotiationAction(action) {
  const playerId = state.marketSelectedPlayerId;
  if (!playerId) throw new Error("먼저 FA 선수를 선택해주세요.");

  setLoading(true, "협상 진행 중...");
  try {
    const flags = getNegotiationUiFlags(state.marketNegotiation || {});

    if (action === "start-fa") await startFaNegotiation(playerId);
    else if (action === "offer-fa") {
      if (!flags.canOfferFa) throw new Error("현재 상태에서는 오퍼를 제출할 수 없습니다.");
      await submitFaOfferAuto();
    } else if (action === "accept-counter") {
      if (!flags.canAcceptCounter) throw new Error("수락 가능한 카운터 오퍼가 없습니다.");
      await acceptFaCounter();
    } else if (action === "commit-fa") {
      if (!flags.canCommitFa) throw new Error("FA 계약 확정 조건이 아직 충족되지 않았습니다.");
      await commitFaSigning();
    } else if (action === "start-two-way") await startTwoWayNegotiation(playerId);
    else if (action === "two-way-accept") {
      if (!flags.canTwoWayDecision) throw new Error("현재 상태에서는 투웨이 수락/거절을 결정할 수 없습니다.");
      await decideTwoWayNegotiation(true);
    } else if (action === "two-way-reject") {
      if (!flags.canTwoWayDecision) throw new Error("현재 상태에서는 투웨이 수락/거절을 결정할 수 없습니다.");
      await decideTwoWayNegotiation(false);
    } else if (action === "commit-two-way") {
      if (!flags.canCommitTwoWay) throw new Error("투웨이 계약 확정 조건이 아직 충족되지 않았습니다.");
      await commitTwoWaySigning();
    }
    else return;

    await refreshSelectedMarketPlayerDetail();
  } finally {
    setLoading(false);
  }
}

async function handleMarketDetailAction(action) {
  try {
    await performMarketNegotiationAction(action);
  } catch (e) {
    const current = state.marketNegotiation || {};
    state.marketNegotiation = {
      ...current,
      error: toFriendlyRuleMessage(e?.message || ""),
      info: null,
    };
    await refreshSelectedMarketPlayerDetail();
    alert(toFriendlyRuleMessage(e?.message || "협상 처리에 실패했습니다."));
  }
}

async function showMarketScreen() {
  if (!state.selectedTeamId) {
    alert("먼저 팀을 선택해주세요.");
    return;
  }

  state.playerDetailBackTarget = "market";
  switchMarketSubTab(state.marketSubTab || "fa");
  activateScreen(els.marketScreen);
  await openMarketSubTab(state.marketSubTab || "fa");

  if (!state.marketTradeModalBound) {
    els.marketTradeModalCancel?.addEventListener("click", closeTradeNegotiationModal);
    els.marketTradeModalBackdrop?.addEventListener("click", closeTradeNegotiationModal);
    els.marketTradeModalStart?.addEventListener("click", () => {
      setLoading(true, "트레이드 협상 세션을 생성하는 중...");
      startTradeNegotiationFromModal().catch((e) => alert(e.message)).finally(() => setLoading(false));
    });
    state.marketTradeModalBound = true;
  }
}

export {
  switchMarketSubTab,
  showMarketScreen,
  loadFaList,
  renderFaRows,
  getFaDisplayRows,
  openMarketSubTab,
  handleMarketDetailAction,
};
