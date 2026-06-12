from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.config import settings
from app.dependencies import (
    SystemAdminContext,
    require_system_admin,
    require_system_admin_role,
)
from app.db.supabase_client import get_supabase
from app.schemas.system_admin import (
    ApproveOrganizationRequest,
    ApproveResponse,
    ApprovedOrgOut,
    AuditLogOut,
    CreatedByOut,
    MeResponse,
    MemberOut,
    OrgDetailOut,
    OrgDetailResponse,
    OrgListItem,
    OrgListResponse,
    Pagination,
    PersonaOut,
    RejectOrganizationRequest,
    RejectResponse,
    RejectedOrgOut,
    StatsResponse,
    SystemAdminInfoOut,
    SystemAdminUserOut,
)
from app.services.audit_service import create_audit_log
from app.services.email_service import send_approval_email, send_rejection_email

router = APIRouter(prefix="/api/system-admin", tags=["system-admin"])


def _permissions(ctx: SystemAdminContext) -> list[str]:
    perms = ["organization.read", "audit.read"]
    if ctx.can_approve:
        perms += ["organization.approve", "organization.reject"]
    return perms


# ── GET /api/system-admin/me ─────────────────────────────────────────────────

@router.get("/me", response_model=MeResponse)
def get_me(ctx: SystemAdminContext = Depends(require_system_admin)):
    return MeResponse(
        user=SystemAdminUserOut(id=ctx.user_id, email=ctx.email),
        system_admin=SystemAdminInfoOut(
            role=ctx.role,
            is_active=True,
            permissions=_permissions(ctx),
        ),
    )


# ── GET /api/system-admin/stats ──────────────────────────────────────────────

@router.get("/stats", response_model=StatsResponse)
def get_stats(ctx: SystemAdminContext = Depends(require_system_admin)):
    supabase = get_supabase()
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    def count(status_val: Optional[str] = None, since: Optional[str] = None) -> int:
        q = supabase.table("organizations").select("id", count="exact")
        if status_val:
            q = q.eq("status", status_val)
        if since:
            q = q.gte("created_at", since)
        r = q.execute()
        return r.count or 0

    return StatsResponse(
        pending_count=count("pending_approval"),
        approved_count=count("approved"),
        rejected_count=count("rejected"),
        organizations_created_last_7_days=count(since=week_ago),
    )


# ── GET /api/system-admin/organizations ─────────────────────────────────────

@router.get("/organizations", response_model=OrgListResponse)
def list_organizations(
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ctx: SystemAdminContext = Depends(require_system_admin),
):
    supabase = get_supabase()

    # Fetch orgs with creator profile joined
    q = (
        supabase.table("organizations")
        .select(
            "id, name, industry_name, status, created_at, created_by, "
            "profiles!organizations_created_by_fkey(id, name, email)"
        )
        .order("created_at", desc=True)
    )
    if status_filter:
        q = q.eq("status", status_filter)

    result = q.execute()
    orgs = result.data or []

    # Apply search filter in Python (Supabase free tier doesn't support ilike on joins easily)
    if search:
        s = search.lower()
        orgs = [
            o for o in orgs
            if s in o["name"].lower()
            or s in (o["profiles"] or {}).get("email", "").lower()
            or s in (o["profiles"] or {}).get("name", "").lower()
            or s in o["industry_name"].lower()
        ]

    total = len(orgs)
    start = (page - 1) * page_size
    page_orgs = orgs[start: start + page_size]

    items = []
    for o in page_orgs:
        org_id = o["id"]
        profile = o.get("profiles") or {}

        # Persona count
        pc = supabase.table("personas").select("id", count="exact").eq("organization_id", org_id).execute()
        persona_count = pc.count or 0

        # Member count (active)
        mc = (
            supabase.table("memberships")
            .select("id", count="exact")
            .eq("organization_id", org_id)
            .eq("status", "active")
            .execute()
        )
        member_count = mc.count or 0

        items.append(OrgListItem(
            organization_id=org_id,
            organization_name=o["name"],
            industry_name=o["industry_name"],
            status=o["status"],
            created_at=o["created_at"],
            created_by=CreatedByOut(
                user_id=profile.get("id", o["created_by"]),
                name=profile.get("name"),
                email=profile.get("email", ""),
            ),
            persona_count=persona_count,
            member_count=member_count,
        ))

    return OrgListResponse(
        items=items,
        pagination=Pagination(page=page, page_size=page_size, total=total),
    )


