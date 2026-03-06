import { els } from "../app/dom.js";
import { state } from "../app/state.js";

const MAX_CACHE_ENTRIES = 180;
const TARGET_CACHE_ENTRIES_AFTER_EVICTION = 140;
const GROUP_ENTRY_LIMITS = {
  "training:session:": 56,
};

async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `요청 실패: ${url}`);
  return data;
}

function normalizeCacheKey(key) {
  return String(key || "").trim();
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
  try {
    return JSON.stringify(data).length;
  } catch (_error) {
    return 1024;
  }
}

function ensureCacheEntryShape(entry, key = "") {
  if (!entry || typeof entry !== "object") return null;
  const fetchedAt = Number(entry.fetchedAt) || Date.now();
  const lastAccessedAt = Number(entry.lastAccessedAt) || fetchedAt;
  const ttlMs = normalizeTtlMs(entry.ttlMs);
  const cacheGroup = entry.cacheGroup || resolveCacheGroup(key);
  const estimatedSize = Number(entry.estimatedSize);
  return {
    ...entry,
    fetchedAt,
    lastAccessedAt,
    ttlMs,
    cacheGroup,
    estimatedSize: Number.isFinite(estimatedSize) ? estimatedSize : estimatePayloadSize(entry.data),
  };
}

function deleteCachedEntry(key) {
  const normalized = normalizeCacheKey(key);
  if (!normalized) return false;
  const cacheStore = getCacheStore();
  if (!(normalized in cacheStore)) return false;
  delete cacheStore[normalized];
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
      deleteCachedEntry(key);
      return;
    }
    cacheStore[key] = normalizedEntry;
    if (isCacheEntryExpired(normalizedEntry, now)) {
      deleteCachedEntry(key);
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

function evictByLru({ targetCount = 0, prefix = "" } = {}) {
  const candidates = getEvictionCandidates(prefix);
  if (!candidates.length) return;
  const cacheStore = getCacheStore();
  let activeCount = Object.keys(cacheStore).length;
  for (const [key] of candidates) {
    if (activeCount <= targetCount) break;
    if (deleteCachedEntry(key)) activeCount -= 1;
  }
}

function evictByGroupLimit() {
  const cacheStore = getCacheStore();
  Object.entries(GROUP_ENTRY_LIMITS).forEach(([prefix, limit]) => {
    const groupKeys = Object.keys(cacheStore).filter((key) => key.startsWith(prefix));
    if (groupKeys.length <= limit) return;
    evictByLru({ targetCount: Object.keys(cacheStore).length - (groupKeys.length - limit), prefix });
  });
}

function evictCacheEntriesIfNeeded() {
  evictExpiredEntries();
  evictByGroupLimit();
  const cacheSize = Object.keys(getCacheStore()).length;
  if (cacheSize <= MAX_CACHE_ENTRIES) return;
  evictByLru({ targetCount: TARGET_CACHE_ENTRIES_AFTER_EVICTION });
}

function getCachedValue(key) {
  const normalized = normalizeCacheKey(key);
  if (!normalized) return null;
  const cacheStore = getCacheStore();
  const entry = ensureCacheEntryShape(cacheStore[normalized], normalized);
  if (!entry) return null;
  entry.lastAccessedAt = Date.now();
  cacheStore[normalized] = entry;
  return entry;
}

function setCachedValue(key, data, fetchedAt = Date.now(), options = {}) {
  const normalized = normalizeCacheKey(key);
  if (!normalized) return;
  const now = Date.now();
  const ttlMs = normalizeTtlMs(options.ttlMs);
  const cacheGroup = options.cacheGroup || resolveCacheGroup(normalized);
  getCacheStore()[normalized] = {
    data,
    fetchedAt: Number(fetchedAt) || Date.now(),
    lastAccessedAt: now,
    ttlMs,
    cacheGroup,
    estimatedSize: estimatePayloadSize(data),
  };
  evictCacheEntriesIfNeeded();
}

function invalidateCachedValue(key) {
  const normalized = normalizeCacheKey(key);
  if (!normalized) return;
  const inflightStore = getInflightStore();
  deleteCachedEntry(normalized);
  inflightStore.delete(normalized);
}

function invalidateCachedValuesByPrefix(prefix) {
  const normalized = normalizeCacheKey(prefix);
  if (!normalized) return;
  const cacheStore = getCacheStore();
  const inflightStore = getInflightStore();
  Object.keys(cacheStore).forEach((key) => {
    if (key.startsWith(normalized)) deleteCachedEntry(key);
  });
  Array.from(inflightStore.keys()).forEach((key) => {
    if (String(key).startsWith(normalized)) inflightStore.delete(key);
  });
}

function clearAllCachedValues() {
  state.viewCache = {};
  if (state.inflightRequests instanceof Map) state.inflightRequests.clear();
  else state.inflightRequests = new Map();
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
  if (!normalizedKey) return fetchJson(url, options);

  const inflightStore = getInflightStore();
  const cached = getCachedValue(normalizedKey);
  const now = Date.now();
  const maxAge = Math.max(0, Number(ttlMs) || 0);
  const isFresh = cached && (maxAge <= 0 || (now - Number(cached.fetchedAt || 0) <= maxAge));

  if (!force && isFresh) {
    return cached.data;
  }

  const doFetch = async () => {
    const data = await fetchJson(url, options);
    setCachedValue(normalizedKey, data, Date.now(), { ttlMs: maxAge });
    return data;
  };

  if (!force && cached && staleWhileRevalidate) {
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
    return cached.data;
  }

  if (inflightStore.has(normalizedKey)) {
    return inflightStore.get(normalizedKey);
  }

  const request = doFetch().finally(() => {
    inflightStore.delete(normalizedKey);
  });
  inflightStore.set(normalizedKey, request);
  return request;
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
  fetchCachedJson,
  getCachedValue,
  setCachedValue,
  invalidateCachedValue,
  invalidateCachedValuesByPrefix,
  clearAllCachedValues,
  setLoading,
  showConfirmModal,
};
