import { els } from "../../app/dom.js";
import { createDefaultPresetOffenseDraft, sanitizePresetOffenseDraft } from "./presetOffenseDraft.js";
import { validatePresetOffenseDraft } from "./presetOffenseConstraints.js";

let currentDraft = createDefaultPresetOffenseDraft();
let onApplyHandler = null;
let isBound = false;

const LEVELS = ["high", "mid", "low"];

function getFocusableModalElements() {
  if (!els.presetOffenseModal) return [];
  return [...els.presetOffenseModal.querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])")]
    .filter((el) => !el.hasAttribute("disabled") && !el.classList.contains("hidden"));
}

function setDeepValue(target, path, value) {
  const keys = String(path || "").split(".").filter(Boolean);
  if (!keys.length) return;
  let cursor = target;
  for (let i = 0; i < keys.length - 1; i += 1) {
    const key = keys[i];
    if (!cursor[key] || typeof cursor[key] !== "object") cursor[key] = {};
    cursor = cursor[key];
  }
  cursor[keys[keys.length - 1]] = value;
}

function updateDraftPath(path, value, kind = "number") {
  if (!path) return;
  const parsed = kind === "level" ? String(value || "mid") : Number(value || 0);
  setDeepValue(currentDraft, path, parsed);
  currentDraft = sanitizePresetOffenseDraft(currentDraft);
}

function levelText(level) {
  return level === "high" ? "많이" : level === "mid" ? "보통" : "적게";
}

function hasWarningFor(validation, key) {
  const warnings = validation?.warnings || [];
  return warnings.some((w) => String(w || "").includes(`[${key}]`));
}

function renderLevelGroup(path, value, title, validation = null, ruleKey = "") {
  const warn = ruleKey ? hasWarningFor(validation, ruleKey) : false;
  return `
    <div class="preset-offense-level-group ${warn ? "is-warning" : ""}" role="group" aria-label="${title}">
      <span class="preset-offense-level-label">${title}${warn ? ' <em class="preset-offense-rule-badge">자동조정</em>' : ""}</span>
      <div class="preset-offense-level-buttons">
        ${LEVELS.map((level) => `
          <button
            type="button"
            data-preset-level="${path}"
            data-preset-value="${level}"
            class="preset-level-btn ${value === level ? "is-active" : ""}"
          >${levelText(level)}</button>
        `).join("")}
      </div>
    </div>
  `;
}

function renderSlider(path, value, title) {
  return `<label>${title} <input data-preset-field="${path}" type="range" min="0" max="100" value="${value}" /></label>`;
}

function renderConstraintSummary(validation) {
  const statuses = [
    { key: "pnrPair", label: "PnR 쌍 극단값 금지" },
    { key: "pnpPair", label: "PnP 쌍 극단값 금지" },
    { key: "transitionPair", label: "Transition 쌍 극단값 금지" },
    { key: "drive", label: "Drive high 2개 시 나머지 low" },
    { key: "iso", label: "ISO high 2개 시 나머지 mid/low" },
    { key: "postUp", label: "PostUp high 2개 시 나머지 low" },
  ];
  return `
    <div class="preset-offense-constraint-summary" aria-live="polite">
      ${statuses.map((s) => {
        const warn = hasWarningFor(validation, s.key);
        return `<span class="preset-constraint-pill ${warn ? "is-warning" : "is-ok"}">${s.label}: ${warn ? "자동조정" : "OK"}</span>`;
      }).join("")}
    </div>
  `;
}