# ── GET /api/system-admin/organizations/{organization_id} ────────────────────

@router.get("/organizations/{organization_id}", response_model=OrgDetailResponse)
def get_organization(
    organization_id: str,
    ctx: SystemAdminContext = Depends(require_system_admin),
):
    supabase = get_supabase()

    org_result = (
        supabase.table("organizations")
        .select(
            "id, name, industry_name, status, created_at, approved_at, rejection_reason, created_by, "
            "profiles!organizations_created_by_fkey(id, name, email)"
        )
        .eq("id", organization_id)
        .single()
        .execute()
    )
    if not org_result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORGANIZATION_NOT_FOUND", "message": "Organization not found."},
        )
    o = org_result.data
    profile = o.get("profiles") or {}

    # Personas
    personas_res = (
        supabase.table("personas")
        .select("id, name, is_active, created_at")
        .eq("organization_id", organization_id)
        .execute()
    )
    personas = [
        PersonaOut(id=p["id"], name=p["name"], is_active=p["is_active"], created_at=p.get("created_at"))
        for p in (personas_res.data or [])
    ]

    # Members
    members_res = (
        supabase.table("memberships")
        .select(
            "id, permission_level, status, joined_at, "
            "profiles!memberships_user_id_fkey(name, email)"
        )
        .eq("organization_id", organization_id)
        .neq("status", "deleted")
        .execute()
    )
    members = []
    for m in (members_res.data or []):
        mp = m.get("profiles") or {}
        members.append(MemberOut(
            membership_id=m["id"],
            name=mp.get("name"),
            email=mp.get("email", ""),
            permission_level=m["permission_level"],
            status=m["status"],
            joined_at=m["joined_at"],
        ))

    # Audit logs
    audit_res = (
        supabase.table("audit_logs")
        .select("id, action, actor_email, created_at, metadata")
        .eq("organization_id", organization_id)
        .order("created_at", desc=True)
        .limit(50)
        .execute()
    )
    audit_logs = [
        AuditLogOut(
            id=a["id"],
            action=a["action"],
            actor_email=a.get("actor_email"),
            created_at=a["created_at"],
            metadata=a.get("metadata") or {},
        )
        for a in (audit_res.data or [])
    ]

    return OrgDetailResponse(
        organization=OrgDetailOut(
            id=o["id"],
            name=o["name"],
            industry_name=o["industry_name"],
            status=o["status"],
            created_at=o["created_at"],
            approved_at=o.get("approved_at"),
            rejection_reason=o.get("rejection_reason"),
        ),
        created_by=CreatedByOut(
            user_id=profile.get("id", o["created_by"]),
            name=profile.get("name"),
            email=profile.get("email", ""),
        ),
        personas=personas,
        members=members,
        audit_logs=audit_logs,
    )


# ── PATCH /api/system-admin/organizations/{organization_id}/approve ──────────

