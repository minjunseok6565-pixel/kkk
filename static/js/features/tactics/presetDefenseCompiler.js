import {
  PRESET_DEFENSE_ACTION_KEYS,
  PRESET_DEFENSE_POLICY_LEVEL,
  PRESET_DEFENSE_POLICY_SIDE,
  createDefaultPresetDefenseDraft,
  sanitizePresetDefenseDraft,
} from "./presetDefenseDraft.js";

const DEF_QUALITY_LABEL_OVERRIDES_CTX_KEY = "DEF_QUALITY_LABEL_OVERRIDES_V1";
const DEF_QUALITY_LABEL_OVERRIDES_VERSION = 1;

const PRESET_DEFENSE_ACTION_TABLE = {
  Cut: {
    groupA: {
      outcomes: ["SHOT_RIM_CONTACT", "SHOT_RIM_DUNK", "SHOT_RIM_LAYUP"],
      mult: { allow: 1.08, strongAllow: 1.1, suppress: 0.92, strongSuppress: 0.9 },
      labels: {
        allow: { weak: ["PASS_KICKOUT"], tight: ["SHOT_RIM_LAYUP"] },
        strongAllow: { weak: ["PASS_KICKOUT", "PASS_EXTRA"], tight: ["SHOT_RIM_LAYUP", "SHOT_RIM_CONTACT"] },
      },
    },
    groupB: {
      outcomes: ["PASS_EXTRA", "PASS_KICKOUT"],
      mult: { allow: 1.12, strongAllow: 1.15, suppress: 0.88, strongSuppress: 0.85 },
      labels: {
        allow: { weak: ["SHOT_RIM_LAYUP"], tight: ["PASS_KICKOUT"] },
        strongAllow: { weak: ["SHOT_RIM_LAYUP", "SHOT_RIM_CONTACT"], tight: ["PASS_KICKOUT", "PASS_EXTRA"] },
      },
    },
  },
  DHO: {
    groupA: {
      outcomes: ["SHOT_RIM_DUNK", "SHOT_RIM_LAYUP", "SHOT_TOUCH_FLOATER", "SHOT_POST"],
      mult: { allow: 1.06, strongAllow: 1.075, suppress: 0.94, strongSuppress: 0.925 },
      labels: {
        allow: { weak: ["SHOT_3_CS"], tight: ["SHOT_RIM_LAYUP"] },
        strongAllow: { weak: ["SHOT_3_CS", "SHOT_MID_PU"], tight: ["SHOT_RIM_LAYUP", "SHOT_TOUCH_FLOATER"] },
      },
    },
    groupB: {
      outcomes: ["SHOT_MID_PU", "SHOT_3_CS", "SHOT_3_OD"],
      mult: { allow: 1.08, strongAllow: 1.1, suppress: 0.92, strongSuppress: 0.9 },
      labels: {
        allow: { weak: ["SHOT_RIM_LAYUP"], tight: ["SHOT_3_CS"] },
        strongAllow: { weak: ["SHOT_RIM_LAYUP", "SHOT_TOUCH_FLOATER"], tight: ["SHOT_3_CS", "SHOT_MID_PU"] },
      },
    },
  },
  Drive: {
    groupA: {
      outcomes: ["SHOT_RIM_CONTACT", "SHOT_RIM_DUNK", "SHOT_RIM_LAYUP"],
      mult: { allow: 1.08, strongAllow: 1.1, suppress: 0.92, strongSuppress: 0.9 },
      labels: {
        allow: { weak: ["SHOT_MID_PU"], tight: ["SHOT_RIM_CONTACT"] },
        strongAllow: { weak: ["SHOT_MID_PU", "PASS_KICKOUT"], tight: ["SHOT_RIM_CONTACT", "SHOT_RIM_DUNK"] },
      },
    },
    groupB: {
      outcomes: ["SHOT_MID_PU", "SHOT_3_OD", "PASS_EXTRA", "PASS_KICKOUT"],
      mult: { allow: 1.06, strongAllow: 1.075, suppress: 0.94, strongSuppress: 0.925 },
      labels: {
        allow: { weak: ["SHOT_RIM_CONTACT"], tight: ["SHOT_MID_PU"] },
        strongAllow: { weak: ["SHOT_RIM_CONTACT", "SHOT_RIM_DUNK"], tight: ["SHOT_MID_PU", "PASS_KICKOUT"] },
      },
    },
  },
  ISO: {
    groupA: {
      outcomes: ["SHOT_RIM_CONTACT", "SHOT_RIM_DUNK", "SHOT_RIM_LAYUP"],
      mult: { allow: 1.08, strongAllow: 1.1, suppress: 0.92, strongSuppress: 0.9 },
      labels: {
        allow: { weak: ["SHOT_3_OD"], tight: ["SHOT_RIM_CONTACT"] },
        strongAllow: { weak: ["SHOT_3_OD", "PASS_EXTRA"], tight: ["SHOT_RIM_CONTACT", "SHOT_RIM_DUNK"] },
      },
    },
    groupB: {
      outcomes: ["SHOT_MID_PU", "SHOT_3_OD", "PASS_EXTRA", "PASS_KICKOUT"],
      mult: { allow: 1.06, strongAllow: 1.075, suppress: 0.94, strongSuppress: 0.925 },
      labels: {
        allow: { weak: ["SHOT_RIM_CONTACT"], tight: ["SHOT_3_OD"] },
        strongAllow: { weak: ["SHOT_RIM_CONTACT", "SHOT_RIM_DUNK"], tight: ["SHOT_3_OD", "PASS_EXTRA"] },
      },
    },
  },
  PnP: {
    groupA: {
      outcomes: ["SHOT_RIM_DUNK", "SHOT_RIM_LAYUP", "SHOT_RIM_CONTACT"],
      mult: { allow: 1.08, strongAllow: 1.1, suppress: 0.92, strongSuppress: 0.9 },
      labels: {
        allow: { weak: ["SHOT_3_CS"], tight: ["SHOT_RIM_LAYUP"] },
        strongAllow: { weak: ["SHOT_3_CS", "SHOT_MID_CS"], tight: ["SHOT_RIM_LAYUP", "SHOT_RIM_CONTACT"] },
      },
    },
    groupB: {
      outcomes: ["SHOT_MID_CS", "SHOT_3_CS", "SHOT_MID_PU", "SHOT_3_OD"],
      mult: { allow: 1.06, strongAllow: 1.075, suppress: 0.94, strongSuppress: 0.925 },
      labels: {
        allow: { weak: ["SHOT_RIM_LAYUP"], tight: ["SHOT_3_CS"] },
        strongAllow: { weak: ["SHOT_RIM_LAYUP", "SHOT_RIM_CONTACT"], tight: ["SHOT_3_CS", "SHOT_MID_CS"] },
      },
    },
  },
  PnR: {
    groupA: {
      outcomes: ["SHOT_RIM_DUNK", "SHOT_RIM_LAYUP", "SHOT_RIM_CONTACT", "PASS_SHORTROLL"],
      mult: { allow: 1.06, strongAllow: 1.075, suppress: 0.94, strongSuppress: 0.925 },
      labels: {
        allow: { weak: ["SHOT_MID_PU"], tight: ["SHOT_RIM_LAYUP"] },
        strongAllow: { weak: ["SHOT_3_OD", "SHOT_MID_PU"], tight: ["SHOT_RIM_LAYUP", "PASS_SHORTROLL"] },
      },
    },
    groupB: {
      outcomes: ["SHOT_MID_PU", "SHOT_3_OD", "SHOT_TOUCH_FLOATER"],
      mult: { allow: 1.08, strongAllow: 1.1, suppress: 0.92, strongSuppress: 0.9 },
      labels: {
        allow: { weak: ["SHOT_RIM_LAYUP"], tight: ["SHOT_MID_PU"] },
        strongAllow: { weak: ["SHOT_RIM_LAYUP", "PASS_SHORTROLL"], tight: ["SHOT_3_OD", "SHOT_MID_PU"] },
      },
    },
  },
  PostUp: {
    groupA: {
      outcomes: ["SHOT_POST", "SHOT_MID_PU"],
      mult: { allow: 1.12, strongAllow: 1.15, suppress: 0.88, strongSuppress: 0.85 },
      labels: {
        allow: { weak: ["PASS_KICKOUT", "PASS_SKIP"], tight: ["SHOT_POST"] },
        strongAllow: { weak: ["PASS_KICKOUT", "PASS_SKIP", "PASS_EXTRA"], tight: ["SHOT_POST", "SHOT_MID_PU"] },
      },
    },
    groupB: {
      outcomes: ["PASS_EXTRA", "PASS_KICKOUT", "PASS_SKIP"],
      mult: { allow: 1.08, strongAllow: 1.1, suppress: 0.92, strongSuppress: 0.9 },
      labels: {
        allow: { weak: ["SHOT_POST"], tight: ["PASS_KICKOUT", "PASS_SKIP"] },
        strongAllow: { weak: ["SHOT_POST", "SHOT_MID_PU"], tight: ["PASS_KICKOUT", "PASS_SKIP", "PASS_EXTRA"] },
      },
    },
  },
  SpotUp: {
    groupA: {
      outcomes: ["SHOT_MID_CS", "SHOT_3_CS"],
      mult: { allow: 1.12, strongAllow: 1.15, suppress: 0.88, strongSuppress: 0.85 },
      labels: {
        allow: { weak: ["SHOT_3_OD"], tight: ["SHOT_3_CS"] },
        strongAllow: { weak: ["SHOT_3_OD", "SHOT_MID_PU"], tight: ["SHOT_3_CS", "SHOT_MID_CS"] },
      },
    },
    groupB: {
      outcomes: ["SHOT_MID_PU", "SHOT_3_OD"],
      mult: { allow: 1.12, strongAllow: 1.15, suppress: 0.88, strongSuppress: 0.85 },
      labels: {
        allow: { weak: ["SHOT_3_CS"], tight: ["SHOT_3_OD"] },
        strongAllow: { weak: ["SHOT_3_CS", "SHOT_MID_CS"], tight: ["SHOT_3_OD", "SHOT_MID_PU"] },
      },
    },
  },
  TransitionEarly: {
    groupA: {
      outcomes: ["SHOT_RIM_CONTACT", "SHOT_RIM_DUNK", "SHOT_RIM_LAYUP", "SHOT_TOUCH_FLOATER"],
      mult: { allow: 1.06, strongAllow: 1.075, suppress: 0.94, strongSuppress: 0.925 },
      labels: {
        allow: { weak: ["SHOT_3_OD"], tight: ["SHOT_RIM_CONTACT"] },
        strongAllow: { weak: ["SHOT_3_OD", "SHOT_3_CS"], tight: ["SHOT_RIM_CONTACT", "SHOT_RIM_DUNK"] },
      },
    },
    groupB: {
      outcomes: ["SHOT_3_CS", "SHOT_3_OD", "PASS_KICKOUT"],
      mult: { allow: 1.08, strongAllow: 1.1, suppress: 0.92, strongSuppress: 0.9 },
      labels: {
        allow: { weak: ["SHOT_RIM_CONTACT"], tight: ["SHOT_3_OD"] },
        strongAllow: { weak: ["SHOT_RIM_CONTACT", "SHOT_RIM_DUNK"], tight: ["SHOT_3_OD", "SHOT_3_CS"] },
      },
    },
  },
};

