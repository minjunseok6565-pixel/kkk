import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import {
  fetchJson,
  fetchTradeNegotiationInbox,
  startTradeNegotiationSession,
  openTradeNegotiationSession,
  rejectTradeNegotiationSession,
  commitTradeNegotiationSession,
  submitCommittedTradeDeal,
  fetchStateSummary,
  setLoading,
} from "../../core/api.js";
import { num } from "../../core/guards.js";
import { formatHeightIn, formatMoney, formatWeightLb } from "../../core/format.js";
import { TEAM_FULL_NAMES } from "../../core/constants/teams.js";
import { loadPlayerDetail } from "../myteam/playerDetail.js";
import { fetchTeamDetail, invalidateTeamDetailCache } from "../team/teamDetailCache.js";

const MARKET_TRADE_INBOX_CACHE_TTL_MS = 30 * 1000;

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

function buildBaseDraftFromSession(session, userTeamId, otherTeamId) {
  const draftDeal = session?.draft_deal;
  const lastOffer = session?.last_offer?.deal || session?.last_offer;
  if (draftDeal) return normalizeTradeDealDraft(draftDeal, userTeamId, otherTeamId);
  if (lastOffer) return normalizeTradeDealDraft(lastOffer, userTeamId, otherTeamId);
  return createEmptyTradeDealDraft(userTeamId, otherTeamId);
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
  if (asset.kind === "swap") return `swap:${asset.swap_id || ""}`;
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
  renderTradeDealAssetList(els.marketTradeDealAssetsMyTeam, myTeamId, state.marketTradeAssetPool?.myTeam || {});
  renderTradeDealAssetList(els.marketTradeDealAssetsOtherTeam, otherTeamId, state.marketTradeAssetPool?.otherTeam || {});
  renderMarketTradeDealDraftPreview();
}

function extractTeamTradeAssets(summaryPayload, ownerTeamId) {
  const teamId = String(ownerTeamId || "").toUpperCase();
  const snapshot = summaryPayload?.db_snapshot?.trade_assets || {};
  const picksRaw = Array.isArray(snapshot?.draft_picks) ? snapshot.draft_picks : [];
  const swapsRaw = Array.isArray(snapshot?.swap_rights) ? snapshot.swap_rights : [];
  const fixedRaw = Array.isArray(snapshot?.fixed_assets) ? snapshot.fixed_assets : [];

  const picks = picksRaw
    .filter((item) => String(item?.owner_team_id || item?.owner_team || "").toUpperCase() === teamId)
    .map((item) => ({ pick_id: item?.pick_id, protection: item?.protection || null }));
  const swaps = swapsRaw
    .filter((item) => String(item?.owner_team_id || item?.owner_team || "").toUpperCase() === teamId)
    .map((item) => ({ swap_id: item?.swap_id, pick_id_a: item?.pick_id_a, pick_id_b: item?.pick_id_b }));
  const fixedAssets = fixedRaw
    .filter((item) => String(item?.owner_team_id || item?.owner_team || "").toUpperCase() === teamId)
    .map((item) => ({ asset_id: item?.asset_id, source_pick_id: item?.source_pick_id || null }));

  return { picks, swaps, fixedAssets };
}

async function loadTradeDealPlayerPools(otherTeamId) {
  const myTeamId = state.selectedTeamId;
  if (!myTeamId || !otherTeamId) return;
  const [myTeamDetail, otherTeamDetail, summary] = await Promise.all([
    fetchTeamDetail(myTeamId, { force: true }),
    fetchTeamDetail(otherTeamId, { force: true }),
    fetchStateSummary(),
  ]);
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
  if (msg.includes("NEGOTIATION_NOT_ACTIVE")) return "협상이 이미 종료되었습니다.";
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
  submitBtn.setAttribute("aria-busy", nextPending ? "true" : "false");
  submitBtn.textContent = nextPending ? "제출 중..." : defaultLabel;
}

