import { fetchCachedJson, getCachedValue, invalidateCachedValuesByPrefix } from "../../core/api.js";

const TEAM_DETAIL_CACHE_TTL_MS = 7000;

function normalizeTeamId(teamId) {
  return String(teamId || "").trim().toUpperCase();
}

function buildTeamDetailCacheKey(teamId) {
  const normalized = normalizeTeamId(teamId);
  if (!normalized) return "";
  return `team-detail:${normalized}`;
}

async function fetchTeamDetail(teamId, {
  force = false,
  staleWhileRevalidate = true,
  onRevalidated = null,
} = {}) {
  const normalized = normalizeTeamId(teamId);
  if (!normalized) throw new Error("유효한 팀 ID가 필요합니다.");

  return fetchCachedJson({
    key: buildTeamDetailCacheKey(normalized),
    url: `/api/team-detail/${encodeURIComponent(normalized)}`,
    ttlMs: TEAM_DETAIL_CACHE_TTL_MS,
    staleWhileRevalidate,
    force,
    onRevalidated,
  });
}

function hasTeamDetailCache(teamId) {
  const key = buildTeamDetailCacheKey(teamId);
  if (!key) return false;
  return Boolean(getCachedValue(key));
}

function invalidateTeamDetailCache(teamId) {
  const normalized = normalizeTeamId(teamId);
  if (!normalized) return;
  invalidateCachedValuesByPrefix(`team-detail:${normalized}`);
}

export {
  TEAM_DETAIL_CACHE_TTL_MS,
  normalizeTeamId,
  buildTeamDetailCacheKey,
  fetchTeamDetail,
  hasTeamDetailCache,
  invalidateTeamDetailCache,
};
