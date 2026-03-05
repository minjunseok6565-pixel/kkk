import { els } from "../app/dom.js";
import { state } from "../app/state.js";

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

function getCachedValue(key) {
  const normalized = normalizeCacheKey(key);
  if (!normalized) return null;
  const entry = getCacheStore()[normalized];
  if (!entry || typeof entry !== "object") return null;
  return entry;
}

function setCachedValue(key, data, fetchedAt = Date.now()) {
  const normalized = normalizeCacheKey(key);
  if (!normalized) return;
  getCacheStore()[normalized] = {
    data,
    fetchedAt: Number(fetchedAt) || Date.now(),
  };
}

function invalidateCachedValue(key) {
  const normalized = normalizeCacheKey(key);
  if (!normalized) return;
  const cacheStore = getCacheStore();
  const inflightStore = getInflightStore();
  delete cacheStore[normalized];
  inflightStore.delete(normalized);
}

function invalidateCachedValuesByPrefix(prefix) {
  const normalized = normalizeCacheKey(prefix);
  if (!normalized) return;
  const cacheStore = getCacheStore();
  const inflightStore = getInflightStore();
  Object.keys(cacheStore).forEach((key) => {
    if (key.startsWith(normalized)) delete cacheStore[key];
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
    setCachedValue(normalizedKey, data);
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
