function reportTradeContractViolation(payload = {}) {
  const base = payload && typeof payload === "object" ? payload : {};
  const eventPayload = {
    event_name: "trade_contract_violation_detected",
    screen: "market_trade_inbox",
    endpoint: String(base.endpoint || ""),
    session_id: String(base.session_id || ""),
    asset_kind: String(base.asset_kind || ""),
    asset_ref: String(base.asset_ref || ""),
    path: String(base.path || ""),
    missing_fields: Array.isArray(base.missing_fields)
      ? base.missing_fields.map((item) => String(item)).filter(Boolean)
      : [],
    direction: String(base.direction || ""),
    detected_at: String(base.detected_at || new Date().toISOString()),
  };

  try {
    console.warn("[trade-contract-violation:report]", eventPayload);
  } catch {
    // no-op
  }

  fetch("/api/telemetry/client", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(eventPayload),
    keepalive: true,
  }).catch(() => {
    // telemetry should never block UX.
  });
}

export { reportTradeContractViolation };
