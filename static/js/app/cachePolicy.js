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

function normalizeTeamId(teamId) {
  return String(teamId || "").trim().toUpperCase();
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
  const keys = buildCacheKeys(tid, context.trainingRange || {});
  return [
    {
      key: keys.schedule,
      url: `/api/team-schedule/${encodeURIComponent(tid)}`,
      ttlMs: CACHE_TTL_MS.schedule,
    },
    {
      key: keys.standings,
      url: "/api/standings/table",
      ttlMs: CACHE_TTL_MS.standings,
    },
    {
      key: keys.teamDetail,
      url: `/api/team-detail/${encodeURIComponent(tid)}`,
      ttlMs: CACHE_TTL_MS.teamDetail,
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
        { key: keys.collegeScouts, url: `/api/scouting/scouts?team_id=${encodeURIComponent(tid)}`, ttlMs: CACHE_TTL_MS.college },
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
  buildCacheKeys,
  getEventMatrix,
  invalidateByEvent,
  getPrefetchPlanAfterGame,
  getPrefetchPlanForEvent,
  runPrefetchPlan,
  registerCachePolicyEventHandlers,
};