@router.patch("/organizations/{organization_id}/approve", response_model=ApproveResponse)
async def approve_organization(
    organization_id: str,
    body: ApproveOrganizationRequest = ApproveOrganizationRequest(),
    ctx: SystemAdminContext = Depends(require_system_admin_role),
):
    supabase = get_supabase()

    org_res = (
        supabase.table("organizations")
        .select("id, name, status, created_by")
        .eq("id", organization_id)
        .single()
        .execute()
    )
    if not org_res.data:
        raise HTTPException(404, detail={"code": "ORGANIZATION_NOT_FOUND", "message": "Organization not found."})

    org = org_res.data
    if org["status"] == "approved":
        raise HTTPException(409, detail={"code": "ORGANIZATION_ALREADY_APPROVED", "message": "Organization is already approved."})
    if org["status"] != "pending_approval":
        raise HTTPException(409, detail={"code": "ORGANIZATION_NOT_PENDING", "message": "Only pending organizations can be approved."})

    now = datetime.now(timezone.utc).isoformat()
    supabase.table("organizations").update({
        "status": "approved",
        "approved_at": now,
        "rejection_reason": None,
        "updated_at": now,
    }).eq("id", organization_id).execute()

    # Get creator profile for email
    profile_res = (
        supabase.table("profiles")
        .select("name, email")
        .eq("id", org["created_by"])
        .single()
        .execute()
    )
    profile = profile_res.data or {}
    admin_email = profile.get("email", "")
    admin_name = profile.get("name", "Admin")

    create_audit_log(
        action="organization.approved",
        entity_type="organization",
        entity_id=organization_id,
        organization_id=organization_id,
        actor_user_id=ctx.user_id,
        actor_email=ctx.email,
        metadata={
            "previous_status": "pending_approval",
            "new_status": "approved",
            "note": body.note,
        },
    )

    login_url = f"{settings.app_url}/login"
    email_sent, email_error = await send_approval_email(admin_email, admin_name, org["name"], login_url)

    return ApproveResponse(
        organization=ApprovedOrgOut(
            id=organization_id,
            name=org["name"],
            status="approved",
            approved_at=datetime.now(timezone.utc),
        ),
        email_sent=email_sent,
        email_error=email_error,
    )


# ── PATCH /api/system-admin/organizations/{organization_id}/reject ───────────

@router.patch("/organizations/{organization_id}/reject", response_model=RejectResponse)
async def reject_organization(
    organization_id: str,
    body: RejectOrganizationRequest,
    ctx: SystemAdminContext = Depends(require_system_admin_role),
):
    supabase = get_supabase()

    org_res = (
        supabase.table("organizations")
        .select("id, name, status, created_by")
        .eq("id", organization_id)
        .single()
        .execute()
    )
    if not org_res.data:
        raise HTTPException(404, detail={"code": "ORGANIZATION_NOT_FOUND", "message": "Organization not found."})

    org = org_res.data
    if org["status"] == "rejected":
        raise HTTPException(409, detail={"code": "ORGANIZATION_ALREADY_REJECTED", "message": "Organization is already rejected."})
    if org["status"] != "pending_approval":
        raise HTTPException(409, detail={"code": "ORGANIZATION_NOT_PENDING", "message": "Only pending organizations can be rejected."})

    now = datetime.now(timezone.utc).isoformat()
    supabase.table("organizations").update({
        "status": "rejected",
        "rejection_reason": body.rejection_reason,
        "approved_by": None,
        "approved_at": None,
        "updated_at": now,
    }).eq("id", organization_id).execute()

    profile_res = (
        supabase.table("profiles")
        .select("name, email")
        .eq("id", org["created_by"])
        .single()
        .execute()
    )
    profile = profile_res.data or {}
    admin_email = profile.get("email", "")
    admin_name = profile.get("name", "Admin")

    create_audit_log(
        action="organization.rejected",
        entity_type="organization",
        entity_id=organization_id,
        organization_id=organization_id,
        actor_user_id=ctx.user_id,
        actor_email=ctx.email,
        metadata={
            "rejection_reason": body.rejection_reason,
            "previous_status": "pending_approval",
            "new_status": "rejected",
        },
    )

    email_sent, email_error = await send_rejection_email(
        admin_email, admin_name, org["name"], body.rejection_reason
    )

    return RejectResponse(
        organization=RejectedOrgOut(
            id=organization_id,
            name=org["name"],
            status="rejected",
            rejection_reason=body.rejection_reason,
        ),
        email_sent=email_sent,
        email_error=email_error,
    )


# ── GET /api/system-admin/audit-logs ────────────────────────────────────────

@router.get("/audit-logs")
def list_audit_logs(
    organization_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    ctx: SystemAdminContext = Depends(require_system_admin),
):
    supabase = get_supabase()
    q = (
        supabase.table("audit_logs")
        .select("id, action, actor_email, organization_id, entity_type, entity_id, created_at, metadata")
        .order("created_at", desc=True)
    )
    if organization_id:
        q = q.eq("organization_id", organization_id)

    result = q.execute()
    logs = result.data or []
    total = len(logs)
    start = (page - 1) * page_size
    return {
        "items": logs[start: start + page_size],
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }
