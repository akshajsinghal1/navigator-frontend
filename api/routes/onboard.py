"""
api/routes/onboard.py
──────────────────────
POST /onboard — connect a new company's Tableau workbook and kick off pipeline.
GET  /onboard/{company_id}/latest — get the latest run_id for a company.
"""

from __future__ import annotations

import logging
import threading
import uuid

from fastapi import APIRouter, HTTPException

from schemas.api import OnboardRequest, OnboardResponse
from api.pipeline_status import (
    create_run, emit, set_status, get_run_for_company, to_dict,
    save_run_log, RunLogHandler,
)
from run_context import set_run_id

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("", response_model=OnboardResponse)
def onboard(req: OnboardRequest):
    """
    Onboard a company: validate credentials, start pipeline, return run_id immediately.
    """
    company_id = req.company_id or _slugify(req.workbook_content_url)
    run_id = str(uuid.uuid4())

    # Register run in in-memory store right away so polling can start immediately
    create_run(run_id, company_id, req.workbook_content_url)
    emit(run_id, "Pipeline", "Onboarding started — validating Tableau credentials…", progress=2)

    # Try to save to DB (optional — no DB in demo mode is fine)
    try:
        from storage.db import get_session, CompanyRepo, PipelineRunRepo
        with get_session() as session:
            if CompanyRepo.get(session, company_id) is None:
                CompanyRepo.create(session, company_id, {
                    "tableau_server_url":   req.tableau_server_url,
                    "tableau_site_name":    req.tableau_site_name,
                    "tableau_pat_name":     req.tableau_pat_name,
                    "tableau_pat_secret":   req.tableau_pat_secret,
                    "workbook_content_url": req.workbook_content_url,
                })
            PipelineRunRepo.create(session, company_id, trigger="onboard")
    except Exception as exc:
        log.debug("DB save skipped (no DB configured): %s", exc)

    creds = {
        "tableau_server_url": req.tableau_server_url,
        "tableau_site_name":  req.tableau_site_name,
        "tableau_pat_name":   req.tableau_pat_name,
        "tableau_pat_secret": req.tableau_pat_secret,
    }

    org_context = {
        "organization_id": req.organization_id,
        "industry_name":   req.industry_name,
        "required_personas": [p.model_dump() for p in req.required_personas],
    }

    thread = threading.Thread(
        target=_run_pipeline_thread,
        args=(company_id, run_id, req.workbook_content_url, creds, org_context),
        daemon=True,
    )
    thread.start()

    return OnboardResponse(company_id=company_id, run_id=run_id, status="queued")


