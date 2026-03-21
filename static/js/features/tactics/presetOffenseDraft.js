function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function normalizePairTo100(a, b) {
  const aa = clamp(Number(a) || 0, 0, 100);
  const bb = clamp(Number(b) || 0, 0, 100);
  const sum = aa + bb;
  if (sum <= 0) return { a: 50, b: 50 };
  const na = Math.round((aa / sum) * 100);
  return { a: na, b: 100 - na };
}

function normalizeTripleTo100(a, b, c) {
  const aa = clamp(Number(a) || 0, 0, 100);
  const bb = clamp(Number(b) || 0, 0, 100);
  const cc = clamp(Number(c) || 0, 0, 100);
  const sum = aa + bb + cc;
  if (sum <= 0) return { a: 34, b: 33, c: 33 };
  const na = Math.round((aa / sum) * 100);
  const nb = Math.round((bb / sum) * 100);
  return { a: na, b: nb, c: 100 - na - nb };
}

function createDefaultPresetOffenseDraft() {
  return {
    actionVolume: { pnrFamily: 20, drive: 20, transition: 20, iso: 20, postUp: 20 },
    passFreq: "mid",
    offballFreq: "mid",
    pnrSplit: { pnr: 50, pnp: 50 },
    offballSplit: { cut: 34, spotUp: 33, dho: 33 },
    outcomes: {
      pnr: { handlerDirect: "mid", rollPass: "mid", rimVsFloater: { rim: 50, floater: 50 }, pullupSplit: { pull3: 50, pull2: 50 } },
      pnp: { handlerDirect: "mid", popOut: "mid", rimVsFloater: { rim: 50, floater: 50 }, pullupSplit: { pull3: 50, pull2: 50 } },
      transitionEarly: { handlerDirect: "mid", openChance3: "mid", directSplit: { trans3: 34, rim: 33, floater: 33 } },
      drive: { rim: "mid", kickout: "mid", pull2: "mid" },
      iso: { rim: "mid", floater: "mid", pullup: "mid", kickout: "mid", pullupSplit: { pull3: 50, pull2: 50 } },
      cut: { finish: "mid", pass: "mid" },
      postUp: { postFinish: "mid", postFadeway: "mid", pass: "mid" },
    },
    foulDraw: 50,
    riskTaking: 50,
    tempo: 50,
  };
}

function clonePresetOffenseDraft(draft) {
  const src = draft || createDefaultPresetOffenseDraft();
  return JSON.parse(JSON.stringify(src));
}

