import { state, syncMarketTradeModalSessionState, transitionMarketTradeSessionFsm, resetTradeDealModalContext } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import {
  fetchJson,
  fetchCachedJson,
  fetchTradeNegotiationInbox,
  startTradeNegotiationSession,
  openTradeNegotiationSession,
  rejectTradeNegotiationSession,
  commitTradeNegotiationSession,
  submitCommittedTradeDeal,
  fetchStateSummary,
  beginScopedRequest,
  isScopedRequestCurrent,
  abortScopedRequest,
  abortAllMarketTradeRequests,
  invalidateCachedValue,
  invalidateCachedValuesByPrefix,
  setLoading,
} from "../../core/api.js";
import { num } from "../../core/guards.js";
import { formatHeightIn, formatMoney, formatWeightLb, strictFormatPickLabel, formatProtectionSummary, formatSwapAssetLabel, formatFixedAssetLabel } from "../../core/format.js";
import { TEAM_FULL_NAMES, applyTeamLogo, getTeamDisplayName } from "../../core/constants/teams.js";
import { reportTradeContractViolation } from "../../core/telemetry.js";
import { loadPlayerDetail } from "../myteam/playerDetail.js";
import { fetchTeamDetail, invalidateTeamDetailCache } from "../team/teamDetailCache.js";

const MARKET_TRADE_INBOX_CACHE_TTL_MS = 30 * 1000;
const MARKET_TRADE_BLOCK_CACHE_TTL_MS = 24 * 60 * 60 * 1000;

function getTradeBlockCacheKeyAll() {
  return "market:trade-block:all";
}

function getTradeBlockCacheKeyMine(teamId) {
  return `market:trade-block:mine:${String(teamId || "").toUpperCase()}`;
}

function invalidateTradeBlockCaches({ teamId = state.selectedTeamId } = {}) {
  invalidateCachedValue(getTradeBlockCacheKeyAll());
  if (teamId) {
    invalidateCachedValue(getTradeBlockCacheKeyMine(teamId));
    return;
  }
  invalidateCachedValuesByPrefix("market:trade-block:mine:");
}

function getActiveTradeSessionId() {
  const sessionId = state.marketTradeActiveSession?.session_id;
  return sessionId == null ? null : String(sessionId);
}

function isMarketUiActive() {
  return Boolean(state.marketScreenActive && state.tradeDealModalOpen);
}

function createTradeIdempotencyKey(action, sessionId = null) {
  // Backend schema constraint: ^[A-Za-z0-9_-]{8,128}$
  const sanitizeToken = (value, fallback) => {
    const text = String(value == null ? "" : value).trim();
    const cleaned = text.replace(/[^A-Za-z0-9_-]/g, "_");
    return cleaned || fallback;
  };

  const normalizedAction = sanitizeToken(action, "action");
  const normalizedSessionId = sanitizeToken(sessionId, "none");
  const ts = Date.now().toString(36);
  const rand = Math.random().toString(36).slice(2, 10);
  const key = `${normalizedSessionId}-${normalizedAction}-${ts}-${rand}`;
  return key.slice(0, 128);
}

function runScopedTask(scope, runner) {
  const key = String(scope || "default");
  if (!state.marketTaskQueueByScope || typeof state.marketTaskQueueByScope !== "object") {
    state.marketTaskQueueByScope = {};
  }
  const previous = state.marketTaskQueueByScope[key] || Promise.resolve();
  const current = previous
    .catch(() => {})
    .then(async () => runner());
  state.marketTaskQueueByScope[key] = current;
  return current;
}

function setActionPending(action, sessionId, pending, idempotencyKey = "") {
  const actionKey = `${String(action || "")}::${String(sessionId || "")}`;
  if (!state.marketTradePendingActions || typeof state.marketTradePendingActions !== "object") {
    state.marketTradePendingActions = {};
  }
  if (pending) {
    state.marketTradePendingActions[actionKey] = {
      pending: true,
      idempotencyKey: String(idempotencyKey || ""),
    };
  } else {
    delete state.marketTradePendingActions[actionKey];
  }
}

function isActionPending(action, sessionId) {
  const actionKey = `${String(action || "")}::${String(sessionId || "")}`;
  return Boolean(state.marketTradePendingActions?.[actionKey]?.pending);
}

function getTradeModalStartPendingKey() {
  const teamId = String(state.selectedTeamId || "").toUpperCase();
  const otherTeamId = String(state.marketTradeModalOtherTeamId || "").toUpperCase();
  const playerId = String(state.marketTradeModalPlayerId || "");
  return `${teamId}:${otherTeamId}:${playerId}`;
}

function syncTradeModalStartButtonState() {
  if (!(els.marketTradeModalStart instanceof HTMLButtonElement)) return;
  const pending = isActionPending("start-from-modal", getTradeModalStartPendingKey());
  els.marketTradeModalStart.disabled = pending;
  els.marketTradeModalStart.setAttribute("aria-disabled", pending ? "true" : "false");
  els.marketTradeModalStart.setAttribute("aria-busy", pending ? "true" : "false");
}

function syncTradeActionButtonStates() {
  document.querySelectorAll("[data-trade-action][data-session-id]").forEach((btn) => {
    if (!(btn instanceof HTMLButtonElement)) return;
    const action = btn.dataset.tradeAction || "";
    const sessionId = btn.dataset.sessionId || "";
    const pending = isActionPending(action, sessionId);
    btn.disabled = pending;
    btn.setAttribute("aria-disabled", pending ? "true" : "false");
    btn.setAttribute("aria-busy", pending ? "true" : "false");
  });

  if (els.marketTradeDealReject) {
    const modalSessionId = getActiveTradeSessionId() || "";
    const pending = isActionPending("reject-from-modal", modalSessionId);
    els.marketTradeDealReject.disabled = pending;
    els.marketTradeDealReject.setAttribute("aria-disabled", pending ? "true" : "false");
    els.marketTradeDealReject.setAttribute("aria-busy", pending ? "true" : "false");
  }
}

function shouldApplyResponseForScope(scope, requestId, sessionId = null, { requireModalOpen = false } = {}) {
  if (!isScopedRequestCurrent(scope, requestId, sessionId)) return false;
  if (!state.marketScreenActive) return false;
  if (requireModalOpen && !isMarketUiActive()) return false;
  if (sessionId != null && getActiveTradeSessionId() !== String(sessionId)) return false;
  return true;
}

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
  if (msg.includes("NEGOTIATION_NOT_AUTHORIZED")) {
    return "해당 제안에 대한 권한이 없습니다.";
  }
  if (msg.includes("NEGOTIATION_NOT_FOUND")) {
    return "존재하지 않는 협상 세션입니다.";
  }
  if (msg.includes("NEGOTIATION_INVALID_PHASE")) {
    return "현재 협상 단계에서는 해당 작업을 수행할 수 없습니다.";
  }
  if (msg.includes("NEGOTIATION_ENDED_BY_AI")) {
    return "상대 팀이 협상을 종료했습니다. 제안이 인박스에서 제거됩니다.";
  }
  if (msg.includes("NEGOTIATION_NOT_ACTIVE") || msg.includes("Negotiation session is closed")) {
    return "협상이 이미 종료되었습니다. 새 협상을 시작해주세요.";
  }
  if (msg.includes("DEAL_INVALIDATED")) {
    return "딜 데이터가 유효하지 않습니다. 자산 구성을 다시 확인해주세요.";
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
  const next = tab === "trade-block" || tab === "trade-inbox" ? tab : "fa";
  state.marketSubTab = next;

  const mapping = {
    fa: [els.marketSubtabFa, els.marketPanelFa],
    "trade-block": [els.marketSubtabTradeBlock, els.marketPanelTradeBlock],
    "trade-inbox": [els.marketSubtabTradeInbox, els.marketPanelTradeInbox],
  };

  Object.entries(mapping).forEach(([key, [btn, panel]]) => {
    const active = key === next;
    btn?.classList.toggle("is-active", active);
    btn?.setAttribute("aria-selected", active ? "true" : "false");
    panel?.classList.toggle("active", active);
    panel?.setAttribute("aria-hidden", active ? "false" : "true");
  });
}

function switchTradeBlockScope(scope) {
  const next = scope === "mine" ? "mine" : "other";
  state.marketTradeBlockScope = next;

  const mapping = {
    other: [els.marketTradeBlockScopeOther, els.marketTradeBlockPanelOther],
    mine: [els.marketTradeBlockScopeMine, els.marketTradeBlockPanelMine],
  };

  Object.entries(mapping).forEach(([key, [btn, panel]]) => {
    const active = key === next;
    btn?.classList.toggle("is-active", active);
    btn?.setAttribute("aria-selected", active ? "true" : "false");
    panel?.classList.toggle("active", active);
    panel?.setAttribute("aria-hidden", active ? "false" : "true");
  });
}

function setMarketTradeInboxLoading(loading) {
  state.marketTradeInboxLoading = !!loading;
  if (els.marketTradeInboxLoading) {
    els.marketTradeInboxLoading.classList.toggle("hidden", !loading);
  }
}

function createEmptyTradeDealDraft(userTeamId, otherTeamId) {
  return {
    teams: [userTeamId, otherTeamId].filter(Boolean),
    legs: {
      ...(userTeamId ? { [userTeamId]: [] } : {}),
      ...(otherTeamId ? { [otherTeamId]: [] } : {}),
    },
    meta: {},
  };
}

function normalizeTradeDealDraft(sourceDeal, userTeamId, otherTeamId) {
  const fallback = createEmptyTradeDealDraft(userTeamId, otherTeamId);
  const deal = sourceDeal && typeof sourceDeal === "object" ? sourceDeal : fallback;
  const teams = Array.isArray(deal?.teams) && deal.teams.length ? deal.teams : fallback.teams;
  const legs = deal?.legs && typeof deal.legs === "object" && !Array.isArray(deal.legs)
    ? { ...deal.legs }
    : { ...fallback.legs };

  teams.forEach((teamId) => {
    const key = String(teamId || "");
    if (!key) return;
    if (!Array.isArray(legs[key])) legs[key] = [];
  });

  return {
    teams,
    legs,
    meta: deal?.meta && typeof deal.meta === "object" ? { ...deal.meta } : {},
  };
}

function normalizeTradeDealCandidate(candidate, userTeamId, otherTeamId) {
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return null;
  const teams = Array.isArray(candidate?.teams) ? candidate.teams : [];
  const legs = candidate?.legs;
  if (!teams.length || !legs || typeof legs !== "object" || Array.isArray(legs)) return null;
  return normalizeTradeDealDraft(candidate, userTeamId, otherTeamId);
}

function normalizeInboxOfferDeal(row, userTeamId = state.selectedTeamId, otherTeamId = row?.other_team_id) {
  const offer = row?.offer;
  const candidates = [
    offer?.deal,
    offer?.offer,
    row?.deal,
    row?.last_offer?.deal,
    row?.last_offer?.offer,
    row?.last_offer,
    offer,
    row,
  ];
  for (const candidate of candidates) {
    const normalized = normalizeTradeDealCandidate(candidate, userTeamId, otherTeamId);
    if (normalized) return normalized;
  }
  return createEmptyTradeDealDraft(userTeamId, otherTeamId);
}

function normalizeSessionDeal(session, userTeamId = state.selectedTeamId, otherTeamId = session?.other_team_id) {
  const candidates = [
    session?.draft_deal,
    session?.last_offer?.deal,
    session?.last_offer?.offer,
    session?.last_offer,
    session?.offer?.deal,
    session?.offer,
  ];
  for (const candidate of candidates) {
    const normalized = normalizeTradeDealCandidate(candidate, userTeamId, otherTeamId);
    if (normalized) return normalized;
  }
  return createEmptyTradeDealDraft(userTeamId, otherTeamId);
}

function normalizeTradeAssetsSnapshot(rawAssets) {
  const src = rawAssets && typeof rawAssets === "object" ? rawAssets : {};

  const toRows = (value) => {
    if (Array.isArray(value)) return value.filter((item) => item && typeof item === "object");
    if (value && typeof value === "object") return Object.values(value).filter((item) => item && typeof item === "object");
    return [];
  };

  return {
    draft_picks: toRows(src?.draft_picks),
    swap_rights: toRows(src?.swap_rights),
    fixed_assets: toRows(src?.fixed_assets),
  };
}

