import {
  PRESET_DEFENSE_ACTION_KEYS,
  createDefaultPresetDefenseDraft,
  sanitizePresetDefenseDraft,
} from "./presetDefenseDraft.js";

const DEF_DRAFT_CTX_KEY = "USER_PRESET_DEFENSE_DRAFT_V1";

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function _fromActionWeightMult(raw) {
  const out = {};
  PRESET_DEFENSE_ACTION_KEYS.forEach((action) => {
    const mult = Number(raw?.[action]);
    out[action] = Number.isFinite(mult) ? clamp(Math.round(mult * 100), 0, 100) : 0;
  });
  const sum = PRESET_DEFENSE_ACTION_KEYS.reduce((acc, action) => acc + out[action], 0);
  if (sum <= 0) return null;

  let allocated = 0;
  PRESET_DEFENSE_ACTION_KEYS.forEach((action, idx) => {
    if (idx === PRESET_DEFENSE_ACTION_KEYS.length - 1) {
      out[action] = Math.max(0, 100 - allocated);
      return;
    }
    out[action] = Math.round((out[action] / sum) * 100);
    allocated += out[action];
  });
  return out;
}

function _toPressureLevelFromGlobalMult(raw) {
  const toHandle = Number(raw?.TO_HANDLE_LOSS);
  if (!Number.isFinite(toHandle)) return 0;
  const mapping = [
    { lv: -2, v: 0.85 },
    { lv: -1, v: 0.925 },
    { lv: 0, v: 1.0 },
    { lv: 1, v: 1.075 },
    { lv: 2, v: 1.15 },
  ];
  let best = mapping[0];
  let bestDiff = Math.abs(toHandle - best.v);
  mapping.forEach((m) => {
    const diff = Math.abs(toHandle - m.v);
    if (diff < bestDiff) {
      best = m;
      bestDiff = diff;
    }
  });
  return best.lv;
}

function _pickDraftSnapshot(raw) {
  if (!raw || typeof raw !== "object") return null;
  if (raw.presetDefenseDraft && typeof raw.presetDefenseDraft === "object") return raw.presetDefenseDraft;
  const ctx = raw.context;
  if (ctx && typeof ctx === "object" && ctx[DEF_DRAFT_CTX_KEY] && typeof ctx[DEF_DRAFT_CTX_KEY] === "object") {
    return ctx[DEF_DRAFT_CTX_KEY];
  }
  return null;
}

function _bestEffortDraftFromCompiled(raw) {
  const d = createDefaultPresetDefenseDraft();
  if (!raw || typeof raw !== "object") return d;

  const awm = raw.action_weight_mult && typeof raw.action_weight_mult === "object" ? raw.action_weight_mult : {};
  const ogm = raw.outcome_global_mult && typeof raw.outcome_global_mult === "object" ? raw.outcome_global_mult : {};

  const actionBudget = _fromActionWeightMult(awm);
  if (actionBudget) d.actionBudget = actionBudget;
  d.pressureLevel = _toPressureLevelFromGlobalMult(ogm);

  return d;
}

function defenseDraftFromSavedTactics(raw) {
  const snapshot = _pickDraftSnapshot(raw);
  if (snapshot) return sanitizePresetDefenseDraft(snapshot);
  return sanitizePresetDefenseDraft(_bestEffortDraftFromCompiled(raw));
}

function injectDefenseDraftSnapshotToContext(draft, tactics = {}) {
  const out = { ...(tactics || {}) };
  const clean = sanitizePresetDefenseDraft(draft || createDefaultPresetDefenseDraft());
  out.context = {
    ...(out.context && typeof out.context === "object" ? out.context : {}),
    [DEF_DRAFT_CTX_KEY]: clean,
  };
  out.presetDefenseDraft = clean;
  return out;
}

export {
  DEF_DRAFT_CTX_KEY,
  defenseDraftFromSavedTactics,
  injectDefenseDraftSnapshotToContext,
};
