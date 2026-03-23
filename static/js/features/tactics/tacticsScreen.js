import { state } from "../../app/state.js";
import { els } from "../../app/dom.js";
import { activateScreen } from "../../app/router.js";
import { fetchCachedJson, fetchJson, setLoading } from "../../core/api.js";
import { TACTICS_OFFENSE_SCHEMES, TACTICS_DEFENSE_SCHEMES, TACTICS_OFFENSE_ROLES } from "../../core/constants/tactics.js";
import { tacticsSchemeLabel, tacticDisplayLabel, getDefenseRolesForScheme, buildTacticsDraft, computeTacticsInsights, rowHealthState } from "./tacticsInsights.js";
import {
  createDefaultPresetOffenseDraft,
  sanitizePresetOffenseDraft,
} from "./presetOffenseDraft.js";
import {
  compilePresetOffenseDraft,
  mergeCompiledPresetIntoTactics,
} from "./presetOffenseCompiler.js";
import {
  openPresetOffenseModal as openPresetOffenseModalUi,
  bindPresetOffenseModalEvents,
} from "./presetOffenseModal.js";
import {
  draftFromSavedTactics,
  injectDraftSnapshotToContext,
} from "./presetOffenseSerde.js";
import {
  createDefaultPresetDefenseDraft,
  sanitizePresetDefenseDraft,
  summarizePresetDefenseDraft,
} from "./presetDefenseDraft.js";
import {
  compilePresetDefenseDraft,
  mergeCompiledPresetDefenseIntoTactics,
} from "./presetDefenseCompiler.js";
import {
  openPresetDefenseModal as openPresetDefenseModalUi,
  bindPresetDefenseModalEvents,
} from "./presetDefenseModal.js";
import {
  defenseDraftFromSavedTactics,
  injectDefenseDraftSnapshotToContext,
} from "./presetDefenseSerde.js";
import { fetchTeamDetail, hasTeamDetailCache } from "../team/teamDetailCache.js";
import { CACHE_EVENT_TYPES, CACHE_TTL_MS, buildCacheKeys, getPrefetchPlanForEvent, runPrefetchPlan } from "../../app/cachePolicy.js";
import { emitCacheEvent } from "../../app/cacheEvents.js";

let tacticsRequestSeq = 0;

function updatePresetOffenseButtonVisibility() {
  if (!els.presetOffenseOpenBtn || !state.tacticsDraft) return;
  const isPreset = String(state.tacticsDraft.offenseScheme) === "Preset_Offense";
  els.presetOffenseOpenBtn.classList.toggle("hidden", !isPreset);
}

function updatePresetDefenseButtonVisibility() {
  if (!els.presetDefenseOpenBtn || !state.tacticsDraft) return;
  const isPreset = String(state.tacticsDraft.defenseScheme) === "Preset_Defense";
  els.presetDefenseOpenBtn.classList.toggle("hidden", !isPreset);
  if (!isPreset && els.presetDefenseErrors) {
    els.presetDefenseErrors.textContent = "";
  }
}

function openPresetOffenseModal() {
  state.presetOffenseDraft = sanitizePresetOffenseDraft(state.presetOffenseDraft || createDefaultPresetOffenseDraft());
  openPresetOffenseModalUi(state.presetOffenseDraft, (nextDraft, validation) => {
    state.presetOffenseDraft = sanitizePresetOffenseDraft(nextDraft);
    if (els.presetOffenseErrors) {
      const warn = validation?.warnings?.length ? ` (자동 조정 ${validation.warnings.length}건)` : "";
      els.presetOffenseErrors.textContent = `프리셋 공격 설정이 적용되었습니다. 전술 저장을 눌러 반영하세요.${warn}`;
    }
    markTacticsDirty();
  });
}

function openPresetDefenseModal() {
  state.presetDefenseDraft = sanitizePresetDefenseDraft(state.presetDefenseDraft || createDefaultPresetDefenseDraft());
  openPresetDefenseModalUi(state.presetDefenseDraft, (nextDraft, validation) => {
    state.presetDefenseDraft = sanitizePresetDefenseDraft(nextDraft);
    if (els.presetDefenseErrors) {
      const summary = summarizePresetDefenseDraft(state.presetDefenseDraft);
      const warn = validation?.warnings?.length ? ` (자동 조정 ${validation.warnings.length}건)` : "";
      els.presetDefenseErrors.textContent = `프리셋 수비 설정 적용: 세부 설정 ${summary.tunedActionCount}개, 강억제 ${summary.strongActionCount}개, 최대 버짓 ${summary.topBudgetAction} ${summary.topBudgetValue}. 전술 저장을 눌러 반영하세요.${warn}`;
    }
    markTacticsDirty();
  });
}