function buildBaseDraftFromSession(session, userTeamId, otherTeamId) {
  return normalizeSessionDeal(session, userTeamId, otherTeamId);
}

function renderMarketTradeDealDraftPreview() {
  if (!els.marketTradeDealLegsPreview) return;
  const draft = state.marketTradeDealDraft;
  const teams = Array.isArray(draft?.teams) ? draft.teams : [];
  if (!teams.length) {
    els.marketTradeDealLegsPreview.innerHTML = '<p class="subtitle">현재 제안된 자산이 없습니다.</p>';
    return;
  }

  const rows = teams.map((teamId) => {
    const key = String(teamId || "");
    const teamLabel = TEAM_FULL_NAMES[key] || key || "-";
    const assets = Array.isArray(draft?.legs?.[key]) ? draft.legs[key] : [];
    return `<div><strong>${teamLabel}</strong>: ${assets.length}개 자산</div>`;
  });

  els.marketTradeDealLegsPreview.innerHTML = rows.join("");
}

function ensurePrefillAssetInDraft(draft, otherTeamId, playerId) {
  if (!playerId || !otherTeamId || !draft) return draft;
  const key = String(otherTeamId || "");
  if (!key) return draft;

  if (!Array.isArray(draft.teams)) draft.teams = [];
  if (!draft.teams.includes(key)) draft.teams.push(key);
  if (!draft.legs || typeof draft.legs !== "object" || Array.isArray(draft.legs)) draft.legs = {};
  if (!Array.isArray(draft.legs[key])) draft.legs[key] = [];

  const exists = draft.legs[key].some((asset) => String(asset?.kind || "") === "player" && String(asset?.player_id || "") === String(playerId));
  if (!exists) {
    draft.legs[key].push({ kind: "player", player_id: playerId });
  }
  return draft;
}

function getTradeDealDraftLegAssets(teamId) {
  const key = String(teamId || "");
  if (!key) return [];
  if (!state.marketTradeDealDraft || typeof state.marketTradeDealDraft !== "object") {
    state.marketTradeDealDraft = createEmptyTradeDealDraft(state.selectedTeamId, null);
  }
  if (!state.marketTradeDealDraft.legs || typeof state.marketTradeDealDraft.legs !== "object") {
    state.marketTradeDealDraft.legs = {};
  }
  if (!Array.isArray(state.marketTradeDealDraft.legs[key])) {
    state.marketTradeDealDraft.legs[key] = [];
  }
  return state.marketTradeDealDraft.legs[key];
}

function toAssetKey(asset) {
  if (!asset || typeof asset !== "object") return "";
  if (asset.kind === "player") return `player:${asset.player_id || ""}`;
  if (asset.kind === "pick") return `pick:${asset.pick_id || ""}`;
  if (asset.kind === "swap") {
    const swapId = String(asset.swap_id || "").trim();
    if (swapId) return `swap:${swapId}`;
    const fallbackSwapId = computeSwapIdFromPair(asset?.pick_id_a, asset?.pick_id_b);
    return fallbackSwapId ? `swap:${fallbackSwapId}` : "";
  }
  if (asset.kind === "fixed_asset") return `fixed_asset:${asset.asset_id || ""}`;
  return "";
}

function hasAsset(teamId, asset) {
  const key = toAssetKey(asset);
  if (!key) return false;
  return getTradeDealDraftLegAssets(teamId).some((existing) => toAssetKey(existing) === key);
}

function addAsset(teamId, asset) {
  const key = toAssetKey(asset);
  if (!key) return;
  const legs = getTradeDealDraftLegAssets(teamId);
  if (!legs.some((existing) => toAssetKey(existing) === key)) {
    legs.push(asset);
  }
}

function removeAsset(teamId, asset) {
  const key = toAssetKey(asset);
  if (!key) return;
  state.marketTradeDealDraft.legs[String(teamId || "")] = getTradeDealDraftLegAssets(teamId)
    .filter((existing) => toAssetKey(existing) !== key);
}

function toggleAsset(teamId, asset) {
  if (hasAsset(teamId, asset)) removeAsset(teamId, asset);
  else addAsset(teamId, asset);
}

function parseProtectionInput(raw) {
  const text = String(raw || "").trim();
  if (!text) return { ok: true, value: null };
  try {
    const parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { ok: false, error: "보호조건은 JSON 객체 형태여야 합니다." };
    }
    if (!parsed.type) {
      return { ok: false, error: "보호조건 JSON에 type 필드가 필요합니다." };
    }
    if (["TOP", "TOP_N", "TOP_PROTECTED"].includes(String(parsed.type).toUpperCase())) {
      const val = Number(parsed.value ?? parsed.n ?? parsed.top_n);
      if (!Number.isFinite(val) || val < 1) {
        return { ok: false, error: "TOP 보호조건은 value(또는 n/top_n)에 1 이상의 숫자가 필요합니다." };
      }
    }
    return { ok: true, value: parsed };
  } catch {
    return { ok: false, error: "보호조건 JSON 파싱에 실패했습니다. 예: {\"type\":\"TOP\",\"value\":5}" };
  }
}

function createPickAsset(pick, protectionText) {
  const protectionParsed = parseProtectionInput(protectionText);
  if (!protectionParsed.ok) throw new Error(protectionParsed.error);
  return {
    kind: "pick",
    pick_id: pick?.pick_id,
    ...(protectionParsed.value ? { protection: protectionParsed.value } : {}),
  };
}

function renderTradeDealAssetList(container, teamId, pool) {
  if (!container) return;
  const players = Array.isArray(pool?.players) ? pool.players : [];
  const picks = Array.isArray(pool?.picks) ? pool.picks : [];
  const swaps = Array.isArray(pool?.swaps) ? pool.swaps : [];
  const fixedAssets = Array.isArray(pool?.fixedAssets) ? pool.fixedAssets : [];
  const hasAny = players.length || picks.length || swaps.length || fixedAssets.length;

  if (!hasAny) {
    container.innerHTML = '<p class="subtitle">거래 자산이 없습니다.</p>';
    return;
  }

  const teamLabel = TEAM_FULL_NAMES[String(teamId || "").toUpperCase()] || String(teamId || "-").toUpperCase();
  container.innerHTML = `
    <h5>${teamLabel}</h5>
    <ul class="market-trade-deal-list-inner">
      ${players.map((player) => {
    const asset = { kind: "player", player_id: player?.player_id };
    const selected = hasAsset(teamId, asset);
    return `<li><span>[선수] ${player?.name || player?.player_id || "-"} (${player?.pos || "-"})</span><button type="button" class="btn btn-secondary" data-deal-toggle-kind="player" data-deal-id="${player?.player_id}" data-deal-team-id="${teamId}">${selected ? "제거" : "추가"}</button></li>`;
  }).join("")}
      ${picks.map((pick) => {
    const asset = { kind: "pick", pick_id: pick?.pick_id };
    const selected = hasAsset(teamId, asset);
    const protection = selected
      ? JSON.stringify((getTradeDealDraftLegAssets(teamId).find((a) => a?.kind === "pick" && String(a?.pick_id || "") === String(pick?.pick_id || "")) || {}).protection || {})
      : "";
    return `<li><span>[픽] ${pick?.pick_id || "-"}</span><input type="text" class="ui-input" data-pick-protection="${pick?.pick_id}" placeholder='{"type":"TOP","value":5}' value='${protection === "{}" ? "" : protection}' /><button type="button" class="btn btn-secondary" data-deal-toggle-kind="pick" data-deal-id="${pick?.pick_id}" data-deal-team-id="${teamId}">${selected ? "제거" : "추가"}</button></li>`;
  }).join("")}
      ${swaps.map((swap) => {
    const asset = { kind: "swap", swap_id: swap?.swap_id, pick_id_a: swap?.pick_id_a, pick_id_b: swap?.pick_id_b };
    const selected = hasAsset(teamId, asset);
    return `<li><span>[스왑] ${swap?.swap_id || "-"}</span><button type="button" class="btn btn-secondary" data-deal-toggle-kind="swap" data-deal-id="${swap?.swap_id}" data-deal-team-id="${teamId}">${selected ? "제거" : "추가"}</button></li>`;
  }).join("")}
      ${fixedAssets.map((fa) => {
    const asset = { kind: "fixed_asset", asset_id: fa?.asset_id };
    const selected = hasAsset(teamId, asset);
    return `<li><span>[고정자산] ${fa?.asset_id || "-"}</span><button type="button" class="btn btn-secondary" data-deal-toggle-kind="fixed_asset" data-deal-id="${fa?.asset_id}" data-deal-team-id="${teamId}">${selected ? "제거" : "추가"}</button></li>`;
  }).join("")}
    </ul>
  `;

  container.querySelectorAll("button[data-deal-toggle-kind]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      const kind = btn.getAttribute("data-deal-toggle-kind");
      const id = btn.getAttribute("data-deal-id");
      const targetTeamId = btn.getAttribute("data-deal-team-id");
      if (!kind || !id || !targetTeamId) return;
      try {
        if (kind === "player") toggleAsset(targetTeamId, { kind: "player", player_id: id });
        if (kind === "pick") {
          const protectionInput = container.querySelector(`input[data-pick-protection='${id}']`);
          const pickAsset = createPickAsset({ pick_id: id }, protectionInput?.value || "");
          toggleAsset(targetTeamId, pickAsset);
        }
        if (kind === "swap") {
          const swap = (pool?.swaps || []).find((it) => String(it?.swap_id || "") === String(id));
          toggleAsset(targetTeamId, { kind: "swap", swap_id: id, pick_id_a: swap?.pick_id_a, pick_id_b: swap?.pick_id_b });
        }
        if (kind === "fixed_asset") toggleAsset(targetTeamId, { kind: "fixed_asset", asset_id: id });
        renderTradeDealEditor();
        setTradeDealEditorMessage("");
      } catch (e) {
        setTradeDealEditorMessage(e.message || "자산 처리 중 오류가 발생했습니다.");
        alert(e.message);
      }
    });
  });
}

function renderTradeDealEditor() {
  const myTeamId = state.selectedTeamId;
  const otherTeamId = String(state.marketTradeActiveSession?.other_team_id || "").toUpperCase();

  // legacy hidden containers are kept for incremental migration compatibility.
  renderTradeDealAssetList(els.marketTradeDealAssetsMyTeam, myTeamId, state.marketTradeAssetPool?.myTeam || {});
  renderTradeDealAssetList(els.marketTradeDealAssetsOtherTeam, otherTeamId, state.marketTradeAssetPool?.otherTeam || {});

  renderTradeDealTopPackage();
  renderTradeDealAssetTabs();
  renderMarketTradeDealDraftPreview();
}

function extractTeamTradeAssets(summaryPayload, ownerTeamId) {
  const teamId = String(ownerTeamId || "").toUpperCase();
  const rawSnapshot = summaryPayload?.db_snapshot?.trade_assets;
  const snapshot = normalizeTradeAssetsSnapshot(rawSnapshot);
  const picksRaw = snapshot.draft_picks;
  const swapsRaw = snapshot.swap_rights;
  const fixedRaw = snapshot.fixed_assets;

  const picks = picksRaw
    .filter((item) => String(item?.owner_team_id || item?.owner_team || "").toUpperCase() === teamId)
    .map((item) => ({
      pick_id: item?.pick_id,
      year: item?.year,
      round: item?.round,
      owner_team: item?.owner_team,
      original_team: item?.original_team,
      protection: item?.protection || null,
    }));
  const swaps = swapsRaw
    .filter((item) => String(item?.owner_team_id || item?.owner_team || "").toUpperCase() === teamId)
    .map((item) => ({
      swap_id: item?.swap_id || computeSwapIdFromPair(item?.pick_id_a, item?.pick_id_b),
      year: item?.year,
      round: item?.round,
      pick_id_a: item?.pick_id_a,
      pick_id_b: item?.pick_id_b,
    }));
  const fixedAssets = fixedRaw
    .filter((item) => String(item?.owner_team_id || item?.owner_team || "").toUpperCase() === teamId)
    .map((item) => ({
      asset_id: item?.asset_id,
      label: item?.label || "",
      draft_year: item?.draft_year ?? null,
      source_pick_id: item?.source_pick_id || null,
    }));

  return { picks, swaps, fixedAssets };
}

