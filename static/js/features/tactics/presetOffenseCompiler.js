import { createDefaultPresetOffenseDraft, sanitizePresetOffenseDraft } from "./presetOffenseDraft.js";

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function mapLevelToMult(level, curve = "default") {
  const key = String(curve || "default");
  const lv = String(level || "mid");
  const table = key === "wide"
    ? { low: 0.9, mid: 1.0, high: 1.1 }
    : { low: 0.92, mid: 1.0, high: 1.08 };
  return table[lv] || table.mid;
}

function buildActionWeightMult(draft) {
  const d = sanitizePresetOffenseDraft(draft);
  const av = d.actionVolume;
  const sum5 = Math.max(1, av.pnrFamily + av.drive + av.transition + av.iso + av.postUp);
  const familyShare = av.pnrFamily / sum5;
  return {
    PnR: clamp((familyShare * (d.pnrSplit.pnr / 100)) / 0.10, 0.85, 1.15),
    PnP: clamp((familyShare * (d.pnrSplit.pnp / 100)) / 0.10, 0.85, 1.15),
    Drive: clamp((av.drive / sum5) / 0.20, 0.85, 1.15),
    TransitionEarly: clamp((av.transition / sum5) / 0.20, 0.85, 1.15),
    ISO: clamp((av.iso / sum5) / 0.20, 0.85, 1.15),
    PostUp: clamp((av.postUp / sum5) / 0.20, 0.85, 1.15),
    Cut: clamp(mapLevelToMult(d.offballFreq) * (d.offballSplit.cut / 34), 0.85, 1.15),
    SpotUp: clamp(mapLevelToMult(d.offballFreq) * (d.offballSplit.spotUp / 33), 0.85, 1.15),
    DHO: clamp(mapLevelToMult(d.offballFreq) * (d.offballSplit.dho / 33), 0.85, 1.15),
  };
}

function buildOutcomeByActionMult(draft) {
  const d = sanitizePresetOffenseDraft(draft);
  return {
    PnR: {
      PASS_SHORTROLL: clamp(mapLevelToMult(d.outcomes.pnr.rollPass), 0.88, 1.12),
      SHOT_3_OD: clamp(mapLevelToMult(d.outcomes.pnr.handlerDirect) * (d.outcomes.pnr.pullupSplit.pull3 / 50), 0.88, 1.12),
      SHOT_MID_PU: clamp(mapLevelToMult(d.outcomes.pnr.handlerDirect) * (d.outcomes.pnr.pullupSplit.pull2 / 50), 0.88, 1.12),
      SHOT_RIM_LAYUP: clamp(mapLevelToMult(d.outcomes.pnr.handlerDirect) * (d.outcomes.pnr.rimVsFloater.rim / 50), 0.88, 1.12),
      SHOT_TOUCH_FLOATER: clamp(mapLevelToMult(d.outcomes.pnr.handlerDirect) * (d.outcomes.pnr.rimVsFloater.floater / 50), 0.88, 1.12),
    },
    PnP: {
      SHOT_3_CS: clamp(mapLevelToMult(d.outcomes.pnp.popOut), 0.88, 1.12),
      SHOT_3_OD: clamp(mapLevelToMult(d.outcomes.pnp.handlerDirect) * (d.outcomes.pnp.pullupSplit.pull3 / 50), 0.88, 1.12),
      SHOT_MID_PU: clamp(mapLevelToMult(d.outcomes.pnp.handlerDirect) * (d.outcomes.pnp.pullupSplit.pull2 / 50), 0.88, 1.12),
    },
    TransitionEarly: {
      SHOT_3_CS: clamp(mapLevelToMult(d.outcomes.transitionEarly.openChance3), 0.88, 1.12),
      SHOT_3_OD: clamp(mapLevelToMult(d.outcomes.transitionEarly.handlerDirect) * (d.outcomes.transitionEarly.directSplit.trans3 / 34), 0.88, 1.12),
      SHOT_RIM_LAYUP: clamp(mapLevelToMult(d.outcomes.transitionEarly.handlerDirect) * (d.outcomes.transitionEarly.directSplit.rim / 33), 0.88, 1.12),
      SHOT_TOUCH_FLOATER: clamp(mapLevelToMult(d.outcomes.transitionEarly.handlerDirect) * (d.outcomes.transitionEarly.directSplit.floater / 33), 0.88, 1.12),
    },
  };
}

function buildOutcomeGlobalMult(draft) {
  const d = sanitizePresetOffenseDraft(draft);
  return {
    FOUL_DRAW_RIM: clamp(0.9 + (d.foulDraw / 100) * 0.22, 0.9, 1.12),
    FOUL_DRAW_POST: clamp(0.9 + (d.foulDraw / 100) * 0.22, 0.9, 1.12),
    TO_HANDLE_LOSS: clamp(0.9 + (d.riskTaking / 100) * 0.25, 0.9, 1.15),
    TO_CHARGE: clamp(1.08 - (d.riskTaking / 100) * 0.18, 0.9, 1.15),
  };
}

function buildContextPatch(draft) {
  const d = sanitizePresetOffenseDraft(draft);
  return {
    tempo_mult: clamp(0.9 + (d.tempo / 100) * 0.22, 0.9, 1.12),
    USER_PRESET_OFFENSE_DRAFT_V1: d,
  };
}

function compilePresetOffenseDraft(draft, baseTactics = {}) {
  const d = sanitizePresetOffenseDraft(draft || createDefaultPresetOffenseDraft());
  return {
    action_weight_mult: buildActionWeightMult(d),
    outcome_by_action_mult: buildOutcomeByActionMult(d),
    outcome_global_mult: buildOutcomeGlobalMult(d),
    context: {
      ...(baseTactics?.context || {}),
      ...buildContextPatch(d),
    },
  };
}

function mergeCompiledPresetIntoTactics(base, compiled) {
  const out = { ...(base || {}) };
  out.action_weight_mult = { ...(out.action_weight_mult || {}), ...(compiled?.action_weight_mult || {}) };
  out.outcome_by_action_mult = { ...(out.outcome_by_action_mult || {}), ...(compiled?.outcome_by_action_mult || {}) };
  out.outcome_global_mult = { ...(out.outcome_global_mult || {}), ...(compiled?.outcome_global_mult || {}) };
  out.context = { ...(out.context || {}), ...(compiled?.context || {}) };
  return out;
}

export {
  compilePresetOffenseDraft,
  mergeCompiledPresetIntoTactics,
  mapLevelToMult,
  buildActionWeightMult,
  buildOutcomeByActionMult,
  buildOutcomeGlobalMult,
  buildContextPatch,
};