function applyTacticsDetail(detail, savedTactics, teamId) {
  state.rosterRows = detail?.roster || [];
  state.tacticsDraft = normalizeDraftForRoster(savedTactics?.tactics, state.rosterRows);
  state.presetOffenseDraft = draftFromSavedTactics(savedTactics?.tactics);
  state.presetDefenseDraft = defenseDraftFromSavedTactics(savedTactics?.tactics);
  state.tacticsDraftTeamId = teamId;
  state.tacticsDirty = false;
  state.tacticsSaving = false;

  renderSchemeOptions("offense");
  renderSchemeOptions("defense");
  renderTacticsScreen();
}

function normalizeRow(row, fallback = {}) {
  const out = {
    pid: String(row?.pid || fallback.pid || ""),
    offenseRole: String(row?.offenseRole || fallback.offenseRole || TACTICS_OFFENSE_ROLES[0]),
    defenseRole: String(row?.defenseRole || fallback.defenseRole || ""),
    minutes: Math.max(0, Math.min(48, Number(row?.minutes ?? fallback.minutes ?? 0))),
  };
  if (!TACTICS_OFFENSE_ROLES.includes(out.offenseRole)) {
    out.offenseRole = String(fallback.offenseRole || TACTICS_OFFENSE_ROLES[0]);
  }
  return out;
}

function normalizeDraftForRoster(raw, rosterRows) {
  const base = buildTacticsDraft(rosterRows || []);
  if (!raw || typeof raw !== "object") return base;

  const offenseScheme = TACTICS_OFFENSE_SCHEMES.some((s) => s.key === raw.offenseScheme)
    ? raw.offenseScheme
    : base.offenseScheme;
  const defenseScheme = TACTICS_DEFENSE_SCHEMES.some((s) => s.key === raw.defenseScheme)
    ? raw.defenseScheme
    : base.defenseScheme;
  const allowedDefenseRoles = getDefenseRolesForScheme(defenseScheme);

  const normalizeRows = (rows, fallbackRows) => {
    const inRows = Array.isArray(rows) ? rows : [];
    return fallbackRows.map((fallbackRow, idx) => {
      const row = normalizeRow(inRows[idx], fallbackRow);
      if (!allowedDefenseRoles.includes(row.defenseRole)) {
        row.defenseRole = allowedDefenseRoles[idx % allowedDefenseRoles.length];
      }
      return row;
    });
  };

  return {
    offenseScheme,
    defenseScheme,
    starters: normalizeRows(raw.starters, base.starters),
    rotation: normalizeRows(raw.rotation, base.rotation),
    baselineHash: String(raw.baselineHash || ""),
  };
}

function buildTacticsPayload() {
  const payload = {
    offenseScheme: state.tacticsDraft?.offenseScheme,
    defenseScheme: state.tacticsDraft?.defenseScheme,
    starters: (state.tacticsDraft?.starters || []).map((r) => ({
      pid: String(r?.pid || ""),
      offenseRole: String(r?.offenseRole || ""),
      defenseRole: String(r?.defenseRole || ""),
      minutes: Math.max(0, Math.min(48, Number(r?.minutes || 0))),
    })),
    rotation: (state.tacticsDraft?.rotation || []).map((r) => ({
      pid: String(r?.pid || ""),
      offenseRole: String(r?.offenseRole || ""),
      defenseRole: String(r?.defenseRole || ""),
      minutes: Math.max(0, Math.min(48, Number(r?.minutes || 0))),
    })),
    baselineHash: String(state.tacticsDraft?.baselineHash || ""),
  };
  if (String(payload.offenseScheme || "") === "Preset_Offense") {
    const compiled = compilePresetOffenseDraft(state.presetOffenseDraft || createDefaultPresetOffenseDraft(), payload);
    Object.assign(payload, mergeCompiledPresetIntoTactics(payload, compiled));
    Object.assign(payload, injectDraftSnapshotToContext(state.presetOffenseDraft, payload));
  }
  if (String(payload.defenseScheme || "") === "Preset_Defense") {
    const compiledDefense = compilePresetDefenseDraft(state.presetDefenseDraft || createDefaultPresetDefenseDraft(), payload);
    Object.assign(payload, mergeCompiledPresetDefenseIntoTactics(payload, compiledDefense));
    Object.assign(payload, injectDefenseDraftSnapshotToContext(state.presetDefenseDraft, payload));
  }
  return payload;
}

