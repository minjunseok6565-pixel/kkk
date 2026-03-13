import { createDefaultPresetOffenseDraft, sanitizePresetOffenseDraft } from "./presetOffenseDraft.js";

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function _toUi0to100FromMult(mult, lo = 0.9, hi = 1.12) {
  const m = Number(mult);
  if (!Number.isFinite(m)) return null;
  const pct = ((m - lo) / (hi - lo)) * 100;
  return clamp(Math.round(pct), 0, 100);
}

function _pickDraftSnapshot(raw) {
  if (!raw || typeof raw !== "object") return null;
  if (raw.presetOffenseDraft && typeof raw.presetOffenseDraft === "object") return raw.presetOffenseDraft;
  const ctx = raw.context;
  if (ctx && typeof ctx === "object" && ctx.USER_PRESET_OFFENSE_DRAFT_V1 && typeof ctx.USER_PRESET_OFFENSE_DRAFT_V1 === "object") {
    return ctx.USER_PRESET_OFFENSE_DRAFT_V1;
  }
  return null;
}

function _bestEffortDraftFromCompiled(raw) {
  const d = createDefaultPresetOffenseDraft();
  if (!raw || typeof raw !== "object") return d;

  const ctx = raw.context && typeof raw.context === "object" ? raw.context : {};
  const ogm = raw.outcome_global_mult && typeof raw.outcome_global_mult === "object" ? raw.outcome_global_mult : {};

  const tempo = _toUi0to100FromMult(ctx.tempo_mult, 0.9, 1.12);
  if (tempo !== null) d.tempo = tempo;

  const foulFromRim = _toUi0to100FromMult(ogm.FOUL_DRAW_RIM, 0.9, 1.12);
  const foulFromPost = _toUi0to100FromMult(ogm.FOUL_DRAW_POST, 0.9, 1.12);
  if (foulFromRim !== null && foulFromPost !== null) {
    d.foulDraw = Math.round((foulFromRim + foulFromPost) / 2);
  } else if (foulFromRim !== null) {
    d.foulDraw = foulFromRim;
  } else if (foulFromPost !== null) {
    d.foulDraw = foulFromPost;
  }

  const riskFromTo = _toUi0to100FromMult(ogm.TO_HANDLE_LOSS, 0.9, 1.15);
  if (riskFromTo !== null) d.riskTaking = riskFromTo;

  return d;
}

function draftFromSavedTactics(raw) {
  const snapshot = _pickDraftSnapshot(raw);
  if (snapshot) return sanitizePresetOffenseDraft(snapshot);
  return sanitizePresetOffenseDraft(_bestEffortDraftFromCompiled(raw));
}

function injectDraftSnapshotToContext(draft, tactics = {}) {
  const out = { ...(tactics || {}) };
  const clean = sanitizePresetOffenseDraft(draft || createDefaultPresetOffenseDraft());
  out.context = {
    ...(out.context && typeof out.context === "object" ? out.context : {}),
    USER_PRESET_OFFENSE_DRAFT_V1: clean,
  };
  out.presetOffenseDraft = clean;
  return out;
}

export {
  draftFromSavedTactics,
  injectDraftSnapshotToContext,
};
