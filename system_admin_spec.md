# system_admin_spec.md

# Internal System Admin Approval Specification

## 0. Purpose

Build the internal approval workflow that allows the company team to review and approve/reject organization creation requests submitted from the customer-facing application.

This specification is separate from `app_spec.md`.

The customer-facing app only allows users to create organizations and wait for approval. This internal system is used by the company team to approve or reject those organizations.

---

## 1. Scope

This specification covers:

- Internal system admin login/access
- Pending organization request list
- Organization request detail page
- Approve organization
- Reject organization
- Approval/rejection email notifications
- Audit logs
- Permission protection
- Basic operational dashboard

This specification does not cover:

- Customer signup
- Customer onboarding
- Customer Admin Panel
- User invitation by customer admins
- Tableau connector configuration by customer admins

Those are covered in `app_spec.md`.

---

## 2. Admin Personas

Internal system users are not customer organization users.

There are two internal roles:

```text
system_admin
system_viewer
```

### 2.1 system_admin

Can:

```text
View pending organization requests
View approved/rejected organizations
Approve organization
Reject organization
Add rejection reason
View audit logs
```

### 2.2 system_viewer

Can:

```text
View organization requests
View status
View audit logs
```

Cannot:

```text
Approve
Reject
Modify anything
```

For MVP, if internal user management is too much, use a simple allowlist based on email addresses.

---

## 3. Architecture

The system admin console can be implemented in one of two ways.

### Option A: Same frontend app, protected internal routes

Routes:

```text
/system-admin/login
/system-admin/organizations
/system-admin/organizations/:organizationId
```

### Option B: Separate internal app

Separate deployment:

```text
admin.yourdomain.com
```

For MVP, Option A is acceptable if internal routes are strongly protected.

---

## 4. Authentication and Authorization

### 4.1 Internal admin auth

Use Supabase Auth or existing internal authentication.

For MVP, use Supabase Auth with an internal allowlist table.

Table:

```sql
create table if not exists public.system_admin_users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  role text not null check (role in ('system_admin', 'system_viewer')),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
```

A logged-in user can access internal admin routes only if:

```text
email exists in system_admin_users
is_active = true
```

### 4.2 Authorization rules

```text
system_admin can approve/reject
system_viewer can only view
inactive internal users cannot access
customer organization admins cannot access system admin pages unless separately allowlisted
```

Do not rely on frontend-only protection.

Every internal backend endpoint must verify internal admin permission.

---

## 5. Internal Routes

Frontend routes:

```text
/system-admin
/system-admin/login
/system-admin/organizations
/system-admin/organizations/pending
/system-admin/organizations/approved
/system-admin/organizations/rejected
/system-admin/organizations/:organizationId
```

Default:

```text
/system-admin -> /system-admin/organizations/pending
```

---

## 6. Internal API Routes

Implement these backend APIs:

```text
GET   /api/system-admin/me
GET   /api/system-admin/organizations
GET   /api/system-admin/organizations/{organization_id}
PATCH /api/system-admin/organizations/{organization_id}/approve
PATCH /api/system-admin/organizations/{organization_id}/reject
GET   /api/system-admin/audit-logs
```

Optional:

```text
GET   /api/system-admin/stats
```

---

## 7. System Admin Bootstrap

Endpoint:

```http
GET /api/system-admin/me
```

Behavior:

1. Verify Supabase session/JWT.
2. Get user email.
3. Check `system_admin_users`.
4. Return role and allowed actions.

Response:

```json
{
  "user": {
    "id": "uuid",
    "email": "internal@example.com",
    "name": "Internal Admin"
  },
  "system_admin": {
    "role": "system_admin",
    "is_active": true,
    "permissions": [
      "organization.read",
      "organization.approve",
      "organization.reject",
      "audit.read"
    ]
  }
}
```

If not allowed:

```json
{
  "error": {
    "code": "SYSTEM_ADMIN_FORBIDDEN",
    "message": "You do not have access to the system admin console."
  }
}
```

---

## 8. Organization List Page

