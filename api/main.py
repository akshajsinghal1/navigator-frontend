"""
api/main.py
────────────
FastAPI application entry point.

Endpoints:
  POST /onboard                   — connect a company's Tableau workbook
  GET  /dashboard/{company_id}    — get the Intelligence Config
  GET  /pipeline/{run_id}/status  — check pipeline run status
  GET  /health                    — health check

Run with:
  uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.onboard import router as onboard_router
from api.routes.dashboard import router as dashboard_router
from api.routes.pipeline import router as pipeline_router
from api.routes.config import router as config_router
from api.routes.viewdata import router as viewdata_router
from api.routes.freshness import router as freshness_router

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title       = "Navigator API",
        description = "AI-powered Tableau Intelligence Platform",
        version     = "1.0.0",
    )

    # ── CORS ────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins     = ["*"],   # tighten in production
        allow_credentials = True,
        allow_methods     = ["*"],
        allow_headers     = ["*"],
    )

    # ── routers ──────────────────────────────────────────────────────────────
    app.include_router(onboard_router,   prefix="/onboard",   tags=["Onboarding"])
    app.include_router(dashboard_router, prefix="/dashboard",  tags=["Dashboard"])
    app.include_router(pipeline_router,  prefix="/pipeline",   tags=["Pipeline"])
    app.include_router(config_router,    prefix="/config",     tags=["Config"])
    app.include_router(viewdata_router,  prefix="/viewdata",   tags=["ViewData"])
    app.include_router(freshness_router, prefix="/freshness",  tags=["Freshness"])

    # ── startup ──────────────────────────────────────────────────────────────
    @app.on_event("startup")
    async def startup():
        log.info("Navigator API starting up")
        # Create DB tables on startup (idempotent)
        try:
            from storage.db import create_all_tables
            create_all_tables()
        except Exception as exc:
            log.warning("DB init skipped (no DB configured): %s", exc)
        # Start background freshness monitor
        from pipeline.freshness_monitor import start as start_freshness_monitor
        start_freshness_monitor()

    # ── health ────────────────────────────────────────────────────────────────
    @app.get("/health", tags=["Health"])
    def health():
        return {"status": "ok"}

    return app


app = create_app()
