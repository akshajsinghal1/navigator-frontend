"""
storage/models.py
──────────────────
SQLAlchemy ORM models for the Navigator database.

Tables:
  companies             — one row per onboarded company/tenant
  pipeline_runs         — one row per pipeline execution
  intelligence_configs  — one row per generated config
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Company(Base):
    """One row per onboarded company/tenant."""

    __tablename__ = "companies"

    id         = Column(String(64), primary_key=True)   # slug or UUID
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # ── Tableau credentials (encrypt in production) ──────────────────────────
    tableau_server_url   = Column(String(512), nullable=False)
    tableau_site_name    = Column(String(256), nullable=False)
    tableau_pat_name     = Column(String(256), nullable=False)
    tableau_pat_secret   = Column(String(512), nullable=False)
    workbook_content_url = Column(String(256), nullable=False)

    # ── Refresh config ───────────────────────────────────────────────────────
    refresh_interval = Column(Integer, default=3600, nullable=False)
    # ^ seconds between checks — e.g. 900=15min, 3600=hourly, 86400=daily

    # ── Scheduler state ──────────────────────────────────────────────────────
    watch_active         = Column(Boolean, default=True)
    status               = Column(String(32), default="idle")
    # ^ idle | running | error

    last_checked_at      = Column(DateTime, nullable=True)
    # ^ when we last hit Tableau to check updated_at

    last_pipeline_run_at = Column(DateTime, nullable=True)
    # ^ when we last ran the full AI pipeline

    last_l1_refresh_at   = Column(DateTime, nullable=True)
    # ^ when we last did a data-only refresh

    # ── Change detection ─────────────────────────────────────────────────────
    tableau_updated_at   = Column(String(64), nullable=True)
    # ^ last known workbook.updated_at string from Tableau

    inventory_hash       = Column(String(64), nullable=True)
    # ^ MD5 of filtered inventory — detects schema vs data-only change

    # ── Relations ────────────────────────────────────────────────────────────
    pipeline_runs        = relationship("PipelineRun",            back_populates="company", lazy="dynamic")
    intelligence_configs = relationship("IntelligenceConfigRecord", back_populates="company", lazy="dynamic")

    def tableau_creds(self) -> dict[str, str]:
        return {
            "tableau_server_url": self.tableau_server_url,
            "tableau_site_name":  self.tableau_site_name,
            "tableau_pat_name":   self.tableau_pat_name,
            "tableau_pat_secret": self.tableau_pat_secret,
        }


class PipelineRun(Base):
    """One row per pipeline execution."""

    __tablename__ = "pipeline_runs"

    id           = Column(String(64), primary_key=True)   # UUID
    company_id   = Column(String(64), ForeignKey("companies.id"), nullable=False)
    created_at   = Column(DateTime,   server_default=func.now())
    completed_at = Column(DateTime,   nullable=True)

    status       = Column(String(32), nullable=False, default="queued")
    # ^ queued | running | completed | failed

    run_type     = Column(String(32), default="full")
    # ^ full | l1_refresh

    stage        = Column(String(64), nullable=True)    # current stage name
    progress_pct = Column(Integer,    default=0)
    error        = Column(Text,       nullable=True)

    trigger      = Column(String(32), default="manual")
    # ^ manual | scheduler_full | scheduler_l1 | onboard

    company = relationship("Company", back_populates="pipeline_runs")


class IntelligenceConfigRecord(Base):
    """Stores the latest Intelligence Config for each company."""

    __tablename__ = "intelligence_configs"

    id         = Column(Integer,    primary_key=True, autoincrement=True)
    company_id = Column(String(64), ForeignKey("companies.id"), nullable=False)
    run_id     = Column(String(64), ForeignKey("pipeline_runs.id"), nullable=True)
    created_at = Column(DateTime,   server_default=func.now())
    version    = Column(String(16), default="1.0")

    config_json = Column(JSON, nullable=False)   # full IntelligenceConfig dict

    company = relationship("Company", back_populates="intelligence_configs")