const PRESSURE_MULT_TABLE = {
  "-2": {
    TO_HANDLE_LOSS: 0.85,
    TO_CHARGE: 0.85,
    FOUL_DRAW_RIM: 0.9,
    FOUL_DRAW_JUMPER: 0.9,
    FOUL_DRAW_POST: 0.9,
    FOUL_REACH_TRAP: 0.9,
  },
  "-1": {
    TO_HANDLE_LOSS: 0.925,
    TO_CHARGE: 0.925,
    FOUL_DRAW_RIM: 0.95,
    FOUL_DRAW_JUMPER: 0.95,
    FOUL_DRAW_POST: 0.95,
    FOUL_REACH_TRAP: 0.95,
  },
  "0": {
    TO_HANDLE_LOSS: 1.0,
    TO_CHARGE: 1.0,
    FOUL_DRAW_RIM: 1.0,
    FOUL_DRAW_JUMPER: 1.0,
    FOUL_DRAW_POST: 1.0,
    FOUL_REACH_TRAP: 1.0,
  },
  "1": {
    TO_HANDLE_LOSS: 1.075,
    TO_CHARGE: 1.075,
    FOUL_DRAW_RIM: 1.05,
    FOUL_DRAW_JUMPER: 1.05,
    FOUL_DRAW_POST: 1.05,
    FOUL_REACH_TRAP: 1.05,
  },
  "2": {
    TO_HANDLE_LOSS: 1.15,
    TO_CHARGE: 1.15,
    FOUL_DRAW_RIM: 1.1,
    FOUL_DRAW_JUMPER: 1.1,
    FOUL_DRAW_POST: 1.1,
    FOUL_REACH_TRAP: 1.1,
  },
};

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function _resolvePolicy(actionPolicy) {
  const side = String(actionPolicy?.side || PRESET_DEFENSE_POLICY_SIDE.NEUTRAL);
  const level = String(actionPolicy?.level || PRESET_DEFENSE_POLICY_LEVEL.NORMAL);
  const isStrong = level === PRESET_DEFENSE_POLICY_LEVEL.STRONG;
  const suppressKey = isStrong ? "strongSuppress" : "suppress";
  const allowKey = isStrong ? "strongAllow" : "allow";
  const labelKey = isStrong ? "strongAllow" : "allow";

  if (side === PRESET_DEFENSE_POLICY_SIDE.A) {
    return { suppressGroup: "groupA", allowGroup: "groupB", suppressKey, allowKey, labelKey };
  }
  if (side === PRESET_DEFENSE_POLICY_SIDE.B) {
    return { suppressGroup: "groupB", allowGroup: "groupA", suppressKey, allowKey, labelKey };
  }
  return null;
}

