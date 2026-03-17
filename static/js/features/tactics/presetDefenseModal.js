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
  if (!spec) return groupKey;
  return groupKey === "A" ? "묶음 A" : "묶음 B";
}

function _renderBudgetRow(action, value) {
  return `
    <label class="preset-defense-budget-row">
      <span>${_actionLabel(action)}</span>
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
    <div class="preset-defense-policy-row" role="group" aria-label="${_actionLabel(action)} 정책">
      <h4>${_actionLabel(action)}</h4>
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
    <label class="preset-defense-pressure-row">
      <span>전역 압박 강도 (${Number(level) || 0})</span>
      <input type="range" min="-2" max="2" step="1" value="${Number(level) || 0}" data-defense-pressure="1" />
      <small>-2(약) / 0(중립) / +2(강)</small>
    </label>
  `;
}

function renderPresetDefenseModal(draft, validation = null) {
  const refs = _getRefs();
  if (!refs.form) return;

  currentDraft = sanitizePresetDefenseDraft(draft || currentDraft || createDefaultPresetDefenseDraft());
  const d = currentDraft;

  refs.form.innerHTML = `
    <section class="preset-defense-section">
      <h3>액션 버짓</h3>
      <div class="preset-defense-budget-grid">
        ${PRESET_DEFENSE_ACTION_KEYS.map((action) => _renderBudgetRow(action, d.actionBudget?.[action])).join("")}
      </div>
    </section>
    <section class="preset-defense-section">
      <h3>액션별 묶음 선택</h3>
      <div class="preset-defense-policy-grid">
        ${PRESET_DEFENSE_ACTION_KEYS.map((action) => _renderPolicyRow(action, d.actionPolicies?.[action])).join("")}
      </div>
    </section>
    <section class="preset-defense-section">
      <h3>전역 압박</h3>
      ${_renderPressure(d.pressureLevel)}
    </section>
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

  refs.form.querySelectorAll("input[data-defense-budget]").forEach((input) => {
    input.addEventListener("input", () => {
      _setDeepValue(currentDraft, `actionBudget.${input.dataset.defenseBudget}`, Number(input.value || 0));
      const v = _validateDraft(currentDraft);
      currentDraft = v.draft;
      renderPresetDefenseModal(currentDraft, v);
    });
  });

  refs.form.querySelectorAll("button[data-defense-side]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const [action, side] = String(btn.dataset.defenseSide || "").split(":");
      _setDeepValue(currentDraft, `actionPolicies.${action}.side`, side || "neutral");
      const v = _validateDraft(currentDraft);
      currentDraft = v.draft;
      renderPresetDefenseModal(currentDraft, v);
    });
  });

  refs.form.querySelectorAll("button[data-defense-level]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const [action, level] = String(btn.dataset.defenseLevel || "").split(":");
      _setDeepValue(currentDraft, `actionPolicies.${action}.level`, level || "normal");
      const v = _validateDraft(currentDraft);
      currentDraft = v.draft;
      renderPresetDefenseModal(currentDraft, v);
    });
  });

  refs.form.querySelectorAll("input[data-defense-pressure]").forEach((input) => {
    input.addEventListener("input", () => {
      _setDeepValue(currentDraft, "pressureLevel", Number(input.value || 0));
      const v = _validateDraft(currentDraft);
      currentDraft = v.draft;
      renderPresetDefenseModal(currentDraft, v);
    });
  });
}

function openPresetDefenseModal(draft, onApply) {
  const refs = _getRefs();
  if (!refs.modal) return;
  onApplyHandler = typeof onApply === "function" ? onApply : null;
  const v = _validateDraft(draft || createDefaultPresetDefenseDraft());
  currentDraft = v.draft;
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
}

export {
  openPresetDefenseModal,
  closePresetDefenseModal,
  renderPresetDefenseModal,
  bindPresetDefenseModalEvents,
};
