"""
api/routes/pipeline.py
───────────────────────
GET /pipeline/{run_id}/status — poll a pipeline run's progress.

Reads from the in-memory store first (always works, even without a DB).
Falls back to PostgreSQL for runs from previous process restarts.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{run_id}/status")
def get_pipeline_status(run_id: str):
    """
    Return the full status of a pipeline run including agent log messages.
    Poll this every 2–3 seconds after POST /onboard.
    When status == "completed", the dashboard is ready at GET /dashboard/{company_id}.
    """
    # ── In-memory first (no DB required) ──────────────────────────────────────
    from api.pipeline_status import get_run, to_dict
    run = get_run(run_id)
    if run is not None:
        return to_dict(run)

    # ── DB fallback (for runs from a previous process) ─────────────────────────
    try:
        from storage.db import get_session, PipelineRunRepo
        with get_session() as session:
            db_run = PipelineRunRepo.get(session, run_id)
        if db_run:
            return {
                "run_id":       db_run.id,
                "company_id":   db_run.company_id,
                "workbook":     "",
                "status":       db_run.status,
                "stage":        db_run.stage or "",
                "progress_pct": db_run.progress_pct or 0,
                "error":        db_run.error,
                "started_at":   None,
                "completed_at": str(db_run.completed_at) if db_run.completed_at else None,
                "messages":     [],
            }
    except Exception as exc:
        log.debug("DB status lookup skipped: %s", exc)

    raise HTTPException(status_code=404, detail=f"Pipeline run '{run_id}' not found")