@router.get("/{company_id}/latest")
def get_latest_run(company_id: str):
    """Return the latest run for a company (for resuming the progress screen)."""
    run = get_run_for_company(company_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No runs found for company '{company_id}'")
    return to_dict(run)


def _run_pipeline_thread(
    company_id: str,
    run_id: str,
    workbook_content_url: str,
    creds: dict,
    org_context: dict | None = None,
) -> None:
    """Background thread: run the full pipeline with rich status messages."""
    log.info("[%s] Pipeline thread started for %s", run_id, company_id)

    # Tag this thread so RunLogHandler can isolate logs from concurrent runs
    set_run_id(run_id)

    # Attach a log handler that routes agent messages into the status store
    handler = RunLogHandler(run_id)
    handler.setLevel(logging.INFO)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    try:
        set_status(run_id, "running")

        # ── Step 1: Validate Tableau connection ───────────────────────────────
        # Use VdsClient (direct REST) — same auth path as the pipeline itself.
        # TableauConnector (TSC library) sometimes fails with 401 even with valid
        # PATs due to TSC version/session handling issues.
        emit(run_id, "Tableau", "Connecting to Tableau server…", progress=5, stage="connecting")
        try:
            from tableau.vds import VdsClient
            with VdsClient(
                server_url = creds["tableau_server_url"],
                site_name  = creds["tableau_site_name"],
                pat_name   = creds["tableau_pat_name"],
                pat_secret = creds["tableau_pat_secret"],
            ) as conn:
                wb = conn.get_workbook_by_content_url(workbook_content_url)
                views = conn.list_views(wb["luid"])
        except Exception as exc:
            emit(run_id, "Tableau", f"Connection failed: {exc}", level="error")
            set_status(run_id, "failed", error=str(exc))
            return

        emit(run_id, "Tableau",
             f"Connected ✓  Workbook: {wb['name']}  ({len(views)} views)",
             level="success", progress=10)

        # ── Steps 2–5: Full pipeline via PipelineRunner ───────────────────────
        # PipelineRunner uses VdsClient (single auth session) + field manifest
        # so domain agents receive EXACT column names, not guesses.
        emit(run_id, "Inventory", "Extracting workbook schema via Metadata API (GraphQL)…",
             progress=12, stage="inventory_extraction")

        from pipeline.runner import PipelineRunner
        runner = PipelineRunner(creds)

        # Step 2a: extract inventory
        raw_inventory = runner._extract_inventory(workbook_content_url)
        emit(run_id, "Inventory", "Schema extraction complete ✓", level="success", progress=22)

        # Step 2b: save inventory to disk so runner can re-use it (avoid double sign-in)
        from pathlib import Path
        from datetime import datetime, timezone as tz
        import json

        inv_dir = Path("output")
        inv_dir.mkdir(exist_ok=True)
        inv_ts   = datetime.now(tz.utc).strftime("%Y%m%dT%H%M%SZ")
        inv_path = inv_dir / f"inventory_{workbook_content_url}_{inv_ts}.json"
        inv_path.write_text(json.dumps(raw_inventory, ensure_ascii=False), encoding="utf-8")

        # Steps 3–5: filter → EDA → manifest → orchestrator → domain/chart agents
        emit(run_id, "Pipeline", "Building field manifest & running AI agents…",
             progress=25, stage="orchestrator")

        config, cfg_path = runner.run_and_save(
            workbook_content_url    = workbook_content_url,
            output_dir              = "output",
            existing_inventory_path = inv_path,
            org_context             = org_context or {},
        )

        emit(run_id, "Orchestrator",
             f"Intelligence Config assembled — "
             f"{len(config.personas)} personas, "
             f"{sum(len(s.kpis) for p in config.personas for s in p.dashboard_sections)} KPIs ✓",
             level="success", progress=90, stage="assembling")

        emit(run_id, "Pipeline", f"Config saved → {cfg_path.name}",
             level="success", progress=100)

        # Try DB save
        try:
            from storage.db import get_session, ConfigRepo
            with get_session() as s:
                ConfigRepo.upsert(s, company_id, run_id, config_dict=config.model_dump())
        except Exception:
            pass

        set_status(run_id, "completed", progress=100)
        log.info("[%s] Pipeline completed successfully", run_id)

        # Warm Redis cache immediately so first dashboard load is instant
        try:
            from storage.cache import ConfigCache
            ConfigCache().warm(company_id, config.model_dump(mode="json"))
        except Exception as exc:
            log.debug("Cache warming failed (non-fatal): %s", exc)

        # Register with freshness monitor — pass view count for schema change detection
        try:
            from pipeline.freshness_monitor import register as fm_register
            wb_updated_at = wb_meta.get("updated_at") if wb_meta else None
            fm_register(
                company_id=company_id,
                workbook_content_url=workbook_content_url,
                initial_updated_at=wb_updated_at,
            )
        except Exception as exc:
            log.debug("Freshness monitor registration failed (non-fatal): %s", exc)

    except Exception as exc:
        log.exception("[%s] Pipeline failed: %s", run_id, exc)
        emit(run_id, "Pipeline", f"Pipeline failed: {exc}", level="error")
        set_status(run_id, "failed", error=str(exc))
    finally:
        root_logger.removeHandler(handler)
        save_run_log(run_id)   # persist to output/logs/<run_id>.json


def _slugify(s: str) -> str:
    """Convert a workbook content URL to a safe company_id."""
    import re
    return re.sub(r"[^a-z0-9_-]", "_", s.lower()).strip("_") or "company"