function sanitizePresetOffenseDraft(raw) {
  const base = createDefaultPresetOffenseDraft();
  const d = {
    ...base,
    ...(raw || {}),
    actionVolume: { ...base.actionVolume, ...(raw?.actionVolume || {}) },
    pnrSplit: { ...base.pnrSplit, ...(raw?.pnrSplit || {}) },
    offballSplit: { ...base.offballSplit, ...(raw?.offballSplit || {}) },
    outcomes: {
      ...base.outcomes,
      ...(raw?.outcomes || {}),
      pnr: { ...base.outcomes.pnr, ...(raw?.outcomes?.pnr || {}) },
      pnp: { ...base.outcomes.pnp, ...(raw?.outcomes?.pnp || {}) },
      transitionEarly: { ...base.outcomes.transitionEarly, ...(raw?.outcomes?.transitionEarly || {}) },
      drive: { ...base.outcomes.drive, ...(raw?.outcomes?.drive || {}) },
      iso: { ...base.outcomes.iso, ...(raw?.outcomes?.iso || {}) },
      cut: { ...base.outcomes.cut, ...(raw?.outcomes?.cut || {}) },
      postUp: { ...base.outcomes.postUp, ...(raw?.outcomes?.postUp || {}) },
    },
  };

  d.outcomes.pnr.rimVsFloater = { ...base.outcomes.pnr.rimVsFloater, ...(raw?.outcomes?.pnr?.rimVsFloater || {}) };
  d.outcomes.pnr.pullupSplit = { ...base.outcomes.pnr.pullupSplit, ...(raw?.outcomes?.pnr?.pullupSplit || {}) };
  d.outcomes.pnp.rimVsFloater = { ...base.outcomes.pnp.rimVsFloater, ...(raw?.outcomes?.pnp?.rimVsFloater || {}) };
  d.outcomes.pnp.pullupSplit = { ...base.outcomes.pnp.pullupSplit, ...(raw?.outcomes?.pnp?.pullupSplit || {}) };
  d.outcomes.transitionEarly.directSplit = { ...base.outcomes.transitionEarly.directSplit, ...(raw?.outcomes?.transitionEarly?.directSplit || {}) };
  d.outcomes.iso.pullupSplit = { ...base.outcomes.iso.pullupSplit, ...(raw?.outcomes?.iso?.pullupSplit || {}) };

  Object.keys(d.actionVolume).forEach((k) => {
    d.actionVolume[k] = clamp(Number(d.actionVolume[k]) || 0, 0, 100);
  });
  d.foulDraw = clamp(Number(d.foulDraw) || 0, 0, 100);
  d.riskTaking = clamp(Number(d.riskTaking) || 0, 0, 100);
  d.tempo = clamp(Number(d.tempo) || 0, 0, 100);

  const pnr = normalizePairTo100(d.pnrSplit.pnr, d.pnrSplit.pnp);
  d.pnrSplit.pnr = pnr.a;
  d.pnrSplit.pnp = pnr.b;

  const off = normalizeTripleTo100(d.offballSplit.cut, d.offballSplit.spotUp, d.offballSplit.dho);
  d.offballSplit.cut = off.a;
  d.offballSplit.spotUp = off.b;
  d.offballSplit.dho = off.c;

  const pnrRF = normalizePairTo100(d.outcomes.pnr.rimVsFloater.rim, d.outcomes.pnr.rimVsFloater.floater);
  d.outcomes.pnr.rimVsFloater.rim = pnrRF.a;
  d.outcomes.pnr.rimVsFloater.floater = pnrRF.b;

  const pnrPU = normalizePairTo100(d.outcomes.pnr.pullupSplit.pull3, d.outcomes.pnr.pullupSplit.pull2);
  d.outcomes.pnr.pullupSplit.pull3 = pnrPU.a;
  d.outcomes.pnr.pullupSplit.pull2 = pnrPU.b;

  const pnpRF = normalizePairTo100(d.outcomes.pnp.rimVsFloater.rim, d.outcomes.pnp.rimVsFloater.floater);
  d.outcomes.pnp.rimVsFloater.rim = pnpRF.a;
  d.outcomes.pnp.rimVsFloater.floater = pnpRF.b;

  const pnpPU = normalizePairTo100(d.outcomes.pnp.pullupSplit.pull3, d.outcomes.pnp.pullupSplit.pull2);
  d.outcomes.pnp.pullupSplit.pull3 = pnpPU.a;
  d.outcomes.pnp.pullupSplit.pull2 = pnpPU.b;

  const tr = normalizeTripleTo100(
    d.outcomes.transitionEarly.directSplit.trans3,
    d.outcomes.transitionEarly.directSplit.rim,
    d.outcomes.transitionEarly.directSplit.floater,
  );
  d.outcomes.transitionEarly.directSplit.trans3 = tr.a;
  d.outcomes.transitionEarly.directSplit.rim = tr.b;
  d.outcomes.transitionEarly.directSplit.floater = tr.c;

  const isoPU = normalizePairTo100(d.outcomes.iso.pullupSplit.pull3, d.outcomes.iso.pullupSplit.pull2);
  d.outcomes.iso.pullupSplit.pull3 = isoPU.a;
  d.outcomes.iso.pullupSplit.pull2 = isoPU.b;

  return d;
}

export {
  createDefaultPresetOffenseDraft,
  clonePresetOffenseDraft,
  sanitizePresetOffenseDraft,
  normalizePairTo100,
  normalizeTripleTo100,
};
