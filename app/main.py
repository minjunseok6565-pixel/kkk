from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from config import BASE_DIR
from team_utils import ui_cache_rebuild_all
import state
from app.api.router import api_router

logger = logging.getLogger(__name__)

app = FastAPI(title="느바 시뮬 GM 서버")

@app.on_event("startup")
def _startup_init_state() -> None:
    # 1) DB init + seed once (per db_path)
    # 2) SSOT state init: season/schedule + cap model
    # 3) repo integrity validate once (per db_path)
    # 4) ingest_turn backfill once (per state instance)
    # 5) UI-only cache bootstrap (derived, non-authoritative)
    db_path = os.environ.get("LEAGUE_DB_PATH")
    if not db_path:
        raise RuntimeError("LEAGUE_DB_PATH is required (no default db_path).")
    state.set_db_path(db_path)

    state.startup_init_state()

    # Explicit UI-only cache bootstrap (derived, non-authoritative).
    # Ensures team/player UI metadata exists from server boot without requiring any read path to "init".
    try:
        ui_cache_rebuild_all()
    except Exception as e:
        raise RuntimeError(f"ui_cache_rebuild_all() failed during startup: {e}") from e


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _auth_guard_middleware(request: Request, call_next):
    """Optional commercial auth guard.

    If NBA_SIM_ADMIN_TOKEN is configured, require it on state-changing API calls.
    """
    required_token = (os.environ.get("NBA_SIM_ADMIN_TOKEN") or "").strip()
    if not required_token:
        return await call_next(request)

    path = request.url.path or ""
    method = (request.method or "GET").upper()
    if method != "POST" or not path.startswith("/api/"):
        return await call_next(request)

    # Keep health/auth bootstrap available.
    if path in {"/api/validate-key"}:
        return await call_next(request)

    provided = (request.headers.get("X-Admin-Token") or "").strip()
    if provided != required_token:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized: invalid X-Admin-Token"})

    return await call_next(request)

# static/NBA.html 서빙
static_dir = os.path.join(BASE_DIR, "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.include_router(api_router)
