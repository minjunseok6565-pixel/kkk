import { els } from "../../app/dom.js";
import { state } from "../../app/state.js";
import { activateScreen } from "../../app/router.js";
import { evaluateTradeDealForTeam, fetchTradeLabTeamAssets, setLoading } from "../../core/api.js";
import { TEAM_FULL_NAMES } from "../../core/constants/teams.js";

const TEAM_IDS = Object.keys(TEAM_FULL_NAMES).sort();

function normalizeTeamId(value) {
  return String(value || "").trim().toUpperCase();
}

function getTeamDisplayName(teamId) {
  const tid = normalizeTeamId(teamId);
  return TEAM_FULL_NAMES[tid] || tid || "-";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#39;");
}

function toFiniteNumber(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function formatMetaValue(value) {
  if (value === null || value === undefined) return "-";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return String(value);
    if (Number.isInteger(value)) return String(value);
    const truncated = Math.trunc(value * 10) / 10;
    return truncated.toFixed(1);
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    if (!value.length) return "[]";
    return value.map((v) => formatMetaValue(v)).join(", ");
  }
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch (_) {
      return String(value);
    }
  }
  return String(value);
}

function renderStepMeta(meta) {
  if (!meta || typeof meta !== "object") return "";
  const entries = Object.entries(meta);
  if (!entries.length) return "";
  const items = entries.map(([k, v]) => (
    `<li class="trade-lab-step-item"><span class="trade-lab-step-code">${escapeHtml(k)}</span><span class="trade-lab-step-label">${escapeHtml(formatMetaValue(v))}</span></li>`
  )).join("");
  return `
    <details class="trade-lab-eval-details">
      <summary>Meta (${entries.length})</summary>
      <ul class="trade-lab-step-list">${items}</ul>
    </details>
  `;
}

function formatValueComponents(vc) {
  const now = toFiniteNumber(vc?.now, 0);
  const future = toFiniteNumber(vc?.future, 0);
  const total = toFiniteNumber(vc?.total, now + future);
  return `now ${now.toFixed(2)} / future ${future.toFixed(2)} / total ${total.toFixed(2)}`;
}

function renderDecisionReasons(reasons) {
  const rows = Array.isArray(reasons) ? reasons : [];
  if (!rows.length) return "<li class=\"trade-lab-step-item\"><span class=\"trade-lab-step-label\">사유 없음</span></li>";
  return rows.map((reason) => {
    const code = escapeHtml(reason?.code || "-");
    const message = escapeHtml(reason?.message || "");
    const impact = Number.isFinite(Number(reason?.impact)) ? ` · impact ${Number(reason.impact).toFixed(3)}` : "";
    return `<li class="trade-lab-step-item"><span class="trade-lab-step-code">${code}</span><span class="trade-lab-step-label">${message}${impact}</span></li>`;
  }).join("");
}

function renderStepList(steps) {
  const rows = Array.isArray(steps) ? steps : [];
  if (!rows.length) return "<li class=\"trade-lab-step-item\"><span class=\"trade-lab-step-label\">상세 step 없음</span></li>";
  return rows.map((step) => {
    const stage = escapeHtml(step?.stage || "-");
    const mode = escapeHtml(step?.mode || "-");
    const code = escapeHtml(step?.code || "-");
    const label = escapeHtml(step?.label || "");
    const factor = Number.isFinite(Number(step?.factor)) ? `factor ${Number(step.factor).toFixed(3)} · ` : "";
    const deltaText = formatValueComponents(step?.delta || {});
    const metaDetails = renderStepMeta(step?.meta);
    return `
      <li class="trade-lab-step-item">
        <span class="trade-lab-step-code">${stage}/${mode}/${code}</span>
        <span class="trade-lab-step-label">${label || "-"}</span>
        <span class="trade-lab-step-meta">${factor}${deltaText}</span>
        ${metaDetails}
      </li>
    `;
  }).join("");
}

