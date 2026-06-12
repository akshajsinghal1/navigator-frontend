from supabase import create_client, Client
from app.config import settings


def get_supabase() -> Client:
    """Service-role Supabase client — bypasses RLS. Backend use only."""
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def get_auth_client() -> Client:
    """Anon client used only for auth token verification."""
    return create_client(settings.supabase_url, settings.supabase_anon_key)