Route:

```text
/system-admin/organizations
```

Default filter:

```text
pending_approval
```

### 8.1 Filters

Support filters:

```text
Status:
- Pending Approval
- Approved
- Rejected
- Suspended

Search:
- Organization name
- Industry
- Admin email
- Admin name

Date range:
- Created from
- Created to
```

For MVP, implement status filter and search.

### 8.2 API

```http
GET /api/system-admin/organizations?status=pending_approval&search=abc&page=1&page_size=20
```

Response:

```json
{
  "items": [
    {
      "organization_id": "uuid",
      "organization_name": "ABC Healthcare",
      "industry_name": "Healthcare",
      "status": "pending_approval",
      "created_at": "2026-06-11T10:00:00Z",
      "created_by": {
        "user_id": "uuid",
        "name": "Shibani Bhuyan",
        "email": "admin@example.com"
      },
      "persona_count": 5,
      "member_count": 1
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 1
  }
}
```

### 8.3 UI columns

```text
Organization Name
Industry
Submitted By
Submitted Email
Personas Count
Members Count
Status
Submitted At
Actions
```

Actions:

```text
View
Approve
Reject
```

Approve/reject buttons should show only for `system_admin` and only when status is `pending_approval`.

---

## 9. Organization Detail Page

Route:

```text
/system-admin/organizations/:organizationId
```

### 9.1 API

```http
GET /api/system-admin/organizations/{organization_id}
```

Response:

```json
{
  "organization": {
    "id": "uuid",
    "name": "ABC Healthcare",
    "industry_name": "Healthcare",
    "status": "pending_approval",
    "created_at": "2026-06-11T10:00:00Z",
    "approved_at": null,
    "rejection_reason": null
  },
  "created_by": {
    "id": "uuid",
    "name": "Shibani Bhuyan",
    "email": "admin@example.com"
  },
  "personas": [
    {
      "id": "uuid",
      "name": "CFO",
      "is_active": true
    },
    {
      "id": "uuid",
      "name": "Navigator",
      "is_active": true
    }
  ],
  "members": [
    {
      "membership_id": "uuid",
      "name": "Shibani Bhuyan",
      "email": "admin@example.com",
      "permission_level": "admin",
      "status": "active",
      "joined_at": "2026-06-11T10:00:00Z"
    }
  ],
  "audit_logs": [
    {
      "id": "uuid",
      "action": "organization.created",
      "actor_email": "admin@example.com",
      "created_at": "2026-06-11T10:00:00Z",
      "metadata": {}
    }
  ]
}
```

### 9.2 UI sections

Show:

```text
Organization Summary
Submitted By
Personas
Members
Audit History
Approval Actions
```

### 9.3 Organization Summary

Fields:

```text
Organization Name
Industry
Status
Created At
Approved At
Rejected Reason
```

### 9.4 Submitted By

Fields:

```text
Name
Email
User ID
```

### 9.5 Personas

Show all personas as tags or table:

```text
Name
Status
Created At
```

### 9.6 Members

For initial request, should usually show only the org admin.

Columns:

```text
Name
Email
Permission
Status
Joined At
```

---

## 10. Approve Organization Flow

### 10.1 UI

Approve button visible only when:

```text
current internal role = system_admin
organization.status = pending_approval
```

Clicking approve opens confirmation modal:

```text
Approve Organization?

This will activate the organization and allow the organization admin to access the Admin Panel.

Organization: ABC Healthcare
Admin: admin@example.com
```

Buttons:

```text
Cancel
Approve Organization
```

### 10.2 API

```http
PATCH /api/system-admin/organizations/{organization_id}/approve
```

Request:

```json
{
  "note": "Optional internal note"
}
```

Backend behavior:

1. Verify internal user is active `system_admin`.
2. Fetch organization.
3. Ensure organization exists.
4. Ensure status is `pending_approval`.
5. Update organization:
   - `status = approved`
   - `approved_by = current internal user profile id if available`
   - `approved_at = now()`
   - `rejection_reason = null`
