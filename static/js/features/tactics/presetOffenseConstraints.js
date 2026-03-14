import { clonePresetOffenseDraft, sanitizePresetOffenseDraft } from "./presetOffenseDraft.js";

function isExtremePair(a, b, level) {
  return String(a || "") === level && String(b || "") === level;
}

function enforcePairNoSameExtreme(left, right, tag = "pair") {
  let l = String(left || "mid");
  let r = String(right || "mid");
  const warnings = [];

  if (isExtremePair(l, r, "high")) {
    r = "mid";
    warnings.push(`[${tag}] 동일 쌍에서 high/high 조합은 허용되지 않아 우측 값을 mid로 조정했습니다.`);
  }
  if (isExtremePair(l, r, "low")) {
    r = "mid";
    warnings.push(`[${tag}] 동일 쌍에서 low/low 조합은 허용되지 않아 우측 값을 mid로 조정했습니다.`);
  }

  return { left: l, right: r, warnings };
}

function enforceDriveRule(rim, kickout, pull2) {
  const vals = [String(rim || "mid"), String(kickout || "mid"), String(pull2 || "mid")];
  const warnings = [];
  const highIdx = vals.map((v, i) => (v === "high" ? i : -1)).filter((i) => i >= 0);
  if (highIdx.length >= 2) {
    const lowIdx = vals.findIndex((v) => v !== "high");
    if (lowIdx >= 0 && vals[lowIdx] !== "low") {
      vals[lowIdx] = "low";
      warnings.push("[drive] Drive 규칙에 따라 high가 2개 이상일 때 나머지 1개를 low로 조정했습니다.");
    }
  }
  return { rim: vals[0], kickout: vals[1], pull2: vals[2], warnings };
}

function enforceIsoRule(rim, floater, pullup, kickout) {
  const vals = [String(rim || "mid"), String(floater || "mid"), String(pullup || "mid"), String(kickout || "mid")];
  const warnings = [];
  const highIdx = vals.map((v, i) => (v === "high" ? i : -1)).filter((i) => i >= 0);
  if (highIdx.length === 2) {
    const rest = [0, 1, 2, 3].filter((i) => !highIdx.includes(i));
    const restVals = rest.map((i) => vals[i]);
    const hasMid = restVals.includes("mid");
    const hasLow = restVals.includes("low");
    if (!(hasMid && hasLow)) {
      vals[rest[0]] = "mid";
      vals[rest[1]] = "low";
      warnings.push("[iso] ISO 규칙에 따라 high 2개일 때 나머지 2개를 mid/low로 조정했습니다.");
    }
  }
  return { rim: vals[0], floater: vals[1], pullup: vals[2], kickout: vals[3], warnings };
}

function enforcePostUpRule(postFinish, postFadeway, pass) {
  const vals = [String(postFinish || "mid"), String(postFadeway || "mid"), String(pass || "mid")];
  const warnings = [];
  const highIdx = vals.map((v, i) => (v === "high" ? i : -1)).filter((i) => i >= 0);
  if (highIdx.length >= 2) {
    const lowIdx = vals.findIndex((v) => v !== "high");
    if (lowIdx >= 0 && vals[lowIdx] !== "low") {
      vals[lowIdx] = "low";
      warnings.push("[postUp] PostUp 규칙에 따라 high가 2개 이상일 때 나머지 1개를 low로 조정했습니다.");
    }
  }
  return { postFinish: vals[0], postFadeway: vals[1], pass: vals[2], warnings };
}

function validatePresetOffenseDraft(rawDraft) {
  const draft = sanitizePresetOffenseDraft(clonePresetOffenseDraft(rawDraft));
  const warnings = [];
  const errors = [];

  const pnr = enforcePairNoSameExtreme(draft.outcomes.pnr.handlerDirect, draft.outcomes.pnr.rollPass, "pnrPair");
  draft.outcomes.pnr.handlerDirect = pnr.left;
  draft.outcomes.pnr.rollPass = pnr.right;
  warnings.push(...pnr.warnings);

  const pnp = enforcePairNoSameExtreme(draft.outcomes.pnp.handlerDirect, draft.outcomes.pnp.popOut, "pnpPair");
  draft.outcomes.pnp.handlerDirect = pnp.left;
  draft.outcomes.pnp.popOut = pnp.right;
  warnings.push(...pnp.warnings);

  const te = enforcePairNoSameExtreme(draft.outcomes.transitionEarly.handlerDirect, draft.outcomes.transitionEarly.openChance3, "transitionPair");
  draft.outcomes.transitionEarly.handlerDirect = te.left;
  draft.outcomes.transitionEarly.openChance3 = te.right;
  warnings.push(...te.warnings);

  const drive = enforceDriveRule(draft.outcomes.drive.rim, draft.outcomes.drive.kickout, draft.outcomes.drive.pull2);
  draft.outcomes.drive.rim = drive.rim;
  draft.outcomes.drive.kickout = drive.kickout;
  draft.outcomes.drive.pull2 = drive.pull2;
  warnings.push(...drive.warnings);

  const iso = enforceIsoRule(draft.outcomes.iso.rim, draft.outcomes.iso.floater, draft.outcomes.iso.pullup, draft.outcomes.iso.kickout);
  draft.outcomes.iso.rim = iso.rim;
  draft.outcomes.iso.floater = iso.floater;
  draft.outcomes.iso.pullup = iso.pullup;
  draft.outcomes.iso.kickout = iso.kickout;
  warnings.push(...iso.warnings);

  const post = enforcePostUpRule(draft.outcomes.postUp.postFinish, draft.outcomes.postUp.postFadeway, draft.outcomes.postUp.pass);
  draft.outcomes.postUp.postFinish = post.postFinish;
  draft.outcomes.postUp.postFadeway = post.postFadeway;
  draft.outcomes.postUp.pass = post.pass;
  warnings.push(...post.warnings);

  const checkLevel = (v, label) => {
    if (!["low", "mid", "high"].includes(String(v || ""))) {
      errors.push(`${label}: 값은 low/mid/high 이어야 합니다.`);
    }
  };

  checkLevel(draft.passFreq, "passFreq");
  checkLevel(draft.offballFreq, "offballFreq");
  checkLevel(draft.outcomes.pnr.handlerDirect, "pnr.handlerDirect");
  checkLevel(draft.outcomes.pnr.rollPass, "pnr.rollPass");
  checkLevel(draft.outcomes.pnp.handlerDirect, "pnp.handlerDirect");
  checkLevel(draft.outcomes.pnp.popOut, "pnp.popOut");
  checkLevel(draft.outcomes.transitionEarly.handlerDirect, "transitionEarly.handlerDirect");
  checkLevel(draft.outcomes.transitionEarly.openChance3, "transitionEarly.openChance3");

  return {
    ok: errors.length === 0,
    errors,
    warnings,
    draft,
  };
}

export {
  enforcePairNoSameExtreme,
  enforceDriveRule,
  enforceIsoRule,
  enforcePostUpRule,
  validatePresetOffenseDraft,
};
