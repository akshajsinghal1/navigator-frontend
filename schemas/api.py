"""
schemas/api.py
──────────────
Pydantic models for the FastAPI request/response layer.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ── Onboarding ───────────────────────────────────────────────────────────────

class OrgPersonaInput(BaseModel):
    """Persona declared at org onboarding — pipeline must not invent others."""
    id: str = Field(..., description="UUID from public.personas")
    name: str = Field(..., description="Display name, e.g. CFO")


class OnboardRequest(BaseModel):
    tableau_server_url: str = Field(..., description="e.g. https://us-east-1.online.tableau.com")
    tableau_site_name:  str = Field(..., description="e.g. navigatorpilot")
    tableau_pat_name:   str = Field(..., description="Personal Access Token name")
    tableau_pat_secret: str = Field(..., description="Personal Access Token secret")
    workbook_content_url: str = Field(..., description="Content URL of the target workbook")
    company_id: str = Field(..., description="Unique identifier for this company/tenant")
    organization_id: Optional[str] = Field(None, description="Customer org UUID (nav-rbac)")
    industry_name: Optional[str] = Field(None, description="Org industry from onboarding")
    required_personas: list[OrgPersonaInput] = Field(
        default_factory=list,
        description="Exact personas from org onboarding — agent must not add/rename",
    )


class OnboardResponse(BaseModel):
    company_id: str
    run_id: str
    status: Literal["queued", "running"] = "queued"
    message: str = "Pipeline started. Poll /pipeline/{run_id}/status for progress."


# ── Pipeline status ──────────────────────────────────────────────────────────

class PipelineStatusResponse(BaseModel):
    run_id: str
    company_id: str
    status: Literal["queued", "running", "completed", "failed"]
    stage: Optional[str] = None       # e.g. "domain_analysis", "chart_generation"
    progress_pct: int = 0
    error: Optional[str] = None
    completed_at: Optional[str] = None


# ── Dashboard config ─────────────────────────────────────────────────────────

class DashboardConfigResponse(BaseModel):
    company_id: str
    config_version: str
    generated_at: str
    cached: bool = False
    config: dict   # the full IntelligenceConfig as a plain dict


# ── Health ───────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    db: bool = True
    cache: bool = True
    tableau_reachable: bool = True
