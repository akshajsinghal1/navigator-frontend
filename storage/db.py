"""
storage/db.py
─────────────
Database engine, session management, and repository helpers.

Supports:
  SQLite  — DATABASE_URL=sqlite:///navigator.db   (dev, zero setup)
  PostgreSQL — DATABASE_URL=postgresql://...      (production)

Usage:
    from storage.db import get_session, CompanyRepo, ConfigRepo

    with get_session() as session:
        company = CompanyRepo.get(session, "acme")
        config  = ConfigRepo.get_latest(session, "acme")
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

from sqlalchemy import create_engine, desc, select
from sqlalchemy.orm import Session, sessionmaker

from storage.models import Base, Company, IntelligenceConfigRecord, PipelineRun

log = logging.getLogger(__name__)

# ── Engine ────────────────────────────────────────────────────────────────────

def _get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        # Default to SQLite in the project root for development
        db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "navigator.db")
        url = f"sqlite:///{db_path}"
        log.info("DATABASE_URL not set — using SQLite at %s", db_path)
    return url


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        url = _get_database_url()
        kwargs: dict[str, Any] = {}
        if url.startswith("sqlite"):
            # SQLite needs check_same_thread=False for use across threads
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs["pool_size"]    = 5
            kwargs["max_overflow"] = 10
            kwargs["pool_pre_ping"]= True
        _engine = create_engine(url, **kwargs)
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Provide a transactional database session."""
    SessionLocal = get_session_factory()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all_tables() -> None:
    """Create all tables (idempotent — safe to call on every startup)."""
    Base.metadata.create_all(get_engine())
    log.info("Database tables ensured")


# ── Inventory hash ────────────────────────────────────────────────────────────

def compute_inventory_hash(filtered_inventory: dict[str, Any]) -> str:
    """
    MD5 of the filtered inventory — used to detect schema changes.

    Only structure matters (field names, types, formulas, view names).
    Data values never appear in the inventory so new rows don't affect the hash.
    """
    canonical = json.dumps(filtered_inventory, sort_keys=True, ensure_ascii=True)
    return hashlib.md5(canonical.encode()).hexdigest()


# ── Company repository ────────────────────────────────────────────────────────

class CompanyRepo:

    @staticmethod
    def create(
        session: Session,
        company_id: str,
        tableau_server_url: str,
        tableau_site_name: str,
        tableau_pat_name: str,
        tableau_pat_secret: str,
        workbook_content_url: str,
        refresh_interval: int = 3600,
    ) -> Company:
        company = Company(
            id                   = company_id,
            tableau_server_url   = tableau_server_url,
            tableau_site_name    = tableau_site_name,
            tableau_pat_name     = tableau_pat_name,
            tableau_pat_secret   = tableau_pat_secret,
            workbook_content_url = workbook_content_url,
            refresh_interval     = refresh_interval,
            status               = "idle",
            watch_active         = True,
        )
        session.add(company)
        session.flush()
        return company

    @staticmethod
    def get(session: Session, company_id: str) -> Company | None:
        return session.get(Company, company_id)

    @staticmethod
    def list_active(session: Session) -> list[Company]:
        """All companies with watch_active=True — iterated by the scheduler."""
        return list(session.scalars(
            select(Company).where(Company.watch_active == True)
        ))

    @staticmethod
    def mark_running(session: Session, company_id: str) -> None:
        c = session.get(Company, company_id)
        if c:
            c.status = "running"
            session.flush()

    @staticmethod
    def mark_idle(session: Session, company_id: str) -> None:
        c = session.get(Company, company_id)
        if c:
            c.status = "idle"
            session.flush()

    @staticmethod
    def mark_error(session: Session, company_id: str) -> None:
        c = session.get(Company, company_id)
        if c:
            c.status = "error"
            session.flush()

    @staticmethod
    def update_after_check(
        session: Session,
        company_id: str,
        tableau_updated_at: str,
        inventory_hash: str | None = None,
        last_pipeline_run_at: datetime | None = None,
        last_l1_refresh_at: datetime | None = None,
    ) -> None:
        """Update scheduler state after a check cycle."""
        c = session.get(Company, company_id)
        if not c:
            return
        c.last_checked_at    = datetime.now(timezone.utc)
        c.tableau_updated_at = tableau_updated_at
        if inventory_hash is not None:
            c.inventory_hash = inventory_hash
        if last_pipeline_run_at is not None:
            c.last_pipeline_run_at = last_pipeline_run_at
        if last_l1_refresh_at is not None:
            c.last_l1_refresh_at = last_l1_refresh_at
        session.flush()


# ── PipelineRun repository ────────────────────────────────────────────────────

class PipelineRunRepo:

    @staticmethod
    def create(
        session: Session,
        company_id: str,
        trigger: str = "manual",
        run_type: str = "full",
    ) -> PipelineRun:
        run = PipelineRun(
            id         = str(uuid.uuid4()),
            company_id = company_id,
            status     = "queued",
            trigger    = trigger,
            run_type   = run_type,
        )
        session.add(run)
        session.flush()
        return run

    @staticmethod
    def get(session: Session, run_id: str) -> PipelineRun | None:
        return session.get(PipelineRun, run_id)

    @staticmethod
    def update_status(
        session: Session,
        run_id: str,
        status: str,
        stage: str | None = None,
        progress_pct: int | None = None,
        error: str | None = None,
    ) -> None:
        run = session.get(PipelineRun, run_id)
        if not run:
            return
        run.status = status
        if stage        is not None: run.stage        = stage
        if progress_pct is not None: run.progress_pct = progress_pct
        if error        is not None: run.error        = error
        if status in ("completed", "failed"):
            run.completed_at = datetime.now(timezone.utc)
        session.flush()


# ── IntelligenceConfig repository ─────────────────────────────────────────────

class ConfigRepo:

    @staticmethod
    def save(
        session: Session,
        company_id: str,
        config_dict: dict[str, Any],
        run_id: str | None = None,
        version: str = "1.0",
    ) -> IntelligenceConfigRecord:
        record = IntelligenceConfigRecord(
            company_id  = company_id,
            run_id      = run_id,
            version     = version,
            config_json = config_dict,
        )
        session.add(record)
        session.flush()
        return record

    # Keep old name as alias
    upsert = save

    @staticmethod
    def get_latest(session: Session, company_id: str) -> IntelligenceConfigRecord | None:
        return session.scalars(
            select(IntelligenceConfigRecord)
            .where(IntelligenceConfigRecord.company_id == company_id)
            .order_by(desc(IntelligenceConfigRecord.created_at))
            .limit(1)
        ).first()
