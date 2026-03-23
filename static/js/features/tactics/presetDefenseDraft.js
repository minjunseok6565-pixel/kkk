const PRESET_DEFENSE_ACTION_KEYS = [
  "Cut",
  "DHO",
  "Drive",
  "ISO",
  "PnP",
  "PnR",
  "PostUp",
  "SpotUp",
  "TransitionEarly",
];

const PRESET_DEFENSE_POLICY_SIDE = {
  NEUTRAL: "neutral",
  A: "A",
  B: "B",
};

const PRESET_DEFENSE_POLICY_LEVEL = {
  NORMAL: "normal",
  STRONG: "strong",
};

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function _normalizeWeightsTo100(input, keys) {
  const out = {};
  const safeKeys = Array.isArray(keys) ? keys : [];
  const raw = safeKeys.map((k) => clamp(Number(input?.[k]) || 0, 0, 100));
  const sum = raw.reduce((acc, v) => acc + v, 0);
  if (sum <= 0 || !safeKeys.length) {
    const base = safeKeys.length ? Math.floor(100 / safeKeys.length) : 0;
    let rem = 100 - (base * safeKeys.length);
    safeKeys.forEach((k) => {
      out[k] = base + (rem > 0 ? 1 : 0);
      rem = Math.max(0, rem - 1);
    });
    return out;
  }

  let allocated = 0;
  safeKeys.forEach((k, idx) => {
    if (idx === safeKeys.length - 1) {
      out[k] = Math.max(0, 100 - allocated);
      return;
    }
    const v = Math.round((raw[idx] / sum) * 100);
    out[k] = v;
    allocated += v;
  });
  return out;
}

function createDefaultPresetDefenseDraft() {
  const actionBudget = _normalizeWeightsTo100({}, PRESET_DEFENSE_ACTION_KEYS);
  const actionPolicies = {};
  PRESET_DEFENSE_ACTION_KEYS.forEach((action) => {
    actionPolicies[action] = {
      side: PRESET_DEFENSE_POLICY_SIDE.NEUTRAL,
      level: PRESET_DEFENSE_POLICY_LEVEL.NORMAL,
    };
  });

  return {
    actionBudget,
    actionPolicies,
    pressureLevel: 0,
  };
}

function clonePresetDefenseDraft(draft) {
  const src = draft || createDefaultPresetDefenseDraft();
  return JSON.parse(JSON.stringify(src));
}

function sanitizePresetDefenseDraft(raw) {
  const base = createDefaultPresetDefenseDraft();
  const d = {
    ...base,
    ...(raw || {}),
    actionBudget: {
      ...base.actionBudget,
      ...(raw?.actionBudget || {}),
    },
    actionPolicies: {
      ...base.actionPolicies,
      ...(raw?.actionPolicies || {}),
    },
  };

  d.actionBudget = _normalizeWeightsTo100(d.actionBudget, PRESET_DEFENSE_ACTION_KEYS);

  const cleanPolicies = {};
  PRESET_DEFENSE_ACTION_KEYS.forEach((action) => {
    const src = d.actionPolicies?.[action] || {};
    const side = String(src.side || PRESET_DEFENSE_POLICY_SIDE.NEUTRAL);
    const level = String(src.level || PRESET_DEFENSE_POLICY_LEVEL.NORMAL);
    cleanPolicies[action] = {
      side: side === PRESET_DEFENSE_POLICY_SIDE.A || side === PRESET_DEFENSE_POLICY_SIDE.B
        ? side
        : PRESET_DEFENSE_POLICY_SIDE.NEUTRAL,
      level: level === PRESET_DEFENSE_POLICY_LEVEL.STRONG
        ? PRESET_DEFENSE_POLICY_LEVEL.STRONG
        : PRESET_DEFENSE_POLICY_LEVEL.NORMAL,
    };
  });
  d.actionPolicies = cleanPolicies;

  const p = Number(d.pressureLevel);
  d.pressureLevel = Number.isFinite(p) ? clamp(Math.round(p), -2, 2) : 0;

  return d;
}

function summarizePresetDefenseDraft(raw) {
  const d = sanitizePresetDefenseDraft(raw);
  let tunedActionCount = 0;
  let strongActionCount = 0;
  let topBudgetAction = PRESET_DEFENSE_ACTION_KEYS[0] || "";
  let topBudgetValue = -1;

  PRESET_DEFENSE_ACTION_KEYS.forEach((action) => {
    const side = String(d.actionPolicies?.[action]?.side || PRESET_DEFENSE_POLICY_SIDE.NEUTRAL);
    const level = String(d.actionPolicies?.[action]?.level || PRESET_DEFENSE_POLICY_LEVEL.NORMAL);
    const budget = Number(d.actionBudget?.[action]) || 0;

    if (side !== PRESET_DEFENSE_POLICY_SIDE.NEUTRAL) tunedActionCount += 1;
    if (side !== PRESET_DEFENSE_POLICY_SIDE.NEUTRAL && level === PRESET_DEFENSE_POLICY_LEVEL.STRONG) strongActionCount += 1;
    if (budget > topBudgetValue) {
      topBudgetValue = budget;
      topBudgetAction = action;
    }
  });

  return {
    tunedActionCount,
    strongActionCount,
    topBudgetAction,
    topBudgetValue: Math.max(0, topBudgetValue),
    pressureLevel: Number(d.pressureLevel) || 0,
  };
}

export {
  PRESET_DEFENSE_ACTION_KEYS,
  PRESET_DEFENSE_POLICY_SIDE,
  PRESET_DEFENSE_POLICY_LEVEL,
  createDefaultPresetDefenseDraft,
  clonePresetDefenseDraft,
  sanitizePresetDefenseDraft,
  summarizePresetDefenseDraft,
};