function _invertLabel(label) {
  if (label === "weak") return "tight";
  if (label === "tight") return "weak";
  return "neutral";
}

function _applyGroupMult(target, groupDef, multKey) {
  const mult = Number(groupDef?.mult?.[multKey]);
  if (!Number.isFinite(mult)) return;
  (groupDef?.outcomes || []).forEach((outcome) => {
    target[String(outcome)] = mult;
  });
}

function _applyLabels(target, labelsDef, invert = false) {
  const weakOutcomes = labelsDef?.weak || [];
  const tightOutcomes = labelsDef?.tight || [];

  weakOutcomes.forEach((outcome) => {
    target[String(outcome)] = invert ? _invertLabel("weak") : "weak";
  });
  tightOutcomes.forEach((outcome) => {
    target[String(outcome)] = invert ? _invertLabel("tight") : "tight";
  });
}

function buildDefenseActionWeightMult(draft) {
  const d = sanitizePresetDefenseDraft(draft);
  const budgets = d.actionBudget || {};
  const sum = PRESET_DEFENSE_ACTION_KEYS.reduce((acc, action) => acc + (Number(budgets[action]) || 0), 0) || 100;
  const baseline = 1 / PRESET_DEFENSE_ACTION_KEYS.length;
  const out = {};

  PRESET_DEFENSE_ACTION_KEYS.forEach((action) => {
    const share = (Number(budgets[action]) || 0) / sum;
    out[action] = clamp(share / baseline, 0.85, 1.15);
  });

  return out;
}