6. Ensure the creator/admin membership is still active.
7. Create audit log:
   - action = `organization.approved`
   - actor_user_id = internal admin user id
   - entity_type = `organization`
   - entity_id = organization id
8. Send approval email to organization creator/admin.
9. Return updated organization.

Response:

```json
{
  "organization": {
    "id": "uuid",
    "name": "ABC Healthcare",
    "status": "approved",
    "approved_at": "2026-06-11T11:00:00Z"
  },
  "email_sent": true
}
```

### 10.3 Approval email

Send to organization creator/admin email.

Subject:

```text
Your organization has been approved
```

Body:

```text
Hello {{admin_name}},

Your organization {{organization_name}} has been approved.

You can now log in and access your admin panel:

{{login_url}}

Thank you.
```

### 10.4 Approval success UI

After approve:

```text
Organization approved successfully.
Approval email sent to admin@example.com.
```

Update list/detail status to:

```text
Approved
```

---

## 11. Reject Organization Flow

### 11.1 UI

Reject button visible only when:

```text
current internal role = system_admin
organization.status = pending_approval
```

Clicking reject opens modal.

Fields:

```text
Rejection Reason
```

Validation:

```text
Reason required
Minimum 10 characters
Maximum 1000 characters
```

Confirmation copy:

```text
Reject Organization?

This will reject the organization request and notify the requester.
```

Buttons:

```text
Cancel
Reject Organization
```

### 11.2 API

```http
PATCH /api/system-admin/organizations/{organization_id}/reject
```

Request:

```json
{
  "rejection_reason": "Unable to verify organization details."
}
```

Backend behavior:

1. Verify internal user is active `system_admin`.
2. Fetch organization.
3. Ensure organization exists.
4. Ensure status is `pending_approval`.
5. Update organization:
   - `status = rejected`
   - `rejection_reason = provided reason`
   - `approved_by = null`
   - `approved_at = null`
6. Create audit log:
   - action = `organization.rejected`
7. Send rejection email to organization creator/admin.
8. Return updated organization.

Response:

```json
{
  "organization": {
    "id": "uuid",
    "name": "ABC Healthcare",
    "status": "rejected",
    "rejection_reason": "Unable to verify organization details."
  },
  "email_sent": true
}
```

### 11.3 Rejection email

Subject:

```text
Organization request update
```

Body:

```text
Hello {{admin_name}},

Your organization request for {{organization_name}} was not approved.

Reason:
{{rejection_reason}}

Please contact our team if you have questions.
```

### 11.4 Rejection success UI

After reject:

```text
Organization rejected.
Rejection email sent to admin@example.com.
```

---

## 12. Status Transition Rules

Allowed transitions:

```text
pending_approval -> approved
pending_approval -> rejected
approved -> suspended
suspended -> approved
```

For MVP, only implement:

```text
pending_approval -> approved
pending_approval -> rejected
```

Do not allow:

```text
approved -> rejected
rejected -> approved
```

unless a future requirement explicitly asks for reopening.

If the user wants rejected organizations to be reconsidered later, implement a separate `reopen` action in the future.

---

## 13. Internal Stats Page

Optional route:

```text
/system-admin/stats
```

Optional endpoint:

```http
GET /api/system-admin/stats
```

Response:

```json
{
  "pending_count": 4,
  "approved_count": 18,
  "rejected_count": 2,
  "organizations_created_last_7_days": 5
}
```

This is optional for MVP.

---

## 14. Audit Logs

Use the same `public.audit_logs` table from `app_spec.md`.

Internal approval actions must create logs.

### 14.1 Required actions

```text
organization.approved
organization.rejected
system_admin.viewed_organization
```

The `viewed_organization` action is optional. Do not add it if it creates too much noise.

### 14.2 Audit log metadata

Approval metadata:

```json
{
  "note": "Optional internal note",
  "previous_status": "pending_approval",
  "new_status": "approved"
}
```

Rejection metadata:

```json
{
  "rejection_reason": "Unable to verify organization details.",
  "previous_status": "pending_approval",
  "new_status": "rejected"
}
```