function renderAssetEvaluationCards(values, { title }) {
  const rows = Array.isArray(values) ? values : [];
  if (!rows.length) {
    return `<div class="trade-lab-eval-empty">${escapeHtml(title)} 자산 없음</div>`;
  }
  return rows.map((row) => {
    const refId = escapeHtml(row?.ref_id || row?.asset_key || "-");
    const kind = escapeHtml(row?.kind || "-");
    const marketValue = formatValueComponents(row?.market_value || {});
    const teamValue = formatValueComponents(row?.team_value || {});
    const marketSteps = renderStepList(row?.market_steps);
    const teamSteps = renderStepList(row?.team_steps);
    return `
      <article class="trade-lab-eval-asset">
        <header>
          <strong>${refId}</strong>
          <span class="trade-lab-step-meta">${kind}</span>
        </header>
        <div class="trade-lab-eval-kv">
          <span>Market: ${marketValue}</span>
          <span>Team: ${teamValue}</span>
        </div>
        <details class="trade-lab-eval-details">
          <summary>Market Steps (${Array.isArray(row?.market_steps) ? row.market_steps.length : 0})</summary>
          <ul class="trade-lab-step-list">${marketSteps}</ul>
        </details>
        <details class="trade-lab-eval-details">
          <summary>Team Steps (${Array.isArray(row?.team_steps) ? row.team_steps.length : 0})</summary>
          <ul class="trade-lab-step-list">${teamSteps}</ul>
        </details>
      </article>
    `;
  }).join("");
}

function renderNeedsList(needs) {
  const rows = Array.isArray(needs) ? needs : [];
  if (!rows.length) return "<li class=\"trade-lab-step-item\"><span class=\"trade-lab-step-label\">needs 없음</span></li>";
  return rows.map((n) => {
    const tag = escapeHtml(n?.tag || "-");
    const weight = toFiniteNumber(n?.weight, 0).toFixed(3);
    const reason = escapeHtml(n?.reason || "");
    return `<li class="trade-lab-step-item"><span class="trade-lab-step-code">${tag} · weight ${weight}</span><span class="trade-lab-step-label">${reason}</span></li>`;
  }).join("");
}

function renderPlayerDebugCards(players) {
  const rows = Array.isArray(players) ? players : [];
  if (!rows.length) return "<div class=\"trade-lab-eval-empty\">선수 컨텍스트 없음</div>";
  return rows.map((p) => {
    const name = escapeHtml(p?.name || p?.player_id || "-");
    const pos = escapeHtml(p?.pos || "-");
    const age = toFiniteNumber(p?.age, 0).toFixed(1);
    const ovr = toFiniteNumber(p?.ovr, 0).toFixed(1);
    const salary = toFiniteNumber(p?.salary_amount, 0).toFixed(2);
    const injury = p?.meta?.injury || {};
    const injuryStatus = escapeHtml(injury?.current?.status || "UNKNOWN");
    const daysToReturn = Number.isFinite(Number(injury?.current?.days_to_return))
      ? ` · DTR ${Number(injury.current.days_to_return)}`
      : "";
    const agency = p?.meta?.agency_state || {};
    const tradeRequest = toFiniteNumber(agency?.trade_request_level, 0).toFixed(2);
    const teamFrustration = toFiniteNumber(agency?.team_frustration, 0).toFixed(2);
    return `
      <article class="trade-lab-eval-asset">
        <header>
          <strong>${name}</strong>
          <span class="trade-lab-step-meta">${pos}</span>
        </header>
        <div class="trade-lab-eval-kv">
          <span>age ${age}</span>
          <span>ovr ${ovr}</span>
          <span>salary ${salary}</span>
          <span>injury ${injuryStatus}${daysToReturn}</span>
          <span>trade_request ${tradeRequest}</span>
          <span>team_frustration ${teamFrustration}</span>
        </div>
      </article>
    `;
  }).join("");
}

