from __future__ import annotations
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, field_validator


# ── Request models ──────────────────────────────────────────────────────────

class ApproveOrganizationRequest(BaseModel):
    note: Optional[str] = None


class RejectOrganizationRequest(BaseModel):
    rejection_reason: str

    @field_validator("rejection_reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 10:
            raise ValueError("Rejection reason must be at least 10 characters.")
        if len(v) > 1000:
            raise ValueError("Rejection reason must be at most 1000 characters.")
        return v


# ── Response models ─────────────────────────────────────────────────────────

class SystemAdminUserOut(BaseModel):
    id: str
    email: str
    name: Optional[str] = None


class SystemAdminInfoOut(BaseModel):
    role: str
    is_active: bool
    permissions: list[str]


class MeResponse(BaseModel):
    user: SystemAdminUserOut
    system_admin: SystemAdminInfoOut


class CreatedByOut(BaseModel):
    user_id: str
    name: Optional[str]
    email: str


class OrgListItem(BaseModel):
    organization_id: str
    organization_name: str
    industry_name: str
    status: str
    created_at: datetime
    created_by: CreatedByOut
    persona_count: int
    member_count: int


class Pagination(BaseModel):
    page: int
    page_size: int
    total: int


class OrgListResponse(BaseModel):
    items: list[OrgListItem]
    pagination: Pagination


class PersonaOut(BaseModel):
    id: str
    name: str
    is_active: bool
    created_at: Optional[datetime] = None


class MemberOut(BaseModel):
    membership_id: str
    name: Optional[str]
    email: str
    permission_level: str
    status: str
    joined_at: datetime


class AuditLogOut(BaseModel):
    id: str
    action: str
    actor_email: Optional[str]
    created_at: datetime
    metadata: dict[str, Any]


class OrgDetailOut(BaseModel):
    id: str
    name: str
    industry_name: str
    status: str
    created_at: datetime
    approved_at: Optional[datetime]
    rejection_reason: Optional[str]


class OrgDetailResponse(BaseModel):
    organization: OrgDetailOut
    created_by: CreatedByOut
    personas: list[PersonaOut]
    members: list[MemberOut]
    audit_logs: list[AuditLogOut]


class ApprovedOrgOut(BaseModel):
    id: str
    name: str
    status: str
    approved_at: Optional[datetime]


class ApproveResponse(BaseModel):
    organization: ApprovedOrgOut
    email_sent: bool
    email_error: Optional[str] = None


class RejectedOrgOut(BaseModel):
    id: str
    name: str
    status: str
    rejection_reason: str


class RejectResponse(BaseModel):
    organization: RejectedOrgOut
    email_sent: bool
    email_error: Optional[str] = None


class StatsResponse(BaseModel):
    pending_count: int
    approved_count: int
    rejected_count: int
    organizations_created_last_7_days: int
