"""
pipeline/freshness_monitor.py
──────────────────────────────
Background thread that polls Tableau every POLL_INTERVAL_SEC to detect
workbook changes and trigger appropriate refresh actions.

Two types of change:
  DATA change   — new rows, updated values (updated_at changes)
                  → clear view cache so charts fetch fresh data
                  → bump data_version so frontend knows to re-fetch config
                  → invalidate Redis cache

  SCHEMA change — new views/fields added or removed (view count changes)
                  → trigger full re-pipeline in background
                  → (takes several minutes but user sees fresh dashboard after)

State is persisted in DB so monitor survives server restarts.
Credentials are NOT stored — uses the shared VDS session from viewdata.py
(same PAT environment credentials for all workbooks on one Tableau instance).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

IST              = timezone(timedelta(hours=5, minutes=30))
POLL_INTERVAL_SEC = 15 * 60   # 15 minutes

_LOCK    = threading.Lock()
_REGISTRY: dict[str, dict] = {}   # company_id → entry
_RUNNING = False
_THREAD: threading.Thread | None = None


# ── Public API ─────────────────────────────────────────────────────────────────

def register(
    company_id: str,
    workbook_content_url: str,
    creds: dict | None = None,              # kept for backward compat, no longer stored
    initial_updated_at: str | None = None,
    initial_view_count: int | None = None,
) -> None:
    """Register a workbook for freshness monitoring. Idempotent."""
    with _LOCK:
        existing = _REGISTRY.get(company_id, {})
        _REGISTRY[company_id] = {
            "company_id":           company_id,
            "workbook_content_url": workbook_content_url,
            "data_version":         existing.get("data_version", 1),
            "last_refreshed_at":    existing.get("last_refreshed_at", _now()),
            "last_updated_at":      existing.get("last_updated_at", initial_updated_at),
            "last_view_count":      existing.get("last_view_count", initial_view_count),
            "status":               "fresh",
        }
    log.info("Freshness monitor: registered '%s' (workbook=%s)", company_id, workbook_content_url)
    # Persist to DB so we survive restarts
    _persist_entry(company_id)


def get_freshness(company_id: str) -> dict | None:
    """Return freshness info for a company, or None if not registered."""
    with _LOCK:
        entry = _REGISTRY.get(company_id)
    if not entry:
        # Try restoring from DB on first request
        _restore_from_db(company_id)
        with _LOCK:
            entry = _REGISTRY.get(company_id)
    if not entry:
        return None
    return {
        "data_version":      entry["data_version"],
        "last_refreshed_at": entry["last_refreshed_at"],
        "status":            entry["status"],
    }


def start() -> None:
    """Start the background polling thread (idempotent). Restores DB state."""
    global _RUNNING, _THREAD
    with _LOCK:
        if _RUNNING:
            return
        _RUNNING = True

    # Restore any previously registered companies from DB
    _restore_all_from_db()

    _THREAD = threading.Thread(target=_poll_loop, name="freshness-monitor", daemon=True)
    _THREAD.start()
    log.info("Freshness monitor started (interval=%ds)", POLL_INTERVAL_SEC)


def stop() -> None:
    global _RUNNING
    _RUNNING = False


# ── Background polling ─────────────────────────────────────────────────────────

def _poll_loop() -> None:
    while _RUNNING:
        time.sleep(POLL_INTERVAL_SEC)
        if not _RUNNING:
            break
        with _LOCK:
            companies = list(_REGISTRY.values())
        for entry in companies:
            try:
                _check_company(dict(entry))
            except Exception as exc:
                log.warning("Freshness check failed for '%s': %s", entry["company_id"], exc)


def _check_company(entry: dict) -> None:
    """
    Check if a workbook has changed. Uses the shared VDS session from viewdata.py
    (no credentials stored — relies on environment PAT).
    """
    from api.routes.viewdata import _get_conn

    company_id           = entry["company_id"]
    workbook_content_url = entry["workbook_content_url"]
    last_updated_at      = entry.get("last_updated_at")
    last_view_count      = entry.get("last_view_count")

    try:
        conn = _get_conn()
        wb   = conn.get_workbook_by_content_url(workbook_content_url)
    except Exception as exc:
        log.warning("Freshness: could not fetch workbook '%s': %s", company_id, exc)
        return

    if not wb:
        return

    current_updated_at = wb.get("updated_at") or wb.get("updatedAt")

    # ── Check for schema change ────────────────────────────────────────────
    # Schema change: view count changed (new dashboards/sheets added or removed)
    try:
        views      = conn.list_views(wb["luid"])
        view_count = len(views) if views else None
    except Exception:
        view_count = None

    schema_changed = (
        last_view_count is not None
        and view_count is not None
        and view_count != last_view_count
    )

    # ── Check for data change ──────────────────────────────────────────────
    data_changed = (
        current_updated_at
        and current_updated_at != last_updated_at
    )

    if not data_changed and not schema_changed:
        log.debug("Freshness: '%s' unchanged", company_id)
        return

    if schema_changed:
        log.info(
            "Freshness: '%s' SCHEMA CHANGED — views: %s → %s. Triggering re-pipeline.",
            company_id, last_view_count, view_count,
        )
        # Update view count immediately to prevent repeated triggers
        with _LOCK:
            if company_id in _REGISTRY:
                _REGISTRY[company_id]["last_view_count"] = view_count
        threading.Thread(
            target=_do_repipeline,
            args=(entry, current_updated_at, view_count),
            daemon=True,
            name=f"repipeline-{company_id}",
        ).start()

    elif data_changed:
        log.info(
            "Freshness: '%s' DATA changed — updated_at: %s → %s",
            company_id, last_updated_at, current_updated_at,
        )
        threading.Thread(
            target=_do_data_refresh,
            args=(entry, current_updated_at, view_count),
            daemon=True,
            name=f"data-refresh-{company_id}",
        ).start()


def _do_data_refresh(entry: dict, new_updated_at: str | None, new_view_count: int | None) -> None:
    """
    Data-only refresh: clear view cache + bump data_version + invalidate Redis.
    Fast — no AI agents, just cache invalidation.
    """
    from api.routes.viewdata import clear_view_cache
    from storage.cache import ConfigCache

    company_id           = entry["company_id"]
    workbook_content_url = entry["workbook_content_url"]

    with _LOCK:
        if company_id in _REGISTRY:
            _REGISTRY[company_id]["status"] = "refreshing"

    try:
        # 1. Clear view cache so next chart fetch gets fresh rows from Tableau
        clear_view_cache(workbook_content_url)
        log.info("Freshness: view cache cleared for '%s'", company_id)

        # 2. Invalidate Redis so next dashboard load reads from file/DB
        ConfigCache().invalidate(company_id)
        log.info("Freshness: Redis cache invalidated for '%s'", company_id)

        # 3. Bump version so frontend knows to re-fetch
        with _LOCK:
            if company_id in _REGISTRY:
                _REGISTRY[company_id]["data_version"]      += 1
                _REGISTRY[company_id]["last_refreshed_at"] = _now()
                _REGISTRY[company_id]["last_updated_at"]   = new_updated_at
                if new_view_count:
                    _REGISTRY[company_id]["last_view_count"] = new_view_count
                _REGISTRY[company_id]["status"]            = "fresh"
                dv = _REGISTRY[company_id]["data_version"]

        _persist_entry(company_id)
        log.info("Freshness: '%s' data refresh complete → v%d", company_id, dv)

    except Exception as exc:
        log.error("Freshness: data refresh failed for '%s': %s", company_id, exc)
        with _LOCK:
            if company_id in _REGISTRY:
                _REGISTRY[company_id]["status"] = "fresh"


def _do_repipeline(entry: dict, new_updated_at: str | None, new_view_count: int | None) -> None:
    """
    Schema change: trigger full re-pipeline. Expensive but necessary.
    Runs in background — dashboard shows stale data until complete.
    """
    from api.routes.viewdata import clear_view_cache
    from storage.cache import ConfigCache
    from storage.db import get_session, ConfigRepo

    company_id           = entry["company_id"]
    workbook_content_url = entry["workbook_content_url"]

    with _LOCK:
        if company_id in _REGISTRY:
            _REGISTRY[company_id]["status"] = "refreshing"

    try:
        # Get credentials from DB (stored during initial onboard)
        creds = _load_creds(company_id)
        if not creds:
            log.warning(
                "Freshness: no credentials found for '%s' — cannot re-pipeline. "
                "User must manually re-onboard.",
                company_id,
            )
            with _LOCK:
                if company_id in _REGISTRY:
                    _REGISTRY[company_id]["status"] = "fresh"
            return

        from pipeline.runner import PipelineRunner
        runner = PipelineRunner(creds)
        config = runner.run(workbook_content_url)

        # Save new config
        try:
            with get_session() as session:
                ConfigRepo.upsert(session, company_id, f"schema-refresh-{int(time.time())}", config_dict=config.model_dump(mode="json"))
        except Exception:
            pass

        # Clear caches
        clear_view_cache(workbook_content_url)
        ConfigCache().invalidate(company_id)

        with _LOCK:
            if company_id in _REGISTRY:
                _REGISTRY[company_id]["data_version"]      += 1
                _REGISTRY[company_id]["last_refreshed_at"] = _now()
                _REGISTRY[company_id]["last_updated_at"]   = new_updated_at
                _REGISTRY[company_id]["last_view_count"]   = new_view_count
                _REGISTRY[company_id]["status"]            = "fresh"
                dv = _REGISTRY[company_id]["data_version"]

        _persist_entry(company_id)
        log.info("Freshness: '%s' re-pipeline complete → v%d", company_id, dv)

    except Exception as exc:
        log.error("Freshness: re-pipeline failed for '%s': %s", company_id, exc)
        with _LOCK:
            if company_id in _REGISTRY:
                _REGISTRY[company_id]["status"] = "fresh"


# ── DB persistence ─────────────────────────────────────────────────────────────

def _persist_entry(company_id: str) -> None:
    """Save current registry entry to DB for restart recovery."""
    try:
        import json
        from storage.db import get_session, MonitorRepo
        with _LOCK:
            entry = _REGISTRY.get(company_id)
        if not entry:
            return
        with get_session() as session:
            MonitorRepo.upsert(session, company_id, json.dumps({
                k: v for k, v in entry.items()
                if k != "creds"   # never persist credentials
            }))
    except Exception as exc:
        log.debug("Could not persist freshness entry: %s", exc)


def _restore_from_db(company_id: str) -> None:
    """Try to restore a single company's entry from DB."""
    try:
        import json
        from storage.db import get_session, MonitorRepo
        with get_session() as session:
            row = MonitorRepo.get(session, company_id)
        if row:
            entry = json.loads(row)
            with _LOCK:
                if company_id not in _REGISTRY:
                    _REGISTRY[company_id] = entry
    except Exception:
        pass