async function loadTradeDealPlayerPools(otherTeamId, { requestCtx = null } = {}) {
  const myTeamId = state.selectedTeamId;
  if (!myTeamId || !otherTeamId) return;

  const prevPool = state.marketTradeAssetPool
    ? JSON.parse(JSON.stringify(state.marketTradeAssetPool))
    : { myTeam: { players: [], picks: [], swaps: [], fixedAssets: [] }, otherTeam: { players: [], picks: [], swaps: [], fixedAssets: [] } };

  try {
    const signal = requestCtx?.signal;
    const requestId = requestCtx?.requestId;
    const scopedSessionId = requestCtx?.sessionId;
    const [myTeamDetail, otherTeamDetail, summary] = await Promise.all([
      fetchTeamDetail(myTeamId, { force: true }),
      fetchTeamDetail(otherTeamId, { force: true }),
      fetchStateSummary({ signal }),
    ]);

    if (requestCtx && !shouldApplyResponseForScope("dealPlayerPool", requestId, scopedSessionId, { requireModalOpen: true })) {
      return;
    }

    const myAssets = extractTeamTradeAssets(summary, myTeamId);
    const otherAssets = extractTeamTradeAssets(summary, otherTeamId);

    state.marketTradeAssetPool = {
      ...state.marketTradeAssetPool,
      myTeam: {
        ...(state.marketTradeAssetPool?.myTeam || {}),
        players: Array.isArray(myTeamDetail?.roster) ? myTeamDetail.roster : [],
        picks: myAssets.picks,
        swaps: myAssets.swaps,
        fixedAssets: myAssets.fixedAssets,
      },
      otherTeam: {
        ...(state.marketTradeAssetPool?.otherTeam || {}),
        players: Array.isArray(otherTeamDetail?.roster) ? otherTeamDetail.roster : [],
        picks: otherAssets.picks,
        swaps: otherAssets.swaps,
        fixedAssets: otherAssets.fixedAssets,
      },
    };
  } catch (error) {
    state.marketTradeAssetPool = prevPool;
    throw new Error(toFriendlyRuleMessage(error?.message || "거래 자산 정보를 불러오지 못했습니다."));
  }
}

function validateTradeDealDraftForCommit() {
  const draft = state.marketTradeDealDraft || {};
  const teams = Array.isArray(draft?.teams) ? draft.teams.map((teamId) => String(teamId || "")).filter(Boolean) : [];
  const legKeys = Object.keys(draft?.legs || {}).map((key) => String(key || "")).filter(Boolean);

  if (!teams.length) return "딜 팀 정보가 유효하지 않습니다.";
  if (legKeys.some((key) => !teams.includes(key))) return "딜 legs의 팀 키가 teams와 일치하지 않습니다.";
  if (teams.some((teamId) => !Array.isArray(draft?.legs?.[teamId]))) return "딜 legs에 팀별 자산 배열이 필요합니다.";

  const allAssets = Object.values(draft?.legs || {}).flatMap((assets) => (Array.isArray(assets) ? assets : []));
  if (!allAssets.length) return "최소 1개 이상의 거래 자산을 추가해야 합니다.";

  const unique = new Set();
  for (const asset of allAssets) {
    const key = toAssetKey(asset);
    if (!key) continue;
    if (unique.has(key)) return "같은 자산을 중복으로 추가할 수 없습니다.";
    unique.add(key);

    if (asset?.kind === "pick" && asset?.protection != null) {
      const protectionCheck = parseProtectionInput(JSON.stringify(asset?.protection ?? {}));
      if (!protectionCheck.ok) return protectionCheck.error;
    }
  }
  return "";
}

function toFriendlyCommitErrorMessage(rawMessage) {
  const msg = String(rawMessage || "");
  if (!msg) return "제안 제출 중 오류가 발생했습니다.";
  if (msg.includes("PICK_NOT_OWNED")) return "소유하지 않은 픽이 포함되어 있습니다.";
  if (msg.includes("PROTECTION_CONFLICT")) return "보호조건이 충돌합니다.";
  if (msg.includes("SWAP_INVALID")) return "스왑 자산 구성이 유효하지 않습니다.";
  if (msg.includes("FIXED_ASSET_NOT_OWNED") || msg.includes("FIXED_ASSET_NOT_FOUND")) return "고정 자산 소유/존재 정보를 다시 확인해주세요.";
  if (msg.includes("PLAYER_NOT_OWNED")) return "소유하지 않은 선수가 포함되어 있습니다.";
  if (msg.includes("NEGOTIATION_NOT_ACTIVE")) return "협상이 이미 종료되었습니다.";
  if (msg.includes("NEGOTIATION_INVALID_PHASE")) return "현재 협상 단계에서는 제출할 수 없습니다.";
  if (msg.includes("DEAL_INVALIDATED")) return "딜 데이터가 유효하지 않습니다. 구성을 다시 확인해주세요.";
  return msg;
}

function setTradeDealEditorMessage(message) {
  if (!els.marketTradeDealMessages) return;
  const text = String(message || "").trim();
  els.marketTradeDealMessages.innerHTML = text ? `<p class="subtitle">${text}</p>` : "";
}

function setTradeDealSubmitPending(pending) {
  const nextPending = !!pending;
  state.marketTradeUi = {
    ...(state.marketTradeUi || {}),
    submitPending: nextPending,
  };

  const submitBtn = els.marketTradeDealSubmit;
  if (!submitBtn) return;

  if (!submitBtn.dataset.defaultLabel) {
    submitBtn.dataset.defaultLabel = submitBtn.textContent?.trim() || "제안 제출";
  }
  const defaultLabel = submitBtn.dataset.defaultLabel || "제안 제출";
  submitBtn.disabled = nextPending;
  submitBtn.setAttribute("aria-disabled", nextPending ? "true" : "false");
  submitBtn.setAttribute("aria-busy", nextPending ? "true" : "false");
  submitBtn.textContent = nextPending ? "제출 중..." : defaultLabel;

  syncTradeActionButtonStates();
}

async function refreshMarketTradeAfterAccepted(otherTeamId) {
  const myTeamId = state.selectedTeamId;
  if (myTeamId) invalidateTeamDetailCache(myTeamId);
  if (otherTeamId) invalidateTeamDetailCache(otherTeamId);
  invalidateTradeBlockCaches({ teamId: myTeamId });

  const activeSessionId = getActiveTradeSessionId();
  await Promise.all([
    loadFaList({ requestCtx: beginScopedRequest("faList", { sessionId: activeSessionId }) }),
    loadTradeBlockList({ requestCtx: beginScopedRequest("tradeBlockList", { sessionId: activeSessionId }) }),
    loadMarketTradeInbox({ force: true, reason: "after-accepted" }),
  ]);

  if (otherTeamId) {
    await loadTradeDealPlayerPools(otherTeamId);
    renderTradeDealEditor();
  }
}

async function submitTradeDealDraft() {
  const sessionId = state.marketTradeActiveSession?.session_id;
  if (!sessionId) throw new Error("세션 정보가 없어 제안을 제출할 수 없습니다.");

  const validationError = validateTradeDealDraftForCommit();
  if (validationError) throw new Error(validationError);

  transitionMarketTradeSessionFsm("submitting", {
    sessionId,
    reason: "submitTradeDealDraft:start",
    strict: false,
  });

  const requestCtx = beginScopedRequest("submitDeal", { sessionId });
  const commitIdempotencyKey = createTradeIdempotencyKey("commit", sessionId);

  const result = await commitTradeNegotiationSession({
    sessionId,
    deal: state.marketTradeDealDraft,
    offerPrivacy: "PRIVATE",
    exposeToMedia: false,
    signal: requestCtx.signal,
    idempotencyKey: commitIdempotencyKey,
  });

  if (!shouldApplyResponseForScope("submitDeal", requestCtx.requestId, requestCtx.sessionId, { requireModalOpen: true })) {
    return result;
  }

  const otherTeamId = String(state.marketTradeActiveSession?.other_team_id || "").toUpperCase();
  const latestSession = result?.session || state.marketTradeActiveSession || {};
  const latestDeal = normalizeSessionDeal(latestSession, state.selectedTeamId, otherTeamId);
  setMarketTradeOfferSnapshot("latest", sessionId, latestDeal);

  if (els.marketTradeDealStatus) {
    const accepted = result?.accepted === true;
    els.marketTradeDealStatus.innerHTML = `<p class="subtitle">제출 결과: ${accepted ? "수락" : "검토중/카운터"}</p>`;
  }
  if (els.marketTradeDealMessages) {
    setTradeDealEditorMessage(result?.message || "제안이 정상 제출되었습니다.");
  }

  const accepted = result?.accepted === true;
  const dealId = result?.deal_id || result?.deal?.deal_id || null;
  if (accepted && dealId) {
    const submitOut = await submitCommittedTradeDeal({
      dealId,
      force: true,
      idempotencyKey: createTradeIdempotencyKey("submit-committed", sessionId),
    });
    if (els.marketTradeDealStatus) {
      els.marketTradeDealStatus.innerHTML = '<p class="subtitle">제출 결과: 수락 · 거래 반영 완료</p>';
    }
    setTradeDealEditorMessage(submitOut?.message || "수락된 제안이 실제 트레이드로 반영되었습니다.");
    await refreshMarketTradeAfterAccepted(otherTeamId);
  }
  transitionMarketTradeSessionFsm("ready", {
    sessionId,
    reason: "submitTradeDealDraft:complete",
    strict: false,
  });
  return result;
}

async function openTradeDealEditorFromSession(session, fallback = {}) {
  const activeSession = session || {};
  const fallbackOtherTeamId = String(fallback?.otherTeamId || "").toUpperCase();
  const otherTeamId = String(activeSession?.other_team_id || fallbackOtherTeamId || "").toUpperCase();
  const prefillPlayerId = fallback?.prefillPlayerId || null;
  const sessionId = activeSession?.session_id || fallback?.sessionId || null;

  transitionMarketTradeSessionFsm("opening", {
    sessionId,
    reason: "openTradeDealEditorFromSession:start",
    strict: false,
  });

  const sync = syncMarketTradeModalSessionState(sessionId, { keepTabsOnReopen: true });
  state.marketTradeActiveSession = activeSession;

  const normalizedDeal = normalizeSessionDeal(activeSession, state.selectedTeamId, otherTeamId);
  if (sync?.didReset || !state.marketTradeInitialOfferSnapshot?.deal) {
    setMarketTradeOfferSnapshot("initial", sessionId, normalizedDeal);
  }
  setMarketTradeOfferSnapshot("latest", sessionId, normalizedDeal);

  state.marketTradeDealDraft = normalizeTradeDealDraft(normalizedDeal, state.selectedTeamId, otherTeamId);
  ensurePrefillAssetInDraft(state.marketTradeDealDraft, otherTeamId, prefillPlayerId);

  if (!els.marketTradeDealModal) return;
  els.marketTradeDealModal.classList.remove("hidden");
  document.body.classList.add("is-modal-open");
  state.tradeDealModalOpen = true;

  if (els.marketTradeDealSession) {
    els.marketTradeDealSession.textContent = `세션: ${sessionId || "-"}`;
  }
  if (els.marketTradeDealMeta) {
    const teamName = TEAM_FULL_NAMES[otherTeamId] || otherTeamId || "-";
    const autoEnd = activeSession?.auto_end || null;
    const autoEnded = String(autoEnd?.status || "").toUpperCase() === "ENDED";
    const statusText = autoEnded ? "AI가 협상 종료" : "협상 가능";
    els.marketTradeDealMeta.textContent = `상대팀: ${teamName} / 상태: ${statusText}`;
  }
  setTradeDealSubmitPending(false);
  setTradeDealEditorMessage("");
  const requestCtx = beginScopedRequest("dealPlayerPool", { sessionId });
  await loadTradeDealPlayerPools(otherTeamId, { requestCtx });
  if (!shouldApplyResponseForScope("dealPlayerPool", requestCtx.requestId, requestCtx.sessionId, { requireModalOpen: true })) {
    return;
  }

  renderTradeDealEditor();
  syncTradeActionButtonStates();
  transitionMarketTradeSessionFsm("ready", {
    sessionId,
    reason: "openTradeDealEditorFromSession:ready",
    strict: false,
  });
}

function normalizeTeamIdUpper(value) {
  return String(value || "").toUpperCase();
}

function getTradeContractViolationSeenStore() {
  if (!state.marketTradeContractViolationSeen || typeof state.marketTradeContractViolationSeen !== "object") {
    state.marketTradeContractViolationSeen = {};
  }
  return state.marketTradeContractViolationSeen;
}

function buildTradeContractViolationKey(violation) {
  const payload = violation && typeof violation === "object" ? violation : {};
  const sessionId = String(payload?.session_id || "row-session-missing");
  const endpoint = String(payload?.endpoint || "");
  const assetKind = String(payload?.asset_kind || "");
  const assetRef = String(payload?.asset_ref || "");
  const missingFields = Array.isArray(payload?.missing_fields)
    ? [...payload.missing_fields].map((item) => String(item || "")).sort().join(",")
    : "";
  const direction = String(payload?.direction || "");
  return [sessionId, endpoint, assetKind, assetRef, missingFields, direction].join("|");
}

function pushTradeContractViolation(violation) {
  const payload = violation && typeof violation === "object" ? { ...violation } : {};
  if (!Array.isArray(state.marketTradeContractViolations)) {
    state.marketTradeContractViolations = [];
  }
  const dedupeKey = buildTradeContractViolationKey(payload);
  const seenStore = getTradeContractViolationSeenStore();
  if (dedupeKey && seenStore[dedupeKey]) return;
  if (dedupeKey) seenStore[dedupeKey] = true;

  const record = {
    ...payload,
    detected_at: new Date().toISOString(),
    screen: "market_trade_inbox",
  };
  state.marketTradeContractViolations.push(record);
  try {
    reportTradeContractViolation(record);
  } catch {
    // no-op
  }
}

function buildContractViolationBadge(message, violation) {
  pushTradeContractViolation(violation);
  const text = String(message || "계약 필수 필드 누락").trim() || "계약 필수 필드 누락";
  return `<span class="market-trade-asset-contract-violation">[계약오류] ${text}</span>`;
}

function getInboxPlayerDirectory() {
  if (!state.marketTradeInboxPlayerDirectory || typeof state.marketTradeInboxPlayerDirectory !== "object") {
    state.marketTradeInboxPlayerDirectory = {};
  }
  return state.marketTradeInboxPlayerDirectory;
}

async function hydrateMarketTradeInboxPlayerDirectory(rows) {
  const userTeamId = normalizeTeamIdUpper(state.selectedTeamId);
  const teamIds = new Set([userTeamId]);
  (rows || []).forEach((row) => {
    const other = normalizeTeamIdUpper(row?.other_team_id);
    if (other) teamIds.add(other);
  });

  const directory = getInboxPlayerDirectory();
  await Promise.all(Array.from(teamIds)
    .filter(Boolean)
    .map(async (teamId) => {
      try {
        const detail = await fetchTeamDetail(teamId, { force: false, staleWhileRevalidate: true });
        const roster = Array.isArray(detail?.roster) ? detail.roster : [];
        roster.forEach((player) => {
          const pid = String(player?.player_id || "");
          if (!pid) return;
          directory[pid] = {
            name: String(player?.name || "").trim() || pid,
            pos: String(player?.pos || "").trim(),
            salary: player?.salary,
          };
        });
      } catch {
        // no-op: directory is optional auxiliary cache only.
      }
    }));
}

function resolveDealAssetReceiver(deal, senderTeamId, asset) {
  const explicit = normalizeTeamIdUpper(asset?.to_team);
  if (explicit) return explicit;
  const teams = Array.isArray(deal?.teams) ? deal.teams.map((teamId) => normalizeTeamIdUpper(teamId)).filter(Boolean) : [];
  if (teams.length === 2) {
    return teams.find((teamId) => teamId !== senderTeamId) || "";
  }
  return "";
}

function splitDealAssetsForInbox(deal, userTeamId, otherTeamId) {
  const outgoing = [];
  const incoming = [];
  const legs = deal?.legs && typeof deal.legs === "object" ? deal.legs : {};

  Object.entries(legs).forEach(([fromTeamIdRaw, assetsRaw]) => {
    const fromTeamId = normalizeTeamIdUpper(fromTeamIdRaw);
    const assets = Array.isArray(assetsRaw) ? assetsRaw : [];
    assets.forEach((asset) => {
      const receiver = resolveDealAssetReceiver(deal, fromTeamId, asset);
      const kind = String(asset?.kind || "").toLowerCase();
      if (!kind) return;

      if (fromTeamId === userTeamId || receiver === otherTeamId) outgoing.push({ ...asset, direction: "outgoing" });
      if (fromTeamId === otherTeamId || receiver === userTeamId) incoming.push({ ...asset, direction: "incoming" });
    });
  });

  return { outgoing, incoming };
}

function formatInboxAssetText(asset, { teamNameById = {}, playerDirectory = {}, sessionId = "" } = {}) {
  const kind = String(asset?.kind || "").toLowerCase();
  if (kind === "player") {
    const pid = String(asset?.player_id || "");
    const player = playerDirectory[pid] || {};
    const dtoName = String(asset?.display_name || "").trim();
    const dtoPos = String(asset?.pos || "").trim();
    const fallbackName = String(player?.name || "").trim();
    const fallbackPos = String(player?.pos || "").trim();
    const name = dtoName || fallbackName;
    const pos = dtoPos || fallbackPos;
    if (!dtoName || !dtoPos) {
      const missing = [];
      if (!dtoName) missing.push("display_name");
      if (!dtoPos) missing.push("pos");
      const badge = buildContractViolationBadge("선수 스냅샷 필드 누락", {
        endpoint: "market.render.asset.player",
        session_id: String(sessionId || "row-session-missing"),
        asset_kind: "player",
        asset_ref: pid,
        missing_fields: missing,
        direction: String(asset?.direction || ""),
      });
      return `[선수] ${badge}`;
    }
    const salary = player?.salary != null ? ` · ${formatMoney(player.salary)}` : "";
    return `[선수] ${name}${pos ? ` (${pos})` : ""}${salary}`;
  }
  if (kind === "pick") {
    const pickId = String(asset?.pick_id || "").trim();
    const missing = [];
    const yearNum = Number(asset?.year);
    const roundNum = Number(asset?.round);
    const originalTeam = normalizeTeamIdUpper(asset?.original_team);
    const ownerTeam = normalizeTeamIdUpper(asset?.owner_team);
    if (!pickId) missing.push("pick_id");
    if (!Number.isFinite(yearNum)) missing.push("year");
    if (!Number.isFinite(roundNum) || roundNum <= 0) missing.push("round");
    if (!originalTeam) missing.push("original_team");
    if (!ownerTeam) missing.push("owner_team");
    if (missing.length) {
      const badge = buildContractViolationBadge("픽 스냅샷 필드 누락", {
        endpoint: "market.render.asset.pick",
        session_id: String(sessionId || "row-session-missing"),
        asset_kind: "pick",
        asset_ref: pickId,
        missing_fields: missing,
        direction: String(asset?.direction || ""),
      });
      return `[픽] ${badge}`;
    }
    const ownerTeamName = teamNameById[ownerTeam] || ownerTeam;
    const pickText = strictFormatPickLabel({ year: yearNum, round: roundNum, teamName: ownerTeamName, includeTeam: Boolean(ownerTeamName) });
    const originText = originalTeam && ownerTeam && originalTeam !== ownerTeam
      ? ` · ORG ${teamNameById[originalTeam] || originalTeam}`
      : "";
    const protectionText = formatProtectionSummary(asset?.protection);
    const protectedBadge = protectionText && protectionText !== "Unprotected" ? ` · ${protectionText}` : "";
    return `[픽] ${pickText}${originText}${protectedBadge}`;
  }
  if (kind === "swap") {
    return `[스왑] ${formatSwapAssetLabel({ year: asset?.year, round: asset?.round, pickA: asset?.pick_id_a, pickB: asset?.pick_id_b })}`;
  }
  if (kind === "fixed_asset") {
    return `[고정자산] ${formatFixedAssetLabel({ label: asset?.label, draftYear: asset?.draft_year, sourcePickId: asset?.source_pick_id, assetId: asset?.asset_id })}`;
  }
  return "[자산] 기타 자산";
}

function getModalPlayerDirectory() {
  const directory = {};
  const myPlayers = Array.isArray(state.marketTradeAssetPool?.myTeam?.players) ? state.marketTradeAssetPool.myTeam.players : [];
  const otherPlayers = Array.isArray(state.marketTradeAssetPool?.otherTeam?.players) ? state.marketTradeAssetPool.otherTeam.players : [];
  [...myPlayers, ...otherPlayers].forEach((player) => {
    const pid = String(player?.player_id || "");
    if (!pid) return;
    directory[pid] = {
      name: String(player?.display_name || player?.name || "").trim() || pid,
      pos: String(player?.pos || "").trim(),
      salary: player?.salary,
    };
  });
  return directory;
}

function setMarketTradeOfferSnapshot(type, sessionId, deal) {
  const payload = {
    sessionId: sessionId ? String(sessionId) : null,
    deal: deal && typeof deal === "object" ? deal : null,
    capturedAt: new Date().toISOString(),
  };
  if (type === "initial") state.marketTradeInitialOfferSnapshot = payload;
  if (type === "latest") state.marketTradeLatestOfferSnapshot = payload;
}

function getActivePackageDeal() {
  const draftDeal = state.marketTradeDealDraft;
  if (draftDeal && typeof draftDeal === "object") {
    return normalizeTradeDealDraft(draftDeal, state.selectedTeamId, state.marketTradeActiveSession?.other_team_id);
  }
  const latestDeal = state.marketTradeLatestOfferSnapshot?.deal;
  if (latestDeal && typeof latestDeal === "object") {
    return normalizeTradeDealDraft(latestDeal, state.selectedTeamId, state.marketTradeActiveSession?.other_team_id);
  }
  const initialDeal = state.marketTradeInitialOfferSnapshot?.deal;
  if (initialDeal && typeof initialDeal === "object") {
    return normalizeTradeDealDraft(initialDeal, state.selectedTeamId, state.marketTradeActiveSession?.other_team_id);
  }
  return createEmptyTradeDealDraft(state.selectedTeamId, state.marketTradeActiveSession?.other_team_id);
}

function renderTradeDealTopPackage() {
  const otherTeamId = normalizeTeamIdUpper(state.marketTradeActiveSession?.other_team_id);
  const myTeamId = normalizeTeamIdUpper(state.selectedTeamId);

  applyTeamLogo(els.marketTradeDealOtherTeamLogo, otherTeamId);
  applyTeamLogo(els.marketTradeDealMyTeamLogo, myTeamId);
  if (els.marketTradeDealOtherTeamName) els.marketTradeDealOtherTeamName.textContent = getTeamDisplayName(otherTeamId);
  if (els.marketTradeDealMyTeamName) els.marketTradeDealMyTeamName.textContent = getTeamDisplayName(myTeamId);

  const packageDeal = getActivePackageDeal();
  const { outgoing, incoming } = splitDealAssetsForInbox(packageDeal, myTeamId, otherTeamId);
  const playerDirectory = getModalPlayerDirectory();
  const teamNameById = {
    [myTeamId]: getTeamDisplayName(myTeamId),
    [otherTeamId]: getTeamDisplayName(otherTeamId),
  };

  if (els.marketTradeDealPackageOtherList) {
    els.marketTradeDealPackageOtherList.innerHTML = incoming.length
      ? incoming.map((asset) => `<li>${formatInboxAssetText(asset, { teamNameById, playerDirectory, sessionId: state.marketTradeActiveSession?.session_id })}</li>`).join("")
      : "<li>제공 자산 없음</li>";
  }
  if (els.marketTradeDealPackageMyList) {
    els.marketTradeDealPackageMyList.innerHTML = outgoing.length
      ? outgoing.map((asset) => `<li>${formatInboxAssetText(asset, { teamNameById, playerDirectory, sessionId: state.marketTradeActiveSession?.session_id })}</li>`).join("")
      : "<li>요청 자산 없음</li>";
  }
}

