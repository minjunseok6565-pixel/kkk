import { els } from "../app/dom.js";
import { state } from "../app/state.js";

const MAX_CACHE_ENTRIES = 220;
const TARGET_CACHE_ENTRIES_AFTER_EVICTION = 170;
const MAINTENANCE_SWEEP_INTERVAL = 20;
const GROUP_ENTRY_LIMITS = {
  "training:session:": 72,
  "college:bigboard:detail:": 24,
};

const cacheMetrics = {
  hits: 0,
  misses: 0,
  staleServed: 0,
  networkFetches: 0,
  evictions: 0,
  evictedExpired: 0,
  evictedByLru: 0,
  evictedByGroup: 0,
  lastLogAt: 0,
};

let maintenanceWriteTick = 0;

function isCacheDebugEnabled() {
  return typeof globalThis !== "undefined" && Boolean(globalThis.__CACHE_DEBUG__);
}

function recordMetric(name, delta = 1) {
  if (!(name in cacheMetrics)) return;
  cacheMetrics[name] += delta;
}

function maybeLogCacheMetrics() {
  if (!isCacheDebugEnabled()) return;
  const now = Date.now();
  if (now - cacheMetrics.lastLogAt < 15000) return;
  cacheMetrics.lastLogAt = now;
  console.debug("[cache] metrics", {
    ...cacheMetrics,
    size: Object.keys(getCacheStore()).length,
    inflight: getInflightStore().size,
  });
}

function resolveApiErrorDetail(data, fallbackUrl = "") {
  const detail = data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (detail && typeof detail === "object") {
    const code = String(detail.code || detail.error_code || "").trim();
    const message = String(detail.message || detail.error || detail.detail || "").trim();
    if (code && message) return `${code}: ${message}`;
    if (code) return code;
    if (message) return message;
  }

  const error = data?.error;
  if (error && typeof error === "object") {
    const code = String(error.code || error.error_code || "").trim();
    const message = String(error.message || error.error || error.detail || "").trim();
    if (code && message) return `${code}: ${message}`;
    if (code) return code;
    if (message) return message;
  }

  const message = String(data?.message || error || "").trim();
  if (message) return message;
  return `요청 실패: ${fallbackUrl}`;
}

async function fetchJson(url, options = {}) {
  let res;
  try {
    res = await fetch(url, options);
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error("REQUEST_ABORTED");
    }
    throw error;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(resolveApiErrorDetail(data, url));
  return data;
}

function normalizeTradeRequestScope(scope) {
  return String(scope || "").trim() || "default";
}

function normalizeOptionalScopeSessionId(sessionId) {
  if (sessionId == null) return null;
  const out = String(sessionId || "").trim();
  return out || null;
}

function getMarketTradeRequestScopesStore() {
  if (!state.marketTradeRequestScopes || typeof state.marketTradeRequestScopes !== "object") {
    state.marketTradeRequestScopes = {};
  }
  return state.marketTradeRequestScopes;
}

function beginScopedRequest(scope, { sessionId = null, abortPrevious = true } = {}) {
  const normalizedScope = normalizeTradeRequestScope(scope);
  const normalizedSessionId = normalizeOptionalScopeSessionId(sessionId);
  const store = getMarketTradeRequestScopesStore();
  const previous = store[normalizedScope];

  if (abortPrevious && previous?.abortController instanceof AbortController) {
    previous.abortController.abort();
  }

  const requestId = Number(state.marketTradeRequestSeq || 0) + 1;
  state.marketTradeRequestSeq = requestId;
  const abortController = new AbortController();
  const startedAt = Date.now();

  store[normalizedScope] = {
    requestId,
    sessionId: normalizedSessionId,
    abortController,
    startedAt,
  };

  return {
    scope: normalizedScope,
    requestId,
    sessionId: normalizedSessionId,
    signal: abortController.signal,
    startedAt,
  };
}