def _restore_all_from_db() -> None:
    """Restore all monitored companies from DB on startup."""
    try:
        import json
        from storage.db import get_session, MonitorRepo
        with get_session() as session:
            rows = MonitorRepo.get_all(session)
        for company_id, data in rows.items():
            with _LOCK:
                if company_id not in _REGISTRY:
                    _REGISTRY[company_id] = json.loads(data)
            log.info("Freshness: restored '%s' from DB", company_id)
    except Exception as exc:
        log.debug("Could not restore freshness state from DB: %s", exc)


def _load_creds(company_id: str) -> dict | None:
    """Load credentials from DB (stored during onboard). Returns None if not found."""
    try:
        from storage.db import get_session, OnboardRepo
        with get_session() as session:
            return OnboardRepo.get_creds(session, company_id)
    except Exception:
        return None


def _load_config(company_id: str) -> dict | None:
    try:
        from storage.db import get_session, ConfigRepo
        with get_session() as session:
            record = ConfigRepo.get_latest(session, company_id)
            if record:
                return record.config_json
    except Exception:
        pass
    import json
    output_dir = Path("output")
    if output_dir.exists():
        key = company_id.lower()
        candidates = sorted(
            [p for p in output_dir.glob("intelligence_config_*.json") if key in p.name.lower()],
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if candidates:
            try:
                return json.loads(candidates[0].read_text(encoding="utf-8"))
            except Exception:
                pass
    return None


def _now() -> str:
    return datetime.now(IST).strftime("%H:%M:%S")
