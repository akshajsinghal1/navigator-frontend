"""
pipeline/freshness_monitor.py
──────────────────────────────
Singleton background thread that polls Tableau's workbook updated_at
every 15 minutes per registered company.

On change detected:
  - Data-only change → L1 refresh + clear view cache (fast, no AI)
  - Schema change → TODO (log warning, skip for now — user must re-run pipeline)

Thread-safe. All state access is protected by _LOCK.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
POLL_INTERVAL_SEC = 15 * 60   # 15 minutes

_LOCK = threading.Lock()
_REGISTRY: dict[str, dict] = {}   # company_id → entry dict
_RUNNING = False
_THREAD: threading.Thread | None = None


# ── Public API ─────────────────────────────────────────────────────────────────

def register(
    company_id: str,
    workbook_content_url: str,
    creds: dict,
    initial_updated_at: str | None = None,
) -> None:
    """Register a company for freshness monitoring. Safe to call multiple times."""
    with _LOCK:
        existing = _REGISTRY.get(company_id, {})
        _REGISTRY[company_id] = {
            "company_id":           company_id,
            "workbook_content_url": workbook_content_url,
            "creds":                creds,
            "data_version":         existing.get("data_version", 1),
            "last_refreshed_at":    existing.get("last_refreshed_at", _now()),
            "last_updated_at":      existing.get("last_updated_at", initial_updated_at),
            "status":               "fresh",   # fresh | refreshing
        }
    log.info("Freshness monitor: registered '%s' (workbook=%s)", company_id, workbook_content_url)


def get_freshness(company_id: str) -> dict | None:
    """Return freshness info for a company, or None if not registered."""
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
    """Start the background polling thread (idempotent)."""
    global _RUNNING, _THREAD
    with _LOCK:
        if _RUNNING:
            return
        _RUNNING = True
    _THREAD = threading.Thread(target=_poll_loop, name="freshness-monitor", daemon=True)
    _THREAD.start()
    log.info("Freshness monitor started (interval=%ds)", POLL_INTERVAL_SEC)


def stop() -> None:
    global _RUNNING
    _RUNNING = False


# ── Background loop ────────────────────────────────────────────────────────────

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
    from tableau.vds import VdsClient

    company_id           = entry["company_id"]
    workbook_content_url = entry["workbook_content_url"]
    creds                = entry["creds"]
    last_updated_at      = entry.get("last_updated_at")

    with VdsClient.from_dict(creds) as conn:
        wb = conn.get_workbook_by_content_url(workbook_content_url)

    current_updated_at = wb.get("updated_at") or wb.get("updatedAt")

    if current_updated_at and current_updated_at == last_updated_at:
        log.debug("Freshness: '%s' unchanged", company_id)
        return

    log.info(
        "Freshness: '%s' changed — updated_at: %s → %s",
        company_id, last_updated_at, current_updated_at,
    )

    # Trigger L1 refresh in a separate thread (non-blocking)
    threading.Thread(
        target=_do_l1_refresh,
        args=(entry, current_updated_at),
        daemon=True,
        name=f"l1-refresh-{company_id}",
    ).start()


def _do_l1_refresh(entry: dict, new_updated_at: str | None) -> None:
    from pipeline.l1_refresher import refresh_l1
    from tableau.vds import VdsClient
    from api.routes.viewdata import clear_view_cache

    company_id           = entry["company_id"]
    workbook_content_url = entry["workbook_content_url"]
    creds                = entry["creds"]

    # Mark as refreshing
    with _LOCK:
        if company_id in _REGISTRY:
            _REGISTRY[company_id]["status"] = "refreshing"

    try:
        config_dict = _load_config(company_id)
        if not config_dict:
            log.warning("Freshness: no config for '%s' — skipping L1 refresh", company_id)
            return

        with VdsClient.from_dict(creds) as conn:
            wb            = conn.get_workbook_by_content_url(workbook_content_url)
            workbook_luid = wb["luid"]
            updated_config = refresh_l1(config_dict, conn, workbook_luid)

        # Save updated config
        _save_config(company_id, workbook_content_url, updated_config)

        # Clear view cache so charts fetch fresh rows
        clear_view_cache(workbook_content_url)

        # Update registry
        with _LOCK:
            if company_id in _REGISTRY:
                entry = _REGISTRY[company_id]
                entry["data_version"]      += 1
                entry["last_refreshed_at"] = _now()
                entry["last_updated_at"]   = new_updated_at
                entry["status"]            = "fresh"
                dv = entry["data_version"]

        log.info("Freshness: '%s' L1 refresh complete → v%d", company_id, dv)

    except Exception as exc:
        log.error("Freshness: L1 refresh failed for '%s': %s", company_id, exc)
        with _LOCK:
            if company_id in _REGISTRY:
                _REGISTRY[company_id]["status"] = "fresh"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_config(company_id: str) -> dict | None:
    """Load latest config from DB or output files."""
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
        key        = company_id.lower()
        candidates = sorted(
            [p for p in output_dir.glob("intelligence_config_*.json") if key in p.name.lower()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            try:
                return json.loads(candidates[0].read_text(encoding="utf-8"))
            except Exception:
                pass
    return None


def _save_config(company_id: str, workbook_content_url: str, config_dict: dict) -> None:
    """Persist refreshed config to DB and output file."""
    import json

    # DB
    try:
        from storage.db import get_session, ConfigRepo
        with get_session() as session:
            # Use a synthetic run_id for the refresh
            run_id = f"refresh-{int(time.time())}"
            ConfigRepo.upsert(session, company_id, run_id, config_dict=config_dict)
    except Exception as exc:
        log.debug("DB save skipped: %s", exc)

    # File
    try:
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)
        ts       = datetime.now(IST).strftime("%Y%m%dT%H%M%SZ")
        filename = f"intelligence_config_{workbook_content_url}_{ts}.json"
        (output_dir / filename).write_text(json.dumps(config_dict, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("Freshness: saved refreshed config → %s", filename)
    except Exception as exc:
        log.warning("Freshness: could not save config file: %s", exc)


def _now() -> str:
    return datetime.now(IST).strftime("%H:%M:%S")
