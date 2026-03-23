import { els } from "../../app/dom.js";
import {
  PRESET_DEFENSE_ACTION_KEYS,
  PRESET_DEFENSE_POLICY_LEVEL,
  PRESET_DEFENSE_POLICY_SIDE,
  createDefaultPresetDefenseDraft,
  sanitizePresetDefenseDraft,
} from "./presetDefenseDraft.js";
import { PRESET_DEFENSE_ACTION_TABLE } from "./presetDefenseCompiler.js";

let currentDraft = createDefaultPresetDefenseDraft();
let onApplyHandler = null;
let isBound = false;
let expandedActions = new Set();

const PRESET_DEFENSE_GROUP_NAMES = {
  Cut: { A: "직접 마무리", B: "컷 파생" },
  DHO: { A: "페인트 압박", B: "점퍼" },
  Drive: { A: "림 돌파", B: "점퍼 + 킥아웃" },
  ISO: { A: "림 돌파", B: "점퍼 + 킥아웃" },
  PnP: { A: "페인트 압박", B: "팝 점퍼" },
  PnR: { A: "롤맨 림 런", B: "볼 핸들러 점퍼" },
  PostUp: { A: "직접 득점", B: "파생" },
  SpotUp: { A: "클로즈 아웃 강하게", B: "체크만" },
  TransitionEarly: { A: "림 압박", B: "퀵 점퍼" },
};

function _modalEl() {
  return els.presetDefenseModal || document.getElementById("preset-defense-modal");
}

function _findEl(...candidates) {
  for (const c of candidates) {
    if (c) return c;
  }
  return null;
}

function _getRefs() {
  const modal = _modalEl();
  return {
    modal,
    backdrop: _findEl(els.presetDefenseModalBackdrop, modal?.querySelector("#preset-defense-modal-backdrop"), modal?.querySelector("[data-preset-defense-backdrop]")),
    closeBtn: _findEl(els.presetDefenseCloseBtn, modal?.querySelector("#preset-defense-close-btn"), modal?.querySelector("[data-preset-defense-close]")),
    resetBtn: _findEl(els.presetDefenseResetBtn, modal?.querySelector("#preset-defense-reset-btn"), modal?.querySelector("[data-preset-defense-reset]")),
    applyBtn: _findEl(els.presetDefenseApplyBtn, modal?.querySelector("#preset-defense-apply-btn"), modal?.querySelector("[data-preset-defense-apply]")),
    form: _findEl(els.presetDefenseForm, modal?.querySelector("#preset-defense-form"), modal?.querySelector("[data-preset-defense-form]")),
    errors: _findEl(els.presetDefenseErrors, modal?.querySelector("#preset-defense-errors"), modal?.querySelector("[data-preset-defense-errors]")),
  };
}

function _focusables() {
  const { modal } = _getRefs();
  if (!modal) return [];
  return [...modal.querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])")]
    .filter((el) => !el.hasAttribute("disabled") && !el.classList.contains("hidden"));
}

function _setDeepValue(target, path, value) {
  const keys = String(path || "").split(".").filter(Boolean);
  if (!keys.length) return;
  let cursor = target;
  for (let i = 0; i < keys.length - 1; i += 1) {
    const k = keys[i];
    if (!cursor[k] || typeof cursor[k] !== "object") cursor[k] = {};
    cursor = cursor[k];
  }
  cursor[keys[keys.length - 1]] = value;
}

function _validateDraft(draft) {
  const d = sanitizePresetDefenseDraft(draft);
  const warnings = [];
  const errors = [];
  const totalBudget = PRESET_DEFENSE_ACTION_KEYS.reduce((acc, a) => acc + (Number(d.actionBudget[a]) || 0), 0);

  if (totalBudget !== 100) {
    warnings.push(`액션 버짓 합계가 ${totalBudget}으로 보정되었습니다.`);
  }

  PRESET_DEFENSE_ACTION_KEYS.forEach((action) => {
    const side = String(d.actionPolicies?.[action]?.side || PRESET_DEFENSE_POLICY_SIDE.NEUTRAL);
    if (![PRESET_DEFENSE_POLICY_SIDE.A, PRESET_DEFENSE_POLICY_SIDE.B, PRESET_DEFENSE_POLICY_SIDE.NEUTRAL].includes(side)) {
      errors.push(`${action} 선택값이 올바르지 않습니다.`);
    }
  });

  return {
    ok: errors.length === 0,
    errors,
    warnings,
    draft: d,
  };
}

function _actionLabel(action) {
  return String(action || "").replaceAll("_", " ");
}

function _groupLabel(action, groupKey) {
  const spec = PRESET_DEFENSE_ACTION_TABLE[action];
  if (!spec) return groupKey === "A" ? "묶음 A" : "묶음 B";
  const named = PRESET_DEFENSE_GROUP_NAMES[action];
  if (named && groupKey in named) return named[groupKey];
  return groupKey === "A" ? "묶음 A" : "묶음 B";
}