function buildDefenseOutcomeByActionMult(draft) {
  const d = sanitizePresetDefenseDraft(draft);
  const out = {};

  PRESET_DEFENSE_ACTION_KEYS.forEach((action) => {
    const spec = PRESET_DEFENSE_ACTION_TABLE[action];
    if (!spec) return;
    const resolved = _resolvePolicy(d.actionPolicies?.[action]);
    if (!resolved) return;

    const actionMap = {};
    _applyGroupMult(actionMap, spec[resolved.suppressGroup], resolved.suppressKey);
    _applyGroupMult(actionMap, spec[resolved.allowGroup], resolved.allowKey);

    if (Object.keys(actionMap).length) {
      out[action] = actionMap;
    }
  });

  return out;
}

function buildDefenseQualityOverrides(draft) {
  const d = sanitizePresetDefenseDraft(draft);
  const actions = {};

  PRESET_DEFENSE_ACTION_KEYS.forEach((action) => {
    const spec = PRESET_DEFENSE_ACTION_TABLE[action];
    if (!spec) return;
    const resolved = _resolvePolicy(d.actionPolicies?.[action]);
    if (!resolved) return;

    const actionLabels = {};
    _applyLabels(actionLabels, spec[resolved.allowGroup]?.labels?.[resolved.labelKey], false);
    _applyLabels(actionLabels, spec[resolved.suppressGroup]?.labels?.[resolved.labelKey], true);

    if (Object.keys(actionLabels).length) {
      actions[action] = actionLabels;
    }
  });

  return {
    version: DEF_QUALITY_LABEL_OVERRIDES_VERSION,
    actions,
  };
}