function renderTeamDebugCard(debugContext) {
  const team = debugContext?.team || {};
  const ts = team?.team_situation || {};
  const dctx = team?.decision_context || {};
  const needs = ts?.needs || [];
  const signals = ts?.signals || {};
  const constraints = ts?.constraints || {};
  const valuationKnobs = dctx?.valuation_knobs || {};

  if (team?.error) {
    return `<div class="trade-lab-eval-empty">${escapeHtml(team.error)}</div>`;
  }

  return `
    <article class="trade-lab-eval-asset">
      <header>
        <strong>Team Context</strong>
        <span class="trade-lab-step-meta">${escapeHtml(ts?.team_id || debugContext?.meta?.team_id || "-")}</span>
      </header>
      <div class="trade-lab-eval-kv">
        <span>tier ${escapeHtml(ts?.competitive_tier || "-")}</span>
        <span>posture ${escapeHtml(ts?.trade_posture || "-")}</span>
        <span>horizon ${escapeHtml(ts?.time_horizon || "-")}</span>
        <span>urgency ${toFiniteNumber(ts?.urgency, 0).toFixed(3)}</span>
        <span>apron ${escapeHtml(constraints?.apron_status || "-")}</span>
        <span>cap_space ${toFiniteNumber(constraints?.cap_space, 0).toFixed(2)}</span>
        <span>win_pct ${toFiniteNumber(signals?.win_pct, 0).toFixed(3)}</span>
        <span>net_rating ${toFiniteNumber(signals?.net_rating, 0).toFixed(2)}</span>
      </div>
      <details class="trade-lab-eval-details">
        <summary>Needs (${Array.isArray(needs) ? needs.length : 0})</summary>
        <ul class="trade-lab-step-list">${renderNeedsList(needs)}</ul>
      </details>
      <details class="trade-lab-eval-details">
        <summary>Decision Context Knobs</summary>
        <ul class="trade-lab-step-list">
          <li class="trade-lab-step-item"><span class="trade-lab-step-label">w_now ${toFiniteNumber(valuationKnobs?.w_now, 0).toFixed(3)} / w_future ${toFiniteNumber(valuationKnobs?.w_future, 0).toFixed(3)}</span></li>
          <li class="trade-lab-step-item"><span class="trade-lab-step-label">pick_multiplier ${toFiniteNumber(valuationKnobs?.pick_multiplier, 0).toFixed(3)} / youth_multiplier ${toFiniteNumber(valuationKnobs?.youth_multiplier, 0).toFixed(3)}</span></li>
          <li class="trade-lab-step-item"><span class="trade-lab-step-label">fit_scale ${toFiniteNumber(valuationKnobs?.fit_scale, 0).toFixed(3)} / min_fit_threshold ${toFiniteNumber(valuationKnobs?.min_fit_threshold, 0).toFixed(3)}</span></li>
          <li class="trade-lab-step-item"><span class="trade-lab-step-label">risk_discount_scale ${toFiniteNumber(valuationKnobs?.risk_discount_scale, 0).toFixed(3)} / finance_penalty_scale ${toFiniteNumber(valuationKnobs?.finance_penalty_scale, 0).toFixed(3)}</span></li>
        </ul>
      </details>
    </article>
  `;
}

function createEmptyTradeLabState() {
  return {
    selectedTeams: { left: null, right: null },
    assetsByTeam: {},
    packageByTeam: {},
    evalResultByTeam: {},
    dealHash: "",
  };
}

function ensureTradeLabState() {
  if (!state.tradeLab || typeof state.tradeLab !== "object") {
    state.tradeLab = createEmptyTradeLabState();
  }
  if (!state.tradeLab.selectedTeams) state.tradeLab.selectedTeams = { left: null, right: null };
  if (!state.tradeLab.assetsByTeam) state.tradeLab.assetsByTeam = {};
  if (!state.tradeLab.packageByTeam) state.tradeLab.packageByTeam = {};
  if (!state.tradeLab.evalResultByTeam) state.tradeLab.evalResultByTeam = {};
  if (typeof state.tradeLab.dealHash !== "string") state.tradeLab.dealHash = "";
  return state.tradeLab;
}

function defaultRightTeam(leftTeamId) {
  const left = normalizeTeamId(leftTeamId);
  const fallback = TEAM_IDS.find((teamId) => teamId !== left);
  return fallback || "";
}

function assetKey(asset) {
  const kind = String(asset?.kind || "").toLowerCase();
  if (kind === "player") return `player:${String(asset?.player_id || "")}`;
  if (kind === "pick") return `pick:${String(asset?.pick_id || "")}`;
  return `${kind}:unknown`;
}

function buildDealPayload() {
  const tradeLab = ensureTradeLabState();
  const leftTeam = normalizeTeamId(tradeLab.selectedTeams.left);
  const rightTeam = normalizeTeamId(tradeLab.selectedTeams.right);
  const leftAssets = [...(tradeLab.packageByTeam[leftTeam] || [])];
  const rightAssets = [...(tradeLab.packageByTeam[rightTeam] || [])];
  return {
    teams: [leftTeam, rightTeam],
    legs: {
      [leftTeam]: leftAssets,
      [rightTeam]: rightAssets,
    },
    meta: { mode: "trade_lab" },
  };
}