---

## 15. Database Additions

This spec requires one additional table beyond the customer app schema.

### 15.1 system_admin_users

```sql
create table if not exists public.system_admin_users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  role text not null check (role in ('system_admin', 'system_viewer')),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
```

Indexes:

```sql
create index if not exists idx_system_admin_users_email
  on public.system_admin_users(email);

create index if not exists idx_system_admin_users_active
  on public.system_admin_users(is_active);
```

Enable RLS:

```sql
alter table public.system_admin_users enable row level security;
```

For MVP, only backend service role should read/write this table.

---

## 16. Backend Permission Helper

Create helper:

```python
require_system_admin(current_user) -> SystemAdminContext
```

Behavior:

1. Read current authenticated user's email.
2. Lowercase email.
3. Query `system_admin_users`.
4. Ensure:
   - user exists
   - `is_active = true`
5. Return role.

Response class:

```python
class SystemAdminContext:
    user_id: UUID
    email: str
    role: Literal["system_admin", "system_viewer"]
    can_approve: bool
    can_reject: bool
    can_view: bool
```

Rules:

```text
system_admin:
  can_view = true
  can_approve = true
  can_reject = true

system_viewer:
  can_view = true
  can_approve = false
  can_reject = false
```

---

## 17. Error Response Format

Use the same error format as app APIs:

```json
{
  "error": {
    "code": "SYSTEM_ADMIN_FORBIDDEN",
    "message": "You do not have access to this action."
  }
}
```

Common error codes:

```text
UNAUTHENTICATED
SYSTEM_ADMIN_FORBIDDEN
SYSTEM_ADMIN_INACTIVE
SYSTEM_ADMIN_ROLE_REQUIRED
ORGANIZATION_NOT_FOUND
ORGANIZATION_NOT_PENDING
ORGANIZATION_ALREADY_APPROVED
ORGANIZATION_ALREADY_REJECTED
REJECTION_REASON_REQUIRED
VALIDATION_ERROR
EMAIL_SEND_FAILED
INTERNAL_ERROR
```

---

## 18. Email Failure Handling

Approval/rejection should still update organization state even if email sending fails, but response must clearly indicate email failure.

Example response:

```json
{
  "organization": {
    "id": "uuid",
    "status": "approved"
  },
  "email_sent": false,
  "email_error": "SMTP connection failed"
}
```

UI should show:

```text
Organization approved, but approval email could not be sent.
Please resend or contact the user manually.
```

Optional future endpoint:

```http
POST /api/system-admin/organizations/{organization_id}/resend-status-email
```

Do not implement unless required.

---

## 19. Frontend Component Plan

Suggested structure if using the same Vite app:

```text
src/
  pages/
    system-admin/
      SystemAdminLoginPage.tsx
      SystemAdminOrganizationsPage.tsx
      SystemAdminOrganizationDetailPage.tsx
      SystemAdminForbiddenPage.tsx
  components/
    system-admin/
      SystemAdminLayout.tsx
      OrganizationRequestTable.tsx
      OrganizationRequestFilters.tsx
      ApproveOrganizationModal.tsx
      RejectOrganizationModal.tsx
      InternalStatusBadge.tsx
      AuditLogTable.tsx
  lib/
    systemAdminApi.ts
```

---

## 20. Internal UI Layout

Use a separate internal layout.

Header:

```text
System Admin
Current internal user email
Role badge
Logout
```

Sidebar/tabs:

```text
Pending Requests
Approved Organizations
Rejected Organizations
All Organizations
```

Do not mix this with the customer Admin Panel.

Customer Admin Panel route:

```text
/admin/*
```

System Admin route:

```text
/system-admin/*
```

---

## 21. Organization List UI Details

### 21.1 Pending tab

Show:

```text
Pending organization requests waiting for approval.
```

Default sorted by:

```text
created_at desc
```

### 21.2 Approved tab

Show approved organizations.

### 21.3 Rejected tab

