"""
scheduler/watcher.py
─────────────────────
Background scheduler — checks every company on its own refresh_interval
and decides what to do:

  workbook.updated_at unchanged  →  skip (serve cache)
  updated_at changed, hash same  →  L1 refresh (~30s, free)
  updated_at changed, hash diff  →  full AI pipeline (~8min, Gemini cost)

Run standalone:
    python -m scheduler.watcher

Or embed in FastAPI:
    from scheduler.watcher import Watcher
    Watcher().start_thread()   # background daemon thread
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# How often the main loop wakes up to check who is due
_TICK_SECONDS = 60


class Watcher:
    """
    Scheduler that polls all active companies and triggers the right
    pipeline action based on what changed in their Tableau workbook.
    """

    def start(self) -> None:
        """Run forever (blocking). Call from a dedicated process/thread."""
        log.info("Scheduler started — tick every %ds", _TICK_SECONDS)
        while True:
            try:
                self._tick()
            except Exception as exc:
                log.error("Scheduler tick error: %s", exc, exc_info=True)
            time.sleep(_TICK_SECONDS)

    def start_thread(self) -> threading.Thread:
        """Start the scheduler in a background daemon thread."""
        t = threading.Thread(target=self.start, daemon=True, name="navigator-scheduler")
        t.start()
        log.info("Scheduler thread started")
        return t

    # ── Main tick ─────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        from storage.db import get_session, CompanyRepo

        with get_session() as session:
            companies = CompanyRepo.list_active(session)

        log.debug("Tick: %d active companies", len(companies))

        for company in companies:
            try:
                self._check_company(company)
            except Exception as exc:
                log.error("Error checking company '%s': %s", company.id, exc, exc_info=True)

    # ── Per-company check ─────────────────────────────────────────────────────

    def _check_company(self, company) -> None:
        from storage.db import get_session, CompanyRepo

        now = datetime.now(timezone.utc)

        # Not due yet?
        if company.last_checked_at:
            last = company.last_checked_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = (now - last).total_seconds()
            if elapsed < company.refresh_interval:
                return

        # Already running?
        if company.status == "running":
            log.info("Company '%s' still running — skipping tick", company.id)
            return

        log.info(
            "Checking company '%s' (interval=%ds, workbook=%s)",
            company.id, company.refresh_interval, company.workbook_content_url,
        )

        # ── Step 1: fetch workbook.updated_at from Tableau ───────────────────
        try:
            from tableau.connector import TableauConnector
            with TableauConnector.from_dict(company.tableau_creds()) as conn:
                wb_meta = conn.get_workbook_by_content_url(company.workbook_content_url)
        except Exception as exc:
            log.error("Tableau unreachable for '%s': %s", company.id, exc)
            with get_session() as s:
                CompanyRepo.update_after_check(
                    s, company.id,
                    tableau_updated_at=company.tableau_updated_at or "",
                )
            return

        new_tableau_ts = str(wb_meta.get("updated_at", ""))

        # ── Step 2: anything changed in Tableau? ─────────────────────────────
        if new_tableau_ts == company.tableau_updated_at:
            log.info("Company '%s' — no Tableau update, skipping", company.id)
            with get_session() as s:
                CompanyRepo.update_after_check(s, company.id, tableau_updated_at=new_tableau_ts)
            return

        log.info(
            "Company '%s' — Tableau updated (%s -> %s), checking schema hash",
            company.id, company.tableau_updated_at, new_tableau_ts,
        )

        # ── Step 3: extract inventory + compute hash (no AI) ─────────────────
        try:
            from tableau_inventory_extractor import TableauInventoryExtractor
            from tableau.semantic_filter import filter_inventory
            from storage.db import compute_inventory_hash

            extractor = TableauInventoryExtractor(
                server_url = company.tableau_server_url,
                site_name  = company.tableau_site_name,
                pat_name   = company.tableau_pat_name,
                pat_secret = company.tableau_pat_secret,
            )
            with extractor as ex:
                raw_inventory = ex.extract_workbook_inventory(company.workbook_content_url)

            filtered = filter_inventory(raw_inventory)
            new_hash = compute_inventory_hash(filtered)

        except Exception as exc:
            log.error("Inventory extraction failed for '%s': %s", company.id, exc)
            return

        # ── Step 4: schema change or data-only? ──────────────────────────────
        schema_changed = (new_hash != company.inventory_hash)

        if schema_changed:
            log.info(
                "Company '%s' — SCHEMA CHANGED (hash %s -> %s) → full AI pipeline",
                company.id, company.inventory_hash, new_hash,
            )
            self._run_full_pipeline(company, filtered, new_tableau_ts, new_hash)
        else:
            log.info(
                "Company '%s' — DATA ONLY (hash unchanged) → L1 refresh",
                company.id,
            )
            self._run_l1_refresh(company, new_tableau_ts, new_hash)

    # ── Full AI pipeline ──────────────────────────────────────────────────────

    def _run_full_pipeline(
        self,
        company,
        filtered_inventory: dict,
        new_tableau_ts: str,
        new_hash: str,
    ) -> None:
        from storage.db import get_session, CompanyRepo, PipelineRunRepo, ConfigRepo
        from pipeline.runner import PipelineRunner

        with get_session() as s:
            CompanyRepo.mark_running(s, company.id)
            run = PipelineRunRepo.create(s, company.id, trigger="scheduler_full", run_type="full")
            run_id = run.id

        try:
            runner = PipelineRunner(company.tableau_creds())
            config = runner.run(company.workbook_content_url)

            with get_session() as s:
                ConfigRepo.save(s, company.id, config.model_dump(), run_id=run_id)
                PipelineRunRepo.update_status(s, run_id, "completed", progress_pct=100)
                CompanyRepo.update_after_check(
                    s, company.id,
                    tableau_updated_at   = new_tableau_ts,
                    inventory_hash       = new_hash,
                    last_pipeline_run_at = datetime.now(timezone.utc),
                )
                CompanyRepo.mark_idle(s, company.id)

            log.info("Full pipeline complete for '%s'", company.id)

        except Exception as exc:
            log.error("Full pipeline failed for '%s': %s", company.id, exc, exc_info=True)
            with get_session() as s:
                PipelineRunRepo.update_status(s, run_id, "failed", error=str(exc))
                CompanyRepo.mark_error(s, company.id)

    # ── L1 data-only refresh ──────────────────────────────────────────────────

    def _run_l1_refresh(
        self,
        company,
        new_tableau_ts: str,
        new_hash: str,
    ) -> None:
        from storage.db import get_session, CompanyRepo, PipelineRunRepo, ConfigRepo
        from pipeline.l1_refresher import refresh_l1
        from tableau.connector import TableauConnector

        with get_session() as s:
            CompanyRepo.mark_running(s, company.id)
            run = PipelineRunRepo.create(s, company.id, trigger="scheduler_l1", run_type="l1_refresh")
            run_id = run.id
            record = ConfigRepo.get_latest(s, company.id)
            if not record:
                log.warning("No existing config for '%s' — falling back to full pipeline", company.id)
                CompanyRepo.mark_idle(s, company.id)
                # No config exists yet — treat as schema change and run full pipeline
                self._run_full_pipeline(company, {}, new_tableau_ts, new_hash)
                return
            config_dict = record.config_json

        try:
            with TableauConnector.from_dict(company.tableau_creds()) as conn:
                wb_meta       = conn.get_workbook_by_content_url(company.workbook_content_url)
                workbook_luid = wb_meta["luid"]
                updated       = refresh_l1(config_dict, conn, workbook_luid)

            with get_session() as s:
                ConfigRepo.save(s, company.id, updated, run_id=run_id)
                PipelineRunRepo.update_status(s, run_id, "completed", progress_pct=100)
                CompanyRepo.update_after_check(
                    s, company.id,
                    tableau_updated_at  = new_tableau_ts,
                    inventory_hash      = new_hash,
                    last_l1_refresh_at  = datetime.now(timezone.utc),
                )
                CompanyRepo.mark_idle(s, company.id)

            log.info("L1 refresh complete for '%s'", company.id)

        except Exception as exc:
            log.error("L1 refresh failed for '%s': %s", company.id, exc, exc_info=True)
            with get_session() as s:
                PipelineRunRepo.update_status(s, run_id, "failed", error=str(exc))
                CompanyRepo.mark_error(s, company.id)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(
        level  = logging.INFO,
        format = "%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from storage.db import create_all_tables
    create_all_tables()

    Watcher().start()