async function refreshMarketTradeAfterAccepted(otherTeamId) {
  const myTeamId = state.selectedTeamId;
  if (myTeamId) invalidateTeamDetailCache(myTeamId);
  if (otherTeamId) invalidateTeamDetailCache(otherTeamId);

  await Promise.all([
    loadFaList(),
    loadTradeBlockList(),
    loadMarketTradeInbox({ force: true }),
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

  const result = await commitTradeNegotiationSession({
    sessionId,
    deal: state.marketTradeDealDraft,
    offerPrivacy: "PRIVATE",
    exposeToMedia: false,
  });

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
    const otherTeamId = String(state.marketTradeActiveSession?.other_team_id || "").toUpperCase();
    const submitOut = await submitCommittedTradeDeal({ dealId, force: true });
    if (els.marketTradeDealStatus) {
      els.marketTradeDealStatus.innerHTML = '<p class="subtitle">제출 결과: 수락 · 거래 반영 완료</p>';
    }
    setTradeDealEditorMessage(submitOut?.message || "수락된 제안이 실제 트레이드로 반영되었습니다.");
    await refreshMarketTradeAfterAccepted(otherTeamId);
  }
  return result;
}

async function openTradeDealEditorFromSession(session, fallback = {}) {
  const activeSession = session || {};
  const fallbackOtherTeamId = String(fallback?.otherTeamId || "").toUpperCase();
  const otherTeamId = String(activeSession?.other_team_id || fallbackOtherTeamId || "").toUpperCase();
  const prefillPlayerId = fallback?.prefillPlayerId || null;

  state.marketTradeActiveSession = activeSession;
  state.marketTradeDealDraft = buildBaseDraftFromSession(activeSession, state.selectedTeamId, otherTeamId);
  ensurePrefillAssetInDraft(state.marketTradeDealDraft, otherTeamId, prefillPlayerId);

  if (!els.marketTradeDealModal) return;
  els.marketTradeDealModal.classList.remove("hidden");
  document.body.classList.add("is-modal-open");

  if (els.marketTradeDealSession) {
    els.marketTradeDealSession.textContent = `세션: ${activeSession?.session_id || fallback?.sessionId || "-"}`;
  }
  if (els.marketTradeDealMeta) {
    const teamName = TEAM_FULL_NAMES[otherTeamId] || otherTeamId || "-";
    const expiresAt = activeSession?.expires_at || activeSession?.valid_until || "-";
    els.marketTradeDealMeta.textContent = `상대팀: ${teamName} / 만료일: ${expiresAt}`;
  }
  setTradeDealSubmitPending(false);
  setTradeDealEditorMessage("");
  await loadTradeDealPlayerPools(otherTeamId);
  renderTradeDealEditor();
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

  grouped.forEach((group) => {
    const section = document.createElement("section");
    section.className = "market-trade-inbox-group";
    const teamId = String(group?.other_team_id || "-").toUpperCase();
    const teamName = TEAM_FULL_NAMES[teamId] || teamId;
    const items = Array.isArray(group?.rows) ? group.rows : [];
    section.innerHTML = `
      <h4>${teamName} · ${items.length}건</h4>
      <ul class="market-trade-inbox-list"></ul>
    `;
    const list = section.querySelector(".market-trade-inbox-list");
    items.forEach((row) => {
      const li = document.createElement("li");
      li.className = "market-trade-inbox-item";
      const sessionId = row?.session_id || "-";
      const updatedAt = row?.updated_at || "-";
      const status = String(row?.status || "-").toUpperCase();
      const phase = String(row?.phase || "-").toUpperCase();
      li.innerHTML = `
        <div class="market-trade-inbox-item-main">
          <strong>세션 ${sessionId}</strong>
          <span>상태 ${status}/${phase}</span>
          <span>업데이트 ${updatedAt}</span>
        </div>
        <div class="market-trade-inbox-item-actions">
          <button type="button" class="btn btn-secondary" data-inbox-action="open" data-session-id="${sessionId}">협상</button>
          <button type="button" class="btn btn-secondary" data-inbox-action="reject" data-session-id="${sessionId}">거절</button>
        </div>
      `;
      const openBtn = li.querySelector('button[data-inbox-action="open"]');
      openBtn?.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        openTradeInboxSession(row).catch((e) => alert(e.message));
      });
      const rejectBtn = li.querySelector('button[data-inbox-action="reject"]');
      rejectBtn?.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        rejectTradeInboxSession(row).catch((e) => alert(e.message));
      });
      list?.appendChild(li);
    });
    groupsEl.appendChild(section);
  });
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