function getDealTabState(side) {
  return side === "my" ? state.marketTradeDealTabs?.myTeamAssetTab : state.marketTradeDealTabs?.otherTeamAssetTab;
}

function setDealTabState(side, kind) {
  if (!state.marketTradeDealTabs || typeof state.marketTradeDealTabs !== "object") {
    state.marketTradeDealTabs = { myTeamAssetTab: "player", otherTeamAssetTab: "player" };
  }
  const normalizedKind = ["player", "pick", "swap", "fixed_asset"].includes(String(kind)) ? String(kind) : "player";
  if (side === "my") state.marketTradeDealTabs.myTeamAssetTab = normalizedKind;
  if (side === "other") state.marketTradeDealTabs.otherTeamAssetTab = normalizedKind;
}

function renderDealTabsForSide(side) {
  const tabs = side === "my"
    ? [
      [els.marketTradeDealTabMyPlayer, els.marketTradeDealPanelMyPlayer, "player"],
      [els.marketTradeDealTabMyPick, els.marketTradeDealPanelMyPick, "pick"],
      [els.marketTradeDealTabMySwap, els.marketTradeDealPanelMySwap, "swap"],
      [els.marketTradeDealTabMyFixedAsset, els.marketTradeDealPanelMyFixedAsset, "fixed_asset"],
    ]
    : [
      [els.marketTradeDealTabOtherPlayer, els.marketTradeDealPanelOtherPlayer, "player"],
      [els.marketTradeDealTabOtherPick, els.marketTradeDealPanelOtherPick, "pick"],
      [els.marketTradeDealTabOtherSwap, els.marketTradeDealPanelOtherSwap, "swap"],
      [els.marketTradeDealTabOtherFixedAsset, els.marketTradeDealPanelOtherFixedAsset, "fixed_asset"],
    ];

  const active = getDealTabState(side) || "player";
  tabs.forEach(([btn, panel, kind]) => {
    const selected = active === kind;
    btn?.setAttribute("aria-selected", selected ? "true" : "false");
    btn?.classList.toggle("is-active", selected);
    panel?.classList.toggle("hidden", !selected);
  });
}

function renderTradeDealTabPanel(panelEl, rows, teamId, playerDirectory, teamNameById) {
  if (!panelEl) return;
  if (!rows.length) {
    panelEl.innerHTML = '<p class="subtitle">표시할 자산이 없습니다.</p>';
    return;
  }

  panelEl.innerHTML = `
    <ul class="market-trade-deal-list-inner">
      ${rows.map((row) => `<li>${row.html}</li>`).join("")}
    </ul>
  `;

  panelEl.querySelectorAll("button[data-deal-toggle-kind]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      const kind = btn.getAttribute("data-deal-toggle-kind");
      const id = btn.getAttribute("data-deal-id");
      if (!kind || !id) return;
      try {
        if (kind === "player") toggleAsset(teamId, { kind: "player", player_id: id });
        if (kind === "pick") {
          const pickRow = rows.find((entry) => String(entry.id) === String(id)) || {};
          const pick = pickRow.raw || {};
          const input = panelEl.querySelector(`input[data-pick-protection-editor='${id}']`);
          const canEditProtection = Boolean(pick?.can_edit_protection);
          let protectionPayload = pick?.protection ?? null;
          if (canEditProtection && input) {
            const parsed = parseProtectionInput(input.value || "");
            if (!parsed.ok) throw new Error(parsed.error || "보호조건 입력값이 유효하지 않습니다.");
            protectionPayload = parsed.value;
          }
          toggleAsset(teamId, {
            kind: "pick",
            pick_id: id,
            ...(protectionPayload != null ? { protection: protectionPayload } : {}),
          });
        }
        if (kind === "swap") {
          const swap = rows.find((entry) => String(entry.id) === String(id))?.raw || {};
          toggleAsset(teamId, { kind: "swap", swap_id: id, pick_id_a: swap?.pick_id_a, pick_id_b: swap?.pick_id_b });
        }
        if (kind === "fixed_asset") toggleAsset(teamId, { kind: "fixed_asset", asset_id: id });
        renderTradeDealEditor();
      } catch (e) {
        setTradeDealEditorMessage(e?.message || "자산 처리 중 오류가 발생했습니다.");
      }
    });
  });
}

function computeSwapIdFromPair(pickA, pickB) {
  const ids = [String(pickA || "").trim(), String(pickB || "").trim()].filter(Boolean).sort();
  if (ids.length !== 2) return "";
  return `SWAP_${ids[0]}__${ids[1]}`;
}

function buildSwapCandidateRows(teamId, existingSwaps, myPool, otherPool) {
  const teamU = normalizeTeamIdUpper(teamId);
  const existingMap = new Map(
    (existingSwaps || [])
      .map((swap) => {
        const swapId = String(swap?.swap_id || "").trim() || computeSwapIdFromPair(swap?.pick_id_a, swap?.pick_id_b);
        return [swapId, swap];
      })
      .filter(([id]) => id),
  );

  const ownPicks = (Array.isArray(myPool?.picks) ? myPool.picks : []).map((p) => ({ ...p, owner_team: teamU }));
  const opponentPicks = (Array.isArray(otherPool?.picks) ? otherPool.picks : [])
    .filter((p) => normalizeTeamIdUpper(p?.owner_team) !== teamU)
    .map((p) => ({ ...p }));

  const candidates = [];
  ownPicks.forEach((pickA) => {
    opponentPicks.forEach((pickB) => {
      const sameYearRound = Number(pickA?.year) === Number(pickB?.year) && Number(pickA?.round) === Number(pickB?.round);
      if (!sameYearRound) return;
      const swapId = computeSwapIdFromPair(pickA?.pick_id, pickB?.pick_id);
      if (!swapId || existingMap.has(swapId)) return;
      candidates.push({
        swap_id: swapId,
        year: pickA?.year,
        round: pickA?.round,
        pick_id_a: pickA?.pick_id,
        pick_id_b: pickB?.pick_id,
        candidate: true,
      });
    });
  });

  const unique = new Map();
  candidates.forEach((row) => {
    if (!unique.has(row.swap_id)) unique.set(row.swap_id, row);
  });
  return Array.from(unique.values());
}

function buildTabRowsForTeam(kind, pool, teamId, teamNameById, playerDirectory, context = {}) {
  const rows = [];
  const teamU = normalizeTeamIdUpper(teamId);

  if (kind === "player") {
    const players = Array.isArray(pool?.players) ? pool.players : [];
    players.forEach((player) => {
      const pid = String(player?.player_id || "");
      if (!pid) return;
      const selected = hasAsset(teamId, { kind: "player", player_id: pid });
      const playerAsset = {
        kind: "player",
        player_id: pid,
        display_name: String(player?.display_name || player?.name || "").trim(),
        pos: String(player?.pos || "").trim(),
      };
      rows.push({
        id: pid,
        raw: player,
        html: `<span>${formatInboxAssetText(playerAsset, { teamNameById, playerDirectory })}</span><button type="button" class="btn btn-secondary" data-deal-toggle-kind="player" data-deal-id="${pid}">${selected ? "제거" : "추가"}</button>`,
      });
    });
  }

  if (kind === "pick") {
    const picks = Array.isArray(pool?.picks) ? pool.picks : [];
    picks.forEach((pick) => {
      const pickId = String(pick?.pick_id || "");
      if (!pickId) return;
      const selected = hasAsset(teamId, { kind: "pick", pick_id: pickId });
      const protectionText = formatProtectionSummary(pick?.protection);
      const canEditProtection = normalizeTeamIdUpper(pick?.owner_team) === normalizeTeamIdUpper(pick?.original_team);
      let label = "";
      try {
        label = strictFormatPickLabel({ year: pick?.year, round: pick?.round, teamName: teamNameById[teamU], includeTeam: true });
      } catch {
        label = buildContractViolationBadge("픽 스냅샷 필드 누락", {
          endpoint: "market.render.dealTab.pick",
          session_id: String(state.marketTradeActiveSession?.session_id || "row-session-missing"),
          asset_kind: "pick",
          asset_ref: pickId,
          missing_fields: ["year", "round"],
          direction: "editor",
        });
      }
      const protectionValue = pick?.protection ? JSON.stringify(pick.protection) : "";
      const protectionBadge = protectionText && protectionText !== "Unprotected"
        ? `<span class="market-trade-asset-badge market-trade-asset-badge-protected">보호픽</span>`
        : "";
      const protectionEditor = canEditProtection
        ? `<input type="text" class="ui-input" data-pick-protection-editor='${pickId}' placeholder='{"type":"TOP","value":5}' value='${protectionValue}' />`
        : `<span class="subtitle">보호조건 편집 불가</span>`;
      rows.push({
        id: pickId,
        raw: { ...pick, can_edit_protection: canEditProtection },
        html: `<div><span>[픽] ${label}${protectionText && protectionText !== "Unprotected" ? ` · ${protectionText}` : ""} ${protectionBadge}</span><div class="market-trade-pick-editor">${protectionEditor}</div></div><button type="button" class="btn btn-secondary" data-deal-toggle-kind="pick" data-deal-id="${pickId}">${selected ? "제거" : "추가"}</button>`,
      });
    });
  }

  if (kind === "swap") {
    const swaps = Array.isArray(pool?.swaps) ? pool.swaps : [];
    const candidates = buildSwapCandidateRows(teamU, swaps, pool, context?.counterPool || {});

    swaps.forEach((swap) => {
      const swapId = String(swap?.swap_id || "");
      if (!swapId) return;
      const selected = hasAsset(teamId, { kind: "swap", swap_id: swapId, pick_id_a: swap?.pick_id_a, pick_id_b: swap?.pick_id_b });
      rows.push({
        id: swapId,
        raw: { ...swap, candidate: false },
        html: `<span>[스왑] ${formatSwapAssetLabel({ year: swap?.year, round: swap?.round, pickA: swap?.pick_id_a, pickB: swap?.pick_id_b })} <span class="market-trade-asset-badge">보유</span></span><button type="button" class="btn btn-secondary" data-deal-toggle-kind="swap" data-deal-id="${swapId}">${selected ? "제거" : "추가"}</button>`,
      });
    });

    candidates.forEach((swap) => {
      const swapId = String(swap?.swap_id || "");
      const selected = hasAsset(teamId, { kind: "swap", swap_id: swapId, pick_id_a: swap?.pick_id_a, pick_id_b: swap?.pick_id_b });
      rows.push({
        id: swapId,
        raw: swap,
        html: `<span>[스왑] ${formatSwapAssetLabel({ year: swap?.year, round: swap?.round, pickA: swap?.pick_id_a, pickB: swap?.pick_id_b })} <span class="market-trade-asset-badge market-trade-asset-badge-candidate">생성 가능</span></span><button type="button" class="btn btn-secondary" data-deal-toggle-kind="swap" data-deal-id="${swapId}">${selected ? "제거" : "추가"}</button>`,
      });
    });
  }

  if (kind === "fixed_asset") {
    const fixedAssets = Array.isArray(pool?.fixedAssets) ? pool.fixedAssets : [];
    fixedAssets.forEach((fixed) => {
      const assetId = String(fixed?.asset_id || "");
      if (!assetId) return;
      const selected = hasAsset(teamId, { kind: "fixed_asset", asset_id: assetId });
      rows.push({
        id: assetId,
        raw: fixed,
        html: `<span>[고정자산] ${formatFixedAssetLabel({ label: fixed?.label, draftYear: fixed?.draft_year, sourcePickId: fixed?.source_pick_id, assetId })}</span><button type="button" class="btn btn-secondary" data-deal-toggle-kind="fixed_asset" data-deal-id="${assetId}">${selected ? "제거" : "추가"}</button>`,
      });
    });
  }
  return rows;
}