function isScopedRequestCurrent(scope, requestId, sessionId = null) {
  const normalizedScope = normalizeTradeRequestScope(scope);
  const normalizedSessionId = normalizeOptionalScopeSessionId(sessionId);
  const store = getMarketTradeRequestScopesStore();
  const current = store[normalizedScope];
  if (!current) return false;
  if (Number(current.requestId) !== Number(requestId)) return false;

  if (normalizedSessionId != null) {
    const activeSessionId = normalizeOptionalScopeSessionId(state.marketTradeActiveSession?.session_id);
    if (activeSessionId !== normalizedSessionId) return false;
  }

  return true;
}

function abortScopedRequest(scope) {
  const normalizedScope = normalizeTradeRequestScope(scope);
  const store = getMarketTradeRequestScopesStore();
  const current = store[normalizedScope];
  if (!current) return false;
  if (current.abortController instanceof AbortController) {
    current.abortController.abort();
  }
  delete store[normalizedScope];
  return true;
}

function abortAllMarketTradeRequests() {
  const store = getMarketTradeRequestScopesStore();
  Object.keys(store).forEach((scope) => {
    abortScopedRequest(scope);
  });
}

function withIdempotencyHeader(headers = {}, idempotencyKey = "") {
  const key = String(idempotencyKey || "").trim();
  if (!key) return { ...headers };
  return {
    ...headers,
    "X-Idempotency-Key": key,
  };
}

async function fetchTradeNegotiationInbox({ teamId, status = "ACTIVE", phase = "OPEN", signal = undefined } = {}) {
  const normalizedTeamId = String(teamId || "").trim();
  if (!normalizedTeamId) throw new Error("team_id가 필요합니다.");
  const params = new URLSearchParams({
    team_id: normalizedTeamId,
    status: String(status || "ACTIVE"),
    phase: String(phase || "OPEN"),
  });
  return fetchJson(`/api/trade/negotiation/inbox?${params.toString()}`, { signal });
}

async function openTradeNegotiationSession({ sessionId, teamId, signal = undefined, idempotencyKey = "" } = {}) {
  if (!sessionId) throw new Error("session_id가 필요합니다.");
  if (!teamId) throw new Error("team_id가 필요합니다.");
  return fetchJson("/api/trade/negotiation/open", {
    method: "POST",
    headers: withIdempotencyHeader({ "Content-Type": "application/json" }, idempotencyKey),
    body: JSON.stringify({ session_id: sessionId, team_id: teamId, idempotency_key: idempotencyKey || null }),
    signal,
  });
}

async function rejectTradeNegotiationSession({ sessionId, teamId, reason = "USER_REJECT", signal = undefined, idempotencyKey = "" } = {}) {
  if (!sessionId) throw new Error("session_id가 필요합니다.");
  if (!teamId) throw new Error("team_id가 필요합니다.");
  return fetchJson("/api/trade/negotiation/reject", {
    method: "POST",
    headers: withIdempotencyHeader({ "Content-Type": "application/json" }, idempotencyKey),
    body: JSON.stringify({ session_id: sessionId, team_id: teamId, reason, idempotency_key: idempotencyKey || null }),
    signal,
  });
}

async function startTradeNegotiationSession({ userTeamId, otherTeamId, defaultOfferPrivacy = "PRIVATE", signal = undefined, idempotencyKey = "" } = {}) {
  if (!userTeamId) throw new Error("user_team_id가 필요합니다.");
  if (!otherTeamId) throw new Error("other_team_id가 필요합니다.");
  return fetchJson("/api/trade/negotiation/start", {
    method: "POST",
    headers: withIdempotencyHeader({ "Content-Type": "application/json" }, idempotencyKey),
    body: JSON.stringify({
      user_team_id: userTeamId,
      other_team_id: otherTeamId,
      default_offer_privacy: defaultOfferPrivacy,
      idempotency_key: idempotencyKey || null,
    }),
    signal,
  });
}

