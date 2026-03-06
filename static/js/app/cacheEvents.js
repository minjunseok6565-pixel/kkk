const listeners = new Map();

function normalizeEventType(type) {
  return String(type || "").trim().toUpperCase();
}

function onCacheEvent(type, handler) {
  const eventType = normalizeEventType(type);
  if (!eventType || typeof handler !== "function") {
    return () => {};
  }
  const set = listeners.get(eventType) || new Set();
  set.add(handler);
  listeners.set(eventType, set);
  return () => {
    const current = listeners.get(eventType);
    if (!current) return;
    current.delete(handler);
    if (!current.size) listeners.delete(eventType);
  };
}

function emitCacheEvent(type, payload = {}) {
  const eventType = normalizeEventType(type);
  if (!eventType) return;
  const current = listeners.get(eventType);
  if (!current || !current.size) return;
  current.forEach((handler) => {
    try {
      handler(payload);
    } catch (e) {
      // fail-soft for UI event bus
    }
  });
}

function clearCacheEventListeners() {
  listeners.clear();
}

export {
  onCacheEvent,
  emitCacheEvent,
  clearCacheEventListeners,
};
