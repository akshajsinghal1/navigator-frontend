-- ============================================================
-- 002_system_admin.sql
-- Internal system admin users table (from system_admin_spec.md)
-- Run AFTER 001_app_schema.sql in the Supabase SQL editor
-- ============================================================

create table if not exists public.system_admin_users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique check (email = lower(email)),
  role text not null check (role in ('system_admin', 'system_viewer')),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_system_admin_users_email
  on public.system_admin_users(email);

create index if not exists idx_system_admin_users_active
  on public.system_admin_users(is_active);

alter table public.system_admin_users disable row level security;
-- RLS disabled — only backend service role accesses this table

-- Enforce lowercase on insert/update via trigger
create or replace function public.system_admin_users_lowercase_email()
returns trigger language plpgsql as $$
begin
  new.email := lower(new.email);
  return new;
end;
$$;

drop trigger if exists trg_system_admin_users_lowercase_email on public.system_admin_users;
create trigger trg_system_admin_users_lowercase_email
  before insert or update on public.system_admin_users
  for each row execute function public.system_admin_users_lowercase_email();

-- ------------------------------------------------------------
-- Seed: replace these emails with your real internal team emails
-- ------------------------------------------------------------
insert into public.system_admin_users (email, role, is_active)
values ('akshaj.singhal@zclap.com', 'system_admin', true)
on conflict (email) do update
  set role = excluded.role,
      is_active = excluded.is_active;

-- Add more internal team members below as needed:
-- insert into public.system_admin_users (email, role, is_active)
-- values ('teammate@yourcompany.com', 'system_viewer', true)
-- on conflict (email) do update
--   set role = excluded.role,
--       is_active = excluded.is_active;
