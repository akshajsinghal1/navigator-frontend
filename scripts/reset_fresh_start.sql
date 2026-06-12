-- =============================================================================
-- reset_fresh_start.sql
-- Wipe ALL customer-app data for a clean end-to-end test.
--
-- Run in: Supabase Dashboard → SQL Editor → New query → paste → Run
--
-- KEEPS:
--   - system_admin_users (your internal team allowlist for system admin login)
--   - table schemas / migrations
--
-- DELETES:
--   - all orgs, personas, memberships, invitations, connectors, pipeline runs
--   - all customer profiles
--   - all Supabase Auth users (you will sign up again from scratch)
--
-- If you also ran backend/migrations/003_sample_data.sql, this removes that too.
-- =============================================================================

begin;

-- App tables (child → parent order)
delete from public.audit_logs;
delete from public.pipeline_runs;
delete from public.invitations;
delete from public.memberships;
delete from public.tableau_connections;
delete from public.personas;
delete from public.organizations;
delete from public.profiles;

-- Auth (customer signups) — required for a true fresh signup flow
delete from auth.sessions;
delete from auth.refresh_tokens;
delete from auth.mfa_factors;
delete from auth.identities;
delete from auth.users;

-- system_admin_users is intentionally NOT deleted.
-- Re-seed if needed: backend/migrations/002_system_admin.sql

commit;

-- Verify (should all be 0):
select 'organizations' as tbl, count(*) from public.organizations
union all select 'personas', count(*) from public.personas
union all select 'memberships', count(*) from public.memberships
union all select 'profiles', count(*) from public.profiles
union all select 'auth.users', count(*) from auth.users
union all select 'system_admin_users', count(*) from public.system_admin_users;
