"""
api/routes/dashboard.py
────────────────────────
GET /dashboard/{company_id} — return the Intelligence Config for a company.

Cache layer: Redis (15-min TTL) → PostgreSQL → 404 if not found.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from schemas.api import DashboardConfigResponse

log = logging.getLogger(__name__)
router = APIRouter()


def _load_config_from_file(company_id: str) -> dict | None:
    import json
    from api.config_files import resolve_intelligence_config_path

    output_dir = Path("output")
    if not output_dir.exists():
        return None
    latest = resolve_intelligence_config_path(output_dir, company_id)
    if not latest:
        return None
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Could not read config file %s: %s", latest, exc)
        return None


@router.get("/{company_id}", response_model=DashboardConfigResponse)
def get_dashboard_config(company_id: str):
    """
    Return the latest Intelligence Config for a company.

    - Checks Redis cache first (fast path, 15-min TTL)
    - Falls back to PostgreSQL if cache miss
    - Returns 404 if no config exists yet (pipeline not run)
    """
    from api.config_files import is_demo_workbook_key

    # Pinned demo snapshot — always read from disk (skip Redis/DB stale cache).
    if is_demo_workbook_key(company_id):
        config_dict = _load_config_from_file(company_id)
        if config_dict is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No demo Intelligence Config found for '{company_id}'. "
                    "Run: python scripts/build_demo_snapshot.py"
                ),
            )
        return DashboardConfigResponse(
            company_id     = company_id,
            config_version = config_dict.get("version", "1.0"),
            generated_at   = config_dict.get("generated_at", ""),
            cached         = False,
            config         = config_dict,
        )

    # ── cache check ────────────────────────────────────────────────────────────
    cached = False
    config_dict = None

    try:
        from storage.cache import ConfigCache
        cache = ConfigCache()
        config_dict = cache.get(company_id)
        if config_dict:
            cached = True
            log.debug("Cache hit for company %s", company_id)
    except Exception as exc:
        log.warning("Cache read error: %s", exc)

    # ── DB fallback ───────────────────────────────────────────────────────────
    if config_dict is None:
        try:
            from storage.db import get_session, ConfigRepo
            with get_session() as session:
                record = ConfigRepo.get_latest(session, company_id)
                if record:
                    config_dict = record.config_json

                    # Warm the cache
                    try:
                        from storage.cache import ConfigCache
                        ConfigCache().set(company_id, config_dict)
                    except Exception:
                        pass
        except Exception as exc:
            log.error("DB read error for company %s: %s", company_id, exc)
            raise HTTPException(status_code=503, detail="Database temporarily unavailable")

    # ── File fallback (development / no-DB mode) ─────────────────────────────
    if config_dict is None:
        config_dict = _load_config_from_file(company_id)
        if config_dict:
            log.info("Loaded Intelligence Config from file for %s", company_id)

    if config_dict is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No Intelligence Config found for '{company_id}'. "
                "Run the pipeline first: python run_pipeline.py --workbook {company_id}"
            ),
        )

    return DashboardConfigResponse(
        company_id      = company_id,
        config_version  = config_dict.get("version", "1.0"),
        generated_at    = config_dict.get("generated_at", ""),
        cached          = cached,
        config          = config_dict,
    )