async function commitTradeNegotiationSession({ sessionId, deal, offerPrivacy = "PRIVATE", exposeToMedia = false, signal = undefined, idempotencyKey = "" } = {}) {
  if (!sessionId) throw new Error("session_id가 필요합니다.");
  if (!deal || typeof deal !== "object") throw new Error("deal payload가 필요합니다.");
  return fetchJson("/api/trade/negotiation/commit", {
    method: "POST",
    headers: withIdempotencyHeader({ "Content-Type": "application/json" }, idempotencyKey),
    body: JSON.stringify({
      session_id: sessionId,
      deal,
      offer_privacy: offerPrivacy,
      expose_to_media: !!exposeToMedia,
      idempotency_key: idempotencyKey || null,
    }),
    signal,
  });
}

async function submitCommittedTradeDeal({ dealId, force = true, signal = undefined, idempotencyKey = "" } = {}) {
  if (!dealId) throw new Error("deal_id가 필요합니다.");
  return fetchJson("/api/trade/submit-committed", {
    method: "POST",
    headers: withIdempotencyHeader({ "Content-Type": "application/json" }, idempotencyKey),
    body: JSON.stringify({ deal_id: dealId, force: !!force, idempotency_key: idempotencyKey || null }),
    signal,
  });
}

async function fetchStateSummary({ signal = undefined } = {}) {
  return fetchJson("/api/state/summary", { signal });
}

async function fetchHomeAttention(
  teamId,
  { limit = 50, offset = 0 } = {},
  { signal = undefined } = {},
) {
  const normalizedTeamId = String(teamId || "").trim().toUpperCase();
  if (!normalizedTeamId) throw new Error("team_id가 필요합니다.");

  const params = new URLSearchParams({
    limit: String(Number.isFinite(Number(limit)) ? Number(limit) : 50),
    offset: String(Number.isFinite(Number(offset)) ? Number(offset) : 0),
  });
  return fetchJson(`/api/home/attention/${encodeURIComponent(normalizedTeamId)}?${params.toString()}`, { signal });
}

async function fetchTradeLabTeamAssets({ teamId, signal = undefined } = {}) {
  const normalizedTeamId = String(teamId || "").trim().toUpperCase();
  if (!normalizedTeamId) throw new Error("team_id가 필요합니다.");
  const params = new URLSearchParams({ team_id: normalizedTeamId });
  return fetchJson(`/api/trade/lab/team-assets?${params.toString()}`, { signal });
}

async function evaluateTradeDealForTeam({ deal, teamId, includeBreakdown = true, signal = undefined } = {}) {
  const normalizedTeamId = String(teamId || "").trim().toUpperCase();
  if (!normalizedTeamId) throw new Error("team_id가 필요합니다.");
  if (!deal || typeof deal !== "object") throw new Error("deal payload가 필요합니다.");
  return fetchJson("/api/trade/evaluate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      deal,
      team_id: normalizedTeamId,
      include_breakdown: Boolean(includeBreakdown),
    }),
    signal,
  });
}

function normalizeCacheKey(key) {
  return String(key || "").trim();
}

function normalizeOptionalString(value) {
  if (value == null) return null;
  const out = String(value).trim();
  return out || null;
}

function getCacheStore() {
  if (!state.viewCache || typeof state.viewCache !== "object") state.viewCache = {};
  return state.viewCache;
}

function getInflightStore() {
  if (!(state.inflightRequests instanceof Map)) state.inflightRequests = new Map();
  return state.inflightRequests;
}

function normalizeTtlMs(ttlMs) {
  const normalized = Number(ttlMs);
  if (!Number.isFinite(normalized) || normalized < 0) return 0;
  return normalized;
}

function resolveCacheGroup(key) {
  const normalized = normalizeCacheKey(key);
  if (!normalized) return "misc";
  const matchedPrefix = Object.keys(GROUP_ENTRY_LIMITS).find((prefix) => normalized.startsWith(prefix));
  return matchedPrefix || "misc";
}

function estimatePayloadSize(data) {
  if (data == null) return 0;
  if (typeof data === "string") return data.length;
  if (typeof data === "number" || typeof data === "boolean") return 8;
  if (Array.isArray(data)) return Math.min(data.length * 32, 20000);
  if (typeof data === "object") return Math.min(Object.keys(data).length * 48, 20000);
  return 256;
}

