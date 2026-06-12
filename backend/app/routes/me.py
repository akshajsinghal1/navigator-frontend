from fastapi import APIRouter, Depends, HTTPException, status
from app.dependencies import CurrentUser, get_current_user
from app.db.supabase_client import get_supabase

router = APIRouter(tags=["me"])


@router.get("/api/me/bootstrap")
def bootstrap(current_user: CurrentUser = Depends(get_current_user)):
    supabase = get_supabase()

    # Ensure profile exists
    profile_res = (
        supabase.table("profiles")
        .select("id, name, email")
        .eq("id", current_user.user_id)
        .single()
        .execute()
    )
    if not profile_res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROFILE_NOT_FOUND", "message": "Profile not found."},
        )
    profile = profile_res.data

    # Get active/pending membership
    membership_res = (
        supabase.table("memberships")
        .select(
            "id, permission_level, status, "
            "organizations(id, name, industry_name, status, rejection_reason)"
        )
        .eq("user_id", current_user.user_id)
        .neq("status", "deleted")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )

    membership_row = (membership_res.data or [None])[0]

    if not membership_row:
        return {
            "user": {"id": profile["id"], "name": profile["name"], "email": profile["email"]},
            "organization": None,
            "membership": None,
            "next_route": "/onboarding",
        }

    org = membership_row.get("organizations") or {}
    org_status = org.get("status", "")
    mem_status = membership_row.get("status", "")
    permission = membership_row.get("permission_level", "member")

    if mem_status == "deactivated":
        next_route = "/deactivated"
    elif org_status == "pending_approval":
        next_route = "/approval-pending"
    elif org_status == "rejected":
        next_route = "/approval-rejected"
    elif org_status == "approved" and permission == "admin":
        next_route = "/admin/organization"
    elif org_status == "approved" and permission == "member":
        next_route = "/app"
    else:
        next_route = "/onboarding"

    return {
        "user": {"id": profile["id"], "name": profile["name"], "email": profile["email"]},
        "organization": {
            "id": org.get("id"),
            "name": org.get("name"),
            "industry_name": org.get("industry_name"),
            "status": org_status,
            "rejection_reason": org.get("rejection_reason"),
        },
        "membership": {
            "id": membership_row["id"],
            "permission_level": permission,
            "status": mem_status,
        },
        "next_route": next_route,
    }