function dealHashOf(deal) {
  return JSON.stringify(deal || {});
}

function ensurePackageListsForSelectedTeams() {
  const tradeLab = ensureTradeLabState();
  const leftTeam = normalizeTeamId(tradeLab.selectedTeams.left);
  const rightTeam = normalizeTeamId(tradeLab.selectedTeams.right);
  if (leftTeam && !Array.isArray(tradeLab.packageByTeam[leftTeam])) tradeLab.packageByTeam[leftTeam] = [];
  if (rightTeam && !Array.isArray(tradeLab.packageByTeam[rightTeam])) tradeLab.packageByTeam[rightTeam] = [];
}

function resetEvaluations() {
  const tradeLab = ensureTradeLabState();
  tradeLab.evalResultByTeam = {};
  els.tradeLabLeftResult.textContent = "";
  els.tradeLabRightResult.textContent = "";
}

function renderTeamSelectOptions() {
  const tradeLab = ensureTradeLabState();
  const leftSelected = normalizeTeamId(tradeLab.selectedTeams.left);
  const rightSelected = normalizeTeamId(tradeLab.selectedTeams.right);

  const optionHtml = TEAM_IDS.map((teamId) => `<option value="${teamId}">${getTeamDisplayName(teamId)}</option>`).join("");

  els.tradeLabLeftTeamSelect.innerHTML = optionHtml;
  els.tradeLabRightTeamSelect.innerHTML = optionHtml;

  if (leftSelected) els.tradeLabLeftTeamSelect.value = leftSelected;
  if (rightSelected) els.tradeLabRightTeamSelect.value = rightSelected;
}

function renderAssetList(targetEl, assets, { side, kind }) {
  const list = Array.isArray(assets) ? assets : [];
  if (!list.length) {
    targetEl.innerHTML = `<li class="trade-lab-asset-item"><span class="trade-lab-asset-meta">표시할 자산이 없습니다.</span></li>`;
    return;
  }

  targetEl.innerHTML = list.map((asset) => {
    if (kind === "player") {
      return `
        <li class="trade-lab-asset-item">
          <div>
            <strong>${asset.name || asset.player_id}</strong>
            <div class="trade-lab-asset-meta">${asset.pos || "-"} · OVR ${asset.ovr ?? "-"} · AGE ${asset.age ?? "-"}</div>
            <div class="trade-lab-asset-meta">SALARY ${toFiniteNumber(asset.salary, 0).toLocaleString()}</div>
          </div>
          <button
            class="trade-lab-inline-btn"
            data-trade-lab-action="add"
            data-side="${side}"
            data-kind="player"
            data-player-id="${asset.player_id}"
            data-player-name="${asset.name || ""}"
            data-player-pos="${asset.pos || ""}"
            data-player-ovr="${asset.ovr ?? ""}"
            data-player-age="${asset.age ?? ""}"
            data-player-salary="${toFiniteNumber(asset.salary, 0)}"
          >패키지 추가</button>
        </li>
      `;
    }

    return `
      <li class="trade-lab-asset-item">
        <div>
          <strong>${asset.pick_id}</strong>
          <div class="trade-lab-asset-meta">${asset.year}년 ${asset.round}R · 원소유 ${asset.original_team}</div>
        </div>
        <button class="trade-lab-inline-btn" data-trade-lab-action="add" data-side="${side}" data-kind="pick" data-pick-id="${asset.pick_id}">패키지 추가</button>
      </li>
    `;
  }).join("");
}

function renderPackageList(targetEl, packageAssets, { side }) {
  const list = Array.isArray(packageAssets) ? packageAssets : [];
  if (!list.length) {
    targetEl.innerHTML = `<li class="trade-lab-package-item"><span class="trade-lab-asset-meta">패키지가 비어 있습니다.</span></li>`;
    return;
  }

  targetEl.innerHTML = list.map((asset) => {
    const isPlayer = String(asset?.kind || "") === "player";
    const label = isPlayer ? `${asset.name || asset.player_id}` : `${asset.pick_id}`;
    const meta = isPlayer
      ? `${asset.pos || "-"} · OVR ${asset.ovr ?? "-"} · AGE ${asset.age ?? "-"}`
      : "1라운드 픽";
    const extraMeta = isPlayer ? `SALARY ${toFiniteNumber(asset.salary, 0).toLocaleString()}` : "";
    return `
      <li class="trade-lab-package-item">
        <div>
          <strong>${label}</strong>
          <div class="trade-lab-asset-meta">${meta}</div>
          ${extraMeta ? `<div class="trade-lab-asset-meta">${extraMeta}</div>` : ""}
        </div>
        <button class="trade-lab-inline-btn remove" data-trade-lab-action="remove" data-side="${side}" data-asset-key="${assetKey(asset)}">제거</button>
      </li>
    `;
  }).join("");
}