function ensureCacheEntryShape(entry, key = "") {
  if (!entry || typeof entry !== "object") return null;
  const fetchedAt = Number(entry.fetchedAt) || Date.now();
  const lastAccessedAt = Number(entry.lastAccessedAt) || fetchedAt;
  const ttlMs = normalizeTtlMs(entry.ttlMs);
  const cacheGroup = entry.cacheGroup || resolveCacheGroup(key);
  const estimatedSize = Number(entry.estimatedSize);
  const sourceEventVersion = normalizeOptionalString(entry.sourceEventVersion);
  const domainTag = normalizeOptionalString(entry.domainTag);
  return {
    ...entry,
    fetchedAt,
    lastAccessedAt,
    ttlMs,
    cacheGroup,
    sourceEventVersion,
    domainTag,
    estimatedSize: Number.isFinite(estimatedSize) ? estimatedSize : estimatePayloadSize(entry.data),
  };
}

function deleteCachedEntry(key, reason = "manual") {
  const normalized = normalizeCacheKey(key);
  if (!normalized) return false;
  const cacheStore = getCacheStore();
  if (!(normalized in cacheStore)) return false;
  delete cacheStore[normalized];
  recordMetric("evictions");
  if (reason === "expired") recordMetric("evictedExpired");
  if (reason === "lru") recordMetric("evictedByLru");
  if (reason === "group") recordMetric("evictedByGroup");
  return true;
}

function isCacheEntryExpired(entry, now = Date.now()) {
  if (!entry) return false;
  if (entry.ttlMs <= 0) return false;
  return now - entry.fetchedAt > entry.ttlMs;
}

function evictExpiredEntries(now = Date.now()) {
  const cacheStore = getCacheStore();
  Object.keys(cacheStore).forEach((key) => {
    const normalizedEntry = ensureCacheEntryShape(cacheStore[key], key);
    if (!normalizedEntry) {
      deleteCachedEntry(key, "manual");
      return;
    }
    cacheStore[key] = normalizedEntry;
    if (isCacheEntryExpired(normalizedEntry, now)) {
      deleteCachedEntry(key, "expired");
    }
  });
}

function getEvictionCandidates(prefix = "") {
  const cacheStore = getCacheStore();
  const inflightStore = getInflightStore();
  return Object.entries(cacheStore)
    .map(([key, entry]) => {
      const normalizedEntry = ensureCacheEntryShape(entry, key);
      if (normalizedEntry) cacheStore[key] = normalizedEntry;
      return [key, normalizedEntry];
    })
    .filter(([key, entry]) => {
      if (!entry) return false;
      if (prefix && !key.startsWith(prefix)) return false;
      return !inflightStore.has(key);
    })
    .sort(([, a], [, b]) => a.lastAccessedAt - b.lastAccessedAt);
}

function evictByLru({ targetCount = 0, prefix = "", reason = "lru" } = {}) {
  const candidates = getEvictionCandidates(prefix);
  if (!candidates.length) return;
  let activeCount = Object.keys(getCacheStore()).length;
  for (const [key] of candidates) {
    if (activeCount <= targetCount) break;
    if (deleteCachedEntry(key, reason)) activeCount -= 1;
  }
}

function evictByGroupLimit(prefix) {
  const limit = GROUP_ENTRY_LIMITS[prefix];
  if (!Number.isFinite(limit) || limit < 1) return;
  const cacheStore = getCacheStore();
  const groupKeys = Object.keys(cacheStore).filter((key) => key.startsWith(prefix));
  if (groupKeys.length <= limit) return;

  const overflow = groupKeys.length - limit;
  const targetCount = Object.keys(cacheStore).length - overflow;
  evictByLru({ targetCount, prefix, reason: "group" });
}

