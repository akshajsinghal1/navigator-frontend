-- ============================================================
-- 003_sample_data.sql
-- Sample data for testing the system admin console
-- Run in Supabase SQL editor (as postgres / service role)
-- Safe to re-run.
-- ============================================================

do $$
declare
  p_alice   uuid := '11111111-0000-0000-0000-000000000001';
  p_bob     uuid := '11111111-0000-0000-0000-000000000002';
  p_carol   uuid := '11111111-0000-0000-0000-000000000003';
  p_dave    uuid := '11111111-0000-0000-0000-000000000004';
  p_eve     uuid := '11111111-0000-0000-0000-000000000005';

  o_pending1  uuid := '22222222-0000-0000-0000-000000000001';
  o_pending2  uuid := '22222222-0000-0000-0000-000000000002';
  o_approved  uuid := '22222222-0000-0000-0000-000000000003';
  o_rejected  uuid := '22222222-0000-0000-0000-000000000004';

  per1 uuid := '33333333-0000-0000-0000-000000000001';
  per2 uuid := '33333333-0000-0000-0000-000000000002';
  per3 uuid := '33333333-0000-0000-0000-000000000003';
  per4 uuid := '33333333-0000-0000-0000-000000000004';
  per5 uuid := '33333333-0000-0000-0000-000000000005';
begin

  -- ── Seed fake rows into auth.users so profiles FK is satisfied ────────────
  insert into auth.users (
    id, instance_id, aud, role, email,
    encrypted_password, email_confirmed_at,
    created_at, updated_at,
    raw_app_meta_data, raw_user_meta_data,
    is_super_admin, confirmation_token, recovery_token,
    email_change_token_new, email_change
  )
  values
    (p_alice, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated',
     'singhalakshaj22@gmail.com',   '', now(), now(), now(), '{}', '{}', false, '', '', '', ''),
    (p_bob,   '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated',
     'akshaj.singhal@zclap.com',    '', now(), now(), now(), '{}', '{}', false, '', '', '', ''),
    (p_carol, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated',
     'akshaj2015@gmail.com',        '', now(), now(), now(), '{}', '{}', false, '', '', '', ''),
    (p_dave,  '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated',
     'dave.okonkwo@healthplus.io',  '', now(), now(), now(), '{}', '{}', false, '', '', '', ''),
    (p_eve,   '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated',
     'eve.petrov@finedge.com',      '', now(), now(), now(), '{}', '{}', false, '', '', '', '')
  on conflict (id) do nothing;

  -- ── Profiles ──────────────────────────────────────────────────────────────
  insert into public.profiles (id, name, email, created_at)
  values
    (p_alice, 'Alice Sharma',  'singhalakshaj22@gmail.com',  now() - interval '10 days'),
    (p_bob,   'Bob Chen',      'akshaj.singhal@zclap.com',   now() - interval '9 days'),
    (p_carol, 'Carol Mendes',  'akshaj2015@gmail.com',       now() - interval '7 days'),
    (p_dave,  'Dave Okonkwo',  'dave.okonkwo@healthplus.io',  now() - interval '6 days'),
    (p_eve,   'Eve Petrov',    'eve.petrov@finedge.com',      now() - interval '3 days')
  on conflict (id) do update
    set name = excluded.name, email = excluded.email;

  -- ── Organizations ─────────────────────────────────────────────────────────
  insert into public.organizations (id, name, industry_name, status, created_by, created_at)
  values
    (o_pending1, 'RetailCo Analytics',  'Retail',        'pending_approval', p_alice, now() - interval '8 days'),
    (o_pending2, 'HealthPlus Insights', 'Healthcare',    'pending_approval', p_carol, now() - interval '5 days'),
    (o_approved, 'FinEdge Capital',     'Finance',       'approved',         p_eve,   now() - interval '3 days'),
    (o_rejected, 'OldSchool Corp',      'Manufacturing', 'rejected',         p_bob,   now() - interval '12 days')
  on conflict (id) do update
    set name = excluded.name,
        industry_name = excluded.industry_name,
        status = excluded.status;

  update public.organizations
    set approved_at = now() - interval '1 day'
    where id = o_approved;

  update public.organizations
    set rejection_reason = 'Industry not currently supported. Please reapply when we expand to manufacturing.'
    where id = o_rejected;

  -- ── Personas ──────────────────────────────────────────────────────────────
  insert into public.personas (id, organization_id, name, description, is_active)
  values
    (per1, o_pending1, 'Store Manager',    'Regional store performance view', true),
    (per2, o_pending1, 'Category Analyst', 'Category-level sales and trends', true),
    (per3, o_pending2, 'Clinician',        'Patient outcome dashboards',      true),
    (per4, o_pending2, 'Operations Lead',  'Capacity and staffing metrics',   true),
    (per5, o_approved, 'Portfolio Manager','Fund performance and risk view',  true)
  on conflict (organization_id, name) do update
    set description = excluded.description;

  -- ── Memberships ───────────────────────────────────────────────────────────
  insert into public.memberships (organization_id, user_id, permission_level, status, joined_at)
  values
    (o_pending1, p_alice, 'admin',  'active', now() - interval '8 days'),
    (o_pending1, p_bob,   'member', 'active', now() - interval '7 days'),
    (o_pending2, p_carol, 'admin',  'active', now() - interval '5 days'),
    (o_pending2, p_dave,  'member', 'active', now() - interval '4 days'),
    (o_approved, p_eve,   'admin',  'active', now() - interval '3 days')
  on conflict (organization_id, user_id) do update
    set permission_level = excluded.permission_level;

  -- ── Audit Logs ────────────────────────────────────────────────────────────
  insert into public.audit_logs (organization_id, actor_email, action, entity_type, entity_id, metadata, created_at)
  values
    (o_pending1, 'singhalakshaj22@gmail.com',  'organization.created',  'organization', o_pending1,
     '{"org_name":"RetailCo Analytics"}', now() - interval '8 days'),
    (o_pending2, 'akshaj2015@gmail.com',       'organization.created',  'organization', o_pending2,
     '{"org_name":"HealthPlus Insights"}', now() - interval '5 days'),
    (o_approved, 'eve.petrov@finedge.com',     'organization.created',  'organization', o_approved,
     '{"org_name":"FinEdge Capital"}', now() - interval '3 days'),
    (o_approved, 'akshaj.singhal@zclap.com',   'organization.approved', 'organization', o_approved,
     '{"org_name":"FinEdge Capital","approved_by":"akshaj.singhal@zclap.com"}', now() - interval '1 day'),
    (o_rejected, 'bob.chen@retailco.com',      'organization.created',  'organization', o_rejected,
     '{"org_name":"OldSchool Corp"}', now() - interval '12 days'),
    (o_rejected, 'akshaj.singhal@zclap.com',   'organization.rejected', 'organization', o_rejected,
     '{"org_name":"OldSchool Corp","reason":"Industry not currently supported."}', now() - interval '10 days');

end $$;