function renderEvaluationResult() {
  const tradeLab = ensureTradeLabState();
  const leftTeam = normalizeTeamId(tradeLab.selectedTeams.left);
  const rightTeam = normalizeTeamId(tradeLab.selectedTeams.right);

  const leftEval = tradeLab.evalResultByTeam[leftTeam];
  const rightEval = tradeLab.evalResultByTeam[rightTeam];

  const formatEval = (entry) => {
    if (!entry) return "";
    const verdict = escapeHtml(entry?.decision?.verdict || "-");
    const confidence = toFiniteNumber(entry?.decision?.confidence, 0.5).toFixed(3);
    const incoming = toFiniteNumber(entry?.evaluation?.incoming_total, 0).toFixed(2);
    const outgoing = toFiniteNumber(entry?.evaluation?.outgoing_total, 0).toFixed(2);
    const net = toFiniteNumber(entry?.evaluation?.net_surplus, 0).toFixed(2);
    const ratio = toFiniteNumber(entry?.evaluation?.surplus_ratio, 0).toFixed(3);
    const side = entry?.evaluation?.side || {};
    const packageSteps = renderStepList(side?.package_steps);
    const incomingCards = renderAssetEvaluationCards(side?.incoming, { title: "Incoming" });
    const outgoingCards = renderAssetEvaluationCards(side?.outgoing, { title: "Outgoing" });
    const reasons = renderDecisionReasons(entry?.decision?.reasons);
    const debugContext = entry?.debug_context || {};
    const teamDebugCard = renderTeamDebugCard(debugContext);
    const playerDebugCards = renderPlayerDebugCards(debugContext?.players);

    return `
      <article class="trade-lab-eval-card">
        <header class="trade-lab-eval-header">
          <strong>Verdict: ${verdict}</strong>
          <span class="trade-lab-step-meta">confidence ${confidence}</span>
        </header>
        <div class="trade-lab-eval-kv">
          <span>incoming_total ${incoming}</span>
          <span>outgoing_total ${outgoing}</span>
          <span>net_surplus ${net}</span>
          <span>surplus_ratio ${ratio}</span>
        </div>

        <details class="trade-lab-eval-details" open>
          <summary>Decision Reasons</summary>
          <ul class="trade-lab-step-list">${reasons}</ul>
        </details>
        <details class="trade-lab-eval-details">
          <summary>Incoming Asset Valuations (${Array.isArray(side?.incoming) ? side.incoming.length : 0})</summary>
          <div class="trade-lab-eval-asset-grid">${incomingCards}</div>
        </details>
        <details class="trade-lab-eval-details">
          <summary>Outgoing Asset Valuations (${Array.isArray(side?.outgoing) ? side.outgoing.length : 0})</summary>
          <div class="trade-lab-eval-asset-grid">${outgoingCards}</div>
        </details>
        <details class="trade-lab-eval-details">
          <summary>Package Steps (${Array.isArray(side?.package_steps) ? side.package_steps.length : 0})</summary>
          <ul class="trade-lab-step-list">${packageSteps}</ul>
        </details>
        <details class="trade-lab-eval-details">
          <summary>Team Debug Context</summary>
          <div class="trade-lab-eval-asset-grid">${teamDebugCard}</div>
        </details>
        <details class="trade-lab-eval-details">
          <summary>Player Debug Context (${Array.isArray(debugContext?.players) ? debugContext.players.length : 0})</summary>
          <div class="trade-lab-eval-asset-grid">${playerDebugCards}</div>
        </details>
      </article>
    `;
  };

  els.tradeLabLeftResult.innerHTML = formatEval(leftEval);
  els.tradeLabRightResult.innerHTML = formatEval(rightEval);
}

