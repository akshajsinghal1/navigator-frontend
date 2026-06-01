"""
api/routes/freshness.py
────────────────────────
GET /api/freshness/{company_id}

Returns a tiny payload the frontend polls to detect data updates.
If data_version has changed since the frontend last checked, it
silently re-fetches the full Intelligence Config and re-renders.
"""

from __future__ import annotations
from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/{company_id}")
def get_freshness(company_id: str):
    from pipeline.freshness_monitor import get_freshness
    info = get_freshness(company_id)
    if info is None:
        # Company not yet registered — return a default (pipeline may not have run yet)
        return {"data_version": 1, "last_refreshed_at": None, "status": "unknown"}
    return info