function maybeRunCacheMaintenance({ incomingKey = "", forceFullSweep = false } = {}) {
  maintenanceWriteTick += 1;
  const cacheSize = Object.keys(getCacheStore()).length;
  const shouldSweep = forceFullSweep || maintenanceWriteTick % MAINTENANCE_SWEEP_INTERVAL === 0 || cacheSize > MAX_CACHE_ENTRIES;

  if (shouldSweep) {
    evictExpiredEntries();
  }

  if (incomingKey) {
    const group = resolveCacheGroup(incomingKey);
    if (group && group !== "misc") {
      evictByGroupLimit(group);
    }
  }

  const nextSize = Object.keys(getCacheStore()).length;
  if (nextSize > MAX_CACHE_ENTRIES) {
    evictByLru({ targetCount: TARGET_CACHE_ENTRIES_AFTER_EVICTION, reason: "lru" });
  }

  maybeLogCacheMetrics();
}

function getCachedValue(key) {
  const normalized = normalizeCacheKey(key);
  if (!normalized) return null;
  const cacheStore = getCacheStore();
  const entry = ensureCacheEntryShape(cacheStore[normalized], normalized);
  if (!entry) {
    recordMetric("misses");
    return null;
  }
  if (isCacheEntryExpired(entry)) {
    deleteCachedEntry(normalized, "expired");
    recordMetric("misses");
    return null;
  }
  entry.lastAccessedAt = Date.now();
  cacheStore[normalized] = entry;
  recordMetric("hits");
  return entry;
}

function setCachedValue(key, data, fetchedAt = Date.now(), options = {}) {
  const normalized = normalizeCacheKey(key);
  if (!normalized) return;
  const now = Date.now();
  const ttlMs = normalizeTtlMs(options.ttlMs);
  const cacheGroup = options.cacheGroup || resolveCacheGroup(normalized);
  const sourceEventVersion = normalizeOptionalString(options.sourceEventVersion);
  const domainTag = normalizeOptionalString(options.domainTag);
  getCacheStore()[normalized] = {
    data,
    fetchedAt: Number(fetchedAt) || Date.now(),
    lastAccessedAt: now,
    ttlMs,
    cacheGroup,
    sourceEventVersion,
    domainTag,
    estimatedSize: estimatePayloadSize(data),
  };
  maybeRunCacheMaintenance({ incomingKey: normalized });
}

function invalidateCachedValue(key) {
  const normalized = normalizeCacheKey(key);
  if (!normalized) return;
  const inflightStore = getInflightStore();
  deleteCachedEntry(normalized, "manual");
  inflightStore.delete(normalized);
}

function invalidateCachedValuesByPrefix(prefix) {
  const normalized = normalizeCacheKey(prefix);
  if (!normalized) return;
  const cacheStore = getCacheStore();
  const inflightStore = getInflightStore();
  Object.keys(cacheStore).forEach((key) => {
    if (key.startsWith(normalized)) deleteCachedEntry(key, "manual");
  });
  Array.from(inflightStore.keys()).forEach((key) => {
    if (String(key).startsWith(normalized)) inflightStore.delete(key);
  });
}

function invalidateCacheKeys(keys) {
  const list = Array.isArray(keys) ? keys : [];
  list.forEach((key) => invalidateCachedValue(key));
}

function clearAllCachedValues() {
  state.viewCache = {};
  if (state.inflightRequests instanceof Map) state.inflightRequests.clear();
  else state.inflightRequests = new Map();
  maintenanceWriteTick = 0;
}