function updateValuationButtonState() {
  const tradeLab = ensureTradeLabState();
  const leftTeam = normalizeTeamId(tradeLab.selectedTeams.left);
  const rightTeam = normalizeTeamId(tradeLab.selectedTeams.right);

  const leftHasPackage = Array.isArray(tradeLab.packageByTeam[leftTeam]) && tradeLab.packageByTeam[leftTeam].length > 0;
  const rightHasPackage = Array.isArray(tradeLab.packageByTeam[rightTeam]) && tradeLab.packageByTeam[rightTeam].length > 0;

  els.tradeLabEvaluateLeftBtn.disabled = !leftHasPackage;
  els.tradeLabEvaluateRightBtn.disabled = !rightHasPackage;
}

function renderTradeLab() {
  const tradeLab = ensureTradeLabState();
  const leftTeam = normalizeTeamId(tradeLab.selectedTeams.left);
  const rightTeam = normalizeTeamId(tradeLab.selectedTeams.right);

  els.tradeLabLeftTitle.textContent = `${getTeamDisplayName(leftTeam)} 자산`;
  els.tradeLabRightTitle.textContent = `${getTeamDisplayName(rightTeam)} 자산`;

  const leftAssets = tradeLab.assetsByTeam[leftTeam] || { players: [], first_round_picks: [] };
  const rightAssets = tradeLab.assetsByTeam[rightTeam] || { players: [], first_round_picks: [] };

  renderAssetList(els.tradeLabLeftPlayers, leftAssets.players, { side: "left", kind: "player" });
  renderAssetList(els.tradeLabLeftPicks, leftAssets.first_round_picks, { side: "left", kind: "pick" });
  renderAssetList(els.tradeLabRightPlayers, rightAssets.players, { side: "right", kind: "player" });
  renderAssetList(els.tradeLabRightPicks, rightAssets.first_round_picks, { side: "right", kind: "pick" });

  renderPackageList(els.tradeLabLeftPackage, tradeLab.packageByTeam[leftTeam], { side: "left" });
  renderPackageList(els.tradeLabRightPackage, tradeLab.packageByTeam[rightTeam], { side: "right" });

  updateValuationButtonState();
  renderEvaluationResult();
}

async function loadAssetsForTeam(teamId) {
  const tid = normalizeTeamId(teamId);
  if (!tid) return;
  const tradeLab = ensureTradeLabState();
  const res = await fetchTradeLabTeamAssets({ teamId: tid });
  tradeLab.assetsByTeam[tid] = {
    players: Array.isArray(res?.players) ? res.players : [],
    first_round_picks: Array.isArray(res?.first_round_picks) ? res.first_round_picks : [],
    current_date: String(res?.current_date || ""),
  };
}

function resolveTeamBySide(side) {
  const tradeLab = ensureTradeLabState();
  return normalizeTeamId(side === "right" ? tradeLab.selectedTeams.right : tradeLab.selectedTeams.left);
}

function buildAssetFromDataset(dataset) {
  const kind = String(dataset.kind || "").toLowerCase();
  if (kind === "player") {
    const playerId = String(dataset.playerId || "").trim();
    if (!playerId) return null;
    return {
      kind: "player",
      player_id: playerId,
      name: String(dataset.playerName || "").trim(),
      pos: String(dataset.playerPos || "").trim(),
      ovr: dataset.playerOvr,
      age: dataset.playerAge,
      salary: toFiniteNumber(dataset.playerSalary, 0),
    };
  }
  if (kind === "pick") {
    const pickId = String(dataset.pickId || "").trim();
    if (!pickId) return null;
    return { kind: "pick", pick_id: pickId };
  }
  return null;
}

function addPackageAsset(side, asset) {
  const teamId = resolveTeamBySide(side);
  if (!teamId || !asset) return;
  const tradeLab = ensureTradeLabState();
  ensurePackageListsForSelectedTeams();
  const list = tradeLab.packageByTeam[teamId] || [];
  const key = assetKey(asset);
  if (list.some((item) => assetKey(item) === key)) return;
  tradeLab.packageByTeam[teamId] = [...list, asset];
  resetEvaluations();
  renderTradeLab();
}

function removePackageAsset(side, assetKeyToRemove) {
  const teamId = resolveTeamBySide(side);
  if (!teamId) return;
  const tradeLab = ensureTradeLabState();
  const list = tradeLab.packageByTeam[teamId] || [];
  tradeLab.packageByTeam[teamId] = list.filter((asset) => assetKey(asset) !== assetKeyToRemove);
  resetEvaluations();
  renderTradeLab();
}

