import logging
from fastapi import HTTPException, status
from app.db.supabase_client import get_auth_client

logger = logging.getLogger(__name__)


def verify_supabase_jwt(token: str, jwt_secret: str = "") -> dict:
    """Verify a Supabase JWT by calling Supabase auth.get_user()."""
    try:
        client = get_auth_client()
        response = client.auth.get_user(token)
        if not response or not response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "UNAUTHENTICATED", "message": "Invalid or expired token."},
                headers={"WWW-Authenticate": "Bearer"},
            )
        user = response.user
        return {
            "sub": str(user.id),
            "email": user.email,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("JWT verification failed: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHENTICATED", "message": "Invalid or expired token."},
            headers={"WWW-Authenticate": "Bearer"},
        )