function updateTacticsSaveButton() {
  if (!els.tacticsSaveBtn) return;
  els.tacticsSaveBtn.disabled = !!state.tacticsSaving;
  els.tacticsSaveBtn.textContent = state.tacticsSaving ? "저장 중..." : "전술 저장";
}

function markTacticsDirty() {
  state.tacticsDirty = true;
}

function hasUnsavedTacticsChanges() {
  return !!state.tacticsDirty;
}

function getStarterDefenseRoleDuplicates() {
  const counts = new Map();
  (state.tacticsDraft?.starters || []).forEach((row) => {
    const key = String(row?.defenseRole || "").trim();
    if (!key) return;
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  return [...counts.entries()].filter(([, count]) => count > 1).map(([role]) => role);
}

async function saveTacticsDraft({ showSuccessMessage = true } = {}) {
  if (!state.selectedTeamId || !state.tacticsDraft) return true;
  const teamId = String(state.tacticsDraftTeamId || state.selectedTeamId || "").trim();
  if (!teamId) return true;

  const isPresetDefense = String(state.tacticsDraft?.defenseScheme || "") === "Preset_Defense";
  const starterDupRoles = isPresetDefense ? [] : getStarterDefenseRoleDuplicates();
  if (starterDupRoles.length) {
    if (els.tacticsTotalMessage) {
      els.tacticsTotalMessage.textContent = `전술 저장 실패: 선발 수비에 중복 역할(${starterDupRoles.map((role) => tacticDisplayLabel(role)).join(", ")})이 있습니다.`;
    }
    return false;
  }

  state.tacticsSaving = true;
  updateTacticsSaveButton();
  try {
    await fetchJson(`/api/tactics/${encodeURIComponent(teamId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tactics: buildTacticsPayload() }),
    });
    state.tacticsDirty = false;
    emitCacheEvent(CACHE_EVENT_TYPES.TACTICS_SAVE, { teamId });
    const prefetchPlan = getPrefetchPlanForEvent(CACHE_EVENT_TYPES.TACTICS_SAVE, { teamId });
    void runPrefetchPlan(prefetchPlan);
    if (showSuccessMessage && els.tacticsTotalMessage) {
      els.tacticsTotalMessage.textContent = "전술 저장이 완료되었습니다.";
    }
    return true;
  } catch (e) {
    if (els.tacticsTotalMessage) {
      els.tacticsTotalMessage.textContent = `전술 저장 실패: ${e.message}`;
    }
    return false;
  } finally {
    state.tacticsSaving = false;
    updateTacticsSaveButton();
  }
}

function renderSchemeOptions(kind) {
  const isOff = kind === "offense";
  const optionsEl = isOff ? els.tacticsOffenseOptions : els.tacticsDefenseOptions;
  const list = isOff ? TACTICS_OFFENSE_SCHEMES : TACTICS_DEFENSE_SCHEMES;
  const selected = isOff ? state.tacticsDraft.offenseScheme : state.tacticsDraft.defenseScheme;
  optionsEl.innerHTML = list.map((s) => `<button type="button" data-key="${s.key}">${tacticDisplayLabel(s.label)}${s.key === selected ? " ✓" : ""}</button>`).join("");
  optionsEl.querySelectorAll("button[data-key]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (isOff) {
        state.tacticsDraft.offenseScheme = btn.dataset.key;
        updatePresetOffenseButtonVisibility();
      } else {
        state.tacticsDraft.defenseScheme = btn.dataset.key;
        updatePresetDefenseButtonVisibility();
        const defRoles = getDefenseRolesForScheme(btn.dataset.key);
        [...state.tacticsDraft.starters, ...state.tacticsDraft.rotation].forEach((row, idx) => {
          if (!defRoles.includes(row.defenseRole)) row.defenseRole = defRoles[idx % defRoles.length];
        });
      }
      optionsEl.classList.add("hidden");
      markTacticsDirty();
      renderTacticsScreen();
    });
  });
}

function buildLineupRowHtml(group, idx, row, defenseRoles, insights) {
  const players = state.rosterRows || [];
  const playerOptions = ['<option value="">- 선택 -</option>', ...players.map((r) => `<option value="${r.player_id}" ${String(r.player_id) === String(row.pid) ? "selected" : ""}>${r.name || r.player_id}</option>`)].join("");
  const offOptions = TACTICS_OFFENSE_ROLES.map((role) => `<option value="${role}" ${role === row.offenseRole ? "selected" : ""}>${tacticDisplayLabel(role)}</option>`).join("");
  const defOptions = defenseRoles.map((role) => `<option value="${role}" ${role === row.defenseRole ? "selected" : ""}>${tacticDisplayLabel(role)}</option>`).join("");
  const health = rowHealthState(row, insights);
  return `
    <div class="tactics-lineup-row" data-group="${group}" data-idx="${idx}">
      <select data-field="pid" class="ui-select">${playerOptions}</select>
      <select data-field="offenseRole" class="ui-select">${offOptions}</select>
      <select data-field="defenseRole" class="ui-select">${defOptions}</select>
      <input data-field="minutes" type="number" min="0" max="48" value="${Number(row.minutes || 0)}" />
      <span class="tactics-role-badge ${health.cls}">${health.text}</span>
    </div>
  `;
}

function bindLineupEvents() {
  document.querySelectorAll(".tactics-lineup-row").forEach((rowEl) => {
    const group = rowEl.dataset.group;
    const idx = Number(rowEl.dataset.idx || 0);
    rowEl.querySelectorAll("select, input").forEach((control) => {
      control.addEventListener("change", () => {
        const field = control.dataset.field;
        const target = group === "starters" ? state.tacticsDraft.starters[idx] : state.tacticsDraft.rotation[idx];
        if (!target || !field) return;
        if (field === "defenseRole") {
          target.defenseRole = control.value;
        } else if (field === "minutes") {
          target.minutes = Math.max(0, Math.min(48, Number(control.value || 0)));
        } else {
          target[field] = control.value;
        }
        rowEl.classList.add("is-edited");
        setTimeout(() => rowEl.classList.remove("is-edited"), 700);
        markTacticsDirty();
        renderTacticsScreen();
      });
    });
  });
}

function renderTacticsRosterList() {
  els.tacticsRosterList.innerHTML = (state.rosterRows || []).length
    ? state.rosterRows.map((r) => `<div class="tactics-roster-item"><span>${r.name || r.player_id}</span><span class="tactics-roster-meta">${r.pos || "-"}</span></div>`).join("")
    : '<p class="empty-copy">로스터 데이터가 없습니다.</p>';
}

function renderTacticsInsights(insights) {
  if (!els.tacticsKpiTotal) return;

  els.tacticsKpiTotal.textContent = `${insights.totalMinutes} / 240`;
  els.tacticsKpiStarters.textContent = `${insights.starterAvg.toFixed(1)}분`;
  els.tacticsKpiRotation.textContent = `${insights.rotationAvg.toFixed(1)}분`;
  els.tacticsKpiDiversity.textContent = `${Math.round(insights.roleDiversity * 100)}%`;

  const totalChip = els.tacticsKpiTotal.closest(".tactics-kpi-chip");
  if (totalChip) totalChip.classList.toggle("kpi-warn", insights.minutesDelta !== 0);

  if (els.tacticsTotalBalance) els.tacticsTotalBalance.textContent = `${insights.totalMinutes} / 240`;
  if (els.tacticsTotalBar) {
    const pct = Math.max(0, Math.min(100, Math.round((insights.totalMinutes / 240) * 100)));
    els.tacticsTotalBar.style.width = `${pct}%`;
    els.tacticsTotalBar.classList.toggle("warn", insights.minutesDelta !== 0);
  }
  if (els.tacticsTotalMessage) {
    els.tacticsTotalMessage.textContent = insights.minutesDelta === 0
      ? "출전시간 분배가 안정적입니다."
      : `240분 기준에서 ${Math.abs(insights.minutesDelta)}분 ${insights.minutesDelta > 0 ? "부족" : "초과"} 상태입니다.`;
  }

  if (els.tacticsRoleCoverage) {
    const topOff = [...insights.offenseCount.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4);
    els.tacticsRoleCoverage.innerHTML = topOff.length
      ? topOff.map(([role, count]) => `<div class="tactics-pill">${tacticDisplayLabel(role)} · ${count}명</div>`).join("")
      : '<p class="empty-copy">역할 데이터가 없습니다.</p>';
  }

  if (els.tacticsWarningList) {
    const warnings = insights.warnings.slice(0, 5);
    els.tacticsWarningList.innerHTML = warnings.length
      ? warnings.map((w) => `<div class="tactics-warning-item ${w.level}">${w.text}</div>`).join("")
      : '<div class="tactics-warning-item">현재 치명적인 전술 경고가 없습니다.</div>';
  }

  if (els.tacticsHeroSub) {
    const offLabel = tacticDisplayLabel(tacticsSchemeLabel(TACTICS_OFFENSE_SCHEMES, state.tacticsDraft.offenseScheme));
    const defLabel = tacticDisplayLabel(tacticsSchemeLabel(TACTICS_DEFENSE_SCHEMES, state.tacticsDraft.defenseScheme));
    els.tacticsHeroSub.textContent = `${offLabel} × ${defLabel} 조합으로 운영 중`;
  }
}

function renderTacticsScreen() {
  if (!state.tacticsDraft) return;
  const defRoles = getDefenseRolesForScheme(state.tacticsDraft.defenseScheme);
  const insights = computeTacticsInsights();

  if (els.tacticsOffenseCurrent) els.tacticsOffenseCurrent.textContent = tacticDisplayLabel(tacticsSchemeLabel(TACTICS_OFFENSE_SCHEMES, state.tacticsDraft.offenseScheme));
  if (els.tacticsDefenseCurrent) els.tacticsDefenseCurrent.textContent = tacticDisplayLabel(tacticsSchemeLabel(TACTICS_DEFENSE_SCHEMES, state.tacticsDraft.defenseScheme));
  updatePresetOffenseButtonVisibility();
  updatePresetDefenseButtonVisibility();

  els.tacticsStarters.innerHTML = state.tacticsDraft.starters.map((r, i) => buildLineupRowHtml("starters", i, r, defRoles, insights)).join("");
  els.tacticsRotation.innerHTML = state.tacticsDraft.rotation.map((r, i) => buildLineupRowHtml("rotation", i, r, defRoles, insights)).join("");

  renderTacticsRosterList();
  renderTacticsInsights(insights);
  bindLineupEvents();
  updateTacticsSaveButton();
}

async function showTacticsScreen() {
  if (!state.selectedTeamId) {
    alert("먼저 팀을 선택해주세요.");
    return;
  }
  const teamId = String(state.selectedTeamId || "").trim();
  const requestSeq = tacticsRequestSeq + 1;
  tacticsRequestSeq = requestSeq;
  const hasCachedDetail = hasTeamDetailCache(teamId);
  if (!hasCachedDetail) setLoading(true, "전술 데이터를 불러오는 중...");
  try {
    bindPresetOffenseModalEvents();
    bindPresetDefenseModalEvents();
    let latestSavedTactics = { tactics: null };
    const tacticsCacheKey = buildCacheKeys(teamId).tactics;
    const savedTacticsPromise = fetchCachedJson({
      key: tacticsCacheKey,
      url: `/api/tactics/${encodeURIComponent(teamId)}`,
      ttlMs: CACHE_TTL_MS.tactics,
      staleWhileRevalidate: true,
    }).catch(() => ({ tactics: null }));
    const [detail, savedTactics] = await Promise.all([
      fetchTeamDetail(teamId, {
        onRevalidated: (freshDetail) => {
          if (requestSeq !== tacticsRequestSeq) return;
          if (String(state.selectedTeamId || "").trim() !== teamId) return;
          if (!els.tacticsScreen?.classList.contains("active")) return;
          if (state.tacticsDirty || state.tacticsSaving) return;
          applyTacticsDetail(freshDetail, latestSavedTactics, teamId);
        },
      }),
      savedTacticsPromise,
    ]);
    latestSavedTactics = savedTactics;
    if (requestSeq !== tacticsRequestSeq) return;

    applyTacticsDetail(detail, savedTactics, teamId);
    activateScreen(els.tacticsScreen);
  } finally {
    if (requestSeq === tacticsRequestSeq) setLoading(false);
  }
}

function toggleTacticsOptions(kind) {
  const target = kind === "offense" ? els.tacticsOffenseOptions : els.tacticsDefenseOptions;
  const other = kind === "offense" ? els.tacticsDefenseOptions : els.tacticsOffenseOptions;
  other.classList.add("hidden");
  target.classList.toggle("hidden");
}

export {
  renderSchemeOptions,
  buildLineupRowHtml,
  bindLineupEvents,
  renderTacticsRosterList,
  renderTacticsInsights,
  renderTacticsScreen,
  showTacticsScreen,
  toggleTacticsOptions,
  openPresetOffenseModal,
  openPresetDefenseModal,
  saveTacticsDraft,
  hasUnsavedTacticsChanges,
};
