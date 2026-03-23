import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { fetchJson } from "../../core/api.js";

const PLAYER_TRAINING_CATEGORIES = [
  "BALANCED",
  "SHOOTING",
  "FINISHING",
  "PLAYMAKING",
  "DEFENSE",
  "REBOUNDING",
  "PHYSICAL",
  "IQ",
  "POST",
];

const PLAYER_TRAINING_INTENSITIES = ["LOW", "MED", "HIGH"];

const CATEGORY_LABELS = {
  BALANCED: "균형",
  SHOOTING: "슈팅",
  FINISHING: "마무리",
  PLAYMAKING: "플레이메이킹",
  DEFENSE: "수비",
  REBOUNDING: "리바운드",
  PHYSICAL: "피지컬",
  IQ: "농구 IQ",
  POST: "포스트",
};

const INTENSITY_LABELS = {
  LOW: "낮음",
  MED: "보통",
  HIGH: "높음",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function normalizeCategory(value, { allowBlank = false } = {}) {
  const v = String(value || "").trim().toUpperCase();
  if (!v && allowBlank) return "";
  if (v && PLAYER_TRAINING_CATEGORIES.includes(v)) return v;
  return allowBlank ? "" : "BALANCED";
}

function normalizeIntensity(value) {
  const v = String(value || "").trim().toUpperCase();
  if (PLAYER_TRAINING_INTENSITIES.includes(v)) return v;
  return "MED";
}

function resetPlayerTrainingDraft() {
  state.playerTrainingDraft = {
    primary: "BALANCED",
    secondary: "",
    intensity: "MED",
  };
}

function buildPlayerTrainingOptionMarkup({ options, selected, labels, allowBlank = false }) {
  const normalizedSelected = String(selected || "").toUpperCase();
  const rows = [];
  if (allowBlank) {
    rows.push(`<option value="" ${!normalizedSelected ? "selected" : ""}>없음</option>`);
  }
  options.forEach((option) => {
    const key = String(option || "").toUpperCase();
    const label = labels[key] || key;
    const isSelected = normalizedSelected === key;
    rows.push(`<option value="${key}" ${isSelected ? "selected" : ""}>${escapeHtml(label)}</option>`);
  });
  return rows.join("");
}

function getPlayerId(row) {
  if (!row || typeof row !== "object") return "";
  const pid = row.player_id ?? row.playerId ?? "";
  return String(pid || "");
}

function getPlayerDisplayName(row) {
  if (!row || typeof row !== "object") return "-";
  return String(row.name || row.player_name || getPlayerId(row) || "-");
}

function ensurePlayerTrainingRoster() {
  const raw = Array.isArray(state.trainingRoster) ? state.trainingRoster : [];
  state.playerTrainingRoster = raw
    .filter((row) => getPlayerId(row))
    .map((row) => ({ ...row }));
}

function getSelectedPlayerRow() {
  const selectedId = String(state.playerTrainingSelectedPlayerId || "");
  if (!selectedId) return null;
  return state.playerTrainingRoster.find((row) => getPlayerId(row) === selectedId) || null;
}

async function fetchPlayerTrainingPlan(playerId, { forceReload = false } = {}) {
  const pid = String(playerId || "");
  if (!pid) return null;
  if (!forceReload && state.playerTrainingPlansByPlayerId?.[pid]) {
    return state.playerTrainingPlansByPlayerId[pid];
  }
  const data = await fetchJson(`/api/training/player/${encodeURIComponent(pid)}`);
  const plan = {
    primary: normalizeCategory(data?.plan?.primary),
    secondary: normalizeCategory(data?.plan?.secondary, { allowBlank: true }),
    intensity: normalizeIntensity(data?.plan?.intensity),
  };
  const cached = {
    player_id: pid,
    season_year: Number(data?.season_year || 0) || 0,
    plan,
    is_default: Boolean(data?.is_default),
    loaded_at: Date.now(),
  };
  state.playerTrainingPlansByPlayerId = {
    ...(state.playerTrainingPlansByPlayerId || {}),
    [pid]: cached,
  };
  return cached;
}

function renderPlayerTrainingList() {
  if (!els.playerTrainingList) return;
  const rows = Array.isArray(state.playerTrainingRoster) ? state.playerTrainingRoster : [];
  if (!rows.length) {
    els.playerTrainingList.innerHTML = '<p class="empty-copy">표시할 선수 데이터가 없습니다.</p>';
    return;
  }
  const selectedId = String(state.playerTrainingSelectedPlayerId || "");
  const html = rows
    .map((row) => {
      const pid = getPlayerId(row);
      const name = getPlayerDisplayName(row);
      const pos = String(row.pos || "-");
      const ovr = Number(row.ovr || 0);
      const isSelected = selectedId && selectedId === pid;
      return `
        <button
          type="button"
          class="player-training-row ${isSelected ? "is-selected" : ""}"
          data-player-training-select="${escapeHtml(pid)}"
          aria-pressed="${isSelected ? "true" : "false"}"
        >
          <span class="player-training-row-main">
            <strong>${escapeHtml(name)}</strong>
            <span>${escapeHtml(pos)} · OVR ${Number.isFinite(ovr) ? ovr : "-"}</span>
          </span>
        </button>
      `;
    })
    .join("");
  els.playerTrainingList.innerHTML = html;
}

function setDraftFromPlan(plan) {
  state.playerTrainingDraft = {
    primary: normalizeCategory(plan?.primary),
    secondary: normalizeCategory(plan?.secondary, { allowBlank: true }),
    intensity: normalizeIntensity(plan?.intensity),
  };
}

function normalizePlanForCompare(plan) {
  return {
    primary: normalizeCategory(plan?.primary),
    secondary: normalizeCategory(plan?.secondary, { allowBlank: true }),
    intensity: normalizeIntensity(plan?.intensity),
  };
}

function isDraftDirty() {
  const pid = String(state.playerTrainingSelectedPlayerId || "");
  if (!pid) return false;
  const cached = state.playerTrainingPlansByPlayerId?.[pid];
  if (!cached?.plan) return true;
  const base = normalizePlanForCompare(cached.plan);
  const draft = normalizePlanForCompare(state.playerTrainingDraft);
  return base.primary !== draft.primary
    || base.secondary !== draft.secondary
    || base.intensity !== draft.intensity;
}

function setPlayerTrainingStatus(message, tone = "") {
  const nextTone = String(tone || "");
  const nextMessage = String(message || "");
  state.playerTrainingStatus = { tone: nextTone, message: nextMessage };
  if (!els.playerTrainingStatus) return;
  els.playerTrainingStatus.classList.remove("is-ok", "is-warn");
  if (nextTone === "ok") els.playerTrainingStatus.classList.add("is-ok");
  if (nextTone === "warn") els.playerTrainingStatus.classList.add("is-warn");
  els.playerTrainingStatus.textContent = nextMessage;
}

function refreshPlayerTrainingSaveUi() {
  if (!els.playerTrainingSaveBtn) return;
  const hasSelected = Boolean(String(state.playerTrainingSelectedPlayerId || ""));
  const dirty = isDraftDirty();
  const saving = Boolean(state.playerTrainingSaving);
  const enabled = hasSelected && dirty && !saving;
  els.playerTrainingSaveBtn.disabled = !enabled;
  els.playerTrainingSaveBtn.setAttribute("aria-disabled", enabled ? "false" : "true");
}

function renderPlayerTrainingDetail() {
  if (!els.playerTrainingDetail) return;
  const row = getSelectedPlayerRow();
  if (!row) {
    els.playerTrainingDetail.innerHTML = '<p class="empty-copy">왼쪽 선수 목록에서 선수를 선택하면 개인 훈련 설정이 표시됩니다.</p>';
    setPlayerTrainingStatus("", "");
    refreshPlayerTrainingSaveUi();
    return;
  }

  const pid = getPlayerId(row);
  const name = getPlayerDisplayName(row);
  const pos = String(row.pos || "-");
  const ovr = Number(row.ovr || 0);
  const cached = state.playerTrainingPlansByPlayerId?.[pid];
  const badge = cached?.is_default ? "기본 플랜" : "사용자 플랜";

  const primaryOptions = buildPlayerTrainingOptionMarkup({
    options: PLAYER_TRAINING_CATEGORIES,
    selected: state.playerTrainingDraft?.primary,
    labels: CATEGORY_LABELS,
  });
  const secondaryOptions = buildPlayerTrainingOptionMarkup({
    options: PLAYER_TRAINING_CATEGORIES.filter((x) => x !== "BALANCED"),
    selected: state.playerTrainingDraft?.secondary,
    labels: CATEGORY_LABELS,
    allowBlank: true,
  });
  const intensityOptions = buildPlayerTrainingOptionMarkup({
    options: PLAYER_TRAINING_INTENSITIES,
    selected: state.playerTrainingDraft?.intensity,
    labels: INTENSITY_LABELS,
  });

  els.playerTrainingDetail.innerHTML = `
    <article class="player-training-player-card">
      <h4>${escapeHtml(name)}</h4>
      <p class="subtitle">${escapeHtml(pos)} · OVR ${Number.isFinite(ovr) ? ovr : "-"}</p>
      <span class="player-training-badge">${escapeHtml(badge)}</span>
    </article>

    <div class="player-training-form">
      <label class="player-training-field">
        <span>Primary Focus</span>
        <select id="player-training-primary-select" data-player-training-field="primary">
          ${primaryOptions}
        </select>
      </label>

      <label class="player-training-field">
        <span>Secondary Focus</span>
        <select id="player-training-secondary-select" data-player-training-field="secondary">
          ${secondaryOptions}
        </select>
      </label>

      <label class="player-training-field">
        <span>Intensity</span>
        <select id="player-training-intensity-select" data-player-training-field="intensity">
          ${intensityOptions}
        </select>
      </label>
    </div>

    <p class="player-training-note">훈련 항목과 강도를 수정한 뒤 저장하면 선수 개인 훈련 플랜에 반영됩니다.</p>
  `;
  refreshPlayerTrainingSaveUi();
}

async function selectPlayerForTraining(playerId, { forceReload = false } = {}) {
  const pid = String(playerId || "");
  if (!pid) return;
  state.playerTrainingSelectedPlayerId = pid;
  renderPlayerTrainingList();

  if (els.playerTrainingDetail) {
    els.playerTrainingDetail.innerHTML = '<p class="empty-copy">개인 훈련 플랜을 불러오는 중...</p>';
  }

  try {
    const cached = await fetchPlayerTrainingPlan(pid, { forceReload });
    setDraftFromPlan(cached?.plan || null);
    setPlayerTrainingStatus("", "");
    renderPlayerTrainingDetail();
  } catch (error) {
    resetPlayerTrainingDraft();
    setPlayerTrainingStatus("", "");
    renderPlayerTrainingDetail();
    const message = error?.message ? String(error.message) : "개인 훈련 플랜을 불러오지 못했습니다.";
    if (els.playerTrainingDetail) {
      els.playerTrainingDetail.insertAdjacentHTML(
        "beforeend",
        `<p class="empty-copy">${escapeHtml(message)}</p>`,
      );
    }
  }
}

function onPlayerTrainingDraftChange(field, value) {
  const key = String(field || "");
  if (!key) return;
  if (!state.playerTrainingDraft || typeof state.playerTrainingDraft !== "object") {
    resetPlayerTrainingDraft();
  }
  if (key === "primary") {
    state.playerTrainingDraft.primary = normalizeCategory(value);
    setPlayerTrainingStatus("", "");
    refreshPlayerTrainingSaveUi();
    return;
  }
  if (key === "secondary") {
    state.playerTrainingDraft.secondary = normalizeCategory(value, { allowBlank: true });
    setPlayerTrainingStatus("", "");
    refreshPlayerTrainingSaveUi();
    return;
  }
  if (key === "intensity") {
    state.playerTrainingDraft.intensity = normalizeIntensity(value);
    setPlayerTrainingStatus("", "");
    refreshPlayerTrainingSaveUi();
  }
}

function buildSavePlanPayload() {
  return {
    primary: normalizeCategory(state.playerTrainingDraft?.primary),
    secondary: normalizeCategory(state.playerTrainingDraft?.secondary, { allowBlank: true }) || null,
    intensity: normalizeIntensity(state.playerTrainingDraft?.intensity),
  };
}

async function savePlayerTrainingPlan() {
  const pid = String(state.playerTrainingSelectedPlayerId || "");
  if (!pid) {
    setPlayerTrainingStatus("먼저 저장할 선수를 선택하세요.", "warn");
    refreshPlayerTrainingSaveUi();
    return { ok: false, reason: "no_player" };
  }
  if (state.playerTrainingSaving) {
    return { ok: false, reason: "saving" };
  }
  if (!isDraftDirty()) {
    setPlayerTrainingStatus("변경된 항목이 없습니다.", "warn");
    refreshPlayerTrainingSaveUi();
    return { ok: false, reason: "no_changes" };
  }

  const savePlan = buildSavePlanPayload();
  const cached = state.playerTrainingPlansByPlayerId?.[pid] || {};
  const payload = {
    player_id: pid,
    primary: savePlan.primary,
    secondary: savePlan.secondary,
    intensity: savePlan.intensity,
  };
  if (Number(cached.season_year || 0) > 0) {
    payload.season_year = Number(cached.season_year);
  }

  state.playerTrainingSaving = true;
  setPlayerTrainingStatus("개인 훈련 플랜을 저장하는 중...", "");
  refreshPlayerTrainingSaveUi();

  try {
    const res = await fetchJson("/api/training/player/set", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const seasonYear = Number(res?.season_year || cached?.season_year || 0) || 0;
    state.playerTrainingPlansByPlayerId = {
      ...(state.playerTrainingPlansByPlayerId || {}),
      [pid]: {
        player_id: pid,
        season_year: seasonYear,
        plan: {
          primary: savePlan.primary,
          secondary: savePlan.secondary || "",
          intensity: savePlan.intensity,
        },
        is_default: false,
        loaded_at: Date.now(),
      },
    };
    setPlayerTrainingStatus("개인 훈련 플랜이 저장되었습니다.", "ok");
    renderPlayerTrainingDetail();
    return { ok: true };
  } catch (error) {
    const message = error?.message ? String(error.message) : "개인 훈련 저장에 실패했습니다.";
    setPlayerTrainingStatus(message, "warn");
    refreshPlayerTrainingSaveUi();
    throw error;
  } finally {
    state.playerTrainingSaving = false;
    refreshPlayerTrainingSaveUi();
  }
}

async function renderPlayerTrainingTab({ autoSelectFirst = true } = {}) {
  ensurePlayerTrainingRoster();
  renderPlayerTrainingList();

  if (!state.playerTrainingRoster.length) {
    if (els.playerTrainingDetail) {
      els.playerTrainingDetail.innerHTML = '<p class="empty-copy">소속 선수 데이터가 없어 개인 훈련 설정을 표시할 수 없습니다.</p>';
    }
    setPlayerTrainingStatus("소속 선수 데이터가 없어 저장 기능을 사용할 수 없습니다.", "warn");
    refreshPlayerTrainingSaveUi();
    return;
  }

  const selectedId = String(state.playerTrainingSelectedPlayerId || "");
  const hasSelected = selectedId && state.playerTrainingRoster.some((row) => getPlayerId(row) === selectedId);
  if (hasSelected) {
    await selectPlayerForTraining(selectedId);
    return;
  }

  if (autoSelectFirst) {
    await selectPlayerForTraining(getPlayerId(state.playerTrainingRoster[0]));
  }
}

async function activateTrainingTab(mode) {
  const next = String(mode || "team").toLowerCase() === "player" ? "player" : "team";
  state.trainingTab = next;

  els.teamTrainingTabBtn?.classList.toggle("is-active", next === "team");
  els.playerTrainingTabBtn?.classList.toggle("is-active", next === "player");

  if (els.teamTrainingPanel) {
    els.teamTrainingPanel.classList.toggle("hidden", next !== "team");
    els.teamTrainingPanel.setAttribute("aria-hidden", next === "team" ? "false" : "true");
  }
  if (els.playerTrainingPanel) {
    els.playerTrainingPanel.classList.toggle("hidden", next !== "player");
    els.playerTrainingPanel.setAttribute("aria-hidden", next === "player" ? "false" : "true");
  }

  if (next === "player") {
    await renderPlayerTrainingTab({ autoSelectFirst: true });
    refreshPlayerTrainingSaveUi();
    return;
  }
  setPlayerTrainingStatus("", "");
}

export {
  activateTrainingTab,
  savePlayerTrainingPlan,
  renderPlayerTrainingTab,
  selectPlayerForTraining,
  onPlayerTrainingDraftChange,
};