async function evaluateForSide(side) {
  const teamId = resolveTeamBySide(side);
  if (!teamId) {
    alert("팀을 먼저 선택해주세요.");
    return;
  }

  const tradeLab = ensureTradeLabState();
  const deal = buildDealPayload();
  const hash = dealHashOf(deal);
  tradeLab.dealHash = hash;

  setLoading(true, `${getTeamDisplayName(teamId)} 관점 밸류에이션 계산 중...`);
  try {
    const result = await evaluateTradeDealForTeam({ deal, teamId, includeBreakdown: true });
    if (tradeLab.dealHash !== hash) return;
    tradeLab.evalResultByTeam[teamId] = result;
    renderTradeLab();
  } catch (error) {
    alert(error?.message || "밸류에이션에 실패했습니다.");
  } finally {
    setLoading(false);
  }
}

async function onTeamSelectChange(side, teamId) {
  const tradeLab = ensureTradeLabState();
  const nextTeam = normalizeTeamId(teamId);
  const otherTeam = normalizeTeamId(side === "left" ? tradeLab.selectedTeams.right : tradeLab.selectedTeams.left);

  if (!nextTeam) return;
  if (nextTeam === otherTeam) {
    alert("양 팀은 서로 달라야 합니다.");
    if (side === "left") {
      els.tradeLabLeftTeamSelect.value = normalizeTeamId(tradeLab.selectedTeams.left);
    } else {
      els.tradeLabRightTeamSelect.value = normalizeTeamId(tradeLab.selectedTeams.right);
    }
    return;
  }

  if (side === "left") tradeLab.selectedTeams.left = nextTeam;
  else tradeLab.selectedTeams.right = nextTeam;

  ensurePackageListsForSelectedTeams();
  resetEvaluations();
  setLoading(true, "팀 자산을 불러오는 중...");
  try {
    await loadAssetsForTeam(nextTeam);
    renderTradeLab();
  } catch (error) {
    alert(error?.message || "팀 자산 로딩 실패");
  } finally {
    setLoading(false);
  }
}

async function initTradeLabData() {
  const tradeLab = ensureTradeLabState();
  const defaultLeft = normalizeTeamId(tradeLab.selectedTeams.left || state.selectedTeamId || "LAL");
  const defaultRight = normalizeTeamId(tradeLab.selectedTeams.right || defaultRightTeam(defaultLeft));

  tradeLab.selectedTeams.left = defaultLeft;
  tradeLab.selectedTeams.right = defaultRight;

  ensurePackageListsForSelectedTeams();
  renderTeamSelectOptions();

  setLoading(true, "Trade Lab 초기화 중...");
  try {
    await Promise.all([loadAssetsForTeam(defaultLeft), loadAssetsForTeam(defaultRight)]);
    renderTradeLab();
  } finally {
    setLoading(false);
  }
}

function bindTradeLabDomEvents() {
  if (els.tradeLabScreen?.dataset.bound === "1") return;

  els.tradeLabScreen?.addEventListener("click", (event) => {
    const target = event.target instanceof HTMLElement ? event.target.closest("button[data-trade-lab-action]") : null;
    if (!target) return;
    const action = String(target.dataset.tradeLabAction || "");
    const side = String(target.dataset.side || "");

    if (action === "add") {
      const asset = buildAssetFromDataset(target.dataset);
      addPackageAsset(side, asset);
      return;
    }

    if (action === "remove") {
      removePackageAsset(side, String(target.dataset.assetKey || ""));
    }
  });

  els.tradeLabLeftTeamSelect?.addEventListener("change", () => {
    void onTeamSelectChange("left", els.tradeLabLeftTeamSelect.value);
  });
  els.tradeLabRightTeamSelect?.addEventListener("change", () => {
    void onTeamSelectChange("right", els.tradeLabRightTeamSelect.value);
  });

  els.tradeLabEvaluateLeftBtn?.addEventListener("click", () => {
    void evaluateForSide("left");
  });
  els.tradeLabEvaluateRightBtn?.addEventListener("click", () => {
    void evaluateForSide("right");
  });

  els.tradeLabScreen.dataset.bound = "1";
}

async function showTradeLabScreen() {
  bindTradeLabDomEvents();
  await initTradeLabData();
  activateScreen(els.tradeLabScreen);
}

export { showTradeLabScreen };
