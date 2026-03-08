from __future__ import annotations

import importlib
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def emit_trade_contract_violation(event: Dict[str, Any]) -> None:
    payload = dict(event) if isinstance(event, dict) else {}
    endpoint = str(payload.get("endpoint") or "")
    session_id = str(payload.get("session_id") or "")
    asset_kind = str(payload.get("asset_kind") or "")
    missing_fields = payload.get("missing_fields")
    if not isinstance(missing_fields, list):
        missing_fields = []

    logger.warning(
        "trade_contract_violation_detected",
        extra={
            "event": "trade_contract_violation_detected",
            "endpoint": endpoint,
            "session_id": session_id,
            "asset_kind": asset_kind,
            "asset_ref": str(payload.get("asset_ref") or ""),
            "path": str(payload.get("path") or ""),
            "missing_fields": [str(x) for x in missing_fields],
            "source": str(payload.get("source") or "server"),
        },
    )

    try:
        sentry_sdk = importlib.import_module("sentry_sdk")
    except Exception:
        return

    try:
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("event", "trade_contract_violation_detected")
            if endpoint:
                scope.set_tag("endpoint", endpoint)
            if session_id:
                scope.set_tag("session_id", session_id)
            if asset_kind:
                scope.set_tag("asset_kind", asset_kind)
            scope.set_context("trade_contract_violation", payload)
            sentry_sdk.capture_message("trade_contract_violation_detected", level="warning")
    except Exception:
        # Telemetry must never break request handling.
        return