function buildDefenseOutcomeGlobalMult(draft) {
  const d = sanitizePresetDefenseDraft(draft);
  const lv = String(Number(d.pressureLevel) || 0);
  return { ...(PRESSURE_MULT_TABLE[lv] || PRESSURE_MULT_TABLE["0"]) };
}

function buildDefenseContextPatch(draft) {
  return {
    [DEF_QUALITY_LABEL_OVERRIDES_CTX_KEY]: buildDefenseQualityOverrides(draft),
  };
}

function compilePresetDefenseDraft(draft, baseTactics = {}) {
  const d = sanitizePresetDefenseDraft(draft || createDefaultPresetDefenseDraft());
  return {
    action_weight_mult: buildDefenseActionWeightMult(d),
    outcome_by_action_mult: buildDefenseOutcomeByActionMult(d),
    outcome_global_mult: buildDefenseOutcomeGlobalMult(d),
    context: {
      ...(baseTactics?.context || {}),
      ...buildDefenseContextPatch(d),
    },
  };
}

function mergeCompiledPresetDefenseIntoTactics(base, compiled) {
  const out = { ...(base || {}) };
  out.action_weight_mult = { ...(out.action_weight_mult || {}), ...(compiled?.action_weight_mult || {}) };
  out.outcome_by_action_mult = { ...(out.outcome_by_action_mult || {}), ...(compiled?.outcome_by_action_mult || {}) };
  out.outcome_global_mult = { ...(out.outcome_global_mult || {}), ...(compiled?.outcome_global_mult || {}) };
  out.context = { ...(out.context || {}), ...(compiled?.context || {}) };
  return out;
}

export {
  PRESET_DEFENSE_ACTION_TABLE,
  PRESSURE_MULT_TABLE,
  DEF_QUALITY_LABEL_OVERRIDES_CTX_KEY,
  DEF_QUALITY_LABEL_OVERRIDES_VERSION,
  buildDefenseActionWeightMult,
  buildDefenseOutcomeByActionMult,
  buildDefenseQualityOverrides,
  buildDefenseOutcomeGlobalMult,
  buildDefenseContextPatch,
  compilePresetDefenseDraft,
  mergeCompiledPresetDefenseIntoTactics,
};