function renderPresetOffenseModal(draft, validation = null) {
  currentDraft = sanitizePresetOffenseDraft(draft || currentDraft || createDefaultPresetOffenseDraft());
  if (!els.presetOffenseForm) return;

  const d = currentDraft;
  els.presetOffenseForm.innerHTML = `
    ${renderConstraintSummary(validation)}
    <div class="preset-offense-grid">
      ${renderSlider("actionVolume.pnrFamily", d.actionVolume.pnrFamily, "픽앤롤")}
      ${renderSlider("actionVolume.drive", d.actionVolume.drive, "드라이브")}
      ${renderSlider("actionVolume.transition", d.actionVolume.transition, "속공")}
      ${renderSlider("actionVolume.iso", d.actionVolume.iso, "아이솔")}
      ${renderSlider("actionVolume.postUp", d.actionVolume.postUp, "포스트업")}
      ${renderSlider("pnrSplit.pnr", d.pnrSplit.pnr, "PnR 비중")}
      ${renderSlider("pnrSplit.pnp", d.pnrSplit.pnp, "PnP 비중")}
      ${renderSlider("offballSplit.cut", d.offballSplit.cut, "Offball Cut")}
      ${renderSlider("offballSplit.spotUp", d.offballSplit.spotUp, "Offball SpotUp")}
      ${renderSlider("offballSplit.dho", d.offballSplit.dho, "Offball DHO")}
      ${renderSlider("outcomes.pnr.rimVsFloater.rim", d.outcomes.pnr.rimVsFloater.rim, "PnR 림 비중")}
      ${renderSlider("outcomes.pnr.rimVsFloater.floater", d.outcomes.pnr.rimVsFloater.floater, "PnR 플로터 비중")}
      ${renderSlider("outcomes.pnr.pullupSplit.pull3", d.outcomes.pnr.pullupSplit.pull3, "PnR 풀업 3점")}
      ${renderSlider("outcomes.pnr.pullupSplit.pull2", d.outcomes.pnr.pullupSplit.pull2, "PnR 풀업 2점")}
      ${renderSlider("outcomes.pnp.rimVsFloater.rim", d.outcomes.pnp.rimVsFloater.rim, "PnP 림 비중")}
      ${renderSlider("outcomes.pnp.rimVsFloater.floater", d.outcomes.pnp.rimVsFloater.floater, "PnP 플로터 비중")}
      ${renderSlider("outcomes.pnp.pullupSplit.pull3", d.outcomes.pnp.pullupSplit.pull3, "PnP 풀업 3점")}
      ${renderSlider("outcomes.pnp.pullupSplit.pull2", d.outcomes.pnp.pullupSplit.pull2, "PnP 풀업 2점")}
      ${renderSlider("outcomes.transitionEarly.directSplit.trans3", d.outcomes.transitionEarly.directSplit.trans3, "속공 직공 3점")}
      ${renderSlider("outcomes.transitionEarly.directSplit.rim", d.outcomes.transitionEarly.directSplit.rim, "속공 직공 림")}
      ${renderSlider("outcomes.transitionEarly.directSplit.floater", d.outcomes.transitionEarly.directSplit.floater, "속공 직공 플로터")}
      ${renderSlider("outcomes.iso.pullupSplit.pull3", d.outcomes.iso.pullupSplit.pull3, "ISO 풀업 3점")}
      ${renderSlider("outcomes.iso.pullupSplit.pull2", d.outcomes.iso.pullupSplit.pull2, "ISO 풀업 2점")}
      ${renderSlider("foulDraw", d.foulDraw, "파울 유도")}
      ${renderSlider("riskTaking", d.riskTaking, "위험 감수")}
      ${renderSlider("tempo", d.tempo, "템포")}
    </div>
    <div class="preset-offense-toggle-row">
      ${renderLevelGroup("passFreq", d.passFreq, "Pass 빈도", validation)}
      ${renderLevelGroup("offballFreq", d.offballFreq, "Off-ball 빈도", validation)}
      ${renderLevelGroup("outcomes.pnr.handlerDirect", d.outcomes.pnr.handlerDirect, "PnR 핸들러 직공", validation, "pnrPair")}
      ${renderLevelGroup("outcomes.pnr.rollPass", d.outcomes.pnr.rollPass, "PnR 롤맨 패스", validation, "pnrPair")}
      ${renderLevelGroup("outcomes.pnp.handlerDirect", d.outcomes.pnp.handlerDirect, "PnP 핸들러 직공", validation, "pnpPair")}
      ${renderLevelGroup("outcomes.pnp.popOut", d.outcomes.pnp.popOut, "PnP 팝아웃", validation, "pnpPair")}
      ${renderLevelGroup("outcomes.transitionEarly.handlerDirect", d.outcomes.transitionEarly.handlerDirect, "속공 핸들러 직공", validation, "transitionPair")}
      ${renderLevelGroup("outcomes.transitionEarly.openChance3", d.outcomes.transitionEarly.openChance3, "속공 오픈 3점", validation, "transitionPair")}
      ${renderLevelGroup("outcomes.drive.rim", d.outcomes.drive.rim, "Drive 림어택", validation, "drive")}
      ${renderLevelGroup("outcomes.drive.kickout", d.outcomes.drive.kickout, "Drive 킥아웃", validation, "drive")}
      ${renderLevelGroup("outcomes.drive.pull2", d.outcomes.drive.pull2, "Drive 풀업2", validation, "drive")}
      ${renderLevelGroup("outcomes.iso.rim", d.outcomes.iso.rim, "ISO 림어택", validation, "iso")}
      ${renderLevelGroup("outcomes.iso.floater", d.outcomes.iso.floater, "ISO 플로터", validation, "iso")}
      ${renderLevelGroup("outcomes.iso.pullup", d.outcomes.iso.pullup, "ISO 풀업", validation, "iso")}
      ${renderLevelGroup("outcomes.iso.kickout", d.outcomes.iso.kickout, "ISO 킥아웃", validation, "iso")}
      ${renderLevelGroup("outcomes.cut.finish", d.outcomes.cut.finish, "Cut 마무리", validation)}
      ${renderLevelGroup("outcomes.cut.pass", d.outcomes.cut.pass, "Cut 패스", validation)}
      ${renderLevelGroup("outcomes.postUp.postFinish", d.outcomes.postUp.postFinish, "PostUp 마무리", validation, "postUp")}
      ${renderLevelGroup("outcomes.postUp.postFadeway", d.outcomes.postUp.postFadeway, "PostUp 페이더웨이", validation, "postUp")}
      ${renderLevelGroup("outcomes.postUp.pass", d.outcomes.postUp.pass, "PostUp 패스", validation, "postUp")}
    </div>
  `;

  if (els.presetOffenseErrors) {
    if (validation?.errors?.length) {
      els.presetOffenseErrors.textContent = `적용 불가: ${validation.errors.join(" / ")}`;
    } else if (validation?.warnings?.length) {
      const cleaned = validation.warnings.map((w) => String(w || "").replace(/^\[[^\]]+\]\s*/, ""));
      els.presetOffenseErrors.textContent = `자동 조정 ${validation.warnings.length}건: ${cleaned[0]}`;
    } else {
      els.presetOffenseErrors.textContent = "";
    }
  }

  els.presetOffenseForm.querySelectorAll("input[data-preset-field]").forEach((input) => {
    input.addEventListener("input", () => {
      updateDraftPath(input.dataset.presetField, input.value);
      const v = validatePresetOffenseDraft(currentDraft);
      currentDraft = sanitizePresetOffenseDraft(v.draft);
      renderPresetOffenseModal(currentDraft, v);
    });
  });

  els.presetOffenseForm.querySelectorAll("button[data-preset-level]").forEach((btn) => {
    btn.addEventListener("click", () => {
      updateDraftPath(btn.dataset.presetLevel, btn.dataset.presetValue, "level");
      const v = validatePresetOffenseDraft(currentDraft);
      currentDraft = sanitizePresetOffenseDraft(v.draft);
      renderPresetOffenseModal(currentDraft, v);
    });
  });
}

function openPresetOffenseModal(draft, onApply) {
  if (!els.presetOffenseModal) return;
  onApplyHandler = typeof onApply === "function" ? onApply : null;
  const initialValidation = validatePresetOffenseDraft(draft || createDefaultPresetOffenseDraft());
  currentDraft = sanitizePresetOffenseDraft(initialValidation.draft);
  renderPresetOffenseModal(currentDraft, initialValidation);
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
    const v = validatePresetOffenseDraft(createDefaultPresetOffenseDraft());
    currentDraft = sanitizePresetOffenseDraft(v.draft);
    renderPresetOffenseModal(currentDraft, v);
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
