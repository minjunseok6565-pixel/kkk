import { invalidateCachedValuesByPrefix, prefetchCachedJson } from "../core/api.js";
import { onCacheEvent } from "./cacheEvents.js";

const CACHE_TTL_MS = {
  schedule: 300_000,
  standings: 300_000,
  teamDetail: 180_000,
  tactics: 180_000,
  training: 180_000,
  medical: 180_000,
  college: 180_000,
};

const TRAINING_RANGE_DAYS = 28;

function normalizeTeamId(teamId) {
  return String(teamId || "").trim().toUpperCase();
}

function normalizeIsoDate(value) {
  const out = String(value || "").slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(out) ? out : "";
}

function addDaysIsoDate(isoDate, days) {
  const normalized = normalizeIsoDate(isoDate);
  if (!normalized) return "";
  const base = new Date(`${normalized}T00:00:00Z`);
  if (Number.isNaN(base.getTime())) return "";
  base.setUTCDate(base.getUTCDate() + Number(days || 0));
  const y = base.getUTCFullYear();
  const m = String(base.getUTCMonth() + 1).padStart(2, "0");
  const d = String(base.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function resolveTrainingRange(context = {}) {
  const fromFromContext = normalizeIsoDate(context.trainingRange?.from);
  const toFromContext = normalizeIsoDate(context.trainingRange?.to);
  if (fromFromContext && toFromContext) {
    return { from: fromFromContext, to: toFromContext, source: "context.trainingRange" };
  }

  const currentDate = normalizeIsoDate(context.currentDate);
  if (currentDate) {
    const fallbackTo = addDaysIsoDate(currentDate, TRAINING_RANGE_DAYS - 1);
    if (fallbackTo) {
      return { from: currentDate, to: fallbackTo, source: "context.currentDate" };
    }
  }

  return { from: "", to: "", source: "none" };
}

function buildCacheKeys(teamId, range = {}) {
  const tid = normalizeTeamId(teamId);
  const from = String(range.from || "");
  const to = String(range.to || "");
  return {
    schedule: tid ? `schedule:${tid}` : "",
    standings: "standings:table",
    teamDetail: tid ? `team-detail:${tid}` : "",
    tactics: tid ? `tactics:${tid}` : "",
    trainingSchedule: tid ? `training:schedule:${tid}` : "",
    trainingTeamDetail: tid ? `training:team-detail:${tid}` : "",
    trainingFamiliarityOffense: tid ? `training:familiarity:${tid}:offense` : "",
    trainingFamiliarityDefense: tid ? `training:familiarity:${tid}:defense` : "",
    trainingSessionsResolve: (tid && from && to) ? `training:sessions-resolve:${tid}:${from}:${to}:nogame:missing` : "",
    medicalOverview: tid ? `medical:overview:${tid}` : "",
    medicalAlerts: tid ? `medical:alerts:${tid}` : "",
    medicalRiskCalendar: tid ? `medical:risk-calendar:${tid}:14` : "",
    collegeMeta: "college:meta",
    collegeTeams: "college:teams",
    collegeExperts: "college:experts",
    collegeScouts: tid ? `college:scouting:team=${tid}:scouts` : "",
    collegeReports: tid ? `college:scouting:team=${tid}:reports` : "",
  };
}

function buildPrefixes(teamId) {
  const tid = normalizeTeamId(teamId);
  return {
    schedule: tid ? [`schedule:${tid}`] : [],
    standings: ["standings:"],
    teamDetail: tid ? [`team-detail:${tid}`] : [],
    training: tid ? [
      `training:schedule:${tid}`,
      `training:sessions:${tid}:`,
      `training:sessions-resolve:${tid}:`,
      `training:session:${tid}:`,
      `training:team-detail:${tid}`,
      `training:familiarity:${tid}:`,
    ] : [],
    medical: tid ? [
      `medical:overview:${tid}`,
      `medical:alerts:${tid}`,
      `medical:risk-calendar:${tid}`,
    ] : [],
    college: ["college:"],
    tactics: tid ? [`tactics:${tid}`] : [],
  };
}

const CACHE_EVENT_TYPES = Object.freeze({
  GAME_PROGRESS: "GAME_PROGRESS",
  TACTICS_SAVE: "TACTICS_SAVE",
  TRAINING_SAVE: "TRAINING_SAVE",
  SCOUT_ASSIGN: "SCOUT_ASSIGN",
  ROSTER_CHANGE: "ROSTER_CHANGE",
});

function getEventMatrix(context = {}) {
  const tid = normalizeTeamId(context.teamId);
  const prefixes = buildPrefixes(tid);
  return {
    [CACHE_EVENT_TYPES.GAME_PROGRESS]: {
      invalidatePrefixes: [
        ...prefixes.schedule,
        ...prefixes.standings,
        ...prefixes.teamDetail,
        ...prefixes.training,
        ...prefixes.medical,
      ],
      prefetchTargets: ["schedule", "standings", "teamDetail", "training", "medical"],
    },
    [CACHE_EVENT_TYPES.TACTICS_SAVE]: {
      invalidatePrefixes: [...prefixes.tactics, ...prefixes.teamDetail, ...prefixes.training],
      prefetchTargets: ["tactics", "teamDetail", "training"],
    },
    [CACHE_EVENT_TYPES.TRAINING_SAVE]: {
      invalidatePrefixes: [...prefixes.training, ...prefixes.medical],
      prefetchTargets: ["training", "medical"],
    },
    [CACHE_EVENT_TYPES.SCOUT_ASSIGN]: {
      invalidatePrefixes: [...prefixes.college],
      prefetchTargets: ["college"],
    },
    [CACHE_EVENT_TYPES.ROSTER_CHANGE]: {
      invalidatePrefixes: [
        ...prefixes.teamDetail,
        ...prefixes.tactics,
        ...prefixes.training,
        ...prefixes.medical,
        ...prefixes.college,
      ],
      prefetchTargets: ["teamDetail", "tactics", "training", "medical", "college"],
    },
  };
}

function invalidateByEvent(eventType, context = {}) {
  const matrix = getEventMatrix(context);
  const plan = matrix[String(eventType || "")] || { invalidatePrefixes: [] };
  plan.invalidatePrefixes.forEach((prefix) => {
    if (!prefix) return;
    invalidateCachedValuesByPrefix(prefix);
  });
  return plan;
}

function getPrefetchPlanAfterGame(context = {}) {
  const tid = normalizeTeamId(context.teamId);
  if (!tid) return [];
  const trainingRange = resolveTrainingRange(context);
  const keys = buildCacheKeys(tid, trainingRange);
  return [
    {
      key: keys.schedule,
      url: `/api/team-schedule/${encodeURIComponent(tid)}`,
      ttlMs: CACHE_TTL_MS.schedule,
      priorityTier: 1,
      critical: true,
    },
    {
      key: keys.standings,
      url: "/api/standings/table",
      ttlMs: CACHE_TTL_MS.standings,
      priorityTier: 1,
      critical: true,
    },
    {
      key: keys.teamDetail,
      url: `/api/team-detail/${encodeURIComponent(tid)}`,
      ttlMs: CACHE_TTL_MS.teamDetail,
      priorityTier: 2,
      critical: true,
    },
    {
      key: keys.tactics,
      url: `/api/tactics/${encodeURIComponent(tid)}`,
      ttlMs: CACHE_TTL_MS.tactics,
      priorityTier: 2,
      critical: true,
    },
    {
      key: keys.trainingSchedule,
      url: `/api/team-schedule/${encodeURIComponent(tid)}?view=light`,
      ttlMs: CACHE_TTL_MS.training,
      priorityTier: 3,
      critical: false,
      timeoutMs: 1200,
    },
    {
      key: keys.trainingTeamDetail,
      url: `/api/team-detail/${encodeURIComponent(tid)}?view=light`,
      ttlMs: CACHE_TTL_MS.training,
      priorityTier: 3,
      critical: false,
      timeoutMs: 1200,
    },
    {
      key: keys.trainingFamiliarityOffense,
      url: `/api/readiness/team/${encodeURIComponent(tid)}/familiarity?scheme_type=offense`,
      ttlMs: CACHE_TTL_MS.training,
      priorityTier: 3,
      critical: false,
      timeoutMs: 1200,
    },
    {
      key: keys.trainingFamiliarityDefense,
      url: `/api/readiness/team/${encodeURIComponent(tid)}/familiarity?scheme_type=defense`,
      ttlMs: CACHE_TTL_MS.training,
      priorityTier: 3,
      critical: false,
      timeoutMs: 1200,
    },
    ...(keys.trainingSessionsResolve ? [{
      key: keys.trainingSessionsResolve,
      url: `/api/practice/team/${encodeURIComponent(tid)}/sessions/resolve?date_from=${encodeURIComponent(trainingRange.from)}&date_to=${encodeURIComponent(trainingRange.to)}&only_missing=true&include_games=false`,
      ttlMs: CACHE_TTL_MS.training,
      priorityTier: 3,
      critical: false,
      timeoutMs: 1400,
    }] : []),
    {
      key: keys.medicalOverview,
      url: `/api/medical/team/${encodeURIComponent(tid)}/overview`,
      ttlMs: CACHE_TTL_MS.medical,
      priorityTier: 4,
      critical: false,
      timeoutMs: 1500,
    },
    {
      key: keys.medicalAlerts,
      url: `/api/medical/team/${encodeURIComponent(tid)}/alerts`,
      ttlMs: CACHE_TTL_MS.medical,
      priorityTier: 4,
      critical: false,
      timeoutMs: 1500,
    },
    {
      key: keys.medicalRiskCalendar,
      url: `/api/medical/team/${encodeURIComponent(tid)}/risk-calendar?days=14`,
      ttlMs: CACHE_TTL_MS.medical,
      priorityTier: 4,
      critical: false,
      timeoutMs: 1500,
    },
    {
      key: keys.collegeMeta,
      url: "/api/college/meta",
      ttlMs: CACHE_TTL_MS.college,
      priorityTier: 5,
      critical: false,
      timeoutMs: 1800,
    },
    {
      key: keys.collegeTeams,
      url: "/api/college/teams",
      ttlMs: CACHE_TTL_MS.college,
      priorityTier: 5,
      critical: false,
      timeoutMs: 1800,
    },
    {
      key: keys.collegeExperts,
      url: "/api/offseason/draft/experts",
      ttlMs: CACHE_TTL_MS.college,
      priorityTier: 5,
      critical: false,
      timeoutMs: 1800,
    },
  ];
}



function getPrefetchPlanForEvent(eventType, context = {}) {
  const tid = normalizeTeamId(context.teamId);
  const keys = buildCacheKeys(tid, context.trainingRange || {});
  const type = String(eventType || "").toUpperCase();

  if (type === CACHE_EVENT_TYPES.TACTICS_SAVE && tid) {
    return [
      { key: keys.tactics, url: `/api/tactics/${encodeURIComponent(tid)}`, ttlMs: CACHE_TTL_MS.tactics },
      { key: keys.teamDetail, url: `/api/team-detail/${encodeURIComponent(tid)}`, ttlMs: CACHE_TTL_MS.teamDetail },
      { key: keys.trainingFamiliarityOffense, url: `/api/readiness/team/${encodeURIComponent(tid)}/familiarity?scheme_type=offense`, ttlMs: CACHE_TTL_MS.training },
      { key: keys.trainingFamiliarityDefense, url: `/api/readiness/team/${encodeURIComponent(tid)}/familiarity?scheme_type=defense`, ttlMs: CACHE_TTL_MS.training },
    ];
  }

  if (type === CACHE_EVENT_TYPES.TRAINING_SAVE && tid) {
    const out = [
      { key: keys.trainingSchedule, url: `/api/team-schedule/${encodeURIComponent(tid)}?view=light`, ttlMs: CACHE_TTL_MS.training },
      { key: keys.trainingTeamDetail, url: `/api/team-detail/${encodeURIComponent(tid)}?view=light`, ttlMs: CACHE_TTL_MS.training },
      { key: keys.medicalOverview, url: `/api/medical/team/${encodeURIComponent(tid)}/overview`, ttlMs: CACHE_TTL_MS.medical },
      { key: keys.medicalAlerts, url: `/api/medical/team/${encodeURIComponent(tid)}/alerts`, ttlMs: CACHE_TTL_MS.medical },
      { key: keys.medicalRiskCalendar, url: `/api/medical/team/${encodeURIComponent(tid)}/risk-calendar?days=14`, ttlMs: CACHE_TTL_MS.medical },
    ];
    if (keys.trainingSessionsResolve) {
      out.push({
        key: keys.trainingSessionsResolve,
        url: `/api/practice/team/${encodeURIComponent(tid)}/sessions/resolve?date_from=${encodeURIComponent(String(context.trainingRange?.from || ""))}&date_to=${encodeURIComponent(String(context.trainingRange?.to || ""))}&only_missing=true&include_games=false`,
        ttlMs: CACHE_TTL_MS.training,
      });
    }
    return out;
  }

  if (type === CACHE_EVENT_TYPES.SCOUT_ASSIGN) {
    return [
      { key: keys.collegeMeta, url: "/api/college/meta", ttlMs: CACHE_TTL_MS.college },
      { key: keys.collegeTeams, url: "/api/college/teams", ttlMs: CACHE_TTL_MS.college },
      { key: keys.collegeExperts, url: "/api/offseason/draft/experts", ttlMs: CACHE_TTL_MS.college },
      ...(tid ? [
        { key: keys.collegeScouts, url: `/api/scouting/scouts/${encodeURIComponent(tid)}`, ttlMs: CACHE_TTL_MS.college },
        { key: keys.collegeReports, url: `/api/scouting/reports?team_id=${encodeURIComponent(tid)}&status=all`, ttlMs: CACHE_TTL_MS.college },
      ] : []),
    ];
  }

  if (type === CACHE_EVENT_TYPES.ROSTER_CHANGE && tid) {
    return [
      { key: keys.teamDetail, url: `/api/team-detail/${encodeURIComponent(tid)}`, ttlMs: CACHE_TTL_MS.teamDetail },
      { key: keys.tactics, url: `/api/tactics/${encodeURIComponent(tid)}`, ttlMs: CACHE_TTL_MS.tactics },
      { key: keys.trainingSchedule, url: `/api/team-schedule/${encodeURIComponent(tid)}?view=light`, ttlMs: CACHE_TTL_MS.training },
      { key: keys.medicalOverview, url: `/api/medical/team/${encodeURIComponent(tid)}/overview`, ttlMs: CACHE_TTL_MS.medical },
    ];
  }

  if (type === CACHE_EVENT_TYPES.GAME_PROGRESS) {
    return getPrefetchPlanAfterGame(context);
  }

  return [];
}

function registerCachePolicyEventHandlers({ getContext } = {}) {
  const unsubscribers = Object.values(CACHE_EVENT_TYPES).map((eventType) => onCacheEvent(eventType, (payload) => {
    const context = typeof getContext === "function" ? getContext(payload, eventType) : payload;
    invalidateByEvent(eventType, context || {});
  }));
  return () => {
    unsubscribers.forEach((off) => {
      if (typeof off === "function") off();
    });
  };
}

async function runPrefetchPlan(plan = []) {
  const tasks = (Array.isArray(plan) ? plan : [])
    .filter((item) => item?.key && item?.url)
    .map((item) => prefetchCachedJson(item));
  await Promise.all(tasks);
}

export {
  CACHE_EVENT_TYPES,
  CACHE_TTL_MS,
  normalizeTeamId,
  resolveTrainingRange,
  buildCacheKeys,
  getEventMatrix,
  invalidateByEvent,
  getPrefetchPlanAfterGame,
  getPrefetchPlanForEvent,
  runPrefetchPlan,
  registerCachePolicyEventHandlers,
};