async function loadMarketTradeInbox({ force = false } = {}) {
  if (!shouldReloadMarketTradeInbox(force)) {
    renderMarketTradeInbox();
    return;
  }

  setMarketTradeInboxLoading(true);
  renderMarketTradeInbox();
  try {
    const payload = await fetchTradeNegotiationInbox({
      teamId: state.selectedTeamId,
      status: "ACTIVE",
      phase: "OPEN",
    });
    const rows = Array.isArray(payload?.rows)
      ? payload.rows
      : Array.isArray(payload?.sessions)
        ? payload.sessions
        : [];
    state.marketTradeInboxRows = rows;
    state.marketTradeInboxGrouped = groupInboxRowsByOtherTeam(rows);
    state.marketTradeInboxLastLoadedAt = Date.now();
    renderMarketTradeInbox();
  } finally {
    setMarketTradeInboxLoading(false);
  }
}

async function openTradeInboxSession(row) {
  const sessionId = row?.session_id;
  if (!sessionId) throw new Error("협상을 열 세션 ID가 없습니다.");
  if (!state.selectedTeamId) throw new Error("먼저 팀을 선택해주세요.");

  setLoading(true, "협상 세션을 여는 중...");
  try {
    const result = await openTradeNegotiationSession({
      sessionId,
      teamId: state.selectedTeamId,
    });
    await openTradeDealEditorFromSession(result?.session || result || {}, {
      sessionId,
      otherTeamId: row?.other_team_id,
    });
    loadMarketTradeInbox({ force: true }).catch(() => {});
  } finally {
    setLoading(false);
  }
}

async function rejectTradeInboxSession(row) {
  const sessionId = row?.session_id;
  if (!sessionId) throw new Error("거절할 세션 ID가 없습니다.");
  if (!state.selectedTeamId) throw new Error("먼저 팀을 선택해주세요.");

  const previousRows = Array.isArray(state.marketTradeInboxRows) ? [...state.marketTradeInboxRows] : [];
  const previousGroups = Array.isArray(state.marketTradeInboxGrouped) ? [...state.marketTradeInboxGrouped] : [];

  // optimistic remove
  state.marketTradeInboxRows = previousRows.filter((item) => String(item?.session_id || "") !== String(sessionId));
  state.marketTradeInboxGrouped = groupInboxRowsByOtherTeam(state.marketTradeInboxRows);
  renderMarketTradeInbox();

  setLoading(true, "제안을 거절하는 중...");
  try {
    await rejectTradeNegotiationSession({
      sessionId,
      teamId: state.selectedTeamId,
      reason: "USER_REJECT",
    });
    state.marketTradeInboxLastLoadedAt = Date.now();
    loadMarketTradeInbox({ force: true }).catch(() => {});
  } catch (error) {
    // rollback on failure
    state.marketTradeInboxRows = previousRows;
    state.marketTradeInboxGrouped = previousGroups;
    renderMarketTradeInbox();
    throw error;
  } finally {
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

  const out = await startTradeNegotiationSession({
    userTeamId: state.selectedTeamId,
    otherTeamId,
    defaultOfferPrivacy: "PRIVATE",
  });

  state.marketTradeNegotiationSession = out?.session || null;
  closeTradeNegotiationModal();
  await openTradeDealEditorFromSession(state.marketTradeNegotiationSession || {}, {
    otherTeamId,
    prefillPlayerId: playerId,
  });
}

function closeTradeDealEditorModal() {
  if (!els.marketTradeDealModal) return;
  els.marketTradeDealModal.classList.add("hidden");
  document.body.classList.remove("is-modal-open");
  setTradeDealSubmitPending(false);
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
  if (!["fa", "trade-block", "trade-inbox"].includes(state.marketSubTab)) return;

  if (state.marketSubTab === "trade-inbox") {
    await loadMarketTradeInbox();
    return;
  }

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

    els.marketTradeDealBackdrop?.addEventListener("click", closeTradeDealEditorModal);
    els.marketTradeDealCancel?.addEventListener("click", closeTradeDealEditorModal);
    els.marketTradeDealSubmit?.addEventListener("click", () => {
      if (state.marketTradeUi?.submitPending) return;
      setTradeDealSubmitPending(true);
      setLoading(true, "제안을 제출하는 중...");
      submitTradeDealDraft()
        .catch((e) => {
          const friendly = toFriendlyCommitErrorMessage(e?.message || "");
          setTradeDealEditorMessage(friendly);
          alert(friendly);
        })
        .finally(() => {
          setTradeDealSubmitPending(false);
          setLoading(false);
        });
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
  loadMarketTradeInbox,
  handleMarketDetailAction,
};