function renderTradeDealAssetTabs() {
  const myTeamId = normalizeTeamIdUpper(state.selectedTeamId);
  const otherTeamId = normalizeTeamIdUpper(state.marketTradeActiveSession?.other_team_id);
  const teamNameById = {
    [myTeamId]: getTeamDisplayName(myTeamId),
    [otherTeamId]: getTeamDisplayName(otherTeamId),
  };
  const playerDirectory = getModalPlayerDirectory();

  const myKind = getDealTabState("my") || "player";
  const otherKind = getDealTabState("other") || "player";

  renderDealTabsForSide("my");
  renderDealTabsForSide("other");

  renderTradeDealTabPanel(
    els.marketTradeDealPanelMyPlayer,
    buildTabRowsForTeam("player", state.marketTradeAssetPool?.myTeam || {}, myTeamId, teamNameById, playerDirectory, { counterPool: state.marketTradeAssetPool?.otherTeam || {} }),
    myTeamId,
    playerDirectory,
    teamNameById,
  );
  renderTradeDealTabPanel(
    els.marketTradeDealPanelMyPick,
    buildTabRowsForTeam("pick", state.marketTradeAssetPool?.myTeam || {}, myTeamId, teamNameById, playerDirectory, { counterPool: state.marketTradeAssetPool?.otherTeam || {} }),
    myTeamId,
    playerDirectory,
    teamNameById,
  );
  renderTradeDealTabPanel(
    els.marketTradeDealPanelMySwap,
    buildTabRowsForTeam("swap", state.marketTradeAssetPool?.myTeam || {}, myTeamId, teamNameById, playerDirectory, { counterPool: state.marketTradeAssetPool?.otherTeam || {} }),
    myTeamId,
    playerDirectory,
    teamNameById,
  );
  renderTradeDealTabPanel(
    els.marketTradeDealPanelMyFixedAsset,
    buildTabRowsForTeam("fixed_asset", state.marketTradeAssetPool?.myTeam || {}, myTeamId, teamNameById, playerDirectory, { counterPool: state.marketTradeAssetPool?.otherTeam || {} }),
    myTeamId,
    playerDirectory,
    teamNameById,
  );

  renderTradeDealTabPanel(
    els.marketTradeDealPanelOtherPlayer,
    buildTabRowsForTeam("player", state.marketTradeAssetPool?.otherTeam || {}, otherTeamId, teamNameById, playerDirectory, { counterPool: state.marketTradeAssetPool?.myTeam || {} }),
    otherTeamId,
    playerDirectory,
    teamNameById,
  );
  renderTradeDealTabPanel(
    els.marketTradeDealPanelOtherPick,
    buildTabRowsForTeam("pick", state.marketTradeAssetPool?.otherTeam || {}, otherTeamId, teamNameById, playerDirectory, { counterPool: state.marketTradeAssetPool?.myTeam || {} }),
    otherTeamId,
    playerDirectory,
    teamNameById,
  );
  renderTradeDealTabPanel(
    els.marketTradeDealPanelOtherSwap,
    buildTabRowsForTeam("swap", state.marketTradeAssetPool?.otherTeam || {}, otherTeamId, teamNameById, playerDirectory, { counterPool: state.marketTradeAssetPool?.myTeam || {} }),
    otherTeamId,
    playerDirectory,
    teamNameById,
  );
  renderTradeDealTabPanel(
    els.marketTradeDealPanelOtherFixedAsset,
    buildTabRowsForTeam("fixed_asset", state.marketTradeAssetPool?.otherTeam || {}, otherTeamId, teamNameById, playerDirectory, { counterPool: state.marketTradeAssetPool?.myTeam || {} }),
    otherTeamId,
    playerDirectory,
    teamNameById,
  );

  // keep tab state touched to avoid lint for derived vars
  void myKind;
  void otherKind;
}

function renderMarketTradeInbox() {
  const groupsEl = els.marketTradeInboxGroups;
  const emptyEl = els.marketTradeInboxEmpty;
  const summaryEl = els.marketTradeInboxSummary;
  if (!groupsEl || !emptyEl || !summaryEl) return;

  const grouped = Array.isArray(state.marketTradeInboxGrouped) ? state.marketTradeInboxGrouped : [];
  const totalRows = Array.isArray(state.marketTradeInboxRows) ? state.marketTradeInboxRows.length : 0;

  summaryEl.textContent = `총 ${totalRows}건 · ${grouped.length}개 팀`;
  groupsEl.innerHTML = "";

  if (!grouped.length) {
    emptyEl.classList.remove("hidden");
    return;
  }
  emptyEl.classList.add("hidden");

  const template = els.marketTradeInboxCardTemplate;
  const playerDirectory = getInboxPlayerDirectory();
  const teamNameById = Object.fromEntries(Object.keys(TEAM_FULL_NAMES).map((id) => [id, getTeamDisplayName(id)]));

  grouped.forEach((group) => {
    const section = document.createElement("section");
    section.className = "market-trade-inbox-group";
    const teamId = normalizeTeamIdUpper(group?.other_team_id || "-");
    const teamName = getTeamDisplayName(teamId);
    const items = Array.isArray(group?.rows) ? group.rows : [];
    section.innerHTML = `
      <h4>${teamName} · ${items.length}건</h4>
      <ul class="market-trade-inbox-list"></ul>
    `;
    const list = section.querySelector(".market-trade-inbox-list");

    items.forEach((row) => {
      const sessionId = row?.session_id || "-";
      const deal = normalizeInboxOfferDeal(row, state.selectedTeamId, row?.other_team_id);
      const { outgoing, incoming } = splitDealAssetsForInbox(
        deal,
        normalizeTeamIdUpper(state.selectedTeamId),
        normalizeTeamIdUpper(row?.other_team_id),
      );
      const createdOrUpdated = row?.created_at || row?.updated_at || "-";
      const dateText = String(createdOrUpdated).slice(5, 10).replace("-", "/") || "--/--";

      const li = document.createElement("li");
      li.className = "market-trade-inbox-item";

      if (template instanceof HTMLTemplateElement) {
        const fragment = template.content.cloneNode(true);
        const article = fragment.querySelector('[data-market-trade-role="card"]');
        const logoEl = fragment.querySelector('#market-trade-inbox-card-logo');
        const outgoingList = fragment.querySelector('#market-trade-inbox-card-outgoing-list');
        const incomingList = fragment.querySelector('#market-trade-inbox-card-incoming-list');
        const dateEl = fragment.querySelector('#market-trade-inbox-card-date');
        const openBtn = fragment.querySelector('#market-trade-inbox-card-open');
        const rejectBtn = fragment.querySelector('#market-trade-inbox-card-reject');

        if (article) {
          article.setAttribute('data-session-id', String(sessionId));
        }
        applyTeamLogo(logoEl, teamId);

        if (outgoingList) {
          outgoingList.innerHTML = outgoing.length
            ? outgoing.map((asset) => `<li>${formatInboxAssetText(asset, { teamNameById, playerDirectory, sessionId: row?.session_id })}</li>`).join('')
            : '<li>요청 자산 없음</li>';
        }
        if (incomingList) {
          incomingList.innerHTML = incoming.length
            ? incoming.map((asset) => `<li>${formatInboxAssetText(asset, { teamNameById, playerDirectory, sessionId: row?.session_id })}</li>`).join('')
            : '<li>제공 자산 없음</li>';
        }
        if (dateEl) dateEl.textContent = dateText;

        if (openBtn instanceof HTMLButtonElement) {
          openBtn.dataset.tradeAction = "open-session";
          openBtn.dataset.sessionId = String(sessionId);
        }
        if (rejectBtn instanceof HTMLButtonElement) {
          rejectBtn.dataset.tradeAction = "reject-session";
          rejectBtn.dataset.sessionId = String(sessionId);
        }

        openBtn?.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          openTradeInboxSession(row).catch((e) => alert(e.message));
        });
        rejectBtn?.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          rejectTradeInboxSession(row).catch((e) => alert(e.message));
        });

        li.appendChild(fragment);
      } else {
        // fallback legacy rendering
        li.innerHTML = `
          <div class="market-trade-inbox-item-main">
            <strong>세션 ${sessionId}</strong>
            <span>${teamName}</span>
            <span>제안일 ${dateText}</span>
          </div>
          <div class="market-trade-inbox-item-actions">
            <button type="button" class="btn btn-secondary" data-trade-action="open-session" data-session-id="${sessionId}">협상</button>
            <button type="button" class="btn btn-secondary" data-trade-action="reject-session" data-session-id="${sessionId}">거절</button>
          </div>
        `;
        const openBtn = li.querySelector('button[data-trade-action="open-session"]');
        openBtn?.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          openTradeInboxSession(row).catch((e) => alert(e.message));
        });
        const rejectBtn = li.querySelector('button[data-trade-action="reject-session"]');
        rejectBtn?.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          rejectTradeInboxSession(row).catch((e) => alert(e.message));
        });
      }

      list?.appendChild(li);
    });
    groupsEl.appendChild(section);
  });

  syncTradeActionButtonStates();
}

function groupInboxRowsByOtherTeam(rows) {
  const bucket = new Map();
  rows.forEach((row) => {
    const teamId = String(row?.other_team_id || "UNKNOWN").toUpperCase();
    if (!bucket.has(teamId)) bucket.set(teamId, []);
    bucket.get(teamId).push(row);
  });

  const groups = Array.from(bucket.entries()).map(([other_team_id, teamRows]) => ({
    other_team_id,
    rows: [...teamRows].sort((a, b) => {
      const tA = Date.parse(a?.updated_at || "") || 0;
      const tB = Date.parse(b?.updated_at || "") || 0;
      return tB - tA;
    }),
  }));

  return groups.sort((a, b) => {
    const aLatest = Date.parse(a?.rows?.[0]?.updated_at || "") || 0;
    const bLatest = Date.parse(b?.rows?.[0]?.updated_at || "") || 0;
    return bLatest - aLatest;
  });
}

function shouldReloadMarketTradeInbox(force = false) {
  if (force) return true;
  if (!state.marketTradeInboxLastLoadedAt) return true;
  return Date.now() - state.marketTradeInboxLastLoadedAt > MARKET_TRADE_INBOX_CACHE_TTL_MS;
}

async function loadMarketTradeInbox({ force = false, reason = "" } = {}) {
  if (!shouldReloadMarketTradeInbox(force)) {
    renderMarketTradeInbox();
    return;
  }

  const previousRows = Array.isArray(state.marketTradeInboxRows) ? [...state.marketTradeInboxRows] : [];
  const previousGroups = Array.isArray(state.marketTradeInboxGrouped) ? [...state.marketTradeInboxGrouped] : [];
  const requestCtx = beginScopedRequest("tradeInbox", { sessionId: getActiveTradeSessionId() });
  const previousViolations = Array.isArray(state.marketTradeContractViolations) ? [...state.marketTradeContractViolations] : [];
  const previousViolationSeen = state.marketTradeContractViolationSeen && typeof state.marketTradeContractViolationSeen === "object"
    ? { ...state.marketTradeContractViolationSeen }
    : {};

  state.marketTradeContractViolations = [];
  state.marketTradeContractViolationSeen = {};

  setMarketTradeInboxLoading(true);
  renderMarketTradeInbox();
  try {
    const payload = await fetchTradeNegotiationInbox({
      teamId: state.selectedTeamId,
      status: "ACTIVE",
      phase: "OPEN",
      signal: requestCtx.signal,
    });

    if (!shouldApplyResponseForScope("tradeInbox", requestCtx.requestId, requestCtx.sessionId, { requireModalOpen: false })) {
      console.debug("[trade-inbox] late response dropped", { reason, requestId: requestCtx.requestId });
      return;
    }

    const rows = Array.isArray(payload?.rows)
      ? payload.rows
      : Array.isArray(payload?.sessions)
        ? payload.sessions
        : [];
    const responseViolations = Array.isArray(payload?.contract_violations) ? payload.contract_violations : [];
    responseViolations.forEach((violation) => pushTradeContractViolation({
      ...violation,
      endpoint: String(violation?.endpoint || "/api/trade/negotiation/inbox"),
    }));
    rows.forEach((row) => {
      const rowViolations = Array.isArray(row?.contract_violations) ? row.contract_violations : [];
      rowViolations.forEach((violation) => pushTradeContractViolation({
        ...violation,
        session_id: String(violation?.session_id || row?.session_id || ""),
        endpoint: String(violation?.endpoint || "/api/trade/negotiation/inbox"),
      }));
    });
    hydrateMarketTradeInboxPlayerDirectory(rows).catch(() => {});
    state.marketTradeInboxRows = rows;
    state.marketTradeInboxGrouped = groupInboxRowsByOtherTeam(rows);
    state.marketTradeInboxLastLoadedAt = Date.now();
    renderMarketTradeInbox();
  } catch (error) {
    if (String(error?.message || "") === "REQUEST_ABORTED") return;
    state.marketTradeInboxRows = previousRows;
    state.marketTradeInboxGrouped = previousGroups;
    state.marketTradeContractViolations = previousViolations;
    state.marketTradeContractViolationSeen = previousViolationSeen;
    renderMarketTradeInbox();
    if (els.marketTradeInboxSummary) {
      els.marketTradeInboxSummary.textContent = toFriendlyRuleMessage(error?.message || "제안 목록을 불러오지 못했습니다.");
    }
    throw new Error(toFriendlyRuleMessage(error?.message || "제안 목록을 불러오지 못했습니다."));
  } finally {
    setMarketTradeInboxLoading(false);
  }
}