Show rejected organizations with rejection reason preview.

### 21.4 Empty states

Pending empty state:

```text
No pending organization requests.
```

Approved empty state:

```text
No approved organizations yet.
```

Rejected empty state:

```text
No rejected organizations.
```

---

## 22. Organization Detail UI Actions

For pending organization:

```text
Approve Organization
Reject Organization
```

For approved organization:

```text
Status: Approved
Approved At
Approved By
No approval actions available.
```

For rejected organization:

```text
Status: Rejected
Rejection Reason
No approval actions available.
```

---

## 23. Acceptance Criteria

### Access Control

- Non-logged-in user cannot access `/system-admin/*`.
- Logged-in customer admin cannot access system admin unless allowlisted.
- Inactive internal admin cannot access.
- `system_viewer` can view but cannot approve/reject.
- `system_admin` can approve/reject.

### Organization List

- System admin can see pending organizations.
- Can filter by status.
- Can search by organization name/admin email.
- Shows correct submitted admin details.
- Shows persona count.
- Shows member count.

### Detail Page

- Shows organization details.
- Shows created-by user.
- Shows personas.
- Shows members.
- Shows audit history.

### Approval

- Pending organization can be approved.
- Approved organization status changes to `approved`.
- `approved_at` is set.
- Audit log is created.
- Approval email is sent to organization admin.
- Customer admin can access Admin Panel after approval.

### Rejection

- Pending organization can be rejected with reason.
- Rejection reason is required.
- Rejected organization status changes to `rejected`.
- Audit log is created.
- Rejection email is sent to organization admin.
- Customer admin sees rejected screen after login.

### Invalid Transitions

- Cannot approve already approved org.
- Cannot reject already approved org.
- Cannot approve rejected org.
- Cannot reject rejected org.

---

## 24. Manual QA Checklist

```text
Log in as non-allowlisted user
Log in as system_viewer
Log in as system_admin
View pending organization
Approve organization
Confirm customer can now access /admin
Reject organization
Confirm customer sees rejected page
Try approving same organization twice
Try rejecting approved organization
Test email failure behavior
Test search
Test status filters
```

---

## 25. Seed Data

For local development, seed one system admin user.

Example:

```sql
insert into public.system_admin_users (email, role, is_active)
values ('internal-admin@example.com', 'system_admin', true)
on conflict (email) do update
set role = excluded.role,
    is_active = excluded.is_active;
```

Also seed one viewer:

```sql
insert into public.system_admin_users (email, role, is_active)
values ('internal-viewer@example.com', 'system_viewer', true)
on conflict (email) do update
set role = excluded.role,
    is_active = excluded.is_active;
```

Do not seed real production emails in committed code unless this is intended.

---

## 26. Development Notes for Codex

1. Implement the customer app first from `app_spec.md`.
2. Implement `system_admin_users` table.
3. Add backend system admin permission helper.
4. Add system admin APIs.
5. Add system admin frontend routes.
6. Ensure `/admin/*` and `/system-admin/*` are fully separate.
7. Ensure organization status changes affect customer app bootstrap routing.
8. Add audit logs and email handling.

---

## 27. Non-Goals for MVP

Do not implement:

```text
Full internal user management UI
Reopen rejected organization
Suspend approved organization
Bulk approval
Advanced compliance workflow
Multi-step approval
Comments thread
File attachment review
CRM integration
```

---

## 28. Required Environment Variables

Use the same backend env vars as customer app plus optional internal settings.

```env
SYSTEM_ADMIN_APP_ENABLED=true
SYSTEM_ADMIN_DEFAULT_PAGE_SIZE=20
```

If using a separate internal frontend deployment:

```env
VITE_SYSTEM_ADMIN_API_BASE_URL=
```

---

## 29. Final Behavior Summary

End-to-end:

```text
Customer signs up
Customer verifies email
Customer submits organization onboarding
Organization status becomes pending_approval
System admin sees request in pending list
System admin opens details
System admin approves or rejects
Customer receives email
Customer app routing changes based on organization status
```