function _renderBudgetRow(action, value) {
  return `
    <label class="preset-defense-budget-control">
      <span>액션 버짓</span>
      <input type="range" min="0" max="100" value="${Number(value) || 0}" data-defense-budget="${action}" />
      <strong>${Number(value) || 0}</strong>
    </label>
  `;
}

function _renderPolicyRow(action, policy) {
  const side = String(policy?.side || PRESET_DEFENSE_POLICY_SIDE.NEUTRAL);
  const level = String(policy?.level || PRESET_DEFENSE_POLICY_LEVEL.NORMAL);
  const showLevel = side !== PRESET_DEFENSE_POLICY_SIDE.NEUTRAL;
  return `
    <div class="preset-defense-policy-block" role="group" aria-label="${_actionLabel(action)} 세부 설정">
      <h4 class="preset-defense-policy-title">${_actionLabel(action)} 세부 묶음 선택</h4>
      <div class="preset-defense-policy-choices">
        <button type="button" data-defense-side="${action}:A" class="${side === "A" ? "is-active" : ""}">
          ${_groupLabel(action, "A")} 억제 + ${_groupLabel(action, "B")} 허용
        </button>
        <button type="button" data-defense-side="${action}:neutral" class="${side === "neutral" ? "is-active" : ""}">
          중립
        </button>
        <button type="button" data-defense-side="${action}:B" class="${side === "B" ? "is-active" : ""}">
          ${_groupLabel(action, "A")} 허용 + ${_groupLabel(action, "B")} 억제
        </button>
      </div>
      ${showLevel ? `
        <div class="preset-defense-policy-level">
          <button type="button" data-defense-level="${action}:normal" class="${level === "normal" ? "is-active" : ""}">억제</button>
          <button type="button" data-defense-level="${action}:strong" class="${level === "strong" ? "is-active" : ""}">강하게 억제</button>
        </div>
      ` : ""}
    </div>
  `;
}

function _renderPressure(level) {
  return `
    <section class="preset-defense-pressure-section">
      <h3 class="preset-defense-pressure-title">전역 압박</h3>
      <label class="preset-defense-pressure-row">
        <span>전역 압박 강도 (${Number(level) || 0})</span>
        <input type="range" min="-2" max="2" step="1" value="${Number(level) || 0}" data-defense-pressure="1" />
        <small>-2(약) / 0(중립) / +2(강)</small>
      </label>
    </section>
  `;
}

function _renderActionItem(action, draft, isExpanded) {
  const actionId = String(action || "").replaceAll(/[^a-zA-Z0-9_-]/g, "-");
  return `
    <article class="preset-defense-action-item ${isExpanded ? "is-expanded" : ""}" data-defense-action-item="${action}" aria-expanded="${isExpanded ? "true" : "false"}">
      <div class="preset-defense-action-head">
        <button type="button" class="preset-defense-action-toggle" data-defense-toggle="${action}" aria-expanded="${isExpanded ? "true" : "false"}" aria-controls="preset-defense-detail-${actionId}">
          <h4 class="preset-defense-action-title">${_actionLabel(action)}</h4>
          <span class="preset-defense-action-meta">${isExpanded ? "세부 설정 닫기" : "세부 설정 열기"}</span>
        </button>
        ${_renderBudgetRow(action, draft.actionBudget?.[action])}
      </div>
      <div id="preset-defense-detail-${actionId}" class="preset-defense-action-detail" aria-hidden="${isExpanded ? "false" : "true"}">
        ${_renderPolicyRow(action, draft.actionPolicies?.[action])}
      </div>
    </article>
  `;
}

function _focusSelectorFor(option = null) {
  if (!option?.type) return null;
  if (option.type === "toggle") return `button[data-defense-toggle="${option.action}"]`;
  if (option.type === "budget") return `input[data-defense-budget="${option.action}"]`;
  if (option.type === "side") return `button[data-defense-side="${option.action}:${option.value}"]`;
  if (option.type === "level") return `button[data-defense-level="${option.action}:${option.value}"]`;
  if (option.type === "pressure") return `input[data-defense-pressure="1"]`;
  return null;
}

function renderPresetDefenseModal(draft, validation = null, focusOption = null) {
  const refs = _getRefs();
  if (!refs.form) return;

  currentDraft = sanitizePresetDefenseDraft(draft || currentDraft || createDefaultPresetDefenseDraft());
  const d = currentDraft;

  refs.form.innerHTML = `
    <section class="preset-defense-action-list" aria-label="액션 버짓 및 세부 설정">
      ${PRESET_DEFENSE_ACTION_KEYS.map((action) => _renderActionItem(action, d, expandedActions.has(action))).join("")}
    </section>
    ${_renderPressure(d.pressureLevel)}
  `;

  if (refs.errors) {
    if (validation?.errors?.length) {
      refs.errors.textContent = `적용 불가: ${validation.errors.join(" / ")}`;
    } else if (validation?.warnings?.length) {
      refs.errors.textContent = `자동 조정 ${validation.warnings.length}건: ${validation.warnings[0]}`;
    } else {
      refs.errors.textContent = "";
    }
  }

  const selector = _focusSelectorFor(focusOption);
  if (selector) {
    const focusTarget = refs.form.querySelector(selector);
    if (focusTarget) focusTarget.focus();
  }
}

