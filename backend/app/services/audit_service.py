import logging
from typing import Any, Optional
from app.db.supabase_client import get_supabase

logger = logging.getLogger(__name__)


def create_audit_log(
    action: str,
    entity_type: str,
    entity_id: Optional[str] = None,
    organization_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    actor_email: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    supabase = get_supabase()
    try:
        supabase.table("audit_logs").insert({
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "organization_id": organization_id,
            # actor_user_id only set when actor is a customer profile (not system admin)
            "actor_user_id": None,
            "actor_email": actor_email,
            "metadata": metadata or {},
        }).execute()
    except Exception as e:
        logger.error("Failed to write audit log for action %s: %s", action, e)
