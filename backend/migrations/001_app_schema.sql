-- ============================================================
-- 001_app_schema.sql
-- Customer-facing app schema (from app_spec.md)
-- Run this in the Supabase SQL editor
-- ============================================================

-- ------------------------------------------------------------
-- profiles
-- ------------------------------------------------------------
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  name text not null,
  email text not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ------------------------------------------------------------
-- organizations
-- ------------------------------------------------------------
create table if not exists public.organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  industry_name text not null,
  status text not null default 'pending_approval'
    check (status in ('pending_approval', 'approved', 'rejected', 'suspended')),
  created_by uuid not null references public.profiles(id),
  approved_by uuid references public.profiles(id),
  approved_at timestamptz,
  rejection_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ------------------------------------------------------------
-- personas
-- ------------------------------------------------------------
create table if not exists public.personas (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  name text not null,
  description text,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (organization_id, name)
);

-- ------------------------------------------------------------
-- memberships
-- ------------------------------------------------------------
create table if not exists public.memberships (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  permission_level text not null default 'member'
    check (permission_level in ('admin', 'member')),
  persona_id uuid references public.personas(id),
  status text not null default 'active'
    check (status in ('active', 'deactivated', 'deleted')),
  invited_by uuid references public.profiles(id),
  joined_at timestamptz not null default now(),
  deactivated_at timestamptz,
  deleted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (organization_id, user_id)
);

-- ------------------------------------------------------------
-- invitations
-- ------------------------------------------------------------
create table if not exists public.invitations (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  email text not null,
  name text not null,
  persona_id uuid references public.personas(id),
  invited_by uuid not null references public.profiles(id),
  token_hash text not null,
  status text not null default 'pending'
    check (status in ('pending', 'accepted', 'expired', 'cancelled')),
  expires_at timestamptz not null,
  accepted_by uuid references public.profiles(id),
  accepted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (organization_id, email)
);

-- ------------------------------------------------------------
-- tableau_connections
-- ------------------------------------------------------------
create table if not exists public.tableau_connections (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  server_url text not null,
  site_name text not null,
  pat_name text not null,
  pat_secret_encrypted text,
  workbook_name text not null,
  status text not null default 'not_tested'
    check (status in ('not_tested', 'connected', 'failed', 'inactive')),
  is_active boolean not null default true,
  last_tested_at timestamptz,
  last_success_at timestamptz,
  last_error text,
  created_by uuid references public.profiles(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ------------------------------------------------------------
-- pipeline_runs
-- ------------------------------------------------------------
create table if not exists public.pipeline_runs (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  tableau_connection_id uuid references public.tableau_connections(id) on delete set null,
  status text not null default 'queued'
    check (status in ('queued', 'running', 'success', 'failed')),
  run_type text not null default 'manual'
    check (run_type in ('manual', 'scheduled', 'system')),
  started_by uuid references public.profiles(id),
  logs jsonb not null default '[]'::jsonb,
  error_message text,
  started_at timestamptz not null default now(),
  finished_at timestamptz
);

-- ------------------------------------------------------------
-- audit_logs
-- ------------------------------------------------------------
create table if not exists public.audit_logs (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid references public.organizations(id),
  actor_user_id uuid references public.profiles(id),
  actor_email text,
  action text not null,
  entity_type text not null,
  entity_id uuid,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- ------------------------------------------------------------
-- Indexes
-- ------------------------------------------------------------
create index if not exists idx_organizations_created_by on public.organizations(created_by);
create index if not exists idx_organizations_status on public.organizations(status);
create index if not exists idx_personas_organization_id on public.personas(organization_id);
create index if not exists idx_memberships_user_id on public.memberships(user_id);
create index if not exists idx_memberships_organization_id on public.memberships(organization_id);
create index if not exists idx_memberships_org_status on public.memberships(organization_id, status);
create index if not exists idx_invitations_email on public.invitations(email);
create index if not exists idx_invitations_org_status on public.invitations(organization_id, status);
create index if not exists idx_invitations_token_hash on public.invitations(token_hash);
create index if not exists idx_tableau_connections_org_active on public.tableau_connections(organization_id, is_active);
create index if not exists idx_pipeline_runs_org on public.pipeline_runs(organization_id);
create index if not exists idx_audit_logs_org on public.audit_logs(organization_id);
create index if not exists idx_audit_logs_actor on public.audit_logs(actor_user_id);

-- ------------------------------------------------------------
-- Row Level Security (backend uses service role, so RLS
-- prevents accidental direct frontend writes)
-- ------------------------------------------------------------
alter table public.profiles enable row level security;
alter table public.organizations enable row level security;
alter table public.personas enable row level security;
alter table public.memberships enable row level security;
alter table public.invitations enable row level security;
alter table public.tableau_connections enable row level security;
alter table public.pipeline_runs enable row level security;
alter table public.audit_logs enable row level security;

-- Allow users to read their own profile
create policy "profiles_select_own" on public.profiles
  for select using (auth.uid() = id);

-- Service role bypasses RLS automatically (used by backend)