function _onFormInput(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;

  if (target.matches("input[data-defense-budget]")) {
    const action = String(target.dataset.defenseBudget || "");
    _setDeepValue(currentDraft, `actionBudget.${action}`, Number(target.value || 0));
    const v = _validateDraft(currentDraft);
    currentDraft = v.draft;
    renderPresetDefenseModal(currentDraft, v, { type: "budget", action });
    return;
  }

  if (target.matches("input[data-defense-pressure]")) {
    _setDeepValue(currentDraft, "pressureLevel", Number(target.value || 0));
    const v = _validateDraft(currentDraft);
    currentDraft = v.draft;
    renderPresetDefenseModal(currentDraft, v, { type: "pressure" });
  }
}

function _onFormClick(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;

  const toggle = target.closest("button[data-defense-toggle]");
  if (toggle) {
    const action = String(toggle.dataset.defenseToggle || "");
    if (expandedActions.has(action)) {
      expandedActions.delete(action);
    } else {
      expandedActions.add(action);
    }
    const v = _validateDraft(currentDraft);
    currentDraft = v.draft;
    renderPresetDefenseModal(currentDraft, v, { type: "toggle", action });
    return;
  }

  const sideBtn = target.closest("button[data-defense-side]");
  if (sideBtn) {
    const [action, side] = String(sideBtn.dataset.defenseSide || "").split(":");
    _setDeepValue(currentDraft, `actionPolicies.${action}.side`, side || "neutral");
    expandedActions.add(action);
    const v = _validateDraft(currentDraft);
    currentDraft = v.draft;
    renderPresetDefenseModal(currentDraft, v, { type: "side", action, value: side || "neutral" });
    return;
  }

  const levelBtn = target.closest("button[data-defense-level]");
  if (levelBtn) {
    const [action, level] = String(levelBtn.dataset.defenseLevel || "").split(":");
    _setDeepValue(currentDraft, `actionPolicies.${action}.level`, level || "normal");
    expandedActions.add(action);
    const v = _validateDraft(currentDraft);
    currentDraft = v.draft;
    renderPresetDefenseModal(currentDraft, v, { type: "level", action, value: level || "normal" });
  }
}

function openPresetDefenseModal(draft, onApply) {
  const refs = _getRefs();
  if (!refs.modal) return;
  onApplyHandler = typeof onApply === "function" ? onApply : null;
  const v = _validateDraft(draft || createDefaultPresetDefenseDraft());
  currentDraft = v.draft;
  expandedActions = new Set();
  renderPresetDefenseModal(currentDraft, v);
  refs.modal.classList.remove("hidden");
  refs.modal.setAttribute("aria-hidden", "false");
  _focusables()[0]?.focus();
}

function closePresetDefenseModal() {
  const refs = _getRefs();
  if (!refs.modal) return;
  refs.modal.classList.add("hidden");
  refs.modal.setAttribute("aria-hidden", "true");
  (els.presetDefenseOpenBtn || document.getElementById("preset-defense-open-btn"))?.focus();
}

function _onModalKeydown(event) {
  const refs = _getRefs();
  if (!refs.modal || refs.modal.classList.contains("hidden")) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closePresetDefenseModal();
    return;
  }
  if (event.key !== "Tab") return;
  const focusables = _focusables();
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function bindPresetDefenseModalEvents() {
  if (isBound) return;
  isBound = true;

  const refs = _getRefs();
  refs.closeBtn?.addEventListener("click", () => closePresetDefenseModal());
  refs.backdrop?.addEventListener("click", () => closePresetDefenseModal());
  refs.resetBtn?.addEventListener("click", () => {
    const v = _validateDraft(createDefaultPresetDefenseDraft());
    currentDraft = v.draft;
    expandedActions = new Set();
    renderPresetDefenseModal(currentDraft, v);
    const nextRefs = _getRefs();
    if (nextRefs.errors) nextRefs.errors.textContent = "초기값으로 되돌렸습니다. 적용을 눌러 반영하세요.";
  });

  refs.applyBtn?.addEventListener("click", () => {
    const v = _validateDraft(currentDraft);
    currentDraft = v.draft;
    if (!v.ok) {
      renderPresetDefenseModal(currentDraft, v);
      return;
    }
    if (onApplyHandler) onApplyHandler({ ...currentDraft }, v);
    closePresetDefenseModal();
  });

  document.addEventListener("keydown", _onModalKeydown);
  refs.form?.addEventListener("click", _onFormClick);
  refs.form?.addEventListener("input", _onFormInput);
}

export {
  openPresetDefenseModal,
  closePresetDefenseModal,
  renderPresetDefenseModal,
  bindPresetDefenseModalEvents,
};
