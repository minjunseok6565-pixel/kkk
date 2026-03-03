from __future__ import annotations

from fastapi.responses import JSONResponse

from trades.errors import TradeError


def _trade_error_response(error: TradeError) -> JSONResponse:
    payload = {
        "ok": False,
        "error": {
            "code": error.code,
            "message": error.message,
            "details": error.details,
        },
    }
    return JSONResponse(status_code=400, content=payload)
