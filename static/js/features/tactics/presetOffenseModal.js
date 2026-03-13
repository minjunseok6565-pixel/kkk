import { els } from "../../app/dom.js";
import { createDefaultPresetOffenseDraft, sanitizePresetOffenseDraft } from "./presetOffenseDraft.js";
import { validatePresetOffenseDraft } from "./presetOffenseConstraints.js";

let currentDraft = createDefaultPresetOffenseDraft();
let onApplyHandler = null;
let isBound = false;

function getFocusableModalElements() {
  if (!els.presetOffenseModal) return [];
  return [...els.presetOffenseModal.querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])")]
    .filter((el) => !el.hasAttribute("disabled") && !el.classList.contains("hidden"));
}

function updateDraftPath(path, value) {
  if (!path) return;
  const [root, key] = String(path).split(".");
  if (root === "actionVolume" && key) {
    currentDraft.actionVolume[key] = Number(value || 0);
  } else {
    currentDraft[root] = Number(value || 0);
  }
  currentDraft = sanitizePresetOffenseDraft(currentDraft);
}

function renderPresetOffenseModal(draft, validation = null) {
  currentDraft = sanitizePresetOffenseDraft(draft || currentDraft || createDefaultPresetOffenseDraft());
  if (!els.presetOffenseForm) return;

  const d = currentDraft;
  els.presetOffenseForm.innerHTML = `
    <div class="preset-offense-grid">
      <label>픽앤롤 <input data-preset-field="actionVolume.pnrFamily" type="range" min="0" max="100" value="${d.actionVolume.pnrFamily}" /></label>
      <label>드라이브 <input data-preset-field="actionVolume.drive" type="range" min="0" max="100" value="${d.actionVolume.drive}" /></label>
      <label>속공 <input data-preset-field="actionVolume.transition" type="range" min="0" max="100" value="${d.actionVolume.transition}" /></label>
      <label>아이솔 <input data-preset-field="actionVolume.iso" type="range" min="0" max="100" value="${d.actionVolume.iso}" /></label>
      <label>포스트업 <input data-preset-field="actionVolume.postUp" type="range" min="0" max="100" value="${d.actionVolume.postUp}" /></label>
      <label>파울 유도 <input data-preset-field="foulDraw" type="range" min="0" max="100" value="${d.foulDraw}" /></label>
      <label>위험 감수 <input data-preset-field="riskTaking" type="range" min="0" max="100" value="${d.riskTaking}" /></label>
      <label>템포 <input data-preset-field="tempo" type="range" min="0" max="100" value="${d.tempo}" /></label>
    </div>
    <div class="preset-offense-toggle-row">
      <span class="preset-offense-chip">Pass 빈도: ${d.passFreq}</span>
      <span class="preset-offense-chip">Offball 빈도: ${d.offballFreq}</span>
    </div>
  `;

  if (els.presetOffenseErrors) {
    if (validation?.errors?.length) {
      els.presetOffenseErrors.textContent = `적용 불가: ${validation.errors.join(" / ")}`;
    } else if (validation?.warnings?.length) {
      els.presetOffenseErrors.textContent = `자동 조정 ${validation.warnings.length}건: ${validation.warnings[0]}`;
    } else {
      els.presetOffenseErrors.textContent = "";
    }
  }

  els.presetOffenseForm.querySelectorAll("input[data-preset-field]").forEach((input) => {
    input.addEventListener("input", () => {
      updateDraftPath(input.dataset.presetField, input.value);
      const v = validatePresetOffenseDraft(currentDraft);
      if (els.presetOffenseErrors && v.warnings.length) {
        els.presetOffenseErrors.textContent = `자동 조정 ${v.warnings.length}건: ${v.warnings[0]}`;
      }
    });
  });
}

function openPresetOffenseModal(draft, onApply) {
  if (!els.presetOffenseModal) return;
  onApplyHandler = typeof onApply === "function" ? onApply : null;
  currentDraft = sanitizePresetOffenseDraft(draft || createDefaultPresetOffenseDraft());
  renderPresetOffenseModal(currentDraft, null);
  els.presetOffenseModal.classList.remove("hidden");
  els.presetOffenseModal.setAttribute("aria-hidden", "false");
  const focusables = getFocusableModalElements();
  focusables[0]?.focus();
}

function closePresetOffenseModal() {
  if (!els.presetOffenseModal) return;
  els.presetOffenseModal.classList.add("hidden");
  els.presetOffenseModal.setAttribute("aria-hidden", "true");
  els.presetOffenseOpenBtn?.focus();
}

function onModalKeydown(event) {
  if (!els.presetOffenseModal || els.presetOffenseModal.classList.contains("hidden")) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closePresetOffenseModal();
    return;
  }
  if (event.key !== "Tab") return;
  const focusables = getFocusableModalElements();
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

function bindPresetOffenseModalEvents() {
  if (isBound) return;
  isBound = true;

  els.presetOffenseCloseBtn?.addEventListener("click", () => closePresetOffenseModal());
  els.presetOffenseModalBackdrop?.addEventListener("click", () => closePresetOffenseModal());
  els.presetOffenseResetBtn?.addEventListener("click", () => {
    currentDraft = createDefaultPresetOffenseDraft();
    renderPresetOffenseModal(currentDraft, null);
    if (els.presetOffenseErrors) {
      els.presetOffenseErrors.textContent = "초기값으로 되돌렸습니다. 적용을 눌러 반영하세요.";
    }
  });
  els.presetOffenseApplyBtn?.addEventListener("click", () => {
    const validation = validatePresetOffenseDraft(currentDraft);
    currentDraft = sanitizePresetOffenseDraft(validation.draft);
    if (!validation.ok) {
      renderPresetOffenseModal(currentDraft, validation);
      return;
    }
    if (onApplyHandler) {
      onApplyHandler({ ...currentDraft }, validation);
    }
    closePresetOffenseModal();
  });

  document.addEventListener("keydown", onModalKeydown);
}

export {
  openPresetOffenseModal,
  closePresetOffenseModal,
  renderPresetOffenseModal,
  bindPresetOffenseModalEvents,
};
