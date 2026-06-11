"""
api/routes/config.py
─────────────────────
GET /config/latest/{workbook_name}  — serve the latest generated config JSON.

Dev/demo mode: reads directly from the output/ directory.
Production: swap for DB/cache lookup (same as dashboard.py).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/latest/{workbook_name}")
def get_latest_config(workbook_name: str):
    """
    Return the most-recently-generated Intelligence Config for a workbook.

    Scans output/intelligence_config_{workbook_name}_*.json and returns the
    lexicographically-last file (ISO timestamps sort correctly).

    Args:
        workbook_name: the workbook content URL, e.g. "Superstore"

    Returns:
        Raw Intelligence Config JSON dict
    """
    from api.config_files import resolve_intelligence_config_path

    output_dir = Path("output")
    latest = resolve_intelligence_config_path(output_dir, workbook_name)

    if not latest:
        raise HTTPException(
            status_code=404,
            detail=f"No Intelligence Config found for workbook '{workbook_name}'. "
                   "Run the pipeline first: python run_pipeline.py",
        )

    log.info("Serving config from %s", latest)

    with open(latest, encoding="utf-8") as f:
        data = json.load(f)

    return JSONResponse(content=data)
