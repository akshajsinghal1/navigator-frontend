# app_spec.md

# Customer-Facing Application Specification

## 0. Purpose

Build the customer-facing web application for a B2B Tableau-connected analytics/admin product.

This specification is intended for Codex or another coding agent to implement the application without needing additional product clarification.

This file covers only the customer-facing application:

- Signup
- Email verification
- Login
- Forgot password
- Password reset
- Organization onboarding
- Pending approval state
- Admin panel
- Organization overview
- Users and roles
- Invitations
- Tableau connector configuration
- Fake pipeline success flow for MVP

The internal organization approval console is intentionally excluded from this file and must be implemented from `system_admin_spec.md`.

---

## 1. Technology Stack

### Frontend

Use:

- Vite
- React
- TypeScript
- React Router
- Supabase JS client
- A clean component structure
- A simple modern UI

Recommended optional choices:

- Tailwind CSS if the repo already uses it
- shadcn/ui if the repo already uses it
- React Hook Form + Zod for form validation

Do not introduce a large frontend framework beyond Vite/React unless the existing repository already uses one.

### Backend

Use:

- Python
- FastAPI
- Supabase Python client or direct Postgres access
- JWT verification for Supabase access tokens
- Service role key only on the backend

### Database/Auth

Use:

- Supabase Auth for email/password authentication
- Supabase Postgres for application tables
- Supabase Row Level Security where applicable
- Backend-controlled writes for sensitive product actions

---

## 2. Product Model

The application is a multi-tenant organization-based product.

A user signs up, verifies their email, creates an organization, enters industry and personas, then waits for internal team approval.

After approval, the user gets access to the Admin Panel.

The organization admin can:

- See organization details
- See themselves as the admin user
- Invite users
- Assign personas to invited/active users
- Deactivate or delete users from the organization
- Configure a Tableau connector
- Run a fake pipeline for MVP
- See the active Tableau connection

---

## 3. Important Domain Rules

### 3.1 Admin is not a persona

Do not model `Admin` as a persona.

Use two separate concepts:

```text
Permission level:
- admin
- member

Persona:
- CFO
- Navigator
- Operations Head
- Sales Manager
- Finance Analyst
- etc.
```

A user may have:

```text
permission_level = admin
persona = null
```

or:

```text
permission_level = member
persona = "Navigator"
```

In future, an admin may also have a persona, but this is optional and not required for MVP.

### 3.2 One organization per newly self-signed-up admin for MVP

For the MVP:

- A self-signup user creates one organization during onboarding.
- The same user should not be allowed to create multiple organizations from the onboarding flow.
- The schema may support multiple organizations in the future, but the UI should assume one active organization.

### 3.3 Invited users skip onboarding

If a user is invited by an organization admin:

- They receive an invitation email.
- They sign up or accept the invitation.
- They do not see organization onboarding.
- They join the organization directly.
- Their persona may already be assigned by the admin or can be assigned later.

### 3.4 Organization must be approved before Admin Panel access

After onboarding:

```text
organization.status = pending_approval
```

The user should see a pending approval screen.

Only after internal approval:

```text
organization.status = approved
```

should the admin be able to access the Admin Panel.

### 3.5 Only one active Tableau connection

For MVP:

- An organization may have multiple historical Tableau connection records.
- Only one Tableau connection can be active at a time.
- The Admin Panel should show only the active/current Tableau connection.
- If a new connection is added and activated, previous active connections must be marked inactive.

### 3.6 Tableau pipeline is fake success for MVP

For MVP:

- Save connector config.
- Create a pipeline run.
- Simulate success.
- Show the connection as connected.
- Do not call the real Tableau API yet.
- Structure the code so that real Tableau validation can be added later.

---

## 4. User States

The app should route users based on their current state.

### 4.1 Possible states

```text
anonymous
email_pending
email_verified_no_org
org_pending_approval
org_rejected
org_approved_admin
org_approved_member
deactivated
deleted
```

### 4.2 Routing decisions

Use a backend bootstrap endpoint as the source of truth.

Endpoint:

```http
GET /api/me/bootstrap
```

Expected response:

```json
{
  "user": {
    "id": "uuid",
    "name": "Shibani Bhuyan",
    "email": "user@example.com"
  },
  "organization": {
    "id": "uuid",
    "name": "Example Organization",
    "industry_name": "Healthcare",
    "status": "approved"
  },
  "membership": {
    "id": "uuid",
    "permission_level": "admin",
    "status": "active",
    "persona": null
  },
  "next_route": "/admin/organization"
}
```

If unauthenticated, return 401 or frontend should detect missing session and route to `/login`.

### 4.3 `next_route` mapping

```text
No Supabase session
  -> /login

Session exists but email not confirmed
  -> /verify-email

Email confirmed but no organization or membership
  -> /onboarding

Organization exists but status = pending_approval
  -> /approval-pending

Organization status = rejected
  -> /approval-rejected

Membership status = deactivated
  -> /deactivated

Membership status = deleted
  -> /login or /access-removed

Organization approved + permission_level = admin
  -> /admin/organization

Organization approved + permission_level = member
  -> /app
```

For MVP, if member app is not implemented yet, route approved members to a placeholder `/app` page saying:

```text
Your account is active. Product workspace access will be available soon.
```

---

## 5. Frontend Route Map

Implement these routes.

```text
/
  Redirect based on bootstrap if logged in
  Otherwise redirect to /login

/login
/signup
/verify-email
/auth/callback
/forgot-password
/reset-password

/onboarding
/approval-pending
/approval-rejected
/deactivated
/access-removed

/admin
/admin/organization
/admin/users
/admin/connectors

/app
```

### 5.1 Public routes

Public routes:

```text
/login
/signup
/verify-email
/auth/callback
/forgot-password
/reset-password
```

### 5.2 Protected routes

Protected routes require a valid Supabase session:

```text
/onboarding
/approval-pending
/approval-rejected
/deactivated
/admin/*
/app
```

### 5.3 Admin-only routes

These require:

```text
organization.status = approved
membership.permission_level = admin
membership.status = active
```

Routes:

```text
/admin
/admin/organization
/admin/users
/admin/connectors
```

---

## 6. Authentication Flow

Use Supabase Auth for all email/password auth.

### 6.1 Signup screen

Route:

```text
/signup
```

Fields:

```text
Name
Email
Password
Confirm Password
```

Validation:

```text
Name is required
Email is required and must be valid
Password is required
Password must be at least 8 characters
Confirm password is required
Password and confirm password must match
```

On submit:

```ts
await supabase.auth.signUp({
  email,
  password,
  options: {
    data: {
      name
    },
    emailRedirectTo: `${APP_URL}/auth/callback`
  }
})
```

After successful signup, route to:

```text
/verify-email?email=user@example.com
```

Show:

```text
Please verify your email.
We sent a verification link to user@example.com.
This verification link expires in 10 minutes.
You can resend the email after 30 seconds.
```

### 6.2 Email verification expiry

Configure Supabase email link/OTP expiry to 10 minutes in the Supabase project settings.

App-level requirement:

```text
Verification link expiry: 10 minutes
```

If the link is expired, show a useful message and allow resend.

### 6.3 Resend verification email

On `/verify-email`:

- Show resend button disabled for 30 seconds after landing.
- After 30 seconds, enable resend.
- After resend, restart 30-second countdown.

Call:

```ts
await supabase.auth.resend({
  type: "signup",
  email,
  options: {
    emailRedirectTo: `${APP_URL}/auth/callback`
  }
})
```

Expected user messages:

Success:

```text
Verification email sent again. Please check your inbox.
```

Rate limit:

```text
Please wait before requesting another verification email.
```

Expired/invalid:

```text
This verification request is no longer valid. Please request a new email.
```

### 6.4 Auth callback

Route:

```text
/auth/callback
```

Responsibilities:

1. Complete Supabase auth session handling.
2. Wait until the session is available.
3. Call `/api/me/bootstrap`.
4. Redirect to `next_route`.

Pseudo flow:

```ts
const { data } = await supabase.auth.getSession()

if (!data.session) {
  redirect("/login")
}

const bootstrap = await api.getBootstrap()
redirect(bootstrap.next_route)
```

### 6.5 Login screen

Route:

```text
/login
```

Fields:

```text
Email
Password
```

Validation:

```text
Email required
Password required
```

On submit:

```ts
await supabase.auth.signInWithPassword({
  email,
  password
})
```

After successful login:

```text
Call /api/me/bootstrap
Redirect to next_route
```

Error handling:

```text
Invalid email or password.
Please verify your email before logging in.
Your account is deactivated. Contact your organization admin.
```

### 6.6 Forgot password

Route:

```text
/forgot-password
```

Fields:

```text
Email
```

Validation:

```text
Email required
Email must be valid
```

On submit:

```ts
await supabase.auth.resetPasswordForEmail(email, {
  redirectTo: `${APP_URL}/reset-password`
})
```

Show a generic success message even if the email is not registered:

```text
If an account exists for this email, we have sent password reset instructions.
```

### 6.7 Reset password

Route:

```text
/reset-password
```

This route is opened from the password recovery email.

Fields:

```text
New Password
Confirm New Password
```

Validation:

```text
Password required
Password at least 8 characters
Passwords must match
```

On submit:

```ts
await supabase.auth.updateUser({
  password: newPassword
})
```

After success:

```text
Password updated successfully.
Redirect to /login.
```

### 6.8 Logout

Add logout button in authenticated layout.

```ts
await supabase.auth.signOut()
redirect("/login")
```

---

## 7. Onboarding Flow

### 7.1 Route

```text
/onboarding
```

Access condition:

```text
Authenticated
Email confirmed
No existing active/pending organization membership
```

### 7.2 Fields

```text
Organization Name
Industry Name
Possible Personas
```

Personas should be entered as tags/chips.

Example personas:

```text
CFO
Navigator
Operations Head
Executive
Finance Analyst
```

Validation:

```text
Organization name required
Industry name required
At least one persona required
Persona name cannot be empty
Duplicate persona names not allowed, case-insensitive
Max 25 personas
Each persona max 80 characters
```

### 7.3 Submit endpoint

```http
POST /api/onboarding/organization
```

Request:

```json
{
  "organization_name": "ABC Healthcare",
  "industry_name": "Healthcare",
  "personas": [
    "CFO",
    "Navigator",
    "Operations Head"
  ]
}
```

Backend behavior:

1. Verify JWT.
2. Get auth user.
3. Ensure profile exists.
4. Ensure user does not already have an active/pending membership.
5. Create organization with `status = pending_approval`.
6. Create personas.
7. Create membership for current user:
   - `permission_level = admin`
   - `status = active`
   - `persona_id = null`
8. Create audit log.
9. Return pending approval state.

Response:

```json
{
  "organization": {
    "id": "uuid",
    "name": "ABC Healthcare",
    "industry_name": "Healthcare",
    "status": "pending_approval"
  },
  "next_route": "/approval-pending"
}
```

After success:

```text
Redirect to /approval-pending
```

### 7.4 Pending approval screen

Route:

```text
/approval-pending
```

Show:

```text
Your organization is registered.
Your request is being processed.
Someone from our team will review and approve the organization.
You will receive an email once your organization is approved.
```

Also show:

```text
Organization Name
Industry
Status: Pending Approval
Submitted Date
```

Button:

```text
Refresh Status
```

Refresh behavior:

```text
Call /api/me/bootstrap
If approved, redirect to /admin/organization
If still pending, stay on page
If rejected, redirect to /approval-rejected
```

### 7.5 Rejected screen

Route:

```text
/approval-rejected
```

Show:

```text
Your organization request was not approved.
Please contact our team for more details.
```

If rejection reason is available:

```text
Reason: <rejection_reason>
```

Do not allow user to create another organization in MVP unless explicitly enabled by backend.

---

## 8. Admin Panel Layout

### 8.1 Route group

```text
/admin/*
```

### 8.2 Layout

Use a persistent admin layout:

Left sidebar or top tabs:

```text
Organization Overview
Users & Roles
Connector Config
```

Header:

```text
Organization Name
Current user name
Logout button
```

### 8.3 Default admin route

```text
/admin
```

Redirect to:

```text
/admin/organization
```

### 8.4 Access guard

Before rendering admin pages:

1. Ensure Supabase session exists.
2. Call `/api/me/bootstrap`.
3. Require:
   - organization.status = approved
   - membership.status = active
   - membership.permission_level = admin

If not admin:

```text
Redirect to /app
```

If pending:

```text
Redirect to /approval-pending
```

If deactivated:

```text
Redirect to /deactivated
```

---

## 9. Organization Overview Tab

### 9.1 Route

```text
/admin/organization
```

### 9.2 Endpoint

```http
GET /api/admin/organization
```

Response:

```json
{
  "organization": {
    "id": "uuid",
    "name": "ABC Healthcare",
    "industry_name": "Healthcare",
    "status": "approved",
    "created_at": "2026-06-11T10:00:00Z",
    "approved_at": "2026-06-11T11:00:00Z"
  },
  "admin": {
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
  ]
}
```

### 9.3 UI content

Show cards:

```text
Organization Name
Industry
Approval Status
Created Date
Approved Date
Admin Name
Admin Email
```

Show personas as tags.

Example:

```text
Personas:
CFO, Navigator, Operations Head
```

### 9.4 MVP editing

For MVP, organization overview is read-only.

Do not implement edit organization unless requested later.

---

## 10. Users & Roles Tab

### 10.1 Route

```text
/admin/users
```

### 10.2 Data endpoint

```http
GET /api/admin/members
```

Response:

```json
{
  "members": [
    {
      "membership_id": "uuid",
      "user_id": "uuid",
      "name": "Shibani Bhuyan",
      "email": "admin@example.com",
      "permission_level": "admin",
      "persona": null,
      "status": "active",
      "joined_at": "2026-06-11T10:00:00Z"
    }
  ],
  "invitations": [
    {
      "invitation_id": "uuid",
      "name": "Richard",
      "email": "richard@example.com",
      "persona": {
        "id": "uuid",
        "name": "Navigator"
      },
      "status": "pending",
      "expires_at": "2026-06-18T10:00:00Z",
      "created_at": "2026-06-11T10:00:00Z"
    }
  ],
  "personas": [
    {
      "id": "uuid",
      "name": "CFO"
    },
    {
      "id": "uuid",
      "name": "Navigator"
    }
  ]
}
```

### 10.3 UI table

Show both active members and pending invitations.

Columns:

```text
Name
Email
Permission
Role / Persona
Status
Actions
```

Admin row:

```text
Name: Current admin name
Email: Current admin email
Permission: Admin
Role / Persona: —
Status: Active
Actions: —
```

Pending invitation row:

```text
Name: Invited name
Email: Invited email
Permission: Member
Role / Persona: selected persona or Unassigned
Status: Pending Invitation
Actions: Resend, Cancel
```

Active member row:

```text
Name
Email
Permission: Member
Role / Persona dropdown
Status: Active
Actions: Deactivate, Delete
```

Deactivated member row:

```text
Status: Deactivated
Actions: Reactivate, Delete
```

### 10.4 Invite user modal

Button:

```text
Invite User
```

Fields:

```text
Name
Email
Role / Persona
```

Role/persona is optional.

Validation:

```text
Name required
Email required and valid
Persona optional
Selected persona must belong to current organization
```

Submit endpoint:

```http
POST /api/admin/invitations
```

Request:

```json
{
  "name": "Richard",
  "email": "richard@example.com",
  "persona_id": "uuid-or-null"
}
```

Backend behavior:

1. Verify admin permission.
2. Check organization is approved.
3. Normalize email to lowercase.
4. Check email is not already an active member.
5. Check there is no pending invitation for same org/email.
6. Validate persona belongs to organization if provided.
7. Create secure random invitation token.
8. Store only token hash in DB.
9. Set status `pending`.
10. Set expiry, recommended 7 days.
11. Send invitation email.
12. Return invitation row.

Response:

```json
{
  "invitation": {
    "id": "uuid",
    "email": "richard@example.com",
    "name": "Richard",
    "status": "pending",
    "expires_at": "2026-06-18T10:00:00Z"
  }
}
```

### 10.5 Invite email

Email subject:

```text
You have been invited to join <Organization Name>
```

Email body:

```text
Hello <Name>,

You have been invited to join <Organization Name>.

Click the link below to accept the invitation and create your account.

<APP_URL>/accept-invitation?token=<raw_token>

This invitation expires in 7 days.
```

### 10.6 Invitation acceptance route

Route:

```text
/accept-invitation?token=<token>
```

Behavior:

1. Call validate endpoint.
2. If valid, show invitation details.
3. If user is not logged in:
   - Show signup form.
   - Email should be prefilled and locked.
4. If user is logged in:
   - Allow accepting invitation if logged-in email matches invitation email.
5. Do not show organization onboarding.

Validate endpoint:

```http
GET /api/invitations/validate?token=<token>
```

Response:

```json
{
  "valid": true,
  "invitation": {
    "email": "richard@example.com",
    "name": "Richard",
    "organization_name": "ABC Healthcare",
    "persona": {
      "id": "uuid",
      "name": "Navigator"
    },
    "expires_at": "2026-06-18T10:00:00Z"
  }
}
```

Invalid response:

```json
{
  "valid": false,
  "reason": "expired"
}
```

### 10.7 Signup from invitation

If invited user has no account:

Use Supabase signup:

```ts
await supabase.auth.signUp({
  email: invitation.email,
  password,
  options: {
    data: {
      name: enteredName,
      invitation_token: token
    },
    emailRedirectTo: `${APP_URL}/auth/callback?invitation_token=${token}`
  }
})
```

After email verification/callback:

```text
Call /api/invitations/accept
```

Endpoint:

```http
POST /api/invitations/accept
```

Request:

```json
{
  "token": "raw-token"
}
```

Backend behavior:

1. Verify JWT.
2. Validate token hash.
3. Ensure invitation status is pending.
4. Ensure invitation is not expired.
5. Ensure logged-in user email matches invitation email.
6. Ensure profile exists.
7. Create membership:
   - organization_id from invitation
   - user_id from auth user
   - permission_level = member
   - persona_id = invitation.persona_id
   - status = active
8. Mark invitation accepted.
9. Create audit log.
10. Return next route.

Response:

```json
{
  "success": true,
  "next_route": "/app"
}
```

### 10.8 Change member persona

Endpoint:

```http
PATCH /api/admin/members/{membership_id}/persona
```

Request:

```json
{
  "persona_id": "uuid-or-null"
}
```

Behavior:

1. Verify current user is org admin.
2. Ensure target membership belongs to same organization.
3. Ensure target user is not deleted.
4. Ensure persona belongs to same organization if not null.
5. Update membership persona.
6. Create audit log.

Do not allow assigning personas from another organization.

### 10.9 Deactivate user

Endpoint:

```http
PATCH /api/admin/members/{membership_id}/deactivate
```

Behavior:

1. Verify current user is org admin.
2. Do not allow admin to deactivate themselves if they are the only active admin.
3. Set:
   - `status = deactivated`
   - `deactivated_at = now()`
4. Create audit log.
5. User should lose access on next bootstrap/API call.

### 10.10 Reactivate user

Endpoint:

```http
PATCH /api/admin/members/{membership_id}/reactivate
```

Behavior:

1. Verify admin permission.
2. Set:
   - `status = active`
   - `deactivated_at = null`
3. Create audit log.

### 10.11 Delete user from organization

Endpoint:

```http
DELETE /api/admin/members/{membership_id}
```

Behavior:

Soft-delete the membership.

Set:

```text
status = deleted
deleted_at = now()
```

Do not delete Supabase auth user.

Do not allow deleting yourself if you are the only active admin.

### 10.12 Resend invitation

Endpoint:

```http
POST /api/admin/invitations/{invitation_id}/resend
```

Behavior:

1. Verify admin.
2. Ensure invitation belongs to org.
3. Ensure invitation status is pending.
4. Create a new token and token hash.
5. Extend expiry.
6. Send email.
7. Create audit log.

### 10.13 Cancel invitation

Endpoint:

```http
POST /api/admin/invitations/{invitation_id}/cancel
```

Behavior:

1. Verify admin.
2. Ensure invitation belongs to org.
3. Set status to `cancelled`.
4. Create audit log.

---

## 11. Connector Config Tab

### 11.1 Route

```text
/admin/connectors
```

### 11.2 Initial empty state

If there is no active Tableau connection:

Show:

```text
No Tableau connection configured.
Connect your Tableau workbook to run the pipeline.
```

Show form.

### 11.3 Tableau config form fields

```text
Server URL
Site Name
PAT Name
PAT Secret
Workbook Name
```

Validation:

```text
Server URL required and must be valid URL
Site Name required
PAT Name required
PAT Secret required when creating a new connection
Workbook Name required
```

Example:

```text
Server URL: https://us-east-1.online.tableau.com
Site Name: navigatorpilot
PAT Name: backend_pat
PAT Secret: ********
Workbook Name: Referral Intelligence Dashboard
```

### 11.4 Save and run pipeline

Primary button:

```text
Save & Run Pipeline
```

Endpoint:

```http
POST /api/admin/connectors/tableau
```

Request:

```json
{
  "server_url": "https://us-east-1.online.tableau.com",
  "site_name": "navigatorpilot",
  "pat_name": "backend_pat",
  "pat_secret": "secret",
  "workbook_name": "Referral Intelligence Dashboard",
  "run_pipeline": true
}
```

Backend behavior for MVP:

1. Verify current user is org admin.
2. Validate organization is approved.
3. Validate all fields.
4. Store connection.
5. Store PAT secret securely.
6. Mark any previous active Tableau connection inactive.
7. Mark new connection active.
8. Create pipeline run:
   - status = running
   - run_type = manual
9. Immediately simulate success:
   - connection.status = connected
   - connection.last_success_at = now()
   - pipeline_run.status = success
   - pipeline_run.finished_at = now()
   - pipeline_run.logs include fake successful steps
10. Return connection and pipeline run.

Response:

```json
{
  "connection": {
    "id": "uuid",
    "server_url": "https://us-east-1.online.tableau.com",
    "site_name": "navigatorpilot",
    "pat_name": "backend_pat",
    "workbook_name": "Referral Intelligence Dashboard",
    "status": "connected",
    "is_active": true,
    "last_success_at": "2026-06-11T10:00:00Z"
  },
  "pipeline_run": {
    "id": "uuid",
    "status": "success",
    "logs": [
      {
        "level": "info",
        "message": "Tableau configuration saved."
      },
      {
        "level": "info",
        "message": "Pipeline validation completed."
      },
      {
        "level": "info",
        "message": "Workbook connected successfully."
      }
    ]
  }
}
```

### 11.5 Connected state UI

If an active connection exists, show a connected card:

```text
Connected Tableau Workbook

Server URL
Site Name
PAT Name
PAT Secret: ••••••••••••••
Workbook Name
Status
Last Successful Pipeline Run
```

Buttons:

```text
Run Pipeline Again
Edit Connection
Add New Connection
Deactivate Connection
```

For MVP, show only the active connection.

### 11.6 Run pipeline again

Endpoint:

```http
POST /api/admin/connectors/tableau/{connection_id}/run-pipeline
```

MVP behavior:

1. Verify admin.
2. Ensure connection belongs to org.
3. Ensure connection is active.
4. Create pipeline run.
5. Simulate success.
6. Update last_success_at.
7. Return run result.

### 11.7 Edit connection

Allow editing:

```text
Server URL
Site Name
PAT Name
PAT Secret
Workbook Name
```

If PAT secret field is left empty during edit:

```text
Keep existing secret
```

If PAT secret is provided:

```text
Replace stored secret
```

Endpoint:

```http
PATCH /api/admin/connectors/tableau/{connection_id}
```

Request:

```json
{
  "server_url": "https://us-east-1.online.tableau.com",
  "site_name": "navigatorpilot",
  "pat_name": "backend_pat",
  "pat_secret": null,
  "workbook_name": "Referral Intelligence Dashboard"
}
```

### 11.8 Add new connection

Button:

```text
Add New Connection
```

Behavior:

- Open same connector form.
- When saved, new connection becomes active.
- Previous active connection becomes inactive.
- UI continues showing only the new active connection.

### 11.9 Deactivate connection

Endpoint:

```http
POST /api/admin/connectors/tableau/{connection_id}/deactivate
```

Behavior:

1. Verify admin.
2. Set:
   - `is_active = false`
   - `status = inactive`
3. UI returns to empty state.

### 11.10 Connector errors

Show user-friendly errors.

Examples:

```text
Server URL is invalid.
PAT name is required.
PAT secret is required.
Workbook name is required.
Unable to save Tableau connection. Please try again.
Pipeline failed. Please check your configuration.
Only organization admins can manage connectors.
```

Even though the pipeline is fake success for MVP, still implement error handling for failed backend requests.

---

## 12. Backend Design

### 12.1 Auth middleware

Every protected backend route must:

1. Read `Authorization: Bearer <access_token>`.
2. Verify token with Supabase/JWT verification.
3. Load the Supabase auth user.
4. Load profile.
5. Attach `current_user` to request context.

Do not trust frontend-provided user IDs.

### 12.2 Admin permission helper

Create backend helper:

```python
require_org_admin(current_user_id: UUID) -> OrgContext
```

It should return:

```python
class OrgContext:
    organization_id: UUID
    organization_status: str
    membership_id: UUID
    permission_level: str
    membership_status: str
```

Required conditions:

```text
membership.status = active
membership.permission_level = admin
organization.status = approved
```

For onboarding, use a different helper because org may not exist yet.

### 12.3 Organization context helper

Create helper:

```python
get_user_org_context(current_user_id: UUID) -> OrgContext | None
```

Used by bootstrap and member routes.

### 12.4 Error response format

Use consistent API errors:

```json
{
  "error": {
    "code": "ORG_NOT_APPROVED",
    "message": "Your organization has not been approved yet."
  }
}
```

Common error codes:

```text
UNAUTHENTICATED
FORBIDDEN
EMAIL_NOT_VERIFIED
PROFILE_NOT_FOUND
ORG_ALREADY_EXISTS
ORG_NOT_FOUND
ORG_NOT_APPROVED
MEMBERSHIP_NOT_FOUND
MEMBERSHIP_DEACTIVATED
ONLY_ADMIN_ALLOWED
INVITATION_NOT_FOUND
INVITATION_EXPIRED
INVITATION_CANCELLED
INVITATION_ALREADY_ACCEPTED
PERSONA_NOT_FOUND
CONNECTOR_NOT_FOUND
VALIDATION_ERROR
INTERNAL_ERROR
```

### 12.5 API route list

Implement:

```text
GET    /api/health

GET    /api/me/bootstrap

POST   /api/onboarding/organization
GET    /api/onboarding/status

GET    /api/admin/organization

GET    /api/admin/members
POST   /api/admin/invitations
GET    /api/invitations/validate
POST   /api/invitations/accept
POST   /api/admin/invitations/{invitation_id}/resend
POST   /api/admin/invitations/{invitation_id}/cancel

PATCH  /api/admin/members/{membership_id}/persona
PATCH  /api/admin/members/{membership_id}/deactivate
PATCH  /api/admin/members/{membership_id}/reactivate
DELETE /api/admin/members/{membership_id}

GET    /api/admin/connectors/tableau
POST   /api/admin/connectors/tableau
PATCH  /api/admin/connectors/tableau/{connection_id}
POST   /api/admin/connectors/tableau/{connection_id}/run-pipeline
POST   /api/admin/connectors/tableau/{connection_id}/activate
POST   /api/admin/connectors/tableau/{connection_id}/deactivate
```

Do not implement internal approval APIs in this file. They belong to `system_admin_spec.md`.

---

## 13. Database Schema

Create migrations for the following tables.

Use UUID primary keys.

### 13.1 profiles

```sql
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  name text not null,
  email text not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
```

### 13.2 organizations

```sql
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
```

### 13.3 personas

```sql
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
```

### 13.4 memberships

```sql
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
```

### 13.5 invitations

```sql
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
```

### 13.6 tableau_connections

```sql
create table if not exists public.tableau_connections (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  server_url text not null,
  site_name text not null,
  pat_name text not null,
  pat_secret_ref text,
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
```

### 13.7 pipeline_runs

```sql
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
```

### 13.8 audit_logs

```sql
create table if not exists public.audit_logs (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid references public.organizations(id),
  actor_user_id uuid references public.profiles(id),
  action text not null,
  entity_type text not null,
  entity_id uuid,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
```

---

## 14. Database Indexes

Create indexes:

```sql
create index if not exists idx_organizations_created_by
  on public.organizations(created_by);

create index if not exists idx_organizations_status
  on public.organizations(status);

create index if not exists idx_personas_organization_id
  on public.personas(organization_id);

create index if not exists idx_memberships_user_id
  on public.memberships(user_id);

create index if not exists idx_memberships_organization_id
  on public.memberships(organization_id);

create index if not exists idx_memberships_org_status
  on public.memberships(organization_id, status);

create index if not exists idx_invitations_email
  on public.invitations(email);

create index if not exists idx_invitations_org_status
  on public.invitations(organization_id, status);

create index if not exists idx_invitations_token_hash
  on public.invitations(token_hash);

create index if not exists idx_tableau_connections_org_active
  on public.tableau_connections(organization_id, is_active);

create index if not exists idx_pipeline_runs_org
  on public.pipeline_runs(organization_id);
```

---

## 15. Row Level Security

Enable RLS:

```sql
alter table public.profiles enable row level security;
alter table public.organizations enable row level security;
alter table public.personas enable row level security;
alter table public.memberships enable row level security;
alter table public.invitations enable row level security;
alter table public.tableau_connections enable row level security;
alter table public.pipeline_runs enable row level security;
alter table public.audit_logs enable row level security;
```

Recommended MVP approach:

- Frontend uses Supabase Auth directly.
- Frontend does not directly write app tables.
- Backend uses service role for app-table writes.
- RLS protects accidental frontend reads/writes.

Minimum read policies may be added for authenticated users to read their own profile and organization context if needed.

If all app-table reads go through backend APIs, keep frontend direct database access minimal.

---

## 16. Profile Creation

On first auth callback or first bootstrap, ensure profile exists.

Profile source:

```text
auth.users.id
auth.users.email
auth.users.raw_user_meta_data.name
```

Backend function:

```python
ensure_profile(user):
    if profile does not exist:
        create profile with id, name, email
    else:
        update email/name if needed
```

Alternative: database trigger on `auth.users`.

For simplicity and better control, implement backend `ensure_profile`.

---

## 17. Secrets Handling

Never expose:

```text
SUPABASE_SERVICE_ROLE_KEY
PAT Secret
Secret encryption key
Internal admin secret
```

Frontend environment variables may include:

```text
VITE_SUPABASE_URL
VITE_SUPABASE_ANON_KEY
VITE_API_BASE_URL
```

Backend environment variables:

```text
SUPABASE_URL
SUPABASE_ANON_KEY
SUPABASE_SERVICE_ROLE_KEY
SUPABASE_JWT_SECRET
APP_URL
API_URL
EMAIL_FROM
SMTP_HOST
SMTP_PORT
SMTP_USER
SMTP_PASSWORD
INVITATION_TOKEN_SECRET
CONNECTOR_SECRET_ENCRYPTION_KEY
```

For Tableau PAT secret:

- Store encrypted value or reference.
- Never return PAT secret in API responses.
- Return masked display only.

API response should use:

```json
{
  "pat_secret_masked": "••••••••••••"
}
```

---

## 18. Email Requirements

The app needs emails for:

```text
Signup verification
Password reset
Invitation
Organization approved
Organization rejected
```

For MVP:

- Supabase can handle signup verification and password reset.
- Backend should handle invitation and organization status emails.
- If using Supabase invite API, call it only from backend.
- If using custom invitation tokens, send invitation email from backend SMTP/email provider.

Email templates should be simple and professional.

### 18.1 Invitation email template

Subject:

```text
You have been invited to join {{organization_name}}
```

Body:

```text
Hello {{name}},

You have been invited to join {{organization_name}}.

Click the link below to accept your invitation and create your account:

{{invite_url}}

This invitation expires in 7 days.

If you were not expecting this invitation, you can ignore this email.
```

### 18.2 Approval email template

This is sent by the system admin approval flow, but customer app must support the result.

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
```

### 18.3 Rejection email template

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

---

## 19. Frontend Component Plan

Suggested structure:

```text
src/
  app/
    App.tsx
    router.tsx
  lib/
    supabase.ts
    api.ts
    auth.ts
  components/
    layout/
      PublicLayout.tsx
      ProtectedLayout.tsx
      AdminLayout.tsx
    forms/
      TextField.tsx
      PasswordField.tsx
      SelectField.tsx
      TagInput.tsx
    feedback/
      LoadingState.tsx
      EmptyState.tsx
      ErrorAlert.tsx
      SuccessAlert.tsx
      StatusBadge.tsx
    admin/
      OrganizationOverview.tsx
      UsersRolesTable.tsx
      InviteUserModal.tsx
      TableauConnectorForm.tsx
      TableauConnectionCard.tsx
  pages/
    LoginPage.tsx
    SignupPage.tsx
    VerifyEmailPage.tsx
    AuthCallbackPage.tsx
    ForgotPasswordPage.tsx
    ResetPasswordPage.tsx
    OnboardingPage.tsx
    ApprovalPendingPage.tsx
    ApprovalRejectedPage.tsx
    DeactivatedPage.tsx
    AccessRemovedPage.tsx
    AdminOrganizationPage.tsx
    AdminUsersPage.tsx
    AdminConnectorsPage.tsx
    AppPlaceholderPage.tsx
  types/
    api.ts
    domain.ts
```

---

## 20. Backend Structure Plan

Suggested structure:

```text
backend/
  app/
    main.py
    config.py
    dependencies.py
    auth/
      supabase_auth.py
      permissions.py
    db/
      supabase_client.py
      repositories.py
    routes/
      health.py
      me.py
      onboarding.py
      admin_organization.py
      admin_members.py
      invitations.py
      connectors.py
    services/
      profile_service.py
      onboarding_service.py
      organization_service.py
      invitation_service.py
      membership_service.py
      connector_service.py
      pipeline_service.py
      email_service.py
      audit_service.py
      secret_service.py
    schemas/
      common.py
      onboarding.py
      organization.py
      members.py
      invitations.py
      connectors.py
    utils/
      tokens.py
      validation.py
      time.py
  tests/
    test_bootstrap.py
    test_onboarding.py
    test_invitations.py
    test_members.py
    test_connectors.py
```

---

## 21. Backend Implementation Details

### 21.1 Token hashing for invitations

Do not store raw invitation token.

Generate:

```python
raw_token = secrets.token_urlsafe(32)
token_hash = sha256(raw_token + INVITATION_TOKEN_SECRET)
```

Store:

```text
token_hash
```

Send:

```text
raw_token
```

When validating:

```python
hash incoming raw token
compare to token_hash
```

### 21.2 Invitation expiry

Default:

```text
7 days
```

Validation:

```text
expires_at > now()
status = pending
```

If expired, backend may update status to `expired`.

### 21.3 Prevent duplicate pending invites

Before creating invitation:

```text
same organization_id
same email
status = pending
```

If exists, return error:

```text
INVITATION_ALREADY_PENDING
```

### 21.4 Prevent duplicate active member

Before inviting:

```text
same organization_id
same email exists as active/deactivated membership
```

Return:

```text
USER_ALREADY_MEMBER
```

### 21.5 Prevent deleting only admin

Before deactivating/deleting admin user:

```text
count active admins in org
if count <= 1 and target is admin -> block
```

Return:

```text
CANNOT_REMOVE_ONLY_ADMIN
```

### 21.6 Pipeline fake success logs

Use deterministic fake logs:

```json
[
  {
    "level": "info",
    "message": "Tableau connector configuration saved."
  },
  {
    "level": "info",
    "message": "Validating workbook metadata."
  },
  {
    "level": "info",
    "message": "Workbook registered successfully."
  },
  {
    "level": "success",
    "message": "Pipeline completed successfully."
  }
]
```

---

## 22. UI Status Badges

Use consistent badges.

### Organization status

```text
pending_approval -> Pending Approval
approved -> Approved
rejected -> Rejected
suspended -> Suspended
```

### Member status

```text
active -> Active
deactivated -> Deactivated
deleted -> Deleted
```

### Invitation status

```text
pending -> Pending Invitation
accepted -> Accepted
expired -> Expired
cancelled -> Cancelled
```

### Connector status

```text
not_tested -> Not Tested
connected -> Connected
failed -> Failed
inactive -> Inactive
```

### Pipeline status

```text
queued -> Queued
running -> Running
success -> Success
failed -> Failed
```

---

## 23. Loading and Empty States

Every async page must have:

```text
Loading state
Error state
Empty state when applicable
Success confirmation when action completes
```

Examples:

Users tab empty state should still show admin user. It should not be fully empty.

Connector empty state:

```text
No Tableau connection configured yet.
Add your Tableau connection to run the pipeline.
```

Invitations empty state:

```text
No pending invitations.
```

---

## 24. Validation Rules

### Email

- Trim
- Lowercase before storing
- Validate format

### Organization name

- Trim
- Required
- 2 to 120 chars

### Industry name

- Trim
- Required
- 2 to 120 chars

### Persona name

- Trim
- Required
- 2 to 80 chars
- Case-insensitive duplicate prevention

### Password

Frontend minimum:

```text
8 characters
```

Do not implement overly complex password policy unless required.

### Server URL

- Required
- Must parse as URL
- Must start with `http://` or `https://`
- Recommend HTTPS but do not block localhost in development

### PAT secret

- Required on create
- Optional on edit

---

## 25. Security Requirements

1. Do not expose service role key to frontend.
2. Do not expose PAT secret to frontend after save.
3. Do not trust frontend user ID.
4. Every backend action must use authenticated user context from JWT.
5. Admin-only routes must check backend permission.
6. Organization IDs must not allow cross-tenant access.
7. Persona assignment must verify same organization.
8. Invitation acceptance must verify email match.
9. Store invitation token hash, not raw token.
10. Use soft delete for memberships.
11. Keep audit logs for all important admin actions.

---

## 26. Audit Log Actions

Create audit logs for:

```text
organization.created
invitation.created
invitation.resent
invitation.cancelled
invitation.accepted
member.persona_updated
member.deactivated
member.reactivated
member.deleted
connector.created
connector.updated
connector.activated
connector.deactivated
pipeline.started
pipeline.succeeded
pipeline.failed
```

Audit metadata example:

```json
{
  "previous_persona_id": "uuid",
  "new_persona_id": "uuid"
}
```

---

## 27. Acceptance Criteria

### Auth

- User can sign up with name, email, password, confirm password.
- User sees verify email screen.
- Verification message says link expires in 10 minutes.
- Resend button has 30-second cooldown.
- User can log in after verification.
- User can request forgot password email.
- User can reset password from reset link.
- User can log out.

### Onboarding

- Verified new user is routed to onboarding.
- User can enter organization name, industry, personas.
- Onboarding creates pending organization.
- User becomes organization admin.
- User sees pending approval screen.
- User cannot access admin panel before approval.

### Admin panel

- Approved admin can access admin panel.
- Organization overview shows organization details.
- Users tab shows admin user with admin badge.
- Admin can invite users.
- Pending invitation appears in table.
- Admin can resend and cancel invitation.
- Invited user can accept invitation without onboarding.
- Admin can assign/change persona.
- Admin can deactivate/reactivate/delete users.
- Deactivated user cannot access organization.

### Connector

- Admin can add Tableau config.
- Required fields are validated.
- PAT secret is never returned.
- Save & Run Pipeline creates fake success.
- Connected card is shown.
- Only one active connection is shown.
- Add new connection makes the new one active.
- Run Pipeline Again creates a success run.

### Security

- Member cannot access admin APIs.
- Cross-organization access is blocked.
- Invitation token cannot be reused after acceptance.
- Expired invitation cannot be accepted.
- Only active approved admins can manage users/connectors.

---

## 28. Non-Goals for MVP

Do not implement:

```text
Real Tableau API connection
Real Tableau metadata extraction
Multiple active dashboards
Advanced RBAC beyond admin/member
Organization switching UI
Editing organization overview
Billing
SSO/SAML
Google login
Member workspace features beyond placeholder
Complex analytics dashboard
System admin approval console in this app spec
```

---

## 29. Development Notes for Codex

When implementing:

1. Start with DB migrations.
2. Implement backend auth middleware and bootstrap.
3. Implement frontend auth screens.
4. Implement onboarding.
5. Implement admin layout.
6. Implement organization overview.
7. Implement users/invitations.
8. Implement connector config.
9. Add tests.

Do not build internal approval in this file. Use `system_admin_spec.md` for that.

---

## 30. Manual QA Checklist

### Signup QA

```text
Create account
Try password mismatch
Try invalid email
Verify email
Resend verification
Login before verification
Login after verification
```

### Onboarding QA

```text
Submit empty form
Submit duplicate personas
Submit valid organization
Refresh pending screen
Attempt /admin before approval
```

### Users QA

```text
Invite user with no role
Invite user with role
Invite duplicate email
Resend invitation
Cancel invitation
Accept invitation
Change role
Deactivate user
Reactivate user
Delete user
```

### Connector QA

```text
Submit empty form
Submit invalid server URL
Submit valid config
Check connected card
Run pipeline again
Edit connection
Add new connection
Deactivate connection
```

---

## 31. Required Environment Variables

### Frontend

```env
VITE_SUPABASE_URL=
VITE_SUPABASE_ANON_KEY=
VITE_API_BASE_URL=
VITE_APP_URL=
```

### Backend

```env
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_JWT_SECRET=
APP_URL=
API_URL=
SMTP_HOST=
SMTP_PORT=
SMTP_USER=
SMTP_PASSWORD=
EMAIL_FROM=
INVITATION_TOKEN_SECRET=
CONNECTOR_SECRET_ENCRYPTION_KEY=
```

---

## 32. Reference Notes

- Supabase handles email/password auth, signup, login, and password reset.
- Supabase auth emails and resend behavior should be configured in the Supabase project.
- Use backend-only service role key for privileged operations.
- Use RLS and backend permission checks together.
- Do not store Tableau PAT secret in plain text.