async function fetchCachedJson({
  key,
  url,
  options = {},
  ttlMs = 10000,
  staleWhileRevalidate = true,
  force = false,
  onRevalidated = null,
}) {
  const normalizedKey = normalizeCacheKey(key);
  if (!normalizedKey) {
    recordMetric("networkFetches");
    return fetchJson(url, options);
  }

  const inflightStore = getInflightStore();
  const cached = getCachedValue(normalizedKey);
  const now = Date.now();
  const maxAge = normalizeTtlMs(ttlMs);
  const isFresh = cached && (maxAge <= 0 || (now - Number(cached.fetchedAt || 0) <= maxAge));

  if (!force && isFresh) {
    return cached.data;
  }

  const doFetch = async () => {
    recordMetric("networkFetches");
    const data = await fetchJson(url, options);
    setCachedValue(normalizedKey, data, Date.now(), { ttlMs: maxAge });
    return data;
  };

  if (!force && cached && staleWhileRevalidate) {
    recordMetric("staleServed");
    if (!inflightStore.has(normalizedKey)) {
      const revalidatePromise = doFetch()
        .then((data) => {
          if (typeof onRevalidated === "function") onRevalidated(data);
          return data;
        })
        .finally(() => {
          inflightStore.delete(normalizedKey);
        });
      inflightStore.set(normalizedKey, revalidatePromise);
    }
    maybeLogCacheMetrics();
    return cached.data;
  }

  if (inflightStore.has(normalizedKey)) {
    return inflightStore.get(normalizedKey);
  }

  const request = doFetch().finally(() => {
    inflightStore.delete(normalizedKey);
  });
  inflightStore.set(normalizedKey, request);
  maybeLogCacheMetrics();
  return request;
}

async function prefetchCachedJson(params = {}) {
  try {
    return await fetchCachedJson({
      ...params,
      staleWhileRevalidate: params.staleWhileRevalidate ?? true,
    });
  } catch (e) {
    return null;
  }
}

function setLoading(show, msg = "") {
  els.loadingOverlay.classList.toggle("hidden", !show);
  if (msg) els.loadingText.textContent = msg;
}

function showConfirmModal({ title, body, okLabel = "확인", cancelLabel = "취소" }) {
  if (!els.confirmModal) return Promise.resolve(window.confirm(body || title || "진행하시겠습니까?"));
  return new Promise((resolve) => {
    const active = document.activeElement;
    if (els.confirmModalTitle) els.confirmModalTitle.textContent = title || "확인";
    if (els.confirmModalBody) els.confirmModalBody.textContent = body || "";
    if (els.confirmModalOk) els.confirmModalOk.textContent = okLabel;
    if (els.confirmModalCancel) els.confirmModalCancel.textContent = cancelLabel;

    els.confirmModal.classList.remove("hidden");
    document.body.classList.add("is-modal-open");

    const close = (result) => {
      els.confirmModal.classList.add("hidden");
      document.body.classList.remove("is-modal-open");
      els.confirmModalOk?.removeEventListener("click", onOk);
      els.confirmModalCancel?.removeEventListener("click", onCancel);
      els.confirmModalBackdrop?.removeEventListener("click", onCancel);
      document.removeEventListener("keydown", onKeydown);
      if (active instanceof HTMLElement) active.focus();
      resolve(result);
    };

    const onOk = () => close(true);
    const onCancel = () => close(false);
    const onKeydown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close(false);
      }
    };

    els.confirmModalOk?.addEventListener("click", onOk);
    els.confirmModalCancel?.addEventListener("click", onCancel);
    els.confirmModalBackdrop?.addEventListener("click", onCancel);
    document.addEventListener("keydown", onKeydown);
    els.confirmModalOk?.focus();
  });
}

export {
  fetchJson,
  beginScopedRequest,
  isScopedRequestCurrent,
  abortScopedRequest,
  abortAllMarketTradeRequests,
  fetchTradeNegotiationInbox,
  startTradeNegotiationSession,
  openTradeNegotiationSession,
  rejectTradeNegotiationSession,
  commitTradeNegotiationSession,
  submitCommittedTradeDeal,
  fetchStateSummary,
  fetchHomeAttention,
  fetchTradeLabTeamAssets,
  evaluateTradeDealForTeam,
  fetchCachedJson,
  getCachedValue,
  setCachedValue,
  invalidateCachedValue,
  invalidateCachedValuesByPrefix,
  invalidateCacheKeys,
  clearAllCachedValues,
  prefetchCachedJson,
  setLoading,
  showConfirmModal,
};
