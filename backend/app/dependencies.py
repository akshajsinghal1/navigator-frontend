from dataclasses import dataclass
from typing import Literal
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.auth.supabase_auth import verify_supabase_jwt
from app.config import settings
from app.db.supabase_client import get_supabase

bearer_scheme = HTTPBearer()


@dataclass
class CurrentUser:
    user_id: str
    email: str


@dataclass
class SystemAdminContext:
    user_id: str
    email: str
    role: Literal["system_admin", "system_viewer"]
    can_approve: bool
    can_reject: bool
    can_view: bool


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> CurrentUser:
    payload = verify_supabase_jwt(credentials.credentials, settings.supabase_jwt_secret)
    user_id = payload.get("sub")
    email = payload.get("email", "")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHENTICATED", "message": "Invalid token payload."},
        )
    return CurrentUser(user_id=user_id, email=email)


def require_system_admin(
    current_user: CurrentUser = Depends(get_current_user),
) -> SystemAdminContext:
    supabase = get_supabase()
    email = current_user.email.lower().strip()
    result = (
        supabase.table("system_admin_users")
        .select("role, is_active")
        .eq("email", email)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    row = rows[0] if rows else None
    if not row:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SYSTEM_ADMIN_FORBIDDEN",
                "message": "You do not have access to the system admin console.",
            },
        )
    if not row["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SYSTEM_ADMIN_INACTIVE",
                "message": "Your system admin account is inactive.",
            },
        )
    role = row["role"]
    return SystemAdminContext(
        user_id=current_user.user_id,
        email=email,
        role=role,
        can_approve=role == "system_admin",
        can_reject=role == "system_admin",
        can_view=True,
    )


def require_system_admin_role(
    ctx: SystemAdminContext = Depends(require_system_admin),
) -> SystemAdminContext:
    """Requires system_admin role (not just viewer)."""
    if not ctx.can_approve:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SYSTEM_ADMIN_ROLE_REQUIRED",
                "message": "This action requires the system_admin role.",
            },
        )
    return ctx