async function openTradeInboxSession(row) {
  const sessionId = row?.session_id;
  if (!sessionId) throw new Error("협상을 열 세션 ID가 없습니다.");
  if (!state.selectedTeamId) throw new Error("먼저 팀을 선택해주세요.");
  if (isActionPending("open-session", sessionId)) return;

  const idempotencyKey = createTradeIdempotencyKey("open-session", sessionId);
  setActionPending("open-session", sessionId, true, idempotencyKey);
  syncTradeActionButtonStates();

  transitionMarketTradeSessionFsm("opening", {
    sessionId,
    reason: "openTradeInboxSession:start",
    strict: false,
  });

  setLoading(true, "협상 세션을 여는 중...");
  try {
    const requestCtx = beginScopedRequest("openSession", { sessionId });
    const result = await openTradeNegotiationSession({
      sessionId,
      teamId: state.selectedTeamId,
      signal: requestCtx.signal,
      idempotencyKey,
    });

    if (!shouldApplyResponseForScope("openSession", requestCtx.requestId, requestCtx.sessionId, { requireModalOpen: false })) {
      return;
    }

    await openTradeDealEditorFromSession(result?.session || result || {}, {
      sessionId,
      otherTeamId: row?.other_team_id,
    });
    await runScopedTask("tradeInboxRefresh", () => loadMarketTradeInbox({ force: true, reason: "post-open" }));
  } catch (error) {
    const msg = String(error?.message || "");
    if (msg === "REQUEST_ABORTED") return;
    if (msg.includes("NEGOTIATION_ENDED_BY_AI")) {
      const currentRows = Array.isArray(state.marketTradeInboxRows) ? state.marketTradeInboxRows : [];
      state.marketTradeInboxRows = currentRows.filter((item) => String(item?.session_id || "") !== String(sessionId));
      state.marketTradeInboxGrouped = groupInboxRowsByOtherTeam(state.marketTradeInboxRows);
      renderMarketTradeInbox();
      state.marketTradeInboxLastLoadedAt = Date.now();
      await runScopedTask("tradeInboxRefresh", () => loadMarketTradeInbox({ force: true, reason: "ai-ended" }));
    }
    throw new Error(toFriendlyRuleMessage(msg || "협상 세션을 열지 못했습니다."));
  } finally {
    setActionPending("open-session", sessionId, false);
    syncTradeActionButtonStates();
    setLoading(false);
  }
}

async function rejectTradeInboxSession(row) {
  const sessionId = row?.session_id;
  if (!sessionId) throw new Error("거절할 세션 ID가 없습니다.");
  if (!state.selectedTeamId) throw new Error("먼저 팀을 선택해주세요.");
  if (isActionPending("reject-session", sessionId)) return;

  const idempotencyKey = createTradeIdempotencyKey("reject-session", sessionId);
  setActionPending("reject-session", sessionId, true, idempotencyKey);
  syncTradeActionButtonStates();

  const previousRows = Array.isArray(state.marketTradeInboxRows) ? [...state.marketTradeInboxRows] : [];
  const previousGroups = Array.isArray(state.marketTradeInboxGrouped) ? [...state.marketTradeInboxGrouped] : [];

  // optimistic remove
  state.marketTradeInboxRows = previousRows.filter((item) => String(item?.session_id || "") !== String(sessionId));
  state.marketTradeInboxGrouped = groupInboxRowsByOtherTeam(state.marketTradeInboxRows);
  renderMarketTradeInbox();

  setLoading(true, "제안을 거절하는 중...");
  try {
    const requestCtx = beginScopedRequest("rejectSession", { sessionId });
    await rejectTradeNegotiationSession({
      sessionId,
      teamId: state.selectedTeamId,
      reason: "USER_REJECT",
      signal: requestCtx.signal,
      idempotencyKey,
    });
    state.marketTradeInboxLastLoadedAt = Date.now();
    await runScopedTask("tradeInboxRefresh", () => loadMarketTradeInbox({ force: true, reason: "post-reject" }));
  } catch (error) {
    if (String(error?.message || "") === "REQUEST_ABORTED") return;
    // rollback on failure
    state.marketTradeInboxRows = previousRows;
    state.marketTradeInboxGrouped = previousGroups;
    renderMarketTradeInbox();
    throw error;
  } finally {
    setActionPending("reject-session", sessionId, false);
    syncTradeActionButtonStates();
    setLoading(false);
  }
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

function renderTradeBlockMineSummary(rows) {
  if (!els.marketTradeBlockMineSummary) return;
  const count = rows.length;
  els.marketTradeBlockMineSummary.textContent = count
    ? `내 팀 등록 선수 ${count}명`
    : "등록된 선수가 없습니다.";
}

function closeTradeNegotiationModal() {
  if (!els.marketTradeModal) return;
  els.marketTradeModal.classList.add("hidden");
  document.body.classList.remove("is-modal-open");
  syncTradeModalStartButtonState();
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
  syncTradeModalStartButtonState();
}

async function startTradeNegotiationFromModal() {
  const playerId = state.marketTradeModalPlayerId;
  const otherTeamId = state.marketTradeModalOtherTeamId;
  if (!state.selectedTeamId) throw new Error("먼저 팀을 선택해주세요.");
  if (!playerId || !otherTeamId) throw new Error("협상을 시작할 선수 정보를 찾을 수 없습니다.");

  const pendingKey = getTradeModalStartPendingKey();
  if (isActionPending("start-from-modal", pendingKey)) return;
  const idempotencyKey = createTradeIdempotencyKey("start-from-modal", pendingKey);
  setActionPending("start-from-modal", pendingKey, true, idempotencyKey);
  syncTradeModalStartButtonState();

  try {
    const requestCtx = beginScopedRequest("startSession", { sessionId: pendingKey });
    const out = await startTradeNegotiationSession({
      userTeamId: state.selectedTeamId,
      otherTeamId,
      defaultOfferPrivacy: "PRIVATE",
      signal: requestCtx.signal,
      idempotencyKey,
    });

    if (!shouldApplyResponseForScope("startSession", requestCtx.requestId, requestCtx.sessionId, { requireModalOpen: false })) {
      return;
    }

    state.marketTradeNegotiationSession = out?.session || null;
    closeTradeNegotiationModal();
    await openTradeDealEditorFromSession(state.marketTradeNegotiationSession || {}, {
      otherTeamId,
      prefillPlayerId: playerId,
    });
  } finally {
    setActionPending("start-from-modal", pendingKey, false);
    syncTradeModalStartButtonState();
  }
}

function bindTradeDealTabEvents() {
  const bindings = [
    [els.marketTradeDealTabMyPlayer, "my", "player"],
    [els.marketTradeDealTabMyPick, "my", "pick"],
    [els.marketTradeDealTabMySwap, "my", "swap"],
    [els.marketTradeDealTabMyFixedAsset, "my", "fixed_asset"],
    [els.marketTradeDealTabOtherPlayer, "other", "player"],
    [els.marketTradeDealTabOtherPick, "other", "pick"],
    [els.marketTradeDealTabOtherSwap, "other", "swap"],
    [els.marketTradeDealTabOtherFixedAsset, "other", "fixed_asset"],
  ];
  bindings.forEach(([btn, side, kind]) => {
    btn?.addEventListener("click", (event) => {
      event.preventDefault();
      setDealTabState(side, kind);
      renderTradeDealEditor();
    });
  });
}

function closeTradeDealEditorModal() {
  if (!els.marketTradeDealModal) return;
  const closingSessionId = getActiveTradeSessionId();
  els.marketTradeDealModal.classList.add("hidden");
  document.body.classList.remove("is-modal-open");
  state.tradeDealModalOpen = false;
  abortScopedRequest("dealPlayerPool");
  abortScopedRequest("submitDeal");
  transitionMarketTradeSessionFsm("closed", {
    sessionId: closingSessionId,
    reason: "closeTradeDealEditorModal",
    strict: false,
  });
  resetTradeDealModalContext({ includeSession: true, includeTabs: false });
  setTradeDealSubmitPending(false);
  syncTradeActionButtonStates();
}

function renderTradeBlockRows(rows) {
  if (!els.marketTradeBlockBody) return;
  els.marketTradeBlockBody.innerHTML = "";

  if (!rows.length) {
    els.marketTradeBlockBody.innerHTML = '<tr><td class="schedule-empty" colspan="9">트레이드 블록 등록 선수가 없습니다.</td></tr>';
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

function renderTradeBlockMineRows(rows) {
  if (!els.marketTradeBlockMineBody) return;
  els.marketTradeBlockMineBody.innerHTML = "";

  if (!rows.length) {
    els.marketTradeBlockMineBody.innerHTML = '<tr><td class="schedule-empty" colspan="8">등록된 선수가 없습니다.</td></tr>';
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
      <td><button type="button" class="btn btn-secondary market-trade-unlist-btn" data-market-trade-unlist="${row.player_id}">해제</button></td>
    `;

    tr.addEventListener("click", (ev) => {
      if (ev.target instanceof HTMLElement && ev.target.closest("[data-market-trade-unlist]")) return;
      state.marketSelectedPlayerId = row.player_id;
      loadPlayerDetail(row.player_id, {
        context: "market-trade-block",
        backTarget: "market",
      }).catch((e) => alert(e.message));
    });

    const unlistBtn = tr.querySelector("[data-market-trade-unlist]");
    unlistBtn?.addEventListener("click", (ev) => {
      ev.stopPropagation();
      setLoading(true, "트레이드 블록에서 해제하는 중...");
      unlistPlayerFromTradeBlock(row.player_id)
        .then((out) => {
          if (out?.removed === false) {
            alert("이미 해제된 선수입니다. 목록을 갱신합니다.");
          }
          return loadTradeBlockMineList();
        })
        .catch((e) => alert(e.message || "트레이드 블록 해제에 실패했습니다."))
        .finally(() => setLoading(false));
    });

    els.marketTradeBlockMineBody.appendChild(tr);
  });
}

function closeTradeBlockRosterModal() {
  if (!els.marketTradeBlockRosterModal) return;
  state.marketTradeBlockRosterModalOpen = false;
  els.marketTradeBlockRosterModal.classList.add("hidden");
  document.body.classList.remove("is-modal-open");
}

function renderTradeBlockRosterModalList(rows) {
  if (!els.marketTradeBlockRosterList) return;
  const selected = String(state.marketTradeBlockSelectedRosterPlayerId || "");
  const listed = new Set((state.marketTradeBlockMyRows || []).map((row) => String(row?.player_id || "")));
  if (!rows.length) {
    els.marketTradeBlockRosterList.innerHTML = '<p class="college-inline-meta">로스터 데이터가 없습니다.</p>';
    return;
  }

  els.marketTradeBlockRosterList.innerHTML = rows.map((row) => {
    const pid = String(row?.player_id || "");
    const disabled = listed.has(pid);
    return `
      <button type="button" class="college-player-option ${selected === pid ? "is-selected" : ""}" data-market-roster-pid="${pid}" ${disabled ? "disabled" : ""}>
        <strong>${row?.name || "-"}</strong>
        <span>${row?.pos || "-"} · OVR ${Math.round(num(row?.overall, 0))}</span>
        ${disabled ? '<em>이미 등록됨</em>' : ""}
      </button>
    `;
  }).join("");
}

async function loadMyTeamRosterForTradeBlock() {
  if (!state.selectedTeamId) throw new Error("먼저 팀을 선택해주세요.");
  const detail = await fetchTeamDetail(state.selectedTeamId, { force: true, staleWhileRevalidate: false });
  const roster = (detail?.roster || []).map((row) => ({
    ...row,
    player_id: String(row?.player_id || ""),
  })).filter((row) => row.player_id);
  state.marketTradeBlockRosterRows = roster;
  return roster;
}

async function openTradeBlockRosterModal() {
  if (!els.marketTradeBlockRosterModal) return;
  state.marketTradeBlockSelectedRosterPlayerId = null;
  const rosterRows = await loadMyTeamRosterForTradeBlock();
  renderTradeBlockRosterModalList(rosterRows);
  state.marketTradeBlockRosterModalOpen = true;
  els.marketTradeBlockRosterModal.classList.remove("hidden");
  document.body.classList.add("is-modal-open");
}

async function listPlayerToTradeBlock(playerId) {
  if (!state.selectedTeamId) throw new Error("먼저 팀을 선택해주세요.");
  if (!playerId) throw new Error("등록할 선수를 선택해주세요.");
  await fetchJson("/api/trade/block/list", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      team_id: state.selectedTeamId,
      player_id: String(playerId),
      visibility: "PUBLIC",
      reason_code: "MANUAL",
    }),
  });
  invalidateTradeBlockCaches();
}

async function unlistPlayerFromTradeBlock(playerId) {
  if (!state.selectedTeamId) throw new Error("먼저 팀을 선택해주세요.");
  if (!playerId) throw new Error("해제할 선수 정보를 찾을 수 없습니다.");

  const out = await fetchJson("/api/trade/block/unlist", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      team_id: state.selectedTeamId,
      player_id: String(playerId),
      reason_code: "MANUAL_REMOVE",
    }),
  });
  invalidateTradeBlockCaches();
  return out;
}

async function loadTradeBlockMineList({ requestCtx = null } = {}) {
  if (!state.selectedTeamId) throw new Error("먼저 팀을 선택해주세요.");
  const payload = await fetchCachedJson({
    key: getTradeBlockCacheKeyMine(state.selectedTeamId),
    url: `/api/trade/block?active_only=true&visibility=PUBLIC&limit=300&sort=priority_desc&team_id=${encodeURIComponent(state.selectedTeamId)}`,
    options: { signal: requestCtx?.signal },
    ttlMs: MARKET_TRADE_BLOCK_CACHE_TTL_MS,
    staleWhileRevalidate: true,
  });
  if (requestCtx && !shouldApplyResponseForScope("tradeBlockMineList", requestCtx.requestId, requestCtx.sessionId, { requireModalOpen: false })) return;
  const rows = (payload?.rows || []).map((row) => ({
    ...row,
    height_in: getFirstNumber(row?.height_in),
    weight_lb: getFirstNumber(row?.weight_lb),
    salary: getFirstNumber(row?.salary),
  }));
  state.marketTradeBlockMyRows = rows;
  const displayRows = getTradeBlockDisplayRows(rows);
  renderTradeBlockMineSummary(displayRows);
  renderTradeBlockMineRows(displayRows);
}

async function openTradeBlockScope(scope) {
  switchTradeBlockScope(scope);
  if (state.marketSubTab !== "trade-block") return;
  if (state.marketTradeBlockScope === "mine") {
    setLoading(true, "내 팀 트레이드 블록 명단을 불러오는 중...");
    try {
      const requestCtx = beginScopedRequest("tradeBlockMineList", { sessionId: getActiveTradeSessionId() });
      await loadTradeBlockMineList({ requestCtx });
    } finally {
      setLoading(false);
    }
  }
}

async function loadTradeBlockList({ requestCtx = null } = {}) {
  const payload = await fetchCachedJson({
    key: getTradeBlockCacheKeyAll(),
    url: "/api/trade/block?active_only=true&visibility=PUBLIC&limit=300&sort=priority_desc",
    options: { signal: requestCtx?.signal },
    ttlMs: MARKET_TRADE_BLOCK_CACHE_TTL_MS,
    staleWhileRevalidate: true,
  });
  if (requestCtx && !shouldApplyResponseForScope("tradeBlockList", requestCtx.requestId, requestCtx.sessionId, { requireModalOpen: false })) return;
  const rows = (payload?.rows || []).map((row) => ({
    ...row,
    height_in: getFirstNumber(row?.height_in),
    weight_lb: getFirstNumber(row?.weight_lb),
    salary: getFirstNumber(row?.salary),
  }));
  const selectedTeamId = String(state.selectedTeamId || "").toUpperCase();
  const filteredRows = rows.filter((row) => String(row?.team_id || "").toUpperCase() !== selectedTeamId);
  state.marketTradeBlockRows = filteredRows;
  const displayRows = getTradeBlockDisplayRows(filteredRows);
  renderTradeBlockSummary(displayRows);
  renderTradeBlockRows(displayRows);
}

async function loadFaList({ requestCtx = null } = {}) {
  const payload = await fetchJson("/api/contracts/free-agents?limit=300", { signal: requestCtx?.signal });
  if (requestCtx && !shouldApplyResponseForScope("faList", requestCtx.requestId, requestCtx.sessionId, { requireModalOpen: false })) return;
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
  return runScopedTask("marketSubTab", async () => {
  switchMarketSubTab(tab);
  if (!["fa", "trade-block", "trade-inbox"].includes(state.marketSubTab)) return;

  if (state.marketSubTab === "trade-inbox") {
    await loadMarketTradeInbox();
    return;
  }

  setLoading(true, state.marketSubTab === "fa" ? "FA 명단을 불러오는 중..." : "트레이드 블록 명단을 불러오는 중...");
  try {
    if (state.marketSubTab === "fa") {
      const requestCtx = beginScopedRequest("faList", { sessionId: getActiveTradeSessionId() });
      await loadFaList({ requestCtx });
    }
    else {
      switchTradeBlockScope(state.marketTradeBlockScope || "other");
      if ((state.marketTradeBlockScope || "other") === "mine") {
        const requestCtx = beginScopedRequest("tradeBlockMineList", { sessionId: getActiveTradeSessionId() });
        await loadTradeBlockMineList({ requestCtx });
      } else {
        const requestCtx = beginScopedRequest("tradeBlockList", { sessionId: getActiveTradeSessionId() });
        await loadTradeBlockList({ requestCtx });
      }
    }
  } finally {
    setLoading(false);
  }
  });
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
  return runScopedTask("showMarketScreen", async () => {
  if (!state.selectedTeamId) {
    alert("먼저 팀을 선택해주세요.");
    return;
  }

  abortAllMarketTradeRequests();
  state.marketScreenActive = true;
  state.playerDetailBackTarget = "market";
  switchMarketSubTab(state.marketSubTab || "fa");
  switchTradeBlockScope(state.marketTradeBlockScope || "other");
  activateScreen(els.marketScreen);
  await openMarketSubTab(state.marketSubTab || "fa");

  if (!state.marketTradeModalBound) {
    els.marketTradeModalCancel?.addEventListener("click", closeTradeNegotiationModal);
    els.marketTradeModalBackdrop?.addEventListener("click", closeTradeNegotiationModal);
    els.marketTradeModalStart?.addEventListener("click", async () => {
      setLoading(true, "트레이드 협상 세션을 생성하는 중...");
      try {
        await startTradeNegotiationFromModal();
      } catch (e) {
        if (String(e?.message || "") !== "REQUEST_ABORTED") alert(e.message);
      } finally {
        setLoading(false);
      }
    });

    els.marketTradeDealBackdrop?.addEventListener("click", closeTradeDealEditorModal);
    els.marketTradeDealCancel?.addEventListener("click", closeTradeDealEditorModal);
    els.marketTradeDealReject?.addEventListener("click", async () => {
      const sessionId = getActiveTradeSessionId();
      if (!sessionId) return;
      if (isActionPending("reject-from-modal", sessionId)) return;
      const idempotencyKey = createTradeIdempotencyKey("reject-from-modal", sessionId);
      setActionPending("reject-from-modal", sessionId, true, idempotencyKey);
      syncTradeActionButtonStates();
      setLoading(true, "협상을 거절하는 중...");
      try {
        const requestCtx = beginScopedRequest("rejectFromModal", { sessionId });
        await rejectTradeNegotiationSession({
          sessionId,
          teamId: state.selectedTeamId,
          reason: "USER_REJECT",
          signal: requestCtx.signal,
          idempotencyKey,
        });
        closeTradeDealEditorModal();
        await runScopedTask("tradeInboxRefresh", () => loadMarketTradeInbox({ force: true, reason: "modal-reject" }));
      } catch (e) {
        if (String(e?.message || "") !== "REQUEST_ABORTED") {
          alert(toFriendlyRuleMessage(e?.message || "협상 거절에 실패했습니다."));
        }
      } finally {
        setActionPending("reject-from-modal", sessionId, false);
        syncTradeActionButtonStates();
        setLoading(false);
      }
    });
    bindTradeDealTabEvents();
    els.marketTradeDealSubmit?.addEventListener("click", async () => {
      if (state.marketTradeUi?.submitPending) return;
      setTradeDealSubmitPending(true);
      setLoading(true, "제안을 제출하는 중...");
      try {
        await submitTradeDealDraft();
      } catch (e) {
        const friendly = toFriendlyCommitErrorMessage(e?.message || "");
        setTradeDealEditorMessage(friendly);
        alert(friendly);
      } finally {
        setTradeDealSubmitPending(false);
        setLoading(false);
      }
    });

    state.marketTradeModalBound = true;
  }

  if (!state.marketTradeBlockRosterModalBound) {
    els.marketTradeBlockRegisterBtn?.addEventListener("click", () => {
      setLoading(true, "로스터를 불러오는 중...");
      openTradeBlockRosterModal().catch((e) => alert(e.message)).finally(() => setLoading(false));
    });
    els.marketTradeBlockRosterModalCancel?.addEventListener("click", closeTradeBlockRosterModal);
    els.marketTradeBlockRosterModalBackdrop?.addEventListener("click", closeTradeBlockRosterModal);
    els.marketTradeBlockRosterList?.addEventListener("click", (event) => {
      const option = event.target instanceof HTMLElement ? event.target.closest("[data-market-roster-pid]") : null;
      if (!option) return;
      const pid = String(option.getAttribute("data-market-roster-pid") || "");
      if (!pid || option.hasAttribute("disabled")) return;
      state.marketTradeBlockSelectedRosterPlayerId = pid;
      renderTradeBlockRosterModalList(state.marketTradeBlockRosterRows || []);
    });
    els.marketTradeBlockRosterModalConfirm?.addEventListener("click", () => {
      setLoading(true, "트레이드 블록에 등록하는 중...");
      listPlayerToTradeBlock(state.marketTradeBlockSelectedRosterPlayerId)
        .then(() => loadTradeBlockMineList())
        .then(() => {
          closeTradeBlockRosterModal();
          alert("트레이드 블록 등록이 완료되었습니다.");
        })
        .catch((e) => alert(e.message || "트레이드 블록 등록에 실패했습니다."))
        .finally(() => setLoading(false));
    });
    state.marketTradeBlockRosterModalBound = true;
  }
  });
}

export {
  switchMarketSubTab,
  switchTradeBlockScope,
  openTradeBlockScope,
  showMarketScreen,
  loadFaList,
  renderFaRows,
  getFaDisplayRows,
  openMarketSubTab,
  loadMarketTradeInbox,
  handleMarketDetailAction,
};
